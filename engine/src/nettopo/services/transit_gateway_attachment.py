"""Collect TGW attachments, including peering and Connect transport
links.

Use ResourceOwnerId for resource attachments and the local TGW owner for
peering. During link resolution, merge both peering views into one node.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    alive,
    classify,
    draft,
    gone,
    pages,
    tags_of,
)

SPEC = Spec(
    "ec2",
    "describe_transit_gateway_attachments",
    "TransitGatewayAttachments",
    "ec2:DescribeTransitGatewayAttachments",
)

CONNECTS_API = "ec2:DescribeTransitGatewayConnects"
PEERS_API = "ec2:DescribeTransitGatewayConnectPeers"
VPC_ATTACHMENTS_API = "ec2:DescribeTransitGatewayVpcAttachments"
_CONNECT = "_Connect"
_PEERS = "_ConnectPeers"
_VPC = "_VpcAttachment"
_VPC_OPTIONS = (
    ("DnsSupport", "dns_support"),
    ("Ipv6Support", "ipv6_support"),
    ("ApplianceModeSupport", "appliance_mode_support"),
    ("SecurityGroupReferencingSupport", "security_group_referencing_support"),
)
_PEER_GONE = frozenset({"deleted"})

_RESOURCE_NODE_TYPES = {
    "vpc": "vpc",
    "direct-connect-gateway": "direct_connect_gateway",
    "vpn": "vpn_connection",
}


def _is_connect(item: dict[str, Any]) -> bool:
    return str(item.get("ResourceType") or "") == "connect"


def _is_vpc(item: dict[str, Any]) -> bool:
    return str(item.get("ResourceType") or "") == "vpc"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Describe TGW VPC attachments and Connect peers.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    return _enrich_connect(client, _enrich_vpc(client, items))


def _enrich_vpc(client: Any, items: list[Fetched]) -> list[Fetched]:
    if not any(_is_vpc(it.payload) for it in items):
        return items
    try:
        got = pages(
            client,
            "describe_transit_gateway_vpc_attachments",
            "TransitGatewayVpcAttachments",
        )
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, VPC_ATTACHMENTS_API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,)) if _is_vpc(it.payload) else it
            for it in items
        ]
    by_id = {
        str(v["TransitGatewayAttachmentId"]): v
        for v in got
        if v.get("TransitGatewayAttachmentId")
    }
    out: list[Fetched] = []
    for it in items:
        aid = str(it.payload.get("TransitGatewayAttachmentId") or "")
        if not _is_vpc(it.payload) or aid not in by_id:
            out.append(it)
            continue
        out.append(Fetched({**it.payload, _VPC: by_id[aid]}, it.reasons))
    return out


def _vpc_parts(item: dict[str, Any]) -> dict[str, Any]:
    vpc = item.get(_VPC)
    if vpc is None:
        return {}
    options = vpc.get("Options") or {}
    return {
        "subnet_ids": sorted({str(s) for s in vpc.get("SubnetIds") or [] if s}),
        **{
            attr: str(options[key]).lower()
            for key, attr in _VPC_OPTIONS
            if options.get(key) is not None
        },
    }


def _association_parts(item: dict[str, Any]) -> dict[str, Any]:
    assoc = item.get("Association") or {}
    if not assoc.get("TransitGatewayRouteTableId"):
        return {}
    out = {"association_route_table_id": str(assoc["TransitGatewayRouteTableId"])}
    if assoc.get("State"):
        out["association_state"] = str(assoc["State"]).lower()
    return out


def _enrich_connect(client: Any, items: list[Fetched]) -> list[Fetched]:
    if not any(_is_connect(it.payload) for it in items):
        return items
    connects: dict[str, dict[str, Any]] | None = None
    peers: dict[str, list[dict[str, Any]]] | None = None
    failed: list[Any] = []
    try:
        got = pages(
            client, "describe_transit_gateway_connects", "TransitGatewayConnects"
        )
        connects = {
            str(c["TransitGatewayAttachmentId"]): c
            for c in got
            if c.get("TransitGatewayAttachmentId")
        }
    except (ClientError, BotoCoreError) as exc:
        failed.append(classify(exc, CONNECTS_API)[1])
    try:
        got = pages(
            client,
            "describe_transit_gateway_connect_peers",
            "TransitGatewayConnectPeers",
        )
        peers = {}
        for p in got:
            if str(p.get("State") or "").lower() in _PEER_GONE:
                continue
            peers.setdefault(str(p.get("TransitGatewayAttachmentId") or ""), []).append(
                p
            )
    except (ClientError, BotoCoreError) as exc:
        failed.append(classify(exc, PEERS_API)[1])
    out: list[Fetched] = []
    for it in items:
        if not _is_connect(it.payload):
            out.append(it)
            continue
        aid = str(it.payload.get("TransitGatewayAttachmentId") or "")
        payload = dict(it.payload)
        if connects is not None:
            payload[_CONNECT] = connects.get(aid) or {}
        if peers is not None:
            payload[_PEERS] = peers.get(aid, [])
        out.append(Fetched(payload, it.reasons + tuple(failed)))
    return out


def _peer_line(p: dict[str, Any]) -> str:
    cfg = p.get("ConnectPeerConfiguration") or {}
    bgp = (cfg.get("BgpConfigurations") or [{}])[0] or {}
    name = tags_of(p).get("Name") or ""
    parts = [
        str(p.get("TransitGatewayConnectPeerId") or "?"),
        str(cfg.get("Protocol") or "gre"),
        str(cfg.get("TransitGatewayAddress") or "?"),
        str(cfg.get("PeerAddress") or "?"),
        ",".join(sorted(str(c) for c in cfg.get("InsideCidrBlocks") or [])) or "?",
        str(bgp.get("TransitGatewayAsn") or "?"),
        str(bgp.get("PeerAsn") or "?"),
    ]
    return " ".join(parts + ([name] if name else []))


def _connect_parts(
    item: dict[str, Any], nid: str, state: str, live: bool
) -> tuple[dict[str, Any], list[EdgeDraft]]:
    conn = item.get(_CONNECT)
    transport = str(
        (conn or {}).get("TransportTransitGatewayAttachmentId")
        or item.get("ResourceId")
        or ""
    )
    attrs: dict[str, Any] = {}
    if transport:
        attrs["transport_attachment_id"] = transport
    if conn:
        proto = (conn.get("Options") or {}).get("Protocol")
        if proto:
            attrs["connect_protocol"] = str(proto)
    if item.get(_PEERS) is not None:
        attrs["connect_peers"] = sorted(_peer_line(p) for p in item[_PEERS])
    edges = (
        [
            EdgeDraft(
                "connect_transport",
                nid,
                transport,
                "transit_gateway_attachment",
                True,
                {"state": state},
                live=live,
            )
        ]
        if transport
        else []
    )
    return attrs, edges


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("transit_gateway_attachment", item.get("State")):
        return None
    tags = tags_of(item)
    kind = str(item.get("ResourceType") or "")
    peering = kind in ("peering", "tgw-peering")
    owner, extra = ctx.attribute(
        item, "TransitGatewayOwnerId" if peering else "ResourceOwnerId"
    )
    native = item["TransitGatewayAttachmentId"]
    nid = node_id("transit_gateway_attachment", owner, ctx.region, native)
    state = str(item.get("State") or "")
    live = alive(state)
    edges: list[EdgeDraft] = []
    if item.get("TransitGatewayId"):
        edges.append(
            EdgeDraft(
                "attachment",
                nid,
                str(item["TransitGatewayId"]),
                "transit_gateway",
                False,
                {"state": state},
                live=live,
            )
        )
    target_type = _RESOURCE_NODE_TYPES.get(str(item.get("ResourceType") or ""))
    connect_attrs: dict[str, Any] = {}
    if _is_connect(item):
        connect_attrs, connect_edges = _connect_parts(item, nid, state, live)
        edges.extend(connect_edges)
    if target_type and item.get("ResourceId"):
        edges.append(
            EdgeDraft(
                "attachment",
                nid,
                str(item["ResourceId"]),
                target_type,
                False,
                {"state": state},
                live=live,
            )
        )
    node = Node(
        id=nid,
        type="transit_gateway_attachment",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": state,
            "resource_type": item.get("ResourceType"),
            "resource_id": item.get("ResourceId"),
            "transit_gateway_id": item.get("TransitGatewayId"),
            "transit_gateway_owner_id": item.get("TransitGatewayOwnerId"),
            "resource_owner_id": item.get("ResourceOwnerId"),
            **_association_parts(item),
            **_vpc_parts(item),
            **(
                {"peer_account_id": str(item["ResourceOwnerId"])}
                if peering and item.get("ResourceOwnerId")
                else {}
            ),
            **connect_attrs,
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
        shared_native=peering,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
