"""Collect notebook subnet, security group, and internet access
settings.

Use a configured subnet as parent, or the region otherwise. If
description
fails, keep a partial node without treating the VPC as absent.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec(
    "sagemaker",
    "list_notebook_instances",
    "NotebookInstances",
    "sagemaker:ListNotebookInstances",
)

_DESCRIBE_API = "sagemaker:DescribeNotebookInstance"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Describe notebooks to enrich subnet, security, and internet
    access settings.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        name = item.payload.get("NotebookInstanceName")
        try:
            detail = client.describe_notebook_instance(NotebookInstanceName=name)
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
    native = item["NotebookInstanceName"]
    attributes: dict[str, Any] = {"instance_type": item.get("InstanceType")}
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("_described"):
        attributes.update(
            {
                "direct_internet_access": item.get("DirectInternetAccess"),
                "security_group_ids": _sorted(item.get("SecurityGroups") or []),
            }
        )
        if item.get("SubnetId"):
            parents.append(node_id("subnet", owner, ctx.region, str(item["SubnetId"])))
            parent_types.append("subnet")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("sagemaker_notebook_instance", owner, ctx.region, native),
        type="sagemaker_notebook_instance",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("NotebookInstanceArn"),
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
    value = item.get("NotebookInstanceStatus")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
