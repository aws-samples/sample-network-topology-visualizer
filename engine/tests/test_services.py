"""서비스별 수집기(「이음매 2」) 축 — 레지스트리 + `load_balancer` 참조 구현.

여기까지 모든 리소스는 **목록 호출 하나**로 끝났다. 즉 「후속 호출이 실패해도 노드는 남고
사유가 붙는다」(불변식 ④의 리소스 단위 경로)는 코드가 아예 없어서 **미측정**이었다.
`collect.py` 의 `partial=True` 는 부모 해소 실패 한 곳에서만 세워지고 있었다.

각 검사는 **살아 있는 짝**을 같이 잰다: 「못 읽음」만 재면 속성을 아예 만들지 않는 구현도
통과하므로, 같은 검사에서 「읽었는데 0개」(`[]`)와 「읽어서 값이 있다」를 함께 확인한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from fakes import (
    ACCOUNT,
    APP_ALB_ARN,
    APP_WEB_ACL_ARN,
    APP_SG,
    APP_VPC,
    ASG_NAME_NEEDING_ENCODING,
    DB_SG,
    EKS_CLUSTER_NAME,
    FW_ARN,
    FW_ENDPOINT_A,
    FW_ENDPOINT_B,
    FW_NAME,
    FW_POLICY_ARN,
    LOAD_BALANCERS,
    OLD_NLB_ARN,
    PARTNER,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    FakeAws,
    client_error,
    factory,
)
from jsonschema import Draft202012Validator
from nettopo.collect import _BUILDERS, _SPECS, SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope, node_id, split_node_id
from nettopo.services import REGISTRY
from nettopo.services import auto_scaling_group as asg_module
from nettopo.services import ec2_instance as ec2_module
from nettopo.services import eks_cluster as eks_module
from nettopo.services import load_balancer as lb_module
from nettopo.services import network_firewall as fw_module

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json"
FIXED_CLOCK = "2026-01-02T03:04:05Z"
ALB_ID = f"load_balancer/{ACCOUNT}/{REGION}/app-alb"
NLB_ID = f"load_balancer/{ACCOUNT}/{REGION}/old-nlb"


def run(client: FakeAws) -> dict:
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )
    return collect(scope, factory(client), clock=lambda: FIXED_CLOCK).to_document()


def node_of(document: dict, node_id: str) -> dict:
    found = [n for n in document["nodes"] if n["id"] == node_id]
    assert len(found) == 1, f"{node_id} 가 산출물에 없다 — 어서션이 무엇도 재지 않는다"
    return found[0]


def coverage_of(document: dict, resource_type: str) -> dict:
    found = [c for c in document["coverage"] if c["resource_type"] == resource_type]
    assert len(found) == 1
    return found[0]


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ 레지스트리


def test_registry_types_are_supported_and_in_the_schema() -> None:
    """서비스 파일만 넣고 계약·수집 순서에 종류를 안 넣으면 **조용히** 수집되지 않는다."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    resource_types = set(schema["$defs"]["resource_type"]["enum"])
    assert REGISTRY, "레지스트리가 비었다 — 아래 어서션이 전부 미측정이 된다"
    for resource_type in REGISTRY:
        assert resource_type in SUPPORTED_RESOURCE_TYPES
        assert resource_type in resource_types


def test_the_registry_is_what_the_collector_actually_uses() -> None:
    """레지스트리가 장식이 아니라 본체에 꽂혀 있는가 — 둘 중 하나만 고치면 FAIL 한다."""
    for resource_type, service in REGISTRY.items():
        assert _SPECS[resource_type] is service.spec
        assert _BUILDERS[resource_type] is service.build


def test_every_supported_type_has_a_service_file() -> None:
    """반대 방향 — 계약에는 있는데 레지스트리에 없으면 그 종류가 **조용히** 빠진다.

    `_SPECS`·`_BUILDERS` 가 레지스트리의 투영이 된 뒤로 레지스트리 한 줄을 빼면 그 종류는
    `collect()` 의 「아직 수집할 수 없는 종류」로 떨어진다. 위 검사는 REGISTRY 를 돌기 때문에
    **빠진 줄을 볼 수 없다** — 그 침묵을 여기서 잡는다. 순서까지 같아야 한다(ADR 0002 §2).
    """
    assert tuple(REGISTRY) == SUPPORTED_RESOURCE_TYPES


def test_registry_module_matches_type() -> None:
    """**파일 하나 = 수집 종류 하나.** 키와 파일명이 어긋나면 3단계 절차가 거짓이 된다.

    절차(`services/__init__.py` 머리말)는 「`services/<종류>.py` 를 만들고 그 종류를 등록한다」다.
    키와 모듈이 갈라지면 다음 사람이 `services/vpc_endpoint.py` 를 고치고도 왜 안 바뀌는지 모른다.
    """
    for resource_type, service in REGISTRY.items():
        module = service.build.__module__
        assert module == f"nettopo.services.{resource_type}", (
            f"{resource_type} 의 빌더가 {module} 에 있다"
        )


# ------------------------------------------------------------------ 후속 호출


def test_listeners_and_target_groups_land_in_attributes() -> None:
    client = FakeAws()
    document = run(client)
    alb = node_of(document, ALB_ID)
    assert alb["attributes"]["listeners"] == ["HTTP:80", "HTTP:8080", "HTTPS:443"]
    assert alb["attributes"]["target_groups"] == ["app-tg-blue", "app-tg-green"]
    assert alb["partial"] is False
    # 살아 있는 짝: 읽었는데 0개인 LB 는 **빈 배열**이다(키가 없는 것과 다른 사실이다).
    nlb = node_of(document, NLB_ID)
    assert nlb["attributes"]["listeners"] == []
    assert nlb["attributes"]["target_groups"] == []
    assert nlb["partial"] is False


def test_the_followup_runs_once_per_load_balancer() -> None:
    client = FakeAws()
    run(client)
    for method in ("describe_listeners", "describe_target_groups"):
        assert client.calls.count(method) == len(LOAD_BALANCERS)


def test_target_health_is_not_collected_into_the_structure() -> None:
    """🔴 healthy/unhealthy 는 시시각각 바뀐다 — `topology.json` 에 들어가면 불변식 ②가 깨진다.

    구조는 `DescribeTargetHealth` 를 **부르지만**(등록 대상 수 · 사용자 결정 2026-10-16) 싣는 것은 개수뿐이다
    (`test_w3_targets_eni.py` ⓪). 헬스 «상태»는 관측 계약(ADR 0006)의 몫이다.
    """
    client = FakeAws()
    document = run(client)
    assert (
        client.calls.count("describe_target_health") == 2
    )  # 대조군 — 부르는데도 아래가 참이어야 한다
    for node in document["nodes"]:
        for key in node.get("attributes", {}):
            assert "health" not in key, f"상태가 구조에 섞였다: {node['id']}.{key}"


def test_a_denied_listener_read_marks_the_node_partial() -> None:
    """불변식 ④ — 한 LB 의 후속 호출만 거부됐을 때.

    ① 노드는 남는다 ② `partial` + 사유가 붙는다 ③ `listeners` **키가 없다**(= 못 읽었다)
    ④ 같은 LB 의 타깃 그룹은 살아 있다 ⑤ coverage 는 여전히 `ok`(목록 호출은 성공했다)
    """
    denied = FakeAws(
        followup_errors={
            ("describe_listeners", APP_ALB_ARN): client_error(
                "AccessDenied", "DescribeListeners"
            )
        }
    )
    document = run(denied)
    alb = node_of(document, ALB_ID)
    assert alb["partial"] is True
    reasons = alb["partial_reasons"]
    assert [r["code"] for r in reasons] == ["access_denied"]
    assert reasons[0]["api"] == "elasticloadbalancing:DescribeListeners"
    assert "listeners" not in alb["attributes"]
    assert alb["attributes"]["target_groups"] == ["app-tg-blue", "app-tg-green"]

    entry = coverage_of(document, "load_balancer")
    assert (entry["status"], entry["item_count"]) == ("ok", len(LOAD_BALANCERS))
    # 판별력 짝: 거부되지 않은 LB 는 partial 이 아니고 「없음」을 빈 배열로 말한다.
    nlb = node_of(document, NLB_ID)
    assert nlb["partial"] is False
    assert nlb["attributes"]["listeners"] == []


def test_alb_web_acl_is_read_and_nlb_is_not_asked() -> None:
    """🔵 ALB 의 web ACL 은 **교차 서비스 후속 호출**(`wafv2`)이고 ALB 만 부른다.

    세 경우를 한 검사에서 가른다 — 하나만 재면 「안 붙었다」와 「안 불렀다」가 구별되지 않는다:
    ⓐ 붙은 ALB → ARN ⓑ **NLB → 아예 부르지 않는다**(호출 로그로 잰다) ⓒ 스키마 통과.
    """
    client = FakeAws()
    document = run(client)
    alb = node_of(document, ALB_ID)
    assert alb["attributes"]["web_acl_arn"] == APP_WEB_ACL_ARN
    # 🔴 NLB 는 web ACL 을 붙일 수 없다 → 키 자체가 없다(「붙지 않았다」가 아니다).
    assert "web_acl_arn" not in node_of(document, NLB_ID)["attributes"]
    # 🔴 판별력: LB 2개인데 호출은 **1번**이어야 한다. 전부 부르면 NLB 마다 쓸모없는 거부가 쌓인다.
    assert client.calls.count("get_web_acl_for_resource") == 1


def test_an_alb_without_a_web_acl_says_none_not_missing() -> None:
    """「붙지 않았다」(값 `None`)와 「못 읽었다」(키 없음)를 가른다 — AWS 는 `WebACL` 키를 안 준다."""
    document = run(FakeAws(web_acls={}))
    alb = node_of(document, ALB_ID)
    assert "web_acl_arn" in alb["attributes"]  # 읽었다
    assert alb["attributes"]["web_acl_arn"] is None  # 붙지 않았다
    assert alb["partial"] is False  # 못 읽은 것이 아니므로 partial 이 아니다


def test_a_denied_web_acl_read_marks_only_that_alb_partial() -> None:
    """불변식 ④ — 거부되면 **키를 만들지 않고** 사유를 단다. 다른 칸은 살아 있다."""
    denied = FakeAws(
        followup_errors={
            ("get_web_acl_for_resource", APP_ALB_ARN): client_error(
                "AccessDeniedException", "GetWebACLForResource"
            )
        }
    )
    document = run(denied)
    alb = node_of(document, ALB_ID)
    assert alb["partial"] is True
    reasons = [
        r for r in alb["partial_reasons"] if r["api"] == "wafv2:GetWebACLForResource"
    ]
    assert [r["code"] for r in reasons] == ["access_denied"]
    assert "web_acl_arn" not in alb["attributes"]  # 「붙지 않았다」로 적지 않는다
    # 살아 있는 짝 — 같은 LB 의 다른 후속 호출은 그대로다.
    assert alb["attributes"]["listeners"] == ["HTTP:80", "HTTP:8080", "HTTPS:443"]
    assert node_of(document, NLB_ID)["partial"] is False


def test_a_failed_wafv2_entry_is_a_reason_not_an_empty_web_acl() -> None:
    """🔴 `wafv2` 클라이언트조차 못 만들면 — 「붙은 게 없다」가 아니라 사유다.

    `collect` 가 `join_web_acl` 에 클라이언트 대신 `None` + 진입 사유를 주는 경로다. 여기서 `None` 을
    적으면 보호되지 않은 ALB 와 구별되지 않는다. `elbv2` 진입은 성공했으므로 LB 노드는 그대로 서 있어야 한다.
    """
    from fakes import factory as _factory

    scope = Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES)
    snapshot = collect(
        scope,
        _factory(
            FakeAws(),
            fail_services={"wafv2": client_error("AccessDenied", "AssumeRole")},
        ),
        clock=lambda: "2026-01-02T03:04:05Z",
    )
    document = snapshot.to_document()
    alb = node_of(document, ALB_ID)
    assert alb["partial"] is True
    assert "web_acl_arn" not in alb["attributes"]
    # 🔵 사유의 API 는 실제로 실패한 진입(`sts:AssumeRole`)이다 — RDS → EC2 ENI 와 같은 규칙
    #    (`collect.py` 「실패한 API 이름은 예외에서 가져온다」). wafv2 권한 탓으로 적으면 오진이다.
    assert [r["api"] for r in alb["partial_reasons"]] == ["sts:AssumeRole"]
    assert (
        node_of(document, NLB_ID)["partial"] is False
    )  # NLB 는 부르지 않으므로 사유도 없다
    # 대조군 — 1층은 멀쩡하다(wafv2 실패가 다른 종류를 denied 로 만들면 오진이다).
    assert coverage_of(document, "load_balancer")["status"] == "ok"


def test_a_denied_followup_keeps_every_load_balancer(
    validator: Draft202012Validator,
) -> None:
    both_denied = FakeAws(
        followup_errors={
            ("describe_listeners", APP_ALB_ARN): client_error(
                "AccessDenied", "DescribeListeners"
            ),
            ("describe_target_groups", OLD_NLB_ARN): client_error(
                "Throttling", "DescribeTargetGroups"
            ),
        }
    )
    document = run(both_denied)
    ids = [n["id"] for n in document["nodes"] if n["type"] == "load_balancer"]
    assert sorted(ids) == sorted([ALB_ID, NLB_ID])
    nlb = node_of(document, NLB_ID)
    assert [r["code"] for r in nlb["partial_reasons"]] == ["throttled"]
    assert "target_groups" not in nlb["attributes"]
    assert nlb["attributes"]["listeners"] == []  # 한 칸만 못 읽었다
    validator.validate(document)  # partial 이면 사유가 필수다(스키마 allOf)


def test_a_load_balancer_without_an_arn_is_partial_not_silent() -> None:
    """ARN 이 없으면 후속 호출 자체가 불가능하다 — 그 사실을 적는다(없는 값을 지어내지 않는다)."""
    items = lb_module.enrich(
        FakeAws(), [lb_module.Fetched({"LoadBalancerName": "no-arn"})]
    )
    assert len(items) == 1
    assert [r.api for r in items[0].reasons] == [
        "elasticloadbalancing:DescribeListeners",
        "elasticloadbalancing:DescribeTargetGroups",
    ]
    assert "Listeners" not in items[0].payload


# ------------------------------------------------------------------ 표기


@pytest.mark.parametrize(
    ("listeners", "expected"),
    [
        # 같은 프로토콜의 80 과 443 — 문자열 정렬이면 "HTTP:443" 이 먼저 온다.
        (
            [{"Protocol": "HTTP", "Port": 443}, {"Protocol": "HTTP", "Port": 80}],
            ["HTTP:80", "HTTP:443"],
        ),
        # 포트 없는 리스너는 「리스너 하나」라고 말할 수 없다 → 버린다
        ([{"Protocol": "HTTP"}, {"Port": 80}], []),
        # 같은 프로토콜·포트가 두 번 나와도 한 줄이다(리스너 ARN 은 표기에 쓰지 않는다)
        (
            [{"Protocol": "tcp", "Port": 80}, {"Protocol": "TCP", "Port": 80}],
            ["TCP:80"],
        ),
    ],
)
def test_listener_labels_sort_by_port_number(
    listeners: list[dict[str, Any]], expected: list[str]
) -> None:
    assert lb_module._listener_labels(listeners) == expected


# ------------------------------------------------------------------ ec2_instance
#
# EC2 는 1차 12종과 두 곳이 다르다: (1) `describe_instances` 가 인스턴스를 예약으로 감싼다 →
# enrich 가 편다, (2) 노드 타입이 스키마에 새로 들어왔다. 두 축을 각각 잰다.


def test_ec2_instance_lands_under_its_subnet_with_config_only() -> None:
    """인스턴스는 서브넷의 자식이고(→VPC→리전), 헬스가 아니라 **구성**만 싣는다."""
    doc = run(FakeAws())
    node = node_of(doc, f"ec2_instance/{ACCOUNT}/{REGION}/i-0bbb2222cccc3333")
    assert node["type"] == "ec2_instance"
    assert node["parent_id"] == f"subnet/{ACCOUNT}/{REGION}/{PRIVATE_SUBNET}"
    attrs = node["attributes"]
    # 속성 집합을 못박는다 — 헬스·CPU·자동할당 퍼블릭 IP 같은 관측값이 새어 들어오면 FAIL.
    assert set(attrs) == {
        "instance_type",
        "state",
        "availability_zone",
        "private_ip_address",
        "security_group_ids",
    }
    assert attrs["instance_type"] == "t3.micro"
    assert attrs["private_ip_address"] == "10.10.2.10"
    # 읽었는데 있는 값은 정렬돼 나온다(픽스처는 [DB_SG, APP_SG] 순으로 줬다).
    assert attrs["security_group_ids"] == sorted([APP_SG, DB_SG])


def test_ec2_instance_makes_no_edges() -> None:
    """인스턴스의 위치는 부모로만 표현한다 — 인스턴스 수집기는 엣지를 만들지 않는다.

    2.7.0 부터 인스턴스에 닿는 엣지는 **LB 가 낸** `load_balancer_target` 뿐이다(ADR 0002 §8 — 대상 노드가 있을 때만
    3패스가 잇는다 · 매달린 참조가 아니다).
    """
    doc = run(FakeAws())
    iid = f"ec2_instance/{ACCOUNT}/{REGION}/i-0bbb2222cccc3333"
    node_of(doc, iid)  # 🔴 노드가 없으면 빈 리스트끼리 비교해 통과한다 = 미측정(§4)
    touching = [e for e in doc["edges"] if e["source"] == iid or e["target"] == iid]
    assert not [e for e in touching if e["source"] == iid]
    assert [e["type"] for e in touching] == ["load_balancer_target"]


def test_ec2_instance_coverage_is_ok() -> None:
    """예약이 인스턴스로 펴져도 coverage 는 목록 호출 기준으로 ok + 예약 수를 적는다."""
    doc = run(FakeAws())
    cov = coverage_of(doc, "ec2_instance")
    assert cov["status"] == "ok"
    assert cov["item_count"] == 2  # 예약 2건(각 인스턴스 1개) — 노드 수와 우연히 같다


def test_describe_instances_reservations_are_flattened() -> None:
    """예약 하나에 인스턴스가 여럿이어도 각각 노드가 된다 — 빌더는 인스턴스만 본다.

    RESOURCE_NODE_COUNT 를 흔들지 않으려고 스냅샷 픽스처는 예약당 1개로 두므로, 진짜
    여럿→여럿 평탄화는 여기서 enrich 를 직접 불러 잰다(불변식 ④: 항목을 버리지 않는다).
    """
    reservation = ec2_module.Fetched(
        {
            "OwnerId": PARTNER,
            "Instances": [
                {"InstanceId": "i-0aaa1111bbbb2222"},
                {"InstanceId": "i-0bbb2222cccc3333"},
            ],
        }
    )
    out = ec2_module.enrich(FakeAws(), [reservation])
    assert [f.payload["InstanceId"] for f in out] == [
        "i-0aaa1111bbbb2222",
        "i-0bbb2222cccc3333",
    ]
    # OwnerId 는 예약에만 있다 → 각 인스턴스에 실려야 ctx.attribute 가 다른 수집기와 같이 돈다.
    assert all(f.payload["OwnerId"] == PARTNER for f in out)


def test_ec2_instance_uses_reservation_owner_for_attribution() -> None:
    """소유자가 scope 안이면 인스턴스는 조회 계정이 아니라 **소유자**로 귀속된다(ADR 0002 §2).

    OwnerId 는 예약에만 있으므로, enrich 가 그것을 인스턴스로 안 내려보내면 귀속이 조용히 깨진다.
    scope 밖 소유자면 조회 계정으로 귀속하고 owner_account_id 만 속성으로 남는다.
    """
    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=("ec2_instance",)
    )
    outsider_owned = FakeAws(
        instances=[
            {
                "OwnerId": "999900001111",  # scope 밖 계정
                "Instances": [
                    {
                        "InstanceId": "i-0ccc3333dddd4444",
                        "InstanceType": "t3.nano",
                        "State": {"Name": "running"},
                        "Placement": {"AvailabilityZone": "us-east-1a"},
                        "VpcId": APP_VPC,
                        "PrivateIpAddress": "10.10.9.9",
                        "SecurityGroups": [],
                    }
                ],
            }
        ]
    )
    doc = collect(
        scope, factory(outsider_owned), clock=lambda: FIXED_CLOCK
    ).to_document()
    node = node_of(doc, f"ec2_instance/{ACCOUNT}/{REGION}/i-0ccc3333dddd4444")
    assert node["account_id"] == ACCOUNT  # 조사 안 한 계정을 그림에 등장시키지 않는다
    assert node["attributes"]["owner_account_id"] == "999900001111"


# ------------------------------------------------------------------ auto_scaling_group
#
# ASG 는 서브넷 하나에 안 살고(여러 서브넷에 걸침) 응답에 VpcId 가 없다 → 부모는 리전, 걸친
# 서브넷은 속성. 그리고 헬스(Instances[].HealthStatus)는 담지 않는다. 두 축을 각각 잰다.


def test_auto_scaling_group_lands_under_region_with_config_only() -> None:
    """ASG 는 리전의 자식이고(걸친 서브넷은 속성으로만), 헬스가 아니라 **구성**만 싣는다."""
    doc = run(FakeAws())
    node = node_of(doc, f"auto_scaling_group/{ACCOUNT}/{REGION}/app-asg")
    assert node["type"] == "auto_scaling_group"
    assert node["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    attrs = node["attributes"]
    # 속성 집합을 못박는다 — HealthStatus·LifecycleState·현재 인스턴스가 새어 들어오면 FAIL.
    assert set(attrs) == {
        "min_size",
        "max_size",
        "desired_capacity",
        "availability_zones",
        "subnet_ids",
        # 2.8.0 — 이 그룹이 인스턴스를 등록하는 대상 그룹(구성 · 화면이 LB → 인스턴스 선을 ASG 선으로 접는 재료 · P1-2)
        "target_group_arns",
    }
    assert attrs["target_group_arns"] == [
        f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/app-tg/abc123"
    ]
    # 🔴 지금 인스턴스는 ASG 노드에 없다 — 소속은 인스턴스 노드 쪽 속성이다(스케일마다 ASG 가 「바뀜」이 되지 않게)
    assert "i-0aaa1111bbbb2222" not in json.dumps(node)
    assert (
        node_of(doc, f"ec2_instance/{ACCOUNT}/{REGION}/i-0aaa1111bbbb2222")[
            "attributes"
        ]["auto_scaling_group_id"]
        == node["id"]
    )
    assert (attrs["min_size"], attrs["desired_capacity"], attrs["max_size"]) == (
        2,
        3,
        6,
    )
    # 걸친 서브넷·AZ 는 정렬돼 나온다(픽스처는 일부러 역순으로 줬다).
    assert attrs["subnet_ids"] == sorted([PUBLIC_SUBNET, PRIVATE_SUBNET])
    assert attrs["availability_zones"] == ["us-east-1a", "us-east-1b"]


def test_auto_scaling_group_makes_no_edges() -> None:
    """ASG→EC2·TargetGroupARNs 엣지를 만들지 않는다(배치 중복·상태 은폐, Topology_2.md §4)."""
    doc = run(FakeAws())
    asg = f"auto_scaling_group/{ACCOUNT}/{REGION}/app-asg"
    node_of(doc, asg)  # 🔴 노드가 없으면 빈 리스트끼리 비교해 통과한다 = 미측정(§4)
    assert not [e for e in doc["edges"] if e["source"] == asg or e["target"] == asg]


def test_auto_scaling_group_names_aws_allows_satisfy_the_node_id_contract() -> None:
    """🔴 **AWS 가 ASG 이름을 제약하지 않는다** — 공백·`|`·`/` 가 실제로 허용된다.

    그대로 `node_id` 에 넣으면 계약 위반 산출물이 **조용히** 써진다(리포에 런타임 스키마 검증이
    없다 — `jsonschema` 호출 0건). 오라클은 스키마 자신이고, 그래서 이 검사는 패턴이 바뀌면
    같이 바뀐다. 실측 2026-09-28: 공백·`|` 는 스키마 오류 · `team/app-asg` 는 **조용히 통과**.

    🔴 **경계는 `/` 가 어디 있느냐다**(리뷰 결정 2026-09-28): `team/app-asg` 는 세그먼트만 늘지만
    `/app`·`app/`·`a//b` 는 **빈 세그먼트**가 되어 패턴이 금지한다 → `safe=""` 로 인코딩한다.
    """
    pattern = re.compile(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["node_id"][
            "pattern"
        ]
    )
    natives = (
        "app-asg",
        "prod app asg",
        "a|b",
        "team/app-asg",
        "tab\there",
        # 🔴 `/` 가 맨 앞·맨 뒤·연속으로 오는 세 경우. `safe="/"` 로 되돌리면 각각
        #    `…//app` · `…/app/` · `…/a//b` 가 되어 **빈 세그먼트**로 패턴이 깨진다.
        "/app",
        "app/",
        "a//b",
        ASG_NAME_NEEDING_ENCODING,
    )
    for native in natives:
        nid = node_id(
            "auto_scaling_group", ACCOUNT, REGION, asg_module._id_segment(native)
        )
        assert pattern.match(nid), (native, nid)
    # 🔴 단사여야 한다 — `_` 로 치환하면 서로 다른 ASG 두 개가 **한 노드로 합쳐진다.**
    assert asg_module._id_segment("a b") != asg_module._id_segment("a_b")


def test_auto_scaling_group_border_names_survive_the_round_trip() -> None:
    """🔴 왕복이 깨지면 그 ASG 의 변경이 **이력 목록에서 말없이 빠진다.**

    경로: `split_node_id()` 가 `ValueError` → `history.target_cells()` 가 `None` → 변경이
    「칸 미상」 개수로만 세어지고 목록에 안 나온다. 빈 세그먼트를 만드는 이름이 바로 그 경로를
    탄다. `_id_segment` 는 단사이므로 `unquote` 가 원래 이름을 정확히 돌려준다.
    """
    for native in ("/app", "app/", "a//b", ASG_NAME_NEEDING_ENCODING):
        nid = node_id(
            "auto_scaling_group", ACCOUNT, REGION, asg_module._id_segment(native)
        )
        node_type, account, region, segment = split_node_id(nid)
        assert (node_type, account, region) == ("auto_scaling_group", ACCOUNT, REGION)
        assert segment is not None
        assert unquote(segment) == native, (native, nid)


def test_auto_scaling_group_encoded_name_reaches_the_snapshot() -> None:
    """픽스처가 실제로 인코딩 경로를 탄다 — `native_id`·`name` 은 **AWS 원문 그대로** 남는다."""
    doc = run(FakeAws())
    nid = node_id(
        "auto_scaling_group",
        ACCOUNT,
        REGION,
        asg_module._id_segment(ASG_NAME_NEEDING_ENCODING),
    )
    node = node_of(doc, nid)
    assert node["native_id"] == ASG_NAME_NEEDING_ENCODING
    assert node["name"] == ASG_NAME_NEEDING_ENCODING
    # id 에는 원문이 남지 않는다(남으면 빈 세그먼트·`|` 가 계약을 깬다).
    assert "|" not in nid and "//" not in nid


def test_auto_scaling_group_coverage_is_ok() -> None:
    doc = run(FakeAws())
    cov = coverage_of(doc, "auto_scaling_group")
    assert cov["status"] == "ok"
    assert cov["item_count"] == 2


# ------------------------------------------------------------------ eks_cluster
#
# EKS 는 두 곳이 다르다: (1) list_clusters 가 이름 **문자열** 목록을 준다 → enrich 가
# describe_cluster 로 상세를 채운다, (2) describe 가 단일 객체라 pages() 로 못 읽는다.
# 상태(status·health)는 담지 않고, 부모는 VPC(걸친 서브넷은 속성)다. 세 축을 각각 잰다.


def test_eks_cluster_lands_under_its_vpc_with_config_only() -> None:
    """클러스터는 VPC 의 자식이고(→리전), 상태가 아니라 **접근 구성**만 싣는다."""
    doc = run(FakeAws())
    node = node_of(doc, f"eks_cluster/{ACCOUNT}/{REGION}/{EKS_CLUSTER_NAME}")
    assert node["type"] == "eks_cluster"
    assert node["parent_id"] == f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
    assert node["partial"] is False
    attrs = node["attributes"]
    # 속성 집합을 못박는다 — status·health.issues 같은 상태값이 새어 들어오면 FAIL.
    assert set(attrs) == {
        "version",
        "endpoint_public_access",
        "endpoint_private_access",
        "public_access_cidrs",
        "subnet_ids",
        "security_group_ids",
        "cluster_security_group_id",
    }
    assert attrs["version"] == "1.29"
    assert attrs["endpoint_public_access"] is True
    assert attrs["public_access_cidrs"] == ["0.0.0.0/0"]
    # 읽었는데 있는 값은 정렬돼 나온다(픽스처는 오름차순이 아닌 순서로 줬다).
    assert attrs["subnet_ids"] == sorted([PUBLIC_SUBNET, PRIVATE_SUBNET])
    assert attrs["security_group_ids"] == sorted([APP_SG, DB_SG])


def test_eks_cluster_makes_no_edges() -> None:
    """위치는 부모·subnet_ids 속성으로만 표현한다 — 엣지를 만들지 않는다(ASG 와 같다)."""
    doc = run(FakeAws())
    cid = f"eks_cluster/{ACCOUNT}/{REGION}/{EKS_CLUSTER_NAME}"
    node_of(doc, cid)  # 🔴 노드가 없으면 빈 리스트끼리 비교해 통과한다 = 미측정(§4)
    assert not [e for e in doc["edges"] if e["source"] == cid or e["target"] == cid]


def test_eks_cluster_coverage_is_ok() -> None:
    """이름 목록을 상세로 채워도 coverage 는 목록 호출 기준으로 ok + 클러스터 수를 적는다."""
    doc = run(FakeAws())
    cov = coverage_of(doc, "eks_cluster")
    assert cov["status"] == "ok"
    assert cov["item_count"] == 1


def test_list_clusters_names_are_described_by_enrich() -> None:
    """list_clusters 는 **이름 문자열**을 준다 — enrich 가 describe_cluster 로 dict 를 채운다.

    payload 가 문자열이라는 사실이 이 수집기의 첫 특이점이다(다른 종류는 dict). 이름을 넘겨
    상세가 채워지는지, 성공 시 사유가 붙지 않는지 잰다.
    """
    out = eks_module.enrich(FakeAws(), [eks_module.Fetched(EKS_CLUSTER_NAME)])
    assert len(out) == 1
    assert out[0].payload["name"] == EKS_CLUSTER_NAME
    assert out[0].payload["resourcesVpcConfig"]["vpcId"] == APP_VPC
    assert out[0].reasons == ()


def test_a_denied_describe_cluster_marks_the_node_partial() -> None:
    """불변식 ④ — describe 가 거부돼도 클러스터 노드는 남는다.

    ① 노드는 남는다(목록은 성공했다) ② partial + 사유가 붙는다 ③ 구성 키가 **없다**(못 읽었다)
    ④ VPC 를 모르므로 부모는 리전 ⑤ coverage 는 여전히 ok(목록 호출은 성공했다).
    """
    denied = FakeAws(
        followup_errors={
            ("describe_cluster", EKS_CLUSTER_NAME): client_error(
                "AccessDenied", "DescribeCluster"
            )
        }
    )
    doc = run(denied)
    node = node_of(doc, f"eks_cluster/{ACCOUNT}/{REGION}/{EKS_CLUSTER_NAME}")
    assert node["partial"] is True
    reasons = node["partial_reasons"]
    assert [r["code"] for r in reasons] == ["access_denied"]
    assert reasons[0]["api"] == "eks:DescribeCluster"
    assert node["attributes"] == {}  # 구성을 못 읽었다 — 지어내지 않는다
    assert node["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    cov = coverage_of(doc, "eks_cluster")
    assert (cov["status"], cov["item_count"]) == ("ok", 1)


# --------------------------------------------------------------- network_firewall
#
# FW 는 EKS 처럼 목록→상세지만 두 곳이 다르다: (1) list_firewalls 는 이름/ARN **dict** 를 준다,
# (2) EndpointId 가 FirewallStatus.SyncStates(AZ 키 map) 안에 있다 — 상태(Status)는 버리고 구조인
# EndpointId(RT 조인 키)만 담는다. 부모는 VPC(걸친 서브넷은 속성)다. 세 축을 각각 잰다.


def test_network_firewall_lands_under_its_vpc_with_config_only() -> None:
    """FW 는 VPC 의 자식이고(→리전), 상태가 아니라 **경로 구성**만 싣는다."""
    doc = run(FakeAws())
    node = node_of(doc, f"network_firewall/{ACCOUNT}/{REGION}/{FW_NAME}")
    assert node["type"] == "network_firewall"
    assert node["parent_id"] == f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
    assert node["partial"] is False
    attrs = node["attributes"]
    # 속성 집합을 못박는다 — FirewallStatus.Status·per-AZ Status 가 새어 들어오면 FAIL.
    assert set(attrs) == {
        "firewall_policy_arn",
        "subnet_ids",
        "firewall_endpoint_ids",
        "delete_protection",
    }
    assert attrs["firewall_policy_arn"] == FW_POLICY_ARN
    assert attrs["delete_protection"] is True
    # 걸친 서브넷·엔드포인트는 정렬돼 나온다(픽스처는 오름차순이 아닌 순서로 줬다).
    assert attrs["subnet_ids"] == sorted([PUBLIC_SUBNET, PRIVATE_SUBNET])
    assert attrs["firewall_endpoint_ids"] == sorted([FW_ENDPOINT_A, FW_ENDPOINT_B])
    # 상태값(READY)은 어느 속성에도 없다 — SyncStates 에서 EndpointId 만 꺼냈다(Status 는 버렸다).
    assert "READY" not in json.dumps(attrs)


def test_network_firewall_makes_no_edges() -> None:
    """위치는 부모·subnet_ids 속성으로만 표현한다 — 엣지를 만들지 않는다(EKS·ASG 와 같다).

    RT→FW endpoint 조인은 vpce-* 공통 네임스페이스(§6 미팅 안건)가 서면 그때 얹는다.
    """
    doc = run(FakeAws())
    fid = f"network_firewall/{ACCOUNT}/{REGION}/{FW_NAME}"
    node_of(doc, fid)  # 🔴 노드가 없으면 빈 리스트끼리 비교해 통과한다 = 미측정(§4)
    assert not [e for e in doc["edges"] if e["source"] == fid or e["target"] == fid]


def test_network_firewall_coverage_is_ok() -> None:
    """이름/ARN 목록을 상세로 채워도 coverage 는 목록 호출 기준으로 ok + FW 수를 적는다."""
    doc = run(FakeAws())
    cov = coverage_of(doc, "network_firewall")
    assert cov["status"] == "ok"
    assert cov["item_count"] == 1


def test_list_firewalls_are_described_by_enrich() -> None:
    """list_firewalls 는 이름/ARN 만 준다 — enrich 가 describe_firewall 로 상세를 채운다.

    성공 시 Firewall 구성과 SyncStates(AZ 키 map)를 payload 에 싣고 사유가 붙지 않는지 잰다.
    """
    out = fw_module.enrich(
        FakeAws(),
        [fw_module.Fetched({"FirewallName": FW_NAME, "FirewallArn": FW_ARN})],
    )
    assert len(out) == 1
    assert out[0].payload["Firewall"]["VpcId"] == APP_VPC
    assert out[0].payload["SyncStates"]  # AZ 키 map 이 실렸다(EndpointId 의 출처)
    assert out[0].reasons == ()


def test_a_denied_describe_firewall_marks_the_node_partial() -> None:
    """불변식 ④ — describe 가 거부돼도 FW 노드는 남는다.

    ① 노드는 남는다(목록은 성공했다) ② partial + 사유가 붙는다 ③ 구성 키가 **없다**(못 읽었다)
    ④ VPC 를 모르므로 부모는 리전 ⑤ coverage 는 여전히 ok(목록 호출은 성공했다).
    """
    denied = FakeAws(
        followup_errors={
            ("describe_firewall", FW_ARN): client_error(
                "AccessDenied", "DescribeFirewall"
            )
        }
    )
    doc = run(denied)
    node = node_of(doc, f"network_firewall/{ACCOUNT}/{REGION}/{FW_NAME}")
    assert node["partial"] is True
    reasons = node["partial_reasons"]
    assert [r["code"] for r in reasons] == ["access_denied"]
    assert reasons[0]["api"] == "network-firewall:DescribeFirewall"
    assert node["attributes"] == {}  # 구성을 못 읽었다 — 지어내지 않는다
    assert node["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    cov = coverage_of(doc, "network_firewall")
    assert (cov["status"], cov["item_count"]) == ("ok", 1)


def test_lambda_function_lands_in_its_vpc_with_config_only() -> None:
    """2.8.0(P2-6) — VPC 함수 = VPC 의 자식 + 걸친 서브넷(속성) · VPC 밖 함수 = 리전의 자식 · 배포마다 바뀌는 값은 싣지 않는다."""
    from fakes import LAMBDA_FN_PLAIN, LAMBDA_FN_VPC, LAMBDA_STATE_LEAKS

    doc = run(FakeAws())
    vpc_fn = node_of(doc, f"lambda_function/{ACCOUNT}/{REGION}/{LAMBDA_FN_VPC}")
    assert vpc_fn["parent_id"] == f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
    assert set(vpc_fn["attributes"]) == {
        "function_name",
        "runtime",
        "subnet_ids",
        "security_group_ids",
    }
    assert vpc_fn["attributes"]["subnet_ids"] == [PRIVATE_SUBNET]
    assert vpc_fn["attributes"]["security_group_ids"] == sorted([DB_SG, APP_SG])
    plain = node_of(doc, f"lambda_function/{ACCOUNT}/{REGION}/{LAMBDA_FN_PLAIN}")
    assert plain["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    assert plain["attributes"]["subnet_ids"] == []
    dumped = json.dumps([vpc_fn, plain])
    for leak in LAMBDA_STATE_LEAKS:
        assert leak not in dumped, leak
