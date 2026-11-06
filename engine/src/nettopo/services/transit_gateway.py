"""Transit Gateway 수집 — 「이음매 2」.

TGW 는 리전 자원이고 계정 간 공유(RAM)의 대표 사례다 — 허브 계정이 소유하고 멤버 계정이
어태치먼트를 만든다. 소유자 귀속은 `ctx.attribute` 가 처리한다(ADR 0002 §2). 어태치먼트는
별 타입(`transit_gateway_attachment.py`)이다.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of

SPEC = Spec(
    "ec2",
    "describe_transit_gateways",
    "TransitGateways",
    "ec2:DescribeTransitGateways",
)


# `Options` 키 → 속성 이름(콘솔 「세부 정보」 칸 순서)
_OPTION_KEYS = (
    ("AssociationDefaultRouteTableId", "association_default_route_table_id"),
    ("DefaultRouteTablePropagation", "default_route_table_propagation"),
    ("PropagationDefaultRouteTableId", "propagation_default_route_table_id"),
    ("AutoAcceptSharedAttachments", "auto_accept_shared_attachments"),
    ("VpnEcmpSupport", "vpn_ecmp_support"),
    ("DnsSupport", "dns_support"),
    ("SecurityGroupReferencingSupport", "security_group_referencing_support"),
    ("MulticastSupport", "multicast_support"),
)


def _options(item: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        attr: str(options[key])
        for key, attr in _OPTION_KEYS
        if options.get(key) not in (None, "")
    }
    if options.get("TransitGatewayCidrBlocks") is not None:
        out["transit_gateway_cidr_blocks"] = sorted(
            {str(c) for c in options["TransitGatewayCidrBlocks"] if c}
        )
    enc = (options.get("EncryptionSupport") or {}).get("EncryptionState")
    if enc:
        out["encryption_support"] = str(enc)
    if item.get("Description"):
        out["description"] = str(item["Description"])
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("transit_gateway", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["TransitGatewayId"]
    options = item.get("Options") or {}
    node = Node(
        id=node_id("transit_gateway", owner, ctx.region, native),
        type="transit_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("TransitGatewayArn"),
        name=tags.get("Name") or (item.get("Description") or None),
        attributes={
            "state": item.get("State"),
            "amazon_side_asn": options.get("AmazonSideAsn"),
            "default_route_table_association": str(
                options.get("DefaultRouteTableAssociation") or ""
            ),
            # 2.13.0 — 콘솔 TGW 「세부 정보」 그대로(정보창 개편 사용자 결정 2026-10-24 · 목록 응답 `Options`).
            #   값은 AWS 어휘(`enable`/`disable`) 그대로 — 화면이 콘솔 말로 옮긴다. 응답에 없는 키는 만들지 않는다.
            **_options(item, options),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


SERVICE = Service(spec=SPEC, build=build)
