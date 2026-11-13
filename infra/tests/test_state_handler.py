"""The state-tier handler, tested for whether its guarantees really hold.

What these tests measure is not that the code runs, but what happens when things
go wrong:

- When the same cycle is retried, does the second attempt create a second
  document?
- When the bundle is not ours, does it run anyway?
- When the observation fails, does a half-built document get uploaded?
- When nobody turned monitoring on, does the layer that costs money run?

S3 is never contacted: `boto3.client` is swapped for a double, and the calls
themselves are what gets counted.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import botocore
import handler as h
import pytest

BUCKET = "nettopo-artifacts-test"
ROLE = "NetworkReadOnlyRole"
EVENT_TIME = "2026-09-24T07:05:00Z"
STAMP = "2026/09/24/0705Z"
STATE_KEY = f"snapshots/state/{STAMP}/observation.json"
OBSERVATION = b'{"run": {"observed_at": "2026-09-24T07:05:09Z"}}'


class _NoSuchKey(Exception):
    pass


class _FakeS3:
    """Stands in for S3, recording what was written to which key and how the
    conditional writes turned out."""

    class exceptions:  # noqa: N801 - mirrors the shape of a botocore client
        NoSuchKey = _NoSuchKey

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.modified: dict[str, datetime] = {}
        self.puts: list[tuple[str, bytes, dict[str, Any]]] = []
        self.conditional_conflicts: set[str] = set()

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        assert Bucket == BUCKET
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        body = self.objects[Key]
        return {
            "Body": _Body(body),
            "LastModified": self.modified.get(
                Key, datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)
            ),
        }

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str, **extra: Any
    ) -> dict[str, Any]:  # noqa: N803
        assert Bucket == BUCKET
        self.lists = [*getattr(self, "lists", []), Prefix]
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, **extra: Any
    ) -> dict[str, Any]:  # noqa: N803
        assert Bucket == BUCKET
        if extra.get("IfNoneMatch") == "*" and Key in self.conditional_conflicts:
            raise botocore.exceptions.ClientError(
                {
                    "Error": {
                        "Code": "PreconditionFailed",
                        "Message": "At least one of the pre-conditions failed",
                    }
                },
                "PutObject",
            )
        self.objects[Key] = Body
        self.puts.append((Key, Body, extra))
        return {}


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


def _control(**overrides: Any) -> bytes:
    doc: dict[str, Any] = {
        "live": True,
        "layer2": False,
        "expiresAt": "2026-09-24T11:00:00Z",
    }
    doc.update(overrides)
    return json.dumps(doc).encode()


def _install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    s3: _FakeS3,
    *,
    exit_code: int = 0,
    writes: bytes | None = OBSERVATION,
) -> list[list[str]]:
    """Install the doubles and record the arguments the engine is called with.
    `writes=None` means the engine produced no output file."""
    calls: list[list[str]] = []

    def fake_observe(argv: list[str]) -> int:
        calls.append(list(argv))
        if writes is not None:
            Path(argv[argv.index("--out") + 1]).write_bytes(writes)
        return exit_code

    monkeypatch.setattr(h, "_observe", fake_observe)
    monkeypatch.setattr(h.boto3, "client", lambda service, **_: s3)
    monkeypatch.setenv("ARTIFACT_BUCKET", BUCKET)
    monkeypatch.setenv("MEMBER_ROLE_NAME", ROLE)
    monkeypatch.setenv("NETTOPO_TMP", str(tmp_path))
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("FLOW_LOGS", raising=False)
    monkeypatch.delenv("MEMBER_EXTERNAL_ID", raising=False)
    return calls


def _ready_bucket() -> _FakeS3:
    return _FakeS3(
        {
            h.CONTROL_KEY: _control(),
            h.STRUCTURE_LATEST_KEY: b'{"topology": true}',
        }
    )


# ── Time-versioned keys ──────────────────────────────────────────────────────


def test_key_comes_from_event_time_not_wall_clock(monkeypatch, tmp_path):
    """The key comes from the event time. Keyed by execution time, a retry would
    land on a different key."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    result = h.handler({"time": EVENT_TIME})

    assert result["key"] == STATE_KEY
    assert STATE_KEY in s3.objects
    # Built from the wall clock, the key would carry today's date; 2026-09-24
    # 07:05Z is the value from the event.
    assert h.stamp_from_event_time(EVENT_TIME)[0] == STAMP


def test_retry_of_same_cycle_writes_no_second_document(monkeypatch, tmp_path):
    """Asynchronous invocations are retried twice automatically, and one cycle
    must never produce two documents claiming to be the current state."""
    s3 = _ready_bucket()
    s3.conditional_conflicts.add(STATE_KEY)  # the first attempt already wrote it
    s3.objects[STATE_KEY] = b'{"first": "attempt"}'
    _install(monkeypatch, tmp_path, s3)

    result = h.handler({"time": EVENT_TIME})

    assert result["result"] == "already-written"
    assert s3.objects[STATE_KEY] == b'{"first": "attempt"}', (
        "the document that landed first was overwritten"
    )
    # The pointer the page polls is still refreshed; that is this cycle's result.
    assert s3.objects[h.STATE_LATEST_KEY] == OBSERVATION


def test_missing_event_time_fails_closed(monkeypatch, tmp_path):
    """Falling back to the wall clock would quietly break idempotency, so raising
    is the right outcome."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    with pytest.raises(h.ScheduleError):
        h.handler({"detail-type": "Scheduled Event"})

    assert s3.puts == []


def test_non_utc_event_time_is_converted(monkeypatch, tmp_path):
    """EventBridge sends UTC, but a value carrying an offset must not change the
    key."""
    stamp, moment = h.stamp_from_event_time("2026-09-24T16:05:00+09:00")
    assert stamp == STAMP
    assert moment.tzinfo == timezone.utc


# ── Bundle verification ──────────────────────────────────────────────────────


def test_bundle_version_mismatch_fails_closed():
    """If the runtime bundle shadows ours, the `awsguard` hooks run against a
    different contract."""
    root = Path("/var/task")
    with pytest.raises(h.BundleError, match="botocore"):
        h.verify_bundle(
            package_root=root,
            recorded={"boto3": "1.43.93", "botocore": "1.43.93"},
            installed={"boto3": "1.43.93", "botocore": "1.40.0"},
            module_paths={"botocore": root / "botocore/__init__.py"},
            in_lambda=True,
        )


def test_bundle_imported_from_outside_zip_fails_closed():
    """Even if the versions happen to match, an import from outside the zip means
    the bundle is missing."""
    with pytest.raises(h.BundleError, match="zip 밖"):
        h.verify_bundle(
            package_root=Path("/var/task"),
            recorded={"boto3": "1.43.93", "botocore": "1.43.93"},
            installed={"boto3": "1.43.93", "botocore": "1.43.93"},
            module_paths={"botocore": Path("/var/runtime/botocore/__init__.py")},
            in_lambda=True,
        )


def test_bundle_record_missing_fails_closed():
    """With no record, this zip was not built by the packaging script, and we do
    not run on a bundle we cannot identify."""
    with pytest.raises(h.BundleError):
        h.verify_bundle(
            package_root=Path("/var/task"),
            recorded=None,
            installed={"boto3": "1.43.93", "botocore": "1.43.93"},
            module_paths={},
            in_lambda=True,
        )


def test_bundle_check_is_lambda_only():
    """There is no bundle locally, and raising here would block both the tests
    and any pre-deployment check."""
    h.verify_bundle(
        package_root=Path("/var/task"),
        recorded=None,
        installed={},
        module_paths={"botocore": Path("/somewhere/else/botocore/__init__.py")},
        in_lambda=False,
    )


# ── Server-side on/off and expiry ────────────────────────────────────────────


def test_default_is_off_and_writes_nothing(monkeypatch, tmp_path):
    """With no control key, nothing happens: we never lean towards the option
    that costs money."""
    s3 = _FakeS3({h.STRUCTURE_LATEST_KEY: b"{}"})
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler({"time": EVENT_TIME})

    assert result["live"] is False
    assert calls == [], "the observation ran even though monitoring is off"
    assert s3.puts == [], "a document was uploaded on a cycle with monitoring off"


def test_expiry_ceiling_comes_from_control_last_modified(monkeypatch, tmp_path):
    """However far in the future the browser's `expiresAt` claims to be, it cannot
    outlast the last PUT plus 8 hours.

    This is how "closing the tab stops the spending" is actually implemented:
    extending the lease requires another PUT, and a PUT refreshes
    `LastModified`.
    """
    modified = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc) - timedelta(hours=9)
    decision = h.decide(
        {"live": True, "expiresAt": "2027-01-01T00:00:00Z"},
        control_modified=modified,
        now=datetime(2026, 9, 24, 7, 5, tzinfo=timezone.utc),
    )
    assert decision.live is False
    assert "만료" in decision.reason

    # The same value is accepted when it was just written: the ceiling is 8
    # hours from the last PUT, not a ban on distant expiry dates.
    fresh = h.decide(
        {"live": True, "expiresAt": "2027-01-01T00:00:00Z"},
        control_modified=datetime(2026, 9, 24, 7, 4, tzinfo=timezone.utc),
        now=datetime(2026, 9, 24, 7, 5, tzinfo=timezone.utc),
    )
    assert fresh.live is True


def test_expired_control_stops_the_cycle(monkeypatch, tmp_path):
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = _control(expiresAt="2026-09-24T07:00:00Z")
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler({"time": EVENT_TIME})

    assert result["live"] is False
    assert calls == []


def test_layer2_only_when_explicitly_true(monkeypatch, tmp_path):
    """The layers that cost money run only on an explicit true, never on a string
    such as `"true"`."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--metrics" not in calls[0]
    assert "--utilization" not in calls[0], (
        "no utilization key and the metric layer off: this is an older page's "
        "resource-state-only mode"
    )

    s3.objects[h.CONTROL_KEY] = _control(layer2="true")
    calls2 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--metrics" not in calls2[0], "the string 'true' was read as enabled"

    s3.objects[h.CONTROL_KEY] = _control(layer2=True)
    calls3 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--metrics" in calls3[0]
    assert "--utilization" in calls3[0], (
        "on an older page with no utilization key, choosing the metric layer also "
        "included utilization, and that behaviour is preserved"
    )
    assert "--previous" in calls3[0], (
        "precondition: the input for long-window carry-over (the previous "
        "observation document) is passed along"
    )


@pytest.mark.parametrize(
    ("layer2", "util", "metrics", "utilization"),
    [
        (False, True, False, True),  # utilization only: that is all they asked for
        (
            True,
            False,
            True,
            False,
        ),  # metric layer only: a false utilization key keeps utilization off
        (True, True, True, True),  # both
        (False, "true", False, False),  # a string is not an enabled switch
    ],
)
def test_utilization_is_its_own_switch(
    monkeypatch, tmp_path, layer2, util, metrics, utilization
):
    """Utilization is separate from monitoring: the control key's `utilization`
    field enables it on its own."""
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = _control(layer2=layer2, utilization=util)
    calls = _install(monkeypatch, tmp_path, s3)
    monkeypatch.setenv("FLOW_LOGS", "ENABLED")

    result = h.handler({"time": EVENT_TIME})

    assert ("--metrics" in calls[0]) is metrics, calls[0]
    assert ("--utilization" in calls[0]) is utilization, calls[0]
    # Flow logs still ride along with the metric layer only; on a
    # utilization-only cycle the engine would reject them.
    assert ("--flow-logs" in calls[0]) is metrics, calls[0]
    assert result["utilization"] is utilization


@pytest.mark.parametrize(
    ("layer2", "env", "expected"),
    [
        (True, "ENABLED", True),
        (
            False,
            "ENABLED",
            False,
        ),  # metric layer off: passing them makes the engine reject the cycle
        (True, "DISABLED", False),
        (True, None, False),  # older stack with no environment variable: off
        (
            True,
            "enabled",
            False,
        ),  # the stack parameter is one of two uppercase values; nothing else counts
    ],
)
def test_flow_logs_only_with_layer2_and_the_stack_switch(
    monkeypatch, tmp_path, layer2, env, expected
):
    """Flow logs are passed only when the stack parameter is enabled and the
    metric layer is on for that cycle."""
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = _control(layer2=layer2)
    calls = _install(monkeypatch, tmp_path, s3)
    if env is not None:
        monkeypatch.setenv("FLOW_LOGS", env)

    result = h.handler({"time": EVENT_TIME})

    assert ("--flow-logs" in calls[0]) is expected, calls[0]
    assert ("--metrics" in calls[0]) is layer2
    assert result["flow_logs"] is expected


# ── Failures are never uploaded ──────────────────────────────────────────────


def test_engine_failure_is_not_uploaded(monkeypatch, tmp_path):
    """A half-built observation document is worse than none, because the page
    renders it as the current state."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3, exit_code=2, writes=b'{"half": "written"}')

    with pytest.raises(h.ObserveError):
        h.handler({"time": EVENT_TIME})

    assert s3.puts == []


def test_missing_structure_snapshot_fails_closed(monkeypatch, tmp_path):
    """An observation is always relative to a structure snapshot, and a missing
    one is never replaced by a guess."""
    s3 = _FakeS3({h.CONTROL_KEY: _control()})
    calls = _install(monkeypatch, tmp_path, s3)

    with pytest.raises(h.MissingInputError):
        h.handler({"time": EVENT_TIME})

    assert calls == []
    assert s3.puts == []


# ── History and cleanup ──────────────────────────────────────────────────────


def test_previous_is_the_latest_pointer(monkeypatch, tmp_path):
    """History takes one input, the previous observation document, which is the
    same object the page polls."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--previous" not in calls[0], (
        "on the first cycle there is no previous document"
    )

    s3.objects[h.STATE_LATEST_KEY] = b'{"run": {"observed_at": "2026-09-24T07:00:09Z"}}'
    calls2 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--previous" in calls2[0]


def test_previous_structure_is_optional_and_passed_when_present(monkeypatch, tmp_path):
    """The input that supplies what a deleted resource looked like: without it
    the observation still runs, and with it the file is passed to the engine."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    result = h.handler({"time": EVENT_TIME})
    assert result["result"] != "skipped" and calls, (
        "the observation was skipped merely because the previous structure was absent"
    )
    assert "--previous-topology" not in calls[0]

    s3.objects[h.STRUCTURE_PREVIOUS_KEY] = b'{"run": {"collected_at": "x"}}'
    calls2 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--previous-topology" in calls2[0]


def test_denied_previous_structure_does_not_stop_the_cycle(
    monkeypatch, tmp_path, capsys
):
    """On a stack deployed without the newer permission, the 5-minute
    observations keep being uploaded; the denial is only logged."""
    s3 = _ready_bucket()
    real_get = s3.get_object

    def get_object(*, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key == h.STRUCTURE_PREVIOUS_KEY:
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
            )
        return real_get(Bucket=Bucket, Key=Key)

    s3.get_object = get_object  # type: ignore[method-assign]
    calls = _install(monkeypatch, tmp_path, s3)
    result = h.handler({"time": EVENT_TIME})
    assert calls and "--previous-topology" not in calls[0]
    assert result["key"] == STATE_KEY and STATE_KEY in s3.objects
    assert "AccessDenied" in capsys.readouterr().out


# ── Finding the previous observation's structure in the archive ──────────────


def _struct(digest: str, at: str) -> bytes:
    return json.dumps({"run": {"content_digest": digest, "collected_at": at}}).encode()


def _gap_bucket(archived_digest: str) -> _FakeS3:
    """The structure changed twice while live monitoring was off, so the previous
    observation's structure (03:21) is neither the current pointer (05:21) nor
    the copy aside (04:21)."""
    s3 = _FakeS3(
        {
            h.CONTROL_KEY: _control(),
            h.STRUCTURE_LATEST_KEY: _struct("sha256:now", "2026-09-24T05:21:23Z"),
            h.STRUCTURE_PREVIOUS_KEY: _struct("sha256:prev", "2026-09-24T04:21:23Z"),
            h.STATE_LATEST_KEY: json.dumps(
                {
                    "run": {
                        "observed_at": "2026-09-24T03:55:00Z",
                        "topology_collected_at": "2026-09-24T03:21:23Z",
                        "topology_content_digest": "sha256:old",
                    }
                }
            ).encode(),
            # Before the window (earlier than collection started): never fetched
            "snapshots/structure/2026/09/24/0315Z/topology.json": _struct(
                "sha256:old", "x"
            ),
            # The archive key for that collection (post_build time, a few
            # minutes after collection started)
            "snapshots/structure/2026/09/24/0324Z/topology.json": _struct(
                archived_digest, "2026-09-24T03:21:23Z"
            ),
            "snapshots/structure/2026/09/24/0424Z/topology.json": _struct(
                "sha256:prev", "2026-09-24T04:21:23Z"
            ),
        }
    )
    return s3


def _record_structures(monkeypatch: pytest.MonkeyPatch) -> list[list[bytes]]:
    seen: list[list[bytes]] = []
    real = h._observe

    def observe(argv: list[str]) -> int:
        seen.append(
            [
                Path(argv[i + 1]).read_bytes()
                for i, a in enumerate(argv)
                if a == "--previous-topology"
            ]
        )
        return real(argv)

    monkeypatch.setattr(h, "_observe", observe)
    return seen


def test_gap_fetches_the_archived_structure_of_the_previous_observation(
    monkeypatch, tmp_path
):
    """When the previous observation's structure is neither the pointer nor the
    copy aside, exactly one archived snapshot is fetched and passed along."""
    s3 = _gap_bucket("sha256:old")
    gets: list[str] = []
    real_get = s3.get_object

    def get_object(*, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        gets.append(Key)
        return real_get(Bucket=Bucket, Key=Key)

    s3.get_object = get_object  # type: ignore[method-assign]
    _install(monkeypatch, tmp_path, s3)
    seen = _record_structures(monkeypatch)
    h.handler({"time": EVENT_TIME})
    assert seen and len(seen[0]) == 2, seen
    assert json.loads(seen[0][1])["run"]["content_digest"] == "sha256:old"
    archived = [k for k in gets if k.startswith("snapshots/structure/")]
    assert archived == ["snapshots/structure/2026/09/24/0324Z/topology.json"], archived
    assert s3.lists == ["snapshots/structure/2026/09/24/"]


def test_archived_structure_with_another_digest_is_not_used(
    monkeypatch, tmp_path, capsys
):
    """A key inside the window is still rejected when its digest differs, since
    names and connections from another moment would be presented as "what it
    looked like then"."""
    s3 = _gap_bucket("sha256:someone-else")
    _install(monkeypatch, tmp_path, s3)
    seen = _record_structures(monkeypatch)
    h.handler({"time": EVENT_TIME})
    assert seen and len(seen[0]) == 1, "an unpaired archived structure was passed on"
    assert "누적 스냅샷에도 없다" in capsys.readouterr().out


def test_paired_pointer_means_no_archive_listing(monkeypatch, tmp_path):
    """On a normal cycle, where the previous observation pairs with the current
    structure, the archive is not even listed, so it costs nothing."""
    s3 = _gap_bucket("sha256:old")
    s3.objects[h.STRUCTURE_LATEST_KEY] = _struct("sha256:old", "2026-09-24T03:21:23Z")
    _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert getattr(s3, "lists", []) == []


def test_archive_window_crosses_midnight_and_keeps_order() -> None:
    """Key timestamps come from the structure tier's post_build wall clock, which
    is later than the start of collection, so a window crossing midnight also
    looks at the next day's prefix, oldest first."""
    assert h.archive_prefixes("2026-09-24T23:50:10Z") == [
        "snapshots/structure/2026/09/24/",
        "snapshots/structure/2026/09/25/",
    ]
    keys = [
        "snapshots/structure/2026/09/25/0005Z/topology.json",
        "snapshots/structure/2026/09/24/2349Z/topology.json",
        "snapshots/structure/2026/09/24/2352Z/topology.json",
        "snapshots/structure/2026/09/25/0300Z/topology.json",
        "snapshots/structure/2026/09/24/2353Z/other.json",
    ]
    assert h.archive_candidates("2026-09-24T23:50:10Z", keys) == [
        "snapshots/structure/2026/09/24/2352Z/topology.json",
        "snapshots/structure/2026/09/25/0005Z/topology.json",
    ]


def test_accumulated_key_and_pointer_get_the_same_bytes(monkeypatch, tmp_path):
    """If the pointer's bytes differed from the archived document, history would
    disagree with itself."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    h.handler({"time": EVENT_TIME})

    assert s3.objects[STATE_KEY] == s3.objects[h.STATE_LATEST_KEY] == OBSERVATION
    encrypted = [extra.get("ServerSideEncryption") for _, _, extra in s3.puts]
    assert encrypted == ["AES256", "AES256"], "uploaded without encryption"


def test_workdir_is_removed_between_cycles(monkeypatch, tmp_path):
    """`/tmp` on a warm container survives until the next invocation, so a
    leftover file from the previous cycle would be a real problem."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    h.handler({"time": EVENT_TIME})

    assert list(tmp_path.iterdir()) == []


# ── Immediate wake-up: the S3 event from writing the control key ─────────────


def _wake(time: str = EVENT_TIME) -> dict[str, Any]:
    """The shape of an S3-to-EventBridge "Object Created" event, with only the
    fields that matter; the handler reads `source` and `time`."""
    return {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "time": time,
        "detail": {"bucket": {"name": BUCKET}, "object": {"key": h.CONTROL_KEY}},
    }


def _previous(observed_at: str, **layers: Any) -> bytes:
    doc = {
        "run": {"observed_at": observed_at},
        "layers": {"describe": True, "metric": False, **layers},
    }
    return json.dumps(doc).encode()


def test_a_wake_runs_and_writes_under_its_own_minute(monkeypatch, tmp_path):
    """The S3 event from switching monitoring on takes the same path as the
    schedule, and the key comes from that event's time."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler(_wake("2026-09-24T07:07:41Z"))

    assert len(calls) == 1
    assert result["trigger"] == "wake"
    key = "snapshots/state/2026/09/24/0707Z/observation.json"
    assert result["key"] == key and s3.objects[key] == OBSERVATION
    assert s3.objects[h.STATE_LATEST_KEY] == OBSERVATION


def test_a_lease_renewal_wake_does_not_observe_again(monkeypatch, tmp_path):
    """While monitoring is on, the page rewrites the same key every 5 minutes. If
    the previous observation is within 6 minutes and the layers match, nothing
    runs, because each run costs money.

    The paired case: the same bucket and the same event delivered by the
    schedule does run, since skipping applies to wake-ups only.
    """
    s3 = _ready_bucket()
    s3.objects[h.STATE_LATEST_KEY] = _previous("2026-09-24T07:01:30Z")
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler(_wake())

    assert calls == [] and s3.puts == []
    assert result["trigger"] == "wake" and "층이 같다" in result["skipped"]

    scheduled = h.handler({"time": EVENT_TIME})
    assert len(calls) == 1 and scheduled["trigger"] == "schedule"


@pytest.mark.parametrize(
    ("control", "layers", "why"),
    [
        # Utilization was turned on while only monitoring was showing; what the
        # user just chose must not arrive late.
        ({"utilization": True}, {}, "utilization was just enabled"),
        (
            {"layer2": True, "utilization": False},
            {},
            "the metric layer was just enabled",
        ),
        # The metric layer was turned off, and the next document has to drop the
        # flow verdicts for the page to agree that monitoring is off.
        (
            {"layer2": False, "utilization": False},
            {"metric": True},
            "the metric layer was turned off",
        ),
    ],
)
def test_a_wake_with_a_new_layer_runs_even_if_the_last_document_is_fresh(
    monkeypatch, tmp_path, control, layers, why
):
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = _control(**control)
    s3.objects[h.STATE_LATEST_KEY] = _previous("2026-09-24T07:04:00Z", **layers)
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler(_wake())

    assert len(calls) == 1, why
    assert "skipped" not in result


def test_a_wake_after_a_stale_document_runs(monkeypatch, tmp_path):
    """The page does not treat a document older than 6 minutes as current, so
    just outside that boundary the cycle runs even with matching layers."""
    s3 = _ready_bucket()
    s3.objects[h.STATE_LATEST_KEY] = _previous("2026-09-24T06:58:59Z")
    calls = _install(monkeypatch, tmp_path, s3)

    h.handler(_wake())

    assert len(calls) == 1


# ── Following the structure: new pointer events, documents of an old structure ─

DG_OLD = "sha256:" + "a" * 64
DG_NEW = "sha256:" + "b" * 64


def _structure_event(time: str = EVENT_TIME) -> dict[str, Any]:
    """The S3 event from the moment the structure tier uploaded
    `structure-latest.json`."""
    return {
        "source": "aws.s3",
        "detail-type": "Object Created",
        "time": time,
        "detail": {
            "bucket": {"name": BUCKET},
            "object": {"key": h.STRUCTURE_LATEST_KEY},
        },
    }


def _paired_previous(observed_at: str, digest: str) -> bytes:
    doc = {
        "run": {"observed_at": observed_at, "topology_content_digest": digest},
        "layers": {"describe": True, "metric": False},
    }
    return json.dumps(doc).encode()


def _structure(digest: str) -> bytes:
    return json.dumps({"run": {"content_digest": digest}}).encode()


def test_a_new_structure_is_observed_at_once_even_if_the_last_document_is_fresh(
    monkeypatch, tmp_path
):
    """The moment a new structure arrives is the moment the previous observation
    becomes one of an old structure, so this is never skipped even within 6
    minutes with matching layers. Skipping it would leave a page that just
    followed "open the latest page" sitting unpaired until the next 5-minute
    cycle.

    The paired case: the same bucket and the same fresh document delivered as a
    control-key event is skipped, because the digests match.
    """
    s3 = _ready_bucket()
    s3.objects[h.STRUCTURE_LATEST_KEY] = _structure(DG_OLD)
    s3.objects[h.STATE_LATEST_KEY] = _paired_previous("2026-09-24T07:03:00Z", DG_OLD)
    calls = _install(monkeypatch, tmp_path, s3)

    skipped = h.handler(_wake())
    assert calls == [] and "층이 같다" in skipped["skipped"]

    result = h.handler(_structure_event())
    assert len(calls) == 1 and result["trigger"] == "structure"
    assert "skipped" not in result and result["key"] == STATE_KEY


def test_a_lease_wake_on_a_document_of_the_old_structure_runs(
    monkeypatch, tmp_path, capsys
):
    """When the structure event was missed (an older stack) or a page on the new
    structure has just switched monitoring on, a fresh previous document still
    triggers a run if it does not pair with the current structure."""
    s3 = _ready_bucket()
    s3.objects[h.STRUCTURE_LATEST_KEY] = _structure(DG_NEW)
    s3.objects[h.STATE_LATEST_KEY] = _paired_previous("2026-09-24T07:03:00Z", DG_OLD)
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler(_wake())

    assert len(calls) == 1 and result["trigger"] == "wake" and "skipped" not in result
    assert "짝이 아니다" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("previous", "structure"),
    [
        # A digest on only one side means "unknown", so the skip decision stands,
        # the same rule as `pairOf` on the page.
        ({"run": {"observed_at": "x"}}, {"run": {"content_digest": DG_NEW}}),
        ({"run": {"topology_content_digest": DG_OLD}}, {"run": {}}),
        (None, {"run": {"content_digest": DG_NEW}}),
        ({"run": {"topology_content_digest": DG_OLD}}, None),
        (
            {"run": {"topology_content_digest": DG_OLD}},
            {"run": {"content_digest": DG_OLD}},
        ),
    ],
)
def test_pair_moved_says_nothing_without_two_different_digests(previous, structure):
    assert h.pair_moved(previous, structure) is None


def test_a_wake_while_off_writes_nothing(monkeypatch, tmp_path):
    """Switching monitoring off writes the same key, and that wake-up ends at the
    "off" decision with no observation and no upload."""
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = b'{"live": false}'
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler(_wake())

    assert result["live"] is False and calls == [] and s3.puts == []


def test_external_id_is_passed_to_the_engine_only_when_set(monkeypatch, tmp_path):
    """Stack A's `MemberExternalId` becomes the `MEMBER_EXTERNAL_ID` environment
    variable and then the engine's `--external-id`; empty means the argument is
    omitted.

    Lose it and every account whose member role carries an `sts:ExternalId`
    condition reports its state as unknown every 5 minutes, while the structure
    diagram still looks fine.
    """
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--external-id" not in calls[-1]
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    monkeypatch.setenv("MEMBER_EXTERNAL_ID", "ext-123")
    h.handler({"time": EVENT_TIME})
    argv = calls[-1]
    assert argv[argv.index("--external-id") + 1] == "ext-123"
