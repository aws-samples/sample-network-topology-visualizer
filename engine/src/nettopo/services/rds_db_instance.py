"""rds_db_instance(RDS DB 인스턴스) 수집 — 「이음매 2」 · W2.

🔴 **응답에 서브넷이 없다.** `describe_db_instances` 는 인스턴스가 사는 AZ(`AvailabilityZone`)와
그 인스턴스가 쓸 수 있는 서브넷 **후보**(`DBSubnetGroup.Subnets`, 보통 AZ 마다 하나)를 줄 뿐,
실제로 어느 서브넷에 ENI 가 있는지는 주지 않는다. 그래서 부모를 이렇게 정한다:

1. 서브넷 그룹 안에서 AZ 가 인스턴스의 AZ 와 같은 서브넷이 **정확히 하나**면 그 서브넷 —
   RDS 는 서브넷 그룹 밖에 ENI 를 두지 않으므로 이것은 추측이 아니라 연역이다.
2. 둘 이상이면 **ENI 로 좁힌다**(W3② · `docs/canvas-principles.md` §17.12) — 아래 `join_enis`.
   좁히지 못하면(0 이거나 ENI 가 답을 하나로 안 준다) 서브넷을 지어내지 않고 VPC(`DBSubnetGroup.VpcId`) → 리전.

후보 목록은 `subnet_group_subnet_ids` 로 싣는다. 🔴 이름을 `subnet_ids` 로 하지 마라 — 렌더러
어댑터는 `subnet_ids` 를 「이 자원이 **걸친** 서브넷」으로 읽는다(EKS·ASG). 후보를 걸침으로 그리면
AZ 하나에 사는 인스턴스가 AZ 여섯 칸에 걸린 띠가 된다.

🔴 **상태는 담지 않는다.** `DBInstanceStatus`(available/backing-up/modifying…)는 시시각각 바뀌므로
불변식 ②를 깬다(ADR 0006 관측 계약 소관). 배치·접근 경로를 정하는 구성만 싣는다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
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

SPEC = Spec("rds", "describe_db_instances", "DBInstances", "rds:DescribeDBInstances")
ENI_API = "ec2:DescribeNetworkInterfaces"
# 엔진이 ENI 로 좁힌 서브넷을 빌더에 넘기는 payload 키 — AWS 응답 키와 섞이지 않게 밑줄로 시작한다.
_ENI_SUBNET = "_EniSubnetId"


def _in_az(item: dict[str, Any]) -> list[str]:
    """서브넷 그룹 중 인스턴스의 AZ 에 있는 서브넷(정렬)."""
    az = item.get("AvailabilityZone")
    return sorted(
        {
            str(s["SubnetIdentifier"])
            for s in (item.get("DBSubnetGroup") or {}).get("Subnets") or []
            if s.get("SubnetIdentifier")
            and (s.get("SubnetAvailabilityZone") or {}).get("Name") == az
        }
    )


def join_enis(
    client: Any, items: list[Fetched], failed: Reason | None
) -> list[Fetched]:
    """같은 AZ 후보가 둘 이상인 DB 만 ENI 로 좁힌다 — 나머지는 호출조차 하지 않는다.

    🔴 RDS ENI 에는 DB 이름이 없다(`Description = RDSNetworkInterface` · 태그 0 — 실측 2026-10-02).
    그래서 **그 VPC·AZ 에 걸친 DB 가 이 하나뿐**일 때만 그 VPC·AZ 의 RDS ENI 가 이 DB 의 것이라고 말할 수
    있다. 대기 복제본(`SecondaryAvailabilityZone`)도 그 AZ 에 ENI 를 둘 수 있으므로 같이 센다. ENI 가 후보
    서브넷 **둘 이상**에 있으면(RDS Proxy 등 같은 요청자의 다른 ENI) 고르지 않는다.
    """
    need = [
        i
        for i, f in enumerate(items)
        if len(_in_az(f.payload)) > 1
        and (f.payload.get("DBSubnetGroup") or {}).get("VpcId")
    ]
    if not need:
        return items
    out = list(items)
    if client is None:
        reason = failed or Reason("api_error", ENI_API, "EC2 클라이언트 없음")
        for i in need:
            out[i] = Fetched(out[i].payload, out[i].reasons + (reason,))
        return out
    # (VPC, AZ) → 그 자리에 걸친 DB 수. 목록 전체로 센다(좁힐 DB 만 세면 이웃 DB 를 놓친다).
    crowd: dict[tuple[str, str], int] = {}
    for f in items:
        vpc = (f.payload.get("DBSubnetGroup") or {}).get("VpcId")
        for az in {
            f.payload.get("AvailabilityZone"),
            f.payload.get("SecondaryAvailabilityZone"),
        }:
            if vpc and az:
                crowd[(vpc, az)] = crowd.get((vpc, az), 0) + 1
    asked: dict[tuple[str, str], list[dict[str, Any]] | Reason] = {}
    for i in need:
        payload = out[i].payload
        vpc = str(payload["DBSubnetGroup"]["VpcId"])
        az = str(payload["AvailabilityZone"])
        if (vpc, az) not in asked:
            try:
                asked[(vpc, az)] = pages(
                    client,
                    "describe_network_interfaces",
                    "NetworkInterfaces",
                    Filters=[
                        {"Name": "requester-id", "Values": ["amazon-rds"]},
                        {"Name": "vpc-id", "Values": [vpc]},
                        {"Name": "availability-zone", "Values": [az]},
                    ],
                )
            except (ClientError, BotoCoreError) as exc:
                asked[(vpc, az)] = classify(exc, ENI_API)[1]
        got = asked[(vpc, az)]
        if isinstance(got, Reason):
            # 좁히지 못했을 뿐 노드는 그대로다 — 「일부 못 읽음」으로 권한을 주면 칸이 정해진다고 말한다.
            out[i] = Fetched(payload, out[i].reasons + (got,))
            continue
        if crowd.get((vpc, az), 0) != 1:
            continue  # 이웃 DB 가 있다 — 어느 ENI 가 누구 것인지 모른다
        candidates = set(_in_az(payload))
        hit = {str(e["SubnetId"]) for e in got if str(e.get("SubnetId")) in candidates}
        if len(hit) == 1:
            out[i] = Fetched({**payload, _ENI_SUBNET: hit.pop()}, out[i].reasons)
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # RDS API 는 계정-로컬이라 OwnerId 를 주지 않는다 → 조회 계정이 소유자다(ASG 와 같다).
    owner = ctx.account_id
    native = item["DBInstanceIdentifier"]
    group = item.get("DBSubnetGroup") or {}
    candidates = group.get("Subnets") or []
    az = item.get("AvailabilityZone")
    in_az = _in_az(item)
    if az and len(in_az) > 1 and item.get(_ENI_SUBNET) in in_az:
        in_az = [str(item[_ENI_SUBNET])]  # ENI 가 하나로 좁혔다(`join_enis`)
    parents: list[str] = []
    parent_types: list[str] = []
    if az and len(in_az) == 1:
        parents.append(node_id("subnet", owner, ctx.region, in_az[0]))
        parent_types.append("subnet")
    if group.get("VpcId"):
        parents.append(node_id("vpc", owner, ctx.region, str(group["VpcId"])))
        parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    attributes: dict[str, Any] = {
        "engine": item.get("Engine"),
        "instance_class": item.get("DBInstanceClass"),
        "multi_az": item.get("MultiAZ"),
        "availability_zone": az,
        "publicly_accessible": item.get("PubliclyAccessible"),
        "subnet_group_name": group.get("DBSubnetGroupName"),
        "subnet_group_subnet_ids": _sorted(
            s.get("SubnetIdentifier") for s in candidates
        ),
        "security_group_ids": _sorted(
            g.get("VpcSecurityGroupId") for g in item.get("VpcSecurityGroups") or []
        ),
    }
    # 있을 때만 싣는다 — Multi-AZ 가 아니면 보조 AZ 가 없고, 클러스터 소속이 아니면 클러스터가 없다.
    if item.get("SecondaryAvailabilityZone"):
        attributes["secondary_availability_zone"] = item["SecondaryAvailabilityZone"]
    if item.get("DBClusterIdentifier"):
        attributes["db_cluster_identifier"] = item["DBClusterIdentifier"]

    tags = tags_of(item, "TagList")
    node = Node(
        id=node_id("rds_db_instance", owner, ctx.region, native),
        type="rds_db_instance",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DBInstanceArn"),
        name=native,
        attributes=attributes,
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — 상태 단 전용(`Service.state`). 구조 속성에는 여전히 싣지 않는다(머리말)."""
    value = item.get("DBInstanceStatus")
    return str(value) if value else None


SERVICE = Service(
    spec=SPEC, build=build, state=state, peer_enrich=(("ec2", join_enis),)
)
