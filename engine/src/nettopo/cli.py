"""`nettopo` CLI — 1차 산출물은 로컬 파일이다(웹 껍데기는 그다음).

🔴 `--region` 에 기본값을 두지 않는다. 셸 기본 리전(`AWS_REGION`)에 의존한 산출물은
재현되지 않고, 이 맥의 기본값(`us-west-2`)은 실제 자원이 있는 리전과 다르다.
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
# 자유 문장(2층 경고) 안에 박힌 계정ID 를 찾아 가린다 — 대상 id 가 `<종류>/<계정>/<리전>/…` 다.
_ACCOUNT_IN_TEXT = re.compile(r"[0-9]{12}")
DEFAULT_ROLE_NAME = "NetworkReadOnlyRole"

# 전역 STS 엔드포인트(`sts.amazonaws.com`). 수집 리전과 독립적이어야 한다 — `_caller_account` 참고.
STS_IDENTITY_REGION = "aws-global"


def _mask(account_id: str) -> str:
    """계정ID 를 화면·로그에 그대로 찍지 않는다(계정 식별자 규칙)."""
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

    # 🔴 `observe` 에는 `--region`·`--accounts`·`--resource-types` 가 **없다.** scope 는 구조
    # 문서에서 읽는다 — 두 문서의 scope 가 갈라지면 관측이 없는 리소스에 상태가 붙거나 그 반대가
    # 되고, `run.topology_collected_at`(스키마 required)이 가리키는 구조와 어긋난다.
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
    # 🔴 기본 off 다(ADR 0006 D4) — 2층은 권한(`cloudwatch:GetMetricData`)이 더 필요하고, 켜지
    # 않았을 때의 「확인 불가」는 **실패가 아니라 「안 켰다」**로 문서에 남는다(coverage `skipped`).
    observe_parser.add_argument(
        "--metrics",
        action="store_true",
        help="2층(CloudWatch 지표)도 읽어 흐름이 있는 대상을 live 로 올린다(기본 off)",
    )
    # 🔴 이쪽은 기본 **on** 이다 — 2층과 반대다. 전부 `Describe*`/`List*` 라 무료이고 로그를 한
    # 줄도 읽지 않는데(ADR 0009 비용), 이것을 끄면 화면이 「흐름 로그가 꺼져 있어 지표 기반의
    # 결과만 나왔다」를 **말할 근거를 잃는다**. 근거가 없으면 알약을 띄우지 않는 것이 맞고, 그러면
    # 사용자는 「꺼져 있다」와 「탐지를 안 했다」를 구별할 수 없다.
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
    # 🔴 기본 off 이고 **2층을 켠 관측에서만** 받는다(ADR 0008 「D4b 개정」). 흐름 로그의 왕복 기록은 2층과 같은 15분
    # 창의 근거라 2층 없이 켜면 창이 없고, 출처 탐지를 끄면 읽을 로그 목록이 없다 — 둘 다 조용히 무시하지 않고 거부한다.
    # 권한은 고객 계정마다 따로 연다(`docs/required-permissions.md` §1-3 · 스택 파라미터 FlowLogReading).
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
    # 🔴 이력의 입력은 **직전 관측 문서 하나**다(ADR 0004 D4). 없으면 기준선이 이번 관측이 되고
    # 그것은 오류가 아니다 — 주기 배치는 매번 같은 인자로 돌고 첫 실행에는 파일이 없다. 그래서
    # `--out` 과 같은 경로를 줘도 된다(읽기가 쓰기보다 먼저 일어난다 — `_run_observe` 참고).
    observe_parser.add_argument(
        "--previous",
        type=Path,
        help=(
            "직전 관측 JSON — 두 문서를 비교해 changes[](구성·상태 변경 이력)를 채운다. "
            "없거나 읽을 수 없으면 경고를 내고 이력을 이번 관측부터 새로 시작한다"
        ),
    )
    # 🔴 없어진 것의 **모습**(`change.was`)은 직전 관측이 그려진 구조에만 있다. 대개 그 구조가
    # `--topology` 와 같지만(삭제는 5분 관측이 매시 구조 수집보다 먼저 본다), 그 사이에 구조가 새로
    # 수집됐으면 옛 구조가 필요하다. 없어도 관측은 그대로 유효하다 — 그 줄에 모습이 안 붙을 뿐이다.
    # 🆕 여러 번 줄 수 있다(QA B 2차 N-05 · 2026-10-17): 실시간을 끈 사이 구조가 두 번 넘게 바뀌면 직전 관측의 구조는
    # `structure-previous.json` 에도 없다 — 상태 함수가 누적 스냅샷에서 그 구조를 찾아 **한 장 더** 넘긴다. 후보는 준 순서대로
    # `--topology` 뒤에 붙고, 짝(`history._paired_structure`)이 맞는 첫 장만 쓰인다(결정론 — 같은 인자면 같은 바이트).
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
    """호출자 (계정ID, ARN). `--region` 과 **무관하게** 전역 STS 엔드포인트에서 확인한다.

    ARN 은 **파티션을 읽기 위해서만** 쓰고 화면·로그에 찍지 않는다(계정ID·역할명이 들어 있다).

    🔴 리전별 STS 엔드포인트를 쓰면 계정이 켜지 않은 옵트인 리전에서 토큰이 거부된다
    (실측 2026-09-14: `--region ap-east-1` → `InvalidClientTokenId`). 수집 대상 리전
    선택이 신원 확인을 깨뜨려서는 안 된다. GetCallerIdentity 는 `Get*` 이므로 읽기
    allowlist 를 그대로 통과한다.
    """
    client = read_only_client(
        "sts", profile_name=profile, region_name=STS_IDENTITY_REGION
    )
    identity = client.get_caller_identity()
    return str(identity["Account"]), str(identity.get("Arn") or "")


class _SessionCache:
    """**계정당 세션 하나.** 같은 계정에 대해 assume 를 반복하지 않는다.

    🔴 **이 클래스가 수집 병렬화의 유일한 경합점이다**(ADR 0004 D2-5 가 지목한 선행 조건).
    `collect._fetch_pairs` 가 (계정,리전) 쌍을 여러 스레드에서 돌리므로 이 메서드가 **동시에**
    불린다. 잠금 없이 두면 두 가지가 깨진다 — ① `self._sessions` 의 read-modify-write 가 겹쳐
    같은 계정을 두 번 assume 하고(STS 요청이 늘고 throttle 에 가까워진다), ② botocore 는
    **클라이언트 생성**이 스레드 안전하지 않다(만들어진 뒤의 호출은 안전하다 — 그 사실이
    `_fetch` 2단계의 근거다).

    🔵 **잠금이 `assume_role_session` 호출까지 덮는 것은 의도다.** 동시에 assume 하면 더 빠르겠지만
    그러면 위 ①이 되살아난다. 그 뒤의 목록 호출 전부는 병렬로 남는다.

    🔴 **키가 계정인 것은 실측으로 정해졌다**(ADR 0004 **D2-6/D2-7**, 2026-09-24). 전에는 키가
    `(계정,리전)`이어서 크로스계정 쌍이 **쌍마다** assume 했고, 그 호출이 이 잠금 안에 있어 쌍
    전부가 한 줄로 섰다 — 상태 단 쌍당 비용이 **자기 계정 0.70s 대 다른 계정 2.28s** 로 3.3배
    갈린 원인이 그것이다. 30계정 × 3리전이면 assume 90번이 직렬이다. 계정으로 줄이면 **30번**이다.

    🔵 **세션의 리전이 무엇이든 수집 리전은 흔들리지 않는다.** 아래 `client()` 가 클라이언트마다
    `region_name=region` 을 **따로** 넘기기 때문이다(그것을 빼면 전 리전이 한 리전으로 접히고
    화면이 다른 리전의 자원을 그 리전 것으로 그린다 — 브레이크 `세션리전누출`).

    🔴 **세션을 만든 리전은 `sts:AssumeRole` 을 부른 엔드포인트만 정한다.** 그 리전은 그 계정을
    가장 먼저 요구한 쌍이 정하므로 스레드 순서에 달렸다 — 산출물 바이트에는 영향이 없다(불변식 ②는
    **결과**에 대한 규칙이다). 단 엔드포인트가 **리전 엔드포인트여야** 한다: 글로벌
    엔드포인트(`sts.amazonaws.com`)가 주는 v1 토큰은 opt-in 리전에서 거부된다. botocore 1.43 의
    기본값이 `regional` 이라 지금은 성립한다(실측 2026-09-24: `sts.<리전>.amazonaws.com`).
    `AWS_STS_REGIONAL_ENDPOINTS=legacy` 를 켜면 깨진다 — 키가 쌍이던 시절에도 깨졌으므로
    이 변경이 만든 문제는 아니다.
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
                    # 크로스계정 경로. 대상 계정에 역할이 배포되기 전에는 실행되지 않는다.
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
    """리전 자동 발견 + 보고. 못 정했으면 `None`(= 호출자가 멈춘다)."""
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
    # 🔴 수집 호출은 리전 × 계정 × 종류로 늘어난다. 범위를 조용히 넓히지 않도록 개수를 먼저 찍는다.
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
    """계정 자동 발견 + 보고 → 수집 대상(명시 ∪ 발견 ∪ 허브, 정렬). 멈춰야 하면 `None`.

    `explicit` = `--accounts` 로 받은 **추가 목록**(ADR 0005). 멈추는 것은 목록 없이 아무것도
    못 본 경우뿐이다 — 목록이 있으면 사람이 범위를 적어 줬으므로 그 범위로 진행한다.
    """
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
            # 🔴 사용자 결정 2026-10-20(C-2) — 명시 목록이 있으면 멈추지 않는다. 사람이 적은 범위가
            # 있으니 「허브 하나뿐」이라는 거짓말은 생기지 않는다. 다만 TGW 가 아는 계정을 이번에는
            # 확인하지 못했다는 사실은 숨기지 않는다(불변식 ④) — 그래서 경고를 남긴다.
            print(
                "⚠ 계정 자동 발견이 모든 리전에서 실패했다 — 명시한 계정 목록"
                f"({len(named)}개 + 허브)으로만 수집한다",
                file=sys.stderr,
            )
            return tuple(sorted({*explicit, caller_account}))
        # 🔴 「멤버가 없다」와 구별할 수 없는 상태다. 허브 하나만 수집하면 스냅샷이
        # 「이게 전부다」라고 거짓말을 한다(불변식 ④) — 그래서 진행하지 않는다.
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
        # 신원 확인 실패는 부분 실패가 아니다(어느 계정으로 도는지 모른 채 수집할 수 없다).
        # 메시지 본문은 찍지 않는다 — AccessDenied 응답에는 호출자 ARN 이 들어 있다.
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

    # 🔴 발견은 **신원 확인 다음**이다 — 허브가 어느 계정인지 모르면 「멤버」를 가릴 수 없고,
    # 세션 캐시를 그대로 써야 읽기 전용 가드·assume 경로가 수집과 같아진다.
    # 🔴 리전이 계정보다 **먼저**다 — 계정 발견이 리전을 순회한다.
    if not regions:
        found_regions = _discover_regions(
            caller_account=caller_account, caller_arn=caller_arn, client=cache.client
        )
        if found_regions is None:
            return 1
        regions = found_regions

    # 🔴 명시 목록에도 허브(호출자) 계정은 **항상** 들어간다 — 자동 발견(`discover_accounts`)과 같은
    # 규칙이다. 빠지면 소스 계정 하나를 더하려다 허브 리소스가 통째로 사라진다(실측 2026-10-14:
    # 스택 A `TargetAccounts=<소스 계정>` 만 주고 수집 → 노드 201 → 20). 스택·런북은 이 파라미터를
    # 「계정을 **더해** 갱신한다」고 적어 왔으므로(ADR 0005) 교체 목록이 아니라 추가 목록이다.
    # 🔴 사용자 결정 2026-10-20(C-2): 그래서 목록을 줘도 **발견은 돈다**(합집합 = 명시 ∪ 발견 ∪ 허브).
    # 목록이 발견을 끄면 계정 하나를 더하려던 사람이 TGW 가 아는 다른 멤버를 조용히 잃는다 —
    # 「더한다」고 적은 파라미터가 실제로는 「바꾼다」로 동작하는 셈이다.
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
    # 계정 이름(사용자 결정 2026-10-28) — 구조 수집만 읽는다. 관측(`_run_observe`)은 켜지 않는다(`test_cli.py` 가 잰다).
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
    # 🔴 이름 자체는 찍지 않는다 — 계정 이름이 이메일 주소인 계정이 있다(실측 2026-10-28). 개수와 못 읽은 계정(가린 id)만.
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
    """파일 → JSON 객체 하나. 실패는 `(None, 문장)` 이고 문장은 호출자가 `say` 로 준다.

    `say` 의 키 = `missing`(파일 없음) · `unreadable`(그 밖의 OS 오류) · `not_json` · `not_object`.
    값의 `{detail}` 자리에 원인이 들어가고, 값이 `None` 이면 `(None, None)` 이다(조용한 정상 경로).
    🔴 예외 사다리는 **한 벌**만 둔다(#32 리뷰 5) — 세 곳이 각자 들고 있으면 한 곳에만 분기가 붙는다.
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
    # 첫 실행의 정상 경로다. 경고는 내되 「고칠 것이 있다」고 읽히지 않게 문장을 고른다.
    "missing": "직전 관측 파일이 없다 — 이력을 이번 관측부터 새로 시작한다",
    "unreadable": "직전 관측을 읽을 수 없다({detail}) — 이력을 새로 시작한다",
    "not_json": "직전 관측이 JSON 이 아니다({detail}) — 이력을 새로 시작한다",
    "not_object": "직전 관측의 최상위가 객체가 아니다 — 이력을 새로 시작한다",
}
_PREVIOUS_TOPOLOGY_READ: dict[str, str | None] = {
    # 스택 A 의 첫 구조 수집 뒤에는 이 파일이 없다 — 정상 경로다.
    "missing": None,
    "unreadable": "직전 구조를 읽을 수 없다({detail}) — 이번 구조로만 모습을 찾는다",
    "not_json": "직전 구조가 JSON 이 아니다({detail}) — 이번 구조로만 모습을 찾는다",
    "not_object": "직전 구조의 최상위가 객체가 아니다 — 이번 구조로만 모습을 찾는다",
}


def _load_topology(path: Path) -> dict[str, Any] | None:
    """구조 문서를 읽고 **관측이 의존하는 필드가 있는지** 확인한다.

    🔴 메이저 버전이 다르면 멈춘다. 관측의 판정은 `attributes` 의 키 이름(`state`·`blackhole`)에
    달려 있고 그것은 구조 계약의 일부다 — 버전이 올라가면 키가 바뀌었을 수 있다. 조용히 읽으면
    「전부 판정 불가」나 「전부 정상」 중 하나가 나오는데, 둘 다 사람을 오판하게 한다(불변식 ④).
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
        # 이 값이 `run.topology_collected_at` 이 된다 — 없으면 관측 문서가 스키마를 못 만족한다.
        print("오류: 구조 스냅샷에 run.collected_at 이 없다", file=sys.stderr)
        return None
    return document


def _load_previous(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """직전 관측 문서를 읽는다 — `(문서, 경고)`. 실패는 **경고이고 오류가 아니다.**

    🔴 `_load_topology` 와 다르게 `rc=1` 로 멈추지 않는다. 구조 문서가 없으면 관측할 대상 자체를
    모르지만, 직전 관측이 없으면 「비교할 것이 없다」일 뿐 이번 관측은 그대로 유효하다. 주기
    배치는 매번 같은 인자로 도는데 첫 실행에는 이 파일이 없으므로, 여기서 멈추면 첫 실행이 항상
    실패하고 상태 화면이 영원히 비어 있게 된다(불변식 ④).

    내용 검증(버전·필수 키·시각 순서)은 `history._refusal` 이 한다 — 여기서는 파일·JSON 만 본다.
    """
    return _read_json_object(path, _PREVIOUS_READ)


def _load_previous_topology(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """직전 구조 문서 — `(문서, 경고)`. `_load_previous` 와 같은 이유로 실패는 **경고**다.

    내용은 `history._paired_structure` 가 짝(내용 지문·수집 시각)으로 거른다 — 짝이 안 맞는
    구조는 어차피 쓰지 않으므로 여기서는 파일·JSON·최상위 모양만 본다.
    """
    return _read_json_object(path, _PREVIOUS_TOPOLOGY_READ)


def _run_observe(args: argparse.Namespace) -> int:
    # 🔴 `--metrics-profile` 만 주고 `--metrics` 를 잊은 경우 — **조용히 무시하면 안 된다.**
    # 실측 2026-09-24: 이 조합으로 2층을 두 번 재고 1층 숫자를 받았는데 화면·로그 어디에도
    # 「안 켰다」가 없었다. 주기 배치에서는 지표 자격증명을 넣어 둔 운영자가 매 실행 「확인 불가」만
    # 보게 되고, 의심할 곳이 권한밖에 없다 — `test_metrics_layer_is_off_unless_asked` 가 지키는
    # 「안 켠 것은 실패가 아니다」의 반대편 구멍이다(안 켠 것을 **안 켰다고 말하지 않는다**).
    # 죽이지는 않는다: 1층 관측은 완전히 유효하고 상태 화면은 서 있어야 한다(불변식 ④).
    # 사용률도 이 프로필로 지표를 읽는다(아래 `metric_cache`) — `--utilization` 만 준 것은 잊은 것이 아니다.
    if args.metrics_profile is not None and not args.metrics and not args.utilization:
        print(
            "경고: --metrics-profile 을 줬지만 --metrics 가 없다 — 2층(지표)을 읽지 않는다. "
            "흐름 판정을 원하면 --metrics 를 같이 줘라",
            file=sys.stderr,
        )
    # 🔴 흐름 로그는 **경고가 아니라 거부**다 — 위 경고와 다르다. 2층 없이 켜면 판독기가 쓸 15분 창이 문서에 없고,
    # 출처 탐지를 끄면 읽을 로그 목록이 없다. 어느 쪽이든 조용히 진행하면 「켰는데 초록이 하나도 없다」가 되고
    # 사람은 그것을 「기록 없음」으로 읽는다. AWS 를 부르기 전에 멈춘다.
    if args.flow_logs and not args.metrics:
        print(
            "오류: --flow-logs 는 --metrics 와 같이 줘야 한다 — 흐름 로그 근거는 2층과 같은 창에서만 판정한다",
            file=sys.stderr,
        )
        return 1
    # 사용률은 `--metrics` 없이도 돈다(스키마 1.5.0 · ADR 0011 D7 개정 2026-10-06 — 「사용률만 보고 싶은 고객」).
    # `compute_utilization` 은 2층 판독과 따로 제 지표를 읽는다 — 아래 `metric_cache` 를 같이 쓸 뿐이다.
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

    # 🔴 **AWS 를 부르기 전에** 읽는다. 순서가 계약이다: `--previous` 와 `--out` 에 같은 경로를
    # 주는 것이 주기 배치의 자연스러운 사용법이고(「같은 파일을 갱신한다」), 쓰기가 먼저 일어나면
    # 이력이 자기 자신을 기준으로 삼아 매 실행 변경 0건이 된다.
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

    # 🔴 발견을 하지 않는다 — scope 는 구조 문서가 준 것 그대로다. 여기서 다시 발견하면
    # 그 사이에 늘어난 계정·리전이 구조에 없는 대상을 만들고, 전부 버려진다(dropped).
    scope = Scope(
        account_ids=accounts,
        regions=regions,
        resource_types=OBSERVED_RESOURCE_TYPES,
    )
    # LB → VPC Lambda(공유 ENI) 선은 상태 단이 함수를 읽지 않아도 구조 문서의 함수로 다시 잇는다(#152 · ADR 0006 결정 15 · AWS 호출 0)
    snapshot = collect(
        scope,
        cache.client,
        with_state=True,
        structure_lambdas=lb_targets.structure_lambdas(topology),
    )
    # 🔴 개발 계정에서는 1층·2층 프로필이 **일부러** 분리돼 있다(`docs/required-permissions.md`
    # §1-2: 구조 읽기 프로필은 지표를 못 읽고 지표 읽기 프로필은 1층을 못 읽는다 — 대조군 4개로 검증).
    # 그래서 여기서 세션을 갈라야 두 층을 한 번에 읽을 수 있다. 고객 계정은 스택 A 의 실행 role
    # 하나가 둘 다 갖고 있으므로 이 값을 주지 않는다(기본 = 1층과 같은 프로필).
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
    # 🔴 창의 끝은 **스냅샷의 시각**이다(`metrics._window`) — 여기서 시계를 또 읽으면 불변식 ②의
    # wall-clock 예외가 둘이 된다. 1층이 끝난 뒤에 부르므로 2층은 이미 읽은 대상만 묻는다.
    metrics = (
        read_metrics(snapshot, metric_cache.client, at=snapshot.collected_at)
        if args.metrics
        else None
    )
    # 🔴 1층과 **같은 프로필**로 읽는다 — 전부 `Describe*`/`List*` 이고 2층(지표 읽기 프로필)의
    # 권한 축과 무관하다. 실패는 kind 단위 `unknown` 으로 남고 관측을 죽이지 않는다(불변식 ④).
    sources = (
        SourceScan.not_scanned()
        if args.no_sources
        else detect_sources(scope, cache.client, snapshot=snapshot)
    )
    # 🔴 흐름 로그는 기본 **1층과 같은 프로필**이다 — 개발 계정의 읽기 전용 권한 세트에 흐름 로그 읽기가 있다
    # (`docs/required-permissions.md` §1-3). 고객 계정은 스택 A·B 역할이 파라미터로 받는다.
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
    # 🔴 지표는 2층 세션 · 서비스 한도는 **1층 세션**으로 읽는다(스택 A 상태 역할 · 스택 B 멤버 역할에 붙인 권한 —
    # `docs/required-permissions.md` §1-5). 긴 묶음 · 한도는 직전 문서(`--previous`)에서 1시간 동안 이월한다(ADR 0011 D6).
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
        # 🔴 관측 시각은 수집기의 시계를 그대로 쓴다 — `collect.utc_now()` 가 불변식 ②의 유일한
        # wall-clock 예외이고, 여기서 따로 시각을 읽으면 예외가 둘이 된다.
        observed_at=snapshot.collected_at,
        tool_version=snapshot.tool_version,
    )
    # 🔴 이력은 AWS 를 다시 부르지 않는다 — 입력은 방금 만든 문서와 직전 문서 두 개뿐이다.
    # 그래서 같은 두 문서로 몇 번 돌려도 바이트가 같다(불변식 ②).
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
        # 🔴 목록의 수를 **갈라서** 말한다. 「목록 N건」만 찍으면 사람은 그것을 「이 구간에 일어난
        # 일 전부」로 읽는다. 실제로는 이번에 새로 본 것 · 지난 목록에서 이어온 것 · 보존 창을
        # 넘어 떨어진 것 · 플래핑으로 접힌 것이 섞여 있고, 합치면 「기록을 버렸다」가 「변경이
        # 없었다」처럼 보인다(2층의 네 수를 가른 것과 같은 이유).
        print(
            f"이력 새 변경 {report.new_rows} · 목록 {len(document['changes'])}"
            f"(승계 {report.carried} · 창 밖 {report.dropped} · 상한 {report.capped}"
            f" · 접힘 {report.collapsed}) · "
            f"기준 {history['baseline_at']} · 해상도 {history['resolution_seconds']}s"
            + (" · 오래된 기록 버림(보존 창 또는 상한)" if history["truncated"] else "")
        )
        if report.shaped or report.unshaped:
            # 🔴 모습을 못 적은 수를 **따로** 말한다 — 화면에서는 그 줄이 그림 없이 글자로만 나온다.
            print(
                f"이력 없어진 것 {report.shaped + report.unshaped} — 모습 적음 {report.shaped} · "
                f"모습 없음 {report.unshaped}"
            )
        # 🔴 **0 도 찍는다.** 이 줄이 없으면 「권한이 사라져 조용한 화면」과 「정말 변경이 없는
        # 화면」이 같아진다. 못 읽은 칸을 `resource_removed` 로 적는 것보다는 낫지만, 세지 않으면
        # 사람은 그 침묵을 「이상 없음」으로 읽는다(S3 에서 실계정이 잡은 결함과 같은 양식).
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
        # 🔴 네 수를 **따로** 말한다. 합치면 「지표가 조용하다」와 「지표를 못 읽었다」와 「이
        # 종류엔 지표가 없다」가 한 덩어리가 되고, 사람은 전부 「흐름 없음」으로 읽는다
        # (§O7-1 함정 1: 차원 상수가 틀려도 같은 화면이 나온다).
        flowing = quiet = unknown = unread = 0
        for observation in document["observations"]:
            evidence = observation["evidence"] or {}
            if any(
                r.get("api") == METRIC_API
                for r in observation.get("partial_reasons") or ()
            ):
                # 🔴 실측 2026-09-23: `--metrics-profile` 없이 돌리면 21개가 전부 이 칸인데 세
                # 수는 모두 0 이었다 — 화면이 「2층에 볼 것이 없었다」처럼 보인다. 읽지 못한 것은
                # 별도로 센다(사유 줄은 아래 coverage 경고가 종류별로 한 번 더 말한다).
                # 🔴 「권한」만 여기 오는 게 아니다 — 1층이 차원 값을 안 줘서 **묻지도 못한** 대상
                # (= 우리 쪽 결함)도 같은 칸이다. 그래서 이름이 「막힘」이면 거짓말이 된다.
                unread += 1
                continue
            if not evidence.get("metric"):
                continue  # 지표가 없는 종류·모양(§O7 의 「없음」) — 2층의 몫이 아니다
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
            # 경고에는 대상 id 가 들어가고 그 안에 계정ID 가 있다(§0 — 화면에 그대로 찍지 않는다).
            print(
                f"⚠ 2층 {_ACCOUNT_IN_TEXT.sub(lambda m: _mask(m.group()), warning)}",
                file=sys.stderr,
            )
    if usage is not None:
        # 🔴 「한도를 계정에서 읽었다」 · 「기본값으로 나눴다」 · 「못 읽었다」를 갈라서 말한다 — 합치면 권한이 없는 계정이
        # 「계정 한도로 나눈 %」처럼 보인다(D5 · 188 사람 프로필은 이 권한이 없다).
        measures = [m for item in usage.items for m in item["measures"]]
        sources_seen: dict[str, int] = {}
        for m in measures:
            if m.get("quota_code"):
                key = m["limit_source"] + ("(이유 있음)" if "limit_reason" in m else "")
                sources_seen[key] = sources_seen.get(key, 0) + 1
        print(
            f"사용량 대상 {len(usage.items)} · 측정 {len(measures)} — "
            f"못 읽음 {sum(1 for m in measures if 'unread' in m)} · "
            # 🔁 2026-10-26 사용량 — 통신량은 한도를 두지 않는다 → 「한도 ?」는 개수 한도 측정에서만 센다
            f"한도 ? {sum(1 for m in measures if m['limit'] is None and m['kind'] == 'count')} · "
            f"긴 묶음 {'새로 읽음' if usage.long_refreshed else '이월'} · 서비스 한도 "
            + (
                " · ".join(f"{k} {n}" for k, n in sorted(sources_seen.items()))
                or "없음"
            )
        )
    if reading is not None:
        # 🔴 「못 읽음」과 「중복으로 덮임」을 가른다 — 덮인 로그는 같은 VPC 를 다른 로그가 이미 읽은 것이라 고객이 할
        # 일이 없다. 합치면 로그 두 개를 켠 고객에게 「하나를 못 읽었다」고 말한다. 로그 id·목적지는 찍지 않는다.
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
    # 🔴 옵션형 근거(ADR 0009 D2) — **네 수를 갈라서** 찍는다. 합치면 「고객이 안 켰다」와 「우리가
    # 탐지를 못 했다」와 「그 자원이 아예 없다」가 한 덩어리가 되고, 사람은 전부 「꺼져 있으니
    # 켜라고 하자」로 읽는다(화면의 알약·배너와 같은 규율 — 검사 100).
    if args.no_sources:
        print(
            "출처 탐지 건너뜀(--no-sources) — 산출물의 sources 는 빈 배열이고 알약이 안 나온다"
        )
    else:
        rows = document["sources"]
        # 분모 0 = 「해당 자원이 없다」다. 꺼짐과 같이 세면 **켤 수 없는 것을 켜라**고 말하게 된다.
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
            # 🔴 침묵을 「전부 꺼짐」으로 읽으면 안 된다 — 탐지가 통째로 안 돈 것일 수 있다.
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
        # 🔴 삼키지 마라 — 구조보다 새 리소스가 생겼다는 사실 자체가 정보다(S4 의 재료).
        print(
            f"⚠ 구조 스냅샷에 없어 버린 대상 {len(dropped)}개 — 구조를 다시 수집하라",
            file=sys.stderr,
        )
    # 🔴 O10 의 사람 몫 — 「일부만 읽었다」를 자리마다 **한 줄로** 먼저 말한다. 아래 종류별 경고만
    # 나열하면 사람은 「이 계정은 못 읽었다」로 읽고, 실제로 읽힌 것까지 다시 확인하러 간다.
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
        # 🔴 종류를 빼면 「그 자리의 1층이 통째로 막혔다」로 읽힌다 — O10 이 없애려던 바로 그
        # 오독이다(실측 2026-09-23: 한 계정에서 6줄이 전부 `…/us-east-1/describe = denied` 로
        # 나왔고, 실제로는 같은 자리에서 4종이 읽혔다).
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
        # 가드가 발동했다는 것은 수집 코드에 쓰기 호출이 섞였다는 뜻이다 — 부분 실패가 아니다.
        print(f"읽기 전용 위반: {exc}", file=sys.stderr)
        return 2
    print(f"오류: 알 수 없는 명령 {args.command}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
