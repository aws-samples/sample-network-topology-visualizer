"""Collect HTTP and WebSocket APIs as api_gateway nodes.

Resolve load balancer targets from VPC link listener ARNs. Record
reasons
for unresolved targets, including Cloud Map, in unresolved_vpc_links.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .api_gateway import API, WHY_ATTR, lb_ref, link_edges
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    pages,
)
from .base import sorted_strs as _sorted

SPEC = Spec("apigatewayv2", "get_apis", "Items", API)
_USES = "_Uses"
_LINKS = "_Links"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query HTTP and WebSocket integrations and VPC links to resolve
    targets.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for it in items:
        try:
            got = pages(
                client, "get_integrations", "Items", ApiId=str(it.payload["ApiId"])
            )
        except (ClientError, BotoCoreError) as exc:
            out.append(Fetched(it.payload, it.reasons + (classify(exc, API)[1],)))
            continue
        uses = sorted(
            {
                (str(g.get("ConnectionId") or ""), str(g.get("IntegrationUri") or ""))
                for g in got
                if g.get("ConnectionType") == "VPC_LINK"
            }
        )
        out.append(Fetched({**it.payload, _USES: uses}, it.reasons))
    if not any(it.payload.get(_USES) for it in out):
        return out
    try:
        links = pages(client, "get_vpc_links", "Items")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,)) if it.payload.get(_USES) else it
            for it in out
        ]
    table = {
        str(v["VpcLinkId"]): {
            "name": str(v.get("Name") or ""),
            "targets": [],
            "subnets": _sorted(v.get("SubnetIds") or []),
        }
        for v in links
        if v.get("VpcLinkId")
    }
    return [
        Fetched({**it.payload, _LINKS: table}, it.reasons)
        if it.payload.get(_USES)
        else it
        for it in out
    ]


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["ApiId"])
    nid = node_id("api_gateway", owner, ctx.region, native)
    attributes: dict[str, Any] = {
        "protocol": str(item.get("ProtocolType") or "") or None
    }
    edges: list[EdgeDraft] = []
    uses = item.get(_USES)
    if uses is not None:
        links = item.get(_LINKS)
        attributes["vpc_link_ids"] = _sorted(link for link, _ in uses)
        lb_uses: list[tuple[str, str | None]] = []
        why: list[str] = []
        for link, uri in uses:
            if lb_ref(uri) is not None:
                lb_uses.append((link, uri))
            elif ":servicediscovery:" in uri:
                why.append(f"{uri.rsplit('/', 1)[-1]} cloud_map")
            else:
                why.append(f"{link} unknown_target")
        edges, more = link_edges(nid, ctx, lb_uses, links if links is not None else {})
        lines = sorted(set(why) | set(more))
        if lines:
            attributes[WHY_ATTR] = lines
    tags = {str(k): str(v) for k, v in (item.get("Tags") or {}).items()}
    node = Node(
        id=nid,
        type="api_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=item.get("Name"),
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
