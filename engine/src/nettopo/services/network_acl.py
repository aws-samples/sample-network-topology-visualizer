"""네트워크 ACL 수집 — 「이음매 2」.

엔트리(규칙)는 노드가 아니다 — 개수와 콘솔 표 그대로의 규칙 줄(`ingress_entries`·`egress_entries`)을 속성으로 싣는다. 연관된 서브넷은 `association` 엣지다.
`DescribeNetworkAcls` 의 연관 항목에는 상태 필드가 없으므로 `state` 를 지어내지 않고
`"associated"` 고정으로 둔다(응답에 없는 값을 추측해 채우면 나중에 상태처럼 읽힌다).
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, draft, tags_of

SPEC = Spec("ec2", "describe_network_acls", "NetworkAcls", "ec2:DescribeNetworkAcls")


# 기본 규칙 번호 — IPv4 · IPv6 기본 거부가 둘 다 이 번호다. 고객이 지우거나 바꿀 수 없다.
_DEFAULT_RULE_NUMBER = 32767


def _entries(item: dict[str, Any], *, egress: bool) -> int:
    return sum(
        1
        for e in item.get("Entries") or []
        if bool(e.get("Egress")) is egress
        and e.get("RuleNumber") != _DEFAULT_RULE_NUMBER
    )


def _entry_rows(item: dict[str, Any], *, egress: bool) -> list[str]:
    """콘솔의 「인바운드/아웃바운드 규칙」 표 그대로 — 기본 `*` 거부(32767)까지 **전부**(콘솔 셈법 · 사용자 결정 2026-10-24).

    한 줄 = 「규칙 번호 \t 프로토콜 \t 포트 범위 \t 소스(대상) CIDR \t 허용/거부」(탭 구분 스칼라 · AWS 원문 —
    프로토콜 `-1`·`6` · 포트 `443-443` · ICMP 는 「icmp <유형>/<코드>」 · 포트가 없으면 빈 칸 · 동작 `allow`·`deny`).
    32767 을 「*」로 쓰는 것은 화면 몫이다. 순서 = 규칙 번호(AWS 가 평가하는 순서) → 같은 번호면 IPv4 먼저.
    실측(2026-10-24 · 188): 콘솔 인바운드 6 = 이 줄 수 · `ingress_entry_count`(한도 단위 · 기본 규칙 뺌) 는 5.
    """
    rows: list[tuple[int, int, str]] = []
    for e in item.get("Entries") or []:
        if bool(e.get("Egress")) is not egress:
            continue
        port = e.get("PortRange") or {}
        icmp = e.get("IcmpTypeCode") or {}
        if port:
            ports = f"{port.get('From')}-{port.get('To')}"
        elif icmp:
            ports = f"icmp {icmp.get('Type')}/{icmp.get('Code')}"
        else:
            ports = ""
        ipv6 = bool(e.get("Ipv6CidrBlock"))
        cidr = str(e.get("Ipv6CidrBlock") or e.get("CidrBlock") or "")
        number = int(e.get("RuleNumber") or 0)
        line = "\t".join(
            (
                str(number),
                str(e.get("Protocol") or "-1"),
                ports,
                cidr,
                str(e.get("RuleAction") or ""),
            )
        )
        rows.append((number, 1 if ipv6 else 0, line))
    return [r[2] for r in sorted(rows)]


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["NetworkAclId"]
    nid = node_id("network_acl", owner, ctx.region, native)
    associations = item.get("Associations") or []
    edges = [
        EdgeDraft(
            "association",
            nid,
            str(a["SubnetId"]),
            "subnet",
            False,
            {"state": "associated"},
        )
        for a in associations
        if a.get("SubnetId")
    ]
    node = Node(
        id=nid,
        type="network_acl",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "is_default": bool(item.get("IsDefault", False)),
            "entry_count": len(item.get("Entries") or []),
            # 한도 단위(ADR 0011 D3) — `entry_count` 는 두 방향 + 기본 규칙(32767)의 합이라 한도(방향마다 20)와
            # 단위가 다르다. 기본 규칙을 빼는 것은 문서에 없다 → 빼고 ADR 에 적었다.
            "ingress_entry_count": _entries(item, egress=False),
            "egress_entry_count": _entries(item, egress=True),
            # 콘솔 표 그대로의 규칙 줄(기본 `*` 거부 포함 · `_entry_rows`) — 줄 수 = 콘솔의 「인바운드 규칙 (N)」
            "ingress_entries": _entry_rows(item, egress=False),
            "egress_entries": _entry_rows(item, egress=True),
            "association_count": len(associations),
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


SERVICE = Service(spec=SPEC, build=build)
