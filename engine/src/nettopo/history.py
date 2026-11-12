"""Compare observations, add changes, and limit history age and count.

Use input observation times, scope, and coverage without calling AWS
APIs.
Do not treat query failures or scope changes as resource deletions.
Get deleted resource details from the topology paired with the previous
observation.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from .model import GLOBAL, split_edge_id, split_node_id
from .observe import (
    EDGE_RESOURCE_FROM_SOURCE,
    EDGE_SOURCE_RESOURCE,
    FLOW_LOG_METRIC,
    FLOW_LOG_ONLY_EDGES,
    METRIC_ONLY_EDGES,
    PRESENCE_ONLY_NODES,
    SCHEMA_VERSION,
)

_DESCRIBE = "describe"
_METRIC = "metric"

Cell = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class HistoryReport:

    """Store counts of entries added or excluded while processing
    history.

    diffed: Whether the previous observation was compared
    new_rows: Change entries added by this comparison
    carried: Previous history entries retained within the retention
        period
    dropped: Entries removed after the retention period
    collapsed: Entries removed by grouping repeated state changes
    capped: Entries removed to meet the maximum count
    unread: Targets not compared because of query failures
    layer_changed: Targets not compared after metric or flow log mode
        changes
    out_of_scope: Targets outside the observations' shared collection
        scope
    unknown_cell: Targets with unknown account, region, or resource type
    unmeasured: Targets with insufficient traffic measurement evidence
    deleting: Targets whose transition to deleting was excluded
    shaped: Entries with pre-deletion resource details in was
    unshaped: Deletion entries without pre-deletion resource details
    presence_rows: Resource additions or deletions found by topology
        comparison
    presence_unpaired: Whether unpaired topology prevented structure
        comparison
    warnings: Warnings from comparison and retention
    """
    diffed: bool = False
    new_rows: int = 0
    carried: int = 0
    dropped: int = 0
    collapsed: int = 0
    capped: int = 0
    unread: int = 0
    layer_changed: int = 0
    out_of_scope: int = 0
    unknown_cell: int = 0
    unmeasured: int = 0
    deleting: int = 0
    shaped: int = 0
    unshaped: int = 0
    presence_rows: int = 0
    presence_unpaired: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def excluded(self) -> int:
        return (
            self.unread
            + self.layer_changed
            + self.out_of_scope
            + self.unknown_cell
            + self.unmeasured
            + self.deleting
        )


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


_PUBLISH_ALLOWANCE_SECONDS = 300


def _minute(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if moment.tzinfo is not None else None


def _occurred_at(
    before: dict[str, Any], after: dict[str, Any], before_at: str, after_at: str
) -> str | None:
    if (before["status"], after["status"]) == ("live", "up"):
        source, key = before, "last_flow_at"
    elif (before["status"], after["status"]) == ("up", "live"):
        source, key = after, "first_flow_at"
    else:
        return None
    if before.get("partial") or after.get("partial"):
        return None
    window = (after.get("evidence") or {}).get("window_seconds")
    if not isinstance(window, int) or isinstance(window, bool):
        return None
    gap = (_at(after_at) - _at(before_at)).total_seconds()
    if gap > window - _PUBLISH_ALLOWANCE_SECONDS:
        return None
    return _minute((source.get("evidence") or {}).get(key))


def _node_cell(value: str) -> Cell | None:
    node_type, account_id, region, native = split_node_id(value)
    if region is None:
        if native is None:
            return None
        return account_id, GLOBAL, node_type
    return account_id, region, node_type


def target_cells(target_id: str, target_kind: str) -> tuple[Cell, ...] | None:
    try:
        if target_kind == "node":
            cell = _node_cell(target_id)
            return None if cell is None else (cell,)
        edge_type, source, target, _disc = split_edge_id(target_id)
        source_cell = _node_cell(source)
        target_cell = _node_cell(target)
    except ValueError:
        return None
    if source_cell is None or target_cell is None:
        return None
    if edge_type in EDGE_RESOURCE_FROM_SOURCE:
        origin = source_cell
    else:
        mapped = EDGE_SOURCE_RESOURCE.get(edge_type)
        if mapped is None:
            return None
        origin = (source_cell[0], source_cell[1], mapped)
    return tuple(dict.fromkeys((origin, source_cell, target_cell)))


def _ok_cells(document: dict[str, Any], layer: str) -> set[Cell]:
    return {
        (row["account_id"], row["region"], row["resource_type"])
        for row in document["coverage"]
        if row.get("layer") == layer and row.get("status") == "ok"
    }


class _Run:

    def __init__(self, document: dict[str, Any]) -> None:
        self.observed_at: str = document["run"]["observed_at"]
        self.by_id: dict[str, dict[str, Any]] = {
            o["target_id"]: o for o in document["observations"]
        }
        self._ok = _ok_cells(document, _DESCRIBE)
        self.metric_ok = _ok_cells(document, _METRIC)
        self.flow_log: bool = document["layers"].get("flow_log") is True
        self._reached = {(account, region) for account, region, _t in self._ok}
        self._requested = set(document["scope"]["resource_types"])
        self.pairs = {
            (account, region)
            for account in document["scope"]["account_ids"]
            for region in document["scope"]["regions"]
        }

    def observable(self, cells: tuple[Cell, ...]) -> bool:
        """Return whether collection results permit history comparison.

        Check coverage success for requested resource types. For
        unrequested types,
        use other successful queries in the same account and region.
        """
        return all(
            self.covers(cell)
            or (cell[2] not in self._requested and (cell[0], cell[1]) in self._reached)
            for cell in cells
        )

    def covers(self, cell: Cell) -> bool:
        """Return query success for an account, region, and resource
        type.

        For global resources, success in any region for the same account
        and type
        is sufficient.
        """
        if cell[1] != GLOBAL:
            return cell in self._ok
        return any(
            (account, kind) == (cell[0], cell[2]) for account, _region, kind in self._ok
        )


def _row(
    at: str,
    kind: str,
    target_id: str,
    target_kind: str,
    before: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    return {
        "at": at,
        "kind": kind,
        "target_id": target_id,
        "target_kind": target_kind,
        "from": before,
        "to": after,
        "repeat_count": 1,
    }


class _Shapes:

    def __init__(self, structure: dict[str, Any]) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        for node in structure.get("nodes") or ():
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                self.nodes[node["id"]] = node
        for edge in structure.get("edges") or ():
            if isinstance(edge, dict) and isinstance(edge.get("id"), str):
                self.edges[edge["id"]] = edge
        self.touching: dict[str, list[dict[str, Any]]] = {}
        for edge_id in sorted(self.edges):
            edge = self.edges[edge_id]
            for end in dict.fromkeys((edge.get("source"), edge.get("target"))):
                if isinstance(end, str):
                    self.touching.setdefault(end, []).append(edge)

    def of(self, target_id: str, target_kind: str) -> dict[str, Any] | None:
        if target_kind == "node":
            node = self.nodes.get(target_id)
            if node is None:
                return None
            return {
                "nodes": [deepcopy(node)],
                "edges": [deepcopy(edge) for edge in self.touching.get(target_id, ())],
            }
        edge = self.edges.get(target_id)
        return None if edge is None else {"nodes": [], "edges": [deepcopy(edge)]}


def _paired_structure(
    previous: dict[str, Any], structures: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the topology document paired with the previous
    observation.

    Compare the observation's run.topology_content_digest with the
    topology's
    run.content_digest. Fall back to collection time if either digest is
    missing. Return None if no document matches.
    """
    run = previous["run"]
    digest = run.get("topology_content_digest")
    collected = run.get("topology_collected_at")
    for structure in structures:
        srun = structure.get("run") if isinstance(structure, dict) else None
        if not isinstance(srun, dict):
            continue
        theirs = srun.get("content_digest")
        if digest and theirs:
            if theirs == digest:
                return structure
        elif collected is not None and srun.get("collected_at") == collected:
            return structure
    return None


def _same_structure(previous: dict[str, Any], document: dict[str, Any]) -> bool:
    a, b = previous["run"], document["run"]
    da, db = a.get("topology_content_digest"), b.get("topology_content_digest")
    if da and db:
        return bool(da == db)
    return a.get("topology_collected_at") == b.get("topology_collected_at")


class _Drawn:

    def __init__(self, structure: dict[str, Any]) -> None:
        self.ids: set[str] = {
            node["id"]
            for node in structure.get("nodes") or ()
            if isinstance(node, dict)
            and node.get("type") in PRESENCE_ONLY_NODES
            and isinstance(node.get("id"), str)
        }
        self._ok = {
            (row.get("account_id"), row.get("region"), row.get("resource_type"))
            for row in structure.get("coverage") or ()
            if isinstance(row, dict) and row.get("status") == "ok"
        }
        scope = structure.get("scope") or {}
        self.requested = {
            t for t in scope.get("resource_types") or () if isinstance(t, str)
        }

    def covers(self, account: str, region: str, resource_type: str) -> bool:
        """Return query success for an account, region, and resource
        type.

        For global resources, success in any region for the same account
        and type
        is sufficient.
        """
        if region != GLOBAL:
            return (account, region, resource_type) in self._ok
        return any((a, t) == (account, resource_type) for a, _r, t in self._ok)


def _presence_rows(
    before: _Drawn,
    after: _Drawn,
    shared: set[tuple[str, str]],
    shared_accounts: set[str],
    skip: set[str],
    observed_at: str,
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    out_of_scope = unread = 0
    for target_id in sorted(before.ids ^ after.ids):
        if target_id in skip:
            continue
        try:
            cell = _node_cell(target_id)
        except ValueError:
            cell = None
        if cell is None:
            continue
        account, region, node_type = cell
        if (
            (account not in shared_accounts)
            if region == GLOBAL
            else ((account, region) not in shared)
        ):
            out_of_scope += 1
            continue
        sources = PRESENCE_ONLY_NODES[node_type]
        asked = {s for s in sources if s in before.requested}
        if not asked or asked != {s for s in sources if s in after.requested}:
            out_of_scope += 1
            continue
        added = target_id in after.ids
        blind = before if added else after
        if not all(blind.covers(account, region, s) for s in sorted(asked)):
            unread += 1
            continue
        rows.append(
            _row(
                observed_at,
                "resource_added" if added else "resource_removed",
                target_id,
                "node",
            )
        )
    return rows, out_of_scope, unread


_ROW_CONTRACT: tuple[tuple[str, tuple[tuple[str, type], ...]], ...] = (
    ("observations", (("target_id", str), ("target_kind", str), ("status", str))),
    ("coverage", (("account_id", str), ("region", str), ("resource_type", str))),
    (
        "changes",
        (("at", str), ("kind", str), ("target_id", str), ("repeat_count", int)),
    ),
)
_SCOPE_KEYS = ("account_ids", "regions", "resource_types")


def _typed(where: str, value: Any, expected: type) -> str | None:
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        return f"{where} 가 {expected.__name__} 이 아니다"
    return None


def _shape_refusal(previous: dict[str, Any]) -> str | None:
    for key in ("run", "scope", "layers", "history"):
        if not isinstance(previous[key], dict):
            return f"{key} 가 객체가 아니다"
    for key in _SCOPE_KEYS:
        values = previous["scope"].get(key)
        if not isinstance(values, list):
            return f"scope.{key} 가 배열이 아니다"
        for index, value in enumerate(values):
            bad = _typed(f"scope.{key}[{index}]", value, str)
            if bad is not None:
                return bad
    for name, keys in _ROW_CONTRACT:
        rows = previous[name]
        if not isinstance(rows, list):
            return f"{name} 가 배열이 아니다"
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                return f"{name}[{index}] 가 객체가 아니다"
            missing = [key for key, _type in keys if key not in row]
            if missing:
                return f"{name}[{index}] 에 {'·'.join(missing)} 가 없다"
            for key, expected in keys:
                bad = _typed(f"{name}[{index}].{key}", row[key], expected)
                if bad is not None:
                    return bad
    resolution = previous["history"].get("resolution_seconds")
    if resolution is not None:
        bad = _typed("history.resolution_seconds", resolution, int)
        if bad is not None:
            return bad
    bad = _typed("history.baseline_at", previous["history"].get("baseline_at"), str)
    if bad is not None:
        return bad
    stamps = [("history.baseline_at", previous["history"]["baseline_at"])]
    stamps += [
        (f"changes[{i}].at", row["at"]) for i, row in enumerate(previous["changes"])
    ]
    for name, value in stamps:
        try:
            _at(value)
        except ValueError:
            return f"{name} 이 RFC3339 가 아니다"
    return None


def _refusal(previous: dict[str, Any], observed_at: str) -> str | None:
    version = str(previous.get("schema_version", ""))
    if version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        return f"schema_version={version or '없음'} 은 이 엔진이 읽는 계약이 아니다"
    for key in (
        "run",
        "scope",
        "layers",
        "observations",
        "coverage",
        "history",
        "changes",
    ):
        if key not in previous:
            return f"필수 키 {key} 가 없다"
    shape = _shape_refusal(previous)
    if shape is not None:
        return shape
    previous_at = previous["run"].get("observed_at")
    if not isinstance(previous_at, str):
        return "run.observed_at 이 없다"
    try:
        if _at(previous_at) >= _at(observed_at):
            return f"이전 관측 시각 {previous_at} 이 이번 관측보다 앞서지 않는다"
    except ValueError:
        return "관측 시각이 RFC3339 가 아니다"
    return None


FLAP_JOIN_GAP_SECONDS = 86400


def _collapse(
    rows: list[dict[str, Any]], threshold: int
) -> tuple[list[dict[str, Any]], int]:
    """Group repeated state changes for a target into one history entry.

    Merge the repeated episode if it meets threshold or contains an
    already
    grouped entry. Return grouped entries and the number removed.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    lifetimes: dict[str, list[str]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] == "status_changed":
            groups.setdefault(row["target_id"], []).append(row)
        else:
            out.append(row)
            lifetimes.setdefault(row["target_id"], []).append(row["at"])
    collapsed = 0
    for group in _episodes(groups, lifetimes):
        total = sum(row["repeat_count"] for row in group)
        if total < threshold and all(row["repeat_count"] == 1 for row in group):
            out.extend(group)
            continue
        latest = max(group, key=lambda row: (row["at"], row["target_id"]))
        folded = {
            key: value
            for key, value in latest.items()
            if key not in ("first_at", "first_from")
        }
        folded["repeat_count"] = total
        start = _fold_start(group)
        if start is not None:
            folded["first_at"], folded["first_from"] = start
        out.append(folded)
        collapsed += len(group) - 1
    return out, collapsed


def _episodes(
    groups: dict[str, list[dict[str, Any]]], lifetimes: dict[str, list[str]]
) -> list[list[dict[str, Any]]]:
    """Split target state changes into episodes by gaps and presence
    changes.
    """
    episodes: list[list[dict[str, Any]]] = []
    for target_id in sorted(groups):
        bounds = lifetimes.get(target_id, [])
        current: list[dict[str, Any]] = []
        for row in sorted(
            groups[target_id],
            key=lambda row: (
                row["at"],
                row["repeat_count"],
                str(row.get("from")),
                str(row.get("to")),
            ),
        ):
            if current:
                end = current[-1]["at"]
                start = row.get("first_at") if row["repeat_count"] > 1 else None
                start = start if isinstance(start, str) else row["at"]
                if any(end < at < row["at"] for at in bounds) or (
                    (_at(start) - _at(end)).total_seconds() > FLAP_JOIN_GAP_SECONDS
                ):
                    episodes.append(current)
                    current = []
            current.append(row)
        episodes.append(current)
    return episodes


def _fold_start(group: list[dict[str, Any]]) -> tuple[str, str] | None:
    starts: list[tuple[str, str]] = []
    for row in group:
        if row["repeat_count"] == 1:
            at, before = row["at"], row.get("from")
        else:
            at, before = row.get("first_at"), row.get("first_from")
        if not isinstance(at, str) or not isinstance(before, str):
            return None
        starts.append((at, before))
    return min(starts)


def apply_history(
    document: dict[str, Any],
    previous: dict[str, Any] | None,
    structures: Sequence[dict[str, Any]] = (),
) -> HistoryReport:
    """Update history and changes in an observation and return a report.

    document: Current observation JSON whose history is updated
    previous: Previous observation JSON (skip comparison if absent)
    structures: Topology documents for configuration and pre-deletion
        details
    """
    history = document["history"]
    observed_at: str = document["run"]["observed_at"]
    retention: int = history["retention_seconds"]
    threshold: int = history["flap_collapse_threshold"]

    if previous is None:
        return HistoryReport()
    refusal = _refusal(previous, observed_at)
    if refusal is not None:
        return HistoryReport(
            warnings=(f"{refusal} — 이력을 이번 관측부터 새로 시작한다",)
        )

    current, before_run = _Run(document), _Run(previous)
    shared = current.pairs & before_run.pairs
    shared_accounts = {account for account, _region in shared}
    drifted_types = current._requested ^ before_run._requested
    rows: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    unread = layer_changed = out_of_scope = unknown_cell = unmeasured = deleting = 0

    for target_id in sorted(set(current.by_id) | set(before_run.by_id)):
        after = current.by_id.get(target_id)
        before = before_run.by_id.get(target_id)
        target_kind = (after or before)["target_kind"]
        cells = target_cells(target_id, target_kind)
        if cells is None:
            unknown_cell += 1
            continue
        if any(
            (cell[0] not in shared_accounts)
            if cell[1] == GLOBAL
            else (cell[0], cell[1]) not in shared
            for cell in cells
        ):
            out_of_scope += 1
            continue
        if any(cell[2] in drifted_types for cell in cells):
            out_of_scope += 1
            continue
        cell = cells[0]
        if before is None or after is None:
            if target_id.split("|")[0] in METRIC_ONLY_EDGES:
                if (cell in current.metric_ok) != (cell in before_run.metric_ok):
                    layer_changed += 1
                else:
                    unmeasured += 1
                continue
            if target_id.split("|")[0] in FLOW_LOG_ONLY_EDGES:
                if current.flow_log != before_run.flow_log:
                    layer_changed += 1
                else:
                    unmeasured += 1
                continue
            blind = before_run if before is None else current
            if not blind.observable(cells):
                unread += 1
                continue
            kind = "resource_added" if before is None else "resource_removed"
            row = _row(observed_at, kind, target_id, target_kind)
            if kind == "resource_removed":
                removed.append(row)
            rows.append(row)
            continue
        if before["status"] == after["status"]:
            continue
        if after.get("deleting") is True:
            deleting += 1
            continue
        if "live" in (before["status"], after["status"]) and (
            (cell in current.metric_ok) != (cell in before_run.metric_ok)
        ):
            layer_changed += 1
            continue
        if {before["status"], after["status"]} == {
            "live",
            "up",
        } and FLOW_LOG_METRIC in (
            (before.get("evidence") or {}).get("metric"),
            (after.get("evidence") or {}).get("metric"),
        ):
            if current.flow_log != before_run.flow_log:
                layer_changed += 1
            else:
                unmeasured += 1
            continue
        row = _row(
            observed_at,
            "status_changed",
            target_id,
            target_kind,
            before["status"],
            after["status"],
        )
        occurred = _occurred_at(before, after, before_run.observed_at, observed_at)
        if occurred is not None:
            row["occurred_at"] = occurred
        rows.append(row)

    warnings: list[str] = []
    presence_rows = 0
    presence_unpaired = False
    if not _same_structure(previous, document):
        drawn_before = _paired_structure(previous, structures)
        drawn_after = _paired_structure(document, structures)
        if drawn_before is None or drawn_after is None:
            presence_unpaired = True
            warnings.append(
                "구조가 바뀌었는데 두 관측이 그려진 구조 문서를 다 찾지 못했다 — 상태 없는 카드"
                f"({len(PRESENCE_ONLY_NODES)}종 · CloudFront·API Gateway 등)의 생김·없어짐을 이번에는 비교하지 않았다"
            )
        else:
            more, skipped_scope, skipped_unread = _presence_rows(
                _Drawn(drawn_before),
                _Drawn(drawn_after),
                shared,
                shared_accounts,
                set(current.by_id) | set(before_run.by_id),
                observed_at,
            )
            out_of_scope += skipped_scope
            unread += skipped_unread
            presence_rows = len(more)
            rows.extend(more)
            removed.extend(row for row in more if row["kind"] == "resource_removed")

    shaped = 0
    if removed:
        paired = _paired_structure(previous, structures)
        shapes = _Shapes(paired) if paired is not None else None
        for row in removed:
            was = shapes.of(row["target_id"], row["target_kind"]) if shapes else None
            if was is not None:
                row["was"] = was
                shaped += 1
        if paired is None:
            warnings.append(
                f"직전 관측이 그려진 구조 문서를 못 찾았다 — 없어진 {len(removed)}개를 "
                "모습 없이 적는다(화면은 이름 없이 글자로만 말한다)"
            )

    horizon = _at(observed_at) - timedelta(seconds=retention)
    kept = [row for row in previous["changes"] if _at(row["at"]) >= horizon]
    combined, collapsed = _collapse(kept + rows, threshold)
    ordered = sorted(
        combined, key=lambda row: (row["at"], row["kind"], row["target_id"])
    )
    cap: int = history["max_changes"]
    capped = max(0, len(ordered) - cap)
    document["changes"] = ordered[capped:]

    baseline_at = previous["history"]["baseline_at"]
    history["baseline_at"] = baseline_at
    gap = ceil((_at(observed_at) - _at(before_run.observed_at)).total_seconds())
    history["resolution_seconds"] = max(
        history["resolution_seconds"],
        int(previous["history"].get("resolution_seconds") or 0),
        gap,
    )
    history["truncated"] = (
        _at(baseline_at) < horizon
        or capped > 0
        or previous["history"].get("truncated") is True
    )

    return HistoryReport(
        diffed=True,
        new_rows=len(rows),
        carried=len(kept),
        dropped=len(previous["changes"]) - len(kept),
        capped=capped,
        collapsed=collapsed,
        unread=unread,
        layer_changed=layer_changed,
        out_of_scope=out_of_scope,
        unknown_cell=unknown_cell,
        unmeasured=unmeasured,
        deleting=deleting,
        shaped=shaped,
        unshaped=len(removed) - shaped,
        presence_rows=presence_rows,
        presence_unpaired=presence_unpaired,
        warnings=tuple(warnings),
    )
