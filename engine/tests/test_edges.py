"""엣지 축 — ADR 0002 의 결정이 산출물에서 실제로 성립하는가.

여기까지 `edges` 는 항상 `[]` 였다. 즉 「모든 엣지의 양 끝이 노드로 존재한다」 같은
참조 정합 어서션은 **통과했지만 미측정**이었다(빈 배열은 어떤 조건도 위반하지 않는다).
이 파일이 그 어서션들에 처음 힘을 준다.

각 검사는 **없어야 하는 것**만 재지 않는다. "선이 없다"는 코드가 그 종류를 아예 만들지
않아도 통과하므로, 같은 검사 안에 **살아 있는 짝**을 함께 재서 판별력을 확인한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    ACCOUNT,
    APP_IGW,
    APP_SG,
    APP_VPC,
    CORE_TGW,
    DB_SG,
    EGRESS_NAT,
    EGRESS_SUBNET,
    EGRESS_VPC,
    OUTSIDER,
    PARTNER,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    TRANSIT_GATEWAYS,
    VPCS,
    FakeAws,
    FakeCloudWatch,
    client_error,
    factory,
    factory_by_pair,
    factory_by_region,
    factory_for,
    series_key,
)
from jsonschema import Draft202012Validator
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.metrics import plan, read_metrics
from nettopo.model import Scope
from nettopo.observe import build_observation

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json"
FIXED_CLOCK = "2026-01-02T03:04:05Z"
SEOUL = "ap-northeast-2"

EDGE_TYPES = frozenset(
    {
        "route",
        "association",
        "attachment",
        "peering",
        "endpoint_connection",
        "security_group_reference",
        "load_balancer_placement",
        "origin",
        "load_balancer_target",
        "endpoint_provider",
        "dns_alias",
        "zone_association",
        # 배치 D(2.10.0) — 기본 가짜에는 `eip_association` 만 있다 · 나머지 셋은 `rel_world` 가 만든다
        "eip_association",
        "vpc_link",
        "proxy_target",
        "accelerator_endpoint",
        # #129(2.11.0) — 다섯 다 `rel_world` 가 만든다(TGW Connect · Cloud WAN · VPC Lattice)
        "connect_transport",
        "core_network_attachment",
        "lattice_vpc_association",
        "lattice_service_association",
        "lattice_target",
        # 2.12.0(ADR 0014) — `rel_world` ⑨ 가 만든다(기본 가짜에는 web ACL 목록이 없다)
        "waf_protection",
    }
)


def scope_of(*accounts: str, regions: tuple[str, ...] = (REGION,)) -> Scope:
    return Scope(
        account_ids=tuple(accounts),
        regions=regions,
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )


def run(scope: Scope, client_factory: Any) -> dict:
    return collect(scope, client_factory, clock=lambda: FIXED_CLOCK).to_document()


@pytest.fixture(scope="module")
def document() -> dict:
    """1계정 1리전 · 12종 전부."""
    return run(scope_of(ACCOUNT), factory(FakeAws()))


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    assert SCHEMA_PATH.is_file(), SCHEMA_PATH
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def node(document: dict, native: str) -> dict:
    found = [n for n in document["nodes"] if n["id"].endswith(f"/{native}")]
    assert len(found) == 1, f"{native} 노드가 {len(found)} 개다"
    return found[0]


def attrs(document: dict, native: str) -> dict:
    return node(document, native)["attributes"]


def out_edges(document: dict, native: str, edge_type: str | None = None) -> list[dict]:
    return [
        e
        for e in document["edges"]
        if e["source"].endswith(f"/{native}")
        and (edge_type is None or e["type"] == edge_type)
    ]


def targets(document: dict, native: str, edge_type: str | None = None) -> list[str]:
    return [
        e["target"].rsplit("/", 1)[1] for e in out_edges(document, native, edge_type)
    ]


# --- 종류 커버리지 ----------------------------------------------------------


def test_every_edge_type_in_the_contract_has_a_generation_path(document):
    """스키마가 정의만 해 두고 엔진이 만들지 않는 엣지 종류가 있으면 렌더러는 그 선을
    영원히 못 그린다 — 그 사실이 조용히 남지 않게 한다."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert EDGE_TYPES == set(schema["$defs"]["edge_type"]["enum"])
    # 🔵 `endpoint_provider`(2.8.0)는 GWLB 와 그 엔드포인트 서비스가 있어야 생긴다 — 기본 가짜에는 없다. 그 세계(`fwd_world`)를
    #    같이 돌려 합집합으로 잰다(기본 가짜에 GWLB 를 더하면 LB 수·속성을 못박은 검사 수십 개가 같이 움직인다).
    import fwd_world

    fwd = collect(
        Scope(
            account_ids=(fwd_world.ACCOUNT,),
            regions=(fwd_world.REGION,),
            resource_types=SUPPORTED_RESOURCE_TYPES,
        ),
        factory(fwd_world.world()),
        clock=lambda: FIXED_CLOCK,
    ).to_document()
    import rel_world

    rel = collect(
        Scope(
            account_ids=(rel_world.ACCOUNT,),
            regions=(rel_world.REGION,),
            resource_types=SUPPORTED_RESOURCE_TYPES,
        ),
        factory(rel_world.world()),
        clock=lambda: FIXED_CLOCK,
    ).to_document()
    assert "endpoint_provider" not in {e["type"] for e in document["edges"]}
    assert (
        {e["type"] for e in document["edges"]}
        | {e["type"] for e in fwd["edges"]}
        | {e["type"] for e in rel["edges"]}
    ) == EDGE_TYPES


def test_the_full_resource_document_satisfies_the_contract(document, validator):
    """12종 전부 + 엣지를 담은 산출물이 스키마를 통과한다. 🔴 `attributes` 는 스칼라와
    스칼라 배열만 허용한다 — 중첩 객체(피어링 requester/accepter)를 그대로 넣으면 여기서 깨진다."""
    assert [
        f"{e.json_path}: {e.message}" for e in validator.iter_errors(document)
    ] == []


def test_edge_ids_are_sorted(document):
    """정렬은 결정론의 일부다(불변식 ②) — 응답 순서가 산출물에 새어 나오면 안 된다.

    id 중복은 여기서 재지 않는다: `_resolve_edges` 가 dict 키로 담아 **구조적으로** 불가능하고
    (`collect.py:1130`), 어떤 픽스처로도 중복을 만들 수 없다. 깨질 수 없는 어서션을 넣으면
    「통과」가 미측정을 덮는다(실패할 수 없는 어서션은 통과가 아니라 미측정).
    """
    ids = [e["id"] for e in document["edges"]]
    assert ids == sorted(ids)


def test_every_edge_endpoint_is_a_node_that_exists(document):
    ids = {n["id"] for n in document["nodes"]}
    assert document["edges"], "엣지가 0개면 이 검사는 아무것도 재지 않는다"
    dangling = [
        e["id"]
        for e in document["edges"]
        if e["source"] not in ids or e["target"] not in ids
    ]
    assert dangling == []


# --- route 집계와 미해결 참조 (ADR 0002 §4·§5) ------------------------------


def test_routes_to_the_same_target_collapse_into_one_edge(document):
    """같은 TGW 로 가는 경로 2개 → 선 1개 + 목적지 배열. 목적지마다 선을 만들면
    캔버스에 같은 두 점을 잇는 선이 겹쳐 관통선 판정이 무의미해진다."""
    routes = out_edges(document, "rtb-0111aaaa2222bbbb", "route")
    assert sorted(targets(document, "rtb-0111aaaa2222bbbb", "route")) == [
        APP_IGW,
        CORE_TGW,
        "vpce-0aaa1111bbbb7777",
    ]
    to_tgw = [e for e in routes if e["target"].endswith(f"/{CORE_TGW}")]
    assert len(to_tgw) == 1
    assert to_tgw[0]["attributes"]["destinations"] == ["10.20.0.0/16", "10.21.0.0/16"]
    # 라우트 8개 중 엣지는 3개다 — 집계가 실제로 줄였다는 뜻(대조군).
    assert attrs(document, "rtb-0111aaaa2222bbbb")["route_count"] == 8
    assert all(e["directed"] for e in routes)


def test_local_route_is_not_an_edge(document):
    """`local` 은 VPC 자기 CIDR 이다. 선으로 그리면 라우트 테이블이 VPC 를 가리키는
    없는 관계가 생긴다."""
    assert APP_VPC not in targets(document, "rtb-0111aaaa2222bbbb", "route")


def test_a_target_without_a_node_is_recorded_not_swallowed(document):
    """조용히 버리면 「이 VPC 는 아무 데도 안 붙어 있다」는 틀린 그림이 된다(ADR 0002 §5).

    `VpcLattice` 는 실계정에서 온 의사 게이트웨이 이름이다(2026-09-15 실측). 미해결
    참조를 `xxx-` 모양으로 걸러내면 이 대상이 조용히 사라진다.
    """
    assert attrs(document, "rtb-0111aaaa2222bbbb")["unresolved_refs"] == [
        "VpcLattice",
        "eni-0dead0000beef1111",
    ]
    assert "eni-0dead0000beef1111" not in targets(document, "rtb-0111aaaa2222bbbb")
    # 짝: 노드가 있는 대상은 unresolved 가 아니라 엣지가 됐다.
    assert attrs(document, "rtb-0222bbbb3333cccc").get("unresolved_refs") is None
    assert targets(document, "rtb-0222bbbb3333cccc", "route") == [EGRESS_NAT]


def test_unresolved_refs_do_not_set_partial(document):
    """`partial` 은 수집 실패용이다. 「범위 밖이라 안 봤다」와 「권한이 없어 못 봤다」를
    한 표시로 합치면 사용자가 구분할 수 없다."""
    marked = [n for n in document["nodes"] if "unresolved_refs" in n["attributes"]]
    assert {n["id"].rsplit("/", 1)[1] for n in marked} == {
        "rtb-0111aaaa2222bbbb",
        DB_SG,
        "pcx-0ccc3333dddd3333",
        # 2.7.0 — 수집하지 않은 인스턴스(i-…0004)를 대상으로 둔 ALB(ADR 0002 §8 · 지어내지 않고 남긴다)
        "app-alb",
        # 2.8.0(P2-4) — 수집하지 않은 내부 ALB 를 가리키는 사설 영역 별칭(ADR 0002 §11 · 지어내지 않고 남긴다)
        "Z0BBB2222CCCC3333",
    }
    assert [n["id"] for n in marked if n["partial"]] == []
    assert [n["id"] for n in marked if n.get("partial_reasons")] == []


# --- 죽은 상태 (ADR 0002 §3) ------------------------------------------------


@pytest.mark.parametrize(
    ("dead", "alive", "edge_type"),
    [
        ("igw-0bbb2222cccc5555", APP_IGW, "attachment"),
        ("tgw-attach-0ccc3333dddd0000", "tgw-attach-0aaa1111bbbb8888", "attachment"),
        ("pcx-0bbb2222cccc2222", "pcx-0aaa1111bbbb1111", "peering"),
        ("old-nlb", "app-alb", "load_balancer_placement"),
    ],
)
def test_dead_state_keeps_the_node_and_drops_the_edge(document, dead, alive, edge_type):
    """자원이 아직 **있는** 죽은 상태(떼어냄·삭제 중·실패)는 노드로 남아야 사람이 「고장/철거 중」을
    본다. 선을 그리면 **없는 경로를 있다고** 그린다. 살아 있는 짝이 선을 갖는지 같이 재야 「그 종류를
    아예 안 만든다」와 구분된다. 이미 **없어진** 상태(`deleted` 등)는 노드째 빠진다 —
    `test_gone_states.py`(ADR 0002 §3 개정 2026-10-14)."""
    assert node(document, dead)["attributes"]["state"] in {
        "detached",
        "deleting",
        "failed",
    }
    assert out_edges(document, dead) == []
    assert out_edges(document, alive, edge_type) != []


def test_a_dead_edge_is_not_reported_as_unresolved(document):
    """실패한 피어링의 상대 VPC 는 「못 찾은 참조」가 아니다 — 애초에 이을 경로가 없다.
    살아 있는 쪽(같은 조건의 범위 밖 상대)은 unresolved 로 남는다."""
    assert "unresolved_refs" not in attrs(document, "pcx-0bbb2222cccc2222")
    assert attrs(document, "pcx-0ccc3333dddd3333")["unresolved_refs"] == [
        "vpc-0fff6666aaaa8888"
    ]
    assert targets(document, "pcx-0ccc3333dddd3333", "peering") == [APP_VPC]


# --- 부모 (ADR 0002 §1) -----------------------------------------------------


def test_parents_follow_the_delete_together_rule(document):
    def parent(native: str) -> str:
        return node(document, native)["parent_id"]

    region_node = f"region/{ACCOUNT}/{REGION}"
    vpc_node = f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
    assert parent("rtb-0111aaaa2222bbbb") == vpc_node
    assert parent(APP_SG) == vpc_node
    assert parent("acl-0aaa1111bbbb2222") == vpc_node
    assert parent("vpce-0bbb2222cccc8888") == vpc_node
    assert parent("app-alb") == vpc_node
    # NAT 는 서브넷에 산다(AZ 가 거기서 결정된다). IGW·TGW·피어링은 VPC 밖이다.
    assert parent(EGRESS_NAT) == f"subnet/{ACCOUNT}/{REGION}/{EGRESS_SUBNET}"
    assert parent(APP_IGW) == region_node
    assert parent(CORE_TGW) == region_node
    assert parent("pcx-0aaa1111bbbb1111") == region_node


def test_the_parent_chain_falls_back_when_the_first_candidate_is_gone(document):
    """서브넷을 못 찾아 실패한 NAT 는 응답에 `SubnetId` 가 없다. 체인이 없으면 이 노드는 참조가
    끊긴 채 산출물에 들어가 스키마 검증을 깬다."""
    assert attrs(document, "nat-0fff6666aaaa7777")["state"] == "failed"
    assert (
        node(document, "nat-0fff6666aaaa7777")["parent_id"]
        == f"vpc/{ACCOUNT}/{REGION}/{EGRESS_VPC}"
    )


def test_the_internet_gateway_relation_to_a_vpc_is_an_edge_not_a_parent(document):
    """IGW 는 VPC 상자 **밖**(인터넷 칸)에 그린다 — 관계는 선으로 남긴다."""
    assert targets(document, APP_IGW, "attachment") == [APP_VPC]
    assert out_edges(document, "igw-0bbb2222cccc5555") == []


# --- 보안 그룹 (ADR 0002 §6) ------------------------------------------------


def test_security_group_self_reference_is_an_attribute_not_a_self_loop(document):
    """자기 루프는 캔버스의 선 규칙(왼→오 흐름)에 정의가 없다."""
    assert attrs(document, APP_SG)["self_referencing"] is True
    assert attrs(document, DB_SG)["self_referencing"] is False
    assert [e["id"] for e in document["edges"] if e["source"] == e["target"]] == []


def test_security_group_edges_carry_direction_and_rules(document):
    """방향과 규칙을 잃으면 「443 만 허용」과 「전부 허용」이 같은 선이 된다."""
    seen = {
        (e["attributes"]["direction"], tuple(e["attributes"]["rules"]))
        for e in out_edges(document, APP_SG, "security_group_reference")
    }
    assert seen == {("ingress", ("tcp/443-443",)), ("egress", ("all",))}
    assert targets(document, DB_SG, "security_group_reference") == [APP_SG]
    assert attrs(document, DB_SG)["unresolved_refs"] == ["sg-0ddd4444eeee4444"]


# --- 엔드포인트·LB 배치 ----------------------------------------------------


def test_only_interface_endpoints_are_placed_in_subnets(document):
    """게이트웨이 엔드포인트는 서브넷에 ENI 를 만들지 않는다 — 라우트 테이블이 가리킨다."""
    assert sorted(
        targets(document, "vpce-0bbb2222cccc8888", "endpoint_connection")
    ) == [
        PUBLIC_SUBNET,
        PRIVATE_SUBNET,
    ]
    assert out_edges(document, "vpce-0aaa1111bbbb7777", "endpoint_connection") == []
    # 짝: 게이트웨이 엔드포인트는 라우트 엣지의 **대상**으로 등장한다.
    assert "vpce-0aaa1111bbbb7777" in targets(document, "rtb-0111aaaa2222bbbb", "route")


def test_load_balancer_placement_points_at_subnets(document):
    assert sorted(targets(document, "app-alb", "load_balancer_placement")) == [
        PUBLIC_SUBNET,
        PRIVATE_SUBNET,
    ]


def test_associations_come_from_route_tables_and_nacls(document):
    """서브넷 연관은 두 종류 모두에서 나온다 — 한쪽만 만들면 서브넷의 절반이 고아가 된다."""
    sources = {
        e["source"].rsplit("/", 1)[1]
        for e in document["edges"]
        if e["type"] == "association"
    }
    assert sources == {
        "rtb-0111aaaa2222bbbb",
        "rtb-0222bbbb3333cccc",
        "acl-0aaa1111bbbb2222",
    }
    # main 연관은 서브넷이 없다 → 엣지가 아니라 속성이다.
    assert attrs(document, "rtb-0111aaaa2222bbbb")["main"] is True
    assert targets(document, "rtb-0111aaaa2222bbbb", "association") == [PUBLIC_SUBNET]


# --- 공유 자원의 정본 계정 (ADR 0002 §2) -----------------------------------


def test_a_shared_resource_becomes_one_node_owned_by_its_owner():
    """RAM 공유 TGW 는 두 계정의 응답에 동시에 나온다. 호출한 계정으로 귀속시키면
    자원 하나가 캔버스에 두 레인으로 그려진다."""
    document = run(
        scope_of(ACCOUNT, PARTNER),
        factory_for(
            {
                ACCOUNT: FakeAws(),
                # 소유 계정은 TGW 만 보고한다(자기 VPC 는 이 시나리오에 없다).
                PARTNER: FakeAws.empty(transit_gateways=TRANSIT_GATEWAYS),
            }
        ),
    )
    tgws = [n for n in document["nodes"] if n["type"] == "transit_gateway"]
    assert [n["id"] for n in tgws] == [f"transit_gateway/{PARTNER}/{REGION}/{CORE_TGW}"]
    assert tgws[0]["account_id"] == PARTNER
    # 소유자가 범위 안이면 별도 표기가 필요 없다.
    assert "owner_account_id" not in tgws[0]["attributes"]
    # 🔴 다른 계정의 라우트가 이 노드로 이어진다 — 계정 경계를 넘는 엣지의 첫 실증.
    assert f"transit_gateway/{PARTNER}/{REGION}/{CORE_TGW}" in [
        e["target"] for e in out_edges(document, "rtb-0111aaaa2222bbbb", "route")
    ]


def test_an_out_of_scope_owner_does_not_get_a_lane(document):
    """조사하지 않은 계정을 레인으로 만들면 `coverage` 가 빈 계정이 그림에 등장한다 —
    「없는데 있다고 그린다」."""
    tgw = node(document, CORE_TGW)
    assert tgw["account_id"] == ACCOUNT
    assert tgw["attributes"]["owner_account_id"] == PARTNER
    accounts = {n["id"] for n in document["nodes"] if n["type"] == "account"}
    assert accounts == {f"account/{ACCOUNT}"}
    assert [
        n["id"] for n in document["nodes"] if PARTNER in n["id"] or OUTSIDER in n["id"]
    ] == []


def coverage_of(document: dict) -> dict[tuple[str, str, str], dict]:
    return {
        (c["account_id"], c["region"], c["resource_type"]): c
        for c in document["coverage"]
    }


def assert_nodes_stay_inside_coverage(document: dict) -> None:
    """파생 불변식(ADR 0002 §2-1): 모든 노드는 자기 (계정, 리전) 조합의 coverage 행이 있고
    그 타입의 `item_count` 는 같은 조합의 노드 수 이상이다.

    역(coverage 수 == 노드 수)은 불변식이 **아니다** — coverage 는 API 응답 개수(계정·리전 간
    중복 포함)이고 노드는 dedupe 된 결과다. 같다고 잡으면 §2·§2-1 의 dedupe 를 금지하게 된다.

    글로벌 리소스(`region: null`, ADR 0010)는 리전 레인에 속하지 않으므로 「그 계정에서 그
    종류를 **어느 리전에서든** 조회했고, 그중 한 호출의 개수가 노드 수 이상」으로 잰다.
    """
    counted: dict[tuple[str, str | None, str], int] = {}
    from nettopo.model import DERIVED_TYPES

    for n in document["nodes"]:
        # 파생 노드(external_ip · s3_bucket — 2.7.0·2.8.0)는 목록 API 가 없다 = coverage 행이 없는 것이 맞다
        if n["type"] in {"account", "region", *DERIVED_TYPES}:
            continue
        key = (n["account_id"], n["region"], n["type"])
        counted[key] = counted.get(key, 0) + 1
    coverage = coverage_of(document)
    assert counted, "리소스 노드가 0개면 이 검사는 아무것도 재지 않는다"

    def rows_for(a: str, r: str | None, t: str) -> list[dict]:
        if r is not None:
            return [coverage[(a, r, t)]] if (a, r, t) in coverage else []
        return [c for (ca, _, ct), c in coverage.items() if (ca, ct) == (a, t)]

    orphans = [f"{a}/{r}/{t}" for (a, r, t) in counted if not rows_for(a, r, t)]
    assert orphans == [], "조회하지 않은 (계정, 리전) 조합에 노드가 놓였다"
    over = [
        f"{a}/{r}/{t}: coverage {[c['item_count'] for c in rows_for(a, r, t)]} < 노드 {count}"
        for (a, r, t), count in counted.items()
        if max((c["item_count"] or 0) for c in rows_for(a, r, t)) < count
    ]
    assert over == []


def test_no_node_lands_outside_the_coverage_we_recorded(document):
    assert_nodes_stay_inside_coverage(document)


@pytest.mark.parametrize(
    ("requester_owner", "requester_region", "why"),
    [
        (ACCOUNT, "eu-west-1", "요청자 리전이 scope 밖"),
        (OUTSIDER, SEOUL, "요청자 계정이 정본 계정과 다르다(실측 2026-09-15)"),
    ],
)
def test_the_canonical_region_stays_inside_what_we_queried(
    requester_owner, requester_region, why
):
    """🔴 실데이터가 잡은 결함(ADR 0002 §2-1). requester=999900001111/ap-northeast-2,
    accepter=111122223333/us-east-1 인 피어링은 accepter 계정 세션의 us-east-1 응답에만 나온다 —
    요청자 리전을 무조건 정본으로 쓰면 노드가 「끝점이 하나도 없는 좌표」에 놓이고
    그 리전의 coverage 는 0 이다."""
    peering = {
        "VpcPeeringConnectionId": "pcx-0eee5555ffff5555",
        "RequesterVpcInfo": {
            "VpcId": "vpc-0fff6666aaaa9999",
            "OwnerId": requester_owner,
            "Region": requester_region,
        },
        "AccepterVpcInfo": {"VpcId": APP_VPC, "OwnerId": ACCOUNT, "Region": REGION},
        "Status": {"Code": "active"},
        "Tags": [],
    }
    document = run(
        scope_of(ACCOUNT, regions=(REGION, SEOUL)),
        # 🔴 한쪽 리전 응답에만 나온다 — 이것이 실제 모습이다(상대 VPC 를 우리 계정이 못 본다).
        factory_by_region(
            {
                REGION: FakeAws.empty(vpcs=VPCS, peerings=[peering]),
                SEOUL: FakeAws.empty(),
            }
        ),
    )
    assert [
        n["id"] for n in document["nodes"] if n["type"] == "vpc_peering_connection"
    ] == [f"vpc_peering_connection/{ACCOUNT}/{REGION}/pcx-0eee5555ffff5555"], why
    # 리전 사실은 속성에 남아 잃지 않는다.
    assert (
        attrs(document, "pcx-0eee5555ffff5555")["requester_region"] == requester_region
    )
    assert_nodes_stay_inside_coverage(document)


# --- TGW 피어링 어태치먼트: 한 자원, 두 쪽 뷰 (ADR 0002 §2-2) --------------------

PEER_ATTACH = "tgw-attach-0abc1111def12222"
REMOTE_TGW = "tgw-0aaa2222bbbb3333"


def _peering_attachment(
    *, local: str, local_owner: str, peer: str, peer_owner: str, kind: str = "peering"
):
    """한 쪽 리전 응답에 나오는 피어링 어태치먼트 한 뷰.

    🔴 `ResourceId` 가 **상대 TGW** 이고 `ResourceOwnerId` 는 그 상대의 계정이다 — 응답에
    상대 «리전» 은 없다. 두 뷰가 같은 (계정, 리전) 을 계산할 수 없는 이유가 이 한 줄이다.
    """
    return {
        "TransitGatewayAttachmentId": PEER_ATTACH,
        "TransitGatewayId": local,
        "TransitGatewayOwnerId": local_owner,
        "ResourceOwnerId": peer_owner,
        "ResourceType": kind,
        "ResourceId": peer,
        "State": "available",
        "Tags": [],
    }


def _tgw(tgw_id: str, owner: str, region: str):
    return {
        "TransitGatewayId": tgw_id,
        "TransitGatewayArn": f"arn:aws:ec2:{region}:{owner}:transit-gateway/{tgw_id}",
        "OwnerId": owner,
        "State": "available",
        "Options": {},
        "Tags": [],
    }


# 🔴 `tgw-peering` 은 deprecated 인데 botocore enum 에 남아 있다 — 같은 피어링으로 다뤄야 한다(#31 리뷰).
PEERING_KINDS = pytest.mark.parametrize("kind", ["peering", "tgw-peering"])


@PEERING_KINDS
def test_a_peering_attachment_sits_in_its_own_transit_gateways_lane(kind):
    """🔴 실계정이 잡은 결함(2026-09-26). 사람이 겪는 일: 두 리전을 잇는 TGW 피어링 카드가
    **그 TGW 가 없는 계정 레인**에 떠 있었다. 그 레인의 「읽은 개수」는 0 이다.

    원인은 한 줄이다 — 피어링의 `ResourceId` 는 붙은 자원이 아니라 상대 TGW 이므로
    `ResourceOwnerId`(=상대 계정)로 귀속시키면 자기 TGW 와 다른 레인에 놓인다(§2-2).
    """
    document = run(
        scope_of(ACCOUNT, PARTNER, regions=(REGION,)),
        factory_by_pair(
            {
                (ACCOUNT, REGION): FakeAws.empty(
                    transit_gateways=[_tgw(CORE_TGW, ACCOUNT, REGION)],
                    transit_gateway_attachments=[
                        _peering_attachment(
                            local=CORE_TGW,
                            local_owner=ACCOUNT,
                            peer=REMOTE_TGW,
                            peer_owner=PARTNER,
                            kind=kind,
                        )
                    ],
                ),
                (PARTNER, REGION): FakeAws.empty(),
            }
        ),
    )
    assert node(document, PEER_ATTACH)["id"] == (
        f"transit_gateway_attachment/{ACCOUNT}/{REGION}/{PEER_ATTACH}"
    )
    # 귀속에 쓰지 않은 상대 계정은 사실로 남는다 — 잃지 않는다.
    assert attrs(document, PEER_ATTACH)["peer_account_id"] == PARTNER
    # 같은 레인의 TGW 로 선이 간다(레인 밖으로 새지 않는다).
    assert targets(document, PEER_ATTACH, "attachment") == [CORE_TGW]
    assert_nodes_stay_inside_coverage(document)


def _two_peering_views(kind: str = "peering") -> dict[tuple[str, str], FakeAws]:
    """한 피어링 어태치먼트를 **두 쌍**(A/REGION · B/SEOUL)에서 본 응답 — 병합 검사와 지표 검사가 같이 쓴다."""
    return {
        # 🔴 두 쌍에만 나온다 — A 의 SEOUL·B 의 REGION 응답에는 없다(그 계정은 그 리전에
        #    이 어태치먼트의 끝점을 갖고 있지 않다).
        (ACCOUNT, REGION): FakeAws.empty(
            transit_gateways=[_tgw(CORE_TGW, ACCOUNT, REGION)],
            transit_gateway_attachments=[
                _peering_attachment(
                    local=CORE_TGW,
                    local_owner=ACCOUNT,
                    peer=REMOTE_TGW,
                    peer_owner=PARTNER,
                    kind=kind,
                )
            ],
        ),
        (PARTNER, SEOUL): FakeAws.empty(
            transit_gateways=[_tgw(REMOTE_TGW, PARTNER, SEOUL)],
            transit_gateway_attachments=[
                dict(
                    _peering_attachment(
                        local=REMOTE_TGW,
                        local_owner=PARTNER,
                        peer=CORE_TGW,
                        peer_owner=ACCOUNT,
                        kind=kind,
                    ),
                    Tags=[{"Key": "Name", "Value": "peer-link"}],
                )
            ],
        ),
        (ACCOUNT, SEOUL): FakeAws.empty(),
        (PARTNER, REGION): FakeAws.empty(),
    }


@PEERING_KINDS
def test_two_views_of_one_peering_attachment_become_one_node_with_both_wires(kind):
    """🔴 한 자원을 두 쪽에서 봤을 때 **카드 하나 · 선 둘**이어야 한다(§2-2).

    사람이 겪는 일: 같은 어태치먼트 ID 를 가진 카드가 두 레인에 두 장 그려지고(그림과 표의
    개수가 어긋난다), 「먼저 만난 것만 남기던」 방식으로 고치면 반대편 TGW 로 가는 선 한 개가
    조용히 사라진다. 실계정 하네스가 그 둘을 각각 R2 「중복 2」· R4 「사라진 선 1」로 잡았다.
    """
    clients = _two_peering_views(kind)
    document = run(
        scope_of(ACCOUNT, PARTNER, regions=(REGION, SEOUL)), factory_by_pair(clients)
    )
    found = [n for n in document["nodes"] if n["native_id"] == PEER_ATTACH]
    assert [n["id"] for n in found] == [
        # 승자는 (계정, 리전) 최소 — scope 순서를 바꿔도 카드가 레인을 옮기지 않는다.
        f"transit_gateway_attachment/{ACCOUNT}/{REGION}/{PEER_ATTACH}"
    ]
    # 🔴 **선은 둘 다 살아 있다** — 하나는 같은 레인의 TGW, 하나는 다른 계정·리전의 TGW.
    assert sorted(targets(document, PEER_ATTACH, "attachment")) == sorted(
        [CORE_TGW, REMOTE_TGW]
    )
    merged = found[0]
    assert merged["attributes"]["merged_from"] == [f"{PARTNER}/{SEOUL}"]
    # 🔴 이름은 상대 계정(PARTNER) 쪽에만 붙어 있었다 — 승자(ACCOUNT) 계정 콘솔은 「–」이므로
    #    우리도 이름 없음이다(사용자 결정 2026-10-25 · 콘솔과 같게). 상대 쪽 Name 태그도 빌려 오지 않는다.
    assert merged["name"] is None
    assert "Name" not in (merged.get("tags") or {})
    assert_nodes_stay_inside_coverage(document)

    # 🔴 **scope 순서를 뒤집어도 같은 레인에 앉는다.** 이 두 번째 실행이 없으면 「먼저 만난 것을
    #    남긴다」로 고쳐도 위 어서션이 전부 통과한다(우연히 첫 뷰가 최소값이다) — 통과가 아니라
    #    미측정이다. 같은 AWS 상태를 보는 사람이 계정 목록 순서만 바꿨는데 카드가 다른 계정
    #    레인으로 옮겨가면 「이 어태치먼트는 누구 것인가」가 실행마다 달라진다.
    flipped = run(
        scope_of(PARTNER, ACCOUNT, regions=(SEOUL, REGION)), factory_by_pair(clients)
    )
    assert node(flipped, PEER_ATTACH)["id"] == merged["id"]
    assert sorted(targets(flipped, PEER_ATTACH, "attachment")) == sorted(
        [CORE_TGW, REMOTE_TGW]
    )


def test_a_merged_peering_attachment_keeps_only_the_winner_accounts_name():
    """병합된 피어링 어태치먼트의 이름 = 승자 계정 쪽 Name 태그(사용자 결정 2026-10-25 · 콘솔과 같게).

    사람이 겪는 일: 승자 계정 콘솔에서 이 어태치먼트 이름은 「core-side」인데, 상대 계정 이름이
    섞여 들어오면 화면과 콘솔이 다른 이름을 말한다. 승자 쪽 이름이 있으면 그것만 쓴다.
    """
    clients = _two_peering_views()
    clients[(ACCOUNT, REGION)] = FakeAws.empty(
        transit_gateways=[_tgw(CORE_TGW, ACCOUNT, REGION)],
        transit_gateway_attachments=[
            dict(
                _peering_attachment(
                    local=CORE_TGW,
                    local_owner=ACCOUNT,
                    peer=REMOTE_TGW,
                    peer_owner=PARTNER,
                ),
                Tags=[{"Key": "Name", "Value": "core-side"}],
            )
        ],
    )
    document = run(
        scope_of(ACCOUNT, PARTNER, regions=(REGION, SEOUL)), factory_by_pair(clients)
    )
    merged = node(document, PEER_ATTACH)
    assert merged["name"] == "core-side"
    assert (merged.get("tags") or {}).get("Name") == "core-side"


def test_merged_from_names_only_other_coordinates_once():
    """`merged_from` 은 「이 자원을 본 **다른** (계정, 리전)」이다(#31 리뷰).

    같은 (계정, 리전) 뷰가 둘 들어오면(한 응답에 같은 항목이 두 번) 승자 자기 좌표가 끼어
    「이 카드는 자기 자신과 병합됐다」로 읽힌다. 승자 좌표는 빼고, 겹치는 좌표는 한 번만 적는다.
    """
    clients = _two_peering_views()
    clients[(ACCOUNT, REGION)] = FakeAws.empty(
        transit_gateways=[_tgw(CORE_TGW, ACCOUNT, REGION)],
        transit_gateway_attachments=[
            _peering_attachment(
                local=CORE_TGW, local_owner=ACCOUNT, peer=REMOTE_TGW, peer_owner=PARTNER
            )
        ]
        * 2,
    )
    document = run(
        scope_of(ACCOUNT, PARTNER, regions=(REGION, SEOUL)), factory_by_pair(clients)
    )
    assert attrs(document, PEER_ATTACH)["merged_from"] == [f"{PARTNER}/{SEOUL}"]


# 지표 창의 끝. 구조 수집 시각과 같게 둔다(가짜 CloudWatch 는 이 끝에서 거꾸로 지점을 만든다).
PEER_AT = FIXED_CLOCK


def _merged_peering() -> Any:
    return collect(
        scope_of(ACCOUNT, PARTNER, regions=(REGION, SEOUL)),
        factory_by_pair(_two_peering_views()),
        clock=lambda: PEER_AT,
    )


def _peering_wires(snapshot: Any) -> dict[str, str]:
    """도착 TGW 의 native id → 그 피어링 선의 id."""
    nodes = {n.id: n for n in snapshot.nodes}
    return {
        nodes[e.target].native_id: e.id
        for e in snapshot.edges
        if e.type == "attachment" and nodes[e.source].native_id == PEER_ATTACH
    }


def _peer_flow(tgw: str) -> dict:
    """그 TGW 쪽에서 본 피어링 어태치먼트의 바이트(양방향). 나머지 계열은 빈 배열이 온다."""
    dims = {"TransitGateway": tgw, "TransitGatewayAttachment": PEER_ATTACH}
    return {
        series_key("AWS/TransitGateway", name, dims): [4096.0, 0.0, 2048.0]
        for name in ("BytesIn", "BytesOut")
    }


def _peering_observation(watches: dict[tuple[str, str], Any]) -> dict[str, Any]:
    """생산과 같은 순서(구조 → 2층 → 판정). `watches` = (계정, 리전) → 가짜 CloudWatch 또는 예외."""
    clients = _two_peering_views()

    def make(account_id: str, region: str, service: str) -> Any:
        if service != "cloudwatch":
            return clients[(account_id, region)]
        watch = watches[(account_id, region)]
        if isinstance(watch, Exception):
            raise watch
        return watch

    snapshot = _merged_peering()
    document, _dropped = build_observation(
        snapshot,
        topology=snapshot.to_document(),
        observed_at=PEER_AT,
        tool_version="test",
        metrics=read_metrics(snapshot, make, at=PEER_AT),
    )
    return document


def test_each_side_of_a_merged_peering_is_measured_on_its_own_transit_gateway():
    """🔴 #31 리뷰 ①(2026-09-28). 병합이 상대편 선의 **출발**을 우리 쪽 카드로 옮긴 뒤, 지표 계획이
    그 선의 자리·TGW 를 출발 카드에서 뽑아 **두 선이 같은 질의**(우리 쪽 TGW)를 받았다.

    사람이 겪는 일: 두 리전을 잇는 TGW 피어링에서 상대편 구간 선이 우리 쪽에서 잰 값으로 초록
    「직접 측정」이 되고, 상대편 TGW 가 버린 패킷은 그 선에 영영 안 뜬다.
    """
    snapshot = _merged_peering()
    wires = _peering_wires(snapshot)
    queries, _ = plan(snapshot)
    asked = {q.target_id: q for q in queries}
    got = {
        tgw: (
            asked[wire].account_id,
            asked[wire].region,
            asked[wire].values,
            asked[wire].observed_on,
        )
        for tgw, wire in wires.items()
    }
    assert got == {
        CORE_TGW: (ACCOUNT, REGION, (CORE_TGW, PEER_ATTACH), None),
        REMOTE_TGW: (PARTNER, SEOUL, (REMOTE_TGW, PEER_ATTACH), None),
    }


def test_our_sides_flow_does_not_paint_the_far_side_of_a_peering():
    """리뷰 ① 의 화면 쪽 — 우리 쪽 TGW 에서만 흐르면 **상대편 선은 흐른다고 말하지 않는다**."""
    document = _peering_observation(
        {
            (ACCOUNT, REGION): FakeCloudWatch(_peer_flow(CORE_TGW)),
            (PARTNER, SEOUL): FakeCloudWatch({}),
        }
    )
    wires = _peering_wires(_merged_peering())
    status = {o["target_id"]: o["status"] for o in document["observations"]}
    assert status.get(wires[CORE_TGW]) == "live"
    assert status.get(wires[REMOTE_TGW]) != "live"


def test_a_denied_far_side_is_reported_where_it_was_asked():
    """🔴 자리를 **계획과 판정이 같이** 옮겨야 한다. 계획만 옮기면 상대편 실패가 상대편 칸에 적히는데
    판정은 우리 쪽 칸에서 찾는다 → 「응답이 빠졌다」는 **개발 결함 행**이 거짓으로 생긴다.

    사람이 겪는 일: 상대 계정이 지표 읽기를 거절했는데, 화면은 권한 문제가 아니라 도구 결함이라고
    말한다(고칠 사람을 잘못 부른다).
    """
    denied = client_error("AccessDenied", "GetMetricData")
    document = _peering_observation(
        {
            (ACCOUNT, REGION): FakeCloudWatch(_peer_flow(CORE_TGW)),
            (PARTNER, SEOUL): denied,
        }
    )
    wires = _peering_wires(_merged_peering())
    rows = {o["target_id"]: o for o in document["observations"]}
    assert rows[wires[CORE_TGW]]["status"] == "live"
    # 못 잰 구간은 행이 없다(D1 의 E) — 사유는 그 칸의 2층 coverage 에 있다.
    assert wires[REMOTE_TGW] not in rows
    cells = {
        (c["account_id"], c["region"], c["resource_type"], c["layer"]): c["status"]
        for c in document["coverage"]
    }
    assert cells[(PARTNER, SEOUL, "transit_gateway_attachment", "metric")] == "denied"
    assert cells[(ACCOUNT, REGION, "transit_gateway_attachment", "metric")] == "ok"


def test_an_edge_can_cross_regions():
    """리전 간 피어링은 한쪽 리전 응답에만 나온다. 참조를 리전 안에서만 찾으면
    두 리전을 잇는 선이 영원히 안 그려진다."""
    remote_vpc = {
        "VpcId": "vpc-0eee5555ffff1111",
        "CidrBlock": "10.30.0.0/16",
        "State": "available",
        "IsDefault": False,
        "Tags": [],
    }
    cross_region_peering = {
        "VpcPeeringConnectionId": "pcx-0ddd4444eeee4444",
        "RequesterVpcInfo": {"VpcId": APP_VPC, "OwnerId": ACCOUNT, "Region": REGION},
        "AccepterVpcInfo": {
            "VpcId": remote_vpc["VpcId"],
            "OwnerId": ACCOUNT,
            "Region": SEOUL,
        },
        "Status": {"Code": "active"},
        "Tags": [],
    }
    document = run(
        scope_of(ACCOUNT, regions=(REGION, SEOUL)),
        factory_by_region(
            {
                REGION: FakeAws.empty(vpcs=VPCS, peerings=[cross_region_peering]),
                SEOUL: FakeAws.empty(vpcs=[remote_vpc]),
            }
        ),
    )

    assert sorted(
        e["target"] for e in out_edges(document, "pcx-0ddd4444eeee4444", "peering")
    ) == [
        f"vpc/{ACCOUNT}/{SEOUL}/{remote_vpc['VpcId']}",
        f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}",
    ]
