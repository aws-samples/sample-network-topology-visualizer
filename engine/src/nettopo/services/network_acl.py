"""Collect network ACL rules and subnet associations.

Store rules in node attributes. Record known associations without adding
state, since the association response does not contain it.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, EdgeDraft, NodeDraft, Service, Spec, draft, tags_of

SPEC = Spec("ec2", "describe_network_acls", "NetworkAcls", "ec2:DescribeNetworkAcls")


_DEFAULT_RULE_NUMBER = 32767


def _entries(item: dict[str, Any], *, egress: bool) -> int:
    return sum(
        1
        for e in item.get("Entries") or []
        if bool(e.get("Egress")) is egress
        and e.get("RuleNumber") != _DEFAULT_RULE_NUMBER
    )


def _entry_rows(item: dict[str, Any], *, egress: bool) -> list[str]:
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
            "ingress_entry_count": _entries(item, egress=False),
            "egress_entry_count": _entries(item, egress=True),
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
