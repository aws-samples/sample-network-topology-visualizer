"""topology.schema.json 의 대조군.

JSON Schema 는 배열 순서와 참조 정합성을 표현할 수 없다. 그런데 그 두 가지가 각각
불변식 ②(결정론)와 ③(엔진·렌더 경계)의 실제 내용이므로 여기서 강제한다.

모든 양성 어서션에는 **반드시 실패하는 짝**을 둔다. 실패할 수 없는 어서션은 통과가 아니라
미측정이다. 이 파일에서 `_detects_` 로 끝나는 테스트가 그 짝이다.
"""

from __future__ import annotations

import json
from datetime import datetime
from itertools import product
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "topology.schema.json"
FIXTURE_DIR = ROOT / "fixtures"

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def errors(doc: dict) -> list[str]:
    return [
        f"{e.json_path}: {e.message}"
        for e in sorted(VALIDATOR.iter_errors(doc), key=lambda e: e.json_path)
    ]


def unsorted_ids(items: list[dict]) -> bool:
    ids = [i["id"] for i in items]
    return ids != sorted(ids)


def coverage_sort_key(entry: dict) -> tuple[str, str, str]:
    """coverage 정렬 키. region 이 null 일 수 있으므로 정규화한다.

    스키마는 `coverage_entry.region` 에 null 을 허용한다(2차의 글로벌 리소스용). 튜플에 None 을
    그대로 넣으면 `sorted` 가 `None < str` 비교에서 TypeError 로 죽는다 — 정렬 위반을 잡아야 할
    테스트가 크래시로 끝나 무엇이 틀렸는지 알려주지 못한다. null 은 리전 없음이므로 맨 앞에 둔다.
    """
    return (entry["account_id"], entry["region"] or "", entry["resource_type"])


def type_prefix_mismatches(doc: dict) -> list[str]:
    """id 의 첫 구획과 type 이 어긋난 노드.

    id 형식이 `<type>/<account_id>/…` 라는 것은 스키마 description 에만 있고 pattern 은 첫 구획이
    무엇이든 통과시킨다. 어긋나면 렌더러가 id 접두어로 타입을 추론하는 순간 조용히 틀린다.
    """
    return [
        f"{n['id']} vs type={n['type']}"
        for n in doc["nodes"]
        if n["id"].split("/", 1)[0] != n["type"]
    ]


def dangling_parents(doc: dict) -> list[str]:
    known = {n["id"] for n in doc["nodes"]}
    return [
        f"{n['id']} -> {n['parent_id']}"
        for n in doc["nodes"]
        if n.get("parent_id") is not None and n["parent_id"] not in known
    ]


def dangling_endpoints(doc: dict) -> list[str]:
    known = {n["id"] for n in doc["nodes"]}
    out = []
    for e in doc["edges"]:
        for side in ("source", "target"):
            if e[side] not in known:
                out.append(f"{e['id']}.{side} -> {e[side]}")
    return out


def missing_coverage(doc: dict) -> list[str]:
    scope = doc["scope"]
    seen = {(c["account_id"], c["region"], c["resource_type"]) for c in doc["coverage"]}
    combos = product(scope["account_ids"], scope["regions"], scope["resource_types"])
    return [f"{a}/{r}/{t}" for a, r, t in combos if (a, r, t) not in seen]


def unrequested_coverage(doc: dict) -> list[str]:
    """scope 에 없는데 coverage 에 있는 항목. missing_coverage 의 반대 방향이다.

    요청하지 않은 것을 수집했다는 뜻이므로 scope 가 산출물의 입력을 정확히 반영하지 못한 상태다
    (스키마 `$defs/scope` description: 요청과 결과를 섞지 마라).
    """
    scope = doc["scope"]
    combos = set(
        product(scope["account_ids"], scope["regions"], scope["resource_types"])
    )
    return [
        f"{c['account_id']}/{c['region']}/{c['resource_type']}"
        for c in doc["coverage"]
        if (c["account_id"], c["region"], c["resource_type"]) not in combos
    ]


@pytest.fixture
def doc() -> dict:
    """minimal.json 사본. 음성 테스트는 여기서 한 곳만 망가뜨린다."""
    return load(FIXTURE_DIR / "minimal.json")


# ---- 스키마 자체 --------------------------------------------------------------


def test_schema_itself_is_a_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_fixture_dir_is_not_empty():
    # 픽스처가 0개면 아래 파라미터화 테스트 전부가 조용히 통과한다(미측정).
    assert FIXTURES, "fixtures/*.json 이 하나도 없다"


def test_resource_type_is_node_type_minus_containers():
    """두 열거를 손으로 중복 유지하고 있으므로 어긋남을 여기서 잡는다.

    JSON Schema 에 집합 차집합이 없어 `resource_type` 은 `node_type` 의 부분집합을 다시 적는다.
    2차 리소스를 node_type 에만 넣고 resource_type 을 잊으면 '노드로는 존재하는데 수집 요청은
    못 하는' 타입이 생긴다.
    """
    node_types = set(SCHEMA["$defs"]["node_type"]["enum"])
    resource_types = set(SCHEMA["$defs"]["resource_type"]["enum"])
    # 🔵 파생 노드 2종(2.7.0 · LB 대상 해소가 짓는다 — 목록 API 가 없다)은 수집 요청·coverage 의 단위가 아니다.
    #    이 집합을 넓히지 마라 — 넓히면 「노드로는 있는데 수집 요청은 못 하는」 타입이 조용히 늘어난다.
    # 🔵 배치 D(2.10.0) — `api_gateway_v2` 는 노드 종류가 아니라 **수집 종류만**이다(HTTP·WebSocket API 도 노드는 `api_gateway` · 목록 API 가 달라
    #    거부가 따로 보이게 수집 종류를 나눴다). 이 집합도 넓히지 마라
    assert (
        resource_types
        == (node_types - {"account", "region"} - DERIVED_NODE_TYPES)
        | RESOURCE_ONLY_TYPES
    )


# 파생 노드(2.7.0) — `engine/src/nettopo/model.py` 의 `DERIVED_TYPES` 와 같아야 한다(엔진 테스트가 그 짝을 잰다).
# 🔵 2.8.0(P2-6) — `lambda_function` 은 수집 종류가 됐다(수집하지 않으면 LB 대상 해소가 여전히 짓는다 — `DERIVABLE_TYPES`)
# 수집 종류이지만 노드 종류는 다른 종류의 것(배치 D · `api_gateway_v2` → 노드 `api_gateway`)
# 2.12.0(ADR 0014) — `waf_web_acl_global`(CloudFront 범위) → 노드 `waf_web_acl`
RESOURCE_ONLY_TYPES = {"api_gateway_v2", "waf_web_acl_global"}
DERIVED_NODE_TYPES = {
    "external_ip",
    "s3_bucket",
}  # s3_bucket = CloudFront S3 원본(2.8.0 · P2-3)


# ---- 픽스처는 계약을 만족한다 -------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_validates(path: Path):
    assert errors(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_node_ids_are_unique(path: Path):
    ids = [n["id"] for n in load(path)["nodes"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_arrays_are_sorted(path: Path):
    """불변식 ②. 정렬이 흔들리면 같은 입력에서 다른 바이트가 나온다."""
    d = load(path)
    assert not unsorted_ids(d["nodes"]), "nodes 가 id 오름차순이 아니다"
    assert not unsorted_ids(d["edges"]), "edges 가 id 오름차순이 아니다"
    cov = [coverage_sort_key(c) for c in d["coverage"]]
    assert cov == sorted(cov), "coverage 정렬이 (계정, 리전, 종류) 순이 아니다"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_references_resolve(path: Path):
    d = load(path)
    assert dangling_parents(d) == []
    assert dangling_endpoints(d) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_node_id_prefix_matches_type(path: Path):
    """id 형식 `<type>/…` 은 스키마 pattern 이 강제하지 못한다. 여기서 강제한다."""
    assert type_prefix_mismatches(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_every_requested_combination_is_accounted_for(path: Path):
    """불변식 ④. 요청했는데 coverage 에 없으면 그 영역은 조용히 사라진 것이다."""
    assert missing_coverage(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_coverage_does_not_exceed_scope(path: Path):
    """반대 방향. 요청하지 않은 것이 coverage 에 있으면 scope 가 입력을 반영하지 못한 것이다."""
    assert unrequested_coverage(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_collected_at_is_rfc3339(path: Path):
    raw = load(path)["run"]["collected_at"]
    datetime.fromisoformat(raw.replace("Z", "+00:00"))


# ---- 계약 위반은 거부된다 -----------------------------------------------------


def test_unknown_top_level_key_rejected(doc):
    doc["render_hints"] = {"theme": "dark"}
    assert errors(doc)


def test_display_field_on_node_rejected(doc):
    """불변식 ③. 렌더러가 표시용 필드를 몰래 끼워넣지 못하게 한다."""
    doc["nodes"][0]["fill_color"] = "#ff0000"
    assert errors(doc)


def test_nested_attribute_object_rejected(doc):
    """attributes 가 임의 중첩을 허용하면 계약이 사실상 사라진다."""
    doc["nodes"][0]["attributes"]["nested"] = {"deep": 1}
    assert errors(doc)


def test_unknown_node_type_rejected(doc):
    # direct_connect_gateway 는 2.1.0 에서 계약에 들어왔다 — 아직 없는 2차 타입으로 잰다.
    doc["nodes"][0]["type"] = (
        "vpc_lattice_service_network"  # 배치 D 가 global_accelerator 를 들였다 — 아직 없는 종류로 잰다
    )
    assert errors(doc)


def test_second_tier_resource_type_is_not_in_scope_enum(doc):
    """2차 리소스 추가는 스키마 PR(승인 2명)이다. 지금은 통과하면 안 된다."""
    doc["scope"]["resource_types"].append("site_to_site_vpn")
    assert errors(doc)


@pytest.mark.parametrize("container", ["account", "region"])
def test_container_type_in_scope_rejected(doc, container):
    """컨테이너는 엔진이 합성하는 노드다 — 수집하라고 요청할 수 있는 대상이 아니다."""
    doc["scope"]["resource_types"].append(container)
    assert errors(doc)


@pytest.mark.parametrize("container", ["account", "region"])
def test_container_type_in_coverage_rejected(doc, container):
    """컨테이너에는 수집 성공·실패라는 사실이 없으므로 coverage 에 나타날 수 없다."""
    doc["coverage"][0]["resource_type"] = container
    assert errors(doc)


def test_node_without_partial_rejected(doc):
    """불변식 ③·④. 생략을 허용하면 엔진·렌더러가 각자 false 를 하드코딩해야 한다."""
    del doc["nodes"][0]["partial"]
    assert errors(doc)


def test_edge_without_partial_rejected(doc):
    del doc["edges"][0]["partial"]
    assert errors(doc)


def test_edge_without_directed_rejected(doc):
    """생략하면 화살표 방향이 계약 밖 기본값에 의존한다."""
    del doc["edges"][0]["directed"]
    assert errors(doc)


def test_coverage_without_item_count_rejected(doc):
    """'부재'는 0 도 null 도 아닌 계약에 없는 제3의 상태다(ADR 0001 결정 3)."""
    del doc["coverage"][0]["item_count"]
    assert errors(doc)


@pytest.mark.parametrize("bad", ["1234", "1111222233334", "11112222333a", ""])
def test_malformed_account_id_rejected(doc, bad):
    doc["nodes"][0]["account_id"] = bad
    assert errors(doc)


@pytest.mark.parametrize("bad", ["US-East-1", "us_east_1", "useast1", "us-east"])
def test_malformed_region_rejected(doc, bad):
    doc["scope"]["regions"] = [bad]
    assert errors(doc)


def test_vpc_without_cidr_rejected(doc):
    vpc = next(n for n in doc["nodes"] if n["type"] == "vpc")
    del vpc["attributes"]["cidr_blocks"]
    assert errors(doc)


def test_subnet_without_availability_zone_rejected(doc):
    subnet = next(n for n in doc["nodes"] if n["type"] == "subnet")
    del subnet["attributes"]["availability_zone"]
    assert errors(doc)


def test_partial_without_reason_rejected(doc):
    """사유 없는 partial 은 '확인하지 못했다'를 표시할 근거를 주지 않는다."""
    doc["nodes"][0]["partial"] = True
    assert errors(doc)


def test_failed_coverage_without_reason_rejected(doc):
    doc["coverage"][0]["status"] = "denied"
    doc["coverage"][0]["reason"] = None
    assert errors(doc)


def test_missing_coverage_rejected(doc):
    del doc["coverage"]
    assert errors(doc)


@pytest.mark.parametrize("bad", ["1.0.0", "3.0.0", "2.0", "2.0.0-rc1"])
def test_wrong_schema_version_rejected(doc, bad):
    """1.0.0 도 거부 대상이다 — partial·directed·item_count 생략이 허용됐던 버전이다."""
    doc["schema_version"] = bad
    assert errors(doc)


# ---- 구조 검사의 판별력(이게 없으면 위 구조 테스트는 미측정) -------------------


def test_sort_check_detects_disorder(doc):
    doc["nodes"].reverse()
    assert unsorted_ids(doc["nodes"])


def test_parent_check_detects_dangling_reference(doc):
    doc["nodes"][1]["parent_id"] = "vpc/111122223333/us-east-1/vpc-0nonexistent"
    assert dangling_parents(doc)


def test_endpoint_check_detects_dangling_reference(doc):
    doc["edges"][0]["target"] = "subnet/111122223333/us-east-1/subnet-0nonexistent"
    assert dangling_endpoints(doc)


def test_coverage_completeness_check_detects_a_hole(doc):
    doc["coverage"].pop()
    assert missing_coverage(doc)


def test_scope_check_detects_unrequested_coverage(doc):
    extra = dict(doc["coverage"][0], resource_type="security_group")
    doc["coverage"].append(extra)
    assert unrequested_coverage(doc)


def test_type_prefix_check_detects_mismatch(doc):
    node = doc["nodes"][1]
    node["id"] = node["id"].replace("internet_gateway/", "route_table/", 1)
    assert type_prefix_mismatches(doc)


def test_coverage_sort_key_tolerates_null_region(doc):
    """2차의 글로벌 리소스는 region 이 null 이다. 스키마상 합법이고 정렬도 되어야 한다."""
    entry = dict(doc["coverage"][0], region=None)
    assert errors({**doc, "coverage": [entry]}) == [], (
        "region: null 은 스키마상 합법이다"
    )
    keys = [coverage_sort_key(entry)] + [coverage_sort_key(c) for c in doc["coverage"]]
    assert sorted(keys)[0] == coverage_sort_key(entry), "null 리전은 맨 앞에 온다"


def test_naive_sort_key_would_crash_on_null_region(doc):
    """위 테스트가 막고 있는 것을 고정한다. 정규화를 되돌리면 TypeError 로 죽는다 —
    정렬 위반을 보고해야 할 테스트가 크래시로 끝나면 원인을 알려주지 못한다."""
    naive = [
        (c["account_id"], c["region"], c["resource_type"]) for c in doc["coverage"]
    ]
    naive.append((doc["coverage"][0]["account_id"], None, "vpc"))
    with pytest.raises(TypeError):
        sorted(naive)


def test_collected_at_check_detects_garbage():
    with pytest.raises(ValueError):
        datetime.fromisoformat("2026-13-45T99:99:99Z".replace("Z", "+00:00"))


# ---- LB 등록 대상 수(사용자 결정 2026-10-16 · ADR 0006 「W3② LB 대상 상태」 개정) ----------------


def registered_count_mismatches(doc: dict) -> list[str]:
    """그룹별 수가 `target_groups` 와 자리 수가 같은가 · 합이 있으면 null 이 없고 합이 맞는가.

    JSON Schema 는 두 배열의 길이·합을 비교하지 못한다 — 스키마 `$comment` 가 이 함수를 지목한다.
    """
    bad = []
    for n in doc["nodes"]:
        if n["type"] != "load_balancer":
            continue
        a = n.get("attributes") or {}
        per = a.get("target_group_registered_target_counts")
        if per is None:
            if "registered_target_count" in a:
                bad.append(f"{n['id']}: 합만 있고 그룹별 수가 없다")
            continue
        if len(per) != len(a.get("target_groups") or []):
            bad.append(
                f"{n['id']}: 그룹별 수 {len(per)}자리 ≠ 대상 그룹 {len(a.get('target_groups') or [])}개"
            )
        total = a.get("registered_target_count")
        if total is not None and (None in per or sum(per) != total):
            bad.append(
                f"{n['id']}: 합 {total} ≠ 그룹별 {per}(못 읽은 그룹이 있으면 합은 없어야 한다)"
            )
    return bad


def _lb(doc: dict) -> dict:
    found = [n for n in doc["nodes"] if n["type"] == "load_balancer"]
    assert found, "minimal.json 에 LB 가 없다 — 아래 음성 테스트가 미측정이 된다"
    assert "registered_target_count" in found[0]["attributes"], (
        "minimal.json LB 에 등록 수가 없다(미측정)"
    )
    return found[0]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_registered_counts_line_up_with_target_groups(path: Path):
    assert registered_count_mismatches(load(path)) == []


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("registered_target_count", "2"),
        ("registered_target_count", -1),
        ("registered_target_count", 1.5),
        ("target_group_registered_target_counts", ["0", 2]),
        ("target_group_registered_target_counts", [-1, 2]),
    ],
)
def test_malformed_registered_counts_rejected(doc, key, bad):
    _lb(doc)["attributes"][key] = bad
    assert errors(doc)


def test_registered_total_without_per_group_counts_rejected(doc):
    del _lb(doc)["attributes"]["target_group_registered_target_counts"]
    assert errors(doc)


def test_registered_count_checker_detects_a_wrong_total_and_a_short_row(doc):
    lb = _lb(doc)
    assert registered_count_mismatches(doc) == []  # 대조군
    lb["attributes"]["registered_target_count"] += 1
    assert registered_count_mismatches(doc)
    lb["attributes"]["registered_target_count"] -= 1
    lb["attributes"]["target_group_registered_target_counts"] = [None, 2]
    assert registered_count_mismatches(doc)  # 못 읽은 그룹이 있는데 합이 있다
    lb["attributes"]["target_group_registered_target_counts"] = [2]
    del lb["attributes"]["registered_target_count"]
    assert registered_count_mismatches(doc)  # 자리 수가 대상 그룹 수와 다르다
