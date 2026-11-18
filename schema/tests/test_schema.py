"""The checks that `topology.schema.json` cannot express on its own.

JSON Schema cannot describe the order of an array or whether a reference
resolves, yet those are what determinism and the boundary between the engine and
the renderer come down to, so they are enforced here.

Every positive assertion has a **counterpart that must fail**. An assertion that
cannot fail is not a passing test but an unmeasured one; the tests whose names
end in `_detects_` are those counterparts.
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
    """The coverage sort key, normalised because the Region may be null.

    The schema allows `coverage_entry.region` to be null, for a global resource.
    Leaving `None` in the tuple makes `sorted` raise a TypeError when it compares
    `None` with a string, so a test meant to report a sorting violation would
    crash instead of reporting what was wrong. Null means no Region,
    so it sorts first.
    """
    return (entry["account_id"], entry["region"] or "", entry["resource_type"])


def type_prefix_mismatches(doc: dict) -> list[str]:
    """Nodes whose first id segment disagrees with their type.

    The schema states the id form `<type>/<account_id>/…` only in a description,
    and the pattern accepts any first segment. When they
    disagree, anything that infers the type from the id prefix, as the renderer
    does, is quietly wrong.
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
    """Entries in coverage that are not in scope, the opposite direction from
    `missing_coverage`.

    It means something was collected that was never requested, so scope no longer
    describes what the collection was asked to do (see the `$defs/scope`
    description in the schema: do not mix the request with the result).
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
    """A copy of minimal.json. A negative test breaks exactly one thing in it."""
    return load(FIXTURE_DIR / "minimal.json")


# ---- The schema itself ------------------------------------------------------


def test_schema_itself_is_a_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_fixture_dir_is_not_empty():
    # With no fixtures, every parametrised test below would pass silently while
    # measuring nothing.
    assert FIXTURES, "there is not a single fixtures/*.json"


def test_resource_type_is_node_type_minus_containers():
    """The two enumerations are kept in step by hand, so a mismatch is caught
    here.

    JSON Schema has no set difference, so `resource_type` restates a subset of
    `node_type`. Add a resource to `node_type` and forget `resource_type` and you
    create a type that exists as a node but cannot be requested for collection.
    """
    node_types = set(SCHEMA["$defs"]["node_type"]["enum"])
    resource_types = set(SCHEMA["$defs"]["resource_type"]["enum"])
    # The two derived node types are built while resolving load balancer targets,
    # because no list API returns them, so they are not units of a collection
    # request or of coverage. Do not widen this set: widening it quietly adds
    # types that exist as nodes but cannot be requested.
    # `api_gateway_v2` is the other direction: a collection type that is not a
    # node type. HTTP and WebSocket APIs are also `api_gateway` nodes, but their
    # list API differs, so the collection type was split to make a denial visible
    # on its own. Do not widen this set either.
    assert (
        resource_types
        == (node_types - {"account", "region"} - DERIVED_NODE_TYPES)
        | RESOURCE_ONLY_TYPES
    )


# Collection types whose nodes are of another type: `api_gateway_v2` becomes an
# `api_gateway` node, and `waf_web_acl_global` (the CloudFront scope) becomes a
# `waf_web_acl` node.
RESOURCE_ONLY_TYPES = {"api_gateway_v2", "waf_web_acl_global"}
# Derived nodes; this set has to match `DERIVED_TYPES` in
# `engine/src/nettopo/model.py`, and an engine test checks that side.
# `lambda_function` is no longer here because it became a collection type;
# resolving load balancer targets still builds one when it is not collected.
DERIVED_NODE_TYPES = {
    "external_ip",
    "s3_bucket",
}  # s3_bucket is a CloudFront S3 origin


# ---- The fixtures satisfy the contract --------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_validates(path: Path):
    assert errors(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_node_ids_are_unique(path: Path):
    ids = [n["id"] for n in load(path)["nodes"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_arrays_are_sorted(path: Path):
    """Determinism: unstable ordering means the same input produces different
    bytes."""
    d = load(path)
    assert not unsorted_ids(d["nodes"]), "nodes is not in ascending id order"
    assert not unsorted_ids(d["edges"]), "edges is not in ascending id order"
    cov = [coverage_sort_key(c) for c in d["coverage"]]
    assert cov == sorted(cov), "coverage is not sorted by (account, Region, type)"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_references_resolve(path: Path):
    d = load(path)
    assert dangling_parents(d) == []
    assert dangling_endpoints(d) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_node_id_prefix_matches_type(path: Path):
    """The schema pattern cannot enforce the `<type>/…` id form, so it is
    enforced here."""
    assert type_prefix_mismatches(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_every_requested_combination_is_accounted_for(path: Path):
    """Something requested but absent from coverage is an area that quietly
    disappeared."""
    assert missing_coverage(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_coverage_does_not_exceed_scope(path: Path):
    """The opposite direction: something unrequested in coverage means scope no
    longer reflects the inputs."""
    assert unrequested_coverage(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_collected_at_is_rfc3339(path: Path):
    raw = load(path)["run"]["collected_at"]
    datetime.fromisoformat(raw.replace("Z", "+00:00"))


# ---- A violation of the contract is rejected --------------------------------


def test_unknown_top_level_key_rejected(doc):
    doc["render_hints"] = {"theme": "dark"}
    assert errors(doc)


def test_display_field_on_node_rejected(doc):
    """Keeps the renderer from slipping a presentation field into the
    contract."""
    doc["nodes"][0]["fill_color"] = "#ff0000"
    assert errors(doc)


def test_nested_attribute_object_rejected(doc):
    """Allowing arbitrary nesting under attributes would effectively remove the
    contract."""
    doc["nodes"][0]["attributes"]["nested"] = {"deep": 1}
    assert errors(doc)


def test_unknown_node_type_rejected(doc):
    # Checked with a type that is not in the contract yet; the ones used before
    # (direct_connect_gateway, global_accelerator) have since been added.
    doc["nodes"][0]["type"] = "vpc_lattice_service_network"
    assert errors(doc)


def test_second_tier_resource_type_is_not_in_scope_enum(doc):
    """Adding a resource type is a schema change that goes through its own
    review, so it must not pass yet."""
    doc["scope"]["resource_types"].append("site_to_site_vpn")
    assert errors(doc)


@pytest.mark.parametrize("container", ["account", "region"])
def test_container_type_in_scope_rejected(doc, container):
    """A container is a node the engine synthesises, not something that can be
    requested for collection."""
    doc["scope"]["resource_types"].append(container)
    assert errors(doc)


@pytest.mark.parametrize("container", ["account", "region"])
def test_container_type_in_coverage_rejected(doc, container):
    """A container has no collection outcome, so it cannot appear in
    coverage."""
    doc["coverage"][0]["resource_type"] = container
    assert errors(doc)


def test_node_without_partial_rejected(doc):
    """Allowing it to be omitted would make the engine and the renderer each
    hard-code their own default of false."""
    del doc["nodes"][0]["partial"]
    assert errors(doc)


def test_edge_without_partial_rejected(doc):
    del doc["edges"][0]["partial"]
    assert errors(doc)


def test_edge_without_directed_rejected(doc):
    """If it is omitted, the arrow direction depends on a default outside the
    contract."""
    del doc["edges"][0]["directed"]
    assert errors(doc)


def test_coverage_without_item_count_rejected(doc):
    """Absence would be a third state, neither 0 nor null, that the contract does
    not define."""
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
    """A partial with no reason gives the screen nothing to show for "could not
    be confirmed"."""
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
    """1.0.0 is rejected too: it was the version that allowed partial, directed
    and item_count to be omitted."""
    doc["schema_version"] = bad
    assert errors(doc)


# ---- Discriminating power: without these, the tests above measure nothing ---


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
    """A global resource has a null Region. That is legal under the schema, and it
    still has to sort."""
    entry = dict(doc["coverage"][0], region=None)
    assert errors({**doc, "coverage": [entry]}) == [], (
        "a null Region is legal under the schema"
    )
    keys = [coverage_sort_key(entry)] + [coverage_sort_key(c) for c in doc["coverage"]]
    assert sorted(keys)[0] == coverage_sort_key(entry), "a null Region sorts first"


def test_naive_sort_key_would_crash_on_null_region(doc):
    """Pins what the test above is preventing: undo the normalisation and this
    crashes with a TypeError, so a test meant to report a sorting violation would
    end in a crash and say nothing about the cause."""
    naive = [
        (c["account_id"], c["region"], c["resource_type"]) for c in doc["coverage"]
    ]
    naive.append((doc["coverage"][0]["account_id"], None, "vpc"))
    with pytest.raises(TypeError):
        sorted(naive)


def test_collected_at_check_detects_garbage():
    with pytest.raises(ValueError):
        datetime.fromisoformat("2026-13-45T99:99:99Z".replace("Z", "+00:00"))


# ---- Registered load balancer target counts ---------------------------------


def registered_count_mismatches(doc: dict) -> list[str]:
    """Whether the per-group counts have as many entries as `target_groups`, and
    whether a total, when present, has no nulls under it and adds up.

    JSON Schema cannot compare the lengths of two arrays or add them up, and the
    schema's `$comment` points at this function.
    """
    bad = []
    for n in doc["nodes"]:
        if n["type"] != "load_balancer":
            continue
        a = n.get("attributes") or {}
        per = a.get("target_group_registered_target_counts")
        if per is None:
            if "registered_target_count" in a:
                bad.append(f"{n['id']}: a total with no per-group counts")
            continue
        if len(per) != len(a.get("target_groups") or []):
            bad.append(
                f"{n['id']}: {len(per)} per-group counts for "
                f"{len(a.get('target_groups') or [])} target groups"
            )
        total = a.get("registered_target_count")
        if total is not None and (None in per or sum(per) != total):
            bad.append(
                f"{n['id']}: total {total} does not match the per-group {per} "
                "(with an unreadable group there should be no total)"
            )
    return bad


def _lb(doc: dict) -> dict:
    found = [n for n in doc["nodes"] if n["type"] == "load_balancer"]
    assert found, (
        "minimal.json has no load balancer, which would leave the negative tests "
        "below measuring nothing"
    )
    assert "registered_target_count" in found[0]["attributes"], (
        "the load balancer in minimal.json has no registered counts, so this "
        "measures nothing"
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
    assert registered_count_mismatches(doc) == []  # control case
    lb["attributes"]["registered_target_count"] += 1
    assert registered_count_mismatches(doc)
    lb["attributes"]["registered_target_count"] -= 1
    lb["attributes"]["target_group_registered_target_counts"] = [None, 2]
    assert registered_count_mismatches(doc)  # a total despite an unreadable group
    lb["attributes"]["target_group_registered_target_counts"] = [2]
    del lb["attributes"]["registered_target_count"]
    assert registered_count_mismatches(
        doc
    )  # the number of entries differs from the number of target groups
