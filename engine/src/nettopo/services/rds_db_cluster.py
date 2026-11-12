"""Collect RDS DB cluster configuration and members.

Resolve the VPC from the subnet group without inferring a specific
subnet
from the cluster endpoint. Exclude the failover-dependent member writer
role from topology attributes.
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
    tags_of,
)
from .base import sorted_strs as _sorted

SPEC = Spec("rds", "describe_db_clusters", "DBClusters", "rds:DescribeDBClusters")
_GROUPS_API = "rds:DescribeDBSubnetGroups"
_GROUP = "_SubnetGroup"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query DB subnet groups to enrich cluster VPC and subnet details.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    if not any(f.payload.get("DBSubnetGroup") for f in items):
        return items
    try:
        groups: dict[str, dict[str, Any]] | Reason = {
            str(g["DBSubnetGroupName"]): g
            for g in pages(client, "describe_db_subnet_groups", "DBSubnetGroups")
            if g.get("DBSubnetGroupName")
        }
    except (ClientError, BotoCoreError) as exc:
        groups = classify(exc, _GROUPS_API)[1]
    out: list[Fetched] = []
    for f in items:
        name = f.payload.get("DBSubnetGroup")
        if not name:
            out.append(f)
        elif isinstance(groups, Reason):
            out.append(Fetched(f.payload, f.reasons + (groups,)))
        elif str(name) in groups:
            out.append(Fetched({**f.payload, _GROUP: groups[str(name)]}, f.reasons))
        else:
            out.append(
                f
            )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["DBClusterIdentifier"])
    group = item.get(_GROUP)
    parents: list[str] = []
    parent_types: list[str] = []
    attributes: dict[str, Any] = {
        "engine": item.get("Engine"),
        "engine_version": item.get("EngineVersion"),
        "engine_mode": item.get("EngineMode"),
        "endpoint": item.get("Endpoint"),
        "reader_endpoint": item.get("ReaderEndpoint"),
        "port": item.get("Port"),
        "multi_az": item.get("MultiAZ"),
        "availability_zones": _sorted(item.get("AvailabilityZones") or []),
        "member_instance_ids": _sorted(
            m.get("DBInstanceIdentifier") for m in item.get("DBClusterMembers") or []
        ),
        "subnet_group_name": item.get("DBSubnetGroup"),
        "security_group_ids": _sorted(
            g.get("VpcSecurityGroupId") for g in item.get("VpcSecurityGroups") or []
        ),
        "storage_encrypted": item.get("StorageEncrypted"),
    }
    sv2 = item.get("ServerlessV2ScalingConfiguration")
    if sv2:
        attributes["serverless_v2_min_capacity"] = sv2.get("MinCapacity")
        attributes["serverless_v2_max_capacity"] = sv2.get("MaxCapacity")
    if "PubliclyAccessible" in item:
        attributes["publicly_accessible"] = item["PubliclyAccessible"]
    if group is not None:
        attributes["subnet_group_subnet_ids"] = _sorted(
            s.get("SubnetIdentifier") for s in group.get("Subnets") or []
        )
        if group.get("VpcId"):
            parents.append(node_id("vpc", owner, ctx.region, str(group["VpcId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=node_id("rds_db_cluster", owner, ctx.region, native),
        type="rds_db_cluster",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DBClusterArn"),
        name=native,
        attributes=attributes,
        tags=tags_of(item, "TagList"),
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    value = item.get("Status")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
