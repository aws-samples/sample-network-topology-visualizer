"""VPC 수집 — 「이음매 2」.

부모는 리전이다(VPC 는 리전 자원이다). 공유 VPC(RAM)는 `ctx.attribute` 가 소유자로 귀속시켜
자원 하나가 두 레인으로 갈라지지 않게 한다 — ADR 0002 §2.

🔵 **콘솔 VPC 「세부 정보」 그대로**(스키마 2.13.0 · 정보창 개편 사용자 결정 2026-10-24 「콘솔이 보여 주는 값은 우리도 보여 준다」):
- 목록 응답 그대로 — `ipv6_cidr_blocks` · `instance_tenancy` · `dhcp_options_id`. 🔴 `cidr_blocks` 는 **연결된(associated) CIDR 만** 싣는다 —
  떼어낸 보조 CIDR(`disassociated`)이 응답에 한동안 남는다. 콘솔 「IPv4 CIDR」 은 연결된 것만 보여 준다.
- 후속 호출 둘(그 쌍에 VPC 가 있을 때만):
  - `DescribeDhcpOptions` 한 번 → `dhcp_options` = 「<키> <값,…>」(그 VPC 의 옵션 세트 · 키 순). 거부 = 모든 VPC 에 사유(키 없음).
  - `DescribeVpcAttribute` **VPC 마다 2번**(`enableDnsSupport`·`enableDnsHostnames` — 목록 응답에 없다 · 페이지네이터 없음) →
    `enable_dns_support` · `enable_dns_hostnames`(불린). 🔴 거부·스로틀 = **그 VPC 만** 사유(키 없음) — 「꺼짐(false)」으로 적으면 DNS 를 끈 VPC 로
    읽힌다(콘솔 「DNS 확인 · DNS 호스트 이름」 칸 · 불변식 ④).
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
# (요청 `Attribute` 값, 응답 키, 속성 이름)
_DNS_ATTRIBUTES = (
    ("enableDnsSupport", "EnableDnsSupport", "enable_dns_support"),
    ("enableDnsHostnames", "EnableDnsHostnames", "enable_dns_hostnames"),
)
# 떼어낸 CIDR 상태 — 연결된 CIDR 로 그리지 않는다(`CidrBlockState.State`)
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
                # 두 속성이 같은 까닭으로 거부되면 사유는 하나(정보창 「!」 줄이 같은 말을 두 번 하지 않게)
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
    # 계약상 비면 안 된다(스키마 allOf — 렌더러가 VPC 를 그린다) → 기본 CIDR 로 내려간다
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
        # 렌더러가 VPC 를 그리려면 cidr_blocks 가 반드시 필요하다(스키마 allOf).
        attributes={
            "cidr_blocks": _cidrs(item),
            "state": item.get("State"),
            "is_default": bool(item.get("IsDefault", False)),
            # 2.13.0 — 콘솔 「세부 정보」(머리말). 응답에 없는 값은 키를 만들지 않는다
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
