"""Collect subnets and use the region parent when the VPC is
unavailable.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, tags_of

SPEC = Spec("ec2", "describe_subnets", "Subnets", "ec2:DescribeSubnets")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    node = Node(
        id=node_id("subnet", owner, ctx.region, item["SubnetId"]),
        type="subnet",
        account_id=owner,
        region=ctx.region,
        native_id=item["SubnetId"],
        arn=item.get("SubnetArn"),
        name=tags.get("Name"),
        attributes={
            "cidr_block": item.get("CidrBlock"),
            "availability_zone": item.get("AvailabilityZone"),
            "availability_zone_id": item.get("AvailabilityZoneId"),
            "state": item.get("State"),
            "map_public_ip_on_launch": bool(item.get("MapPublicIpOnLaunch", False)),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(
            node_id("vpc", owner, ctx.region, str(item.get("VpcId"))),
            ctx.region_node(owner),
        ),
        parent_types=("vpc", "region"),
    )


def _state_attributes(item: dict[str, Any]) -> dict[str, Any]:
    count = item.get("AvailableIpAddressCount")
    return {"available_ip_address_count": count} if isinstance(count, int) else {}


SERVICE = Service(spec=SPEC, build=build, state_attributes=_state_attributes)
