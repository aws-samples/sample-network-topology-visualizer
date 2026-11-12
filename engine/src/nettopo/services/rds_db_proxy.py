"""Collect RDS proxy configuration and database target links.

Group cluster targets into cluster links and record member IDs in
target_instance_ids. Exclude authentication settings, secret
locations, and target health from topology.
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
    tags_of,
)
from .base import sorted_strs as _sorted

SPEC = Spec("rds", "describe_db_proxies", "DBProxies", "rds:DescribeDBProxies")
TARGETS_API = "rds:DescribeDBProxyTargets"
_TARGETS = "_Targets"
WHY_ATTR = "unresolved_proxy_targets"
_CLUSTER_TYPES = ("TRACKED_CLUSTER", "RDS_SERVERLESS_ENDPOINT")


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query proxy DB targets to enrich instance and cluster references.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for it in items:
        name = str(it.payload.get("DBProxyName") or "")
        try:
            got = pages(
                client, "describe_db_proxy_targets", "Targets", DBProxyName=name
            )
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(it.payload, it.reasons + (classify(exc, TARGETS_API)[1],))
            )
            continue
        targets = sorted(
            {
                (str(t.get("Type") or ""), str(t["RdsResourceId"]), t.get("Port"))
                for t in got
                if t.get("RdsResourceId")
            },
            key=lambda x: (x[0], x[1], x[2] or 0),
        )
        out.append(Fetched({**it.payload, _TARGETS: targets}, it.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["DBProxyName"])
    nid = node_id("rds_db_proxy", owner, ctx.region, native)
    attributes: dict[str, Any] = {
        "engine_family": item.get("EngineFamily"),
        "endpoint": item.get("Endpoint"),
        "require_tls": item.get("RequireTLS"),
        "idle_client_timeout": item.get("IdleClientTimeout"),
        "subnet_ids": _sorted(item.get("VpcSubnetIds") or []),
        "security_group_ids": _sorted(item.get("VpcSecurityGroupIds") or []),
    }
    edges: list[EdgeDraft] = []
    targets = item.get(_TARGETS)
    if targets is not None:
        clusters = sorted({rid for kind, rid, _ in targets if kind in _CLUSTER_TYPES})
        instances = sorted({rid for kind, rid, _ in targets if kind == "RDS_INSTANCE"})
        ports = _sorted(str(p) for _, _, p in targets if p)
        attributes["target_count"] = len({rid for _, rid, _ in targets})
        if clusters and instances:
            attributes["target_instance_ids"] = instances
        picked = (
            [("rds_db_cluster", c) for c in clusters]
            if clusters
            else [("rds_db_instance", i) for i in instances]
        )
        for kind, rid in picked:
            edges.append(
                EdgeDraft(
                    "proxy_target",
                    nid,
                    node_id(kind, owner, ctx.region, rid),
                    None,
                    True,
                    {
                        "target_type": "cluster"
                        if kind == "rds_db_cluster"
                        else "instance",
                        "ports": [int(p) for p in ports],
                    },
                    by_id=True,
                    ref=rid,
                    why_attr=WHY_ATTR,
                )
            )
    tags = {
        str(t["Key"]): str(t["Value"])
        for t in item.get("TagList") or []
        if "Key" in t and "Value" in t
    } or tags_of(item)
    node = Node(
        id=nid,
        type="rds_db_proxy",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DBProxyArn"),
        name=native,
        attributes=attributes,
        tags=tags,
    )
    parents: list[str] = []
    types: list[str] = []
    if item.get("VpcId"):
        parents.append(node_id("vpc", owner, ctx.region, str(item["VpcId"])))
        types.append("vpc")
    parents.append(ctx.region_node(owner))
    types.append("region")
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(types),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
