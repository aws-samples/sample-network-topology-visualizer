"""Collect standard Global Accelerator configuration in us-west-2.

Resolve ALB, NLB, instance, and EIP endpoints from the full collection.
Record unresolved endpoints and reasons in unresolved_endpoints.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import GLOBAL, Node, node_id
from .api_gateway import lb_ref
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

API_REGION = "us-west-2"
SPEC = Spec(
    "globalaccelerator",
    "list_accelerators",
    "Accelerators",
    "globalaccelerator:ListAccelerators",
    region=API_REGION,
)
LISTENERS_API = "globalaccelerator:ListListeners"
GROUPS_API = "globalaccelerator:ListEndpointGroups"
_EPS = "_Endpoints"
_PORTS = "_Ports"
_EMPTY = "_EmptyRegions"
WHY_ATTR = "unresolved_endpoints"


def _ports(listener: dict[str, Any]) -> list[str]:
    proto = str(listener.get("Protocol") or "")
    out = []
    for r in listener.get("PortRanges") or []:
        a, b = r.get("FromPort"), r.get("ToPort")
        out.append(f"{proto} {a}" if a == b else f"{proto} {a}-{b}")
    return out


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query listeners and endpoint groups to enrich accelerator
    targets.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for it in items:
        arn = str(it.payload.get("AcceleratorArn") or "")
        reasons = list(it.reasons)
        try:
            listeners = pages(client, "list_listeners", "Listeners", AcceleratorArn=arn)
        except (ClientError, BotoCoreError) as exc:
            reasons.append(classify(exc, LISTENERS_API)[1])
            out.append(Fetched(it.payload, tuple(reasons)))
            continue
        eps: set[tuple[str, str, str]] = (
            set()
        )
        ports: set[str] = set()
        empty: set[str] = set()
        failed = False
        for ls in sorted(listeners, key=lambda x: str(x.get("ListenerArn"))):
            lp = _ports(ls)
            ports.update(lp)
            try:
                groups = pages(
                    client,
                    "list_endpoint_groups",
                    "EndpointGroups",
                    ListenerArn=str(ls.get("ListenerArn")),
                )
            except (ClientError, BotoCoreError) as exc:
                r = classify(exc, GROUPS_API)[1]
                if r not in reasons:
                    reasons.append(r)
                failed = True
                continue
            for g in groups:
                region = str(g.get("EndpointGroupRegion") or "")
                ds = [
                    str(d["EndpointId"])
                    for d in g.get("EndpointDescriptions") or []
                    if d.get("EndpointId")
                ]
                if not ds:
                    empty.add(region)
                for eid in ds:
                    for p in lp or [""]:
                        eps.add((region, eid, p))
        payload = {**it.payload, _PORTS: sorted(ports), _EMPTY: sorted(empty)}
        if not failed:
            payload[_EPS] = sorted(eps)
        out.append(Fetched(payload, tuple(reasons)))
    return out


_LB_KIND = {"app": "alb", "net": "nlb", "gwy": "gwlb"}


def _kind_of(eid: str) -> str:
    if lb_ref(eid) is not None:
        res = str(eid).split(":", 5)[5].split("/")
        return _LB_KIND.get(res[1] if len(res) > 1 else "", "lb")
    if eid.startswith("i-"):
        return "ec2"
    if eid.startswith("eipalloc-"):
        return "eip"
    return "unknown"


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    arn = str(item["AcceleratorArn"])
    native = arn.rsplit("/", 1)[-1]
    nid = node_id("global_accelerator", owner, GLOBAL, native)
    attributes: dict[str, Any] = {
        "enabled": item.get("Enabled"),
        "ip_address_type": item.get("IpAddressType"),
        "dns_name": item.get("DnsName"),
        "static_ips": _sorted(
            ip for s in item.get("IpSets") or [] for ip in s.get("IpAddresses") or []
        ),
    }
    if item.get(_PORTS) is not None:
        attributes["listener_ports"] = list(item[_PORTS])
    if item.get(_EMPTY):
        attributes["empty_endpoint_regions"] = list(item[_EMPTY])
    edges: list[EdgeDraft] = []
    eps = item.get(_EPS)
    if eps is not None:
        attributes["endpoint_regions"] = _sorted(r for r, _, _ in eps)
        attributes["endpoint_targets"] = _sorted(
            f"{region} {_kind_of(eid)} {(lb_ref(eid) or (None, None, eid))[2]}"
            for region, eid, _ in eps
        )
        per: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        why: set[str] = set()
        for region, eid, port in eps:
            ref = lb_ref(eid)
            if ref is not None:
                r, account, name = ref
                key = (node_id("load_balancer", account, r, name), None, name)
                in_scope = account in ctx.scope_accounts and r in ctx.scope_regions
                kind = "lb"
            elif eid.startswith("i-"):
                key, in_scope, kind = (
                    (eid, "ec2_instance", eid),
                    region in ctx.scope_regions,
                    "ec2",
                )
            elif eid.startswith("eipalloc-"):
                key = (node_id("elastic_ip", owner, region, eid), None, eid)
                in_scope, kind = region in ctx.scope_regions, "eip"
            else:
                why.add(f"{eid} unknown_type {region}")
                continue
            if not in_scope:
                why.add(f"{key[2]} not_in_scope {region}")
                continue
            slot = per.setdefault(key, {"ports": set(), "region": region, "kind": kind})
            if port:
                slot["ports"].add(port)
        for (target, ttype, ref), v in sorted(
            per.items(), key=lambda x: (x[0][0], x[0][2])
        ):
            edges.append(
                EdgeDraft(
                    "accelerator_endpoint",
                    nid,
                    target,
                    ttype,
                    True,
                    {
                        "endpoint_type": v["kind"],
                        "endpoint_region": v["region"],
                        "listener_ports": sorted(v["ports"]),
                    },
                    by_id=ttype is None,
                    ref=ref,
                    why_attr=WHY_ATTR,
                )
            )
        if why:
            attributes[WHY_ATTR] = sorted(why)
    node = Node(
        id=nid,
        type="global_accelerator",
        account_id=owner,
        region=None,
        native_id=native,
        arn=arn,
        name=item.get("Name") or native,
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
