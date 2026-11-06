"""sagemaker_endpoint(SageMaker 추론 엔드포인트) 수집 — 「이음매 2」 · W2.

🔴 **엔드포인트의 네트워크 위치는 엔드포인트에 없다.** 사슬을 세 번 따라가야 한다:
`describe_endpoint`(설정 이름) → `describe_endpoint_config`(변형마다 모델 이름) →
`describe_model`(`VpcConfig.Subnets`·`SecurityGroupIds`). 모델이 여럿이면 서브넷·SG 를 합친다.

- 사슬 어디서든 거부되면 그 엔드포인트에 사유를 달고 `subnet_ids`·`security_group_ids` 키를
  **만들지 않는다**. 일부 모델만 읽고 합친 서브넷을 싣으면 「이 서브넷에만 있다」가 되어 거짓이다.
- 모든 모델을 읽었고 VpcConfig 가 하나도 없으면 `subnet_ids` = `[]` — VPC 밖 엔드포인트라는
  **확인된** 사실이다(화면은 「위치를 못 정한 리소스」로 보낸다).

부모는 리전이다(ECS·ASG 와 같다) — 여러 서브넷에 걸치고 VpcId 가 응답에 없어 어댑터가 서브넷으로
VPC 를 찾는다. 🔴 **상태는 담지 않는다.** `EndpointStatus`·인스턴스 수는 불변식 ② 소관 밖이다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("sagemaker", "list_endpoints", "Endpoints", "sagemaker:ListEndpoints")


def _network(client: Any, name: str) -> tuple[dict[str, Any], Reason | None]:
    """엔드포인트 하나의 사슬. (덧붙일 키, 실패 사유). 실패면 키는 비어 있다."""
    try:
        endpoint = client.describe_endpoint(EndpointName=name)
    except (ClientError, BotoCoreError) as exc:
        return {}, classify(exc, "sagemaker:DescribeEndpoint")[1]
    config_name = endpoint.get("EndpointConfigName")
    got: dict[str, Any] = {"_config_name": config_name}
    try:
        config = client.describe_endpoint_config(EndpointConfigName=config_name)
    except (ClientError, BotoCoreError) as exc:
        return got, classify(exc, "sagemaker:DescribeEndpointConfig")[1]
    variants = list(config.get("ProductionVariants") or []) + list(
        config.get("ShadowProductionVariants") or []
    )
    models = _sorted(v.get("ModelName") for v in variants)
    got["_model_names"] = models
    subnets: set[str] = set()
    groups: set[str] = set()
    for model in models:
        try:
            detail = client.describe_model(ModelName=model)
        except (ClientError, BotoCoreError) as exc:
            return got, classify(exc, "sagemaker:DescribeModel")[1]
        vpc = detail.get("VpcConfig") or {}
        subnets.update(str(s) for s in vpc.get("Subnets") or [])
        groups.update(str(g) for g in vpc.get("SecurityGroupIds") or [])
    got["_subnet_ids"] = sorted(subnets)
    got["_security_group_ids"] = sorted(groups)
    return got, None


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for item in items:
        got, reason = _network(client, str(item.payload.get("EndpointName")))
        reasons = item.reasons + ((reason,) if reason else ())
        out.append(Fetched(payload={**item.payload, **got}, reasons=reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["EndpointName"]
    attributes: dict[str, Any] = {}
    if item.get("_config_name"):
        attributes["endpoint_config_name"] = item["_config_name"]
    if "_model_names" in item:
        attributes["model_names"] = item["_model_names"]
    if "_subnet_ids" in item:
        attributes["subnet_ids"] = item["_subnet_ids"]
        attributes["security_group_ids"] = item["_security_group_ids"]
    node = Node(
        id=node_id("sagemaker_endpoint", owner, ctx.region, native),
        type="sagemaker_endpoint",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("EndpointArn"),
        name=native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — 상태 단 전용(`Service.state`). 목록 응답에도 있는 값이라 상세가 거부돼도 읽힌다."""
    value = item.get("EndpointStatus")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
