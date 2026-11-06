"""W3① — 구조가 일부러 안 싣는 상태 값을 **상태 단에서만** 읽어 판정한다(ADR 0006 §O7 「W3 판정 규칙」).

세 축을 잰다:
① 구조 바이트는 그대로다(불변식 ② — 탐침은 `collect(with_state=True)` 에서만 돈다)
② 값마다 `up`·`down` 이 맞게 갈린다(픽스처 기본값은 전부 `up` 이라 `down` 은 여기서만 잴 수 있다)
③ 상세가 거부된 노드는 지어내지 않고 `none` + 그 노드의 거부 사유다(불변식 ④)
그리고 TGW 라우트 테이블이 만든 경로 선의 칸 = **그 표**다(W2 에서 발견한 결함 — `route` 를 VPC 라우트
표 칸에 고정하던 동안 TGW 경로 선은 VPC 라우트 표가 거부되면 같이 죽었고 TGW 표가 거부돼도 살아 있었다).
"""

from __future__ import annotations

import dataclasses
from typing import Any

import fakes
import pytest
from fakes import (
    ACCOUNT,
    ECS_CLUSTER_ARN,
    ECS_SERVICE_ARN,
    EKS_CLUSTER_NAME,
    FW_ARN,
    REGION,
    SM_DOMAIN,
    SM_ENDPOINT,
    SM_NOTEBOOK,
    FakeAws,
    client_error,
    factory,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Reason, Scope
from nettopo.observe import NODE_RULES, OBSERVED_RESOURCE_TYPES
from nettopo.serialize import to_json
from nettopo.services import ecs_service
from test_observe import OBSERVED_AT, STRUCTURE_AT, _by_id, _observe, _snapshot

# 구조가 상태를 **안 싣고** 탐침으로만 읽는 종류. EC2·EIC 는 구조 속성 `state` 를 그대로 쓴다.
PROBED = (
    "rds_db_instance",
    "ecs_service",
    "sagemaker_notebook_instance",
    "sagemaker_domain",
    "sagemaker_endpoint",
    "eks_cluster",
    "network_firewall",
)
W3_JUDGED = (*PROBED, "ec2_instance", "ec2_instance_connect_endpoint")


def _of_type(document: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [
        o
        for o in document["observations"]
        if o["target_kind"] == "node" and o["target_id"].startswith(f"{node_type}/")
    ]


def _status_of(document: dict[str, Any], node_type: str) -> dict[str, Any]:
    found = _of_type(document, node_type)
    assert len(found) == 1, f"{node_type} 관측 {len(found)}개 — 1개를 기대했다"
    return found[0]


def _denied(method: str, key: str, op: str) -> dict[str, Any]:
    return {"followup_errors": {(method, key): client_error("AccessDenied", op)}}


# ------------------------------------------------------------------ ① 구조 바이트


def test_structure_collection_never_carries_the_probed_state():
    """🔴 탐침이 구조 단에서 돌면 상태가 바뀔 때마다 구조 해시가 바뀐다(불변식 ②) — 기본값은 끈다."""
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )
    plain = collect(scope, factory(FakeAws()), clock=lambda: STRUCTURE_AT)
    off = collect(
        scope, factory(FakeAws()), clock=lambda: STRUCTURE_AT, with_state=False
    )
    on = collect(scope, factory(FakeAws()), clock=lambda: STRUCTURE_AT, with_state=True)
    assert to_json(plain) == to_json(off)
    for t in PROBED:
        nodes = [n for n in plain.nodes if n.type == t]
        assert nodes, f"전제: {t} 가 구조에 있다"
        assert all("state" not in n.attributes for n in nodes), t
        # 대조군: 켜면 **실제로** 붙는다(안 붙으면 위 검사는 탐침이 죽어도 통과한다).
        assert all("state" in n.attributes for n in on.nodes if n.type == t), t
    assert to_json(plain) != to_json(on)


# ------------------------------------------------------------------ ② 판정


def test_every_w3_type_is_judged_up_from_the_default_fixture():
    document, _ = _observe()
    for t in W3_JUDGED:
        assert t in OBSERVED_RESOURCE_TYPES, t
        for o in _of_type(document, t) or [None]:
            assert o is not None, f"{t} 관측이 없다"
            assert (o["status"], o["partial"]) == ("up", False), (t, o["why"])
            assert o["evidence"]["api"] == NODE_RULES[t].api, t


@pytest.mark.parametrize(
    ("payload", "value"),
    [
        ({"status": "ACTIVE", "runningCount": 2, "desiredCount": 2}, "active"),
        (
            {"status": "ACTIVE", "runningCount": 1, "desiredCount": 3},
            "active-under-desired",
        ),
        (
            {"status": "ACTIVE", "runningCount": 0, "desiredCount": 2},
            "active-no-running-tasks",
        ),
        (
            {"status": "ACTIVE", "runningCount": 0, "desiredCount": 0},
            "active-desired-zero",
        ),
        ({"status": "DRAINING", "runningCount": 1, "desiredCount": 1}, "draining"),
        ({"status": "INACTIVE"}, "inactive"),
        # 수가 없다 = 상세를 못 읽었다 → 지어내지 않는다.
        ({"status": "ACTIVE"}, None),
        ({"clusterArn": "x"}, None),
    ],
)
def test_ecs_probe_folds_status_and_task_counts(payload, value):
    assert ecs_service.state(payload) == value


@pytest.mark.parametrize(
    ("running", "desired", "status", "why_has"),
    [
        (0, 2, "down", "실행 중인 태스크가 0개"),
        (0, 0, "down", "원하는 태스크 수가 0"),
        (1, 3, "up", None),
    ],
)
def test_an_ecs_service_without_running_tasks_is_down(
    monkeypatch: pytest.MonkeyPatch, running, desired, status, why_has
):
    """🔴 `ACTIVE` 만 보면 「서비스는 있는데 태스크 0개」가 `up` 이 된다 — ECS 에서 가장 흔한 장애다."""
    detail = dict(fakes.ECS_SERVICES[ECS_SERVICE_ARN])
    detail.update(runningCount=running, desiredCount=desired)
    monkeypatch.setitem(fakes.ECS_SERVICES, ECS_SERVICE_ARN, detail)
    o = _status_of(_observe()[0], "ecs_service")
    assert o["status"] == status
    if why_has is None:
        assert o["why"] is None
    else:
        assert why_has in o["why"]


def _set(monkeypatch: pytest.MonkeyPatch, table: Any, key: Any, field: str, value: str):
    # 원소 dict 를 직접 고친다 — 가짜는 모듈 수준 목록의 **같은 객체**를 돌려준다.
    monkeypatch.setitem(table[key], field, value)


@pytest.mark.parametrize(
    ("node_type", "patch", "status"),
    [
        ("rds_db_instance", "rds:stopped", "down"),
        ("rds_db_instance", "rds:available", "up"),
        ("sagemaker_notebook_instance", "nb:Stopped", "down"),
        ("sagemaker_domain", "domain:Failed", "down"),
        ("sagemaker_domain", "domain:Updating", "up"),
        ("sagemaker_endpoint", "ep:OutOfService", "down"),
        ("sagemaker_endpoint", "ep:SystemUpdating", "up"),
        ("eks_cluster", "eks:CREATING", "down"),
        ("eks_cluster", "eks:UPDATING", "up"),
        ("network_firewall", "fw:PROVISIONING", "down"),
    ],
)
def test_each_probe_value_lands_on_its_side(
    monkeypatch: pytest.MonkeyPatch, node_type: str, patch: str, status: str
):
    """값 하나를 바꾸면 **그 노드만** 뒤집힌다 — 기본 픽스처는 전부 `up` 이라 `down` 쪽은 여기서만 잰다."""
    kind, value = patch.split(":")
    if kind == "rds":
        item = dict(fakes.RDS_DB_INSTANCES[0], DBInstanceStatus=value)
        overrides: dict[str, Any] = {"rds_instances": [item]}
    else:
        overrides = {}
        if kind == "nb":
            _set(monkeypatch, fakes.SM_NOTEBOOKS, 0, "NotebookInstanceStatus", value)
            _set(
                monkeypatch,
                fakes.SM_NOTEBOOK_DETAILS,
                SM_NOTEBOOK,
                "NotebookInstanceStatus",
                value,
            )
        elif kind == "domain":
            _set(monkeypatch, fakes.SM_DOMAINS, 0, "Status", value)
            _set(monkeypatch, fakes.SM_DOMAIN_DETAILS, SM_DOMAIN, "Status", value)
        elif kind == "ep":
            _set(monkeypatch, fakes.SM_ENDPOINTS, 0, "EndpointStatus", value)
            _set(
                monkeypatch,
                fakes.SM_ENDPOINT_DETAILS,
                SM_ENDPOINT,
                "EndpointStatus",
                value,
            )
        elif kind == "eks":
            _set(monkeypatch, fakes.EKS_CLUSTERS, EKS_CLUSTER_NAME, "status", value)
        elif kind == "fw":
            detail = dict(fakes.NETWORK_FIREWALL_DETAILS[FW_ARN])
            detail["FirewallStatus"] = {**detail["FirewallStatus"], "Status": value}
            monkeypatch.setitem(fakes.NETWORK_FIREWALL_DETAILS, FW_ARN, detail)
    o = _status_of(_observe(**overrides)[0], node_type)
    assert o["status"] == status, o["why"]
    if status == "down":
        assert value in o["why"]  # 사람이 AWS 콘솔에서 볼 값 그대로


# ------------------------------------------------------------------ ③ 부분 노드


@pytest.mark.parametrize(
    ("node_type", "method", "key", "op", "api"),
    [
        (
            "ecs_service",
            "describe_services",
            ECS_CLUSTER_ARN,
            "DescribeServices",
            "ecs:DescribeServices",
        ),
        (
            "eks_cluster",
            "describe_cluster",
            EKS_CLUSTER_NAME,
            "DescribeCluster",
            "eks:DescribeCluster",
        ),
        (
            "network_firewall",
            "describe_firewall",
            FW_ARN,
            "DescribeFirewall",
            "network-firewall:DescribeFirewall",
        ),
    ],
)
def test_a_partial_node_without_a_state_value_is_none_with_its_reasons(
    node_type: str, method: str, key: str, op: str, api: str
):
    """🔴 상세가 거부되면 상태 값이 없다 — `up` 으로 채우면 「확인했다」가 거짓이고, 「규칙에 없다(개발)」는
    틀린 원인이다. 사유는 **그 노드의 거부 사유**다(ADR 0006 W3 규칙 3)."""
    o = _status_of(_observe(**_denied(method, key, op))[0], node_type)
    assert o["status"] == "none"
    assert o["partial"] is True
    assert "개발" not in o["why"]
    assert [(r["code"], r["api"]) for r in o["partial_reasons"]] == [
        ("access_denied", api)
    ]


def test_a_denied_notebook_detail_still_judges_from_the_list():
    """대조군 — 노트북 상태는 **목록**에도 있다. 상세만 거부되면 노드는 부분이지만 판정은 선다."""
    o = _status_of(
        _observe(
            **_denied(
                "describe_notebook_instance", SM_NOTEBOOK, "DescribeNotebookInstance"
            )
        )[0],
        "sagemaker_notebook_instance",
    )
    assert o["status"] == "up"


# ------------------------------------------------------------------ TGW 라우트 테이블 칸


def _deny_cell(state: Any, resource_type: str) -> Any:
    return dataclasses.replace(
        state,
        coverage=tuple(
            dataclasses.replace(
                c,
                status="denied",
                item_count=None,
                reason=Reason("access_denied", "테스트", "테스트"),
            )
            if c.resource_type == resource_type and c.region is not None
            else c
            for c in state.coverage
        ),
    )


def _route_edges(topology: dict[str, Any], source_type: str) -> set[str]:
    node_type = {n["id"]: n["type"] for n in topology["nodes"]}
    return {
        e["id"]
        for e in topology["edges"]
        if e["type"] == "route" and node_type.get(e["source"]) == source_type
    }


def test_tgw_route_edges_belong_to_the_tgw_route_table_cell():
    """TGW 표가 거부되면 그 경로 선이 **침묵**하고, VPC 라우트 표 선은 산다(양방향)."""
    from nettopo.observe import build_observation

    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    tgw = _route_edges(topology, "transit_gateway_route_table")
    vpc = _route_edges(topology, "route_table")
    assert tgw and vpc, "전제: 두 종류의 경로 선이 다 있다"

    state = _deny_cell(
        _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT), "transit_gateway_route_table"
    )
    document, _ = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )
    seen = set(_by_id(document))
    assert not tgw & seen
    assert vpc <= seen


def test_tgw_route_edge_is_up_with_its_own_evidence():
    """「(개발)」이 아니라 `up` + 근거 `SearchTransitGatewayRoutes` — 활성 경로만 선이 되기 때문이다."""
    document, _ = _observe()
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    by_id = _by_id(document)
    tgw = _route_edges(topology, "transit_gateway_route_table")
    assert tgw
    for e in tgw:
        assert by_id[e]["status"] == "up"
        assert by_id[e]["evidence"]["api"] == "ec2:SearchTransitGatewayRoutes"


# ------------------------------------------------------------------ 2층 칸


def test_metric_layer_on_says_skipped_for_types_without_a_probe():
    """🔴 §O7 이 지표를 적었지만 프로브가 아직 없는 6종 — 2층을 켜도 `skipped`(안 했다)다.

    `ok` 로 적으면 화면은 「쟀고 조용하다」가 된다 — 지표를 한 번도 안 물었는데.
    """
    from nettopo.metrics import METRIC_PENDING_TYPES
    from test_metrics import FakeCloudWatch, _metric_rows, _series
    from test_metrics import _observe as observe_with_metrics

    document, _ = observe_with_metrics(FakeCloudWatch(_series()))
    rows = _metric_rows(document)
    for t in METRIC_PENDING_TYPES:
        assert rows[t]["status"] == "skipped", t
        assert rows[t]["item_count"] is None, t
    # 대조군: 프로브가 있는 종류는 같은 문서에서 `ok` 다(전부 skipped 면 위는 아무것도 안 잰다).
    assert rows["nat_gateway"]["status"] == "ok"
