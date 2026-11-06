"""elasticache_cluster(ElastiCache 캐시 클러스터) 수집 — 「이음매 2」 · #114 · 스키마 2.9.0.

**카드 하나 = 캐시 클러스터 하나**(`DescribeCacheClusters` 의 항목). Redis·Valkey 는 클러스터 하나가 노드 하나이고
(복제 그룹의 주·복제 노드가 각자 클러스터다), Memcached 는 클러스터 하나에 노드 여럿이다. RDS 인스턴스와 같은 단위다 —
복제 그룹은 카드가 아니라 각 카드의 속성(`replication_group_*`)이다(Aurora 클러스터가 카드인 것과 다르다: 복제 그룹에는
클러스터 엔드포인트 외에 자기 네트워크 자리가 없고, 그 엔드포인트는 주 노드의 IP 를 가리킨다).

🔴 **응답에 서브넷이 없다**(RDS 와 같다). 클러스터는 AZ(노드마다 `CustomerAvailabilityZone` · 단일 노드면
`PreferredAvailabilityZone`)와 서브넷 그룹 **이름**만 준다. 그래서 서브넷 그룹을 한 번 더 읽고(`DescribeCacheSubnetGroups` ·
리전마다 1회) 이렇게 정한다:

1. 노드가 사는 AZ 마다 그룹 안의 같은 AZ 서브넷이 **정확히 하나**면 그 서브넷이다 — ElastiCache 는 서브넷 그룹 밖에 노드를
   두지 않으므로 연역이다. 그 서브넷이 하나면 부모 = 그 서브넷 · 여럿이면(다중 AZ Memcached) 부모 = VPC + `subnet_ids`(걸침).
2. 어느 AZ 하나라도 후보가 둘 이상이면 서브넷을 지어내지 않는다 — 부모 = VPC(서브넷 그룹의 `VpcId`) · `subnet_ids` 없음.
3. 서브넷 그룹을 못 읽었으면 VPC 도 모른다 → 부모 = 리전 + 사유(「일부 못 읽음」 — 렌더러는 자리를 짓지 않고 잔차로 신고한다).

후보 목록은 `subnet_group_subnet_ids` 로 싣는다(`rds_db_instance.py` 와 같은 이유로 `subnet_ids` 와 이름을 가른다).

🔴 **상태는 담지 않는다.** `CacheClusterStatus` 는 상태 탐침(`state`)으로만 나간다. 복제 그룹의 주/복제 **역할**
(`CurrentRole`)도 싣지 않는다 — 장애 조치마다 뒤집혀 사람이 아무것도 안 바꿨는데 구조가 바뀐다(불변식 ②).
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
)
from .base import sorted_strs as _sorted

SPEC = Spec(
    "elasticache",
    "describe_cache_clusters",
    "CacheClusters",
    "elasticache:DescribeCacheClusters",
    params=(("ShowCacheNodeInfo", True),),
)
_GROUPS_API = "elasticache:DescribeCacheSubnetGroups"
_RG_API = "elasticache:DescribeReplicationGroups"
# 엔진이 덧붙인 것을 빌더에 넘기는 payload 키 — AWS 응답 키와 섞이지 않게 밑줄로 시작한다.
_GROUP = "_SubnetGroup"
_RG = "_ReplicationGroup"


def _zones(item: dict[str, Any]) -> list[str]:
    """노드가 사는 AZ(정렬). 노드 정보가 있으면 노드에서, 없으면 단일 노드 클러스터의 선호 AZ."""
    nodes = item.get("CacheNodes") or []
    found = {
        str(n["CustomerAvailabilityZone"])
        for n in nodes
        if n.get("CustomerAvailabilityZone")
    }
    if found:
        return sorted(found)
    pref = item.get("PreferredAvailabilityZone")
    # 다중 AZ Memcached 는 `Multiple` 을 준다 — AZ 이름이 아니다
    return [str(pref)] if pref and pref != "Multiple" else []


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """서브넷 그룹·복제 그룹을 **리전마다 한 번씩** 읽어 항목에 붙인다. 거부는 그 정보가 필요한 항목의 사유로만 남는다.

    🔴 항목을 버리지 않는다 — 목록이 성공했으므로 「캐시가 있다」는 확정이다(불변식 ④).
    """
    need_groups = any(f.payload.get("CacheSubnetGroupName") for f in items)
    need_rgs = any(f.payload.get("ReplicationGroupId") for f in items)
    groups: dict[str, dict[str, Any]] | Reason = {}
    rgs: dict[str, dict[str, Any]] | Reason = {}
    if need_groups:
        try:
            groups = {
                str(g["CacheSubnetGroupName"]): g
                for g in pages(
                    client, "describe_cache_subnet_groups", "CacheSubnetGroups"
                )
                if g.get("CacheSubnetGroupName")
            }
        except (ClientError, BotoCoreError) as exc:
            groups = classify(exc, _GROUPS_API)[1]
    if need_rgs:
        try:
            rgs = {
                str(g["ReplicationGroupId"]): g
                for g in pages(
                    client, "describe_replication_groups", "ReplicationGroups"
                )
                if g.get("ReplicationGroupId")
            }
        except (ClientError, BotoCoreError) as exc:
            rgs = classify(exc, _RG_API)[1]
    out: list[Fetched] = []
    for f in items:
        payload = dict(f.payload)
        reasons = f.reasons
        name = payload.get("CacheSubnetGroupName")
        if name:
            if isinstance(groups, Reason):
                reasons = reasons + (groups,)
            elif str(name) in groups:
                payload[_GROUP] = groups[str(name)]
        rg = payload.get("ReplicationGroupId")
        if rg:
            if isinstance(rgs, Reason):
                reasons = reasons + (rgs,)
            elif str(rg) in rgs:
                payload[_RG] = rgs[str(rg)]
        out.append(Fetched(payload, reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # ElastiCache API 는 계정-로컬이라 소유자를 주지 않는다 → 조회 계정이 소유자다(RDS 와 같다).
    owner = ctx.account_id
    native = str(item["CacheClusterId"])
    zones = _zones(item)
    group = item.get(_GROUP)
    candidates = (group or {}).get("Subnets") or []
    by_zone: dict[str, set[str]] = {}
    for s in candidates:
        sid = s.get("SubnetIdentifier")
        az = (s.get("SubnetAvailabilityZone") or {}).get("Name")
        if sid and az:
            by_zone.setdefault(str(az), set()).add(str(sid))
    picked = [by_zone.get(z, set()) for z in zones]
    resolved = bool(zones) and all(len(p) == 1 for p in picked)
    subnets = sorted({next(iter(p)) for p in picked}) if resolved else []

    parents: list[str] = []
    parent_types: list[str] = []
    attributes: dict[str, Any] = {
        "engine": item.get("Engine"),
        "engine_version": item.get("EngineVersion"),
        "cache_node_type": item.get("CacheNodeType"),
        "num_cache_nodes": item.get("NumCacheNodes"),
        "availability_zones": zones,
        "subnet_group_name": item.get("CacheSubnetGroupName"),
        "security_group_ids": _sorted(
            g.get("SecurityGroupId") for g in item.get("SecurityGroups") or []
        ),
        "transit_encryption_enabled": item.get("TransitEncryptionEnabled"),
        "at_rest_encryption_enabled": item.get("AtRestEncryptionEnabled"),
    }
    port = (item.get("ConfigurationEndpoint") or {}).get("Port") or next(
        (
            (n.get("Endpoint") or {}).get("Port")
            for n in item.get("CacheNodes") or []
            if (n.get("Endpoint") or {}).get("Port")
        ),
        None,
    )
    if port is not None:
        attributes["port"] = port
    if group is not None:
        # 서브넷 그룹을 읽었다 — 후보 목록 키는 읽었을 때만 만든다(못 읽음 ≠ 빈 그룹)
        attributes["subnet_group_subnet_ids"] = _sorted(
            s.get("SubnetIdentifier") for s in candidates
        )
        if len(subnets) == 1:
            parents.append(node_id("subnet", owner, ctx.region, subnets[0]))
            parent_types.append("subnet")
        elif len(subnets) > 1:
            attributes["subnet_ids"] = subnets
        if group.get("VpcId"):
            parents.append(node_id("vpc", owner, ctx.region, str(group["VpcId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")
    if item.get("ReplicationGroupId"):
        attributes["replication_group_id"] = item["ReplicationGroupId"]
    rg = item.get(_RG)
    if rg is not None:
        attributes["replication_group_member_ids"] = _sorted(
            rg.get("MemberClusters") or []
        )
        attributes["replication_group_cluster_mode"] = rg.get("ClusterEnabled")
        attributes["replication_group_multi_az"] = rg.get("MultiAZ")
        attributes["replication_group_automatic_failover"] = rg.get("AutomaticFailover")

    node = Node(
        id=node_id("elasticache_cluster", owner, ctx.region, native),
        type="elasticache_cluster",
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
    """상태 탐침 — 상태 단 전용(`Service.state`). 구조 속성에는 싣지 않는다(머리말)."""
    value = item.get("CacheClusterStatus")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
