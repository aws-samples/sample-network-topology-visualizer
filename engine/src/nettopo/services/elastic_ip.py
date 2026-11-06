"""elastic_ip(탄력적 IP) 수집 — 「이음매 2」 · 배치 D(스키마 2.10.0 · ADR 0002 §12-2 · 사용자 결정 2026-10-20 · target-gap #12).

「이 퍼블릭 IP 는 누구 것인가」 — 선이 아니라 **주인의 정보창 줄**이다(`docs/canvas-principles.md` §17.34). 노드 하나 = 할당 하나
(id 의 native = `AllocationId`) · 부모 = 리전(EIP 는 VPC·서브넷에 살지 않는다 — 연결이 바뀌어도 할당은 그대로다).

주인 찾기(3패스 · `nettopo/relations.py` `eip_owners`):
- `InstanceId` 가 있으면 그 EC2(인스턴스의 주 ENI 에 붙은 것 · 응답에 ENI 도 같이 온다 — ENI 는 묻지 않는다).
- 없고 `NetworkInterfaceId` 만 있으면 그 ENI 를 **이 쌍에서** `DescribeNetworkInterfaces`(`network-interface-id` 필터 · 100개씩)로 묻고
  ENI 의 주인을 LB IP 대상과 **같은 규칙**(`lb_targets._eni_owner`)으로 찾는다 + NAT 게이트웨이(ENI 종류 `nat_gateway` · 설명의 `nat-…`).
  서비스 관리형 EIP(`ServiceManaged` = `alb`·`nlb`·`rds` — AWS 가 그 서비스에 붙인 퍼블릭 IP)도 같은 길이다(실측 2026-10-20 us-east-1).
- 연결이 없으면(`AssociationId` 없음) 주인이 없다 — 화면은 그리지 않고 「!」 목록에도 세우지 않는다(AWS 쪽 사실 · 사용자 결정 2026-10-21 오전 · §17.34).

🔴 ENI 응답은 구조에 싣지 않는다(`NodeDraft.private`) — 주인을 가르는 재료일 뿐이다. 🔴 ENI 조회 거부 = 그 EIP 들의 사유(불변식 ④ · 주인 「읽지 못함」).
🔴 구성만 싣는다 — `AssociationId`(연결할 때마다 바뀌는 토큰)·`CarrierIp` 는 싣지 않는다. 퍼블릭 IP 는 할당의 고정 사실이라 싣는다(정보창에만 · 첫 화면 0 · §17.2).
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

SPEC = Spec("ec2", "describe_addresses", "Addresses", "ec2:DescribeAddresses")
ENI_API = "ec2:DescribeNetworkInterfaces"
_ENI = "_Eni"
_CHUNK = 100


def _eni_wanted(item: dict[str, Any]) -> str | None:
    """ENI 를 물어야 주인을 아는 EIP 의 ENI id — 인스턴스에 붙었으면 묻지 않는다."""
    eni = item.get("NetworkInterfaceId")
    return str(eni) if eni and not item.get("InstanceId") else None


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    want = sorted({e for it in items if (e := _eni_wanted(it.payload))})
    if not want:
        return items
    got: list[dict[str, Any]] = []
    try:
        for i in range(0, len(want), _CHUNK):
            got.extend(
                pages(
                    client,
                    "describe_network_interfaces",
                    "NetworkInterfaces",
                    Filters=[
                        {"Name": "network-interface-id", "Values": want[i : i + _CHUNK]}
                    ],
                )
            )
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, ENI_API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,))
            if _eni_wanted(it.payload)
            else it
            for it in items
        ]
    by = {
        str(e.get("NetworkInterfaceId")): e for e in got if e.get("NetworkInterfaceId")
    }
    out: list[Fetched] = []
    for it in items:
        eid = _eni_wanted(it.payload)
        if eid and eid in by:
            out.append(Fetched({**it.payload, _ENI: by[eid]}, it.reasons))
        else:
            out.append(it)
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    # EC2-Classic EIP 는 할당 id 가 없었다(지금은 없다) — 그때만 퍼블릭 IP 를 id 로 쓴다
    native = str(item.get("AllocationId") or item.get("PublicIp"))
    nid = node_id("elastic_ip", ctx.account_id, ctx.region, native)
    associated = bool(
        item.get("AssociationId")
        or item.get("InstanceId")
        or item.get("NetworkInterfaceId")
    )
    attributes: dict[str, Any] = {
        "public_ip": item.get("PublicIp"),
        "domain": item.get("Domain"),
        "network_border_group": item.get("NetworkBorderGroup"),
        "public_ipv4_pool": item.get("PublicIpv4Pool"),
        "associated": associated,
    }
    for key, attr in (
        ("ServiceManaged", "service_managed"),
        ("InstanceId", "instance_id"),
        ("NetworkInterfaceId", "network_interface_id"),
        ("PrivateIpAddress", "private_ip_address"),
    ):
        if item.get(key):
            attributes[attr] = str(item[key])
    node = Node(
        id=nid,
        type="elastic_ip",
        account_id=ctx.account_id,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes=attributes,
        tags=tags,
    )
    eni = item.get(_ENI)
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(),),
        parent_types=("region",),
        private={"eni": eni} if eni else None,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
