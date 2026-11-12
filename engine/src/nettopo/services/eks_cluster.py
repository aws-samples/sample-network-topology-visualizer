"""Collect EKS network configuration through listing and description
APIs.

Keep a partial node with the known cluster name if description fails.
Exclude cluster state and node groups. Use subnet_ids for multi-subnet
placement.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("eks", "list_clusters", "clusters", "eks:ListClusters")

_DESCRIBE_API = "eks:DescribeCluster"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Describe each cluster to enrich its network configuration.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        name = item.payload
        try:
            cluster = client.describe_cluster(name=name)["cluster"]
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(
                    payload={"name": name},
                    reasons=item.reasons + (classify(exc, _DESCRIBE_API)[1],),
                )
            )
            continue
        out.append(Fetched(payload=cluster, reasons=item.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["name"]
    nid = node_id("eks_cluster", owner, ctx.region, native)

    vpc_config = item.get("resourcesVpcConfig")
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if vpc_config is not None:
        attributes = {
            "version": item.get("version"),
            "endpoint_public_access": vpc_config.get("endpointPublicAccess"),
            "endpoint_private_access": vpc_config.get("endpointPrivateAccess"),
            "public_access_cidrs": _sorted(vpc_config.get("publicAccessCidrs") or []),
            "subnet_ids": _sorted(vpc_config.get("subnetIds") or []),
            "security_group_ids": _sorted(vpc_config.get("securityGroupIds") or []),
            "cluster_security_group_id": vpc_config.get("clusterSecurityGroupId"),
        }
        vpc_id = vpc_config.get("vpcId")
        if vpc_id:
            parents.append(node_id("vpc", owner, ctx.region, str(vpc_id)))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=nid,
        type="eks_cluster",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("arn"),
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
    value = item.get("status")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
