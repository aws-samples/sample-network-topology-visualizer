"""Direct Connect 게이트웨이(DXGW) 수집 — 「이음매 2」, **첫 글로벌 리소스**(ADR 0010).

DXGW 는 여러 리전·계정의 VGW/TGW 를 묶는 글로벌 라우트 리플렉터다(ARN 에 리전이 없다). 그런데
글로벌 엔드포인트가 없어 **리전 엔드포인트 어디서든** 같은 목록이 나온다(§5.3 「어느 리전에서
부르나」). 수집기는 (계정, 리전) 곱집합을 돌므로 리전마다 같은 DXGW 를 다시 만난다.

그래서 두 가지를 다르게 한다:

1. **id 의 리전 자리는 `GLOBAL`**, `region` 은 null, 부모는 **계정 노드**. 어느 리전에서 봐도
   id 가 같으므로 `collect._resolve` 의 「먼저 만난 것을 남긴다」가 그대로 중복 제거가 된다.
   조회한 리전을 id 에 넣으면 DXGW 하나가 리전 수만큼 늘어난다.
2. **coverage 는 리전마다 남긴다.** 호출은 실제로 리전마다 일어났고 각 호출의 성공·실패는
   다른 사실이다. 한 리전이 거부되고 다른 리전이 성공하면 노드는 있고 그 리전의 coverage 만
   denied 다 — 그게 맞다.

**후속 호출 1개** — `DescribeDirectConnectGatewayAssociations`(DXGW 당 1회, 페이지네이터 있음).
연결 상대(VGW·TGW)의 id·리전·소유 계정을 **속성**으로 싣는다. TGW 로 가는 엣지는 여기서 만들지
않는다 — TGW 어태치먼트(`ResourceType=direct-connect-gateway`)가 **같은 사실**이고 그쪽이 이미
DXGW 를 가리키므로 한 번만 그린다(§5.2). VGW 는 노드 타입이 아직 없어 속성으로만 남는다.
`DescribeDirectConnectGatewayAttachments` 는 부르지 않는다 — VIF 응답의 `directConnectGatewayId`
와 같은 사실이다(§5.1).

🔴 후속 호출은 **인자 없이 부를 수 없다**(`DirectConnectClientException`) — DXGW 마다 id 를 준다.
🔴 `stateChangeError` 는 싣지 않는다(에러 문구는 상태다). `totalPrefixPoolAllocations` 는 용량 카운터다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import GLOBAL, Node, Reason, node_id
from .base import (
    Ctx,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    alive,
    classify,
    draft,
    gone,
    pages,
)
from .base import sorted_strs as _sorted
from .direct_connect_connection import dx_tags

SPEC = Spec(
    "directconnect",
    "describe_direct_connect_gateways",
    "directConnectGateways",
    "directconnect:DescribeDirectConnectGateways",
)

_ASSOCIATIONS = (
    "directConnectGatewayAssociations",
    "describe_direct_connect_gateway_associations",
    "directconnect:DescribeDirectConnectGatewayAssociations",
)


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """DXGW 마다 연결(association)을 더 읽는다. 실패는 그 DXGW 의 `reasons` 로만 남는다.

    항목을 줄이지 않는다 — 거부된 DXGW 를 빼면 「DXGW 가 없다」가 되고 그건 거짓이다.
    응답 키를 payload 에 그대로 합친다(AWS 가 준 이름). 못 읽었으면 **키를 만들지 않는다**
    (`[]` 는 「연결이 없다」라는 다른 사실이다 — ADR 0007).
    """
    key, method, api = _ASSOCIATIONS
    out: list[Fetched] = []
    for item in items:
        dxgw_id = item.payload.get("directConnectGatewayId")
        payload = dict(item.payload)
        reasons: list[Reason] = list(item.reasons)
        if not dxgw_id:
            reasons.append(
                Reason(code="api_error", api=api, message="directConnectGatewayId 없음")
            )
        else:
            try:
                payload[key] = pages(
                    client, method, key, directConnectGatewayId=dxgw_id
                )
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, api)[1])
        out.append(Fetched(payload=payload, reasons=tuple(reasons)))
    return out


def _gateway_ids(associations: list[dict[str, Any]], gateway_type: str) -> list[str]:
    """살아 있는 연결의 상대 게이트웨이 id. 죽은(disassociated) 연결은 관계가 아니다(ADR 0002 §3)."""
    return _sorted(
        (a.get("associatedGateway") or {}).get("id")
        for a in associations
        if str((a.get("associatedGateway") or {}).get("type") or "") == gateway_type
        and alive(a.get("associationState"))
    )


def _association_rows(associations: list[dict[str, Any]]) -> list[str]:
    rows = []
    for a in associations:
        g = a.get("associatedGateway") or {}
        prefixes = sorted(
            str(p.get("cidr"))
            for p in a.get("allowedPrefixesToDirectConnectGateway") or []
            if p.get("cidr")
        )
        rows.append(
            "\t".join(
                (
                    str(a.get("associationId") or ""),
                    str(g.get("id") or ""),
                    str(g.get("type") or ""),
                    str(g.get("region") or ""),
                    str(g.get("ownerAccount") or ""),
                    str(a.get("associationState") or ""),
                    ",".join(prefixes),
                )
            )
        )
    return sorted(rows)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("direct_connect_gateway", item.get("directConnectGatewayState")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = dx_tags(item)
    owner, extra = ctx.attribute(item, "ownerAccount")
    native = item["directConnectGatewayId"]
    attributes: dict[str, Any] = {
        "state": item.get("directConnectGatewayState"),
        "amazon_side_asn": item.get("amazonSideAsn"),
        **extra,
    }
    # 후속 호출이 실패하면 키 자체가 없다(= 못 읽었다). 읽었는데 0개면 `[]`(= 없다).
    associations = item.get(_ASSOCIATIONS[0])
    if associations is not None:
        attributes["associated_transit_gateway_ids"] = _gateway_ids(
            associations, "transitGateway"
        )
        attributes["associated_virtual_private_gateway_ids"] = _gateway_ids(
            associations, "virtualPrivateGateway"
        )
        # 연결 상대가 어느 리전·어느 계정에 있는가 — DXGW 가 잇는 경계를 한눈에 보이게 한다.
        live = [
            a.get("associatedGateway") or {}
            for a in associations
            if alive(a.get("associationState"))
        ]
        attributes["associated_regions"] = _sorted(g.get("region") for g in live)
        attributes["associated_account_ids"] = _sorted(
            g.get("ownerAccount") for g in live
        )
        # 콘솔 「게이트웨이 연결」 표 한 줄(사용자 결정 2026-10-24) =
        # 「연결 id \t 게이트웨이 id \t 게이트웨이 유형 \t 리전 \t 계정 \t 상태 \t 허용 접두사(쉼표 · 정렬)」 — 연결 id 순.
        # 지워진(disassociated) 연결도 콘솔처럼 상태와 함께 남긴다 — 위 id 목록(살아 있는 것만)과 다른 칸이다.
        attributes["gateway_associations"] = _association_rows(associations)
    node = Node(
        id=node_id("direct_connect_gateway", owner, GLOBAL, native),
        type="direct_connect_gateway",
        account_id=owner,
        region=None,
        native_id=native,
        name=tags.get("Name") or (item.get("directConnectGatewayName") or None),
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
