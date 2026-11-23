"""The history axis (`nettopo observe --previous`): does it avoid recording as
a change something that is not one?

What this file is about is not whether changes are caught. A set difference
takes three lines and it almost always catches them. The problem is the other
direction. When a permission disappears, when the metric toggle is turned off,
or when a Region drops out of the scope, a plain difference records that as
resources having been deleted or traffic having stopped. What somebody sees on
screen is "12 resources deleted", when in fact nothing was deleted at all.

So every guard comes with a control case: the situation it has to suppress, and
the situation it must not suppress, which is a real deletion or a real stop in
traffic. Without the control case an implementation that suppresses everything
passes too, and an assertion that cannot fail is not a pass but an absence of
measurement.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import (
    ACCOUNT,
    EGRESS_NAT,
    EGRESS_SUBNET,
    EGRESS_VPC,
    INSTANCE_RESERVATIONS,
    NAT_GATEWAYS,
    REGION,
    VPC_ENDPOINTS,
    FakeAws,
    FakeCloudWatch,
    client_error,
    factory,
    factory_by_service,
    series_key,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.history import apply_history, target_cells
from nettopo.metrics import read_metrics
from nettopo.model import GLOBAL, Scope, node_id
from nettopo.serialize import content_digest
from nettopo.observe import (
    DEFAULT_FLAP_COLLAPSE_THRESHOLD,
    DEFAULT_MAX_CHANGES,
    DEFAULT_RESOLUTION_SECONDS,
    DEFAULT_RETENTION_SECONDS,
    OBSERVED_RESOURCE_TYPES,
    build_observation,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "observation.schema.json").read_text(encoding="utf-8")
)
TOPOLOGY_SCHEMA = json.loads(
    (ROOT / "schema" / "topology.schema.json").read_text(encoding="utf-8")
)

# The structure snapshot comes before every observation, which is the real
# order: the structure tier in CodeBuild, then the state tier batch.
STRUCTURE_AT = "2026-08-31T00:00:00Z"
T1 = "2026-09-01T00:00:00Z"
T2 = "2026-09-01T00:05:00Z"
T3 = "2026-09-01T00:10:00Z"
T4 = "2026-09-01T00:15:00Z"
T5 = "2026-09-01T00:20:00Z"
# The schema oracle in `schema/tests`, which puts the same verdict functions on
# the engine's output.
_spec = importlib.util.spec_from_file_location(
    "observation_oracle_history",
    ROOT / "schema" / "tests" / "test_observation_schema.py",
)
assert _spec is not None and _spec.loader is not None
ORACLE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ORACLE)

# Nineteen days after T2, which is outside the seven-day retention window.
LATE = "2026-09-20T00:00:00Z"

NAT_ID = node_id("nat_gateway", ACCOUNT, REGION, EGRESS_NAT)
NEW_NAT = "nat-0aaa9999bbbb8888"
NAT_FLOW = series_key(
    "AWS/NATGateway", "BytesOutToDestination", {"NatGatewayId": EGRESS_NAT}
)


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _snapshot(
    resource_types: tuple[str, ...],
    at: str,
    regions: tuple[str, ...],
    errors: dict[str, Exception] | None,
    overrides: dict[str, Any],
) -> Any:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=regions, resource_types=resource_types
    )
    # The state tier, the observe CLI, runs with the state probes on
    # (`with_state=True` in `cli.py`), while the structure tier runs with them
    # off. Observing with them off leaves RDS, ECS and others reporting a value
    # of None that no rule covers.
    return collect(
        scope,
        factory(FakeAws(errors=errors, **overrides)),
        clock=lambda: at,
        with_state=at != STRUCTURE_AT,
    )


def _structure(
    regions: tuple[str, ...] = (REGION,), **overrides: Any
) -> dict[str, Any]:
    """The structure document, carrying a content digest the way a release does
    (`serialize.to_json`). It is what pairs up the `was` shapes.
    """
    document = _snapshot(
        SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, regions, None, overrides
    ).to_document()
    document["run"]["content_digest"] = content_digest(document)
    return document


def _document(
    at: str,
    *,
    regions: tuple[str, ...] = (REGION,),
    series: dict[Any, list[float]] | None = None,
    state_errors: dict[str, Exception] | None = None,
    observed_types: tuple[str, ...] = OBSERVED_RESOURCE_TYPES,
    topology: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """The output of one `observe` run.

    `state_errors` applies to the state tier only, which is the shape a lost
    permission takes against a real account. The structure document was
    collected while the permission was still there, because Stack A runs on a
    longer schedule, and only the state tier is denied. So the target is still
    in the structure while the observation document has no entry for it, which
    gives the same bytes as having been deleted.
    """
    if topology is None:
        topology = _snapshot(
            SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, regions, None, overrides
        ).to_document()
    state = _snapshot(observed_types, at, regions, state_errors, overrides)
    layer = (
        read_metrics(
            state,
            factory_by_service({"cloudwatch": FakeCloudWatch(series)}),
            at=at,
        )
        if series is not None
        else None
    )
    document, _dropped = build_observation(
        state, topology=topology, observed_at=at, tool_version="test", metrics=layer
    )
    return document


def _nat(state: str, *, extra: bool = False, only_first: bool = False) -> list[dict]:
    """A variation on the NAT gateway dataset: change one state, or add or
    remove a resource.
    """
    data = copy.deepcopy(NAT_GATEWAYS)
    data[0]["State"] = state
    if only_first:
        del data[1]
    if extra:
        data.append(
            {
                "NatGatewayId": NEW_NAT,
                "SubnetId": EGRESS_SUBNET,
                "VpcId": EGRESS_VPC,
                "State": "available",
                "ConnectivityType": "public",
                "NatGatewayAddresses": [],
                "Tags": [],
            }
        )
    return data


def _kinds(document: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [row for row in document["changes"] if row["kind"] == kind]


def _chain(
    pairs: list[tuple[str, dict[str, Any]]],
    previous: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Observe each `(moment, overrides)` in order, accumulating the history,
    which is what the batch actually does.

    Given `previous`, it continues from that document, for the case of following
    a document that was edited into an older shape.
    """
    documents: list[dict[str, Any]] = []
    for at, overrides in pairs:
        document = _document(at, **overrides)
        apply_history(document, previous)
        documents.append(document)
        previous = document
    return documents


# ------------------------------------------------------------------- baseline


def test_the_first_run_has_nothing_to_compare_against():
    """An empty list does not mean nothing changed. It means there is nothing
    to compare against at all.

    What somebody experiences: on the day the batch is first turned on, the
    screen has to say that the record starts here, not that there have been no
    recent changes. The baseline moment equalling this observation is how that
    fact is represented in the data.
    """
    document = _document(T1)
    report = apply_history(document, None)

    assert report.diffed is False
    assert document["changes"] == []
    assert document["history"]["baseline_at"] == document["run"]["observed_at"]
    assert document["history"]["truncated"] is False
    assert report.warnings == ()
    jsonschema.Draft202012Validator(SCHEMA).validate(document)


# ----------------------------------------------------- configuration changes


def test_a_new_resource_is_recorded_as_added():
    before = _document(T1)
    after = _document(T2, nat_gateways=_nat("available", extra=True))
    report = apply_history(after, before)

    added = _kinds(after, "resource_added")
    assert [row["target_id"] for row in added] == [
        node_id("nat_gateway", ACCOUNT, REGION, NEW_NAT)
    ]
    # `at` is the moment the change was seen, which is this observation.
    assert added[0]["at"] == T2
    # For a configuration change the before and after are written as null
    # rather than omitted; omitting them would give one fact two byte
    # representations.
    assert added[0]["from"] is None and added[0]["to"] is None
    assert added[0]["repeat_count"] == 1
    assert report.new_rows == 1 and report.excluded == 0
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_a_real_deletion_is_recorded_once_and_not_again():
    """The control case: a resource that really did disappear is recorded once,
    and later runs do not produce the same row again.

    What somebody experiences: deleting one resource puts one row in the list,
    and the next observation does not turn that row into two. If it did, it
    would read as the same thing being deleted every five minutes.
    """
    full = {"nat_gateways": _nat("available")}
    gone = {"nat_gateways": _nat("available", only_first=True)}
    first, second, third = _chain([(T1, full), (T2, gone), (T3, gone)])

    removed = _kinds(second, "resource_removed")
    assert len(removed) == 1, removed
    assert removed[0]["target_id"].endswith("nat-0fff6666aaaa7777")
    assert removed[0]["at"] == T2
    # The third run has nothing new to record, because the target is absent
    # from both documents.
    assert _kinds(third, "resource_removed") == removed
    assert first["changes"] == []


def test_an_add_and_a_remove_in_one_run_pass_the_schema_sort_oracle():
    """One appearance and one disappearance in a single observation. The schema
    oracle has to accept the engine's order, `(at, kind, target_id)`, as it is.

    Nobody experiences this directly, since the screen re-sorts newest first.
    But an oracle that sorts on a different key accuses a correctly sorted
    release of being out of order, and every check that trusts that oracle then
    fails falsely. This surfaced while building a customer demo, with an older
    helper keyed on `(at, target_id)`. The id of the disappearing side has to be
    the smaller one for the `kind` axis to decide the order; the other way round
    both keys give the same answer and nothing is measured.
    """
    swapped = _nat("available", only_first=True)
    swapped.append(
        {**_nat("available", extra=True)[-1], "NatGatewayId": "nat-0fff9999cccc0000"}
    )
    _, after = _chain(
        [(T1, {"nat_gateways": _nat("available")}), (T2, {"nat_gateways": swapped})]
    )
    rows = [(row["kind"], row["target_id"]) for row in after["changes"]]
    assert rows == [
        (
            "resource_added",
            node_id("nat_gateway", ACCOUNT, REGION, "nat-0fff9999cccc0000"),
        ),
        ("resource_removed", GONE_NAT),
    ]
    assert rows[1][1] < rows[0][1]  # premise: the disappearing id is smaller
    assert not ORACLE.unsorted(ORACLE.change_sort_keys(after))


# --------------------------- the shape of what disappeared (`was`)

GONE_NAT = node_id("nat_gateway", ACCOUNT, REGION, "nat-0fff6666aaaa7777")


def _incident(structure: dict[str, Any], target: str) -> list[dict[str, Any]]:
    return sorted(
        (e for e in structure["edges"] if target in (e["source"], e["target"])),
        key=lambda e: e["id"],
    )


def _removal_pair() -> tuple[dict, dict, dict, dict]:
    """The case where the deletion is seen after the structure has been
    collected afresh, so the shape exists only in the previous structure.
    """
    full = {"nat_gateways": _nat("available")}
    gone = {"nat_gateways": _nat("available", only_first=True)}
    full_s, gone_s = _structure(**full), _structure(**gone)
    before = _document(T1, topology=full_s, **full)
    after = _document(T2, topology=gone_s, **gone)
    return before, after, full_s, gone_s


def test_a_removed_card_carries_its_shape_from_the_structure_it_was_drawn_on():
    """What somebody experiences: clicking the NAT gateway that disappeared in
    the history shows that card standing greyed out in the diagram of the time,
    with its name and its connections. Without the shape, the screen can only
    put the last few characters of an id in the list.
    """
    before, after, full_s, gone_s = _removal_pair()
    report = apply_history(after, before, (gone_s, full_s))

    [row] = _kinds(after, "resource_removed")
    assert row["target_id"] == GONE_NAT
    node = next(n for n in full_s["nodes"] if n["id"] == GONE_NAT)
    assert row["was"]["nodes"] == [node]
    # Every connection it had, in id order. If it were empty this check could
    # not measure the order.
    assert row["was"]["edges"] == _incident(full_s, GONE_NAT)
    assert report.shaped == 1 and report.unshaped == 0 and report.warnings == ()
    jsonschema.Draft202012Validator(SCHEMA).validate(after)
    # Exactly the shape of the structure schema, so the screen can redraw it
    # with the same layout function.
    defs = TOPOLOGY_SCHEMA["$defs"]
    for key, items in (("node", row["was"]["nodes"]), ("edge", row["was"]["edges"])):
        validator = jsonschema.Draft202012Validator(
            {"$defs": defs, "$ref": f"#/$defs/{key}"}
        )
        for item in items:
            validator.validate(item)


def test_a_removed_card_takes_every_line_that_touched_it_in_id_order():
    """When a card that had connections disappears, an internet gateway with a
    VPC attachment and routes, those lines go into the shape as well.

    What somebody experiences: in the diagram of the time, the lines leading to
    the card that disappeared stand greyed out alongside it. Without the lines
    the card floats on its own and there is no telling what it was connected to.
    """
    full_s = _structure()
    before = _document(T1, topology=full_s)
    after = _document(T2, topology=full_s, internet_gateways=[])
    report = apply_history(after, before, (full_s,))

    igw = next(n["id"] for n in full_s["nodes"] if n["type"] == "internet_gateway")
    [row] = [r for r in _kinds(after, "resource_removed") if r["target_id"] == igw]
    expected = _incident(full_s, igw)
    assert len(expected) >= 2, (
        "the control case is empty, so the order cannot be measured"
    )
    assert row["was"]["edges"] == expected  # `_incident` sorts by id
    assert report.unshaped == 0


def test_a_deletion_seen_before_the_structure_refresh_uses_the_current_structure():
    """The common case: the five-minute observation sees the deletion before the
    hourly structure collection does, so the shape is in the current structure.
    """
    full = {"nat_gateways": _nat("available")}
    full_s = _structure(**full)
    before = _document(T1, topology=full_s, **full)
    after = _document(
        T2, topology=full_s, nat_gateways=_nat("available", only_first=True)
    )
    report = apply_history(after, before, (full_s,))

    [row] = _kinds(after, "resource_removed")
    assert row["was"]["nodes"][0]["id"] == GONE_NAT
    assert report.shaped == 1


def test_a_structure_the_previous_run_was_not_drawn_on_gives_no_shape():
    """Do not take a shape from a structure that does not pair up. The name and
    connections from another moment are not the shape it had at the time.

    Absence means not knowing, so no `null` is written. The number that could
    not be recorded is counted and warned about, and the screen describes that
    row in words with no picture.
    """
    before, after, _full_s, gone_s = _removal_pair()
    report = apply_history(after, before, (gone_s,))

    [row] = _kinds(after, "resource_removed")
    assert "was" not in row
    assert report.shaped == 0 and report.unshaped == 1
    assert any("구조 문서를 못 찾았다" in w for w in report.warnings), report.warnings


def test_a_same_id_in_another_structure_is_not_borrowed():
    """What pairs them up is the content digest. Two structures collected at the
    same moment with different digests are different structures; only a
    re-collection with identical content pairs up.
    """
    before, after, full_s, gone_s = _removal_pair()
    forged = copy.deepcopy(full_s)
    forged["run"]["content_digest"] = "sha256:" + "0" * 64
    # Put the forged copy first. Behind, the first candidate, the new structure,
    # has no such card, so the check passes without ever looking at the pairing.
    report = apply_history(after, before, (forged, gone_s))
    assert report.unshaped == 1 and "was" not in _kinds(after, "resource_removed")[0]


def test_only_removal_rows_get_a_shape_and_inherited_rows_keep_theirs():
    """An appearance row and a state row carry no shape, which the schema
    forbids. A row carried over from before goes through as written.
    """
    before, after, full_s, gone_s = _removal_pair()
    apply_history(after, before, (gone_s, full_s))
    shaped = copy.deepcopy(_kinds(after, "resource_removed")[0])

    grown = {"nat_gateways": _nat("failed", only_first=True, extra=True)}
    grown_s = _structure(**grown)
    later = _document(T3, topology=grown_s, **grown)
    report = apply_history(later, after, (grown_s, gone_s))
    assert report.new_rows >= 2  # one appearance and one state change
    for row in later["changes"]:
        if row["kind"] != "resource_removed":
            assert "was" not in row, row
    assert _kinds(later, "resource_removed") == [shaped]
    assert report.shaped == 0 and report.unshaped == 0 and report.warnings == ()
    jsonschema.Draft202012Validator(SCHEMA).validate(later)


def test_the_shape_is_byte_identical_across_runs():
    """The shape, too, comes only from the input, the structure bytes."""

    def run() -> str:
        before, after, full_s, gone_s = _removal_pair()
        apply_history(after, before, (gone_s, full_s))
        return json.dumps(after, sort_keys=False, ensure_ascii=False)

    assert run() == run()


def test_the_schema_refuses_a_shape_on_rows_that_are_still_there():
    before, after, full_s, gone_s = _removal_pair()
    apply_history(after, before, (gone_s, full_s))
    validator = jsonschema.Draft202012Validator(SCHEMA)
    row = _kinds(after, "resource_removed")[0]
    for kind, extra in (
        ("resource_added", {}),
        ("status_changed", {"from": "up", "to": "down"}),
    ):
        bad = copy.deepcopy(after)
        bad["changes"] = [{**row, "kind": kind, **extra}]
        assert not validator.is_valid(bad), kind
    bad = copy.deepcopy(after)
    bad["changes"] = [{**row, "was": None}]
    assert not validator.is_valid(bad), "null was accepted in place of absence"


def test_losing_permission_is_not_recorded_as_deletion():
    """The product defect this guard exists to prevent.

    What somebody experiences: the permission for a NAT gateway that was
    readable yesterday is gone today. The observation document has no entry for
    that type at all, which gives the same bytes as having been deleted. A plain
    difference puts "2 NAT gateways deleted" on the screen, and somebody goes
    hunting through the console for resources that disappeared. Nothing was
    deleted, and there is one permission to fix.

    So not a single row goes into the list. Instead two targets are counted as
    excluded from this comparison. Swallowing that as zero would be
    indistinguishable from a screen that is quiet because nothing happened.
    """
    before = _document(T1)
    after = _document(
        T2,
        state_errors={
            "describe_nat_gateways": client_error("AccessDenied", "DescribeNatGateways")
        },
    )
    report = apply_history(after, before)

    assert _kinds(after, "resource_removed") == []
    assert after["changes"] == []
    # Two NAT gateways, one alive and one that failed to be created, plus one
    # route towards them. That the route drops out alongside them is the defect
    # this check caught first. The route comes from the route table response and
    # that type was read, but `collect` cannot find the endpoint and so never
    # builds the line at all. Asking only about the response that built the edge
    # puts one route deletion in the list, which on screen reads as the path to
    # the NAT gateway having been cut.
    assert report.unread == 3, report
    assert report.excluded == 3 and report.out_of_scope == 0
    # Coverage already states what could not be read. All that is needed here
    # is that the history does not translate it into a change.
    denied = [
        row
        for row in after["coverage"]
        if row["layer"] == "describe" and row["status"] == "denied"
    ]
    assert [row["resource_type"] for row in denied] == ["nat_gateway"]


def test_removing_a_region_from_the_scope_is_not_a_deletion():
    """Accounts and Regions are added and removed from the screen, and silence
    where one was removed is not a deletion.

    What somebody experiences: yesterday they were looking at two Regions and
    today they took one off the list. If every resource in the removed Region
    floods the list as deleted, the one real change of the day is buried in it.
    """
    before = _document(T1, regions=(REGION, "eu-west-1"))
    after = _document(T2, regions=(REGION,))
    report = apply_history(after, before)

    assert after["changes"] == []
    assert report.out_of_scope == len(before["observations"]) - len(
        after["observations"]
    )
    assert report.out_of_scope > 0 and report.unread == 0


def test_a_resource_type_entering_or_leaving_the_scope_is_not_a_change():
    """The resource type is part of the scope too. This is a product defect
    reproduced from the bytes of a real account.

    The first way somebody experiences it: the day the engine is upgraded and
    starts supporting a new resource type. The previous observation document has
    no such type at all, so a plain difference records every resource that was
    already there as created. The screen says 30 resources were created and
    somebody goes looking for what was built overnight. Nothing was built.

    The second way: on the day a type is taken off the list, the same thing
    happens in reverse and 30 resources are reported deleted. It is exactly the
    same falsehood as removing a Region, which had a guard, while the type had
    none.

    What was measured: pushing one type each way through a real-account document
    of 192 targets recorded 30 rows each time, with all four exclusion counters
    at zero. So these are counted as out of scope, counted rather than dropped.
    """
    narrow = tuple(t for t in OBSERVED_RESOURCE_TYPES if t != "subnet")
    wide = _document(T1)
    thin = _document(T2, observed_types=narrow)
    assert "subnet" not in thin["scope"]["resource_types"]

    # First: a type was removed, which is not a deletion.
    removed = apply_history(copy.deepcopy(thin), wide)
    # Second: a type started being supported, which is not a creation. The same
    # pair, simply reversed.
    later = _document(T3)
    added = apply_history(later, thin)

    for label, document, report in (
        ("removed", thin, removed),
        ("newly supported", later, added),
    ):
        assert document["changes"] == [], (label, document["changes"])
        assert report.out_of_scope > 0, label
        # This is not a cell that could not be read. A lost permission and a
        # narrowed scope have different things to fix.
        assert report.unread == 0 and report.unknown_cell == 0, label

    # The control case: between two documents with the same types, a real
    # deletion is recorded as it is. Without this, an implementation that
    # swallows everything as out of scope would pass the assertions above.
    control = _document(T3, nat_gateways=_nat("available", only_first=True))
    report = apply_history(control, _document(T2, nat_gateways=_nat("available")))
    assert [row["kind"] for row in control["changes"]] == ["resource_removed"]
    assert report.out_of_scope == 0

    # The types neither side asks about, the security groups and network ACLs
    # that can only produce `none`, are not in the symmetric difference, so this
    # guard leaves them alone. Touching them would keep a new security group out
    # of the history for ever.
    assert "security_group" not in wide["scope"]["resource_types"]
    sg_gone = _document(T3, security_groups=[])
    sg_report = apply_history(sg_gone, _document(T2))
    # Two nodes disappear along with the three reference edges between them,
    # and all of them have to be recorded.
    assert {row["kind"] for row in sg_gone["changes"]} == {"resource_removed"}
    assert {row["target_id"].split("/")[0] for row in sg_gone["changes"]} == {
        "security_group",
        "security_group_reference|security_group",
    }, sg_gone["changes"]
    assert sg_report.out_of_scope == 0


# ----------------------------------------------------------- state changes


def test_a_gateway_going_down_is_recorded_with_both_ends():
    before = _document(T1, nat_gateways=_nat("available"))
    after = _document(T2, nat_gateways=_nat("failed"))
    apply_history(after, before)

    changed = _kinds(after, "status_changed")
    assert [(row["target_id"], row["from"], row["to"]) for row in changed] == [
        (NAT_ID, "up", "down")
    ]
    assert changed[0]["repeat_count"] == 1
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def _instances(state: str) -> list[dict]:
    """A variation on the EC2 dataset: change the state of the first instance,
    `app-1`, and nothing else.
    """
    data = copy.deepcopy(INSTANCE_RESERVATIONS)
    data[0]["Instances"][0]["State"] = {"Name": state}
    return data


APP_INSTANCE = INSTANCE_RESERVATIONS[0]["Instances"][0]["InstanceId"]


@pytest.mark.parametrize(
    "refreshed", [False, True], ids=["structure-stale", "structure-refreshed"]
)
@pytest.mark.parametrize(
    ("dataset", "alive", "dead", "target"),
    [
        (
            "instances",
            _instances("running"),
            _instances("terminated"),
            node_id("ec2_instance", ACCOUNT, REGION, APP_INSTANCE),
        ),
        ("nat_gateways", _nat("available"), _nat("deleted"), NAT_ID),
    ],
    ids=["ec2-terminated", "nat-deleted"],
)
def test_a_resource_that_reached_a_gone_state_is_removed_not_down(
    dataset, alive, dead, target, refreshed
):
    """The defect where an Auto Scaling group scaling in, or a stack being torn
    down, was recorded as healthy turning broken.

    What somebody experiences: an instance was terminated, the history calls it
    a fault, and somebody goes chasing an outage. Collection drops the whole
    node for a gone state, so the observation entry disappears and the history
    records a disappearance. The result has to be the same even when the
    structure document is still the older one, since Stack A runs on a longer
    schedule and the state tier sees it first.
    """
    alive_s = _structure(**{dataset: alive})
    dead_s = _structure(**{dataset: dead})
    before = _document(T1, topology=alive_s, **{dataset: alive})
    after = _document(T2, topology=dead_s if refreshed else alive_s, **{dataset: dead})
    report = apply_history(after, before, (dead_s, alive_s))

    assert _kinds(after, "status_changed") == []
    removed = {row["target_id"]: row for row in _kinds(after, "resource_removed")}
    assert target in removed, after["changes"]
    # That it existed until a moment ago is carried by the shape in `was`, so
    # there is no need to keep the node around for it.
    assert [n["id"] for n in removed[target]["was"]["nodes"]] == [target]
    assert report.unshaped == 0
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_turning_the_metric_layer_off_is_not_a_status_change():
    """The metric toggle is certain to turn off, four hours into a session by
    default.

    What somebody experiences: while the metrics are on to watch traffic, a NAT
    gateway shows as carrying traffic. When the toggle expires the same NAT
    gateway goes back to healthy with traffic unconfirmed. Traffic did not stop;
    asking about it stopped. Recording that in the list puts "traffic stopped
    everywhere" on the screen every four hours, and a real stop disappears into
    it.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2)  # a run with the metrics left off
    report = apply_history(after, before)

    assert {o["status"] for o in before["observations"]} >= {"live"}
    assert "live" not in {o["status"] for o in after["observations"]}
    assert after["changes"] == []
    # One `live → up` on the NAT gateway, plus the metric-layer-only connection
    # rows that disappear with the layer: the ALB placement line and the subnet
    # span of an accepted endpoint. An empty response means measured and zero,
    # so those rows exist only on the side where the layer was on.
    edges = [
        o
        for o in before["observations"]
        if o["target_id"].startswith(
            ("load_balancer_placement|", "endpoint_connection|")
        )
    ]
    kinds = {o["target_id"].split("|", 1)[0] for o in edges}
    assert kinds == {"load_balancer_placement", "endpoint_connection"}, (
        "premise: the run with the layer on has both the ALB placement line "
        "and the endpoint span row (empty_is_zero)"
    )
    assert report.layer_changed == 1 + len(edges), report
    assert report.unread == 0 and report.out_of_scope == 0


def test_flow_stopping_is_recorded_when_the_metric_layer_stayed_on():
    """The control case: with the metric layer left on throughout, traffic
    stopping really is a change.

    Without this check, the guard above would pass even if it suppressed every
    transition that involves `live`.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    report = apply_history(after, before)

    changed = _kinds(after, "status_changed")
    assert [(row["target_id"], row["from"], row["to"]) for row in changed] == [
        (NAT_ID, "live", "up")
    ]
    assert report.layer_changed == 0


# ---- The minute traffic actually stopped or started ------------------------
#
# What somebody experiences: the traffic indicator changes late, by up to
# about 21 minutes for a stop, which is by design. Even so, somebody reading
# the change history needs to know the minute it was cut to line it up against
# a cause such as a deployment or a security group change. So alongside `at`,
# the moment it was seen, the minute the metrics give is recorded separately.
# The i-th value of the fake CloudWatch, counting newest first, is the interval
# (i+1) minutes before the observation, so [8192, 4096] at T1 (00:00) means
# 23:59 and 23:58.


def _flow_row(document: dict[str, Any]) -> dict[str, Any]:
    (row,) = [r for r in _kinds(document, "status_changed") if r["target_id"] == NAT_ID]
    return row


def _nat_row(document: dict[str, Any]) -> dict[str, Any]:
    (row,) = [o for o in document["observations"] if o["target_id"] == NAT_ID]
    return row


def test_a_stop_records_the_last_flowing_minute_of_the_previous_run():
    """A stop is the last minute with traffic in the previous document. This
    document has no traffic, so it cannot hold that minute.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    assert _nat_row(before)["evidence"]["last_flow_at"] == "2026-08-31T23:59:00Z"
    assert _nat_row(before)["evidence"]["first_flow_at"] == "2026-08-31T23:58:00Z"
    # A row with no traffic has neither key. Absence is the one way of
    # expressing not knowing.
    assert "last_flow_at" not in _nat_row(after)["evidence"]
    apply_history(after, before)

    row = _flow_row(after)
    assert (row["from"], row["to"]) == ("live", "up")
    assert row["occurred_at"] == "2026-08-31T23:59:00Z"
    # The moment it was seen stays as it is: it is the sort key and the
    # identity of the row on screen.
    assert row["at"] == T2
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_a_start_records_the_first_flowing_minute_of_this_run():
    """A start is the first minute with traffic in this document, the earliest
    non-zero data point rather than the first in sort order.
    """
    before = _document(T1, series={NAT_FLOW: [0.0, 0.0]})
    # Newest first from 00:05: 00:04=0, 00:03=0, 00:02=5, 00:01=7, 00:00=0
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0, 5.0, 7.0, 0.0]})
    apply_history(after, before)

    row = _flow_row(after)
    assert (row["from"], row["to"]) == ("up", "live")
    assert row["occurred_at"] == "2026-09-01T00:01:00Z"
    assert _nat_row(after)["evidence"]["last_flow_at"] == "2026-09-01T00:02:00Z"
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


@pytest.mark.parametrize(
    ("after_at", "expected"),
    [
        # A ten-minute gap is the fifteen-minute window minus five minutes of
        # publishing delay, so there is no blind spot, boundary included.
        ("2026-09-01T00:10:00Z", "2026-08-31T23:59:00Z"),
        # One second over and there is a span between the two observations that
        # nobody saw, so no minute is recorded.
        ("2026-09-01T00:10:01Z", None),
        # A three-hour gap. Recording it would stamp a minute from three hours
        # ago as the moment traffic stopped.
        ("2026-09-01T03:00:00Z", None),
    ],
)
def test_the_minute_is_left_out_when_the_two_runs_leave_an_unseen_gap(
    after_at, expected
):
    """The transition itself stays and only the minute drops out. That it
    stopped is true; when is what is unknown.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(after_at, series={NAT_FLOW: [0.0, 0.0]})
    apply_history(after, before)

    row = _flow_row(after)
    assert (row["from"], row["to"]) == ("live", "up")
    assert row.get("occurred_at") == expected
    jsonschema.Draft202012Validator(SCHEMA).validate(after)


def test_an_older_previous_document_without_flow_minutes_still_diffs():
    """An older document with no such key, from an engine before this feature,
    is not rejected; only the minute is left empty.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    for obs in before["observations"]:
        (obs.get("evidence") or {}).pop("first_flow_at", None)
        (obs.get("evidence") or {}).pop("last_flow_at", None)
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    report = apply_history(after, before)

    assert report.warnings == ()
    assert "occurred_at" not in _flow_row(after)


def test_a_partial_row_does_not_get_a_minute():
    """For a row with one series missing, no traffic may only mean not having
    looked at enough. The transition is recorded, but the minute is not settled.
    """
    before = _document(T1, series={NAT_FLOW: [8192.0, 4096.0]})
    after = _document(T2, series={NAT_FLOW: [0.0, 0.0]})
    row = _nat_row(after)
    row["partial"] = True
    row["partial_reasons"] = [{"code": "api_error", "api": "cloudwatch:GetMetricData"}]
    apply_history(after, before)

    assert "occurred_at" not in _flow_row(after)


def test_a_minute_is_only_written_for_live_and_up():
    """Other transitions, such as `up → down`, have no minute from the metrics,
    and the schema forbids one on a configuration change as well.
    """
    before = _document(T1, nat_gateways=_nat("available"))
    after = _document(T2, nat_gateways=_nat("failed"))
    apply_history(after, before)
    assert after["changes"], "premise: this pair has to produce a change"
    assert all("occurred_at" not in row for row in after["changes"])

    added = {
        "at": T2,
        "kind": "resource_added",
        "target_id": NAT_ID,
        "target_kind": "node",
        "from": None,
        "to": None,
        "repeat_count": 1,
        "occurred_at": T1,
    }
    change = jsonschema.Draft202012Validator(
        {"$ref": "#/$defs/change", "$defs": SCHEMA["$defs"]}
    )
    assert list(change.iter_errors(added)), (
        "occurred_at must not be allowed on a configuration change"
    )
    # Control case: removing just that key has to pass, which shows the failure
    # above was not for some other reason.
    assert (
        list(change.iter_errors({k: v for k, v in added.items() if k != "occurred_at"}))
        == []
    )


# ------------------------------------------------------------------ two caps


def test_flapping_folds_into_one_row_only_at_the_threshold():
    """The collapse happens only at the threshold: three times stay as they
    are, four become one row.

    What somebody experiences: when an attachment keeps dropping and coming
    back, the list fills up with that one target. So four or more collapse into
    a single row saying it repeated four times. Two or three do not collapse,
    because that is the range where somebody reads the individual moments to
    work out when it started.
    """
    assert DEFAULT_FLAP_COLLAPSE_THRESHOLD == 4
    flap = [
        (T1, {"nat_gateways": _nat("available")}),
        (T2, {"nat_gateways": _nat("failed")}),
        (T3, {"nat_gateways": _nat("available")}),
        (T4, {"nat_gateways": _nat("failed")}),
        (T5, {"nat_gateways": _nat("available")}),
    ]
    _, two, three, four, five = _chain(flap)

    # Up to three they stay as individual rows. This is what tells them apart:
    # defeat the threshold and this becomes one row.
    assert [row["repeat_count"] for row in three["changes"]] == [1, 1]
    assert [row["repeat_count"] for row in four["changes"]] == [1, 1, 1]
    # On the fourth it collapses into one row, keeping the before and after of
    # the last transition, as in repeated N times, last one down to up.
    assert len(five["changes"]) == 1, five["changes"]
    assert five["changes"][0]["repeat_count"] == 4
    assert (five["changes"][0]["at"], five["changes"][0]["from"]) == (T5, "down")
    assert five["changes"][0]["to"] == "up"
    assert len(two["changes"]) == 1
    jsonschema.Draft202012Validator(SCHEMA).validate(five)
    # The start of the repetition is kept too. With four flips, an even number,
    # the last `from` (down) is not the state before the repetition (up).
    # What somebody experiences: clicking a moment before the repetition has to
    # show that NAT gateway as healthy. Working back from the last `from` would
    # show it as broken.
    assert (five["changes"][0]["first_at"], five["changes"][0]["first_from"]) == (
        T2,
        "up",
    )
    # A row that was not collapsed carries no start, because `at` and `from`
    # already are the start. One fact must not have two byte representations.
    assert all(
        "first_at" not in row and "first_from" not in row for row in four["changes"]
    )


T6 = "2026-09-01T00:25:00Z"


def _flap_to(last_state: str) -> list[dict[str, Any]]:
    """Four flips from T1 to T5, which collapses, then one more at T6: the case
    where an already collapsed row merges with a new one.
    """
    return _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, {"nat_gateways": _nat("failed")}),
            (T3, {"nat_gateways": _nat("available")}),
            (T4, {"nat_gateways": _nat("failed")}),
            (T5, {"nat_gateways": _nat("available")}),
            (T6, {"nat_gateways": _nat(last_state)}),
        ]
    )


def test_a_folded_row_keeps_its_start_when_it_folds_again():
    """When a collapsed row collapses again on the next round, the start stays
    the original one and only the before and after of the last round change.
    """
    six = _flap_to("failed")[-1]
    (row,) = six["changes"]
    assert row["repeat_count"] == 5 and (row["at"], row["from"], row["to"]) == (
        T6,
        "up",
        "down",
    )
    assert (row["first_at"], row["first_from"]) == (T2, "up")
    jsonschema.Draft202012Validator(SCHEMA).validate(six)


def test_a_fold_from_before_the_start_was_recorded_does_not_invent_one():
    """Merging with a row collapsed by an older version, which has no start,
    leaves the start unknown, so neither key is written.

    What somebody experiences: an invented start would let the screen assert
    that the moment before it was healthy. In fact there may have been flips
    before that too, which the older row discarded. When it is unknown, the
    screen has to say the state at that moment is unknown.
    """
    five = _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, {"nat_gateways": _nat("failed")}),
            (T3, {"nat_gateways": _nat("available")}),
            (T4, {"nat_gateways": _nat("failed")}),
            (T5, {"nat_gateways": _nat("available")}),
        ]
    )[-1]
    old = copy.deepcopy(five)
    for row in old["changes"]:
        del row["first_at"], row["first_from"]  # the older collapsed-row shape
    six = _document(T6, nat_gateways=_nat("failed"))
    apply_history(six, old)
    (row,) = six["changes"]
    assert row["repeat_count"] == 5
    assert "first_at" not in row and "first_from" not in row, row
    jsonschema.Draft202012Validator(SCHEMA).validate(six)


def _nat_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in document["changes"]
        if row["target_id"] == NAT_ID and row["kind"] == "status_changed"
    ]


def _plus(at: str, **delta: float) -> str:
    return (_at(at) + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _flap_four(start: str) -> list[tuple[str, dict[str, Any]]]:
    """Four flips at five-minute intervals after `start`: failed, available,
    failed, available.
    """
    states = ("failed", "available", "failed", "available")
    return [
        (_plus(start, minutes=5 * (i + 1)), {"nat_gateways": _nat(state)})
        for i, state in enumerate(states)
    ]


def _assert_contract(document: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert ORACLE.fold_start_after_last(document) == []
    assert ORACLE.collapsed_below_threshold(document) == []


def test_a_recreated_resource_starts_a_new_repetition():
    """For a target that was deleted and then created again under the same id,
    the new repetition does not collapse into the row from its earlier life.

    What somebody ran into: an ALB deleted days earlier was recreated under the
    same name, and the name is the id. The new repetition of traffic starting
    and stopping merged with the row from the first life, which an older version
    had collapsed without a start, and became a repetition of eight, the first
    life plus the second. With the start lost, comparing against an earlier
    moment reported the ALB as being in an unknown state, when in fact it was
    healthy for the first few minutes after being recreated.
    The older row is within the same day, down to the minute, so the gap rule
    does not cut it. Only the lifetime boundary does.
    """
    nat = NAT_GATEWAYS[0]["NatGatewayId"]
    gone = {"nat_gateways": [n for n in _nat("available") if n["NatGatewayId"] != nat]}
    first = _chain([(T1, {"nat_gateways": _nat("available")}), *_flap_four(T1)])[-1]
    (old,) = _nat_rows(first)
    assert old["repeat_count"] == 4 and old["first_at"] == T2
    legacy = copy.deepcopy(first)
    # The shape of a row collapsed by an older version, with no start
    for row in _nat_rows(legacy):
        del row["first_at"], row["first_from"]
    removed_at, back_at = _plus(T5, minutes=5), _plus(T5, minutes=10)
    later = _chain(
        [
            (removed_at, gone),
            (back_at, {"nat_gateways": _nat("available")}),
            *_flap_four(back_at),
        ],
        previous=legacy,
    )
    last = later[-1]
    lifetime = [
        (row["kind"], row["at"])
        for row in last["changes"]
        if row["target_id"] == NAT_ID and row["kind"] != "status_changed"
    ]
    assert lifetime == [("resource_removed", removed_at), ("resource_added", back_at)]
    rows = _nat_rows(last)
    # The row from the earlier life stays as written, with no start, while the
    # repetition in the new life has a start of its own.
    assert [(row["at"], row["repeat_count"]) for row in rows] == [
        (T5, 4),
        (_plus(back_at, minutes=20), 4),
    ], rows
    assert "first_at" not in rows[0] and "first_from" not in rows[0]
    assert (rows[1]["first_at"], rows[1]["first_from"]) == (
        _plus(back_at, minutes=5),
        "up",
    )
    _assert_contract(last)


def test_a_quiet_day_ends_a_repetition():
    """A new flip on a target that was quiet for more than a day does not
    collapse into the earlier repetition, while a pause of a few hours still
    collapses into it.

    What somebody experiences: a NAT gateway that wobbled for a few minutes on
    Monday morning and wobbles again on Wednesday evening leaves two rows in the
    list. Merging them would put the two days between inside one repetition, so
    clicking a moment on Tuesday would report an unknown state when in fact it
    was healthy throughout.
    The control case: when the batch stops for seven hours and comes back, the
    flips that follow are the same repetition. Cutting there would flood the
    list.
    """
    first = _chain([(T1, {"nat_gateways": _nat("available")}), *_flap_four(T1)])
    quiet = [
        (_plus(T5, hours=12 * (i + 1)), {"nat_gateways": _nat("available")})
        for i in range(3)
    ]
    later = _chain([*quiet, *_flap_four(quiet[-1][0])], previous=first[-1])[-1]
    rows = _nat_rows(later)
    assert [(row["repeat_count"], row["first_at"]) for row in rows] == [
        (4, T2),
        (4, _plus(quiet[-1][0], minutes=5)),
    ], rows
    _assert_contract(later)

    # Control case: one more flip after a seven-hour gap collapses into the same
    # repetition, keeping the original start.
    paused = _chain(
        [(_plus(T5, hours=7), {"nat_gateways": _nat("failed")})], previous=first[-1]
    )[-1]
    (row,) = _nat_rows(paused)
    assert (row["repeat_count"], row["first_at"], row["first_from"]) == (5, T2, "up")
    _assert_contract(paused)


def test_the_retention_window_drops_old_rows_and_says_so():
    """`truncated` states a different fact from `baseline_at`.

    What somebody experiences: changes older than seven days drop off the list.
    If the screen does not say that there is more before this, somebody reads
    the first row of the list as the beginning. The baseline moment still points
    at when the record started, and that it was truncated is recorded
    separately.
    """
    gone = {"nat_gateways": _nat("available", only_first=True)}
    _, recent, late = _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, gone),
            (LATE, gone),
        ]
    )

    assert len(recent["changes"]) == 1 and recent["history"]["truncated"] is False
    assert late["changes"] == []  # dropped out of the window, not "no changes"
    assert late["history"]["truncated"] is True
    # Measure the relation in both directions.
    for document in (recent, late):
        history = document["history"]
        horizon = _at(document["run"]["observed_at"]) - timedelta(
            seconds=history["retention_seconds"]
        )
        assert history["truncated"] == (_at(history["baseline_at"]) < horizon)
    # The baseline moment keeps pointing at the first observation, regardless
    # of truncation.
    assert late["history"]["baseline_at"] == T1
    assert late["history"]["retention_seconds"] == DEFAULT_RETENTION_SECONDS


def test_the_change_cap_keeps_the_newest_rows_and_says_so():
    """The cap on the number of entries, 5,000, is also a fact about something
    discarded.

    What somebody experiences: in an organization with a great many changes, a
    list that exceeds the cap loses its oldest rows first. If the screen does
    not say that there is more before this, even though it happened within the
    seven days, somebody reads the first remaining row as the beginning.
    The discarded rows were inside the window, so they are absent on the next
    round too, which is why the truncation mark is carried over.
    """

    def chain(cap: int) -> list[tuple[dict[str, Any], Any]]:
        out: list[tuple[dict[str, Any], Any]] = []
        previous: dict[str, Any] | None = None
        for at, state in (
            (T1, "available"),
            (T2, "failed"),
            (T3, "available"),
            (T4, "failed"),
            (
                T5,
                "failed",
            ),  # nothing changed, so there is nothing to discard this time;
            # this is the round that tells carrying-over apart
        ):
            document = _document(at, nat_gateways=_nat(state))
            document["history"]["max_changes"] = cap
            out.append((document, apply_history(document, previous)))
            previous = document
        return out

    capped = chain(2)
    four, four_report = capped[3]
    five, five_report = capped[4]
    # The one row over the cap is the oldest, T2, leaving T3 and T4.
    assert [row["at"] for row in four["changes"]] == [T3, T4]
    assert four_report.capped == 1 and four_report.dropped == 0
    assert four["history"]["truncated"] is True
    # The record started inside the window, so this truncation cannot be
    # explained by the first cause, the retention window.
    horizon = _at(four["run"]["observed_at"]) - timedelta(
        seconds=four["history"]["retention_seconds"]
    )
    assert _at(four["history"]["baseline_at"]) >= horizon
    # Nothing was discarded this time, yet T2 is still absent from the list, so
    # the truncation mark is carried over.
    assert five_report.capped == 0 and len(five["changes"]) == 2
    assert five["history"]["truncated"] is True
    jsonschema.Draft202012Validator(SCHEMA).validate(five)

    # Control case: under the default cap the same three rows all survive and
    # there is no truncation. That is what tells the two apart.
    control = chain(DEFAULT_MAX_CHANGES)
    assert [row["at"] for row in control[4][0]["changes"]] == [T2, T3, T4]
    assert control[4][0]["history"]["truncated"] is False
    assert control[4][1].capped == 0


# -------------------------------------------- resolution and rejection


def test_resolution_reports_the_coarsest_gap_it_actually_saw():
    """A screen that says it runs every five minutes is making a false
    statement if the real interval was three hours.

    What somebody experiences: the note at the bottom of the screen, that
    changes which came and went faster than this interval are not visible, has
    to state the real interval. On the day the batch stopped for three hours and
    came back, a number still reading 300 seconds tells somebody that nothing
    happened during those three hours.
    """
    before = _document(T1)
    after = _document("2026-09-01T03:00:00Z")
    apply_history(after, before)
    assert after["history"]["resolution_seconds"] == 10800

    # Once the list has become coarse it does not become fine again, because the
    # list describes the span from the baseline moment onwards.
    later = _document("2026-09-01T03:05:00Z")
    apply_history(later, after)
    assert later["history"]["resolution_seconds"] == 10800
    # An interval shorter than the requested schedule is reported as the
    # requested schedule. Claiming finer resolution is the overstatement.
    quick = _document("2026-09-01T00:00:30Z")
    apply_history(quick, before)
    assert quick["history"]["resolution_seconds"] == DEFAULT_RESOLUTION_SECONDS


def test_an_unusable_previous_document_restarts_the_baseline_instead_of_failing():
    """Failing to carry the history over is not an error. This observation
    remains valid as it is.

    What somebody experiences: handing it the same file twice, or files in
    reverse order, or an older file with a different contract, says that the
    history starts again from this observation, and the state screen appears as
    usual. Killing the run here would stop the state screen along with it.
    """
    current = _document(T1)
    future = _document(T2)

    report = apply_history(current, future)
    assert report.diffed is False and report.warnings
    assert current["changes"] == []
    assert current["history"]["baseline_at"] == T1

    same = _document(T2)
    # Taking itself as the baseline is rejected too; that would report no
    # changes on every run.
    assert apply_history(same, _document(T2)).diffed is False

    stale = _document(T1)
    broken = _document(T1)
    broken["schema_version"] = "9.0.0"
    assert apply_history(stale, broken).diffed is False
    missing = _document(T1)
    del missing["changes"]
    assert apply_history(_document(T2), missing).diffed is False
    # The warning text carries neither a target id nor an account id.
    for warning in apply_history(_document(T2), missing).warnings:
        assert ACCOUNT not in warning


def _drifted(mutate) -> dict[str, Any]:
    previous = _document(T1, nat_gateways=_nat("available"))
    previous["changes"] = [
        {
            "at": T1,
            "kind": "status_changed",
            "target_id": NAT_ID,
            "target_kind": "node",
            "from": "up",
            "to": "down",
            "repeat_count": 1,
        }
    ]
    mutate(previous)
    return previous


def test_a_same_major_document_with_a_drifted_shape_also_restarts_the_baseline():
    """Rejecting on the major version alone keeps only half the promise.

    A document with a matching major version in `schema_version` is carried
    over, but the code that actually reads it indexes nested keys directly, as
    in `row["at"]` and `scope["resource_types"]`. So a file with the same major
    version but a shifted shape raises `KeyError`, and `nettopo` died with a
    traceback and a non-zero exit code. Failing to carry the history over has to
    be a warning rather than an error; dying here throws away that day's
    observation along with it.

    This is not hypothetical. One output file carried the same `1.0.0` with only
    two keys in `scope` while another `1.0.0` output had three. Our own writer
    has already changed the shape within one major version, and the only reason
    nothing died then is that the major version went up afterwards.

    What somebody experiences: handing it an older, or hand-edited, file through
    `--previous` still succeeds, and all that appears is a note that the history
    starts again from this observation. The state screen does not stop with a
    Python error.
    """
    drifts = {
        "missing scope key": lambda d: d["scope"].pop("resource_types"),
        "wrong scope type": lambda d: d["scope"].update(regions=REGION),
        "missing coverage row key": lambda d: d["coverage"][0].pop("account_id"),
        "missing observations row key": lambda d: d["observations"][0].pop(
            "target_kind"
        ),
        "observations row is not an object": lambda d: d["observations"].__setitem__(
            0, "x"
        ),
        "missing at on a changes row": lambda d: d["changes"][0].pop("at"),
        "broken at on a changes row": lambda d: d["changes"][0].update(at="yesterday"),
        "missing repeat_count on a changes row": lambda d: d["changes"][0].pop(
            "repeat_count"
        ),
        "history is not an object": lambda d: d.update(history=[]),
        "broken baseline_at": lambda d: d["history"].update(baseline_at="2026-09-00"),
        # `_Run` reads whether the flow log reader was on from `layers`, and
        # its absence used to raise `KeyError`.
        "missing layers": lambda d: d.pop("layers"),
        "layers is not an object": lambda d: d.update(layers=[]),
    }
    for label, mutate in drifts.items():
        current = _document(T2, nat_gateways=_nat("failed", extra=True))
        report = apply_history(current, _drifted(mutate))  # does not die
        assert report.diffed is False, label
        assert report.warnings, label
        assert ACCOUNT not in " ".join(report.warnings), label
        # Control case: with the shape left alone, those two documents really do
        # produce a change. Without this, an implementation that rejects
        # everything would pass the assertions above.
        assert current["changes"] == [], label
        assert current["history"]["baseline_at"] == T2, label

    intact = _document(T2, nat_gateways=_nat("failed", extra=True))
    control = apply_history(intact, _drifted(lambda _d: None))
    assert control.diffed is True and control.new_rows >= 1
    assert intact["history"]["baseline_at"] == T1


def test_a_drifted_value_type_in_the_previous_document_also_restarts_the_baseline():
    """A check that asks only whether a key is present stops only half of it.

    Given a file with the same major version and every key in place but the
    types of the values shifted, this module failed one step at a time: a string
    `repeat_count` broke the `sum` in `_collapse`; a non-string `kind` or
    `target_id` broke the final `sorted`; an unhashable element in `coverage` or
    `scope` broke `set()`; and a non-numeric string `resolution_seconds` broke
    `int()`. Every one gave a traceback and a non-zero exit code, which is the
    opposite of warning and starting again from this observation, and throws
    away that day's observation with it.

    `resolution_seconds="9000"` does not even die: `int()` accepts it and it
    passes quietly. So what that line prevents is not a crash but carrying over
    bytes from outside the contract.
    """
    drifts = {
        "repeat_count is a string": lambda d: d["changes"][0].update(repeat_count="2"),
        "repeat_count is a bool": lambda d: d["changes"][0].update(repeat_count=True),
        "changes kind is an integer": lambda d: d["changes"][0].update(kind=5),
        "changes target_id is None": lambda d: d["changes"][0].update(target_id=None),
        "observations target_id is an integer": lambda d: d["observations"][0].update(
            target_id=7
        ),
        "coverage region is an array": lambda d: d["coverage"][0].update(
            region=[REGION]
        ),
        "scope element is an object": lambda d: d["scope"].update(
            account_ids=[{"id": ACCOUNT}]
        ),
        "scope element is an integer": lambda d: d["scope"].update(regions=[1]),
        "resolution_seconds is a string": lambda d: d["history"].update(
            resolution_seconds="abc"
        ),
        "resolution_seconds is a numeric string": lambda d: d["history"].update(
            resolution_seconds="9000"
        ),
    }
    for label, mutate in drifts.items():
        current = _document(T2, nat_gateways=_nat("failed", extra=True))
        report = apply_history(current, _drifted(mutate))  # does not die
        assert report.diffed is False, label
        assert report.warnings, label
        assert ACCOUNT not in " ".join(report.warnings), label
        assert current["changes"] == [], label
        assert current["history"]["baseline_at"] == T2, label

    # Control case: with the types left alone, the same pair really does produce
    # a change, which rules out an implementation that rejects everything.
    intact = _document(T2, nat_gateways=_nat("failed", extra=True))
    control = apply_history(intact, _drifted(lambda _d: None))
    assert control.diffed is True and control.new_rows >= 1


# ------------------------------------- determinism and cell mapping


def test_the_same_pair_of_documents_produces_the_same_bytes():
    """The moment is read only from the input, so two runs give the same bytes."""
    before = _document(T1, nat_gateways=_nat("available"))

    def run() -> str:
        after = _document(T2, nat_gateways=_nat("failed", extra=True))
        apply_history(after, copy.deepcopy(before))
        return json.dumps(after, sort_keys=False, ensure_ascii=False)

    assert run() == run()


def test_every_observed_target_maps_to_a_cell_inside_the_scope():
    """Even one target with an unknown cell is a defect on our side, because
    that target quietly drops out of the history.

    Adding a new resource type or edge type without updating
    `EDGE_SOURCE_RESOURCE` is caught here.
    """
    document = _document(T1)
    pairs = {
        (account, region)
        for account in document["scope"]["account_ids"]
        for region in document["scope"]["regions"]
    }
    unmapped = [
        observation["target_id"]
        for observation in document["observations"]
        if (cells := target_cells(observation["target_id"], observation["target_kind"]))
        is None
        or any(
            # A global cell, such as a Direct Connect gateway, sits outside the
            # Region axis; only its account has to be in scope.
            cell[0] not in document["scope"]["account_ids"]
            if cell[1] == GLOBAL
            else (cell[0], cell[1]) not in pairs
            for cell in cells
        )
    ]
    assert unmapped == [], unmapped
    # Control case: a global node really does pass through this check. Without
    # one, the branch above is unmeasured.
    assert any(
        (cells := target_cells(o["target_id"], o["target_kind"])) is not None
        and cells[0][1] == GLOBAL
        for o in document["observations"]
    )
    # An edge requires the cells of its endpoints as well: a line exists only
    # when both end nodes were collected this time.
    route = next(
        o["target_id"]
        for o in document["observations"]
        if o["target_id"].startswith("route|") and o["target_id"].endswith(EGRESS_NAT)
    )
    assert target_cells(route, "edge") == (
        (ACCOUNT, REGION, "route_table"),
        (ACCOUNT, REGION, "nat_gateway"),
    )

    # It has to be zero in a real comparison too. Above is the static mapping;
    # below is the difference path.
    report = apply_history(_document(T2), document)
    assert report.unknown_cell == 0 and report.excluded == 0


# ---- Being deleted ---------------------------------------------------------
# What somebody ran into: deleting a VPC interface endpoint on purpose left a
# red "broken, healthy turning faulty" in the history before the disappearance.
# What is wanted: one disappearance row when it drops off the list. For a
# resource that was already faulty, that earlier row is not removed.

DELETED_ENDPOINT = "vpce-0bbb2222cccc8888"  # an interface endpoint, ssm
ENDPOINT_ID = node_id("vpc_endpoint", ACCOUNT, REGION, DELETED_ENDPOINT)


def _endpoints(state: str | None) -> dict[str, Any]:
    """Change only the state of the interface endpoint. `None` means it dropped
    off the list, because AWS finished deleting it.
    """
    data = copy.deepcopy(VPC_ENDPOINTS)
    if state is None:
        data = [e for e in data if e["VpcEndpointId"] != DELETED_ENDPOINT]
    else:
        for e in data:
            if e["VpcEndpointId"] == DELETED_ENDPOINT:
                e["State"] = state
    return {"vpc_endpoints": data}


def _rows_of(document: dict[str, Any], target_id: str) -> list[tuple[Any, ...]]:
    return [
        (r["kind"], r["from"], r["to"])
        for r in document["changes"]
        if r["target_id"] == target_id
    ]


def test_a_deliberate_delete_leaves_exactly_one_removed_row_and_no_cut():
    """available, then deleting, then off the list: exactly one disappearance
    row, no broken rows turning down, and none turning unobservable.
    """
    first, deleting, gone = _chain(
        [
            (T1, _endpoints("available")),
            (T2, _endpoints("deleting")),
            (T3, _endpoints(None)),
        ]
    )
    row = next(o for o in deleting["observations"] if o["target_id"] == ENDPOINT_ID)
    # While being deleted the card is still there and the verdict is that it is
    # being deleted, not that it is faulty.
    assert (row["status"], row.get("deleting")) == ("none", True)
    assert _rows_of(deleting, ENDPOINT_ID) == []
    assert _rows_of(gone, ENDPOINT_ID) == [("resource_removed", None, None)]
    assert not [r for r in gone["changes"] if r["to"] == "down"], gone["changes"]
    assert first["changes"] == []
    for document in (deleting, gone):
        jsonschema.Draft202012Validator(SCHEMA).validate(document)


def test_the_deletion_transition_is_counted_not_silently_dropped():
    """A transition that is not recorded is still counted. Without the count,
    a quiet history and a filtered-out deletion look the same.
    """
    before = _document(T1, **_endpoints("available"))
    after = _document(T2, **_endpoints("deleting"))
    report = apply_history(after, before)
    assert report.deleting == 1
    assert report.new_rows == 0
    assert report.excluded == report.deleting


def test_an_earlier_down_row_survives_the_deletion():
    """Deleting a resource that was already faulty: the earlier healthy-to-
    faulty row stays, the deletion adds no new transition, and a disappearance
    comes at the end.
    """
    _first, failed, deleting, gone = _chain(
        [
            (T1, _endpoints("available")),
            (T2, _endpoints("failed")),
            (T3, _endpoints("deleting")),
            (T4, _endpoints(None)),
        ]
    )
    assert _rows_of(failed, ENDPOINT_ID) == [("status_changed", "up", "down")]
    # The deletion produced no new row; only the carried-over faulty row is
    # there.
    assert _rows_of(deleting, ENDPOINT_ID) == [("status_changed", "up", "down")]
    assert _rows_of(gone, ENDPOINT_ID) == [
        ("status_changed", "up", "down"),
        ("resource_removed", None, None),
    ]


def test_a_delete_that_fails_is_still_recorded():
    """Control case: being deleted and then failing is a fault. That transition
    was not produced by the deletion, so it is recorded.
    """
    _first, _deleting, failed = _chain(
        [
            (T1, _endpoints("available")),
            (T2, _endpoints("deleting")),
            (T3, _endpoints("failed")),
        ]
    )
    assert _rows_of(failed, ENDPOINT_ID) == [("status_changed", "none", "down")]


def test_a_nat_delete_reads_the_same_way():
    """A NAT gateway behaves the same way, since `deleted` drops the whole node
    through `GONE_STATES`. The fix was not specific to endpoints.
    """
    _first, deleting, gone = _chain(
        [
            (T1, {"nat_gateways": _nat("available")}),
            (T2, {"nat_gateways": _nat("deleting")}),
            (T3, {"nat_gateways": _nat("deleted")}),
        ]
    )
    target = node_id("nat_gateway", ACCOUNT, REGION, NAT_GATEWAYS[0]["NatGatewayId"])
    assert _rows_of(deleting, target) == []
    assert _rows_of(gone, target) == [("resource_removed", None, None)]


# ---- Appearance and disappearance of cards that have no state --------------

# Two fixtures split the eight types between them: `fixtures/relations.json`
# holds four and `fixtures/lb-targets.json` the other four.
PRESENCE_FIXTURES = {
    "relations": (
        "api_gateway",
        "global_accelerator",
        "elastic_ip",
        "rds_db_proxy",
        # Cloud WAN core networks and VPC Lattice service networks and
        # services, from axes ⑥, ⑦ and ⑧ of `rel_world`.
        "core_network",
        "lattice_service_network",
        "lattice_service",
        # WAF web ACLs (2.12.0), from axis ⑨ of `rel_world`, in both the
        # regional and the CloudFront scope.
        "waf_web_acl",
    ),
    "lb-targets": ("cloudfront", "route53_hosted_zone", "lambda_function", "s3_bucket"),
}
# The four types named in the issue. If the appearance or disappearance of any
# of them goes missing, the check below fails by name.
PRESENCE_MUST = ("api_gateway", "global_accelerator", "elastic_ip", "cloudfront")
P1 = "2026-01-02T03:09:05Z"
P2 = "2026-01-02T03:14:05Z"
P3 = "2026-01-02T03:19:05Z"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))


def _without(
    structure: dict[str, Any], kinds: tuple[str, ...], **coverage: str
) -> dict[str, Any]:
    """A structure with the cards of that type, and the lines touching them,
    taken out: the structure collection after the test stack was deleted.
    `coverage` maps a type to the status to change it to.
    """
    out = copy.deepcopy(structure)
    gone = {n["id"] for n in out["nodes"] if n["type"] in kinds}
    out["nodes"] = [n for n in out["nodes"] if n["id"] not in gone]
    out["edges"] = [
        e for e in out["edges"] if e["source"] not in gone and e["target"] not in gone
    ]
    for row in out["coverage"]:
        if row["resource_type"] in coverage:
            row["status"], row["item_count"] = coverage[row["resource_type"]], None
            row["reason"] = {"code": "AccessDenied", "api": None, "message": "test"}
    out["run"]["content_digest"] = content_digest(out)
    return out


def _bare(structure: dict[str, Any], at: str) -> dict[str, Any]:
    """The observation document paired with that structure, with no state
    entries. A card that has no state has no observation entry at all, which is
    the premise of this axis.
    """
    run = structure["run"]
    accounts = structure["scope"]["account_ids"]
    regions = structure["scope"]["regions"]
    coverage = []
    for account in accounts:
        for region in regions:
            for kind in sorted(OBSERVED_RESOURCE_TYPES):
                for layer, status, count in (
                    ("describe", "ok", 0),
                    ("metric", "skipped", None),
                ):
                    coverage.append(
                        {
                            "account_id": account,
                            "region": region,
                            "resource_type": kind,
                            "layer": layer,
                            "status": status,
                            "reason": None,
                            "item_count": count,
                        }
                    )
    return {
        "schema_version": SCHEMA["properties"]["schema_version"]["const"],
        "run": {
            "observed_at": at,
            "tool_version": "test",
            "topology_collected_at": run["collected_at"],
            "topology_content_digest": run["content_digest"],
        },
        "scope": {
            "account_ids": list(accounts),
            "regions": list(regions),
            "resource_types": sorted(OBSERVED_RESOURCE_TYPES),
        },
        "layers": {"describe": True, "metric": False},
        "sources": [],
        "observations": [],
        "coverage": coverage,
        "history": {
            "baseline_at": at,
            "resolution_seconds": DEFAULT_RESOLUTION_SECONDS,
            "retention_seconds": DEFAULT_RETENTION_SECONDS,
            "truncated": False,
            "flap_collapse_threshold": DEFAULT_FLAP_COLLAPSE_THRESHOLD,
            "max_changes": DEFAULT_MAX_CHANGES,
        },
        "changes": [],
    }


def _deleted_then_recreated(name: str) -> tuple[Any, ...]:
    """Three observations, with the test stack deleted and then recreated,
    following the order of the QA run, with one structure collection in between
    each.
    """
    full = _fixture(name)
    gone = _without(full, PRESENCE_FIXTURES[name])
    first, second, third = _bare(full, P1), _bare(gone, P2), _bare(full, P3)
    removed = apply_history(second, first, (gone, full))
    added = apply_history(third, second, (full, gone))
    return full, gone, second, third, removed, added


@pytest.mark.parametrize("name", sorted(PRESENCE_FIXTURES))
def test_stateless_cards_leave_removed_and_added_rows_from_the_two_structures(name):
    """What somebody experiences: deleting an API Gateway or a Global
    Accelerator makes the card disappear from the screen. A disappearance row
    stays in the history as well, and clicking it draws the shape it had at the
    time, the card with the lines touching it. Recreating it leaves an
    appearance row.
    """
    full, gone, second, third, removed, added = _deleted_then_recreated(name)
    want = sorted(
        n["id"] for n in full["nodes"] if n["type"] in PRESENCE_FIXTURES[name]
    )
    kinds = {i.split("/")[0] for i in want}
    assert kinds == set(PRESENCE_FIXTURES[name]), (
        f"premise: the fixture has no such type; {kinds}"
    )

    gone_rows = _kinds(second, "resource_removed")
    assert sorted(r["target_id"] for r in gone_rows) == want
    for row in gone_rows:
        assert row["at"] == P2 and row["target_kind"] == "node"
        node = next(n for n in full["nodes"] if n["id"] == row["target_id"])
        assert row["was"]["nodes"] == [node]
        assert row["was"]["edges"] == _incident(full, row["target_id"])
    assert removed.presence_rows == len(want) and removed.shaped == len(want)
    assert removed.excluded == 0 and removed.warnings == ()

    come_rows = _kinds(third, "resource_added")
    assert sorted(r["target_id"] for r in come_rows) == want
    assert all(r["at"] == P3 and "was" not in r for r in come_rows)
    assert added.presence_rows == len(want) and added.excluded == 0
    # There is no state, so not one observation entry is added. The
    # unobservable pill and the question mark on the card stay as they are.
    assert second["observations"] == [] and third["observations"] == []
    for document, structure in ((second, gone), (third, full)):
        jsonschema.Draft202012Validator(SCHEMA).validate(document)
        assert ORACLE.dangling_changes(document, structure) == []
        assert not ORACLE.unsorted(ORACLE.change_sort_keys(document))


@pytest.mark.parametrize("kind", PRESENCE_MUST)
def test_the_four_named_kinds_get_both_a_removed_and_an_added_row(kind):
    """The four types named in the issue, each needing at least one appearance
    and one disappearance. Each type is measured separately, so a single missing
    one fails by name.
    """
    name = next(n for n, kinds in PRESENCE_FIXTURES.items() if kind in kinds)
    _full, _gone, second, third, _r, _a = _deleted_then_recreated(name)

    def of(document: dict[str, Any], change: str) -> list[dict[str, Any]]:
        return [
            r for r in _kinds(document, change) if r["target_id"].startswith(kind + "/")
        ]

    assert of(second, "resource_removed"), f"no disappearance row for {kind}"
    assert of(third, "resource_added"), f"no appearance row for {kind}"


def test_presence_rows_are_byte_identical_across_runs():
    """The same two observations with the same two structures give the same
    bytes.
    """

    def run() -> str:
        _f, _g, second, third, _r, _a = _deleted_then_recreated("relations")
        return json.dumps([second, third], ensure_ascii=False)

    assert run() == run()


def test_the_same_structure_never_opens_the_structure_documents():
    """The ordinary five-minute round: when two observations rest on the same
    structure, no card can have changed. Even with no structure supplied there
    are no warnings and no rows.
    """
    full = _fixture("relations")
    first, second = _bare(full, P1), _bare(full, P2)
    report = apply_history(second, first, ())
    assert second["changes"] == [] and report.presence_rows == 0
    assert report.presence_unpaired is False and report.warnings == ()


def test_a_kind_denied_in_the_new_structure_is_unread_not_removed():
    """A structure where the API Gateway could not be read because the
    permission is gone. A missing card is not a disappearance; it is counted as
    a cell that could not be read.
    Control case: in the same structure, the disappearance of the Global
    Accelerator, which was read, is recorded as it is. That catches an
    implementation which suppresses everything.
    """
    full = _fixture("relations")
    gone = _without(full, ("api_gateway", "global_accelerator"), api_gateway="denied")
    first, second = _bare(full, P1), _bare(gone, P2)
    report = apply_history(second, first, (gone, full))
    removed = {r["target_id"].split("/")[0] for r in _kinds(second, "resource_removed")}
    assert removed == {"global_accelerator"}
    n_api = sum(1 for n in full["nodes"] if n["type"] == "api_gateway")
    assert report.unread == n_api and n_api >= 1


def test_a_kind_the_old_structure_never_asked_for_is_not_added():
    """The day the engine starts supporting a new type: the older structure
    never even asked about Global Accelerator, so an accelerator that was
    already there is not an appearance.
    """
    full = _fixture("relations")
    old = _without(full, ("global_accelerator",))
    old["scope"]["resource_types"].remove("global_accelerator")
    old["coverage"] = [
        c for c in old["coverage"] if c["resource_type"] != "global_accelerator"
    ]
    old["run"]["content_digest"] = content_digest(old)
    first, second = _bare(old, P1), _bare(full, P2)
    report = apply_history(second, first, (full, old))
    assert _kinds(second, "resource_added") == []
    assert report.out_of_scope == 1 and report.presence_rows == 0
    # The other way round: the newer structure stops asking about that type,
    # having dropped it from the scope. That is neither a disappearance nor a
    # failure to read but being out of scope. The two counts in the report send
    # somebody to do different things; a failure to read sends them to fix a
    # permission.
    third = _bare(old, P3)
    report = apply_history(third, second, (old, full))
    assert _kinds(third, "resource_removed") == []
    assert (report.out_of_scope, report.unread) == (1, 0)


def test_a_missing_structure_is_said_out_loud_not_guessed():
    """The structure changed but the structure behind the previous observation
    was not supplied. Do not compare against a structure from another moment:
    no rows, and a warning.
    """
    full = _fixture("relations")
    gone = _without(full, PRESENCE_FIXTURES["relations"])
    first, second = _bare(full, P1), _bare(gone, P2)
    report = apply_history(second, first, (gone,))
    assert second["changes"] == [] and report.presence_unpaired is True
    assert any("상태 없는 카드" in w for w in report.warnings), report.warnings


def test_presence_kinds_are_exactly_the_stateless_cards():
    """The cause of that issue was adding a type with no state without updating
    the history side. A stateless card now has to be one of two things: it
    either reports appearance and disappearance from the structure, through
    `PRESENCE_ONLY_NODES`, or it is excluded with a recorded reason, in the set
    below, which matches the comments in `observe.py`.
    """
    from nettopo.observe import NO_STATE_NODES, PRESENCE_ONLY_NODES

    left_out = {
        "account",
        "region",
        "route_table",
        "transit_gateway_route_table",
        "external_ip",
    }
    assert set(PRESENCE_ONLY_NODES) == set(NO_STATE_NODES) - left_out
    for sources in PRESENCE_ONLY_NODES.values():
        assert sources and set(sources) <= set(SUPPORTED_RESOURCE_TYPES)
