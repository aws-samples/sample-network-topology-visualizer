"""Collect Site-to-Site VPN customer gateway configuration.

IP addresses and BGP ASNs are configuration values. Do not query the
connection state of on-premises devices.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of

SPEC = Spec(
    "ec2",
    "describe_customer_gateways",
    "CustomerGateways",
    "ec2:DescribeCustomerGateways",
)


def _asn(item: dict[str, Any]) -> int | None:
    for key in ("BgpAsnExtended", "BgpAsn"):
        raw = item.get(key)
        if raw not in (None, ""):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


# Four-byte ASNs beyond BgpAsn's range appear in BgpAsnExtended.
def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("customer_gateway", item.get("State")):
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["CustomerGatewayId"]
    node = Node(
        id=node_id("customer_gateway", owner, ctx.region, native),
        type="customer_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name") or (item.get("DeviceName") or None),
        attributes={
            "state": item.get("State"),
            "type": item.get("Type"),
            "ip_address": item.get("IpAddress"),
            "bgp_asn": _asn(item),
            "device_name": item.get("DeviceName"),
            "uses_certificate": bool(item.get("CertificateArn")),
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
