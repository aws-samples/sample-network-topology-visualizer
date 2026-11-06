"""계약 축 — 산출물이 `schema/topology.schema.json` 을 만족하는가 + 불변식 ④.

JSON Schema 가 표현할 수 없는 것(배열 정렬·참조 정합성·coverage 완전성)은 여기서 잡는다.
`schema/tests/test_schema.py` 가 fixture 에 대해 같은 규칙을 강제한다 — 엔진 산출물도
같은 규칙을 통과해야 fixture 로 승격할 수 있다.
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pytest
from fakes import ACCOUNT, REGION, SUBNETS, FakeEc2, client_error, factory
from jsonschema import Draft202012Validator
from nettopo.awsguard import ReadOnlyViolation
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Edge, Scope

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "topology.schema.json"
SCOPE = Scope(
    account_ids=(ACCOUNT,), regions=(REGION,), resource_types=("vpc", "subnet")
)
FIXED_CLOCK = "2026-01-02T03:04:05Z"


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    # 계약 파일이 없으면 이 파일의 모든 검사가 조용히 무의미해진다 — 먼저 그것부터 깬다.
    assert SCHEMA_PATH.is_file(), SCHEMA_PATH
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def errors(validator: Draft202012Validator, document: dict) -> list[str]:
    return [f"{e.json_path}: {e.message}" for e in validator.iter_errors(document)]


def build(**kwargs) -> dict:
    client = FakeEc2(**kwargs)
    return collect(SCOPE, factory(client), clock=lambda: FIXED_CLOCK).to_document()


def missing_coverage(document: dict) -> list[str]:
    scope = document["scope"]
    seen = {
        (c["account_id"], c["region"], c["resource_type"]) for c in document["coverage"]
    }
    combos = product(scope["account_ids"], scope["regions"], scope["resource_types"])
    return [f"{a}/{r}/{t}" for a, r, t in combos if (a, r, t) not in seen]


# --- 정상 경로 -------------------------------------------------------------


def test_snapshot_validates(validator):
    assert errors(validator, build()) == []


def test_node_ids_are_unique_and_sorted():
    document = build()
    ids = [n["id"] for n in document["nodes"]]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    coverage = [
        (c["account_id"], c["region"], c["resource_type"]) for c in document["coverage"]
    ]
    assert coverage == sorted(coverage)


def test_hierarchy_and_edge_references_resolve():
    document = build()
    known = {n["id"] for n in document["nodes"]}
    assert [
        n["id"] for n in document["nodes"] if n["parent_id"] not in known | {None}
    ] == []
    dangling = [
        f"{e['id']}.{side}"
        for e in document["edges"]
        for side in ("source", "target")
        if e[side] not in known
    ]
    assert dangling == []


def test_hierarchy_is_account_region_vpc_subnet():
    document = build()
    by_id = {n["id"]: n for n in document["nodes"]}
    subnet = by_id[f"subnet/{ACCOUNT}/{REGION}/subnet-0aaa1111bbbb3333"]
    vpc = by_id[subnet["parent_id"]]
    region = by_id[vpc["parent_id"]]
    account = by_id[region["parent_id"]]
    assert [vpc["type"], region["type"], account["type"]] == [
        "vpc",
        "region",
        "account",
    ]
    assert account["parent_id"] is None and account["region"] is None


def test_every_requested_combination_is_accounted_for():
    assert missing_coverage(build()) == []


def test_coverage_check_detects_a_hole():
    """위 검사의 대조군 — 한 조합을 빼면 잡아내야 한다."""
    document = build()
    document["coverage"] = [
        c for c in document["coverage"] if c["resource_type"] != "subnet"
    ]
    assert missing_coverage(document) == [f"{ACCOUNT}/{REGION}/subnet"]


def test_zero_items_is_ok_not_failure(validator):
    document = build(vpcs=[], subnets=[])
    counts = {
        c["resource_type"]: (c["status"], c["item_count"]) for c in document["coverage"]
    }
    # 0 과 null 은 다른 사실이다. 없는 것을 실패로 적으면 사람이 오판한다.
    assert counts == {"vpc": ("ok", 0), "subnet": ("ok", 0)}
    assert errors(validator, document) == []


# --- v2.0.0: 생략으로 사실을 표현하지 않는다 -------------------------------


def test_flags_are_never_omitted(validator):
    """`partial`·`directed` 는 거짓이어도 적는다(스키마 v2.0.0 `required`).

    생략을 허용하면 소비자가 «없으면 false» 를 각자 하드코딩한다 — 그 지식은 계약 밖이고
    (불변식 ③), 같은 사실에 두 가지 바이트 표현이 생긴다(불변식 ②). `partial` 은 불변식 ④의
    핵심 플래그라 「없으면 알아서 false」가 곧 조용한 실패의 입구다.
    """
    document = build()
    assert [n["id"] for n in document["nodes"] if "partial" not in n] == []

    # 엣지는 이 얇은 슬라이스에서 아직 나오지 않으므로 모델 수준에서 잰다.
    # 🔴 id 는 **엔진 문법**(`<type>|<source>|<target>`, `collect.py:257`)이어야 한다 — 손으로 쓴
    # 슬래시 꼴을 두면 이 테스트가 「스키마가 받는 바이트」를 재지 않는다(2026-09-23 실측: 계약에
    # `edge_id` 패턴이 없어 가짜 문법이 통과하고 있었다).
    source, target = f"account/{ACCOUNT}", f"region/{ACCOUNT}/{REGION}"
    edge = Edge(
        id=f"attachment|{source}|{target}",
        type="attachment",
        source=source,
        target=target,
    ).to_dict()
    assert (edge["directed"], edge["partial"]) == (True, False)
    document["edges"] = [edge]
    assert errors(validator, document) == []


def test_node_id_prefix_matches_type():
    """`<type>/…` 접두어와 `type` 이 어긋난 문서는 v2.0.0 계약 위반이다.

    렌더러가 접두어로 타입을 추론할 여지가 있어 계약으로 굳혔다 —
    `id: "subnet/…"` + `type: "route_table"` 이 v1.0.0 에서는 유효했다.
    """
    document = build()
    assert [
        (n["id"], n["type"])
        for n in document["nodes"]
        if n["id"].split("/", 1)[0] != n["type"]
    ] == []


# --- 불변식 ④ 부분 실패 ----------------------------------------------------


def test_denied_vpc_is_recorded_with_reason(validator):
    document = build(
        errors={"describe_vpcs": client_error("UnauthorizedOperation", "DescribeVpcs")}
    )
    coverage = {c["resource_type"]: c for c in document["coverage"]}
    assert coverage["vpc"]["status"] == "denied"
    assert coverage["vpc"]["reason"]["code"] == "access_denied"
    assert coverage["vpc"]["reason"]["api"] == "ec2:DescribeVpcs"
    # 실패를 0 으로 적으면 「VPC 가 없는 계정」과 구분되지 않는다.
    assert coverage["vpc"]["item_count"] is None
    assert coverage["subnet"]["status"] == "ok"
    assert errors(validator, document) == []


def test_orphan_subnets_stay_referentially_sound_and_marked(validator):
    """부모 VPC 를 모르는 서브넷을 끊긴 참조로 남기지 않고, 모른다는 사실을 노드에 적는다."""
    document = build(
        errors={"describe_vpcs": client_error("AccessDenied", "DescribeVpcs")}
    )
    known = {n["id"] for n in document["nodes"]}
    subnets = [n for n in document["nodes"] if n["type"] == "subnet"]
    assert len(subnets) == len(SUBNETS)
    assert all(n["parent_id"] in known for n in subnets)
    assert all(n["parent_id"] == f"region/{ACCOUNT}/{REGION}" for n in subnets)
    assert all(n["partial"] is True for n in subnets)
    assert all(n["partial_reasons"][0]["code"] == "access_denied" for n in subnets)
    assert errors(validator, document) == []


@pytest.mark.parametrize(
    ("code", "status", "reason_code"),
    [
        ("AccessDenied", "denied", "access_denied"),
        ("UnauthorizedOperation", "denied", "access_denied"),
        ("Throttling", "throttled", "throttled"),
        ("RequestLimitExceeded", "throttled", "throttled"),
        ("AuthFailure", "unsupported", "unsupported_region"),
        ("OptInRequired", "unsupported", "unsupported_region"),
        ("InternalError", "error", "api_error"),
    ],
)
def test_error_codes_map_to_status(validator, code, status, reason_code):
    document = build(errors={"describe_subnets": client_error(code, "DescribeSubnets")})
    entry = next(c for c in document["coverage"] if c["resource_type"] == "subnet")
    assert (entry["status"], entry["reason"]["code"]) == (status, reason_code)
    assert entry["reason"]["message"], "사유 없는 실패는 사람이 고칠 단서를 주지 않는다"
    assert errors(validator, document) == []


def test_partial_failure_does_not_lose_the_other_resource():
    """한 리소스가 죽어도 나머지는 끝까지 수집한다 — 전체를 죽이면 그게 최악이다."""
    document = build(
        errors={"describe_vpcs": client_error("Throttling", "DescribeVpcs")}
    )
    assert len([n for n in document["nodes"] if n["type"] == "subnet"]) == len(SUBNETS)
    assert [n for n in document["nodes"] if n["type"] == "vpc"] == []


# --- 삼키지 말아야 하는 것 -------------------------------------------------


def test_client_factory_failure_names_the_api_that_actually_failed(validator):
    """크로스계정 진입 실패는 `sts:AssumeRole` 이다 — 고정 문자열로 적으면 오진한다.

    「EC2 권한이 없다」로 읽히면 사용자는 엉뚱한 정책을 고친다. 팩토리는 대상 계정의 역할이
    없거나 신뢰정책이 막을 때 여기로 온다(실측 2026-09-14).
    """

    def failing_factory(account_id, region, service):
        raise client_error("AccessDenied", "AssumeRole", "역할을 assume 할 수 없다")

    document = collect(SCOPE, failing_factory, clock=lambda: FIXED_CLOCK).to_document()

    assert errors(validator, document) == []
    assert missing_coverage(document) == []
    for entry in document["coverage"]:
        assert entry["status"] == "denied"
        assert entry["item_count"] is None
        assert entry["reason"]["api"] == "sts:AssumeRole"
        assert entry["reason"]["code"] == "access_denied"
    # 계정·리전 노드는 남는다 — 「못 봤다」를 그릴 자리가 있어야 한다(불변식 ④).
    assert [n["type"] for n in document["nodes"]] == ["account", "region"]


def test_read_only_violation_is_not_swallowed():
    """가드 위반은 부분 실패가 아니라 버그다. coverage 에 적고 넘어가면 안 된다."""
    client = FakeEc2(errors={"describe_vpcs": ReadOnlyViolation("일부러")})
    with pytest.raises(ReadOnlyViolation):
        collect(SCOPE, factory(client), clock=lambda: FIXED_CLOCK)


def test_unimplemented_resource_type_raises_instead_of_skipping():
    """실패를 `skipped` 로 감추지 마라 — skipped 는 사용자가 제외한 경우뿐이다."""
    # 2차 리소스(아직 수집하지 않는 목록). 구현되는 날 이 어서션이 미측정으로 변하지 않도록 전제를 먼저 잰다.
    assert "client_vpn_endpoint" not in SUPPORTED_RESOURCE_TYPES
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=("vpc", "client_vpn_endpoint"),
    )
    with pytest.raises(ValueError, match="client_vpn_endpoint"):
        collect(scope, factory(FakeEc2()), clock=lambda: FIXED_CLOCK)


def test_scope_reflects_the_request_not_the_result(validator):
    """scope 와 coverage 를 섞지 마라 — 요청과 결과는 다른 사실이다."""
    document = build(
        errors={"describe_vpcs": client_error("AccessDenied", "DescribeVpcs")}
    )
    assert document["scope"]["resource_types"] == ["vpc", "subnet"]
    assert errors(validator, document) == []
