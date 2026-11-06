"""서브넷 수집 — 「이음매 2」.

부모 후보는 (VPC, 리전)이다. VPC 수집이 거부됐으면 첫 후보가 없으므로 리전으로 내려간다 —
체인은 `collect.py` 의 3패스가 고른다(ADR 0002 §1).
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, tags_of

SPEC = Spec("ec2", "describe_subnets", "Subnets", "ec2:DescribeSubnets")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    node = Node(
        id=node_id("subnet", owner, ctx.region, item["SubnetId"]),
        type="subnet",
        account_id=owner,
        region=ctx.region,
        native_id=item["SubnetId"],
        arn=item.get("SubnetArn"),
        name=tags.get("Name"),
        # 서브넷은 CIDR·AZ 없이는 배치할 수 없다(스키마 allOf).
        # AvailableIpAddressCount 같은 시시각각 변하는 값은 담지 않는다 — 불변식 ②가 깨진다.
        attributes={
            "cidr_block": item.get("CidrBlock"),
            "availability_zone": item.get("AvailabilityZone"),
            "availability_zone_id": item.get("AvailabilityZoneId"),
            "state": item.get("State"),
            "map_public_ip_on_launch": bool(item.get("MapPublicIpOnLaunch", False)),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(
            node_id("vpc", owner, ctx.region, str(item.get("VpcId"))),
            ctx.region_node(owner),
        ),
        parent_types=("vpc", "region"),
    )


def _state_attributes(item: dict[str, Any]) -> dict[str, Any]:
    """상태 단에서만(`collect(with_state=True)`) — 남은 IP 수(ADR 0011 D3 · 사용률).

    🔴 구조 스냅샷에는 넣지 않는다(위 `build` 의 주석 · 불변식 ②). 상태 단의 스냅샷은 관측 문서의 재료이고
    관측은 시각마다 다른 것이 정상이다.
    """
    count = item.get("AvailableIpAddressCount")
    return {"available_ip_address_count": count} if isinstance(count, int) else {}


SERVICE = Service(spec=SPEC, build=build, state_attributes=_state_attributes)
