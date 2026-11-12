"""Collect global CloudFront distributions, origins, and cache
behaviors.

Merge results with the same Node.id into one node. Record coverage by
query region. Exclude transient distribution Status from topology.
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
)
from .base import sorted_strs as _sorted

_LB_DNS = re.compile(r"\.elb\.(?:[a-z0-9-]+\.)?amazonaws\.com(?:\.cn)?$")

SPEC = Spec(
    "cloudfront",
    "list_distributions",
    "DistributionList.Items",
    "cloudfront:ListDistributions",
)


VPC_ORIGINS_API = "cloudfront:ListVpcOrigins"
_VPC_ORIGINS = "_VpcOrigins"


def _vpc_origin_ids(item: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str((o.get("VpcOriginConfig") or {}).get("VpcOriginId"))
            for o in _items(item.get("Origins"))
            if isinstance(o, dict)
            and (o.get("VpcOriginConfig") or {}).get("VpcOriginId")
        }
    )


CACHE_POLICIES_API = "cloudfront:ListCachePolicies"
OACS_API = "cloudfront:ListOriginAccessControls"
_CACHE_POLICY_NAMES = "_CachePolicyNames"
_OAC_NAMES = "_OacNames"


def _behaviors(item: dict[str, Any]) -> list[dict[str, Any]]:
    out = [b for b in _items(item.get("CacheBehaviors")) if isinstance(b, dict)]
    default = item.get("DefaultCacheBehavior")
    if isinstance(default, dict):
        out.append({**default, "PathPattern": ""})
    return out


def _cache_policy_ids(item: dict[str, Any]) -> list[str]:
    return sorted(
        {str(b["CachePolicyId"]) for b in _behaviors(item) if b.get("CachePolicyId")}
    )


def _oac_ids(item: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(o["OriginAccessControlId"])
            for o in _items(item.get("Origins"))
            if isinstance(o, dict) and o.get("OriginAccessControlId")
        }
    )


def _list_names(client: Any, method: str, list_key: str, pick: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    params: dict[str, Any] = {}
    seen: set[str] = set()
    while True:
        page = (getattr(client, method)(**params) or {}).get(list_key) or {}
        for v in page.get("Items") or []:
            got = pick(v)
            if got and got[0] and got[1]:
                found[str(got[0])] = str(got[1])
        marker = page.get("NextMarker")
        if (
            not marker or marker in seen
        ):
            break
        seen.add(marker)
        params = {"Marker": marker}
    return found


def _enrich_names(client: Any, items: list[Fetched]) -> list[Fetched]:
    jobs = (
        (
            _cache_policy_ids,
            "list_cache_policies",
            "CachePolicyList",
            CACHE_POLICIES_API,
            _CACHE_POLICY_NAMES,
            lambda v: (
                (v.get("CachePolicy") or {}).get("Id"),
                ((v.get("CachePolicy") or {}).get("CachePolicyConfig") or {}).get(
                    "Name"
                ),
            ),
        ),
        (
            _oac_ids,
            "list_origin_access_controls",
            "OriginAccessControlList",
            OACS_API,
            _OAC_NAMES,
            lambda v: (v.get("Id"), v.get("Name")),
        ),
    )
    for want_of, method, list_key, api, key, pick in jobs:
        if not any(want_of(it.payload) for it in items):
            continue
        reason = None
        found: dict[str, str] = {}
        try:
            found = _list_names(client, method, list_key, pick)
        except (ClientError, BotoCoreError) as exc:
            reason = classify(exc, api)[1]
        out: list[Fetched] = []
        for it in items:
            want = want_of(it.payload)
            if not want:
                out.append(it)
            elif reason is not None:
                out.append(Fetched(it.payload, (*it.reasons, reason)))
            else:
                out.append(
                    Fetched(
                        {**it.payload, key: {k: found[k] for k in want if k in found}},
                        it.reasons,
                    )
                )
        items = out
    return items


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Enrich distributions with VPC origins, cache policies, and OAC
    names.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    return _enrich_names(client, _enrich_vpc_origins(client, items))


def _enrich_vpc_origins(client: Any, items: list[Fetched]) -> list[Fetched]:
    if not any(_vpc_origin_ids(it.payload) for it in items):
        return items
    found: dict[str, str] = {}
    reason = None
    try:
        params: dict[str, Any] = {}
        while True:
            page = (client.list_vpc_origins(**params) or {}).get("VpcOriginList") or {}
            for v in page.get("Items") or []:
                if v.get("Id") and v.get("OriginEndpointArn"):
                    found[str(v["Id"])] = str(v["OriginEndpointArn"])
            marker = page.get("NextMarker")
            if not marker:
                break
            params = {"Marker": marker}
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, VPC_ORIGINS_API)[1]
    out: list[Fetched] = []
    for it in items:
        want = _vpc_origin_ids(it.payload)
        if not want:
            out.append(it)
        elif reason is not None:
            out.append(Fetched(it.payload, it.reasons + (reason,)))
        else:
            out.append(
                Fetched(
                    {
                        **it.payload,
                        _VPC_ORIGINS: {k: found[k] for k in want if k in found},
                    },
                    it.reasons,
                )
            )
    return out


def _vpc_origin_edges(source: str, item: dict[str, Any]) -> list[EdgeDraft]:
    out: list[EdgeDraft] = []
    for arn in sorted(set((item.get(_VPC_ORIGINS) or {}).values())):
        parts = arn.split(":", 5)
        res = parts[5] if len(parts) == 6 else ""
        if res.startswith("loadbalancer/") and len(res.split("/")) > 2:
            target, kind = res.split("/")[2], "load_balancer"
        elif res.startswith("instance/"):
            target, kind = res.split("/", 1)[1], "ec2_instance"
        else:
            continue
        out.append(
            EdgeDraft("origin", source, target, kind, True, {"via": "vpc_origin"})
        )
    return out


def _items(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return []
    return list(value.get("Items") or [])


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["Id"]
    origins = _items(item.get("Origins"))
    node = Node(
        id=node_id("cloudfront", owner, GLOBAL, native),
        type="cloudfront",
        account_id=owner,
        region=None,
        native_id=native,
        arn=item.get("ARN"),
        name=item.get("DomainName"),
        attributes={
            "domain_name": item.get("DomainName"),
            "aliases": _sorted(_items(item.get("Aliases"))),
            "origin_domain_names": _sorted(o.get("DomainName") for o in origins),
            "price_class": item.get("PriceClass"),
            "enabled": item.get("Enabled"),
            "is_ipv6_enabled": item.get("IsIPV6Enabled"),
            "http_version": item.get("HttpVersion"),
            "web_acl_id": item.get("WebACLId") or None,
            "comment": item.get("Comment") or None,
            "origins": _origin_rows(origins, item.get(_OAC_NAMES)),
            "behaviors": _behavior_rows(item, item.get(_CACHE_POLICY_NAMES)),
        },
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
        edges=_vpc_origin_edges(node.id, item) + _origin_edges(node.id, origins),
    )


def _origin_type(o: dict[str, Any]) -> str:
    if (o.get("VpcOriginConfig") or {}).get("VpcOriginId"):
        return "vpc"
    if "S3OriginConfig" in o:
        return "s3"
    if "CustomOriginConfig" in o:
        return "custom"
    return ""


def _origin_rows(origins: list[Any], oac_names: Any) -> list[str]:
    names = oac_names if isinstance(oac_names, dict) else {}
    rows = []
    for o in origins:
        if not isinstance(o, dict):
            continue
        oac = str(o.get("OriginAccessControlId") or "")
        rows.append(
            "\t".join(
                (
                    str(o.get("Id") or ""),
                    str(o.get("DomainName") or ""),
                    _origin_type(o),
                    str(o.get("OriginPath") or ""),
                    oac,
                    str(names.get(oac, "")) if oac else "",
                )
            )
        )
    return rows


def _behavior_rows(item: dict[str, Any], policy_names: Any) -> list[str]:
    names = policy_names if isinstance(policy_names, dict) else {}
    rows = []
    for n, b in enumerate(_behaviors(item)):
        policy = str(b.get("CachePolicyId") or "")
        rows.append(
            "\t".join(
                (
                    str(n),
                    str(b.get("PathPattern") or ""),
                    str(b.get("TargetOriginId") or ""),
                    str(b.get("ViewerProtocolPolicy") or ""),
                    policy,
                    str(names.get(policy, "")) if policy else "",
                )
            )
        )
    return rows


def _origin_edges(source: str, origins: list[Any]) -> list[EdgeDraft]:
    keys = {
        dns_key(o["DomainName"])
        for o in origins
        if isinstance(o, dict) and isinstance(o.get("DomainName"), str)
    }
    return [
        EdgeDraft(
            type="origin",
            source=source,
            target_native=key,
            target_type="load_balancer",
            directed=True,
            attributes={},
            by_dns=True,
        )
        for key in sorted(keys)
        if _LB_DNS.search(key)
    ]


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
