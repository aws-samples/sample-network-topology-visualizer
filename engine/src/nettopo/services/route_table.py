"""라우트 테이블 수집 — 「이음매 2」.

이 파일이 만드는 엣지 두 종류의 성질이 다르다:

- `association` — 대상이 서브넷으로 **확정**돼 있다(`target_type="subnet"`).
- `route` — 대상 종류를 **모른다**(`target_type=None`). `igw-`·`nat-`·`tgw-`·`vpce-`·`eni-` 가 다
  올 수 있고 2차 리소스(VPN·Core Network)일 수도 있다. 3패스가 네이티브 ID 로 찾고, 못 찾으면
  `unresolved_refs` 에 남긴다(ADR 0002 §5) — 없는 노드를 지어내지 않는다.

🔴 목적지마다 선을 만들지 않는다. 대상별로 하나로 묶는다(ADR 0002 §4) — 라우트 50개가 모두
TGW 로 가면 선 50개가 아니라 1개고, 목적지 목록은 그 선의 속성이다.

🔵 **접두사 목록 펼치기**(배치 D · 스키마 2.10.0 · ADR 0002 §12-1 · `docs/canvas-principles.md` §17.33) — 목적지가 `pl-…` 인 라우트는
그 쌍에서 `DescribeManagedPrefixLists` 로 이름·주인을 읽고(AWS 관리형 = `OwnerId` 「AWS」 · S3·DynamoDB 등), **고객 관리형**만
`GetManagedPrefixListEntries` 로 CIDR 항목을 읽는다(AWS 관리형 S3 목록은 수백 줄이고 고객 구성이 아니다). 엣지가 아니라 테이블 속성이다 —
`prefix_lists`(「<pl-id> <aws|customer> <이름>」) · `prefix_list_entries`(「<pl-id> <CIDR>」 · 고객 관리형만) ·
`prefix_list_entries_unread`(항목을 못 읽은 고객 관리형 목록 id). 🔴 거부는 그 목록을 쓰는 테이블의 사유다(불변식 ④ · 키를 만들지 않는다) —
「이름 없는 목록」과 「이름을 못 읽은 목록」이 같은 모양이면 안 된다. 이 계정이 볼 수 없는 목록(공유가 끊겼다)은 이름을 지어내지 않는다(키에 없다).

🔵 **콘솔 「라우팅」 탭 그대로의 경로 표**(스키마 2.13.0 · 정보창 개편 사용자 결정 2026-10-24 「콘솔이 보여 주는 값은 우리도 보여 준다」) —
`routes` = 경로 한 줄에 한 문자열 「<목적지> <대상> <상태> <라우팅 오리진>」(콘솔 열 「대상 · 대상 · 상태 · 라우팅 오리진」 · 「전파됨」 = 오리진이
`EnableVgwRoutePropagation`). 🔴 **VPC 내부 경로(`local`)도 싣는다** — 대상 노드가 없어 엣지는 안 만들지만(위 `route` 엣지 규칙 그대로) 콘솔은 늘
첫 줄에 보여 준다. 빼면 `route_count`(3) 와 화면 줄 수(2)가 어긋나 「경로가 빠졌다」로 읽힌다(실측 2026-10-24). 대상은 AWS 가 준 id 그대로
(Cloud WAN 은 ARN 끝 조각 — 계정 id 가 든 ARN 을 화면 재료로 싣지 않는다). 경로별 상태가 여기 있으므로 엣지의 `blackhole`(대상 묶음 OR)은 선의 판정
재료로만 남고, 엣지 `blackhole_destinations` 가 그 선에서 **실제로 죽은 목적지**만 든다(같은 대상의 정상 경로까지 블랙홀로 번지지 않는다).
`edge_association_gateway_ids`(콘솔 「엣지 연결」 — 게이트웨이 연결) · `propagating_vgw_ids`(「라우팅 전파」 — 전파 켠 VGW)도 같은 응답에 있다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
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
    pages,
    tags_of,
)

SPEC = Spec("ec2", "describe_route_tables", "RouteTables", "ec2:DescribeRouteTables")
PREFIX_LIST_API = "ec2:DescribeManagedPrefixLists"
PREFIX_ENTRIES_API = "ec2:GetManagedPrefixListEntries"
_PL = "_PrefixLists"
# AWS 관리형 접두사 목록의 주인(`OwnerId`) — 실측: `com.amazonaws.<리전>.s3` 등 AWS 관리형 목록은 전부 「AWS」
_AWS_OWNER = "AWS"


def _pl_refs(item: dict[str, Any]) -> set[str]:
    return {
        str(r["DestinationPrefixListId"])
        for r in item.get("Routes") or []
        if r.get("DestinationPrefixListId")
    }


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """라우트가 가리키는 접두사 목록을 그 쌍에서 한 번 읽는다 — 목록을 안 쓰는 쌍은 부르지 않는다.

    🔴 `PrefixListIds` 로 좁히지 않는다 — 없는 id 가 하나라도 섞이면 AWS 가 **호출 전체**를 `InvalidPrefixListID.NotFound` 로
    거절한다(공유가 끊긴 목록 하나가 그 쌍의 모든 이름을 지운다). 이 계정이 보는 목록 전부를 읽고 쓰는 것만 고른다(AWS 관리형 ≈ 20개/리전).
    """
    wanted = sorted({pl for it in items for pl in _pl_refs(it.payload)})
    if not wanted:
        return items
    try:
        lists = pages(client, "describe_managed_prefix_lists", "PrefixLists")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, PREFIX_LIST_API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,)) if _pl_refs(it.payload) else it
            for it in items
        ]
    seen = {str(p.get("PrefixListId")): p for p in lists if p.get("PrefixListId")}
    info: dict[str, dict[str, Any]] = {}
    failed: dict[str, Reason] = {}
    for pl in wanted:
        raw = seen.get(pl)
        if raw is None:
            continue  # 이 계정이 볼 수 없는 목록 — 이름을 지어내지 않는다(화면은 id 그대로)
        managed = str(raw.get("OwnerId") or "") == _AWS_OWNER
        entries: list[str] | None = None
        if not managed:
            try:
                got = pages(
                    client,
                    "get_managed_prefix_list_entries",
                    "Entries",
                    PrefixListId=pl,
                )
            except (ClientError, BotoCoreError) as exc:
                failed[pl] = classify(exc, PREFIX_ENTRIES_API)[1]
            else:
                entries = sorted({str(e["Cidr"]) for e in got if e.get("Cidr")})
        info[pl] = {
            "name": str(raw.get("PrefixListName") or ""),
            "managed": managed,
            "entries": entries,
        }
    out: list[Fetched] = []
    for it in items:
        refs = sorted(_pl_refs(it.payload))
        if not refs:
            out.append(it)
            continue
        reasons = list(it.reasons)
        for pl in refs:
            r = failed.get(pl)
            if r is not None and r not in reasons:
                reasons.append(r)
        payload = {**it.payload, _PL: {pl: info[pl] for pl in refs if pl in info}}
        out.append(Fetched(payload, tuple(reasons)))
    return out


def _prefix_attributes(pls: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """접두사 목록 속성 셋(정렬 · 스칼라 배열 — 계약 `$defs/attributes`). 고객 관리형이 없으면 항목 키를 만들지 않는다."""
    out: dict[str, Any] = {
        "prefix_lists": sorted(
            f"{pl} {'aws' if v['managed'] else 'customer'} {v['name']}".rstrip()
            for pl, v in pls.items()
        )
    }
    customer = {pl: v for pl, v in pls.items() if not v["managed"]}
    if customer:
        out["prefix_list_entries"] = sorted(
            f"{pl} {c}" for pl, v in customer.items() for c in v["entries"] or []
        )
    unread = sorted(pl for pl, v in customer.items() if v["entries"] is None)
    if unread:
        out["prefix_list_entries_unread"] = unread
    return out


# 라우트의 대상. 앞에서부터 처음 값이 있는 것을 쓴다 — `GatewayId` 는 `local`(VPC 자기 CIDR)
# 이나 `igw-`·`vpce-` 를 담아서 특수 키들보다 뒤에 둔다.
_ROUTE_TARGET_KEYS = (
    "TransitGatewayId",
    "NatGatewayId",
    "VpcPeeringConnectionId",
    "EgressOnlyInternetGatewayId",
    "CarrierGatewayId",
    "LocalGatewayId",
    "NetworkInterfaceId",
    "InstanceId",
    "CoreNetworkArn",
    "GatewayId",
)


def _route_target(route: dict[str, Any]) -> str | None:
    for key in _ROUTE_TARGET_KEYS:
        value = route.get(key)
        if value and str(value) != "local":
            if key == "CoreNetworkArn":
                # #129(2.11.0 · ADR 0002 §13-2) — Cloud WAN 코어 네트워크는 ARN 으로만 온다(`arn:aws:networkmanager::<계정>:core-network/<id>`).
                #   노드의 native id 는 끝 조각(`core-network-…`)이다 → 그 조각으로 찾아야 3패스가 코어 네트워크 노드에 잇는다.
                #   ARN 그대로 두면 늘 `unresolved_refs` 였다(target-gap §C 15). 못 찾으면 그 id 가 `unresolved_refs` 에 남는다.
                return str(value).rsplit("/", 1)[-1]
            return str(value)
    return None


_PROPAGATED = "EnableVgwRoutePropagation"


def _destination(route: dict[str, Any]) -> str | None:
    value = (
        route.get("DestinationCidrBlock")
        or route.get("DestinationIpv6CidrBlock")
        or route.get("DestinationPrefixListId")
    )
    return str(value) if value else None


def _route_line(route: dict[str, Any]) -> str:
    """경로 하나 → 콘솔 「라우팅」 표 한 줄의 재료(스칼라). 빈 칸은 `-` — 공백으로 가른 자리가 밀리지 않게."""
    target = _route_target(route) or (
        "local" if str(route.get("GatewayId") or "") == "local" else "-"
    )
    return " ".join(
        [
            _destination(route) or "-",
            target,
            str(route.get("State") or "-").lower(),
            str(route.get("Origin") or "-"),
        ]
    )


def _quota_routes(routes: list[dict[str, Any]]) -> dict[str, int]:
    static = [r for r in routes if r.get("Origin") != _PROPAGATED]
    return {
        "ipv4_route_count": sum(
            1
            for r in static
            if r.get("DestinationCidrBlock") or r.get("DestinationPrefixListId")
        ),
        "ipv6_route_count": sum(1 for r in static if r.get("DestinationIpv6CidrBlock")),
        "propagated_route_count": len(routes) - len(static),
    }


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["RouteTableId"]
    nid = node_id("route_table", owner, ctx.region, native)
    associations = item.get("Associations") or []
    routes = item.get("Routes") or []
    edges: list[EdgeDraft] = []

    edge_gateways: set[str] = set()
    for association in associations:
        subnet = association.get("SubnetId")
        if not subnet:
            # 메인 연관은 서브넷이 없다 — attributes.main 으로 남긴다. 게이트웨이 연결(엣지 연결)은 속성으로(2.13.0)
            if association.get("GatewayId") and alive(
                (association.get("AssociationState") or {}).get("State")
            ):
                edge_gateways.add(str(association["GatewayId"]))
            continue
        state = str(
            (association.get("AssociationState") or {}).get("State") or "associated"
        )
        edges.append(
            EdgeDraft(
                "association",
                nid,
                str(subnet),
                "subnet",
                False,
                {"state": state},
                live=alive(state),
            )
        )

    # 목적지마다 선을 만들지 않는다 — 대상별로 하나로 묶는다(ADR 0002 §4).
    grouped: dict[str, dict[str, Any]] = {}
    for route in routes:
        target = _route_target(route)
        if target is None:
            continue
        bucket = grouped.setdefault(
            target, {"destinations": set(), "dead": set(), "blackhole": False}
        )
        destination = _destination(route)
        if destination:
            bucket["destinations"].add(destination)
        if str(route.get("State") or "").lower() == "blackhole":
            bucket["blackhole"] = True
            if destination:
                bucket["dead"].add(destination)
    for target, bucket in grouped.items():
        edges.append(
            EdgeDraft(
                "route",
                nid,
                target,
                None,
                True,
                {
                    "destinations": sorted(bucket["destinations"]),
                    "blackhole": bucket["blackhole"],
                    # 2.13.0 — 이 선에서 **실제로** 죽은 목적지만(같은 대상의 정상 경로는 들지 않는다)
                    "blackhole_destinations": sorted(bucket["dead"]),
                },
            )
        )

    node = Node(
        id=nid,
        type="route_table",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "main": any(a.get("Main") for a in associations),
            "association_count": len(associations),
            "route_count": len(routes),
            # 한도 단위(ADR 0011 D3) — `route_count` 는 계열을 섞는다. 한도는 IPv4 · IPv6 따로(500) · 전파 따로(100).
            # 실측(2026-10-04): AWS/Usage `RoutesPerRouteTable` = 전파 아닌 IPv4 + 접두사 목록(1개 = 1 · local 포함)
            # 18/18 일치. 전파 라우트를 빼는지는 188 에 전파가 없어 미측정이다.
            **_quota_routes(routes),
            # 콘솔 「라우팅」 표(2.13.0 · 머리말) — local 포함 · `route_count` 와 줄 수가 같다
            "routes": sorted(_route_line(r) for r in routes),
            "edge_association_gateway_ids": sorted(edge_gateways),
            "propagating_vgw_ids": sorted(
                {
                    str(v["GatewayId"])
                    for v in item.get("PropagatingVgws") or []
                    if v.get("GatewayId")
                }
            ),
            # 접두사 목록(배치 D · 2.10.0) — 키가 없으면 목록을 안 쓰거나 못 읽었다(못 읽었으면 이 노드가 `partial`)
            **(_prefix_attributes(item[_PL]) if item.get(_PL) is not None else {}),
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
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
