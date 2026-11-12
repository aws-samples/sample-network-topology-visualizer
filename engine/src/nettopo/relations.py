"""Link EIPs to owners and WAF ACLs to protected resources.

Resolve EIP ENI owners using the load balancer target rules. Do not make
additional AWS API calls. Store unresolved reasons in EipOwners.why.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lb_targets import _eni_of, _eni_owner, _Index
from .model import Edge, node_id
from .services.base import NodeDraft
from .services.waf_web_acl import EDGE as WAF_EDGE
from .services.waf_web_acl import acl_node_id

EIP_EDGE = "eip_association"
EIP_WHY_ATTR = "association_unresolved"
ENI_API = "ec2:DescribeNetworkInterfaces"
_NAT_TOKEN = re.compile(r"\b(nat-[0-9a-f]{8,})\b")


@dataclass(slots=True)
class EipOwners:
    """Store Elastic IP owner links and unresolved references.

    edges: Relationships between Elastic IPs and owner resources
    owner: Mapping from Elastic IP Node.id to owner Node.id
    why: Unresolved references and reasons by Elastic IP Node.id
    """
    edges: list[Edge] = field(default_factory=list)
    owner: dict[str, str] = field(default_factory=dict)
    why: dict[str, str] = field(default_factory=dict)


def _nat_owner(eni_type: str, desc: str, ix: _Index) -> str | None:
    if eni_type != "nat_gateway" and not desc.startswith("Interface for NAT Gateway"):
        return None
    m = _NAT_TOKEN.search(desc)
    return ix.native(m.group(1), "nat_gateway") if m else None


def eip_owners(
    kept: list[NodeDraft], ids: set[str], by_native: dict[str, str]
) -> EipOwners:
    """Return Elastic IP owners, relationships, and unresolved reasons.

    kept: Collected Elastic IPs and candidate owners
    ids: Node.id values included in the collection result
    by_native: Mapping from AWS resource IDs to Node.id
    """
    out = EipOwners()
    eips = sorted(
        (d for d in kept if d.node.type == "elastic_ip"), key=lambda d: d.node.id
    )
    if not eips:
        return out
    ix = _Index(kept, ids, by_native)
    for d in eips:
        a = d.node.attributes
        if not a.get("associated"):
            continue
        hit: str | None = None
        code: str | None = None
        inst = a.get("instance_id")
        eni_id = a.get("network_interface_id")
        if inst:
            ref = str(inst)
            hit = ix.native(ref, "ec2_instance")
            code = None if hit else "not_collected"
        elif eni_id:
            ref = str(eni_id)
            raw = d.private.get("eni")
            if raw is None:
                denied = any(r.api == ENI_API for r in d.node.partial_reasons)
                code = "eni_unread" if denied else "no_eni"
            else:
                vid = node_id(
                    "vpc", d.account_id, d.region, str(raw.get("VpcId") or "")
                )
                eni = _eni_of(raw, d.account_id, d.region, vid)
                hit = _nat_owner(eni.interface_type, eni.description, ix)
                if hit is None:
                    hit, code = _eni_owner(eni, ix)
        else:
            continue
        if hit:
            out.owner[d.node.id] = hit
            attrs = {
                k: str(a[k])
                for k in ("network_interface_id", "private_ip_address")
                if a.get(k)
            }
            out.edges.append(
                Edge(
                    id="|".join([EIP_EDGE, d.node.id, hit]),
                    type=EIP_EDGE,
                    source=d.node.id,
                    target=hit,
                    directed=True,
                    attributes=attrs,
                )
            )
        else:
            out.why[d.node.id] = f"{ref} {code or 'not_collected'}"
    out.edges.sort(key=lambda e: e.id)
    return out


_WAF_ATTR = {"load_balancer": "web_acl_arn", "cloudfront": "web_acl_id"}


def waf_protections(kept: list[NodeDraft], ids: set[str]) -> list[Edge]:
    """Return relationships to resources protected by WAF web ACLs.

    kept: Collected load balancers, CloudFront distributions, and ACL
        references
    ids: Node.id values included in the collection result

    Link only web ACLs present in the collection result.
    """
    out: list[Edge] = []
    for d in sorted(kept, key=lambda d: d.node.id):
        attr = _WAF_ATTR.get(d.node.type)
        if attr is None:
            continue
        acl = acl_node_id(str(d.node.attributes.get(attr) or ""))
        if acl is None or acl not in ids:
            continue
        out.append(
            Edge(
                id="|".join([WAF_EDGE, acl, d.node.id]),
                type=WAF_EDGE,
                source=acl,
                target=d.node.id,
                directed=True,
            )
        )
    return out
