"""Collect inference endpoint network settings from configurations and
models.

Merge subnet and security group settings only after reading all related
models. If an intermediate query fails, omit placement attributes rather
than establishing placement from incomplete data.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("sagemaker", "list_endpoints", "Endpoints", "sagemaker:ListEndpoints")


def _network(client: Any, name: str) -> tuple[dict[str, Any], Reason | None]:
    try:
        endpoint = client.describe_endpoint(EndpointName=name)
    except (ClientError, BotoCoreError) as exc:
        return {}, classify(exc, "sagemaker:DescribeEndpoint")[1]
    config_name = endpoint.get("EndpointConfigName")
    got: dict[str, Any] = {"_config_name": config_name}
    try:
        config = client.describe_endpoint_config(EndpointConfigName=config_name)
    except (ClientError, BotoCoreError) as exc:
        return got, classify(exc, "sagemaker:DescribeEndpointConfig")[1]
    variants = list(config.get("ProductionVariants") or []) + list(
        config.get("ShadowProductionVariants") or []
    )
    models = _sorted(v.get("ModelName") for v in variants)
    got["_model_names"] = models
    subnets: set[str] = set()
    groups: set[str] = set()
    for model in models:
        try:
            detail = client.describe_model(ModelName=model)
        except (ClientError, BotoCoreError) as exc:
            return got, classify(exc, "sagemaker:DescribeModel")[1]
        vpc = detail.get("VpcConfig") or {}
        subnets.update(str(s) for s in vpc.get("Subnets") or [])
        groups.update(str(g) for g in vpc.get("SecurityGroupIds") or [])
    got["_subnet_ids"] = sorted(subnets)
    got["_security_group_ids"] = sorted(groups)
    return got, None


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query endpoints, configurations, and models to resolve network
    placement.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        got, reason = _network(client, str(item.payload.get("EndpointName")))
        reasons = item.reasons + ((reason,) if reason else ())
        out.append(Fetched(payload={**item.payload, **got}, reasons=reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["EndpointName"]
    attributes: dict[str, Any] = {}
    if item.get("_config_name"):
        attributes["endpoint_config_name"] = item["_config_name"]
    if "_model_names" in item:
        attributes["model_names"] = item["_model_names"]
    if "_subnet_ids" in item:
        attributes["subnet_ids"] = item["_subnet_ids"]
        attributes["security_group_ids"] = item["_security_group_ids"]
    node = Node(
        id=node_id("sagemaker_endpoint", owner, ctx.region, native),
        type="sagemaker_endpoint",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("EndpointArn"),
        name=native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


def state(item: dict[str, Any]) -> str | None:
    value = item.get("EndpointStatus")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
