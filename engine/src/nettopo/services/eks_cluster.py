"""eks_cluster(EKS 클러스터) 수집 — 목록→상세 2단 호출.

EKS 는 1차 12종·EC2·ASG 와 두 가지가 다르다:

1. **목록이 이름 문자열을 준다.** `list_clusters` 는 dict 가 아니라 클러스터 **이름** 목록을
   돌려준다(`{"clusters": ["app-eks", ...]}`). 그래서 enrich 이전의 `Fetched.payload` 는
   문자열이다 — 다른 수집기의 dict payload 와 다르다. `describe_cluster` 로 상세를 채운다.
2. **상세가 단일 객체다.** `describe_cluster` 는 리스트가 아니라 `{"cluster": {...}}` 하나를
   주므로 `pages()`(응답 키 아래가 리스트라고 가정)로 못 읽는다 — enrich 에서 직접 부른다.

🔴 **상태는 담지 않는다.** `status`·`health.issues`·노드그룹 헬스는 시시각각 바뀌므로
`topology.json` 에 넣으면 불변식 ②가 깨진다(LB 타깃 헬스와 같은 이유, ADR 0006 관측 계약).
여기서는 **접근 경로를 결정하는 구성**만 싣는다: endpointPublicAccess/endpointPrivateAccess/
publicAccessCidrs(Topology_2.md §5 필수) + version·subnet_ids·security_group_ids.

🔴 **노드그룹은 노드로 만들지 않는다.** Topology_2.md §5 는 노드그룹을 §4 ASG 그룹에 병합
표현하기로 했고(그 병합은 렌더러 어댑터의 일), 노드그룹 스코프 자체가 미결이다(미팅 합의 필요).
그래서 클러스터 노드만 만들고 `ListNodegroups`/`DescribeNodegroup` 후속 호출은 하지 않는다.

🔴 **엣지는 만들지 않는다.** 클러스터의 위치는 부모(VPC→리전)와 `subnet_ids` 속성으로만
표현한다 — ASG 와 같다(여러 서브넷에 걸치므로 단일 서브넷 부모를 지어내지 않는다).
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("eks", "list_clusters", "clusters", "eks:ListClusters")

_DESCRIBE_API = "eks:DescribeCluster"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """이름 하나마다 `describe_cluster` 로 구성을 채운다. 거부는 그 클러스터의 사유로만 남는다.

    목록(`list_clusters`)이 성공했으므로 「클러스터가 있다」는 확정이다 — describe 가 거부돼도
    항목을 버리지 않는다(버리면 「클러스터가 없다」가 되어 거짓이다, 불변식 ④). 이름만 든 payload 를
    남기면 `build` 가 리전 밑에 부분 노드로 세우고 `collect` 가 사유를 붙인다.
    """
    out: list[Fetched] = []
    for item in items:
        # 🔴 이 payload 는 문자열(클러스터 이름)이다 — list_clusters 가 dict 가 아니라 이름을 준다.
        name = item.payload
        try:
            cluster = client.describe_cluster(name=name)["cluster"]
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(
                    payload={"name": name},
                    reasons=item.reasons + (classify(exc, _DESCRIBE_API)[1],),
                )
            )
            continue
        out.append(Fetched(payload=cluster, reasons=item.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["name"]
    nid = node_id("eks_cluster", owner, ctx.region, native)

    # describe 가 성공해야 resourcesVpcConfig 가 있다. 실패했으면(이름만 있는 payload) 구성 키를
    # 만들지 않는다 — `[]`/None 을 채우면 「없음」과 「못 읽음」이 같아진다(LB 리스너와 같은 규율).
    vpc_config = item.get("resourcesVpcConfig")
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if vpc_config is not None:
        attributes = {
            "version": item.get("version"),
            "endpoint_public_access": vpc_config.get("endpointPublicAccess"),
            "endpoint_private_access": vpc_config.get("endpointPrivateAccess"),
            "public_access_cidrs": _sorted(vpc_config.get("publicAccessCidrs") or []),
            "subnet_ids": _sorted(vpc_config.get("subnetIds") or []),
            "security_group_ids": _sorted(vpc_config.get("securityGroupIds") or []),
            "cluster_security_group_id": vpc_config.get("clusterSecurityGroupId"),
        }
        vpc_id = vpc_config.get("vpcId")
        if vpc_id:
            parents.append(node_id("vpc", owner, ctx.region, str(vpc_id)))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=nid,
        type="eks_cluster",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("arn"),
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
    """상태 탐침 — 상태 단 전용(`Service.state`). `DescribeCluster` 가 거부되면(이름만) `None`."""
    value = item.get("status")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
