"""api_gateway(API Gateway REST API · v1) 수집 — 「이음매 2」 · 배치 D(스키마 2.10.0 · ADR 0002 §12-3 · target-gap #10).

「이 API 는 VPC 링크로 사설 NLB 에 들어온다」. 노드 하나 = REST API 하나 · 부모 = 리전(API 는 VPC 에 살지 않는다 — 화면 = 리전 자원 줄 · §17.35).
HTTP·WebSocket API(v2)는 같은 노드 종류를 `api_gateway_v2.py` 가 짓는다(목록 API 가 달라 수집 종류는 둘 — 거부가 따로 보인다).

엣지 `vpc_link`(API → LB · 방향 있음):
1. API 마다 `GetResources(embed=methods)` — 메서드 통합 중 `connectionType` = `VPC_LINK` 인 것의 `connectionId`(VPC 링크 id).
2. 그런 API 가 하나라도 있으면 그 쌍에서 `GetVpcLinks` 1회 — 링크의 `targetArns` = NLB ARN.
3. NLB ARN 의 (리전, 계정, 이름)으로 LB 노드 id 를 짓는다(`EdgeDraft.by_id`) — 같은 LB 로 가는 링크는 선 하나(`vpc_link_ids`·`vpc_link_names`).
못 이음(지어내지 않는다 · `unresolved_vpc_links` = 「<ref> <까닭>」): 단계 변수 링크(`${stageVariables.…}` = `stage_variable` — 배포 단계마다 다르다) ·
링크 목록에 없는 id(`link_not_found`) · 이 스냅샷에 없는 NLB(`not_collected` · 3패스).

🔴 IAM 은 `apigateway:GET` 하나를 **경로 ARN** 으로 좁힌다(`/restapis` · `/restapis/*/resources` · `/vpclinks` — 스택 B `ReadApiGateway`) — 와일드카드 액션이 아니다.
가드는 boto3 작업 이름(`GetRestApis`·`GetResources`·`GetVpcLinks`)으로 읽기 verb 를 잰다(불변식 ①). API 이름은 `"apigateway:GET"` 하나다.
🔴 구성만 싣는다 — 통합 URI·정책·키·스테이지 변수 값은 싣지 않는다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
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

API = "apigateway:GET"
SPEC = Spec("apigateway", "get_rest_apis", "items", API)
_LINK_IDS = "_LinkIds"
_LINKS = "_Links"
WHY_ATTR = "unresolved_vpc_links"


def lb_ref(arn: str) -> tuple[str, str, str] | None:
    """ELBv2 ARN(LB 또는 리스너) → (리전, 계정, LB 이름). 꼴이 아니면 None."""
    parts = str(arn).split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[2] != "elasticloadbalancing":
        return None
    res = parts[5].split("/")
    # loadbalancer/app/<이름>/<id> · listener/app/<이름>/<id>/<id>
    if len(res) < 3 or res[0] not in ("loadbalancer", "listener"):
        return None
    return parts[3], parts[4], res[2]


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for it in items:
        try:
            res = pages(
                client,
                "get_resources",
                "items",
                restApiId=str(it.payload["id"]),
                embed=["methods"],
            )
        except (ClientError, BotoCoreError) as exc:
            out.append(Fetched(it.payload, it.reasons + (classify(exc, API)[1],)))
            continue
        ids = sorted(
            {
                str((m.get("methodIntegration") or {}).get("connectionId"))
                for r in res
                for m in (r.get("resourceMethods") or {}).values()
                if (m.get("methodIntegration") or {}).get("connectionType")
                == "VPC_LINK"
                and (m.get("methodIntegration") or {}).get("connectionId")
            }
        )
        out.append(Fetched({**it.payload, _LINK_IDS: ids}, it.reasons))
    if not any(it.payload.get(_LINK_IDS) for it in out):
        return out
    try:
        links = pages(client, "get_vpc_links", "items")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,))
            if it.payload.get(_LINK_IDS)
            else it
            for it in out
        ]
    table = {
        str(v["id"]): {
            "name": str(v.get("name") or ""),
            "targets": _sorted(v.get("targetArns") or []),
        }
        for v in links
        if v.get("id")
    }
    return [
        Fetched({**it.payload, _LINKS: table}, it.reasons)
        if it.payload.get(_LINK_IDS)
        else it
        for it in out
    ]


def link_edges(
    nid: str,
    ctx: Ctx,
    uses: list[tuple[str, str | None]],
    links: dict[str, dict[str, Any]] | None,
) -> tuple[list[EdgeDraft], list[str]]:
    """(VPC 링크 id, 그 통합이 직접 가리키는 LB·리스너 ARN 또는 None) 목록 → (LB 마다 엣지 하나, 까닭 줄). v1·v2 가 같이 쓴다."""
    per: dict[str, dict[str, Any]] = {}
    why: set[str] = set()
    for link, uri in uses:
        if link.startswith("${"):
            why.add(f"{link} stage_variable")
            continue
        meta = (links or {}).get(link)
        if uri is not None:
            arns = [uri]
        elif meta is None:
            if links is not None:
                why.add(f"{link} link_not_found")
            continue
        else:
            arns = list(meta["targets"])
        for arn in arns:
            ref = lb_ref(arn)
            if ref is None:
                why.add(f"{link} unknown_target")
                continue
            region, account, name = ref
            if account not in ctx.scope_accounts or region not in ctx.scope_regions:
                why.add(f"{name} not_in_scope")
                continue
            tid = node_id("load_balancer", account, region, name)
            slot = per.setdefault(tid, {"name": name, "ids": set(), "names": set()})
            slot["ids"].add(link)
            if meta and meta["name"]:
                slot["names"].add(meta["name"])
    edges = [
        EdgeDraft(
            "vpc_link",
            nid,
            tid,
            None,
            True,  # API → 그 LB 로 넘긴다
            {"vpc_link_ids": sorted(v["ids"]), "vpc_link_names": sorted(v["names"])},
            by_id=True,
            ref=v["name"],
            why_attr=WHY_ATTR,
        )
        for tid, v in sorted(per.items())
    ]
    return edges, sorted(why)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["id"])
    nid = node_id("api_gateway", owner, ctx.region, native)
    attributes: dict[str, Any] = {
        "protocol": "REST",
        "endpoint_types": _sorted(
            (item.get("endpointConfiguration") or {}).get("types") or []
        ),
    }
    edges: list[EdgeDraft] = []
    ids = item.get(_LINK_IDS)
    if ids is not None:
        attributes["vpc_link_ids"] = list(ids)
        edges, why = link_edges(nid, ctx, [(i, None) for i in ids], item.get(_LINKS))
        if why:
            attributes[WHY_ATTR] = why
    tags = {str(k): str(v) for k, v in (item.get("tags") or {}).items()}
    node = Node(
        id=nid,
        type="api_gateway",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=item.get("name"),
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
