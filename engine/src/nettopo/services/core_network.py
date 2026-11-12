"""Collect Cloud WAN core networks and attachments in us-west-2.

Resolve VPC targets using the account and region in attachment ARNs.
Place nodes in the owner account when in scope. Record attachments that
cannot be resolved to VPCs in other_attachments.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import GLOBAL, Node, node_id
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
from .base import sorted_strs as _sorted

API_REGION = "us-west-2"
SPEC = Spec(
    "networkmanager",
    "list_core_networks",
    "CoreNetworks",
    "networkmanager:ListCoreNetworks",
    region=API_REGION,
)
ATTACHMENTS_API = "networkmanager:ListAttachments"
_ATT = "_Attachments"
WHY_ATTR = "unresolved_attachments"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query core network attachments to resolve VPCs and other
    attachments.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for it in items:
        try:
            got = pages(
                client,
                "list_attachments",
                "Attachments",
                CoreNetworkId=str(it.payload["CoreNetworkId"]),
            )
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(it.payload, it.reasons + (classify(exc, ATTACHMENTS_API)[1],))
            )
            continue
        out.append(Fetched({**it.payload, _ATT: got}, it.reasons))
    return out


def _vpc_ref(arn: str) -> tuple[str, str, str] | None:
    parts = str(arn).split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[2] != "ec2":
        return None
    kind, _, vid = parts[5].partition("/")
    if kind != "vpc" or not vid:
        return None
    return parts[3], parts[4], vid


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    raw_owner = str(item.get("OwnerAccountId") or "")
    if raw_owner and raw_owner != ctx.account_id and raw_owner in ctx.scope_accounts:
        return None
    owner, extra = ctx.attribute(item, "OwnerAccountId")
    native = str(item["CoreNetworkId"])
    nid = node_id("core_network", owner, GLOBAL, native)
    tags = tags_of(item)
    attributes: dict[str, Any] = {
        "global_network_id": item.get("GlobalNetworkId"),
        "description": item.get("Description") or None,
        **extra,
    }
    edges: list[EdgeDraft] = []
    atts = item.get(_ATT)
    if atts is not None:
        per: dict[str, dict[str, Any]] = {}
        why: set[str] = set()
        others: set[str] = set()
        locations: set[str] = set()
        live_n = 0
        for a in sorted(atts, key=lambda x: str(x.get("AttachmentId"))):
            state = str(a.get("State") or "").lower()
            if not alive(state):
                continue
            live_n += 1
            kind = str(a.get("AttachmentType") or "")
            loc = str(a.get("EdgeLocation") or "")
            if loc:
                locations.add(loc)
            aid = str(a.get("AttachmentId") or "?")
            ref = _vpc_ref(str(a.get("ResourceArn") or "")) if kind == "VPC" else None
            if ref is None:
                others.add(f"{aid} {kind.lower() or 'unknown'} {loc or '?'}".rstrip())
                continue
            region, account, vid = ref
            if account not in ctx.scope_accounts or region not in ctx.scope_regions:
                why.add(f"{vid} not_in_scope {region}")
                continue
            target = node_id("vpc", account, region, vid)
            slot = per.setdefault(
                target,
                {
                    "ids": set(),
                    "segments": set(),
                    "locations": set(),
                    "states": set(),
                    "ref": vid,
                },
            )
            slot["ids"].add(aid)
            if a.get("SegmentName"):
                slot["segments"].add(str(a["SegmentName"]))
            if loc:
                slot["locations"].add(loc)
            slot["states"].add(state)
        for target, v in sorted(per.items()):
            edges.append(
                EdgeDraft(
                    "core_network_attachment",
                    nid,
                    target,
                    None,
                    False,
                    {
                        "attachment_ids": sorted(v["ids"]),
                        "segments": sorted(v["segments"]),
                        "edge_locations": sorted(v["locations"]),
                        "state": ",".join(sorted(v["states"])),
                    },
                    by_id=True,
                    ref=v["ref"],
                    why_attr=WHY_ATTR,
                )
            )
        attributes["attachment_count"] = live_n
        attributes["edge_locations"] = _sorted(locations)
        attributes["other_attachments"] = sorted(others)
        if why:
            attributes[WHY_ATTR] = sorted(why)
    node = Node(
        id=nid,
        type="core_network",
        account_id=owner,
        region=None,
        native_id=native,
        arn=item.get("CoreNetworkArn"),
        name=tags.get("Name") or native,
        attributes={k: v for k, v in attributes.items() if v is not None},
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
