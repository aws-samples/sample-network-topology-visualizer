"""Site-to-Site VPN 연결 수집 — 「이음매 2」 · 2차 Site-to-Site VPN(2.6.0).

연결 하나 = 터널 두 개 = 「고객 게이트웨이(CGW) → AWS 측 종단」 하나(`docs/resource-catalog.md` §4.0).
종단은 VGW · TGW · VPN Concentrator(→TGW) · 미연결(Cloud WAN) 중 하나다. 부모는 리전.

엣지(§4.2) — 같은 사실은 **한 번만** 그린다:

- **CGW** ← 이 연결: `attachment`(`CustomerGatewayId`).
- **VGW** ← 이 연결: `attachment`(`VpnGatewayId`).
- **TGW**: 여기서 만들지 않는다. TGW 어태치먼트(`ResourceType=vpn` · `ResourceId=vpn-…`)가 **같은
  사실**이고 그쪽이 이 노드를 가리킨다(`services/transit_gateway_attachment.py` 의
  `_RESOURCE_NODE_TYPES` · DXGW 와 같은 규약 — ADR 0010). 종단 TGW id 는 속성으로만 남긴다.

🔴 **비밀은 읽은 자리에서 버린다**(§4.4 「뺄 것」 · §4.5-9). `CustomerGatewayConfiguration`(PSK 가 든
XML)과 `Options.TunnelOptions[].PreSharedKey` 는 응답에 평문으로 온다 — 아래 `build` 는 **허용한 키만
골라** 싣는다(응답을 통째로 복사하지 않는다). `PreSharedKeyArn` 도 싣지 않는다.

🔴 **터널 상태는 구조에 싣지 않는다**(「이음매 2」 상태 금지). `VgwTelemetry[].Status`(UP/DOWN) ·
`LastStatusChange` · `AcceptedRouteCount` 는 시시각각 바뀐다 — 구조 문서에는 터널의 **바깥 IP**(설정)와
**개수**만 싣고, UP/DOWN 은 상태 단 탐침(`state`)으로만 꺼낸다(ADR 0006 §O7 `vpn_connection` 행).
연결의 `State=available` 은 프로비저닝 상태다 — 「터널이 살아 있다」가 아니다(§4.5-10).
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_vpn_connections",
    "VpnConnections",
    "ec2:DescribeVpnConnections",
)

# `Options` 에서 싣는 키 — 허용 목록이다(위 🔴). IKE/IPsec 파라미터·로그 옵션은 토폴로지 노이즈다(§4.4).
_OPTION_KEYS = (
    ("StaticRoutesOnly", "static_routes_only"),
    ("EnableAcceleration", "enable_acceleration"),
    ("OutsideIpAddressType", "outside_ip_address_type"),
    ("TransportTransitGatewayAttachmentId", "transport_transit_gateway_attachment_id"),
    ("TunnelInsideIpVersion", "tunnel_inside_ip_version"),
    ("TunnelBandwidth", "tunnel_bandwidth"),
    ("LocalIpv4NetworkCidr", "local_ipv4_network_cidr"),
    ("RemoteIpv4NetworkCidr", "remote_ipv4_network_cidr"),
    ("LocalIpv6NetworkCidr", "local_ipv6_network_cidr"),
    ("RemoteIpv6NetworkCidr", "remote_ipv6_network_cidr"),
)


def _tunnel_ips(item: dict[str, Any]) -> list[str]:
    """터널의 AWS 측 바깥 IP. 설정(`TunnelOptions`)과 상태(`VgwTelemetry`)가 같은 키를 쓴다(§4.2 조인 키).

    둘 중 하나만 오는 경우가 있어 합집합을 쓴다 — 값은 설정이고 상태가 아니다.
    """
    options = item.get("Options") or {}
    return _sorted(
        [t.get("OutsideIpAddress") for t in options.get("TunnelOptions") or []]
        + [t.get("OutsideIpAddress") for t in item.get("VgwTelemetry") or []]
    )


def _tunnel_inside(item: dict[str, Any]) -> list[str]:
    """터널의 설정 줄(콘솔 「터널 세부 정보」 표의 설정 칸 · 사용자 결정 2026-10-24) — 바깥 IP 순.

    한 줄 = 「바깥 IP \t 내부 IPv4 CIDR \t 내부 IPv6 CIDR」(탭 구분 스칼라 · 없으면 빈 칸). 설정이라 구조에 싣는다 —
    UP/DOWN · 마지막 변경 · 상태 메시지는 상태 단(`state_attributes`)이다(위 🔴).
    """
    rows = []
    for t in (item.get("Options") or {}).get("TunnelOptions") or []:
        rows.append(
            "\t".join(
                (
                    str(t.get("OutsideIpAddress") or ""),
                    str(t.get("TunnelInsideCidr") or ""),
                    str(t.get("TunnelInsideIpv6Cidr") or ""),
                )
            )
        )
    return sorted(rows)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpn_connection", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["VpnConnectionId"]
    nid = node_id("vpn_connection", owner, ctx.region, native)
    state = str(item.get("State") or "")
    live = alive(state)
    edges: list[EdgeDraft] = []
    for key, target_type in (
        ("CustomerGatewayId", "customer_gateway"),
        ("VpnGatewayId", "vpn_gateway"),
    ):
        if item.get(key):
            edges.append(
                EdgeDraft(
                    "attachment",
                    nid,
                    str(item[key]),
                    target_type,
                    False,
                    {"state": state},
                    live=live,
                )
            )
    options = item.get("Options") or {}
    tunnel_ips = _tunnel_ips(item)
    attributes: dict[str, Any] = {
        "state": state,
        "type": item.get("Type"),
        "category": item.get("Category"),
        "customer_gateway_id": item.get("CustomerGatewayId"),
        "vpn_gateway_id": item.get("VpnGatewayId"),
        "transit_gateway_id": item.get("TransitGatewayId"),
        "vpn_concentrator_id": item.get("VpnConcentratorId"),
        "core_network_arn": item.get("CoreNetworkArn"),
        "gateway_association_state": item.get("GatewayAssociationState"),
        **{name: options.get(key) for key, name in _OPTION_KEYS},
        "tunnel_outside_ips": tunnel_ips,
        "tunnel_count": len(tunnel_ips),
        "tunnel_inside_cidrs": _tunnel_inside(item),
        # 정적 라우트(StaticRoutesOnly=true 일 때) — 목적지만. 지워진 라우트는 라우트가 아니다.
        "static_route_destinations": _sorted(
            r.get("DestinationCidrBlock")
            for r in item.get("Routes") or []
            if alive(r.get("State"))
        ),
        **extra,
    }
    node = Node(
        id=nid,
        type="vpn_connection",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — 연결 상태와 **터널 두 개의 UP/DOWN 을 한 값으로 접는다**(ADR 0006 §O7 `vpn_connection` 행).

    `available` 하나로는 「연결은 있는데 터널이 다 죽었다」를 못 가른다(§4.5-10) — VPN 에서 가장 흔한
    장애다. 한 터널만 UP 이면 트래픽은 흐르지만 이중화가 없다(AWS Health 가 알리는 위험 상태 · §4.1).
    터널 정보가 아예 없으면(`VgwTelemetry` 빈 목록) 연결 상태 그대로 둔다 — 지어내지 않는다.
    """
    status = str(item.get("State") or "").lower()
    if not status:
        return None
    if status != "available":
        return status
    tunnels = [
        str(t.get("Status") or "").upper() for t in item.get("VgwTelemetry") or []
    ]
    if not tunnels:
        return "available"
    up = sum(1 for t in tunnels if t == "UP")
    if up == 0:
        return "available-all-tunnels-down"
    if up < len(tunnels):
        return "available-one-tunnel-down"
    return "available"


def state_attributes(item: dict[str, Any]) -> dict[str, Any]:
    """터널별 상태 줄(콘솔 「터널 세부 정보」 표의 상태 칸 · 관측 `tunnels` · 사용자 결정 2026-10-24).

    한 줄 = 「바깥 IP \t 상태(UP/DOWN · AWS 원문) \t 마지막 상태 변경(ISO) \t 상태 메시지 \t 수락한 라우트 수」 — 바깥 IP 순.
    🔴 **상태 단에서만** 불린다(`collect(with_state=True)`) — 위 `state` 와 같은 이유(시시각각 바뀐다 · 불변식 ② ·
    contract-seams 「이음매 2」 상태 금지). 위 `state` 가 접은 값은 「전부 / 하나 / 없음」만 말하고 **어느 터널**인지는
    버린다 — 그 줄을 여기서 따로 싣는다. `VgwTelemetry` 가 없으면 `{}`(= 「모른다」 · 빈 목록으로 적으면 「터널 없음」).
    """
    telemetry = item.get("VgwTelemetry")
    if not isinstance(telemetry, list) or not telemetry:
        return {}
    rows = []
    for t in telemetry:
        last = t.get("LastStatusChange")
        last_text = last.isoformat() if hasattr(last, "isoformat") else str(last or "")
        rows.append(
            "\t".join(
                (
                    str(t.get("OutsideIpAddress") or ""),
                    str(t.get("Status") or "").upper(),
                    last_text,
                    " ".join(str(t.get("StatusMessage") or "").split()),
                    str(
                        t.get("AcceptedRouteCount")
                        if t.get("AcceptedRouteCount") is not None
                        else ""
                    ),
                )
            )
        )
    return {"tunnel_status": sorted(rows)}


SERVICE = Service(
    spec=SPEC, build=build, state=state, state_attributes=state_attributes
)
