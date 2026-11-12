"""Collect Direct Connect VIF configuration and gateway links.

Use the region as parent if a hosted VIF's connection is outside scope.
Exclude authentication keys and router configuration. Store BGP state in
observations, separate from topology.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, gone
from .base import sorted_strs as _sorted
from .direct_connect_connection import dx_tags

SPEC = Spec(
    "directconnect",
    "describe_virtual_interfaces",
    "virtualInterfaces",
    "directconnect:DescribeVirtualInterfaces",
)


def _asn(item: dict[str, Any]) -> int | None:
    for key in ("asnLong", "asn"):
        value = item.get(key)
        if value:
            return int(value)
    return None


def _peer_labels(peers: list[dict[str, Any]]) -> list[str]:
    labels: set[str] = set()
    for peer in peers:
        family = str(peer.get("addressFamily") or "").lower()
        if not family:
            continue
        asn = _asn(peer)
        labels.add(f"{family}:{asn}" if asn is not None else family)
    return sorted(labels)


def _peer_details(peers: list[dict[str, Any]]) -> list[str]:
    rows = []
    for peer in peers:
        asn = _asn(peer)
        rows.append(
            "\t".join(
                (
                    str(peer.get("addressFamily") or "").lower(),
                    "" if asn is None else str(asn),
                    str(peer.get("amazonAddress") or ""),
                    str(peer.get("customerAddress") or ""),
                    str(peer.get("bgpPeerId") or ""),
                )
            )
        )
    return sorted(rows)


    # Exclude authKey and customerRouterConfig from JSON attributes.
def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("direct_connect_virtual_interface", item.get("virtualInterfaceState")):
        return None
    tags = dx_tags(item)
    owner, extra = ctx.attribute(item, "ownerAccount")
    native = item["virtualInterfaceId"]
    nid = node_id("direct_connect_virtual_interface", owner, ctx.region, native)
    state = str(item.get("virtualInterfaceState") or "")
    connection_id = item.get("connectionId")
    dxgw_id = item.get("directConnectGatewayId")
    edges: list[EdgeDraft] = []
    if dxgw_id:
        edges.append(
            EdgeDraft(
                "attachment",
                nid,
                str(dxgw_id),
                "direct_connect_gateway",
                False,
                {
                    "state": state,
                    "virtual_interface_type": item.get("virtualInterfaceType"),
                },
                live=alive(state),
            )
        )
    node = Node(
        id=nid,
        type="direct_connect_virtual_interface",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name") or (item.get("virtualInterfaceName") or None),
        attributes={
            "state": state,
            "virtual_interface_type": item.get("virtualInterfaceType"),
            "connection_id": connection_id,
            "vlan": item.get("vlan"),
            "asn": _asn(item),
            "amazon_side_asn": item.get("amazonSideAsn"),
            "address_family": item.get("addressFamily"),
            "mtu": item.get("mtu"),
            "jumbo_frame_capable": item.get("jumboFrameCapable"),
            "direct_connect_gateway_id": dxgw_id or None,
            "virtual_gateway_id": item.get("virtualGatewayId") or None,
            "site_link_enabled": item.get("siteLinkEnabled"),
            "route_filter_prefixes": _sorted(
                p.get("cidr") for p in item.get("routeFilterPrefixes") or []
            ),
            "bgp_peers": _peer_labels(item.get("bgpPeers") or []),
            "bgp_peer_details": _peer_details(item.get("bgpPeers") or []),
            "amazon_address": item.get("amazonAddress") or None,
            "customer_address": item.get("customerAddress") or None,
            **extra,
        },
        tags=tags,
    )
    parents: list[str] = []
    parent_types: list[str] = []
    if connection_id:
        parents.append(
            node_id("direct_connect_connection", owner, ctx.region, str(connection_id))
        )
        parent_types.append("direct_connect_connection")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
        edges=edges,
    )


def state(item: dict[str, Any]) -> str | None:
    status = str(item.get("virtualInterfaceState") or "").lower()
    if not status:
        return None
    if status != "available":
        return status
    peers = [str(p.get("bgpStatus") or "").lower() for p in item.get("bgpPeers") or []]
    if not peers:
        return "available"
    up = sum(1 for p in peers if p == "up")
    if up == 0:
        return "available-all-bgp-down"
    if up < len(peers):
        return "available-some-bgp-down"
    return "available"


def state_attributes(item: dict[str, Any]) -> dict[str, Any]:
    peers = item.get("bgpPeers")
    if not isinstance(peers, list):
        return {}
    statuses = [str(p.get("bgpStatus") or "").lower() for p in peers]
    return {
        "bgp_peer_count": len(statuses),
        "bgp_up_count": statuses.count("up"),
    }


SERVICE = Service(
    spec=SPEC, build=build, state=state, state_attributes=state_attributes
)
