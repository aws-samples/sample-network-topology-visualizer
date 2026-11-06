"""관측 축(`nettopo observe`) — **`down` 을 여기서만 잴 수 있다.**

🔴 이 파일이 있어야 하는 이유는 실계정이 `down` 을 못 준다는 실측이다. 2계정 × 3리전의 구조
스냅샷에서 상태 값을 전수 세어 보니 `available`(VPC·Subnet·NAT·TGW·TGW첨부·엔드포인트) ·
`attached`(IGW) · `active`(LB·피어링) · `associated`(연관) · `blackhole=False` **뿐**이었다
— 끊긴 것이 **0건**이다. 그래서 「실계정 1회 실행이 통과했다」는 판정 로직이 살아 있다는 증거가
되지 못한다(판정이 통째로 죽어도 전부 `up` 이 나온다). `fakes.py` 의 픽스처는 반대로 죽은 상태를
일부러 담고 있다(실패한 NAT·삭제 중 TGW첨부·실패한 피어링·실패한 LB·blackhole 라우트·떼어낸 IGW)
— 그 6개가 실제로 `down` 으로 나오는지가 이 파일의 중심이다.

그리고 ADR 0006 §O7 표 ↔ 코드 상수의 일치를 강제한다. 표에 행을 더하고 규칙을 안 더하면 그 타입은
상태가 조용히 안 붙는다(§O7 마지막 🔴 가 「테스트가 강제한다」고 선언한 것의 생산 쪽 절반이다 —
계약 쪽 절반은 `schema/tests/test_observation_schema.py` 의 `test_o7_covers_every_node_type`).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import (
    ACCOUNT,
    EGRESS_NAT,
    REGION,
    ROUTE_TABLES,
    FakeAws,
    client_error,
    factory,
)
from internal_docs import read_or_skip
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import DERIVED_TYPES, Reason, Scope
from nettopo.observe import (
    ASSOCIATION_EVIDENCE,
    DEFAULT_FLAP_COLLAPSE_THRESHOLD,
    DEFAULT_MAX_CHANGES,
    DEFAULT_RESOLUTION_SECONDS,
    DEFAULT_RETENTION_SECONDS,
    EDGE_RULES,
    FLOW_LOG_ONLY_EDGES,
    METRIC_ONLY_EDGES,
    NO_MEANS_EDGES,
    NO_MEANS_NODES,
    NO_STATE_EDGES,
    NO_STATE_NODES,
    NODE_RULES,
    OBSERVED_RESOURCE_TYPES,
    build_observation,
)
from nettopo.serialize import to_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "observation.schema.json").read_text(encoding="utf-8")
)


def _adr6() -> str:
    """ADR 0006 — 내부 설계 문서라 공개 트리에는 없다. 쓰는 테스트만 사유를 적고 건너뛴다(import 때 읽지 않는다)."""
    return read_or_skip("docs/adr/0006-observation-schema.md")


STRUCTURE_AT = "2026-09-23T00:00:00Z"
OBSERVED_AT = "2026-09-23T00:05:00Z"


def _snapshot(resource_types: tuple[str, ...], at: str, **overrides: Any) -> Any:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=resource_types
    )
    # 🔴 상태 단(observe CLI)은 상태 탐침을 켜고 돈다(`cli.py` 의 `with_state=True`) — 구조 단은 끈다.
    # 끈 채로 관측하면 RDS·ECS 등이 「규칙에 없는 값 None」(개발)이 된다(W3 실측).
    return collect(
        scope,
        factory(FakeAws(**overrides)),
        clock=lambda: at,
        with_state=at != STRUCTURE_AT,
    )


def _observe(**overrides: Any) -> tuple[dict[str, Any], list[str]]:
    """구조는 12종 전부, 관측은 10종 — **생산과 같은 조합**이다.

    같은 `FakeAws` 를 두 번 돌리는 것이 실제 순서와 같다(구조 단 CodeBuild → 상태 단 Lambda,
    ADR 0004 D2). 한 번만 돌려 재사용하면 「상태 단이 부르지 않는 종류」의 항목이
    스냅샷에 남아 있어 버그를 덮는다.
    """
    topology = _snapshot(
        SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, **overrides
    ).to_document()
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT, **overrides)
    return build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )


def _by_id(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["target_id"]: o for o in document["observations"]}


def _one(document: dict[str, Any], needle: str) -> dict[str, Any]:
    """id 에 `needle` 이 들어간 관측 **정확히 하나**. 0개·2개면 어서션이 거짓이 된다."""
    found = [o for o in document["observations"] if needle in o["target_id"]]
    assert len(found) == 1, f"{needle!r} 로 찾은 관측 {len(found)}개 — 1개를 기대했다"
    return found[0]


# ---------------------------------------------------------------- 계약


def test_observation_satisfies_the_schema():
    document, _ = _observe()
    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert document["run"]["topology_collected_at"] == STRUCTURE_AT
    assert document["run"]["observed_at"] == OBSERVED_AT
    assert document["layers"] == {"describe": True, "metric": False}


def test_scope_comes_from_the_structure_document():
    """관측이 scope 를 스스로 정하면 두 문서가 갈라진다 — `--region` 이 없는 이유다."""
    document, _ = _observe()
    assert document["scope"] == {
        "account_ids": [ACCOUNT],
        "regions": [REGION],
        # 🔴 종류만은 **관측이 부른 것**이다(ADR 0006 O10) — 구조는 12종, 관측은 10종. 구조의
        # 12종을 적으면 부르지 않는 SG·NACL 이 영원히 「빠진 칸」으로 보인다(빈칸 검사가 FAIL).
        "resource_types": sorted(OBSERVED_RESOURCE_TYPES),
    }


def test_document_is_byte_identical_across_runs():
    """불변식 ② — 같은 입력이면 같은 바이트. 정렬이 빠지면 dict 순서가 새어 나온다."""
    first, _ = _observe()
    second, _ = _observe()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    ids = [o["target_id"] for o in first["observations"]]
    assert ids == sorted(ids)


# ---------------------------------------------------------------- `down` (실계정 0건)


@pytest.mark.parametrize(
    ("needle", "why_contains"),
    [
        ("nat-0fff6666aaaa7777", "failed"),  # 생성에 실패한 NAT
        ("pcx-0bbb2222cccc2222", "failed"),  # 실패한 피어링(Status.Code)
        ("old-nlb", "failed"),  # 실패한 LB(State.Code)
        ("igw-0bbb2222cccc5555", "detached"),  # 떼어낸 IGW(Attachments[] 가 비었다)
    ],
)
def test_dead_resources_are_down(needle, why_contains):
    """🔴 실계정에서 **한 건도** 나오지 않는 경로다. 여기서 안 재면 어디서도 안 잰다."""
    document, _ = _observe()
    observation = _one(document, needle)
    assert observation["status"] == "down", observation
    # 판정만 맞고 근거가 비면 화면이 「왜 빨간가」를 말할 수 없다.
    assert why_contains in (observation["why"] or "")
    assert observation["error_count"] == 1
    assert observation["partial"] is False


def test_a_deleting_attachment_is_not_down():
    """🔴 삭제 중 TGW 첨부는 「이상」이 아니다(사용자 결정 2026-10-21 · QA C) — 일부러 지운 자원이 빨간 「끊김」을 남기면 장애로 읽힌다.

    가짜 계정 그대로(`fakes.py` 의 `deleting` 첨부)를 관측 끝까지 통과시킨다 — `_judge` 단위 검사만으로는 문서에
    키가 실리는지(`to_dict`) 를 못 잰다.
    """
    document, _ = _observe()
    observation = _one(document, "tgw-attach-0ccc3333dddd0000")
    assert observation["status"] == "none", observation
    assert observation["deleting"] is True
    assert "'deleting'" in observation["why"] and "삭제 중" in observation["why"]
    assert observation["error_count"] == 0 and observation["partial"] is False
    # 대조군 — 지우는 중이 아닌 행에는 키가 **없다**(`false` 를 적으면 같은 사실에 바이트 표현이 둘이 된다).
    # 가짜 계정의 지우는 중은 이 첨부와 DX VIF(`fakes.DX_DELETING_VIF`) 둘뿐이다.
    marked = sorted(
        o["target_id"].rsplit("/", 1)[1]
        for o in document["observations"]
        if "deleting" in o
    )
    assert marked == ["dxvif-fg0ddd4444", "tgw-attach-0ccc3333dddd0000"]


def test_blackhole_route_is_down():
    """§O7 이 「제품에서 가장 값비싼 상태」라 적은 것 — 구조만 보면 이어져 보인다.

    🔴 `fakes.py:139-143` 의 blackhole 라우트로는 **잴 수 없다.** 그 라우트의 대상이 ENI 라
    노드가 없고(`unresolved_refs`), 엣지가 **아예 만들어지지 않는다** → 관측 항목도 없다.
    그래서 대상이 살아 있는 blackhole 을 따로 만들어 잰다. 대상이 없는 blackhole 이 안 보이는
    것은 구조 수집기의 성질이고 이 파일이 고칠 수 없다(핸드오프에 적었다).
    """
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[0]["Routes"].append(
        # 대상 노드는 존재하고(NAT) 경로만 죽었다 — 화면에 선이 남는 유일한 blackhole 형태다.
        {
            "DestinationCidrBlock": "10.99.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "blackhole",
        }
    )
    document, _ = _observe(route_tables=tables)

    routes = [
        o for o in document["observations"] if o["target_id"].startswith("route|")
    ]
    blackholed = [o for o in routes if o["status"] == "down"]
    assert len(routes) > len(blackholed) >= 1, (
        f"살아 있는 라우트 {len(routes) - len(blackholed)}개 · blackhole "
        f"{len(blackholed)}개 — 둘 다 있어야 판정이 실제로 갈라진다"
    )
    assert "blackhole" in (blackholed[0]["why"] or "")
    assert blackholed[0]["error_count"] == 1


def test_a_healthy_route_sharing_a_target_with_a_blackhole_is_reported_down():
    """같은 NAT 로 가는 정상 경로와 죽은 경로 — 선은 하나이고 `down` 이지만 **죽은 목적지만** 이름으로 말한다(2.13.0).

    `services/route_table.py` 는 같은 (라우트테이블, 대상) 의 경로를 엣지 하나로 합치고 `blackhole` 을 **OR** 한다(선의 판정).
    2.13.0 부터 그 엣지가 `blackhole_destinations`(실제로 죽은 목적지만)를 들고, 테이블의 `routes` 가 경로별 상태를 든다.
    🔴 `why` 가 멀쩡한 `10.98.0.0/16` 까지 「blackhole」이라 말하면 사람이 멀쩡한 경로를 지운다.
    """
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[0]["Routes"] += [
        {
            "DestinationCidrBlock": "10.98.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "active",
        },
        {
            "DestinationCidrBlock": "10.99.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "blackhole",
        },
    ]
    document, _ = _observe(route_tables=tables)

    merged = [
        o
        for o in document["observations"]
        if o["target_id"].startswith("route|")
        and EGRESS_NAT in o["target_id"]
        and o["target_id"].split("|")[1].endswith(ROUTE_TABLES[0]["RouteTableId"])
    ]
    assert len(merged) == 1, (
        f"경로 2개가 선 1개로 합쳐져야 이 한계가 재진다 — {len(merged)}개 나왔다"
    )
    assert merged[0]["status"] == "down"
    why = merged[0]["why"] or ""
    assert "10.99.0.0/16" in why and "10.98.0.0/16" not in why, why


def test_live_resources_are_up_and_carry_no_why():
    document, _ = _observe()
    # 노드 행으로 고른다 — 2.7.0 부터 `app-alb` 는 LB → 대상 선 행(`load_balancer_target|…app-alb|…`)의 id 에도 들어간다
    (healthy,) = [
        o
        for o in document["observations"]
        if o["target_id"] == "load_balancer/111122223333/us-east-1/app-alb"
    ]
    assert (healthy["status"], healthy["why"], healthy["error_count"]) == (
        "up",
        None,
        0,
    )
    assert healthy["evidence"]["api"] == "elasticloadbalancing:DescribeLoadBalancers"
    # 2층은 안 켰다 → 지표 칸은 비어 있어야 한다(S3 이 채운다).
    assert healthy["evidence"]["metric"] is None
    assert healthy["evidence"]["datapoints"] is None


# ---------------------------------------------------------------- 「모르는 값」


def test_unknown_state_is_not_silently_up():
    """🔴 up 집합에도 down 집합에도 없는 값. 조용히 `up` 이 되면 고장이 정상으로 그려진다."""
    document, _ = _observe(
        vpcs=[
            {
                "VpcId": "vpc-0aaa1111bbbb2222",
                "CidrBlock": "10.10.0.0/16",
                "State": "quantum-superposition",
                "IsDefault": False,
                "Tags": [],
            }
        ]
    )
    observation = _one(document, "vpc-0aaa1111bbbb2222")
    assert observation["status"] == "none"
    assert observation["partial"] is True
    # 실제 값을 적어야 사람이 §O7 에 무엇을 더할지 안다.
    assert "quantum-superposition" in (observation["why"] or "")
    assert observation["error_count"] is None
    # 읽기는 성공했다 → 근거 API 는 남는다(「못 읽었다」와 다르다).
    assert observation["evidence"]["api"] == "ec2:DescribeVpcs"


def test_missing_state_field_is_not_silently_up():
    """`State` 키가 아예 없는 응답. `_judge` 가 `None` 을 up 으로 읽으면 안 된다."""
    document, _ = _observe(
        vpcs=[
            {"VpcId": "vpc-0aaa1111bbbb2222", "CidrBlock": "10.10.0.0/16", "Tags": []}
        ]
    )
    observation = _one(document, "vpc-0aaa1111bbbb2222")
    assert observation["status"] == "none"
    assert observation["partial"] is True


def test_state_comparison_is_case_insensitive():
    """AWS 문서의 엔드포인트 상태는 `Available` 대문자인데 실측은 소문자였다.

    어느 쪽이 와도 「모르는 값」으로 떨어지면 화면이 통째로 `?` 가 된다. 서비스 파일은 값을
    정규화하지 않으므로(`services/vpc_endpoint.py:28`) 판정이 접어야 한다.
    """
    document, _ = _observe(
        vpc_endpoints=[
            {
                "VpcEndpointId": "vpce-0aaa1111bbbb2222",
                "VpcId": "vpc-0aaa1111bbbb2222",
                "State": "Available",
                "VpcEndpointType": "Interface",
                "ServiceName": "com.amazonaws.us-east-1.s3",
                "SubnetIds": [],
                "Tags": [],
            }
        ]
    )
    assert _one(document, "vpce-0aaa1111bbbb2222")["status"] == "up"


# ---------------------------------------------------------------- `none` vs 「상태 없음」


def test_security_group_and_nacl_get_a_none_entry_without_evidence():
    """§O7: `none` = 항목을 만들고 `?` 를 붙인다(「관측 수단이 없다」를 말해야 한다)."""
    document, _ = _observe()
    # 🔴 접두를 붙여 고른다 — `sg-…` 만으로 찾으면 그 SG 를 참조하는
    # `security_group_reference` 엣지 3개까지 걸려 무엇을 재는지 알 수 없다(실측).
    picked = [
        o
        for o in document["observations"]
        if o["target_id"].startswith(("security_group/", "network_acl/"))
    ]
    assert len(picked) == 4, f"SG 2 + NACL 2 를 기대했다 — {len(picked)}개"
    for observation in picked:
        assert observation["status"] == "none"
        # 영원히 관측 불가다 → 「지금 규칙이 없다」(partial)와 구별돼야 한다.
        assert observation["partial"] is False
        assert observation["evidence"] is None
        assert observation["error_count"] is None
        assert "AWS" in (observation["why"] or "")


def test_no_state_types_have_no_entry_at_all():
    """§O7: 「상태 없음」 = **항목을 만들지 않는다**. `none` 을 주면 화면에 `?` 가 뜬다."""
    document, _ = _observe()
    # 🔴 노드 id 는 `type/…`, 엣지 id 는 `type|…` 다. 슬래시만 자르면 엣지 타입이 절대 일치하지
    # 않아 아래 어서션이 **실패할 수 없게 된다**(통과가 아니라 미측정). 실제로 처음 그렇게 썼다.
    kinds = {re.split(r"[/|]", o["target_id"])[0] for o in document["observations"]}
    assert "association" in kinds, "엣지 타입을 자르는 방식에 판별력이 있어야 한다"
    # 흐름 로그 전용(피어링)도 여기 든다 — 판독기를 안 켠 실행에서는 「상태 없음」과 같이 행이 없다.
    # 🔴 빼면 피어링이 이 검사에서 사라지고, 판독기 없이 피어링 행을 만드는 회귀를 아무도 못 잡는다.
    silent = NO_STATE_NODES | NO_STATE_EDGES | FLOW_LOG_ONLY_EDGES
    assert "peering" in silent
    for type_name in silent:
        assert type_name not in kinds, f"{type_name} 은 관측 항목을 만들지 않아야 한다"
    # 같은 타입이 구조 문서에는 있어야 한다 — 없으면 「안 만들었다」가 아니라 「없었다」다.
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    present = {n["type"] for n in topology["nodes"]} | {
        e["type"] for e in topology["edges"]
    }
    # 파생 노드 2종(2.7.0)은 LB 의 Lambda·외부 IP 대상이 있어야 생긴다 — 기본 가짜에는 없다. 그 둘의 「행 없음」은
    #    `test_lb_targets.py::test_derived_nodes_have_no_observation_rows` 가 그 노드가 **있는** 문서로 잰다.
    # `endpoint_provider`(2.8.0)도 GWLB 엔드포인트 서비스가 있어야 생긴다 — `test_forwarding.py::test_provider_edges_have_no_observation_rows`
    #    가 그 선이 **있는** 문서로 「행 없음」을 잰다.
    # 배치 D(2.10.0) — API·프록시·가속기와 그 선은 기본 가짜에 없다(`rel_world`) — `test_relations_d.py::test_batch_d_types_have_no_observation_rows`
    #    가 그것들이 **있는** 문서로 「행 없음」을 잰다(탄력적 IP · `eip_association` 은 기본 가짜에 있다)
    batch_d = {
        "api_gateway",
        "rds_db_proxy",
        "global_accelerator",
        "vpc_link",
        "proxy_target",
        "accelerator_endpoint",
        # #129(2.11.0) — 셋 다 `rel_world` 에만 있다 · `test_relations_129.py::test_129_types_have_no_observation_rows` 가 잰다
        "core_network",
        "lattice_service_network",
        "lattice_service",
        "connect_transport",
        "core_network_attachment",
        "lattice_vpc_association",
        "lattice_service_association",
        "lattice_target",
        # 2.12.0(ADR 0014) — `rel_world` ⑨ 에만 있다 · `test_waf.py::test_waf_types_have_no_observation_rows` 가 잰다
        "waf_web_acl",
        "waf_protection",
    }
    assert silent - set(DERIVED_TYPES) - {"endpoint_provider"} - batch_d <= present


def test_nacl_association_is_none_not_up():
    """🔴 `services/network_acl.py:31` 이 상수 `"associated"` 를 넣는다 — AWS 값이 아니다.

    타입만 보고 판정하면 NACL↔서브넷 선이 **영원히 `up`** 이다. 대조군은 같은 `association`
    타입인데 출처가 route_table 인 선이다 — 그쪽은 `up` 이어야 한다.
    """
    document, _ = _observe()
    associations = [
        o for o in document["observations"] if o["target_id"].startswith("association|")
    ]
    from_nacl = [o for o in associations if "|network_acl/" in o["target_id"]]
    from_route_table = [o for o in associations if "|route_table/" in o["target_id"]]

    assert from_nacl, "픽스처에 NACL 연관이 있어야 이 테스트가 판별력을 가진다"
    assert from_route_table, "대조군(route_table 발 연관)이 없으면 어서션이 절반이다"
    assert {o["status"] for o in from_nacl} == {"none"}
    # 근거(수집기가 상수를 넣는다 · `services/network_acl.py:31`)는 `observe.py` 주석이 든다 — 화면 「사유」는 고객 말(#130)
    assert all("상태를 제공하지 않습니다" in (o["why"] or "") for o in from_nacl)
    assert {o["status"] for o in from_route_table} == {"up"}


def test_route_without_the_blackhole_key_is_none():
    """구조 수집기가 키를 안 담는 쪽으로 바뀌면 `up` 으로 때우지 않고 「모른다」를 낸다."""
    from nettopo.observe import _observe_route

    observation = _observe_route("route/x", {}).to_dict()
    assert observation["status"] == "none"
    assert observation["partial"] is True


# ---------------------------------------------------------------- coverage · 매달린 참조


def test_unreadable_region_produces_no_observations_and_a_null_count():
    """§O7 규칙 2 — 못 읽은 (계정,리전)의 대상에는 항목을 만들지 않는다. `none` 을 쓰지 마라.

    🔴 `item_count: 0` 으로 적으면 「읽었고 대상이 없다」와 같은 바이트가 된다.
    """
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=OBSERVED_RESOURCE_TYPES,
    )
    denied = FakeAws(
        errors={
            method: client_error("AccessDenied", method, "권한이 없다")
            for method in (
                "describe_vpcs",
                "describe_subnets",
                "describe_route_tables",
                "describe_internet_gateways",
                "describe_nat_gateways",
                "describe_transit_gateways",
                "describe_transit_gateway_attachments",
                "describe_vpc_peering_connections",
                "describe_vpc_endpoints",
                "describe_load_balancers",
                # W3 — 판정이 붙은 10종의 목록 호출. `list_clusters` 는 ECS·EKS 가 같은 이름이라 한 줄로 둘 다 막힌다.
                "describe_transit_gateway_route_tables",
                "describe_db_instances",
                "list_clusters",
                "list_notebook_instances",
                "list_domains",
                "list_endpoints",
                "describe_instance_connect_endpoints",
                "describe_instances",
                "list_firewalls",
                # 2.6.0 — VPN 3종.
                "describe_vpn_gateways",
                "describe_customer_gateways",
                "describe_vpn_connections",
                # 2026-10-13 — Direct Connect 3종(DXGW 는 글로벌이지만 리전마다 부른다 — 이 리전 칸이 거부된다).
                "describe_connections",
                "describe_virtual_interfaces",
                "describe_direct_connect_gateways",
                # #114(2.9.0) — VPC 안 IP 를 쓰는 서비스.
                "describe_cache_clusters",
                "describe_db_clusters",
                "list_domain_names",
                "list_clusters_v2",
            )
        }
    )
    state = collect(scope, factory(denied), clock=lambda: OBSERVED_AT, with_state=True)

    document, dropped = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )

    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert document["observations"] == []
    assert dropped == []
    describe = [c for c in document["coverage"] if c["layer"] == "describe"]
    # O10: 칸이 **종류마다** 하나다. 20종을 다 거부했으니 20칸 전부 `denied` 이고, 「하나로
    # 접힌 denied」와 구별된다 — 접혀 있으면 사람은 어느 종류가 막혔는지 알 수 없었다.
    assert len(describe) == len(OBSERVED_RESOURCE_TYPES)
    assert {c["status"] for c in describe} == {"denied"}
    assert all(c["item_count"] is None for c in describe)
    assert all(c["reason"]["code"] == "access_denied" for c in describe)


# 🔴 위 검사는 10종을 **전부** 거부한다 → 읽힌 대상이 0개라서 「못 읽은 자리에는 주장하지
# 않는다」 분기를 **지나가지 않는다**(실패할 수 없는 어서션 = 미측정). 아래가
# 그 절반을 잰다 — 실계정에서만 드러난 결함이 정확히 이 형태였다.
PARTIALLY_DENIED = {
    method: client_error("AccessDenied", method, "권한이 없다")
    for method in (
        "describe_subnets",
        "describe_internet_gateways",
        "describe_nat_gateways",
        "describe_vpc_peering_connections",
        "describe_vpc_endpoints",
        "describe_load_balancers",
    )
}


def test_a_partially_readable_region_keeps_what_it_read_and_says_nothing_about_the_rest():
    """실계정에서만 드러난 결함(2026-09-23 실측 — 한 역할이 14액션 중 4개만 허용).

    🔴 **이 검사는 같은 날 두 번 뒤집혔다.** 처음(자리 단위 게이트)에는 「읽은 것까지 전부
    버린다」를 기대했다 — 그때 관측 coverage 의 칸이 (계정,리전,층)뿐이어서 「서브넷은 못 읽었고
    VPC 는 up 이다」를 적을 자리가 없었기 때문이다. 사용자 결정(ADR 0006 O10): 「일부만 완료를
    만들어서 넣어라. 기존에 한 것을 나중에 또 할 필요가 없다.」 → 칸에 종류축이 생겼고 지금은
    **읽은 종류는 남고 못 읽은 종류만 침묵한다.**

    **양방향을 잰다**(억제 방향만 재면 「전부 억제」도 통과한다 = 미측정):
    ① 거부된 종류의 대상에는 항목이 없다 ② **`ok` 인 종류의 대상에는 항목이 있다.**
    """
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT, errors=PARTIALLY_DENIED)
    # 🔴 전제부터 잰다: 거부되지 않은 종류는 **실제로 읽혔다.** 이게 거짓이면 아래 ②는
    # 「살렸다」가 아니라 「읽은 게 없었다」를 재는 미측정 어서션이 된다.
    read = {n.type for n in state.nodes} - {"account", "region"}
    assert {"vpc", "transit_gateway_attachment"} <= read, read
    assert "subnet" not in read

    document, dropped = build_observation(
        state,
        topology=_snapshot(
            SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, errors=PARTIALLY_DENIED
        ).to_document(),
        observed_at=OBSERVED_AT,
        tool_version="test",
    )

    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert dropped == []
    kinds = {o["target_id"].split("/")[0] for o in document["observations"]}
    # ② 읽은 종류는 살아 있다 — 사람이 이미 확인된 VPC·TGW 를 다시 확인하지 않아도 된다.
    assert {"vpc", "transit_gateway", "transit_gateway_attachment"} <= kinds, kinds
    # ① 거부된 종류는 없다. `none` 으로도 적지 않는다(「영원히 관측 불가」와 같은 `?` 가 된다).
    assert not kinds & {"subnet", "nat_gateway", "internet_gateway", "vpc_endpoint"}

    # 🔴 route 엣지는 NAT 가 거부된 자리에서도 살아야 한다 — 그 선은 route_table 응답에서
    # 나오고, 끝점 종류로 판정하면 틀린다(ADR 0006 O10 마지막 항목).
    assert _one(document, "route|route_table/")["status"] == "up"

    describe = {
        c["resource_type"]: (c["status"], c["item_count"])
        for c in document["coverage"]
        if c["layer"] == "describe"
    }
    # 「일부만 읽었다」= 같은 자리에 `ok` 행과 실패 행이 함께 있는 것. status 값 하나가 아니다.
    assert describe["vpc"] == ("ok", 2)
    assert describe["subnet"] == ("denied", None)
    assert {s for s, _ in describe.values()} == {"ok", "denied"}


def test_a_denied_type_claims_nothing_even_with_the_response_in_hand():
    """억제 방향의 판별력 대조군 — 위 검사에서 거부된 종류는 스냅샷에 **애초에 없다.**

    거부되면 대상도 없으니 「항목이 없다」는 억제가 아니라 재료 부재로도 통과한다(미측정).
    그래서 여기서는 **대상은 그대로 두고 coverage 만** `denied` 로 바꾼다 — 계약이 「그 칸을 못
    읽었다」고 말하는 동안 코드가 그 종류의 상태를 주장하면 안 된다(`schema/tests/
    test_observation_schema.py` 의 `claims_without_reading` 가 잡는 자기모순).
    """
    import dataclasses

    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    assert any(n.type == "vpc" for n in state.nodes), "전제: VPC 응답이 손에 있다"
    state = dataclasses.replace(
        state,
        coverage=tuple(
            dataclasses.replace(
                c, status="denied", item_count=None, reason=c.reason or None
            )
            if c.resource_type == "vpc" and c.region is not None
            else c
            for c in state.coverage
        ),
    )

    document, _ = build_observation(
        state,
        topology=_snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document(),
        observed_at=OBSERVED_AT,
        tool_version="test",
    )

    kinds = {o["target_id"].split("/")[0] for o in document["observations"]}
    assert "vpc" not in kinds
    # 대조군: 다른 종류는 살아 있다(게이트가 전부를 죽인 것이 아니다).
    assert "transit_gateway" in kinds


def test_a_denied_route_table_silences_the_edges_it_would_have_made():
    """🔴 **엣지 쪽 게이트는 여기까지 미측정이었다**(`prove-breaks.sh` 가 찾았다, 2026-09-23).

    `observe.py:473` 의 `if not readable(edge.id)` 를 `and False` 로 풀어도 엔진 검사가 **하나도**
    실패하지 않았다 = 그 게이트는 통과가 아니라 미측정이었다. 위 두 검사가 못 잡는
    이유가 서로 다르다: `PARTIALLY_DENIED` 판은 거부된 종류의 **대상이 애초에 없고**(엣지도 없다),
    종류 하나(`vpc`)만 막는 판은 route 엣지의 종류가 `vpc` 가 아니라 `route_table` 이라 안 닿는다.

    그래서 여기서는 **그 선을 만든 응답**인 `route_table` 만 거부로 바꾼다. 끝점(NAT·IGW·TGW)은
    전부 읽혔고 경로 속성도 손에 있지만, 경로가 지금도 그 모양인지는 모른다 → 주장하지 않는다.
    `association` 도 같이 검사한다 — route_table 이 만든 연관은 사라지고 NACL 이 만든 연관은
    `none` 으로 남아야 한다(엣지의 종류 = 그것을 만든 응답, ADR 0006 O10).
    """
    import dataclasses

    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    # 전제: 경로가 손에 있다. 거짓이면 아래는 「억제했다」가 아니라 「재료가 없다」를 잰다.
    assert any(e.type == "route" for e in state.edges), "전제: route 응답이 손에 있다"
    state = dataclasses.replace(
        state,
        coverage=tuple(
            dataclasses.replace(
                c,
                status="denied",
                item_count=None,
                reason=Reason("access_denied", "DescribeRouteTables", "테스트"),
            )
            if c.resource_type == "route_table" and c.region is not None
            else c
            for c in state.coverage
        ),
    )

    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    document, dropped = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )

    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert dropped == []
    node_type = {n["id"]: n["type"] for n in topology["nodes"]}
    from_route_table = {
        e["id"]
        for e in topology["edges"]
        if e["type"] in ("route", "association")
        and node_type.get(e["source"]) == "route_table"
    }
    assert {e.split("|")[0] for e in from_route_table} == {"route", "association"}
    assert not from_route_table & set(_by_id(document))
    # 🔴 대조군 ⓪(W3): TGW 라우트 테이블이 만든 `route` 선은 **그 응답**에서 나왔으므로 살아 있다. 칸을 엣지
    # 종류(`route` → `route_table`)로 고정하던 동안은 여기서 같이 죽었다 — 읽은 것을 버린 것이다.
    tgw_routes = {
        e["id"]
        for e in topology["edges"]
        if e["type"] == "route"
        and node_type.get(e["source"]) == "transit_gateway_route_table"
    }
    assert tgw_routes and tgw_routes <= set(_by_id(document))
    assert {_by_id(document)[e]["status"] for e in tgw_routes} == {"up"}

    # 대조군 ①: 같은 자리의 노드 상태는 살아 있다(게이트가 자리째 죽인 것이 아니다).
    kinds = {o["target_id"].split("/")[0] for o in document["observations"]}
    assert {"vpc", "subnet", "nat_gateway", "internet_gateway"} <= kinds, kinds
    # 대조군 ②: NACL 이 만든 연관은 그대로 `none` — 권한과 무관한 판단이라 거부에 영향받지 않는다.
    nacl_assoc = {
        e["id"]
        for e in topology["edges"]
        if e["type"] == "association" and node_type.get(e["source"]) == "network_acl"
    }
    by_id = _by_id(document)
    assert nacl_assoc and nacl_assoc <= set(by_id)
    assert {by_id[e]["status"] for e in nacl_assoc} == {"none"}


def test_metric_layer_is_skipped_not_denied():
    """2층 기본 off(ADR 0006 D4) — 「막혔다」가 아니라 「안 켰다」다."""
    document, _ = _observe()
    metric = [c for c in document["coverage"] if c["layer"] == "metric"]
    assert metric and all(c["status"] == "skipped" for c in metric)
    assert all(c["item_count"] is None and c["reason"] is None for c in metric)


def test_describe_coverage_counts_only_what_it_emitted():
    """🔴 합이 관측 수와 **같지 않다** — 그 차이가 계약의 사실이다(ADR 0006 O10).

    `item_count` 는 「그 (계정,리전,종류,층) 칸에서 판정한 대상 수」인데, 부르지 않는 종류
    (SG·NACL·SG 참조·NACL 연관)의 `none` 항목은 **어느 칸에도 속하지 않는다.** 그래서
    `sum(item_count) + 칸 밖 항목 = 관측 수`다. 차이를 상수로 박지 않고 구조 문서에서 센다.
    """
    document, _ = _observe()
    describe = [c for c in document["coverage"] if c["layer"] == "describe"]
    assert len(describe) == len(OBSERVED_RESOURCE_TYPES)
    assert {c["status"] for c in describe} == {"ok"}

    unobserved = set(SUPPORTED_RESOURCE_TYPES) - set(OBSERVED_RESOURCE_TYPES)
    # DX 3종은 2026-10-13 에 1층 규칙이 붙어 이 목록에서 빠졌다(§O7 「DX 3종」 — DXGW 는 글로벌 자리 규칙).
    assert unobserved == {
        "security_group",
        "network_acl",
        # 🔴 오토 스케일링 그룹은 판정할 것이 없다(논리 묶음 — §O7 `none`). 칸 밖 항목이다.
        "auto_scaling_group",
        # 🔴 cloudfront 는 이유가 다르다 — 지표가 없는 게 아니라 `AWS/CloudFront` 가 **us-east-1
        # 에만** 있어서다(`observe.py` 의 규칙 표 주석). 리전 축을 고정하기 전에 넣으면
        # us-east-1 을 안 보는 고객에게 전 배포가 「확인 불가」가 된다.
        "cloudfront",
        # W3(2026-10-02) — 2차 수집기 3종(EC2·EKS·FW)과 W2 7종은 판정이 붙어 이 목록에서 빠졌다.
        # 2.8.0(P2-6) — Lambda 함수는 자기 상태 API 를 부르지 않는다(`NO_STATE_NODES` · LB → 함수 선의 헬스는 선이 든다)
        "lambda_function",
        # 2.8.0(P2-4) — Route 53 호스팅 영역도 상태가 없다(`NO_STATE_NODES`)
        "route53_hosted_zone",
        # 배치 D(2.10.0) — 다섯 다 상태 없음(`NO_STATE_NODES` · ADR 0006 §O7 「배치 D」 행) · `api_gateway_v2` 는 노드가 `api_gateway` 다
        "elastic_ip",
        "api_gateway",
        "api_gateway_v2",
        "rds_db_proxy",
        "global_accelerator",
        # #129(2.11.0) — 셋 다 상태 없음(`NO_STATE_NODES` · ADR 0006 §O7 「#129」 행)
        "core_network",
        "lattice_service_network",
        "lattice_service",
        # 2.12.0(ADR 0014) — 상태 없음(`NO_STATE_NODES` · ADR 0006 §O7 「waf_web_acl」 행) · 두 수집 종류가 같은 카드
        "waf_web_acl",
        "waf_web_acl_global",
    }, unobserved
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    node_type = {n["id"]: n["type"] for n in topology["nodes"]}
    offgrid = {n["id"] for n in topology["nodes"] if n["type"] in unobserved}
    offgrid |= {
        e["id"] for e in topology["edges"] if node_type.get(e["source"]) in unobserved
    }
    priced = [o for o in document["observations"] if o["target_id"] not in offgrid]

    assert sum(c["item_count"] for c in describe) == len(priced)
    # 대조군: 칸 밖 항목이 0이면 위 등식은 「합 == 전체」와 같아져 이 검사가 무엇도 재지 않는다.
    assert 0 < len(priced) < len(document["observations"])


def test_a_target_missing_from_the_structure_document_is_dropped():
    """🔴 매달린 참조 = 「없어진 리소스에 상태가 붙은 그림」. 조용히 넣으면 렌더가 깨진다."""
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    victim = next(n["id"] for n in topology["nodes"] if n["type"] == "nat_gateway")
    topology["nodes"] = [n for n in topology["nodes"] if n["id"] != victim]
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)

    document, dropped = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )

    assert victim in dropped
    assert victim not in _by_id(document)


# ---------------------------------------------------------------- 표 ↔ 코드


def _o7_rows(block_title: str) -> dict[str, str]:
    """§O7 표의 **1열 타입 → 마지막 열(「낼 수 있는 최대 어휘」)**.

    §O7 은 표를 정본이라 선언했다. 여기서 읽는 것은 그 선언의 생산 쪽 절반이다 — 행을 더하고
    코드에 규칙을 안 더하면 그 타입은 상태가 조용히 안 붙는다.
    """
    section = _adr6().split("### O7 — ")[1].split("\n### ")[0]
    _, _, after = section.partition(f"**{block_title}**")
    # 🔴 노드 블록은 「**엣지**」 앞까지 **전부**다 — 추가 표(2차 4종 · CloudFront · W2 7종)가 굵은 머리말 뒤에
    # 따로 있다. 첫 머리말에서 끊던 동안 그 12행은 대조에 안 들어갔고(W3 에서 발견), 규칙 없이 「`live`·`up`·
    # `down`」 이라 적힌 행(EC2 등)이 조용히 통과했다. 스키마 쪽 파서(`o7_vocabulary`)는 처음부터 전부 읽었다.
    block = (
        after.split("**엣지**")[0] if block_title == "노드" else after.split("\n**")[0]
    )

    rows: dict[str, str] = {}
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or all(set(c) <= {"-", ":"} for c in cells):
            continue
        # 🔴 행 이름에 수식어가 붙을 수 있다(`association←network_acl` — §O7 「행 이름 문법」,
        # 승인 2026-09-23). 수식어를 못 읽으면 그 행이 **통째로 빠지고**, 아래 대조는 코드에만
        # 있는 타입을 지목하는 대신 조용히 작아진다.
        # 🔴 `0-9` 가 빠져 있던 동안 `ec2_instance`·`ec2_instance_connect_endpoint` 행이 조용히 안 읽혔다(W3 발견 —
        # 스키마 쪽 `o7_state_source_types` 는 2026-09-26 에 같은 결함을 고쳤다).
        types = re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", cells[0])
        if not types or "node_type" in types or "edge_type" in types:
            continue  # 헤더 행
        for type_name in types:
            rows[type_name] = cells[-1]
    assert rows, f"§O7 의 **{block_title}** 표에서 행을 못 읽었다 — 어서션이 무력해진다"
    return rows


def _classify(vocabulary: str) -> str:
    # 🔴 「2층 전용」이 먼저다(ADR 0008 D8-1). 이 행들은 1층이 근거를 안 주지만 **「상태 없음」이
    # 아니다** — 2층이 그 구간을 재면 행이 생긴다. 순서를 뒤집으면 둘이 합쳐져 아래 대조가
    # 「엣지에 2층을 붙이지 말라」는 옛 규칙으로 되돌아간다.
    if "2층 전용" in vocabulary:
        return "metric_only"
    # 「흐름 로그 전용」(ADR 0008 「D4b 개정」)도 「상태 없음」보다 먼저다 — 같은 이유다.
    if "흐름 로그 전용" in vocabulary:
        return "flow_log_only"
    if "상태 없음" in vocabulary:
        return "no_state"
    if "`none`" in vocabulary:
        return "no_means"
    return "rule"


def test_o7_parser_reads_the_last_column():
    """파서 판별력 — 네 분류가 표에서 **실제로** 갈라지는지. 하나로 뭉치면 아래가 무력해진다."""
    nodes = _o7_rows("노드")
    assert _classify(nodes["vpc"]) == "rule"
    assert _classify(nodes["route_table"]) == "no_state"
    assert _classify(nodes["security_group"]) == "no_means"
    assert len({_classify(v) for v in nodes.values()}) == 3

    # 네 번째·다섯 번째 분류는 엣지 표에만 있다 — 노드 표에서 세 분류가 갈라지는 것과 별개로 잰다.
    # 피어링은 흐름 로그 전용으로 옮겼고 「상태 없음」 행은 `origin`(#43) 이 다시 채운다 — 다섯 분류다.
    edges = _o7_rows("엣지")
    assert _classify(edges["attachment"]) == "metric_only"
    assert _classify(edges["peering"]) == "flow_log_only"
    assert _classify(edges["security_group_reference"]) == "no_means"
    assert _classify(edges["origin"]) == "no_state"
    assert {_classify(v) for v in edges.values()} == {
        "rule",
        "no_state",
        "no_means",
        "metric_only",
        "flow_log_only",
    }


def test_node_rules_match_the_o7_table():
    documented = _o7_rows("노드")
    by_class: dict[str, set[str]] = {
        "rule": set(),
        "no_state": set(),
        "no_means": set(),
    }
    for type_name, vocabulary in documented.items():
        by_class[_classify(vocabulary)].add(type_name)

    assert by_class["rule"] == set(NODE_RULES), (
        "§O7 노드 표에서 어휘를 낼 수 있다고 적힌 타입과 `NODE_RULES` 가 다르다 — "
        f"표에만 있다: {sorted(by_class['rule'] - set(NODE_RULES))} · "
        f"코드에만 있다: {sorted(set(NODE_RULES) - by_class['rule'])}"
    )
    assert by_class["no_means"] == set(NO_MEANS_NODES)
    assert by_class["no_state"] == set(NO_STATE_NODES)


def test_edge_rules_match_the_o7_table():
    """🔴 수식어 행은 분류에서 **빼고** 아래 짝 테스트가 따로 잰다.

    `association` 은 §O7 에서 두 행이다(`←route_table` = 판정 / `←network_acl` = `none`).
    엣지 타입 하나가 두 분류에 동시에 속하므로 타입 이름만 보는 이 대조에 섞으면 어느 쪽으로
    맞춰도 거짓이 된다 — 코드의 갈림은 타입이 아니라 **source 노드의 종류**이기 때문이다
    (`ASSOCIATION_EVIDENCE`, `observe.py:142`).
    """
    documented = _o7_rows("엣지")
    by_class: dict[str, set[str]] = {
        "rule": set(),
        "no_state": set(),
        "no_means": set(),
        "metric_only": set(),
        "flow_log_only": set(),
    }
    for type_name, vocabulary in documented.items():
        if "←" in type_name:
            continue
        by_class[_classify(vocabulary)].add(type_name)

    # `route` 는 값이 불린이라 `_Rule` 이 아니라 `_observe_route()` 가 든다 — 규칙 1 의 예외.
    # `association` 은 수식어 행뿐이라 여기서 빠진다(아래 짝 테스트가 든다).
    # `load_balancer_target`(2.7.0)도 `_Rule` 이 아니라 `_observe_lb_target()` 이 든다 — 값이 등록 헬스의 수다.
    assert by_class["rule"] == (set(EDGE_RULES) - {"association"}) | {
        "route",
        "load_balancer_target",
    }
    assert by_class["no_means"] == set(NO_MEANS_EDGES)
    assert by_class["no_state"] == set(NO_STATE_EDGES)
    # 🔴 ADR 0008 D8-1 — 이 세 종류는 1층이 아니라 **2층(구간 지표)** 이 행을 만든다.
    # 표에서 한 줄을 「상태 없음」으로 되돌리면 코드는 여전히 2층 행을 만들고, 이 어서션만이
    # 그 불일치를 잡는다(사람이 보는 결과: 어태치먼트 선이 측정됐는데 화면에 안 나온다).
    assert by_class["metric_only"] == set(METRIC_ONLY_EDGES)
    # ADR 0008 「D4b 개정」 — 피어링은 흐름 로그가 행을 만든다. 표를 「상태 없음」으로 되돌리거나
    # 코드에서 집합을 비우면 둘 중 한쪽만 바뀐 것이고, 이 어서션만이 그 불일치를 잡는다.
    assert by_class["flow_log_only"] == set(FLOW_LOG_ONLY_EDGES)


def test_association_rows_match_the_evidence_map():
    """§O7 의 `association` 두 행 ↔ `ASSOCIATION_EVIDENCE` 의 두 항목.

    수식어 행을 도입한 뒤 위 대조가 `association` 을 건너뛰므로, 이 검사가 없으면 두 행 중
    하나를 지워도(또는 `ASSOCIATION_EVIDENCE` 에서 한 수집기를 빼도) 아무것도 빨개지지 않는다.

    사람이 보는 결과: NACL↔서브넷 선이 영원히 초록(`up`)이 되거나, 라우트테이블↔서브넷 선이
    영원히 `?` 가 된다. 둘 다 화면만 보고는 판정이 죽은 것과 구별할 수 없다.
    """
    documented = _o7_rows("엣지")
    rows = {
        name.split("←")[1]: _classify(vocabulary)
        for name, vocabulary in documented.items()
        if name.startswith("association←")
    }
    assert rows == {"route_table": "rule", "network_acl": "no_means"}, rows
    assert {source: kind == "rule" for source, kind in rows.items()} == (
        ASSOCIATION_EVIDENCE
    ), f"표 {rows} ≠ 코드 {ASSOCIATION_EVIDENCE}"
    assert "association" in EDGE_RULES, (
        "표가 판정을 허락한 쪽(route_table 발)을 들 규칙이 코드에 없다"
    )


def test_association_evidence_covers_every_collector_that_makes_one():
    """`association` 엣지를 만드는 서비스 파일이 늘면 근거 유무를 등록해야 한다.

    등록하지 않으면 `_association_evidence()` 가 `None` 을 돌려 관측이 「결함」이라 말한다 —
    조용히 `up` 이 되지는 않지만, 그 상태로 릴리스되면 화면이 통째로 `?` 다.
    """
    sources = set()
    for path in (ROOT / "engine" / "src" / "nettopo" / "services").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"association"' in text:
            sources.add(path.stem)
    assert sources, "association 엣지를 만드는 파일을 못 찾았다 — 어서션이 무력해진다"
    assert sources == set(ASSOCIATION_EVIDENCE), (
        f"association 을 만드는 수집기 {sorted(sources)} ≠ "
        f"등록된 근거 {sorted(ASSOCIATION_EVIDENCE)}"
    )


def test_observed_types_exclude_the_ones_with_nothing_to_read():
    """SG·NACL 은 `none` 밖에 못 내므로 AWS 를 부를 이유가 0이다(ADR 0004 D2-2 함정 ①)."""
    assert set(OBSERVED_RESOURCE_TYPES) <= set(SUPPORTED_RESOURCE_TYPES)
    assert set(NO_MEANS_NODES) & set(OBSERVED_RESOURCE_TYPES) == set()
    # route_table 은 상태가 없는데도 있어야 한다 — route/association 이 그 응답에서만 나온다.
    assert "route_table" in OBSERVED_RESOURCE_TYPES


def test_history_defaults_match_the_adr():
    """「승인된 기본값」 표가 정본이다 — 코드만 고치면 픽스처·스키마 테스트와 갈라진다."""
    section = _adr6().split("## 승인된 기본값")[1].split("\n## ")[0]
    numbers = {
        int(n.replace(",", "")) for n in re.findall(r"\*\*([0-9][0-9,]*)\*\*", section)
    } | {int(n.replace(",", "")) for n in re.findall(r"`([0-9][0-9,]*)`", section)}
    assert numbers, "승인된 기본값 표에서 숫자를 못 읽었다 — 어서션이 무력해진다"
    for value in (
        DEFAULT_RESOLUTION_SECONDS,
        DEFAULT_RETENTION_SECONDS,
        DEFAULT_FLAP_COLLAPSE_THRESHOLD,
        DEFAULT_MAX_CHANGES,
    ):
        assert value in numbers, f"{value} 가 ADR 「승인된 기본값」에 없다"


def test_history_block_uses_those_defaults():
    document, _ = _observe()
    assert document["history"] == {
        "baseline_at": OBSERVED_AT,
        "resolution_seconds": DEFAULT_RESOLUTION_SECONDS,
        "retention_seconds": DEFAULT_RETENTION_SECONDS,
        "truncated": False,
        "flap_collapse_threshold": DEFAULT_FLAP_COLLAPSE_THRESHOLD,
        "max_changes": DEFAULT_MAX_CHANGES,
    }
    # 🔴 빈 배열은 「변경 없음」이 아니라 「기준선이 이번 관측이라 비교 대상이 없다」다(S4).
    assert document["changes"] == []


def test_the_pair_carries_the_structure_fingerprint_when_the_structure_has_one():
    """구조 문서에 지문이 있으면 관측 문서가 **그대로** 옮긴다 — 화면은 이 값으로 짝을 맞춘다."""
    topology = json.loads(to_json(_snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT)))
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    document, _ = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )
    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert (
        document["run"]["topology_content_digest"] == topology["run"]["content_digest"]
    )


def test_a_structure_without_a_fingerprint_leaves_the_key_out():
    """옛 구조 문서(지문 없음) → 키째 없다. `null` 을 적으면 화면이 「양쪽 다 null = 같다」로 읽을 수 있다."""
    document, _ = _observe()
    assert "topology_content_digest" not in document["run"]


# ── QA B P2(2026-10-14): 전환 상태는 「관측 불가」가 아니라 `down` ── (지우는 중은 예외 — QA C 2026-10-21 · `test_deleting_state.py`) ──────────────────────────────────────
# 실측: TGW 피어링 수락 대기(`pendingAcceptance`)가 이력에 「관측 불가 → 정상」(B-08) · 엔드포인트 삭제 중(`deleting`)이
# 「정상 → 관측 불가」(B-12). 목록에 없는 값은 `_judge` 가 `none` 으로 떨어뜨린다 — 전환 상태를 규칙에 넣어야 한다.
TRANSITIONAL_DOWN = [
    ("vpc", "pending"),
    ("subnet", "pending"),
    ("subnet", "unavailable"),
    ("internet_gateway", "attaching"),
    ("internet_gateway", "detaching"),
    ("nat_gateway", "pending"),
    ("transit_gateway", "pending"),
    ("transit_gateway_attachment", "pendingAcceptance"),
    ("transit_gateway_attachment", "initiatingRequest"),
    ("transit_gateway_attachment", "initiating"),
    ("transit_gateway_attachment", "pending"),
    ("transit_gateway_attachment", "rollingBack"),
    ("vpc_peering_connection", "pending-acceptance"),
    ("vpc_peering_connection", "initiating-request"),
    ("vpc_peering_connection", "provisioning"),
    ("vpc_endpoint", "pendingAcceptance"),
    ("vpc_endpoint", "pending"),
    ("vpc_endpoint", "partial"),
    ("load_balancer", "provisioning"),
]


@pytest.mark.parametrize("node_type,value", TRANSITIONAL_DOWN)
def test_a_transitional_state_is_down_not_unknown(node_type, value):
    from nettopo.observe import NODE_RULES, _judge

    got = _judge(
        NODE_RULES[node_type], value, f"{node_type}/111122223333/us-east-1/x", "node"
    )
    assert got.status == "down", (
        f"{node_type}={value} → {got.status}(관측 불가로 떨어지면 이력에 「관측 불가 → 정상」)"
    )
    assert value in (
        got.why or ""
    )  # 사람이 콘솔에서 찾을 수 있게 AWS 원문 값을 그대로 말한다


@pytest.mark.parametrize(
    "node_type,value",
    [("transit_gateway", "modifying"), ("transit_gateway_attachment", "modifying")],
)
def test_modifying_still_carries_traffic(node_type, value):
    from nettopo.observe import NODE_RULES, _judge

    assert (
        _judge(
            NODE_RULES[node_type],
            value,
            f"{node_type}/111122223333/us-east-1/x",
            "node",
        ).status
        == "up"
    )


def test_an_impaired_load_balancer_is_up_and_says_so():
    from nettopo.observe import NODE_RULES, _judge

    got = _judge(
        NODE_RULES["load_balancer"],
        "active_impaired",
        "load_balancer/111122223333/us-east-1/x",
        "node",
    )
    assert got.status == "up" and "손상" in (got.why or "")


# ── 화면 「사유」 칸 말투(#130 · docs/qa/copy-a.md ⑬) ─────────────────────────────────────────
# 관측 행의 `why` 는 정보창 「사유」 칸(`flow.js` `obsRows`)과 VIF 줄에 **그대로** 나온다. 화면 말투는 AWS 한국어
# 콘솔의 「~합니다」다 — 「~다」로 끝나는 절(「없다」·「않는다」·「보인다」)이 하나라도 있으면 FAIL. 렌더러 쪽 같은 규율 =
# `renderer/verify-aws.mjs` 검사 129 「옛 말투」. 절 = ` — `·`. `·` · `·줄바꿈으로 나눈 조각 · 끝 괄호는 안팎 둘 다 본다.
_OLD_TONE_OK = ("캐나다",)


def _old_tone_clauses(text: str) -> list[str]:
    def polite(c: str) -> bool:
        if c.endswith("습니다"):
            return True
        # 「합니다·됩니다·입니다」 = 「니다」 앞 글자 받침 ㅂ(종성 17). 「아니다」는 받침이 없다 → 옛 말투
        return (
            c.endswith("니다")
            and len(c) >= 3
            and "가" <= c[-3] <= "힣"
            and (ord(c[-3]) - 0xAC00) % 28 == 17
        )

    bad = []
    for clause in re.split(r" — |\. | · |\n", text):
        cands = [clause.strip()]
        while cands[-1].endswith(")") and "(" in cands[-1]:
            head, _, inner = cands[-1][:-1].rpartition("(")
            cands += [inner.strip(), head.strip()]
        for c in cands:
            c = c.rstrip(".!? ")
            if c.endswith("다") and not polite(c) and not c.endswith(_OLD_TONE_OK):
                bad.append(clause.strip())
                break
    return bad


def test_the_old_tone_detector_catches_what_it_should():
    """판정기 자체의 대조군 — 잡아야 할 것과 놓아야 할 것을 한 번씩."""
    assert _old_tone_clauses("대상이 없다")
    assert _old_tone_clauses("판정하지 않습니다(이력에 남는다)")
    assert _old_tone_clauses("관측 불가 — 이 칸은 「없음」이 아니다")
    assert _old_tone_clauses("세지 못했다(0 이 아닙니다)")
    assert not _old_tone_clauses(
        "삭제 중입니다(삭제가 끝나면 이력에 「없어짐」으로 남습니다)"
    )
    assert not _old_tone_clauses("대상 2개 중 정상 0 — AWS 가 비정상 2개로 보고")
    assert not _old_tone_clauses("태스크가 0개입니다(원하는 수 > 0)")
    assert not _old_tone_clauses("캐나다")


def _every_screen_why() -> dict[str, str]:
    """엔진이 관측 행 `why` 로 낼 수 있는 문장 전부(값을 끼우는 문장은 대표 값으로 만든다)."""
    from nettopo import metrics as _metrics
    from nettopo import observe as _observe

    out: dict[str, str] = {}
    for key, why in _observe._FOLDED_WHY.items():
        out[f"접힌 값 {key}"] = why
    for key, why in {**_observe.NO_MEANS_NODES, **_observe.NO_MEANS_EDGES}.items():
        out[f"관측 수단 없음 {key}"] = why
    out["NACL 연결"] = _observe.NO_MEANS_NACL_ASSOCIATION
    for node_type, rule in sorted(_observe.NODE_RULES.items()):
        for value in sorted(rule.deleting):
            got = _observe._judge(rule, value, f"{node_type}/x", "node")
            out[f"삭제 중 {node_type}={value}"] = got.why or ""
        unknown = _observe._judge(rule, "__no_such_value__", f"{node_type}/x", "node")
        out[f"규칙 없음 {node_type}"] = unknown.why or ""
    blackhole = {"blackhole": True, "destinations": ["10.0.0.0/16"]}
    out["경로 blackhole"] = _observe._observe_route("e", blackhole).why or ""
    out["경로 blackhole 키 없음"] = _observe._observe_route("e", {}).why or ""
    out["타입 규칙 없음"] = _observe._no_rule("x", "node", "made_up").why or ""
    for reporting in (_metrics.REPORT_ALWAYS, _metrics.REPORT_NONZERO_ONLY):
        reading = _metrics.Reading("BytesOut", "flow", "Sum", 0, 0, None, reporting)
        out[f"조용한 지표 {reporting}"] = _observe._quiet_why(reading) or ""
    verdict = _metrics.Verdict(
        metric_names=("BytesIn",),
        flow_names=(),
        flow_metric=None,
        flow_value=0.0,
        flow_points=0,
        drop_metric=None,
        drop_value=0.0,
        block_drop_metric=None,
        block_drop_value=0.0,
        blocked_metric=None,
        measured=True,
        arrival_metric="BytesIn",
        arrival_value=5.0,
    )
    out["들어옴만"] = verdict.arrival_why or ""
    # 🆕 #150 — 선(엣지) 행의 `why` 전부. 선 정보창 「사유」 칸은 고객 말을 내고 이 원문은 그 칸의 title 에 남는다
    for key, why in _edge_whys().items():
        out[f"선 {key}"] = why
    return out


def _edge_whys() -> dict[str, str]:
    """엔진이 **엣지** 관측 행 `why` 로 낼 수 있는 문장 전부(#150 · 선 정보창 「사유」 칸 원문)."""
    from nettopo import metrics as _metrics
    from nettopo import observe as _observe

    out: dict[str, str] = {}
    blackhole = {"blackhole": True, "destinations": ["10.0.0.0/16"]}
    out["경로 blackhole"] = _observe._observe_route("e", blackhole).why or ""
    out["경로 blackhole 키 없음"] = _observe._observe_route("e", {}).why or ""
    rule = _observe.EDGE_RULES["association"]
    for value in sorted(rule.down):
        out[f"연관 {value}"] = _observe._judge(rule, value, "e", "edge").why or ""
    out["연관 규칙 없음"] = _observe._judge(rule, "__no__", "e", "edge").why or ""
    out["엣지 타입 규칙 없음"] = _observe._no_rule("e", "edge", "made_up").why or ""
    for key, why in _observe.NO_MEANS_EDGES.items():
        out[f"관측 수단 없음 {key}"] = why
    out["NACL 연결"] = _observe.NO_MEANS_NACL_ASSOCIATION
    lbt = _observe._observe_lb_target(
        "e",
        {"target_count": 2, "healthy_target_count": 0, "unhealthy_target_count": 2},
        None,
    )
    out["LB 대상 전부 비정상"] = lbt.why or ""
    out["2층 응답 누락"] = _observe._MISSING_RESULT
    probe = _metrics.EDGE_PROBES["attachment"]
    out["2층 차원 못 만듦"] = _metrics._unbuildable_why(probe)
    base = dict(
        metric_names=("BytesIn",),
        flow_names=("BytesOut",),
        flow_metric="BytesOut",
        flow_value=0.0,
        flow_points=0,
        drop_metric="PacketDropCountBlackhole",
        drop_value=3.0,
        block_drop_metric="PacketDropCountBlackhole",
        block_drop_value=3.0,
        blocked_metric=None,
        measured=True,
        arrival_metric="BytesIn",
        arrival_value=5.0,
    )
    out["차단 드롭"] = _metrics.Verdict(**base).block_reason or ""
    state = {**base, "blocked_metric": "TunnelState", "block_drop_value": 0.0}
    out["차단 상태 지표"] = _metrics.Verdict(**state).block_reason or ""
    return out


def test_every_why_the_panel_shows_is_in_console_tone():
    """🔴 「사유」 칸은 엔진 문장을 그대로 낸다 — 엔진이 「~다」로 쓰면 화면 한 칸만 옛 말투가 된다(#130 · QA C 실계정)."""
    whys = _every_screen_why()
    assert any(k.startswith("삭제 중") for k in whys) and len(whys) > 20, sorted(whys)
    assert all(whys.values()), [k for k, v in whys.items() if not v]
    bad = {k: _old_tone_clauses(v) for k, v in whys.items() if _old_tone_clauses(v)}
    assert not bad, bad
    assert not [k for k, v in whys.items() if "**" in v], (
        "화면 문장에 마크다운 강조가 있다"
    )
