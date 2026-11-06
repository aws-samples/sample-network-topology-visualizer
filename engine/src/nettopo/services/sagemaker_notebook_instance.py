"""sagemaker_notebook_instance(SageMaker 노트북 인스턴스) 수집 — 「이음매 2」 · W2.

목록(`list_notebook_instances`)에는 네트워크 구성이 없다 → 노트북마다 `describe_notebook_instance`
로 서브넷·SG·인터넷 직접 접근을 채운다. 거부되면 이름만 든 부분 노드 + 사유(EKS 와 같은 규율).

🔴 **응답에 VpcId 가 없다.** 서브넷이 있으면 그 서브넷이 부모이고(서브넷 노드가 VPC 를 안다), 없으면
(VPC 밖 노트북) 리전이다. `direct_internet_access` = `Enabled` 면 SageMaker 가 관리하는 망으로 인터넷에
**직접** 나간다 — VPC 의 IGW·NAT 를 거치지 않는 길이므로 접근 경로의 핵심 구성이다.

🔴 **상태는 담지 않는다.** `NotebookInstanceStatus`(InService/Stopped…)는 시시각각 바뀐다(불변식 ②).
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec(
    "sagemaker",
    "list_notebook_instances",
    "NotebookInstances",
    "sagemaker:ListNotebookInstances",
)

_DESCRIBE_API = "sagemaker:DescribeNotebookInstance"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for item in items:
        name = item.payload.get("NotebookInstanceName")
        try:
            detail = client.describe_notebook_instance(NotebookInstanceName=name)
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
    native = item["NotebookInstanceName"]
    attributes: dict[str, Any] = {"instance_type": item.get("InstanceType")}
    parents: list[str] = []
    parent_types: list[str] = []
    # describe 가 실패했으면 네트워크 키를 만들지 않는다 — 「못 읽음」을 「VPC 밖」으로 그리면 거짓이다.
    if item.get("_described"):
        attributes.update(
            {
                "direct_internet_access": item.get("DirectInternetAccess"),
                "security_group_ids": _sorted(item.get("SecurityGroups") or []),
            }
        )
        if item.get("SubnetId"):
            parents.append(node_id("subnet", owner, ctx.region, str(item["SubnetId"])))
            parent_types.append("subnet")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("sagemaker_notebook_instance", owner, ctx.region, native),
        type="sagemaker_notebook_instance",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("NotebookInstanceArn"),
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
    """상태 탐침 — 상태 단 전용(`Service.state`). 목록 응답에도 있는 값이라 상세가 거부돼도 읽힌다."""
    value = item.get("NotebookInstanceStatus")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
