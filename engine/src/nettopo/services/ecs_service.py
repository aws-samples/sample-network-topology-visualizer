"""ecs_service(ECS 서비스) 수집 — 「이음매 2」 · W2.

🔴 **노드 단위는 서비스다(사용자 결정 2026-10-01).** 태스크는 배포·스케일마다 생기고 죽어 이력이
소음으로 덮이고(불변식 ② — 태스크 수는 상태다), 클러스터는 네트워크 위치가 없다(서브넷·SG 는
서비스의 `awsvpcConfiguration` 에 있다). 그래서 클러스터를 펴서 그 안의 서비스를 노드로 만든다.

호출 사슬: `list_clusters`(목록) → 클러스터마다 `list_services` → 10개씩 `describe_services`.

- 🔴 `list_services` 가 거부되면 그 클러스터에 서비스가 몇 개인지 **모른다** — 사유를 달 항목이 없다.
  건너뛰면 「서비스 없음」이 되므로 `ListingFailed` 로 종류 전체를 실패로 적는다(base.py).
- `describe_services` 가 거부되면 서비스 ARN 은 안다 → 이름만 든 부분 노드 + 사유(EKS 와 같은 규율).
  응답의 `failures[]`(경합으로 사라진 서비스 등)도 그 서비스의 사유로 남긴다.

🔴 **상태는 담지 않는다.** `runningCount`·`desiredCount`·`deployments`·`events`·`taskDefinition`
(배포마다 리비전이 바뀐다)은 싣지 않는다. 접근 경로를 정하는 구성만: 서브넷·SG·퍼블릭 IP·LB 타깃 그룹.

🔴 **부모는 리전이다(ASG 와 같다).** 서비스는 여러 서브넷에 걸치고 응답에 VpcId 가 없다 — 어댑터가
`subnet_ids` 로 VPC 를 찾는다. awsvpc 가 아닌 서비스(bridge/host)는 `subnet_ids` 가 `[]` 다(서브넷이
없다는 **확인된** 사실) → 화면은 그 서비스를 「위치를 못 정한 리소스」로 보낸다.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .base import (
    Ctx,
    Fetched,
    ListingFailed,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    gone,
)
from .base import pages as _pages
from .base import sorted_strs as _sorted

SPEC = Spec("ecs", "list_clusters", "clusterArns", "ecs:ListClusters")

_LIST_API = "ecs:ListServices"
_DESCRIBE_API = "ecs:DescribeServices"
# `describe_services` 는 한 번에 서비스 10개까지만 받는다(API 제한).
_BATCH = 10


def _last(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """클러스터 ARN 목록 → 서비스 목록. 항목 수가 바뀐다(EC2 의 예약 펴기와 같은 자리)."""
    out: list[Fetched] = []
    for item in items:
        # 🔴 이 payload 는 문자열(클러스터 ARN)이다 — list_clusters 가 dict 가 아니라 ARN 을 준다.
        cluster_arn = str(item.payload)
        try:
            service_arns = sorted(
                {
                    str(a)
                    for a in _pages(
                        client, "list_services", "serviceArns", cluster=cluster_arn
                    )
                }
            )
        except (ClientError, BotoCoreError) as exc:
            raise ListingFailed(exc, _LIST_API) from exc
        for start in range(0, len(service_arns), _BATCH):
            batch = service_arns[start : start + _BATCH]
            try:
                resp = client.describe_services(cluster=cluster_arn, services=batch)
            except (ClientError, BotoCoreError) as exc:
                reason = classify(exc, _DESCRIBE_API)[1]
                out.extend(
                    Fetched(
                        payload={"serviceArn": arn, "clusterArn": cluster_arn},
                        reasons=item.reasons + (reason,),
                    )
                    for arn in batch
                )
                continue
            described = {
                str(s.get("serviceArn")): s for s in resp.get("services") or []
            }
            failed = {str(f.get("arn")): f for f in resp.get("failures") or []}
            for arn in batch:
                if arn in described:
                    payload = dict(described[arn])
                    payload.setdefault("clusterArn", cluster_arn)
                    out.append(Fetched(payload=payload, reasons=item.reasons))
                    continue
                # 목록엔 있었는데 상세가 없다 — 버리면 「없음」, 지어내면 거짓이다 → 사유를 단다.
                f = failed.get(arn) or {}
                message = " ".join(
                    str(p) for p in (f.get("reason"), f.get("detail")) if p
                )
                out.append(
                    Fetched(
                        payload={"serviceArn": arn, "clusterArn": cluster_arn},
                        reasons=item.reasons
                        + (
                            Reason(
                                code="api_error",
                                api=_DESCRIBE_API,
                                message=message or "describe_services 응답에 없다",
                            ),
                        ),
                    )
                )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("ecs_service", item.get("status")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    owner = ctx.account_id
    service_arn = str(item["serviceArn"])
    cluster_name = _last(str(item.get("clusterArn") or ""))
    service_name = str(item.get("serviceName") or _last(service_arn))
    # 서비스 이름은 클러스터 안에서만 유일하다 → 클러스터를 앞에 붙인다. `/` 는 노드 id 의 구분자라
    # id 자리에서는 인코딩한다(ASG 의 `_id_segment` 와 같은 이유 · native_id 에는 원문).
    native = f"{cluster_name}/{service_name}"
    attributes: dict[str, Any] = {"cluster_name": cluster_name}
    # describe 가 성공했을 때만 구성 키를 만든다 — 실패한 부분 노드에 `[]` 를 채우면 「서브넷 없음」이
    # 되어 거짓이다(EKS 의 resourcesVpcConfig 와 같은 규율). `serviceName` 은 describe 에만 있다.
    if "serviceName" in item:
        awsvpc = (item.get("networkConfiguration") or {}).get(
            "awsvpcConfiguration"
        ) or {}
        attributes.update(
            {
                "launch_type": item.get("launchType"),
                "capacity_providers": _sorted(
                    s.get("capacityProvider")
                    for s in item.get("capacityProviderStrategy") or []
                ),
                "subnet_ids": _sorted(awsvpc.get("subnets") or []),
                "security_group_ids": _sorted(awsvpc.get("securityGroups") or []),
                "assign_public_ip": awsvpc.get("assignPublicIp"),
                "target_group_arns": _sorted(
                    lb.get("targetGroupArn") for lb in item.get("loadBalancers") or []
                ),
            }
        )
    node = Node(
        id=node_id("ecs_service", owner, ctx.region, quote(native, safe="")),
        type="ecs_service",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=service_arn,
        name=service_name,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — `status` 와 태스크 수를 **한 값으로 접는다**(ADR 0006 §O7 `ecs_service` 행).

    `ACTIVE` 하나로는 「서비스는 있는데 태스크가 0개」를 못 가른다 — 그것이 ECS 에서 가장 흔한 장애다.
    `DescribeServices` 가 거부된 부분 노드는 수가 없다 → `None`(지어내지 않는다).
    """
    status = str(item.get("status") or "").lower()
    if not status:
        return None
    if status != "active":
        return status
    running, desired = item.get("runningCount"), item.get("desiredCount")
    if not isinstance(running, int) or not isinstance(desired, int):
        return None
    if desired == 0:
        return "active-desired-zero"
    if running == 0:
        return "active-no-running-tasks"
    if running < desired:
        return "active-under-desired"
    return "active"


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
