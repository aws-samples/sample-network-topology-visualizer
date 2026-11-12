"""Collect route table subnet associations and links to route targets.

Group destinations sharing a target into one link. Resolve targets after
full collection. Read customer-managed prefix list entries; retain only
metadata for AWS-managed lists. On additional query failure, omit the
affected attribute and record its reason in partial_reasons.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
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
    pages,
    tags_of,
)

SPEC = Spec("ec2", "describe_route_tables", "RouteTables", "ec2:DescribeRouteTables")
PREFIX_LIST_API = "ec2:DescribeManagedPrefixLists"
PREFIX_ENTRIES_API = "ec2:GetManagedPrefixListEntries"
_PL = "_PrefixLists"
_AWS_OWNER = "AWS"


def _pl_refs(item: dict[str, Any]) -> set[str]:
    return {
        str(r["DestinationPrefixListId"])
        for r in item.get("Routes") or []
        if r.get("DestinationPrefixListId")
    }


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query route prefix lists and enrich customer-managed list
    entries.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    wanted = sorted({pl for it in items for pl in _pl_refs(it.payload)})
    if not wanted:
        return items
    try:
        lists = pages(client, "describe_managed_prefix_lists", "PrefixLists")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, PREFIX_LIST_API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,)) if _pl_refs(it.payload) else it
            for it in items
        ]
    seen = {str(p.get("PrefixListId")): p for p in lists if p.get("PrefixListId")}
    info: dict[str, dict[str, Any]] = {}
    failed: dict[str, Reason] = {}
    for pl in wanted:
        raw = seen.get(pl)
        if raw is None:
            continue
        managed = str(raw.get("OwnerId") or "") == _AWS_OWNER
        entries: list[str] | None = None
        if not managed:
            try:
                got = pages(
                    client,
                    "get_managed_prefix_list_entries",
                    "Entries",
                    PrefixListId=pl,
                )
            except (ClientError, BotoCoreError) as exc:
                failed[pl] = classify(exc, PREFIX_ENTRIES_API)[1]
            else:
                entries = sorted({str(e["Cidr"]) for e in got if e.get("Cidr")})
        info[pl] = {
            "name": str(raw.get("PrefixListName") or ""),
            "managed": managed,
            "entries": entries,
        }
    out: list[Fetched] = []
    for it in items:
        refs = sorted(_pl_refs(it.payload))
        if not refs:
            out.append(it)
            continue
        reasons = list(it.reasons)
        for pl in refs:
            r = failed.get(pl)
            if r is not None and r not in reasons:
                reasons.append(r)
        payload = {**it.payload, _PL: {pl: info[pl] for pl in refs if pl in info}}
        out.append(Fetched(payload, tuple(reasons)))
    return out


def _prefix_attributes(pls: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "prefix_lists": sorted(
            f"{pl} {'aws' if v['managed'] else 'customer'} {v['name']}".rstrip()
            for pl, v in pls.items()
        )
    }
    customer = {pl: v for pl, v in pls.items() if not v["managed"]}
    if customer:
        out["prefix_list_entries"] = sorted(
            f"{pl} {c}" for pl, v in customer.items() for c in v["entries"] or []
        )
    unread = sorted(pl for pl, v in customer.items() if v["entries"] is None)
    if unread:
        out["prefix_list_entries_unread"] = unread
    return out


_ROUTE_TARGET_KEYS = (
    "TransitGatewayId",
    "NatGatewayId",
    "VpcPeeringConnectionId",
    "EgressOnlyInternetGatewayId",
    "CarrierGatewayId",
    "LocalGatewayId",
    "NetworkInterfaceId",
    "InstanceId",
    "CoreNetworkArn",
    "GatewayId",
)


def _route_target(route: dict[str, Any]) -> str | None:
    for key in _ROUTE_TARGET_KEYS:
        value = route.get(key)
        if value and str(value) != "local":
            if key == "CoreNetworkArn":
                return str(value).rsplit("/", 1)[-1]
            return str(value)
    return None


_PROPAGATED = "EnableVgwRoutePropagation"


def _destination(route: dict[str, Any]) -> str | None:
    value = (
        route.get("DestinationCidrBlock")
        or route.get("DestinationIpv6CidrBlock")
        or route.get("DestinationPrefixListId")
    )
    return str(value) if value else None


def _route_line(route: dict[str, Any]) -> str:
    target = _route_target(route) or (
        "local" if str(route.get("GatewayId") or "") == "local" else "-"
    )
    return " ".join(
        [
            _destination(route) or "-",
            target,
            str(route.get("State") or "-").lower(),
            str(route.get("Origin") or "-"),
        ]
    )


def _quota_routes(routes: list[dict[str, Any]]) -> dict[str, int]:
    static = [r for r in routes if r.get("Origin") != _PROPAGATED]
    return {
        "ipv4_route_count": sum(
            1
            for r in static
            if r.get("DestinationCidrBlock") or r.get("DestinationPrefixListId")
        ),
        "ipv6_route_count": sum(1 for r in static if r.get("DestinationIpv6CidrBlock")),
        "propagated_route_count": len(routes) - len(static),
    }


# Group destinations sharing a route target into one relationship.
def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["RouteTableId"]
    nid = node_id("route_table", owner, ctx.region, native)
    associations = item.get("Associations") or []
    routes = item.get("Routes") or []
    edges: list[EdgeDraft] = []

    edge_gateways: set[str] = set()
    for association in associations:
        subnet = association.get("SubnetId")
        if not subnet:
            if association.get("GatewayId") and alive(
                (association.get("AssociationState") or {}).get("State")
            ):
                edge_gateways.add(str(association["GatewayId"]))
            continue
        state = str(
            (association.get("AssociationState") or {}).get("State") or "associated"
        )
        edges.append(
            EdgeDraft(
                "association",
                nid,
                str(subnet),
                "subnet",
                False,
                {"state": state},
                live=alive(state),
            )
        )

    grouped: dict[str, dict[str, Any]] = {}
    for route in routes:
        target = _route_target(route)
        if target is None:
            continue
        bucket = grouped.setdefault(
            target, {"destinations": set(), "dead": set(), "blackhole": False}
        )
        destination = _destination(route)
        if destination:
            bucket["destinations"].add(destination)
        if str(route.get("State") or "").lower() == "blackhole":
            bucket["blackhole"] = True
            if destination:
                bucket["dead"].add(destination)
    for target, bucket in grouped.items():
        edges.append(
            EdgeDraft(
                "route",
                nid,
                target,
                None,
                True,
                {
                    "destinations": sorted(bucket["destinations"]),
                    "blackhole": bucket["blackhole"],
                    "blackhole_destinations": sorted(bucket["dead"]),
                },
            )
        )

    node = Node(
        id=nid,
        type="route_table",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "main": any(a.get("Main") for a in associations),
            "association_count": len(associations),
            "route_count": len(routes),
            **_quota_routes(routes),
            "routes": sorted(_route_line(r) for r in routes),
            "edge_association_gateway_ids": sorted(edge_gateways),
            "propagating_vgw_ids": sorted(
                {
                    str(v["GatewayId"])
                    for v in item.get("PropagatingVgws") or []
                    if v.get("GatewayId")
                }
            ),
            **(_prefix_attributes(item[_PL]) if item.get(_PL) is not None else {}),
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
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
