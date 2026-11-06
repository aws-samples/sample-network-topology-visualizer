"""LB → 대상 선(`load_balancer_target` · 스키마 2.7.0 · ADR 0002 §8 · ADR 0007 결정 6 개정 · 사용자 결정 2026-10-20).

축 일곱:
① 대상 종류마다 잇는다 — instance · alb · lambda · ip(ECS · VPC 엔드포인트 · RDS · EKS · EC2 · 다른 LB · EIC) · 외부 IP
② **(LB, 대상 노드) 마다 선 하나** — 등록을 묶고(그룹·포트·종류·수) IP 단위 선을 만들지 않는다(ADR 0002 §4 와 같은 규칙)
③ 못 찾은 대상은 **지어내지 않는다** — `unresolved_refs` + `unresolved_targets`(「<id> <까닭>」 · 수집하지 않는 서비스는 그 이름)
④ 불변식 ④ — 그룹 하나·ENI 조회 하나가 거부돼도 나머지 선은 생기고 LB 가 `partial` + 사유를 든다
⑤ 불변식 ② — 응답 순서와 무관하게 바이트가 같다 · 헬스는 구조에 없다(상태 단 엣지 속성 → 관측 행 `targets`)
⑥ 크로스계정 — AZ `all` IP 가 다른 계정의 수집한 VPC 안이면 **그 계정 쌍**에서 ENI 를 묻는다
⑦ 산출물 = `fixtures/lb-targets.json`(렌더러 하네스 입력) — 이 세계를 엔진에 돌린 바이트 그대로
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
from pathlib import Path
from typing import Any

import jsonschema
import lbt_world as W
import pytest
from fakes import FakeAws, client_error, factory, factory_by_pair
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.lb_targets import structure_lambdas
from nettopo.model import DERIVED_TYPES, Scope
from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation
from nettopo.serialize import dumps, to_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "topology.schema.json").read_text(encoding="utf-8")
)
OBS_SCHEMA = json.loads(
    (ROOT / "schema" / "observation.schema.json").read_text(encoding="utf-8")
)
FIXTURE = ROOT / "fixtures" / "lb-targets.json"
OBS_FIXTURE = ROOT / "fixtures" / "observation" / "lb-targets.json"
_DETAIL = ("health_check_off", "initial", "draining", "unused", "other_unavailable")


def _t(total: int, healthy: int, unhealthy: int, **detail: int) -> dict[str, int]:
    """관측 `targets` 기대값(1.15.0 · #137) — 다섯 갈래는 늘 함께 온다(0 포함)."""
    assert set(detail) <= set(_DETAIL), detail
    return {
        "total": total,
        "healthy": healthy,
        "unhealthy": unhealthy,
        **{k: detail.get(k, 0) for k in _DETAIL},
    }


CLOCK = "2026-01-02T03:04:05Z"
A, R = W.ACCOUNT, W.REGION
SCOPE = Scope(account_ids=(A,), regions=(R,), resource_types=SUPPORTED_RESOURCE_TYPES)


def lb(name: str, account: str = A) -> str:
    return f"load_balancer/{account}/{R}/{name}"


def _snap(
    client: FakeAws,
    scope: Scope = SCOPE,
    *,
    state: bool = False,
    fac: Any = None,
    seeded: tuple[Any, ...] = (),
) -> Any:
    return collect(
        scope,
        fac or factory(client),
        clock=lambda: CLOCK,
        with_state=state,
        structure_lambdas=seeded,
    )


def _doc(client: FakeAws | None = None, **kw: Any) -> dict[str, Any]:
    return _snap(client or W.world(), **kw).to_document()


def _observe(client: FakeAws) -> tuple[dict[str, Any], dict[str, Any]]:
    """(구조 문서, 관측 문서) — 생산과 같은 조합(구조 = 전 종류 · 상태 단 = 관측 종류 · 같은 가짜를 두 번)."""
    topology = _doc(client)
    state = _snap(
        client,
        Scope(account_ids=(A,), regions=(R,), resource_types=OBSERVED_RESOURCE_TYPES),
        state=True,
        # 생산(`cli._run_observe`)과 같다 — 구조 문서의 VPC Lambda 로 공유 ENI 를 가린다(#152)
        seeded=structure_lambdas(topology),
    )
    doc, dropped = build_observation(
        state, topology=topology, observed_at=CLOCK, tool_version="test"
    )
    assert not dropped, f"상태 단 대상이 구조에 없다: {dropped}"
    return topology, doc


def _edges(doc: dict[str, Any], source: str | None = None) -> dict[str, dict[str, Any]]:
    return {
        e["target"]: e
        for e in doc["edges"]
        if e["type"] == "load_balancer_target"
        and (source is None or e["source"] == source)
    }


def _node(doc: dict[str, Any], nid: str) -> dict[str, Any]:
    found = [n for n in doc["nodes"] if n["id"] == nid]
    assert len(found) == 1, f"{nid} 가 {len(found)}개 — 어서션이 무엇도 재지 않는다"
    return found[0]


# ------------------------------------------------------------------ ① 종류마다


def test_every_target_shape_resolves_to_its_node():
    doc = _doc()
    web = _edges(doc, lb("web-alb"))
    nlb = _edges(doc, lb("edge-nlb"))
    assert (
        sorted(web)
        == sorted(
            [f"ec2_instance/{A}/{R}/{i}" for i in W.WEB]
            + [
                f"ecs_service/{A}/{R}/lbt-ecs%2Fapi-svc",
                f"lambda_function/{A}/{R}/thumbnailer",
                # 2.8.0 — ASG 가 등록한 인스턴스도 엔진 선은 인스턴스로 간다(화면이 ASG 선으로 접는다 · §17.27)
                *[f"ec2_instance/{A}/{R}/{i}" for i in W.ASG_INSTANCES],
                f"eks_cluster/{A}/{R}/{W.EKS}",  # 2.8.0(P2-5) — LB Controller 대상 그룹(태그)의 파드 IP
                f"ec2_instance/{A}/{R}/{W.WORKER}",  # 같은 그룹에 손으로 등록한 남의 인스턴스
            ]
        )
    )
    assert (
        sorted(nlb)
        == sorted(
            [
                lb("web-alb"),  # alb 대상(NLB → ALB)
                lb("int-alb"),  # ip 대상인데 그 IP 가 내부 ALB 의 ENI
                f"vpc_endpoint/{A}/{R}/{W.VPCE}",
                f"rds_db_instance/{A}/{R}/{W.RDS}",
                f"ec2_instance/{A}/{R}/{W.WORKER}",  # ENI 부착 인스턴스
                f"lambda_function/{A}/{R}/{W.LAMBDA_VPC_FN}",  # 2.8.0(P2-6) — VPC Lambda 공유 ENI
                f"elasticache_cluster/{A}/{R}/{W.CACHE}",  # 2.9.0 — ENI 설명의 클러스터 id 낱말(#114)
                f"rds_db_cluster/{A}/{R}/{W.AURORA}",  # 2.9.0 — 그 AZ 의 DB 둘이 다 한 클러스터의 멤버
                f"opensearch_domain/{A}/{R}/{W.SEARCH}",  # 2.9.0 — ENI 설명의 도메인 이름 낱말
                f"msk_cluster/{A}/{R}/{W.KAFKA}",  # 2.9.0 — 브로커 ENI id(`ListNodes`) · 브로커 둘 = 선 하나
                *[f"external_ip/{A}/{R}/{ip}" for ip in W.EXTERNAL],
            ]
        )
    )
    assert (
        sorted(_edges(doc, lb("int-alb")))
        == sorted(
            [
                f"ec2_instance/{A}/{R}/{W.WORKER}",
                f"ecs_service/{A}/{R}/lbt-ecs%2Fbridge-svc",  # 2.8.0 — instance 대상(컨테이너 인스턴스)이어도 그룹을 든 서비스
                f"ecs_service/{A}/{R}/lbt-ecs%2Fidle-svc",  # 2.8.0 — 등록 0 이어도 그룹을 든 서비스
            ]
        )
    )
    assert nlb[lb("web-alb")]["attributes"]["target_types"] == ["alb"]
    assert nlb[lb("int-alb")]["attributes"]["target_types"] == ["ip"]
    for e in doc["edges"]:
        if e["type"] == "load_balancer_target":
            assert e["directed"] is True and e["source"].startswith("load_balancer/")
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))


def test_lambda_and_external_ip_are_minimal_derived_nodes():
    doc = _doc()
    fn = _node(doc, f"lambda_function/{A}/{R}/thumbnailer")
    assert fn["parent_id"] == f"region/{A}/{R}"
    assert fn["attributes"] == {"function_name": "thumbnailer", "qualifiers": ["live"]}
    assert fn["arn"] == W.FN_ARN  # 별칭을 뗀 함수 ARN
    exits = {
        ip: _node(doc, f"external_ip/{A}/{R}/{ip}")["attributes"]["route_exits"]
        for ip in W.EXTERNAL
    }
    # 가장 긴 접두사 — 172.16.0.0/12 → TGW · 192.168.0.0/16 → VGW(전파 라우트도 라우트다)
    assert exits == {
        "172.16.5.9": [W.TGW],
        "192.168.10.5": [W.VGW],
        "192.168.10.6": [W.VGW],
    }
    edge = _edges(doc, lb("edge-nlb"))[f"external_ip/{A}/{R}/192.168.10.5"]
    assert edge["attributes"]["route_exits"] == [W.VGW]
    assert set(DERIVED_TYPES) == set(SCHEMA["$defs"]["node_type"]["enum"]) - set(
        SCHEMA["$defs"]["resource_type"]["enum"]
    ) - {"account", "region"}


def test_longest_prefix_wins_for_the_route_exit():
    """더 넓은 라우트(10.0.0.0/8 → TGW)와 더 좁은 라우트(192.168.10.0/24 → VGW)가 겹쳐도 좁은 쪽이 출구다."""
    client = W.world()
    main = next(
        rt
        for rt in client._data["describe_route_tables"][1]
        if rt["RouteTableId"] == W.RT_MAIN
    )
    main["Routes"] = [
        {
            "DestinationCidrBlock": "192.168.0.0/16",
            "TransitGatewayId": W.TGW,
            "State": "active",
        },
        {
            "DestinationCidrBlock": "192.168.10.0/24",
            "GatewayId": W.VGW,
            "State": "active",
        },
        {
            "DestinationCidrBlock": "172.16.0.0/12",
            "TransitGatewayId": W.TGW,
            "State": "active",
        },
    ]
    doc = _doc(client)
    assert _node(doc, f"external_ip/{A}/{R}/192.168.10.5")["attributes"][
        "route_exits"
    ] == [W.VGW]


# ------------------------------------------------------------------ ② 묶기


def test_one_edge_per_target_node_with_grouped_registrations():
    """ECS 태스크 IP 둘 = 선 하나(등록 2) · 같은 인스턴스를 두 그룹·두 포트로 = 선 하나(등록 2)."""
    doc = _doc()
    ecs = _edges(doc, lb("web-alb"))[f"ecs_service/{A}/{R}/lbt-ecs%2Fapi-svc"][
        "attributes"
    ]
    assert ecs == {
        "target_groups": ["api-tg"],
        "ports": [8080],
        "target_types": ["ip"],
        "target_count": 2,
    }
    client = W.world()
    client._keyed["describe_target_groups"][2][W.INT_ALB_ARN].append(
        W._tg("svc", "int-tg-b", "instance", 9090)
    )
    client._target_health[W.TG["svc"]] = [W._target(W.WORKER, 9090)]
    edge = _edges(_doc(client), lb("int-alb"))[f"ec2_instance/{A}/{R}/{W.WORKER}"][
        "attributes"
    ]
    assert edge == {
        "target_groups": ["int-tg", "int-tg-b"],
        "ports": [8080, 9090],
        "target_types": ["instance"],
        "target_count": 2,
    }
    # 🔴 IP 단위 선이 없다 — 태스크 IP 가 배포마다 바뀌어도 선 개수는 그대로다
    assert not [
        e
        for e in doc["edges"]
        if e["type"] == "load_balancer_target" and "10.20." in e["target"]
    ]


def test_ecs_target_group_wins_over_the_eni():
    """그 대상 그룹을 든 ECS 서비스가 하나면 그 서비스다 — 태스크 ENI 설명(ECS 첨부 ARN)으로 가지 않는다."""
    client = W.world()
    doc = _doc(client)
    assert f"ecs_service/{A}/{R}/lbt-ecs%2Fapi-svc" in _edges(doc, lb("web-alb"))
    # 서비스가 그 그룹을 안 들면 같은 ENI 는 「ECS 태스크 — 서비스를 특정 못 함」으로 남는다(지어내지 않는다)
    client._ecs[W.ECS_SERVICE_ARN] = {
        **client._ecs[W.ECS_SERVICE_ARN],
        "loadBalancers": [],
    }
    doc = _doc(client)
    assert f"ecs_service/{A}/{R}/lbt-ecs%2Fapi-svc" not in _edges(doc, lb("web-alb"))
    assert {"10.20.11.50 ecs_task", "10.20.12.51 ecs_task"} <= set(
        _node(doc, lb("web-alb"))["attributes"]["unresolved_targets"]
    )


ASG_LINE = f"auto_scaling_group/{A}/{R}/{W.ASG}"
ASG_IDLE_LINE = f"auto_scaling_group/{A}/{R}/{W.ASG_IDLE}"
BRIDGE = f"ecs_service/{A}/{R}/lbt-ecs%2Fbridge-svc"
IDLE = f"ecs_service/{A}/{R}/lbt-ecs%2Fidle-svc"


def test_ecs_service_owns_its_instance_target_group():
    """2.8.0(사용자 결정 2026-10-20 P1-1) — ECS on EC2(bridge)의 대상은 컨테이너 인스턴스(`instance`)다. 그 그룹을 든 서비스가
    하나면 등록은 **서비스 선 하나**로 간다 — 호스트 EC2 로 가는 선은 없다(있으면 「이 LB 뒤의 서비스」가 안 보이고 호스트만 보인다)."""
    doc = _doc()
    edges = _edges(doc, lb("int-alb"))
    e = edges[BRIDGE]
    assert e["attributes"] == {
        "target_groups": ["bridge-tg"],
        "ports": [32768, 32769],  # 대상 포트(동적) — 그룹 포트 80 을 섞지 않는다
        "target_types": ["instance"],
        "target_count": 3,
    }
    for host in W.ECS_HOSTS:
        assert f"ec2_instance/{A}/{R}/{host}" not in edges
    # 같은 그룹을 서비스 둘이 들면 고르지 않는다 — 인스턴스 대상은 원래대로 EC2 로 간다(지어내지 않는다)
    client = W.world()
    twin = {
        **client._ecs[W.ECS_BRIDGE_ARN],
        "serviceArn": W.ECS_BRIDGE_ARN + "-2",
        "serviceName": "bridge-svc-2",
    }
    client._ecs[W.ECS_BRIDGE_ARN + "-2"] = twin
    key, kwarg, listed = client._keyed["list_services"]
    client._keyed["list_services"] = (
        key,
        kwarg,
        {W.ECS_CLUSTER_ARN: [*listed[W.ECS_CLUSTER_ARN], W.ECS_BRIDGE_ARN + "-2"]},
    )
    edges = _edges(_doc(client), lb("int-alb"))
    assert BRIDGE not in edges
    assert {f"ec2_instance/{A}/{R}/{h}" for h in W.ECS_HOSTS} <= set(edges)


def test_a_claimed_group_without_registrations_is_still_a_line():
    """2.8.0 — 원하는 수 0 인 서비스도 「이 LB 뒤의 서비스」다(등록 0 · 그룹 포트 · 그룹 종류). 계약은 0 을 그 경우에만 받는다."""
    doc = _doc()
    e = _edges(doc, lb("int-alb"))[IDLE]
    assert e["attributes"] == {
        "target_groups": ["idle-tg"],
        "ports": [8080],
        "target_types": ["ip"],
        "target_count": 0,
    }
    validator = jsonschema.Draft202012Validator(SCHEMA)
    assert not list(validator.iter_errors(doc))
    # 같은 0 이 EC2 로 가는 선이면 계약 위반이다(그 밖의 대상은 등록이 있어야 생긴다)
    ec2 = next(
        x
        for x in doc["edges"]
        if x["type"] == "load_balancer_target"
        and x["target"].startswith("ec2_instance/")
    )
    broken = {**ec2, "attributes": {**ec2["attributes"], "target_count": 0}}
    bad = {**doc, "edges": [broken if x is ec2 else x for x in doc["edges"]]}
    assert list(validator.iter_errors(bad)), "EC2 로 가는 등록 0 선을 계약이 받아들였다"
    # 그룹을 못 읽은 LB 는 빈 그룹 선을 짓지 않는다(어느 그룹인지 모른다 · 불변식 ④)
    client = W.world(
        followup_errors={
            ("describe_target_groups", W.INT_ALB_ARN): client_error(
                "AccessDenied", "DescribeTargetGroups"
            )
        }
    )
    doc = _doc(client)
    assert IDLE not in _edges(doc, lb("int-alb"))
    assert _node(doc, lb("int-alb"))["partial"] is True


def test_a_vpc_lambda_eni_belongs_to_the_one_function_with_that_subnet_and_groups():
    """2.8.0(사용자 결정 2026-10-20 P2-6) — VPC Lambda 의 ENI 는 같은 서브넷·SG 조합의 함수끼리 공유된다. 맞는 수집한 함수가 하나면 그
    함수 · 둘이면 고르지 않는다(`ambiguous`) · 수집한 함수가 LB 의 Lambda 대상이면 파생 노드를 짓지 않는다(같은 id)."""
    doc = _doc()
    fn = f"lambda_function/{A}/{R}/{W.LAMBDA_VPC_FN}"
    assert _edges(doc, lb("edge-nlb"))[fn]["attributes"]["target_types"] == ["ip"]
    node = _node(doc, fn)
    assert node["parent_id"] == f"vpc/{A}/{R}/{W.VPC}"
    assert node["attributes"]["subnet_ids"] == [W.SN["data-b"][0]]
    assert [n["id"] for n in doc["nodes"]].count(fn) == 1
    # 같은 서브넷·SG 의 함수가 하나 더 있으면 공유 ENI 의 주인을 고르지 않는다
    client = W.world()
    key, fns = client._data["list_functions"]
    client._data["list_functions"] = (
        key,
        [
            *fns,
            {
                **fns[0],
                "FunctionName": "orders-fn-2",
                "FunctionArn": fns[0]["FunctionArn"] + "-2",
            },
        ],
    )
    doc = _doc(client)
    assert fn not in _edges(doc, lb("edge-nlb"))
    assert (
        f"{W.LAMBDA_ENI_IP} ambiguous"
        in _node(doc, lb("edge-nlb"))["attributes"]["unresolved_targets"]
    )
    # 함수를 수집하지 않으면 설명의 이름으로 찾다가 없다 → `lambda`(수집하지 않은 Lambda) — 지어내지 않는다
    narrow = Scope(
        account_ids=(A,),
        regions=(R,),
        resource_types=tuple(
            t for t in SUPPORTED_RESOURCE_TYPES if t != "lambda_function"
        ),
    )
    doc = _snap(W.world(), narrow).to_document()
    assert (
        f"{W.LAMBDA_ENI_IP} lambda"
        in _node(doc, lb("edge-nlb"))["attributes"]["unresolved_targets"]
    )
    # LB 의 Lambda 대상(thumbnailer)은 수집하지 않은 함수라 예전처럼 파생 노드다(리전 부모)
    assert (
        _node(doc, f"lambda_function/{A}/{R}/thumbnailer")["parent_id"]
        == f"region/{A}/{R}"
    )
    # 그 함수를 **수집하면** 파생 노드를 짓지 않는다 — 같은 id 의 노드는 하나(수집한 쪽 · 런타임을 안다)
    client = W.world()
    key, fns = client._data["list_functions"]
    thumb = {
        "FunctionName": "thumbnailer",
        "FunctionArn": W.FN_ARN,
        "Runtime": "nodejs20.x",
        "VpcConfig": {"VpcId": "", "SubnetIds": [], "SecurityGroupIds": []},
    }
    client._data["list_functions"] = (key, [*fns, thumb])
    doc = _doc(client)
    tid = f"lambda_function/{A}/{R}/thumbnailer"
    assert [n["id"] for n in doc["nodes"]].count(tid) == 1
    assert _node(doc, tid)["attributes"]["runtime"] == "nodejs20.x"
    assert tid in _edges(doc, lb("web-alb"))


def test_an_lb_controller_target_group_belongs_to_its_eks_cluster():
    """2.8.0(사용자 결정 2026-10-20 P2-5) — 대상 그룹 태그 `elbv2.k8s.aws/cluster` = 그 클러스터의 Ingress/Service. 파드 IP 는 ENI 를
    묻지 않고도 그 클러스터 선 하나로 간다(ENI 가 이 세계에 없다 — 태그가 없으면 `no_eni` 로 남는다)."""
    doc = _doc()
    e = _edges(doc, lb("web-alb"))[f"eks_cluster/{A}/{R}/{W.EKS}"]
    assert e["attributes"] == {
        "target_groups": ["k8s-web-tg"],
        "ports": [8080],
        "target_types": ["ip"],
        "target_count": 2,
    }
    # 태그를 못 읽으면 LB 가 「일부 못 읽음」 · 파드 IP 는 원래 해소(ENI 없음 → no_eni)로 — 클러스터를 지어내지 않는다
    client = W.world()
    client._errors["describe_tags"] = client_error("AccessDenied", "DescribeTags")
    doc = _doc(client)
    assert f"eks_cluster/{A}/{R}/{W.EKS}" not in _edges(doc, lb("web-alb"))
    web = _node(doc, lb("web-alb"))
    assert web["partial"] is True
    assert "elasticloadbalancing:DescribeTags" in [
        r["api"] for r in web["partial_reasons"]
    ]
    assert {f"{ip} no_eni" for ip in W.POD_IPS} <= set(
        web["attributes"]["unresolved_targets"]
    )
    # 태그는 대상 그룹이 있는 LB 마다 묻는다(그룹 ARN 만 · 정렬)
    client = W.world()
    _doc(client)
    asked = [
        kw["ResourceArns"] for m, kw in client.direct_calls if m == "describe_tags"
    ]
    assert asked and W.TG["k8s"] in [a for x in asked for a in x]
    assert all(x == sorted(x) and len(x) <= 20 for x in asked)


def test_asg_instances_carry_their_group_and_the_group_its_target_groups():
    """2.8.0(사용자 결정 2026-10-20 P1-2) — 화면이 LB → 인스턴스 선을 **LB → ASG 선 하나**로 접는 재료 둘(§17.27 「ASG」 행).

    ① ASG 노드 = 구성 `target_group_arns` · 🔴 인스턴스 목록은 ASG 노드에 없다(스케일마다 「바뀜」이 된다)
    ② 인스턴스 노드 = `auto_scaling_group_id`(그 ASG 의 지금 인스턴스일 때만) — 손으로 등록한 남의 인스턴스에는 없다
    엔진의 선은 인스턴스로 간다 — 상태 단이 ASG 를 안 부르므로 엔진이 접으면 구조·상태의 선 id 가 갈린다.
    """
    doc = _doc()
    node = _node(doc, ASG_LINE)
    assert node["attributes"]["target_group_arns"] == [W.TG["asg"]]
    dumped = json.dumps(node)
    for iid in W.ASG_INSTANCES:
        assert iid not in dumped
        assert (
            _node(doc, f"ec2_instance/{A}/{R}/{iid}")["attributes"][
                "auto_scaling_group_id"
            ]
            == ASG_LINE
        )
    assert (
        "auto_scaling_group_id"
        not in _node(doc, f"ec2_instance/{A}/{R}/{W.WORKER}")["attributes"]
    )
    web = _edges(doc, lb("web-alb"))
    assert web[f"ec2_instance/{A}/{R}/{W.ASG_INSTANCES[1]}"]["attributes"][
        "target_groups"
    ] == ["asg-tg"]
    # 손으로 다른 그룹에도 등록한 ASG 인스턴스 — 선은 하나(그룹 둘) · 화면은 이 선을 ASG 로 접지 않는다(§17.27)
    assert web[f"ec2_instance/{A}/{R}/{W.ASG_INSTANCES[0]}"]["attributes"][
        "target_groups"
    ] == ["asg-tg", "web-tg"]
    assert ASG_LINE not in web  # 엔진은 ASG 로 가는 선을 만들지 않는다
    # 원하는 수 0 인 ASG — 그룹은 구성으로 들지만 등록이 없으니 선이 없다(정보창이 「등록 0」을 말한다)
    assert _node(doc, ASG_IDLE_LINE)["attributes"]["target_group_arns"] == [
        W.TG["standby"]
    ]
    assert ASG_IDLE_LINE not in _edges(doc, lb("int-alb"))
    # 상태 단(ASG 를 안 부른다)도 같은 선 id 를 낸다 — 선 헬스 행이 구조에 붙는다
    _observe(W.world())
    # 계약 — 소속은 ASG 노드 id 여야 한다(이름·ARN 이면 화면이 계정·리전을 다시 맞춰야 한다)
    validator = jsonschema.Draft202012Validator(SCHEMA)
    assert not list(validator.iter_errors(doc))
    member = _node(doc, f"ec2_instance/{A}/{R}/{W.ASG_INSTANCES[1]}")
    bad = {
        **doc,
        "nodes": [
            {**n, "attributes": {**n["attributes"], "auto_scaling_group_id": W.ASG}}
            if n is member
            else n
            for n in doc["nodes"]
        ],
    }
    assert list(validator.iter_errors(bad)), (
        "ASG 이름을 소속으로 받았다(노드 id 여야 한다)"
    )


def test_two_groups_claiming_one_instance_label_neither():
    """같은 인스턴스를 ASG 둘이 말하면(AWS 가 허용하지 않는 모양) 소속을 달지 않는다 — 고르지 않는다."""
    client = W.world()
    key, asgs = client._data["describe_auto_scaling_groups"]
    twin = {
        **asgs[0],
        "AutoScalingGroupName": "web-asg-twin",
        "AutoScalingGroupARN": asgs[0]["AutoScalingGroupARN"] + "-twin",
    }
    client._data["describe_auto_scaling_groups"] = (key, [*asgs, twin])
    doc = _doc(client)
    for iid in W.ASG_INSTANCES:
        assert (
            "auto_scaling_group_id"
            not in _node(doc, f"ec2_instance/{A}/{R}/{iid}")["attributes"]
        )


# ------------------------------------------------------------------ ③ 못 찾은 대상


def test_unresolved_targets_are_recorded_with_why_and_never_invented():
    doc = _doc()
    web = _node(doc, lb("web-alb"))["attributes"]
    nlb = _node(doc, lb("edge-nlb"))["attributes"]
    assert web["unresolved_targets"] == [f"{W.GONE_INSTANCE} not_collected"]
    assert nlb["unresolved_targets"] == [
        f"{W.UNRESOLVED_IP} no_eni",
        f"{W.EFS_IP} efs",
    ]
    assert set(nlb["unresolved_refs"]) == {W.UNRESOLVED_IP, W.EFS_IP}
    # 노드를 짓지 않는다 — 수집하지 않는 서비스(EFS)·주인 없는 IP 는 외부 IP 도 아니다
    assert not [
        n for n in doc["nodes"] if n["native_id"] in (W.UNRESOLVED_IP, W.EFS_IP)
    ]
    # 2.9.0(#114) — ElastiCache ENI 는 이제 그 캐시 카드다(그 전엔 이 자리가 「수집하지 않는 서비스」였다)
    assert f"elasticache_cluster/{A}/{R}/{W.CACHE}" in _edges(doc, lb("edge-nlb"))
    # 못 이은 것은 「수집 실패」가 아니다 — partial 이 아니다(ADR 0002 §5)
    assert _node(doc, lb("edge-nlb"))["partial"] is False


@pytest.mark.parametrize(
    ("desc", "extra", "code"),
    [
        # 2.9.0 — 수집하는 서비스인데 그 카드가 이 스냅샷에 없다(낱말이 수집한 클러스터 id 와 안 맞는다) = 서비스 코드 그대로
        ("ElastiCache other-cache-0001-001", {}, "elasticache"),
        # 🔴 부분 문자열로 고르지 않는다 — `lbt-cache-0001-0010` 은 `lbt-cache-0001-001` 카드가 아니다
        (f"ElastiCache {W.CACHE}0", {}, "elasticache"),
        ("EFS mount target for fs-0abc (fsmt-0abc)", {}, "efs"),
        ("AWS Lambda VPC ENI-resize-1234", {"InterfaceType": "lambda"}, "lambda"),
        (
            "Interface for NAT Gateway nat-0abc",
            {"InterfaceType": "nat_gateway"},
            "nat_gateway",
        ),
        # 2.9.0 — 수집하는 서비스인데 그 도메인 카드가 없다(낱말이 수집한 도메인 이름과 안 맞는다) = 서비스 코드 그대로
        ("ES other-search", {}, "opensearch"),
        ("arn:aws:ecs:us-east-1:111122223333:attachment/0f", {}, "ecs_task"),
        (
            "RDSNetworkInterface",
            # 그 VPC·AZ 에 DB 가 없다 — 2.9.0 부터 두 AZ 다 DB 가 있어(Aurora 멤버) 세 번째 AZ 로 둔다
            {"RequesterId": "amazon-rds", "AvailabilityZone": "us-east-1c"},
            "rds",
        ),
        ("some appliance", {}, "eni"),
        ("", {"InterfaceType": "efa"}, "efa"),
    ],
)
def test_uncollected_owner_is_named(desc, extra, code):
    """주인이 수집하지 않는 서비스면 그 서비스로 적는다 — 화면이 「대상: 10.x (… — 수집하지 않는 서비스)」로 말한다."""
    client = W.world()
    client._enis = [
        e for e in client._enis if e["PrivateIpAddress"] != W.CACHE_NODE_IP
    ] + [
        W._eni(
            "eni-0abc1234def57099",
            W.CACHE_NODE_IP,
            "app-b" if code == "rds" else "data-b",
            Description=desc,
            **extra,
        )
    ]
    attrs = _node(_doc(client), lb("edge-nlb"))["attributes"]
    assert f"{W.CACHE_NODE_IP} {code}" in attrs["unresolved_targets"]


def test_out_of_scope_lambda_and_alb_are_not_drawn():
    client = W.world()
    other = "444455556666"
    client._target_health[W.TG["fn"]] = [
        W._target(f"arn:aws:lambda:{R}:{other}:function:remote")
    ]
    client._target_health[W.TG["alb"]] = [
        W._target(
            f"arn:aws:elasticloadbalancing:{R}:{other}:loadbalancer/app/far-alb/0f0e0d0c0b0a0908",
            80,
        )
    ]
    doc = _doc(client)
    # 범위 밖 함수는 짓지 않는다 — 남는 Lambda 노드는 이 계정에서 **수집한** 함수뿐이다(2.8.0 · P2-6)
    assert [n["id"] for n in doc["nodes"] if n["type"] == "lambda_function"] == [
        f"lambda_function/{A}/{R}/{W.LAMBDA_VPC_FN}"
    ]
    assert (
        f"arn:aws:lambda:{R}:{other}:function:remote not_in_scope"
        in _node(doc, lb("web-alb"))["attributes"]["unresolved_targets"]
    )
    assert any(
        t.endswith(" not_collected") and "far-alb" in t
        for t in _node(doc, lb("edge-nlb"))["attributes"]["unresolved_targets"]
    )


@pytest.mark.parametrize(
    ("eni", "node_type", "native"),
    [
        (
            dict(
                Description="",
                TagSet=[{"Key": "cluster.k8s.amazonaws.com/name", "Value": "orders"}],
                Attachment={"InstanceId": W.WORKER},
            ),
            "eks_cluster",
            "orders",
        ),
        (
            dict(Description="Amazon EKS orders", RequesterId="123456789012"),
            "eks_cluster",
            "orders",
        ),
        (
            dict(
                Description="EC2 Instance Connect Endpoint eice-0abc1234def5aaaa",
                InterfaceType="ec2_instance_connect_endpoint",
            ),
            "ec2_instance_connect_endpoint",
            "eice-0abc1234def5aaaa",
        ),
    ],
)
def test_eks_and_eic_owners(eni, node_type, native):
    """EKS — VPC CNI 태그(부착 인스턴스보다 앞선다) · 제어부 설명 / EIC — 설명의 엔드포인트 id."""
    client = W.world()
    client._data["list_clusters"] = ("clusters", ["orders"])
    client._enis.append(W._eni("eni-0abc1234def57050", "10.20.12.77", "app-b", **eni))
    client._target_health[W.TG["svc"]].append(
        W._target("10.20.12.77", 443, "us-east-1b")
    )
    client._data["describe_instance_connect_endpoints"] = (
        "InstanceConnectEndpoints",
        [
            {
                "InstanceConnectEndpointId": "eice-0abc1234def5aaaa",
                "OwnerId": A,
                "State": "create-complete",
                "VpcId": W.VPC,
                "SubnetId": W.SN["app-b"][0],
                "AvailabilityZone": "us-east-1b",
                "SecurityGroupIds": [],
                "Tags": [],
            }
        ],
    )
    import fakes

    saved = dict(fakes.EKS_CLUSTERS)
    fakes.EKS_CLUSTERS["orders"] = {
        "name": "orders",
        "status": "ACTIVE",
        "version": "1.31",
        "resourcesVpcConfig": {
            "vpcId": W.VPC,
            "subnetIds": [W.SN["app-a"][0], W.SN["app-b"][0]],
            "securityGroupIds": [],
        },
    }
    try:
        doc = _doc(client)
    finally:
        fakes.EKS_CLUSTERS.clear()
        fakes.EKS_CLUSTERS.update(saved)
    assert f"{node_type}/{A}/{R}/{native}" in _edges(doc, lb("edge-nlb"))


# ------------------------------------------------------------------ ④ 부분 실패


def test_one_denied_target_group_keeps_the_other_groups_lines():
    client = W.world(
        followup_errors={
            ("describe_target_health", W.TG["svc"]): client_error(
                "AccessDenied", "DescribeTargetHealth"
            )
        }
    )
    doc = _doc(client)
    nlb = _node(doc, lb("edge-nlb"))
    assert nlb["partial"] is True
    assert [r["api"] for r in nlb["partial_reasons"]] == [
        "elasticloadbalancing:DescribeTargetHealth"
    ]
    edges = _edges(doc, lb("edge-nlb"))
    assert (
        lb("web-alb") in edges and f"external_ip/{A}/{R}/172.16.5.9" in edges
    )  # 다른 그룹의 선은 남는다
    assert (
        f"rds_db_instance/{A}/{R}/{W.RDS}" not in edges
    )  # 못 읽은 그룹의 선은 지어내지 않는다


def test_a_denied_eni_lookup_marks_the_asking_load_balancers_partial():
    client = W.world(
        errors={
            "describe_network_interfaces": client_error(
                "UnauthorizedOperation", "DescribeNetworkInterfaces"
            )
        }
    )
    doc = _doc(client)
    nlb = _node(doc, lb("edge-nlb"))
    assert nlb["partial"] is True
    assert ("access_denied", "ec2:DescribeNetworkInterfaces") in {
        (r["code"], r["api"]) for r in nlb["partial_reasons"]
    }
    assert f"{W.CACHE_NODE_IP} eni_unread" in nlb["attributes"]["unresolved_targets"]
    # ENI 가 필요 없는 선(alb · 외부 IP · ECS 그룹)은 그대로 생긴다 — 외부 IP 는 VPC CIDR 로 판정한다
    edges = _edges(doc, lb("edge-nlb"))
    assert lb("web-alb") in edges and f"external_ip/{A}/{R}/192.168.10.5" in edges
    assert f"ecs_service/{A}/{R}/lbt-ecs%2Fapi-svc" in _edges(doc, lb("web-alb"))
    # 물은 적 없는 LB(int-alb — 인스턴스 대상만)는 사유가 없다
    assert _node(doc, lb("int-alb"))["partial"] is False


def test_eni_lookups_are_filtered_by_vpc_and_address():
    client = W.world()
    _doc(client)
    # 가짜의 `_FilteredPages` 가 필터 없는 호출을 거부한다 — 한 번은 불렀다(VPC 하나)
    assert client.calls.count("describe_network_interfaces") == 1


# ------------------------------------------------------------------ ⑤ 결정론 · 헬스


def _shuffled() -> FakeAws:
    client = W.world()
    rng = random.Random(7)
    for key in client._target_health:
        rng.shuffle(client._target_health[key])
    rng.shuffle(client._enis)
    for arn in client._keyed["describe_target_groups"][2]:
        rng.shuffle(client._keyed["describe_target_groups"][2][arn])
    return client


def test_order_of_responses_does_not_change_the_bytes():
    assert to_json(_snap(W.world())) == to_json(_snap(_shuffled()))


def test_health_is_not_in_the_structure_and_flaps_keep_the_bytes():
    sick = W.world(health={"web": "unhealthy", "svc": "draining"})
    assert to_json(_snap(W.world())) == to_json(_snap(sick))
    for e in _doc(sick)["edges"]:
        assert (
            "healthy_target_count" not in e["attributes"]
            and "unhealthy_target_count" not in e["attributes"]
        )


def test_state_snapshot_carries_line_health_and_observe_judges_each_line():
    client = W.world(health={"web": "unhealthy", "api": "initial"})
    state = _snap(client, state=True).to_document()
    web = _edges(state, lb("web-alb"))
    one = web[f"ec2_instance/{A}/{R}/{W.WEB[0]}"]["attributes"]
    assert (one["healthy_target_count"], one["unhealthy_target_count"]) == (0, 1)
    _, obs = _observe(client)
    rows = {o["target_id"]: o for o in obs["observations"]}
    down = rows[web[f"ec2_instance/{A}/{R}/{W.WEB[0]}"]["id"]]
    assert down["status"] == "down" and down["targets"] == _t(1, 0, 1)
    # 등록 중(initial)뿐인 선은 `up` 이다 — 배포할 때마다 빨갛게 하지 않는다
    ecs = rows[web[f"ecs_service/{A}/{R}/lbt-ecs%2Fapi-svc"]["id"]]
    assert ecs["status"] == "up" and ecs["targets"] == _t(2, 0, 0, initial=2)
    # 파생 노드(Lambda · 외부 IP)는 행이 없다 — 상태 없음(ADR 0006 §O7)
    assert not [t for t in rows if t.startswith(("lambda_function/", "external_ip/"))]
    jsonschema.Draft202012Validator(OBS_SCHEMA).validate(obs)


def test_a_lambda_target_with_the_health_check_off_is_counted_apart_and_not_unhealthy():
    """#137 · 관측 1.15.0 — 세계의 Lambda 대상 = AWS `unavailable` + `Target.HealthCheckDisabled`(헬스 체크 꺼짐).

    선 행 · LB 행 모두 「헬스 체크 꺼짐 1」로 따로 세고, 비정상에도 「그 밖의 unavailable」에도 넣지 않는다. 판정은 그대로 `up`.
    """
    client = W.world()
    state = _snap(client, state=True).to_document()
    fn = _edges(state, lb("web-alb"))
    line = next(e for t, e in fn.items() if t.startswith("lambda_function/"))
    assert (
        line["attributes"]["health_check_off_target_count"],
        line["attributes"]["unhealthy_target_count"],
        line["attributes"]["other_unavailable_target_count"],
    ) == (1, 0, 0)
    _, obs = _observe(client)
    rows = {o["target_id"]: o for o in obs["observations"]}
    assert rows[line["id"]]["targets"] == _t(1, 0, 0, health_check_off=1)
    assert rows[line["id"]]["status"] == "up"
    web = rows[lb("web-alb")]
    total = web["targets"]["total"]
    assert web["targets"] == _t(total, total - 1, 0, health_check_off=1)
    assert (web["status"], web["why"]) == ("up", None)
    jsonschema.Draft202012Validator(OBS_SCHEMA).validate(obs)


def test_a_denied_group_makes_every_line_of_that_lb_partial_without_counts():
    client = W.world(
        followup_errors={
            ("describe_target_health", W.TG["svc"]): client_error(
                "AccessDenied", "DescribeTargetHealth"
            )
        }
    )
    edge = _edges(_snap(client, state=True).to_document(), lb("edge-nlb"))[
        lb("web-alb")
    ]
    assert "healthy_target_count" not in edge["attributes"]
    _, obs = _observe(client)
    row = next(o for o in obs["observations"] if o["target_id"] == edge["id"])
    assert row["status"] == "up" and row["partial"] is True and "targets" not in row
    jsonschema.Draft202012Validator(OBS_SCHEMA).validate(obs)


def test_derived_nodes_have_no_observation_rows():
    topology, obs = _observe(W.world())
    # 그 노드가 **있는** 문서다(s3_bucket 은 CloudFront 원본에서만 생긴다 — `test_cloudfront_origins.py` 가 잰다)
    assert {n["type"] for n in topology["nodes"]} >= set(DERIVED_TYPES) - {"s3_bucket"}
    kinds = {o["target_id"].split("/")[0] for o in obs["observations"]}
    assert "load_balancer" in kinds
    assert not kinds & set(DERIVED_TYPES)


# ------------------------------------------------------------------ ⑤' LB → VPC Lambda 선의 상태(#152 · ADR 0006 결정 15)

_spec = importlib.util.spec_from_file_location(
    "observation_oracle_lbt", ROOT / "schema" / "tests" / "test_observation_schema.py"
)
assert _spec is not None and _spec.loader is not None
ORACLE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ORACLE)

ORDERS_FN = f"lambda_function/{A}/{R}/{W.LAMBDA_VPC_FN}"
ORDERS_LINE = f"load_balancer_target|{lb('edge-nlb')}|{ORDERS_FN}"
THUMB_LINE = f"load_balancer_target|{lb('web-alb')}|lambda_function/{A}/{R}/thumbnailer"
STATE_SCOPE = Scope(
    account_ids=(A,), regions=(R,), resource_types=OBSERVED_RESOURCE_TYPES
)


def _three(targets: dict[str, int]) -> dict[str, int]:
    """판정이 보는 세 수만(#137 이 더하는 다섯 갈래와 무관하게 같은 사실을 잰다)."""
    return {k: targets[k] for k in ("total", "healthy", "unhealthy")}


def test_the_line_to_a_vpc_lambda_has_a_state_row_without_reading_lambda():
    """#152 — 상태 단은 Lambda 를 읽지 않는다(`OBSERVED_RESOURCE_TYPES`). 그래도 LB → VPC Lambda(공유 ENI) 선에 행이 있다:
    구조 문서의 함수 서브넷·보안 그룹으로 구조와 같은 선을 다시 잇고, 판정은 그 LB 가 이미 읽은 대상 헬스다(AWS 호출 0).

    사람이 보는 결과: 모니터링을 켜면 NLB → orders-fn 선도 다른 대상 선처럼 정상·이상을 말한다(그 전엔 아무 말이 없었다).
    """
    # 전제 — 관측 종류에 넣으면 이 길이 아니라 수집이 잇는다(그때는 이 검사를 다시 쓴다)
    assert "lambda_function" not in OBSERVED_RESOURCE_TYPES
    topology, obs = _observe(W.world())
    assert ORDERS_LINE in {e["id"] for e in topology["edges"]}
    rows = {o["target_id"]: o for o in obs["observations"]}
    row = rows.get(ORDERS_LINE)
    assert row is not None, "LB → VPC Lambda 선에 관측 행이 없다(#152 재발)"
    assert row["status"] == "up" and _three(row["targets"]) == {
        "total": 1,
        "healthy": 1,
        "unhealthy": 0,
    }
    assert row["evidence"]["api"] == "elasticloadbalancing:DescribeTargetHealth"
    # 계약 쪽 오라클 — 읽은 종류의 선에 행이 없으면(빈자리) 잡힌다. 이 세계에 알려진 빈자리는 없다.
    assert ORACLE.silent_where_read(obs, topology, ORACLE.o7_required_entry()) == []
    assert ORACLE.claims_without_reading(obs, topology) == []
    assert ORACLE.dangling_observations(obs, topology) == []
    jsonschema.Draft202012Validator(OBS_SCHEMA).validate(obs)
    # 그 함수 자신은 상태 없음이다(§O7) — 선만 행을 든다
    assert ORDERS_FN not in rows
    # 판정이 그 등록의 헬스를 따라간다 — 비정상이면 그 선이 이상이다
    _, sick = _observe(W.world(health={"svc": "unhealthy"}))
    bad = next(o for o in sick["observations"] if o["target_id"] == ORDERS_LINE)
    assert bad["status"] == "down" and _three(bad["targets"]) == {
        "total": 1,
        "healthy": 0,
        "unhealthy": 1,
    }


def test_the_state_stage_rejoins_the_vpc_lambda_with_no_extra_aws_call():
    """구조 문서의 함수로 잇는 것은 상태 단 전용이고 AWS 호출을 늘리지 않는다. 구조 수집에 주면 거절한다(구조가 직전 구조에
    기대면 불변식 ②가 깨진다). 넘기지 않으면 예전처럼 그 IP 는 `lambda` 로 남는다 — 지어내지 않는다."""
    seeded = structure_lambdas(_doc(W.world()))
    assert [k.node_id for k in seeded] == [ORDERS_FN]
    plain = W.world()
    without = _snap(plain, STATE_SCOPE, state=True).to_document()
    client = W.world()
    state = _snap(client, STATE_SCOPE, state=True, seeded=seeded).to_document()
    assert sorted(client.calls) == sorted(plain.calls)
    assert "list_functions" not in client.calls
    assert ORDERS_LINE in {e["id"] for e in state["edges"]}
    # 선의 끝 노드도 상태 스냅샷에 있다(선만 있고 끝이 없는 스냅샷을 만들지 않는다)
    assert ORDERS_FN in {n["id"] for n in state["nodes"]}
    assert ORDERS_LINE not in {e["id"] for e in without["edges"]}
    assert (
        f"{W.LAMBDA_ENI_IP} lambda"
        in _node(without, lb("edge-nlb"))["attributes"]["unresolved_targets"]
    )
    with pytest.raises(ValueError):
        _snap(W.world(), seeded=seeded)


def test_the_observe_command_passes_the_structure_lambdas(monkeypatch, tmp_path):
    """생산 경로(`nettopo observe` · 상태 Lambda 가 부르는 그 명령)가 구조 문서의 함수를 상태 단에 넘긴다 — 넘기지 않으면 위
    검사들은 초록인데 고객 화면의 그 선은 다시 조용하다(테스트 도우미 `_observe` 만 고친 꼴)."""
    from nettopo import cli

    class _Sts:
        def get_caller_identity(self) -> dict[str, str]:
            return {"Account": A, "Arn": "arn:aws:sts::secret:secret"}

    client = W.world()
    monkeypatch.setattr(
        cli,
        "read_only_client",
        lambda service, **_: _Sts() if service == "sts" else client,
    )
    topology = tmp_path / "topology.json"
    topology.write_text(to_json(_snap(W.world())), encoding="utf-8")
    out = tmp_path / "obs.json"
    args = ["observe", "--topology", str(topology), "--out", str(out)]
    assert cli.main([*args, "--no-sources"]) == 0
    obs = json.loads(out.read_text(encoding="utf-8"))
    row = next((o for o in obs["observations"] if o["target_id"] == ORDERS_LINE), None)
    assert row is not None and row["status"] == "up"
    assert "list_functions" not in client.calls


def test_a_lambda_target_with_the_health_check_off_is_not_down():
    """Lambda 대상 그룹은 헬스 체크가 기본으로 꺼져 있고 AWS 는 그 대상을 `unavailable` + `Target.HealthCheckDisabled` 로 답한다.
    그것은 정상도 비정상도 아니다(#137 과 같은 규칙) — 그 선을 이상으로 그리면 Lambda 를 붙인 LB 는 늘 빨갛다."""
    client = W.world()
    client._target_health[W.TG["fn"]] = [
        {
            "Target": {"Id": f"{W.FN_ARN}:live"},
            "TargetHealth": {
                "State": "unavailable",
                "Reason": "Target.HealthCheckDisabled",
                "Description": "Health checks disabled",
            },
        }
    ]
    _, obs = _observe(client)
    rows = {o["target_id"]: o for o in obs["observations"]}
    row = rows[THUMB_LINE]
    assert row["status"] == "up" and _three(row["targets"]) == {
        "total": 1,
        "healthy": 0,
        "unhealthy": 0,
    }
    assert rows[lb("web-alb")]["status"] == "up"
    jsonschema.Draft202012Validator(OBS_SCHEMA).validate(obs)


# ------------------------------------------------------------------ ⑥ 크로스계정


def test_outside_ip_in_another_accounts_collected_vpc_is_asked_there():
    other = "444455556666"
    far_vpc, far_sn = "vpc-0fed1234cba56001", "subnet-0fed1234cba56011"
    home = W.world()
    home._target_health[W.TG["onprem"]].append(W._target("10.30.1.10", 443, "all"))
    far = FakeAws.empty(
        vpcs=[
            {
                "VpcId": far_vpc,
                "CidrBlock": "10.30.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": [],
            }
        ],
        subnets=[
            {
                "SubnetId": far_sn,
                "VpcId": far_vpc,
                "CidrBlock": "10.30.1.0/24",
                "AvailabilityZone": "us-east-1a",
                "State": "available",
                "MapPublicIpOnLaunch": False,
                "Tags": [],
            }
        ],
        instances=[
            {
                "OwnerId": other,
                "Instances": [
                    {
                        "InstanceId": "i-0fed1234cba56101",
                        "InstanceType": "t3.micro",
                        "State": {"Name": "running"},
                        "Placement": {"AvailabilityZone": "us-east-1a"},
                        "SubnetId": far_sn,
                        "VpcId": far_vpc,
                        "PrivateIpAddress": "10.30.1.10",
                        "SecurityGroups": [],
                        "Tags": [],
                    }
                ],
            }
        ],
        network_interfaces=[
            {
                "NetworkInterfaceId": "eni-0fed1234cba57001",
                "VpcId": far_vpc,
                "SubnetId": far_sn,
                "AvailabilityZone": "us-east-1a",
                "PrivateIpAddress": "10.30.1.10",
                "PrivateIpAddresses": [{"PrivateIpAddress": "10.30.1.10"}],
                "InterfaceType": "interface",
                "Description": "",
                "Attachment": {"InstanceId": "i-0fed1234cba56101"},
            }
        ],
        web_acls={},
    )
    scope = Scope(
        account_ids=(A, other), regions=(R,), resource_types=SUPPORTED_RESOURCE_TYPES
    )
    doc = _snap(
        home, scope, fac=factory_by_pair({(A, R): home, (other, R): far})
    ).to_document()
    edges = _edges(doc, lb("edge-nlb"))
    assert f"ec2_instance/{other}/{R}/i-0fed1234cba56101" in edges
    assert (
        f"external_ip/{A}/{R}/10.30.1.10" not in edges
    )  # 수집한 VPC 안이면 외부 IP 가 아니다
    assert far.calls.count("describe_network_interfaces") == 1  # 그 계정 쌍에서 물었다


# ------------------------------------------------------------------ ⑦ fixture


def test_the_fixture_is_this_world_collected():
    """`fixtures/lb-targets.json` = 이 세계를 엔진에 돌린 바이트(렌더러 하네스 입력 · 검사 148).

    🔴 손으로 고친 fixture 는 엔진 모양에서 조용히 멀어진다 — 다시 쓰려면 `NETTOPO_REGEN_FIXTURE=1`.
    """
    text = to_json(_snap(W.world()))
    if os.environ.get("NETTOPO_REGEN_FIXTURE") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text


def test_the_observation_fixture_is_this_world_observed():
    """`fixtures/observation/lb-targets.json` = 이 세계를 엔진에 돌린 관측 문서 바이트(#137 · 렌더러 검사 129 `lbt` 의 입력).

    하네스가 「대상」 줄의 기대 글을 손으로 지은 행이 아니라 **엔진이 낸 행**에서 잰다 — Lambda 대상의 헬스 체크 꺼짐이 그대로 든다.
    🔴 다시 쓰려면 `NETTOPO_REGEN_FIXTURE=1`.
    """
    _, obs = _observe(W.world())
    text = dumps(obs)
    if os.environ.get("NETTOPO_REGEN_FIXTURE") == "1":
        OBS_FIXTURE.write_text(text, encoding="utf-8")
    assert OBS_FIXTURE.read_text(encoding="utf-8") == text


def test_the_contract_pins_the_line_shape():
    """스키마 2.7.0 — 대상 선은 LB 에서 나가고 · 방향이 있고 · 묶음 속성 넷을 든다. 하나라도 어기면 계약 위반이다."""
    doc = _doc()
    validator = jsonschema.Draft202012Validator(SCHEMA)
    assert not list(validator.iter_errors(doc))
    edge = next(e for e in doc["edges"] if e["type"] == "load_balancer_target")
    for broken in (
        {**edge, "source": f"ec2_instance/{A}/{R}/{W.WORKER}"},
        {**edge, "directed": False},
        {
            **edge,
            "attributes": {
                k: v for k, v in edge["attributes"].items() if k != "target_count"
            },
        },
        {**edge, "attributes": {**edge["attributes"], "target_types": ["udp"]}},
    ):
        bad = {**doc, "edges": [broken if e is edge else e for e in doc["edges"]]}
        assert list(validator.iter_errors(bad)), f"계약이 이 선을 받아들였다: {broken}"
    ext = next(n for n in doc["nodes"] if n["type"] == "external_ip")
    bad = {
        **doc,
        "nodes": [{**n, "attributes": {}} if n is ext else n for n in doc["nodes"]],
    }
    assert list(validator.iter_errors(bad)), "IP 없는 외부 IP 노드를 계약이 받아들였다"


def test_deregistering_a_target_is_a_structure_change_in_history(monkeypatch):
    """사용자 결정(2026-10-20): 대상의 정체는 구조다 — 해제하면 그 선이 이력에 「없어짐」 1줄로 남는다."""
    from nettopo.history import apply_history

    _, before = _observe(W.world())
    monkeypatch.setitem(globals(), "CLOCK", "2026-01-02T03:09:05Z")
    after_world = W.world()
    after_world._target_health[W.TG["int"]] = []
    _, after = _observe(after_world)
    report = apply_history(after, before)
    rows = [(r["kind"], r["target_id"]) for r in after["changes"]]
    assert report.new_rows == 1
    assert rows == [
        (
            "resource_removed",
            f"load_balancer_target|{lb('int-alb')}|ec2_instance/{A}/{R}/{W.WORKER}",
        )
    ]
