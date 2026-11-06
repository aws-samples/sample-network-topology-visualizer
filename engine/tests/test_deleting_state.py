"""지우는 중은 「이상」이 아니다 — 사용자 결정 2026-10-21 · QA C · ADR 0006 §O7 「지우는 중은 `down` 이 아니다」.

사람이 겪은 일: 고객이 VPC 인터페이스 엔드포인트를 **일부러** 지웠다. 이력에 빨간 「끊김 VPC 엔드포인트 logs · 정상 →
이상」이 「없어짐」보다 먼저 남았다 — 일부러 지울 때마다(엔드포인트 · NAT · TGW 첨부 …) 장애 줄이 생긴다.

🔴 아래 표는 규칙의 사본이 아니라 **기대값**이다. 종류마다 「지우는 중」 어휘가 다르다(`deleting` · `Deleting` ·
`DELETING` · `shutting-down` · `delete-in-progress` · ECS `draining`) — 정규식 하나로 뭉치면 `delete-failed` 같은
실패까지 삼킨다. 규칙에 값을 몰래 더하거나 빼면 여기서 FAIL 한다.
"""

from __future__ import annotations

import pytest

from nettopo.observe import EDGE_RULES, NODE_RULES, _judge

# 종류 → AWS 가 「지우고 있다 / 다 지웠다」고 보고하는 값(AWS 표기 그대로). 빈 튜플 = 그 종류에는 지우는 중 상태가 없다
# (AWS 가 지운 항목을 목록에서 바로 뺀다 · 또는 구조 수집기가 값을 접는다).
DELETING_EXPECTED: dict[str, tuple[str, ...]] = {
    "vpc": (),
    "subnet": (),
    "internet_gateway": (),
    "nat_gateway": ("deleting", "deleted"),
    "transit_gateway": ("deleting", "deleted"),
    "transit_gateway_attachment": ("deleting", "deleted"),
    "vpc_peering_connection": ("deleting", "deleted"),
    "vpc_endpoint": ("deleting", "deleted"),
    "load_balancer": (),
    "rds_db_instance": ("deleting",),
    "ecs_service": ("draining", "inactive"),
    "sagemaker_notebook_instance": ("Deleting",),
    "sagemaker_domain": ("Deleting",),
    "sagemaker_endpoint": ("Deleting",),
    "ec2_instance": ("shutting-down", "terminated"),
    "eks_cluster": ("DELETING",),
    "network_firewall": ("DELETING",),
    "ec2_instance_connect_endpoint": ("delete-in-progress", "delete-complete"),
    "vpn_gateway": ("deleting", "deleted"),
    "customer_gateway": ("deleting", "deleted"),
    "vpn_connection": ("deleting", "deleted"),
    "direct_connect_connection": ("deleting", "deleted"),
    "direct_connect_virtual_interface": ("deleting", "deleted"),
    "direct_connect_gateway": ("deleting", "deleted"),
    "elasticache_cluster": ("deleting", "deleted"),
    "rds_db_cluster": ("deleting",),
    "opensearch_domain": ("Deleting",),
    "msk_cluster": ("DELETING",),
}

# 실패·손상은 **그대로 `down`** 이다 — 「지우다 실패했다」는 장애다(지우는 중과 글자가 닮았다).
STILL_DOWN: list[tuple[str, str]] = [
    ("nat_gateway", "failed"),
    ("vpc_endpoint", "failed"),
    ("vpc_endpoint", "rejected"),
    ("transit_gateway_attachment", "failed"),
    ("transit_gateway_attachment", "rejecting"),
    ("vpc_peering_connection", "failed"),
    ("load_balancer", "failed"),
    ("rds_db_instance", "failed"),
    ("ec2_instance", "stopping"),  # 중지는 삭제가 아니다
    ("ec2_instance", "stopped"),
    ("eks_cluster", "FAILED"),
    ("ec2_instance_connect_endpoint", "delete-failed"),
    ("sagemaker_domain", "Delete_Failed"),
    ("sagemaker_endpoint", "Failed"),
    ("msk_cluster", "FAILED"),
    ("direct_connect_connection", "down"),
    ("vpn_connection", "available-all-tunnels-down"),
]


def _id(node_type: str) -> str:
    return f"{node_type}/111122223333/us-east-1/x"


def test_every_rule_states_its_deleting_values_and_they_are_these():
    """표 = 코드. 종류가 늘었는데 이 표에 행이 없으면(또는 그 반대) FAIL — 새 종류가 이 질문을 건너뛰지 못한다."""
    assert set(DELETING_EXPECTED) == set(NODE_RULES)
    for node_type, values in DELETING_EXPECTED.items():
        assert NODE_RULES[node_type].deleting == frozenset(
            v.casefold() for v in values
        ), node_type
    # 연관 엣지에는 지우는 중 값이 없다(떼는 중인 연관은 선 자체가 안 생긴다 — `services/base.py` `DEAD_STATES`).
    assert all(rule.deleting == frozenset() for rule in EDGE_RULES.values())


def test_up_down_and_deleting_never_overlap():
    """한 값이 두 칸에 있으면 `_judge` 의 검사 순서가 판정을 정한다 — 표가 말하지 않는 규칙이 된다."""
    for name, rule in {**NODE_RULES, **EDGE_RULES}.items():
        assert not (rule.up & rule.deleting), name
        assert not (rule.down & rule.deleting), name
        assert not (rule.up & rule.down), name


@pytest.mark.parametrize(
    ("node_type", "value"),
    [(t, v) for t, values in DELETING_EXPECTED.items() for v in values],
)
def test_a_deleting_value_is_not_judged_down(node_type, value):
    """종류마다: 지우는 중 → `none` + `deleting` · 「삭제 중」 · AWS 원문 값(콘솔에서 찾을 수 있게)."""
    got = _judge(NODE_RULES[node_type], value, _id(node_type), "node")
    assert got.status == "none", f"{node_type}={value} → {got.status}"
    assert got.deleting is True
    # 「이상」이 아니다 — 오류 수 0 · 근거가 모자란 것도 아니다(`partial` 이 아니다: AWS 가 정확히 말했다)
    assert got.error_count == 0 and got.partial is False
    assert "삭제 중" in (got.why or "") and repr(value) in (got.why or "")
    assert got.to_dict()["deleting"] is True


@pytest.mark.parametrize(
    "value", ["deleting", "Deleting", "DELETING"], ids=["lower", "title", "upper"]
)
def test_the_endpoint_reads_any_case(value):
    """AWS 문서의 엔드포인트 상태는 `Deleting`(첫 글자 대문자) · 실측은 소문자다 — 어느 쪽이 와도 지우는 중이다."""
    got = _judge(NODE_RULES["vpc_endpoint"], value, _id("vpc_endpoint"), "node")
    assert (got.status, got.deleting) == ("none", True)
    assert repr(value) in (got.why or "")  # 사람에게는 AWS 가 준 그대로


@pytest.mark.parametrize(("node_type", "value"), STILL_DOWN)
def test_failures_stay_down(node_type, value):
    got = _judge(NODE_RULES[node_type], value, _id(node_type), "node")
    assert got.status == "down", f"{node_type}={value} → {got.status}"
    assert got.deleting is False and "deleting" not in got.to_dict()
    assert got.error_count == 1
