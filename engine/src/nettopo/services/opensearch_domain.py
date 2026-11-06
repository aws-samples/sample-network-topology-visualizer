"""opensearch_domain(OpenSearch Service 도메인) 수집 — 「이음매 2」 · #114 · 스키마 2.9.0.

**카드 하나 = 도메인 하나.** 목록(`ListDomainNames`)은 이름만 주고 네트워크 구성은 `DescribeDomains`(한 번에 5개까지)가
준다 — 거부되면 이름만 든 부분 노드 + 사유(SageMaker 도메인과 같은 규율 · 「VPC 밖이다」로 지어내지 않는다).

자리는 구성이 말한다(SageMaker Studio 도메인 · EKS 와 같은 모양):

- **VPC 도메인**(`VPCOptions` 가 있다) = 부모 VPC(`VPCId`) + 걸친 서브넷 `subnet_ids`(`SubnetIds` — 영역 인식이면 AZ 마다 하나).
  데이터 노드 ENI 는 그 서브넷들에 선다(공식 문서 「VPC 지원」 — 데이터 노드마다 ENI 하나).
- **공개 도메인**(`VPCOptions` 가 없다 · 상세는 읽었다) = 부모 리전 + `subnet_ids: []`(읽었고 비었다) — 고객 VPC 안에 ENI 가 없다.
  렌더러는 리전 줄에 세운다(§17.12 ① · 「서브넷을 정하지 않는 구성」).

🔴 **상태는 담지 않는다.** `Processing`·`UpgradeProcessing`·`DomainProcessingStatus` 는 상태 탐침(`state`)으로만 나간다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("opensearch", "list_domain_names", "DomainNames", "es:ListDomainNames")
_DESCRIBE_API = "es:DescribeDomains"
# `DescribeDomains` 는 한 번에 5개까지 받는다(API 참조 `DomainNames` 최대 5) — 넘기면 ValidationException 으로 전부를 잃는다.
_BATCH = 5


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """이름 5개씩 `describe_domains` 로 채운다. 거부된 묶음의 도메인은 이름만 든 부분 노드 + 사유(버리지 않는다)."""
    names = sorted(
        {str(f.payload["DomainName"]) for f in items if f.payload.get("DomainName")}
    )
    details: dict[str, dict[str, Any]] = {}
    failed: dict[str, Any] = {}
    for i in range(0, len(names), _BATCH):
        chunk = names[i : i + _BATCH]
        try:
            got = (
                client.describe_domains(DomainNames=chunk).get("DomainStatusList") or []
            )
        except (ClientError, BotoCoreError) as exc:
            reason = classify(exc, _DESCRIBE_API)[1]
            for n in chunk:
                failed[n] = reason
            continue
        for d in got:
            if d.get("DomainName"):
                details[str(d["DomainName"])] = d
    out: list[Fetched] = []
    for f in items:
        name = str(f.payload.get("DomainName") or "")
        if name in details:
            out.append(
                Fetched({**f.payload, **details[name], "_described": True}, f.reasons)
            )
        elif name in failed:
            out.append(
                Fetched({**f.payload, "_described": False}, f.reasons + (failed[name],))
            )
        else:
            # 목록엔 있는데 상세 응답에 없다(그 사이 지워졌다) — 자리를 모른다 · 지어내지 않는다
            out.append(Fetched({**f.payload, "_described": False}, f.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["DomainName"])
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("_described"):
        cluster = item.get("ClusterConfig") or {}
        vpc = item.get("VPCOptions") or {}
        endpoints = item.get("Endpoints") or {}
        attributes = {
            "engine_version": item.get("EngineVersion"),
            "instance_type": cluster.get("InstanceType"),
            "instance_count": cluster.get("InstanceCount"),
            "dedicated_master_enabled": cluster.get("DedicatedMasterEnabled"),
            "zone_awareness_enabled": cluster.get("ZoneAwarenessEnabled"),
            "endpoint": endpoints.get("vpc") or item.get("Endpoint"),
            "encryption_at_rest_enabled": (
                item.get("EncryptionAtRestOptions") or {}
            ).get("Enabled"),
            "node_to_node_encryption_enabled": (
                item.get("NodeToNodeEncryptionOptions") or {}
            ).get("Enabled"),
            "enforce_https": (item.get("DomainEndpointOptions") or {}).get(
                "EnforceHTTPS"
            ),
            # 읽었다 — VPC 도메인이면 그 서브넷들, 공개 도메인이면 비었다(못 읽음 ≠ 빈 목록 · 못 읽었으면 키가 없다)
            "subnet_ids": _sorted(vpc.get("SubnetIds") or []),
            "availability_zones": _sorted(vpc.get("AvailabilityZones") or []),
            "security_group_ids": _sorted(vpc.get("SecurityGroupIds") or []),
        }
        if vpc.get("VPCId"):
            parents.append(node_id("vpc", owner, ctx.region, str(vpc["VPCId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=node_id("opensearch_domain", owner, ctx.region, native),
        type="opensearch_domain",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("ARN"),
        name=native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — `DomainProcessingStatus` 가 있으면 그것, 없으면(옛 엔진 응답) 불리언 셋으로 접는다. 상세를 못 읽었으면 `None`."""
    if not item.get("_described"):
        return None
    value = item.get("DomainProcessingStatus")
    if value:
        return str(value)
    if item.get("Deleted"):
        return "Deleting"
    if item.get("Created") is False:
        return "Creating"
    return "Modifying" if item.get("Processing") else "Active"


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
