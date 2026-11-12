"""Collect REST API configuration and load balancer links through VPC
links.

Record stage-variable targets in unresolved_vpc_links without resolving
them. Exclude integration URIs, policies, keys, and stage variable
values
from output.
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
    classify,
    draft,
    pages,
)
from .base import sorted_strs as _sorted

API = "apigateway:GET"
SPEC = Spec("apigateway", "get_rest_apis", "items", API)
_LINK_IDS = "_LinkIds"
_LINKS = "_Links"
WHY_ATTR = "unresolved_vpc_links"


def lb_ref(arn: str) -> tuple[str, str, str] | None:
    parts = str(arn).split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[2] != "elasticloadbalancing":
        return None
    res = parts[5].split("/")
    if len(res) < 3 or res[0] not in ("loadbalancer", "listener"):
        return None
    return parts[3], parts[4], res[2]


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query REST API resources and methods to resolve VPC links and
    targets.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for it in items:
        try:
            res = pages(
                client,
                "get_resources",
                "items",
                restApiId=str(it.payload["id"]),
                embed=["methods"],
            )
        except (ClientError, BotoCoreError) as exc:
            out.append(Fetched(it.payload, it.reasons + (classify(exc, API)[1],)))
            continue
        ids = sorted(
            {
                str((m.get("methodIntegration") or {}).get("connectionId"))
                for r in res
                for m in (r.get("resourceMethods") or {}).values()
                if (m.get("methodIntegration") or {}).get("connectionType")
                == "VPC_LINK"
                and (m.get("methodIntegration") or {}).get("connectionId")
            }
        )
        out.append(Fetched({**it.payload, _LINK_IDS: ids}, it.reasons))
    if not any(it.payload.get(_LINK_IDS) for it in out):
        return out
    try:
        links = pages(client, "get_vpc_links", "items")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,))
            if it.payload.get(_LINK_IDS)
            else it
            for it in out
        ]
    table = {
        str(v["id"]): {
            "name": str(v.get("name") or ""),
            "targets": _sorted(v.get("targetArns") or []),
        }
        for v in links
        if v.get("id")
    }
    return [
        Fetched({**it.payload, _LINKS: table}, it.reasons)
        if it.payload.get(_LINK_IDS)
        else it
        for it in out
    ]


def link_edges(
    nid: str,
    ctx: Ctx,
    uses: list[tuple[str, str | None]],
    links: dict[str, dict[str, Any]] | None,
) -> tuple[list[EdgeDraft], list[str]]:
    per: dict[str, dict[str, Any]] = {}
    why: set[str] = set()
    for link, uri in uses:
        if link.startswith("${"):
            why.add(f"{link} stage_variable")
            continue
        meta = (links or {}).get(link)
        if uri is not None:
            arns = [uri]
        elif meta is None:
            if links is not None:
                why.add(f"{link} link_not_found")
            continue
        else:
            arns = list(meta["targets"])
        for arn in arns:
            ref = lb_ref(arn)
            if ref is None:
                why.add(f"{link} unknown_target")
                continue
            region, account, name = ref
            if account not in ctx.scope_accounts or region not in ctx.scope_regions:
                why.add(f"{name} not_in_scope")
                continue
            tid = node_id("load_balancer", account, region, name)
            slot = per.setdefault(tid, {"name": name, "ids": set(), "names": set()})
            slot["ids"].add(link)
            if meta and meta["name"]:
                slot["names"].add(meta["name"])
    edges = [
        EdgeDraft(
            "vpc_link",
            nid,
            tid,
            None,
            True,
            {"vpc_link_ids": sorted(v["ids"]), "vpc_link_names": sorted(v["names"])},
            by_id=True,
            ref=v["name"],
            why_attr=WHY_ATTR,
        )
        for tid, v in sorted(per.items())
    ]
    return edges, sorted(why)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["id"])
    nid = node_id("api_gateway", owner, ctx.region, native)
    attributes: dict[str, Any] = {
        "protocol": "REST",
        "endpoint_types": _sorted(
            (item.get("endpointConfiguration") or {}).get("types") or []
        ),
    }
    edges: list[EdgeDraft] = []
    ids = item.get(_LINK_IDS)
    if ids is not None:
        attributes["vpc_link_ids"] = list(ids)
        edges, why = link_edges(nid, ctx, [(i, None) for i in ids], item.get(_LINKS))
        if why:
            attributes[WHY_ATTR] = why
    tags = {str(k): str(v) for k, v in (item.get("tags") or {}).items()}
    node = Node(
        id=nid,
        type="api_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=item.get("name"),
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
