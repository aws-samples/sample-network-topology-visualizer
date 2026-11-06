"""Direct Connect 3종(2차 첫 종류, ADR 0010) — 글로벌 리소스·비밀 제외·수동 페이지네이션.

여기까지의 리소스는 전부 리전 리소스였고 API 는 전부 boto3 페이지네이터가 있었다. 그래서
① 「어느 리전에서 봐도 노드 하나」 ② 「`nextToken` 을 손으로 돌린다」 ③ 「응답의 비밀 필드를
버린다」는 코드가 없어서 **미측정**이었다. 각 검사는 살아 있는 짝을 같이 잰다 — 비밀이 없는
것만 재면 속성을 아예 만들지 않는 구현도 통과한다.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    ACCOUNT,
    CORE_TGW,
    DIRECT_CONNECT_CONNECTIONS,
    DIRECT_CONNECT_GATEWAYS,
    DIRECT_CONNECT_VIRTUAL_INTERFACES,
    DX_CONNECTION,
    DX_CONNECTION_DOWN,
    DX_DELETING_VIF,
    DX_HOSTED_VIF,
    DX_PRIVATE_VIF,
    DX_PUBLIC_VIF,
    DX_PEER_ADDRESSES,
    DX_SECRETS,
    DXGW,
    DXGW_TGW,
    REGION,
    FakeAws,
    client_error,
    factory,
    factory_by_region,
)
from jsonschema import Draft202012Validator
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.history import apply_history, target_cells
from nettopo.model import GLOBAL, Scope
from nettopo.observe import (
    NODE_RULES,
    OBSERVED_RESOURCE_TYPES,
    _judge,
    build_observation,
)
from nettopo.serialize import to_json
from nettopo.services import direct_connect_virtual_interface
from test_history import T1, T2, _document
from test_observe import OBSERVED_AT, SCHEMA, STRUCTURE_AT, _by_id, _observe

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json"
FIXED_CLOCK = "2026-01-02T03:04:05Z"
SEOUL = "ap-northeast-2"
DX_TYPES = (
    "direct_connect_connection",
    "direct_connect_virtual_interface",
    "direct_connect_gateway",
)

CONNECTION_ID = f"direct_connect_connection/{ACCOUNT}/{REGION}/{DX_CONNECTION}"
DXGW_ID = f"direct_connect_gateway/{ACCOUNT}/global/{DXGW}"
DXGW_TGW_ID = f"direct_connect_gateway/{ACCOUNT}/global/{DXGW_TGW}"


def run(client: FakeAws, regions: tuple[str, ...] = (REGION,)) -> dict:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=regions, resource_types=SUPPORTED_RESOURCE_TYPES
    )
    return collect(scope, factory(client), clock=lambda: FIXED_CLOCK).to_document()


def node(document: dict, native: str) -> dict:
    found = [n for n in document["nodes"] if n["native_id"] == native]
    assert len(found) == 1, f"{native} 노드가 {len(found)} 개다"
    return found[0]


def edges_from(document: dict, node_id: str) -> list[dict]:
    return [e for e in document["edges"] if e["source"] == node_id]


@pytest.fixture(scope="module")
def document() -> dict:
    return run(FakeAws())


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ 계약


def test_direct_connect_types_are_in_the_contract_and_the_document(document, validator):
    """스키마 2.1.0 의 3종이 실제로 수집돼 계약을 통과한다 — 어느 한쪽만 늘리면 여기서 깨진다."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for t in DX_TYPES:
        assert t in schema["$defs"]["node_type"]["enum"]
        assert t in schema["$defs"]["resource_type"]["enum"]
        assert t in SUPPORTED_RESOURCE_TYPES
    present = {n["type"] for n in document["nodes"]}
    assert set(DX_TYPES) <= present
    assert [
        f"{e.json_path}: {e.message}" for e in validator.iter_errors(document)
    ] == []


# ------------------------------------------------------------------ 연결


def test_connection_lives_under_the_region_and_drops_secrets(document):
    conn = node(document, DX_CONNECTION)
    assert conn["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    assert (
        conn["name"] == "dx-seoul-primary"
    )  # Name 태그(소문자 key/value 표기)가 이름이다
    a = conn["attributes"]
    assert (a["state"], a["location"], a["bandwidth"]) == (
        "available",
        "EqSe2",
        "10Gbps",
    )
    assert a["aws_device"] == "EqSe2-device-A"
    assert a["encryption_mode"] == "should_encrypt"
    # MACsec 키 목록·LOA 발급 시각·포트 암호화 **상태**는 싣지 않는다 — MACsec 은 구성 값 하나로만 말한다.
    assert not {k for k in a if "key" in k.lower() or "loa" in k.lower()}
    assert "port_encryption_status" not in a


# ------------------------------------------------------------------ VIF


def test_vif_parent_is_its_connection_and_a_hosted_vif_falls_back_to_the_region(
    document,
):
    """연결을 지우면 VIF 도 사라진다 → 부모(ADR 0002 §1). hosted VIF 의 연결은 파트너 소유라
    목록에 없다 → 리전으로 내려가되 **partial 은 아니다**(연결 수집이 실패한 게 아니다)."""
    private = node(document, DX_PRIVATE_VIF)
    assert private["parent_id"] == CONNECTION_ID
    hosted = node(document, DX_HOSTED_VIF)
    assert hosted["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    assert hosted["partial"] is False
    assert hosted["attributes"]["state"] == "confirming"


def test_vif_to_dxgw_is_an_attachment_and_a_dead_vif_draws_no_line(document):
    private = node(document, DX_PRIVATE_VIF)
    [edge] = edges_from(document, private["id"])
    assert (edge["type"], edge["target"], edge["directed"]) == (
        "attachment",
        DXGW_ID,
        False,
    )
    assert edge["attributes"]["virtual_interface_type"] == "private"
    # 삭제 중인 VIF: 노드는 남고 선은 없다 — 없는 경로를 있다고 그리지 않는다(ADR 0002 §3).
    deleted = node(document, DX_DELETING_VIF)
    assert edges_from(document, deleted["id"]) == []
    assert "unresolved_refs" not in deleted["attributes"]


def test_public_vif_without_a_gateway_is_normal_not_a_broken_reference(document):
    public = node(document, DX_PUBLIC_VIF)
    a = public["attributes"]
    assert a["direct_connect_gateway_id"] is None
    assert a["virtual_gateway_id"] is None
    assert "unresolved_refs" not in a
    assert a["route_filter_prefixes"] == ["198.51.100.0/24", "203.0.113.0/24"]
    # 피어는 주소 체계별 구성만, 정렬돼 있다(입력은 ipv6 가 먼저였다).
    assert a["bgp_peers"] == ["ipv4:65002", "ipv6:65002"]


def test_four_byte_asn_reads_asn_long(document):
    private = node(document, DX_PRIVATE_VIF)
    assert private["attributes"]["asn"] == 4200000001
    assert private["attributes"]["bgp_peers"] == ["ipv4:4200000001"]


def test_secrets_never_reach_the_snapshot():
    """BGP 인증키·MACsec CKN·Secrets Manager ARN·라우터 설정은 응답에 **있지만** 산출물에 없다.

    피어 주소(Amazon·고객 쪽)는 비밀이 아니다 — 콘솔이 보여 주는 값이라 싣는다(사용자 결정 2026-10-24 · 아래 검사).
    """
    text = to_json(
        collect(
            Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES),
            factory(FakeAws()),
            clock=lambda: FIXED_CLOCK,
        )
    )
    leaked = [s for s in DX_SECRETS if s in text]
    assert leaked == [], f"비밀이 산출물에 남았다: {leaked}"
    # 살아 있는 짝: 같은 응답의 비밀 아닌 값은 들어와 있다(속성을 아예 안 만드는 구현이 아니다).
    assert "4200000001" in text
    assert "should_encrypt" in text


def test_bgp_peer_addresses_are_shown_like_the_console(document):
    """콘솔 VIF 「피어링」 표의 Amazon·고객 쪽 주소를 싣는다 — 인증 키·BGP 상태는 줄에 없다."""
    private = node(document, DX_PRIVATE_VIF)
    rows = private["attributes"]["bgp_peer_details"]
    assert rows and all(
        DX_PEER_ADDRESSES[0] in r and DX_PEER_ADDRESSES[1] in r for r in rows
    )
    assert not any(DX_SECRETS[0] in r for r in rows)
    assert not any(r.split("\t")[-1] in ("up", "down") for r in rows)


def test_bgp_status_is_not_collected_into_the_structure():
    """`bgpStatus` up/down 은 시시각각 바뀐다 — 구조에 담으면 불변식 ②가 깨진다(관측 계약 몫)."""
    baseline = run(FakeAws())
    flipped = [dict(v) for v in DIRECT_CONNECT_VIRTUAL_INTERFACES]
    for vif in flipped:
        vif["bgpPeers"] = [
            {**p, "bgpStatus": "down" if p.get("bgpStatus") == "up" else "up"}
            for p in vif.get("bgpPeers") or []
        ]
    assert run(FakeAws(dx_virtual_interfaces=flipped)) == baseline
    for n in baseline["nodes"]:
        for key in n["attributes"]:
            assert "status" not in key, f"상태가 구조에 섞였다: {n['id']}.{key}"


# ------------------------------------------------------------------ DXGW — 글로벌 리소스


def test_dxgw_is_one_node_across_regions_with_no_region_and_the_account_as_parent():
    """리전마다 같은 목록이 나오는 글로벌 리소스. id 에 조회 리전을 넣으면 DXGW 하나가 리전
    수만큼 늘어난다. coverage 는 호출이 일어난 리전마다 남는다(다른 사실이다)."""
    document = run(FakeAws(), regions=(REGION, SEOUL))
    dxgws = [n for n in document["nodes"] if n["type"] == "direct_connect_gateway"]
    assert sorted(n["id"] for n in dxgws) == sorted([DXGW_ID, DXGW_TGW_ID])
    for n in dxgws:
        assert n["region"] is None
        assert n["parent_id"] == f"account/{ACCOUNT}"
        assert "/global/" in n["id"]
    covered = [
        c
        for c in document["coverage"]
        if c["resource_type"] == "direct_connect_gateway"
    ]
    assert sorted(c["region"] for c in covered) == sorted([REGION, SEOUL])
    assert all(c["status"] == "ok" and c["item_count"] == 2 for c in covered)


def test_dxgw_associations_land_in_attributes_as_scalars(document):
    dxgw = node(document, DXGW)
    a = dxgw["attributes"]
    assert a["state"] == "available"
    assert a["associated_virtual_private_gateway_ids"] == ["vgw-0aaa1111bbbb2222"]
    assert a["associated_transit_gateway_ids"] == []
    # 해제된(disassociated) 연결의 리전·계정은 관계가 아니다 — 들어오지 않는다.
    assert a["associated_regions"] == ["us-west-2"]
    assert dxgw["name"] == "corp-dxgw"
    tgw_side = node(document, DXGW_TGW)["attributes"]
    assert tgw_side["associated_transit_gateway_ids"] == [CORE_TGW]
    assert tgw_side["associated_regions"] == [REGION]
    assert "totalPrefixPoolAllocations" not in json.dumps(tgw_side)


def test_the_dxgw_to_tgw_line_comes_from_the_tgw_attachment_only(document):
    """같은 사실을 두 번 그리지 않는다 — DXGW ↔ TGW 는 어태치먼트 노드의 `attachment` 하나다."""
    incoming = [e for e in document["edges"] if e["target"] == DXGW_TGW_ID]
    assert [e["type"] for e in incoming] == ["attachment"]
    assert incoming[0]["source"].startswith("transit_gateway_attachment/")
    # DXGW 노드 자신은 선을 내지 않는다(연결은 속성이다).
    assert edges_from(document, DXGW_TGW_ID) == []
    attachment = node(document, "tgw-attach-0ddd4444eeee1111")
    assert "unresolved_refs" not in attachment["attributes"]


def test_a_denied_association_read_marks_only_that_dxgw_partial(validator):
    """불변식 ④ — DXGW 하나의 후속 호출만 거부됐을 때: 노드는 남고 partial + 사유, 연결 키는
    **없다**(≠ `[]`), 다른 DXGW 는 온전하고 coverage 는 여전히 ok(목록 호출은 성공했다)."""
    denied = FakeAws(
        followup_errors={
            ("describe_direct_connect_gateway_associations", DXGW): client_error(
                "AccessDeniedException", "DescribeDirectConnectGatewayAssociations"
            )
        }
    )
    document = run(denied)
    hit = node(document, DXGW)
    assert hit["partial"] is True
    assert [r["code"] for r in hit["partial_reasons"]] == ["access_denied"]
    assert hit["partial_reasons"][0]["api"] == (
        "directconnect:DescribeDirectConnectGatewayAssociations"
    )
    assert "associated_transit_gateway_ids" not in hit["attributes"]
    other = node(document, DXGW_TGW)
    assert other["partial"] is False
    assert other["attributes"]["associated_transit_gateway_ids"] == [CORE_TGW]
    [entry] = [
        c
        for c in document["coverage"]
        if c["resource_type"] == "direct_connect_gateway"
    ]
    assert (entry["status"], entry["item_count"]) == ("ok", 2)
    validator.validate(document)


# ------------------------------------------------------------------ 수동 페이지네이션


def test_unpaginated_apis_are_read_to_the_last_page():
    """`DescribeConnections`·`DescribeVirtualInterfaces` 는 페이지네이터가 없다. 첫 장만 읽으면
    뒷장의 연결이 **조용히** 사라진다 — 실패가 아니라 「없음」으로 그려지므로 최악이다."""
    client = FakeAws()
    document = run(client)
    counts = {c["resource_type"]: c["item_count"] for c in document["coverage"]}
    assert counts["direct_connect_connection"] == len(DIRECT_CONNECT_CONNECTIONS)
    assert counts["direct_connect_virtual_interface"] == len(
        DIRECT_CONNECT_VIRTUAL_INTERFACES
    )
    assert client.calls.count("describe_connections") == 2
    # 두 번째 호출은 첫 응답의 토큰을 **같은 이름의 인자**로 되돌려 보낸다.
    tokens = [
        kw.get("nextToken")
        for m, kw in client.direct_calls
        if m == "describe_connections"
    ]
    assert tokens == [None, "page-2"]


def test_a_failing_unpaginated_call_is_a_coverage_failure_not_a_crash():
    client = FakeAws(
        errors={
            "describe_connections": client_error(
                "AccessDeniedException", "DescribeConnections"
            )
        }
    )
    document = run(client)
    [entry] = [
        c
        for c in document["coverage"]
        if c["resource_type"] == "direct_connect_connection"
    ]
    assert (entry["status"], entry["item_count"]) == ("denied", None)
    assert entry["reason"]["api"] == "directconnect:DescribeConnections"
    # 연결 수집이 실패했으므로 VIF 의 부모 후보(연결)가 없다 → 리전으로 내려가고 그 사유가 붙는다.
    private = node(document, DX_PRIVATE_VIF)
    assert private["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    assert private["partial"] is True
    assert private["partial_reasons"][0]["api"] == "directconnect:DescribeConnections"


# ------------------------------------------------------------------ 1층 관측(ADR 0006 §O7 「DX 3종」 · 2026-10-13)
#
# 🔴 실계정의 DX 는 전부 `available` + BGP up 이다(2026-10-13 실측) — 판정이 통째로 죽어도 같은 그림이 나온다.
# `down`·`none` 경로의 정본은 아래 가짜다(`observe.py` 머리말과 같은 이유).


CONNECTION_DOWN_ID = (
    f"direct_connect_connection/{ACCOUNT}/{REGION}/{DX_CONNECTION_DOWN}"
)

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "observation_oracle_dx", _ROOT / "schema" / "tests" / "test_observation_schema.py"
)
assert _spec is not None and _spec.loader is not None
ORACLE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ORACLE)


def _vif_id(native: str) -> str:
    return f"direct_connect_virtual_interface/{ACCOUNT}/{REGION}/{native}"


def _vifs_with(native: str, statuses: list[str]) -> list[dict[str, Any]]:
    """VIF 데이터셋 변형 — `native` VIF 를 available 로 두고 BGP 피어 상태만 바꾼다(피어 수 = 상태 수)."""
    data = copy.deepcopy(DIRECT_CONNECT_VIRTUAL_INTERFACES)
    for vif in data:
        if vif["virtualInterfaceId"] == native:
            vif["virtualInterfaceState"] = "available"
            vif["bgpPeers"] = [
                {"addressFamily": "ipv4", "asn": 65000 + i, "bgpStatus": s}
                for i, s in enumerate(statuses)
            ]
    return data


def _oracle_problems(doc: dict[str, Any], topo: dict[str, Any]) -> list[str]:
    out = list(ORACLE.errors(doc))
    out += ORACLE.claims_without_reading(doc, topo)
    out += ORACLE.dangling_observations(doc, topo)
    out += ORACLE.out_of_scope_observations(doc, topo)
    out += ORACLE.silent_where_read(doc, topo, ORACLE.o7_required_entry())
    out += ORACLE.states_outside_the_table(doc, topo)
    written, priced, _ = ORACLE.item_count_ledger(doc, topo)
    if written != priced:
        out.append(f"1층 원장: 적힌 {written} ≠ 칸에 속한 {priced}")
    return out


@pytest.mark.parametrize(
    ("item", "value"),
    [
        (
            {"virtualInterfaceState": "available", "bgpPeers": [{"bgpStatus": "up"}]},
            "available",
        ),
        (
            {
                "virtualInterfaceState": "available",
                "bgpPeers": [{"bgpStatus": "up"}, {"bgpStatus": "down"}],
            },
            "available-some-bgp-down",
        ),
        (
            {
                "virtualInterfaceState": "available",
                "bgpPeers": [{"bgpStatus": "down"}, {"bgpStatus": "DOWN"}],
            },
            "available-all-bgp-down",
        ),
        # 피어 정보가 없다 = BGP 를 못 봤다 → 지어내지 않고 VIF 상태 그대로.
        ({"virtualInterfaceState": "available", "bgpPeers": []}, "available"),
        ({"virtualInterfaceState": "available"}, "available"),
        # 수명주기가 먼저다 — 전환 중 VIF 의 BGP down 은 당연하므로 접지 않는다.
        (
            {
                "virtualInterfaceState": "confirming",
                "bgpPeers": [{"bgpStatus": "down"}],
            },
            "confirming",
        ),
        ({"virtualInterfaceState": "deleted"}, "deleted"),
        ({}, None),
    ],
)
def test_vif_probe_folds_state_and_bgp_peers(item, value):
    assert direct_connect_virtual_interface.state(item) == value


def test_bgp_fold_runs_only_in_the_state_tier():
    """구조 바이트는 BGP 와 무관하다(불변식 ②) — 대조군: 같은 응답을 상태 단은 다르게 읽는다(탐침이 살아 있다)."""
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )

    def once(with_state: bool, data: list[dict[str, Any]]) -> Any:
        return collect(
            scope,
            factory(FakeAws(dx_virtual_interfaces=data)),
            clock=lambda: FIXED_CLOCK,
            with_state=with_state,
        )

    dead = _vifs_with(DX_PRIVATE_VIF, ["down", "down"])
    healthy = _vifs_with(DX_PRIVATE_VIF, ["up", "up"])
    assert to_json(once(False, dead)) == to_json(once(False, healthy))
    structure = once(False, dead).to_document()
    assert node(structure, DX_PRIVATE_VIF)["attributes"]["state"] == "available"
    assert (
        node(once(True, dead).to_document(), DX_PRIVATE_VIF)["attributes"]["state"]
        == "available-all-bgp-down"
    )
    # BGP 피어 수(관측 `bgp` 의 원료)도 상태 단에만 있다 — 구조에 수가 섞이면 BGP 가 흔들릴 때마다 구조가 바뀐다
    attrs = node(structure, DX_PRIVATE_VIF)["attributes"]
    assert {"bgp_peer_count", "bgp_up_count"} & set(attrs) == set()
    live = node(once(True, dead).to_document(), DX_PRIVATE_VIF)["attributes"]
    assert (live["bgp_peer_count"], live["bgp_up_count"]) == (2, 0)


# 🔴 규칙의 사본이 아니라 **기대값**이다(ADR 0006 W3 규칙 2 · 위임 범위 2026-10-13).
_DX_EXPECTED = {
    # (up, down, 지우는 중) — 지우는 중 = `none` + `deleting`(사용자 결정 2026-10-21 · QA C · ADR 0006 §O7)
    "direct_connect_connection": (
        ("available",),
        ("ordering", "requested", "pending", "down", "rejected"),
        ("deleting", "deleted"),
    ),
    "direct_connect_virtual_interface": (
        ("available", "available-some-bgp-down"),
        (
            "available-all-bgp-down",
            "confirming",
            "verifying",
            "pending",
            "down",
            "testing",
            "rejected",
        ),
        ("deleting", "deleted"),
    ),
    "direct_connect_gateway": (("available",), ("pending",), ("deleting", "deleted")),
}


@pytest.mark.parametrize(
    ("node_type", "value", "status"),
    [
        (t, v, status)
        for t, (ups, downs, deletings) in _DX_EXPECTED.items()
        for status, values in (("up", ups), ("down", downs), ("deleting", deletings))
        for v in values
    ]
    # AWS 의 `unknown` 과 목록 밖 값은 「모른다」 — `up` 으로도 `down` 으로도 삼키지 않는다.
    + [(t, "unknown", "none") for t in _DX_EXPECTED]
    + [("direct_connect_gateway", "associating", "none")],
)
def test_each_dx_value_lands_where_w3_rule_2_puts_it(node_type, value, status):
    judged = _judge(NODE_RULES[node_type], value, f"{node_type}/x", "node")
    if status == "deleting":
        assert (judged.status, judged.deleting, judged.partial) == ("none", True, False)
        assert repr(value) in (judged.why or "") and "삭제 중" in (judged.why or "")
    else:
        assert judged.status == status and judged.deleting is False
    if status == "none":
        assert judged.partial and repr(value) in (judged.why or "")
    if status == "down":
        assert judged.error_count == 1 and judged.why
    assert judged.evidence["api"].startswith("directconnect:Describe")


def test_dx_rules_are_exactly_the_value_lists():
    """규칙에 값을 몰래 더하면(특히 `up` 쪽) 여기서 FAIL 한다."""
    for node_type, (ups, downs, deletings) in _DX_EXPECTED.items():
        rule = NODE_RULES[node_type]
        assert rule.up == frozenset(ups), node_type
        assert rule.down == frozenset(downs), node_type
        assert rule.deleting == frozenset(deletings), node_type
    assert set(_DX_EXPECTED) <= set(OBSERVED_RESOURCE_TYPES)


def test_dx_rows_end_to_end():
    """가짜 계정 그대로: 연결 up/down · VIF 4종(BGP up · 미수락 · public · 삭제 중) · DXGW 2개(글로벌)."""
    document, dropped = _observe()
    by_id = _by_id(document)
    assert dropped == []
    assert by_id[CONNECTION_ID]["status"] == "up"
    down = by_id[CONNECTION_DOWN_ID]
    assert (down["status"], down["why"]) == ("down", "AWS 가 보고한 state = 'down'")
    assert down["evidence"]["api"] == "directconnect:DescribeConnections"
    assert by_id[_vif_id(DX_PRIVATE_VIF)]["status"] == "up"
    assert by_id[_vif_id(DX_PRIVATE_VIF)]["why"] is None
    assert by_id[_vif_id(DX_HOSTED_VIF)]["status"] == "down"  # confirming = 미수락
    assert by_id[_vif_id(DX_PUBLIC_VIF)]["status"] == "up"
    # 삭제 중 VIF = 「이상」이 아니다(사용자 결정 2026-10-21 · QA C) — 판정하지 않고 까닭을 단다
    deleting_vif = by_id[_vif_id(DX_DELETING_VIF)]
    assert (deleting_vif["status"], deleting_vif.get("deleting")) == ("none", True)
    for dxgw in (DXGW_ID, DXGW_TGW_ID):
        row = by_id[dxgw]
        assert row["status"] == "up", dxgw
        assert row["evidence"]["api"] == "directconnect:DescribeDirectConnectGateways"


def test_all_bgp_peers_down_is_down_and_some_down_is_up_with_a_reason():
    dead, _ = _observe(
        dx_virtual_interfaces=_vifs_with(DX_PRIVATE_VIF, ["down", "down"])
    )
    row = _by_id(dead)[_vif_id(DX_PRIVATE_VIF)]
    assert row["status"] == "down"
    assert "BGP 피어가 전부 down" in row["why"]
    half, _ = _observe(dx_virtual_interfaces=_vifs_with(DX_PRIVATE_VIF, ["up", "down"]))
    row = _by_id(half)[_vif_id(DX_PRIVATE_VIF)]
    assert row["status"] == "up"
    assert "BGP 피어 일부가 down" in row["why"]


def test_vif_state_attributes_count_peers_and_never_invent_zero():
    """「몇 개 중 몇 개」(화면 「BGP 일부 down 1/2」). `up` 은 AWS 가 up 이라고 한 피어만 · 키가 없으면 수도 없다."""
    f = direct_connect_virtual_interface.state_attributes
    peers = [{"bgpStatus": "up"}, {"bgpStatus": "DOWN"}, {"bgpStatus": "unknown"}]
    assert f({"bgpPeers": peers}) == {"bgp_peer_count": 3, "bgp_up_count": 1}
    assert f({"bgpPeers": []}) == {"bgp_peer_count": 0, "bgp_up_count": 0}
    assert f({}) == {}


@pytest.mark.parametrize(
    ("statuses", "status", "bgp"),
    [
        (["up", "up"], "up", {"total": 2, "up": 2}),
        (["up", "down"], "up", {"total": 2, "up": 1}),
        (["down", "down"], "down", {"total": 2, "up": 0}),
    ],
)
def test_bgp_counts_ride_on_available_vif_rows_without_changing_the_verdict(
    statuses, status, bgp
):
    """관측 `bgp`(스키마 1.6.0) — 판정은 탐침이 접은 값 그대로이고 수만 더한다. 문서는 계약을 지킨다."""
    document, _ = _observe(dx_virtual_interfaces=_vifs_with(DX_PRIVATE_VIF, statuses))
    row = _by_id(document)[_vif_id(DX_PRIVATE_VIF)]
    assert (row["status"], row.get("bgp")) == (status, bgp)
    assert [e.message for e in Draft202012Validator(SCHEMA).iter_errors(document)] == []


def test_bgp_counts_stay_off_vifs_judged_by_lifecycle_and_off_other_rows():
    """미수락(`confirming`)·삭제 중 VIF 는 BGP 를 보지 않고 판정했다 — 「BGP up 0/1」을 붙이면 이유를 BGP 로 읽는다.
    대조군: 같은 문서의 available VIF 에는 수가 있다(키를 아예 안 만드는 구현이 아니다)."""
    document, _ = _observe()
    by_id = _by_id(document)
    assert by_id[_vif_id(DX_PRIVATE_VIF)].get("bgp") is not None
    for native in (DX_HOSTED_VIF, DX_DELETING_VIF):
        assert "bgp" not in by_id[_vif_id(native)], native
    others = [
        o["target_id"]
        for o in document["observations"]
        if "bgp" in o
        and not o["target_id"].startswith("direct_connect_virtual_interface/")
    ]
    assert others == []


def test_bgp_is_only_allowed_on_vif_rows():
    """스키마가 다른 행의 `bgp` 와 모양이 틀린 `bgp` 를 거부한다 — 대조군으로 VIF 행에서는 통과한다."""
    document, _ = _observe()
    validator = Draft202012Validator(SCHEMA)
    assert not list(validator.iter_errors(document))
    conn = _by_id(document)[CONNECTION_ID]
    conn["bgp"] = {"total": 1, "up": 1}
    assert list(validator.iter_errors(document))
    del conn["bgp"]
    vif = _by_id(document)[_vif_id(DX_PRIVATE_VIF)]
    vif["bgp"] = {"total": 1}
    assert list(validator.iter_errors(document))
    vif["bgp"] = {"total": 1, "up": 1}
    assert not list(validator.iter_errors(document))


def test_an_unknown_connection_state_is_none_with_the_raw_value():
    data = copy.deepcopy(DIRECT_CONNECT_CONNECTIONS)
    for c in data:
        if c["connectionId"] == DX_CONNECTION:
            c["connectionState"] = "unknown"
    document, _ = _observe(dx_connections=data)
    row = _by_id(document)[CONNECTION_ID]
    assert (row["status"], row["partial"]) == ("none", True)
    assert "'unknown'" in row["why"]
    assert row["partial_reasons"][0]["code"] == "api_error"


def test_a_denied_vif_list_silences_only_the_vifs():
    """불변식 ④ — VIF 목록이 거부되면 VIF 행이 없고(`none` 도 아니다) 칸이 `denied` 다. 연결·DXGW 는 남는다."""
    errors = {
        "describe_virtual_interfaces": client_error(
            "AccessDeniedException", "DescribeVirtualInterfaces"
        )
    }
    document, _ = _observe(errors=errors)
    kinds = {o["target_id"].split("/")[0] for o in document["observations"]}
    assert "direct_connect_virtual_interface" not in kinds
    assert {"direct_connect_connection", "direct_connect_gateway"} <= kinds
    cell = next(
        c
        for c in document["coverage"]
        if c["resource_type"] == "direct_connect_virtual_interface"
        and c["layer"] == "describe"
    )
    assert (cell["status"], cell["item_count"]) == ("denied", None)


def test_a_partial_dxgw_is_still_judged_from_its_list_state():
    """DXGW 의 후속 호출(association)만 거부 — 노드는 partial 이지만 `state` 는 목록 응답에 있으므로 판정한다."""
    denied = {
        ("describe_direct_connect_gateway_associations", DXGW): client_error(
            "AccessDeniedException", "DescribeDirectConnectGatewayAssociations"
        )
    }
    document, _ = _observe(followup_errors=denied)
    assert _by_id(document)[DXGW_ID]["status"] == "up"


# ---- 글로벌 자리 — DXGW 는 `region` 이 null 이다


def _two_region_observation(
    deny_dxgw_in: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """구조는 두 리전 다 읽었고, 상태 단에서 `deny_dxgw_in` 리전의 DXGW 목록만 거부된다."""
    regions = (REGION, SEOUL)
    topology = collect(
        Scope(
            account_ids=(ACCOUNT,),
            regions=regions,
            resource_types=SUPPORTED_RESOURCE_TYPES,
        ),
        factory(FakeAws()),
        clock=lambda: STRUCTURE_AT,
    ).to_document()
    deny = {
        "describe_direct_connect_gateways": client_error(
            "AccessDeniedException", "DescribeDirectConnectGateways"
        )
    }
    clients = {r: FakeAws(errors=deny if r in deny_dxgw_in else None) for r in regions}
    state = collect(
        Scope(
            account_ids=(ACCOUNT,),
            regions=regions,
            resource_types=OBSERVED_RESOURCE_TYPES,
        ),
        factory_by_region(clients),
        clock=lambda: OBSERVED_AT,
        with_state=True,
    )
    document, _ = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )
    return document, topology


def _dxgw_cells(document: dict[str, Any]) -> dict[str, tuple[str, int | None]]:
    return {
        c["region"]: (c["status"], c["item_count"])
        for c in document["coverage"]
        if c["resource_type"] == "direct_connect_gateway" and c["layer"] == "describe"
    }


def test_a_global_node_is_judged_when_any_region_read_it_and_counted_once():
    """한 리전이 거부되고 다른 리전이 읽었다 → DXGW 는 판정되고, 읽은 칸 **하나**에만 센다(원장 등식)."""
    document, topology = _two_region_observation(deny_dxgw_in=(SEOUL,))
    by_id = _by_id(document)
    assert by_id[DXGW_ID]["status"] == by_id[DXGW_TGW_ID]["status"] == "up"
    assert _dxgw_cells(document) == {REGION: ("ok", 2), SEOUL: ("denied", None)}
    assert _oracle_problems(document, topology) == []


def test_a_global_node_lives_in_the_alphabetically_first_ok_region():
    """둘 다 읽었으면 이름순 첫 리전(`ap-northeast-2` < `us-east-1`) 칸에 센다 — 응답·scope 순서에 기대지 않는다."""
    document, topology = _two_region_observation(deny_dxgw_in=())
    assert _dxgw_cells(document) == {SEOUL: ("ok", 2), REGION: ("ok", 0)}
    assert _by_id(document)[DXGW_ID]["status"] == "up"
    assert _oracle_problems(document, topology) == []


def test_a_global_node_read_nowhere_has_no_row():
    """규칙 2 — 어느 리전도 못 읽었으면 행을 만들지 않는다(`none` 도 아니다). 그 사실은 coverage 가 말한다."""
    document, topology = _two_region_observation(deny_dxgw_in=(REGION, SEOUL))
    by_id = _by_id(document)
    assert DXGW_ID not in by_id and DXGW_TGW_ID not in by_id
    assert _dxgw_cells(document) == {REGION: ("denied", None), SEOUL: ("denied", None)}
    # 대조군: 리전 노드인 VIF 는 그대로 판정된다(게이트가 전부를 죽인 것이 아니다).
    assert by_id[_vif_id(DX_PRIVATE_VIF)]["status"] == "up"
    assert _oracle_problems(document, topology) == []


def test_the_oracles_now_see_the_global_path():
    """🔴 오라클의 판별력 — 2026-10-13 전에는 `region is None` 을 건너뛰어 아래 두 위조가 **둘 다 통과**했다."""
    unread, topology = _two_region_observation(deny_dxgw_in=(REGION, SEOUL))
    forged = copy.deepcopy(unread)
    row = copy.deepcopy(_by_id(unread)[_vif_id(DX_PRIVATE_VIF)])
    row.update(target_id=DXGW_ID, why=None)
    forged["observations"] = sorted(
        [*forged["observations"], row], key=lambda o: o["target_id"]
    )
    assert any(DXGW_ID in p for p in ORACLE.claims_without_reading(forged, topology))

    read, topology = _two_region_observation(deny_dxgw_in=())
    silent = copy.deepcopy(read)
    silent["observations"] = [
        o for o in silent["observations"] if o["target_id"] != DXGW_ID
    ]
    assert ORACLE.silent_where_read(silent, topology, ORACLE.o7_required_entry()) == [
        DXGW_ID
    ]


def test_global_nodes_have_a_history_cell():
    """이력 칸 = (계정, global, 종류). 옛 판은 `None`(칸 미상)이라 DXGW 변경이 이력에서 통째로 빠졌다."""
    assert target_cells(DXGW_ID, "node") == (
        (ACCOUNT, GLOBAL, "direct_connect_gateway"),
    )
    edge = f"attachment|{_vif_id(DX_PRIVATE_VIF)}|{DXGW_ID}"
    assert target_cells(edge, "edge") == (
        (ACCOUNT, REGION, "direct_connect_virtual_interface"),
        (ACCOUNT, GLOBAL, "direct_connect_gateway"),
    )
    # 계정 컨테이너는 여전히 칸이 없다.
    assert target_cells(f"account/{ACCOUNT}", "node") is None


def _dxgws(state: str) -> list[dict[str, Any]]:
    data = copy.deepcopy(DIRECT_CONNECT_GATEWAYS)
    for g in data:
        if g["directConnectGatewayId"] == DXGW:
            g["directConnectGatewayState"] = state
    return data


def test_a_dxgw_state_change_is_recorded_in_history():
    before = _document(T1)
    after = _document(T2, dx_gateways=_dxgws("pending"))
    report = apply_history(after, before)
    rows = [r for r in after["changes"] if r["target_id"] == DXGW_ID]
    assert [(r["kind"], r["from"], r["to"]) for r in rows] == [
        ("status_changed", "up", "down")
    ]
    assert report.unknown_cell == 0
    # 지우는 중으로 바뀐 것은 적지 않는다(사용자 결정 2026-10-21 · QA C) — 「없어짐」은 목록에서 빠질 때 한 줄이다.
    deleting = _document(T2, dx_gateways=_dxgws("deleting"))
    report = apply_history(deleting, before)
    assert [r for r in deleting["changes"] if r["target_id"] == DXGW_ID] == []
    assert report.deleting >= 1


def test_losing_the_dxgw_permission_is_unread_not_deleted():
    """권한을 잃은 날 DXGW 두 개가 「삭제됨」으로 적히면 이력이 막으려는 거짓이다 — 「못 읽음」으로 센다."""
    before = _document(T1)
    deny = {
        "describe_direct_connect_gateways": client_error(
            "AccessDeniedException", "DescribeDirectConnectGateways"
        )
    }
    after = _document(T2, state_errors=deny)
    report = apply_history(after, before)
    assert [
        r for r in after["changes"] if "direct_connect_gateway/" in r["target_id"]
    ] == []
    assert report.unread >= 2
    # 대조군: 정말 사라지면 적힌다(읽었는데 없다).
    gone = [g for g in DIRECT_CONNECT_GATEWAYS if g["directConnectGatewayId"] != DXGW]
    later = _document(T2, dx_gateways=gone)
    apply_history(later, before)
    assert [r["kind"] for r in later["changes"] if r["target_id"] == DXGW_ID] == [
        "resource_removed"
    ]


def test_with_the_metric_layer_on_dx_cells_say_not_measured():
    """2층은 후속 — 연결·VIF 칸은 `skipped`(안 했다), DXGW 는 지표가 없는 종류라 `ok`+0 이다."""
    document = _document(T1, series={})
    metric = {
        c["resource_type"]: (c["status"], c["item_count"])
        for c in document["coverage"]
        if c["layer"] == "metric" and c["resource_type"].startswith("direct_connect_")
    }
    assert metric == {
        "direct_connect_connection": ("skipped", None),
        "direct_connect_virtual_interface": ("skipped", None),
        "direct_connect_gateway": ("ok", 0),
    }
