"""Create CloudFront S3 origin nodes and load balancer origin links.

Build S3 nodes from bucket names in origin hostnames without querying
ownership or policies. Resolve custom domains to load balancers through
collected Route 53 aliases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import GLOBAL, Edge, Node, node_id
from .services.base import NodeDraft, dns_key

DERIVED = "s3_bucket"
_S3 = re.compile(
    r"^(?P<bucket>[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9])\.s3"
    r"(?:(?P<web>-website)?(?:[.\-](?P<region>[a-z]{2}(?:-[a-z]+)+-\d))?)?\.amazonaws\.com(?:\.cn)?$"
)


@dataclass(slots=True)
class Joined:
    """Store links from CloudFront origins to collected resources.

    nodes: Bucket nodes derived from S3 origin references
    edges: Relationships between CloudFront distributions and origin
        resources
    """
    nodes: list[NodeDraft]
    edges: list[Edge]


def _s3(domain: str) -> tuple[str, bool, str | None] | None:
    m = _S3.match(dns_key(domain))
    if not m:
        return None
    return m.group("bucket"), bool(m.group("web")), m.group("region")


def join(
    kept: list[NodeDraft], ids: set[str], by_dns: dict[str, str], existing: set[str]
) -> Joined:
    """Return CloudFront origin relationships and S3 bucket nodes.

    kept: Collected CloudFront, Route 53, and load balancer details
    ids: Node.id values included in the collection result
    by_dns: Mapping from DNS names to collected Node.id
    existing: Existing Edge.id values used to avoid duplicate links

    Create bucket nodes for S3 origins and link collected load balancer
    origins. Do not make additional API calls.
    """
    alias: dict[str, set[str]] = {}
    for d in kept:
        if d.node.type != "route53_hosted_zone":
            continue
        for e in d.edges:
            if e.type == "dns_alias":
                for name in e.attributes.get("record_names") or []:
                    alias.setdefault(str(name), set()).add(e.target_native)
    buckets: dict[str, dict[str, object]] = {}
    edges: dict[str, Edge] = {}
    for d in sorted(kept, key=lambda x: x.node.id):
        n = d.node
        if n.type != "cloudfront":
            continue
        for raw in sorted(
            str(x) for x in n.attributes.get("origin_domain_names") or []
        ):
            s3 = _s3(raw)
            if s3:
                bucket, web, region = s3
                bid = node_id(DERIVED, n.account_id, GLOBAL, bucket)
                slot = buckets.setdefault(
                    bid,
                    {
                        "account": n.account_id,
                        "bucket": bucket,
                        "web": False,
                        "regions": set(),
                    },
                )
                slot["web"] = bool(slot["web"]) or web
                if region:
                    slot["regions"].add(region)  # type: ignore[union-attr]
                _add(edges, existing, n.id, bid, {"via": "s3"})
                continue
            key = dns_key(raw)
            targets = sorted(
                t
                for t in (by_dns.get(x) for x in alias.get(key, set()))
                if t and t.startswith("load_balancer/") and t in ids
            )
            if len(targets) == 1:
                _add(
                    edges,
                    existing,
                    n.id,
                    targets[0],
                    {"via": "dns_alias", "alias": key},
                )
    nodes = [
        NodeDraft(
            node=Node(
                id=bid,
                type=DERIVED,
                account_id=str(s["account"]),
                region=None,
                native_id=str(s["bucket"]),
                name=str(s["bucket"]),
                parent_id=node_id("account", str(s["account"])),
                attributes={
                    "bucket_name": s["bucket"],
                    "website": bool(s["web"]),
                    "regions": sorted(s["regions"]),  # type: ignore[arg-type]
                },
            ),
            account_id=str(s["account"]),
            region="",
            parents=(node_id("account", str(s["account"])),),
            parent_types=("account",),
        )
        for bid, s in sorted(buckets.items())
    ]
    return Joined(nodes=nodes, edges=[edges[k] for k in sorted(edges)])


def _add(
    edges: dict[str, Edge],
    existing: set[str],
    src: str,
    dst: str,
    attrs: dict[str, object],
) -> None:
    eid = "|".join(("origin", src, dst))
    if eid in existing or eid in edges:
        return
    edges[eid] = Edge(
        id=eid, type="origin", source=src, target=dst, directed=True, attributes=attrs
    )
