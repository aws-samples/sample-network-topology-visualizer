"""Collect security group rules and references to other groups.

Use ingress or egress as the link's disc to distinguish rule direction.
Record self-references in self_referencing rather than as relationships.
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
    return " ".join(str(text or "").split())


def _rule_rows(permissions: list[dict[str, Any]], owner: str) -> list[str]:
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
