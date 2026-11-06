"""api_gateway_v2(API Gateway HTTP·WebSocket API) 수집 — 「이음매 2」 · 배치 D(스키마 2.10.0 · ADR 0002 §12-3 · target-gap #10).

노드 종류는 REST 와 같은 `api_gateway`(`protocol` = `HTTP`·`WEBSOCKET`) — 수집 종류만 따로다(목록 API 가 `apigatewayv2:GetApis` 라
거부가 REST 와 따로 일어난다 · 한 종류로 합치면 v2 거부를 붙일 항목이 없다 — 불변식 ④).

엣지 `vpc_link`(API → LB): API 마다 `GetIntegrations` — `ConnectionType` = `VPC_LINK` 인 통합의 `IntegrationUri` 가 **ALB·NLB 리스너 ARN**
(HTTP API 의 VPC 링크 = 서브넷·보안 그룹에 붙는다 · 대상은 통합이 말한다)이면 그 LB(리스너 ARN 의 이름 · `api_gateway.link_edges`).
Cloud Map 서비스 ARN(`servicediscovery`)은 우리가 수집하지 않는 종류다 — 「<서비스 id> cloud_map」. WebSocket 의 VPC 링크 통합은 REST 의
VPC 링크(NLB)를 쓴다 — `IntegrationUri` 가 ARN 이 아니면 링크 id 로 `unknown_target`(지어내지 않는다).
VPC 링크 이름·서브넷은 그 쌍의 `GetVpcLinks`(v2 · 페이지네이터 없음 → `NextToken`) 1회 — 통합에 VPC 링크가 있을 때만.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .api_gateway import API, WHY_ATTR, lb_ref, link_edges
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    pages,
)
from .base import sorted_strs as _sorted

SPEC = Spec("apigatewayv2", "get_apis", "Items", API)
_USES = "_Uses"
_LINKS = "_Links"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for it in items:
        try:
            got = pages(
                client, "get_integrations", "Items", ApiId=str(it.payload["ApiId"])
            )
        except (ClientError, BotoCoreError) as exc:
            out.append(Fetched(it.payload, it.reasons + (classify(exc, API)[1],)))
            continue
        uses = sorted(
            {
                (str(g.get("ConnectionId") or ""), str(g.get("IntegrationUri") or ""))
                for g in got
                if g.get("ConnectionType") == "VPC_LINK"
            }
        )
        out.append(Fetched({**it.payload, _USES: uses}, it.reasons))
    if not any(it.payload.get(_USES) for it in out):
        return out
    try:
        links = pages(client, "get_vpc_links", "Items")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,)) if it.payload.get(_USES) else it
            for it in out
        ]
    table = {
        str(v["VpcLinkId"]): {
            "name": str(v.get("Name") or ""),
            "targets": [],
            "subnets": _sorted(v.get("SubnetIds") or []),
        }
        for v in links
        if v.get("VpcLinkId")
    }
    return [
        Fetched({**it.payload, _LINKS: table}, it.reasons)
        if it.payload.get(_USES)
        else it
        for it in out
    ]


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["ApiId"])
    nid = node_id("api_gateway", owner, ctx.region, native)
    attributes: dict[str, Any] = {
        "protocol": str(item.get("ProtocolType") or "") or None
    }
    edges: list[EdgeDraft] = []
    uses = item.get(_USES)
    if uses is not None:
        links = item.get(_LINKS)
        attributes["vpc_link_ids"] = _sorted(link for link, _ in uses)
        lb_uses: list[tuple[str, str | None]] = []
        why: list[str] = []
        for link, uri in uses:
            if lb_ref(uri) is not None:
                lb_uses.append((link, uri))
            elif ":servicediscovery:" in uri:
                why.append(f"{uri.rsplit('/', 1)[-1]} cloud_map")
            else:
                why.append(f"{link} unknown_target")
        # 링크 이름표만 쓴다(대상은 통합의 URI) — 링크 목록을 못 읽었으면 이름 없이 잇는다(사유는 노드에 있다)
        edges, more = link_edges(nid, ctx, lb_uses, links if links is not None else {})
        lines = sorted(set(why) | set(more))
        if lines:
            attributes[WHY_ATTR] = lines
    tags = {str(k): str(v) for k, v in (item.get("Tags") or {}).items()}
    node = Node(
        id=nid,
        type="api_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=item.get("Name"),
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
