"""Generate topology and observation JSON with collect and observe.

collect --out specifies the topology JSON path; observe --out specifies
the observation JSON path. observe reads the topology through --topology
and uses its collection scope to query current state.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from . import __version__, lb_targets
from .awsguard import (
    ReadOnlyViolation,
    assume_role_session,
    read_only_client,
    read_only_session,
)
from .collect import SUPPORTED_RESOURCE_TYPES, collect
from .discover import (
    DISCOVERY_API,
    REGION_DISCOVERY_API,
    bootstrap_region,
    discover_accounts,
    discover_regions,
)
from .flowlogs import read_flow_logs
from .history import apply_history
from .metrics import METRIC_API, PERIOD_SECONDS, read_metrics
from .model import SCHEMA_VERSION as TOPOLOGY_SCHEMA_VERSION
from .model import Scope
from .observe import OBSERVED_RESOURCE_TYPES, build_observation
from .serialize import digest_excluding_collected_at, dumps, to_json
from .sources import SourceScan, detect_sources
from .utilization import compute_utilization

_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")
_ACCOUNT_IN_TEXT = re.compile(r"[0-9]{12}")
DEFAULT_ROLE_NAME = "NetworkReadOnlyRole"

STS_IDENTITY_REGION = "aws-global"


def _mask(account_id: str) -> str:
    return f"{account_id[:3]}…{account_id[-3:]}" if len(account_id) == 12 else "?"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettopo",
        description="AWS 네트워크 토폴로지를 읽기 전용으로 수집한다",
    )
    parser.add_argument("--version", action="version", version=f"nettopo {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    collect_parser = sub.add_parser("collect", help="스냅샷 JSON 을 만든다")
    collect_parser.add_argument(
        "--profile", help="AWS 프로필(읽기 전용 권한 · 예: my-hub)"
    )
    collect_parser.add_argument(
        "--region",
        action="append",
        metavar="REGION",
        help=(
            "수집할 리전. 여러 번 줄 수 있다. **생략하면** 계정이 켜 둔 리전을 "
            "ec2:DescribeRegions 로 자동 발견한다(ADR 0003 D1). 셸 기본 리전은 쓰지 않는다"
        ),
    )
    collect_parser.add_argument(
        "--accounts",
        help=(
            "쉼표로 구분한 12자리 계정ID 목록 = **더할** 계정(ADR 0005). 줘도 허브 TGW "
            "어태치먼트의 ResourceOwnerId 로 자동 발견을 돌리고 합집합(명시 ∪ 발견 ∪ 허브)을 "
            "수집한다(ADR 0003 D1 · 사용자 결정 2026-10-20). 발견이 모든 리전에서 실패하면 "
            "명시 목록 ∪ 허브로 진행한다(경고). 생략하면 발견만 쓰고, 실패 시 멈춘다"
        ),
    )
    collect_parser.add_argument(
        "--resource-types",
        default=",".join(SUPPORTED_RESOURCE_TYPES),
        help=f"쉼표로 구분. 지금 지원: {', '.join(SUPPORTED_RESOURCE_TYPES)}",
    )
    collect_parser.add_argument(
        "--role-name",
        default=DEFAULT_ROLE_NAME,
        help=f"다른 계정에 들어갈 때 assume 할 역할 이름(기본 {DEFAULT_ROLE_NAME})",
    )
    collect_parser.add_argument(
        "--external-id",
        default=None,
        help="멤버 역할 신뢰 정책의 sts:ExternalId(스택 B ExternalId 를 줬을 때만 · 비우면 넘기지 않는다)",
    )
    collect_parser.add_argument("--out", required=True, type=Path, help="산출물 경로")

    observe_parser = sub.add_parser(
        "observe", help="구조 스냅샷 기준으로 1층 상태 관측 JSON 을 만든다"
    )
    observe_parser.add_argument(
        "--profile", help="AWS 프로필(읽기 전용 권한 · 예: my-hub)"
    )
    observe_parser.add_argument(
        "--topology",
        required=True,
        type=Path,
        help="`collect` 가 만든 topology.json — scope·대상 id·기준 시각을 여기서 읽는다",
    )
    observe_parser.add_argument(
        "--role-name",
        default=DEFAULT_ROLE_NAME,
        help=f"다른 계정에 들어갈 때 assume 할 역할 이름(기본 {DEFAULT_ROLE_NAME})",
    )
    observe_parser.add_argument(
        "--external-id",
        default=None,
        help="멤버 역할 신뢰 정책의 sts:ExternalId(스택 B ExternalId 를 줬을 때만 · 비우면 넘기지 않는다)",
    )
    observe_parser.add_argument("--out", required=True, type=Path, help="산출물 경로")
    observe_parser.add_argument(
        "--metrics",
        action="store_true",
        help="2층(CloudWatch 지표)도 읽어 흐름이 있는 대상을 live 로 올린다(기본 off)",
    )
    observe_parser.add_argument(
        "--no-sources",
        action="store_true",
        help=(
            "옵션형 근거 출처(흐름 로그·Container Insights 등) 탐지를 건너뛴다. "
            "그러면 산출물의 sources 가 빈 배열이 되고 상단 알약이 사라진다"
        ),
    )
    observe_parser.add_argument(
        "--metrics-profile",
        help=(
            "2층만 다른 프로필로 읽는다(생략하면 --profile 과 같다). **같은 계정의 다른 "
            "프로필**용이다 — 예: 구조 읽기와 지표 읽기를 서로 다른 권한 세트로 나눈 경우"
        ),
    )
    observe_parser.add_argument(
        "--flow-logs",
        action="store_true",
        help=(
            "VPC 흐름 로그를 읽어 인터넷·피어링 선의 흐름을 왕복 기록으로 확인한다(기본 off · "
            "--metrics 필요 · 로그 그룹·버킷 읽기 권한 필요)"
        ),
    )
    observe_parser.add_argument(
        "--utilization",
        action="store_true",
        help=(
            "사용량을 계산한다 — 기간 6개(15분~3달)의 합 · 가장 바빴던 때 · 평균 · 개수 한도(기본 off · "
            "--metrics 와 독립 · 서비스 한도 읽기 권한이 없으면 문서 기본값으로 나눈다)"
        ),
    )
    observe_parser.add_argument(
        "--flow-logs-profile",
        help="흐름 로그만 다른 프로필로 읽는다(생략하면 --profile 과 같다 · --metrics-profile 과 같은 용도)",
    )
    observe_parser.add_argument(
        "--previous",
        type=Path,
        help=(
            "직전 관측 JSON — 두 문서를 비교해 changes[](구성·상태 변경 이력)를 채운다. "
            "없거나 읽을 수 없으면 경고를 내고 이력을 이번 관측부터 새로 시작한다"
        ),
    )
    observe_parser.add_argument(
        "--previous-topology",
        type=Path,
        action="append",
        help=(
            "직전 구조 JSON(선택 · 여러 번 줄 수 있다) — 없어진 리소스의 이름·연결을 이력에 같이 적는 데 쓴다. "
            "없거나 읽을 수 없으면 경고를 내고 나머지 구조로 찾는다"
        ),
    )
    return parser


def _parse_list(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


def _caller_identity(profile: str | None) -> tuple[str, str]:
    """Return the caller's account ID and ARN through STS
    GetCallerIdentity.

    Use the ARN to identify the AWS partition and discovery region.
    """
    client = read_only_client(
        "sts", profile_name=profile, region_name=STS_IDENTITY_REGION
    )
    identity = client.get_caller_identity()
    return str(identity["Account"]), str(identity.get("Arn") or "")


class _SessionCache:
    """Reuse per-account AWS sessions and lock session and client
    creation.

    Run AssumeRole under the same lock to avoid duplicate requests for
    an
    account. Use the requested region's STS endpoint for cross-account
    roles.
    """

    def __init__(
        self,
        *,
        profile: str | None,
        role_name: str,
        caller_account: str,
        external_id: str | None = None,
    ) -> None:
        self._profile = profile
        self._role_name = role_name
        self._external_id = external_id
        self._caller_account = caller_account
        self._sessions: dict[str, Any] = {}
        self._lock = threading.Lock()

    def client(self, account_id: str, region: str, service: str) -> Any:
        with self._lock:
            if account_id not in self._sessions:
                if account_id == self._caller_account:
                    self._sessions[account_id] = read_only_session(
                        profile_name=self._profile, region_name=region
                    )
                else:
                    self._sessions[account_id] = assume_role_session(
                        role_arn=f"arn:aws:iam::{account_id}:role/{self._role_name}",
                        session_name="nettopo",
                        region_name=region,
                        profile_name=self._profile,
                        external_id=self._external_id,
                    )
            return read_only_client(
                service, session=self._sessions[account_id], region_name=region
            )


def _discover_regions(
    *, caller_account: str, caller_arn: str, client: Any
) -> tuple[str, ...] | None:
    bootstrap = bootstrap_region(caller_arn)
    if bootstrap is None:
        print(
            "오류: 호출자 파티션을 모른다 — --region 으로 리전을 명시하라",
            file=sys.stderr,
        )
        return None
    found = discover_regions(
        hub_account=caller_account, bootstrap=bootstrap, client=client
    )
    if found.failure is not None:
        print(
            f"⚠ 리전 자동 발견 실패 {found.asked} = {found.failure.status}"
            f" ({found.failure.reason.code}, {found.failure.reason.api})",
            file=sys.stderr,
        )
    if found.blind:
        print(
            "오류: 리전 자동 발견이 실패했다 — --region 으로 리전을 명시하라",
            file=sys.stderr,
        )
        return None
    print(
        f"리전 자동 발견({REGION_DISCOVERY_API}) — 활성 리전 {len(found.regions)}개"
        f"(부트스트랩 {found.asked}): {', '.join(found.regions)}"
    )
    return found.regions


def _discover(
    *,
    caller_account: str,
    regions: tuple[str, ...],
    client: Any,
    explicit: tuple[str, ...] = (),
) -> tuple[str, ...] | None:
    found = discover_accounts(
        hub_account=caller_account, regions=regions, client=client
    )
    for failure in found.failures:
        print(
            f"⚠ 계정 자동 발견 실패 {failure.region} = {failure.status}"
            f" ({failure.reason.code}, {failure.reason.api})",
            file=sys.stderr,
        )
    named = sorted(set(explicit) - {caller_account})
    if found.blind:
        if explicit:
            print(
                "⚠ 계정 자동 발견이 모든 리전에서 실패했다 — 명시한 계정 목록"
                f"({len(named)}개 + 허브)으로만 수집한다",
                file=sys.stderr,
            )
            return tuple(sorted({*explicit, caller_account}))
        print(
            "오류: 모든 리전에서 계정 자동 발견이 실패했다 — "
            "--accounts 로 계정을 명시하라",
            file=sys.stderr,
        )
        return None
    head = f"계정 자동 발견({DISCOVERY_API}) — 조회한 리전 {len(found.looked)}개 · "
    if explicit:
        added = sorted(set(found.discovered) - set(explicit))
        print(
            head
            + f"허브 1 + 명시 {len(named)}개 + 발견으로 더한 계정 {len(added)}개"
            + (f": {', '.join(_mask(a) for a in added)}" if added else "")
        )
    else:
        print(
            head
            + f"허브 1 + 멤버 {len(found.discovered)}개"
            + (
                f": {', '.join(_mask(a) for a in found.discovered)}"
                if found.discovered
                else ""
            )
        )
    return tuple(sorted({*found.accounts, *explicit}))


def _run_collect(args: argparse.Namespace) -> int:
    accounts = _parse_list(args.accounts) if args.accounts else ()
    bad = [a for a in accounts if not _ACCOUNT_ID.match(a)]
    if bad:
        print(
            f"오류: 계정ID 형식이 아니다(12자리 숫자) — {len(bad)}개", file=sys.stderr
        )
        return 1

    resource_types = _parse_list(args.resource_types)
    unsupported = [t for t in resource_types if t not in SUPPORTED_RESOURCE_TYPES]
    if unsupported:
        print(
            f"오류: 아직 수집할 수 없는 종류 {', '.join(unsupported)} — "
            f"지금 지원: {', '.join(SUPPORTED_RESOURCE_TYPES)}",
            file=sys.stderr,
        )
        return 1

    regions = tuple(dict.fromkeys(args.region or ()))

    try:
        caller_account, caller_arn = _caller_identity(args.profile)
    except (ClientError, BotoCoreError) as exc:
        print(
            f"오류: 호출자 신원 확인 실패({_error_code(exc)}) — "
            "프로필·SSO 세션을 확인하라",
            file=sys.stderr,
        )
        return 1
    cache = _SessionCache(
        profile=args.profile,
        role_name=args.role_name,
        caller_account=caller_account,
        external_id=args.external_id,
    )

    if not regions:
        found_regions = _discover_regions(
            caller_account=caller_account, caller_arn=caller_arn, client=cache.client
        )
        if found_regions is None:
            return 1
        regions = found_regions

    found = _discover(
        caller_account=caller_account,
        regions=regions,
        client=cache.client,
        explicit=accounts,
    )
    if found is None:
        return 1
    accounts = found

    scope = Scope(account_ids=accounts, regions=regions, resource_types=resource_types)
    snapshot = collect(scope, cache.client, account_names=True)
    text = to_json(snapshot)
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    document = snapshot.to_document()
    failed = [c for c in document["coverage"] if c["status"] != "ok"]
    print(f"스냅샷 → {out}")
    print(
        f"노드 {len(document['nodes'])} · 엣지 {len(document['edges'])} · "
        f"coverage {len(document['coverage'])}(실패 {len(failed)})"
    )
    print(f"해시(collected_at 제외) {digest_excluding_collected_at(text)}")
    for entry in failed:
        reason = entry["reason"] or {}
        print(
            f"⚠ {_mask(entry['account_id'])}/{entry['region']}/{entry['resource_type']}"
            f" = {entry['status']} ({reason.get('code')}, {reason.get('api')})",
            file=sys.stderr,
        )
    accounts_drawn = [n for n in document["nodes"] if n["type"] == "account"]
    print(
        f"계정 이름 {sum(1 for n in accounts_drawn if n['name'])}/{len(accounts_drawn)}개 읽음"
    )
    for node in accounts_drawn:
        if "name_unread" in node["attributes"]:
            print(
                f"⚠ 계정 이름 {_mask(node['account_id'])} = {node['attributes']['name_unread']}"
                f" ({node['attributes']['name_unread_api']}) — 화면은 계정 id 로 쓴다",
                file=sys.stderr,
            )
    return 0


def _read_json_object(
    path: Path, say: dict[str, str | None]
) -> tuple[dict[str, Any] | None, str | None]:
    """Read a JSON file and return (document, error description).

    Use say's messages for missing files, read errors, invalid JSON, and
    non-object input. Return no error description when its message is
    None.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        kind = "missing" if isinstance(exc, FileNotFoundError) else "unreadable"
        detail = f"{exc.strerror}"
    except json.JSONDecodeError as exc:
        kind, detail = "not_json", exc.msg
    else:
        if isinstance(document, dict):
            return document, None
        kind, detail = "not_object", ""
    template = say[kind]
    return None, None if template is None else template.format(detail=detail)


_TOPOLOGY_READ: dict[str, str | None] = {
    "missing": "오류: 구조 스냅샷을 읽을 수 없다 — {detail}",
    "unreadable": "오류: 구조 스냅샷을 읽을 수 없다 — {detail}",
    "not_json": "오류: 구조 스냅샷이 JSON 이 아니다 — {detail}",
    "not_object": "오류: 구조 스냅샷의 최상위가 객체가 아니다",
}
_PREVIOUS_READ: dict[str, str | None] = {
    "missing": "직전 관측 파일이 없다 — 이력을 이번 관측부터 새로 시작한다",
    "unreadable": "직전 관측을 읽을 수 없다({detail}) — 이력을 새로 시작한다",
    "not_json": "직전 관측이 JSON 이 아니다({detail}) — 이력을 새로 시작한다",
    "not_object": "직전 관측의 최상위가 객체가 아니다 — 이력을 새로 시작한다",
}
_PREVIOUS_TOPOLOGY_READ: dict[str, str | None] = {
    "missing": None,
    "unreadable": "직전 구조를 읽을 수 없다({detail}) — 이번 구조로만 모습을 찾는다",
    "not_json": "직전 구조가 JSON 이 아니다({detail}) — 이번 구조로만 모습을 찾는다",
    "not_object": "직전 구조의 최상위가 객체가 아니다 — 이번 구조로만 모습을 찾는다",
}


def _load_topology(path: Path) -> dict[str, Any] | None:
    """Read topology JSON and check required fields and the schema
    version.

    Print an error and return None if the schema major version differs
    or a
    required field is missing.
    """
    document, problem = _read_json_object(path, _TOPOLOGY_READ)
    if document is None:
        print(problem, file=sys.stderr)
        return None

    version = str(document.get("schema_version", ""))
    expected_major = TOPOLOGY_SCHEMA_VERSION.split(".")[0]
    if version.split(".")[0] != expected_major:
        print(
            f"오류: 구조 스냅샷 schema_version={version or '없음'} — 이 엔진은 "
            f"{expected_major}.x 만 읽는다(attributes 키 이름이 계약이다)",
            file=sys.stderr,
        )
        return None

    missing = [
        key
        for key in ("run", "scope", "nodes", "edges", "coverage")
        if key not in document
    ]
    if missing:
        print(
            f"오류: 구조 스냅샷에 필수 키가 없다 — {', '.join(missing)}",
            file=sys.stderr,
        )
        return None
    if "collected_at" not in document["run"]:
        print("오류: 구조 스냅샷에 run.collected_at 이 없다", file=sys.stderr)
        return None
    return document


def _load_previous(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return the previous observation JSON and any read error
    description.

    Return None for the document if unreadable. The caller then starts
    history
    with the new observation.
    """
    return _read_json_object(path, _PREVIOUS_READ)


def _load_previous_topology(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return the previous topology JSON and any read error description.

    history.py checks whether it matches the previous observation.
    """
    return _read_json_object(path, _PREVIOUS_TOPOLOGY_READ)


def _run_observe(args: argparse.Namespace) -> int:
    if args.metrics_profile is not None and not args.metrics and not args.utilization:
        print(
            "경고: --metrics-profile 을 줬지만 --metrics 가 없다 — 2층(지표)을 읽지 않는다. "
            "흐름 판정을 원하면 --metrics 를 같이 줘라",
            file=sys.stderr,
        )
    if args.flow_logs and not args.metrics:
        print(
            "오류: --flow-logs 는 --metrics 와 같이 줘야 한다 — 흐름 로그 근거는 2층과 같은 창에서만 판정한다",
            file=sys.stderr,
        )
        return 1
    if args.flow_logs and args.no_sources:
        print(
            "오류: --flow-logs 와 --no-sources 는 같이 줄 수 없다 — 읽을 흐름 로그 목록은 출처 탐지가 만든다",
            file=sys.stderr,
        )
        return 1
    if args.flow_logs_profile is not None and not args.flow_logs:
        print(
            "경고: --flow-logs-profile 을 줬지만 --flow-logs 가 없다 — 흐름 로그를 읽지 않는다",
            file=sys.stderr,
        )
    topology = _load_topology(args.topology)
    if topology is None:
        return 1

    previous, previous_warning = (
        _load_previous(args.previous) if args.previous is not None else (None, None)
    )
    loaded = [_load_previous_topology(path) for path in args.previous_topology or ()]
    previous_topologies = [doc for doc, _warning in loaded]
    previous_topology_warnings = [warning for _doc, warning in loaded]

    accounts = tuple(topology["scope"]["account_ids"])
    regions = tuple(topology["scope"]["regions"])
    if not accounts or not regions:
        print(
            "오류: 구조 스냅샷의 scope 가 비어 있다 — 관측할 (계정,리전)이 없다",
            file=sys.stderr,
        )
        return 1

    try:
        caller_account, _ = _caller_identity(args.profile)
    except (ClientError, BotoCoreError) as exc:
        print(
            f"오류: 호출자 신원 확인 실패({_error_code(exc)}) — "
            "프로필·SSO 세션을 확인하라",
            file=sys.stderr,
        )
        return 1
    cache = _SessionCache(
        profile=args.profile,
        role_name=args.role_name,
        caller_account=caller_account,
        external_id=args.external_id,
    )

    scope = Scope(
        account_ids=accounts,
        regions=regions,
        resource_types=OBSERVED_RESOURCE_TYPES,
    )
    snapshot = collect(
        scope,
        cache.client,
        with_state=True,
        structure_lambdas=lb_targets.structure_lambdas(topology),
    )
    metric_cache = (
        cache
        if args.metrics_profile is None
        else _SessionCache(
            profile=args.metrics_profile,
            role_name=args.role_name,
            caller_account=caller_account,
            external_id=args.external_id,
        )
    )
    metrics = (
        read_metrics(snapshot, metric_cache.client, at=snapshot.collected_at)
        if args.metrics
        else None
    )
    sources = (
        SourceScan.not_scanned()
        if args.no_sources
        else detect_sources(scope, cache.client, snapshot=snapshot)
    )
    flow_cache = (
        cache
        if args.flow_logs_profile is None
        else _SessionCache(
            profile=args.flow_logs_profile,
            role_name=args.role_name,
            caller_account=caller_account,
            external_id=args.external_id,
        )
    )
    reading = (
        read_flow_logs(
            snapshot, sources.flow_logs, flow_cache.client, at=snapshot.collected_at
        )
        if args.flow_logs
        else None
    )
    usage = (
        compute_utilization(
            topology,
            snapshot,
            metric_factory=metric_cache.client,
            quota_factory=cache.client,
            at=snapshot.collected_at,
            previous=previous,
        )
        if args.utilization
        else None
    )
    document, dropped = build_observation(
        snapshot,
        topology=topology,
        metrics=metrics,
        sources=sources,
        flow_logs=reading,
        utilization=usage.items if usage is not None else None,
        observed_at=snapshot.collected_at,
        tool_version=snapshot.tool_version,
    )
    report = apply_history(
        document,
        previous,
        tuple(t for t in (topology, *previous_topologies) if t is not None),
    )

    text = dumps(document)
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    counts: dict[str, int] = {}
    for observation in document["observations"]:
        counts[observation["status"]] = counts.get(observation["status"], 0) + 1
    partial = sum(1 for o in document["observations"] if o["partial"])
    failed = [c for c in document["coverage"] if c["status"] not in ("ok", "skipped")]

    print(f"관측 → {out}")
    print(
        f"대상 {len(document['observations'])} · "
        + " · ".join(f"{k} {counts.get(k, 0)}" for k in ("up", "down", "live", "none"))
        + f" · partial {partial}"
    )
    print(
        f"기준 구조 {document['run']['topology_collected_at']} · 관측 {document['run']['observed_at']}"
    )
    history = document["history"]
    if report.diffed:
        print(
            f"이력 새 변경 {report.new_rows} · 목록 {len(document['changes'])}"
            f"(승계 {report.carried} · 창 밖 {report.dropped} · 상한 {report.capped}"
            f" · 접힘 {report.collapsed}) · "
            f"기준 {history['baseline_at']} · 해상도 {history['resolution_seconds']}s"
            + (" · 오래된 기록 버림(보존 창 또는 상한)" if history["truncated"] else "")
        )
        if report.shaped or report.unshaped:
            print(
                f"이력 없어진 것 {report.shaped + report.unshaped} — 모습 적음 {report.shaped} · "
                f"모습 없음 {report.unshaped}"
            )
        print(
            f"이력 제외 {report.excluded} — 못 읽은 칸 {report.unread} · "
            f"2층 차이 {report.layer_changed} · 범위 밖 {report.out_of_scope} · "
            f"칸 미상 {report.unknown_cell} · 값 없는 연결 {report.unmeasured} · "
            f"삭제 중 {report.deleting}"
        )
    else:
        print(
            f"이력 기준선 = 이번 관측(비교할 직전 관측이 없다) · 목록 {len(document['changes'])} — "
            "다음 실행에 --previous 로 이 파일을 주면 변경이 쌓인다"
        )
    for warning in (previous_warning, *previous_topology_warnings, *report.warnings):
        if warning:
            print(f"⚠ 이력 {warning}", file=sys.stderr)
    if metrics is not None:
        flowing = quiet = unknown = unread = 0
        for observation in document["observations"]:
            evidence = observation["evidence"] or {}
            if any(
                r.get("api") == METRIC_API
                for r in observation.get("partial_reasons") or ()
            ):
                unread += 1
                continue
            if not evidence.get("metric"):
                continue
            if observation["status"] == "live":
                flowing += 1
            elif evidence.get("datapoints") is None:
                unknown += 1
            else:
                quiet += 1
        print(
            f"2층 지표 창 {metrics.window_seconds}s/{PERIOD_SECONDS}s — "
            f"흐름 {flowing} · 조용함 {quiet} · 확인 불가 {unknown} · 못 읽음 {unread}"
        )
        for warning in metrics.warnings:
            print(
                f"⚠ 2층 {_ACCOUNT_IN_TEXT.sub(lambda m: _mask(m.group()), warning)}",
                file=sys.stderr,
            )
    if usage is not None:
        measures = [m for item in usage.items for m in item["measures"]]
        sources_seen: dict[str, int] = {}
        for m in measures:
            if m.get("quota_code"):
                key = m["limit_source"] + ("(이유 있음)" if "limit_reason" in m else "")
                sources_seen[key] = sources_seen.get(key, 0) + 1
        print(
            f"사용량 대상 {len(usage.items)} · 측정 {len(measures)} — "
            f"못 읽음 {sum(1 for m in measures if 'unread' in m)} · "
            f"한도 ? {sum(1 for m in measures if m['limit'] is None and m['kind'] == 'count')} · "
            f"긴 묶음 {'새로 읽음' if usage.long_refreshed else '이월'} · 서비스 한도 "
            + (
                " · ".join(f"{k} {n}" for k, n in sorted(sources_seen.items()))
                or "없음"
            )
        )
    if reading is not None:
        covered = sum(1 for r in reading.resources if r.why_code == "duplicate_covered")
        unread_codes: dict[str, int] = {}
        for result in reading.resources:
            if not result.used and result.why_code not in (None, "duplicate_covered"):
                unread_codes[result.why_code] = unread_codes.get(result.why_code, 0) + 1
        by_subnet = sum(len(o.get("by_subnet") or ()) for o in document["observations"])
        print(
            f"흐름 로그 {len(reading.resources)}개 — 사용 {sum(1 for r in reading.resources if r.used)} · "
            f"못 읽음 {sum(unread_codes.values())} · 중복으로 덮임 {covered} · "
            f"왕복 근거 {len(reading.proofs)}(서브넷 선 {by_subnet}) · 호출 {reading.calls}"
        )
        if unread_codes:
            print(
                "⚠ 흐름 로그 못 읽은 이유 — "
                + " · ".join(f"{code} {n}" for code, n in sorted(unread_codes.items())),
                file=sys.stderr,
            )
    if args.no_sources:
        print(
            "출처 탐지 건너뜀(--no-sources) — 산출물의 sources 는 빈 배열이고 알약이 안 나온다"
        )
    else:
        rows = document["sources"]
        absent = [
            r
            for r in rows
            if r["state"] == "off"
            and (r.get("scope") or {"total_ids": 1})["total_ids"] == 0
        ]
        on = [r for r in rows if r["state"] == "on"]
        print(
            f"출처 {len(rows)}종 — 켜짐 {len(on)}(우리가 씀 {sum(1 for r in on if r['used'])}) · "
            f"꺼짐 {sum(1 for r in rows if r['state'] == 'off') - len(absent)} · "
            f"확인 못함 {sum(1 for r in rows if r['state'] == 'unknown')} · "
            f"해당 자원 없음 {len(absent)}"
        )
        if not rows:
            print(
                "⚠ 출처 0종 — 탐지가 아무것도 돌려주지 않았다(개발 결함일 수 있다)",
                file=sys.stderr,
            )
        for row in rows:
            if row["state"] == "unknown":
                print(
                    f"⚠ 출처 {row['kind']} = 확인 못함 ({_ACCOUNT_IN_TEXT.sub(lambda m: _mask(m.group()), str(row.get('why')))})",
                    file=sys.stderr,
                )
    if dropped:
        print(
            f"⚠ 구조 스냅샷에 없어 버린 대상 {len(dropped)}개 — 구조를 다시 수집하라",
            file=sys.stderr,
        )
    tally: dict[tuple[str, str], list[int]] = {}
    for row in document["coverage"]:
        if row["layer"] != "describe":
            continue
        counted = tally.setdefault((row["account_id"], row["region"] or "-"), [0, 0])
        counted[1] += 1
        if row["status"] == "ok":
            counted[0] += 1
    for (account_id, region), (ok, total) in sorted(tally.items()):
        if ok != total:
            print(
                f"⚠ {_mask(account_id)}/{region} — {total}종 중 {ok}종만 읽었다",
                file=sys.stderr,
            )
    for entry in failed:
        reason = entry["reason"] or {}
        print(
            f"⚠ {_mask(entry['account_id'])}/{entry['region']}"
            f"/{entry['resource_type']}/{entry['layer']}"
            f" = {entry['status']} ({reason.get('code')}, {reason.get('api')})",
            file=sys.stderr,
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "collect":
            return _run_collect(args)
        if args.command == "observe":
            return _run_observe(args)
    except ReadOnlyViolation as exc:
        print(f"읽기 전용 위반: {exc}", file=sys.stderr)
        return 2
    print(f"오류: 알 수 없는 명령 {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
