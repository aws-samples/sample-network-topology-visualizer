"""W3② — LB 대상 상태(상태 단 후속 호출) + RDS 를 ENI 로 서브넷에 좁히기(구조).

LB 대상 상태(ADR 0006 「W3② LB 대상 상태」 · 사용자 결정 2026-10-16) 다섯 축:
⓪ 구조 수집은 `DescribeTargetHealth` 를 **불러 등록 대상 수만** 싣는다(LB 합 + 그룹별 · ALB/NLB/GWLB 모두) —
   헬스가 바뀌어도 바이트는 그대로, 등록이 바뀌면 바뀐다(불변식 ②) · 한 그룹 실패 = LB 는 남고 `partial`(불변식 ④)
① 상태 단은 같은 응답을 다시 묻지 않는다(그룹당 1회)
② 수는 타깃 그룹 전부의 합이고 `unhealthy` 는 AWS 가 `unhealthy` 라고 한 것만이다
③ 「살아 있는데 대상이 전부 비정상」만 `down` 이다(등록 중뿐인 LB 는 `up`)
④ 거부되면 수를 지어내지 않고 행이 그 사유를 든다 — 판정은 `State.Code` 그대로다(불변식 ④)

RDS ENI(`docs/canvas-principles.md` §17.12 「W3② ENI 로 좁힌다」) 세 축:
① 같은 AZ 후보가 하나면 ENI 를 **묻지 않는다** ② 이 DB 가 그 VPC·AZ 의 유일한 DB 이고 ENI 가 후보 하나에만
있을 때만 서브넷 ③ 거부 = VPC + 노드 사유(지어내지 않는다).
"""

from __future__ import annotations

import json
from typing import Any

import fakes
import jsonschema
import pytest
from fakes import (
    ACCOUNT,
    APP_ALB_ARN,
    APP_VPC,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    RDS_DB_INSTANCES,
    RDS_INSTANCE,
    REGION,
    FakeAws,
    client_error,
    factory,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.serialize import to_json
from test_observe import SCHEMA, STRUCTURE_AT, _by_id, _observe
from test_services import node_of, run

ALB_ID = f"load_balancer/{ACCOUNT}/{REGION}/app-alb"
NLB_ID = f"load_balancer/{ACCOUNT}/{REGION}/old-nlb"
GREEN = f"{APP_ALB_ARN}/tg/green"
BLUE = f"{APP_ALB_ARN}/tg/blue"
API = "elasticloadbalancing:DescribeTargetHealth"
RDS_ID = f"rds_db_instance/{ACCOUNT}/{REGION}/{RDS_INSTANCE}"
VPC_ID = f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
PRIVATE_ID = f"subnet/{ACCOUNT}/{REGION}/{PRIVATE_SUBNET}"
PUBLIC_ID = f"subnet/{ACCOUNT}/{REGION}/{PUBLIC_SUBNET}"


def _health(*states: str) -> list[dict[str, Any]]:
    """`state` 또는 `state:Reason`(예: `unavailable:Target.HealthCheckDisabled`) — AWS `TargetHealth` 모양."""
    out = []
    for i, s in enumerate(states):
        state, _, reason = s.partition(":")
        health: dict[str, Any] = {"State": state}
        if reason:
            health["Reason"] = reason
        out.append({"Target": {"Id": f"i-0aaa1111bbbb{i:04d}"}, "TargetHealth": health})
    return out


OFF = "unavailable:Target.HealthCheckDisabled"
_DETAIL = ("health_check_off", "initial", "draining", "unused", "other_unavailable")


def _t(total: int, healthy: int, unhealthy: int, **detail: int) -> dict[str, int]:
    """관측 `targets` 기대값(1.15.0) — 다섯 갈래는 늘 함께 온다(0 포함)."""
    assert set(detail) <= set(_DETAIL), detail
    return {
        "total": total,
        "healthy": healthy,
        "unhealthy": unhealthy,
        **{k: detail.get(k, 0) for k in _DETAIL},
    }


def _alb(**fake: Any) -> dict[str, Any]:
    return _by_id(_observe(**fake)[0])[ALB_ID]


# ------------------------------------------------------------------ LB ① 구조 바이트


_SCOPE_ALL = Scope(
    account_ids=(ACCOUNT,), regions=(REGION,), resource_types=SUPPORTED_RESOURCE_TYPES
)
# 🔴 헬스 «상태» 키 — 구조 문서의 어느 노드에도 있으면 안 된다(상태 단 전용 · `state_attributes`).
_HEALTH_KEYS = (
    "target_count",
    "healthy_target_count",
    "unhealthy_target_count",
    "health_check_off_target_count",
    "initial_target_count",
    "draining_target_count",
    "unused_target_count",
    "other_unavailable_target_count",
)


def _structure(client: FakeAws) -> Any:
    return collect(_SCOPE_ALL, factory(client), clock=lambda: STRUCTURE_AT)


def _node(snap: Any, nid: str) -> Any:
    found = [n for n in snap.nodes if n.id == nid]
    assert len(found) == 1, f"{nid} 가 없다 — 어서션이 무엇도 재지 않는다"
    return found[0]


def test_structure_carries_registered_counts_per_group_and_in_total():
    """⓪ 등록 대상 수(사용자 결정 2026-10-16) — 그룹별 수는 `target_groups`(이름 오름차순)와 같은 자리다.

    green 3 · blue 1 로 일부러 **다르게** 준다(같으면 자리가 뒤바뀌어도 통과한다). 목록은 green 이 먼저 온다
    (`fakes.TARGET_GROUPS`) — 응답 순서가 아니라 이름 순이어야 한다(불변식 ②).
    """
    client = FakeAws(
        target_health={
            GREEN: _health("healthy", "unhealthy", "draining"),
            BLUE: _health("initial"),
        }
    )
    snap = _structure(client)
    alb = _node(snap, ALB_ID).attributes
    assert alb["target_groups"] == ["app-tg-blue", "app-tg-green"]
    assert alb["target_group_registered_target_counts"] == [1, 3]
    assert alb["registered_target_count"] == 4
    # 그룹 0개인 NLB = 「읽었고 없다」(0 · 빈 배열) — 키가 없는 「못 읽음」과 다르다
    nlb = _node(snap, NLB_ID).attributes
    assert (
        nlb["registered_target_count"],
        nlb["target_group_registered_target_counts"],
    ) == (0, [])
    assert client.calls.count("describe_target_health") == 2  # 그룹마다 한 번
    for node in snap.nodes:
        leaked = [k for k in _HEALTH_KEYS if k in node.attributes]
        assert not leaked, f"헬스 상태가 구조에 섞였다: {node.id} {leaked}"


def test_health_flaps_keep_the_structure_bytes_and_registration_changes_them():
    """🔴 헬스가 오르내려도 구조 바이트·내용 지문은 같다 · 대상을 하나 등록하면 바뀐다(그것은 구성 변경이다)."""
    from nettopo.serialize import content_digest

    base = _structure(
        FakeAws(target_health={GREEN: _health("healthy"), BLUE: _health("healthy")})
    )
    flap = _structure(
        FakeAws(target_health={GREEN: _health("unhealthy"), BLUE: _health("draining")})
    )
    more = _structure(
        FakeAws(
            target_health={
                GREEN: _health("healthy", "initial"),
                BLUE: _health("healthy"),
            }
        )
    )
    assert to_json(base) == to_json(flap)
    assert to_json(base) != to_json(more)
    assert content_digest(base.to_document()) != content_digest(more.to_document())
    assert _node(more, ALB_ID).attributes["registered_target_count"] == 3


@pytest.mark.parametrize("denied", [GREEN, BLUE])
def test_one_denied_group_keeps_the_load_balancer_partial_without_a_total(denied):
    """⓪ 불변식 ④ — 한 그룹만 거부: LB 는 남고 `partial` + 사유 · 그 그룹 자리 = `null` · 다른 그룹 수는 그대로 ·
    LB 합은 **없다**(읽은 그룹만 더하면 틀린 수다)."""
    client = FakeAws(
        target_health={
            GREEN: _health("healthy", "healthy", "healthy"),
            BLUE: _health("healthy"),
        },
        followup_errors={
            ("describe_target_health", denied): client_error(
                "AccessDenied", "DescribeTargetHealth"
            )
        },
    )
    node = _node(_structure(client), ALB_ID)
    want = [None, 3] if denied == BLUE else [1, None]
    assert node.attributes["target_group_registered_target_counts"] == want
    assert "registered_target_count" not in node.attributes
    assert node.partial is True
    assert [(r.code, r.api) for r in node.partial_reasons] == [("access_denied", API)]
    # 같은 LB 의 다른 후속 호출(리스너)은 살아 있다
    assert node.attributes["listeners"] == ["HTTP:80", "HTTP:8080", "HTTPS:443"]


_GWLB_ARN = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/gwy/inspect-gwlb/70dc6c495c0c9177"


@pytest.mark.parametrize("kind", ["application", "network", "gateway"])
def test_every_load_balancer_type_carries_registered_counts(kind):
    """ALB · NLB · GWLB 모두 — `DescribeLoadBalancers` 가 세 종류를 다 주고 수집기는 종류로 거르지 않는다."""
    lb = {
        "LoadBalancerName": "inspect-lb",
        "LoadBalancerArn": _GWLB_ARN,
        "Scheme": "internal",
        "Type": kind,
        "VpcId": APP_VPC,
        "State": {"Code": "active"},
        "AvailabilityZones": [{"ZoneName": "us-east-1a", "SubnetId": PUBLIC_SUBNET}],
    }
    tg = f"{_GWLB_ARN}/tg/inspect"
    client = FakeAws(
        load_balancers=[lb],
        listeners={_GWLB_ARN: []},
        target_groups={
            _GWLB_ARN: [{"TargetGroupName": "inspect-tg", "TargetGroupArn": tg}]
        },
        target_health={tg: _health("healthy", "unhealthy")},
        web_acls={},
    )
    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=("load_balancer",)
    )
    node = _node(
        collect(scope, factory(client), clock=lambda: STRUCTURE_AT),
        f"load_balancer/{ACCOUNT}/{REGION}/inspect-lb",
    )
    assert node.attributes["load_balancer_type"] == kind
    assert node.attributes["registered_target_count"] == 2
    assert node.attributes["target_group_registered_target_counts"] == [2]
    assert node.partial is False


def test_the_state_stage_counts_the_structure_read_without_asking_again():
    """① 상태 단도 그룹당 한 번만 묻는다 — 등록 수(구조)와 대상 수(상태)는 같은 응답에서 나온다."""
    state_client = FakeAws()
    on = collect(
        _SCOPE_ALL, factory(state_client), clock=lambda: STRUCTURE_AT, with_state=True
    )
    assert (
        state_client.calls.count("describe_target_health") == 2
    )  # 그룹 2개 · 두 번 묻지 않는다
    alb = _node(on, ALB_ID).attributes
    assert alb["target_count"] == alb["registered_target_count"] == 4


def test_the_state_snapshot_with_target_counts_still_satisfies_the_topology_schema():
    from test_services import SCHEMA_PATH

    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )
    doc = collect(
        scope, factory(FakeAws()), clock=lambda: STRUCTURE_AT, with_state=True
    ).to_document()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = jsonschema.Draft202012Validator(schema).iter_errors(doc)
    assert [f"{e.json_path}: {e.message}" for e in errors] == []


# ------------------------------------------------------------------ LB ② 수 · ③ 판정


def test_counts_sum_every_group_and_initial_is_neither_side():
    """기본 = green 정상 2 · blue 정상 1 + 등록 중 1 → 4개 중 정상 3 · 비정상 0 · `up`."""
    o = _alb()
    assert o["targets"] == _t(4, 3, 0, initial=1)
    assert (o["status"], o["partial"]) == ("up", False)
    jsonschema.Draft202012Validator(SCHEMA).validate(_observe()[0])


def test_a_load_balancer_without_target_groups_reads_zero_not_missing():
    """old-nlb 는 그룹이 0개다 — 「읽었고 대상 없음」(0)이지 「못 읽음」(키 없음)이 아니다. 판정은 State.Code."""
    o = _by_id(_observe()[0])[NLB_ID]
    assert o["targets"] == _t(0, 0, 0)
    assert (
        o["status"] == "down"
    )  # failed 상태 LB — 대상 규칙이 아니라 State.Code 가 낸 down
    assert "대상" not in (o["why"] or "")


@pytest.mark.parametrize(
    ("green", "blue", "status", "targets", "detail"),
    [
        # 전부 비정상 → down
        (("unhealthy", "unhealthy"), ("unhealthy",), "down", (3, 0, 3), {}),
        # 정상 0 이지만 비정상도 0(등록 중·빼는 중뿐) → up(배포 중을 빨갛게 그리지 않는다)
        (
            ("initial", "draining"),
            ("unused",),
            "up",
            (3, 0, 0),
            {"initial": 1, "draining": 1, "unused": 1},
        ),
        # 비정상이 있어도 정상이 하나라도 있으면 up
        (("unhealthy", "unhealthy"), ("healthy",), "up", (3, 1, 2), {}),
        # 그 밖의 unavailable(Reason 이 헬스 체크 꺼짐이 아니다) + 비정상 1 → 정상 0 · 비정상 1 → down
        (("unavailable",), ("unhealthy",), "down", (2, 0, 1), {"other_unavailable": 1}),
        # 대상 0개 → up(대상 규칙은 「대상이 있다」가 전제)
        ((), (), "up", (0, 0, 0), {}),
    ],
)
def test_down_only_when_targets_exist_none_healthy_and_some_unhealthy(
    green, blue, status, targets, detail
):
    o = _alb(target_health={GREEN: _health(*green), BLUE: _health(*blue)})
    total, healthy, unhealthy = targets
    assert o["targets"] == _t(total, healthy, unhealthy, **detail)
    assert o["status"] == status
    if status == "down":
        assert (
            o["why"] == f"대상 {total}개 중 정상 0 — AWS 가 비정상 {unhealthy}개로 보고"
        )
        assert o["evidence"]["api"] == API
    else:
        assert o["evidence"]["api"] == "elasticloadbalancing:DescribeLoadBalancers"


# ------------------------------------------------------------------ LB ②b 다섯 갈래(관측 1.15.0 · #137)


@pytest.mark.parametrize(
    ("state", "key"),
    [
        (OFF, "health_check_off"),
        ("unavailable:Elb.InternalError", "other_unavailable"),
        (
            "unavailable",
            "other_unavailable",
        ),  # Reason 이 없으면 꺼짐이라고 단정하지 않는다
        ("initial:Elb.RegistrationInProgress", "initial"),
        ("draining:Target.DeregistrationInProgress", "draining"),
        ("unused:Target.NotInUse", "unused"),
    ],
)
def test_each_state_lands_in_its_own_count_and_never_in_unhealthy(state, key):
    """AWS `TargetHealth.State`(+ `unavailable` 은 `Reason`)마다 제 갈래 하나에만 1 — 정상·비정상·다른 갈래는 0."""
    o = _alb(target_health={GREEN: _health(state), BLUE: _health("healthy")})
    assert o["targets"] == _t(2, 1, 0, **{key: 1})
    assert (o["status"], o["why"]) == ("up", None)


def test_unknown_states_stay_outside_every_count():
    """표에 없는 상태(`unhealthy.draining`)는 어느 갈래에도 넣지 않는다 — `total` 과의 차로 남는다(화면 「그 밖」)."""
    o = _alb(
        target_health={GREEN: _health("unhealthy.draining"), BLUE: _health("healthy")}
    )
    assert o["targets"] == _t(2, 1, 0)


def test_health_check_off_only_never_makes_the_load_balancer_down():
    """🔴 헬스 체크를 끈 대상(Lambda 의 기본값)뿐인 LB 는 이상이 아니다 — 정상 0 이어도 비정상 0 이라 `up` 이다."""
    o = _alb(target_health={GREEN: _health(OFF, OFF), BLUE: _health(OFF)})
    assert o["targets"] == _t(3, 0, 0, health_check_off=3)
    assert (o["status"], o["why"], o["error_count"]) == ("up", None, 0)
    assert o["evidence"]["api"] == "elasticloadbalancing:DescribeLoadBalancers"


def test_the_state_snapshot_carries_the_five_counts_as_attributes():
    """상태 단 노드 속성 = 다섯 갈래 키(0 포함) · 구조 단에는 없다(불변식 ②)."""
    client = FakeAws(target_health={GREEN: _health(OFF, "draining"), BLUE: _health()})
    on = collect(
        _SCOPE_ALL, factory(client), clock=lambda: STRUCTURE_AT, with_state=True
    )
    a = _node(on, ALB_ID).attributes
    assert {
        k: a[k]
        for k in (
            "health_check_off_target_count",
            "initial_target_count",
            "draining_target_count",
            "unused_target_count",
            "other_unavailable_target_count",
        )
    } == {
        "health_check_off_target_count": 1,
        "initial_target_count": 0,
        "draining_target_count": 1,
        "unused_target_count": 0,
        "other_unavailable_target_count": 0,
    }
    off = _node(_structure(FakeAws(target_health={GREEN: _health(OFF)})), ALB_ID)
    assert not [
        k
        for k in off.attributes
        if k.endswith("_target_count") and k != "registered_target_count"
    ]


# ------------------------------------------------------------------ LB ④ 거부


@pytest.mark.parametrize("denied", [GREEN, BLUE])
def test_one_denied_group_drops_every_count_and_keeps_the_state_judgement(denied):
    """🔴 한 그룹만 거부돼도 수를 싣지 않는다 — 읽은 그룹만 세면 「3개 중 정상 0」이 「6개 중 3」일 수 있다."""
    o = _alb(
        target_health={GREEN: _health("unhealthy"), BLUE: _health("unhealthy")},
        followup_errors={
            ("describe_target_health", denied): client_error(
                "AccessDenied", "DescribeTargetHealth"
            )
        },
    )
    assert "targets" not in o
    assert o["status"] == "up"  # 비정상 대상만 있어도 모르는 수로 down 을 내지 않는다
    assert o["partial"] is True
    assert [(r["code"], r["api"]) for r in o["partial_reasons"]] == [
        ("access_denied", API)
    ]
    jsonschema.Draft202012Validator(SCHEMA).validate(
        _observe(
            followup_errors={
                ("describe_target_health", denied): client_error(
                    "AccessDenied", "DescribeTargetHealth"
                )
            }
        )[0]
    )


def test_unreadable_target_groups_never_ask_target_health():
    """그룹 목록을 못 읽었으면 어느 그룹을 물을지 모른다 — 부르지 않고, 「대상 0개」도 적지 않는다."""
    client = FakeAws(
        followup_errors={
            ("describe_target_groups", APP_ALB_ARN): client_error(
                "AccessDenied", "DescribeTargetGroups"
            )
        }
    )
    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=("load_balancer",)
    )
    snap = collect(scope, factory(client), clock=lambda: STRUCTURE_AT, with_state=True)
    alb = next(n for n in snap.nodes if n.id == ALB_ID)
    assert "target_count" not in alb.attributes
    # 등록 수도 「0」이 아니라 **키 없음**이다(목록을 못 읽었다 = 몇 개인지 모른다)
    assert "registered_target_count" not in alb.attributes
    assert "target_group_registered_target_counts" not in alb.attributes
    assert [r.api for r in alb.partial_reasons] == [
        "elasticloadbalancing:DescribeTargetGroups"
    ]
    # old-nlb(그룹 0개)만 남아도 그 LB 는 부를 그룹이 없다 → 호출 0
    assert "describe_target_health" not in client.calls


def test_targets_are_only_allowed_on_load_balancer_rows():
    """스키마가 다른 행의 `targets` 를 거부한다 — 대조군으로 같은 키가 LB 행에서는 통과한다."""
    document, _ = _observe()
    validator = jsonschema.Draft202012Validator(SCHEMA)
    row = next(o for o in document["observations"] if o["target_id"] == VPC_ID)
    row["targets"] = {"total": 1, "healthy": 1, "unhealthy": 0}
    assert list(validator.iter_errors(document))
    del row["targets"]
    assert not list(validator.iter_errors(document))


# ------------------------------------------------------------------ RDS ENI


def _rds_in_two_1b_subnets(**extra: Any) -> dict[str, Any]:
    """같은 AZ(1b) 에 후보 서브넷이 둘 — 서브넷 그룹만으로는 고를 수 없다."""
    item = json.loads(json.dumps(RDS_DB_INSTANCES[0]))
    for s in item["DBSubnetGroup"]["Subnets"]:
        s["SubnetAvailabilityZone"]["Name"] = "us-east-1b"
    item.update(extra)
    return item


def _eni(subnet: str, *, vpc: str = APP_VPC, az: str = "us-east-1b") -> dict[str, Any]:
    return {
        "NetworkInterfaceId": f"eni-0{subnet[-12:]}",
        "SubnetId": subnet,
        "VpcId": vpc,
        "AvailabilityZone": az,
        "RequesterId": "amazon-rds",
        "Description": "RDSNetworkInterface",
    }


def _rds(client: FakeAws) -> dict[str, Any]:
    return node_of(run(client), RDS_ID)


def test_a_unique_az_subnet_never_asks_for_enis():
    """기본 픽스처는 1b 후보가 하나다 — 그때 ENI 를 물으면 권한만 늘고 답은 같다."""
    client = FakeAws(network_interfaces=[_eni(PUBLIC_SUBNET)])
    node = _rds(client)
    assert node["parent_id"] == PRIVATE_ID
    assert "describe_network_interfaces" not in client.calls


@pytest.mark.parametrize("subnet", [PRIVATE_SUBNET, PUBLIC_SUBNET])
def test_the_only_db_with_eni_in_one_candidate_lands_under_that_subnet(subnet):
    client = FakeAws(
        rds_instances=[_rds_in_two_1b_subnets()],
        network_interfaces=[_eni(subnet)],
    )
    node = _rds(client)
    assert node["parent_id"] == f"subnet/{ACCOUNT}/{REGION}/{subnet}"
    assert node["partial"] is False
    assert client.calls.count("describe_network_interfaces") == 1
    # 엔진 내부 키가 노드 속성으로 새지 않는다
    assert "_EniSubnetId" not in json.dumps(node)


@pytest.mark.parametrize(
    "enis",
    [
        [],  # ENI 가 안 보인다
        [_eni(PRIVATE_SUBNET), _eni(PUBLIC_SUBNET)],  # 후보 둘 다에 있다
        [
            _eni(PRIVATE_SUBNET, vpc="vpc-0fff9999eeee0000")
        ],  # 다른 VPC(필터가 걸러야 한다)
        [_eni(PRIVATE_SUBNET, az="us-east-1a")],  # 다른 AZ
        [
            _eni("subnet-0fff9999eeee1111")
        ],  # 후보 밖 서브넷(같은 VPC·AZ 의 RDS Proxy 등)
    ],
)
def test_without_a_single_answer_the_db_stays_under_the_vpc(enis):
    node = _rds(
        FakeAws(rds_instances=[_rds_in_two_1b_subnets()], network_interfaces=enis)
    )
    assert node["parent_id"] == VPC_ID
    assert node["partial"] is False


@pytest.mark.parametrize(
    "neighbour",
    [
        {"AvailabilityZone": "us-east-1b"},
        # 이웃의 주 AZ 는 1a 이지만 대기 복제본이 1b 에 있다 → 그 ENI 도 1b 에 있을 수 있다
        {"AvailabilityZone": "us-east-1a", "SecondaryAvailabilityZone": "us-east-1b"},
    ],
)
def test_a_neighbour_db_in_the_same_vpc_and_az_blocks_the_guess(neighbour):
    """🔴 RDS ENI 에는 DB 이름이 없다 — 같은 VPC·AZ 에 DB 가 둘이면 ENI 가 하나여도 누구 것인지 모른다."""
    other = json.loads(json.dumps(RDS_DB_INSTANCES[0]))
    other["DBInstanceIdentifier"] = "app-db-2"
    other["DBInstanceArn"] = other["DBInstanceArn"].replace(RDS_INSTANCE, "app-db-2")
    other.update(neighbour)
    node = _rds(
        FakeAws(
            rds_instances=[_rds_in_two_1b_subnets(), other],
            network_interfaces=[_eni(PRIVATE_SUBNET)],
        )
    )
    assert node["parent_id"] == VPC_ID


def test_a_neighbour_in_another_vpc_does_not_block():
    """대조군 — 다른 VPC 의 DB 는 이웃이 아니다(위 검사가 「DB 가 둘이면 무조건 VPC」로 통과하지 않게)."""
    other = json.loads(json.dumps(RDS_DB_INSTANCES[0]))
    other["DBInstanceIdentifier"] = "app-db-2"
    other["DBInstanceArn"] = other["DBInstanceArn"].replace(RDS_INSTANCE, "app-db-2")
    other["DBSubnetGroup"]["VpcId"] = "vpc-0fff9999eeee0000"
    node = _rds(
        FakeAws(
            rds_instances=[_rds_in_two_1b_subnets(), other],
            network_interfaces=[_eni(PRIVATE_SUBNET)],
        )
    )
    assert node["parent_id"] == PRIVATE_ID


def test_denied_enis_keep_the_vpc_and_say_why():
    """거부 = 서브넷을 지어내지 않고 VPC · 노드에 「일부 못 읽음」 — 권한을 주면 칸이 정해진다고 말한다."""
    client = FakeAws(
        rds_instances=[_rds_in_two_1b_subnets()],
        network_interfaces=[_eni(PRIVATE_SUBNET)],
        errors={
            "describe_network_interfaces": client_error(
                "UnauthorizedOperation", "DescribeNetworkInterfaces"
            )
        },
    )
    doc = run(client)
    node = node_of(doc, RDS_ID)
    assert node["parent_id"] == VPC_ID
    assert node["partial"] is True
    assert [r["api"] for r in node["partial_reasons"]] == [
        "ec2:DescribeNetworkInterfaces"
    ]
    # RDS 목록은 읽었다 — 종류 전체가 실패한 것이 아니다
    cov = [c for c in doc["coverage"] if c["resource_type"] == "rds_db_instance"]
    assert [c["status"] for c in cov] == ["ok"]


def test_an_ec2_entry_failure_only_marks_the_db_partial():
    """EC2 클라이언트를 못 만들어도(진입 실패) RDS 는 남고 사유만 붙는다 — RDS 만 수집하는 범위."""
    from fakes import factory as make_factory

    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=("rds_db_instance",)
    )
    client = FakeAws(rds_instances=[_rds_in_two_1b_subnets()])
    snap = collect(
        scope,
        make_factory(
            client,
            fail_services={"ec2": client_error("AccessDenied", "AssumeRole")},
        ),
        clock=lambda: STRUCTURE_AT,
    ).to_document()
    node = node_of(snap, RDS_ID)
    # 이 범위엔 VPC 노드가 없어 리전으로 올라간다 — 요점은 서브넷을 고르지 않았다는 것이다.
    assert not node["parent_id"].startswith("subnet/")
    assert node["partial"] is True
    assert [r["api"] for r in node["partial_reasons"]] == ["sts:AssumeRole"]
    assert [c["status"] for c in snap["coverage"]] == ["ok"]


def test_eni_lookups_are_cached_per_vpc_and_az():
    """같은 VPC·AZ 의 DB 둘(서로 이웃이라 좁히지는 못한다)도 ENI 는 한 번만 묻는다."""
    other = _rds_in_two_1b_subnets(
        DBInstanceIdentifier="app-db-2",
        DBInstanceArn=RDS_DB_INSTANCES[0]["DBInstanceArn"].replace(
            RDS_INSTANCE, "app-db-2"
        ),
    )
    client = FakeAws(
        rds_instances=[_rds_in_two_1b_subnets(), other],
        network_interfaces=[_eni(PRIVATE_SUBNET)],
    )
    run(client)
    assert client.calls.count("describe_network_interfaces") == 1


def test_fakes_keep_the_default_target_health_untouched():
    """가짜의 기본 표를 테스트가 고치면 다음 테스트가 바뀐 값으로 통과한다 — 원본은 그대로여야 한다."""
    assert [h["TargetHealth"]["State"] for h in fakes.TARGET_HEALTH[BLUE]] == [
        "healthy",
        "initial",
    ]
