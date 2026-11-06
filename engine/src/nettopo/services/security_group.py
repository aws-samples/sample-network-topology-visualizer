"""보안 그룹 수집 — 「이음매 2」.

규칙(CIDR·프리픽스 리스트)은 노드가 아니다 — 콘솔 표 그대로의 줄(`ingress_rules`·`egress_rules`)을 속성으로 싣는다. **다른 SG 를 가리키는 규칙만** 엣지가 된다
(`security_group_reference`). 방향은 구분자(`disc`)로 남겨 ingress·egress 가 한 선으로 합쳐지지
않게 한다.

🔴 자기참조는 엣지로 만들지 않는다(자기 루프) — `attributes.self_referencing` 으로 남긴다
(ADR 0002 §6). 렌더러에 자기 루프를 그리는 경로가 없고, 만들면 화면에서 점 하나로 사라진다.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, draft, tags_of

SPEC = Spec(
    "ec2",
    "describe_security_groups",
    "SecurityGroups",
    "ec2:DescribeSecurityGroups",
)


def _rule_label(permission: dict[str, Any]) -> str:
    protocol = str(permission.get("IpProtocol") or "-1")
    if protocol == "-1":
        return "all"
    low = permission.get("FromPort")
    high = permission.get("ToPort")
    if low is None:
        return protocol
    return f"{protocol}/{low}-{high}"


def _clean(text: Any) -> str:
    # 한 줄의 칸 구분자(탭)·줄바꿈이 설명에 섞이면 칸이 밀린다 — 공백으로 바꾼다
    return " ".join(str(text or "").split())


def _rule_rows(permissions: list[dict[str, Any]], owner: str) -> list[str]:
    """콘솔의 「인바운드/아웃바운드 규칙」 표 한 줄 = 소스(대상) 하나(콘솔 셈법 · 사용자 결정 2026-10-24).

    한 줄 = 「IP 버전 \\t 프로토콜 \\t 포트 범위 \\t 소스(대상) \\t 설명」(탭 구분 스칼라 — 속성은 중첩 객체 금지).
    값은 AWS 원문이다(프로토콜 `-1`·`tcp` · 포트 `443-443` · 프로토콜이 `-1` 이거나 포트가 없으면 빈 칸) — 「HTTPS」 같은
    유형 이름은 콘솔이 프로토콜·포트에서 짓는 말이라 화면이 짓는다. IP 버전은 CIDR 일 때만(접두사 목록·참조 SG 는 빈 칸 —
    콘솔도 「–」). 다른 계정의 참조 SG 는 「<계정>/<sg-…>」. 실측(2026-10-24 · 188): 콘솔 인바운드 4·아웃바운드 2 =
    이 줄 수 · 묶음(IpPermission) 수는 3·1.
    """
    rows: list[tuple[Any, ...]] = []
    for permission in permissions:
        protocol = str(permission.get("IpProtocol") or "-1")
        low, high = permission.get("FromPort"), permission.get("ToPort")
        ports = "" if protocol == "-1" or low is None else f"{low}-{high}"
        sources: list[tuple[str, str, Any]] = []
        sources += [
            ("IPv4", str(r.get("CidrIp")), r.get("Description"))
            for r in permission.get("IpRanges") or []
        ]
        sources += [
            ("IPv6", str(r.get("CidrIpv6")), r.get("Description"))
            for r in permission.get("Ipv6Ranges") or []
        ]
        sources += [
            ("", str(r.get("PrefixListId")), r.get("Description"))
            for r in permission.get("PrefixListIds") or []
        ]
        for pair in permission.get("UserIdGroupPairs") or []:
            group = str(pair.get("GroupId") or "")
            user = str(pair.get("UserId") or "")
            sources.append(
                (
                    "",
                    f"{user}/{group}" if user and user != owner else group,
                    pair.get("Description"),
                )
            )
        for version, peer, description in sources:
            rows.append(
                (
                    protocol,
                    low if isinstance(low, int) else -1,
                    ports,
                    version,
                    peer,
                    _clean(description),
                )
            )
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[4], r[5]))
    return ["\t".join((r[3], r[0], r[2], r[4], r[5])) for r in rows]


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["GroupId"]
    nid = node_id("security_group", owner, ctx.region, native)
    references: dict[tuple[str, str], set[str]] = {}
    self_referencing = False
    counts = {}
    # 한도 단위의 규칙 수(ADR 0011 D3 · #27 사용률). `counts` 는 **묶음(IpPermission) 수**라 한도와 단위가
    # 다르다 — AWS 는 CIDR · 참조 SG · 접두사 목록마다 1 로 세고, 방향 × 주소 계열(4칸)마다 따로 건다.
    # 참조 SG 는 계열을 말하지 않으므로 두 계열에 1씩 센다(적게 말하지 않는다). 실측(2026-10-04):
    # AWS/Usage `RulesPerSecurityGroup` = 이 4칸의 최대와 20/20 일치 — 다만 두 셈법(IPv4 에만 · 둘 다)을
    # 가를 SG(IPv6 규칙이 IPv4 보다 많은 SG)는 없었다 → 그 갈래는 미측정이다.
    # 접두사 목록 규칙은 그 목록의 최대 항목 수만큼 센다(문서) — 그 수를 수집하지 않으므로 개수만 남기고
    # 사용률 쪽이 「한도 ?」로 내린다.
    quota_rules: dict[str, int] = {}
    for direction, key in (
        ("ingress", "IpPermissions"),
        ("egress", "IpPermissionsEgress"),
    ):
        permissions = item.get(key) or []
        counts[direction] = len(permissions)
        refs = sum(len(p.get("UserIdGroupPairs") or []) for p in permissions)
        quota_rules[f"{direction}_ipv4_rule_count"] = refs + sum(
            len(p.get("IpRanges") or []) for p in permissions
        )
        quota_rules[f"{direction}_ipv6_rule_count"] = refs + sum(
            len(p.get("Ipv6Ranges") or []) for p in permissions
        )
        quota_rules[f"{direction}_prefix_list_rule_count"] = sum(
            len(p.get("PrefixListIds") or []) for p in permissions
        )
        for permission in permissions:
            for pair in permission.get("UserIdGroupPairs") or []:
                group = pair.get("GroupId")
                if not group:
                    continue
                if str(group) == native:
                    # 자기참조는 엣지로 만들지 않는다(자기 루프) — 속성으로 남긴다(ADR 0002 §6).
                    self_referencing = True
                    continue
                references.setdefault((direction, str(group)), set()).add(
                    _rule_label(permission)
                )
    edges = [
        EdgeDraft(
            "security_group_reference",
            nid,
            group,
            "security_group",
            True,
            {"direction": direction, "rules": sorted(rules)},
            disc=direction,
        )
        for (direction, group), rules in references.items()
    ]
    ingress_rows = _rule_rows(item.get("IpPermissions") or [], owner)
    egress_rows = _rule_rows(item.get("IpPermissionsEgress") or [], owner)
    node = Node(
        id=nid,
        type="security_group",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name") or item.get("GroupName"),
        attributes={
            "group_name": item.get("GroupName"),
            "description": item.get("Description"),
            "ingress_rule_count": counts["ingress"],
            "egress_rule_count": counts["egress"],
            # 콘솔 표 그대로의 규칙 줄(소스 하나 = 한 줄 · `_rule_rows`) — 위 두 수는 묶음 수라 콘솔 수와 다르다
            "ingress_rules": ingress_rows,
            "egress_rules": egress_rows,
            "ingress_rule_entry_count": len(ingress_rows),
            "egress_rule_entry_count": len(egress_rows),
            **dict(sorted(quota_rules.items())),
            "self_referencing": self_referencing,
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
