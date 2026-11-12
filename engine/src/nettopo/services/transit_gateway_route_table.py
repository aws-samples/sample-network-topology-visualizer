"""Collect TGW route table associations, propagations, and target links.

On additional query failure, omit the affected attribute and record its
reason in partial_reasons. Use empty arrays only for successful empty
queries. Retain blackhole destinations separately.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    alive,
    classify,
    draft,
    gone,
    tags_of,
)
from .base import pages as _pages

SPEC = Spec(
    "ec2",
    "describe_transit_gateway_route_tables",
    "TransitGatewayRouteTables",
    "ec2:DescribeTransitGatewayRouteTables",
)

_FOLLOWUPS: tuple[tuple[str, str, str, str, dict[str, Any]], ...] = (
    (
        "_associations",
        "get_transit_gateway_route_table_associations",
        "Associations",
        "ec2:GetTransitGatewayRouteTableAssociations",
        {},
    ),
    (
        "_propagations",
        "get_transit_gateway_route_table_propagations",
        "TransitGatewayRouteTablePropagations",
        "ec2:GetTransitGatewayRouteTablePropagations",
        {},
    ),
    (
        "_routes",
        "search_transit_gateway_routes",
        "Routes",
        "ec2:SearchTransitGatewayRoutes",
        {"Filters": [{"Name": "type", "Values": ["static", "propagated"]}]},
    ),
    (
        "_prefix_list_references",
        "get_transit_gateway_prefix_list_references",
        "TransitGatewayPrefixListReferences",
        "ec2:GetTransitGatewayPrefixListReferences",
        {},
    ),
)

_PROPAGATION_OFF = frozenset({"disabled", "disabling"})


# Follow-up calls can fail independently. Omit keys for unread data.
def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query TGW route table associations, propagations, and routes.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        payload = dict(item.payload)
        reasons = item.reasons
        table_id = payload.get("TransitGatewayRouteTableId")
        for key, method, result_key, api, extra in _FOLLOWUPS:
            try:
                payload[key] = _pages(
                    client,
                    method,
                    result_key,
                    TransitGatewayRouteTableId=table_id,
                    **extra,
                )
            except (ClientError, BotoCoreError) as exc:
                reasons = reasons + (classify(exc, api)[1],)
        out.append(Fetched(payload=payload, reasons=reasons))
    return out


def _attachment_ids(entries: list[dict[str, Any]], keep: Any) -> list[str]:
    return sorted(
        {
            str(e["TransitGatewayAttachmentId"])
            for e in entries
            if e.get("TransitGatewayAttachmentId") and keep(e.get("State"))
        }
    )


def _s(value: Any) -> str:
    return str(value) if value not in (None, "") else "-"


def _entry_lines(entries: list[dict[str, Any]]) -> list[str]:
    return sorted(
        " ".join(
            [
                _s(e.get("TransitGatewayAttachmentId")),
                _s(e.get("ResourceType")),
                _s(e.get("ResourceId")),
                _s(str(e.get("State") or "").lower()),
            ]
        )
        for e in entries
        if e.get("TransitGatewayAttachmentId") and alive(e.get("State"))
    )


def _route_lines(routes: list[dict[str, Any]]) -> list[str]:
    lines: set[str] = set()
    for route in routes:
        state = str(route.get("State") or "").lower()
        destination = _destination(route)
        if not alive(state) or destination is None:
            continue
        kind = _s(str(route.get("Type") or "").lower())
        attachments = route.get("TransitGatewayAttachments") or [{}]
        for a in attachments:
            lines.add(
                " ".join(
                    [
                        destination,
                        _s(a.get("TransitGatewayAttachmentId")),
                        _s(a.get("ResourceType")),
                        _s(a.get("ResourceId")),
                        kind,
                        _s(state),
                    ]
                )
            )
    return sorted(lines)


def _prefix_reference_lines(refs: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for r in refs:
        state = str(r.get("State") or "").lower()
        if not r.get("PrefixListId") or not alive(state):
            continue
        att = r.get("TransitGatewayAttachment") or {}
        out.append(
            " ".join(
                [
                    str(r["PrefixListId"]),
                    _s(r.get("PrefixListOwnerId")),
                    _s(att.get("TransitGatewayAttachmentId")),
                    _s(att.get("ResourceType")),
                    _s(att.get("ResourceId")),
                    _s(state),
                    "yes" if r.get("Blackhole") else "no",
                ]
            )
        )
    return sorted(out)


def _destination(route: dict[str, Any]) -> str | None:
    value = route.get("DestinationCidrBlock") or route.get("PrefixListId")
    return str(value) if value else None


def _route_edges(
    nid: str, routes: list[dict[str, Any]]
) -> tuple[list[EdgeDraft], list[str]]:
    grouped: dict[str, dict[str, set[str]]] = {}
    blackholes: set[str] = set()
    for route in routes:
        state = str(route.get("State") or "").lower()
        destination = _destination(route)
        if not alive(state) or destination is None:
            continue
        if state == "blackhole":
            blackholes.add(destination)
            continue
        for attachment in route.get("TransitGatewayAttachments") or []:
            target = attachment.get("TransitGatewayAttachmentId")
            if not target:
                continue
            bucket = grouped.setdefault(str(target), {"all": set(), "static": set()})
            bucket["all"].add(destination)
            if str(route.get("Type") or "").lower() == "static":
                bucket["static"].add(destination)
    edges = [
        EdgeDraft(
            "route",
            nid,
            target,
            "transit_gateway_attachment",
            True,
            {
                "destinations": sorted(bucket["all"]),
                "static_destinations": sorted(bucket["static"]),
            },
        )
        for target, bucket in sorted(grouped.items())
    ]
    return edges, sorted(blackholes)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("transit_gateway_route_table", item.get("State")):
        return None
    owner = ctx.account_id
    native = item["TransitGatewayRouteTableId"]
    tags = tags_of(item)
    attributes: dict[str, Any] = {
        "transit_gateway_id": item.get("TransitGatewayId"),
        "state": item.get("State"),
        "default_association": item.get("DefaultAssociationRouteTable"),
        "default_propagation": item.get("DefaultPropagationRouteTable"),
    }
    if "_associations" in item:
        attributes["association_attachment_ids"] = _attachment_ids(
            item["_associations"], alive
        )
        attributes["associations"] = _entry_lines(item["_associations"])
    if "_propagations" in item:
        attributes["propagation_attachment_ids"] = _attachment_ids(
            item["_propagations"],
            lambda state: str(state or "").lower() not in _PROPAGATION_OFF,
        )
        attributes["propagations"] = _entry_lines(item["_propagations"])
    if "_prefix_list_references" in item:
        attributes["prefix_list_references"] = _prefix_reference_lines(
            item["_prefix_list_references"]
        )
    nid = node_id("transit_gateway_route_table", owner, ctx.region, native)
    edges: list[EdgeDraft] = []
    if "_routes" in item:
        edges, blackholes = _route_edges(nid, item["_routes"])
        attributes["blackhole_destinations"] = blackholes
        attributes["routes"] = _route_lines(item["_routes"])

    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("TransitGatewayId"):
        parents.append(
            node_id("transit_gateway", owner, ctx.region, str(item["TransitGatewayId"]))
        )
        parent_types.append("transit_gateway")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=nid,
        type="transit_gateway_route_table",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
