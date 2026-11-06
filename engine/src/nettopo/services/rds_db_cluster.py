"""rds_db_cluster(Aurora · RDS 다중 AZ DB 클러스터) 수집 — 「이음매 2」 · #114 · 스키마 2.9.0.

**카드 하나 = DB 클러스터 하나**(`DescribeDBClusters` 의 항목). 클러스터는 자기 ENI 가 없다 — 쓰기·읽기 엔드포인트(DNS)가
멤버 인스턴스의 IP 를 가리킬 뿐이고, 멤버 인스턴스는 이미 `rds_db_instance` 카드다(제 서브넷 칸). 그래서 클러스터 카드는
**VPC 에 산다**(어느 서브넷이라고 말할 근거가 없다 — 멤버가 여러 AZ 에 흩어지고 장애 조치로 쓰기 쪽이 옮긴다).
멤버는 `member_instance_ids`(정렬)로 잇는다 — 렌더러가 정보창 「멤버 인스턴스」 칸에서 그 카드를 누르게 한다.

🔴 **응답에 VPC 가 없다.** 클러스터는 서브넷 그룹 **이름**만 준다(`DBSubnetGroup` = 문자열 · 인스턴스 응답과 모양이 다르다).
그래서 서브넷 그룹을 리전마다 한 번 읽는다(`DescribeDBSubnetGroups`). 못 읽으면 VPC 를 모른다 → 부모 = 리전 + 사유
(렌더러는 자리를 짓지 않고 잔차로 신고한다).

🔴 **상태는 담지 않는다.** `Status` 는 탐침(`state`)으로만 나간다. 멤버의 쓰기/읽기 **역할**(`IsClusterWriter`)도 싣지 않는다 —
장애 조치마다 뒤집혀 사람이 아무것도 안 바꿨는데 구조가 바뀐다(불변식 ②). 멤버 **목록**은 구성이다(인스턴스를 더하고 빼는 것은 사람의 일).
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

SPEC = Spec("rds", "describe_db_clusters", "DBClusters", "rds:DescribeDBClusters")
_GROUPS_API = "rds:DescribeDBSubnetGroups"
_GROUP = "_SubnetGroup"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """서브넷 그룹을 **리전마다 한 번** 읽어 붙인다. 거부는 서브넷 그룹을 가진 클러스터의 사유로만 남는다(항목은 그대로)."""
    if not any(f.payload.get("DBSubnetGroup") for f in items):
        return items
    try:
        groups: dict[str, dict[str, Any]] | Reason = {
            str(g["DBSubnetGroupName"]): g
            for g in pages(client, "describe_db_subnet_groups", "DBSubnetGroups")
            if g.get("DBSubnetGroupName")
        }
    except (ClientError, BotoCoreError) as exc:
        groups = classify(exc, _GROUPS_API)[1]
    out: list[Fetched] = []
    for f in items:
        name = f.payload.get("DBSubnetGroup")
        if not name:
            out.append(f)
        elif isinstance(groups, Reason):
            out.append(Fetched(f.payload, f.reasons + (groups,)))
        elif str(name) in groups:
            out.append(Fetched({**f.payload, _GROUP: groups[str(name)]}, f.reasons))
        else:
            out.append(
                f
            )  # 그룹이 목록에 없다(지워진 이름) — 지어내지 않는다 · VPC 를 모른다
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # RDS API 는 계정-로컬이다 → 조회 계정이 소유자다(`rds_db_instance.py` 와 같다).
    owner = ctx.account_id
    native = str(item["DBClusterIdentifier"])
    group = item.get(_GROUP)
    parents: list[str] = []
    parent_types: list[str] = []
    attributes: dict[str, Any] = {
        "engine": item.get("Engine"),
        "engine_version": item.get("EngineVersion"),
        "engine_mode": item.get("EngineMode"),
        "endpoint": item.get("Endpoint"),
        "reader_endpoint": item.get("ReaderEndpoint"),
        "port": item.get("Port"),
        "multi_az": item.get("MultiAZ"),
        "availability_zones": _sorted(item.get("AvailabilityZones") or []),
        "member_instance_ids": _sorted(
            m.get("DBInstanceIdentifier") for m in item.get("DBClusterMembers") or []
        ),
        "subnet_group_name": item.get("DBSubnetGroup"),
        "security_group_ids": _sorted(
            g.get("VpcSecurityGroupId") for g in item.get("VpcSecurityGroups") or []
        ),
        "storage_encrypted": item.get("StorageEncrypted"),
    }
    sv2 = item.get("ServerlessV2ScalingConfiguration")
    if sv2:
        # Serverless v2 용량 범위(ACU) — 구성이다(사람이 정한 최소·최대). 지금 쓰는 ACU 는 지표 몫이다(상태 금지)
        attributes["serverless_v2_min_capacity"] = sv2.get("MinCapacity")
        attributes["serverless_v2_max_capacity"] = sv2.get("MaxCapacity")
    if "PubliclyAccessible" in item:
        # 다중 AZ DB 클러스터만 준다(Aurora 는 인스턴스 단위) — 없으면 키도 없다(「아니오」를 지어내지 않는다)
        attributes["publicly_accessible"] = item["PubliclyAccessible"]
    if group is not None:
        attributes["subnet_group_subnet_ids"] = _sorted(
            s.get("SubnetIdentifier") for s in group.get("Subnets") or []
        )
        if group.get("VpcId"):
            parents.append(node_id("vpc", owner, ctx.region, str(group["VpcId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=node_id("rds_db_cluster", owner, ctx.region, native),
        type="rds_db_cluster",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DBClusterArn"),
        name=native,
        attributes=attributes,
        tags=tags_of(item, "TagList"),
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — 상태 단 전용(`Service.state`)."""
    value = item.get("Status")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
