"""Collect Site-to-Site VPN configuration and gateway links.

Build TGW links from TGW attachments. Exclude pre-shared keys and
customer
gateway XML from output. Use tunnel state for observations.
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
    options = item.get("Options") or {}
    return _sorted(
        [t.get("OutsideIpAddress") for t in options.get("TunnelOptions") or []]
        + [t.get("OutsideIpAddress") for t in item.get("VgwTelemetry") or []]
    )


def _tunnel_inside(item: dict[str, Any]) -> list[str]:
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


# Copy only selected configuration fields; responses may contain
# plaintext PSKs.
def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpn_connection", item.get("State")):
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
