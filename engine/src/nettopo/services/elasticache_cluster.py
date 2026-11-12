"""Collect ElastiCache clusters and subnet group configuration.

Resolve placement only with one candidate subnet per active AZ. Store
multiple resolved subnets in subnet_ids; use the VPC parent if
ambiguous.
Record subnet group query failures in partial_reasons.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .base import (
    Ctx,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    pages,
)
from .base import sorted_strs as _sorted

SPEC = Spec(
    "elasticache",
    "describe_cache_clusters",
    "CacheClusters",
    "elasticache:DescribeCacheClusters",
    params=(("ShowCacheNodeInfo", True),),
)
_GROUPS_API = "elasticache:DescribeCacheSubnetGroups"
_RG_API = "elasticache:DescribeReplicationGroups"
_GROUP = "_SubnetGroup"
_RG = "_ReplicationGroup"


def _zones(item: dict[str, Any]) -> list[str]:
    nodes = item.get("CacheNodes") or []
    found = {
        str(n["CustomerAvailabilityZone"])
        for n in nodes
        if n.get("CustomerAvailabilityZone")
    }
    if found:
        return sorted(found)
    pref = item.get("PreferredAvailabilityZone")
    return [str(pref)] if pref and pref != "Multiple" else []


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query subnet and replication groups to enrich cluster placement.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    need_groups = any(f.payload.get("CacheSubnetGroupName") for f in items)
    need_rgs = any(f.payload.get("ReplicationGroupId") for f in items)
    groups: dict[str, dict[str, Any]] | Reason = {}
    rgs: dict[str, dict[str, Any]] | Reason = {}
    if need_groups:
        try:
            groups = {
                str(g["CacheSubnetGroupName"]): g
                for g in pages(
                    client, "describe_cache_subnet_groups", "CacheSubnetGroups"
                )
                if g.get("CacheSubnetGroupName")
            }
        except (ClientError, BotoCoreError) as exc:
            groups = classify(exc, _GROUPS_API)[1]
    if need_rgs:
        try:
            rgs = {
                str(g["ReplicationGroupId"]): g
                for g in pages(
                    client, "describe_replication_groups", "ReplicationGroups"
                )
                if g.get("ReplicationGroupId")
            }
        except (ClientError, BotoCoreError) as exc:
            rgs = classify(exc, _RG_API)[1]
    out: list[Fetched] = []
    for f in items:
        payload = dict(f.payload)
        reasons = f.reasons
        name = payload.get("CacheSubnetGroupName")
        if name:
            if isinstance(groups, Reason):
                reasons = reasons + (groups,)
            elif str(name) in groups:
                payload[_GROUP] = groups[str(name)]
        rg = payload.get("ReplicationGroupId")
        if rg:
            if isinstance(rgs, Reason):
                reasons = reasons + (rgs,)
            elif str(rg) in rgs:
                payload[_RG] = rgs[str(rg)]
        out.append(Fetched(payload, reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["CacheClusterId"])
    zones = _zones(item)
    group = item.get(_GROUP)
    candidates = (group or {}).get("Subnets") or []
    by_zone: dict[str, set[str]] = {}
    for s in candidates:
        sid = s.get("SubnetIdentifier")
        az = (s.get("SubnetAvailabilityZone") or {}).get("Name")
        if sid and az:
            by_zone.setdefault(str(az), set()).add(str(sid))
    picked = [by_zone.get(z, set()) for z in zones]
    resolved = bool(zones) and all(len(p) == 1 for p in picked)
    subnets = sorted({next(iter(p)) for p in picked}) if resolved else []

    parents: list[str] = []
    parent_types: list[str] = []
    attributes: dict[str, Any] = {
        "engine": item.get("Engine"),
        "engine_version": item.get("EngineVersion"),
        "cache_node_type": item.get("CacheNodeType"),
        "num_cache_nodes": item.get("NumCacheNodes"),
        "availability_zones": zones,
        "subnet_group_name": item.get("CacheSubnetGroupName"),
        "security_group_ids": _sorted(
            g.get("SecurityGroupId") for g in item.get("SecurityGroups") or []
        ),
        "transit_encryption_enabled": item.get("TransitEncryptionEnabled"),
        "at_rest_encryption_enabled": item.get("AtRestEncryptionEnabled"),
    }
    port = (item.get("ConfigurationEndpoint") or {}).get("Port") or next(
        (
            (n.get("Endpoint") or {}).get("Port")
            for n in item.get("CacheNodes") or []
            if (n.get("Endpoint") or {}).get("Port")
        ),
        None,
    )
    if port is not None:
        attributes["port"] = port
    if group is not None:
        attributes["subnet_group_subnet_ids"] = _sorted(
            s.get("SubnetIdentifier") for s in candidates
        )
        if len(subnets) == 1:
            parents.append(node_id("subnet", owner, ctx.region, subnets[0]))
            parent_types.append("subnet")
        elif len(subnets) > 1:
            attributes["subnet_ids"] = subnets
        if group.get("VpcId"):
            parents.append(node_id("vpc", owner, ctx.region, str(group["VpcId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    if item.get("ReplicationGroupId"):
        attributes["replication_group_id"] = item["ReplicationGroupId"]
    rg = item.get(_RG)
    if rg is not None:
        attributes["replication_group_member_ids"] = _sorted(
            rg.get("MemberClusters") or []
        )
        attributes["replication_group_cluster_mode"] = rg.get("ClusterEnabled")
        attributes["replication_group_multi_az"] = rg.get("MultiAZ")
        attributes["replication_group_automatic_failover"] = rg.get("AutomaticFailover")

    node = Node(
        id=node_id("elasticache_cluster", owner, ctx.region, native),
        type="elasticache_cluster",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("ARN"),
        name=native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    value = item.get("CacheClusterStatus")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
