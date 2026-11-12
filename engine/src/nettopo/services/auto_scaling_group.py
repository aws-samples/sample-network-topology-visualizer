"""Collect Auto Scaling group configuration and subnet lists.

Use the region as the group parent and record placements in subnet_ids.
Store current instance IDs in NodeDraft.asg_members to resolve
membership.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft
from .base import sorted_strs as _sorted

SPEC = Spec(
    "autoscaling",
    "describe_auto_scaling_groups",
    "AutoScalingGroups",
    "autoscaling:DescribeAutoScalingGroups",
)


def _id_segment(native: str) -> str:
    """Percent-encode the group name for use in Node.id.

    Encode slashes, spaces, pipes, and percent signs. Store the
    original,
    unencoded group name in Node.native_id.
    """
    return quote(native, safe="")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["AutoScalingGroupName"]
    subnet_ids = _sorted((item.get("VPCZoneIdentifier") or "").split(","))
    node = Node(
        id=node_id("auto_scaling_group", owner, ctx.region, _id_segment(native)),
        type="auto_scaling_group",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("AutoScalingGroupARN"),
        name=native,
        attributes={
            "min_size": item.get("MinSize"),
            "max_size": item.get("MaxSize"),
            "desired_capacity": item.get("DesiredCapacity"),
            "availability_zones": _sorted(item.get("AvailabilityZones") or []),
            "subnet_ids": subnet_ids,
            "target_group_arns": _sorted(item.get("TargetGroupARNs") or []),
        },
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        asg_members=frozenset(
            str(i["InstanceId"])
            for i in item.get("Instances") or []
            if i.get("InstanceId")
        ),
    )


SERVICE = Service(spec=SPEC, build=build)
