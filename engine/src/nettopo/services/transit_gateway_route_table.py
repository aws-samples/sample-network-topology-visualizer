"""transit_gateway_route_table(TGW 라우트 테이블) 수집 — 「이음매 2」 · W2.

TGW 를 지나는 길이 **어디로 갈 수 있는가**는 TGW 노드만으로는 안 보인다. 같은 TGW 에 붙은 VPC 두
개라도 서로 다른 라우트 테이블에 연결(association)돼 있고 상대의 전파(propagation)가 없으면 서로
못 간다. 그래서 테이블마다 세 가지를 싣는다:

- `association_attachment_ids` — 이 테이블을 쓰는 어태치먼트(그 어태치먼트에서 **나가는** 트래픽이 이 표를 본다)
- `propagation_attachment_ids` — 이 테이블에 경로를 **알려 주는** 어태치먼트
- 경로 — 대상 어태치먼트마다 `route` 엣지 하나(목적지 목록은 엣지 속성 · VPC 라우트 테이블과 같은
  모양, ADR 0002 §4). 대상이 없는 블랙홀 경로는 엣지를 만들 수 없어 `blackhole_destinations` 로 싣는다.

🔴 **연결·전파를 엣지로 만들지 않는 이유** — 속성 값은 스칼라·스칼라 배열만 된다(스키마
`attributes`). 연결·전파는 「어태치먼트 id 목록」이면 충분하고(어태치먼트 노드가 종류·VPC 를 안다),
새 엣지 종류를 만들면 관측 계약(ADR 0006 §O7)에 행이 하나 더 생긴다. 경로만 엣지인 이유는 「목적지 →
어태치먼트」 짝이 목록 하나로는 표현이 안 되기 때문이다. 테이블은 화면에 아이콘으로 그리지 않고 TGW
정보 창에서 표로 보여 준다(`docs/canvas-principles.md` §17.11).

🔴 **후속 호출 3개는 따로 실패한다.** 한 호출이 거부되면 그 키만 빼고(「못 읽음」) 사유를 단다 —
`[]` 를 채우면 「연결된 어태치먼트가 없다」(= 이 표는 아무도 안 쓴다)가 되어 거짓이다(불변식 ④).
경로 호출이 거부되면 `route` 엣지도 `blackhole_destinations` 도 없다 — 노드의 `partial` 이 그것을 말한다.

라우트 테이블 API 는 TGW **소유 계정**에만 테이블을 보여 준다(RAM 으로 공유받은 계정은 TGW 는 보지만
테이블은 못 본다) → 조회 계정이 소유자이고, 부모는 같은 계정·리전의 TGW 노드다.

🔵 **콘솔 탭 그대로의 표**(스키마 2.13.0 · 정보창 개편 사용자 결정 2026-10-24) — 콘솔의 네 탭(연결 · 전파 · 접두사 목록 참조 · 경로)을
한 줄 한 문자열로 싣는다(공백으로 가른 자리 · 빈 칸 `-`):
- `associations` = 「<어태치먼트 id> <리소스 종류> <리소스 id> <상태>」(콘솔 「연결」 탭 열 순서)
- `propagations` = 같은 꼴(「전파」 탭 · 꺼진 전파도 상태와 함께 싣는다 — 콘솔이 그렇게 보여 준다. 위 id 목록은 그대로 켜진 것만)
- `routes` = 「<CIDR|접두사 목록 id> <어태치먼트 id> <리소스 종류> <리소스 id> <경로 유형> <경로 상태>」(「경로」 탭 · ECMP 는 어태치먼트마다 한 줄 ·
  블랙홀은 어태치먼트 자리 `-`)
- `prefix_list_references` = 「<접두사 목록 id> <소유자 id> <어태치먼트 id> <리소스 종류> <리소스 id> <상태> <블랙홀 yes|no>」(「접두사 목록 참조」 탭 ·
  새 후속 호출 `ec2:GetTransitGatewayPrefixListReferences` — 거부 = 그 키 없음 + 사유)
지워진 경로(`deleted`·`deleting`)는 위 엣지 규칙과 같이 싣지 않는다. 후속 호출이 실패한 키는 만들지 않는다(같은 규칙).
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
    alive,
    classify,
    draft,
    gone,
    tags_of,
)
from .base import pages as _pages

SPEC = Spec(
    "ec2",
    "describe_transit_gateway_route_tables",
    "TransitGatewayRouteTables",
    "ec2:DescribeTransitGatewayRouteTables",
)

# (payload 키, 메서드, 응답 키, IAM 액션, 추가 인자)
# 🔴 `search_transit_gateway_routes` 는 `Filters` 가 **필수**다 — 빼면 API 가 거부한다. 정적·전파
#    둘 다 달라고 `type` 필터를 준다(이 필터가 경로 종류 전부다).
_FOLLOWUPS: tuple[tuple[str, str, str, str, dict[str, Any]], ...] = (
    (
        "_associations",
        "get_transit_gateway_route_table_associations",
        "Associations",
        "ec2:GetTransitGatewayRouteTableAssociations",
        {},
    ),
    (
        "_propagations",
        "get_transit_gateway_route_table_propagations",
        "TransitGatewayRouteTablePropagations",
        "ec2:GetTransitGatewayRouteTablePropagations",
        {},
    ),
    (
        "_routes",
        "search_transit_gateway_routes",
        "Routes",
        "ec2:SearchTransitGatewayRoutes",
        {"Filters": [{"Name": "type", "Values": ["static", "propagated"]}]},
    ),
    # 2.13.0 — 콘솔 「접두사 목록 참조」 탭
    (
        "_prefix_list_references",
        "get_transit_gateway_prefix_list_references",
        "TransitGatewayPrefixListReferences",
        "ec2:GetTransitGatewayPrefixListReferences",
        {},
    ),
)

# 전파는 `enabled`/`disabled` 어휘라 `DEAD_STATES` 로 못 거른다 — 꺼진 전파는 경로를 알려 주지 않는다.
_PROPAGATION_OFF = frozenset({"disabled", "disabling"})


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for item in items:
        payload = dict(item.payload)
        reasons = item.reasons
        table_id = payload.get("TransitGatewayRouteTableId")
        for key, method, result_key, api, extra in _FOLLOWUPS:
            try:
                payload[key] = _pages(
                    client,
                    method,
                    result_key,
                    TransitGatewayRouteTableId=table_id,
                    **extra,
                )
            except (ClientError, BotoCoreError) as exc:
                reasons = reasons + (classify(exc, api)[1],)
        out.append(Fetched(payload=payload, reasons=reasons))
    return out


def _attachment_ids(entries: list[dict[str, Any]], keep: Any) -> list[str]:
    return sorted(
        {
            str(e["TransitGatewayAttachmentId"])
            for e in entries
            if e.get("TransitGatewayAttachmentId") and keep(e.get("State"))
        }
    )


def _s(value: Any) -> str:
    return str(value) if value not in (None, "") else "-"


def _entry_lines(entries: list[dict[str, Any]]) -> list[str]:
    """연결·전파 탭 한 줄 = 「<어태치먼트 id> <리소스 종류> <리소스 id> <상태>」(2.13.0 · 끊긴 연결은 뺀다 — `alive`)."""
    return sorted(
        " ".join(
            [
                _s(e.get("TransitGatewayAttachmentId")),
                _s(e.get("ResourceType")),
                _s(e.get("ResourceId")),
                _s(str(e.get("State") or "").lower()),
            ]
        )
        for e in entries
        if e.get("TransitGatewayAttachmentId") and alive(e.get("State"))
    )


def _route_lines(routes: list[dict[str, Any]]) -> list[str]:
    """「경로」 탭 한 줄씩(2.13.0) — ECMP 는 어태치먼트마다 · 블랙홀은 어태치먼트 자리가 `-`."""
    lines: set[str] = set()
    for route in routes:
        state = str(route.get("State") or "").lower()
        destination = _destination(route)
        if not alive(state) or destination is None:
            continue
        kind = _s(str(route.get("Type") or "").lower())
        attachments = route.get("TransitGatewayAttachments") or [{}]
        for a in attachments:
            lines.add(
                " ".join(
                    [
                        destination,
                        _s(a.get("TransitGatewayAttachmentId")),
                        _s(a.get("ResourceType")),
                        _s(a.get("ResourceId")),
                        kind,
                        _s(state),
                    ]
                )
            )
    return sorted(lines)


def _prefix_reference_lines(refs: list[dict[str, Any]]) -> list[str]:
    """「접두사 목록 참조」 탭 한 줄씩(2.13.0) — 지워지는 중인 참조는 뺀다."""
    out: list[str] = []
    for r in refs:
        state = str(r.get("State") or "").lower()
        if not r.get("PrefixListId") or not alive(state):
            continue
        att = r.get("TransitGatewayAttachment") or {}
        out.append(
            " ".join(
                [
                    str(r["PrefixListId"]),
                    _s(r.get("PrefixListOwnerId")),
                    _s(att.get("TransitGatewayAttachmentId")),
                    _s(att.get("ResourceType")),
                    _s(att.get("ResourceId")),
                    _s(state),
                    "yes" if r.get("Blackhole") else "no",
                ]
            )
        )
    return sorted(out)


def _destination(route: dict[str, Any]) -> str | None:
    # 목적지는 CIDR 아니면 프리픽스 리스트다(둘 중 하나만 온다).
    value = route.get("DestinationCidrBlock") or route.get("PrefixListId")
    return str(value) if value else None


def _route_edges(
    nid: str, routes: list[dict[str, Any]]
) -> tuple[list[EdgeDraft], list[str]]:
    """(대상 어태치먼트별 `route` 엣지, 블랙홀 목적지). 목적지마다 선을 만들지 않는다(ADR 0002 §4)."""
    grouped: dict[str, dict[str, set[str]]] = {}
    blackholes: set[str] = set()
    for route in routes:
        state = str(route.get("State") or "").lower()
        destination = _destination(route)
        if not alive(state) or destination is None:
            continue
        if state == "blackhole":
            blackholes.add(destination)
            continue
        # ECMP(VPN 여러 개)면 어태치먼트가 여럿이다 — 목적지를 각 대상에 싣는다.
        for attachment in route.get("TransitGatewayAttachments") or []:
            target = attachment.get("TransitGatewayAttachmentId")
            if not target:
                continue
            bucket = grouped.setdefault(str(target), {"all": set(), "static": set()})
            bucket["all"].add(destination)
            if str(route.get("Type") or "").lower() == "static":
                bucket["static"].add(destination)
    edges = [
        EdgeDraft(
            "route",
            nid,
            target,
            "transit_gateway_attachment",
            True,
            {
                "destinations": sorted(bucket["all"]),
                # 손으로 넣은 경로(정적) — 나머지는 어태치먼트가 알려 준 경로(전파)다.
                "static_destinations": sorted(bucket["static"]),
            },
        )
        for target, bucket in sorted(grouped.items())
    ]
    return edges, sorted(blackholes)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("transit_gateway_route_table", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    owner = ctx.account_id
    native = item["TransitGatewayRouteTableId"]
    tags = tags_of(item)
    attributes: dict[str, Any] = {
        "transit_gateway_id": item.get("TransitGatewayId"),
        "state": item.get("State"),
        "default_association": item.get("DefaultAssociationRouteTable"),
        "default_propagation": item.get("DefaultPropagationRouteTable"),
    }
    # 후속 호출이 실패한 키는 **만들지 않는다**(모듈 머리말). 성공했으면 빈 목록도 그대로 싣는다.
    if "_associations" in item:
        attributes["association_attachment_ids"] = _attachment_ids(
            item["_associations"], alive
        )
        attributes["associations"] = _entry_lines(item["_associations"])
    if "_propagations" in item:
        attributes["propagation_attachment_ids"] = _attachment_ids(
            item["_propagations"],
            lambda state: str(state or "").lower() not in _PROPAGATION_OFF,
        )
        attributes["propagations"] = _entry_lines(item["_propagations"])
    if "_prefix_list_references" in item:
        attributes["prefix_list_references"] = _prefix_reference_lines(
            item["_prefix_list_references"]
        )
    nid = node_id("transit_gateway_route_table", owner, ctx.region, native)
    edges: list[EdgeDraft] = []
    if "_routes" in item:
        edges, blackholes = _route_edges(nid, item["_routes"])
        attributes["blackhole_destinations"] = blackholes
        attributes["routes"] = _route_lines(item["_routes"])

    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("TransitGatewayId"):
        parents.append(
            node_id("transit_gateway", owner, ctx.region, str(item["TransitGatewayId"]))
        )
        parent_types.append("transit_gateway")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=nid,
        type="transit_gateway_route_table",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
