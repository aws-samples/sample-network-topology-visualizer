"""Collect RDS instances and resolve subnet placement.

Choose a unique subnet matching the AZ; otherwise, narrow candidates by
ENI ownership. Distinguish candidate subnet_group_subnet_ids from
resolved
subnet_ids. Multiple candidates do not establish multi-subnet placement.
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

SPEC = Spec("rds", "describe_db_instances", "DBInstances", "rds:DescribeDBInstances")
ENI_API = "ec2:DescribeNetworkInterfaces"
_ENI_SUBNET = "_EniSubnetId"


def _in_az(item: dict[str, Any]) -> list[str]:
    az = item.get("AvailabilityZone")
    return sorted(
        {
            str(s["SubnetIdentifier"])
            for s in (item.get("DBSubnetGroup") or {}).get("Subnets") or []
            if s.get("SubnetIdentifier")
            and (s.get("SubnetAvailabilityZone") or {}).get("Name") == az
        }
    )


def join_enis(
    client: Any, items: list[Fetched], failed: Reason | None
) -> list[Fetched]:
    need = [
        i
        for i, f in enumerate(items)
        if len(_in_az(f.payload)) > 1
        and (f.payload.get("DBSubnetGroup") or {}).get("VpcId")
    ]
    if not need:
        return items
    out = list(items)
    if client is None:
        reason = failed or Reason("api_error", ENI_API, "EC2 클라이언트 없음")
        for i in need:
            out[i] = Fetched(out[i].payload, out[i].reasons + (reason,))
        return out
    crowd: dict[tuple[str, str], int] = {}
    for f in items:
        vpc = (f.payload.get("DBSubnetGroup") or {}).get("VpcId")
        for az in {
            f.payload.get("AvailabilityZone"),
            f.payload.get("SecondaryAvailabilityZone"),
        }:
            if vpc and az:
                crowd[(vpc, az)] = crowd.get((vpc, az), 0) + 1
    asked: dict[tuple[str, str], list[dict[str, Any]] | Reason] = {}
    for i in need:
        payload = out[i].payload
        vpc = str(payload["DBSubnetGroup"]["VpcId"])
        az = str(payload["AvailabilityZone"])
        if (vpc, az) not in asked:
            try:
                asked[(vpc, az)] = pages(
                    client,
                    "describe_network_interfaces",
                    "NetworkInterfaces",
                    Filters=[
                        {"Name": "requester-id", "Values": ["amazon-rds"]},
                        {"Name": "vpc-id", "Values": [vpc]},
                        {"Name": "availability-zone", "Values": [az]},
                    ],
                )
            except (ClientError, BotoCoreError) as exc:
                asked[(vpc, az)] = classify(exc, ENI_API)[1]
        got = asked[(vpc, az)]
        if isinstance(got, Reason):
            out[i] = Fetched(payload, out[i].reasons + (got,))
            continue
        if crowd.get((vpc, az), 0) != 1:
            continue
        candidates = set(_in_az(payload))
        hit = {str(e["SubnetId"]) for e in got if str(e.get("SubnetId")) in candidates}
        if len(hit) == 1:
            out[i] = Fetched({**payload, _ENI_SUBNET: hit.pop()}, out[i].reasons)
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["DBInstanceIdentifier"]
    group = item.get("DBSubnetGroup") or {}
    candidates = group.get("Subnets") or []
    az = item.get("AvailabilityZone")
    in_az = _in_az(item)
    if az and len(in_az) > 1 and item.get(_ENI_SUBNET) in in_az:
        in_az = [str(item[_ENI_SUBNET])]
    parents: list[str] = []
    parent_types: list[str] = []
    if az and len(in_az) == 1:
        parents.append(node_id("subnet", owner, ctx.region, in_az[0]))
        parent_types.append("subnet")
    if group.get("VpcId"):
        parents.append(node_id("vpc", owner, ctx.region, str(group["VpcId"])))
        parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    attributes: dict[str, Any] = {
        "engine": item.get("Engine"),
        "instance_class": item.get("DBInstanceClass"),
        "multi_az": item.get("MultiAZ"),
        "availability_zone": az,
        "publicly_accessible": item.get("PubliclyAccessible"),
        "subnet_group_name": group.get("DBSubnetGroupName"),
        "subnet_group_subnet_ids": _sorted(
            s.get("SubnetIdentifier") for s in candidates
        ),
        "security_group_ids": _sorted(
            g.get("VpcSecurityGroupId") for g in item.get("VpcSecurityGroups") or []
        ),
    }
    if item.get("SecondaryAvailabilityZone"):
        attributes["secondary_availability_zone"] = item["SecondaryAvailabilityZone"]
    if item.get("DBClusterIdentifier"):
        attributes["db_cluster_identifier"] = item["DBClusterIdentifier"]

    tags = tags_of(item, "TagList")
    node = Node(
        id=node_id("rds_db_instance", owner, ctx.region, native),
        type="rds_db_instance",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DBInstanceArn"),
        name=native,
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    value = item.get("DBInstanceStatus")
    return str(value) if value else None


SERVICE = Service(
    spec=SPEC, build=build, state=state, peer_enrich=(("ec2", join_enis),)
)
