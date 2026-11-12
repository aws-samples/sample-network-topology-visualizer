"""Collect VPC peering and links to requester and accepter VPCs.

Choose the node's account and region within scope. collect.py merges
both sides' views of the same peering into one node.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_vpc_peering_connections",
    "VpcPeeringConnections",
    "ec2:DescribeVpcPeeringConnections",
)


def _peer_cidrs(info: dict[str, Any]) -> list[str]:
    blocks = [b.get("CidrBlock") for b in info.get("CidrBlockSet") or []]
    return _sorted(blocks or [info.get("CidrBlock")])


def _more(
    item: dict[str, Any], requester: dict[str, Any], accepter: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for side, info in (("requester", requester), ("accepter", accepter)):
        v6 = _sorted(b.get("Ipv6CidrBlock") for b in info.get("Ipv6CidrBlockSet") or [])
        if v6:
            out[f"{side}_ipv6_cidr_blocks"] = v6
        dns = (info.get("PeeringOptions") or {}).get("AllowDnsResolutionFromRemoteVpc")
        if dns is not None:
            out[f"{side}_allow_dns_resolution_from_remote_vpc"] = bool(dns)
    expires = item.get("ExpirationTime")
    if expires:
        out["expiration_time"] = (
            expires.isoformat() if hasattr(expires, "isoformat") else str(expires)
        )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpc_peering_connection", (item.get("Status") or {}).get("Code")):
        return None
    tags = tags_of(item)
    requester = item.get("RequesterVpcInfo") or {}
    accepter = item.get("AccepterVpcInfo") or {}
    owner, extra = ctx.attribute(requester)
    region = ctx.canonical_region(requester, owner)
    native = item["VpcPeeringConnectionId"]
    nid = node_id("vpc_peering_connection", owner, region, native)
    state = str((item.get("Status") or {}).get("Code") or "")
    live = alive(state)
    edges = [
        EdgeDraft(
            "peering",
            nid,
            str(info["VpcId"]),
            "vpc",
            False,
            {"state": state, "side": side},
            disc=side,
            live=live,
        )
        for side, info in (("requester", requester), ("accepter", accepter))
        if info.get("VpcId")
    ]
    node = Node(
        id=nid,
        type="vpc_peering_connection",
        account_id=owner,
        region=region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": state,
            "requester_vpc_id": requester.get("VpcId"),
            "requester_account_id": requester.get("OwnerId"),
            "requester_region": requester.get("Region"),
            "requester_cidr_blocks": _peer_cidrs(requester),
            "accepter_vpc_id": accepter.get("VpcId"),
            "accepter_account_id": accepter.get("OwnerId"),
            "accepter_region": accepter.get("Region"),
            "accepter_cidr_blocks": _peer_cidrs(accepter),
            **_more(item, requester, accepter),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(
            node_id("region", owner, region),
            ctx.region_node(owner),
            ctx.region_node(),
        ),
        parent_types=("region", "region", "region"),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build)
