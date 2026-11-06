"""#114 — VPC 안 IP 를 쓰는 서비스 수집(스키마 2.9.0 · ADR 0013): ElastiCache · Aurora/RDS 클러스터 · OpenSearch · MSK.

각 종류마다 W2 와 같은 세 축을 잰다(`test_w2_services.py` 머리말):
① 어디에 사는가(부모 체인 — 서브넷을 지어내지 않는다) ② 무엇을 싣는가(속성 집합을 못박아 상태값 유입을 FAIL 로)
③ 후속 호출이 거부되면 무엇이 남는가(불변식 ④ — 키는 **없고** 사유가 붙는다, 빈 값으로 채우지 않는다).
그리고 상태 탐침(상태 단 전용)이 구조 문서에 새지 않는지와 관측 판정이 붙는지를 잰다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import fakes
import pytest
from fakes import (
    ACCOUNT,
    APP_SG,
    APP_VPC,
    CACHE_GROUP,
    CACHE_MEMCACHED,
    CACHE_PRIMARY,
    CACHE_REPLICA,
    CACHE_RG,
    CACHE_STATE_LEAKS,
    DB_CLUSTER,
    DB_CLUSTER_STATE_LEAKS,
    DB_SG,
    MSK_ARN,
    MSK_CLUSTER,
    MSK_ENIS,
    MSK_SERVERLESS,
    OS_DOMAIN,
    OS_PUBLIC,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    FakeAws,
    client_error,
    factory,
)
from jsonschema import Draft202012Validator
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation
from nettopo.services import elasticache_cluster as cache_module
from nettopo.services import msk_cluster as msk_module
from nettopo.services import opensearch_domain as os_module
from nettopo.services import rds_db_cluster as dbc_module
from test_services import SCHEMA_PATH, coverage_of, node_of, run

NEW_TYPES = (
    "elasticache_cluster",
    "rds_db_cluster",
    "opensearch_domain",
    "msk_cluster",
)
REGION_ID = f"region/{ACCOUNT}/{REGION}"
VPC_ID = f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
PUBLIC_ID = f"subnet/{ACCOUNT}/{REGION}/{PUBLIC_SUBNET}"
PRIVATE_ID = f"subnet/{ACCOUNT}/{REGION}/{PRIVATE_SUBNET}"
BOTH_SUBNETS = sorted([PRIVATE_SUBNET, PUBLIC_SUBNET])


def cache_id(name: str) -> str:
    return f"elasticache_cluster/{ACCOUNT}/{REGION}/{name}"


def _state_doc(client: FakeAws) -> tuple[dict[str, Any], dict[str, Any]]:
    """(구조 문서, 관측 문서) — 상태 단은 관측 종류만 `with_state=True` 로 다시 읽는다(제품 `nettopo observe` 와 같은 길)."""
    topology = run(client)
    state = collect(
        Scope((ACCOUNT,), (REGION,), OBSERVED_RESOURCE_TYPES),
        factory(client),
        clock=lambda: "2026-01-02T04:00:00Z",
        with_state=True,
    )
    obs, _ = build_observation(
        state, topology=topology, observed_at="2026-01-02T04:00:00Z", tool_version="t"
    )
    return topology, obs


# ------------------------------------------------------------------ 계약


def test_new_types_are_in_the_contract_and_the_document() -> None:
    """스키마 양쪽 enum + `SUPPORTED_RESOURCE_TYPES` + 관측 종류 + 실제 수집 + 스키마 통과 — 한쪽만 늘리면 깨진다."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    doc = run(FakeAws())
    drawn = {n["type"] for n in doc["nodes"]}
    for t in NEW_TYPES:
        assert t in schema["$defs"]["node_type"]["enum"], t
        assert t in schema["$defs"]["resource_type"]["enum"], t
        assert t in SUPPORTED_RESOURCE_TYPES, t
        assert t in OBSERVED_RESOURCE_TYPES, t
        assert t in drawn, f"{t} 가 산출물에 없다"
        assert coverage_of(doc, t)["status"] == "ok", t
    errors = Draft202012Validator(schema).iter_errors(doc)
    assert [f"{e.json_path}: {e.message}" for e in errors] == []


# -------------------------------------------------------------- ElastiCache


def test_cache_nodes_land_in_the_subnet_of_their_az() -> None:
    """복제 그룹의 두 노드 = 각자 자기 AZ 의 서브넷(그룹에 AZ 마다 하나) · 다중 AZ Memcached = VPC + 걸친 서브넷 둘."""
    doc = run(FakeAws())
    assert node_of(doc, cache_id(CACHE_PRIMARY))["parent_id"] == PUBLIC_ID
    assert node_of(doc, cache_id(CACHE_REPLICA))["parent_id"] == PRIVATE_ID
    mem = node_of(doc, cache_id(CACHE_MEMCACHED))
    assert mem["parent_id"] == VPC_ID
    assert mem["attributes"]["subnet_ids"] == BOTH_SUBNETS
    # 하나에 사는 노드는 걸침 키가 없다 — 렌더러가 띠로 그리지 않는다
    assert "subnet_ids" not in node_of(doc, cache_id(CACHE_PRIMARY))["attributes"]


def test_cache_attributes_are_config_only() -> None:
    """속성 집합을 못박는다 — 상태(`CacheClusterStatus`)·장애 조치 역할(`CurrentRole`)이 새면 FAIL."""
    doc = run(FakeAws())
    primary = node_of(doc, cache_id(CACHE_PRIMARY))
    assert primary["attributes"] == {
        "engine": "valkey",
        "engine_version": "7.2",
        "cache_node_type": "cache.t4g.micro",
        "num_cache_nodes": 1,
        "availability_zones": ["us-east-1a"],
        "subnet_group_name": CACHE_GROUP,
        "subnet_group_subnet_ids": BOTH_SUBNETS,
        "security_group_ids": [DB_SG],
        "transit_encryption_enabled": True,
        "at_rest_encryption_enabled": True,
        "port": 6379,
        "replication_group_id": CACHE_RG,
        "replication_group_member_ids": sorted([CACHE_PRIMARY, CACHE_REPLICA]),
        "replication_group_cluster_mode": False,
        "replication_group_multi_az": "enabled",
        "replication_group_automatic_failover": "enabled",
    }
    assert primary["arn"].endswith(f":cluster:{CACHE_PRIMARY}")
    text = json.dumps(doc)
    for leak in CACHE_STATE_LEAKS:
        assert f'"{leak}"' not in text, f"상태 값 {leak!r} 이 구조 문서에 샜다"
    # Memcached 는 복제 그룹이 없다 — 키도 없다(「복제 그룹 없음」을 지어내지 않는다)
    mem = node_of(doc, cache_id(CACHE_MEMCACHED))["attributes"]
    assert "replication_group_id" not in mem and mem["port"] == 11211
    assert mem["availability_zones"] == ["us-east-1a", "us-east-1b"]


def test_cache_list_call_asks_for_node_info() -> None:
    """🔴 `ShowCacheNodeInfo` 를 빼면 노드(=AZ)가 응답에 없다 — 다중 AZ Memcached 가 `Multiple` 만 남아 자리를 잃는다."""
    seen: list[dict[str, Any]] = []

    class Recording(FakeAws):
        def get_paginator(self, method: str) -> Any:
            inner = super().get_paginator(method)
            if method != "describe_cache_clusters":
                return inner

            class P:
                def paginate(self, **kwargs: Any) -> Any:
                    seen.append(kwargs)
                    return inner.paginate(**kwargs)

            return P()

    run(Recording())
    assert seen == [{"ShowCacheNodeInfo": True}]


def test_ambiguous_az_does_not_invent_a_subnet() -> None:
    """같은 AZ 후보가 둘이면 서브넷을 고르지 않는다 — VPC 그대로(걸침 키도 없다)."""
    # 🔴 둘째 후보도 **수집한** 서브넷이어야 한다 — 없는 서브넷을 고르면 3패스가 VPC 로 접어 줘서 이 검사가 실패할 수 없다
    second = "subnet-0fff0000aaaa0001"
    subnets = json.loads(json.dumps(fakes.SUBNETS))
    subnets.append(
        {**subnets[1], "SubnetId": second, "CidrBlock": "10.10.9.0/24", "Tags": []}
    )
    groups = json.loads(json.dumps(fakes.CACHE_SUBNET_GROUPS))
    groups[0]["Subnets"].append(
        {"SubnetIdentifier": second, "SubnetAvailabilityZone": {"Name": "us-east-1a"}}
    )
    doc = run(FakeAws(cache_subnet_groups=groups, subnets=subnets))
    assert any(
        n["id"].endswith(second) for n in doc["nodes"]
    )  # 둘째 후보가 정말 그려진다
    primary = node_of(doc, cache_id(CACHE_PRIMARY))
    assert primary["parent_id"] == VPC_ID
    assert "subnet_ids" not in primary["attributes"]
    # 다른 AZ 의 노드는 그대로 하나로 연역된다(한 노드의 모호함이 이웃을 끌어내리지 않는다)
    assert node_of(doc, cache_id(CACHE_REPLICA))["parent_id"] == PRIVATE_ID


def test_a_denied_subnet_group_read_keeps_the_caches_partial() -> None:
    """서브넷 그룹을 못 읽으면 VPC 도 모른다 — 리전 밑 부분 노드 + 사유. 후보 키는 **없다**(빈 그룹이 아니다)."""
    denied = client_error("AccessDenied", "DescribeCacheSubnetGroups")
    doc = run(FakeAws(errors={"describe_cache_subnet_groups": denied}))
    assert coverage_of(doc, "elasticache_cluster")["status"] == "ok"
    for name in (CACHE_PRIMARY, CACHE_REPLICA, CACHE_MEMCACHED):
        node = node_of(doc, cache_id(name))
        assert node["parent_id"] == REGION_ID
        assert node["partial"] is True
        assert [r["api"] for r in node["partial_reasons"]] == [
            "elasticache:DescribeCacheSubnetGroups"
        ]
        assert "subnet_group_subnet_ids" not in node["attributes"]
        assert node["attributes"]["subnet_group_name"] == CACHE_GROUP


def test_a_denied_replication_group_read_drops_only_its_keys() -> None:
    """복제 그룹을 못 읽어도 자리는 그대로다 — 복제 그룹 «상세» 키만 없고 그 노드만 부분이다(Memcached 는 묻지도 않았다)."""
    denied = client_error("AccessDenied", "DescribeReplicationGroups")
    doc = run(FakeAws(errors={"describe_replication_groups": denied}))
    primary = node_of(doc, cache_id(CACHE_PRIMARY))
    assert primary["parent_id"] == PUBLIC_ID
    assert primary["partial"] is True
    assert [r["api"] for r in primary["partial_reasons"]] == [
        "elasticache:DescribeReplicationGroups"
    ]
    assert primary["attributes"]["replication_group_id"] == CACHE_RG
    assert "replication_group_member_ids" not in primary["attributes"]
    assert node_of(doc, cache_id(CACHE_MEMCACHED))["partial"] is False


def test_a_denied_cache_listing_is_a_visible_hole() -> None:
    """목록이 거부되면 그 종류 칸이 `denied` 다 — 「캐시가 없다」(`ok`/0)와 다른 바이트(불변식 ④)."""
    denied = client_error("AccessDenied", "DescribeCacheClusters")
    doc = run(FakeAws(errors={"describe_cache_clusters": denied}))
    cov = coverage_of(doc, "elasticache_cluster")
    assert cov["status"] == "denied"
    assert cov["reason"]["api"] == "elasticache:DescribeCacheClusters"
    assert not [n for n in doc["nodes"] if n["type"] == "elasticache_cluster"]
    empty = run(FakeAws(elasticache_clusters=[]))
    assert coverage_of(empty, "elasticache_cluster")["status"] == "ok"


def test_cache_state_is_probe_only_and_judged() -> None:
    """상태는 상태 단에만 — 구조에는 없고, 관측은 `snapshotting` 을 `up`(연결을 받은 채 도는 값)으로 판정한다."""
    topology, obs = _state_doc(FakeAws())
    assert "state" not in node_of(topology, cache_id(CACHE_PRIMARY))["attributes"]
    row = next(
        o for o in obs["observations"] if o["target_id"] == cache_id(CACHE_PRIMARY)
    )
    assert row["status"] == "up", row
    assert row["evidence"]["api"] == "elasticache:DescribeCacheClusters"


@pytest.mark.parametrize(
    ("value", "status"),
    [("available", "up"), ("creating", "down"), ("rebooting cluster nodes", "down")],
)
def test_cache_state_vocabulary(value: str, status: str) -> None:
    clusters = json.loads(json.dumps(fakes.ELASTICACHE_CLUSTERS))
    for c in clusters:
        c["CacheClusterStatus"] = value
    _, obs = _state_doc(FakeAws(elasticache_clusters=clusters))
    row = next(
        o for o in obs["observations"] if o["target_id"] == cache_id(CACHE_PRIMARY)
    )
    assert row["status"] == status, row


def test_cache_module_reads_only_what_it_declares() -> None:
    """불변식 ① 의 서비스 축 — 이 파일이 부르는 API 는 `Describe*` 셋뿐이다(권한 문서·스택 정책이 이 이름을 그대로 쓴다)."""
    text = Path(str(cache_module.__file__)).read_text(encoding="utf-8")
    assert sorted(set(re.findall(r'"(elasticache:[A-Za-z]+)"', text))) == [
        "elasticache:DescribeCacheClusters",
        "elasticache:DescribeCacheSubnetGroups",
        "elasticache:DescribeReplicationGroups",
    ]


# -------------------------------------------------------------- Aurora / RDS DB 클러스터

DBC_ID = f"rds_db_cluster/{ACCOUNT}/{REGION}/{DB_CLUSTER}"


def test_db_cluster_lives_in_its_vpc_with_config_only() -> None:
    """클러스터는 자기 ENI 가 없다 → VPC 에 산다(서브넷을 고르지 않는다). 상태·멤버 역할이 새면 FAIL."""
    doc = run(FakeAws())
    node = node_of(doc, DBC_ID)
    assert node["parent_id"] == VPC_ID
    assert (
        node["attributes"]
        == {
            "engine": "aurora-postgresql",
            "engine_version": "16.4",
            "engine_mode": "provisioned",
            "endpoint": f"{DB_CLUSTER}.cluster-abc.{REGION}.rds.amazonaws.com",
            "reader_endpoint": f"{DB_CLUSTER}.cluster-ro-abc.{REGION}.rds.amazonaws.com",
            "port": 5432,
            "multi_az": False,
            "availability_zones": ["us-east-1a", "us-east-1b"],
            "member_instance_ids": [],  # 실측 모양 — 인스턴스 없는 클러스터(「멤버 0개」 ≠ 「못 읽음」)
            "subnet_group_name": "app-db-subnets",
            "subnet_group_subnet_ids": BOTH_SUBNETS,
            "security_group_ids": [DB_SG],
            "storage_encrypted": True,
            "serverless_v2_min_capacity": 0.5,
            "serverless_v2_max_capacity": 4.0,
        }
    )
    assert node["tags"] == {"team": "app"}
    text = json.dumps(node)
    for leak in DB_CLUSTER_STATE_LEAKS:
        assert f'"{leak}"' not in text


def test_db_cluster_members_are_listed_without_roles() -> None:
    """멤버 **목록**은 구성이다(정렬) · 쓰기/읽기 역할은 장애 조치마다 뒤집히므로 싣지 않는다."""
    clusters = json.loads(json.dumps(fakes.RDS_DB_CLUSTERS))
    clusters[0]["DBClusterMembers"] = [
        {"DBInstanceIdentifier": "app-aurora-2", "IsClusterWriter": False},
        {"DBInstanceIdentifier": "app-aurora-1", "IsClusterWriter": True},
    ]
    node = node_of(run(FakeAws(rds_clusters=clusters)), DBC_ID)
    assert node["attributes"]["member_instance_ids"] == ["app-aurora-1", "app-aurora-2"]
    assert "writer" not in json.dumps(node).lower()


def test_a_denied_db_subnet_group_read_keeps_the_cluster_partial() -> None:
    """서브넷 그룹을 못 읽으면 VPC 를 모른다 — 리전 밑 부분 노드 + 사유(렌더러는 자리를 짓지 않는다)."""
    denied = client_error("AccessDenied", "DescribeDBSubnetGroups")
    doc = run(FakeAws(errors={"describe_db_subnet_groups": denied}))
    node = node_of(doc, DBC_ID)
    assert node["parent_id"] == REGION_ID
    assert node["partial"] is True
    assert [r["api"] for r in node["partial_reasons"]] == ["rds:DescribeDBSubnetGroups"]
    assert "subnet_group_subnet_ids" not in node["attributes"]
    assert coverage_of(doc, "rds_db_cluster")["status"] == "ok"


def test_db_cluster_state_is_probe_only_and_judged() -> None:
    topology, obs = _state_doc(FakeAws())
    assert "state" not in node_of(topology, DBC_ID)["attributes"]
    row = next(o for o in obs["observations"] if o["target_id"] == DBC_ID)
    assert row["status"] == "up", row  # backing-up = 연결을 받은 채 돈다
    assert row["evidence"]["api"] == "rds:DescribeDBClusters"


def test_rds_eni_shared_by_two_clusters_is_not_guessed() -> None:
    """같은 AZ 의 DB 둘이 **다른** 클러스터면 그 RDS ENI 는 어느 쪽 것인지 모른다 — 고르지 않는다(지어내지 않는다)."""
    import lbt_world as W
    from test_lb_targets import _doc, _node, lb

    client = W.world()
    client._data["describe_db_instances"][1][2]["DBClusterIdentifier"] = "other-aurora"
    attrs = _node(_doc(client), lb("edge-nlb"))["attributes"]
    assert f"{W.AURORA_IP} rds" in attrs["unresolved_targets"]


def test_db_cluster_module_reads_only_what_it_declares() -> None:
    text = Path(str(dbc_module.__file__)).read_text(encoding="utf-8")
    assert sorted(set(re.findall(r'"(rds:[A-Za-z]+)"', text))) == [
        "rds:DescribeDBClusters",
        "rds:DescribeDBSubnetGroups",
    ]


# -------------------------------------------------------------- OpenSearch


def os_id(name: str) -> str:
    return f"opensearch_domain/{ACCOUNT}/{REGION}/{name}"


def test_vpc_domain_lives_in_its_vpc_across_its_subnets() -> None:
    """VPC 도메인 = 부모 VPC + 걸친 서브넷(EKS·Studio 도메인과 같은 모양) · 상태 불리언은 없다."""
    doc = run(FakeAws())
    node = node_of(doc, os_id(OS_DOMAIN))
    assert node["parent_id"] == VPC_ID
    assert node["attributes"] == {
        "engine_version": "OpenSearch_2.13",
        "instance_type": "t3.small.search",
        "instance_count": 2,
        "dedicated_master_enabled": False,
        "zone_awareness_enabled": True,
        "endpoint": f"vpc-{OS_DOMAIN}-abc.{REGION}.es.amazonaws.com",
        "encryption_at_rest_enabled": True,
        "node_to_node_encryption_enabled": True,
        "enforce_https": True,
        "subnet_ids": BOTH_SUBNETS,
        "availability_zones": ["us-east-1a", "us-east-1b"],
        "security_group_ids": [DB_SG],
    }
    assert "Processing" not in json.dumps(node) and "Active" not in json.dumps(node)


def test_public_domain_has_empty_subnets_not_missing() -> None:
    """공개 도메인 = 읽었고 서브넷이 비었다(`[]` — 리전 줄) · 「못 읽음」(키 없음)과 다른 바이트."""
    doc = run(FakeAws())
    node = node_of(doc, os_id(OS_PUBLIC))
    assert node["parent_id"] == REGION_ID
    assert node["attributes"]["subnet_ids"] == []
    assert node["attributes"]["endpoint"].startswith("search-")
    assert node["partial"] is False


def test_a_denied_describe_domains_keeps_name_only_partials() -> None:
    """상세가 거부되면 이름만 든 부분 노드 + 사유 · 네트워크 키는 **없다**(공개 도메인처럼 `[]` 로 적지 않는다)."""
    denied = client_error("AccessDenied", "DescribeDomains")
    doc = run(FakeAws(errors={"describe_domains": denied}))
    for name in (OS_DOMAIN, OS_PUBLIC):
        node = node_of(doc, os_id(name))
        assert node["parent_id"] == REGION_ID
        assert node["partial"] is True
        assert [r["api"] for r in node["partial_reasons"]] == ["es:DescribeDomains"]
        assert "subnet_ids" not in node["attributes"]
    assert coverage_of(doc, "opensearch_domain")["status"] == "ok"


def test_describe_domains_is_batched_by_five() -> None:
    """🔴 6개 이상이면 5개씩 나눠 묻는다 — 한 번에 넘기면 ValidationException 으로 **전부를** 잃는다."""
    names = [{"DomainName": f"d-{i:02d}", "EngineType": "OpenSearch"} for i in range(7)]
    details = {
        n["DomainName"]: {
            **fakes.OPENSEARCH_DETAILS[OS_PUBLIC],
            "DomainName": n["DomainName"],
        }
        for n in names
    }
    client = FakeAws(opensearch_domains=names, opensearch_details=details)
    doc = run(client)
    calls = [c for m, c in client.direct_calls if m == "describe_domains"]
    assert [len(c["DomainNames"]) for c in calls] == [5, 2]
    assert all(not node_of(doc, os_id(n["DomainName"]))["partial"] for n in names)


def test_opensearch_state_is_probe_only_and_judged() -> None:
    topology, obs = _state_doc(FakeAws())
    row = next(o for o in obs["observations"] if o["target_id"] == os_id(OS_DOMAIN))
    assert row["status"] == "up", row
    pub = next(o for o in obs["observations"] if o["target_id"] == os_id(OS_PUBLIC))
    assert pub["status"] == "up", (
        pub
    )  # Processing = Modifying(블루/그린) — 옛 노드가 받는다
    assert "state" not in node_of(topology, os_id(OS_DOMAIN))["attributes"]


def test_opensearch_module_reads_only_what_it_declares() -> None:
    text = Path(str(os_module.__file__)).read_text(encoding="utf-8")
    assert sorted(set(re.findall(r'"(es:[A-Za-z]+)"', text))) == [
        "es:DescribeDomains",
        "es:ListDomainNames",
    ]


# -------------------------------------------------------------- MSK


def msk_id(name: str) -> str:
    return f"msk_cluster/{ACCOUNT}/{REGION}/{name}"


def test_msk_cluster_lives_under_the_region_with_its_client_subnets() -> None:
    """MSK 응답에 VPC 가 없다 → 부모 = 리전 + 걸친 서브넷(ASG·ECS 와 같다 · 렌더러가 서브넷으로 VPC 를 찾는다) · 상태 없음."""
    doc = run(FakeAws())
    node = node_of(doc, msk_id(MSK_CLUSTER))
    assert node["parent_id"] == REGION_ID
    assert node["attributes"] == {
        "cluster_type": "PROVISIONED",
        "kafka_version": "3.6.0",
        "broker_instance_type": "kafka.t3.small",
        "number_of_broker_nodes": 2,
        "subnet_ids": BOTH_SUBNETS,
        "security_group_ids": [DB_SG],
        "client_broker_encryption": "TLS",
        "broker_eni_ids": sorted(MSK_ENIS),
    }
    assert node["arn"] == MSK_ARN and node["tags"] == {"team": "app"}
    assert '"UPDATING"' not in json.dumps(node)
    sls = node_of(doc, msk_id(MSK_SERVERLESS))
    assert sls["attributes"] == {
        "cluster_type": "SERVERLESS",
        "subnet_ids": BOTH_SUBNETS,
        "security_group_ids": [APP_SG],
    }


def test_list_nodes_runs_only_for_provisioned_clusters() -> None:
    """서버리스는 브로커를 보이지 않는다 — 묻지 않는다(묻으면 BadRequest 로 「일부 못 읽음」이 거짓으로 선다)."""
    client = FakeAws()
    run(client)
    assert client.calls.count("list_nodes") == 1


def test_a_denied_list_nodes_drops_only_the_broker_enis() -> None:
    """브로커를 못 읽으면 그 클러스터만 부분 — 구성의 서브넷은 그대로 · 브로커 ENI 키는 **없다**(「브로커 0개」가 아니다)."""
    denied = client_error("AccessDenied", "ListNodes")
    doc = run(FakeAws(followup_errors={("list_nodes", MSK_ARN): denied}))
    node = node_of(doc, msk_id(MSK_CLUSTER))
    assert node["partial"] is True
    assert [r["api"] for r in node["partial_reasons"]] == ["kafka:ListNodes"]
    assert "broker_eni_ids" not in node["attributes"]
    assert node["attributes"]["subnet_ids"] == BOTH_SUBNETS
    assert node_of(doc, msk_id(MSK_SERVERLESS))["partial"] is False


def test_msk_state_is_probe_only_and_judged() -> None:
    topology, obs = _state_doc(FakeAws())
    row = next(o for o in obs["observations"] if o["target_id"] == msk_id(MSK_CLUSTER))
    assert row["status"] == "up", row  # UPDATING = 롤링 — 클러스터는 받는다
    assert "state" not in node_of(topology, msk_id(MSK_CLUSTER))["attributes"]


def test_msk_broker_eni_without_a_name_resolves_by_id_only() -> None:
    """브로커 ENI 설명에 이름이 없어도 ENI id 로 잇는다 · ListNodes 를 못 읽었으면 「MSK(Kafka) — 카드를 찾지 못함」(지어내지 않는다)."""
    import lbt_world as W
    from test_lb_targets import _doc, _edges, _node, lb

    doc = _doc(W.world())
    edge = _edges(doc, lb("edge-nlb"))[f"msk_cluster/{ACCOUNT}/{REGION}/{W.KAFKA}"]
    assert edge["attributes"]["target_count"] == 2 and edge["attributes"]["ports"] == [
        9094
    ]
    denied = client_error("AccessDenied", "ListNodes")
    doc = _doc(W.world(followup_errors={("list_nodes", W.KAFKA_ARN): denied}))
    unres = _node(doc, lb("edge-nlb"))["attributes"]["unresolved_targets"]
    assert {f"{ip} msk" for _, ip, _ in W.KAFKA_BROKERS} <= set(unres)


def test_msk_module_reads_only_what_it_declares() -> None:
    text = Path(str(msk_module.__file__)).read_text(encoding="utf-8")
    assert sorted(set(re.findall(r'"(kafka:[A-Za-z0-9]+)"', text))) == [
        "kafka:ListClustersV2",
        "kafka:ListNodes",
    ]


# -------------------------------------------------------------- Bedrock 엔드포인트(ADR 0013)

BR = f"com.amazonaws.{REGION}.bedrock-runtime"


def _ep(eid: str, name: str, kind: str = "Interface") -> dict[str, Any]:
    return {
        "VpcEndpointId": eid,
        "VpcEndpointType": kind,
        "VpcId": APP_VPC,
        "ServiceName": name,
        "State": "available",
        "SubnetIds": [PRIVATE_SUBNET] if kind == "Interface" else [],
        "RouteTableIds": [],
        "PrivateDnsEnabled": True,
        "Tags": [],
    }


ENDPOINTS = [
    _ep("vpce-0aaa1111bbbb0b01", BR),
    _ep("vpce-0aaa1111bbbb0b02", f"com.amazonaws.{REGION}.bedrock-agentcore.gateway"),
    # 🔴 흉내 셋 — 부분 문자열로는 Bedrock 이지만 목록에 없다 · 고객 PrivateLink · 다른 리전 이름
    _ep("vpce-0aaa1111bbbb0b03", f"com.amazonaws.{REGION}.bedrock-runtime-preview"),
    _ep(
        "vpce-0aaa1111bbbb0b04",
        f"com.amazonaws.vpce.{REGION}.vpce-svc-0bedrock000000000",
    ),
    _ep("vpce-0aaa1111bbbb0b05", "com.amazonaws.us-west-2.bedrock-runtime"),
]
SERVICES = {
    BR: "amazon",
    # 소유자가 amazon 이 아니면(있을 수 없는 모양 — 이중 확인이 정말 재는지 본다) Bedrock 이라 하지 않는다
    f"com.amazonaws.{REGION}.bedrock-agentcore.gateway": "111122223333",
    # amazon 소유라도 확정 목록에 없는 새 이름은 일반 엔드포인트다(모르는 것을 Bedrock 이라 하지 않는다)
    f"com.amazonaws.{REGION}.bedrock-runtime-preview": "amazon",
}


def _ep_attrs(doc: dict, eid: str) -> dict:
    return node_of(doc, f"vpc_endpoint/{ACCOUNT}/{REGION}/{eid}")["attributes"]


def test_only_exact_amazon_owned_bedrock_names_are_marked() -> None:
    doc = run(FakeAws(vpc_endpoints=ENDPOINTS, consumer_services=SERVICES))
    assert _ep_attrs(doc, "vpce-0aaa1111bbbb0b01")["aws_service"] == "bedrock"
    assert _ep_attrs(doc, "vpce-0aaa1111bbbb0b01")["service_owner"] == "amazon"
    gw = _ep_attrs(doc, "vpce-0aaa1111bbbb0b02")
    assert "aws_service" not in gw and gw["service_owner"] == "111122223333"
    for eid in (
        "vpce-0aaa1111bbbb0b03",
        "vpce-0aaa1111bbbb0b04",
        "vpce-0aaa1111bbbb0b05",
    ):
        a = _ep_attrs(doc, eid)
        assert "aws_service" not in a and "service_owner" not in a, eid


def test_owner_lookup_asks_only_the_bedrock_names() -> None:
    """🔴 흉내 이름·게이트웨이 엔드포인트는 묻지 않는다 · 이름이 Bedrock 이 아니면 호출 자체가 없다(비용 0) · 이름마다 한 번(P2-1 과 같은 호출)."""
    client = FakeAws(
        vpc_endpoints=ENDPOINTS + [_ep("vpce-0aaa1111bbbb0b06", BR, "Gateway")],
        consumer_services=SERVICES,
    )
    run(client)
    asked = sorted(
        c["ServiceNames"][0]
        for m, c in client.direct_calls
        if m == "describe_vpc_endpoint_services"
    )
    # 다른 리전 이름(us-west-2)도 이름 모양은 Bedrock 이다 — 가짜 클라이언트에는 리전이 없어 묻는다(실 클라이언트는 `meta.region_name` 으로 거른다) ·
    #   고객 PrivateLink(`vpce-svc-`)는 P2-1 이 원래 묻는다. 그래도 `aws_service` 는 그 리전 = 수집 리전 + amazon 일 때만 붙는다(위 검사)
    assert asked == sorted(
        [
            BR,
            f"com.amazonaws.{REGION}.bedrock-agentcore.gateway",
            "com.amazonaws.us-west-2.bedrock-runtime",
            f"com.amazonaws.vpce.{REGION}.vpce-svc-0bedrock000000000",
        ]
    )
    client = FakeAws()
    run(client)
    assert not [
        m for m, _ in client.direct_calls if m == "describe_vpc_endpoint_services"
    ]


def test_a_denied_owner_lookup_is_unknown_not_generic() -> None:
    """소유자를 못 읽으면 그 엔드포인트만 「일부 못 읽음」 + `aws_service` 없음 — 「Bedrock 이 아니다」가 아니라 「확인 못 했다」."""
    denied = client_error("UnauthorizedOperation", "DescribeVpcEndpointServices")
    doc = run(
        FakeAws(
            vpc_endpoints=ENDPOINTS[:1],
            errors={"describe_vpc_endpoint_services": denied},
        )
    )
    node = node_of(doc, f"vpc_endpoint/{ACCOUNT}/{REGION}/vpce-0aaa1111bbbb0b01")
    assert node["partial"] is True
    assert [r["api"] for r in node["partial_reasons"]] == [
        "ec2:DescribeVpcEndpointServices"
    ]
    assert (
        "aws_service" not in node["attributes"]
        and "service_owner" not in node["attributes"]
    )
    assert coverage_of(doc, "vpc_endpoint")["status"] == "ok"


def test_msk_eni_with_the_real_description_resolves_by_cluster_arn() -> None:
    """실측 설명(「DO NOT DELETE - Amazon MSK network interface for cluster <ARN>.」)이면 ListNodes 를 못 읽어도 ARN 낱말로 잇는다.

    🔴 ARN 이 통째로 같을 때만 — 다른 클러스터의 ARN 접두(같은 이름 · 다른 UUID)로는 잇지 않는다.
    """
    import lbt_world as W
    from test_lb_targets import _doc, _edges, _node, lb

    denied = client_error("AccessDenied", "ListNodes")
    client = W.world(followup_errors={("list_nodes", W.KAFKA_ARN): denied})
    eids = {eid for eid, _, _ in W.KAFKA_BROKERS}
    for e in client._enis:
        if e["NetworkInterfaceId"] in eids:
            e["Description"] = (
                f"DO NOT DELETE - Amazon MSK network interface for cluster {W.KAFKA_ARN}."
            )
    doc = _doc(client)
    assert f"msk_cluster/{ACCOUNT}/{REGION}/{W.KAFKA}" in _edges(doc, lb("edge-nlb"))
    # 다른 UUID 의 같은 이름 클러스터 ARN = 남의 것이다
    client = W.world(followup_errors={("list_nodes", W.KAFKA_ARN): denied})
    for e in client._enis:
        if e["NetworkInterfaceId"] in eids:
            e["Description"] = (
                f"DO NOT DELETE - Amazon MSK network interface for cluster {W.KAFKA_ARN[:-3]}9-1."
            )
    unres = _node(_doc(client), lb("edge-nlb"))["attributes"]["unresolved_targets"]
    assert {f"{ip} msk" for _, ip, _ in W.KAFKA_BROKERS} <= set(unres)
