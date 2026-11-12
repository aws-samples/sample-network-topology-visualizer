"""Collect SageMaker Studio domain network configuration.

Query VPC placement, subnets, and network access mode through
DescribeDomain.
Keep a partial node with its known name if description fails.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("sagemaker", "list_domains", "Domains", "sagemaker:ListDomains")

_DESCRIBE_API = "sagemaker:DescribeDomain"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Describe domains to enrich VPC, subnet, and access mode details.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        domain_id = item.payload.get("DomainId")
        try:
            detail = client.describe_domain(DomainId=domain_id)
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(
                    payload={**item.payload, "_described": False},
                    reasons=item.reasons + (classify(exc, _DESCRIBE_API)[1],),
                )
            )
            continue
        out.append(
            Fetched(
                payload={**item.payload, **detail, "_described": True},
                reasons=item.reasons,
            )
        )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["DomainId"]
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("_described"):
        settings = item.get("DefaultUserSettings") or {}
        attributes = {
            "app_network_access_type": item.get("AppNetworkAccessType"),
            "subnet_ids": _sorted(item.get("SubnetIds") or []),
            "security_group_ids": _sorted(settings.get("SecurityGroups") or []),
        }
        if item.get("VpcId"):
            parents.append(node_id("vpc", owner, ctx.region, str(item["VpcId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("sagemaker_domain", owner, ctx.region, native),
        type="sagemaker_domain",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DomainArn"),
        name=item.get("DomainName"),
        attributes=attributes,
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
