"""WAF 웹 ACL 노드(스키마 2.12.0 · ADR 0014 · 사용자 결정 2026-10-23 「캔버스만 보면 WAF 가 없는 것처럼 보인다」).

기본 가짜 세계를 쓴다 — 그 세계의 ALB(`app-alb`)에는 이미 리전 범위 web ACL `prod-edge-acl` 이 붙어 있고(`WEB_ACLS` · `GetWebACLForResource`),
CloudFront 배포 하나는 CloudFront 범위 web ACL ARN 을 `WebACLId` 로 든다. 여기에 web ACL 목록·상세·보호 대상(API Gateway 스테이지)을 얹는다.

재는 것: 두 범위의 노드 모양 · 보호 선 셋(ALB = ALB 쪽 사실 · CloudFront = 배포 쪽 사실 · API Gateway = `ListResourcesForWebACL`) ·
못 읽음(목록 · 상세 · 보호 대상 — 「없음」과 다른 모양 · 불변식 ④) · CloudFront 범위 = us-east-1 고정 · 한 서비스(`wafv2`)를 두 리전에서 부르는
클라이언트 열쇠 · `NextMarker` 둘째 장 · 묻지 않기로 한 종류는 묻지 않는다 · 스키마 · 결정론. 🔴 실계정 미검증(권한 적용 전) — 가짜로만 쟀다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from fakes import (
    ACCOUNT,
    APP_WEB_ACL_ARN,
    CLOUDFRONT_DISTRIBUTION,
    REGION,
    FakeAws,
    client_error,
    factory,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import GLOBAL, Scope
from nettopo.serialize import to_json
from nettopo.services.waf_web_acl import acl_node_id
from test_services import coverage_of, node_of

CLOCK = "2026-01-02T03:04:05Z"
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json").read_text(
        encoding="utf-8"
    )
)
SCOPE = Scope((ACCOUNT,), (REGION,), SUPPORTED_RESOURCE_TYPES)

EDGE_ID = "abc12345"  # `APP_WEB_ACL_ARN` 의 id(ALB 에 붙은 것)
IDLE_ID = "def67890"
DENIED_ID = "0aa11bb2"
CF_ID = "abc"  # 기본 세계 CloudFront 배포의 `WebACLId` 끝(`…:global/webacl/prod-edge-acl/abc`)
_WAF = f"arn:aws:wafv2:{REGION}:{ACCOUNT}"
IDLE_ARN = f"{_WAF}:regional/webacl/idle-acl/{IDLE_ID}"
DENIED_ARN = f"{_WAF}:regional/webacl/locked-acl/{DENIED_ID}"
CF_ARN = f"arn:aws:wafv2:us-east-1:{ACCOUNT}:global/webacl/prod-edge-acl/{CF_ID}"
REST_API = "r1e2s3t4a5"  # pragma: allowlist secret — 가짜 API id(비밀 아님)
GONE_API = "z9y8x7w6v5"  # pragma: allowlist secret — 이 세계에 없는 API
_STAGE = f"arn:aws:apigateway:{REGION}::/restapis"

ACL = f"waf_web_acl/{ACCOUNT}/{REGION}/{EDGE_ID}"
IDLE = f"waf_web_acl/{ACCOUNT}/{REGION}/{IDLE_ID}"
DENIED = f"waf_web_acl/{ACCOUNT}/{REGION}/{DENIED_ID}"
CF_ACL = f"waf_web_acl/{ACCOUNT}/{GLOBAL}/{CF_ID}"
ALB = f"load_balancer/{ACCOUNT}/{REGION}/app-alb"
DIST = f"cloudfront/{ACCOUNT}/{GLOBAL}/{CLOUDFRONT_DISTRIBUTION}"
API = f"api_gateway/{ACCOUNT}/{REGION}/{REST_API}"


def _summary(name: str, acl_id: str, arn: str) -> dict[str, Any]:
    return {
        "Name": name,
        "Id": acl_id,
        "ARN": arn,
        "LockToken": f"lock-{acl_id}",
        "Description": "비밀 아님",
    }


DETAILS: dict[str, dict[str, Any]] = {
    EDGE_ID: {
        "Name": "prod-edge-acl",
        "Id": EDGE_ID,
        "ARN": APP_WEB_ACL_ARN,
        "DefaultAction": {"Allow": {}},
        "Capacity": 725,
        "ManagedByFirewallManager": False,
        # 일부러 우선순위 역순 — 평가 순서로 다시 놓는지 잰다
        "Rules": [
            {
                "Name": "rate-limit",
                "Priority": 2,
                "Action": {"Block": {}},
                "Statement": {"RateBasedStatement": {"Limit": 2000}},
            },
            {
                "Name": "common",
                "Priority": 0,
                "OverrideAction": {"None": {}},
                "Statement": {
                    "ManagedRuleGroupStatement": {
                        "VendorName": "AWS",
                        "Name": "AWSManagedRulesCommonRuleSet",
                    }
                },
            },
            {
                "Name": "bad-inputs",
                "Priority": 1,
                "OverrideAction": {"None": {}},
                "Statement": {
                    "ManagedRuleGroupStatement": {
                        "VendorName": "AWS",
                        "Name": "AWSManagedRulesKnownBadInputsRuleSet",
                    }
                },
            },
        ],
        "VisibilityConfig": {"MetricName": "누출되면안됨"},
    },
    IDLE_ID: {
        "Name": "idle-acl",
        "Id": IDLE_ID,
        "ARN": IDLE_ARN,
        "DefaultAction": {"Block": {}},
        "Rules": [],
        "Capacity": 0,
    },
    CF_ID: {
        "Name": "prod-edge-acl",
        "Id": CF_ID,
        "ARN": CF_ARN,
        "DefaultAction": {"Allow": {}},
        "Rules": [
            {
                "Name": "geo",
                "Priority": 0,
                "Action": {"Block": {}},
                "Statement": {"GeoMatchStatement": {}},
            }
        ],
        "ManagedByFirewallManager": True,
    },
}


def world(**over: Any) -> FakeAws:
    kw: dict[str, Any] = {
        "waf_regional": [
            _summary("prod-edge-acl", EDGE_ID, APP_WEB_ACL_ARN),
            _summary("idle-acl", IDLE_ID, IDLE_ARN),
            _summary("locked-acl", DENIED_ID, DENIED_ARN),
        ],
        "waf_cloudfront": [_summary("prod-edge-acl", CF_ID, CF_ARN)],
        "waf_details": DETAILS,
        "waf_resources": {
            APP_WEB_ACL_ARN: [
                f"{_STAGE}/{REST_API}/stages/prod",
                f"{_STAGE}/{REST_API}/stages/beta",
                f"{_STAGE}/{GONE_API}/stages/prod",
            ]
        },
        "rest_apis": [{"id": REST_API, "name": "orders-api"}],
        "followup_errors": {
            ("get_web_acl", DENIED_ID): client_error(
                "AccessDeniedException", "GetWebACL"
            ),
            ("list_resources_for_web_acl", DENIED_ARN): client_error(
                "AccessDeniedException", "ListResourcesForWebACL"
            ),
        },
    }
    kw.update(over)
    return FakeAws(**kw)


def _doc(client: FakeAws | None = None, scope: Scope = SCOPE) -> dict[str, Any]:
    return collect(scope, factory(client or world()), clock=lambda: CLOCK).to_document()


def _protects(doc: dict[str, Any], source: str) -> dict[str, dict[str, Any]]:
    return {
        e["target"]: e
        for e in doc["edges"]
        if e["type"] == "waf_protection" and e["source"] == source
    }


# ------------------------------------------------------------------ 노드


def test_a_regional_web_acl_is_a_region_card_with_its_rules_in_evaluation_order() -> (
    None
):
    n = node_of(_doc(), ACL)
    assert (
        n["type"] == "waf_web_acl"
        and n["region"] == REGION
        and n["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    )
    assert n["name"] == "prod-edge-acl" and n["partial"] is False
    a = n["attributes"]
    assert a["scope"] == "regional" and a["default_action"] == "allow"
    assert a["rule_count"] == 3 and a["rule_names"] == [
        "common",
        "bad-inputs",
        "rate-limit",
    ]
    assert a["managed_rule_groups"] == [
        "AWS/AWSManagedRulesCommonRuleSet",
        "AWS/AWSManagedRulesKnownBadInputsRuleSet",
    ]
    assert a["firewall_manager"] is False and a["capacity"] == 725
    # 구성만 — 잠금 토큰(매번 새 값)·지표 이름·설명은 새지 않는다(불변식 ②)
    text = json.dumps(n, ensure_ascii=False)
    assert (
        "lock-" not in text and "누출되면안됨" not in text and "비밀 아님" not in text
    )


def test_a_cloudfront_scope_web_acl_is_a_global_card() -> None:
    n = node_of(_doc(), CF_ACL)
    assert n["region"] is None and n["parent_id"] == f"account/{ACCOUNT}"
    assert (
        n["attributes"]["scope"] == "cloudfront"
        and n["attributes"]["firewall_manager"] is True
    )
    assert n["attributes"]["default_action"] == "allow" and n["attributes"][
        "rule_names"
    ] == ["geo"]


def test_an_idle_web_acl_is_a_card_with_no_protection_line() -> None:
    doc = _doc()
    n = node_of(doc, IDLE)
    assert (
        n["partial"] is False
        and n["attributes"]["default_action"] == "block"
        and n["attributes"]["rule_count"] == 0
    )
    assert _protects(doc, IDLE) == {}


# ------------------------------------------------------------------ 보호 선


def test_the_alb_line_comes_from_the_albs_own_web_acl() -> None:
    doc = _doc()
    out = _protects(doc, ACL)
    assert out[ALB]["directed"] is True and out[ALB]["attributes"] == {}
    assert out[ALB]["id"] == f"waf_protection|{ACL}|{ALB}"
    # ALB 의 속성 줄은 그대로다(정보창 · 3패스 재료)
    assert node_of(doc, ALB)["attributes"]["web_acl_arn"] == APP_WEB_ACL_ARN


def test_the_cloudfront_line_comes_from_the_distributions_web_acl_id() -> None:
    doc = _doc()
    assert set(_protects(doc, CF_ACL)) == {DIST}
    assert node_of(doc, DIST)["attributes"]["web_acl_id"] == CF_ARN


def test_api_gateway_stages_fold_into_one_line_and_an_uncollected_api_is_named() -> (
    None
):
    doc = _doc()
    out = _protects(doc, ACL)
    assert out[API]["attributes"] == {"stages": ["beta", "prod"]}
    assert node_of(doc, ACL)["attributes"]["unresolved_protected"] == [
        f"{GONE_API} not_collected"
    ]
    assert set(out) == {ALB, API}


def test_list_resources_is_asked_only_for_api_gateway_and_never_for_the_cloudfront_scope() -> (
    None
):
    client = world()
    _doc(client)
    asked = [kw for m, kw in client.direct_calls if m == "list_resources_for_web_acl"]
    assert asked and {kw["ResourceType"] for kw in asked} == {"API_GATEWAY"}
    assert all(":regional/webacl/" in kw["WebACLArn"] for kw in asked)


def test_without_a_web_acl_node_there_is_no_line_but_the_alb_row_stays() -> None:
    """목록이 거부되면 web ACL 카드가 없다 → 선을 짓지 않는다(끝이 없는 선) · ALB 의 「웹 ACL」 줄은 그대로 · coverage 가 「확인 못 함」을 말한다."""
    client = world(
        errors={
            "list_web_acls#REGIONAL": client_error(
                "AccessDeniedException", "ListWebACLs"
            )
        }
    )
    doc = _doc(client)
    assert all(n["type"] != "waf_web_acl" or n["region"] is None for n in doc["nodes"])
    assert _protects(doc, ACL) == {}
    assert node_of(doc, ALB)["attributes"]["web_acl_arn"] == APP_WEB_ACL_ARN
    (cov,) = [c for c in doc["coverage"] if c["resource_type"] == "waf_web_acl"]
    assert cov["status"] == "denied" and cov["reason"]["api"] == "wafv2:ListWebACLs"
    # 다른 범위는 무관하다
    assert coverage_of(doc, "waf_web_acl_global")["status"] == "ok" and _protects(
        doc, CF_ACL
    )


# ------------------------------------------------------------------ 못 읽음(불변식 ④)


def test_a_denied_detail_is_partial_with_no_made_up_rules() -> None:
    n = node_of(_doc(), DENIED)
    assert n["partial"] is True
    apis = sorted(r["api"] for r in n["partial_reasons"])
    assert apis == ["wafv2:GetWebACL", "wafv2:ListResourcesForWebACL"]
    # 규칙 수 0 · 기본 동작 · 「붙은 곳 없음」을 지어내지 않는다 — 키 자체가 없다
    assert n["attributes"] == {"scope": "regional"}


def test_a_denied_resource_list_is_not_an_unprotected_api() -> None:
    client = world(
        followup_errors={
            ("list_resources_for_web_acl", APP_WEB_ACL_ARN): client_error(
                "AccessDeniedException", "ListResourcesForWebACL"
            )
        }
    )
    doc = _doc(client)
    n = node_of(doc, ACL)
    assert n["partial"] is True and [r["api"] for r in n["partial_reasons"]] == [
        "wafv2:ListResourcesForWebACL"
    ]
    assert (
        "unresolved_protected" not in n["attributes"]
        and n["attributes"]["rule_count"] == 3
    )
    # API 선만 빠진다 — ALB 선은 ALB 쪽 사실이라 그대로
    assert set(_protects(doc, ACL)) == {ALB}


# ------------------------------------------------------------------ 호출 모양


def test_the_cloudfront_scope_is_read_in_us_east_1_and_the_alb_web_acl_in_the_scan_region() -> (
    None
):
    """🔴 한 서비스(`wafv2`)를 두 리전에서 부른다 — 클라이언트 열쇠가 서비스 이름뿐이면 서울 ALB 의 web ACL 을 us-east-1 에 묻는다."""
    scan = "ap-northeast-2"
    pinned, local = world(), world()
    seen: list[tuple[str, str]] = []

    def fac(account: str, region: str, service: str) -> Any:
        seen.append((service, region))
        return pinned if (service, region) == ("wafv2", "us-east-1") else local

    collect(
        Scope((ACCOUNT,), (scan,), SUPPORTED_RESOURCE_TYPES), fac, clock=lambda: CLOCK
    )
    assert {r for s, r in seen if s == "wafv2"} == {scan, "us-east-1"}
    assert {kw["Scope"] for m, kw in pinned.direct_calls if m == "list_web_acls"} == {
        "CLOUDFRONT"
    }
    assert {kw["Scope"] for m, kw in local.direct_calls if m == "list_web_acls"} == {
        "REGIONAL"
    }
    assert (
        "get_web_acl_for_resource" in local.calls
        and "get_web_acl_for_resource" not in pinned.calls
    )
    assert "list_resources_for_web_acl" not in pinned.calls


def test_the_second_page_of_web_acls_is_read() -> None:
    client = world()
    doc = _doc(client)
    markers = [
        kw.get("NextMarker")
        for m, kw in client.direct_calls
        if m == "list_web_acls" and kw["Scope"] == "REGIONAL"
    ]
    assert markers == [None, "page-2"]
    assert {ACL, IDLE, DENIED} <= {n["id"] for n in doc["nodes"]}
    assert coverage_of(doc, "waf_web_acl")["item_count"] == 3


def test_a_repeated_marker_stops_the_walk() -> None:
    """같은 표지를 되돌려 주는 API 에 무한히 돌지 않는다(`base._manual_pages`)."""
    from nettopo.services.base import pages

    class Loop:
        def __init__(self) -> None:
            self.n = 0

        def can_paginate(self, method: str) -> bool:
            return False

        def list_web_acls(self, **kw: Any) -> dict[str, Any]:
            self.n += 1
            assert self.n < 5
            return {"WebACLs": [{"Id": str(self.n)}], "NextMarker": "same"}

    c = Loop()
    assert [
        x["Id"] for x in pages(c, "list_web_acls", "WebACLs", Scope="REGIONAL")
    ] == ["1", "2"]


def test_no_web_acl_means_no_follow_up_calls() -> None:
    client = FakeAws()
    _doc(client)
    assert (
        "get_web_acl" not in client.calls
        and "list_resources_for_web_acl" not in client.calls
    )


# ------------------------------------------------------------------ 계약


def test_acl_node_id_reads_both_scopes_and_refuses_other_shapes() -> None:
    assert acl_node_id(APP_WEB_ACL_ARN) == ACL
    assert acl_node_id(CF_ARN) == CF_ACL
    for bad in (
        "",
        "abc-classic-id",
        f"{_WAF}:regional/ipset/x/1",
        "arn:aws:waf::1:webacl/x",
    ):
        assert acl_node_id(bad) is None


def test_the_snapshot_validates_and_is_deterministic() -> None:
    doc = _doc()
    errors = [e.message for e in Draft202012Validator(SCHEMA).iter_errors(doc)]
    assert not errors, errors[:3]
    a = to_json(collect(SCOPE, factory(world()), clock=lambda: CLOCK))
    b = to_json(collect(SCOPE, factory(world()), clock=lambda: CLOCK))
    assert a == b


def test_the_schema_refuses_a_cloudfront_scope_card_with_a_region() -> None:
    doc = _doc()
    bad = json.loads(json.dumps(doc))
    for n in bad["nodes"]:
        if n["id"] == CF_ACL:
            n["region"] = "us-east-1"
    assert list(Draft202012Validator(SCHEMA).iter_errors(bad))
    bad2 = json.loads(json.dumps(doc))
    for e in bad2["edges"]:
        if e["type"] == "waf_protection":
            e["target"] = "vpc/111122223333/us-east-1/vpc-x"
    assert list(Draft202012Validator(SCHEMA).iter_errors(bad2))


def test_waf_types_have_no_observation_rows() -> None:
    """상태 없음(ADR 0006 §O7 「waf_web_acl」·「waf_protection」 행) — 관측 문서에 항목이 없다 · 카드의 생김·없어짐은 이력이 구조 두 장으로 낸다."""
    import re

    import rel_world as RW
    from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation

    scope = Scope((RW.ACCOUNT,), (RW.REGION,), SUPPORTED_RESOURCE_TYPES)
    topology = collect(scope, factory(RW.world()), clock=lambda: CLOCK).to_document()
    present = {n["type"] for n in topology["nodes"]} | {
        e["type"] for e in topology["edges"]
    }
    assert {"waf_web_acl", "waf_protection"} <= present
    state = collect(
        Scope((RW.ACCOUNT,), (RW.REGION,), OBSERVED_RESOURCE_TYPES),
        factory(RW.world()),
        clock=lambda: "2026-01-02T04:00:00Z",
        with_state=True,
    )
    obs, _ = build_observation(
        state, topology=topology, observed_at="2026-01-02T04:00:00Z", tool_version="t"
    )
    kinds = {re.split(r"[/|]", o["target_id"])[0] for o in obs["observations"]}
    assert not kinds & {"waf_web_acl", "waf_protection"}
    # 관측은 WAF 를 부르지 않는다(5분 차례의 호출 수를 늘리지 않는다)
    assert not {"waf_web_acl", "waf_web_acl_global"} & set(OBSERVED_RESOURCE_TYPES)
