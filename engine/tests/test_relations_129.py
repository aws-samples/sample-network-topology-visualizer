"""#129(스키마 2.11.0 · ADR 0002 §13 · target-gap §C 15·16) — 오버레이·서비스 네트워크: TGW Connect · Cloud WAN · VPC Lattice.

| 축 | 무엇 | 잇는 법 |
|---|---|---|
| ⑥ TGW Connect | Connect 어태치먼트(GRE) → 그것이 올라탄 VPC·DX 어태치먼트 + GRE 피어 | `connect_transport` · 속성 `connect_peers` |
| ⑦ Cloud WAN | 코어 네트워크(글로벌 · API = us-west-2) ↔ VPC · 라우트 `CoreNetworkArn` → 코어 네트워크 | `core_network_attachment` · `route` |
| ⑧ VPC Lattice | 서비스 네트워크 ↔ VPC · ↔ 서비스 · 서비스 → EC2·ALB·Lambda | `lattice_vpc_association` · `lattice_service_association` · `lattice_target` |

세계는 `rel_world.py`(배치 D 와 같은 세계 · `fixtures/relations.json` = 그 산출물 · `test_relations_d.py::test_the_fixture_is_this_world_collected`).
축마다 모양 · 못 읽음(거부 = 그 노드의 사유 · 키 없음 · 불변식 ④) · 안 부를 때는 안 부른다(최소 호출)를 잰다. 🔴 실계정에는 셋 다 없었다 — 가짜로만 쟀다.
"""

from __future__ import annotations

import json
import re
from typing import Any

import rel_world as RW
from fakes import FakeAws, client_error, factory
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import GLOBAL, Scope
from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation
from test_services import coverage_of, node_of, run

CLOCK = "2026-01-02T03:04:05Z"
SCOPE = Scope((RW.ACCOUNT,), (RW.REGION,), SUPPORTED_RESOURCE_TYPES)


def _doc(client: FakeAws | None = None, scope: Scope = SCOPE) -> dict[str, Any]:
    return collect(
        scope, factory(client or RW.world()), clock=lambda: CLOCK
    ).to_document()


def _wid(kind: str, native: str) -> str:
    return f"{kind}/{RW.ACCOUNT}/{RW.REGION}/{native}"


def _out(doc: dict[str, Any], kind: str, source: str) -> dict[str, dict[str, Any]]:
    return {
        e["target"]: e
        for e in doc["edges"]
        if e["type"] == kind and e["source"] == source
    }


# ------------------------------------------------------------------ ⑥ TGW Connect

CONNECT = _wid("transit_gateway_attachment", RW.CONNECT_VPC)
CONNECT_DX = _wid("transit_gateway_attachment", RW.CONNECT_DX)
CONNECT_GONE = _wid("transit_gateway_attachment", RW.CONNECT_GONE)


def test_a_connect_attachment_rides_on_its_vpc_or_dx_transport() -> None:
    doc = _doc()
    assert set(_out(doc, "connect_transport", CONNECT)) == {
        _wid("transit_gateway_attachment", RW.TGW_ATT)
    }
    assert set(_out(doc, "connect_transport", CONNECT_DX)) == {
        _wid("transit_gateway_attachment", RW.DX_ATT)
    }
    e = _out(doc, "connect_transport", CONNECT)[
        _wid("transit_gateway_attachment", RW.TGW_ATT)
    ]
    assert e["directed"] is True and e["attributes"] == {"state": "available"}
    a = node_of(doc, CONNECT)["attributes"]
    assert a["transport_attachment_id"] == RW.TGW_ATT and a["connect_protocol"] == "gre"
    # 지워진 피어는 싣지 않는다 · BGP 세션 상태는 구조에 새지 않는다(불변식 ②)
    assert a["connect_peers"] == [
        f"{RW.PEER_A} gre 10.99.0.10 {RW.SDWAN_IP} 169.254.100.0/29 64512 65010 sdwan-a"
    ]
    text = json.dumps(node_of(doc, CONNECT))
    assert (
        RW.PEER_DELETED not in text and '"up"' not in text and "BgpStatus" not in text
    )
    # 연결 노드는 TGW 쪽 선 하나만 그대로 — 전송을 `attachment` 로 잇지 않는다(선 종류를 섞지 않는다)
    att = _out(doc, "attachment", CONNECT)
    assert set(att) == {f"transit_gateway/{RW.ACCOUNT}/{RW.REGION}/{RW.TGW}"}


def test_a_connect_whose_transport_is_not_collected_says_so() -> None:
    doc = _doc()
    n = node_of(doc, CONNECT_GONE)
    assert not _out(doc, "connect_transport", CONNECT_GONE)
    assert n["attributes"]["unresolved_refs"] == [RW.GONE_TRANSPORT]
    # 피어를 읽었고 없다 = [] (못 읽음과 다르다)
    assert n["attributes"]["connect_peers"] == [] and n["partial"] is False


def test_a_denied_connect_read_keeps_the_transport_from_the_listing() -> None:
    client = RW.world()
    client._errors["describe_transit_gateway_connects"] = client_error(
        "UnauthorizedOperation", "DescribeTransitGatewayConnects"
    )
    doc = _doc(client)
    n = node_of(doc, CONNECT)
    assert n["partial"] is True
    assert "ec2:DescribeTransitGatewayConnects" in [
        r["api"] for r in n["partial_reasons"]
    ]
    # 전송은 목록의 `ResourceId` 로 그대로 잇는다 · 프로토콜은 모른다(키 없음)
    assert "connect_protocol" not in n["attributes"]
    assert _out(doc, "connect_transport", CONNECT)
    # VPC 어태치먼트는 그 거부와 무관하다
    assert (
        node_of(doc, _wid("transit_gateway_attachment", RW.TGW_ATT))["partial"] is False
    )


def test_a_denied_peer_read_is_not_an_empty_peer_list() -> None:
    """불변식 ④ — 「피어 없음」(`[]`)과 「피어를 못 읽음」(키 없음 + partial)이 같은 모양이면 안 된다."""
    client = RW.world()
    client._errors["describe_transit_gateway_connect_peers"] = client_error(
        "UnauthorizedOperation", "DescribeTransitGatewayConnectPeers"
    )
    doc = _doc(client)
    n = node_of(doc, CONNECT)
    assert n["partial"] is True and "connect_peers" not in n["attributes"]
    assert "ec2:DescribeTransitGatewayConnectPeers" in [
        r["api"] for r in n["partial_reasons"]
    ]


def test_no_connect_attachment_means_no_connect_call() -> None:
    client = FakeAws()
    run(client)
    assert "describe_transit_gateway_connects" not in client.calls
    assert "describe_transit_gateway_connect_peers" not in client.calls


# ------------------------------------------------------------------ ⑦ Cloud WAN

CORE = f"core_network/{RW.ACCOUNT}/{GLOBAL}/{RW.CORE}"


def test_the_cloud_wan_api_is_called_in_us_west_2_whatever_the_scan_regions() -> None:
    seen: list[tuple[str, str]] = []
    client = RW.world()

    def fac(account: str, region: str, service: str) -> Any:
        seen.append((service, region))
        return client

    collect(
        Scope((RW.ACCOUNT,), ("ap-northeast-2",), SUPPORTED_RESOURCE_TYPES),
        fac,
        clock=lambda: CLOCK,
    )
    assert {r for s, r in seen if s == "networkmanager"} == {"us-west-2"}
    # Lattice 는 리전 서비스 — 수집 리전 그대로
    assert {r for s, r in seen if s == "vpc-lattice"} == {"ap-northeast-2"}


def test_world_core_network_reaches_its_vpc_and_names_what_it_cannot() -> None:
    doc = _doc()
    cn = node_of(doc, CORE)
    assert cn["parent_id"] == f"account/{RW.ACCOUNT}" and cn["region"] is None
    assert cn["name"] == "rel-core"
    out = _out(doc, "core_network_attachment", CORE)
    assert set(out) == {_wid("vpc", RW.VPC)}
    e = out[_wid("vpc", RW.VPC)]
    # 지우는 중인 연결(dev)은 선에 없다 · 방향 없음
    assert e["directed"] is False and e["attributes"] == {
        "attachment_ids": ["attachment-0rel1234abcd0211"],
        "segments": ["prod"],
        "edge_locations": [RW.REGION],
        "state": "available",
    }
    a = cn["attributes"]
    assert a["unresolved_attachments"] == [
        f"{RW.EU_VPC} not_in_scope eu-west-1",
        f"{RW.GONE_VPC} not_collected",
    ]
    assert a["other_attachments"] == [
        f"attachment-0rel1234abcd0214 connect {RW.REGION}"
    ]
    assert a["attachment_count"] == 4 and a["edge_locations"] == [
        "eu-west-1",
        RW.REGION,
    ]
    # 코어 네트워크 State(UPDATING)는 구조에 새지 않는다(불변식 ②)
    assert "UPDATING" not in json.dumps(cn) and "DELETING" not in json.dumps(
        doc["edges"]
    )


def test_a_core_network_route_target_resolves_to_the_core_network() -> None:
    """target-gap §C 15 — 라우트 `CoreNetworkArn` 이 지금까지 `unresolved_refs` 였다."""
    doc = _doc()
    app = _wid("route_table", RW.RT_APP)
    e = _out(doc, "route", app)[CORE]
    assert e["attributes"]["destinations"] == [RW.CORE_CIDR]
    assert RW.CORE not in (node_of(doc, app)["attributes"].get("unresolved_refs") or [])
    assert RW.CORE_ARN not in json.dumps(node_of(doc, app)["attributes"])


def test_an_uncollected_core_network_route_is_unresolved_by_its_id() -> None:
    client = RW.world()
    key, _ = client._data["list_core_networks"]
    client._data["list_core_networks"] = (key, [])
    doc = _doc(client)
    app = node_of(doc, _wid("route_table", RW.RT_APP))
    assert RW.CORE in app["attributes"]["unresolved_refs"]
    assert not [e for e in doc["edges"] if e["target"] == CORE]


def test_cloud_wan_reads_are_partial_failures_of_that_core_network() -> None:
    client = RW.world()
    client._followup_errors[("list_attachments", RW.CORE)] = client_error(
        "AccessDeniedException", "ListAttachments"
    )
    doc = _doc(client)
    cn = node_of(doc, CORE)
    assert cn["partial"] is True and "networkmanager:ListAttachments" in [
        r["api"] for r in cn["partial_reasons"]
    ]
    assert "attachment_count" not in cn["attributes"]
    assert not _out(doc, "core_network_attachment", CORE)
    client = RW.world()
    client._errors["list_core_networks"] = client_error(
        "AccessDeniedException", "ListCoreNetworks"
    )
    assert coverage_of(_doc(client), "core_network")["status"] == "denied"


def test_a_shared_view_yields_to_the_owner_in_scope() -> None:
    """공유받은 계정의 응답은 자기 연결만 본다 — 소유 계정이 범위 안이면 그 뷰만 노드다(먼저 만난 뷰가 이기지 않게)."""
    member = "444455556666"
    owner_client = RW.world()
    member_client = RW.world()
    key, items = member_client._data["list_core_networks"]
    member_client._data["list_core_networks"] = (key, items)
    member_client._keyed["list_attachments"] = (
        "Attachments",
        "CoreNetworkId",
        {RW.CORE: []},
    )

    def fac(account: str, region: str, service: str) -> Any:
        return member_client if account == member else owner_client

    doc = collect(
        Scope((member, RW.ACCOUNT), (RW.REGION,), ("vpc", "core_network")),
        fac,
        clock=lambda: CLOCK,
    ).to_document()
    cns = [n for n in doc["nodes"] if n["type"] == "core_network"]
    assert [n["id"] for n in cns] == [CORE]
    assert _out(doc, "core_network_attachment", CORE)
    # 그 계정 coverage 는 목록 그대로(항목 수 1 · 노드를 안 만든 것과 무관)
    rows = [c for c in doc["coverage"] if c["resource_type"] == "core_network"]
    assert {c["account_id"]: c["item_count"] for c in rows} == {
        member: 1,
        RW.ACCOUNT: 1,
    }


def test_the_core_network_is_one_node_across_scan_regions() -> None:
    scope = Scope((RW.ACCOUNT,), (RW.REGION, "us-west-2"), SUPPORTED_RESOURCE_TYPES)
    doc = _doc(scope=scope)
    assert [n["id"] for n in doc["nodes"]].count(CORE) == 1


# ------------------------------------------------------------------ ⑧ VPC Lattice

SN = _wid("lattice_service_network", RW.SN_ID)
ORDERS = _wid("lattice_service", RW.SVC_ORDERS)
STATUS = _wid("lattice_service", RW.SVC_STATUS)


def test_world_service_network_reaches_its_vpc_and_services() -> None:
    doc = _doc()
    sn = node_of(doc, SN)
    assert (
        sn["parent_id"] == f"region/{RW.ACCOUNT}/{RW.REGION}" and sn["name"] == "rel-sn"
    )
    assert set(_out(doc, "lattice_vpc_association", SN)) == {_wid("vpc", RW.VPC)}
    assert set(_out(doc, "lattice_service_association", SN)) == {ORDERS, STATUS}
    a = sn["attributes"]
    # 지우는 중인 연관은 선도 수도 아니다 · 이 세계에 없는 VPC · 서비스는 까닭 줄
    assert a["vpc_association_count"] == 2 and a["service_association_count"] == 3
    assert a["unresolved_vpc_associations"] == [f"{RW.LATTICE_GONE_VPC} not_collected"]
    assert a["unresolved_service_associations"] == ["partner-svc not_collected"]


def test_world_service_reaches_ec2_alb_and_lambda_targets() -> None:
    doc = _doc()
    out = _out(doc, "lattice_target", ORDERS)
    assert set(out) == {
        _wid("ec2_instance", RW.APP1),
        _wid("load_balancer", "int-alb"),
        _wid("lambda_function", RW.FN),
    }
    assert out[_wid("ec2_instance", RW.APP1)]["attributes"] == {
        "target_groups": ["orders-ec2"],
        "ports": [8080],
        "target_type": "instance",
    }
    assert out[_wid("lambda_function", RW.FN)]["attributes"]["ports"] == []
    a = node_of(doc, ORDERS)["attributes"]
    assert a["unresolved_lattice_targets"] == [
        f"{RW.LATTICE_IP} ip_target",
        f"{RW.GONE_INSTANCE} not_collected",
    ]
    assert a["target_groups"] == [
        "orders-alb alb HTTP 80",
        "orders-ec2 instance HTTP 8080",
        "orders-fn lambda - -",
        "orders-ip ip HTTP 8080",
    ]
    s = node_of(doc, STATUS)["attributes"]
    assert s["empty_target_groups"] == ["status-empty"] and not _out(
        doc, "lattice_target", STATUS
    )
    # 대상 헬스는 구조에 새지 않는다(불변식 ②)
    text = json.dumps([e for e in doc["edges"] if e["type"] == "lattice_target"])
    assert "HEALTHY" not in text and "UNAVAILABLE" not in text


def test_lattice_reads_are_partial_failures_of_that_resource() -> None:
    client = RW.world()
    client._errors["list_target_groups"] = client_error(
        "AccessDeniedException", "ListTargetGroups"
    )
    doc = _doc(client)
    for sid in (ORDERS, STATUS):
        n = node_of(doc, sid)
        assert n["partial"] is True and "target_groups" not in n["attributes"]
        assert "vpc-lattice:ListTargetGroups" in [
            r["api"] for r in n["partial_reasons"]
        ]
    assert not [e for e in doc["edges"] if e["type"] == "lattice_target"]
    client = RW.world()
    client._followup_errors[("list_targets", "tg-0rel1234abcd0341")] = client_error(
        "AccessDeniedException", "ListTargets"
    )
    doc = _doc(client)
    n = node_of(doc, ORDERS)
    assert n["partial"] is True and "vpc-lattice:ListTargets" in [
        r["api"] for r in n["partial_reasons"]
    ]
    # 못 읽은 그룹의 대상만 빠진다 — 다른 그룹 선은 그대로 · 빈 그룹으로 적지 않는다
    assert _wid("ec2_instance", RW.APP1) not in _out(doc, "lattice_target", ORDERS)
    assert _wid("load_balancer", "int-alb") in _out(doc, "lattice_target", ORDERS)
    assert "orders-ec2" not in (n["attributes"].get("empty_target_groups") or [])
    assert node_of(doc, STATUS)["partial"] is False
    client = RW.world()
    client._followup_errors[("list_service_network_vpc_associations", RW.SN_ID)] = (
        client_error("AccessDeniedException", "ListServiceNetworkVpcAssociations")
    )
    doc = _doc(client)
    sn = node_of(doc, SN)
    assert sn["partial"] is True and "vpc_association_count" not in sn["attributes"]
    assert not _out(doc, "lattice_vpc_association", SN)
    assert _out(doc, "lattice_service_association", SN)


def test_no_lattice_service_means_no_target_group_call() -> None:
    client = FakeAws()
    run(client)
    assert (
        "list_target_groups" not in client.calls and "list_targets" not in client.calls
    )


def test_an_unused_target_group_is_not_asked_for_its_targets() -> None:
    """이 쌍의 서비스가 쓰지 않는 그룹(`unused`)은 대상을 묻지 않는다 — 그룹마다 한 번씩만(쓰는 그룹 5개)."""
    client = RW.world()
    _doc(client)
    assert client.calls.count("list_targets") == 5
    assert client.calls.count("list_target_groups") == 1


# ------------------------------------------------------------------ 관측 — #129 종류는 상태가 없다


def test_129_types_have_no_observation_rows() -> None:
    topology = _doc()
    state = collect(
        Scope((RW.ACCOUNT,), (RW.REGION,), OBSERVED_RESOURCE_TYPES),
        factory(RW.world()),
        clock=lambda: "2026-01-02T04:00:00Z",
        with_state=True,
    )
    obs, _ = build_observation(
        state, topology=topology, observed_at="2026-01-02T04:00:00Z", tool_version="t"
    )
    kinds = {re.split(r"[/|]", o["target_id"])[0] for o in obs["observations"]}
    new = {
        "core_network",
        "lattice_service_network",
        "lattice_service",
        "connect_transport",
        "core_network_attachment",
        "lattice_vpc_association",
        "lattice_service_association",
        "lattice_target",
    }
    present = {n["type"] for n in topology["nodes"]} | {
        e["type"] for e in topology["edges"]
    }
    assert new <= present, new - present
    assert not kinds & new, kinds & new
    # Connect 어태치먼트 자신은 TGW 어태치먼트 규칙으로 판정된다(상태가 있다)
    assert CONNECT in {o["target_id"] for o in obs["observations"]}
