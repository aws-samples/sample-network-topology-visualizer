"""Collect Lambda configuration and VPC subnet membership.

Use the same Node.id as Lambda nodes derived from load balancer targets.
Link shared Hyperplane ENIs to functions only when the owner is unique.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft
from .base import sorted_strs as _sorted

SPEC = Spec("lambda", "list_functions", "Functions", "lambda:ListFunctions")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    name = str(item["FunctionName"])
    vpc_cfg = item.get("VpcConfig") or {}
    vpc_id = str(vpc_cfg.get("VpcId") or "")
    parents: list[str] = []
    parent_types: list[str] = []
    if vpc_id:
        parents.append(node_id("vpc", owner, ctx.region, vpc_id))
        parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("lambda_function", owner, ctx.region, name),
        type="lambda_function",
        account_id=owner,
        region=ctx.region,
        native_id=name,
        arn=item.get("FunctionArn"),
        name=name,
        attributes={
            "function_name": name,
            "runtime": item.get("Runtime"),
            "subnet_ids": _sorted(vpc_cfg.get("SubnetIds") or []),
            "security_group_ids": _sorted(vpc_cfg.get("SecurityGroupIds") or []),
        },
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


SERVICE = Service(spec=SPEC, build=build)
