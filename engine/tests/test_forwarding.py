"""넘김 선(스키마 2.8.0 · ADR 0002 §9 · 사용자 결정 2026-10-20 P1-3·P1-4) — GWLB 엔드포인트 → 그 서비스를 제공하는 GWLB ·
Network Firewall 엔드포인트 → 그 방화벽.

축:
① 서브넷 → (라우트) → GWLB 엔드포인트 → (`endpoint_provider`) → GWLB → (`load_balancer_target`) → 어플라이언스 — 끝까지 이어진다
② 제공자를 못 찾으면 **지어내지 않는다** — 엔드포인트 `unresolved_refs` = 서비스 이름
③ 엔드포인트 서비스 읽기가 거부되면 GWLB 가 `partial` + 사유 · 엔드포인트는 선 없이 남는다(불변식 ④)
④ 결정론 · 상태 없음(관측 행을 만들지 않는다) · 산출물 = `fixtures/forwarding.json`
⑤ 방화벽 — 웹 서브넷의 기본 라우트 → 방화벽 엔드포인트 → (`endpoint_provider` · `firewall_endpoint`) → 방화벽
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import fwd_world as W
import jsonschema
from fakes import client_error, factory
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation
from nettopo.serialize import to_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "topology.schema.json").read_text(encoding="utf-8")
)
FIXTURE = ROOT / "fixtures" / "forwarding.json"
CLOCK = "2026-01-02T03:04:05Z"
A, R = W.ACCOUNT, W.REGION
SCOPE = Scope(account_ids=(A,), regions=(R,), resource_types=SUPPORTED_RESOURCE_TYPES)
GWLB = f"load_balancer/{A}/{R}/inspect-gwlb"
GWLBE = f"vpc_endpoint/{A}/{R}/{W.GWLBE}"
ORPHAN = f"vpc_endpoint/{A}/{R}/{W.GWLBE_ORPHAN}"
NFW = f"network_firewall/{A}/{R}/{W.FW_NAME}"
NLB = f"load_balancer/{A}/{R}/partner-nlb"
IF_EP = f"vpc_endpoint/{A}/{R}/{W.IF_EP}"
IF_EP_PARTNER = f"vpc_endpoint/{A}/{R}/{W.IF_EP_PARTNER}"
NFW_EP = f"vpc_endpoint/{A}/{R}/{W.NFW_EP}"


def _snap(client: Any, scope: Scope = SCOPE, *, state: bool = False) -> Any:
    return collect(scope, factory(client), clock=lambda: CLOCK, with_state=state)


def _doc(client: Any = None) -> dict[str, Any]:
    return _snap(client or W.world()).to_document()


def _node(doc: dict[str, Any], nid: str) -> dict[str, Any]:
    return next(n for n in doc["nodes"] if n["id"] == nid)


def _edges(doc: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [e for e in doc["edges"] if e["type"] == kind]


def test_the_inspection_path_is_connected_end_to_end():
    """① 앱 서브넷의 기본 라우트 → GWLB 엔드포인트 → GWLB → 어플라이언스."""
    doc = _doc()
    rt = f"route_table/{A}/{R}/{W.RT_APP}"
    route = [
        e for e in _edges(doc, "route") if e["source"] == rt and e["target"] == GWLBE
    ]
    assert [e["target"] for e in route] == [GWLBE]
    assert route[0]["attributes"]["destinations"] == ["0.0.0.0/0"]
    prov = [e for e in _edges(doc, "endpoint_provider") if e["source"] == GWLBE]
    assert [(e["source"], e["target"]) for e in prov] == [(GWLBE, GWLB)]
    assert prov[0]["directed"] is True
    assert prov[0]["attributes"] == {
        "service_name": W.SVC,
        "resolved_by": "endpoint_service",
    }
    assert _node(doc, GWLB)["attributes"]["endpoint_service_names"] == [W.SVC]
    lbt = [e for e in _edges(doc, "load_balancer_target") if e["source"] == GWLB]
    assert [e["target"] for e in lbt] == [f"ec2_instance/{A}/{R}/{W.APPLIANCE}"]
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))


def test_an_endpoint_whose_provider_is_not_collected_is_not_invented():
    """② 서비스를 제공하는 LB 가 이 스냅샷에 없다 — 선을 짓지 않고 서비스 이름을 남긴다."""
    doc = _doc()
    assert not [e for e in _edges(doc, "endpoint_provider") if e["source"] == ORPHAN]
    node = _node(doc, ORPHAN)
    assert node["attributes"]["unresolved_refs"] == [W.SVC_ORPHAN]
    assert (
        node["partial"] is False
    )  # 못 찾은 것은 사실이다 — 못 읽은 것이 아니다(ADR 0002 §5)


def test_a_denied_service_lookup_marks_the_providers_partial_and_draws_nothing():
    """③ 엔드포인트 서비스 읽기 거부 — GWLB 가 「일부 못 읽음」 · 키가 없다(「서비스 없음」이 아니다) · 선 없음."""
    denied = client_error(
        "UnauthorizedOperation", "DescribeVpcEndpointServiceConfigurations"
    )
    doc = _doc(W.world(errors={"describe_vpc_endpoint_service_configurations": denied}))
    gwlb = _node(doc, GWLB)
    assert gwlb["partial"] is True
    assert "ec2:DescribeVpcEndpointServiceConfigurations" in [
        r["api"] for r in gwlb["partial_reasons"]
    ]
    assert "endpoint_service_names" not in gwlb["attributes"]
    assert [e["source"] for e in _edges(doc, "endpoint_provider")] == [
        NFW_EP
    ]  # 방화벽 열쇠는 엔드포인트 서비스와 무관하다
    assert _node(doc, GWLBE)["attributes"]["unresolved_refs"] == [W.SVC]
    # 다른 선(LB → 어플라이언스 · 라우트)은 그대로다
    assert [e["target"] for e in _edges(doc, "load_balancer_target")] == [
        f"ec2_instance/{A}/{R}/{W.APPLIANCE}"
    ]


def test_read_but_empty_is_an_empty_list_not_a_missing_key():
    """읽었고 서비스가 없다 = `[]`(사실) · 못 읽었다 = 키 없음(③). 둘을 섞으면 「없음」과 「모름」이 한 모양이다."""
    doc = _doc(W.world(endpoint_services=[]))
    gwlb = _node(doc, GWLB)
    assert gwlb["attributes"]["endpoint_service_names"] == []
    assert gwlb["partial"] is False


def test_contract_pins_the_provider_edge():
    doc = _doc()
    validator = jsonschema.Draft202012Validator(SCHEMA)
    edge = next(e for e in _edges(doc, "endpoint_provider") if e["source"] == GWLBE)
    for broken in (
        {**edge, "source": GWLB},
        {**edge, "target": GWLBE},
        {**edge, "directed": False},
        {**edge, "attributes": {"service_name": W.SVC}},
        {**edge, "attributes": {**edge["attributes"], "resolved_by": "guess"}},
    ):
        bad = {**doc, "edges": [broken if e is edge else e for e in doc["edges"]]}
        assert list(validator.iter_errors(bad)), f"계약이 이 선을 받아들였다: {broken}"


def test_a_route_to_a_firewall_endpoint_reaches_the_firewall():
    """⑤(P1-4) 「이 서브넷 트래픽은 방화벽을 지난다」 — 라우트 → 방화벽 엔드포인트 → 방화벽. 방화벽 엔드포인트의 서비스는 AWS 계정 것이라
    엔드포인트 서비스 열쇠로는 못 잇는다 — 방화벽의 `firewall_endpoint_ids` 가 그 id 를 말한다."""
    doc = _doc()
    rt = f"route_table/{A}/{R}/{W.RT_WEB}"
    assert [e["target"] for e in _edges(doc, "route") if e["source"] == rt] == [NFW_EP]
    prov = [e for e in _edges(doc, "endpoint_provider") if e["source"] == NFW_EP]
    assert [(e["target"], e["attributes"]["resolved_by"]) for e in prov] == [
        (NFW, "firewall_endpoint")
    ]
    assert prov[0]["attributes"]["service_name"] == W.NFW_SVC
    assert "unresolved_refs" not in _node(doc, NFW_EP)["attributes"]
    assert _node(doc, NFW)["attributes"]["firewall_endpoint_ids"] == [W.NFW_EP]
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))
    # 방화벽을 수집하지 않으면(종류를 뺐다) 그 엔드포인트는 서비스 이름으로 찾다가 못 찾는다 — 지어내지 않는다
    narrow = Scope(
        account_ids=(A,),
        regions=(R,),
        resource_types=tuple(
            t for t in SUPPORTED_RESOURCE_TYPES if t != "network_firewall"
        ),
    )
    doc = _snap(W.world(), narrow).to_document()
    assert not [e for e in _edges(doc, "endpoint_provider") if e["source"] == NFW_EP]
    assert _node(doc, NFW_EP)["attributes"]["unresolved_refs"] == [W.NFW_SVC]


def test_an_interface_endpoint_reaches_the_nlb_behind_its_service():
    """P2-1 — 고객·파트너 PrivateLink: 인터페이스 엔드포인트 → 그 서비스를 제공하는 NLB(제공자 계정을 수집했으면) ·
    못 잇으면 「어느 계정의 서비스인가」(소비자 쪽 `service_owner`)라도 말한다. AWS 서비스 엔드포인트는 보지 않는다."""
    doc = _doc()
    prov = {e["source"]: e for e in _edges(doc, "endpoint_provider")}
    assert prov[IF_EP]["target"] == NLB
    assert prov[IF_EP]["attributes"] == {
        "service_name": W.SVC_NLB,
        "resolved_by": "endpoint_service",
    }
    assert _node(doc, NLB)["attributes"]["endpoint_service_names"] == [W.SVC_NLB]
    assert _node(doc, IF_EP)["attributes"]["service_owner"] == A
    partner = _node(doc, IF_EP_PARTNER)
    assert IF_EP_PARTNER not in prov
    assert partner["attributes"]["unresolved_refs"] == [W.SVC_PARTNER]
    assert partner["attributes"]["service_owner"] == W.PARTNER
    assert partner["partial"] is False
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))


def test_the_consumer_side_owner_is_asked_per_service_and_a_gone_service_is_a_fact():
    """소비자 쪽 주인 묻기 — 이름마다 한 번(없어진 서비스 하나가 다른 이름까지 「모름」으로 만들지 않게) · 없어진 서비스 = 사유 없음 ·
    거부 = 그 엔드포인트 `partial` + 주인 키 없음."""
    client = W.world(consumer_services={W.SVC_PARTNER: W.PARTNER})
    client._consumer_services.pop(W.SVC_ORPHAN, None)
    doc = _doc(client)
    asked = [
        kw["ServiceNames"]
        for m, kw in client.direct_calls
        if m == "describe_vpc_endpoint_services"
    ]
    assert asked and all(len(x) == 1 for x in asked)
    orphan = _node(doc, ORPHAN)
    assert "service_owner" not in orphan["attributes"] and orphan["partial"] is False
    assert _node(doc, IF_EP_PARTNER)["attributes"]["service_owner"] == W.PARTNER
    denied = client_error("UnauthorizedOperation", "DescribeVpcEndpointServices")
    doc = _doc(W.world(errors={"describe_vpc_endpoint_services": denied}))
    p = _node(doc, IF_EP_PARTNER)
    assert p["partial"] is True and "service_owner" not in p["attributes"]
    assert "ec2:DescribeVpcEndpointServices" in [r["api"] for r in p["partial_reasons"]]
    # AWS 서비스 엔드포인트(com.amazonaws.<리전>.<서비스>)는 묻지 않는다 — 주인이 이름에 있다
    assert all(".vpce-svc-" in x[0] for x in asked)


def test_a_provider_in_another_collected_account_is_reached_by_name():
    """P2-1 «다른 계정» — 제공자 NLB 가 다른 계정이어도 그 계정을 수집했으면 서비스 이름으로 잇는다(이름에 리전이 들어 있어 겹치지 않는다)."""
    from fakes import FakeAws, factory_by_pair

    B = "222233334444"
    nlb_arn = f"arn:aws:elasticloadbalancing:{R}:{B}:loadbalancer/net/provider-nlb/1a2b3c4d5e6f7093"
    provider = FakeAws.empty(
        vpcs=[
            {
                "VpcId": "vpc-0abc1234def58001",
                "CidrBlock": "10.50.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": [],
            }
        ],
        load_balancers=[
            {
                "LoadBalancerName": "provider-nlb",
                "LoadBalancerArn": nlb_arn,
                "Scheme": "internal",
                "Type": "network",
                "VpcId": "vpc-0abc1234def58001",
                "State": {"Code": "active"},
                "AvailabilityZones": [],
            }
        ],
        listeners={nlb_arn: []},
        target_groups={nlb_arn: []},
        web_acls={},
        endpoint_services=[
            {"ServiceName": W.SVC_PARTNER, "NetworkLoadBalancerArns": [nlb_arn]}
        ],
    )
    fac = factory_by_pair({(A, R): W.world(), (B, R): provider})
    scope = Scope(
        account_ids=(A, B), regions=(R,), resource_types=SUPPORTED_RESOURCE_TYPES
    )
    doc = collect(scope, fac, clock=lambda: CLOCK).to_document()
    prov = {e["source"]: e["target"] for e in _edges(doc, "endpoint_provider")}
    assert prov[IF_EP_PARTNER] == f"load_balancer/{B}/{R}/provider-nlb"
    assert "unresolved_refs" not in _node(doc, IF_EP_PARTNER)["attributes"]


def test_a_route_to_an_eni_reaches_its_owner():
    """P2-2 — 라우트의 `eni-` 대상(NVA · NAT 인스턴스 · 어플라이언스)을 그 ENI 의 주인으로 잇는다(LB IP 대상과 같은 주인 순서).
    엣지 id 끝에 ENI · 속성 `via_eni`. 없는 ENI 는 지어내지 않는다 — 테이블 `unresolved_refs` + `unresolved_route_targets`(까닭 · 목적지)."""
    doc = _doc()
    rt = f"route_table/{A}/{R}/{W.RT_APP}"
    nva = f"ec2_instance/{A}/{R}/{W.NVA}"
    e = next(
        x for x in _edges(doc, "route") if x["source"] == rt and x["target"] == nva
    )
    assert e["id"] == f"route|{rt}|{nva}|{W.NVA_ENI}"
    assert e["attributes"]["destinations"] == ["10.99.0.0/16"]
    assert e["attributes"]["via_eni"] == W.NVA_ENI
    node = _node(doc, rt)
    assert node["attributes"]["unresolved_refs"] == [W.GONE_ENI]
    assert node["attributes"]["unresolved_route_targets"] == [
        f"{W.GONE_ENI} no_eni 10.98.0.0/16"
    ]
    assert node["partial"] is False
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))
    # ENI 조회 거부 = 그 테이블 「일부 못 읽음」 · 두 ENI 다 `eni_unread`(선 없음)
    denied = client_error("UnauthorizedOperation", "DescribeNetworkInterfaces")
    doc = _doc(W.world(errors={"describe_network_interfaces": denied}))
    node = _node(doc, rt)
    assert node["partial"] is True
    assert not [
        x for x in _edges(doc, "route") if x["source"] == rt and x["target"] == nva
    ]
    assert node["attributes"]["unresolved_route_targets"] == sorted(
        [
            f"{W.GONE_ENI} eni_unread 10.98.0.0/16",
            f"{W.NVA_ENI} eni_unread 10.99.0.0/16",
        ]
    )


def test_provider_edges_have_no_observation_rows():
    """④ 상태 없음(ADR 0006 §O7) — 엔드포인트의 상태는 노드가 든다. 선에 행을 만들면 화면에 `?` 가 붙는다."""
    client = W.world()
    topology = _doc(client)
    state = _snap(
        client,
        Scope(account_ids=(A,), regions=(R,), resource_types=OBSERVED_RESOURCE_TYPES),
        state=True,
    )
    doc, dropped = build_observation(
        state, topology=topology, observed_at=CLOCK, tool_version="test"
    )
    assert not dropped
    assert not [
        o
        for o in doc["observations"]
        if o["target_id"].startswith("endpoint_provider|")
    ]
    assert [o for o in doc["observations"] if o["target_id"] == GWLBE], (
        "엔드포인트 노드 행은 있어야 한다"
    )


def test_the_fixture_is_this_world_collected():
    """`fixtures/forwarding.json` = 이 세계를 엔진에 돌린 바이트(렌더러 검사 149 의 입력) · 두 번 돌려도 같다(불변식 ②)."""
    text = to_json(_snap(W.world()))
    assert text == to_json(_snap(W.world()))
    if os.environ.get("NETTOPO_REGEN_FIXTURE") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text
