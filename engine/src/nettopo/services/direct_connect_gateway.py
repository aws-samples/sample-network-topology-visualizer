"""Collect global Direct Connect gateway configuration and associations.

Use global in Node.id's region component to merge regional views.
Record coverage by query region and build TGW links from TGW
attachments.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import GLOBAL, Node, Reason, node_id
from .base import (
    Ctx,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    alive,
    classify,
    draft,
    gone,
    pages,
)
from .base import sorted_strs as _sorted
from .direct_connect_connection import dx_tags

SPEC = Spec(
    "directconnect",
    "describe_direct_connect_gateways",
    "directConnectGateways",
    "directconnect:DescribeDirectConnectGateways",
)

_ASSOCIATIONS = (
    "directConnectGatewayAssociations",
    "describe_direct_connect_gateway_associations",
    "directconnect:DescribeDirectConnectGatewayAssociations",
)


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query additional Direct Connect gateway association details.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    key, method, api = _ASSOCIATIONS
    out: list[Fetched] = []
    for item in items:
        dxgw_id = item.payload.get("directConnectGatewayId")
        payload = dict(item.payload)
        reasons: list[Reason] = list(item.reasons)
        if not dxgw_id:
            reasons.append(
                Reason(code="api_error", api=api, message="directConnectGatewayId 없음")
            )
        else:
            try:
                payload[key] = pages(
                    client, method, key, directConnectGatewayId=dxgw_id
                )
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, api)[1])
        out.append(Fetched(payload=payload, reasons=tuple(reasons)))
    return out


def _gateway_ids(associations: list[dict[str, Any]], gateway_type: str) -> list[str]:
    return _sorted(
        (a.get("associatedGateway") or {}).get("id")
        for a in associations
        if str((a.get("associatedGateway") or {}).get("type") or "") == gateway_type
        and alive(a.get("associationState"))
    )


def _association_rows(associations: list[dict[str, Any]]) -> list[str]:
    rows = []
    for a in associations:
        g = a.get("associatedGateway") or {}
        prefixes = sorted(
            str(p.get("cidr"))
            for p in a.get("allowedPrefixesToDirectConnectGateway") or []
            if p.get("cidr")
        )
        rows.append(
            "\t".join(
                (
                    str(a.get("associationId") or ""),
                    str(g.get("id") or ""),
                    str(g.get("type") or ""),
                    str(g.get("region") or ""),
                    str(g.get("ownerAccount") or ""),
                    str(a.get("associationState") or ""),
                    ",".join(prefixes),
                )
            )
        )
    return sorted(rows)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("direct_connect_gateway", item.get("directConnectGatewayState")):
        return None
    tags = dx_tags(item)
    owner, extra = ctx.attribute(item, "ownerAccount")
    native = item["directConnectGatewayId"]
    attributes: dict[str, Any] = {
        "state": item.get("directConnectGatewayState"),
        "amazon_side_asn": item.get("amazonSideAsn"),
        **extra,
    }
    associations = item.get(_ASSOCIATIONS[0])
    if associations is not None:
        attributes["associated_transit_gateway_ids"] = _gateway_ids(
            associations, "transitGateway"
        )
        attributes["associated_virtual_private_gateway_ids"] = _gateway_ids(
            associations, "virtualPrivateGateway"
        )
        live = [
            a.get("associatedGateway") or {}
            for a in associations
            if alive(a.get("associationState"))
        ]
        attributes["associated_regions"] = _sorted(g.get("region") for g in live)
        attributes["associated_account_ids"] = _sorted(
            g.get("ownerAccount") for g in live
        )
        attributes["gateway_associations"] = _association_rows(associations)
    node = Node(
        id=node_id("direct_connect_gateway", owner, GLOBAL, native),
        type="direct_connect_gateway",
        account_id=owner,
        region=None,
        native_id=native,
        name=tags.get("Name") or (item.get("directConnectGatewayName") or None),
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
