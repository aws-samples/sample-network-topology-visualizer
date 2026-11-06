"""Direct Connect 가상 인터페이스(private · public · transit) 수집 — 「이음매 2」(ADR 0010).

VIF 는 연결 위의 802.1Q VLAN + BGP 세션이다. **부모는 연결**(연결을 지우면 VIF 도 사라진다,
ADR 0002 §1) → 연결이 없으면 리전으로 내려간다. hosted VIF 가 그 경우다: VIF 소유 계정에서
연결은 다른 계정(또는 파트너) 소유라 같은 계정의 `DescribeConnections` 에 없다(§5.5-2).

엣지는 **VIF → DXGW `attachment`** 하나다(`directConnectGatewayId`). `virtualGatewayId`(VGW
직결)는 대상 노드 타입이 아직 없어 속성으로만 남긴다 — 끝점 없는 엣지를 만들면 매달린 참조가
된다(`load_balancer.py` 의 타깃과 같은 판단). public VIF 는 게이트웨이 필드가 둘 다 비어
있는 것이 **정상**이다(§5.5-8) — 끊긴 참조로 취급하지 않는다.

🔴 **비밀은 싣지 않는다** — `authKey`(VIF · `bgpPeers[]`) · `customerRouterConfig`. 라우터 설정을 통째로 주는
`DescribeRouterConfiguration` 은 부르지 않는다(§5.4). 피어 주소(`amazonAddress` · `customerAddress`)는 예전엔
여기 묶여 있었으나 **싣는다** — 콘솔이 VIF 세부 정보에 그대로 보여 주는 값이다(사용자 결정 2026-10-24
「콘솔이 보여 주는 값이면 우리도 보여 줘도 된다」 · `bgp_peer_details`).
🔴 **BGP 상태는 구조에 싣지 않는다.** `bgpPeers[].bgpStatus`(up/down) 는 시시각각 바뀌는 값이라
관측 계약(ADR 0006)의 몫이다(`load_balancer.py` 의 타깃 헬스와 같은 이유). 여기에는 피어의
**구성**(주소 체계·ASN)만 싣고, up/down 은 상태 단 탐침(아래 `state`)으로만 꺼낸다.
`state`(`virtualInterfaceState`)는 다른 종류의 `state` 와
같은 수명주기 필드라 싣는다.
🔴 4바이트 ASN 이면 `asn` 은 0 이다 — `asnLong` 을 먼저 읽는다(§5.5-10).
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, gone
from .base import sorted_strs as _sorted
from .direct_connect_connection import dx_tags

SPEC = Spec(
    "directconnect",
    "describe_virtual_interfaces",
    "virtualInterfaces",
    "directconnect:DescribeVirtualInterfaces",
)


def _asn(item: dict[str, Any]) -> int | None:
    """`asnLong` 우선 — 4바이트 ASN 이면 `asn` 은 0 으로 온다."""
    for key in ("asnLong", "asn"):
        value = item.get(key)
        if value:
            return int(value)
    return None


def _peer_labels(peers: list[dict[str, Any]]) -> list[str]:
    """`["ipv4:65000"]` — 주소 체계별 피어 구성. 상태(`bgpStatus`)는 일부러 빼고 정렬한다."""
    labels: set[str] = set()
    for peer in peers:
        family = str(peer.get("addressFamily") or "").lower()
        if not family:
            continue
        asn = _asn(peer)
        labels.add(f"{family}:{asn}" if asn is not None else family)
    return sorted(labels)


def _peer_details(peers: list[dict[str, Any]]) -> list[str]:
    """콘솔 「피어링」 표 한 줄 = 「주소 체계 \t ASN \t Amazon 쪽 주소 \t 고객 쪽 주소 \t 피어 ID」(탭 구분 스칼라).

    `authKey` · `bgpStatus` 는 일부러 뺀다(비밀 · 상태 단). 정렬 = 줄 글자 순(결정론).
    """
    rows = []
    for peer in peers:
        asn = _asn(peer)
        rows.append(
            "\t".join(
                (
                    str(peer.get("addressFamily") or "").lower(),
                    "" if asn is None else str(asn),
                    str(peer.get("amazonAddress") or ""),
                    str(peer.get("customerAddress") or ""),
                    str(peer.get("bgpPeerId") or ""),
                )
            )
        )
    return sorted(rows)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("direct_connect_virtual_interface", item.get("virtualInterfaceState")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = dx_tags(item)
    owner, extra = ctx.attribute(item, "ownerAccount")
    native = item["virtualInterfaceId"]
    nid = node_id("direct_connect_virtual_interface", owner, ctx.region, native)
    state = str(item.get("virtualInterfaceState") or "")
    connection_id = item.get("connectionId")
    dxgw_id = item.get("directConnectGatewayId")
    edges: list[EdgeDraft] = []
    if dxgw_id:
        edges.append(
            EdgeDraft(
                "attachment",
                nid,
                str(dxgw_id),
                "direct_connect_gateway",
                False,
                {
                    "state": state,
                    "virtual_interface_type": item.get("virtualInterfaceType"),
                },
                live=alive(state),
            )
        )
    node = Node(
        id=nid,
        type="direct_connect_virtual_interface",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name") or (item.get("virtualInterfaceName") or None),
        attributes={
            "state": state,
            "virtual_interface_type": item.get("virtualInterfaceType"),
            "connection_id": connection_id,
            "vlan": item.get("vlan"),
            "asn": _asn(item),
            "amazon_side_asn": item.get("amazonSideAsn"),
            "address_family": item.get("addressFamily"),
            "mtu": item.get("mtu"),
            "jumbo_frame_capable": item.get("jumboFrameCapable"),
            "direct_connect_gateway_id": dxgw_id or None,
            "virtual_gateway_id": item.get("virtualGatewayId") or None,
            "site_link_enabled": item.get("siteLinkEnabled"),
            # public VIF 가 광고하는 prefix. private/transit 은 빈 배열이 정상이다.
            "route_filter_prefixes": _sorted(
                p.get("cidr") for p in item.get("routeFilterPrefixes") or []
            ),
            "bgp_peers": _peer_labels(item.get("bgpPeers") or []),
            "bgp_peer_details": _peer_details(item.get("bgpPeers") or []),
            "amazon_address": item.get("amazonAddress") or None,
            "customer_address": item.get("customerAddress") or None,
            **extra,
        },
        tags=tags,
    )
    parents: list[str] = []
    parent_types: list[str] = []
    if connection_id:
        parents.append(
            node_id("direct_connect_connection", owner, ctx.region, str(connection_id))
        )
        parent_types.append("direct_connect_connection")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
        edges=edges,
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — VIF 수명주기와 **BGP 피어의 up/down 을 한 값으로 접는다**(ADR 0006 §O7 DX 행).

    `available` 은 VLAN 이 프로비저닝됐다는 뜻이지 「경로를 받고 있다」가 아니다 — BGP 세션이 전부
    내려가면 VIF 는 `available` 그대로인데 온프레미스로 가는 경로가 없다(`docs/resource-catalog.md` §5).
    VPN 탐침(`vpn_connection.state`)과 같은 정책이다: 전부 down 이면 접힌 값(`down` 판정), 일부만
    down 이면 남은 피어로 통하므로 접힌 `up` 값(이유 문구가 「일부 down」을 말한다).
    피어 정보가 아예 없으면(`bgpPeers` 빈 목록) VIF 상태 그대로 둔다 — 지어내지 않는다.
    🔴 구조 수집(`with_state=False`)은 이 함수를 부르지 않는다 — `bgpStatus` 는 구조 바이트에 들어가지 않는다.
    """
    status = str(item.get("virtualInterfaceState") or "").lower()
    if not status:
        return None
    if status != "available":
        return status
    peers = [str(p.get("bgpStatus") or "").lower() for p in item.get("bgpPeers") or []]
    if not peers:
        return "available"
    up = sum(1 for p in peers if p == "up")
    if up == 0:
        return "available-all-bgp-down"
    if up < len(peers):
        return "available-some-bgp-down"
    return "available"


def state_attributes(item: dict[str, Any]) -> dict[str, Any]:
    """BGP 피어 수 둘(정수) — 화면의 VIF 줄 「BGP up 2/2」 · 「BGP 일부 down 1/2」(관측 `bgp` · ADR 0006 §O7 DX 행).

    🔴 **상태 단에서만** 불린다(`collect(with_state=True)`) — 구조 바이트는 BGP 와 무관하다(위 `state` 와 같은
    이유 · 불변식 ②). 위 `state` 가 접은 값은 「전부 / 일부 / 없음」만 말하고 **몇 개 중 몇 개**는 버린다 — 그 수를
    여기서 따로 싣는다. `up` 은 AWS 가 `up` 이라고 한 피어만 센다(`down`·`unknown` 등은 세지 않는다).
    `bgpPeers` 키가 없으면 `{}` — 키가 없다 = 「모른다」(0 으로 적으면 「피어 없음」이 된다 · 불변식 ④).
    """
    peers = item.get("bgpPeers")
    if not isinstance(peers, list):
        return {}
    statuses = [str(p.get("bgpStatus") or "").lower() for p in peers]
    return {
        "bgp_peer_count": len(statuses),
        "bgp_up_count": statuses.count("up"),
    }


SERVICE = Service(
    spec=SPEC, build=build, state=state, state_attributes=state_attributes
)
