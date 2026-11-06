"""Transit Gateway 어태치먼트 수집 — 「이음매 2」.

소유자 키가 `OwnerId` 가 아니라 **`ResourceOwnerId`** 다 — 어태치먼트를 만든 계정이 소유자이고,
TGW 소유자(`TransitGatewayOwnerId`)는 다른 계정일 수 있다(ADR 0002 §2). 이 두 값을 섞으면
멤버 계정의 어태치먼트가 허브 계정 레인으로 옮겨간다.

🔴 **단, 피어링은 예외다(ADR 0002 §2-2).** `ResourceId` 가 「붙은 자원」이 아니라 **상대편 TGW** 라서
`ResourceOwnerId` 는 「상대 계정」이 된다 — 그 계정은 이 리전에서 이 어태치먼트를 보지도 못한다.
실측 2026-09-26: 그렇게 놓은 노드 2개는 자기 (계정, 리전) 의 `coverage.item_count` 가 **0** 이었다
(§2-1 파생 불변식 위반, 스냅샷 전체에서 이 2행만). 피어링만 **로컬 TGW 소유자**로 귀속시킨다.

🔵 **TGW Connect**(#129 · 스키마 2.11.0 · ADR 0002 §13-1 · target-gap §C 15) — `connect` 어태치먼트는 SD-WAN 어플라이언스와 TGW 사이의 GRE
오버레이이고 **다른 어태치먼트(VPC·DX) 위에 올라탄다**. 지금까지 이 종류는 대상 노드가 없어 `ResourceId` 가 `unresolved_refs` 로 남았고 화면은 TGW 에
묻었다(「오버레이 경로가 끊겨 보인다」). 그 쌍에 Connect 어태치먼트가 하나라도 있을 때만 두 번 묻는다:
`DescribeTransitGatewayConnects`(전송 어태치먼트 id · 프로토콜 — 🔴 전송 id 의 정본 · 못 읽으면 목록의 `ResourceId` 로 잇는다) ·
`DescribeTransitGatewayConnectPeers`(GRE 피어 — TGW 쪽 주소 · 피어 주소 · BGP 내부 CIDR · ASN). 엣지 `connect_transport`(Connect → 전송 · 방향 있음).
속성 `connect_peers` = 「<피어 id> <프로토콜> <TGW 주소> <피어 주소> <내부 CIDR,…> <TGW ASN> <피어 ASN> [이름]」(피어 id 순). 🔴 BGP 세션 상태(`BgpStatus`)·
피어 `State` 는 싣지 않는다(시시각각 · 상태 단의 몫 · 불변식 ②). 거부 = 그 Connect 어태치먼트의 사유(키를 만들지 않는다 · 불변식 ④) —
「피어 없음」(`connect_peers` = [])과 「피어를 못 읽음」(키 없음 + `partial`)이 같은 모양이면 안 된다. 🔴 188 에는 Connect 가 없다 — 가짜 세계로만 쟀다.

🔵 **콘솔 어태치먼트 화면 그대로**(스키마 2.13.0 · 정보창 개편 사용자 결정 2026-10-24) — 목록 응답에 이미 있는 연결된 라우팅 테이블
(`association_route_table_id`·`association_state`)과 리소스 소유자(`resource_owner_id` — 늘 싣는다 · 귀속과 무관한 사실)를 싣는다.
VPC 어태치먼트는 콘솔 상세의 「서브넷 · DNS 지원 · IPv6 지원 · 어플라이언스 모드 · 보안 그룹 참조」를 그 쌍에 VPC 어태치먼트가 하나라도 있을 때만
`DescribeTransitGatewayVpcAttachments` 한 번으로 읽는다(`subnet_ids` · `dns_support` · `ipv6_support` · `appliance_mode_support` ·
`security_group_referencing_support`). 🔴 거부 = 그 쌍의 VPC 어태치먼트마다 사유(키를 만들지 않는다) — 「서브넷 없음」과 「못 읽음」이 같은 모양이면
안 된다(지금까지 정보창이 「연결된 서브넷 — 수집 안 함」이라 말하던 자리 · 콘솔은 「서브넷 2개」).
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
    pages,
    tags_of,
)

SPEC = Spec(
    "ec2",
    "describe_transit_gateway_attachments",
    "TransitGatewayAttachments",
    "ec2:DescribeTransitGatewayAttachments",
)

CONNECTS_API = "ec2:DescribeTransitGatewayConnects"
PEERS_API = "ec2:DescribeTransitGatewayConnectPeers"
VPC_ATTACHMENTS_API = "ec2:DescribeTransitGatewayVpcAttachments"
_CONNECT = "_Connect"
_PEERS = "_ConnectPeers"
_VPC = "_VpcAttachment"
# VPC 어태치먼트 옵션(`Options`) → 속성 이름(2.13.0 · 콘솔 상세의 칸 순서)
_VPC_OPTIONS = (
    ("DnsSupport", "dns_support"),
    ("Ipv6Support", "ipv6_support"),
    ("ApplianceModeSupport", "appliance_mode_support"),
    ("SecurityGroupReferencingSupport", "security_group_referencing_support"),
)
# 지워진 피어는 싣지 않는다(목록에 한동안 남는다 — `base.GONE_STATES` 와 같은 이유)
_PEER_GONE = frozenset({"deleted"})

# 어태치먼트 `ResourceType` → 그 대상의 노드 타입. 여기 없는 종류(peering·vpn-concentrator)는
# 대상 노드가 없어 `unresolved_refs` 로 남는다. `connect` 는 아래 `connect_transport` 엣지가 따로 잇는다(#129).
_RESOURCE_NODE_TYPES = {
    "vpc": "vpc",
    "direct-connect-gateway": "direct_connect_gateway",
    # 2.6.0 — TGW ↔ VPN 연결은 **이 엣지 하나**로만 그린다(VPN 연결 쪽 `TransitGatewayId` 는 속성).
    "vpn": "vpn_connection",
}


def _is_connect(item: dict[str, Any]) -> bool:
    return str(item.get("ResourceType") or "") == "connect"


def _is_vpc(item: dict[str, Any]) -> bool:
    return str(item.get("ResourceType") or "") == "vpc"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    return _enrich_connect(client, _enrich_vpc(client, items))


def _enrich_vpc(client: Any, items: list[Fetched]) -> list[Fetched]:
    """VPC 어태치먼트가 있는 쌍에서만 `DescribeTransitGatewayVpcAttachments` 를 한 번 묻는다(2.13.0) — 없으면 호출 0."""
    if not any(_is_vpc(it.payload) for it in items):
        return items
    try:
        got = pages(
            client,
            "describe_transit_gateway_vpc_attachments",
            "TransitGatewayVpcAttachments",
        )
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, VPC_ATTACHMENTS_API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,)) if _is_vpc(it.payload) else it
            for it in items
        ]
    by_id = {
        str(v["TransitGatewayAttachmentId"]): v
        for v in got
        if v.get("TransitGatewayAttachmentId")
    }
    out: list[Fetched] = []
    for it in items:
        aid = str(it.payload.get("TransitGatewayAttachmentId") or "")
        if not _is_vpc(it.payload) or aid not in by_id:
            # 목록에 있는데 상세에 없다 = 그 사이에 지워졌다(목록·상세 시차) — 지어내지 않는다(키 없음)
            out.append(it)
            continue
        out.append(Fetched({**it.payload, _VPC: by_id[aid]}, it.reasons))
    return out


def _vpc_parts(item: dict[str, Any]) -> dict[str, Any]:
    vpc = item.get(_VPC)
    if vpc is None:
        return {}
    options = vpc.get("Options") or {}
    return {
        "subnet_ids": sorted({str(s) for s in vpc.get("SubnetIds") or [] if s}),
        **{
            attr: str(options[key]).lower()
            for key, attr in _VPC_OPTIONS
            if options.get(key) is not None
        },
    }


def _association_parts(item: dict[str, Any]) -> dict[str, Any]:
    """연결된 라우팅 테이블(2.13.0) — 연결이 없으면 키 없음(콘솔은 「-」)."""
    assoc = item.get("Association") or {}
    if not assoc.get("TransitGatewayRouteTableId"):
        return {}
    out = {"association_route_table_id": str(assoc["TransitGatewayRouteTableId"])}
    if assoc.get("State"):
        out["association_state"] = str(assoc["State"]).lower()
    return out


def _enrich_connect(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Connect 어태치먼트가 있는 쌍에서만 Connect·피어를 한 번씩 묻는다 — 없으면 호출 0(최소 호출)."""
    if not any(_is_connect(it.payload) for it in items):
        return items
    connects: dict[str, dict[str, Any]] | None = None
    peers: dict[str, list[dict[str, Any]]] | None = None
    failed: list[Any] = []
    try:
        got = pages(
            client, "describe_transit_gateway_connects", "TransitGatewayConnects"
        )
        connects = {
            str(c["TransitGatewayAttachmentId"]): c
            for c in got
            if c.get("TransitGatewayAttachmentId")
        }
    except (ClientError, BotoCoreError) as exc:
        failed.append(classify(exc, CONNECTS_API)[1])
    try:
        got = pages(
            client,
            "describe_transit_gateway_connect_peers",
            "TransitGatewayConnectPeers",
        )
        peers = {}
        for p in got:
            if str(p.get("State") or "").lower() in _PEER_GONE:
                continue
            peers.setdefault(str(p.get("TransitGatewayAttachmentId") or ""), []).append(
                p
            )
    except (ClientError, BotoCoreError) as exc:
        failed.append(classify(exc, PEERS_API)[1])
    out: list[Fetched] = []
    for it in items:
        if not _is_connect(it.payload):
            out.append(it)
            continue
        aid = str(it.payload.get("TransitGatewayAttachmentId") or "")
        payload = dict(it.payload)
        if connects is not None:
            payload[_CONNECT] = connects.get(aid) or {}
        if peers is not None:
            payload[_PEERS] = peers.get(aid, [])
        out.append(Fetched(payload, it.reasons + tuple(failed)))
    return out


def _peer_line(p: dict[str, Any]) -> str:
    """피어 하나 → 정보창 한 줄의 재료(스칼라 · 중첩 객체 금지). BGP 세션 상태는 뺀다."""
    cfg = p.get("ConnectPeerConfiguration") or {}
    bgp = (cfg.get("BgpConfigurations") or [{}])[0] or {}
    name = tags_of(p).get("Name") or ""
    parts = [
        str(p.get("TransitGatewayConnectPeerId") or "?"),
        str(cfg.get("Protocol") or "gre"),
        str(cfg.get("TransitGatewayAddress") or "?"),
        str(cfg.get("PeerAddress") or "?"),
        ",".join(sorted(str(c) for c in cfg.get("InsideCidrBlocks") or [])) or "?",
        str(bgp.get("TransitGatewayAsn") or "?"),
        str(bgp.get("PeerAsn") or "?"),
    ]
    return " ".join(parts + ([name] if name else []))


def _connect_parts(
    item: dict[str, Any], nid: str, state: str, live: bool
) -> tuple[dict[str, Any], list[EdgeDraft]]:
    """Connect 어태치먼트의 속성·엣지(#129). 전송 id = Connect 응답(정본) · 못 읽었으면 목록의 `ResourceId`."""
    conn = item.get(_CONNECT)
    transport = str(
        (conn or {}).get("TransportTransitGatewayAttachmentId")
        or item.get("ResourceId")
        or ""
    )
    attrs: dict[str, Any] = {}
    if transport:
        attrs["transport_attachment_id"] = transport
    if conn:
        proto = (conn.get("Options") or {}).get("Protocol")
        if proto:
            attrs["connect_protocol"] = str(proto)
    if item.get(_PEERS) is not None:
        attrs["connect_peers"] = sorted(_peer_line(p) for p in item[_PEERS])
    edges = (
        [
            EdgeDraft(
                "connect_transport",
                nid,
                transport,
                "transit_gateway_attachment",
                True,  # 이 Connect 는 그 어태치먼트 위에 올라탄다
                {"state": state},
                live=live,
            )
        ]
        if transport
        else []
    )
    return attrs, edges


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("transit_gateway_attachment", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    kind = str(item.get("ResourceType") or "")
    # 🔴 `tgw-peering` 은 deprecated 인데 enum 에 남아 있다(`resource-catalog.md` 13번) — 같은 피어링이다.
    # 문자열 하나로만 가르면 그 값이 올 때 상대 계정으로 귀속되고 병합이 꺼져 카드가 둘이 된다(R15).
    peering = kind in ("peering", "tgw-peering")
    # 피어링은 대상이 남의 리전 TGW 다 → 「붙은 쪽」 대신 **자기 TGW 쪽**에 앉힌다(위 docstring).
    owner, extra = ctx.attribute(
        item, "TransitGatewayOwnerId" if peering else "ResourceOwnerId"
    )
    native = item["TransitGatewayAttachmentId"]
    nid = node_id("transit_gateway_attachment", owner, ctx.region, native)
    state = str(item.get("State") or "")
    live = alive(state)
    edges: list[EdgeDraft] = []
    if item.get("TransitGatewayId"):
        edges.append(
            EdgeDraft(
                "attachment",
                nid,
                str(item["TransitGatewayId"]),
                "transit_gateway",
                False,
                {"state": state},
                live=live,
            )
        )
    # 노드 타입이 있는 자원만 이어 붙인다. peering 은 아직 노드가 없다 → 대상이 없으면
    # unresolved_refs 로 남는다(ADR 0002 §5). direct-connect-gateway 는 2.1.0 에서 노드가 생겼다 —
    # DXGW ↔ TGW 관계는 **이 엣지 하나**로만 그린다(DXGW 쪽 association 은 속성, ADR 0010).
    target_type = _RESOURCE_NODE_TYPES.get(str(item.get("ResourceType") or ""))
    connect_attrs: dict[str, Any] = {}
    if _is_connect(item):
        connect_attrs, connect_edges = _connect_parts(item, nid, state, live)
        edges.extend(connect_edges)
    if target_type and item.get("ResourceId"):
        edges.append(
            EdgeDraft(
                "attachment",
                nid,
                str(item["ResourceId"]),
                target_type,
                False,
                {"state": state},
                live=live,
            )
        )
    node = Node(
        id=nid,
        type="transit_gateway_attachment",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": state,
            "resource_type": item.get("ResourceType"),
            "resource_id": item.get("ResourceId"),
            "transit_gateway_id": item.get("TransitGatewayId"),
            "transit_gateway_owner_id": item.get("TransitGatewayOwnerId"),
            # 2.13.0 — 콘솔 「리소스 소유자 ID」 · 「연결된 라우팅 테이블 ID」 · 「연결 상태」(목록 응답 그대로)
            "resource_owner_id": item.get("ResourceOwnerId"),
            **_association_parts(item),
            **_vpc_parts(item),
            # 피어링에서만 — 귀속에 쓰지 않은 값을 사실로는 남긴다(상대 TGW 를 가진 계정).
            **(
                {"peer_account_id": str(item["ResourceOwnerId"])}
                if peering and item.get("ResourceOwnerId")
                else {}
            ),
            **connect_attrs,
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
        # 피어링 어태치먼트는 **한 자원인데 양쪽 리전 응답에 다 나온다**(id 가 같다). 응답에
        # 상대 리전이 없어 정본 리전(§2-1)을 계산할 수 없으므로 native id 로 병합한다(§2-2).
        # 나머지 종류는 리전 하나에만 있는 자원이라 이 스위치를 켜지 않는다.
        shared_native=peering,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
