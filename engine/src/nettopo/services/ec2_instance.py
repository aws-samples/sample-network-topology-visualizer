"""Collect EC2 instances from DescribeInstances Reservations.

coverage.item_count counts Reservations, not instances. Choose a
collected
parent in subnet, VPC, then region order.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec("ec2", "describe_instances", "Reservations", "ec2:DescribeInstances")


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Expand instances in Reservations into individual Fetched items.

    client: Client required by the common enrichment signature (unused
        here)
    items: Reservations and query failure reasons

    Copy each reservation's OwnerId to its instances.
    """
    out: list[Fetched] = []
    for reservation in items:
        owner = reservation.payload.get("OwnerId")
        for instance in reservation.payload.get("Instances") or []:
            payload = dict(instance)
            if owner is not None and "OwnerId" not in payload:
                payload["OwnerId"] = owner
            out.append(Fetched(payload=payload, reasons=reservation.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("ec2_instance", (item.get("State") or {}).get("Name")):
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["InstanceId"]
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
        id=node_id("ec2_instance", owner, ctx.region, native),
        type="ec2_instance",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "instance_type": item.get("InstanceType"),
            "state": (item.get("State") or {}).get("Name"),
            "availability_zone": (item.get("Placement") or {}).get("AvailabilityZone"),
            "private_ip_address": item.get("PrivateIpAddress"),
            "security_group_ids": _sorted(
                g.get("GroupId") for g in item.get("SecurityGroups") or []
            ),
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


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
