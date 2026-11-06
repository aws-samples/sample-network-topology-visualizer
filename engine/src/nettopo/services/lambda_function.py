"""lambda_function(Lambda 함수) 수집 — 「이음매 2」 · 2.8.0(사용자 결정 2026-10-20 P2-6).

2.7.0 까지 Lambda 함수는 **파생 노드**였다 — LB 대상 그룹이 함수 ARN 을 말할 때만 이름·계정·리전으로 지었고
(`nettopo/lb_targets.py`), VPC 에 붙은 함수인지 몰라 리전 자원 줄에 섰다(§17.27 「Lambda」). 이 수집기가
`lambda:ListFunctions` 의 `VpcConfig` 로 **그 함수가 걸친 서브넷**을 알려 주면 화면이 그 서브넷 칸에 세운다(ECS 서비스와
같은 `spans` 규칙 · §17.12). LB 대상 해소는 같은 id(`lambda_function/<계정>/<리전>/<함수 이름>`)를 쓰므로 수집한 노드가
있으면 파생 노드를 짓지 않는다 — 수집 범위에서 이 종류를 뺐거나 거부되면 예전처럼 파생 노드다.

🔵 **ENI 주인**: VPC 함수의 ENI(`InterfaceType` = `lambda` · 설명 `AWS Lambda VPC ENI-<함수 이름>-…`)는 같은 서브넷·보안 그룹
조합의 **함수끼리 공유**된다(Hyperplane). 그래서 ENI 주인을 정할 때는 서브넷·보안 그룹이 맞는 수집한 함수가 **하나**일 때만
그 함수로 잇는다(`lb_targets._eni_owner`) — 둘 이상이면 고르지 않는다(`ambiguous`).

🔴 **구성만 싣는다.** `LastModified`(wall-clock)·`CodeSha256`·`Version`·`State`(ListFunctions 에는 대개 없다)는 배포마다
바뀌는 값이라 담지 않는다(불변식 ②). 런타임은 사람이 정한 설정이라 담는다.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft
from .base import sorted_strs as _sorted

SPEC = Spec("lambda", "list_functions", "Functions", "lambda:ListFunctions")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # Lambda API 는 계정-로컬이다 — 조회 계정이 소유자다(ASG·CloudFront 와 같다).
    owner = ctx.account_id
    name = str(item["FunctionName"])
    vpc_cfg = item.get("VpcConfig") or {}
    vpc_id = str(vpc_cfg.get("VpcId") or "")
    parents: list[str] = []
    parent_types: list[str] = []
    if vpc_id:
        parents.append(node_id("vpc", owner, ctx.region, vpc_id))
        parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("lambda_function", owner, ctx.region, name),
        type="lambda_function",
        account_id=owner,
        region=ctx.region,
        native_id=name,
        arn=item.get("FunctionArn"),
        name=name,
        attributes={
            "function_name": name,
            "runtime": item.get("Runtime"),
            # VPC 에 붙지 않은 함수 = `[]`(읽었고 없다 — 리전 자원 줄) · 붙은 함수 = 그 서브넷들(정렬)
            "subnet_ids": _sorted(vpc_cfg.get("SubnetIds") or []),
            "security_group_ids": _sorted(vpc_cfg.get("SecurityGroupIds") or []),
        },
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


SERVICE = Service(spec=SPEC, build=build)
