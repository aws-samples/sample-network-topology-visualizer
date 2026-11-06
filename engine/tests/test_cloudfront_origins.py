"""CloudFront 원본 넓히기(스키마 2.8.0 · ADR 0002 §7-1 · 사용자 결정 2026-10-20 P2-3).

축:
① S3 원본 → 파생 노드 `s3_bucket`(같은 계정의 배포 둘이 같은 버킷 = 노드 하나 · 웹사이트 꼴 · 도메인의 리전)
② 사용자 도메인 원본 → Route 53 별칭 → 수집한 LB(`via` = `dns_alias` · `alias`) · 같은 LB 를 직접 적은 원본과 겹치면 선 하나
③ VPC 원본 → `ListVpcOrigins` 의 엔드포인트 ARN → 사설 ALB · EC2(`via` = `vpc_origin`) · 끝까지(Marker) · 거부 = 그 배포의 사유
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
from fakes import (
    ACCOUNT,
    CLOUDFRONT_DISTRIBUTIONS,
    REGION,
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
ALB = f"load_balancer/{ACCOUNT}/{REGION}/app-alb"


def _doc(client: FakeAws) -> dict[str, Any]:
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )
    return collect(
        scope, factory(client), clock=lambda: "2026-01-02T03:04:05Z"
    ).to_document()


def _dist(*origins: dict[str, Any]) -> list[dict[str, Any]]:
    d = copy.deepcopy(CLOUDFRONT_DISTRIBUTIONS[1])
    d["Origins"] = {"Quantity": len(origins), "Items": list(origins)}
    return [d]


def _origins(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["target"]: e for e in doc["edges"] if e["type"] == "origin"}


def test_s3_origins_become_one_bucket_node_per_account():
    doc = _doc(
        FakeAws()
    )  # 기본 가짜 — 두 배포가 같은 S3 원본 `assets.s3.amazonaws.com`
    bucket = f"s3_bucket/{ACCOUNT}/{GLOBAL}/assets"
    nodes = [n for n in doc["nodes"] if n["type"] == "s3_bucket"]
    assert [n["id"] for n in nodes] == [bucket]
    assert nodes[0]["parent_id"] == f"account/{ACCOUNT}" and nodes[0]["region"] is None
    assert nodes[0]["attributes"] == {
        "bucket_name": "assets",
        "website": False,
        "regions": [],
    }
    assert (
        sum(1 for e in doc["edges"] if e["type"] == "origin" and e["target"] == bucket)
        == 2
    )
    assert all(
        e["attributes"] == {"via": "s3"} for e in doc["edges"] if e["target"] == bucket
    )
    # 웹사이트 엔드포인트 · 리전이 든 꼴
    doc = _doc(
        FakeAws(
            cloudfront_distributions=_dist(
                {
                    "Id": "w",
                    "DomainName": "site.example.s3-website-us-west-2.amazonaws.com",
                },
                {"Id": "r", "DomainName": "logs-bkt.s3.eu-west-1.amazonaws.com"},
            )
        )
    )
    by = {
        n["native_id"]: n["attributes"]
        for n in doc["nodes"]
        if n["type"] == "s3_bucket"
    }
    assert by["site.example"] == {
        "bucket_name": "site.example",
        "website": True,
        "regions": ["us-west-2"],
    }
    assert by["logs-bkt"]["regions"] == ["eu-west-1"]
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))


def test_a_custom_domain_reaches_its_lb_through_a_route53_alias():
    """`www.example.com` 은 기본 가짜의 공개 영역에서 app-alb 별칭이다 — DNS 를 풀지 않고 AWS 응답의 별칭만 따라간다."""
    doc = _doc(
        FakeAws(
            cloudfront_distributions=_dist({"Id": "c", "DomainName": "www.example.com"})
        )
    )
    e = _origins(doc)[ALB]
    assert e["attributes"] == {"via": "dns_alias", "alias": "www.example.com"}
    # 별칭이 없는 사용자 도메인 = 선도 미해결도 없다(우리가 그리는 종류가 아닐 수 있다)
    doc = _doc(
        FakeAws(
            cloudfront_distributions=_dist(
                {"Id": "c", "DomainName": "nope.example.org"}
            )
        )
    )
    assert ALB not in _origins(doc)
    # 같은 LB 를 주소 꼴로도 적으면 선 하나(먼저 생긴 주소 꼴 선이 남는다)
    doc = _doc(
        FakeAws(
            cloudfront_distributions=_dist(
                {"Id": "c", "DomainName": "www.example.com"},
                {
                    "Id": "d",
                    "DomainName": f"app-alb-1234567890.{REGION}.elb.amazonaws.com",
                },
            )
        )
    )
    assert (
        sum(1 for e in doc["edges"] if e["type"] == "origin" and e["target"] == ALB)
        == 1
    )
    assert _origins(doc)[ALB]["attributes"] == {}


def test_vpc_origins_reach_the_private_alb_and_instance():
    alb_arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/app-alb/abc123"
    ec2 = "i-0aaa1111bbbb2222"
    client = FakeAws(
        cloudfront_distributions=_dist(
            {
                "Id": "v1",
                "DomainName": "internal.example",
                "VpcOriginConfig": {"VpcOriginId": "vo_alb"},
            },
            {
                "Id": "v2",
                "DomainName": "ip-10-0-0-1.ec2.internal",
                "VpcOriginConfig": {"VpcOriginId": "vo_ec2"},
            },
        ),
        vpc_origins=[
            {"Id": "vo_alb", "OriginEndpointArn": alb_arn},
            {
                "Id": "vo_other",
                "OriginEndpointArn": "arn:aws:ec2:us-east-1:111122223333:instance/i-0zzz",
            },
            {
                "Id": "vo_ec2",
                "OriginEndpointArn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{ec2}",
            },
        ],
    )
    doc = _doc(client)
    got = _origins(doc)
    assert got[ALB]["attributes"] == {"via": "vpc_origin"}
    assert got[f"ec2_instance/{ACCOUNT}/{REGION}/{ec2}"]["attributes"] == {
        "via": "vpc_origin"
    }
    markers = [
        kw.get("Marker") for m, kw in client.direct_calls if m == "list_vpc_origins"
    ]
    assert markers == [None, "page-2"]  # 두 장 끝까지
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))
    # 실제 VPC 원본의 도메인 = 그 ALB 의 DNS(주소 꼴) — 엣지 하나 · `via` 가 남는다(VPC 원본 우선)
    client = FakeAws(
        cloudfront_distributions=_dist(
            {
                "Id": "v1",
                "DomainName": f"app-alb-1234567890.{REGION}.elb.amazonaws.com",
                "VpcOriginConfig": {"VpcOriginId": "vo_alb"},
            },
        ),
        vpc_origins=[{"Id": "vo_alb", "OriginEndpointArn": alb_arn}],
    )
    doc = _doc(client)
    assert [
        e["attributes"]
        for e in doc["edges"]
        if e["type"] == "origin" and e["target"] == ALB
    ] == [{"via": "vpc_origin"}]
    # 거부 = VPC 원본을 쓰는 배포에 사유 · 선 없음(다른 원본은 그대로)
    client = FakeAws(
        cloudfront_distributions=_dist(
            {
                "Id": "v1",
                "DomainName": "internal.example",
                "VpcOriginConfig": {"VpcOriginId": "vo_alb"},
            }
        ),
        errors={"list_vpc_origins": client_error("AccessDenied", "ListVpcOrigins")},
    )
    doc = _doc(client)
    dist = next(n for n in doc["nodes"] if n["type"] == "cloudfront")
    assert dist["partial"] is True
    assert "cloudfront:ListVpcOrigins" in [r["api"] for r in dist["partial_reasons"]]
    assert ALB not in _origins(doc)
    # VPC 원본이 없으면 부르지 않는다
    client = FakeAws()
    _doc(client)
    assert "list_vpc_origins" not in client.calls
