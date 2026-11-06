"""sagemaker_domain(SageMaker Studio 도메인) 수집 — 「이음매 2」 · W2.

도메인은 VPC 하나와 서브넷 여러 개에 걸친다(EKS 와 같은 모양) → 부모는 VPC, 걸친 서브넷은
`subnet_ids`. 목록(`list_domains`)에는 네트워크 구성이 없어 `describe_domain` 으로 채운다 — 거부되면
이름만 든 부분 노드 + 사유.

`app_network_access_type` = `PublicInternetOnly` 면 Studio 앱이 SageMaker 관리 망으로 인터넷에 나가고,
`VpcOnly` 면 이 VPC 의 경로(NAT·엔드포인트)만 탄다 — 접근 경로를 가르는 구성이다.

🔴 **상태는 담지 않는다.** `Status`(InService/Updating…)는 불변식 ② 소관 밖이다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("sagemaker", "list_domains", "Domains", "sagemaker:ListDomains")

_DESCRIBE_API = "sagemaker:DescribeDomain"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for item in items:
        domain_id = item.payload.get("DomainId")
        try:
            detail = client.describe_domain(DomainId=domain_id)
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(
                    payload={**item.payload, "_described": False},
                    reasons=item.reasons + (classify(exc, _DESCRIBE_API)[1],),
                )
            )
            continue
        out.append(
            Fetched(
                payload={**item.payload, **detail, "_described": True},
                reasons=item.reasons,
            )
        )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["DomainId"]
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("_described"):
        settings = item.get("DefaultUserSettings") or {}
        attributes = {
            "app_network_access_type": item.get("AppNetworkAccessType"),
            "subnet_ids": _sorted(item.get("SubnetIds") or []),
            "security_group_ids": _sorted(settings.get("SecurityGroups") or []),
        }
        if item.get("VpcId"):
            parents.append(node_id("vpc", owner, ctx.region, str(item["VpcId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("sagemaker_domain", owner, ctx.region, native),
        type="sagemaker_domain",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DomainArn"),
        name=item.get("DomainName"),
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — 상태 단 전용(`Service.state`). 목록 응답에도 있는 값이라 상세가 거부돼도 읽힌다."""
    value = item.get("Status")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
