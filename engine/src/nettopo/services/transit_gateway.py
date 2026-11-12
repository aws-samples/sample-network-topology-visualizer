"""Collect regional Transit Gateway configuration and owner accounts.

Place nodes in the owner account if in scope, or the querying account
otherwise. Record out-of-scope owners that differ from the querying
account in attributes.owner_account_id.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of

SPEC = Spec(
    "ec2",
    "describe_transit_gateways",
    "TransitGateways",
    "ec2:DescribeTransitGateways",
)


_OPTION_KEYS = (
    ("AssociationDefaultRouteTableId", "association_default_route_table_id"),
    ("DefaultRouteTablePropagation", "default_route_table_propagation"),
    ("PropagationDefaultRouteTableId", "propagation_default_route_table_id"),
    ("AutoAcceptSharedAttachments", "auto_accept_shared_attachments"),
    ("VpnEcmpSupport", "vpn_ecmp_support"),
    ("DnsSupport", "dns_support"),
    ("SecurityGroupReferencingSupport", "security_group_referencing_support"),
    ("MulticastSupport", "multicast_support"),
)


def _options(item: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        attr: str(options[key])
        for key, attr in _OPTION_KEYS
        if options.get(key) not in (None, "")
    }
    if options.get("TransitGatewayCidrBlocks") is not None:
        out["transit_gateway_cidr_blocks"] = sorted(
            {str(c) for c in options["TransitGatewayCidrBlocks"] if c}
        )
    enc = (options.get("EncryptionSupport") or {}).get("EncryptionState")
    if enc:
        out["encryption_support"] = str(enc)
    if item.get("Description"):
        out["description"] = str(item["Description"])
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("transit_gateway", item.get("State")):
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["TransitGatewayId"]
    options = item.get("Options") or {}
    node = Node(
        id=node_id("transit_gateway", owner, ctx.region, native),
        type="transit_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("TransitGatewayArn"),
        name=tags.get("Name") or (item.get("Description") or None),
        attributes={
            "state": item.get("State"),
            "amazon_side_asn": options.get("AmazonSideAsn"),
            "default_route_table_association": str(
                options.get("DefaultRouteTableAssociation") or ""
            ),
            **_options(item, options),
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
