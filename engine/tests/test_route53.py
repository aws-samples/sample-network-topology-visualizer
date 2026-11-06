"""Route 53 호스팅 영역(스키마 2.8.0 · ADR 0002 §11 · 사용자 결정 2026-10-20 P2-4) — 세 번째 글로벌 리소스.

축:
① 별칭(A·AAAA)이 수집한 LB·CloudFront 를 가리키면 `dns_alias`(DNS 이름 색인 · `dualstack.`·끝 점을 접는다 · 대상마다 하나)
② LB·CloudFront 꼴이 아닌 별칭(S3 웹사이트 등)은 잇지도 남기지도 않는다 · 꼴인데 수집하지 않았으면 `unresolved_refs`
③ 사설 영역 ↔ VPC = `zone_association` · 레코드 값(IP·CNAME)은 구조에 없다(민감)
④ 글로벌 — 리전 둘을 돌아도 노드 하나 · 거부는 그 영역의 사유(불변식 ④)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from fakes import (
    ACCOUNT,
    APP_VPC,
    REGION,
    ZONE_LEAK,
    ZONE_PRIVATE,
    ZONE_PUBLIC,
    FakeAws,
    client_error,
    factory,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import GLOBAL, Scope

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "topology.schema.json").read_text(encoding="utf-8")
)
PUB = f"route53_hosted_zone/{ACCOUNT}/{GLOBAL}/{ZONE_PUBLIC}"
PRIV = f"route53_hosted_zone/{ACCOUNT}/{GLOBAL}/{ZONE_PRIVATE}"


def _doc(client: Any = None, regions: tuple[str, ...] = (REGION,)) -> dict[str, Any]:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=regions, resource_types=SUPPORTED_RESOURCE_TYPES
    )
    return collect(
        scope, factory(client or FakeAws()), clock=lambda: "2026-01-02T03:04:05Z"
    ).to_document()


def _node(doc: dict[str, Any], nid: str) -> dict[str, Any]:
    return next(n for n in doc["nodes"] if n["id"] == nid)


def _edges(doc: dict[str, Any], kind: str, source: str) -> dict[str, dict[str, Any]]:
    return {
        e["target"]: e
        for e in doc["edges"]
        if e["type"] == kind and e["source"] == source
    }


def test_alias_records_reach_the_collected_lb_and_distribution():
    doc = _doc()
    alias = _edges(doc, "dns_alias", PUB)
    lb = f"load_balancer/{ACCOUNT}/{REGION}/app-alb"
    cf = next(
        n["id"]
        for n in doc["nodes"]
        if n["type"] == "cloudfront"
        and n["attributes"]["domain_name"] == "d1aaaaaaaaaaaa.cloudfront.net"
    )
    assert set(alias) == {lb, cf}
    assert alias[lb]["attributes"] == {"record_names": ["www.example.com"]}
    assert alias[lb]["directed"] is True
    assert alias[cf]["attributes"] == {"record_names": ["cdn.example.com"]}
    node = _node(doc, PUB)
    assert node["parent_id"] == f"account/{ACCOUNT}" and node["region"] is None
    assert (
        node["attributes"]["zone_name"] == "example.com"
        and node["attributes"]["private"] is False
    )
    # S3 웹사이트 별칭은 우리가 그리는 종류가 아니다 — 잇지도 미해결로 남기지도 않는다
    assert "static.example.com" not in node["attributes"]["alias_record_names"]
    assert "unresolved_refs" not in node["attributes"]
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))


def test_private_zone_associates_its_vpc_and_keeps_an_uncollected_alias():
    doc = _doc()
    assoc = _edges(doc, "zone_association", PRIV)
    assert list(assoc) == [f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"]
    assert assoc[f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"]["directed"] is False
    node = _node(doc, PRIV)
    assert node["attributes"]["private"] is True and node["attributes"]["vpc_ids"] == [
        APP_VPC
    ]
    # 수집하지 않은 내부 ALB 를 가리키는 별칭 — 지어내지 않고 남긴다(DNS 이름을 접은 꼴)
    assert node["attributes"]["unresolved_refs"] == [
        f"internal-app-alb-1234567890.{REGION}.elb.amazonaws.com"
    ]
    assert node["partial"] is False


def test_record_values_never_reach_the_snapshot():
    """레코드 값(IP·CNAME)은 고객 도메인 전체 목록이다 — 별칭 이름만 싣는다."""
    assert ZONE_LEAK not in json.dumps(_doc())


def test_a_zone_is_one_node_across_regions_and_denials_are_its_reasons():
    doc = _doc(regions=(REGION, "us-west-2"))
    assert [n["id"] for n in doc["nodes"]].count(PUB) == 1
    client = FakeAws(
        followup_errors={
            ("list_resource_record_sets", f"/hostedzone/{ZONE_PUBLIC}"): client_error(
                "AccessDenied", "ListResourceRecordSets"
            ),
            ("get_hosted_zone", f"/hostedzone/{ZONE_PRIVATE}"): client_error(
                "AccessDenied", "GetHostedZone"
            ),
        }
    )
    doc = _doc(client)
    pub = _node(doc, PUB)
    assert pub["partial"] is True and "alias_record_names" not in pub["attributes"]
    assert "route53:ListResourceRecordSets" in [
        r["api"] for r in pub["partial_reasons"]
    ]
    assert not _edges(doc, "dns_alias", PUB)
    priv = _node(doc, PRIV)
    assert priv["partial"] is True and "vpc_ids" not in priv["attributes"]
    assert "route53:GetHostedZone" in [r["api"] for r in priv["partial_reasons"]]
    assert not _edges(doc, "zone_association", PRIV)
