"""ec2_instance(EC2 인스턴스) 수집 — 「이음매 2」.

파일명이 `ec2.py` 가 아니라 **수집 종류**(`ec2_instance`)인 이유는 `services/__init__.py`
머리말에 있다: EC2 한 서비스가 종류 11개를 낳으므로 파일 단위를 AWS 서비스로 잡을 수 없다.
boto3 서비스 이름과 IAM 접두는 둘 다 `ec2` 라 겹쳐 보이지만, 파일이 소유하는 「종류」는
그 서비스가 낳는 11종 중 하나(인스턴스)다. 다음 종류(ENI·볼륨 등)는 각자 파일이 된다.

🔴 **`describe_instances` 는 인스턴스를 예약(Reservation)으로 감싸 돌려준다.** 목록 호출의
result_key 는 `Reservations` 이므로 그대로 두면 빌더가 예약 하나를 노드 하나로 착각한다
(예약 하나에 인스턴스가 여럿일 수 있다). 그래서 `enrich` 가 예약을 인스턴스로 **평탄화**한다 —
후속 AWS 호출이 아니라 응답 모양을 펴는 용도다. `collect.py` 는 `item_count` 를 **목록 호출
기준**(= 예약 수)으로 적는다(그 파일 226행 주석) — 노드 수와 어긋날 수 있으나 coverage 는
「목록 호출이 성공했나」를 말하는 자리이지 노드를 세는 자리가 아니다.

🔴 **헬스·사용률은 구성이 아니다.** CPU·네트워크처럼 시시각각 변하는 값과 자동할당 퍼블릭 IP 는
담지 않는다 — 불변식 ②(관측 계약 `schema/observation.schema.json`, ADR 0006 소관). 인스턴스의
존재·타입·상태·배치·사설 IP 같은 **구성**만 싣는다. `state`(running/stopped)는 서서히 바뀌는
값이라 다른 수집기(subnet·nat_gateway)의 `state` 와 같은 취급으로 남긴다.

🔴 **타깃/피어로 가는 엣지는 만들지 않는다.** 인스턴스의 위치는 부모 체인(서브넷→VPC→리전)으로만
표현한다. LB→인스턴스 같은 엣지를 새로 만들면 끝점이 없는 매달린 참조가 된다
(`load_balancer.py` 가 타깃 그룹을 속성으로만 남기는 것과 같은 이유).
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, draft, gone, tags_of
from .base import sorted_strs as _sorted

SPEC = Spec("ec2", "describe_instances", "Reservations", "ec2:DescribeInstances")


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """예약을 인스턴스로 평탄화한다. AWS 를 더 부르지 않는다 — 응답 모양만 편다.

    `OwnerId` 는 예약에만 있고 인스턴스 payload 에는 없다 → 각 인스턴스에 실어 내려
    빌더가 `ctx.attribute` 로 하는 공유 자원 귀속을 다른 수집기와 똑같이 굴린다.
    항목을 지어내거나 버리지 않는다: 인스턴스가 0개인 예약은 노드를 안 만들 뿐이다.
    """
    out: list[Fetched] = []
    for reservation in items:
        owner = reservation.payload.get("OwnerId")
        for instance in reservation.payload.get("Instances") or []:
            payload = dict(instance)
            if owner is not None and "OwnerId" not in payload:
                payload["OwnerId"] = owner
            out.append(Fetched(payload=payload, reasons=reservation.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("ec2_instance", (item.get("State") or {}).get("Name")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["InstanceId"]
    # 부모 후보를 **있는 것만** 쌓는다(nat_gateway 와 같은 이유) — SubnetId 없는 인스턴스
    # (종료 중·EC2-Classic 잔재)에 없는 id 를 체인에 넣으면 3패스가 리전으로도 못 내려간다.
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("SubnetId"):
        parents.append(node_id("subnet", owner, ctx.region, str(item["SubnetId"])))
        parent_types.append("subnet")
    if item.get("VpcId"):
        parents.append(node_id("vpc", owner, ctx.region, str(item["VpcId"])))
        parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    node = Node(
        id=node_id("ec2_instance", owner, ctx.region, native),
        type="ec2_instance",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "instance_type": item.get("InstanceType"),
            "state": (item.get("State") or {}).get("Name"),
            "availability_zone": (item.get("Placement") or {}).get("AvailabilityZone"),
            "private_ip_address": item.get("PrivateIpAddress"),
            "security_group_ids": _sorted(
                g.get("GroupId") for g in item.get("SecurityGroups") or []
            ),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
