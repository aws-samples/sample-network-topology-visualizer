"""VPN 게이트웨이(VGW · 가상 프라이빗 게이트웨이) 수집 — 「이음매 2」 · 2차 Site-to-Site VPN(2.6.0).

VGW 는 「Amazon 측 VPN 집선기」이고 VPC 하나에만 붙는다(`docs/resource-catalog.md` §4.1). IGW 와
같은 이유로 🔴 **부모는 리전**이다 — VPC 에서 떼어도 살아 있고, DX private VIF·DXGW 의 종단으로만
쓰이는 VGW 도 있다(§4.5-2 · 실측 us-east-1: VGW 3 · VPN 연결 0). VPC 를 부모로 두면 떨어진
VGW 가 그림에서 사라진다. VPC 와의 관계는 `attachment` 엣지로 표현한다(ADR 0002 §1).

VPC 라우트 테이블의 `GatewayId=vgw-…` 라우트(정적 · 전파 둘 다)는 `services/route_table.py` 가
이미 `route` 엣지로 내고 있다 — 이 노드가 생기면서 3패스가 그 엣지를 이 노드에 잇는다(2.6.0 전에는
`unresolved_refs` 로 남았다). 여기서 다시 만들지 않는다.

`DescribeVpnGateways` 는 페이지네이터가 없고 토큰도 주지 않는다(§4.5-11 · 리전당 기본 5) — `pages`
가 한 장으로 끝낸다.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_vpn_gateways",
    "VpnGateways",
    "ec2:DescribeVpnGateways",
)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpn_gateway", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["VpnGatewayId"]
    nid = node_id("vpn_gateway", owner, ctx.region, native)
    attachments = item.get("VpcAttachments") or []
    edges = [
        EdgeDraft(
            "attachment",
            nid,
            str(a["VpcId"]),
            "vpc",
            False,
            {"state": str(a.get("State") or "")},
            live=alive(a.get("State")),
        )
        for a in attachments
        if a.get("VpcId")
    ]
    asn = item.get("AmazonSideAsn")
    node = Node(
        id=nid,
        type="vpn_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": item.get("State"),
            "type": item.get("Type"),
            "amazon_side_asn": asn if asn is None else int(asn),
            # 붙어 있는(살아 있는) VPC 만 — 떨어진 VPC 는 관계가 아니다(ADR 0002 §3).
            "attached_vpc_ids": _sorted(
                a.get("VpcId") for a in attachments if alive(a.get("State"))
            ),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build)
