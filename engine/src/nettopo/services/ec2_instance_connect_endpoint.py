"""Collect EC2 Instance Connect endpoints in their configured subnets.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_instance_connect_endpoints",
    "InstanceConnectEndpoints",
    "ec2:DescribeInstanceConnectEndpoints",
)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("ec2_instance_connect_endpoint", item.get("State")):
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["InstanceConnectEndpointId"]
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("SubnetId"):
        parents.append(node_id("subnet", owner, ctx.region, str(item["SubnetId"])))
        parent_types.append("subnet")
    if item.get("VpcId"):
        parents.append(node_id("vpc", owner, ctx.region, str(item["VpcId"])))
        parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("ec2_instance_connect_endpoint", owner, ctx.region, native),
        type="ec2_instance_connect_endpoint",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("InstanceConnectEndpointArn"),
        name=tags.get("Name"),
        attributes={
            "state": item.get("State"),
            "availability_zone": item.get("AvailabilityZone"),
            "preserve_client_ip": item.get("PreserveClientIp"),
            "security_group_ids": _sorted(item.get("SecurityGroupIds") or []),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


SERVICE = Service(spec=SPEC, build=build)
