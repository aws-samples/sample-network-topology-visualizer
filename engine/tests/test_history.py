"""이력 축(`nettopo observe --previous`) — **「변경이 아닌 것」을 변경으로 적지 않는가.**

🔴 이 파일의 중심은 「변경을 잡는가」가 아니다. 집합 차분은 3줄이면 되고 그것은 거의 언제나
「잡는다」. 문제는 **반대쪽**이다 — 권한이 사라지거나, 2층 토글이 꺼지거나, 범위에서 리전이
빠지면 단순 차분은 그것을 「자원이 사라졌다」·「흐름이 멈췄다」로 적는다. 사람이 화면에서 보는
것은 「자원 12개 삭제됨」이고, 실제로는 아무것도 지워지지 않았다.

그래서 각 가드마다 **대조군을 짝으로 둔다**: 가드가 억제해야 하는 경우와, 억제하면 안 되는
경우(진짜 삭제·진짜 흐름 정지)를 같이 잰다. 대조군이 없으면 「전부 억제」하는 구현도 통과한다
(실패할 수 없는 어서션은 통과가 아니라 미측정).
"""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import (
    ACCOUNT,
    EGRESS_NAT,
    EGRESS_SUBNET,
    EGRESS_VPC,
    INSTANCE_RESERVATIONS,
    NAT_GATEWAYS,
    REGION,
    VPC_ENDPOINTS,
    FakeAws,
    FakeCloudWatch,
    client_error,
    factory,
    factory_by_service,
    series_key,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.history import apply_history, target_cells
from nettopo.metrics import read_metrics
from nettopo.model import GLOBAL, Scope, node_id
from nettopo.serialize import content_digest
from nettopo.observe import (
    DEFAULT_FLAP_COLLAPSE_THRESHOLD,
    DEFAULT_MAX_CHANGES,
    DEFAULT_RESOLUTION_SECONDS,
    DEFAULT_RETENTION_SECONDS,
    OBSERVED_RESOURCE_TYPES,
    build_observation,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "observation.schema.json").read_text(encoding="utf-8")
)
TOPOLOGY_SCHEMA = json.loads(
    (ROOT / "schema" / "topology.schema.json").read_text(encoding="utf-8")
)

# 구조 스냅샷은 **모든 관측보다 앞**이다(실제 순서: 구조 단 CodeBuild → 상태 단 배치).
STRUCTURE_AT = "2026-08-31T00:00:00Z"
T1 = "2026-09-01T00:00:00Z"
T2 = "2026-09-01T00:05:00Z"
T3 = "2026-09-01T00:10:00Z"
T4 = "2026-09-01T00:15:00Z"
T5 = "2026-09-01T00:20:00Z"
# 스키마 오라클(`schema/tests`) — 엔진 산출물에 **같은 판정 함수**를 건다(`test_direct_connect.py` 와 같은 방식).
_spec = importlib.util.spec_from_file_location(
    "observation_oracle_history",
    ROOT / "schema" / "tests" / "test_observation_schema.py",
)
assert _spec is not None and _spec.loader is not None
ORACLE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ORACLE)

# T2 로부터 19일 뒤 = 보존 창(7일) 밖.
LATE = "2026-09-20T00:00:00Z"

NAT_ID = node_id("nat_gateway", ACCOUNT, REGION, EGRESS_NAT)
NEW_NAT = "nat-0aaa9999bbbb8888"
NAT_FLOW = series_key(
    "AWS/NATGateway", "BytesOutToDestination", {"NatGatewayId": EGRESS_NAT}
)


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _snapshot(
    resource_types: tuple[str, ...],
    at: str,
    regions: tuple[str, ...],
    errors: dict[str, Exception] | None,
    overrides: dict[str, Any],
) -> Any:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=regions, resource_types=resource_types
    )
    # 🔴 상태 단(observe CLI)은 상태 탐침을 켜고 돈다(`cli.py` 의 `with_state=True`) — 구조 단은 끈다.
    # 끈 채로 관측하면 RDS·ECS 등이 「규칙에 없는 값 None」(개발)이 된다(W3 실측).
    return collect(
        scope,
        factory(FakeAws(errors=errors, **overrides)),
        clock=lambda: at,
        with_state=at != STRUCTURE_AT,
    )


def _structure(
    regions: tuple[str, ...] = (REGION,), **overrides: Any
) -> dict[str, Any]:
    """구조 문서 — 배포본처럼 **내용 지문**을 싣는다(`serialize.to_json`). 모습(`was`)의 짝 맞추기용."""
    document = _snapshot(
        SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, regions, None, overrides
    ).to_document()
    document["run"]["content_digest"] = content_digest(document)
    return document


def _document(
    at: str,
    *,
    regions: tuple[str, ...] = (REGION,),
    series: dict[Any, list[float]] | None = None,
    state_errors: dict[str, Exception] | None = None,
    observed_types: tuple[str, ...] = OBSERVED_RESOURCE_TYPES,
    topology: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """한 번의 `observe` 산출물.

    🔴 `state_errors` 는 **상태 단에만** 먹는다. 실계정에서 권한이 사라지는 모양이 이것이다 —
    구조 문서는 권한이 있던 시절에 수집돼 있고(스택 A 는 주기가 더 길다), 상태 단만 거부당한다.
    그래서 구조에는 대상이 그대로 있는데 관측 문서에는 항목이 없다 = 지워진 것과 바이트가 같다.
    """
    if topology is None:
        topology = _snapshot(
            SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, regions, None, overrides
        ).to_document()
    state = _snapshot(observed_types, at, regions, state_errors, overrides)
    layer = (
        read_metrics(
            state,
            factory_by_service({"cloudwatch": FakeCloudWatch(series)}),
            at=at,
        )
        if series is not None
        else None
    )
    document, _dropped = build_observation(
        state, topology=topology, observed_at=at, tool_version="test", metrics=layer
    )
    return document


def _nat(state: str, *, extra: bool = False, only_first: bool = False) -> list[dict]:
    """NAT 데이터셋 변형 — 상태 하나만 바꾸거나 자원을 늘리거나 줄인다."""
    data = copy.deepcopy(NAT_GATEWAYS)
    data[0]["State"] = state
    if only_first:
        del data[1]
    if extra:
        data.append(
            {
                "NatGatewayId": NEW_NAT,
                "SubnetId": EGRESS_SUBNET,
                "VpcId": EGRESS_VPC,
                "State": "available",
                "ConnectivityType": "public",
                "NatGatewayAddresses": [],
                "Tags": [],
            }
        )
    return data


def _kinds(document: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [row for row in document["changes"] if row["kind"] == kind]


def _chain(
    pairs: list[tuple[str, dict[str, Any]]],
    previous: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """`(시각, overrides)` 을 순서대로 관측하며 이력을 **누적**한다 — 배치의 실제 모양.

    `previous` 를 주면 그 문서에 이어 쌓는다(옛 판 모양으로 고친 문서 뒤를 잇는 경우).
    """
    documents: list[dict[str, Any]] = []
    for at, overrides in pairs:
        document = _document(at, **overrides)
        apply_history(document, previous)
        documents.append(document)
        previous = document
    return documents


# ------------------------------------------------------------------ 기준선


def test_the_first_run_has_nothing_to_compare_against():
    """🔴 빈 목록이 「변경 없음」이 아니다 — 비교 대상이 아예 없다는 뜻이다(ADR 0006 O3).

    사람이 겪는 일: 배치를 처음 켠 날의 화면은 「최근 변경 없음」이 아니라 「여기서부터 기록이
    시작된다」여야 한다. 기준 시각이 이번 관측과 같은 것이 그 사실의 데이터 표현이다.
    """
    document = _document(T1)
    report = apply_history(document, None)

    assert report.diffed is False
    assert document["changes"] == []
    assert document["history"]["baseline_at"] == document["run"]["observed_at"]
    assert document["history"]["truncated"] is False
    assert report.warnings == ()
    jsonschema.Draft202012Validator(SCHEMA).validate(document)


# ------------------------------------------------------------------ 구성 변경


def test_a_new_resource_is_recorded_as_added():
    before = _document(T1)
    after = _document(T2, nat_gateways=_nat("available", extra=True))
    report = apply_history(after, before)

    added = _kinds(after, "resource_added")
    assert [row["target_id"] for row in added] == [
        node_id("nat_gateway", ACCOUNT, REGION, NEW_NAT)
    ]
    # `at` 은 **변경을 본 시각** = 이번 관측이다(스키마 `change.at` 의 $comment).
    assert added[0]["at"] == T2
    # 구성 변경에도 앞뒤를 **null 로 적는다** — 생략하면 같은 사실에 두 바이트 표현이 생긴다.
    assert added[0]["from"] is None and added[0]["to"] is None
    assert added[0]["repeat_count"] == 1
    assert report.new_rows == 1 and report.excluded == 0
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_a_real_deletion_is_recorded_once_and_not_again():
    """대조군 — 진짜로 사라진 자원은 **한 번** 적히고, 그 뒤 실행이 같은 줄을 또 만들지 않는다.

    사람이 겪는 일: 자원 하나를 지우면 목록에 한 줄이 생기고, 다음 관측에서 그 줄이 두 줄로
    늘어나지 않는다. 늘어나면 「같은 것이 매 5분마다 삭제되고 있다」로 읽힌다.
    """
    full = {"nat_gateways": _nat("available")}
    gone = {"nat_gateways": _nat("available", only_first=True)}
    first, second, third = _chain([(T1, full), (T2, gone), (T3, gone)])

    removed = _kinds(second, "resource_removed")
    assert len(removed) == 1, removed
    assert removed[0]["target_id"].endswith("nat-0fff6666aaaa7777")
    assert removed[0]["at"] == T2
    # 세 번째 실행은 **새로 적을 것이 없다** — 두 문서 모두에 그 대상이 없다.
    assert _kinds(third, "resource_removed") == removed
    assert first["changes"] == []


def test_an_add_and_a_remove_in_one_run_pass_the_schema_sort_oracle():
    """한 관측에 생김 1 + 없어짐 1 — 엔진 순서 `(at, kind, target_id)` 그대로를 스키마 오라클이 받아야 한다.

    사람이 겪는 일은 없다(화면은 최신 우선으로 다시 정렬한다). 그러나 오라클이 엔진과 다른 키로 재면
    바르게 정렬된 배포본을 「어긋났다」고 고발하고, 그 오라클을 믿는 검사가 전부 거짓 FAIL 을 낸다
    (2026-10-15 고객 데모 생성 중 발견 — 옛 헬퍼 키 `(at, target_id)`). 없어진 쪽 id 가 **더 작게**
    골라야 `kind` 축이 순서를 정한다 — 반대면 두 키가 같은 답을 내서 아무것도 안 잰다.
    """
    swapped = _nat("available", only_first=True)
    swapped.append(
        {**_nat("available", extra=True)[-1], "NatGatewayId": "nat-0fff9999cccc0000"}
    )
    _, after = _chain(
        [(T1, {"nat_gateways": _nat("available")}), (T2, {"nat_gateways": swapped})]
    )
    rows = [(row["kind"], row["target_id"]) for row in after["changes"]]
    assert rows == [
        (
            "resource_added",
            node_id("nat_gateway", ACCOUNT, REGION, "nat-0fff9999cccc0000"),
        ),
        ("resource_removed", GONE_NAT),
    ]
    assert rows[1][1] < rows[0][1]  # 전제 — 없어진 쪽 id 가 더 작다
    assert not ORACLE.unsorted(ORACLE.change_sort_keys(after))


# ------------------------------------------------------------------ 없어진 것의 모습(`was`)

GONE_NAT = node_id("nat_gateway", ACCOUNT, REGION, "nat-0fff6666aaaa7777")


def _incident(structure: dict[str, Any], target: str) -> list[dict[str, Any]]:
    return sorted(
        (e for e in structure["edges"] if target in (e["source"], e["target"])),
        key=lambda e: e["id"],
    )


def _removal_pair() -> tuple[dict, dict, dict, dict]:
    """구조가 **새로 수집된 뒤** 삭제를 본 경우 — 모습은 직전 구조에만 있다."""
    full = {"nat_gateways": _nat("available")}
    gone = {"nat_gateways": _nat("available", only_first=True)}
    full_s, gone_s = _structure(**full), _structure(**gone)
    before = _document(T1, topology=full_s, **full)
    after = _document(T2, topology=gone_s, **gone)
    return before, after, full_s, gone_s


def test_a_removed_card_carries_its_shape_from_the_structure_it_was_drawn_on():
    """사람이 겪는 일: 이력의 「NAT 게이트웨이 없어짐」을 누르면 그때 그림에 그 카드가 이름과 연결을
    달고 회색으로 서 있다. 모습이 없으면 화면은 id 끝 몇 글자만 목록에 적을 수 있다."""
    before, after, full_s, gone_s = _removal_pair()
    report = apply_history(after, before, (gone_s, full_s))

    [row] = _kinds(after, "resource_removed")
    assert row["target_id"] == GONE_NAT
    node = next(n for n in full_s["nodes"] if n["id"] == GONE_NAT)
    assert row["was"]["nodes"] == [node]
    # 붙어 있던 연결 **전부**, id 순(불변식 ②). 비어 있으면 이 검사는 순서를 재지 못한다.
    assert row["was"]["edges"] == _incident(full_s, GONE_NAT)
    assert report.shaped == 1 and report.unshaped == 0 and report.warnings == ()
    jsonschema.Draft202012Validator(SCHEMA).validate(after)
    # 구조 스키마 모양 **그대로** — 화면이 같은 배치 함수로 다시 그린다.
    defs = TOPOLOGY_SCHEMA["$defs"]
    for key, items in (("node", row["was"]["nodes"]), ("edge", row["was"]["edges"])):
        validator = jsonschema.Draft202012Validator(
            {"$defs": defs, "$ref": f"#/$defs/{key}"}
        )
        for item in items:
            validator.validate(item)


def test_a_removed_card_takes_every_line_that_touched_it_in_id_order():
    """연결이 붙은 카드(인터넷 게이트웨이 — VPC 연결 + 라우트)가 없어지면 그 선들도 모습에 들어간다.

    사람이 겪는 일: 그때 그림에서 없어진 카드에 이어진 선이 회색으로 같이 서 있다. 선이 빠지면
    카드만 허공에 떠 있어 「무엇과 이어져 있던 것」인지 알 수 없다.
    """
    full_s = _structure()
    before = _document(T1, topology=full_s)
    after = _document(T2, topology=full_s, internet_gateways=[])
    report = apply_history(after, before, (full_s,))

    igw = next(n["id"] for n in full_s["nodes"] if n["type"] == "internet_gateway")
    [row] = [r for r in _kinds(after, "resource_removed") if r["target_id"] == igw]
    expected = _incident(full_s, igw)
    assert len(expected) >= 2, "대조군이 비었다 — 순서를 재지 못한다"
    assert row["was"]["edges"] == expected  # `_incident` 가 id 순이다
    assert report.unshaped == 0


def test_a_deletion_seen_before_the_structure_refresh_uses_the_current_structure():
    """🔴 흔한 경우: 5분 관측이 매시 구조 수집보다 **먼저** 삭제를 본다 — 모습은 지금 구조에 있다."""
    full = {"nat_gateways": _nat("available")}
    full_s = _structure(**full)
    before = _document(T1, topology=full_s, **full)
    after = _document(
        T2, topology=full_s, nat_gateways=_nat("available", only_first=True)
    )
    report = apply_history(after, before, (full_s,))

    [row] = _kinds(after, "resource_removed")
    assert row["was"]["nodes"][0]["id"] == GONE_NAT
    assert report.shaped == 1


def test_a_structure_the_previous_run_was_not_drawn_on_gives_no_shape():
    """🔴 짝이 안 맞는 구조에서 모습을 꺼내지 않는다 — 다른 시각의 이름·연결은 「그때 모습」이 아니다.

    부재는 「모른다」다(`null` 을 두지 않는다). 못 적은 수는 **세고 경고한다** — 화면은 그 줄을
    그림 없이 글자로만 말한다.
    """
    before, after, _full_s, gone_s = _removal_pair()
    report = apply_history(after, before, (gone_s,))

    [row] = _kinds(after, "resource_removed")
    assert "was" not in row
    assert report.shaped == 0 and report.unshaped == 1
    assert any("구조 문서를 못 찾았다" in w for w in report.warnings), report.warnings


def test_a_same_id_in_another_structure_is_not_borrowed():
    """짝 = 내용 지문. 수집 시각이 같아도 지문이 다르면 다른 구조다(내용이 같은 재수집만 같은 짝)."""
    before, after, full_s, gone_s = _removal_pair()
    forged = copy.deepcopy(full_s)
    forged["run"]["content_digest"] = "sha256:" + "0" * 64
    # 위조본을 **앞에** 둔다 — 뒤에 두면 첫 후보(새 구조)에 그 카드가 없어서 짝을 안 봐도 통과한다.
    report = apply_history(after, before, (forged, gone_s))
    assert report.unshaped == 1 and "was" not in _kinds(after, "resource_removed")[0]


def test_only_removal_rows_get_a_shape_and_inherited_rows_keep_theirs():
    """생긴 줄·상태 줄에는 모습이 없다(스키마가 금지). 이어받은 줄은 적힌 그대로 간다."""
    before, after, full_s, gone_s = _removal_pair()
    apply_history(after, before, (gone_s, full_s))
    shaped = copy.deepcopy(_kinds(after, "resource_removed")[0])

    grown = {"nat_gateways": _nat("failed", only_first=True, extra=True)}
    grown_s = _structure(**grown)
    later = _document(T3, topology=grown_s, **grown)
    report = apply_history(later, after, (grown_s, gone_s))
    assert report.new_rows >= 2  # 생김 1 + 상태 1
    for row in later["changes"]:
        if row["kind"] != "resource_removed":
            assert "was" not in row, row
    assert _kinds(later, "resource_removed") == [shaped]
    assert report.shaped == 0 and report.unshaped == 0 and report.warnings == ()
    jsonschema.Draft202012Validator(SCHEMA).validate(later)


def test_the_shape_is_byte_identical_across_runs():
    """ADR 0006 결정 5 — 모습도 입력(구조 바이트)에서만 온다."""

    def run() -> str:
        before, after, full_s, gone_s = _removal_pair()
        apply_history(after, before, (gone_s, full_s))
        return json.dumps(after, sort_keys=False, ensure_ascii=False)

    assert run() == run()


def test_the_schema_refuses_a_shape_on_rows_that_are_still_there():
    before, after, full_s, gone_s = _removal_pair()
    apply_history(after, before, (gone_s, full_s))
    validator = jsonschema.Draft202012Validator(SCHEMA)
    row = _kinds(after, "resource_removed")[0]
    for kind, extra in (
        ("resource_added", {}),
        ("status_changed", {"from": "up", "to": "down"}),
    ):
        bad = copy.deepcopy(after)
        bad["changes"] = [{**row, "kind": kind, **extra}]
        assert not validator.is_valid(bad), kind
    bad = copy.deepcopy(after)
    bad["changes"] = [{**row, "was": None}]
    assert not validator.is_valid(bad), "null 을 부재 대신 받았다"


def test_losing_permission_is_not_recorded_as_deletion():
    """🔴 S4 가 막으려는 제품 결함(실측 근거 `observe.py:590`·`:613`).

    사람이 겪는 일: 어제까지 읽히던 NAT 게이트웨이의 권한이 오늘 빠졌다. 관측 문서에는 그 종류의
    항목이 **아예 없다** — 지워진 것과 바이트가 같다. 단순 차분이면 화면에 「NAT 게이트웨이 2개
    삭제됨」이 뜨고, 사람은 없어진 자원을 찾으러 콘솔을 뒤진다. 실제로는 아무것도 지워지지 않았고
    고칠 곳은 권한 하나다.

    그래서 목록에 한 줄도 적지 않고, 대신 「이번 비교에서 제외한 대상 2개」로 센다. 0 으로
    삼키면 「조용해서 아무것도 안 뜬 화면」과 구별되지 않는다.
    """
    before = _document(T1)
    after = _document(
        T2,
        state_errors={
            "describe_nat_gateways": client_error("AccessDenied", "DescribeNatGateways")
        },
    )
    report = apply_history(after, before)

    assert _kinds(after, "resource_removed") == []
    assert after["changes"] == []
    # NAT 2개(살아 있는 것 + 생성에 실패한 것) + **그 NAT 로 가는 경로 1개**. 🔴 경로가 같이 빠지는
    # 것이 이 검사가 처음 잡은 결함이다(2026-09-24): 경로는 route_table 응답에서 나오고 그 종류는
    # 읽혔는데, `collect` 가 끝점을 못 찾아 선을 아예 만들지 않는다(`collect.py:291`). 엣지를 만든
    # 응답만 물으면 「경로 1건 삭제」가 목록에 적힌다 — 화면에서는 「NAT 로 가는 길이 끊겼다」다.
    assert report.unread == 3, report
    assert report.excluded == 3 and report.out_of_scope == 0
    # 못 읽은 사실은 이미 coverage 가 말한다 — 이력이 그것을 변경으로 번역하지 않는 것이 전부다.
    denied = [
        row
        for row in after["coverage"]
        if row["layer"] == "describe" and row["status"] == "denied"
    ]
    assert [row["resource_type"] for row in denied] == ["nat_gateway"]


def test_removing_a_region_from_the_scope_is_not_a_deletion():
    """🔴 계정·리전은 화면에서 늘고 준다(ADR 0005) — 뺀 자리의 침묵은 삭제가 아니다.

    사람이 겪는 일: 어제는 두 리전을 보고 있었고 오늘 한 리전을 목록에서 뺐다. 뺀 리전의 자원이
    전부 「삭제됨」으로 목록을 덮으면, 그날의 진짜 변경 한 줄이 그 안에 묻힌다.
    """
    before = _document(T1, regions=(REGION, "eu-west-1"))
    after = _document(T2, regions=(REGION,))
    report = apply_history(after, before)

    assert after["changes"] == []
    assert report.out_of_scope == len(before["observations"]) - len(
        after["observations"]
    )
    assert report.out_of_scope > 0 and report.unread == 0


def test_a_resource_type_entering_or_leaving_the_scope_is_not_a_change():
    """🔴 **종류도 범위다** — 실계정 바이트로 재현한 제품 결함(2026-09-24).

    사람이 겪는 일 ①: 엔진을 올려서 새 리소스 종류를 지원하기 시작한 날. 직전 관측 문서에는 그
    종류가 통째로 없으니 단순 차분은 **원래 있던 자원 전부를 「생성됨」**으로 적는다. 화면에는
    「자원 30개 생성됨」이 뜨고, 사람은 누가 밤에 무엇을 만들었는지 찾는다 — 만들어진 것은 없다.

    사람이 겪는 일 ②: 반대로 종류를 목록에서 뺀 날은 「자원 30개 삭제됨」이 된다. 리전을 뺀 경우와
    **똑같은 거짓**인데(그쪽은 가드가 있었다) 종류에는 없었다.

    실측: `out/r-chain.json`(실계정 192대상)에서 종류 하나를 양방향으로 밀었더니 각각 **30줄**이
    적히고 제외 카운터는 넷 다 **0** 이었다. 그래서 「범위 밖」으로 센다 — 없애지 않고 센다.
    """
    narrow = tuple(t for t in OBSERVED_RESOURCE_TYPES if t != "subnet")
    wide = _document(T1)
    thin = _document(T2, observed_types=narrow)
    assert "subnet" not in thin["scope"]["resource_types"]

    # ① 종류를 뺐다 → 삭제가 아니다
    removed = apply_history(copy.deepcopy(thin), wide)
    # ② 종류를 새로 지원했다 → 생성이 아니다 (같은 쌍을 뒤집기만 한다)
    later = _document(T3)
    added = apply_history(later, thin)

    for label, document, report in (
        ("뺐다", thin, removed),
        ("새로 지원했다", later, added),
    ):
        assert document["changes"] == [], (label, document["changes"])
        assert report.out_of_scope > 0, label
        # 「못 읽은 칸」이 아니다 — 권한이 사라진 것과 범위를 줄인 것은 고칠 곳이 다르다.
        assert report.unread == 0 and report.unknown_cell == 0, label

    # 🔴 대조군 — 종류가 **같은** 두 문서에서는 진짜 삭제가 그대로 적힌다. 이 줄이 없으면
    # 「전부 범위 밖으로 삼키는」 구현도 위 어서션을 통과한다.
    control = _document(T3, nat_gateways=_nat("available", only_first=True))
    report = apply_history(control, _document(T2, nat_gateways=_nat("available")))
    assert [row["kind"] for row in control["changes"]] == ["resource_removed"]
    assert report.out_of_scope == 0

    # 🔴 양쪽 다 묻지 않는 종류(SG·NACL — `none` 밖에 못 내는 것)는 대칭차에 없으므로 이 가드가
    # 건드리지 않는다. 건드리면 보안 그룹 추가가 영원히 이력에 안 남는다.
    assert "security_group" not in wide["scope"]["resource_types"]
    sg_gone = _document(T3, security_groups=[])
    sg_report = apply_history(sg_gone, _document(T2))
    # 노드 2개 + 그것들 사이의 참조 엣지 3개가 같이 사라진다 — 전부 적혀야 한다.
    assert {row["kind"] for row in sg_gone["changes"]} == {"resource_removed"}
    assert {row["target_id"].split("/")[0] for row in sg_gone["changes"]} == {
        "security_group",
        "security_group_reference|security_group",
    }, sg_gone["changes"]
    assert sg_report.out_of_scope == 0


# ------------------------------------------------------------------ 상태 변경


def test_a_gateway_going_down_is_recorded_with_both_ends():
    before = _document(T1, nat_gateways=_nat("available"))
    after = _document(T2, nat_gateways=_nat("failed"))
    apply_history(after, before)

    changed = _kinds(after, "status_changed")
    assert [(row["target_id"], row["from"], row["to"]) for row in changed] == [
        (NAT_ID, "up", "down")
    ]
    assert changed[0]["repeat_count"] == 1
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def _instances(state: str) -> list[dict]:
    """EC2 데이터셋 변형 — 첫 인스턴스(`app-1`)의 상태만 바꾼다."""
    data = copy.deepcopy(INSTANCE_RESERVATIONS)
    data[0]["Instances"][0]["State"] = {"Name": state}
    return data


APP_INSTANCE = INSTANCE_RESERVATIONS[0]["Instances"][0]["InstanceId"]


@pytest.mark.parametrize(
    "refreshed", [False, True], ids=["structure-stale", "structure-refreshed"]
)
@pytest.mark.parametrize(
    ("dataset", "alive", "dead", "target"),
    [
        (
            "instances",
            _instances("running"),
            _instances("terminated"),
            node_id("ec2_instance", ACCOUNT, REGION, APP_INSTANCE),
        ),
        ("nat_gateways", _nat("available"), _nat("deleted"), NAT_ID),
    ],
    ids=["ec2-terminated", "nat-deleted"],
)
def test_a_resource_that_reached_a_gone_state_is_removed_not_down(
    dataset, alive, dead, target, refreshed
):
    """🔴 QA B(2026-10-14) — Auto Scaling 축소·스택 철거가 「[끊김] 정상 → 이상」으로 적히던 결함.

    사람이 겪는 일: 인스턴스가 종료됐는데 이력에는 「고장」으로 적혀 사람이 장애를 쫓는다. 없어진
    상태는 수집이 노드째 버리므로(ADR 0002 §3) 관측 항목이 사라지고 이력은 「없어짐」을 적는다.
    구조 문서가 아직 옛것이어도(스택 A 주기가 더 길다) 같은 결과여야 한다 — 상태 단이 먼저 본다.
    """
    alive_s = _structure(**{dataset: alive})
    dead_s = _structure(**{dataset: dead})
    before = _document(T1, topology=alive_s, **{dataset: alive})
    after = _document(T2, topology=dead_s if refreshed else alive_s, **{dataset: dead})
    report = apply_history(after, before, (dead_s, alive_s))

    assert _kinds(after, "status_changed") == []
    removed = {row["target_id"]: row for row in _kinds(after, "resource_removed")}
    assert target in removed, after["changes"]
    # 「방금까지 있었다」는 모습(`was`)이 싣는다 — 그것을 위해 노드를 남길 필요가 없다.
    assert [n["id"] for n in removed[target]["was"]["nodes"]] == [target]
    assert report.unshaped == 0
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_turning_the_metric_layer_off_is_not_a_status_change():
    """🔴 2층 토글은 **반드시** 꺼진다(세션 기본 +4시간, ADR 0006 §O8).

    사람이 겪는 일: 흐름을 보려고 지표를 켜 둔 동안 NAT 는 「흐름 있음」으로 보인다. 토글이
    만료되면 같은 NAT 가 「정상(흐름 미확인)」으로 돌아간다 — 흐름이 멈춘 것이 아니라 **묻기를
    멈춘 것**이다. 이것을 목록에 적으면 매 4시간마다 「전 구간 흐름 정지」가 뜨고, 진짜 정지가
    그 안에서 사라진다.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2)  # 지표를 켜지 않은 실행
    report = apply_history(after, before)

    assert {o["status"] for o in before["observations"]} >= {"live"}
    assert "live" not in {o["status"] for o in after["observations"]}
    assert after["changes"] == []
    # NAT 의 `live → up` 1 + 2층을 끄며 사라진 2층 전용 연결 행(ALB 배치 선 · 수락된 엔드포인트의 서브넷 구간 —
    # 빈 응답 = 쟀고 0 이라 켠 쪽에만 있다 · ADR 0006 §O7-2a)
    edges = [
        o
        for o in before["observations"]
        if o["target_id"].startswith(
            ("load_balancer_placement|", "endpoint_connection|")
        )
    ]
    kinds = {o["target_id"].split("|", 1)[0] for o in edges}
    assert kinds == {"load_balancer_placement", "endpoint_connection"}, (
        "전제: 켠 실행에 ALB 배치 선 · 엔드포인트 구간 행이 둘 다 있다(empty_is_zero)"
    )
    assert report.layer_changed == 1 + len(edges), report
    assert report.unread == 0 and report.out_of_scope == 0


def test_flow_stopping_is_recorded_when_the_metric_layer_stayed_on():
    """대조군 — 2층을 **계속 켠** 채로 흐름이 멈추면 그것은 진짜 변경이다.

    이 검사가 없으면 위 가드가 `live` 가 끼인 모든 전이를 억제해도 통과한다.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    report = apply_history(after, before)

    changed = _kinds(after, "status_changed")
    assert [(row["target_id"], row["from"], row["to"]) for row in changed] == [
        (NAT_ID, "live", "up")
    ]
    assert report.layer_changed == 0


# --------------------------------------------- 실제로 멈춘/시작한 분 (ADR 0008 D6 보강)
#
# 사람이 겪는 일: 흐름 표시는 늦게 바뀐다(멈춤 최대 ~21분 — 사양). 그래도 변경 이력을 보는 사람은
# 「몇 시 몇 분에 끊겼나」를 알아야 원인(배포·보안 그룹 변경)과 맞춰 볼 수 있다. 그래서 `at`(본 시각)
# 옆에 **지표가 말하는 분**을 따로 적는다. 가짜 CloudWatch 의 i 번째 값(최신 우선)은 관측 시각에서
# (i+1)분 앞 구간이다 — T1(00:00)의 [8192, 4096] 은 23:59·23:58 이다.


def _flow_row(document: dict[str, Any]) -> dict[str, Any]:
    (row,) = [r for r in _kinds(document, "status_changed") if r["target_id"] == NAT_ID]
    return row


def _nat_row(document: dict[str, Any]) -> dict[str, Any]:
    (row,) = [o for o in document["observations"] if o["target_id"] == NAT_ID]
    return row


def test_a_stop_records_the_last_flowing_minute_of_the_previous_run():
    """멈춤 = **이전 문서의** 마지막 흐름 분. 이번 문서에는 흐름이 없으니 그 분을 가질 수 없다."""
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    assert _nat_row(before)["evidence"]["last_flow_at"] == "2026-08-31T23:59:00Z"
    assert _nat_row(before)["evidence"]["first_flow_at"] == "2026-08-31T23:58:00Z"
    # 🔴 흐름이 없는 행에는 두 키가 **없다**(모름의 표현은 부재 하나).
    assert "last_flow_at" not in _nat_row(after)["evidence"]
    apply_history(after, before)

    row = _flow_row(after)
    assert (row["from"], row["to"]) == ("live", "up")
    assert row["occurred_at"] == "2026-08-31T23:59:00Z"
    # 본 시각은 그대로다 — 정렬 키이고 화면 행의 정체다.
    assert row["at"] == T2
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_a_start_records_the_first_flowing_minute_of_this_run():
    """시작 = **이번 문서의** 첫 흐름 분 — 가장 이른 0 이 아닌 지점이다(정렬 순서가 아니다)."""
    before = _document(T1, series={NAT_FLOW: [0.0, 0.0]})
    # 00:05 기준 최신 우선: 00:04=0 · 00:03=0 · 00:02=5 · 00:01=7 · 00:00=0
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0, 5.0, 7.0, 0.0]})
    apply_history(after, before)

    row = _flow_row(after)
    assert (row["from"], row["to"]) == ("up", "live")
    assert row["occurred_at"] == "2026-09-01T00:01:00Z"
    assert _nat_row(after)["evidence"]["last_flow_at"] == "2026-09-01T00:02:00Z"
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


@pytest.mark.parametrize(
    ("after_at", "expected"),
    [
        # 간격 10분 = 창 15분 − 게시 지연 여유 5분 → 빈틈 없음(경계 포함).
        ("2026-09-01T00:10:00Z", "2026-08-31T23:59:00Z"),
        # 1초만 넘어도 두 관측 사이에 아무도 못 본 구간이 생긴다 → 분을 적지 않는다.
        ("2026-09-01T00:10:01Z", None),
        # 3시간 공백 — 적으면 3시간 전의 분이 「멈춘 시각」으로 찍힌다.
        ("2026-09-01T03:00:00Z", None),
    ],
)
def test_the_minute_is_left_out_when_the_two_runs_leave_an_unseen_gap(
    after_at, expected
):
    """🔴 전이 자체는 남고 **분만** 빠진다 — 「멈췄다」는 사실은 참이고 「언제」만 모른다."""
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(after_at, series={NAT_FLOW: [0.0, 0.0]})
    apply_history(after, before)

    row = _flow_row(after)
    assert (row["from"], row["to"]) == ("live", "up")
    assert row.get("occurred_at") == expected
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_an_older_previous_document_without_flow_minutes_still_diffs():
    """새 키가 없는 옛 문서(이 기능 전의 엔진)는 **거절하지 않는다** — 분만 비운다."""
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    for obs in before["observations"]:
        (obs.get("evidence") or {}).pop("first_flow_at", None)
        (obs.get("evidence") or {}).pop("last_flow_at", None)
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    report = apply_history(after, before)

    assert report.warnings == ()
    assert "occurred_at" not in _flow_row(after)


def test_a_partial_row_does_not_get_a_minute():
    """한 계열이 빠진 행의 「흐름 없음」은 덜 본 것일 수 있다 — 전이는 적되 분까지는 확정하지 않는다."""
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    row = _nat_row(after)
    row["partial"] = True
    row["partial_reasons"] = [{"code": "api_error", "api": "cloudwatch:GetMetricData"}]
    apply_history(after, before)

    assert "occurred_at" not in _flow_row(after)


def test_a_minute_is_only_written_for_live_and_up():
    """`up → down` 같은 다른 전이에는 지표가 말하는 분이 없다 — 스키마도 구성 변경에는 금지한다."""
    before = _document(T1, nat_gateways=_nat("available"))
    after = _document(T2, nat_gateways=_nat("failed"))
    apply_history(after, before)
    assert after["changes"], "전제: 이 쌍은 변경을 만들어야 한다"
    assert all("occurred_at" not in row for row in after["changes"])

    added = {
        "at": T2,
        "kind": "resource_added",
        "target_id": NAT_ID,
        "target_kind": "node",
        "from": None,
        "to": None,
        "repeat_count": 1,
        "occurred_at": T1,
    }
    change = jsonschema.Draft202012Validator(
        {"$ref": "#/$defs/change", "$defs": SCHEMA["$defs"]}
    )
    assert list(change.iter_errors(added)), (
        "구성 변경에 occurred_at 을 허용하면 안 된다"
    )
    # 대조군 — 그 키만 빼면 통과해야 한다(위 FAIL 이 다른 이유로 난 것이 아님).
    assert (
        list(change.iter_errors({k: v for k, v in added.items() if k != "occurred_at"}))
        == []
    )


# ------------------------------------------------------------------ 캡 2개


def test_flapping_folds_into_one_row_only_at_the_threshold():
    """접힘은 **임계값에서만** 일어난다 — 3회는 그대로, 4회는 1줄(ADR 0004 D4 캡 1).

    사람이 겪는 일: 어떤 첨부가 끊겼다 붙었다를 반복하면 목록이 그 한 대상으로 도배된다. 그래서
    4회 이상은 「4회 반복」 한 줄로 접는다. 다만 2~3회는 접지 않는다 — 사람이 개별 시각을 보고
    「언제부터인가」를 판단하는 범위다.
    """
    assert DEFAULT_FLAP_COLLAPSE_THRESHOLD == 4
    flap = [
        (T1, {"nat_gateways": _nat("available")}),
        (T2, {"nat_gateways": _nat("failed")}),
        (T3, {"nat_gateways": _nat("available")}),
        (T4, {"nat_gateways": _nat("failed")}),
        (T5, {"nat_gateways": _nat("available")}),
    ]
    _, two, three, four, five = _chain(flap)

    # 3회까지는 개별 줄이다(판별력 — 임계값을 무력화하면 이 줄이 1개가 된다).
    assert [row["repeat_count"] for row in three["changes"]] == [1, 1]
    assert [row["repeat_count"] for row in four["changes"]] == [1, 1, 1]
    # 4회째에 1줄로 접히고, 마지막 전이의 앞뒤를 남긴다(「N회 반복, 마지막은 down→up」).
    assert len(five["changes"]) == 1, five["changes"]
    assert five["changes"][0]["repeat_count"] == 4
    assert (five["changes"][0]["at"], five["changes"][0]["from"]) == (T5, "down")
    assert five["changes"][0]["to"] == "up"
    assert len(two["changes"]) == 1
    jsonschema.Draft202012Validator(SCHEMA).validate(five)
    # 반복의 **시작**도 남는다(ADR 0006 결정 12) — 4번(짝수) 뒤집혔으니 마지막 `from`(down)은 반복 전 상태(up)가 아니다.
    # 사람이 겪는 일: 반복 전 시각을 누르면 그 NAT 가 「정상」이어야 한다(마지막 from 으로 되짚으면 「이상」).
    assert (five["changes"][0]["first_at"], five["changes"][0]["first_from"]) == (
        T2,
        "up",
    )
    # 접지 않은 줄에는 시작을 적지 않는다 — `at`·`from` 이 곧 시작이다(같은 사실에 두 바이트 표현 금지).
    assert all(
        "first_at" not in row and "first_from" not in row for row in four["changes"]
    )


T6 = "2026-09-01T00:25:00Z"


def _flap_to(last_state: str) -> list[dict[str, Any]]:
    """T1~T5 로 4번 뒤집힌 뒤(접힘) T6 에 한 번 더 — 이미 접힌 줄과 새 줄이 다시 합쳐지는 경우."""
    return _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, {"nat_gateways": _nat("failed")}),
            (T3, {"nat_gateways": _nat("available")}),
            (T4, {"nat_gateways": _nat("failed")}),
            (T5, {"nat_gateways": _nat("available")}),
            (T6, {"nat_gateways": _nat(last_state)}),
        ]
    )


def test_a_folded_row_keeps_its_start_when_it_folds_again():
    """접힌 줄이 다음 회차에 다시 접혀도 시작은 **처음 것** 그대로다 — 마지막 회차의 앞뒤만 바뀐다."""
    six = _flap_to("failed")[-1]
    (row,) = six["changes"]
    assert row["repeat_count"] == 5 and (row["at"], row["from"], row["to"]) == (
        T6,
        "up",
        "down",
    )
    assert (row["first_at"], row["first_from"]) == (T2, "up")
    jsonschema.Draft202012Validator(SCHEMA).validate(six)


def test_a_fold_from_before_the_start_was_recorded_does_not_invent_one():
    """🔴 1.7.0 전에 접힌 줄(시작이 없다)과 다시 합쳐지면 시작을 **모른다** — 두 키를 적지 않는다.

    사람이 겪는 일: 지어낸 시작이 있으면 화면은 그 앞 시각을 「그때 정상」으로 단정한다. 실제로는 그 앞에도
    뒤집힘이 있었을 수 있다(옛 줄은 그 사실을 버렸다). 모르면 화면이 「그때 상태를 모른다」고 말해야 한다.
    """
    five = _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, {"nat_gateways": _nat("failed")}),
            (T3, {"nat_gateways": _nat("available")}),
            (T4, {"nat_gateways": _nat("failed")}),
            (T5, {"nat_gateways": _nat("available")}),
        ]
    )[-1]
    old = copy.deepcopy(five)
    for row in old["changes"]:
        del row["first_at"], row["first_from"]  # 옛 판의 접힌 줄 모양
    six = _document(T6, nat_gateways=_nat("failed"))
    apply_history(six, old)
    (row,) = six["changes"]
    assert row["repeat_count"] == 5
    assert "first_at" not in row and "first_from" not in row, row
    jsonschema.Draft202012Validator(SCHEMA).validate(six)


def _nat_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in document["changes"]
        if row["target_id"] == NAT_ID and row["kind"] == "status_changed"
    ]


def _plus(at: str, **delta: float) -> str:
    return (_at(at) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _flap_four(start: str) -> list[tuple[str, dict[str, Any]]]:
    """`start` 뒤 5분 간격으로 4번 뒤집힌다(failed · available · failed · available)."""
    states = ("failed", "available", "failed", "available")
    return [
        (_plus(start, minutes=5 * (i + 1)), {"nat_gateways": _nat(state)})
        for i, state in enumerate(states)
    ]


def _assert_contract(document: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert ORACLE.fold_start_after_last(document) == []
    assert ORACLE.collapsed_below_threshold(document) == []


def test_a_recreated_resource_starts_a_new_repetition():
    """🔴 지웠다가 **같은 id 로 다시 생긴** 대상의 반복은 옛 수명의 줄과 접히지 않는다(ADR 0006 결정 13 · QA B 2차 N-04).

    사람이 겪은 일(2026-10-17): 며칠 전 지운 ALB 를 같은 이름으로 다시 만들었더니(이름이 곧 id 다) 새 「흐름 시작 · 멈춤」
    반복이 1차 때 줄(1.7.0 전에 접혀 시작이 없다)과 합쳐져 「8회 반복」 = 1차 + 2차가 됐고, 시작을 잃어 그 앞 시각 비교가
    「그때 상태를 모르는 것: ALB」로 나왔다. 실제로는 다시 만든 뒤 처음 몇 분은 정상이었다.
    옛 줄은 같은 하루 안(분 단위)이라 공백 규칙이 아니라 **수명 경계**만이 끊는다.
    """
    nat = NAT_GATEWAYS[0]["NatGatewayId"]
    gone = {"nat_gateways": [n for n in _nat("available") if n["NatGatewayId"] != nat]}
    first = _chain([(T1, {"nat_gateways": _nat("available")}), *_flap_four(T1)])[-1]
    (old,) = _nat_rows(first)
    assert old["repeat_count"] == 4 and old["first_at"] == T2
    legacy = copy.deepcopy(first)
    # 1.7.0 전에 접힌 줄 모양 — 시작을 모른다
    for row in _nat_rows(legacy):
        del row["first_at"], row["first_from"]
    removed_at, back_at = _plus(T5, minutes=5), _plus(T5, minutes=10)
    later = _chain(
        [
            (removed_at, gone),
            (back_at, {"nat_gateways": _nat("available")}),
            *_flap_four(back_at),
        ],
        previous=legacy,
    )
    last = later[-1]
    lifetime = [
        (row["kind"], row["at"])
        for row in last["changes"]
        if row["target_id"] == NAT_ID and row["kind"] != "status_changed"
    ]
    assert lifetime == [("resource_removed", removed_at), ("resource_added", back_at)]
    rows = _nat_rows(last)
    # 옛 수명의 줄은 적힌 그대로(시작 모름) · 새 수명의 반복은 제 시작을 가진다.
    assert [(row["at"], row["repeat_count"]) for row in rows] == [
        (T5, 4),
        (_plus(back_at, minutes=20), 4),
    ], rows
    assert "first_at" not in rows[0] and "first_from" not in rows[0]
    assert (rows[1]["first_at"], rows[1]["first_from"]) == (
        _plus(back_at, minutes=5),
        "up",
    )
    _assert_contract(last)


def test_a_quiet_day_ends_a_repetition():
    """🔴 하루 넘게 조용했던 대상의 새 뒤집힘은 그 전 반복과 접히지 않는다 — 몇 시간 멈춘 것은 이어 접힌다(ADR 0006 결정 13).

    사람이 겪는 일: 월요일 아침 몇 분 흔들린 NAT 가 수요일 저녁 다시 흔들리면 목록에 「흔들림」 두 줄이 남는다. 하나로
    합치면 그 사이 이틀이 전부 반복 안이 되어 화요일 시각을 누를 때 「그때 상태를 모른다」가 된다(실제로는 계속 정상).
    대조군: 배치가 7시간 멈췄다 돌아와서(QA B 2차 실측 공백) 이어진 뒤집힘은 **같은 반복**이다 — 끊으면 줄이 도배된다.
    """
    first = _chain([(T1, {"nat_gateways": _nat("available")}), *_flap_four(T1)])
    quiet = [
        (_plus(T5, hours=12 * (i + 1)), {"nat_gateways": _nat("available")})
        for i in range(3)
    ]
    later = _chain([*quiet, *_flap_four(quiet[-1][0])], previous=first[-1])[-1]
    rows = _nat_rows(later)
    assert [(row["repeat_count"], row["first_at"]) for row in rows] == [
        (4, T2),
        (4, _plus(quiet[-1][0], minutes=5)),
    ], rows
    _assert_contract(later)

    # 대조군 — 7시간 공백 뒤 한 번 더 뒤집히면 같은 반복으로 접힌다(시작은 처음 것 그대로).
    paused = _chain(
        [(_plus(T5, hours=7), {"nat_gateways": _nat("failed")})], previous=first[-1]
    )[-1]
    (row,) = _nat_rows(paused)
    assert (row["repeat_count"], row["first_at"], row["first_from"]) == (5, T2, "up")
    _assert_contract(paused)


def test_the_retention_window_drops_old_rows_and_says_so():
    """🔴 `truncated` 는 `baseline_at` 과 **다른 사실**이다(ADR 0006 결정 8).

    사람이 겪는 일: 7일보다 오래된 변경은 목록에서 떨어진다. 그때 화면이 「이 앞에 더 있다」를
    말하지 않으면 사람은 목록의 첫 줄을 「처음」으로 읽는다. 기준 시각은 여전히 「기록이 시작된
    시각」을 가리키고, 잘렸다는 사실은 따로 적는다.
    """
    gone = {"nat_gateways": _nat("available", only_first=True)}
    _, recent, late = _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, gone),
            (LATE, gone),
        ]
    )

    assert len(recent["changes"]) == 1 and recent["history"]["truncated"] is False
    assert late["changes"] == []  # 창 밖으로 떨어졌다 — 「변경이 없었다」가 아니다
    assert late["history"]["truncated"] is True
    # 함수 관계를 양방향으로 잰다(ADR 0006 O4).
    for document in (recent, late):
        history = document["history"]
        horizon = _at(document["run"]["observed_at"]) - timedelta(
            seconds=history["retention_seconds"]
        )
        assert history["truncated"] == (_at(history["baseline_at"]) < horizon)
    # 기준 시각은 잘림과 무관하게 첫 관측을 계속 가리킨다.
    assert late["history"]["baseline_at"] == T1
    assert late["history"]["retention_seconds"] == DEFAULT_RETENTION_SECONDS


def test_the_change_cap_keeps_the_newest_rows_and_says_so():
    """🔴 항목 수 상한(대장 19 · 승인 2026-10-02 = 5,000)도 「버린 사실」이다(ADR 0006 O4).

    사람이 겪는 일: 변경이 아주 많은 조직에서 목록이 상한을 넘으면 **오래된 줄부터** 사라진다.
    그때 7일 안의 일인데도 「이 앞에 더 있다」를 말하지 않으면 사람은 남은 첫 줄을 「처음」으로 읽는다.
    버린 줄은 창 안에 있던 줄이라 **다음 회차에도 없다** — 그래서 잘림 표시는 이어받는다.
    """

    def chain(cap: int) -> list[tuple[dict[str, Any], Any]]:
        out: list[tuple[dict[str, Any], Any]] = []
        previous: dict[str, Any] | None = None
        for at, state in (
            (T1, "available"),
            (T2, "failed"),
            (T3, "available"),
            (T4, "failed"),
            (
                T5,
                "failed",
            ),  # 변화 없음 — 이번엔 버릴 것이 없다(이어받기를 가르는 회차)
        ):
            document = _document(at, nat_gateways=_nat(state))
            document["history"]["max_changes"] = cap
            out.append((document, apply_history(document, previous)))
            previous = document
        return out

    capped = chain(2)
    four, four_report = capped[3]
    five, five_report = capped[4]
    # 넘친 1줄은 **가장 오래된** 것(T2)이다 — 남은 2줄은 T3·T4.
    assert [row["at"] for row in four["changes"]] == [T3, T4]
    assert four_report.capped == 1 and four_report.dropped == 0
    assert four["history"]["truncated"] is True
    # 기록은 창 안에서 시작했다 — 첫 원인(보존 창)으로는 설명이 안 되는 잘림이다.
    horizon = _at(four["run"]["observed_at"]) - timedelta(
        seconds=four["history"]["retention_seconds"]
    )
    assert _at(four["history"]["baseline_at"]) >= horizon
    # 이번엔 버린 것이 없어도 T2 는 여전히 목록에 없다 → 잘림 표시를 이어받는다.
    assert five_report.capped == 0 and len(five["changes"]) == 2
    assert five["history"]["truncated"] is True
    jsonschema.Draft202012Validator(SCHEMA).validate(five)

    # 대조군 — 기본 상한이면 같은 변경 3줄이 전부 남고 잘림도 없다(판별력).
    control = chain(DEFAULT_MAX_CHANGES)
    assert [row["at"] for row in control[4][0]["changes"]] == [T2, T3, T4]
    assert control[4][0]["history"]["truncated"] is False
    assert control[4][1].capped == 0


# ------------------------------------------------------------------ 해상도·거부


def test_resolution_reports_the_coarsest_gap_it_actually_saw():
    """🔴 「5분 주기」라고 적힌 화면이 실제로 3시간 간격이면 그 문장이 거짓이다.

    사람이 겪는 일: 화면 아래의 「이 주기보다 짧게 생겼다 사라진 변경은 보이지 않는다」가 실제
    간격을 말해야 한다. 배치가 3시간 멈췄다 돌아온 날 그 숫자가 300초로 남아 있으면, 사람은 그
    3시간 동안 아무 일도 없었다고 읽는다.
    """
    before = _document(T1)
    after = _document("2026-09-01T03:00:00Z")
    apply_history(after, before)
    assert after["history"]["resolution_seconds"] == 10800

    # 한 번 거칠어진 목록은 다시 촘촘해지지 않는다 — 목록은 기준 시각부터의 **구간**을 말한다.
    later = _document("2026-09-01T03:05:00Z")
    apply_history(later, after)
    assert later["history"]["resolution_seconds"] == 10800
    # 요청 주기보다 짧은 간격은 요청 주기로 말한다(과장하는 쪽은 촘촘하다고 말하는 쪽이다).
    quick = _document("2026-09-01T00:00:30Z")
    apply_history(quick, before)
    assert quick["history"]["resolution_seconds"] == DEFAULT_RESOLUTION_SECONDS


def test_an_unusable_previous_document_restarts_the_baseline_instead_of_failing():
    """🔴 이어받기 실패는 **오류가 아니다** — 이번 관측은 그대로 유효하다(불변식 ④).

    사람이 겪는 일: 같은 파일을 두 번 주거나, 순서가 뒤집힌 파일을 주거나, 계약이 다른 옛 파일을
    주면 「이력을 이번 관측부터 새로 시작한다」가 뜨고 상태 화면은 그대로 뜬다. 여기서 실행을
    죽이면 상태 화면까지 같이 멈춘다.
    """
    current = _document(T1)
    future = _document(T2)

    report = apply_history(current, future)
    assert report.diffed is False and report.warnings
    assert current["changes"] == []
    assert current["history"]["baseline_at"] == T1

    same = _document(T2)
    # 자기 자신을 기준으로 삼는 것도 거부한다 — 그러면 매 실행 「변경 0건」이 된다.
    assert apply_history(same, _document(T2)).diffed is False

    stale = _document(T1)
    broken = _document(T1)
    broken["schema_version"] = "9.0.0"
    assert apply_history(stale, broken).diffed is False
    missing = _document(T1)
    del missing["changes"]
    assert apply_history(_document(T2), missing).diffed is False
    # 경고 문구에 대상 id·계정ID 를 넣지 않는다(§0).
    for warning in apply_history(_document(T2), missing).warnings:
        assert ACCOUNT not in warning


def _drifted(mutate) -> dict[str, Any]:
    previous = _document(T1, nat_gateways=_nat("available"))
    previous["changes"] = [
        {
            "at": T1,
            "kind": "status_changed",
            "target_id": NAT_ID,
            "target_kind": "node",
            "from": "up",
            "to": "down",
            "repeat_count": 1,
        }
    ]
    mutate(previous)
    return previous


def test_a_same_major_document_with_a_drifted_shape_also_restarts_the_baseline():
    """🔴 major 만 보는 거부는 **약속을 절반만 지킨다**(실측 2026-09-24).

    `schema_version` 의 major 가 같으면 이어받는다 — 그런데 그 문서를 실제로 읽는 코드는 중첩 키를
    `row["at"]`·`scope["resource_types"]` 처럼 **직접 인덱싱**한다. 그래서 같은 major 인데 모양이
    밀린 파일을 주면 `KeyError` 가 나고 `nettopo` 가 트레이스백 + rc≠0 으로 죽었다. 이어받기 실패는
    오류가 아니라 경고여야 한다(불변식 ④) — 여기서 죽으면 그날의 관측까지 같이 버린다.

    가정이 아니다: `out/observe-run1.json` 은 같은 `1.0.0` 인데 `scope` 가 2키뿐이고 다른 `1.0.0`
    산출물은 3키다. **한 major 안에서 우리 writer 가 이미 모양을 바꿨고**, 그때 죽지 않은 이유는
    그 뒤에 major 가 올라갔기 때문뿐이다.

    사람이 겪는 일: 옛 형식(또는 손으로 고친) 파일을 `--previous` 로 물려도 주기 관측은 성공하고
    「이력을 이번 관측부터 새로 시작한다」만 뜬다. 상태 화면이 파이썬 오류로 멈추지 않는다.
    """
    drifts = {
        "scope 키 누락": lambda d: d["scope"].pop("resource_types"),
        "scope 타입 뒤바뀜": lambda d: d["scope"].update(regions=REGION),
        "coverage 행 키 누락": lambda d: d["coverage"][0].pop("account_id"),
        "observations 행 키 누락": lambda d: d["observations"][0].pop("target_kind"),
        "observations 행이 객체가 아님": lambda d: d["observations"].__setitem__(
            0, "x"
        ),
        "changes 행 at 누락": lambda d: d["changes"][0].pop("at"),
        "changes 행 at 이 깨짐": lambda d: d["changes"][0].update(at="어제"),
        "changes 행 repeat_count 누락": lambda d: d["changes"][0].pop("repeat_count"),
        "history 가 객체가 아님": lambda d: d.update(history=[]),
        "baseline_at 이 깨짐": lambda d: d["history"].update(baseline_at="2026-09-00"),
        # #36 리뷰 — `_Run` 이 흐름 로그 판독기를 켰나를 `layers` 에서 읽는다. 없으면 `KeyError` 였다.
        "layers 누락": lambda d: d.pop("layers"),
        "layers 가 객체가 아님": lambda d: d.update(layers=[]),
    }
    for label, mutate in drifts.items():
        current = _document(T2, nat_gateways=_nat("failed", extra=True))
        report = apply_history(current, _drifted(mutate))  # 죽지 않는다
        assert report.diffed is False, label
        assert report.warnings, label
        assert ACCOUNT not in " ".join(report.warnings), label
        # 🔴 대조군 — 위 두 문서는 모양만 밀지 않았으면 **실제로 변경이 잡히는** 쌍이다.
        # 이것이 없으면 「전부 거부」하는 구현도 위 어서션을 통과한다.
        assert current["changes"] == [], label
        assert current["history"]["baseline_at"] == T2, label

    intact = _document(T2, nat_gateways=_nat("failed", extra=True))
    control = apply_history(intact, _drifted(lambda _d: None))
    assert control.diffed is True and control.new_rows >= 1
    assert intact["history"]["baseline_at"] == T1


def test_a_drifted_value_type_in_the_previous_document_also_restarts_the_baseline():
    """🔴 「키가 있나」만 묻는 검사는 **절반만** 막는다(실측 2026-09-24 2차 · 실계정 바이트).

    같은 major 에 키도 다 있는데 **값의 타입**이 밀린 파일을 주면 이 모듈이 차례로 터졌다:
    `repeat_count` 가 문자열이면 `_collapse` 의 `sum`, `kind`·`target_id` 가 문자열이 아니면 최종
    `sorted`, `coverage`·`scope` 원소가 unhashable 이면 `set()`, `resolution_seconds` 가 숫자 아닌
    문자열이면 `int()` 다. 전부 트레이스백 + rc≠0 — 불변식 ④ 가 말하는 「경고 + 이번 관측부터 새로
    시작」의 반대이고, 그날의 관측까지 같이 버린다.

    🔴 `resolution_seconds="9000"` 은 **죽지도 않는다** — `int()` 가 받아서 조용히 통과한다.
    그래서 그 줄이 막는 것은 죽음이 아니라 **계약 밖 바이트를 이어받는 일**이다.
    """
    drifts = {
        "repeat_count 가 문자열": lambda d: d["changes"][0].update(repeat_count="2"),
        "repeat_count 가 bool": lambda d: d["changes"][0].update(repeat_count=True),
        "changes kind 가 정수": lambda d: d["changes"][0].update(kind=5),
        "changes target_id 가 None": lambda d: d["changes"][0].update(target_id=None),
        "observations target_id 가 정수": lambda d: d["observations"][0].update(
            target_id=7
        ),
        "coverage region 이 배열": lambda d: d["coverage"][0].update(region=[REGION]),
        "scope 원소가 객체": lambda d: d["scope"].update(account_ids=[{"id": ACCOUNT}]),
        "scope 원소가 정수": lambda d: d["scope"].update(regions=[1]),
        "resolution_seconds 가 문자열": lambda d: d["history"].update(
            resolution_seconds="abc"
        ),
        "resolution_seconds 가 숫자 문자열": lambda d: d["history"].update(
            resolution_seconds="9000"
        ),
    }
    for label, mutate in drifts.items():
        current = _document(T2, nat_gateways=_nat("failed", extra=True))
        report = apply_history(current, _drifted(mutate))  # 죽지 않는다
        assert report.diffed is False, label
        assert report.warnings, label
        assert ACCOUNT not in " ".join(report.warnings), label
        assert current["changes"] == [], label
        assert current["history"]["baseline_at"] == T2, label

    # 🔴 대조군 — 타입을 밀지 않으면 같은 쌍이 **실제로 변경을 잡는다**(전부 거부하는 구현 배제).
    intact = _document(T2, nat_gateways=_nat("failed", extra=True))
    control = apply_history(intact, _drifted(lambda _d: None))
    assert control.diffed is True and control.new_rows >= 1


# ------------------------------------------------------------------ 결정론·칸 매핑


def test_the_same_pair_of_documents_produces_the_same_bytes():
    """ADR 0006 결정 5 — 시각을 입력에서만 읽으므로 두 번 돌려도 바이트가 같다."""
    before = _document(T1, nat_gateways=_nat("available"))

    def run() -> str:
        after = _document(T2, nat_gateways=_nat("failed", extra=True))
        apply_history(after, copy.deepcopy(before))
        return json.dumps(after, sort_keys=False, ensure_ascii=False)

    assert run() == run()


def test_every_observed_target_maps_to_a_cell_inside_the_scope():
    """🔴 「칸 미상」이 1 이라도 나오면 우리 쪽 결함이다 — 그 대상은 이력에서 조용히 빠진다.

    새 리소스 종류나 새 엣지 종류를 더하면서 `EDGE_SOURCE_RESOURCE` 를 안 고치면 여기서 잡힌다.
    """
    document = _document(T1)
    pairs = {
        (account, region)
        for account in document["scope"]["account_ids"]
        for region in document["scope"]["regions"]
    }
    unmapped = [
        observation["target_id"]
        for observation in document["observations"]
        if (cells := target_cells(observation["target_id"], observation["target_kind"]))
        is None
        or any(
            # 글로벌 칸(DXGW)은 리전 축 밖이다 — 계정만 scope 안이면 된다(`history._node_cell`).
            cell[0] not in document["scope"]["account_ids"]
            if cell[1] == GLOBAL
            else (cell[0], cell[1]) not in pairs
            for cell in cells
        )
    ]
    assert unmapped == [], unmapped
    # 🔴 글로벌 노드가 **실제로** 이 검사를 지난다(대조군) — 없으면 위 분기는 미측정이다.
    assert any(
        (cells := target_cells(o["target_id"], o["target_kind"])) is not None
        and cells[0][1] == GLOBAL
        for o in document["observations"]
    )
    # 🔴 엣지는 **끝점의 칸까지** 요구한다 — 양 끝 노드가 이번에 수집돼야 선이 생긴다.
    route = next(
        o["target_id"]
        for o in document["observations"]
        if o["target_id"].startswith("route|") and o["target_id"].endswith(EGRESS_NAT)
    )
    assert target_cells(route, "edge") == (
        (ACCOUNT, REGION, "route_table"),
        (ACCOUNT, REGION, "nat_gateway"),
    )

    # 그리고 실제 비교에서도 0 이어야 한다(위는 정적 매핑, 아래는 차분 경로).
    report = apply_history(_document(T2), document)
    assert report.unknown_cell == 0 and report.excluded == 0


# ------------------------------------------------------------------ 지우는 중(사용자 결정 2026-10-21 · QA C)
# 사람이 겪은 일: VPC 인터페이스 엔드포인트를 일부러 지웠더니 이력에 빨간 「끊김 · 정상 → 이상」이 「없어짐」보다 먼저 남았다.
# 원하는 모습: 목록에서 빠질 때 「없어짐」 한 줄만. 이미 「이상」이던 자원이면 그 줄은 지우지 않는다.

DELETED_ENDPOINT = "vpce-0bbb2222cccc8888"  # 인터페이스 엔드포인트(fakes · ssm)
ENDPOINT_ID = node_id("vpc_endpoint", ACCOUNT, REGION, DELETED_ENDPOINT)


def _endpoints(state: str | None) -> dict[str, Any]:
    """인터페이스 엔드포인트의 상태만 바꾼다 · `None` = 목록에서 빠졌다(AWS 가 다 지웠다)."""
    data = copy.deepcopy(VPC_ENDPOINTS)
    if state is None:
        data = [e for e in data if e["VpcEndpointId"] != DELETED_ENDPOINT]
    else:
        for e in data:
            if e["VpcEndpointId"] == DELETED_ENDPOINT:
                e["State"] = state
    return {"vpc_endpoints": data}


def _rows_of(document: dict[str, Any], target_id: str) -> list[tuple[Any, ...]]:
    return [
        (r["kind"], r["from"], r["to"])
        for r in document["changes"]
        if r["target_id"] == target_id
    ]


def test_a_deliberate_delete_leaves_exactly_one_removed_row_and_no_cut():
    """available → deleting → 목록에서 빠짐 = 「없어짐」 정확히 한 줄 · 「끊김」(→ down) 0줄 · 「→ 관측 불가」 0줄."""
    first, deleting, gone = _chain(
        [
            (T1, _endpoints("available")),
            (T2, _endpoints("deleting")),
            (T3, _endpoints(None)),
        ]
    )
    row = next(o for o in deleting["observations"] if o["target_id"] == ENDPOINT_ID)
    # 지우는 중에도 카드는 남아 있고 판정은 「삭제 중」이다 — 「이상」이 아니다
    assert (row["status"], row.get("deleting")) == ("none", True)
    assert _rows_of(deleting, ENDPOINT_ID) == []
    assert _rows_of(gone, ENDPOINT_ID) == [("resource_removed", None, None)]
    assert not [r for r in gone["changes"] if r["to"] == "down"], gone["changes"]
    assert first["changes"] == []
    for document in (deleting, gone):
        jsonschema.Draft202012Validator(SCHEMA).validate(document)


def test_the_deletion_transition_is_counted_not_silently_dropped():
    """🔴 적지 않은 전이도 **센다**(CLI 「삭제 중 N」) — 안 세면 「이력이 조용하다」와 「삭제를 걸렀다」가 같아진다."""
    before = _document(T1, **_endpoints("available"))
    after = _document(T2, **_endpoints("deleting"))
    report = apply_history(after, before)
    assert report.deleting == 1
    assert report.new_rows == 0
    assert report.excluded == report.deleting


def test_an_earlier_down_row_survives_the_deletion():
    """이미 「이상」이던 자원을 지우면: 앞의 「정상 → 이상」 줄은 그대로 · 삭제가 새 전이를 더하지 않는다 · 끝에 「없어짐」."""
    _first, failed, deleting, gone = _chain(
        [
            (T1, _endpoints("available")),
            (T2, _endpoints("failed")),
            (T3, _endpoints("deleting")),
            (T4, _endpoints(None)),
        ]
    )
    assert _rows_of(failed, ENDPOINT_ID) == [("status_changed", "up", "down")]
    # 삭제가 만든 새 줄은 없다 — 이어받은 「이상」 줄만 있다
    assert _rows_of(deleting, ENDPOINT_ID) == [("status_changed", "up", "down")]
    assert _rows_of(gone, ENDPOINT_ID) == [
        ("status_changed", "up", "down"),
        ("resource_removed", None, None),
    ]


def test_a_delete_that_fails_is_still_recorded():
    """대조군 — 지우는 중 → 실패는 장애다. 그 전이는 「삭제가 만든 것」이 아니므로 적힌다(`none → down` = 끊김)."""
    _first, _deleting, failed = _chain(
        [
            (T1, _endpoints("available")),
            (T2, _endpoints("deleting")),
            (T3, _endpoints("failed")),
        ]
    )
    assert _rows_of(failed, ENDPOINT_ID) == [("status_changed", "none", "down")]


def test_a_nat_delete_reads_the_same_way():
    """NAT 도 같다(`deleted` 는 노드째 빠진다 — `GONE_STATES`) — 엔드포인트만 고친 것이 아니다."""
    _first, deleting, gone = _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, {"nat_gateways": _nat("deleting")}),
            (T3, {"nat_gateways": _nat("deleted")}),
        ]
    )
    target = node_id("nat_gateway", ACCOUNT, REGION, NAT_GATEWAYS[0]["NatGatewayId"])
    assert _rows_of(deleting, target) == []
    assert _rows_of(gone, target) == [("resource_removed", None, None)]


# ------------------------------------------------------------------ 상태 없는 카드의 생김·없어짐(#133 · ADR 0006 결정 14)

# 픽스처 두 장이 8종을 나눠 담는다(`fixtures/relations.json` = 배치 D 4종 · `fixtures/lb-targets.json` = 배치 A 4종).
PRESENCE_FIXTURES = {
    "relations": (
        "api_gateway",
        "global_accelerator",
        "elastic_ip",
        "rds_db_proxy",
        # #129 — Cloud WAN 코어 네트워크 · VPC Lattice 서비스 네트워크·서비스(rel_world ⑥⑦⑧)
        "core_network",
        "lattice_service_network",
        "lattice_service",
        # 2.12.0 — WAF 웹 ACL(rel_world ⑨ · 리전 범위 + CloudFront 범위)
        "waf_web_acl",
    ),
    "lb-targets": ("cloudfront", "route53_hosted_zone", "lambda_function", "s3_bucket"),
}
# 이슈가 이름으로 요구한 넷 — 이 넷의 생김·없어짐이 사라지면 아래 검사가 이름으로 FAIL 한다.
PRESENCE_MUST = ("api_gateway", "global_accelerator", "elastic_ip", "cloudfront")
P1 = "2026-01-02T03:09:05Z"
P2 = "2026-01-02T03:14:05Z"
P3 = "2026-01-02T03:19:05Z"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _without(
    structure: dict[str, Any], kinds: tuple[str, ...], **coverage: str
) -> dict[str, Any]:
    """그 종류의 카드(와 닿은 선)를 뺀 구조 — 시험 스택을 지운 뒤의 구조 수집. `coverage` = 종류 → 바꿀 status."""
    out = copy.deepcopy(structure)
    gone = {n["id"] for n in out["nodes"] if n["type"] in kinds}
    out["nodes"] = [n for n in out["nodes"] if n["id"] not in gone]
    out["edges"] = [
        e for e in out["edges"] if e["source"] not in gone and e["target"] not in gone
    ]
    for row in out["coverage"]:
        if row["resource_type"] in coverage:
            row["status"], row["item_count"] = coverage[row["resource_type"]], None
            row["reason"] = {"code": "AccessDenied", "api": None, "message": "test"}
    out["run"]["content_digest"] = content_digest(out)
    return out


def _bare(structure: dict[str, Any], at: str) -> dict[str, Any]:
    """그 구조에 짝지은 관측 문서 — 상태 항목 0. 🔴 상태 없는 카드는 관측 항목이 **없다**(그것이 이 축의 전제다)."""
    run = structure["run"]
    accounts = structure["scope"]["account_ids"]
    regions = structure["scope"]["regions"]
    coverage = []
    for account in accounts:
        for region in regions:
            for kind in sorted(OBSERVED_RESOURCE_TYPES):
                for layer, status, count in (
                    ("describe", "ok", 0),
                    ("metric", "skipped", None),
                ):
                    coverage.append(
                        {
                            "account_id": account,
                            "region": region,
                            "resource_type": kind,
                            "layer": layer,
                            "status": status,
                            "reason": None,
                            "item_count": count,
                        }
                    )
    return {
        "schema_version": SCHEMA["properties"]["schema_version"]["const"],
        "run": {
            "observed_at": at,
            "tool_version": "test",
            "topology_collected_at": run["collected_at"],
            "topology_content_digest": run["content_digest"],
        },
        "scope": {
            "account_ids": list(accounts),
            "regions": list(regions),
            "resource_types": sorted(OBSERVED_RESOURCE_TYPES),
        },
        "layers": {"describe": True, "metric": False},
        "sources": [],
        "observations": [],
        "coverage": coverage,
        "history": {
            "baseline_at": at,
            "resolution_seconds": DEFAULT_RESOLUTION_SECONDS,
            "retention_seconds": DEFAULT_RETENTION_SECONDS,
            "truncated": False,
            "flap_collapse_threshold": DEFAULT_FLAP_COLLAPSE_THRESHOLD,
            "max_changes": DEFAULT_MAX_CHANGES,
        },
        "changes": [],
    }


def _deleted_then_recreated(name: str) -> tuple[Any, ...]:
    """시험 스택을 지웠다가(P2) 다시 만든(P3) 세 관측 — QA C 의 순서 그대로(구조 수집이 사이마다 한 번)."""
    full = _fixture(name)
    gone = _without(full, PRESENCE_FIXTURES[name])
    first, second, third = _bare(full, P1), _bare(gone, P2), _bare(full, P3)
    removed = apply_history(second, first, (gone, full))
    added = apply_history(third, second, (full, gone))
    return full, gone, second, third, removed, added


@pytest.mark.parametrize("name", sorted(PRESENCE_FIXTURES))
def test_stateless_cards_leave_removed_and_added_rows_from_the_two_structures(name):
    """사람이 겪는 일(QA C 2026-10-21): API Gateway · Global Accelerator 를 지우면 화면에서 카드가 사라진다 — 이력에도
    「없어짐」 줄이 남고, 그 줄을 누르면 그때 모습(카드 + 닿은 선)이 그려진다. 다시 만들면 「생김」 줄이 남는다."""
    full, gone, second, third, removed, added = _deleted_then_recreated(name)
    want = sorted(
        n["id"] for n in full["nodes"] if n["type"] in PRESENCE_FIXTURES[name]
    )
    kinds = {i.split("/")[0] for i in want}
    assert kinds == set(PRESENCE_FIXTURES[name]), (
        f"전제: 픽스처에 그 종류가 없다 — {kinds}"
    )

    gone_rows = _kinds(second, "resource_removed")
    assert sorted(r["target_id"] for r in gone_rows) == want
    for row in gone_rows:
        assert row["at"] == P2 and row["target_kind"] == "node"
        node = next(n for n in full["nodes"] if n["id"] == row["target_id"])
        assert row["was"]["nodes"] == [node]
        assert row["was"]["edges"] == _incident(full, row["target_id"])
    assert removed.presence_rows == len(want) and removed.shaped == len(want)
    assert removed.excluded == 0 and removed.warnings == ()

    come_rows = _kinds(third, "resource_added")
    assert sorted(r["target_id"] for r in come_rows) == want
    assert all(r["at"] == P3 and "was" not in r for r in come_rows)
    assert added.presence_rows == len(want) and added.excluded == 0
    # 🔴 상태는 없다 — 관측 항목이 하나도 늘지 않는다(「관측 불가」 알약 · 카드의 `?` 가 그대로다).
    assert second["observations"] == [] and third["observations"] == []
    for document, structure in ((second, gone), (third, full)):
        jsonschema.Draft202012Validator(SCHEMA).validate(document)
        assert ORACLE.dangling_changes(document, structure) == []
        assert not ORACLE.unsorted(ORACLE.change_sort_keys(document))


@pytest.mark.parametrize("kind", PRESENCE_MUST)
def test_the_four_named_kinds_get_both_a_removed_and_an_added_row(kind):
    """이슈 #133 이 이름으로 요구한 넷 — 생김 1+ · 없어짐 1+. 종류마다 따로 재서 하나만 빠져도 그 이름으로 FAIL 한다."""
    name = next(n for n, kinds in PRESENCE_FIXTURES.items() if kind in kinds)
    _full, _gone, second, third, _r, _a = _deleted_then_recreated(name)

    def of(document: dict[str, Any], change: str) -> list[dict[str, Any]]:
        return [
            r for r in _kinds(document, change) if r["target_id"].startswith(kind + "/")
        ]

    assert of(second, "resource_removed"), f"{kind} 없어짐 줄이 없다"
    assert of(third, "resource_added"), f"{kind} 생김 줄이 없다"


def test_presence_rows_are_byte_identical_across_runs():
    """불변식 ② — 같은 두 관측 + 같은 구조 두 장 → 바이트 동일."""

    def run() -> str:
        _f, _g, second, third, _r, _a = _deleted_then_recreated("relations")
        return json.dumps([second, third], ensure_ascii=False)

    assert run() == run()


def test_the_same_structure_never_opens_the_structure_documents():
    """평소 5분 차례 — 두 관측이 같은 구조를 기준으로 했으면 카드가 바뀔 수 없다. 구조를 안 줘도 경고 0 · 줄 0."""
    full = _fixture("relations")
    first, second = _bare(full, P1), _bare(full, P2)
    report = apply_history(second, first, ())
    assert second["changes"] == [] and report.presence_rows == 0
    assert report.presence_unpaired is False and report.warnings == ()


def test_a_kind_denied_in_the_new_structure_is_unread_not_removed():
    """🔴 권한이 사라져 API Gateway 를 못 읽은 구조 — 카드가 없어도 「없어짐」이 아니다(불변식 ④). 못 읽은 칸으로 센다.
    대조군: 같은 구조에서 읽힌 Global Accelerator 의 없어짐은 그대로 적힌다(전부 억제하는 구현을 잡는다)."""
    full = _fixture("relations")
    gone = _without(full, ("api_gateway", "global_accelerator"), api_gateway="denied")
    first, second = _bare(full, P1), _bare(gone, P2)
    report = apply_history(second, first, (gone, full))
    removed = {r["target_id"].split("/")[0] for r in _kinds(second, "resource_removed")}
    assert removed == {"global_accelerator"}
    n_api = sum(1 for n in full["nodes"] if n["type"] == "api_gateway")
    assert report.unread == n_api and n_api >= 1


def test_a_kind_the_old_structure_never_asked_for_is_not_added():
    """엔진이 종류를 새로 지원한 날 — 옛 구조는 Global Accelerator 를 묻지도 않았다. 원래 있던 가속기가 「생김」이 아니다."""
    full = _fixture("relations")
    old = _without(full, ("global_accelerator",))
    old["scope"]["resource_types"].remove("global_accelerator")
    old["coverage"] = [
        c for c in old["coverage"] if c["resource_type"] != "global_accelerator"
    ]
    old["run"]["content_digest"] = content_digest(old)
    first, second = _bare(old, P1), _bare(full, P2)
    report = apply_history(second, first, (full, old))
    assert _kinds(second, "resource_added") == []
    assert report.out_of_scope == 1 and report.presence_rows == 0
    # 거꾸로 — 새 구조가 그 종류를 더 묻지 않는다(scope 에서 뺐다). 「없어짐」도 「못 읽음」도 아니라 「범위 밖」이다
    # (보고서의 두 수는 사람에게 다른 일을 시킨다 — 못 읽음은 권한을 고치러 간다).
    third = _bare(old, P3)
    report = apply_history(third, second, (old, full))
    assert _kinds(third, "resource_removed") == []
    assert (report.out_of_scope, report.unread) == (1, 0)


def test_a_missing_structure_is_said_out_loud_not_guessed():
    """구조가 바뀌었는데 직전 관측의 구조를 못 받았다 — 다른 시각의 구조로 비교하지 않는다. 줄 0 + 경고."""
    full = _fixture("relations")
    gone = _without(full, PRESENCE_FIXTURES["relations"])
    first, second = _bare(full, P1), _bare(gone, P2)
    report = apply_history(second, first, (gone,))
    assert second["changes"] == [] and report.presence_unpaired is True
    assert any("상태 없는 카드" in w for w in report.warnings), report.warnings


def test_presence_kinds_are_exactly_the_stateless_cards():
    """🔴 #133 의 원인 = 상태 없는 종류를 새로 넣으면서 이력 쪽을 안 고쳤다. 이제 「상태 없음」 카드는 **둘 중 하나**여야 한다 —
    구조로 생김·없어짐을 내거나(`PRESENCE_ONLY_NODES`), 이유를 적고 뺀다(아래 집합 · `observe.py` 주석과 같다)."""
    from nettopo.observe import NO_STATE_NODES, PRESENCE_ONLY_NODES

    left_out = {
        "account",
        "region",
        "route_table",
        "transit_gateway_route_table",
        "external_ip",
    }
    assert set(PRESENCE_ONLY_NODES) == set(NO_STATE_NODES) - left_out
    for sources in PRESENCE_ONLY_NODES.values():
        assert sources and set(sources) <= set(SUPPORTED_RESOURCE_TYPES)
