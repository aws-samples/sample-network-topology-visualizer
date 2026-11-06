"""W2 수집 7종 — TGW 라우트 테이블 · RDS · ECS 서비스 · SageMaker 3종 · EC2 Instance Connect.

각 종류마다 세 축을 잰다(test_services.py 의 EKS·FW 블록과 같은 틀):
① 어디에 사는가(부모 체인) ② 무엇을 싣는가(속성 집합을 못박아 상태값 유입을 FAIL 로)
③ 후속 호출이 거부되면 무엇이 남는가(불변식 ④ — 키는 **없고** 사유가 붙는다, 빈 값으로 채우지 않는다).
"""

from __future__ import annotations

import json
from urllib.parse import quote

import fakes
import pytest
from fakes import (
    ACCOUNT,
    APP_SG,
    APP_VPC,
    CORE_TGW,
    DB_SG,
    ECS_CLUSTER,
    ECS_CLUSTER_ARN,
    ECS_SERVICE,
    ECS_SERVICE_ARN,
    ECS_STATE_LEAKS,
    EIC_ENDPOINT,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    RDS_DB_INSTANCES,
    RDS_INSTANCE,
    RDS_STATE_LEAKS,
    REGION,
    SM_DOMAIN,
    SM_ENDPOINT,
    SM_NOTEBOOK,
    TGW_ROUTE_TABLE,
    FakeAws,
    client_error,
)
from jsonschema import Draft202012Validator
from nettopo.collect import SUPPORTED_RESOURCE_TYPES
from nettopo.services import ecs_service as ecs_module
from test_services import SCHEMA_PATH, coverage_of, node_of, run

W2_TYPES = (
    "transit_gateway_route_table",
    "rds_db_instance",
    "ecs_service",
    "sagemaker_notebook_instance",
    "sagemaker_domain",
    "sagemaker_endpoint",
    "ec2_instance_connect_endpoint",
)
REGION_ID = f"region/{ACCOUNT}/{REGION}"
VPC_ID = f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
PRIVATE_ID = f"subnet/{ACCOUNT}/{REGION}/{PRIVATE_SUBNET}"
RT_ID = f"transit_gateway_route_table/{ACCOUNT}/{REGION}/{TGW_ROUTE_TABLE}"
ECS_NATIVE = f"{ECS_CLUSTER}/{ECS_SERVICE}"
ECS_ID = f"ecs_service/{ACCOUNT}/{REGION}/{quote(ECS_NATIVE, safe='')}"
RDS_ID = f"rds_db_instance/{ACCOUNT}/{REGION}/{RDS_INSTANCE}"
EIC_ID = f"ec2_instance_connect_endpoint/{ACCOUNT}/{REGION}/{EIC_ENDPOINT}"
NB_ID = f"sagemaker_notebook_instance/{ACCOUNT}/{REGION}/{SM_NOTEBOOK}"
DOMAIN_ID = f"sagemaker_domain/{ACCOUNT}/{REGION}/{SM_DOMAIN}"
EP_ID = f"sagemaker_endpoint/{ACCOUNT}/{REGION}/{SM_ENDPOINT}"
BOTH_SUBNETS = sorted([PRIVATE_SUBNET, PUBLIC_SUBNET])
BOTH_SGS = sorted([APP_SG, DB_SG])


def edges_of(document: dict, nid: str) -> list[dict]:
    return [e for e in document["edges"] if nid in (e["source"], e["target"])]


def denied(method: str, key: str, op: str) -> FakeAws:
    return FakeAws(followup_errors={(method, key): client_error("AccessDenied", op)})


# ------------------------------------------------------------------ 계약


def test_w2_types_are_in_the_contract_and_the_document() -> None:
    """스키마 양쪽 enum + `SUPPORTED_RESOURCE_TYPES` + 실제 수집 + 스키마 통과 — 한쪽만 늘리면 깨진다."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    doc = run(FakeAws())
    drawn = {n["type"] for n in doc["nodes"]}
    for t in W2_TYPES:
        assert t in schema["$defs"]["node_type"]["enum"], t
        assert t in schema["$defs"]["resource_type"]["enum"], t
        assert t in SUPPORTED_RESOURCE_TYPES, t
        assert t in drawn, f"{t} 가 산출물에 없다"
        assert coverage_of(doc, t)["status"] == "ok", t
    errors = Draft202012Validator(schema).iter_errors(doc)
    assert [f"{e.json_path}: {e.message}" for e in errors] == []


# -------------------------------------------------------------- RDS


def test_rds_lands_under_the_subnet_of_its_az_with_config_only() -> None:
    """서브넷 그룹 중 **자기 AZ 의 서브넷이 하나**면 그 서브넷 아래다. 상태(백업 중·복구 시점)는 없다."""
    doc = run(FakeAws())
    node = node_of(doc, RDS_ID)
    assert node["parent_id"] == PRIVATE_ID  # 1b 서브넷 — 1a 공용 서브넷이 아니다
    assert node["partial"] is False
    attrs = node["attributes"]
    assert set(attrs) == {
        "engine",
        "instance_class",
        "multi_az",
        "availability_zone",
        "publicly_accessible",
        "subnet_group_name",
        "subnet_group_subnet_ids",
        "security_group_ids",
    }
    assert attrs["subnet_group_subnet_ids"] == BOTH_SUBNETS
    assert attrs["security_group_ids"] == BOTH_SGS
    assert node["tags"] == {"team": "app"}
    dumped = json.dumps(node)
    for leak in RDS_STATE_LEAKS:
        assert leak not in dumped, leak
    assert edges_of(doc, RDS_ID) == []


def test_rds_without_a_unique_az_subnet_falls_back_to_the_vpc() -> None:
    """같은 AZ 서브넷이 둘이면 어느 쪽인지 모른다 — 하나를 고르지 않고 VPC 에 둔다(지어내지 않는다)."""
    item = json.loads(json.dumps(RDS_DB_INSTANCES[0]))
    for s in item["DBSubnetGroup"]["Subnets"]:
        s["SubnetAvailabilityZone"]["Name"] = "us-east-1b"
    doc = run(FakeAws(rds_instances=[item]))
    assert node_of(doc, RDS_ID)["parent_id"] == VPC_ID


def test_rds_without_a_subnet_group_lands_under_the_region() -> None:
    item = json.loads(json.dumps(RDS_DB_INSTANCES[0]))
    del item["DBSubnetGroup"]
    doc = run(FakeAws(rds_instances=[item]))
    node = node_of(doc, RDS_ID)
    assert node["parent_id"] == REGION_ID
    # 응답이 그룹 없음을 말했다 = 읽었는데 없다 → 빈 목록(못 읽은 것이 아니다).
    assert node["attributes"]["subnet_group_subnet_ids"] == []


# -------------------------------------------------------------- EIC


def test_instance_connect_endpoint_lands_under_its_subnet() -> None:
    doc = run(FakeAws())
    node = node_of(doc, EIC_ID)
    assert node["parent_id"] == PRIVATE_ID
    assert node["name"] == "app-eic"
    assert set(node["attributes"]) == {
        "state",
        "availability_zone",
        "preserve_client_ip",
        "security_group_ids",
    }
    assert node["attributes"]["security_group_ids"] == BOTH_SGS
    assert edges_of(doc, EIC_ID) == []


# -------------------------------------------------------------- TGW 라우트 테이블
#
# 연결·전파는 attachment id 목록(속성) · 경로는 `route` 엣지(표 → attachment)다. 스키마가 속성에
# 객체를 허용하지 않으므로 경로를 속성에 넣을 수 없다 — 엣지가 원래 그 일을 한다(VPC 라우트 표와 같다).


def test_tgw_route_table_keeps_only_live_associations_and_propagations() -> None:
    doc = run(FakeAws())
    node = node_of(doc, RT_ID)
    assert node["parent_id"] == f"transit_gateway/{ACCOUNT}/{REGION}/{CORE_TGW}"
    assert node["partial"] is False
    attrs = node["attributes"]
    # 끊긴 연결(EGRESS)은 빠진다 — 담으면 「이 VPC 가 이 표를 쓴다」가 거짓이다.
    assert attrs["association_attachment_ids"] == [
        "tgw-attach-0aaa1111bbbb8888",
        "tgw-attach-0bbb2222cccc9999",
    ]
    # 꺼진 전파(VPN)는 빠진다.
    assert attrs["propagation_attachment_ids"] == ["tgw-attach-0aaa1111bbbb8888"]
    assert attrs["blackhole_destinations"] == ["0.0.0.0/0"]
    assert attrs["default_association"] is True


def test_tgw_route_table_routes_become_edges_to_attachments() -> None:
    """경로 1개(10.10/16 → APP attachment). 블랙홀은 갈 곳이 없어 속성 · 지워진 경로는 아무 데도 없다."""
    doc = run(FakeAws())
    edges = edges_of(doc, RT_ID)
    assert [(e["type"], e["target"], e["directed"]) for e in edges] == [
        (
            "route",
            f"transit_gateway_attachment/{ACCOUNT}/{REGION}/tgw-attach-0aaa1111bbbb8888",
            True,
        )
    ]
    assert edges[0]["attributes"] == {
        "destinations": ["10.10.0.0/16"],
        "static_destinations": [],
    }
    # deleted 경로(10.20/16 → EGRESS)는 엣지에도 블랙홀에도 없다. VPC CIDR 과 겹치므로 문서 전체가 아니라 이 표만 본다.
    assert "10.20.0.0/16" not in json.dumps([node_of(doc, RT_ID), *edges])


def test_tgw_route_table_ecmp_route_reaches_every_attachment() -> None:
    routes = {
        TGW_ROUTE_TABLE: [
            {
                "DestinationCidrBlock": "10.30.0.0/16",
                "Type": "static",
                "State": "active",
                "TransitGatewayAttachments": [
                    {"TransitGatewayAttachmentId": "tgw-attach-0bbb2222cccc9999"},
                    {"TransitGatewayAttachmentId": "tgw-attach-0aaa1111bbbb8888"},
                ],
            }
        ]
    }
    doc = run(FakeAws(tgw_rt_routes=routes))
    edges = edges_of(doc, RT_ID)
    assert len(edges) == 2
    for e in edges:
        assert e["attributes"] == {
            "destinations": ["10.30.0.0/16"],
            "static_destinations": ["10.30.0.0/16"],
        }


@pytest.mark.parametrize(
    ("method", "op", "api", "gone"),
    [
        (
            "get_transit_gateway_route_table_associations",
            "GetTransitGatewayRouteTableAssociations",
            "ec2:GetTransitGatewayRouteTableAssociations",
            {"association_attachment_ids", "associations"},
        ),
        (
            "get_transit_gateway_route_table_propagations",
            "GetTransitGatewayRouteTablePropagations",
            "ec2:GetTransitGatewayRouteTablePropagations",
            {"propagation_attachment_ids", "propagations"},
        ),
        (
            "search_transit_gateway_routes",
            "SearchTransitGatewayRoutes",
            "ec2:SearchTransitGatewayRoutes",
            {"blackhole_destinations", "routes"},
        ),
        # 2.13.0 — 콘솔 「접두사 목록 참조」 탭(새 권한 · 고객 스택 B 를 갱신하기 전에는 거부된다)
        (
            "get_transit_gateway_prefix_list_references",
            "GetTransitGatewayPrefixListReferences",
            "ec2:GetTransitGatewayPrefixListReferences",
            {"prefix_list_references"},
        ),
    ],
)
def test_a_denied_tgw_route_table_followup_drops_only_its_own_keys(
    method: str, op: str, api: str, gone: set[str]
) -> None:
    """후속 셋 중 하나가 거부되면 **그 키만** 없다(빈 목록이 아니다 — 「연결 없음」과 「못 읽음」은 다르다)."""
    doc = run(denied(method, TGW_ROUTE_TABLE, op))
    node = node_of(doc, RT_ID)
    assert node["partial"] is True
    assert [(r["code"], r["api"]) for r in node["partial_reasons"]] == [
        ("access_denied", api)
    ]
    full = set(node_of(run(FakeAws()), RT_ID)["attributes"])
    assert set(node["attributes"]) == full - gone
    route_edges = edges_of(doc, RT_ID)
    assert (route_edges == []) is (method == "search_transit_gateway_routes")
    assert coverage_of(doc, "transit_gateway_route_table")["status"] == "ok"


# -------------------------------------------------------------- ECS 서비스


def test_ecs_service_is_one_node_per_service_with_network_config_only() -> None:
    """노드 = 서비스(클러스터가 아니다). id 의 `/` 는 인코딩되고 native_id 는 「클러스터/서비스」 그대로다."""
    doc = run(FakeAws())
    node = node_of(doc, ECS_ID)
    assert node["native_id"] == ECS_NATIVE
    assert node["arn"] == ECS_SERVICE_ARN
    assert node["name"] == ECS_SERVICE
    assert node["parent_id"] == REGION_ID
    attrs = node["attributes"]
    assert set(attrs) == {
        "cluster_name",
        "launch_type",
        "capacity_providers",
        "subnet_ids",
        "security_group_ids",
        "assign_public_ip",
        "target_group_arns",
    }
    assert attrs["subnet_ids"] == BOTH_SUBNETS
    assert attrs["security_group_ids"] == BOTH_SGS
    dumped = json.dumps(node)
    for leak in ECS_STATE_LEAKS:  # 배포 상태·태스크 정의 개정 번호는 구조가 아니다
        assert leak not in dumped, leak
    assert "runningCount" not in dumped
    assert edges_of(doc, ECS_ID) == []
    assert coverage_of(doc, "ecs_service")["item_count"] == 1


def test_a_denied_list_services_fails_the_whole_type() -> None:
    """🔴 서비스 목록이 거부되면 클러스터를 건너뛰는 게 아니라 **종류 전체가 실패**다.

    건너뛰면 coverage 가 ok + 0개 = 「ECS 서비스 없음」이 된다 — 있는데 없다고 그린다(불변식 ④).
    """
    doc = run(denied("list_services", ECS_CLUSTER_ARN, "ListServices"))
    assert [n for n in doc["nodes"] if n["type"] == "ecs_service"] == []
    cov = coverage_of(doc, "ecs_service")
    assert cov["status"] == "denied"
    assert cov["reason"]["api"] == "ecs:ListServices"


def test_a_denied_describe_services_keeps_the_service_partial() -> None:
    doc = run(denied("describe_services", ECS_CLUSTER_ARN, "DescribeServices"))
    node = node_of(doc, ECS_ID)
    assert node["partial"] is True
    assert [r["api"] for r in node["partial_reasons"]] == ["ecs:DescribeServices"]
    assert node["attributes"] == {"cluster_name": ECS_CLUSTER}  # 구성을 지어내지 않는다
    assert coverage_of(doc, "ecs_service")["status"] == "ok"


def test_describe_services_is_batched_by_ten_and_failures_are_partial() -> None:
    """실제 API 는 한 번에 10개까지 — 12개면 10+2 로 나눠 부른다. 응답에 없는 서비스는 사유가 붙는다."""
    extra = [
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/{ECS_CLUSTER}/svc-{i:02d}"
        for i in range(11)
    ]
    client = FakeAws(ecs_services={ECS_CLUSTER_ARN: [ECS_SERVICE_ARN, *extra]})
    out = ecs_module.enrich(client, [ecs_module.Fetched(ECS_CLUSTER_ARN)])
    sizes = [
        len(kw["services"]) for m, kw in client.direct_calls if m == "describe_services"
    ]
    assert sizes == [10, 2]
    assert len(out) == 12
    described = [f for f in out if "serviceName" in f.payload]
    assert [f.payload["serviceArn"] for f in described] == [ECS_SERVICE_ARN]
    missing = [f for f in out if "serviceName" not in f.payload]
    assert len(missing) == 11
    for f in missing:
        assert [(r.code, r.api) for r in f.reasons] == [
            ("api_error", "ecs:DescribeServices")
        ]
        assert f.payload["clusterArn"] == ECS_CLUSTER_ARN


# -------------------------------------------------------------- SageMaker


def test_notebook_lands_under_its_subnet() -> None:
    doc = run(FakeAws())
    node = node_of(doc, NB_ID)
    assert node["parent_id"] == PRIVATE_ID
    assert node["attributes"] == {
        "instance_type": "ml.t3.medium",
        "direct_internet_access": "Disabled",
        "security_group_ids": BOTH_SGS,
    }
    assert "InService" not in json.dumps(node)


def test_a_denied_describe_notebook_keeps_the_listed_fields_only() -> None:
    doc = run(
        denied("describe_notebook_instance", SM_NOTEBOOK, "DescribeNotebookInstance")
    )
    node = node_of(doc, NB_ID)
    assert node["partial"] is True
    assert node["parent_id"] == REGION_ID  # 서브넷을 모른다
    assert node["attributes"] == {"instance_type": "ml.t3.medium"}


def test_domain_lands_under_its_vpc_with_its_subnets_as_attributes() -> None:
    doc = run(FakeAws())
    node = node_of(doc, DOMAIN_ID)
    assert node["name"] == "app-studio"
    assert node["parent_id"] == VPC_ID
    assert node["attributes"] == {
        "app_network_access_type": "VpcOnly",
        "subnet_ids": BOTH_SUBNETS,
        "security_group_ids": BOTH_SGS,
    }


def test_a_denied_describe_domain_has_no_network_keys() -> None:
    doc = run(denied("describe_domain", SM_DOMAIN, "DescribeDomain"))
    node = node_of(doc, DOMAIN_ID)
    assert node["partial"] is True
    assert node["parent_id"] == REGION_ID
    assert node["attributes"] == {}


def test_endpoint_unions_the_subnets_of_every_model() -> None:
    """엔드포인트 → 설정 → 모델 2개의 VpcConfig 를 **합친다**(모델마다 다른 서브넷을 쓸 수 있다)."""
    doc = run(FakeAws())
    node = node_of(doc, EP_ID)
    assert node["parent_id"] == REGION_ID
    assert node["partial"] is False
    assert node["attributes"] == {
        "endpoint_config_name": "app-infer-cfg",
        "model_names": ["app-model-a", "app-model-b"],
        "subnet_ids": BOTH_SUBNETS,
        "security_group_ids": BOTH_SGS,
    }


def test_endpoint_without_vpc_config_has_empty_subnets_not_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VPC 밖 엔드포인트는 `subnet_ids: []`(읽었는데 없다) — 못 읽은 경우(키 없음)와 구별된다."""
    for name in ("app-model-a", "app-model-b"):
        monkeypatch.setitem(fakes.SM_MODELS, name, {"ModelName": name})
    node = node_of(run(FakeAws()), EP_ID)
    assert node["partial"] is False
    assert node["attributes"]["subnet_ids"] == []
    assert node["attributes"]["security_group_ids"] == []


@pytest.mark.parametrize(
    ("method", "key", "op", "kept"),
    [
        ("describe_endpoint", SM_ENDPOINT, "DescribeEndpoint", set()),
        (
            "describe_endpoint_config",
            "app-infer-cfg",
            "DescribeEndpointConfig",
            {"endpoint_config_name"},
        ),
        (
            "describe_model",
            "app-model-b",
            "DescribeModel",
            {"endpoint_config_name", "model_names"},
        ),
    ],
)
def test_a_denied_link_in_the_endpoint_chain_stops_there(
    method: str, key: str, op: str, kept: set[str]
) -> None:
    """사슬 중간이 거부되면 거기서 멈춘다 — 모델 하나만 읽고 그 서브넷만 적으면 「여기에만 있다」가 거짓이다."""
    node = node_of(run(denied(method, key, op)), EP_ID)
    assert node["partial"] is True
    assert [r["api"] for r in node["partial_reasons"]] == [f"sagemaker:{op}"]
    assert set(node["attributes"]) == kept
