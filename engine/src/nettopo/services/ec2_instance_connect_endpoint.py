"""ec2_instance_connect_endpoint(EC2 Instance Connect 엔드포인트) 수집 — 「이음매 2」 · W2.

EIC 엔드포인트는 서브넷 하나에 ENI 하나로 산다 — 위치는 부모 체인(서브넷→VPC→리전)으로만
표현한다(EC2 인스턴스와 같다). 이 엔드포인트로 들어오는 SSH/RDP 는 사설 경로이므로 인터넷 쪽으로
선을 긋지 않는다.

`state`(create-complete 등)는 서브넷·NAT 의 `state` 와 같은 취급으로 남긴다 — 서서히 바뀌는 수명
상태이고, 지우는 중인 엔드포인트를 그대로 그리면 「지금 들어갈 수 있다」고 오판한다.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_instance_connect_endpoints",
    "InstanceConnectEndpoints",
    "ec2:DescribeInstanceConnectEndpoints",
)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("ec2_instance_connect_endpoint", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["InstanceConnectEndpointId"]
    # 있는 것만 쌓는다(ec2_instance 와 같은 이유) — 없는 id 를 체인에 넣으면 리전으로도 못 내려간다.
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("SubnetId"):
        parents.append(node_id("subnet", owner, ctx.region, str(item["SubnetId"])))
        parent_types.append("subnet")
    if item.get("VpcId"):
        parents.append(node_id("vpc", owner, ctx.region, str(item["VpcId"])))
        parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("ec2_instance_connect_endpoint", owner, ctx.region, native),
        type="ec2_instance_connect_endpoint",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("InstanceConnectEndpointArn"),
        name=tags.get("Name"),
        attributes={
            "state": item.get("State"),
            "availability_zone": item.get("AvailabilityZone"),
            "preserve_client_ip": item.get("PreserveClientIp"),
            "security_group_ids": _sorted(item.get("SecurityGroupIds") or []),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


SERVICE = Service(spec=SPEC, build=build)
