"""Collect VPC configuration, associated CIDRs, DHCP options, and DNS
settings.

On DNS query failure, omit the attribute and record its reason in
partial_reasons. Do not treat failure as disabled DNS.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import (
    Ctx,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    pages,
    tags_of,
)
from .base import sorted_strs as _sorted

SPEC = Spec("ec2", "describe_vpcs", "Vpcs", "ec2:DescribeVpcs")
DHCP_API = "ec2:DescribeDhcpOptions"
ATTRIBUTE_API = "ec2:DescribeVpcAttribute"
_DHCP = "_DhcpOptions"
_DNS = "_Dns"
_DNS_ATTRIBUTES = (
    ("enableDnsSupport", "EnableDnsSupport", "enable_dns_support"),
    ("enableDnsHostnames", "EnableDnsHostnames", "enable_dns_hostnames"),
)
_CIDR_GONE = frozenset({"disassociated", "disassociating", "failed", "failing"})


def _dhcp_lines(options: dict[str, Any]) -> list[str]:
    lines = []
    for cfg in options.get("DhcpConfigurations") or []:
        key = cfg.get("Key")
        if not key:
            continue
        values = [
            str(v.get("Value")) for v in cfg.get("Values") or [] if v.get("Value")
        ]
        lines.append(f"{key} {','.join(values) or '-'}")
    return sorted(lines)


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query additional VPC DHCP options and DNS attributes.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    if not items:
        return items
    dhcp: dict[str, list[str]] | None = None
    dhcp_reason = None
    try:
        got = pages(client, "describe_dhcp_options", "DhcpOptions")
        dhcp = {
            str(o["DhcpOptionsId"]): _dhcp_lines(o)
            for o in got
            if o.get("DhcpOptionsId")
        }
    except (ClientError, BotoCoreError) as exc:
        dhcp_reason = classify(exc, DHCP_API)[1]
    out: list[Fetched] = []
    for it in items:
        payload = dict(it.payload)
        reasons = list(it.reasons)
        if dhcp_reason is not None:
            reasons.append(dhcp_reason)
        elif dhcp is not None and payload.get("DhcpOptionsId") in dhcp:
            payload[_DHCP] = dhcp[str(payload["DhcpOptionsId"])]
        dns: dict[str, bool] = {}
        for attribute, key, attr in _DNS_ATTRIBUTES:
            try:
                answer = client.describe_vpc_attribute(
                    VpcId=payload["VpcId"], Attribute=attribute
                )
            except (ClientError, BotoCoreError) as exc:
                reason = classify(exc, ATTRIBUTE_API)[1]
                if not any(
                    (r.code, r.api) == (reason.code, reason.api) for r in reasons
                ):
                    reasons.append(reason)
                continue
            value = (answer.get(key) or {}).get("Value")
            if value is not None:
                dns[attr] = bool(value)
        payload[_DNS] = dns
        out.append(Fetched(payload, tuple(reasons)))
    return out


def _cidrs(item: dict[str, Any]) -> list[str]:
    associations = item.get("CidrBlockAssociationSet") or [
        {"CidrBlock": item.get("CidrBlock")}
    ]
    kept = _sorted(
        a.get("CidrBlock")
        for a in associations
        if str((a.get("CidrBlockState") or {}).get("State") or "").lower()
        not in _CIDR_GONE
    )
    return kept or _sorted([item.get("CidrBlock")])


def _ipv6(item: dict[str, Any]) -> list[str]:
    return _sorted(
        a.get("Ipv6CidrBlock")
        for a in item.get("Ipv6CidrBlockAssociationSet") or []
        if str((a.get("Ipv6CidrBlockState") or {}).get("State") or "").lower()
        not in _CIDR_GONE
    )


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    v6 = _ipv6(item)
    node = Node(
        id=node_id("vpc", owner, ctx.region, item["VpcId"]),
        type="vpc",
        account_id=owner,
        region=ctx.region,
        native_id=item["VpcId"],
        name=tags.get("Name"),
        attributes={
            "cidr_blocks": _cidrs(item),
            "state": item.get("State"),
            "is_default": bool(item.get("IsDefault", False)),
            **({"ipv6_cidr_blocks": v6} if v6 else {}),
            **(
                {"instance_tenancy": str(item["InstanceTenancy"])}
                if item.get("InstanceTenancy")
                else {}
            ),
            **(
                {"dhcp_options_id": str(item["DhcpOptionsId"])}
                if item.get("DhcpOptionsId")
                else {}
            ),
            **({"dhcp_options": item[_DHCP]} if item.get(_DHCP) is not None else {}),
            **(item.get(_DNS) or {}),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
