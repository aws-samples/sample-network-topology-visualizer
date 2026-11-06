"""VPC 피어링 수집 — 「이음매 2」.

크로스리전·크로스계정 피어링을 **양쪽에서 봐도 노드가 하나**여야 한다. 그래서 계정 축의 정본
(`ctx.attribute`)에 더해 리전 축의 정본(`ctx.canonical_region`)까지 필요한 유일한 타입이다
(ADR 0002 §2-1). 요청자 리전을 무조건 쓰면 끝점이 하나도 없는 좌표에 노드가 놓인다.

🔴 스키마의 `attributes` 는 스칼라·스칼라배열만 허용한다 → 양쪽 VPC 정보를 중첩 없이 펼친다.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, alive, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec(
    "ec2",
    "describe_vpc_peering_connections",
    "VpcPeeringConnections",
    "ec2:DescribeVpcPeeringConnections",
)


def _peer_cidrs(info: dict[str, Any]) -> list[str]:
    blocks = [b.get("CidrBlock") for b in info.get("CidrBlockSet") or []]
    return _sorted(blocks or [info.get("CidrBlock")])


def _more(
    item: dict[str, Any], requester: dict[str, Any], accepter: dict[str, Any]
) -> dict[str, Any]:
    """양쪽 IPv6 CIDR · 원격 VPC DNS 확인 허용(`PeeringOptions` — 활성 피어링에만 온다) · 수락 만료 시각(수락 대기에만).

    응답에 없는 값은 키를 만들지 않는다 — 「꺼짐」과 「모름」을 같은 모양으로 두지 않는다.
    """
    out: dict[str, Any] = {}
    for side, info in (("requester", requester), ("accepter", accepter)):
        v6 = _sorted(b.get("Ipv6CidrBlock") for b in info.get("Ipv6CidrBlockSet") or [])
        if v6:
            out[f"{side}_ipv6_cidr_blocks"] = v6
        dns = (info.get("PeeringOptions") or {}).get("AllowDnsResolutionFromRemoteVpc")
        if dns is not None:
            out[f"{side}_allow_dns_resolution_from_remote_vpc"] = bool(dns)
    expires = item.get("ExpirationTime")
    if expires:
        out["expiration_time"] = (
            expires.isoformat() if hasattr(expires, "isoformat") else str(expires)
        )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpc_peering_connection", (item.get("Status") or {}).get("Code")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    requester = item.get("RequesterVpcInfo") or {}
    accepter = item.get("AccepterVpcInfo") or {}
    owner, extra = ctx.attribute(requester)
    # 정본 리전(ADR 0002 §2-1) — 크로스리전 피어링을 양쪽에서 봐도 노드 id 가 같아지되,
    # 조회한 (계정, 리전) 조합 밖에는 놓지 않는다.
    region = ctx.canonical_region(requester, owner)
    native = item["VpcPeeringConnectionId"]
    nid = node_id("vpc_peering_connection", owner, region, native)
    state = str((item.get("Status") or {}).get("Code") or "")
    live = alive(state)
    edges = [
        EdgeDraft(
            "peering",
            nid,
            str(info["VpcId"]),
            "vpc",
            False,
            {"state": state, "side": side},
            disc=side,
            live=live,
        )
        for side, info in (("requester", requester), ("accepter", accepter))
        if info.get("VpcId")
    ]
    node = Node(
        id=nid,
        type="vpc_peering_connection",
        account_id=owner,
        region=region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": state,
            # 스키마의 attributes 는 스칼라·스칼라배열만 허용한다 → 중첩 없이 펼친다.
            "requester_vpc_id": requester.get("VpcId"),
            "requester_account_id": requester.get("OwnerId"),
            "requester_region": requester.get("Region"),
            "requester_cidr_blocks": _peer_cidrs(requester),
            "accepter_vpc_id": accepter.get("VpcId"),
            "accepter_account_id": accepter.get("OwnerId"),
            "accepter_region": accepter.get("Region"),
            "accepter_cidr_blocks": _peer_cidrs(accepter),
            # 2.13.0 — 콘솔 피어링 「세부 정보」·「DNS」 탭 그대로(정보창 개편 사용자 결정 2026-10-24 · 같은 목록 응답)
            **_more(item, requester, accepter),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        # 요청자 리전이 scope 밖일 수 있다 → 조회 중인 리전으로 내려간다.
        parents=(
            node_id("region", owner, region),
            ctx.region_node(owner),
            ctx.region_node(),
        ),
        parent_types=("region", "region", "region"),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build)
