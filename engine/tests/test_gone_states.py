"""없어진 상태는 노드로 만들지 않는다 — ADR 0002 §3 개정(2026-10-14 · QA B 실측).

사람이 겪는 일: 스택을 지우고 나서도 AWS 는 NAT `deleted` · EC2 `terminated` · TGW 계열 `deleted` 를
1시간 넘게 돌려준다. 그것을 카드로 그리면 화면이 지워진 자원을 한 시간 동안 그리고, 이력은 「없어짐」
대신 「[끊김] 정상 → 이상」을 적는다(Auto Scaling 축소 · 스택 철거마다). 그래서 수집이 노드째 버리고,
「방금까지 있었다」는 이력의 `resource_removed` + `was` 가 싣는다(ADR 0006 #11).

🔴 반대쪽도 같이 잰다: 자원이 아직 **있는** 전이·실패 상태(`deleting`·`stopped`·`failed`…)는 남아야
한다. 「죽은 것은 다 버린다」로 구현하면 철거 중·고장 난 카드가 화면에서 사라진다 — 그것이 더 나쁘다.

픽스처는 기본 가짜 계정(`fakes.py`)의 **첫 항목 하나만** 상태를 바꾼다. 나머지 응답은 그대로이므로
「그 노드 하나만 빠지고 다른 것은 다 남는다」를 기본 문서와 대조해서 잴 수 있다.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fakes
import pytest
from fakes import ACCOUNT, ECS_SERVICE_ARN, REGION, FakeAws, factory
from jsonschema import Draft202012Validator
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.serialize import to_json
from nettopo.services.base import GONE_STATES, gone

FIXED_CLOCK = "2026-01-02T03:04:05Z"
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json").read_text(
        encoding="utf-8"
    )
)


def _top(field: str) -> Callable[[dict[str, Any], str], None]:
    def put(item: dict[str, Any], state: str) -> None:
        item[field] = state

    return put


def _nested(outer: str, inner: str) -> Callable[[dict[str, Any], str], None]:
    def put(item: dict[str, Any], state: str) -> None:
        item[outer] = {**(item.get(outer) or {}), inner: state}

    return put


# 종류 → (데이터셋 이름, native id 키, 상태를 바꾸는 함수, 남아야 하는 전이·실패 상태)
# 🔴 남기는 상태는 `DEAD_STATES`(엣지를 끊는 집합)와 겹친다 — 그것이 이 표의 요점이다.
_CASES: dict[
    str, tuple[str, str, Callable[[dict[str, Any], str], None], tuple[str, ...]]
] = {
    "nat_gateway": (
        "nat_gateways",
        "NatGatewayId",
        _top("State"),
        ("deleting", "failed"),
    ),
    "transit_gateway": (
        "transit_gateways",
        "TransitGatewayId",
        _top("State"),
        ("deleting",),
    ),
    "transit_gateway_route_table": (
        "tgw_route_tables",
        "TransitGatewayRouteTableId",
        _top("State"),
        ("deleting",),
    ),
    "transit_gateway_attachment": (
        "transit_gateway_attachments",
        "TransitGatewayAttachmentId",
        _top("State"),
        ("deleting", "failed", "failing", "rejecting"),
    ),
    "vpc_peering_connection": (
        "peerings",
        "VpcPeeringConnectionId",
        _nested("Status", "Code"),
        ("deleting", "failed"),
    ),
    "vpc_endpoint": (
        "vpc_endpoints",
        "VpcEndpointId",
        _top("State"),
        ("deleting", "failed"),
    ),
    "vpn_connection": (
        "vpn_connections",
        "VpnConnectionId",
        _top("State"),
        ("deleting",),
    ),
    "vpn_gateway": ("vpn_gateways", "VpnGatewayId", _top("State"), ("deleting",)),
    "customer_gateway": (
        "customer_gateways",
        "CustomerGatewayId",
        _top("State"),
        ("deleting",),
    ),
    "direct_connect_connection": (
        "dx_connections",
        "connectionId",
        _top("connectionState"),
        ("deleting", "down"),
    ),
    "direct_connect_virtual_interface": (
        "dx_virtual_interfaces",
        "virtualInterfaceId",
        _top("virtualInterfaceState"),
        ("deleting", "down"),
    ),
    "direct_connect_gateway": (
        "dx_gateways",
        "directConnectGatewayId",
        _top("directConnectGatewayState"),
        ("deleting",),
    ),
    "ec2_instance_connect_endpoint": (
        "instance_connect_endpoints",
        "InstanceConnectEndpointId",
        _top("State"),
        ("delete-in-progress", "delete-failed"),
    ),
}

# 응답 모양이 다른 두 종류는 아래에서 따로 꾸민다(EC2 = 예약 안의 인스턴스 · ECS = 후속 상세 호출).
_SPECIAL_KEPT = {
    "ec2_instance": ("shutting-down", "stopping", "stopped"),
    "ecs_service": ("DRAINING",),
}


def _run(client: FakeAws, *, with_state: bool) -> dict[str, Any]:
    scope = Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES)
    snapshot = collect(
        scope, factory(client), clock=lambda: FIXED_CLOCK, with_state=with_state
    )
    return snapshot.to_document()


def _with_state(
    resource_type: str, state: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeAws, str]:
    """기본 가짜 계정에서 그 종류의 첫 항목 하나만 `state` 로 바꾼다 → (클라이언트, native id)."""
    if resource_type == "ec2_instance":
        data = copy.deepcopy(fakes.INSTANCE_RESERVATIONS)
        instance = data[0]["Instances"][0]
        instance["State"] = {"Name": state}
        return FakeAws(instances=data), instance["InstanceId"]
    if resource_type == "ecs_service":
        # 실제 응답은 대문자다(`ACTIVE`·`DRAINING`·`INACTIVE`) — 표는 소문자라 비교가 대소문자를 접어야 한다.
        detail = {**fakes.ECS_SERVICES[ECS_SERVICE_ARN], "status": state.upper()}
        monkeypatch.setitem(fakes.ECS_SERVICES, ECS_SERVICE_ARN, detail)
        return FakeAws(), f"{fakes.ECS_CLUSTER}/{fakes.ECS_SERVICE}"
    dataset, key, put, _ = _CASES[resource_type]
    default = next(d for name, _, _, d in fakes._DATASETS if name == dataset)
    data = copy.deepcopy(default)
    put(data[0], state)
    return FakeAws(**{dataset: data}), str(data[0][key])


def _natives(document: dict[str, Any], resource_type: str) -> list[str]:
    return [n["native_id"] for n in document["nodes"] if n["type"] == resource_type]


_GONE = [(rt, state) for rt in sorted(GONE_STATES) for state in sorted(GONE_STATES[rt])]
_KEPT = [(rt, s) for rt, case in sorted(_CASES.items()) for s in case[3]] + [
    (rt, s) for rt, states in sorted(_SPECIAL_KEPT.items()) for s in states
]


def test_every_gone_set_is_measured_here():
    """표에 종류를 더하고 여기 사례를 안 더하면 그 종류의 버리기는 미측정이다(그 반대도)."""
    assert set(GONE_STATES) == set(_CASES) | set(_SPECIAL_KEPT)
    assert set(GONE_STATES) <= set(SUPPORTED_RESOURCE_TYPES)


@pytest.mark.parametrize("with_state", [False, True], ids=["structure", "state"])
@pytest.mark.parametrize(("resource_type", "state"), _GONE)
def test_a_gone_resource_is_not_a_node(resource_type, state, with_state, monkeypatch):
    baseline = _run(FakeAws(), with_state=with_state)
    client, native = _with_state(resource_type, state, monkeypatch)
    document = _run(client, with_state=with_state)

    assert native in _natives(baseline, resource_type), "전제: 살아 있으면 노드다"
    assert native not in _natives(document, resource_type)
    # 그 하나만 빠진다 — 다른 노드는 하나도 안 사라진다(부모를 잃은 자식도 남는다).
    lost = {n["id"] for n in baseline["nodes"]} - {n["id"] for n in document["nodes"]}
    assert len(lost) == 1, sorted(lost)
    # 🔴 coverage 는 **목록 호출**의 사실이다 — 읽은 항목 수는 그대로다(버린 것은 그림이지 읽기가 아니다).
    assert document["coverage"] == baseline["coverage"]
    # 부모를 잃은 자식에 「못 읽었다」를 지어 붙이지 않는다 — 버린 것은 실패가 아니다(불변식 ④).
    partial = sorted(n["id"] for n in document["nodes"] if n["partial"])
    assert partial == sorted(n["id"] for n in baseline["nodes"] if n["partial"])
    # 버린 노드를 가리키던 선이 끊긴 참조로 문서를 깨지 않는다.
    ids = {n["id"] for n in document["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in document["edges"])
    assert all(
        n["parent_id"] is None or n["parent_id"] in ids for n in document["nodes"]
    )
    Draft202012Validator(SCHEMA).validate(document)


@pytest.mark.parametrize(("resource_type", "state"), _KEPT)
def test_a_transitional_or_failed_resource_stays_with_its_state(
    resource_type, state, monkeypatch
):
    """철거 중·고장 난 자원은 아직 있다 — 카드가 남아야 사람이 그것을 본다."""
    client, native = _with_state(resource_type, state, monkeypatch)
    for with_state in (False, True):
        document = _run(client, with_state=with_state)
        [found] = [
            n
            for n in document["nodes"]
            if n["type"] == resource_type and n["native_id"] == native
        ]
        if resource_type != "ecs_service":  # ECS 는 구조에 상태를 안 싣는다(탐침만)
            assert str(found["attributes"]["state"]).lower() == state.lower()


def test_gone_compares_case_insensitively_and_does_not_guess_a_missing_state():
    assert gone("ecs_service", "INACTIVE")
    assert gone("vpc_endpoint", "Deleted")
    # 상태 값이 없는 부분 응답은 「없어졌다」가 아니다 — 지어내지 않는다(불변식 ④).
    assert not gone("nat_gateway", None)
    assert not gone("nat_gateway", "")
    # 표에 없는 종류는 아무것도 버리지 않는다.
    assert not gone("load_balancer", "deleted")


def test_dropping_is_deterministic(monkeypatch):
    client, _ = _with_state("nat_gateway", "deleted", monkeypatch)
    scope = Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES)

    def once() -> str:
        return to_json(collect(scope, factory(client), clock=lambda: FIXED_CLOCK))

    assert once() == once()


def test_a_route_to_a_dropped_nat_becomes_an_unresolved_reference(monkeypatch):
    """알려진 한계(ADR 0002 §3 개정) — 지운 NAT 를 가리키는 라우트(AWS 가 `blackhole` 로 바꾼다)는
    선이 아니라 라우트 테이블의 「미해결 대상」(`unresolved_refs`)으로 남는다. 조용히 사라지지 않는다(§5).
    개정 전에도 AWS 가 `deleted` NAT 를 목록에서 빼는 순간(약 1시간 뒤) 같은 모양이 됐다 — 시점이 당겨졌다.
    """
    client, native = _with_state("nat_gateway", "deleted", monkeypatch)
    document = _run(client, with_state=False)
    holders = [
        n["type"]
        for n in document["nodes"]
        if native in n["attributes"].get("unresolved_refs", [])
    ]
    assert holders == ["route_table"], "지운 NAT 로 가던 라우트가 흔적 없이 사라졌다"
    assert all(not e["target"].endswith(native) for e in document["edges"])
