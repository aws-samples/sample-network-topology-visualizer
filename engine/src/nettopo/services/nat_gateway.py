"""NAT 게이트웨이 수집 — 「이음매 2」.

부모 후보 체인이 가장 긴 타입이다: (서브넷, VPC, 리전). 응답에 `SubnetId` 가 없는 상태
(삭제 중인 NAT GW)도 있으므로 후보를 **있는 것만** 쌓는다 — 없는 id 를 체인에 넣으면
3패스가 그 자리에서 멈춰 리전으로도 못 내려간다(ADR 0002 §1).
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec("ec2", "describe_nat_gateways", "NatGateways", "ec2:DescribeNatGateways")


def _address_rows(addresses: list[dict[str, Any]]) -> list[str]:
    rows = []
    for a in addresses:
        primary = bool(a.get("IsPrimary"))
        line = "\t".join(
            (
                str(a.get("PublicIp") or ""),
                str(a.get("PrivateIp") or ""),
                str(a.get("NetworkInterfaceId") or ""),
                str(a.get("AllocationId") or ""),
                "true" if primary else "false",
            )
        )
        rows.append((0 if primary else 1, str(a.get("PrivateIp") or ""), line))
    return [r[2] for r in sorted(rows)]


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("nat_gateway", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["NatGatewayId"]
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
        id=node_id("nat_gateway", owner, ctx.region, native),
        type="nat_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": item.get("State"),
            "connectivity_type": item.get("ConnectivityType"),
            "public_ips": _sorted(
                a.get("PublicIp") for a in item.get("NatGatewayAddresses") or []
            ),
            # 콘솔 「세부 정보」의 주소 칸(사용자 결정 2026-10-24) — 기본 주소 한 줄 =
            # 「퍼블릭 IP \t 프라이빗 IP \t 네트워크 인터페이스 id \t 할당 id \t 기본 여부(true/false)」 · 기본 먼저 → 프라이빗 IP 순
            "addresses": _address_rows(item.get("NatGatewayAddresses") or []),
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
