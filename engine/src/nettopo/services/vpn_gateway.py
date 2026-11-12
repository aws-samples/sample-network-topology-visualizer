"""Collect VPN gateway configuration and VPC attachments.

Use the region as parent. Keep gateways without VPC attachments.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_vpn_gateways",
    "VpnGateways",
    "ec2:DescribeVpnGateways",
)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpn_gateway", item.get("State")):
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["VpnGatewayId"]
    nid = node_id("vpn_gateway", owner, ctx.region, native)
    attachments = item.get("VpcAttachments") or []
    edges = [
        EdgeDraft(
            "attachment",
            nid,
            str(a["VpcId"]),
            "vpc",
            False,
            {"state": str(a.get("State") or "")},
            live=alive(a.get("State")),
        )
        for a in attachments
        if a.get("VpcId")
    ]
    asn = item.get("AmazonSideAsn")
    node = Node(
        id=nid,
        type="vpn_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": item.get("State"),
            "type": item.get("Type"),
            "amazon_side_asn": asn if asn is None else int(asn),
            "attached_vpc_ids": _sorted(
                a.get("VpcId") for a in attachments if alive(a.get("State"))
            ),
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
    )


SERVICE = Service(spec=SPEC, build=build)
