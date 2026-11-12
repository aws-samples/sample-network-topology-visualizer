"""Collect NAT gateways and find parents in subnet, VPC, then region
order.

Add parent candidates only when the response contains their IDs.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec("ec2", "describe_nat_gateways", "NatGateways", "ec2:DescribeNatGateways")


def _address_rows(addresses: list[dict[str, Any]]) -> list[str]:
    rows = []
    for a in addresses:
        primary = bool(a.get("IsPrimary"))
        line = "\t".join(
            (
                str(a.get("PublicIp") or ""),
                str(a.get("PrivateIp") or ""),
                str(a.get("NetworkInterfaceId") or ""),
                str(a.get("AllocationId") or ""),
                "true" if primary else "false",
            )
        )
        rows.append((0 if primary else 1, str(a.get("PrivateIp") or ""), line))
    return [r[2] for r in sorted(rows)]


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("nat_gateway", item.get("State")):
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["NatGatewayId"]
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
        id=node_id("nat_gateway", owner, ctx.region, native),
        type="nat_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": item.get("State"),
            "connectivity_type": item.get("ConnectivityType"),
            "public_ips": _sorted(
                a.get("PublicIp") for a in item.get("NatGatewayAddresses") or []
            ),
            "addresses": _address_rows(item.get("NatGatewayAddresses") or []),
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
