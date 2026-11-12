"""Collect regional Direct Connect connection configuration.

Use the region as the parent and resolve virtual interface links
separately. Exclude MACsec keys and transient port encryption state.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of

SPEC = Spec(
    "directconnect",
    "describe_connections",
    "connections",
    "directconnect:DescribeConnections",
)


def dx_tags(item: dict[str, Any]) -> dict[str, str]:
    return tags_of(item, "tags", "key", "value")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("direct_connect_connection", item.get("connectionState")):
        return None
    tags = dx_tags(item)
    owner, extra = ctx.attribute(item, "ownerAccount")
    native = item["connectionId"]
    node = Node(
        id=node_id("direct_connect_connection", owner, ctx.region, native),
        type="direct_connect_connection",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name") or (item.get("connectionName") or None),
        attributes={
            "state": item.get("connectionState"),
            "location": item.get("location"),
            "bandwidth": item.get("bandwidth"),
            "vlan": item.get("vlan"),
            "lag_id": item.get("lagId"),
            "partner_name": item.get("partnerName"),
            "provider_name": item.get("providerName"),
            "aws_device": item.get("awsDeviceV2"),
            "aws_logical_device_id": item.get("awsLogicalDeviceId"),
            "has_logical_redundancy": item.get("hasLogicalRedundancy"),
            "jumbo_frame_capable": item.get("jumboFrameCapable"),
            "macsec_capable": item.get("macSecCapable"),
            "encryption_mode": item.get("encryptionMode"),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


SERVICE = Service(spec=SPEC, build=build)
