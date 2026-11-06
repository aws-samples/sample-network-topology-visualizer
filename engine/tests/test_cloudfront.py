"""CloudFront(글로벌 2번째) — **중첩 result_key** · 글로벌 좌표 · WAF 는 속성.

DXGW 가 「어느 리전에서 봐도 노드 하나」를 이미 쟀다. 여기서 새로 재는 것은 셋이다:

1. 🔴 **중첩 `result_key`** — 항목이 `DistributionList.Items` 에 있다. 평평한 키를 가정하는
   구현은 **예외 없이 0건**을 준다 → 「배포가 하나도 없다」로 그려진다(불변식 ④가 막으려는
   조용한 빈 그래프). 가짜도 **실제로 중첩된** 페이지를 준다(`fakes._nest`) — 가짜가 평평하면
   이 축은 미측정이다.
2. 🔴 **배포 상태를 구성에 섞지 않는가** — 응답에 `Status`·`LastModifiedTime`·`ETag` 를
   일부러 넣어 두고 산출물에 새는지 잰다. `Enabled`(사람이 정한 설정)는 **담는 쪽**이다.
3. 🔵 **WAF 는 노드가 아니라 `web_acl_id` 한 줄** — 스키마에 `waf` 타입이 없는 것까지 잰다.

각 검사는 살아 있는 짝을 같이 잰다 — 「없다」만 재면 속성을 아예 만들지 않는 구현도 통과한다.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fakes import (
    ACCOUNT,
    CLOUDFRONT_DISABLED,
    CLOUDFRONT_DISTRIBUTION,
    CLOUDFRONT_DISTRIBUTIONS,
    CLOUDFRONT_STATE_LEAKS,
    LOAD_BALANCERS,
    PARTNER,
    REGION,
    FakeAws,
    client_error,
    factory,
    factory_for,
)
from jsonschema import Draft202012Validator
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.serialize import to_json
from nettopo.services.base import dig

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json"
FIXED_CLOCK = "2026-01-02T03:04:05Z"
SEOUL = "ap-northeast-2"

DIST_ID = f"cloudfront/{ACCOUNT}/global/{CLOUDFRONT_DISTRIBUTION}"
DISABLED_ID = f"cloudfront/{ACCOUNT}/global/{CLOUDFRONT_DISABLED}"
ALB_DNS = f"app-alb-1234567890.{REGION}.elb.amazonaws.com"


def run(client: FakeAws, regions: tuple[str, ...] = (REGION,)) -> dict:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=regions, resource_types=SUPPORTED_RESOURCE_TYPES
    )
    return collect(scope, factory(client), clock=lambda: FIXED_CLOCK).to_document()


def node_of(document: dict, node_id: str) -> dict:
    found = [n for n in document["nodes"] if n["id"] == node_id]
    assert len(found) == 1, f"{node_id} 노드가 {len(found)} 개다"
    return found[0]


def coverage_of(document: dict, resource_type: str) -> list[dict]:
    return [c for c in document["coverage"] if c["resource_type"] == resource_type]


@pytest.fixture(scope="module")
def document() -> dict:
    return run(FakeAws())


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ 계약


def test_cloudfront_is_in_the_contract_and_the_document(document, validator):
    """스키마 양쪽 enum + `SUPPORTED_RESOURCE_TYPES` + 실제 수집 — 한쪽만 늘리면 여기서 깨진다."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "cloudfront" in schema["$defs"]["node_type"]["enum"]
    assert "cloudfront" in schema["$defs"]["resource_type"]["enum"]
    assert "cloudfront" in SUPPORTED_RESOURCE_TYPES
    assert "cloudfront" in {n["type"] for n in document["nodes"]}
    assert [
        f"{e.json_path}: {e.message}" for e in validator.iter_errors(document)
    ] == []


# ------------------------------------------------------------------ 중첩 result_key


def test_nested_result_key_reads_every_distribution(document):
    """🔴 항목은 `DistributionList.Items` 에 있다 — 평평한 키면 **조용히 0건**이다.

    개수와 **구체적인 id 둘 다** 어서션한다: 개수만 보면 두 장 중 한 장만 읽는 구현이
    통과할 수 있고, id 만 보면 중복이 통과한다.
    """
    dists = [n for n in document["nodes"] if n["type"] == "cloudfront"]
    assert sorted(n["id"] for n in dists) == sorted([DISABLED_ID, DIST_ID])
    cov = coverage_of(document, "cloudfront")
    assert [c["status"] for c in cov] == ["ok"]
    assert [c["item_count"] for c in cov] == [2]


def test_dig_walks_a_dotted_path_and_a_flat_one():
    """`dig()` 의 단위 축 — 평평한 키는 조각이 하나인 경로다(같은 코드가 처리한다)."""
    assert dig({"DistributionList": {"Items": [1, 2]}}, "DistributionList.Items") == [
        1,
        2,
    ]
    assert dig({"Firewalls": [1]}, "Firewalls") == [1]


@pytest.mark.parametrize(
    ("page", "why"),
    [
        ({}, "키가 아예 없다"),
        ({"DistributionList": {}}, "마지막 조각이 없다"),
        ({"DistributionList": {"Items": None}}, "값이 null 이다"),
        ({"DistributionList": []}, "중간이 dict 가 아니다"),
        ({"DistributionList": {"Items": {"a": 1}}}, "목록이 아니다"),
    ],
)
def test_dig_returns_empty_instead_of_raising(page: dict, why: str):
    """🔴 못 읽은 것을 여기서 예외로 만들지 않는다 — 「못 읽음」과 「없음」의 구분은
    호출한 쪽의 `classify` 와 coverage 가 한다. 여기서 터지면 한 계정이 전체를 죽인다."""
    assert dig(page, "DistributionList.Items") == [], why


# ------------------------------------------------------------------ 글로벌 좌표


def test_cloudfront_is_one_node_across_regions_under_the_account():
    """DXGW 와 같은 규약 — id 리전 자리는 `global`, `region` 은 null, 부모는 계정 노드.

    조회 리전을 id 에 넣으면 배포 하나가 리전 수만큼 늘어난다. coverage 는 호출이 일어난
    리전마다 남는다(각 호출의 성공·실패는 다른 사실이다).
    """
    document = run(FakeAws(), regions=(REGION, SEOUL))
    dists = [n for n in document["nodes"] if n["type"] == "cloudfront"]
    assert sorted(n["id"] for n in dists) == sorted([DISABLED_ID, DIST_ID])
    for n in dists:
        assert n["region"] is None
        assert n["parent_id"] == f"account/{ACCOUNT}"
        assert "/global/" in n["id"]
    cov = coverage_of(document, "cloudfront")
    assert sorted(c["region"] for c in cov) == sorted([REGION, SEOUL])
    assert all(c["status"] == "ok" and c["item_count"] == 2 for c in cov)


# ------------------------------------------------------------------ 구성만 싣는다


def test_cloudfront_carries_config_and_not_deployment_state(document):
    """속성 집합을 못박는다 — `Status`·`LastModifiedTime`·`ETag` 가 새어 들어오면 FAIL."""
    node = node_of(document, DIST_ID)
    assert node["region"] is None
    assert node["name"] == "d1aaaaaaaaaaaa.cloudfront.net"
    attrs = node["attributes"]
    assert set(attrs) == {
        "domain_name",
        "aliases",
        "origin_domain_names",
        "price_class",
        "enabled",
        "is_ipv6_enabled",
        "http_version",
        "web_acl_id",
        # 콘솔 표의 재료(사용자 결정 2026-10-24) — 설명 · 원본 표 · 동작 표
        "comment",
        "origins",
        "behaviors",
    }
    # 살아 있는 짝 — 「설정은 담는다」
    assert attrs["enabled"] is True
    assert attrs["price_class"] == "PriceClass_All"
    assert attrs["is_ipv6_enabled"] is True
    # 대체 도메인·오리진은 정렬돼 나온다(픽스처는 일부러 역순으로 줬다)
    assert attrs["aliases"] == ["example.com", "www.example.com"]
    assert attrs["origin_domain_names"] == sorted([ALB_DNS, "assets.s3.amazonaws.com"])


def test_deployment_state_never_reaches_the_snapshot():
    """🔴 전문 검사 — 속성 집합 어서션은 **그 노드의 키**만 본다. 상태 문자열이 태그·이름·다른
    노드로 흘러도 통과한다. 그래서 산출물 전체 바이트에서 찾는다(DX 의 비밀 검사와 같은 방식).
    """
    blob = to_json(
        collect(
            Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES),
            factory(FakeAws()),
            clock=lambda: FIXED_CLOCK,
        )
    )
    for leak in CLOUDFRONT_STATE_LEAKS:
        assert leak not in blob, f"배포 상태/시각이 산출물에 샜다: {leak}"
    # 대조군 — 같은 바이트에 **담아야 하는 것**은 있다. 없으면 위 검사는 빈 문서도 통과한다.
    assert "d1aaaaaaaaaaaa.cloudfront.net" in blob


def test_an_unattached_web_acl_is_none_not_an_empty_string(document):
    """AWS 는 붙지 않은 배포에 **빈 문자열**을 준다. 두 가지 「없다」를 만들지 않는다(불변식 ②)."""
    assert node_of(document, DISABLED_ID)["attributes"]["web_acl_id"] is None
    # 살아 있는 짝 — 붙은 쪽은 값이 온다
    assert node_of(document, DIST_ID)["attributes"]["web_acl_id"].startswith(
        "arn:aws:wafv2:"
    )


def test_waf_is_an_attribute_not_a_resource_type():
    """🔵 WAF 를 종류로 만들지 않는다 — 렌더러도 `web ACL` 을 카드의 한 줄로 그린다."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "waf" not in schema["$defs"]["node_type"]["enum"]
    assert "waf" not in SUPPORTED_RESOURCE_TYPES


# ------------------------------------------------------------------ 엣지·부분 실패


ALB_ID = f"load_balancer/{ACCOUNT}/{REGION}/app-alb"


def origin_edges(document: dict, source: str = DIST_ID) -> list[dict]:
    """LB 로 가는 원본 선(#43) — S3 원본(2.8.0 · `origins.py` 의 파생 노드)은 `s3_origin_edges` 가 잰다."""
    return [
        e
        for e in document["edges"]
        if e["type"] == "origin"
        and e["source"] == source
        and not e["target"].startswith("s3_bucket/")
    ]


def with_origins(*domains: str) -> list[dict]:
    """오리진만 바꾼 배포 하나 — 나머지 필드는 픽스처 그대로다."""
    dist = copy.deepcopy(CLOUDFRONT_DISTRIBUTIONS[1])
    dist["Origins"] = {
        "Quantity": len(domains),
        "Items": [{"Id": f"o{i}", "DomainName": d} for i, d in enumerate(domains)],
    }
    return [dist]


def test_an_alb_origin_becomes_one_directed_edge(document):
    """🔵 오리진 DNS 가 수집한 LB 의 `dns_name` 과 맞물리면 배포 → LB 선 하나(#43).

    🔴 노드 존재를 **먼저** 잰다 — 수집기가 노드를 안 만들면 빈 리스트끼리 비교해 통과한다(§4 미측정).
    """
    node = node_of(document, DIST_ID)
    assert ALB_DNS in node["attributes"]["origin_domain_names"]
    edges = origin_edges(document)
    assert [(e["target"], e["directed"]) for e in edges] == [(ALB_ID, True)]
    # 같은 배포의 S3 오리진은 선도 해소 실패도 만들지 않는다
    assert "unresolved_refs" not in node["attributes"]
    # LB 오리진이 없는 배포는 선이 없다
    assert not origin_edges(document, DISABLED_ID)


def test_only_lb_shaped_origins_are_joined():
    """🔴 LB 주소 꼴만 잇는다 — LB 의 이름(`native_id`)과 같은 사용자 도메인이어도 선도 해소 실패도 없다.

    엔진은 DNS 를 풀지 않으니 사용자 도메인이 LB 를 가리키는지 모른다. 해소 실패로 적으면 화면이
    「수집하지 않은 로드밸런서」라고 틀리게 말한다.
    """
    # 🔵 2.8.0(P2-3) — 사용자 도메인은 **Route 53 별칭이 있으면** 그 LB 로 간다(`test_cloudfront_origins.py`). 여기는 별칭이 없는 세계다
    document = run(
        FakeAws(
            cloudfront_distributions=with_origins("app-alb", "www.example.com"),
            hosted_zones=[],
        )
    )
    assert not origin_edges(document)
    assert "unresolved_refs" not in node_of(document, DIST_ID)["attributes"]


def test_one_lb_written_two_ways_is_one_line():
    """같은 LB 를 `dualstack.` 유무로 두 번 적은 배포는 선 하나다 — 접은 키로 묶는다."""
    document = run(
        FakeAws(cloudfront_distributions=with_origins(ALB_DNS, f"dualstack.{ALB_DNS}"))
    )
    assert [e["target"] for e in origin_edges(document)] == [ALB_ID]
    # 🔴 접지 않으면 선은 그대로 하나지만 `dualstack.` 쪽이 「수집하지 않은 LB」로 남는다
    assert "unresolved_refs" not in node_of(document, DIST_ID)["attributes"]


@pytest.mark.parametrize(
    "domain",
    [
        f"dualstack.app-alb-1234567890.{REGION}.elb.amazonaws.com",
        f"APP-ALB-1234567890.{REGION}.ELB.AMAZONAWS.COM",
        f"app-alb-1234567890.{REGION}.elb.amazonaws.com.",
    ],
)
def test_origin_dns_is_folded_before_the_join(domain):
    """🔵 `dualstack.` 접두·대소문자·끝 점 — 사람이 콘솔에 넣는 세 모양이 같은 LB 로 간다."""
    document = run(FakeAws(cloudfront_distributions=with_origins(domain)))
    assert [e["target"] for e in origin_edges(document)] == [ALB_ID]


def test_an_nlb_shaped_origin_joins_too():
    """🔵 NLB 의 DNS 모양(`…elb.<region>.amazonaws.com`)도 LB 모양으로 본다."""
    nlb_dns = f"old-nlb-9876543210.elb.{REGION}.amazonaws.com"
    lbs = copy.deepcopy(LOAD_BALANCERS)
    lbs[1]["DNSName"] = nlb_dns
    document = run(
        FakeAws(load_balancers=lbs, cloudfront_distributions=with_origins(nlb_dns))
    )
    assert [e["target"] for e in origin_edges(document)] == [
        f"load_balancer/{ACCOUNT}/{REGION}/old-nlb"
    ]


def test_an_uncollected_lb_origin_is_recorded_not_dropped():
    """🔴 LB 모양인데 색인에 없으면 선 대신 `unresolved_refs` 에 남는다(ADR 0002 §5).

    조용히 버리면 화면은 「원본 없음」과 「수집 안 한 LB」를 구별하지 못한다.
    """
    missing = f"other-alb-1111111111.{REGION}.elb.amazonaws.com"
    document = run(
        FakeAws(
            cloudfront_distributions=with_origins(missing, "assets.s3.amazonaws.com")
        )
    )
    assert not origin_edges(document)
    # S3 오리진은 LB 모양이 아니라 해소 실패에도 들어가지 않는다
    assert node_of(document, DIST_ID)["attributes"]["unresolved_refs"] == [missing]


def test_a_cross_account_origin_resolves():
    """🔵 다른 계정의 ALB 를 가리키는 오리진도 선이 된다 — DNS 이름은 계정 경계 없이 유일하다."""
    scope = Scope(
        account_ids=(ACCOUNT, PARTNER),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )
    clients = {
        ACCOUNT: FakeAws(load_balancers=[]),
        PARTNER: FakeAws(cloudfront_distributions=[]),
    }
    document = collect(
        scope, factory_for(clients), clock=lambda: FIXED_CLOCK
    ).to_document()
    node_of(document, DIST_ID)
    assert [e["target"] for e in origin_edges(document)] == [
        f"load_balancer/{PARTNER}/{REGION}/app-alb"
    ]


def test_a_denied_list_keeps_the_rest_of_the_graph(validator):
    """🔴 부분 실패는 정상 경로다 — CloudFront 가 거부돼도 리전 레인은 그대로 선다(불변식 ④)."""
    document = run(
        FakeAws(
            errors={
                "list_distributions": client_error("AccessDenied", "ListDistributions")
            }
        )
    )
    cov = coverage_of(document, "cloudfront")
    assert [c["status"] for c in cov] == ["denied"]
    # 🔴 `0` 이 아니라 `None` 이다 — 「읽었는데 0개」와 「못 읽었다」는 다른 사실이고, 0 으로
    #    적으면 화면이 「배포가 없다」를 말하게 된다(ADR 0007 · 불변식 ④).
    assert [c["item_count"] for c in cov] == [None]
    assert not [n for n in document["nodes"] if n["type"] == "cloudfront"]
    # 대조군 — 다른 종류는 살아 있다. 전체가 죽으면 위 어서션도 통과한다.
    assert {n["type"] for n in document["nodes"]} >= {"vpc", "subnet", "load_balancer"}
    assert [
        f"{e.json_path}: {e.message}" for e in validator.iter_errors(document)
    ] == []
