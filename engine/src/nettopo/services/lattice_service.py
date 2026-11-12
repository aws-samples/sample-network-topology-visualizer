"""Collect VPC Lattice service configuration and registered targets.

Resolve instance, ALB, and Lambda targets from collected resources.
Record IP targets as unresolved and query failures in partial_reasons.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
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
from .lattice_service_network import arn_account

SPEC = Spec("vpc-lattice", "list_services", "items", "vpc-lattice:ListServices")
TG_API = "vpc-lattice:ListTargetGroups"
TARGETS_API = "vpc-lattice:ListTargets"
_TGS = "_TargetGroups"
WHY_ATTR = "unresolved_lattice_targets"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query service target groups and registrations to enrich target
    links.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    if not items:
        return items
    try:
        groups = pages(client, "list_target_groups", "items")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, TG_API)[1]
        return [Fetched(it.payload, it.reasons + (reason,)) for it in items]
    arns = {str(it.payload.get("arn") or "") for it in items}
    targets: dict[str, list[dict[str, Any]] | Reason] = {}
    for g in sorted(groups, key=lambda x: str(x.get("id"))):
        if not arns & {str(a) for a in g.get("serviceArns") or []}:
            continue
        try:
            targets[str(g["id"])] = pages(
                client, "list_targets", "items", targetGroupIdentifier=str(g["id"])
            )
        except (ClientError, BotoCoreError) as exc:
            targets[str(g["id"])] = classify(exc, TARGETS_API)[1]
    out: list[Fetched] = []
    for it in items:
        arn = str(it.payload.get("arn") or "")
        mine = [
            g for g in groups if arn in {str(a) for a in g.get("serviceArns") or []}
        ]
        reasons = list(it.reasons)
        rows: list[dict[str, Any]] = []
        for g in sorted(mine, key=lambda x: (str(x.get("name")), str(x.get("id")))):
            got = targets.get(str(g["id"]))
            if isinstance(got, Reason):
                if got not in reasons:
                    reasons.append(got)
                rows.append({**g, "_Targets": None})
            else:
                rows.append({**g, "_Targets": got or []})
        out.append(Fetched({**it.payload, _TGS: rows}, tuple(reasons)))
    return out


def _target(kind: str, tid: str, ctx: Ctx) -> tuple[str, str | None, bool, str] | str:
    if kind == "instance" and tid.startswith("i-"):
        return tid, "ec2_instance", False, tid
    if kind == "alb":
        ref = lb_ref(tid)
        if ref is None:
            return "unknown_type"
        region, account, name = ref
        return node_id("load_balancer", account, region, name), None, True, name
    if kind == "lambda":
        parts = tid.split(":")
        if len(parts) >= 7 and parts[2] == "lambda" and parts[5] == "function":
            return (
                node_id("lambda_function", parts[4], parts[3], parts[6]),
                None,
                True,
                parts[6],
            )
        return "unknown_type"
    if kind == "ip":
        return "ip_target"
    return "unknown_type"


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    raw_owner = arn_account(str(item.get("arn") or ""))
    if raw_owner and raw_owner != ctx.account_id and raw_owner in ctx.scope_accounts:
        return None
    owner, extra = ctx.attribute({"OwnerId": raw_owner})
    native = str(item["id"])
    nid = node_id("lattice_service", owner, ctx.region, native)
    dns = (item.get("dnsEntry") or {}).get("domainName")
    attributes: dict[str, Any] = {
        **({"dns_name": str(dns)} if dns else {}),
        **(
            {"custom_domain_name": str(item["customDomainName"])}
            if item.get("customDomainName")
            else {}
        ),
        **extra,
    }
    edges: list[EdgeDraft] = []
    rows = item.get(_TGS)
    if rows is not None:
        attributes["target_groups"] = sorted(
            f"{g.get('name') or g.get('id')} {str(g.get('type') or '?').lower()} "
            f"{g.get('protocol') or '-'} {g.get('port') if g.get('port') is not None else '-'}"
            for g in rows
        )
        empty = sorted(
            str(g.get("name") or g.get("id")) for g in rows if g["_Targets"] == []
        )
        if empty:
            attributes["empty_target_groups"] = empty
        per: dict[tuple[str, str | None, bool, str], dict[str, Any]] = {}
        why: set[str] = set()
        for g in rows:
            if g["_Targets"] is None:
                continue
            kind = str(g.get("type") or "").lower()
            for t in g["_Targets"]:
                tid = str(t.get("id") or "")
                got = _target(kind, tid, ctx)
                if isinstance(got, str):
                    why.add(f"{tid} {got}")
                    continue
                slot = per.setdefault(got, {"tgs": set(), "ports": set(), "kind": kind})
                slot["tgs"].add(str(g.get("name") or g.get("id")))
                port = t.get("port") if t.get("port") is not None else g.get("port")
                if port is not None:
                    slot["ports"].add(int(port))
        for (target, ttype, by_id, ref), v in sorted(
            per.items(), key=lambda x: (x[0][0], x[0][3])
        ):
            edges.append(
                EdgeDraft(
                    "lattice_target",
                    nid,
                    target,
                    ttype,
                    True,
                    {
                        "target_groups": sorted(v["tgs"]),
                        "ports": sorted(v["ports"]),
                        "target_type": v["kind"],
                    },
                    by_id=by_id,
                    ref=ref,
                    why_attr=WHY_ATTR,
                )
            )
        if why:
            attributes[WHY_ATTR] = sorted(why)
    node = Node(
        id=nid,
        type="lattice_service",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("arn"),
        name=item.get("name") or native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
