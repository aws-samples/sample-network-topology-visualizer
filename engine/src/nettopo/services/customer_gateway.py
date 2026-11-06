"""고객 게이트웨이(CGW) 수집 — 「이음매 2」 · 2차 Site-to-Site VPN(2.6.0).

CGW 는 온프렘 장비를 **나타내는** AWS 리소스다 — 장비 자체는 API 로 보이지 않고, 「CGW 리소스는
장비를 설정하거나 만들지 않는다」(`docs/resource-catalog.md` §4.1). 그래서 부모는 리전이고(VPC 와
무관), VPN 연결 쪽이 이 노드로 `attachment` 엣지를 낸다(`services/vpn_connection.py`).

🔴 `IpAddress` 는 **고객 온프렘의 공인 IP** 다 — 실계정 산출물은 원래 커밋하지 않지만, fixture 로
승격할 때는 스크럽 대상이다(보안 규칙). `DescribeCustomerGateways` 는 페이지네이터가 없다(§4.5-11).
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of

SPEC = Spec(
    "ec2",
    "describe_customer_gateways",
    "CustomerGateways",
    "ec2:DescribeCustomerGateways",
)


def _asn(item: dict[str, Any]) -> int | None:
    # `BgpAsn` 은 문자열이고 2,147,483,647 까지만 담는다 — 4바이트 ASN 은 `BgpAsnExtended` 에만 있다.
    for key in ("BgpAsnExtended", "BgpAsn"):
        raw = item.get(key)
        if raw not in (None, ""):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("customer_gateway", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["CustomerGatewayId"]
    node = Node(
        id=node_id("customer_gateway", owner, ctx.region, native),
        type="customer_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name") or (item.get("DeviceName") or None),
        attributes={
            "state": item.get("State"),
            "type": item.get("Type"),
            "ip_address": item.get("IpAddress"),
            "bgp_asn": _asn(item),
            "device_name": item.get("DeviceName"),
            # 인증서로 인증하는 CGW 는 고정 IP 가 없을 수 있다 — ARN 은 싣지 않고 사실만 남긴다.
            "uses_certificate": bool(item.get("CertificateArn")),
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
