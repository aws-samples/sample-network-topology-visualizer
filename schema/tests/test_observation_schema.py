"""The checks that `observation.schema.json` cannot express on its own.

This contract has an unusual number of them, and all three matter:

- **It references another document.** `observations[].target_id` is the id of a
  node or an edge in `topology.json`. A dangling reference produces a picture
  with state attached to a resource that no longer exists, which is a quietly
  wrong picture.
- **The order of judgement is fixed**: unknown before broken. Recording `down`
  for an (account, Region) that could not be read is a lie. JSON Schema cannot
  express an order of evaluation, so it is enforced here.
- **Ordering**, which is what determinism consists of.

Every positive assertion has a **counterpart that must fail**. An assertion that
cannot fail is not a passing test but an unmeasured one; the tests whose names
end in `_detects_` are those counterparts.
"""

from __future__ import annotations

import copy
import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path

import pytest
from internal_docs import read_or_skip
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "observation.schema.json"
TOPOLOGY_SCHEMA_PATH = ROOT / "schema" / "topology.schema.json"
SEAMS_PATH = ROOT / "docs" / "contract-seams.md"
ADR_PATH = ROOT / "docs" / "adr" / "0006-observation-schema.md"
ADR9_PATH = ROOT / "docs" / "adr" / "0009-optional-evidence-sources.md"
TOPOLOGY_FIXTURE_DIR = ROOT / "fixtures"
FIXTURE_DIR = TOPOLOGY_FIXTURE_DIR / "observation"

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
TOPOLOGY_SCHEMA = json.loads(TOPOLOGY_SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
TOPOLOGY_VALIDATOR = Draft202012Validator(TOPOLOGY_SCHEMA)
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))
# The marker for flow-log evidence (`$defs/flow_log_evidence`). It sits where a
# metric name goes but **is not a metric**, so the metric-layer ledger, the
# metric-only checks and the "live requires the metric layer to be ok" check must
# not count it as one.
FLOW_LOG = "flow_log"

REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]$")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _doc(path: Path) -> str:
    """Read an internal design document. They are absent from the public tree, so
    the comparisons that need one record a reason and skip.

    Nothing is read at import time. Every value taken from a table
    (`approved_defaults()`, `o7_vocabulary()` and so on) is read inside the first
    test that uses it and cached there. Read at module level, a missing document
    would turn into a collection error for every engine test file that imports
    this one as an oracle; we measured 11 such files in the public tree.
    """
    return read_or_skip(path.relative_to(ROOT).as_posix())


@cache
def approved_defaults() -> dict[str, int]:
    """Parse the approved-defaults table out of the design document, which is the
    source of truth for these numbers rather than the schema.

    Pinning them in the schema with `const` would make a per-deployment
    adjustment a contract violation, and writing them as `default` would make
    them a comment no validator reads. So the values live in the document and
    only their agreement with the fixtures is checked here.
    """
    text = _doc(ADR_PATH)
    parts = text.split("## 승인된 기본값")
    assert len(parts) == 2, (
        f"{ADR_PATH.name} has no approved-defaults section; with the source of "
        "truth for these numbers gone, the assertions below would compare against "
        "an empty table and pass silently"
    )
    section = parts[1].split("\n## ")[0]
    return {
        key: int(value)
        for key, value in re.findall(
            r"^\| `([a-z_]+)` \| `([0-9]+)` \|", section, re.MULTILINE
        )
    }


@cache
def adr9_source_kinds(text: str | None = None) -> set[str]:
    """The first column of the design document's adoption table is the source of
    truth for the optional evidence-source vocabulary.

    The document says a new term is added to the adoption table first, so the
    tests enforce that order. They block the other direction too: a term only in
    the table gives the engine nothing to detect, and a term only in the enum
    means a call with no recorded reason for reading that source.

    The bonus table is deliberately excluded. Those are not switches anyone turns
    on or off but paired metrics that are already on, and they belong to a
    different fixed table, so they are not part of the `sources[]` vocabulary.

    `text` exists to demonstrate discriminating power; see
    `test_adr9_kind_parser_reads_rows_not_prose`.
    """
    text = _doc(ADR9_PATH) if text is None else text
    parts = text.split("#### 1차 — ")
    assert len(parts) == 2, (
        f"{ADR9_PATH.name} has no adoption section; with the section gone, the "
        "assertions below would compare two empty sets and pass silently"
    )
    section = parts[1].split("\n#### ")[0]
    # Written as `[a-z_]+` this silently drops `ec2_detailed_monitoring`, which
    # contains a digit.
    rows = re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.MULTILINE)
    return set(rows) - {"kind"}


def source_sort_keys(doc: dict) -> list[str]:
    return [s["kind"] for s in doc["sources"]]


def sources_over_total(doc: dict) -> list[str]:
    """JSON Schema cannot express `enabled_ids <= total_ids`, because that
    compares two sibling values, and the schema's `$comment` promises this check
    instead. When it is exceeded, the screen draws something like "9/7 VPCs"."""
    return [
        s["kind"]
        for s in doc["sources"]
        if "scope" in s and s["scope"]["enabled_ids"] > s["scope"]["total_ids"]
    ]


def sources_used_while_off(doc: dict) -> list[str]:
    """The schema's `allOf` already blocks this, but the fact that it blocks it
    needs its own check;
    `test_source_check_detects_a_source_used_while_off` demonstrates the
    discriminating power."""
    return [s["kind"] for s in doc["sources"] if s["state"] == "off" and s["used"]]


@cache
def o7_state_source_types(text: str | None = None) -> tuple[set[str], set[str]]:
    """The first column of the design document's state-source table is the source
    of truth for which types may carry a state.

    The document says that a type with no row there is a defect rather than a
    `none`, that a new resource gets its row first, and that a test enforces the
    agreement. That test did not exist: the only thing parsing the document was
    `approved_defaults()` above. Add a collector without adding a row and that
    type quietly carries no state, which on screen is indistinguishable from "not
    observable".

    `text` exists to demonstrate discriminating power. Checking that the parser
    reads table rows rather than scraping prose needs a synthetic document, which
    means not touching the real file; see `test_o7_parser_reads_rows_not_prose`.
    """
    text = _doc(ADR_PATH) if text is None else text
    parts = text.split("### O7 — ")
    assert len(parts) == 2, (
        f"{ADR_PATH.name} has no state-source section; with the section gone, the "
        "assertions below would compare two empty sets and pass silently"
    )
    section = parts[1].split("\n### ")[0]
    _, _, after_nodes = section.partition("**노드**")
    node_block, _, edge_block = after_nodes.partition("**엣지**")

    def first_column_types(block: str) -> set[str]:
        """Qualifiers on a row name are folded down to the base type.

        In `association←route_table`, what follows the arrow is not a type but the
        kind of response that produced the target. Without folding, the
        comparison against the enum goes red in both directions at once: no
        `association` row exists, and two rows exist that the enum does not have.

        The character class has to include `0-9`. Written as `[a-z_]+` it
        silently fails to read the `ec2_instance` row and then reports that the
        table has no row for it. `adr9_source_kinds()` above hit the same trap
        once.
        """
        found: set[str] = set()
        for row in re.findall(r"^\|([^|]+)\|", block, re.MULTILINE):
            found |= {
                name.split("←")[0]
                for name in re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", row)
            }
        return found - {"node_type", "edge_type"}

    return first_column_types(node_block), first_column_types(edge_block)


@cache
def o7_vocabulary(text: str | None = None) -> dict[str, set[str]]:
    """The **last column** of that table is the source of truth for which states a
    type may report. An empty set means no entry is created at all.

    The document distinguishes three cases: "no state" means no entry in
    `observations`, because there is nothing to draw; `none` means an entry that
    carries a `?`; and a vocabulary (`up`, `down`, `live`) means a verdict is
    recorded. Those are the three things a person sees: a plain line, a card with
    a `?`, and a card with a colour.

    Only the last column is read. Searching the whole row for "no state" would
    pick up wording in another column, such as the resource-state cell of the
    `attachment` row, and the classification would wobble.

    **The keys keep their qualifiers** (`association←network_acl`), because the
    same edge type may report a different vocabulary depending on the response
    that produced it. If the keys were folded, recording `up` on an association
    derived from a network ACL would pass. Where a lookup has to be by base type, because the
    check does not know the qualifier, use `o7_allowed()`.
    """
    text = _doc(ADR_PATH) if text is None else text
    section = text.split("### O7 — ")[1].split("\n### ")[0]
    _, _, after_nodes = section.partition("**노드**")
    node_block, _, edge_block = after_nodes.partition("**엣지**")
    words = {"live", "up", "down", "none"}
    out: dict[str, set[str]] = {}
    for block in (node_block, edge_block):
        for line in block.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            # The `0-9` is required. Without it `ec2_instance` becomes a type
            # that is not in this table, so those nodes fall into "no row",
            # which counts as a defect, and quietly carry no state.
            types = set(re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", cells[0])) - {
                "node_type",
                "edge_type",
            }
            allowed: set[str] = (
                set()
                if "상태 없음" in cells[-1]
                else set(re.findall(r"`([a-z]+)`", cells[-1])) & words
            )
            for t in types:
                out[t] = allowed
    return out


@cache
def o7_entry_bearing() -> frozenset[str]:
    # Folded to the base type, because this set is compared against the
    # `node_type` and `edge_type` enums and is used where the question "should
    # there be an entry" is asked by type name alone (`silent_where_read`). If any
    # qualified row reports a vocabulary, that type is one that creates entries.
    return frozenset(
        t.split("←")[0] for t, allowed in o7_vocabulary().items() if allowed
    )


def o7_metric_only(text: str | None = None) -> set[str]:
    """The row names the table marks as metric-layer only.

    This is a fourth shape. The resource-state layer says **nothing** about such a
    segment, since the node carries that value, and a row appears only when the
    metric layer actually answered. So a missing row is not a defect here: the
    metric layer is off by default, and while it is off this kind being silent
    throughout is correct.

    If that exemption widened into "an edge may stay silent forever", we would be
    back to the end-to-end inference that was abandoned. So instead of dropping
    the check, the discriminating power moves into two others: at least one
    fixture carries a row of this kind
    (`test_some_fixture_carries_a_metric_only_edge`), and that row has to cite a
    **metric** (`test_metric_only_edges_must_cite_a_metric`).
    """
    text = _doc(ADR_PATH) if text is None else text
    section = text.split("### O7 — ")[1].split("\n### ")[0]
    out: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue  # the prose outside the table says this too; read only rows
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or "2층 전용" not in cells[-1]:
            continue
        out |= set(re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", cells[0])) - {
            "node_type",
            "edge_type",
        }
    return out


@cache
def o7_metric_only_types() -> frozenset[str]:
    return frozenset(t.split("←")[0] for t in o7_metric_only())


def o7_flow_log_only(text: str | None = None) -> set[str]:
    """The row names the table marks as flow-log only.

    The node carries the resource-state layer and there is no segment metric
    either: a row appears only when flow logs are enabled in that account and we
    read them and found the round trip. These rows get the same exemption as the
    metric-layer-only ones, a missing row is not a defect, but for a different
    reason: the metric layer is a layer we switch on, while this is a source the
    operator switches on.

    They are therefore counted separately. If the two sets were merged, the check
    that a metric-layer-only row has to cite a metric
    (`metric_only_without_a_metric`) would reject the flow-log evidence on a
    peering row, or it would be loosened to let that through, and then an
    attachment line could go green carrying only a flow-log marker and no metric.
    Rows are read the same way as in `o7_metric_only`, since this phrase appears
    in the prose as well.
    """
    text = _doc(ADR_PATH) if text is None else text
    section = text.split("### O7 — ")[1].split("\n### ")[0]
    out: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or "흐름 로그 전용" not in cells[-1]:
            continue
        out |= set(re.findall(r"`([a-z_]+(?:←[a-z_]+)?)`", cells[0])) - {
            "node_type",
            "edge_type",
        }
    return out


@cache
def o7_flow_log_only_types() -> frozenset[str]:
    return frozenset(t.split("←")[0] for t in o7_flow_log_only())


@cache
def o7_required_entry() -> frozenset[str]:
    # "May create an entry" and "must have an entry" are different things. The
    # metric-only and flow-log-only rows are the former, not the latter, and the
    # set handed to `silent_where_read` below has to be the latter.
    return o7_entry_bearing() - o7_metric_only_types() - o7_flow_log_only_types()


def o7_allowed(o7_type: str | None) -> set[str] | None:
    """Row name to the states it may report. **`None` means the table has no such
    row**, which is a defect; an empty set means "no state".

    Where the qualifier is known, pass the exact row name
    (`association←network_acl`); where it is not, pass the base type
    (`association`), and the qualified rows of that type are then unioned. That
    is acceptable there because the only question is whether an entry is allowed
    at all. A check that judges a verdict always passes the qualified name:
    judging against the union would let an `up` on a network-ACL association
    through.

    Written as `dict.get(x, {"x"})` this would merge "no row" and "no state" into
    one value, and a person sees different results for the two: the first is a
    defect to fix, the second is intended silence.
    """
    if o7_type is None:
        return None
    rows = [
        allowed
        for name, allowed in o7_vocabulary().items()
        if name == o7_type or name.startswith(f"{o7_type}←")
    ]
    if not rows:
        return None
    return set().union(*rows)


def o7_gaps(documented: set[str], declared: set[str]) -> tuple[set[str], set[str]]:
    """(enum values with no row in the table, rows with no enum value). Both have
    to be empty."""
    return declared - documented, documented - declared


def errors(doc: dict, validator: Draft202012Validator = VALIDATOR) -> list[str]:
    return [
        f"{e.json_path}: {e.message}"
        for e in sorted(validator.iter_errors(doc), key=lambda e: e.json_path)
    ]


def topology_for(path: Path) -> Path:
    """The structure fixture an observation fixture belongs to; the first segment
    of the file name decides the pairing.

    `minimal.metric-off.json` pairs with `fixtures/minimal.json`. Two structure
    fixtures share the same `collected_at`, so that value cannot select the pair.
    The name selects it, and a test below checks that
    `run.topology_collected_at` does not disagree with the pair.
    """
    return TOPOLOGY_FIXTURE_DIR / f"{path.name.split('.')[0]}.json"


def place_of(node_id: str) -> tuple[str, str | None] | None:
    """The (account, Region) of a node id, or None when it cannot be read.

    The id form (`<type>/<account>/<region>/…`) is stated only in a description in
    topology.schema.json, and the pattern does not enforce the number of
    segments. So the parsing is defensive: unreadable gives None, and the caller
    treats "could not read" as a failure rather than passing silently.

    **An edge id is rejected.** An edge id looks like
    `association|route_table/<acct>/<region>/…|…`, so splitting on `/` makes
    segments 1 and 2 *look* like an account and a Region, and without the guard
    below this function would quietly break its own contract of reading a node
    id. The reason that went unnoticed for a while is that the assertions were
    using fake edge ids the engine never produces.
    """
    if "|" in node_id:
        return None
    parts = node_id.split("/")
    if parts[0] == "account" and len(parts) == 2:
        return (parts[1], None)
    if parts[0] == "region" and len(parts) == 3:
        return (parts[1], parts[2])
    # A global node, such as a Direct Connect gateway, has the literal `global`
    # in the Region position. Return the marker as it is rather than folding it to
    # "no Region": folded, it would equal the account container and the checks
    # below would skip that node.
    if (
        len(parts) >= 4
        and re.fullmatch(r"[0-9]{12}", parts[1])
        and parts[2] == GLOBAL_SLOT
    ):
        return (parts[1], GLOBAL_SLOT)
    if (
        len(parts) >= 4
        and re.fullmatch(r"[0-9]{12}", parts[1])
        and REGION_RE.match(parts[2])
    ):
        return (parts[1], parts[2])
    return None


GLOBAL_SLOT = "global"


def home_cell(
    doc: dict, place: tuple[str, str | None] | None, resource_type: str | None
) -> tuple[str, str, str] | None:
    """The resource-state cell (account, Region, type) that may judge this target,
    or None when it belongs to no cell.

    **A global slot** (`GLOBAL_SLOT`) has no Region. The rule: among the Regions
    where the describe of that type in that account is `ok`, the first by name is
    its cell. The list is the same read from any Region, so one `ok` means it was
    read, and the ledger counts it in a single cell. With no `ok` at all there is
    no cell, which means no verdict may be claimed. The rule is restated here
    rather than calling the engine function (`observe.global_home`), because an
    oracle that calls the product code is wrong in the same way the code is.
    """
    if place is None or place[1] is None or resource_type is None:
        return None
    account, region = place
    if region != GLOBAL_SLOT:
        return (account, region, resource_type)
    regions = sorted(
        c["region"]
        for c in doc["coverage"]
        if c["layer"] == "describe"
        and c["status"] == "ok"
        and c["account_id"] == account
        and c["resource_type"] == resource_type
        and c["region"] is not None
    )
    return (account, regions[0], resource_type) if regions else None


def topology_index(topo: dict) -> dict[str, str]:
    """target_id → target_kind."""
    index = {n["id"]: "node" for n in topo["nodes"]}
    for edge in topo["edges"]:
        index[edge["id"]] = "edge"
    return index


def places(topo: dict) -> dict[str, tuple[str, str | None] | None]:
    """target_id to (account, Region). An edge uses the place of its source node.

    One exception: when an attachment line reaches a Transit Gateway that is not
    the attachment's own, which is the far side of a merged peering, that line
    came from the far side's response and so takes the place of the **target**
    gateway. The rule is restated here rather than reusing the engine function,
    because an oracle that calls the product code is wrong in the same way the
    code is.
    """
    by_id = {n["id"]: n for n in topo["nodes"]}
    out: dict[str, tuple[str, str | None] | None] = {
        n["id"]: place_of(n["id"]) for n in topo["nodes"]
    }
    for edge in topo["edges"]:
        source = by_id.get(edge["source"], {})
        target = by_id.get(edge["target"], {})
        own = (source.get("attributes") or {}).get("transit_gateway_id")
        far = (
            edge["type"] == "attachment"
            and target.get("type") == "transit_gateway"
            and own
            and target.get("native_id") != own
        )
        out[edge["id"]] = out.get(edge["target"] if far else edge["source"])
    return out


def resource_types(topo: dict) -> dict[str, str]:
    """target_id to the kind of response the target was read from, which is the
    third coverage key.

    A node is its own kind. An edge is **the kind of its source node**, and all
    four documented cases agree with that value (`route` and
    `association←route_table` give `route_table`; `association←network_acl` gives
    `network_acl`; `security_group_reference` gives `security_group`). The
    collector puts the node that came out of the response on the source side,
    which is the same convention `places()` uses above.

    **Reading the target's kind instead would be wrong.** The target of a `route`
    edge is a NAT gateway, internet gateway or Transit Gateway, and even when
    that kind was denied, the route itself was in the route table response. "We
    could not read the NAT" and "the route to that NAT is there" can both hold at
    once, and that is the truth rather than a contradiction.
    """
    out: dict[str, str] = {n["id"]: n["type"] for n in topo["nodes"]}
    for edge in topo["edges"]:
        source_type = out.get(edge["source"])
        if source_type is not None:
            out[edge["id"]] = source_type
    return out


# ---- What JSON Schema cannot see: pure functions, each pinned by a pair ------


def unsorted(values: list) -> bool:
    return values != sorted(values)


def observation_sort_keys(doc: dict) -> list[str]:
    return [o["target_id"] for o in doc["observations"]]


# The sort key of a history line, in the order the `$comment` on
# `history.max_changes` states, and the same as
# `sorted(..., key=(at, kind, target_id))` in the engine. Drop `kind` and a
# correctly sorted document that records an appearance and a removal in the same
# observation is judged out of order; we hit that with the old key
# `(at, target_id)` while generating a demo.
CHANGE_SORT_FIELDS = ("at", "kind", "target_id")


def change_sort_keys(doc: dict) -> list[tuple[str, str, str]]:
    return [(c["at"], c["kind"], c["target_id"]) for c in doc["changes"]]


def coverage_sort_keys(doc: dict) -> list[tuple[str, str, str, str]]:
    """There are **four** keys. Without the type axis, several types in the same
    place would look like one cell, and there would be nowhere to record "we read
    the VPCs but could not read the subnets"."""
    return [
        (c["account_id"], c["region"], c["resource_type"], c["layer"])
        for c in doc["coverage"]
    ]


def fold_start_after_last(doc: dict) -> list[str]:
    """Lines where the start of a folded repetition (`first_at`) is not before its
    last occurrence (`at`), an ordering JSON Schema cannot check.

    A repetition spans at least two distinct observation times
    (`repeat_count` >= 2), so the start is **strictly** before the last.
    """

    def ts(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return [
        c["target_id"]
        for c in doc["changes"]
        if isinstance(c.get("first_at"), str) and ts(c["first_at"]) >= ts(c["at"])
    ]


def dangling_observations(doc: dict, topo: dict) -> list[str]:
    index = topology_index(topo)
    return [
        o["target_id"]
        for o in doc["observations"]
        if index.get(o["target_id"]) != o["target_kind"]
    ]


def dangling_changes(doc: dict, topo: dict) -> list[str]:
    """`resource_removed` is the exception: something that is gone is correctly
    absent from the structure snapshot.

    Without the exception there would be no way to record a removal at all.
    Widening it, though, would let a mistyped id through silently, so the
    exemption is granted per kind only.
    """
    index = topology_index(topo)
    return [
        c["target_id"]
        for c in doc["changes"]
        if c["kind"] != "resource_removed"
        and index.get(c["target_id"]) != c["target_kind"]
    ]


def requested_cells(doc: dict) -> set[tuple[str, str, str, str]]:
    """Every cell scope asked for: account x Region x **type** x layer.

    The type axis is what made `scope.resource_types` required. With no request
    recorded, "that type failed" and "that type was never called" are
    indistinguishable.
    """
    return {
        (a, r, t, layer)
        for a in doc["scope"]["account_ids"]
        for r in doc["scope"]["regions"]
        for t in doc["scope"]["resource_types"]
        for layer in ("describe", "metric")
    }


def coverage_holes(doc: dict) -> list[tuple[str, str, str, str]]:
    return sorted(requested_cells(doc) - set(coverage_sort_keys(doc)))


def unrequested_coverage(doc: dict) -> list[tuple[str, str, str, str]]:
    return sorted(set(coverage_sort_keys(doc)) - requested_cells(doc))


def layer_status(doc: dict) -> dict[tuple[str, str, str, str], str]:
    return {
        (c["account_id"], c["region"], c["resource_type"], c["layer"]): c["status"]
        for c in doc["coverage"]
    }


def item_count_ledger(doc: dict, topo: dict) -> tuple[int, int, int]:
    """(the total written in the `ok` describe cells, the number of entries that
    belong to a resource-state cell, the number that belong to no cell).

    `item_count` is the number of targets judged in that (account, Region, type,
    layer) cell, which means **some entries belong to no cell at all**: security
    groups, network ACLs and security group references are kinds the observation
    never calls, so they have no describe row, and their `none` entries come from
    the structure document. The identities are `total == entries in cells` and
    `entries in cells + entries outside + metric-only entries == all of them`.

    The difference must not be pinned as a constant: the day a fixture gains one
    more security group, that would quietly be wrong.

    **Metric-only edges are outside the resource-state ledger**, the same rule as
    the `counts` loop in `observe.py`. Those rows were created by a metric rather
    than by a describe, so counting them in a resource-state cell would make the
    resource-state numbers move every time monitoring is switched on and off.
    `metric_item_count_ledger()` measures them in the metric cells instead.
    """
    seen = places(topo)
    kinds = resource_types(topo)
    rows = {
        (c["account_id"], c["region"], c["resource_type"]): c
        for c in doc["coverage"]
        if c["layer"] == "describe"
    }
    priced = unpriced = 0
    for obs in doc["observations"]:
        if (
            obs["target_id"].split("|")[0]
            in o7_metric_only_types() | o7_flow_log_only_types()
        ):
            continue
        key = home_cell(doc, seen.get(obs["target_id"]), kinds.get(obs["target_id"]))
        if key in rows:
            priced += 1
        else:
            unpriced += 1
    written = sum(c["item_count"] or 0 for c in rows.values() if c["status"] == "ok")
    return written, priced, unpriced


def metric_item_count_ledger(doc: dict) -> tuple[int, int]:
    """(the total written in the `ok` metric cells, the number of entries that
    actually cite a metric).

    The metric layer keeps its own ledger: only some of the targets in a
    resource-state cell get an answer from a metric, and metric-only edges are not
    in a resource-state cell at all. Without this identity **the metric numbers
    are compared against nothing**, and the summary badge could say "12 flowing"
    while three things are green on screen.

    "Cites a metric" means `evidence.metric` is non-empty. Using `datapoints`
    instead would drop the "measured, and it was zero" rows (`up` with
    `datapoints: 0`), and those are rows the metric layer did answer.

    Membership of a cell is deliberately **not** filtered here. Filtering would
    let through a metric row whose cell has no coverage row, which is a green
    verdict that cannot cite its evidence; without that filter, `written` comes
    out smaller and the mismatch is caught.

    An attachment line raised by TGW flow logs is also a row the metric layer
    answered: flow logs add `live` on top of a row the metric layer had already
    made `up` and change its evidence to the flow log. Because it sits on a
    metric-only edge, that row cannot exist without the metric layer.
    """
    rows = [c for c in doc["coverage"] if c["layer"] == "metric"]
    cited = sum(
        1
        for obs in doc["observations"]
        if (obs.get("evidence") or {}).get("metric") not in (None, "", FLOW_LOG)
        or _tgw_flow_lifted(obs)
    )
    written = sum(c["item_count"] or 0 for c in rows if c["status"] == "ok")
    return written, cited


def _tgw_flow_lifted(obs: dict) -> bool:
    """An attachment line that a TGW flow-log round trip raised to `live`; it is
    only ever layered on top of a metric-layer row."""
    return (
        obs["target_id"].startswith("attachment|transit_gateway_attachment/")
        and (obs.get("evidence") or {}).get("metric") == FLOW_LOG
        and obs["status"] == "live"
    )


def metric_only_without_a_metric(doc: dict) -> list[str]:
    """Observations on a metric-only edge whose evidence is not a metric; any of
    them is a defect.

    This function protects the point of the whole arrangement. The price of
    exempting those three edge types from having to produce a row is that **when
    a row is there, it has to carry a metric**. A segment verdict reached without
    one is by definition "both ends are alive, so traffic probably flows between
    them", which is the end-to-end inference that was abandoned.

    Three cases are separated: no evidence object at all, an empty `metric`, and a
    resource-state API recorded instead. The last is the quietest: recording
    `ec2:DescribeTransitGatewayAttachments` would claim the segment was measured
    with a describe call, and no such API exists.
    """
    out = []
    for obs in doc["observations"]:
        if obs["target_id"].split("|")[
            0
        ] not in o7_metric_only_types() or _tgw_flow_lifted(obs):
            continue
        evidence = obs.get("evidence") or {}
        if not evidence.get("metric"):
            out.append(f"{obs['target_id']}: no `evidence.metric`")
        elif evidence.get("api") != "cloudwatch:GetMetricData":
            out.append(
                f"{obs['target_id']}: the evidence API is not a metric call "
                f"({evidence.get('api')})"
            )
        elif obs["status"] not in {"live", "up", "down"}:
            out.append(
                f"{obs['target_id']}: not a status the table allows ({obs['status']})"
            )
    return out


def route_table_users(topo: dict) -> dict[str, set[str]]:
    """Route table id to the set of subnet ids that **actually use** that table.

    A subnet uses it in one of two ways: there is an explicit association edge
    (`association|route_table/…|subnet/…`), or the table is the main table of the
    same VPC (`attributes.main`) and that subnet has no explicit association. A
    main association draws no edge, so dropping the second case would make it
    impossible to prove which subnets use the main table; loosening it to "same
    VPC is enough" would let **a record from a neighbouring subnet turn this line
    green**, which removes the reason for having per-subnet evidence at all.

    This is an oracle: it does not call the engine's attribution code, which
    would be wrong in the same way.
    """
    nodes = {n["id"]: n for n in topo["nodes"]}
    explicit: dict[str, str] = {}
    for edge in topo["edges"]:
        if (
            edge["type"] == "association"
            and nodes.get(edge["source"], {}).get("type") == "route_table"
            and nodes.get(edge["target"], {}).get("type") == "subnet"
        ):
            explicit[edge["target"]] = edge["source"]
    users: dict[str, set[str]] = {
        n["id"]: set() for n in nodes.values() if n["type"] == "route_table"
    }
    for subnet, table in explicit.items():
        users.setdefault(table, set()).add(subnet)
    mains = {
        n.get("parent_id"): n["id"]
        for n in nodes.values()
        if n["type"] == "route_table"
        and (n.get("attributes") or {}).get("main") is True
    }
    for n in nodes.values():
        if (
            n["type"] == "subnet"
            and n["id"] not in explicit
            and n.get("parent_id") in mains
        ):
            users[mains[n["parent_id"]]].add(n["id"])
    return users


def flow_log_problems(doc: dict, topo: dict) -> list[str]:
    """Places where flow-log evidence does not agree with the structure or with
    the sources, which is what the schema's `$comment` promises to check here.

    JSON Schema only sees the shape of one row. What follows are the false greens
    that appear only when **two documents and several rows** are held against
    each other; miss one and a person sees a line as green while nothing flows.

    1. The layer is off, yet flow-log evidence or a log list is present; a screen
       with it off has to differ from one with it on.
    2. `by_subnet` is not in ascending, unique `subnet_id` order; the same subnet
       twice counts twice.
    3. That subnet **does not use that route table** (`route_table_users`). This
       is the central one: the rows were split per subnet precisely so that every
       subnet line sharing a table does not go green together.
    4. The `observed_on` of a peering route item is not that subnet, which makes
       its "where we saw it" false.
    5. A flow-log row on an internet gateway card or a peering line **has no
       subnet proof behind it**. For an internet gateway, the `by_subnet` of the
       route towards it has to contain the `observed_on` subnet. For a peering,
       either the `by_subnet` of the route towards that `pcx` contains it, or, if
       a VPC was recorded, that VPC is one side of the peering and carries a VPC
       log that was read but could not attribute a subnet
       (`subnet_unattributable`).
    6. The log list is not in ascending, unique id order; a source marked as used
       disagrees with the list; `attached_to` is absent from the structure; or no
       VPC flow log that was read covers the subnet that was proven.
    """
    out: list[str] = []
    nodes = {n["id"]: n for n in topo["nodes"]}
    edges = {e["id"]: e for e in topo["edges"]}
    users = route_table_users(topo)
    resources = [
        (source["kind"], r)
        for source in doc["sources"]
        for r in source.get("resources", [])
    ]
    on = doc["layers"].get("flow_log") is True

    # ①
    if not on:
        for obs in doc["observations"]:
            if (
                "by_subnet" in obs
                or (obs.get("evidence") or {}).get("metric") == FLOW_LOG
            ):
                out.append(
                    f"{obs['target_id']}: flow-log evidence while the layer is off"
                )
        for source in doc["sources"]:
            if "resources" in source:
                out.append(f"{source['kind']}: a log list while the layer is off")

    # ②③④
    proven: dict[str, set[str]] = {}
    claimed: set[str] = set()
    for obs in doc["observations"]:
        items = obs.get("by_subnet")
        if not items:
            continue
        ids = [i["subnet_id"] for i in items]
        if ids != sorted(set(ids)):
            out.append(
                f"{obs['target_id']}: by_subnet is not in ascending, unique order"
            )
        edge = edges.get(obs["target_id"])
        if edge is None:
            out.append(
                f"{obs['target_id']}: the edge of a row carrying by_subnet is not "
                "in the structure"
            )
            continue
        for item in items:
            subnet = item["subnet_id"]
            if subnet not in users.get(edge["source"], set()):
                out.append(
                    f"{obs['target_id']}: {subnet} does not use this route table"
                )
                continue
            if (
                edge["target"].startswith("vpc_peering_connection/")
                and item["evidence"].get("observed_on") != subnet
            ):
                out.append(
                    f"{obs['target_id']}: the observed_on of the {subnet} item is "
                    "not that subnet"
                )
            proven.setdefault(edge["target"], set()).add(subnet)
            claimed.add(subnet)

    # ⑤
    for obs in doc["observations"]:
        evidence = obs.get("evidence") or {}
        if evidence.get("metric") != FLOW_LOG:
            continue
        target_id, seen_on = obs["target_id"], evidence.get("observed_on")
        if target_id.startswith("internet_gateway/"):
            if seen_on not in proven.get(target_id, set()):
                out.append(
                    f"{target_id}: no subnet proof from {seen_on} towards this "
                    "internet gateway"
                )
        elif target_id.startswith("peering|"):
            pcx = target_id.split("|")[1]
            if str(seen_on).startswith("vpc/"):
                sides = {
                    e["target"]
                    for e in topo["edges"]
                    if e["type"] == "peering" and e["source"] == pcx
                }
                backed = any(
                    kind == "vpc_flow_log"
                    and r["used"]
                    and r.get("attached_to") == seen_on
                    and r.get("why_code") == "subnet_unattributable"
                    for kind, r in resources
                )
                if seen_on not in sides or not backed:
                    out.append(
                        f"{target_id}: {seen_on} is not a log that was read on "
                        "one side of this peering and could not attribute a subnet"
                    )
            elif seen_on not in proven.get(pcx, set()):
                out.append(
                    f"{target_id}: no subnet proof from {seen_on} towards this peering"
                )

    # ⑥
    for source in doc["sources"]:
        listed = source.get("resources")
        if listed is None:
            continue
        ids = [r["id"] for r in listed]
        if ids != sorted(set(ids)):
            out.append(
                f"{source['kind']}: the log list is not in ascending, unique id order"
            )
        if source["used"] != any(r["used"] for r in listed):
            out.append(
                f"{source['kind']}: the source's used flag disagrees with the list"
            )
        for r in listed:
            if r.get("attached_to") is not None and r["attached_to"] not in nodes:
                out.append(
                    f"{source['kind']}: {r['id']} is attached to "
                    f"{r['attached_to']}, which is not in the structure"
                )
    read = [r for kind, r in resources if kind == "vpc_flow_log" and r["used"]]
    for subnet in sorted(claimed):
        vpc = nodes.get(subnet, {}).get("parent_id")
        if not any(
            r.get("attached_to") == subnet
            or (
                r.get("why_code") != "subnet_unattributable"
                and (r.get("attached_to") == vpc or r["level"] == "network_interface")
            )
            for r in read
        ):
            out.append(f"{subnet}: no VPC flow log that was read covers this subnet")
    return out


def claims_without_reading(doc: dict, topo: dict) -> list[str]:
    """Observations that claim a verdict about something that could not be read.

    This is what "unknown before broken" actually consists of. Without an `ok`
    describe layer there is no claiming `up`, `down` or `live`, because the
    resource-state layer is the floor every verdict stands on. Without an `ok`
    metric layer there is no claiming `live`. Only `none` is exempt, because "not
    observable by construction" can be true regardless of permissions.
    """
    seen = places(topo)
    kinds = resource_types(topo)
    status = layer_status(doc)
    bad = []
    for obs in doc["observations"]:
        if obs["status"] == "none":
            continue
        place = seen.get(obs["target_id"])
        if place is None:
            bad.append(f"{obs['target_id']}: could not read its place")
            continue
        if place[1] is None:
            continue  # an account container belongs to no cell and has no state
        # The type has to be considered too. Going by the place alone, a claim
        # about a subnet would pass in a place where the VPCs were read but the
        # subnets were not, so a target of unknown type is treated as a failure.
        resource_type = kinds.get(obs["target_id"])
        if resource_type is None:
            bad.append(f"{obs['target_id']}: could not read its type")
            continue
        # A global node is **not** skipped. It used to be, and during that time
        # "up although no Region could read it" passed. No cell means no Region in
        # that account read that type with an `ok`.
        cell = home_cell(doc, place, resource_type)
        if cell is None:
            bad.append(
                f"{obs['target_id']}: no Region read the global {resource_type}, "
                f"yet {obs['status']}"
            )
            continue
        if status.get((*cell, "describe")) != "ok":
            bad.append(
                f"{obs['target_id']}: the {resource_type} describe is not ok, yet "
                f"{obs['status']}"
            )
        # Flow-log evidence is not tied to a metric cell. What backs such a row
        # is the read flag in `sources[].resources[]`, and `flow_log_problems`
        # checks that. Without this exemption a peering row could never be
        # recorded, because there is no peering metric cell.
        flow_log = (obs.get("evidence") or {}).get("metric") == FLOW_LOG
        if (
            obs["status"] == "live"
            and not flow_log
            and status.get((*cell, "metric")) != "ok"
        ):
            bad.append(
                f"{obs['target_id']}: the {resource_type} metric is not ok, yet live"
            )
    return bad


def silent_where_read(doc: dict, topo: dict, state_bearing: set[str]) -> list[str]:
    """The **opposite direction**: a target of a type that was read has no entry.

    Measuring only the suppressing direction (`claims_without_reading`) would let
    "silent about everything" pass, and an implementation really did that once: a
    partial failure in one place threw away **even what had been read** there.
    What a person saw was a screen telling them to re-check the very types they
    had permission for.

    `state_bearing` is the set of types the table does **not** mark as "no
    state", which is the set that must have an entry. It is taken from the table,
    so this check moves with the source of truth.
    """
    seen = places(topo)
    kinds = resource_types(topo)
    status = layer_status(doc)
    have = {o["target_id"] for o in doc["observations"]}
    missing = []
    for target_id, place in seen.items():
        if target_id in have or place is None or place[1] is None:
            continue
        resource_type = kinds.get(target_id)
        node_or_edge_type = (
            target_id.split("|")[0] if "|" in target_id else resource_type
        )
        if node_or_edge_type not in state_bearing:
            continue
        # A global slot asks the same question: if any Region in that account
        # read it with an `ok`, there has to be an entry.
        cell = home_cell(doc, place, resource_type)
        if cell is not None and status.get((*cell, "describe")) == "ok":
            missing.append(target_id)
    return sorted(missing)


def o7_type_of(target_id: str, topo: dict) -> str | None:
    """The row name in the state-source table. For an edge this is the **edge
    type**, which differs from the coverage type.

    The coverage type is the response the target was read from (`route` gives
    `route_table`), while a row in the table answers whether a state may be
    attached to that target. Mixing the two axes would make a `route` edge look at
    the `route_table` row, which says "no state", and conclude that no entry
    should exist, inverting the one exception the table's first rule grants.

    **One exception: `association` is not decided by the edge type alone.** The
    same type comes out of two responses and one of them, the network ACL, has no
    state field at all, so there are two rows. The row name is therefore
    qualified with the type of the source node (`association←network_acl`). With
    no qualified row it falls back to the base row, and failing that it is `None`,
    meaning the table has no such row.
    """
    if "|" in target_id:
        edge_type = target_id.split("|")[0]
        source = {e["id"]: e["source"] for e in topo["edges"]}.get(target_id)
        source_type = {n["id"]: n["type"] for n in topo["nodes"]}.get(source or "")
        qualified = f"{edge_type}←{source_type}"
        if qualified in o7_vocabulary():
            return qualified
        return edge_type if edge_type in o7_vocabulary() else None
    return {n["id"]: n["type"] for n in topo["nodes"]}.get(target_id)


def states_outside_the_table(doc: dict, topo: dict) -> list[str]:
    """Entries recording a state the last column of the table does not allow.

    It catches three things at once:
    - An entry on a type marked "no state", where the same fact could then be
      carried in two places and drift apart.
    - A verdict on a type the table allows only `none` for, or the reverse, a `?`
      on a type that can be read. The difference matters to a person: `?` means
      "we have no way of seeing this", which is false when the thing can be seen.
    - An entry on a type with no row in the table, which the table itself calls a
      defect rather than a `none`.

    One known gap, since resolved: the engine reports `none` for a network ACL to
    subnet `association`, because the collector fills in a constant. The old
    `association` row listed only `up` and `down`, so a fixture carrying that case
    turned this check red **while the code was right**. Splitting the table into
    `association←route_table` and `association←network_acl` was the answer, and
    this check now measures the two cases of the same type separately.
    """
    bad = []
    for obs in doc["observations"]:
        o7_type = o7_type_of(obs["target_id"], topo)
        allowed = o7_allowed(o7_type)
        if o7_type is None or allowed is None:
            bad.append(f"{obs['target_id']}: a type with no row in the table")
            continue
        if not allowed:
            bad.append(
                f"{obs['target_id']}: {o7_type} is marked 'no state', yet there is "
                "an entry"
            )
        elif obs.get("deleting") is True:
            # "Being deleted" sits outside the vocabulary column: it is not a
            # verdict but "no verdict, and here is why". Two things are checked
            # instead. It has to be `none`, so that a deletion does not read as an
            # outage again, and it has to be a type that has a resource-state
            # verdict at all: the types that only ever report `none`, such as
            # security groups and network ACLs, have no response that exposes a
            # deletion lifecycle.
            if obs["status"] != "none":
                bad.append(
                    f"{obs['target_id']}: being deleted, yet {obs['status']}; it "
                    "has to be none"
                )
            elif not allowed & {"up", "down"}:
                bad.append(
                    f"{obs['target_id']}: {o7_type} has no resource-state verdict, "
                    "yet it is marked as being deleted"
                )
        elif obs["status"] not in allowed:
            bad.append(
                f"{obs['target_id']}: {o7_type} may report "
                f"{sorted(allowed)}, yet {obs['status']}"
            )
    return bad


def live_without_metric_layer(doc: dict) -> list[str]:
    if doc["layers"]["metric"]:
        return []
    return [o["target_id"] for o in doc["observations"] if o["status"] == "live"]


def metric_rows_not_skipped_while_off(doc: dict) -> list[tuple[str, str, str, str]]:
    if doc["layers"]["metric"]:
        return []
    return [
        k for k, v in layer_status(doc).items() if k[3] == "metric" and v != "skipped"
    ]


def collapsed_below_threshold(doc: dict) -> list[str]:
    """Rows folded below the folding threshold. Folding has to be a function of
    that threshold.

    A row with `repeat_count` 2 next to a threshold of 4 means the screen shows
    "repeated twice" while the rule says folding starts at four, which is the rule
    and the data disagreeing.
    """
    threshold = doc["history"]["flap_collapse_threshold"]
    return [c["target_id"] for c in doc["changes"] if 1 < c["repeat_count"] < threshold]


def out_of_window_changes(doc: dict) -> list[str]:
    """Changes before the baseline or after the observation time.

    Nothing can be reconstructed from before the baseline, so a change before it
    cannot exist. A change after the observation time is in the future.
    """
    lo = ts(doc["history"]["baseline_at"])
    hi = ts(doc["run"]["observed_at"])
    return [c["target_id"] for c in doc["changes"] if not lo <= ts(c["at"]) <= hi]


def window_start(doc: dict) -> datetime:
    """The start of the window the list covers. When the retention window begins
    later than the baseline, the window wins."""
    h = doc["history"]
    return max(
        ts(h["baseline_at"]),
        ts(doc["run"]["observed_at"]) - timedelta(seconds=h["retention_seconds"]),
    )


def outside_retention_window(doc: dict) -> list[str]:
    """Changes older than the retention window.

    A change past the window has to **fall out** of the output. If one is left in,
    the screen says the list covers seven days while showing a line from eight
    days ago.
    """
    lo = window_start(doc)
    return [c["target_id"] for c in doc["changes"] if ts(c["at"]) < lo]


def truncation_mismatch(doc: dict) -> str | None:
    """Disagreements between `truncated` and the two caps, the retention window
    and the row count, as far as one document can show them.

    First, a record that begins outside the window has to carry
    `truncated: true`; otherwise the screen falsely claims this is the beginning.
    Second, the list never exceeds `max_changes` rows; exceeding it would make the
    cap a number that is merely carried along. The other direction, `true` while
    inside the window, **can now be correct**: once the cap has dropped something,
    that fact is carried forward. No document states that, so an engine test
    checks it as a functional relationship
    (`test_the_change_cap_keeps_the_newest_rows_and_says_so`).
    """
    h = doc["history"]
    dropped = ts(h["baseline_at"]) < ts(doc["run"]["observed_at"]) - timedelta(
        seconds=h["retention_seconds"]
    )
    if dropped and not h["truncated"]:
        return "the record begins outside the window, yet truncated=false"
    if len(doc["changes"]) > h["max_changes"]:
        return f"{len(doc['changes'])} rows in the list > the cap of {h['max_changes']}"
    return None


def defaults_drift(doc: dict, approved: dict[str, int]) -> dict[str, tuple[int, int]]:
    """The entries where a fixture's number differs from the approved one, as
    (fixture value, approved value)."""
    return {
        key: (doc["history"][key], value)
        for key, value in approved.items()
        if doc["history"][key] != value
    }


def out_of_scope_observations(doc: dict, topo: dict) -> list[str]:
    seen = places(topo)
    accounts = set(doc["scope"]["account_ids"])
    regions = set(doc["scope"]["regions"])
    bad = []
    for obs in doc["observations"]:
        place = seen.get(obs["target_id"])
        if place is None:
            bad.append(obs["target_id"])
            continue
        account, region = place
        # A global slot is off the Region axis; only its account has to be in
        # scope.
        if account not in accounts or (
            region not in (None, GLOBAL_SLOT) and region not in regions
        ):
            bad.append(obs["target_id"])
    return bad


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


DAY = timedelta(days=1)


@pytest.fixture
def doc() -> dict:
    """A copy of minimal.down.json. A negative test breaks exactly one thing in
    it.

    This one rather than `minimal.json`, because it is the only fixture whose
    `changes[]` is not empty, so the history negative tests cannot pass merely
    because the array was empty.
    """
    return load(FIXTURE_DIR / "minimal.down.json")


@pytest.fixture
def topo() -> dict:
    return load(TOPOLOGY_FIXTURE_DIR / "minimal.json")


# ---- The schema itself ------------------------------------------------------


def test_schema_itself_is_a_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_fixture_dir_is_not_empty():
    # With no fixtures, every parametrised test below would pass silently while
    # measuring nothing.
    assert FIXTURES, "there is not a single fixtures/observation/*.json"


def test_observation_fixtures_are_outside_the_topology_glob():
    """An observation document must not sit in the structure fixture directory.

    `schema/tests/test_schema.py` validates every `fixtures/*.json` against the
    topology schema with a non-recursive glob. An observation document next to
    them makes that test fail, which is correct, and is why they live in a
    subdirectory. Without pinning it here, someone moves one later and cannot
    find the cause.
    """
    assert FIXTURE_DIR.parent == TOPOLOGY_FIXTURE_DIR
    assert not sorted(TOPOLOGY_FIXTURE_DIR.glob("observation*.json"))


def test_status_enum_is_the_four_words_of_the_canonical_doc():
    """The source of truth for the status vocabulary is a table in
    `docs/contract-seams.md`.

    "Do not invent a fifth word" is not kept by being written in a document.
    Holding the schema enum and that table together here means a change that adds
    a word only passes if it changes the source of truth as well.
    """
    text = _doc(SEAMS_PATH)
    section = text.split("## 이음매 3")[1].split("\n## ")[0]
    documented = set(re.findall(r"^\| `([a-z]+)` \|", section, re.MULTILINE))
    assert (
        set(SCHEMA["$defs"]["status"]["enum"])
        == documented
        == {
            "live",
            "up",
            "down",
            "none",
        }
    )


def test_reason_code_enum_matches_topology():
    """The two files keep the same reason enumeration in step by hand, and a
    mismatch is caught here.

    JSON Schema has no safe way to pull in a `$defs` from another file. Change one
    side only and a reason the engine records becomes invalid in the observation
    document.
    """
    assert (
        SCHEMA["$defs"]["reason"]["properties"]["code"]["enum"]
        == TOPOLOGY_SCHEMA["$defs"]["reason"]["properties"]["code"]["enum"]
    )


def test_resource_type_enum_matches_topology():
    """The two files keep `$defs/resource_type` in step by hand, which is the
    check the `$comment` promises.

    Once the two drift apart, their coverage speaks about **different sets of
    types**: one side can record "this type could not be read" while the other
    cannot record that type at all, and the screen ends up trusting one document
    only.
    """
    assert (
        SCHEMA["$defs"]["resource_type"]["enum"]
        == TOPOLOGY_SCHEMA["$defs"]["resource_type"]["enum"]
    )


def test_coverage_status_enum_matches_topology():
    assert (
        SCHEMA["$defs"]["coverage_entry"]["properties"]["status"]["enum"]
        == TOPOLOGY_SCHEMA["$defs"]["coverage_entry"]["properties"]["status"]["enum"]
    )


def test_adr9_kind_table_is_parseable():
    """If the table cannot be read, the comparison below compares against an empty
    set and **cannot fail**, which is not a pass but a measurement that never
    happened."""
    assert len(adr9_source_kinds()) >= 8, adr9_source_kinds()


def test_adr9_kind_parser_reads_rows_not_prose():
    """Discriminating power: delete a row from the table and that source has to
    disappear from the set."""
    text = _doc(ADR9_PATH)
    without = "\n".join(
        line for line in text.splitlines() if not line.startswith("| `vpc_flow_log` |")
    )
    assert "vpc_flow_log" not in adr9_source_kinds(without)
    assert "vpc_flow_log" in adr9_source_kinds()


def test_source_kind_enum_matches_adr9():
    """The source of truth for this vocabulary is the adoption table in the design
    document, not the schema, which is what that document promises.

    Without this comparison, a source that costs money to read, such as X-Ray or
    Cost Explorer, could slip into the enum. Keeping what the rejected table
    lists out of the vocabulary is the only structural guard behind "nothing gets
    billed by accident"; see the `$comment` on `kind` in the schema.
    """
    assert set(SCHEMA["$defs"]["source"]["properties"]["kind"]["enum"]) == (
        adr9_source_kinds()
    )


def test_source_kind_enum_is_sorted():
    """Out of order, the enum still passes the set comparison above, while the diff
    moves around on every change."""
    enum = SCHEMA["$defs"]["source"]["properties"]["kind"]["enum"]
    assert enum == sorted(enum)


def test_source_scope_unit_is_not_the_resource_type_vocabulary():
    """Merging the two vocabularies would mean **growing the list of resources the
    topology supports just to record an observation source**.

    `$defs/resource_type` is held byte-identical to `topology.schema.json`
    (`test_resource_type_enum_matches_topology`), so if `ecs_cluster` went in
    there, the structure document would also be claiming it can draw ECS clusters. "The unit someone switches on" and "the resource we draw" overlap by
    coincidence; they are not the same axis.
    """
    scope = SCHEMA["$defs"]["source"]["properties"]["scope"]
    assert scope["properties"]["unit"] == {"$ref": "#/$defs/source_scope_unit"}
    units = set(SCHEMA["$defs"]["source_scope_unit"]["enum"])
    structural = set(SCHEMA["$defs"]["resource_type"]["enum"])
    assert units != structural
    # Pins the reason the two could not be shared: these two are **not** in the
    # structural vocabulary.
    #
    # This set used to have four members, with `ec2_instance` and `eks_cluster`
    # also serving as evidence of "not in the structural vocabulary". Both are
    # drawn now and so joined that vocabulary. The resulting overlap is not a
    # defect but an instance of what this check says: they overlap by coincidence
    # and are not the same axis. The two that still do not overlap carry the
    # proof, since an ECS cluster and a Resolver endpoint are units someone
    # switches evidence on for but are not resources we draw.
    only_here = {"ecs_cluster", "resolver_endpoint"}
    assert only_here <= units
    assert not (only_here & structural)


def test_o7_table_is_parseable():
    """If the table cannot be read, the two assertions below compare two empty
    sets and **cannot fail**, which is not a pass but a measurement that never
    happened."""
    assert len(o7_state_source_types()[0]) >= 14, o7_state_source_types()[0]
    assert len(o7_state_source_types()[1]) >= 7, o7_state_source_types()[1]


def test_o7_parser_reads_rows_not_prose():
    """Discriminating power: delete a row from the table and that type has to
    disappear from the set.

    Measured once: deleting one real row (`account` and `region`) from the
    document made four checks below fail, while deleting the **header** row did
    not, because `first_column_types` drops `node_type`, which is correct. That
    demonstration is pinned here with a synthetic document.
    If a refactor turned the parser into something that scrapes backticks out of
    prose, the two checks above would stay green and mean nothing.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 근거 |\n|---|---|\n"
        "| `vpc` | 있다 |\n| `subnet` · `nat_gateway` | 있다 |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 |\n|---|---|\n| `route` | 있다 |\n\n"
        "### 다음 절 — 여기 `peering` 이 산문으로 나온다\n"
    )
    nodes, edges = o7_state_source_types(full)
    assert nodes == {"vpc", "subnet", "nat_gateway"}, nodes
    assert edges == {"route"}, edges

    nodes2, _ = o7_state_source_types(
        full.replace("| `subnet` · `nat_gateway` | 있다 |\n", "")
    )
    assert nodes2 == {"vpc"}, (
        f"the row was deleted yet the parser returned {sorted(nodes2)}; if it is "
        "scraping types out of prose or another column instead of the first, the "
        "coverage checks against this table are green forever"
    )


def test_o7_parser_reads_row_names_that_contain_digits():
    """Discriminating power: miss the digits in a type name and that node quietly
    becomes "no row".

    While the character class was `[a-z_]+`, the coverage check failed with "no
    row" even when the `ec2_instance` row was written **correctly**;
    `adr9_source_kinds()` had hit the same trap earlier. The other direction is
    worse: a typo in a row name after a digit would leave the parser silent.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `ec2_instance` | `State.Name` | 없음 | `live`·`up`·`down` |\n"
        "| `eks_cluster` | `status` | 없음 | `up`·`down` |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 | 최대 어휘 |\n|---|---|---|\n"
        "| `route` | 있다 | `up` |\n\n### 다음 절\n"
    )
    nodes, _ = o7_state_source_types(full)
    assert nodes == {"ec2_instance", "eks_cluster"}, nodes
    assert o7_vocabulary(full)["ec2_instance"] == {"live", "up", "down"}


def test_o7_entry_bearing_parser_reads_the_last_column():
    """Discriminating power: "no state" has to be read from the **last column**
    only.

    The synthetic table sets two traps. The `attachment` row writes "none, the
    node carries it" in the resource-state column and "no state" in the last one.
    The `peering` row does the opposite: its resource-state column contains
    wording that looks like "no state" while its last column is a vocabulary, so a
    parser that scrapes the whole row misses it.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `account` · `region` | — | — | **상태 없음**(컨테이너) |\n"
        "| `vpc` | `State` | 없음 | `up` |\n"
        "| `security_group` | 없음 | 없음 | **`none`** |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 | 최대 어휘 |\n|---|---|---|\n"
        "| `attachment` | 없음 — 노드가 든다 | **상태 없음**(규칙 1) |\n"
        "| `peering` | 상태 없음 같은 문구 | `up`·`down` |\n"
        "| `association←route_table` | `AssociationState.State` | `up`·`down` |\n"
        "| `association←network_acl` | 없음 | **`none`** |\n"
        "\n### 다음 절\n"
    )
    table = o7_vocabulary(full)
    assert table == {
        "account": set(),
        "region": set(),
        "vpc": {"up"},
        "security_group": {"none"},
        "attachment": set(),
        "peering": {"up", "down"},
        # The qualified rows have to arrive **still separated**. Under a single
        # key, a later row overwrites an earlier one, and then recording `up` on a
        # network-ACL association, or `?` on a route-table one, would pass.
        "association←route_table": {"up", "down"},
        "association←network_acl": {"none"},
    }, table
    # Was only the last column read? Scraping the whole row would drop `peering`
    # into the empty set, meaning no entry.
    assert {t for t, allowed in table.items() if allowed} == {
        "vpc",
        "security_group",
        "peering",
        "association←route_table",
        "association←network_acl",
    }


def test_o7_metric_only_parser_reads_only_table_rows():
    """Discriminating power: "metric-layer only" has to be read from **a table
    row** and nowhere else.

    The synthetic table sets three traps. The same phrase appears in the prose, as
    it does in the real document. A row with that phrase in the resource-state
    column (`peering`) is **not** metric-only. Only the row with it in the
    vocabulary column (`attachment`) is. Scrape the prose as well and all three
    kinds become exempt, and then **an edge could stay silent forever** without
    any check going red, which is the exact path back to the end-to-end inference
    that was abandoned.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `vpc` | `State` | 없음 | `up` |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `attachment` | 쓰지 않는다 | `BytesIn` | **2층 전용** · `live`·`up`·`down` |\n"
        "| `peering` | 2층 전용 같은 문구 | 없음 | **상태 없음**(규칙 1) |\n"
        "| `route` | `Routes[].State` | 없음 | `up`·`down` |\n\n"
        "🔴 2층 전용 행은 산문에서도 이렇게 불린다 — 이 줄이 걸리면 파서가 틀렸다.\n"
        "\n### 다음 절\n"
    )
    assert o7_metric_only(full) == {"attachment"}
    # Do the three shapes really separate in the same table? A metric-only row
    # also reports a vocabulary; it is not the empty set.
    table = o7_vocabulary(full)
    assert table["attachment"] == {"live", "up", "down"}
    assert table["peering"] == set()
    assert table["route"] == {"up", "down"}


def test_o7_flow_log_only_parser_reads_only_table_rows():
    """Discriminating power: "flow-log only" is also read from **the last column
    of a table row** and nowhere else, the same trap as in `o7_metric_only`.

    The traps: the phrase in the prose; a row with it in another column, the
    resource-state one (`route`); and the metric-only row (`attachment`), which is
    **not** flow-log only. Scrape the prose and the route line becomes exempt, so
    **a route line with no entry although its resource state was read** would pass
    silently.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `vpc` | `State` | 없음 | `up` |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `attachment` | 쓰지 않는다 | `BytesIn` | **2층 전용** · `live`·`up`·`down` |\n"
        "| `peering` | 노드가 든다 | 없음 | **흐름 로그 전용** · `live` |\n"
        "| `route` | 흐름 로그 전용 같은 문구 | 없음 | `up`·`down` |\n\n"
        "🔴 흐름 로그 전용 행은 산문에서도 이렇게 불린다 — 이 줄이 걸리면 파서가 틀렸다.\n"
        "\n### 다음 절\n"
    )
    assert o7_flow_log_only(full) == {"peering"}
    assert o7_metric_only(full) == {"attachment"}
    table = o7_vocabulary(full)
    assert table["peering"] == {"live"}
    assert table["route"] == {"up", "down"}


def test_o7_entry_bearing_set_is_not_everything():
    """If the set taken from the source of truth were everything, or empty, the
    opposite-direction check below would measure nothing."""
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"]) | set(
        TOPOLOGY_SCHEMA["$defs"]["edge_type"]["enum"]
    )
    assert o7_entry_bearing() < declared, "not a single 'no state' row was filtered out"
    assert len(o7_entry_bearing()) >= 12, sorted(o7_entry_bearing())
    # The ones the table marks as "no state": let these in and a card with no
    # entry becomes a defect.
    assert not o7_entry_bearing() & {"account", "region", "route_table"}
    # A metric-only edge **may create an entry** (`o7_entry_bearing()`) and
    # **need not have one** (outside `o7_required_entry()`). If the two sets
    # became equal, one of those would be false: dropped from the first, the type
    # would be forbidden by the table from carrying an attachment line even when
    # measured; added to the second, a screen with monitoring off would be
    # reported as defective throughout.
    assert o7_metric_only_types() == {
        "attachment",
        "endpoint_connection",
        "load_balancer_placement",
    }, sorted(o7_metric_only_types())
    assert o7_metric_only_types() <= o7_entry_bearing()
    assert not o7_metric_only_types() & o7_required_entry()
    # The peering line moved from "no state" to "flow-log only". The same two
    # directions apply: it may create an entry, when the logs were read and the
    # round trip was found, and it need not have one, when the logs were never
    # enabled or could not be read, which must not report that whole screen as
    # defective.
    assert o7_flow_log_only_types() == {"peering"}, sorted(o7_flow_log_only_types())
    assert o7_flow_log_only_types() <= o7_entry_bearing()
    assert not o7_flow_log_only_types() & o7_required_entry()
    # The two exemptions never mix. If they did, `metric_only_without_a_metric`
    # would either reject the flow-log evidence on a peering or, loosened to avoid
    # that, let an attachment line through with no metric at all.
    assert not o7_flow_log_only_types() & o7_metric_only_types()
    assert o7_required_entry(), (
        "an empty required set would leave the silence check measuring nothing"
    )


def test_o7_covers_every_node_type():
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"])
    missing_rows, orphan_rows = o7_gaps(o7_state_source_types()[0], declared)
    assert not missing_rows, (
        f"node_type values with no row in the state-source table: "
        f"{sorted(missing_rows)}. The table says a type with no row is a defect "
        "rather than a none, and that a new resource gets its row first. Add a "
        "collector without a row and that type quietly carries no state, which the "
        "screen cannot tell apart from 'not observable'"
    )
    assert not orphan_rows, (
        f"types with a row in the table but no node_type enum value: "
        f"{sorted(orphan_rows)}; either the table ran ahead of the code or a type "
        "was renamed"
    )


def test_o7_covers_every_edge_type():
    declared = set(TOPOLOGY_SCHEMA["$defs"]["edge_type"]["enum"])
    missing_rows, orphan_rows = o7_gaps(o7_state_source_types()[1], declared)
    assert not missing_rows, (
        f"edge_type values with no row in the table: {sorted(missing_rows)}"
    )
    assert not orphan_rows, (
        f"rows in the table with no edge_type enum value: {sorted(orphan_rows)}"
    )


def test_o7_splits_association_by_the_response_that_made_it():
    """One edge type has two rows in the table.

    The split exists because of what a person sees. AWS reports a state for a
    route-table-to-subnet association, so a broken one has to be a **red line**;
    AWS reports no state for a network-ACL-to-subnet association, so that one has
    to be a **line with a `?`** at all times. In a single row, one of the two
    becomes false: degrade the first to `?` and an outage cannot be drawn, read
    the second as `up` and the picture looks the same even when every verdict is
    dead.
    """
    assert o7_vocabulary()["association←route_table"] == {"up", "down"}
    assert o7_vocabulary()["association←network_acl"] == {"none"}
    assert "association" not in o7_vocabulary(), (
        "with the base row still present, an edge whose qualifier could not be "
        "read would quietly fall into it"
    )
    # The enum comparison folds to the base type, and the two checks below depend
    # on that folding.
    assert "association" in o7_state_source_types()[1]
    assert not {t for t in o7_state_source_types()[1] if "←" in t}, sorted(
        o7_state_source_types()[1]
    )
    assert "association" in o7_entry_bearing(), (
        "an association line is a type that creates entries"
    )
    # "No row" (None) and "no state" (the empty set) must not be merged: the
    # first is a defect, the second is intended silence.
    assert o7_allowed("association") == {"up", "down", "none"}
    # The representative of "no state" is now a node type; peering moved to
    # flow-log only and reports `live`.
    assert o7_allowed("route_table") == set()
    assert o7_allowed("peering") == {"live"}
    assert o7_allowed("mystery") is None


def test_o7_gap_check_detects_a_type_without_a_row():
    """Discriminating power: add a type without adding a row and this has to fail.

    While this check did not exist, two open changes added four and three
    `node_type` values respectively without touching the table, and nothing went
    red.
    """
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"]) | {
        "gateway_load_balancer"
    }
    missing_rows, orphan_rows = o7_gaps(o7_state_source_types()[0], declared)
    assert missing_rows == {"gateway_load_balancer"}
    assert not orphan_rows


def test_o7_gap_check_detects_an_orphan_row():
    """The opposite direction: a row that exists only in the table has to be
    caught too, which is how a rename arrives."""
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"])
    missing_rows, orphan_rows = o7_gaps(
        o7_state_source_types()[0] | {"vpc_endpoint_service"}, declared
    )
    assert orphan_rows == {"vpc_endpoint_service"}
    assert not missing_rows


def test_the_two_contracts_reject_each_other():
    """If an observation document passed the structure schema, or the other way
    round, the claim that these are two separate contracts would be false."""
    observation = load(FIXTURE_DIR / "minimal.json")
    topology = load(TOPOLOGY_FIXTURE_DIR / "minimal.json")
    assert errors(observation, TOPOLOGY_VALIDATOR), (
        "an observation document passed the structure schema"
    )
    assert errors(topology, VALIDATOR), (
        "a structure document passed the observation schema"
    )


# ---- The fixtures satisfy the contract --------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_validates(path: Path):
    assert errors(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_has_a_topology_pair(path: Path):
    pair = topology_for(path)
    assert pair.exists(), f"{path.name} has no pair {pair.name}"
    assert (
        load(path)["run"]["topology_collected_at"] == load(pair)["run"]["collected_at"]
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_is_sorted(path: Path):
    doc = load(path)
    assert not unsorted(observation_sort_keys(doc)), (
        "observations has to be in ascending target_id order"
    )
    assert not unsorted(change_sort_keys(doc)), (
        "changes has to be in ascending (at, kind, target_id) order"
    )
    assert not unsorted(coverage_sort_keys(doc)), (
        "coverage has to be in ascending (account, Region, type, layer) order"
    )
    assert not unsorted(source_sort_keys(doc)), (
        "sources has to be in ascending kind order"
    )
    assert not unsorted(doc["scope"]["account_ids"])
    assert not unsorted(doc["scope"]["regions"])
    assert not unsorted(doc["scope"]["resource_types"])


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_sources_are_coherent(path: Path):
    """Two rules about sources: the enabled count never exceeds the total, and
    something switched off is never recorded as used."""
    doc = load(path)
    assert sources_over_total(doc) == [], "enabled_ids exceeds total_ids"
    assert sources_used_while_off(doc) == [], "state=off yet used=true"
    assert len({s["kind"] for s in doc["sources"]}) == len(doc["sources"]), (
        "the same kind appears on two rows, so the badge counts one source twice"
    )


def test_the_fixture_set_exercises_every_source_state():
    """Unless all four badge states are reproduced by a fixture, those branches
    are never measured."""
    seen = {(s["state"], s["used"]) for p in FIXTURES for s in load(p)["sources"]}
    assert ("off", False) in seen, "the metrics-only path"
    assert ("on", True) in seen, "the additional-evidence path"
    assert ("on", False) in seen, (
        "the evidence-unreadable path: it was enabled but we could not read it"
    )
    assert ("unknown", False) in seen, "the evidence-unconfirmed path"


def test_some_fixture_reports_a_partial_scope():
    """The path that draws something like "3/7 VPCs". With only all-or-nothing
    fixtures, that branch is never measured."""
    partial = [
        s
        for p in FIXTURES
        for s in load(p)["sources"]
        if "scope" in s and 0 < s["scope"]["enabled_ids"] < s["scope"]["total_ids"]
    ]
    assert partial, "no fixture has a partially enabled scope"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_references_resolve(path: Path):
    doc, topo = load(path), load(topology_for(path))
    assert dangling_observations(doc, topo) == []
    assert dangling_changes(doc, topo) == []


def misshapen_was(doc: dict) -> list[str]:
    """Places where `resource_removed.was` is not the shape of **that row's
    target**.

    The schema requires only the keys the screen needs, so as to avoid a
    cross-file `$ref` (see the `$comment` on `was`). The full shape and identity
    are checked here. Get it wrong and the history comparison view brings back **a
    different card** as the removed one, or the layout function receives a shape
    it does not know and that part of the picture comes out quietly empty.
    """
    defs = TOPOLOGY_SCHEMA["$defs"]
    shape = {
        key: Draft202012Validator({"$defs": defs, "$ref": f"#/$defs/{key}"})
        for key in ("node", "edge")
    }
    bad = []
    for c in doc["changes"]:
        was = c.get("was")
        if was is None:
            continue
        tid = c["target_id"]
        for key, items in (("node", was["nodes"]), ("edge", was["edges"])):
            for item in items:
                bad += [
                    f"{tid}: {key} {e.message}" for e in shape[key].iter_errors(item)
                ]
        if [e["id"] for e in was["edges"]] != sorted(e["id"] for e in was["edges"]):
            bad.append(f"{tid}: edges is not in id order")
        if c["target_kind"] == "node":
            if [n["id"] for n in was["nodes"]] != [tid]:
                bad.append(f"{tid}: nodes is not just that one card")
            bad += [
                f"{tid}: {e['id']} is a line that is not attached to it"
                for e in was["edges"]
                if tid not in (e["source"], e["target"])
            ]
        elif was["nodes"] or [e["id"] for e in was["edges"]] != [tid]:
            bad.append(f"{tid}: the shape of a line row is that one line only")
    return bad


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_was_is_the_shape_of_its_row(path: Path):
    assert misshapen_was(load(path)) == []


def test_some_fixture_carries_a_was_for_a_card_and_for_a_line():
    """Keeps the check above from passing merely because no shape was present at
    all, for a card row and for a line row alike."""
    kinds = {
        c["target_kind"]
        for path in FIXTURES
        for c in load(path)["changes"]
        if c.get("was") and (c["target_kind"] != "node" or c["was"]["edges"])
    }
    assert kinds == {"node", "edge"}, kinds


@pytest.fixture
def hist() -> dict:
    """A copy of minimal.history.json, the only fixture that carries a `was`, as
    used by the history comparison view."""
    return load(FIXTURE_DIR / "minimal.history.json")


def _was_row(doc: dict, kind: str) -> dict:
    return next(c for c in doc["changes"] if c.get("was") and c["target_kind"] == kind)


def test_was_check_detects_another_cards_shape(hist):
    _was_row(hist, "node")["was"]["nodes"][0]["id"] = (
        "nat_gateway/111122223333/us-east-1/nat-0aaa1111bbbb0000"
    )
    assert misshapen_was(hist)


def test_was_check_detects_a_line_that_did_not_touch_the_card(hist):
    edge = _was_row(hist, "node")["was"]["edges"][0]
    edge["target"] = edge["id"] = (
        "subnet/111122223333/us-east-1/subnet-0aaa1111bbbb4444"
    )
    assert misshapen_was(hist)


def test_was_check_detects_a_shape_the_topology_contract_rejects(hist):
    del _was_row(hist, "node")["was"]["nodes"][0]["account_id"]
    assert misshapen_was(hist)


def test_was_check_detects_a_line_row_carrying_a_card(hist):
    row = _was_row(hist, "edge")
    row["was"]["nodes"] = [_was_row(hist, "node")["was"]["nodes"][0]]
    assert misshapen_was(hist)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_coverage_matches_scope(path: Path):
    doc = load(path)
    assert coverage_holes(doc) == [], (
        "cells of scope x layer that are missing from coverage"
    )
    assert unrequested_coverage(doc) == [], (
        "coverage contains a cell that was never requested"
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_does_not_claim_what_it_could_not_read(path: Path):
    doc, topo = load(path), load(topology_for(path))
    assert claims_without_reading(doc, topo) == []
    assert live_without_metric_layer(doc) == []
    assert metric_rows_not_skipped_while_off(doc) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_says_something_about_what_it_did_read(path: Path):
    """The opposite direction: a target of a type that was read must not be empty
    on screen.

    Measuring only the first direction makes "silent about everything" a perfect
    score. The difference matters: if types we have permission for also come out
    grey, the screen shows "we could read nothing" and "we could not read the
    subnets only" in exactly the same way.
    """
    doc, topo = load(path), load(topology_for(path))
    assert silent_where_read(doc, topo, o7_required_entry()) == KNOWN_SILENT.get(
        path.name, []
    )


# **Known gaps** that an engine-produced fixture revealed; a fixed one is deleted
# from this table, and the comparison is exact, so growing or shrinking fails.
# It is empty at the moment. The load balancer to VPC Lambda line in
# `lb-targets.json` (`edge-nlb` to `orders-fn`) used to be here and was fixed by
# making the state tier attribute the shared ENI as a function of the structure
# document, which makes that line's row appear; the engine then rewrote the
# fixture.
KNOWN_SILENT: dict[str, list[str]] = {}


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_states_stay_inside_the_o7_table(path: Path):
    """A fixture never records a state from outside the table.

    While this gate did not exist, the fixtures carried five entries that
    **contradicted** the table, and with that data the `?` path appeared to be
    measured. A person would see a `?` on a connection AWS does report a state
    for, and a green on an attachment line that has no state at all, both of them
    drawing facts that do not exist.
    """
    doc, topo = load(path), load(topology_for(path))
    assert states_outside_the_table(doc, topo) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_item_counts_add_up(path: Path):
    """The numbers in coverage agree with the actual number of entries.

    When they are out of step, a person sees a summary saying "9 of 12 were read"
    next to a screen that does not show nine. There is no way to tell which is right from
    the screen alone, and most people believe the summary.
    """
    doc, topo = load(path), load(topology_for(path))
    written, priced, unpriced = item_count_ledger(doc, topo)
    # Metric-only and flow-log-only rows do not count towards a resource-state
    # cell, because the ledger skips them, so they are added separately here.
    exempt = sum(
        1
        for o in doc["observations"]
        if o["target_id"].split("|")[0]
        in o7_metric_only_types() | o7_flow_log_only_types()
    )
    assert written == priced, (
        f"the coverage total {written} does not match the {priced} entries that "
        "belong to a cell"
    )
    assert priced + unpriced + exempt == len(doc["observations"])


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_metric_item_counts_add_up(path: Path):
    """The metric numbers agree with the actual number of entries as well.

    While only the resource-state layer was measured, the metric `item_count` was
    **compared against nothing**: the summary could say "12 confirmed by metrics"
    while only three cards on screen cited any evidence, and nothing complained.
    """
    written, cited = metric_item_count_ledger(load(path))
    assert written == cited, (
        f"the metric coverage total {written} does not match the {cited} entries "
        "that cite a metric"
    )


def test_some_fixture_carries_a_metric_only_edge():
    """Discriminating power, part one: **at least one** fixture has to carry a
    metric-only edge row.

    This kind is outside the required set of `silent_where_read`, since it is
    correctly silent while the metric layer is off. Because of that exemption, the
    central idea of judging an edge by a segment metric **could go untouched by
    every fixture**, and then a pass measures nothing. What a person would see:
    monitoring is on, yet the attachment, endpoint and load balancer placement
    lines stay grey forever, and no check goes red.
    """
    carried = {
        path.name: sorted(
            {
                o["target_id"].split("|")[0]
                for o in load(path)["observations"]
                if o["target_id"].split("|")[0] in o7_metric_only_types()
            }
        )
        for path in FIXTURES
    }
    assert any(carried.values()), carried


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_metric_only_edges_must_cite_a_metric(path: Path):
    """Discriminating power, part two: the evidence on a metric-only edge row has
    to be **a metric**.

    This check protects the point of the whole arrangement. A metric-only edge row
    made without a metric is by definition "both ends are alive, so traffic
    probably flows between them", which is the end-to-end inference that was
    rejected as not what this tool is meant to do.
    """
    assert metric_only_without_a_metric(load(path)) == []


def test_metric_only_check_detects_an_edge_made_without_a_metric():
    """Discriminating power: break it in three ways to confirm the check above
    really catches them.

    Without this, that check is indistinguishable from "passes when there are no
    metric-only rows at all". In terms of what a person sees: monitoring is on, an
    attachment line is green, and the only thing behind that green is "both ends
    are alive". If that comes back, this check has to go red.
    """
    base = load(FIXTURE_DIR / "minimal.json")
    metric_only = [
        o
        for o in base["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    ]
    assert metric_only, (
        "minimal.json lost its metric-only edges, which leaves this negative test "
        "measuring nothing"
    )

    no_evidence = deepcopy(base)
    target = next(
        o
        for o in no_evidence["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    )
    target["evidence"] = None
    assert metric_only_without_a_metric(no_evidence), (
        "missed a segment verdict that cites no evidence"
    )

    describe_api = deepcopy(base)
    target = next(
        o
        for o in describe_api["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    )
    target["evidence"]["api"] = "elasticloadbalancing:DescribeLoadBalancers"
    assert metric_only_without_a_metric(describe_api), (
        "missed a segment verdict disguised as a resource-state API call"
    )

    wrong_word = deepcopy(base)
    target = next(
        o
        for o in wrong_word["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    )
    target["status"] = "none"
    assert metric_only_without_a_metric(wrong_word), (
        "missed a state from outside the vocabulary of the table"
    )


def test_metric_ledger_check_detects_a_drifted_item_count():
    """Discriminating power: confirm the metric ledger really catches a drift.

    Editing `item_count` by hand used to go unnoticed. What a person would see:
    the pill along the top says "12 confirmed by metrics" while only three lines
    on screen cite any evidence.
    """
    base = load(FIXTURE_DIR / "minimal.json")
    assert metric_item_count_ledger(base)[0] > 0, (
        "if every metric cell in minimal.json is 0, the edit below only moves it "
        "from 0 to 1, which barely discriminates"
    )
    drifted = deepcopy(base)
    row = next(c for c in drifted["coverage"] if c["layer"] == "metric")
    row["item_count"] = (row["item_count"] or 0) + 1
    written, cited = metric_item_count_ledger(drifted)
    assert written != cited


def test_some_fixture_has_an_entry_outside_every_cell():
    """Discriminating power: with no entries outside every cell, the second term of
    the equation above measures nothing.

    Entries outside every cell are the `?` on security groups, network ACLs and
    security group references. If no fixture has one, the equation degenerates into
    `total == everything` and breaks quietly the day a type nobody asks for arrives.
    """
    outside = {
        path.name: item_count_ledger(load(path), load(topology_for(path)))[2]
        for path in FIXTURES
    }
    assert any(n > 0 for n in outside.values()), outside


def test_the_fixture_set_exercises_all_three_columns_of_o7():
    """Discriminating power: the gate above has to go through **all three**
    branches. With only one branch covered, a pass measures nothing.

    The branches are a verdict (`up`, `down`, `live`); `none`, where the entry
    exists and carries a `?`; and no state at all, where there is no entry. The
    third one is confirmed by absence: a type the table marks as having no state
    has to be **present in the structure** and missing from the observations,
    which is what rule 1 says.
    """
    judged, silent_types = set(), set()
    for path in FIXTURES:
        doc, topo = load(path), load(topology_for(path))
        for obs in doc["observations"]:
            judged.add(obs["status"])
        have = {o["target_id"] for o in doc["observations"]}
        for target_id, o7_type in ((n["id"], n["type"]) for n in topo["nodes"]):
            if o7_allowed(o7_type) == set() and target_id not in have:
                silent_types.add(o7_type)
        for edge in topo["edges"]:
            # The qualifier is not known here, so this looks at the union of the
            # base-form lookups. `association` is not stateless, because one of
            # its qualified rows does yield a vocabulary, and it correctly does
            # not count as silent here.
            if o7_allowed(edge["type"]) == set() and edge["id"] not in have:
                silent_types.add(edge["type"])
    assert {"up", "down", "live"} <= judged, sorted(judged)
    assert "none" in judged, (
        "no fixture carries a `?`, which leaves the `none` path measuring nothing"
    )
    assert len(silent_types) >= 3, sorted(silent_types)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_history_is_consistent(path: Path):
    doc = load(path)
    assert ts(doc["history"]["baseline_at"]) <= ts(doc["run"]["observed_at"])
    assert out_of_window_changes(doc) == []
    assert collapsed_below_threshold(doc) == []
    assert outside_retention_window(doc) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_marks_truncation_consistently(path: Path):
    """`truncated` has to agree with the retention window and the cap on the number
    of entries, which is the direction `truncation_mismatch` can measure from the
    document.

    The retention window used to be a number that was merely carried along. No
    assertion read it, so a document could hold both `truncated: true` and a
    `baseline_at` inside the window, and that contradiction passed.
    """
    assert truncation_mismatch(load(path)) is None


def test_the_approved_defaults_table_is_parseable():
    """If the table cannot be read, the tests above end up comparing against an
    empty dict and passing without measuring anything.

    So this checks that all four cells are there, and that the schema really does
    require those fields.
    """
    assert set(approved_defaults()) == {
        "resolution_seconds",
        "retention_seconds",
        "flap_collapse_threshold",
        "max_changes",
    }
    assert set(approved_defaults()) <= set(SCHEMA["$defs"]["history"]["required"])


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_uses_the_approved_defaults(path: Path):
    """If a fixture drifts from the approved numbers, the control group no longer
    reflects what was approved."""
    assert defaults_drift(load(path), approved_defaults()) == {}


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_observations_are_in_scope(path: Path):
    doc, topo = load(path), load(topology_for(path))
    assert out_of_scope_observations(doc, topo) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_timestamps_are_rfc3339(path: Path):
    doc = load(path)
    ts(doc["run"]["observed_at"])
    ts(doc["run"]["topology_collected_at"])
    ts(doc["history"]["baseline_at"])
    for change in doc["changes"]:
        ts(change["at"])
        if "occurred_at" in change:
            ts(change["occurred_at"])
    for obs in doc["observations"]:
        evidence = obs.get("evidence") or {}
        for key in ("first_flow_at", "last_flow_at"):
            if key in evidence:
                ts(evidence[key])


def test_some_fixture_exercises_every_status():
    """All four words have to appear in the fixtures. A word no fixture carries
    goes unmeasured in the renderer."""
    seen = {o["status"] for path in FIXTURES for o in load(path)["observations"]}
    assert seen == {"live", "up", "down", "none"}


def test_some_fixture_exercises_every_change_kind():
    seen = {c["kind"] for path in FIXTURES for c in load(path)["changes"]}
    assert seen == {"resource_added", "resource_removed", "status_changed"}


def test_some_fixture_turns_the_metric_layer_off():
    """Without a fixture on the metric-layer-off axis, every rule along that axis
    measures nothing rather than passing."""
    assert any(not load(path)["layers"]["metric"] for path in FIXTURES)


def test_some_fixture_collapses_a_flap():
    assert any(
        c["repeat_count"] > 1 for path in FIXTURES for c in load(path)["changes"]
    )


def test_some_fixture_is_truncated():
    assert any(load(path)["history"]["truncated"] for path in FIXTURES)


# ---- discriminating power of the schema assertions ---------------------------


def test_schema_rejects_unknown_top_level_key(doc):
    doc["latest"] = {}
    assert errors(doc)


def test_schema_rejects_a_fifth_status_word(doc):
    doc["observations"][0]["status"] = "unknown"
    assert errors(doc), "a fifth status word passed"


def test_schema_rejects_live_without_metric_evidence(doc):
    obs = doc["observations"][0]
    obs["status"] = "live"
    obs["evidence"] = {
        "api": "ec2:DescribeRouteTables",
        "metric": None,
        "datapoints": None,
    }
    assert errors(doc), "a live with no evidence passed"


def test_schema_rejects_live_with_zero_datapoints(doc):
    obs = doc["observations"][0]
    obs["status"] = "live"
    obs["evidence"] = {
        "api": "cloudwatch:GetMetricData",
        "metric": "BytesIn",
        "datapoints": 0,
    }
    assert errors(doc), "a live with zero datapoints passed, and that is an up"


def test_schema_rejects_none_without_why(doc):
    # This does not go looking for a `none` in the fixtures. If the check depended
    # on which fixture happens to carry a `?`, it would die quietly the day a
    # cleanup of the table removes that `none`, which is exactly what happened
    # once before.
    obs = doc["observations"][0]
    obs["status"] = "none"
    obs["evidence"] = None
    obs["error_count"] = None
    obs["why"] = "AWS does not report a state for this target"
    assert not errors(doc), (
        "the control group is already rejected, so the assertion below cannot "
        "measure `why`"
    )
    obs["why"] = None
    assert errors(doc), "a `?` with no reason passed"


def test_schema_rejects_partial_without_reasons(doc):
    doc["observations"][0]["partial"] = True
    assert errors(doc)


def test_schema_rejects_missing_error_count(doc):
    del doc["observations"][0]["error_count"]
    assert errors(doc), "if the omission passes, the renderer reads absence as zero"


def test_schema_rejects_missing_repeat_count(doc):
    del doc["changes"][0]["repeat_count"]
    assert errors(doc)


def _folded(doc: dict) -> dict:
    """One folded state row: the `repeat_count` 6 row in minimal.down.json, in the
    older shape that carries no start."""
    return next(c for c in doc["changes"] if c["repeat_count"] > 1)


def test_schema_accepts_a_folded_row_with_or_without_its_start(doc):
    """The start is an **optional key**. Without it the answer is "not known", as
    for rows folded before 1.7.0; with it, both halves have to be there."""
    assert errors(doc) == []  # the older shape, with no start, is valid too
    row = _folded(doc)
    row["first_at"], row["first_from"] = "2026-01-02T02:49:05Z", "down"
    assert errors(doc) == []
    assert fold_start_after_last(doc) == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("first_from"), id="start-time-only"),
        pytest.param(lambda r: r.pop("first_at"), id="start-state-only"),
        pytest.param(lambda r: r.update(first_from="flapping"), id="not-a-state"),
        pytest.param(lambda r: r.update(first_at=None), id="null"),
        pytest.param(lambda r: r.update(repeat_count=1), id="unfolded-row"),
    ],
)
def test_schema_rejects_a_malformed_fold_start(doc, mutate):
    row = _folded(doc)
    row["first_at"], row["first_from"] = "2026-01-02T02:49:05Z", "down"
    mutate(row)
    assert errors(doc), "a malformed start on a folded repeat passed"


def test_schema_rejects_a_fold_start_on_a_configuration_change(doc):
    row = next(c for c in doc["changes"] if c["kind"] == "resource_added")
    row["first_at"], row["first_from"] = "2026-01-02T02:49:05Z", "up"
    assert errors(doc), "a configuration change picked up a fold start"


def test_fold_start_check_detects_a_start_not_before_the_last(doc):
    row = _folded(doc)
    row["first_at"], row["first_from"] = row["at"], "down"
    assert fold_start_after_last(doc) == [row["target_id"]]
    assert (
        errors(doc) == []
    )  # JSON Schema cannot measure this ordering, hence the pure function


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_fold_starts_before_its_last_change(path: Path):
    assert fold_start_after_last(load(path)) == []


def test_some_fixture_carries_a_fold_start():
    """A precondition: one fixture has to hold a folded row with a start, or the
    check above measures nothing. It is also the input to the history view."""
    assert any("first_at" in c for path in FIXTURES for c in load(path)["changes"])


def test_schema_rejects_status_change_without_both_ends(doc):
    change = next(c for c in doc["changes"] if c["kind"] == "status_changed")
    change["from"] = None
    assert errors(doc), "a status change with no earlier end passed"


def test_schema_rejects_config_change_carrying_a_status(doc):
    change = next(c for c in doc["changes"] if c["kind"] == "resource_added")
    change["to"] = "up"
    assert errors(doc), "a configuration change picked up a status"


def test_schema_rejects_describe_layer_off(doc):
    doc["layers"]["describe"] = False
    assert errors(doc), "an observation with no resource-state layer passed"


# ---- optional evidence sources -----------------------------------------------


def a_source(doc: dict, kind: str) -> dict:
    return next(s for s in doc["sources"] if s["kind"] == kind)


def test_schema_rejects_missing_sources(doc):
    """If the omission passes, "we never looked" and "everything is off" become the
    same document.

    The first has no business showing a pill at all and the second has to show
    "metrics only". Writing the key down, even as an empty array, is what keeps
    them apart.
    """
    del doc["sources"]
    assert errors(doc)


def test_schema_rejects_a_ninth_source_kind(doc):
    """If a source outside the vocabulary passes, **a source that costs money to
    read** can arrive without anyone noticing."""
    a_source(doc, "vpc_flow_log")["kind"] = "xray_traces"
    assert errors(doc)


def test_schema_rejects_a_fourth_source_state(doc):
    a_source(doc, "vpc_flow_log")["state"] = "partial"
    assert errors(doc), "the state vocabulary is no longer three values"


def test_schema_rejects_an_unknown_source_without_why(doc):
    del a_source(doc, "eks_network_policy_log")["why"]
    assert errors(doc), (
        "if an unknown with no reason passes, the `?` on screen is decoration"
    )


def test_schema_rejects_a_source_used_while_off(doc):
    a_source(doc, "container_insights")["used"] = True
    assert errors(doc), "if a source that is off can be recorded as used, the pill lies"


def test_schema_rejects_an_unread_source_without_why(doc):
    """`state: on` with `used: false` covers **two cases that call for opposite
    things from the customer**.

    "The bucket is closed to us" is fixed by opening the policy; "this version
    does not read it" needs nothing at all. With no reason, the pill raises a
    warning without saying what to fix.
    """
    del a_source(doc, "tgw_flow_log")["why"]
    assert errors(doc)


def test_schema_rejects_a_source_on_with_nothing_enabled(doc):
    """A source that is on with zero resources enabled makes the screen draw
    "0/1 VPCs on"."""
    a_source(doc, "vpc_flow_log")["scope"]["enabled_ids"] = 0
    assert errors(doc)


def test_schema_rejects_a_resource_type_as_a_source_scope_unit(doc):
    """A structure resource word cannot serve as a scope unit. The two
    vocabularies belong to different axes."""
    a_source(doc, "vpc_flow_log")["scope"]["unit"] = "security_group"
    assert errors(doc)


def test_schema_rejects_a_scope_without_the_total(doc):
    """With no denominator, all that is left is "3 VPCs on", and nobody can tell
    whether that is all of them."""
    del a_source(doc, "vpc_flow_log")["scope"]["total_ids"]
    assert errors(doc)


def test_source_sort_check_detects_disorder(doc):
    doc["sources"].reverse()
    assert unsorted(source_sort_keys(doc))


def test_source_scope_check_detects_more_enabled_than_total(doc):
    """A comparison between sibling values, which the schema cannot make. This
    check is the only thing guarding it."""
    a_source(doc, "vpc_flow_log")["scope"]["enabled_ids"] = 99
    assert errors(doc) == [], (
        "if the schema catches it first, this check's discriminating power goes "
        "unmeasured"
    )
    assert sources_over_total(doc) == ["vpc_flow_log"]


def test_source_off_check_detects_a_used_source(doc):
    """Discriminating power for `sources_used_while_off`: the same rule, measured
    outside the schema as well."""
    a_source(doc, "container_insights")["used"] = True
    assert sources_used_while_off(doc) == ["container_insights"]


def test_schema_rejects_a_wrong_schema_version(doc):
    doc["schema_version"] = "2.0.0"
    assert errors(doc)


def test_schema_rejects_missing_history(doc):
    del doc["history"]
    assert errors(doc), "changes cannot ride along without the basis for the diff"


def test_schema_rejects_a_resource_type_as_a_layer(doc):
    doc["coverage"][0]["layer"] = "vpc"
    assert errors(doc), "a resource type landed where a layer belongs"


def test_schema_rejects_failure_without_reason(doc):
    doc["coverage"][0]["status"] = "denied"
    doc["coverage"][0]["reason"] = None
    assert errors(doc)


# ---- discriminating power of the structural checks ---------------------------


def test_sort_check_detects_disorder(doc):
    doc["observations"].reverse()
    assert unsorted(observation_sort_keys(doc))


def test_change_sort_check_detects_disorder(doc):
    doc["changes"].reverse()
    assert unsorted(change_sort_keys(doc))


def _one_run_add_and_remove(at: str) -> list[dict]:
    """One added and one removed in a single run, where the removed one has the
    **smaller** id, so that the `kind` axis is what decides the order."""
    row = {"at": at, "target_kind": "node", "from": None, "to": None, "repeat_count": 1}
    return [
        {
            **row,
            "kind": "resource_added",
            "target_id": "nat_gateway/111122223333/us-east-1/nat-0fff9999",
        },
        {
            **row,
            "kind": "resource_removed",
            "target_id": "nat_gateway/111122223333/us-east-1/nat-0aaa1111",
        },
    ]


def test_change_sort_key_is_the_documented_order():
    """The helper's key is the `(at, kind, target_id)` the schema writes down. If
    the document and the helper drift apart, this is where it fails."""
    comment = SCHEMA["$defs"]["history"]["properties"]["max_changes"]["$comment"]
    assert f"({', '.join(CHANGE_SORT_FIELDS)})" in comment
    rows = _one_run_add_and_remove("2026-09-01T00:05:00Z")
    assert change_sort_keys({"changes": rows}) == [
        tuple(c[f] for f in CHANGE_SORT_FIELDS) for c in rows
    ]


def test_change_sort_accepts_an_add_and_a_remove_in_one_run(doc):
    """A document correctly sorted in engine order, with one added and one removed
    in a single run, has to **pass** the sort check.

    The old key `(at, target_id)` called this document out of order, because the
    removed row has the smaller id. As a control: swapping those two rows, so the
    removal comes first, is reported as out of order.
    """
    at = doc["changes"][-1]["at"]
    later = (
        datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(minutes=5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _one_run_add_and_remove(later)
    doc["changes"] = doc["changes"] + rows
    assert not unsorted(change_sort_keys(doc))
    doc["changes"] = doc["changes"][:-2] + rows[::-1]
    assert unsorted(change_sort_keys(doc))


def test_coverage_sort_check_detects_disorder(doc):
    doc["coverage"].reverse()
    assert unsorted(coverage_sort_keys(doc))


def test_reference_check_detects_a_dangling_observation(doc, topo):
    doc["observations"][0]["target_id"] = "route/rtb-0aaa1111bbbb6666/10.0.0.0_8"
    assert dangling_observations(doc, topo)


def test_reference_check_detects_a_mislabelled_kind(doc, topo):
    doc["observations"][0]["target_kind"] = "node"
    assert dangling_observations(doc, topo), "an edge recorded as a node passed"


def test_reference_check_detects_a_dangling_added_resource(doc, topo):
    change = next(c for c in doc["changes"] if c["kind"] == "resource_added")
    change["target_id"] = "subnet/111122223333/us-east-1/subnet-0nonexistent"
    assert dangling_changes(doc, topo)


def test_reference_check_tolerates_a_removed_resource(doc, topo):
    """What is gone is not in the structure snapshot. Treating that as a dangling
    reference would leave no way to record that something was removed."""
    removed = [c for c in doc["changes"] if c["kind"] == "resource_removed"]
    assert removed, "measuring the exemption path needs at least one resource_removed"
    assert all(c["target_id"] not in topology_index(topo) for c in removed)
    assert dangling_changes(doc, topo) == []


def test_coverage_check_detects_a_hole(doc):
    doc["coverage"].pop()
    assert coverage_holes(doc)


def test_coverage_check_detects_an_unrequested_cell(doc):
    doc["coverage"].append(dict(doc["coverage"][0], region="eu-west-1"))
    assert unrequested_coverage(doc)


def a_claimed_type(doc: dict, topo: dict) -> str:
    """One type that actually has an entry carrying a verdict. Without one, the
    control group below measures nothing.

    Since the type axis arrived, breaking the first coverage row **quietly kills
    the control group**: if the type on that first row has no entries at all, the
    suppression check has nothing to look at and passes. So the cell to break is
    picked backwards, starting from the entries.
    """
    kinds = resource_types(topo)
    for obs in doc["observations"]:
        if obs["status"] != "none" and obs["target_id"] in kinds:
            return kinds[obs["target_id"]]
    raise AssertionError(
        "no entry carries a verdict, so this fixture cannot measure the "
        "suppression direction"
    )


def test_order_of_judgement_check_detects_a_claim_on_a_denied_region(doc, topo):
    """Discriminating power for turning "not known" into "broken": writing a
    verdict on a type that could not be read has to be caught."""
    kind = a_claimed_type(doc, topo)
    describe = next(
        c
        for c in doc["coverage"]
        if c["layer"] == "describe" and c["resource_type"] == kind
    )
    describe["status"] = "denied"
    describe["reason"] = {
        "code": "access_denied",
        "api": "sts:AssumeRole",
        "message": None,
    }
    assert claims_without_reading(doc, topo)


def test_order_of_judgement_check_detects_live_without_metric_coverage(doc, topo):
    obs = next(
        o
        for o in doc["observations"]
        if resource_types(topo).get(o["target_id"]) == a_claimed_type(doc, topo)
    )
    metric = next(
        c
        for c in doc["coverage"]
        if c["layer"] == "metric"
        and c["resource_type"] == resource_types(topo)[obs["target_id"]]
    )
    metric["status"] = "throttled"
    metric["reason"] = {
        "code": "throttled",
        "api": "cloudwatch:GetMetricData",
        "message": None,
    }
    obs["status"] = "live"
    obs["evidence"] = {
        "api": "cloudwatch:GetMetricData",
        "metric": "BytesIn",
        "datapoints": 3,
        "window_seconds": 300,
    }
    assert claims_without_reading(doc, topo)


def test_order_of_judgement_check_exempts_structurally_unobservable(doc, topo):
    """A `none` can be true regardless of permissions. Without the exemption, grey
    and `?` get mixed together."""
    describe = next(c for c in doc["coverage"] if c["layer"] == "describe")
    describe["status"] = "denied"
    describe["reason"] = {
        "code": "access_denied",
        "api": "sts:AssumeRole",
        "message": None,
    }
    doc["observations"] = [o for o in doc["observations"] if o["status"] == "none"]
    assert claims_without_reading(doc, topo) == []


def test_entry_check_detects_silence_where_it_read(doc, topo):
    """Discriminating power for the opposite direction: deleting an entry of a type
    that was read has to be caught.

    Without this counterpart,
    `test_fixture_says_something_about_what_it_did_read` above could pass merely
    because there were no targets to begin with, which is the same trap as in the
    suppression direction.
    """
    dropped = next(
        o for o in doc["observations"] if o["target_id"].startswith("subnet/")
    )
    doc["observations"] = [
        o for o in doc["observations"] if o["target_id"] != dropped["target_id"]
    ]
    assert silent_where_read(doc, topo, o7_required_entry()) == [dropped["target_id"]]


def test_entry_check_tolerates_silence_where_it_could_not_read(doc, topo):
    """A control: delete the same entry but also mark its type denied, and silence
    is now the **right answer**.

    This is where the difference a person sees comes from. A gap caused by missing
    permission has to read as "we could not check this"; a gap where we do have
    permission is a defect. The two checks have to move in opposite directions on
    the same data.
    """
    dropped = next(
        o for o in doc["observations"] if o["target_id"].startswith("subnet/")
    )
    doc["observations"] = [
        o for o in doc["observations"] if o["target_id"] != dropped["target_id"]
    ]
    for row in doc["coverage"]:
        if row["resource_type"] == "subnet" and row["layer"] == "describe":
            row["status"] = "denied"
            row["reason"] = {
                "code": "access_denied",
                "api": "ec2:DescribeSubnets",
                "message": None,
            }
            row["item_count"] = None
    assert silent_where_read(doc, topo, o7_required_entry()) == []


def test_a_line_to_a_stateless_lambda_still_needs_its_row(doc, topo):
    """A load balancer to Lambda line (`load_balancer_target`) **still needs its
    row**, even when the card at the far end (`lambda_function`) has no state.

    The cell a line belongs to is that load balancer's `load_balancer` cell, whose
    evidence is its `DescribeTargetHealth` call, not the type of the card at the
    far end. Exempting it by that card's type would leave the contract checks
    silent the next time the engine loses the line, since the state tier does not
    read Lambda, and the customer would turn monitoring on and still see nothing
    on that line. The function card itself, on the other hand, has to have no row.
    """
    lb_id = next(n["id"] for n in topo["nodes"] if n["type"] == "load_balancer")
    lb_node = next(n for n in topo["nodes"] if n["id"] == lb_id)
    fn = f"lambda_function/{lb_node['account_id']}/{lb_node['region']}/orders-fn"
    topo["nodes"].append(
        {
            "id": fn,
            "type": "lambda_function",
            "account_id": lb_node["account_id"],
            "region": lb_node["region"],
            "parent_id": f"region/{lb_node['account_id']}/{lb_node['region']}",
            "native_id": "orders-fn",
            "name": "orders-fn",
            "attributes": {"function_name": "orders-fn"},
            "partial": False,
        }
    )
    line = f"load_balancer_target|{lb_id}|{fn}"
    topo["edges"].append(
        {
            "id": line,
            "type": "load_balancer_target",
            "source": lb_id,
            "target": fn,
            "directed": True,
            "attributes": {"target_count": 1},
            "partial": False,
        }
    )
    assert silent_where_read(doc, topo, o7_required_entry()) == [line]
    doc["observations"].append(
        {
            "target_id": line,
            "target_kind": "edge",
            "status": "up",
            "why": None,
            "evidence": {
                "api": "elasticloadbalancing:DescribeTargetHealth",
                "metric": None,
                "datapoints": None,
                "window_seconds": None,
            },
            "error_count": 0,
            "partial": False,
            "targets": {"total": 1, "healthy": 0, "unhealthy": 0},
        }
    )
    doc["observations"].sort(key=lambda o: o["target_id"])
    assert silent_where_read(doc, topo, o7_required_entry()) == []
    assert states_outside_the_table(doc, topo) == []
    assert errors(doc) == []
    # Attaching a row to the function card breaks the "no state" rule: the line and
    # the card would state the same evidence twice.
    doc["observations"].append(
        {**doc["observations"][-1], "target_id": fn, "target_kind": "node"}
    )
    assert [p.split(":")[0] for p in states_outside_the_table(doc, topo)] == [fn]


def test_ledger_check_detects_a_drifted_item_count(doc, topo):
    """Raising only the number by one has to be caught. Deleting an entry instead
    lowers both sides and leaves the equation intact."""
    row = next(
        c for c in doc["coverage"] if c["layer"] == "describe" and c["status"] == "ok"
    )
    row["item_count"] += 1
    written, priced, _ = item_count_ledger(doc, topo)
    assert written != priced


def test_table_check_detects_a_state_on_a_no_state_type(doc, topo):
    """Rule 1 of the table: creating an entry on a type marked "no state" is
    caught.

    What a person would see: the same fact rides in **two places**, the node card
    and the attachment line, and once only one of them is updated the screen
    contradicts itself, with no way to tell which half is right.
    """
    no_state = next(n["id"] for n in topo["nodes"] if o7_allowed(n["type"]) == set())
    doc["observations"][0]["target_id"] = no_state
    doc["observations"][0]["target_kind"] = "node"
    assert states_outside_the_table(doc, topo)


def test_table_check_detects_a_word_the_table_did_not_allow(doc, topo):
    """Writing `live` on a type the table gives only `up` is caught. The
    vocabulary differs per type."""
    subnet = next(
        o for o in doc["observations"] if o["target_id"].startswith("subnet/")
    )
    # Subnets gained a transitional `down`, and the point of the control group,
    # that the table has no `live` for them, is unchanged.
    assert "live" not in o7_vocabulary()["subnet"], (
        "the premise has changed, so pick a different control group"
    )
    subnet["status"] = "live"
    assert states_outside_the_table(doc, topo)


def _association(doc: dict, source_type: str) -> dict:
    """One association entry that came out of that type's response. If there is
    none, the assertion is toothless, so this fails here instead."""
    found = [
        o
        for o in doc["observations"]
        if o["target_id"].startswith(f"association|{source_type}/")
    ]
    assert found, (
        f"no fixture has an association entry coming from {source_type}, which "
        "leaves the control group below passing for want of an entry; network ACLs "
        "were in exactly that state once, appearing in no fixture at all"
    )
    return found[0]


def test_table_check_detects_a_question_mark_on_a_route_table_association(doc, topo):
    """One direction of the split table: degrading an association that can be
    judged into a `?` is caught.

    What a person would see: a route table association breaks and the screen keeps
    saying "cannot be checked". A line that could have shown the break stays grey
    forever, and on screen that is indistinguishable from the judgement code being
    dead. This direction is why the alternative of merging both cases into a
    single `none`/`up`/`down` row was dropped, since a merged row lets this
    degradation through.
    """
    _association(doc, "route_table")["status"] = "none"
    assert states_outside_the_table(doc, topo)


def test_table_check_detects_a_judgement_on_a_nacl_association(doc, topo):
    """The other direction: writing a verdict on an association AWS reports no
    state for is caught.

    What a person would see: the network ACL to subnet line is **green forever**.
    It reads a constant the collector filled in (`associated`), so whatever
    actually happens, that line never changes.
    """
    _association(doc, "network_acl")["status"] = "up"
    assert states_outside_the_table(doc, topo)


def _deleting_subnet(doc: dict) -> dict:
    """Turn one subnet row into "being deleted", which the table says is not a
    `down`."""
    row = next(o for o in doc["observations"] if o["target_id"].startswith("subnet/"))
    row.update(
        status="none",
        why="being deleted; AWS reported state = 'deleting'",
        error_count=0,
        deleting=True,
    )
    return row


def test_a_deleting_row_is_none_and_passes_the_table(doc, topo):
    """Being deleted is `none` plus `deleting`, which passes both the schema and
    the table oracle. This is the control group."""
    _deleting_subnet(doc)
    assert errors(doc) == []
    assert states_outside_the_table(doc, topo) == []


@pytest.mark.parametrize("status", ["down", "up"])
def test_a_deleting_row_cannot_carry_a_judgement(doc, topo, status):
    """With `down`, a resource someone deliberately deleted reads as an outage
    again; with `up`, a resource on its way out is drawn as alive."""
    _deleting_subnet(doc)["status"] = status
    assert errors(doc)
    assert any(
        "being deleted, yet" in row for row in states_outside_the_table(doc, topo)
    )


def test_deleting_false_is_not_a_second_spelling(doc):
    """Absence already means "not being deleted". Accepting `false` as well would
    give one fact two byte representations."""
    _deleting_subnet(doc)["deleting"] = False
    assert errors(doc)


def test_a_deleting_row_on_a_none_only_type_is_caught(doc, topo):
    """A type that can only ever report `none`, such as a network ACL, has no
    response exposing a deletion lifecycle, so "being deleted" would be made up."""
    nacl = next(
        o for o in doc["observations"] if o["target_id"].startswith("network_acl/")
    )
    nacl["deleting"] = True
    assert any(
        "has no resource-state verdict" in row
        for row in states_outside_the_table(doc, topo)
    )


def test_table_check_detects_a_type_without_a_row(doc, topo):
    """A type with no row in the table is a **defect**, not a `none`.

    Let it through quietly and, the day a new resource type arrives, that one type
    alone is drawn without a state, which the screen cannot tell apart from "there
    is no way to observe this".
    """
    doc["observations"][0]["target_id"] = "mystery|a|b"
    doc["observations"][0]["target_kind"] = "edge"
    assert any(
        "a type with no row in the table" in row
        for row in states_outside_the_table(doc, topo)
    )


def test_metric_layer_check_detects_live_while_off(doc):
    doc["layers"]["metric"] = False
    doc["observations"][0]["status"] = "live"
    assert live_without_metric_layer(doc)


def test_metric_layer_check_detects_a_non_skipped_row_while_off(doc):
    doc["layers"]["metric"] = False
    assert metric_rows_not_skipped_while_off(doc), (
        "the metric layer is off, yet a cell recorded as ok passed"
    )


def test_window_check_detects_a_change_before_the_baseline(doc):
    # This is computed from the fixture's own timestamps. A hard-coded date would
    # kill this counterpart the moment the baseline moves, which happened once,
    # when adding the retention-window assertion moved the baseline out of range.
    doc["changes"][0]["at"] = stamp(ts(doc["history"]["baseline_at"]) - DAY)
    assert out_of_window_changes(doc)


def test_window_check_detects_a_change_after_the_observation(doc):
    doc["changes"][0]["at"] = "2027-01-01T00:00:00Z"
    assert out_of_window_changes(doc)


def test_retention_check_detects_a_change_older_than_the_window(doc):
    # The baseline sits outside the window in minimal.down, so the window sets the
    # lower bound, and a change before it should have been dropped.
    doc["changes"][0]["at"] = stamp(window_start(doc) - DAY)
    assert outside_retention_window(doc)


def test_truncation_check_detects_a_list_longer_than_the_cap(doc):
    # Set the cap one below the current number of rows. If the overflowing rows are
    # still there, the cap is a number that is merely carried along. The older
    # counterpart, "true while inside the window", became a legitimate path once a
    # record dropped by the cap started being carried over.
    assert doc["changes"], "with zero rows this counterpart has no discriminating power"
    doc["history"]["max_changes"] = len(doc["changes"]) - 1 or 1
    if len(doc["changes"]) == 1:
        doc["changes"].append(copy.deepcopy(doc["changes"][0]))
    assert truncation_mismatch(doc)


def test_truncation_check_allows_a_cap_flag_inside_the_window(doc):
    # The other side of the discriminating power: a record dropped by the cap is
    # true even inside the window, and rejecting that would block the engine.
    doc["history"]["baseline_at"] = doc["changes"][0]["at"]
    doc["history"]["truncated"] = True
    assert truncation_mismatch(doc) is None


def test_truncation_check_detects_a_missing_flag(doc):
    # The other direction: the record starts outside the window, yet truncated is
    # false.
    doc["history"]["truncated"] = False
    assert truncation_mismatch(doc)


def test_defaults_check_detects_a_drifted_number(doc):
    # Derived from the approved value. A hard-coded number would kill this
    # counterpart quietly the day that number becomes the approved one.
    doc["history"]["retention_seconds"] = approved_defaults()["retention_seconds"] + 1
    assert defaults_drift(doc, approved_defaults())


def test_flap_check_detects_a_collapse_below_the_threshold(doc):
    doc["changes"][0]["repeat_count"] = 2
    assert collapsed_below_threshold(doc)


def test_scope_check_detects_an_observation_outside_scope(doc, topo):
    doc["scope"]["regions"] = ["eu-west-1"]
    assert out_of_scope_observations(doc, topo)


def test_place_parser_reports_failure_instead_of_guessing():
    """When the place cannot be read the answer is None. Smoothing it over with a 0
    or an empty tuple would let the checks above pass quietly."""
    assert place_of("account/111122223333") == ("111122223333", None)
    assert place_of("region/111122223333/us-east-1") == ("111122223333", "us-east-1")
    assert place_of("vpc/111122223333/us-east-1/vpc-0aaa") == (
        "111122223333",
        "us-east-1",
    )
    # This only discriminates with an edge id **the engine actually produces**. The
    # earlier assertion used a made-up slash form
    # (`association/rtb-0aaa/subnet-0bbb`) with only three segments, which any
    # implementation would return None for.
    assert (
        place_of(
            "association|route_table/111122223333/us-east-1/rtb-0aaa"
            "|subnet/111122223333/us-east-1/subnet-0bbb"
        )
        is None
    )


def test_pair_resolution_detects_a_wrong_topology_timestamp(doc):
    doc["run"]["topology_collected_at"] = "2020-01-01T00:00:00Z"
    assert (
        doc["run"]["topology_collected_at"]
        != load(TOPOLOGY_FIXTURE_DIR / "minimal.json")["run"]["collected_at"]
    )


# ------------------------------------------------------------------- id syntax
#
# What went unmeasured while this section did not exist: `target_id` enforced
# **only the node id pattern** (`^[a-z0-9_]+/...`), and since the engine separates
# edge ids with `|`, **no edge observation could pass validation at all.** This
# file was nevertheless 187 green, because the fixtures wrote a slash form the
# engine never produces (`association/rtb-.../subnet-...`), so the fixtures and the
# schema agreed with **each other and nothing else**. The defect only surfaced once
# engine/tests/test_observe.py started validating real output.
# The lesson: when a contract fixture uses a different syntax from production
# output, its green measures nothing.

NODE_ID_RE = re.compile(SCHEMA["$defs"]["node_id"]["pattern"])
EDGE_ID_RE = re.compile(SCHEMA["$defs"]["edge_id"]["pattern"])


def engine_edge_id(edge: dict) -> str:
    """A copy of `_edge_id()` in engine/src/nettopo/collect.py.

    The contract layer does not import production code, which is why this is a
    copy. The engine half is held up instead by
    `test_observation_satisfies_the_schema` in engine/tests/test_observe.py, which
    validates real output against this schema, so if the two syntaxes drift apart
    one of the two is guaranteed to fail.
    """
    parts = [edge["type"], edge["source"], edge["target"]]
    tail = edge["id"].split("|")[3:]
    return "|".join(parts + tail)


def test_id_patterns_match_the_topology_contract():
    """`node_id` and `edge_id` have to be **byte-identical** in both schemas.

    JSON Schema cannot follow a `$ref` across file boundaries without a resolver,
    hence the copy. Editing only one side gives "the structure is accepted and the
    observation is rejected", and the symptom is edge observations quietly
    disappearing.
    """
    for name in ("node_id", "edge_id"):
        assert name in TOPOLOGY_SCHEMA["$defs"], (
            f"the topology contract has no definition for {name}"
        )
        assert (
            SCHEMA["$defs"][name]["pattern"]
            == TOPOLOGY_SCHEMA["$defs"][name]["pattern"]
        ), f"the {name} pattern differs between the two schemas"
    # Discriminating power: the two patterns really have to diverge. Identical
    # patterns would render every kind check below toothless.
    node = "vpc/111122223333/us-east-1/vpc-0aaa"
    edge = f"association|{node}|{node}"
    assert NODE_ID_RE.fullmatch(node) and not EDGE_ID_RE.fullmatch(node)
    assert EDGE_ID_RE.fullmatch(edge) and not NODE_ID_RE.fullmatch(edge)


@pytest.mark.parametrize(
    "path", sorted(TOPOLOGY_FIXTURE_DIR.glob("*.json")), ids=lambda p: p.name
)
def test_topology_fixture_edge_ids_are_what_the_engine_produces(path: Path):
    """A fixture edge id is `<type>|<source>|<target>[|<qualifier>]`.

    While this did not exist, three edges in fixtures/minimal.json were
    hand-written values and fixtures/multi-account.json had **duplicate ids**: the
    same string for different accounts, which the engine syntax keeps apart.
    Duplicate ids quietly fold into one in the dict `topology_index()` builds,
    which disables the reference checks.
    """
    edges = load(path)["edges"]
    wrong = [
        (e["id"], engine_edge_id(e)) for e in edges if e["id"] != engine_edge_id(e)
    ]
    assert wrong == [], (
        f"{len(wrong)} edge ids differ from the engine syntax: {wrong[:2]}"
    )
    ids = [e["id"] for e in edges]
    assert len(ids) == len(set(ids)), (
        "edge ids are duplicated, which quietly disables the reference checks"
    )
    assert all(EDGE_ID_RE.fullmatch(i) for i in ids)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_target_id_syntax_agrees_with_target_kind(path: Path):
    """Catch an id whose syntax disagrees with what `target_kind` says. Both
    directions are measured."""
    doc = load(path)
    rows = [
        (row["target_id"], row["target_kind"])
        for row in doc["observations"] + doc["changes"]
    ]
    kinds = {kind for _, kind in rows}
    # A guard on discriminating power: with only one kind present, the loop below
    # covers half of what it should and the rest goes unmeasured.
    assert kinds == {"node", "edge"}, f"{path.name} only has {sorted(kinds)}"
    for target_id, kind in rows:
        expected = NODE_ID_RE if kind == "node" else EDGE_ID_RE
        other = EDGE_ID_RE if kind == "node" else NODE_ID_RE
        assert expected.fullmatch(target_id), (
            f"a {kind} with the wrong syntax: {target_id}"
        )
        assert not other.fullmatch(target_id), f"this passes both syntaxes: {target_id}"


def test_schema_rejects_a_node_pattern_only_target_id(doc):
    """A regression brake for the defect described above: if an edge observation is
    rejected, this has to fail.

    It reverts the schema, pointing `target_id` at node_id alone, and measures
    whether that state really does break the fixtures. If it does not, there are no
    edge observations in the fixtures, and this file is back to measuring nothing.
    """
    edges = [o for o in doc["observations"] if o["target_kind"] == "edge"]
    assert edges, "with no edge observations this brake measures nothing"
    broken = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    broken["$defs"]["target_id"] = {"$ref": "#/$defs/node_id"}
    assert Draft202012Validator(broken).iter_errors(doc), (
        "the fixture passed even with node_id alone allowed, so the edge id syntax "
        "has become the same as a node's"
    )


def test_schema_rejects_an_edge_id_with_a_pipe_in_a_node_segment():
    """A control group for the separator being unique: a `|` inside a node id makes
    parsing an edge id ambiguous."""
    assert not NODE_ID_RE.fullmatch("vpc/111122223333/us-east-1/vpc-0a|b")
    assert not EDGE_ID_RE.fullmatch(
        "association|vpc/111122223333/us-east-1/vpc-0a|b|vpc/111122223333/us-east-1/v"
    )


def test_place_of_check_detects_an_edge_id(doc, topo):
    """Catch `place_of` reading an edge id as though it were a node, by imitating
    the state where that guard has been deleted."""
    edge = next(e["id"] for e in topo["edges"])
    assert place_of(edge) is None
    # Without the guard (`"|" in node_id`), parsing on `/` **does extract** an
    # account and a Region, which is the reason this check exists.
    parts = edge.split("/")
    assert re.fullmatch(r"[0-9]{12}", parts[1]) and REGION_RE.match(parts[2]), (
        "if an edge id does not read as an account and a Region, this brake is a "
        "dead line"
    )


# ---- flow log evidence, where stage one says only that traffic flowed --------
#
# In terms of what a person sees: a green on the line from a public subnet to its
# internet gateway has to mean there was a record of traffic leaving **that
# subnet** and coming back. A green lit by a record from the neighbouring subnet
# that shares a route table, a green left on screen after the logs were turned
# off, and a green lit by logs that could not be read are all false. What follows
# measures whether the schema catches those three within a single row, and whether
# `flow_log_problems` catches them by holding two documents side by side.


def _row(doc: dict, prefix: str) -> dict:
    return next(o for o in doc["observations"] if o["target_id"].startswith(prefix))


def _resource(doc: dict, rid: str) -> dict:
    return next(
        r for s in doc["sources"] for r in s.get("resources", []) if r["id"] == rid
    )


@pytest.fixture
def mfl() -> tuple[dict, dict]:
    """The internet gateway shape: one public subnet made a round trip through the
    gateway, and the same VPC has two logs, one of them a duplicate."""
    return load(FIXTURE_DIR / "minimal.flow-log.json"), load(
        TOPOLOGY_FIXTURE_DIR / "minimal.json"
    )


@pytest.fixture
def pfl() -> tuple[dict, dict]:
    """The peering shape: a subnet with an explicit association (SA1), a subnet on
    the main table (SA2), and the far side (SB1, with no record)."""
    return load(FIXTURE_DIR / "peering.flow-log.json"), load(
        TOPOLOGY_FIXTURE_DIR / "peering.json"
    )


PFL_SA1 = "subnet/111122223333/us-east-1/subnet-0bbb1111cccc0011"
PFL_SA2 = "subnet/111122223333/us-east-1/subnet-0bbb1111cccc0012"
PFL_VA = "vpc/111122223333/us-east-1/vpc-0bbb1111cccc0001"
PFL_RA0 = "route|route_table/111122223333/us-east-1/rtb-0bbb1111cccc0030|"
PFL_RA1 = "route|route_table/111122223333/us-east-1/rtb-0bbb1111cccc0031|"
PFL_RB0 = "route|route_table/111122223333/us-east-1/rtb-0bbb1111cccc0040|"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_flow_log_evidence_agrees_with_structure(path: Path):
    """Flow-log evidence agrees with the structure and the log list: in an off
    fixture because there is no evidence, in an on fixture because it agrees."""
    assert flow_log_problems(load(path), load(topology_for(path))) == []


def test_some_fixture_carries_every_flow_log_shape():
    """Discriminating power: every shape needs a fixture of its own, or the check
    above measures nothing.

    The shapes are subnet evidence on an internet gateway route; flow on the
    gateway card itself; subnet evidence on a peering route, both through an
    explicit association and through the main table; the peering line; a log list
    entry that was read and one that was not; a log covered by a duplicate; and a
    log at network-interface level. Miss any one of them and breaking that shape
    turns no check red.
    """
    seen: set[str] = set()
    for path in FIXTURES:
        doc, topo = load(path), load(topology_for(path))
        edges = {e["id"]: e for e in topo["edges"]}
        for o in doc["observations"]:
            if o.get("by_subnet"):
                target = edges[o["target_id"]]["target"].split("/")[0]
                seen.add(f"by_subnet→{target}")
                users = route_table_users(topo)[edges[o["target_id"]]["source"]]
                explicit = {
                    e["target"]
                    for e in topo["edges"]
                    if e["type"] == "association"
                    and e["source"] == edges[o["target_id"]]["source"]
                }
                for item in o["by_subnet"]:
                    assert item["subnet_id"] in users
                    seen.add("explicit" if item["subnet_id"] in explicit else "main")
                # A precondition for the sibling check: a neighbouring subnet that
                # uses the same table and has no item, whose line has to stay as
                # it is.
                if users - {i["subnet_id"] for i in o["by_subnet"]}:
                    seen.add("sibling_without_entry")
            if (o.get("evidence") or {}).get("metric") == FLOW_LOG:
                seen.add(o["target_id"].split("/")[0].split("|")[0] + ":flow_log")
        for r in (r for s in doc["sources"] for r in s.get("resources", [])):
            seen.add(f"used={r['used']}")
            seen.add(f"level={r['level']}")
            if r.get("why_code"):
                seen.add(r["why_code"])
    assert {
        "by_subnet→internet_gateway",
        "by_subnet→vpc_peering_connection",
        "explicit",
        "main",
        "internet_gateway:flow_log",
        "peering:flow_log",
        "used=True",
        "used=False",
        "level=vpc",
        "level=network_interface",
        "duplicate_covered",
        "sibling_without_entry",
    } <= seen, sorted(seen)


def test_flow_log_fixtures_are_the_off_documents_plus_the_reader():
    """The screen with the feature off looks exactly as it did before: strip the
    reader's contribution out of the on fixture and the off fixture is what is left.

    This is a design decision. A customer who leaves the feature off sees today's
    screen. If the on fixture differs from the off one in **anything beyond the
    reader's contribution**, such as row states, item counts or source sentences,
    then these fixtures cannot say "turning it on changes the screen by exactly
    this much".
    """
    on, off = (
        load(FIXTURE_DIR / "minimal.flow-log.json"),
        load(FIXTURE_DIR / "minimal.json"),
    )
    del on["layers"]["flow_log"]
    for o in on["observations"]:
        o.pop("by_subnet", None)
    igw = _row(on, "internet_gateway/")
    igw.update(_row(off, "internet_gateway/"))
    on["sources"] = off["sources"]
    assert on == off


def test_flow_log_check_detects_evidence_while_the_layer_is_off(mfl):
    """Flow-log evidence and a log list are left in a document with the layer off,
    so a customer who turned it off still sees a green."""
    doc, topo = mfl
    del doc["layers"]["flow_log"]
    assert errors(doc) == [], (
        "the schema allows the layer key to be absent, which is why this check is "
        "needed"
    )
    problems = flow_log_problems(doc, topo)
    assert any("flow-log evidence" in p for p in problems), problems
    assert any("a log list" in p for p in problems), problems


def test_flow_log_check_detects_a_list_while_the_layer_is_off(mfl):
    """The evidence is stripped out and only the log list is left, so the pill
    still says the source was used."""
    doc, topo = mfl
    off = load(FIXTURE_DIR / "minimal.json")
    del doc["layers"]["flow_log"]
    for o in doc["observations"]:
        o.pop("by_subnet", None)
    _row(doc, "internet_gateway/").update(_row(off, "internet_gateway/"))
    assert flow_log_problems(doc, topo) == [
        "vpc_flow_log: a log list while the layer is off"
    ]


def test_flow_log_check_detects_a_repeated_subnet(pfl):
    """The same subnet is written twice, so its round trips are counted twice."""
    doc, topo = pfl
    row = _row(doc, PFL_RA1)
    row["by_subnet"] = row["by_subnet"] * 2
    assert any("ascending, unique order" in p for p in flow_log_problems(doc, topo))


def test_flow_log_check_detects_a_subnet_that_does_not_use_the_table(pfl):
    """The central case: a line for this table was lit by a record from a subnet
    that uses a different one.

    SA1 uses RA1 through an **explicit association**. Writing SA1 against RA0, the
    main table of the same VPC, is false, because a main table serves only the
    subnets with no explicit association. Miss this case and every subnet in the
    VPC can light the main table's line green.
    """
    doc, topo = pfl
    row = _row(doc, PFL_RA0)
    row["by_subnet"][0]["subnet_id"] = PFL_SA1
    row["by_subnet"][0]["evidence"]["observed_on"] = PFL_SA1
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert any("does not use this route table" in p for p in problems), problems


def test_flow_log_check_detects_a_subnet_of_the_other_vpc(pfl):
    """A subnet from this side is written against the main table of the VPC on the
    far side."""
    doc, topo = pfl
    row = _row(doc, PFL_RB0)
    row["by_subnet"] = deepcopy(_row(doc, PFL_RA0)["by_subnet"])
    assert any(
        "does not use this route table" in p for p in flow_log_problems(doc, topo)
    )


def test_flow_log_check_detects_a_pcx_item_seen_elsewhere(pfl):
    """The where-it-was-seen of a peering route item is not that subnet, which
    makes the explanation on screen false."""
    doc, topo = pfl
    _row(doc, PFL_RA1)["by_subnet"][0]["evidence"]["observed_on"] = PFL_SA2
    assert errors(doc) == []
    assert any("is not that subnet" in p for p in flow_log_problems(doc, topo))


def test_flow_log_check_detects_an_igw_card_without_a_subnet_proof(mfl):
    """The internet gateway card is green while not a single subnet line towards
    that gateway is proven."""
    doc, topo = mfl
    for o in doc["observations"]:
        o.pop("by_subnet", None)
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert any("towards this internet gateway" in p for p in problems), problems


def test_flow_log_check_detects_an_igw_card_seen_on_another_subnet(mfl):
    """The where-it-was-seen of the internet gateway card is not one of the proven
    subnets."""
    doc, topo = mfl
    _row(doc, "internet_gateway/")["evidence"]["observed_on"] = (
        "subnet/111122223333/us-east-1/subnet-0aaa1111bbbb4444"
    )
    assert errors(doc) == []
    assert any(
        "towards this internet gateway" in p for p in flow_log_problems(doc, topo)
    )


def test_flow_log_check_detects_a_peering_line_without_a_subnet_proof(pfl):
    """The peering line is green while there is no subnet evidence towards that
    peering."""
    doc, topo = pfl
    for o in doc["observations"]:
        o.pop("by_subnet", None)
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert sum("towards this peering" in p for p in problems) == 2, problems


def _peering_seen_on_the_vpc(doc: dict) -> dict:
    """A document that proves only the peering, using a VPC log that cannot
    attribute a subnet. This is the one VPC path the design allows."""
    for o in doc["observations"]:
        o.pop("by_subnet", None)
        if o["target_id"].startswith("peering|"):
            o["evidence"]["observed_on"] = PFL_VA
    log = _resource(doc, "fl-0bbb1111cccc0061")
    log["why_code"] = "subnet_unattributable"
    log["why"] = "the log has no subnet-id field, so it cannot attribute a subnet"
    return doc


def test_flow_log_check_accepts_a_peering_line_seen_on_a_vpc(pfl):
    """A control group: a peering seen through a VPC log that was read but cannot
    attribute a subnet is accepted."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    assert errors(doc) == []
    assert flow_log_problems(doc, topo) == []


def test_flow_log_check_detects_a_vpc_proof_without_an_unattributable_log(pfl):
    """A VPC was written down while that VPC's log can attribute a subnet, in which
    case the subnet should have been written down instead."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    log = _resource(doc, "fl-0bbb1111cccc0061")
    del log["why_code"], log["why"]
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert sum("could not attribute a subnet" in p for p in problems) == 2, problems


def test_flow_log_check_detects_a_vpc_proof_from_an_unread_log(pfl):
    """A VPC was written down while the log attached to it **could not be read**,
    which is a green lit by a log nobody read."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    log = _resource(doc, "fl-0bbb1111cccc0061")
    log.update(used=False, why_code="access_denied", why="no permission to read it")
    doc["sources"][0]["used"] = False
    problems = flow_log_problems(doc, topo)
    assert sum("could not attribute a subnet" in p for p in problems) == 2, problems


def test_flow_log_check_detects_a_vpc_proof_from_a_vpc_off_the_peering(pfl):
    """A VPC was written down that is neither side of this peering."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    elsewhere = "vpc/111122223333/us-east-1/vpc-0bbb1111cccc0009"
    topo["nodes"].append(
        {**next(n for n in topo["nodes"] if n["id"] == PFL_VA), "id": elsewhere}
    )
    for o in doc["observations"]:
        if o["target_id"].startswith("peering|"):
            o["evidence"]["observed_on"] = elsewhere
    _resource(doc, "fl-0bbb1111cccc0061")["attached_to"] = elsewhere
    problems = flow_log_problems(doc, topo)
    assert sum("could not attribute a subnet" in p for p in problems) == 2, problems


def test_flow_log_check_detects_an_unsorted_log_list(pfl):
    """The order of the log list, which the determinism invariant depends on: the
    same input has to produce the same bytes."""
    doc, topo = pfl
    doc["sources"][0]["resources"].reverse()
    assert any("ascending, unique id order" in p for p in flow_log_problems(doc, topo))


def test_flow_log_check_detects_a_source_used_flag_that_disagrees(pfl):
    """The source says it was not used while the list holds a log that was read, so
    the pill and the lines say different things."""
    doc, topo = pfl
    doc["sources"][0]["used"] = False
    doc["sources"][0]["why"] = "it was not read"
    assert errors(doc) == []
    assert any(
        "used flag disagrees with the list" in p for p in flow_log_problems(doc, topo)
    )


def test_flow_log_check_detects_a_log_attached_to_nothing(pfl):
    """What the log is attached to is missing from the structure, so the screen
    cannot attach that log to any VPC."""
    doc, topo = pfl
    _resource(doc, "fl-0bbb1111cccc0062")["attached_to"] = (
        "vpc/111122223333/us-east-1/vpc-0bbb1111cccc0009"
    )
    assert errors(doc) == []
    assert any(
        "which is not in the structure" in p for p in flow_log_problems(doc, topo)
    )


def test_flow_log_check_detects_a_subnet_proof_without_a_log_that_covers_it(mfl):
    """A subnet was proven while no log that was read can attribute it.

    If a VPC log was read without a `subnet-id` field (`subnet_unattributable`),
    that log cannot light a subnet line, since doing so would make every subnet in
    the VPC a candidate for green.
    """
    doc, topo = mfl
    log = _resource(doc, "fl-0aaa1111bbbb0a01")
    log["why_code"] = "subnet_unattributable"
    log["why"] = "the log has no subnet-id field"
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert any("covers this subnet" in p for p in problems), problems


def test_flow_log_check_accepts_an_eni_log_that_carries_the_subnet(pfl):
    """A control group: a log at network-interface level also attributes a subnet,
    as long as it has the subnet-id field."""
    doc, topo = pfl
    eni, vpc = (
        _resource(doc, "fl-0bbb1111cccc0060"),
        _resource(doc, "fl-0bbb1111cccc0061"),
    )
    eni["used"] = True
    del eni["why_code"], eni["why"]
    vpc.update(
        used=False,
        why_code="format_v2",
        why="the default format has no route-number field",
    )
    assert errors(doc) == []
    assert flow_log_problems(doc, topo) == []


# ---- flow logs: the shapes the schema rejects within a single row ------------


def test_schema_rejects_by_subnet_off_a_route_row(pfl):
    """Per-subnet evidence somewhere other than a route row, on an association. An
    association line is never judged from a flow log."""
    doc, _ = pfl
    _row(doc, "association|")["by_subnet"] = deepcopy(_row(doc, PFL_RA1)["by_subnet"])
    assert errors(doc)


def test_schema_rejects_by_subnet_on_a_route_to_another_target(pfl):
    """A route towards something other than an internet gateway or a peering, which
    is a segment the stage-one reader does not prove."""
    doc, _ = pfl
    row = _row(doc, PFL_RA1)
    row["target_id"] = row["target_id"].split("|vpc_peering_connection/")[0] + (
        "|transit_gateway/111122223333/us-east-1/tgw-0bbb1111cccc0070"
    )
    assert errors(doc)


def test_schema_rejects_by_subnet_on_a_down_route(mfl):
    """Flow evidence on a row whose resource state is blocked. The resource state
    wins, so that green and red never overlap on one line."""
    doc, _ = mfl
    down = load(FIXTURE_DIR / "minimal.down.json")
    route = _row(doc, "route|")
    route.update({k: v for k, v in _row(down, "route|").items() if k != "target_id"})
    assert route["status"] == "down", (
        "minimal.down.json lost its blocked route, which kills the control group"
    )
    assert "by_subnet" in route, (
        "the update above also wiped the subnet evidence, leaving this check "
        "nothing to measure"
    )
    control = deepcopy(doc)
    del _row(control, "route|")["by_subnet"]
    assert errors(control) == []
    assert errors(doc)


def test_schema_rejects_an_igw_item_with_observed_on(mfl):
    """An internet gateway route item is grade A, and attaching a
    where-it-was-seen makes it read as grade B."""
    doc, _ = mfl
    item = _row(doc, "route|")["by_subnet"][0]
    item["evidence"]["observed_on"] = item["subnet_id"]
    assert errors(doc)


def test_schema_rejects_a_pcx_item_without_observed_on(pfl):
    """A peering route item is grade B, and dropping the fact that it was seen from
    one side makes it read as grade A."""
    doc, _ = pfl
    del _row(doc, PFL_RA1)["by_subnet"][0]["evidence"]["observed_on"]
    assert errors(doc)


def test_schema_rejects_a_peering_row_without_flow_log_evidence(pfl):
    """A peering line is flow-log only. A peering green written from a
    resource-state API call is end-to-end inference."""
    doc, _ = pfl
    _row(doc, "peering|")["evidence"] = {
        "api": "ec2:DescribeVpcPeeringConnections",
        "metric": None,
        "datapoints": None,
        "window_seconds": None,
    }
    assert errors(doc)


def test_schema_rejects_a_flow_log_row_that_is_not_live(mfl):
    """A flow log says only that traffic flowed, so flow-log evidence cannot carry
    an up or a down."""
    doc, _ = mfl
    _row(doc, "internet_gateway/")["status"] = "up"
    assert errors(doc)


def test_schema_rejects_a_flow_log_row_that_is_not_an_igw_or_a_peering(mfl):
    """Flow-log evidence belongs only on an internet gateway card and a peering
    line. A subnet card is never lit from a flow log."""
    doc, _ = mfl
    subnet = _row(doc, "subnet/")
    subnet["status"] = "live"
    subnet["evidence"] = deepcopy(_row(doc, "internet_gateway/")["evidence"])
    assert errors(doc)


def test_schema_rejects_the_layer_written_as_false(mfl):
    """A layer that is off **has no key**. Allowing `false` as well would give a
    document with the layer off two possible sets of bytes."""
    doc, _ = mfl
    doc["layers"]["flow_log"] = False
    assert errors(doc)


def test_schema_rejects_the_flow_log_layer_without_the_metric_layer(mfl):
    """Flow logs sit on top of the metric layer, so there is no combination with
    metrics off and flow logs on."""
    doc, _ = mfl
    doc["layers"]["metric"] = False
    assert [e for e in errors(doc) if "layers" in e]


def test_schema_rejects_a_log_list_on_an_off_source():
    """A log list on a source that is off, which is a document saying off while
    holding a log that was read."""
    doc = load(FIXTURE_DIR / "minimal.json")
    source = next(s for s in doc["sources"] if s["kind"] == "vpc_flow_log")
    assert source["state"] == "off"
    source["resources"] = [
        {
            "id": "fl-0aaa1111bbbb0a01",
            "level": "vpc",
            "used": False,
            "why_code": "format_v2",
            "why": "x",
        }
    ]
    assert errors(doc)


def test_schema_rejects_an_unread_log_without_a_reason(pfl):
    """It could not be read and there is no reason, so the customer cannot tell
    whether to open a policy or wait for the tool."""
    doc, _ = pfl
    del _resource(doc, "fl-0bbb1111cccc0062")["why_code"]
    assert errors(doc)


def test_schema_rejects_a_read_log_with_a_cannot_read_reason(pfl):
    """It was read, yet the reason given is Parquet. A log marked as read cannot
    carry a reason that means not one line could be read."""
    doc, _ = pfl
    log = _resource(doc, "fl-0bbb1111cccc0061")
    log.update(why_code="format_parquet", why="Parquet")
    assert errors(doc)


def test_schema_rejects_an_eni_log_attached_to_a_node(pfl):
    """An attachment target on a log at network-interface level, which the engine
    never draws in the structure."""
    doc, _ = pfl
    _resource(doc, "fl-0bbb1111cccc0060")["attached_to"] = PFL_SA1
    assert errors(doc)


def test_schema_rejects_a_log_attached_to_the_wrong_kind_of_node(pfl):
    """It claims VPC level while being attached to a subnet, so the screen uses
    that log to explain the wrong line."""
    doc, _ = pfl
    _resource(doc, "fl-0bbb1111cccc0061")["attached_to"] = PFL_SA1
    assert errors(doc)


def test_schema_rejects_a_tgw_level_line_in_the_vpc_source(pfl):
    """A row at transit gateway level inside the VPC flow log source, which makes
    the pill count one log under two sources."""
    doc, _ = pfl
    log = _resource(doc, "fl-0bbb1111cccc0060")
    log["level"] = "transit_gateway"
    assert errors(doc)


def test_schema_rejects_the_withdrawn_reader_off_code(pfl):
    """`reader_off` was withdrawn: with the feature off there is no log list at
    all."""
    doc, _ = pfl
    _resource(doc, "fl-0bbb1111cccc0062")["why_code"] = "reader_off"
    assert errors(doc)
    assert "reader_off" not in SCHEMA["$defs"]["flow_log_why_code"]["enum"]


# ---- utilization -------------------------------------------------------------
#
# What JSON Schema cannot see: ordering and uniqueness; whether the node is in the
# structure; agreement with layers.utilization; whether a window length and its
# step go together; whether a measure goes with its node type and unit; and
# average being at most peak. Pure functions extract those, and negative
# counterparts pin down their discriminating power.

UTIL_FIXTURE = FIXTURE_DIR / "minimal.utilization.json"

# Window length in minutes to the step in seconds that the busiest moment is
# measured at, which is CloudWatch's retention resolution.
STEP_FOR_MINUTES = {15: 60, 60: 60, 1440: 60, 10080: 60, 43200: 300, 129600: 3600}
# The short windows, recomputed every minute, where computed_at is the
# observed_at of that run.
SHORT_WINDOWS = {15, 60, 1440}
# EC2 basic monitoring reports a point every five minutes, so for EC2 no window
# has a step finer than 300.
EC2_MIN_STEP = 300

# Measure to the node types it can attach to and its unit. The value-only ones,
# whose limit is unknown, are here too.
MEASURE_TABLE: dict[str, tuple[set[str], str]] = {
    "bandwidth": (
        {
            "transit_gateway_attachment",
            "direct_connect_connection",
            "direct_connect_virtual_interface",
            "network_firewall",
            "nat_gateway",
            "vpc_endpoint",
            "load_balancer",
            "ec2_instance",
            "rds_db_instance",
            "cloudfront",
            "vpn_connection",
            "api_gateway",
            "elasticache_cluster",
            "global_accelerator",
        },
        "bit_per_second",
    ),
    "packet_rate": ({"transit_gateway_attachment"}, "packet_per_second"),
    # Usage: a service with no byte count reports a number of calls, and RDS
    # reports its connection count as a secondary measure.
    "invocations": ({"lambda_function"}, "count_per_second"),
    "requests": ({"api_gateway"}, "count_per_second"),
    "connections": ({"rds_db_instance"}, "count"),
    "capacity": ({"load_balancer"}, "percent"),
    "rejected_connections": ({"load_balancer"}, "count"),
    "port_allocation_errors": ({"nat_gateway", "load_balancer"}, "count"),
    "packets_dropped": ({"vpc_endpoint"}, "count"),
    "ip_addresses": ({"subnet"}, "count"),
    "security_group_rules": ({"security_group"}, "count"),
    "network_acl_entries": ({"network_acl"}, "count"),
    "routes_ipv4": ({"route_table"}, "count"),
    "routes_ipv6": ({"route_table"}, "count"),
    "routes_propagated": ({"route_table"}, "count"),
    "vpc_ipv4_cidrs": ({"vpc"}, "count"),
    "vpc_ipv6_cidrs": ({"vpc"}, "count"),
    "vpc_subnets": ({"vpc"}, "count"),
    "vpc_network_acls": ({"vpc"}, "count"),
    "vpc_route_tables": ({"vpc"}, "count"),
    "vpc_interface_endpoints": ({"vpc"}, "count"),
    "vpc_active_peerings": ({"vpc"}, "count"),
    "tgw_attachments": ({"transit_gateway"}, "count"),
    "tgw_route_tables": ({"transit_gateway"}, "count"),
    "tgw_peering_attachments": ({"transit_gateway"}, "count"),
    "tgw_routes": ({"transit_gateway"}, "count"),
    "dxgw_virtual_interfaces": ({"direct_connect_gateway"}, "count"),
    "dxgw_virtual_private_gateways": ({"direct_connect_gateway"}, "count"),
    "dxgw_transit_gateways": ({"direct_connect_gateway"}, "count"),
}


def _measures(doc: dict):
    for item in doc.get("utilization", []):
        for m in item["measures"]:
            yield item["target_id"], m


def utilization_order_problems(doc: dict) -> list[str]:
    """Ordering and uniqueness: targets by target_id, measures by name, and windows
    by minutes, all ascending."""
    out: list[str] = []
    targets = [i["target_id"] for i in doc.get("utilization", [])]
    if unsorted(targets) or len(set(targets)) != len(targets):
        out.append("utilization: target_id in ascending, unique order")
    for item in doc.get("utilization", []):
        names = [m["measure"] for m in item["measures"]]
        if unsorted(names) or len(set(names)) != len(names):
            out.append(f"{item['target_id']}: measure in ascending, unique order")
        for m in item["measures"]:
            mins = [w["minutes"] for w in m.get("windows", [])]
            if unsorted(mins) or len(set(mins)) != len(mins):
                out.append(
                    f"{item['target_id']}/{m['measure']}: minutes in ascending, "
                    "unique order"
                )
    return out


def dangling_utilization(doc: dict, topo: dict) -> list[str]:
    ids = {n["id"] for n in topo["nodes"]}
    return [
        i["target_id"] for i in doc.get("utilization", []) if i["target_id"] not in ids
    ]


def utilization_layer_mismatch(doc: dict) -> bool:
    """With layers.utilization present the array is present too, and without it the
    array is absent. This is the same rule as for flow logs."""
    return ("utilization" in doc["layers"]) != ("utilization" in doc)


def utilization_table_problems(doc: dict, topo: dict) -> list[str]:
    """Whether the measure can attach to that node type, whether the unit agrees,
    and that a percent only ever comes from AWS."""
    types = {n["id"]: n["type"] for n in topo["nodes"]}
    out: list[str] = []
    for target, m in _measures(doc):
        allowed, unit = MEASURE_TABLE[m["measure"]]
        if types.get(target) not in allowed:
            out.append(
                f"{target}: {m['measure']} does not attach to {types.get(target)}"
            )
        if m["unit"] != unit:
            out.append(f"{target}/{m['measure']}: unit {m['unit']} is not {unit}")
        if m["unit"] == "percent" and (
            m["limit"] != 100 or m["limit_source"] != "aws_computed"
        ):
            out.append(
                f"{target}/{m['measure']}: a percent comes from AWS, so the limit "
                "has to be 100 and the source aws_computed"
            )
    return out


def utilization_window_problems(doc: dict) -> list[str]:
    """A window length agrees with its step, the time covered is at most the window,
    average is at most peak, and the moment it was computed is at most the moment
    of the run, which for a short window means exactly equal."""
    observed = ts(doc["run"]["observed_at"])
    out: list[str] = []
    for target, m in _measures(doc):
        for w in m.get("windows", []):
            here = f"{target}/{m['measure']}/{w['minutes']}"
            step = STEP_FOR_MINUTES[w["minutes"]]
            if target.startswith("ec2_instance/"):
                step = max(step, EC2_MIN_STEP)
            if w["step_seconds"] != step:
                out.append(f"{here}: step {w['step_seconds']} is not {step}")
            if w["covered_seconds"] > w["minutes"] * 60:
                out.append(f"{here}: the time covered is longer than the window")
            peak, avg = w.get("peak"), w.get("average")
            if peak is not None and avg is not None and avg > peak:
                out.append(f"{here}: average {avg} is above the peak {peak}")
            at = ts(w["computed_at"])
            if at > observed or (w["minutes"] in SHORT_WINDOWS and at != observed):
                out.append(f"{here}: computed_at {w['computed_at']}")
            if w.get("peak_at") is not None:
                # A long window carried over from an earlier run is measured from
                # the moment it was computed. Measuring from the moment of this
                # run would put a burst an hour ago outside the window.
                stop = at.timestamp() - at.timestamp() % w["step_seconds"]
                start = datetime.fromtimestamp(stop, tz=timezone.utc) - timedelta(
                    minutes=w["minutes"]
                )
                if not (start <= ts(w["peak_at"]) <= at):
                    out.append(f"{here}: peak_at falls outside the window")
    return out


@pytest.fixture
def util() -> dict:
    return load(UTIL_FIXTURE)


def test_the_fixture_set_exercises_utilization():
    """One fixture carries utilization: all three shapes (rate, events and count),
    a value with no limit, something unread, a default quota with and without a
    reason, and an account quota."""
    doc = load(UTIL_FIXTURE)
    ms = [m for _, m in _measures(doc)]
    assert {m["kind"] for m in ms} == {"rate", "events", "count"}
    assert any(m["limit"] is None for m in ms)
    assert any("unread" in m for m in ms)
    assert any(m["limit_source"] == "default_quota" and "limit_reason" in m for m in ms)
    assert any(
        m["limit_source"] == "default_quota" and "limit_reason" not in m for m in ms
    )
    assert any(m["limit_source"] == "account_quota" for m in ms)
    assert any(
        w["computed_at"] != doc["run"]["observed_at"]
        for m in ms
        for w in m.get("windows", [])
    )
    # When a burst is short, the busiest minute of the one-week window and its
    # average differ by a lot, which is the reason the window exists. Usage is the
    # total over the window.
    bw = next(m for m in ms if m["measure"] == "bandwidth")
    assert bw["total_unit"] == "byte" and bw["limit"] is None
    week = next(w for w in bw["windows"] if w["minutes"] == 10080)
    assert week["peak"] > 1000 * week["average"] and week["total"] > 0


def test_the_fixture_set_tells_no_points_from_a_real_zero():
    """One fixture carries both shapes: a window where a rejection count, which is
    only sent when there is something to send, sent no points at all (`no_points`,
    meaning no value), and a window where points arrived and they were zero (a real
    zero). The render harness uses this fixture to keep the two apart on screen. An
    empty window on a usage rate is a zero, so a rate never has `no_points`.
    """
    doc = load(UTIL_FIXTURE)
    wins = [(m, w) for _, m in _measures(doc) for w in m.get("windows", [])]
    empty = [(m, w) for m, w in wins if w.get("no_points")]
    assert {m["kind"] for m, _ in empty} == {"events"}
    assert any(
        m["kind"] == "rate" and w.get("peak") == 0 and w.get("total") == 0
        for m, w in wins
    )
    assert all(
        w.get("peak") is None and "sum" not in w and w["covered_seconds"] == 0
        for _, w in empty
    )
    assert any(m["kind"] == "rate" and w.get("peak") == 0 for m, w in wins)
    assert any(m["kind"] == "events" and w.get("sum") == 0 for m, w in wins)


def _no_points_window(util: dict) -> dict:
    ev = util["utilization"][0]["measures"][1]
    return next(w for w in ev["windows"] if w.get("no_points"))


def test_schema_rejects_a_no_points_window_that_carries_a_zero(util):
    """Carrying a 0 while meaning no value leaves the screen unable to tell it from
    a real zero."""
    _no_points_window(util)["peak"] = 0.0
    assert errors(util)


def test_schema_rejects_a_no_points_window_with_a_total(util):
    """A window with no value carries no total either, and that is not a 0."""
    _no_points_window(util)["total"] = 0.0
    assert errors(util)


def test_schema_rejects_a_no_points_event_window_with_a_sum(util):
    ev = util["utilization"][0]["measures"][1]
    next(w for w in ev["windows"] if w.get("no_points"))["sum"] = 0
    assert errors(util)


def test_schema_rejects_a_false_no_points(util):
    """The key means something only by being present. A `false` would give "there
    is a value" two spellings."""
    cap = util["utilization"][0]["measures"][0]
    next(w for w in cap["windows"] if w.get("peak") == 0)["no_points"] = False
    assert errors(util)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_utilization_is_coherent(path: Path):
    doc = load(path)
    topo = load(topology_for(path))
    assert not utilization_layer_mismatch(doc)
    assert utilization_order_problems(doc) == []
    assert dangling_utilization(doc, topo) == []
    assert utilization_table_problems(doc, topo) == []
    assert utilization_window_problems(doc) == []


def test_measure_table_covers_the_schema_enum():
    """If MEASURE_TABLE and the schema enum drift apart, a new measure arrives with
    no check on it at all."""
    enum = SCHEMA["$defs"]["utilization_measure"]["properties"]["measure"]["enum"]
    assert set(MEASURE_TABLE) == set(enum)
    node_types = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"])
    assert all(
        t in node_types for allowed, _ in MEASURE_TABLE.values() for t in allowed
    )


def test_utilization_is_not_required():
    """A document from before utilization existed stays valid, which is what makes
    this a minor version."""
    assert "utilization" not in SCHEMA["required"]
    assert all("utilization" not in load(p) for p in FIXTURES if p != UTIL_FIXTURE)


# The negative counterparts, each breaking exactly one thing.


def test_table_detects_a_percent_with_a_limit_other_than_100(util):
    """A value is in its own unit, so writing 0.84 makes the screen draw "0.84%".
    That is why a percent goes with a limit of 100, which the table check above
    measures."""
    m = util["utilization"][0]["measures"][0]
    m["measure"], m["unit"], m["limit"], m["limit_source"] = (
        "capacity",
        "percent",
        1,
        "aws_computed",
    )
    assert utilization_table_problems(util, load(TOPOLOGY_FIXTURE_DIR / "minimal.json"))


def test_schema_rejects_an_unknown_limit_without_a_null_source(util):
    util["utilization"][0]["measures"][1]["limit_source"] = "fixed"
    assert errors(util)


def test_schema_rejects_a_limit_reason_on_an_account_quota(util):
    vpc = util["utilization"][4]["measures"]
    vpc[1]["limit_reason"] = {"code": "access_denied", "api": None, "message": None}
    assert errors(util)


def test_schema_rejects_a_quota_code_on_a_fixed_limit(util):
    nacl = util["utilization"][1]["measures"][0]
    nacl["limit_source"] = "fixed"
    assert errors(util)


def test_schema_rejects_a_count_with_windows(util):
    sub = util["utilization"][2]["measures"][0]
    sub["windows"] = deepcopy(util["utilization"][0]["measures"][0]["windows"])
    assert errors(util)


def test_schema_rejects_a_rate_with_a_current_count(util):
    util["utilization"][0]["measures"][0]["now"] = {"used": 1}
    assert errors(util)


def test_schema_rejects_an_events_measure_with_a_limit(util):
    """A rejection or drop count is a fact in itself and is never divided into a
    percentage."""
    m = util["utilization"][0]["measures"][1]
    m["limit"], m["limit_source"] = 100, "fixed"
    assert errors(util)


def test_schema_rejects_an_unread_measure_that_still_has_a_value(util):
    util["utilization"][3]["measures"][0]["now"] = {"used": 3}
    assert errors(util)


def test_schema_rejects_a_read_count_without_a_value(util):
    del util["utilization"][2]["measures"][0]["now"]
    assert errors(util)


def test_schema_rejects_an_unknown_window_length(util):
    util["utilization"][0]["measures"][0]["windows"][0]["minutes"] = 30
    assert errors(util)


def test_schema_accepts_utilization_without_the_metric_layer(util):
    """Utilization is independent of the metric layer, for a customer who wants to
    see utilization and nothing else."""
    util["layers"]["metric"] = False
    assert errors(util) == []
    # The counterpart that is still alive: flow logs do still require the metric
    # layer, so the surrounding allOf was not deleted along the way.
    util["layers"]["flow_log"] = True
    assert errors(util)


def test_schema_rejects_an_edge_target(util):
    """Utilization attaches to nodes only. Which lines to draw is the renderer's
    choice."""
    util["utilization"][0]["target_id"] = util["observations"][0]["target_id"]
    assert errors(util)


def test_layer_pairing_detects_both_halves(util):
    lone = deepcopy(util)
    del lone["layers"]["utilization"]
    assert utilization_layer_mismatch(lone)
    del util["utilization"]
    assert utilization_layer_mismatch(util)


def test_order_detects_unsorted_targets_measures_and_windows(util):
    a = deepcopy(util)
    a["utilization"].reverse()
    assert utilization_order_problems(a)
    b = deepcopy(util)
    b["utilization"][4]["measures"].reverse()
    assert utilization_order_problems(b)
    c = deepcopy(util)
    c["utilization"][0]["measures"][0]["windows"].reverse()
    assert utilization_order_problems(c)
    d = deepcopy(util)
    d["utilization"].append(deepcopy(d["utilization"][-1]))
    assert utilization_order_problems(d)


def test_dangling_detects_a_target_missing_from_the_topology(util, topo):
    util["utilization"][4]["target_id"] = (
        "vpc/111122223333/us-east-1/vpc-0fff1111bbbb9999"
    )
    assert dangling_utilization(util, topo)


def test_table_detects_a_measure_on_the_wrong_node_type(util, topo):
    util["utilization"][2]["measures"][0]["measure"] = "vpc_subnets"
    assert utilization_table_problems(util, topo)


def test_table_detects_a_wrong_unit(util, topo):
    util["utilization"][0]["measures"][0]["unit"] = "packet_per_second"
    assert utilization_table_problems(util, topo)


def test_window_detects_a_step_that_outlives_retention(util):
    """Recording a month at a one-minute step is false, because CloudWatch does not
    serve one-minute points older than fifteen days."""
    util["utilization"][0]["measures"][0]["windows"][4]["step_seconds"] = 60
    assert utilization_window_problems(util)


def test_window_detects_coverage_longer_than_the_window(util):
    w = util["utilization"][0]["measures"][0]["windows"][0]
    w["covered_seconds"] = 15 * 60 + 1
    assert utilization_window_problems(util)


def test_window_detects_an_average_above_the_peak(util):
    """Swapping the peak and the average puts the average above the peak."""
    w = util["utilization"][0]["measures"][0]["windows"][3]
    w["peak"], w["average"] = w["average"], w["peak"]
    assert utilization_window_problems(util)


def test_window_detects_a_stale_short_window(util):
    """A short window is recomputed every minute, so a value carried over from an
    hour ago makes the "now" on screen false."""
    util["utilization"][0]["measures"][0]["windows"][0]["computed_at"] = (
        "2026-01-02T02:10:05Z"
    )
    assert utilization_window_problems(util)


def test_window_detects_a_peak_outside_the_window(util):
    util["utilization"][0]["measures"][0]["windows"][0]["peak_at"] = (
        "2026-01-02T01:00:00Z"
    )
    assert utilization_window_problems(util)


# ---- the five kinds of load balancer target ----------------------------------

TARGET_DETAIL = (
    "health_check_off",
    "initial",
    "draining",
    "unused",
    "other_unavailable",
)


def _targets(total: int, healthy: int, unhealthy: int, **detail: int) -> dict:
    return {
        "total": total,
        "healthy": healthy,
        "unhealthy": unhealthy,
        **{k: detail.get(k, 0) for k in TARGET_DETAIL},
    }


def targets_over_total(doc: dict) -> list[str]:
    """healthy plus unhealthy plus the five kinds is at most total, because one
    target is in one state. JSON Schema cannot express a constraint on a sum."""
    bad = []
    for o in doc["observations"]:
        t = o.get("targets")
        if not t:
            continue
        parts = t["healthy"] + t["unhealthy"] + sum(t.get(k, 0) for k in TARGET_DETAIL)
        if parts > t["total"]:
            bad.append(o["target_id"])
    return bad


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_target_parts_never_exceed_the_total(path: Path):
    assert targets_over_total(load(path)) == []


def test_the_engine_fixture_carries_a_health_check_off_count():
    """A guard against measuring nothing: the render harness only measures the
    health-check-off count if the engine-produced fixture (`lb-targets.json`)
    holds a load balancer row whose count is above zero."""
    doc = load(FIXTURE_DIR / "lb-targets.json")
    rows = [
        o
        for o in doc["observations"]
        if (o.get("targets") or {}).get("health_check_off")
    ]
    assert any(o["target_id"].startswith("load_balancer/") for o in rows)
    assert all(o["status"] == "up" for o in rows)  # health check off is not a fault


def _lb_row(doc: dict) -> dict:
    return next(o for o in doc["observations"] if o.get("targets"))


def test_target_parts_come_together_or_not_at_all(doc):
    """The five kinds arrive together. With only some of them, the screen reads a
    missing kind as a zero. The older set of three on its own stays valid."""
    row = _lb_row(doc)
    row["targets"] = {"total": 3, "healthy": 2, "unhealthy": 0}
    assert errors(doc) == []  # the older shape, without the five, is still valid
    row["targets"] = _targets(3, 2, 0, health_check_off=1)
    assert errors(doc) == []
    for k in TARGET_DETAIL:
        row["targets"] = _targets(3, 2, 0, health_check_off=1)
        del row["targets"][k]
        assert errors(doc), f"{k} alone was missing, yet it passed"
    row["targets"] = _targets(3, 2, 0)
    row["targets"]["unhealthy_draining"] = 1
    assert errors(doc), "an unknown kind key was accepted"
    row["targets"] = _targets(3, 2, 0, initial=-1)
    assert errors(doc), "a negative count was accepted"


def test_target_parts_over_the_total_are_caught(doc):
    row = _lb_row(doc)
    row["targets"] = _targets(2, 2, 0, health_check_off=1)
    assert targets_over_total(doc) == [row["target_id"]]
    row["targets"] = _targets(3, 2, 0, health_check_off=1)
    assert targets_over_total(doc) == []
