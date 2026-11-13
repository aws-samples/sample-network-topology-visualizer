"""State tier entry point: a thin Lambda handler invoked by EventBridge.

Two things invoke it: the 5-minute schedule, and an S3 event (the page writing
the control key, or the structure tier publishing a new structure pointer).

The handler does only four things:

1. Verify the bundle: confirm the boto3 and botocore we packaged are the ones
   that were actually imported.
2. Build a time-versioned key from the EventBridge event's `time`, not from the
   wall clock at execution.
3. Run the observation by calling `nettopo observe` on local files only; the
   engine knows nothing about S3.
4. Upload the result with `put_object`. Archive keys are never overwritten.

No observation logic lives here. Keeping `s3` and `put_object` out of
`engine/src/` is what makes the engine testable as a read-only component; once
that boundary leaks, the read-only guarantee has to be verified outside the
engine as well.

Failures are never turned into quiet successes. A mismatched bundle, an event
without `time`, or a non-zero exit from the observation raises, which sends the
invocation to the DLQ after the two automatic retries. Uploading a half-built
observation document would be worse: the page renders whatever it finds as the
current state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple

import boto3
import botocore

PACKAGE_ROOT = Path(__file__).resolve().parent
# Written into the zip by the packaging script (`build-state-lambda.sh`). It is a
# fact about the deployed artifact, so it is not a template parameter: a
# parameter someone forgets still deploys, a missing file here does not.
BUNDLED_VERSIONS = PACKAGE_ROOT / "bundled-versions.json"

# The only object the UI writes. Absent means live monitoring was never enabled.
CONTROL_KEY = "control/desired.json"
# Pointer that the structure tier (CodeBuild) overwrites at the end of every
# build, so finding the newest structure costs one GET instead of a listing.
STRUCTURE_LATEST_KEY = "structure-latest.json"
# The structure the pointer referred to before the current one. The structure
# tier copies it aside only when the content actually changed. It is used only to
# record what a deleted resource looked like; without it the history line still
# gets written, just without that detail.
STRUCTURE_PREVIOUS_KEY = "structure-previous.json"
# What the page polls is the previous observation document itself, which is why
# the history input (`--previous`) reads the same key.
STATE_LATEST_KEY = "state-latest.json"
STATE_PREFIX = "snapshots/state"
# Archive keys written by the structure tier
# (`snapshots/structure/YYYY/MM/DD/HHMMZ/topology.json`). We search here only
# when the structure paired with the previous observation is neither the current
# pointer nor the copy aside: if the structure changed more than once while live
# monitoring was off, the copy has already been overwritten, and a deleted
# resource would appear in the history with its id but no name.
STRUCTURE_ARCHIVE_PREFIX = "snapshots/structure"
# The timestamp in an archive key comes from the structure tier's post_build wall
# clock (`STAMP="$(date -u …)"` in `stack-a-collector.yaml`), which is later than
# the time collection started (`run.collected_at`, the observation's
# `topology_collected_at`). The key therefore cannot be derived exactly from that
# time: we list the date prefixes around it, take the keys inside
# [collection time floored to the minute, + window] oldest first, and confirm the
# match by content digest.
ARCHIVE_WINDOW = timedelta(hours=2)
ARCHIVE_TRIES = 3
_ARCHIVE_KEY = re.compile(
    r"^snapshots/structure/(\d{4})/(\d{2})/(\d{2})/(\d{2})(\d{2})Z/topology\.json$"
)

# Server-side ceiling applied to the `expiresAt` the browser sends.
MAX_TTL = timedelta(hours=8)
# On a wake-up (a control-key write), skip the run if the previous observation is
# this fresh and was taken with the same layers enabled. While live monitoring is
# on, the page rewrites the same key every 5 minutes to extend its lease. The 6
# minutes matches the page's `LIVE_FRESH_MS`, the age at which it still treats a
# document as current: inside that window the page is already showing the
# document, so running again would show the user the same thing while adding
# metric calls, which cost money.
WAKE_FRESH = timedelta(minutes=6)
WAKE_SOURCE = "aws.s3"
TRIGGER_WORD = {"schedule": "주기", "wake": "깨우기", "structure": "새 구조"}


def _tmp_root() -> Path:
    # A function rather than a constant: resolved at import time, tests would
    # write to the real `/tmp`.
    return Path(os.environ.get("NETTOPO_TMP", "/tmp"))  # noqa: S108


class BundleError(RuntimeError):
    """The bundle is not ours, so the read-only guard is running against a
    botocore we never verified."""


class ScheduleError(RuntimeError):
    """The event carries no timestamp (both the schedule and S3 events provide
    `time`), so no time-versioned key can be built."""


class ObserveError(RuntimeError):
    """The engine exited non-zero, so nothing is uploaded."""


class MissingInputError(RuntimeError):
    """The observation input (a structure snapshot) is missing: the structure
    tier has never completed successfully."""


def verify_bundle(
    *,
    package_root: Path,
    recorded: dict[str, str] | None,
    installed: dict[str, str],
    module_paths: dict[str, Path],
    in_lambda: bool,
) -> None:
    """Raise unless the bundle is the one we built.

    Why two version numbers matter this much: `awsguard.py` enforces read-only
    access at runtime by hooking two botocore events
    (`before-parameter-build` and `before-call`). Those event names and their
    ordering are botocore internals, and AWS upgrades the boto3 bundled with the
    Lambda runtime without notice. If the runtime copy wins the import, the hooks
    keep running against a different contract and nothing reports that the guard
    has loosened. So we refuse to run on a bundle we have not verified.

    Locally (`in_lambda=False`) there is no bundle to check, so this function only
    judges inside Lambda; tests should pass `in_lambda=True` to exercise it.
    """
    if not in_lambda:
        return
    if recorded is None:
        raise BundleError(
            f"{BUNDLED_VERSIONS.name} 이 없다 — 이 zip 은 패키징 스크립트가 만든 것이 아니다"
        )
    for name in ("boto3", "botocore"):
        want = recorded.get(name)
        got = installed.get(name)
        if want != got:
            raise BundleError(
                f"{name} 버전이 배포 기록과 다르다: 기록 {want!r} · 임포트된 것 {got!r} "
                "— 런타임 번들이 우리 것을 가렸다"
            )
    for name, path in module_paths.items():
        if not path.is_relative_to(package_root):
            raise BundleError(
                f"{name} 를 zip 밖에서 임포트했다({path}) — 번들이 빠졌다. "
                "런타임 제공 boto3 로는 돌지 않는다"
            )


def stamp_from_event_time(raw: Any) -> tuple[str, datetime]:
    """Build the key from the event time, never from the execution time.

    Asynchronous invocations are retried twice automatically. Keyed by execution
    time, a retry of the same cycle would land on a different key and produce two
    documents both claiming to be the current state, with no way to tell which is
    right. Keyed by event time, retries are idempotent.
    """
    if not isinstance(raw, str) or not raw:
        raise ScheduleError(
            "이벤트에 `time` 이 없다 — 벽시계로 대체하지 않는다(재시도가 멱등하지 않게 된다)"
        )
    text = raw.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ScheduleError(f"이벤트 `time` 을 읽을 수 없다: {raw!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y/%m/%d/%H%MZ"), moment


class Decision(NamedTuple):
    live: bool
    layer2: bool
    reason: str
    # Utilization is independent of the metric layer: someone who only wants
    # utilization can enable just that.
    utilization: bool = False


def decide(
    control: dict[str, Any] | None,
    *,
    control_modified: datetime | None,
    now: datetime,
) -> Decision:
    """Interpret the control key server-side rather than trusting the browser.

    The ceiling is measured from the control object's `LastModified`. Only the
    browser knows when the user pressed the button, and it can forge that value,
    whereas `LastModified` is written by S3. So however far in the future
    `expiresAt` claims to be, it cannot outlast the last PUT plus 8 hours.
    Extending the lease means writing the key again, which refreshes
    `LastModified`, and that removes the path where a closed tab keeps spending
    money.

    The default is off: if the control key is missing or unreadable, we never lean
    towards the option that costs money.
    """
    if control is None:
        return Decision(
            False, False, f"{CONTROL_KEY} 이 없다 — 실시간이 켜진 적이 없다"
        )
    if control.get("live") is not True:
        return Decision(False, False, "제어 키의 live 가 true 가 아니다")
    raw_expiry = control.get("expiresAt")
    if not isinstance(raw_expiry, str):
        return Decision(
            False, False, "제어 키에 expiresAt 이 없다 — 만료 없는 on 은 받지 않는다"
        )
    try:
        expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
    except ValueError:
        return Decision(False, False, f"expiresAt 을 읽을 수 없다: {raw_expiry!r}")
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    effective = expiry.astimezone(timezone.utc)
    if control_modified is not None:
        ceiling = control_modified.astimezone(timezone.utc) + MAX_TTL
        if effective > ceiling:
            effective = ceiling
    if effective <= now:
        return Decision(
            False,
            False,
            f"만료됐다(유효 만료 {effective.isoformat()} ≤ 이벤트 시각 {now.isoformat()})",
        )
    layer2 = control.get("layer2") is True
    # A missing key means the control object was written by an older page, where
    # choosing the metric layer also turned utilization on. Treating "missing" as
    # "follow the metric layer" keeps those pages working. When the key is
    # present, only an explicit true enables it, because it costs money.
    raw_util = control.get("utilization")
    utilization = layer2 if raw_util is None else raw_util is True
    return Decision(True, layer2, "제어 키가 on · 만료 전", utilization)


def wake_is_redundant(
    previous: dict[str, Any] | None,
    decision: Decision,
    *,
    flow_logs: bool,
    now: datetime,
) -> str | None:
    """Return why a wake-up has nothing to do, or `None` if it should run.

    Nothing to do means the previous observation is within `WAKE_FRESH` and was
    taken with exactly the layers now being requested. If any layer differs (the
    user just enabled utilization, or turned the metric layer off) we run,
    because the point of this rule is to avoid redundant work, not to delay what
    the user just asked for. If the previous document is missing or unreadable we
    also run: when in doubt, favour the user.
    """
    if not isinstance(previous, dict):
        return None
    run = previous.get("run")
    layers = previous.get("layers")
    if not isinstance(run, dict) or not isinstance(layers, dict):
        return None
    raw = run.get("observed_at")
    if not isinstance(raw, str):
        return None
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    if observed < now - WAKE_FRESH:
        return None
    have = (
        layers.get("metric") is True,
        layers.get("flow_log") is True,
        layers.get("utilization") is True,
    )
    want = (decision.layer2, decision.layer2 and flow_logs, decision.utilization)
    if have != want:
        return None
    return f"직전 관측 {raw} 이 {int(WAKE_FRESH.total_seconds() // 60)}분 안이고 층이 같다(임대 연장 쓰기)"


def event_key(event: dict[str, Any]) -> str | None:
    """The object key from an S3-to-EventBridge "Object Created" event, or `None`
    for any other shape."""
    detail = event.get("detail")
    obj = detail.get("object") if isinstance(detail, dict) else None
    key = obj.get("key") if isinstance(obj, dict) else None
    return key if isinstance(key, str) else None


def pair_moved(
    previous: dict[str, Any] | None, structure: dict[str, Any] | None
) -> str | None:
    """Return why the previous observation does not pair with the current
    structure pointer, or `None` when it does pair (or when we cannot tell).

    Skipping a wake-up on freshness alone breaks this case: the structure has
    just changed, a page on the new structure turns monitoring on, and it
    receives a document describing the old structure, so it sits unpaired until
    the next 5-minute cycle. Pairing uses the same rule as the page: compare the
    content digests (`run.topology_content_digest` against the structure's
    `run.content_digest`) only when both sides have one. If either is missing the
    answer is "unknown", and the skip decision stands.
    """
    if not isinstance(previous, dict) or not isinstance(structure, dict):
        return None
    prun = previous.get("run")
    srun = structure.get("run")
    if not isinstance(prun, dict) or not isinstance(srun, dict):
        return None
    theirs = prun.get("topology_content_digest")
    ours = srun.get("content_digest")
    if (
        not isinstance(theirs, str)
        or not isinstance(ours, str)
        or not theirs
        or not ours
    ):
        return None
    if theirs == ours:
        return None
    return (
        "직전 관측이 지금 구조와 짝이 아니다(내용 지문이 다르다) — 새 구조로 다시 잰다"
    )


def _get_json(
    s3: Any, bucket: str, key: str
) -> tuple[dict[str, Any] | None, datetime | None]:
    """Return `(None, None)` when the key is absent. Other errors are not
    swallowed: turning a permission problem into "not there" would leave the page
    unable to tell "the user switched this off" from "we could not read it"."""
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return None, None
    body = response["Body"].read()
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    return parsed, response.get("LastModified")


def _download(s3: Any, bucket: str, key: str, dest: Path) -> bool:
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return False
    dest.write_bytes(response["Body"].read())
    return True


def _download_optional(s3: Any, bucket: str, key: str, dest: Path) -> bool:
    """An optional input: a denied read must not kill the cycle, only log.

    Why this differs from `_download`: without a required input (the structure,
    or the previous observation) the observation would be wrong, whereas without
    this one the observation is still correct and a deleted resource simply has
    no "before" detail attached. Stopping every 5-minute observation on a stack
    deployed without the newer permission would cost far more than that.
    """
    try:
        return _download(s3, bucket, key, dest)
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "?")
        print(f"선택 입력 {key} 를 못 읽었다({code}) — 없어진 것의 모습 없이 관측한다")
        return False


def structure_pairs(previous: Any, structure: Any) -> bool:
    """Does the previous observation pair with that structure? Same rule as the
    engine's `history._paired_structure`.

    Compare content digests (`run.topology_content_digest` against
    `run.content_digest`) when both sides have one, otherwise compare collection
    times. If this rule drifts from the engine's, the cycles this function skips
    as "already paired" are the ones where the engine finds no pair, and history
    lines lose their "before" detail.
    """
    if not isinstance(previous, dict) or not isinstance(structure, dict):
        return False
    prun = previous.get("run")
    srun = structure.get("run")
    if not isinstance(prun, dict) or not isinstance(srun, dict):
        return False
    digest = prun.get("topology_content_digest")
    theirs = srun.get("content_digest")
    if digest and theirs:
        return bool(digest == theirs)
    collected = prun.get("topology_collected_at")
    return collected is not None and srun.get("collected_at") == collected


def archive_prefixes(collected_at: str) -> list[str]:
    """Date prefixes from that collection time to the end of the window: usually
    one, two when the window crosses midnight."""
    start = _minute_floor(collected_at)
    days = dict.fromkeys(
        t.strftime(f"{STRUCTURE_ARCHIVE_PREFIX}/%Y/%m/%d/")
        for t in (start, start + ARCHIVE_WINDOW)
    )
    return list(days)


def archive_candidates(collected_at: str, keys: list[str]) -> list[str]:
    """Archive keys inside [collection time floored to the minute, + window],
    oldest first so the result is deterministic, at most `ARCHIVE_TRIES` of
    them."""
    start = _minute_floor(collected_at)
    found: list[tuple[datetime, str]] = []
    for key in keys:
        m = _ARCHIVE_KEY.match(key)
        if not m:
            continue
        y, mo, d, hh, mm = (int(x) for x in m.groups())
        at = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc)
        if start <= at <= start + ARCHIVE_WINDOW:
            found.append((at, key))
    return [key for _at, key in sorted(found)][:ARCHIVE_TRIES]


def _minute_floor(raw: str) -> datetime:
    at = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    return at.replace(second=0, microsecond=0)


def _archived_structure(
    s3: Any, bucket: str, previous: dict[str, Any], dest: Path
) -> bool:
    """Find the structure paired with the previous observation in the archive and
    write it to `dest`. Return `False` when there is none; the cycle continues
    either way.

    This is an optional input, for the same reason as `_download_optional`:
    without it the observation is still correct, only the "before" detail is
    missing. A downloaded candidate is used only after its digest confirms the
    pairing. Handing over some other structure from inside the window (a manual
    collection, say) merely leaves the engine without a pair today, but the day
    the two rules drift apart it would present names and connections from a
    different moment as "what it looked like then".
    """
    run = previous.get("run")
    collected = run.get("topology_collected_at") if isinstance(run, dict) else None
    if not isinstance(collected, str):
        return False
    try:
        _minute_floor(collected)
    except ValueError:
        return False
    try:
        keys: list[str] = []
        for prefix in archive_prefixes(collected):
            token: str | None = None
            while True:
                extra = {"ContinuationToken": token} if token else {}
                page = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, **extra)
                keys += [o["Key"] for o in page.get("Contents", []) if "Key" in o]
                token = page.get("NextContinuationToken")
                if not page.get("IsTruncated") or not token:
                    break
        for key in archive_candidates(collected, keys):
            doc, _ = _get_json(s3, bucket, key)
            if structure_pairs(previous, doc):
                dest.write_text(json.dumps(doc), encoding="utf-8")
                print(f"직전 관측의 구조를 누적 스냅샷에서 찾았다 — {key}")
                return True
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "?")
        print(f"누적 구조를 못 읽었다({code}) — 없어진 것의 모습 없이 관측한다")
        return False
    print(
        "직전 관측의 구조가 누적 스냅샷에도 없다(창 안 · 지문 불일치) — 없어진 것의 모습 없이 관측한다"
    )
    return False


def _put_new_only(s3: Any, bucket: str, key: str, body: bytes) -> str:
    """Write to an archive key, leaving it alone if something is already there.

    A 412 from the conditional write (`IfNoneMatch="*"`) is not an error: it
    means a retry ended idempotently. This is the counterpart to keying by event
    time, since retries of one cycle aim at the same key and the document that
    landed first wins.
    """
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
        )
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("PreconditionFailed", "412"):
            return "already-written"
        raise
    return "written"


def _read_local(path: Path) -> Any:
    """Read a downloaded input for the pairing check. Return `None` if it is
    corrupt: the engine makes the same judgement again, so we do not raise."""
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def _put(s3: Any, bucket: str, key: str, body: bytes) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )


def _observe(argv: list[str]) -> int:
    # Called in-process rather than through `subprocess`. A shell would add one
    # more layer of argument quoting rules, which has already bitten us in the
    # structure tier's buildspec, and it would lengthen cold starts.
    from nettopo.cli import main as nettopo_main

    return nettopo_main(argv)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    verify_bundle(
        package_root=PACKAGE_ROOT,
        recorded=json.loads(BUNDLED_VERSIONS.read_text())
        if BUNDLED_VERSIONS.exists()
        else None,
        installed={"boto3": boto3.__version__, "botocore": botocore.__version__},
        module_paths={
            "boto3": Path(boto3.__file__).resolve(),
            "botocore": Path(botocore.__file__).resolve(),
        },
        in_lambda=bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME")),
    )
    stamp, event_time = stamp_from_event_time(event.get("time"))
    bucket = os.environ["ARTIFACT_BUCKET"]
    role_name = os.environ["MEMBER_ROLE_NAME"]
    # This is the `FlowLogReading` stack parameter verbatim, `DISABLED` by
    # default. Only the exact value `ENABLED` turns it on, because the permission
    # policy is created from the same value: reading anything else as "on" would
    # pile up permission errors every cycle instead.
    flow_logs = os.environ.get("FLOW_LOGS", "DISABLED") == "ENABLED"
    s3 = boto3.client("s3")

    control, modified = _get_json(s3, bucket, CONTROL_KEY)
    decision = decide(control, control_modified=modified, now=event_time)
    if not decision.live:
        # Upload nothing. A document written during a cycle where monitoring is
        # off would still be read by the page as the current state; the page
        # conveys "this is off" from the age of the last document instead.
        print(f"실시간 꺼짐 — {decision.reason} · 주기 {stamp}")
        return {"stamp": stamp, "live": False, "reason": decision.reason}
    # Three ways in: the 5-minute cycle (`schedule`), a control-key write
    # (`wake`), and a new structure pointer (`structure`). A structure-pointer
    # event is never skipped, because the very reason it arrived is that the
    # previous observation now describes an old structure, and a page that has
    # just moved to the new diagram should find the matching state already there.
    trigger = "schedule"
    if event.get("source") == WAKE_SOURCE:
        trigger = "structure" if event_key(event) == STRUCTURE_LATEST_KEY else "wake"
    if trigger == "wake":
        previous_doc, _ = _get_json(s3, bucket, STATE_LATEST_KEY)
        redundant = wake_is_redundant(
            previous_doc, decision, flow_logs=flow_logs, now=event_time
        )
        if redundant is not None:
            # Fresh, same layers, but still run if the document describes an old
            # structure. We only read the structure at the moment we are about to
            # skip (once per 5-minute lease renewal); when we do run, the same key
            # is downloaded below anyway.
            structure_doc, _ = _get_json(s3, bucket, STRUCTURE_LATEST_KEY)
            moved = pair_moved(previous_doc, structure_doc)
            if moved:
                print(f"깨우기 건너뛰지 않음 — {moved} · 주기 {stamp}")
                redundant = None
        if redundant:
            # Upload nothing: the page is already showing that document, and the
            # next observation comes from the 5-minute cycle.
            print(f"깨우기 건너뜀 — {redundant} · 주기 {stamp}")
            return {
                "stamp": stamp,
                "live": True,
                "trigger": trigger,
                "skipped": redundant,
            }

    # `/tmp` on a warm container survives until the next invocation, so each
    # cycle gets its own directory and removes it afterwards. A leftover file
    # from the previous cycle could end up uploaded as the current document.
    workdir = _tmp_root() / f"nettopo-{stamp.replace('/', '-')}"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    try:
        topology = workdir / "topology.json"
        if not _download(s3, bucket, STRUCTURE_LATEST_KEY, topology):
            raise MissingInputError(
                f"s3://<bucket>/{STRUCTURE_LATEST_KEY} 가 없다 — 구조 단(CodeBuild)이 한 번도 "
                "성공하지 않았다. 관측은 구조 스냅샷 기준이므로 추측으로 대체하지 않는다"
            )
        out = workdir / "observation.json"
        argv = [
            "observe",
            "--topology",
            str(topology),
            "--out",
            str(out),
            "--role-name",
            role_name,
        ]
        # A member role created with an `ExternalId` only admits callers passing
        # the same value (Stack A's `MemberExternalId`). Empty means send none.
        external_id = os.environ.get("MEMBER_EXTERNAL_ID", "")
        if external_id:
            argv += ["--external-id", external_id]
        if decision.layer2:
            argv.append("--metrics")
            # Flow-log evidence is only judged within the same window as the
            # metric layer; passing it on a cycle without metrics makes the
            # engine reject the run.
            if flow_logs:
                argv.append("--flow-logs")
        if decision.utilization:
            # Utilization is enabled separately from the metric layer; the engine
            # accepts it on its own. Long-window values and the hourly carry-over
            # of service quotas are read from `--previous` below. A stack without
            # the quota-read permission keeps running against default limits.
            argv.append("--utilization")
        previous_topology = workdir / "previous-topology.json"
        if _download_optional(s3, bucket, STRUCTURE_PREVIOUS_KEY, previous_topology):
            # Optional input. A deletion is usually seen by the 5-minute
            # observation before the hourly structure collection, so the "before"
            # detail is already in `--topology`. This file is needed only when a
            # new structure was collected in between.
            argv += ["--previous-topology", str(previous_topology)]
        previous = workdir / "previous.json"
        if _download(s3, bucket, STATE_LATEST_KEY, previous):
            # History takes exactly one input: the previous observation document.
            # Without it this observation becomes the baseline, which is not an
            # error; on the first cycle the file does not exist yet.
            argv += ["--previous", str(previous)]
            # If the structure paired with the previous observation is neither the
            # current pointer nor the copy aside, pass one more from the archive.
            # When either of those two pairs, we do not even list the bucket, so
            # the usual 5-minute cycle costs nothing extra.
            prev_doc = _read_local(previous)
            have = [_read_local(p) for p in (topology, previous_topology) if p.exists()]
            if isinstance(prev_doc, dict) and not any(
                structure_pairs(prev_doc, d) for d in have
            ):
                archived = workdir / "archived-topology.json"
                if _archived_structure(s3, bucket, prev_doc, archived):
                    argv += ["--previous-topology", str(archived)]
        code = _observe(argv)
        if code != 0:
            raise ObserveError(f"nettopo observe 가 {code} 로 끝났다 — 올리지 않는다")
        body = out.read_bytes()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    key = f"{STATE_PREFIX}/{stamp}/observation.json"
    written = _put_new_only(s3, bucket, key, body)
    # The pointer the page polls. Byte-for-byte the same as the archive key, so
    # it doubles as the history input (`--previous`).
    _put(s3, bucket, STATE_LATEST_KEY, body)
    print(
        f"관측 {len(body)}바이트 · 키 {key} · {written} · 2층 {'on' if decision.layer2 else 'off'}"
        f" · 흐름 로그 {'on' if decision.layer2 and flow_logs else 'off'}"
        f" · 사용률 {'on' if decision.utilization else 'off'}"
        f" · {TRIGGER_WORD[trigger]}"
    )
    return {
        "stamp": stamp,
        "live": True,
        "trigger": trigger,
        "layer2": decision.layer2,
        "flow_logs": decision.layer2 and flow_logs,
        "utilization": decision.utilization,
        "key": key,
        "result": written,
        "bytes": len(body),
    }
