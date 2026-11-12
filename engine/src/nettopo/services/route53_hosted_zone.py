"""Collect global Route 53 zones, aliases, and private VPC associations.

Link aliases to collected load balancers and CloudFront distributions.
Exclude non-alias record values from topology.
"""

from __future__ import annotations

import re
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
    classify,
    dns_key,
    draft,
    pages,
)
from .base import sorted_strs as _sorted

SPEC = Spec("route53", "list_hosted_zones", "HostedZones", "route53:ListHostedZones")
RECORDS_API = "route53:ListResourceRecordSets"
ZONE_API = "route53:GetHostedZone"
_ALIASES = "_Aliases"
_VPCS = "_Vpcs"
_TARGET_DNS = re.compile(
    r"(\.elb\.(?:[a-z0-9-]+\.)?amazonaws\.com(?:\.cn)?|\.cloudfront\.net)$"
)


def _zone_id(raw: str) -> str:
    return str(raw).rsplit("/", 1)[-1]


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query zone VPC associations and records to enrich alias details.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for it in items:
        zid = str(it.payload.get("Id") or "")
        payload = dict(it.payload)
        reasons = list(it.reasons)
        try:
            records = pages(
                client,
                "list_resource_record_sets",
                "ResourceRecordSets",
                HostedZoneId=zid,
            )
        except (ClientError, BotoCoreError) as exc:
            reasons.append(classify(exc, RECORDS_API)[1])
        else:
            aliases: dict[str, set[str]] = {}
            for r in records:
                target = (r.get("AliasTarget") or {}).get("DNSName")
                if not target or r.get("Type") not in ("A", "AAAA"):
                    continue
                key = dns_key(str(target))
                if _TARGET_DNS.search(key):
                    aliases.setdefault(key, set()).add(
                        dns_key(str(r.get("Name") or ""))
                    )
            payload[_ALIASES] = {k: sorted(v) for k, v in sorted(aliases.items())}
        if (it.payload.get("Config") or {}).get("PrivateZone"):
            try:
                got = client.get_hosted_zone(Id=zid)
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, ZONE_API)[1])
            else:
                payload[_VPCS] = sorted(
                    (str(v.get("VPCId")), str(v.get("VPCRegion") or ""))
                    for v in got.get("VPCs") or []
                    if v.get("VPCId")
                )
        out.append(Fetched(payload, tuple(reasons)))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = _zone_id(item["Id"])
    nid = node_id("route53_hosted_zone", owner, GLOBAL, native)
    private = bool((item.get("Config") or {}).get("PrivateZone"))
    attributes: dict[str, Any] = {
        "zone_name": str(item.get("Name") or "").rstrip("."),
        "private": private,
        "record_count": item.get("ResourceRecordSetCount"),
    }
    edges: list[EdgeDraft] = []
    aliases = item.get(_ALIASES)
    if aliases is not None:
        attributes["alias_record_names"] = _sorted(
            n for names in aliases.values() for n in names
        )
        for key, names in aliases.items():
            edges.append(
                EdgeDraft(
                    "dns_alias",
                    nid,
                    key,
                    None,
                    True,
                    {"record_names": list(names)},
                    by_dns=True,
                )
            )
    vpcs = item.get(_VPCS)
    if vpcs is not None:
        attributes["vpc_ids"] = _sorted(v for v, _ in vpcs)
        for vpc_id, vpc_region in vpcs:
            edges.append(
                EdgeDraft(
                    "zone_association",
                    nid,
                    vpc_id,
                    "vpc",
                    False,
                    {"vpc_region": vpc_region},
                )
            )
    node = Node(
        id=nid,
        type="route53_hosted_zone",
        account_id=owner,
        region=None,
        native_id=native,
        name=attributes["zone_name"] or native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
