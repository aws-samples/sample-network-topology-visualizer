"""Site-to-Site VPN 3종(2차 · 스키마 2.6.0) — 게이트웨이 둘 · 연결 하나 · 비밀 제외 · 터널 상태는 탐침만.

개발 계정에 VPN 연결이 0건이라(2026-10-06 실측 · us-east-1·us-west-2·ap-northeast-2) 이 파일이 연결
경로의 유일한 측정이다. 각 검사는 살아 있는 짝을 같이 잰다 — 비밀이 없는 것만 재면 속성을 아예
안 만드는 구현도 통과하고, 선이 없는 것만 재면 선을 아예 안 만드는 구현도 통과한다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    ACCOUNT,
    APP_VPC,
    CGW,
    CORE_TGW,
    CUSTOMER_GATEWAYS,
    EGRESS_VPC,
    REGION,
    ROUTE_TABLES,
    VGW,
    VPN_CONNECTIONS,
    VPN_GATEWAYS,
    VPN_SECRET,
    VPN_TGW,
    VPN_VGW,
    FakeAws,
    factory,
)
from jsonschema import Draft202012Validator
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.serialize import to_json
from nettopo.services import vpn_connection
from test_observe import _by_id, _observe

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json"
FIXED_CLOCK = "2026-01-02T03:04:05Z"
VPN_TYPES = ("vpn_gateway", "customer_gateway", "vpn_connection")
DEAD_VGW = "vgw-0dead0000beef2222"
TGW_VPN_ATTACH = "tgw-attach-0bbb2222cccc9999"


def _id(node_type: str, native: str) -> str:
    return f"{node_type}/{ACCOUNT}/{REGION}/{native}"


def run(client: FakeAws, *, with_state: bool = False) -> dict[str, Any]:
    scope = Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES)
    return collect(
        scope, factory(client), clock=lambda: FIXED_CLOCK, with_state=with_state
    ).to_document()


def node(document: dict[str, Any], native: str) -> dict[str, Any]:
    found = [n for n in document["nodes"] if n["native_id"] == native]
    assert len(found) == 1, f"{native} 노드가 {len(found)} 개다"
    return found[0]


def edges_from(document: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    return sorted(
        (e for e in document["edges"] if e["source"] == node_id),
        key=lambda e: e["target"],
    )


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return run(FakeAws())


# ------------------------------------------------------------------ 계약


def test_vpn_types_are_in_the_contract_and_the_document(document):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    for t in VPN_TYPES:
        assert t in schema["$defs"]["node_type"]["enum"]
        assert t in schema["$defs"]["resource_type"]["enum"]
        assert t in SUPPORTED_RESOURCE_TYPES
    assert set(VPN_TYPES) <= {n["type"] for n in document["nodes"]}
    validator = Draft202012Validator(schema)
    assert [
        f"{e.json_path}: {e.message}" for e in validator.iter_errors(document)
    ] == []


# ------------------------------------------------------------------ 게이트웨이


def test_vgw_lives_under_the_region_and_attaches_to_its_vpc(document):
    vgw = node(document, VGW)
    assert vgw["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    assert vgw["name"] == "app-vgw"
    a = vgw["attributes"]
    assert (a["state"], a["type"], a["amazon_side_asn"]) == (
        "available",
        "ipsec.1",
        64512,
    )
    assert a["attached_vpc_ids"] == [APP_VPC]
    [edge] = edges_from(document, vgw["id"])
    assert (edge["type"], edge["target"]) == ("attachment", _id("vpc", APP_VPC))


def test_a_detached_vgw_stays_as_a_node_and_draws_no_line(document):
    """떨어진 VGW — 노드는 남고(지워지지 않았다) VPC 선은 없다(없는 경로를 그리지 않는다 · ADR 0002 §3)."""
    dead = node(document, DEAD_VGW)
    assert dead["attributes"]["attached_vpc_ids"] == []
    assert edges_from(document, dead["id"]) == []
    # 응답에는 VPC 가 있다(떨어진 상태로) — 선이 없는 이유가 「VPC 를 몰라서」가 아니다.
    assert dead["attributes"].get("unresolved_refs") is None
    assert EGRESS_VPC in json.dumps(VPN_GATEWAYS)


def test_cgw_carries_its_device_and_asn(document):
    cgw = node(document, CGW)
    assert cgw["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    assert cgw["name"] == "onprem-cgw"
    a = cgw["attributes"]
    assert (a["ip_address"], a["bgp_asn"], a["device_name"]) == (
        "198.51.100.10",
        65000,  # 문자열 "65000" → 정수(`BgpAsnExtended` 가 있으면 그쪽)
        "onprem-router",
    )
    assert a["uses_certificate"] is False


def test_cgw_name_falls_back_to_the_device_name():
    untagged = [
        {**g, "Tags": [], "BgpAsnExtended": "4200000001"} for g in CUSTOMER_GATEWAYS
    ]
    cgw = node(run(FakeAws(customer_gateways=untagged)), CGW)
    assert cgw["name"] == "onprem-router"
    assert cgw["attributes"]["bgp_asn"] == 4200000001


# ------------------------------------------------------------------ 연결


def test_a_vgw_connection_links_both_gateways(document):
    conn = node(document, VPN_VGW)
    assert conn["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    targets = [(e["type"], e["target"]) for e in edges_from(document, conn["id"])]
    assert targets == [
        ("attachment", _id("customer_gateway", CGW)),
        ("attachment", _id("vpn_gateway", VGW)),
    ]
    a = conn["attributes"]
    assert a["static_routes_only"] is True
    # 지워진 정적 라우트는 라우트가 아니다 — 살아 있는 짝(192.168/16)과 같이 잰다.
    assert a["static_route_destinations"] == ["192.168.0.0/16"]
    assert a["tunnel_outside_ips"] == ["203.0.113.11", "203.0.113.12"]
    assert a["tunnel_count"] == 2


def test_a_tgw_connection_is_drawn_once_through_the_tgw_attachment(document):
    """TGW ↔ VPN 은 TGW 어태치먼트(`ResourceType=vpn`)가 든다 — 연결 쪽이 TGW 선을 또 그리면 같은 사실이 두 줄이다."""
    conn = node(document, VPN_TGW)
    targets = [e["target"] for e in edges_from(document, conn["id"])]
    assert targets == [_id("customer_gateway", CGW)]
    assert conn["attributes"]["transit_gateway_id"] == CORE_TGW
    assert conn["attributes"]["vpn_gateway_id"] is None
    attach = _id("transit_gateway_attachment", TGW_VPN_ATTACH)
    assert conn["id"] in {e["target"] for e in edges_from(document, attach)}
    # 그 어태치먼트에 못 그린 참조가 남아 있지 않다(2.6.0 전에는 `vpn-…` 이 unresolved 였다).
    attach_node = next(n for n in document["nodes"] if n["id"] == attach)
    assert VPN_TGW not in json.dumps(
        attach_node["attributes"].get("unresolved_refs", [])
    )


def test_a_vpc_route_to_the_vgw_resolves_to_the_vgw_node():
    """`route_table.py` 는 `vgw-` 대상을 이미 냈다 — 2.6.0 부터 그 끝에 노드가 있다."""
    tables = copy.deepcopy(ROUTE_TABLES)
    table = next(t for t in tables if t["VpcId"] == APP_VPC)
    table["Routes"].append(
        {"DestinationCidrBlock": "192.168.0.0/16", "GatewayId": VGW, "State": "active"}
    )
    document = run(FakeAws(route_tables=tables))
    rtb = _id("route_table", table["RouteTableId"])
    routes = [e for e in edges_from(document, rtb) if e["type"] == "route"]
    assert _id("vpn_gateway", VGW) in {e["target"] for e in routes}
    # 이어졌으니 미해결 참조에 남지 않는다(2.13.0 — 경로 표 `routes` 는 콘솔처럼 대상 id 를 그대로 든다)
    assert VGW not in (
        node(document, table["RouteTableId"])["attributes"].get("unresolved_refs") or []
    )
    assert (
        "192.168.0.0/16 " + VGW + " active -"
        in node(document, table["RouteTableId"])["attributes"]["routes"]
    )


def test_vpn_secrets_never_reach_the_snapshot():
    """PSK(터널 옵션)·장비 설정 XML 은 응답에 **평문으로 있지만** 산출물에 없다 — 상태를 켜도 같다."""
    for with_state in (False, True):
        text = to_json(
            collect(
                Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES),
                factory(FakeAws()),
                clock=lambda: FIXED_CLOCK,
                with_state=with_state,
            )
        )
        assert VPN_SECRET not in text, with_state
        assert "PreSharedKey" not in text and "<vpn_connection>" not in text
        # 살아 있는 짝: 같은 응답의 비밀 아닌 값(터널 바깥 IP)은 들어와 있다.
        assert "203.0.113.11" in text


def test_tunnel_status_is_not_collected_into_the_structure():
    """터널 UP/DOWN 은 시시각각 바뀐다 — 구조에 담으면 불변식 ② 가 깨진다(상태 탐침 몫)."""
    baseline = run(FakeAws())
    flipped = copy.deepcopy(VPN_CONNECTIONS)
    for conn in flipped:
        for t in conn.get("VgwTelemetry") or []:
            t["Status"] = "DOWN" if t["Status"] == "UP" else "UP"
            t["AcceptedRouteCount"] = 99
    assert run(FakeAws(vpn_connections=flipped)) == baseline
    conn = node(baseline, VPN_TGW)
    assert conn["attributes"]["state"] == "available"
    # 대조군: 상태 단에서는 같은 응답이 다르게 읽힌다(탐침이 살아 있다).
    on = run(FakeAws(), with_state=True)
    assert node(on, VPN_TGW)["attributes"]["state"] == "available-one-tunnel-down"
    assert node(on, VPN_VGW)["attributes"]["state"] == "available"


# ------------------------------------------------------------------ 상태 탐침 · 판정


@pytest.mark.parametrize(
    ("item", "value"),
    [
        (
            {
                "State": "available",
                "VgwTelemetry": [{"Status": "UP"}, {"Status": "UP"}],
            },
            "available",
        ),
        (
            {
                "State": "available",
                "VgwTelemetry": [{"Status": "UP"}, {"Status": "DOWN"}],
            },
            "available-one-tunnel-down",
        ),
        (
            {
                "State": "available",
                "VgwTelemetry": [{"Status": "DOWN"}, {"Status": "DOWN"}],
            },
            "available-all-tunnels-down",
        ),
        # 원격 측정이 없다 = 터널 정보를 못 받았다 → 지어내지 않고 연결 상태 그대로.
        ({"State": "available", "VgwTelemetry": []}, "available"),
        ({"State": "pending", "VgwTelemetry": [{"Status": "DOWN"}]}, "pending"),
        ({"State": "deleted"}, "deleted"),
        ({}, None),
    ],
)
def test_probe_folds_state_and_tunnels(item, value):
    assert vpn_connection.state(item) == value


def _vpn_observation(**overrides: Any) -> dict[str, Any]:
    document, _ = _observe(**overrides)
    return _by_id(document)[_id("vpn_connection", VPN_TGW)]


def test_one_tunnel_down_is_up_and_says_so():
    o = _vpn_observation()
    assert o["status"] == "up"
    assert "터널 일부가 DOWN" in o["why"]
    assert o["evidence"]["api"] == "ec2:DescribeVpnConnections"


def test_all_tunnels_down_is_down():
    dead = copy.deepcopy(VPN_CONNECTIONS)
    for t in dead[0]["VgwTelemetry"]:
        t["Status"] = "DOWN"
    o = _vpn_observation(vpn_connections=dead)
    assert o["status"] == "down"
    assert "터널이 전부 DOWN" in o["why"]


def test_gateways_are_judged_from_their_state():
    document, _ = _observe()
    by_id = _by_id(document)
    assert by_id[_id("vpn_gateway", VGW)]["status"] == "up"
    assert by_id[_id("customer_gateway", CGW)]["status"] == "up"
