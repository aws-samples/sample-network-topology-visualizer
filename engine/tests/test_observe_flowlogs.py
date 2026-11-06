"""흐름 로그 판독 결과를 관측 문서에 붙이는 규칙(S5 U7 · ADR 0008 「D4b 개정」 ⑤-2).

판독기(`flowlogs.py`)가 찾은 왕복 짝을 **어느 행에 어떻게** 적는가만 잰다 — 짝 찾기 자체는
`test_flowlogs.py` 가 잰다. 모든 문서는 스키마 + 계약 쪽 대조군(`schema/tests/test_observation_schema.py`
의 `flow_log_problems` 등)을 통과해야 한다. 🔴 대조군은 엔진 코드를 부르지 않는다(같이 틀린다) — 그래서
엔진 테스트가 그 파일을 **읽어서** 쓴다. 제품 코드는 그 파일을 import 하지 않는다.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import test_flowlogs as tf
from fakes import (
    ACCOUNT,
    APP_IGW,
    APP_VPC,
    EGRESS_SUBNET,
    EGRESS_VPC,
    PEERINGS,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    ROUTE_TABLES,
    FakeAws,
    FakeCloudWatch,
    factory,
    factory_by_service,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.flowlogs import (
    API_CWL,
    API_S3,
    WHY,
    FlowLogReading,
    LogResult,
    Proof,
    read_flow_logs,
    why_of,
)
from nettopo.history import apply_history
from nettopo.metrics import read_metrics
from nettopo.model import Scope, node_id
from nettopo.observe import (
    FLOW_LOG_NONE_READ,
    OBSERVED_RESOURCE_TYPES,
    build_observation,
)
from nettopo.sources import (
    _NOT_READ_BY_KIND,
    TGW_FLOW_LOG,
    VPC_FLOW_LOG,
    Source,
    SourceScan,
)

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "observation_oracle", ROOT / "schema" / "tests" / "test_observation_schema.py"
)
assert _spec is not None and _spec.loader is not None
ORACLE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ORACLE)

STRUCTURE_AT = "2026-09-28T00:00:00Z"
AT = tf.AT  # 판독기 기록의 시각이 이 창 안에 있다(`test_flowlogs.T`)
LATER = "2026-09-29T01:20:00Z"


def nid(node_type: str, native: str) -> str:
    return node_id(node_type, ACCOUNT, REGION, native)


PCX_NATIVE = "pcx-0aaa1111bbbb1111"
APP_RTB = nid("route_table", "rtb-0111aaaa2222bbbb")
EGRESS_RTB = nid("route_table", "rtb-0222bbbb3333cccc")
OWN_RTB = nid("route_table", "rtb-0333cccc4444dddd")
IGW = nid("internet_gateway", APP_IGW)
PCX = nid("vpc_peering_connection", PCX_NATIVE)
APP = nid("vpc", APP_VPC)
EGRESS = nid("vpc", EGRESS_VPC)
PUB = nid("subnet", PUBLIC_SUBNET)
PRIV = nid("subnet", PRIVATE_SUBNET)
EGR = nid("subnet", EGRESS_SUBNET)
IGW_ROUTE = f"route|{APP_RTB}|{IGW}"
APP_PCX_ROUTE = f"route|{APP_RTB}|{PCX}"
LOG = "fl-0aaa1111bbbb0001"


# ---- 세계 --------------------------------------------------------------------------------------


def _tables(
    *, pcx_state: str = "active", private_own: bool = False
) -> list[dict[str, Any]]:
    """기본 라우트 테이블 + 양쪽 VPC 의 피어링 경로. `private_own` = 프라이빗 서브넷이 **자기** 테이블을 명시 연관한다(피어링 경로 없음)."""
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[0]["Routes"].append(
        {
            "DestinationCidrBlock": "10.20.5.0/24",
            "VpcPeeringConnectionId": PCX_NATIVE,
            "State": pcx_state,
        }
    )
    tables[1]["Routes"].append(
        {
            "DestinationCidrBlock": "10.10.0.0/16",
            "VpcPeeringConnectionId": PCX_NATIVE,
            "State": "active",
        }
    )
    if private_own:
        tables.append(
            {
                "RouteTableId": "rtb-0333cccc4444dddd",
                "VpcId": APP_VPC,
                "Associations": [
                    {
                        "RouteTableAssociationId": "rtbassoc-0bbb2222",
                        "SubnetId": PRIVATE_SUBNET,
                        "AssociationState": {"State": "associated"},
                    }
                ],
                "Routes": [
                    {
                        "DestinationCidrBlock": "0.0.0.0/0",
                        "GatewayId": APP_IGW,
                        "State": "active",
                    }
                ],
                "Tags": [],
            }
        )
    return tables


def _snapshot(types: tuple[str, ...], at: str, **overrides: Any) -> Any:
    scope = Scope(account_ids=(ACCOUNT,), regions=(REGION,), resource_types=types)
    # 🔴 상태 단(observe CLI)은 상태 탐침을 켜고 돈다(`cli.py` 의 `with_state=True`) — 구조 단은 끈다.
    # 끈 채로 관측하면 RDS·ECS 등이 「규칙에 없는 값 None」(개발)이 된다(W3 실측).
    return collect(
        scope,
        factory(FakeAws(**overrides)),
        clock=lambda: at,
        with_state=at != STRUCTURE_AT,
    )


def _scan(vpc: str = "on", tgw: str = "off") -> SourceScan:
    return SourceScan(
        scanned=True,
        found=(
            Source(
                kind=VPC_FLOW_LOG,
                state=vpc,
                unit="vpc",
                enabled_ids=1 if vpc == "on" else 0,
                total_ids=2,
            ),
            Source(
                kind=TGW_FLOW_LOG,
                state=tgw,
                unit="transit_gateway",
                enabled_ids=1 if tgw == "on" else 0,
                total_ids=1,
            ),
        ),
    )


def _log(
    log_id: str = LOG,
    *,
    used: bool = True,
    attached: str | None = APP,
    kind: str = VPC_FLOW_LOG,
    level: str = "vpc",
    why_code: str | None = None,
) -> LogResult:
    return LogResult(
        id=log_id,
        level=level,
        used=used,
        kind=kind,
        account_id=ACCOUNT,
        region=REGION,
        attached_to=attached,
        why_code=why_code,
    )


def _reading(
    *proofs: Proof, resources: tuple[LogResult, ...] | None = None
) -> FlowLogReading:
    return FlowLogReading(
        resources=(_log(),) if resources is None else resources,
        proofs=proofs,
        calls=1,
    )


def _igw(subnet: str = PUB, pairs: int = 1, api: str = API_CWL) -> Proof:
    return Proof(IGW, subnet, IGW_ROUTE, pairs, api)


def _observe(
    reading: FlowLogReading | None,
    *,
    at: str = AT,
    scan: SourceScan | None = None,
    metrics: bool = True,
    **overrides: Any,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """생산과 같은 순서: 구조 → 상태 → 2층 → (흐름 로그) → 판정."""
    topology = _snapshot(
        SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, **overrides
    ).to_document()
    state = _snapshot(OBSERVED_RESOURCE_TYPES, at, **overrides)
    layer = (
        read_metrics(state, factory_by_service({"cloudwatch": FakeCloudWatch()}), at=at)
        if metrics
        else None
    )
    document, dropped = build_observation(
        state,
        topology=topology,
        observed_at=at,
        tool_version="test",
        metrics=layer,
        sources=_scan() if scan is None else scan,
        flow_logs=reading,
    )
    return document, dropped, topology


def _problems(doc: dict[str, Any], topology: dict[str, Any]) -> list[str]:
    """스키마 + 계약 대조군 전부. 빈 목록이어야 한다."""
    out = list(ORACLE.errors(doc))
    out += ORACLE.flow_log_problems(doc, topology)
    out += ORACLE.claims_without_reading(doc, topology)
    out += ORACLE.dangling_observations(doc, topology)
    written, priced, _unpriced = ORACLE.item_count_ledger(doc, topology)
    if written != priced:
        out.append(f"1층 원장: 적힌 {written} ≠ 칸에 속한 {priced}")
    m_written, m_cited = ORACLE.metric_item_count_ledger(doc)
    if m_written != m_cited:
        out.append(f"2층 원장: 적힌 {m_written} ≠ 지표를 든 {m_cited}")
    return out


def _row(doc: dict[str, Any], target_id: str) -> dict[str, Any] | None:
    return next((o for o in doc["observations"] if o["target_id"] == target_id), None)


def _peering_edges(topology: dict[str, Any]) -> list[str]:
    return sorted(
        e["id"]
        for e in topology["edges"]
        if e["type"] == "peering" and e["source"] == PCX
    )


def _source(doc: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(s for s in doc["sources"] if s["kind"] == kind)


# ---- 꺼짐 · 켰는데 아무것도 없음 --------------------------------------------------------------


def test_off_writes_no_flow_log_key() -> None:
    """🔴 끈 화면 = U6 이전 화면. 흐름 로그 키가 하나라도 새면 끈 고객의 산출물이 바뀐다."""
    doc, dropped, topology = _observe(None)
    assert "flow_log" not in doc["layers"]
    assert not any("by_subnet" in o for o in doc["observations"])
    assert not any(o["target_id"].startswith("peering|") for o in doc["observations"])
    assert not any("resources" in s for s in doc["sources"])
    # 끈 사람에게는 켜는 스위치를 말한다 — 「탐지만 한다」는 판독기가 생긴 뒤로 거짓이다(U9).
    assert _source(doc, VPC_FLOW_LOG)["why"] == _NOT_READ_BY_KIND[VPC_FLOW_LOG]
    assert "FlowLogReading" in _source(doc, VPC_FLOW_LOG)["why"]
    assert "--flow-logs" in _source(doc, VPC_FLOW_LOG)["why"]
    assert dropped == []
    assert _problems(doc, topology) == []


def test_unread_sentences_differ_by_kind() -> None:
    """판독기를 끈 문서의 사유 — TGW 로그도 이제 읽으므로(D4c ③) 켜는 스위치를 말하되 VPC 와 **다른 문장**이고, 도구가
    읽지 않는 다른 출처는 「탐지만 한다」다. VPC 문장이 다른 출처로 새면 Container Insights 를 켠 고객에게 흐름 로그 스위치를 말한다."""

    def why(kind: str) -> str:
        return Source(kind=kind, state="on").to_dict()["why"]

    assert why(TGW_FLOW_LOG) == _NOT_READ_BY_KIND[TGW_FLOW_LOG]
    assert "FlowLogReading" in why(TGW_FLOW_LOG)
    assert why(TGW_FLOW_LOG) != why(VPC_FLOW_LOG)
    assert why(VPC_FLOW_LOG) == _NOT_READ_BY_KIND[VPC_FLOW_LOG]
    assert "탐지만" in why("container_insights")
    assert why("container_insights") != why(VPC_FLOW_LOG)


def test_on_with_nothing_found_changes_only_the_layer_flag() -> None:
    off, _, _ = _observe(None)
    on, dropped, topology = _observe(_reading(resources=()))
    assert on["layers"].pop("flow_log") is True
    assert on == off
    assert dropped == []
    assert _problems(on, topology) == []


def test_flow_logs_leave_the_counts_alone() -> None:
    """1층·2층 원장은 흐름 로그를 켜도 그대로다 — 피어링 행은 흐름 로그가 만든 것이다."""
    off, _, _ = _observe(None, route_tables=_tables())
    on, _, topology = _observe(
        _reading(_igw(), Proof(PCX, PUB, APP_PCX_ROUTE, 2, API_CWL)),
        route_tables=_tables(),
    )
    assert (
        _row(on, _peering_edges(topology)[0]) is not None
    )  # 대조군: 행이 실제로 늘었다
    assert on["coverage"] == off["coverage"]
    assert _problems(on, topology) == []


# ---- 라우트 행 · IGW 카드 ----------------------------------------------------------------------


def test_igw_proof_lands_on_its_subnet_only() -> None:
    """🔴 같은 라우트 테이블을 쓰는 프라이빗 서브넷은 자기 기록이 없으면 항목이 없다 — 행 상태도 그대로."""
    doc, dropped, topology = _observe(_reading(_igw(pairs=3)))
    route = _row(doc, IGW_ROUTE)
    assert route is not None and route["status"] == "up"
    assert route["by_subnet"] == [
        {
            "subnet_id": PUB,
            "status": "live",
            "evidence": {
                "api": API_CWL,
                "metric": "flow_log",
                "datapoints": 3,
                "window_seconds": 900,
            },
        }
    ]
    card = _row(doc, IGW)
    assert card is not None and card["status"] == "live"
    assert card["evidence"]["metric"] == "flow_log"
    assert card["evidence"]["datapoints"] == 3
    assert card["evidence"]["observed_on"] == PUB
    assert card["evidence"]["api"] == API_CWL
    assert dropped == []
    assert _problems(doc, topology) == []


def test_two_subnets_are_sorted_and_the_card_sums_them() -> None:
    """프라이빗 서브넷은 메인 테이블(= 같은 테이블)을 쓴다 — 자기 기록이 있으면 자기 항목이 생긴다."""
    doc, dropped, topology = _observe(
        _reading(_igw(PRIV, 3, API_S3), _igw(PUB, 2, API_CWL))
    )
    route = _row(doc, IGW_ROUTE)
    assert route is not None
    assert [i["subnet_id"] for i in route["by_subnet"]] == sorted([PUB, PRIV])
    assert [i["evidence"]["datapoints"] for i in route["by_subnet"]] == (
        [2, 3] if PUB < PRIV else [3, 2]
    )
    card = _row(doc, IGW)
    assert card is not None
    assert card["evidence"]["datapoints"] == 5
    first = min(PUB, PRIV)
    assert card["evidence"]["observed_on"] == first
    assert card["evidence"]["api"] == (API_CWL if first == PUB else API_S3)
    assert dropped == []
    assert _problems(doc, topology) == []


def test_subnet_on_another_table_is_dropped_not_written() -> None:
    doc, dropped, topology = _observe(_reading(_igw(EGR)))
    route = _row(doc, IGW_ROUTE)
    assert route is not None and "by_subnet" not in route
    card = _row(doc, IGW)
    assert card is not None and card["status"] == "up"
    assert dropped == [f"{IGW_ROUTE}#{EGR}"]
    assert _problems(doc, topology) == []


def test_explicit_association_beats_the_main_table() -> None:
    """프라이빗 서브넷이 자기 테이블을 명시 연관하면 메인 테이블의 IGW 경로 증명이 될 수 없다."""
    doc, dropped, topology = _observe(
        _reading(_igw(PRIV)), route_tables=_tables(private_own=True)
    )
    route = _row(doc, IGW_ROUTE)
    assert route is not None and "by_subnet" not in route
    assert dropped == [f"{IGW_ROUTE}#{PRIV}"]
    # 대조군: 같은 서브넷의 자기 테이블 경로로 오면 붙는다
    own = f"route|{OWN_RTB}|{IGW}"
    doc, dropped, topology = _observe(
        _reading(Proof(IGW, PRIV, own, 1, API_CWL)),
        route_tables=_tables(private_own=True),
    )
    row = _row(doc, own)
    assert row is not None and [i["subnet_id"] for i in row["by_subnet"]] == [PRIV]
    assert dropped == []
    assert _problems(doc, topology) == []


def test_proof_for_a_route_outside_the_structure_is_dropped() -> None:
    ghost = f"route|{APP_RTB}|{nid('internet_gateway', 'igw-0fff0000ffff0000')}"
    doc, dropped, topology = _observe(
        _reading(
            Proof(IGW, PUB, ghost, 1, API_CWL), Proof(PCX, PUB, IGW_ROUTE, 1, API_CWL)
        )
    )
    assert sorted(dropped) == sorted([ghost, IGW_ROUTE])
    route = _row(doc, IGW_ROUTE)
    assert route is not None and "by_subnet" not in route
    assert _problems(doc, topology) == []


def test_blocked_route_gets_no_items_and_the_card_stays() -> None:
    """🔴 1층 막힘이 이긴다 — 기록이 있어도 블랙홀 경로에 초록 항목을 달지 않는다."""
    tables = copy.deepcopy(ROUTE_TABLES)
    for route in tables[0]["Routes"]:
        if route.get("GatewayId") == APP_IGW:
            route["State"] = "blackhole"
    doc, dropped, topology = _observe(_reading(_igw()), route_tables=tables)
    route = _row(doc, IGW_ROUTE)
    assert route is not None and route["status"] == "down"
    assert "by_subnet" not in route
    card = _row(doc, IGW)
    assert card is not None and card["status"] == "up"
    assert dropped == []
    assert _problems(doc, topology) == []


def test_detached_igw_card_is_not_greened() -> None:
    igws = [
        {
            "InternetGatewayId": APP_IGW,
            "Attachments": [{"VpcId": APP_VPC, "State": "detached"}],
            "Tags": [],
        }
    ]
    doc, _dropped, topology = _observe(_reading(_igw()), internet_gateways=igws)
    card = _row(doc, IGW)
    assert card is not None and card["status"] == "down"
    assert _problems(doc, topology) == []


# ---- 피어링 ------------------------------------------------------------------------------------


def test_subnet_proof_writes_both_peering_sides() -> None:
    doc, dropped, topology = _observe(
        _reading(Proof(PCX, PUB, APP_PCX_ROUTE, 2, API_CWL)), route_tables=_tables()
    )
    sides = _peering_edges(topology)
    assert len(sides) == 2
    for edge_id in sides:
        row = _row(doc, edge_id)
        assert row is not None and row["status"] == "live"
        assert row["evidence"] == {
            "api": API_CWL,
            "metric": "flow_log",
            "datapoints": 2,
            "window_seconds": 900,
            "observed_on": PUB,
        }
    route = _row(doc, APP_PCX_ROUTE)
    assert route is not None
    assert route["by_subnet"][0]["evidence"]["observed_on"] == PUB
    pcx = _row(doc, PCX)
    assert pcx is not None and pcx["status"] == "up"  # 노드는 1층 그대로
    assert dropped == []
    assert _problems(doc, topology) == []


def test_no_proof_no_peering_row() -> None:
    doc, _dropped, topology = _observe(_reading(_igw()), route_tables=_tables())
    assert not any(o["target_id"].startswith("peering|") for o in doc["observations"])
    assert _problems(doc, topology) == []


def test_undecided_peering_is_not_greened() -> None:
    """피어링 자체를 판정 못 했으면(규칙에 없는 상태) 선을 초록으로 만들지 않는다 — 라우트 항목은 경로의 사실이라 남는다."""
    peerings = copy.deepcopy(PEERINGS)
    # 규칙에 없는 값 — `provisioning` 은 2026-10-14(QA B P2)에 전환 상태(`down`)로 규칙에 들어갔다
    peerings[0]["Status"] = {"Code": "future-unknown-state", "Message": "Future"}
    doc, _dropped, topology = _observe(
        _reading(Proof(PCX, PUB, APP_PCX_ROUTE, 2, API_CWL)),
        route_tables=_tables(),
        peerings=peerings,
    )
    pcx = _row(doc, PCX)
    assert pcx is not None and pcx["status"] == "none"
    assert _peering_edges(topology)  # 대조군: 선은 구조에 있다
    assert not any(o["target_id"].startswith("peering|") for o in doc["observations"])
    route = _row(doc, APP_PCX_ROUTE)
    assert route is not None and len(route["by_subnet"]) == 1
    assert _problems(doc, topology) == []


UNATTRIBUTABLE = _log(why_code="subnet_unattributable")


def test_vpc_level_proof_writes_peering_when_every_table_routes_there() -> None:
    doc, dropped, topology = _observe(
        _reading(Proof(PCX, APP, None, 4, API_S3), resources=(UNATTRIBUTABLE,)),
        route_tables=_tables(),
    )
    for edge_id in _peering_edges(topology):
        row = _row(doc, edge_id)
        assert row is not None and row["status"] == "live"
        assert row["evidence"]["observed_on"] == APP
        assert row["evidence"]["datapoints"] == 4
        assert row["evidence"]["api"] == API_S3
    assert not any("by_subnet" in o for o in doc["observations"])
    assert dropped == []
    assert _problems(doc, topology) == []


@pytest.mark.parametrize(
    ("case", "proof", "resources", "tables", "dropped"),
    [
        (
            "로그가 서브넷을 가릴 수 있다",
            Proof(PCX, APP, None, 1, API_S3),
            (_log(),),
            _tables(),
            [f"{PCX}#{APP}"],
        ),
        (
            "그 VPC 가 이 피어링의 한쪽이 아니다",
            Proof(
                nid("vpc_peering_connection", "pcx-0bbb2222cccc2222"),
                APP,
                None,
                1,
                API_S3,
            ),
            (UNATTRIBUTABLE,),
            _tables(),
            [f"{nid('vpc_peering_connection', 'pcx-0bbb2222cccc2222')}#{APP}"],
        ),
        (
            "서브넷 하나가 피어링 경로 없는 테이블을 쓴다",
            Proof(PCX, APP, None, 1, API_S3),
            (UNATTRIBUTABLE,),
            _tables(private_own=True),
            [f"{PCX}#{APP}"],
        ),
        (
            "피어링 경로 하나가 막혔다",
            Proof(PCX, APP, None, 1, API_S3),
            (UNATTRIBUTABLE,),
            _tables(pcx_state="blackhole"),
            [],
        ),
    ],
)
def test_vpc_level_proof_needs_all_three_conditions(
    case: str,
    proof: Proof,
    resources: tuple[LogResult, ...],
    tables: list[dict[str, Any]],
    dropped: list[str],
) -> None:
    doc, got, topology = _observe(
        _reading(proof, resources=resources), route_tables=tables
    )
    assert not any(
        o["target_id"].startswith("peering|") for o in doc["observations"]
    ), case
    assert got == dropped, case
    assert _problems(doc, topology) == [], case


# ---- 출처 행 -----------------------------------------------------------------------------------


def test_used_log_clears_the_source_why() -> None:
    doc, _dropped, topology = _observe(
        _reading(
            _igw(),
            resources=(
                _log(),
                _log("fl-0aaa1111bbbb0002", used=False, why_code="duplicate_covered"),
            ),
        )
    )
    source = _source(doc, VPC_FLOW_LOG)
    assert source["used"] is True
    assert "why" not in source
    assert [r["id"] for r in source["resources"]] == [LOG, "fl-0aaa1111bbbb0002"]
    assert _problems(doc, topology) == []


def test_unread_logs_share_one_why_or_say_none_were_read() -> None:
    same = (
        _log(used=False, why_code="fields_missing"),
        _log("fl-0aaa1111bbbb0002", used=False, why_code="fields_missing"),
    )
    doc, _dropped, topology = _observe(_reading(resources=same))
    source = _source(doc, VPC_FLOW_LOG)
    assert source["used"] is False
    assert source["why"] == WHY["fields_missing"]
    assert _problems(doc, topology) == []

    mixed = (
        _log(used=False, why_code="fields_missing"),
        _log("fl-0aaa1111bbbb0002", used=False, why_code="access_denied"),
    )
    doc, _dropped, topology = _observe(_reading(resources=mixed))
    assert _source(doc, VPC_FLOW_LOG)["why"] == FLOW_LOG_NONE_READ
    assert _problems(doc, topology) == []


def test_tgw_log_is_listed_without_hiding_the_vpc_use() -> None:
    # 판독기는 단위별 문장(why_of)을 싣는다 — 출처 행은 그 문장을 그대로 올린다.
    tgw = dataclasses.replace(
        _log(
            "fl-0ccc3333dddd0001",
            used=False,
            kind=TGW_FLOW_LOG,
            level="transit_gateway",
            attached=None,
            why_code="shared_participant",
        ),
        why=why_of("shared_participant", "transit_gateway"),
    )
    doc, _dropped, topology = _observe(
        _reading(_igw(), resources=(_log(), tgw)), scan=_scan(tgw="on")
    )
    assert _source(doc, VPC_FLOW_LOG)["used"] is True
    tgw_row = _source(doc, TGW_FLOW_LOG)
    assert tgw_row["used"] is False
    assert tgw_row["why"] == why_of("shared_participant", "transit_gateway")
    assert _problems(doc, topology) == []


def test_attached_to_outside_the_structure_is_stripped() -> None:
    ghost = nid("vpc", "vpc-0fff0000ffff0000")
    doc, _dropped, topology = _observe(
        _reading(resources=(_log(attached=ghost, used=False, why_code="not_found"),))
    )
    [entry] = _source(doc, VPC_FLOW_LOG)["resources"]
    assert entry["id"] == LOG
    assert "attached_to" not in entry
    assert _problems(doc, topology) == []


@pytest.mark.parametrize(
    ("case", "kwargs", "resources"),
    [
        ("2층이 꺼짐", {"metrics": False}, (_log(),)),
        ("출처를 탐지하지 않음", {"scan": SourceScan.not_scanned()}, (_log(),)),
        ("출처가 켜짐이 아니다", {"scan": _scan(vpc="off")}, (_log(),)),
        (
            "출처 행이 없다",
            {"scan": SourceScan(scanned=True, found=())},
            (_log(),),
        ),
        ("모르는 종류", {}, (_log(kind="log_delivery"),)),
    ],
)
def test_contradictions_raise(
    case: str, kwargs: dict[str, Any], resources: tuple[LogResult, ...]
) -> None:
    with pytest.raises(ValueError):
        _observe(_reading(resources=resources), **kwargs)


# ---- 결정론 · 대조군이 무는지 ------------------------------------------------------------------


def test_log_rows_are_sorted_whatever_order_they_arrive_in() -> None:
    """#36 리뷰 — 판독기가 이미 id 순으로 주지만 관측은 그 순서에 기대지 않는다(불변식 ②)."""
    logs = (
        _log(),
        _log("fl-0aaa1111bbbb0002", used=False, why_code="duplicate_covered"),
    )
    a, _, _ = _observe(_reading(_igw(), resources=logs))
    b, _, _ = _observe(_reading(_igw(), resources=tuple(reversed(logs))))
    assert [r["id"] for r in _source(b, VPC_FLOW_LOG)["resources"]] == [
        LOG,
        "fl-0aaa1111bbbb0002",
    ]
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)


def test_proof_order_does_not_change_the_bytes() -> None:
    proofs = [
        _igw(PRIV, 3),
        _igw(PUB, 2),
        Proof(PCX, PUB, APP_PCX_ROUTE, 1, API_CWL),
    ]
    a, _, _ = _observe(_reading(*proofs), route_tables=_tables())
    b, _, _ = _observe(_reading(*reversed(proofs)), route_tables=_tables())
    assert json.dumps(a, ensure_ascii=False) == json.dumps(b, ensure_ascii=False)


def test_the_oracle_bites_on_an_engine_document() -> None:
    """🔴 `_problems` 가 빈 목록을 내는 것이 의미 있으려면, 같은 문서를 한 곳만 비틀었을 때 무는 것을 봐야 한다."""
    doc, _dropped, topology = _observe(_reading(_igw()))
    assert _problems(doc, topology) == []

    neighbour = copy.deepcopy(doc)
    route = _row(neighbour, IGW_ROUTE)
    assert route is not None
    route["by_subnet"].append({**route["by_subnet"][0], "subnet_id": EGR})
    route["by_subnet"].sort(key=lambda i: i["subnet_id"])
    assert any(
        "라우트 테이블을 쓰지 않는다" in p for p in _problems(neighbour, topology)
    )

    unbacked = copy.deepcopy(doc)
    card = _row(unbacked, IGW)
    assert card is not None
    card["evidence"]["observed_on"] = PRIV
    assert any("서브넷 증명이 없다" in p for p in _problems(unbacked, topology))


# ---- 판독기에서 문서까지 ----------------------------------------------------------------------


def test_reader_to_document_end_to_end() -> None:
    """손으로 만든 증명이 판독기 출력과 어긋나면 위 검사가 전부 거짓이 된다 — 실제 판독기로 한 번 통과시킨다."""
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    state = _snapshot(OBSERVED_RESOURCE_TYPES, AT)
    layer = read_metrics(
        state, factory_by_service({"cloudwatch": FakeCloudWatch()}), at=AT
    )
    lines = tf.trip(subnet_id=PUBLIC_SUBNET, srcaddr="10.10.1.5", vpc_id=APP_VPC)
    log = tf.fl(target=APP_VPC, vpc_id=APP_VPC)
    reading = read_flow_logs(state, [log], tf.Clients(tf.cwl(lines)), at=AT)
    assert reading.proofs, "판독기가 짝을 못 찾았다 — 아래 검사가 빈 판독으로 통과한다"
    doc, dropped = build_observation(
        state,
        topology=topology,
        observed_at=AT,
        tool_version="test",
        metrics=layer,
        sources=_scan(),
        flow_logs=reading,
    )
    route = _row(doc, IGW_ROUTE)
    assert route is not None
    assert [i["subnet_id"] for i in route["by_subnet"]] == [PUB]
    card = _row(doc, IGW)
    assert card is not None and card["status"] == "live"
    assert _source(doc, VPC_FLOW_LOG)["used"] is True
    assert dropped == []
    assert _problems(doc, topology) == []


# ---- 이력 --------------------------------------------------------------------------------------


def _changes(doc: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    return [c for c in doc["changes"] if c["target_id"] == target_id]


def test_history_peering_appearing_is_not_a_new_link() -> None:
    before, _, _ = _observe(_reading(_igw()), route_tables=_tables())
    after, _, topology = _observe(
        _reading(_igw(), Proof(PCX, PUB, APP_PCX_ROUTE, 1, API_CWL)),
        at=LATER,
        route_tables=_tables(),
    )
    report = apply_history(after, before)
    for edge_id in _peering_edges(topology):
        assert _row(after, edge_id) is not None
        assert _changes(after, edge_id) == []
    assert report.unmeasured >= 2
    assert report.layer_changed == 0


def test_history_turning_the_reader_on_is_a_layer_change() -> None:
    before, _, _ = _observe(None, route_tables=_tables())
    after, _, topology = _observe(
        _reading(Proof(PCX, PUB, APP_PCX_ROUTE, 1, API_CWL)),
        at=LATER,
        route_tables=_tables(),
    )
    report = apply_history(after, before)
    for edge_id in _peering_edges(topology):
        assert _changes(after, edge_id) == []
    assert report.layer_changed >= 2


def test_history_igw_live_to_up_is_not_a_status_change() -> None:
    """🔴 흐름 로그의 `up` = 「이 창에 왕복 기록 없음」 — 「흐름이 멈췄다」 줄을 쓰면 1단계가 금지한 말이다."""
    before, _, _ = _observe(_reading(_igw()))
    after, _, _ = _observe(_reading(), at=LATER)
    assert _row(before, IGW)["status"] == "live"  # type: ignore[index]
    assert _row(after, IGW)["status"] == "up"  # type: ignore[index]
    report = apply_history(after, before)
    assert _changes(after, IGW) == []
    assert report.unmeasured >= 1


def test_history_igw_live_to_down_is_still_recorded() -> None:
    igws = [
        {
            "InternetGatewayId": APP_IGW,
            "Attachments": [{"VpcId": APP_VPC, "State": "detached"}],
            "Tags": [],
        }
    ]
    before, _, _ = _observe(_reading(_igw()))
    after, _, _ = _observe(_reading(), at=LATER, internet_gateways=igws)
    apply_history(after, before)
    [row] = [c for c in _changes(after, IGW) if c["kind"] == "status_changed"]
    assert (row["from"], row["to"]) == ("live", "down")
