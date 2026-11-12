"""Collect internet gateway configuration and VPC attachment links.

Use the region as parent. Keep gateway nodes without VPC attachments.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_internet_gateways",
    "InternetGateways",
    "ec2:DescribeInternetGateways",
)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["InternetGatewayId"]
    nid = node_id("internet_gateway", owner, ctx.region, native)
    attachments = item.get("Attachments") or []
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
    node = Node(
        id=nid,
        type="internet_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": "attached"
            if any(alive(a.get("State")) for a in attachments)
            else "detached",
            "attached_vpc_ids": _sorted(a.get("VpcId") for a in attachments),
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
