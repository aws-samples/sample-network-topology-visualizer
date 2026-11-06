"""상태 단 핸들러 — ADR 0004 D2-1 의 조건 5개가 실제로 강제되는지 재는 검사.

여기서 재는 것은 「코드가 돌아간다」가 아니라 **사고가 났을 때 무엇이 일어나는가**다:

- 같은 주기가 **재시도**되면 두 번째가 문서를 하나 더 만드는가(조건 2)
- 번들이 우리 것이 아닐 때 **그냥 돌아버리는가**(조건 1)
- 관측이 실패했는데 **반쯤 만든 문서를 올리는가**(불변식 ④)
- 켠 적이 없는데 **돈이 드는 층이 돌아가는가**(ADR 0005 D4)

🔴 실제 S3 를 부르지 않는다. `boto3.client` 를 대역으로 갈아끼워 **호출 자체**를 센다.
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
    """S3 대역. 기록하는 것은 **무엇을 어느 키에 썼는가**와 조건부 쓰기의 결과다."""

    class exceptions:  # noqa: N801 — botocore 클라이언트의 모양을 따른다
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
    """대역을 깔고 엔진 호출 인자를 기록한다. `writes=None` 이면 엔진이 파일을 안 만든 것이다."""
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


# ── 조건 2. 시간 버전 키 ─────────────────────────────────────────────────────


def test_key_comes_from_event_time_not_wall_clock(monkeypatch, tmp_path):
    """🔴 키는 **이벤트 시각**이다. 실행 시각이면 재시도가 다른 키로 앉는다."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    result = h.handler({"time": EVENT_TIME})

    assert result["key"] == STATE_KEY
    assert STATE_KEY in s3.objects
    # 벽시계로 만들었으면 오늘 날짜가 들어갔을 것이다 — 2026-09-24 07:05Z 는 이벤트의 값이다.
    assert h.stamp_from_event_time(EVENT_TIME)[0] == STAMP


def test_retry_of_same_cycle_writes_no_second_document(monkeypatch, tmp_path):
    """🔴 비동기 호출은 자동 재시도 2회다. 한 주기에 「지금 상태」가 둘이면 안 된다."""
    s3 = _ready_bucket()
    s3.conditional_conflicts.add(STATE_KEY)  # 첫 시도가 이미 써 둔 상태
    s3.objects[STATE_KEY] = b'{"first": "attempt"}'
    _install(monkeypatch, tmp_path, s3)

    result = h.handler({"time": EVENT_TIME})

    assert result["result"] == "already-written"
    assert s3.objects[STATE_KEY] == b'{"first": "attempt"}', "먼저 성공한 문서를 덮었다"
    # 화면이 폴링하는 포인터는 갱신된다 — 그것이 이 주기의 결과다.
    assert s3.objects[h.STATE_LATEST_KEY] == OBSERVATION


def test_missing_event_time_fails_closed(monkeypatch, tmp_path):
    """벽시계로 대체하면 「멱등」이 조용히 깨진다 → 아예 죽는 것이 맞다."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    with pytest.raises(h.ScheduleError):
        h.handler({"detail-type": "Scheduled Event"})

    assert s3.puts == []


def test_non_utc_event_time_is_converted(monkeypatch, tmp_path):
    """EventBridge 는 UTC 를 주지만 오프셋이 붙은 값이 와도 키가 흔들려선 안 된다."""
    stamp, moment = h.stamp_from_event_time("2026-09-24T16:05:00+09:00")
    assert stamp == STAMP
    assert moment.tzinfo == timezone.utc


# ── 조건 1. 번들 검사 ────────────────────────────────────────────────────────


def test_bundle_version_mismatch_fails_closed():
    """런타임 번들이 우리 것을 가리면 `awsguard` 훅이 **다른 계약** 위에서 돈다."""
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
    """버전이 우연히 같아도 **zip 밖에서** 임포트됐다면 번들이 빠진 것이다."""
    with pytest.raises(h.BundleError, match="zip 밖"):
        h.verify_bundle(
            package_root=Path("/var/task"),
            recorded={"boto3": "1.43.93", "botocore": "1.43.93"},
            installed={"boto3": "1.43.93", "botocore": "1.43.93"},
            module_paths={"botocore": Path("/var/runtime/botocore/__init__.py")},
            in_lambda=True,
        )


def test_bundle_record_missing_fails_closed():
    """기록이 없으면 이 zip 은 패키징 스크립트가 만든 것이 아니다 — 모르는 번들로 돌지 않는다."""
    with pytest.raises(h.BundleError):
        h.verify_bundle(
            package_root=Path("/var/task"),
            recorded=None,
            installed={"boto3": "1.43.93", "botocore": "1.43.93"},
            module_paths={},
            in_lambda=True,
        )


def test_bundle_check_is_lambda_only():
    """로컬에는 번들이 없다 — 여기서 죽으면 검사도 배포 전 확인도 못 한다."""
    h.verify_bundle(
        package_root=Path("/var/task"),
        recorded=None,
        installed={},
        module_paths={"botocore": Path("/somewhere/else/botocore/__init__.py")},
        in_lambda=False,
    )


# ── ADR 0005 D4. 서버측 on/off·만료 ─────────────────────────────────────────


def test_default_is_off_and_writes_nothing(monkeypatch, tmp_path):
    """제어 키가 없으면 **아무것도 하지 않는다**(돈이 드는 쪽으로 기울지 않는다)."""
    s3 = _FakeS3({h.STRUCTURE_LATEST_KEY: b"{}"})
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler({"time": EVENT_TIME})

    assert result["live"] is False
    assert calls == [], "꺼져 있는데 관측을 돌렸다"
    assert s3.puts == [], "꺼진 주기에 문서를 올렸다"


def test_expiry_ceiling_comes_from_control_last_modified(monkeypatch, tmp_path):
    """🔴 브라우저가 보낸 `expiresAt` 이 먼 미래여도 **마지막 PUT + 8시간**을 넘지 못한다.

    이것이 「탭을 닫아도 과금이 이어지지 않는다」의 실제 구현이다 — 연장은 다시 PUT 하는 것으로만
    되고, PUT 하면 `LastModified` 가 갱신된다.
    """
    modified = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc) - timedelta(hours=9)
    decision = h.decide(
        {"live": True, "expiresAt": "2027-01-01T00:00:00Z"},
        control_modified=modified,
        now=datetime(2026, 9, 24, 7, 5, tzinfo=timezone.utc),
    )
    assert decision.live is False
    assert "만료" in decision.reason

    # 같은 값이라도 방금 PUT 된 것이면 켜진다 — 상한은 「먼 미래 금지」가 아니라 「8시간」이다.
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
    """🔴 돈이 드는 층은 **명시적 true** 에서만 돈다(`"true"` 같은 문자열도 아니다)."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--metrics" not in calls[0]
    assert "--utilization" not in calls[0], (
        "사용률 키가 없고 2층도 꺼짐 — 옛 화면 파일의 「구조 조회만」이다"
    )

    s3.objects[h.CONTROL_KEY] = _control(layer2="true")
    calls2 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--metrics" not in calls2[0], "문자열 'true' 를 켜짐으로 읽었다"

    s3.objects[h.CONTROL_KEY] = _control(layer2=True)
    calls3 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--metrics" in calls3[0]
    assert "--utilization" in calls3[0], (
        "사용률 키가 없는 옛 화면 파일의 「지표까지」는 사용률도 받았다 — 이어 간다"
    )
    assert "--previous" in calls3[0], (
        "전제: 긴 묶음 이월의 입력(직전 관측 문서)이 같이 간다"
    )


@pytest.mark.parametrize(
    ("layer2", "util", "metrics", "utilization"),
    [
        (False, True, False, True),  # 사용률만 — 고객이 「사용률」만 눌렀다
        (
            True,
            False,
            True,
            False,
        ),  # 모니터링(지표까지)만 — 사용률 키가 false 면 사용률을 돌리지 않는다
        (True, True, True, True),  # 둘 다
        (False, "true", False, False),  # 문자열은 켜짐이 아니다(돈이 드는 층)
    ],
)
def test_utilization_is_its_own_switch(
    monkeypatch, tmp_path, layer2, util, metrics, utilization
):
    """사용률은 모니터링과 별개다(사용자 결정 2026-10-06) — 제어 키의 `utilization` 이 따로 켠다."""
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = _control(layer2=layer2, utilization=util)
    calls = _install(monkeypatch, tmp_path, s3)
    monkeypatch.setenv("FLOW_LOGS", "ENABLED")

    result = h.handler({"time": EVENT_TIME})

    assert ("--metrics" in calls[0]) is metrics, calls[0]
    assert ("--utilization" in calls[0]) is utilization, calls[0]
    # 흐름 로그는 여전히 2층에만 붙는다 — 사용률만 켠 주기에 붙이면 엔진이 거부한다
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
        ),  # 2층을 끈 주기 — 붙이면 엔진이 거부하고 그 주기 문서가 안 올라간다
        (True, "DISABLED", False),
        (True, None, False),  # 옛 스택(환경 변수 없음) = 꺼짐
        (
            True,
            "enabled",
            False,
        ),  # 스택 값은 대문자 둘 중 하나뿐이다 — 다른 값은 켜짐이 아니다
    ],
)
def test_flow_logs_only_with_layer2_and_the_stack_switch(
    monkeypatch, tmp_path, layer2, env, expected
):
    """S5 U9 — 흐름 로그는 **스택 파라미터가 켜졌고 그 주기에 2층이 켜졌을 때만** 붙는다."""
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = _control(layer2=layer2)
    calls = _install(monkeypatch, tmp_path, s3)
    if env is not None:
        monkeypatch.setenv("FLOW_LOGS", env)

    result = h.handler({"time": EVENT_TIME})

    assert ("--flow-logs" in calls[0]) is expected, calls[0]
    assert ("--metrics" in calls[0]) is layer2
    assert result["flow_logs"] is expected


# ── 불변식 ④. 실패는 올리지 않는다 ──────────────────────────────────────────


def test_engine_failure_is_not_uploaded(monkeypatch, tmp_path):
    """반쯤 만들어진 관측 문서는 「없다」보다 나쁘다 — 화면이 그것을 「지금」으로 그린다."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3, exit_code=2, writes=b'{"half": "written"}')

    with pytest.raises(h.ObserveError):
        h.handler({"time": EVENT_TIME})

    assert s3.puts == []


def test_missing_structure_snapshot_fails_closed(monkeypatch, tmp_path):
    """관측은 구조 스냅샷 기준이다. 없으면 추측으로 대체하지 않는다."""
    s3 = _FakeS3({h.CONTROL_KEY: _control()})
    calls = _install(monkeypatch, tmp_path, s3)

    with pytest.raises(h.MissingInputError):
        h.handler({"time": EVENT_TIME})

    assert calls == []
    assert s3.puts == []


# ── 이력·정리 ────────────────────────────────────────────────────────────────


def test_previous_is_the_latest_pointer(monkeypatch, tmp_path):
    """이력 입력은 **직전 관측 문서 하나**다. 그 문서가 곧 화면이 폴링하는 포인터다."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--previous" not in calls[0], "첫 주기에는 직전 문서가 없다"

    s3.objects[h.STATE_LATEST_KEY] = b'{"run": {"observed_at": "2026-09-24T07:00:09Z"}}'
    calls2 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--previous" in calls2[0]


def test_previous_structure_is_optional_and_passed_when_present(monkeypatch, tmp_path):
    """없어진 것의 모습 입력 — 없으면 관측은 그대로 돌고(첫 구조 뒤), 있으면 엔진에 넘긴다."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)
    result = h.handler({"time": EVENT_TIME})
    assert result["result"] != "skipped" and calls, (
        "직전 구조가 없다고 관측을 건너뛰었다"
    )
    assert "--previous-topology" not in calls[0]

    s3.objects[h.STRUCTURE_PREVIOUS_KEY] = b'{"run": {"collected_at": "x"}}'
    calls2 = _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert "--previous-topology" in calls2[0]


def test_denied_previous_structure_does_not_stop_the_cycle(
    monkeypatch, tmp_path, capsys
):
    """🔴 권한이 빠진 스택(옛 템플릿 + 새 코드)에서도 5분 관측은 계속 올라간다 — 로그에만 남긴다."""
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


# ── 🆕 누적 구조에서 직전 관측의 구조 찾기(QA B 2차 N-05 · ADR 0004 D4 보강 개정 2026-10-17) ─────────────


def _struct(digest: str, at: str) -> bytes:
    return json.dumps({"run": {"content_digest": digest, "collected_at": at}}).encode()


def _gap_bucket(archived_digest: str) -> _FakeS3:
    """실시간을 끈 사이 구조가 두 번 바뀌었다 — 직전 관측(03:21 구조)은 지금 포인터(05:21)에도 사본(04:21)에도 없다."""
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
            # 창 앞(수집 시작보다 이르다) — 받지도 않는다
            "snapshots/structure/2026/09/24/0315Z/topology.json": _struct(
                "sha256:old", "x"
            ),
            # 그 수집의 누적 키(post_build 시각 = 수집 시작 + 몇 분)
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
    """직전 관측의 구조가 포인터·사본 어디에도 없으면 누적 스냅샷에서 **그 한 장**을 받아 한 장 더 넘긴다."""
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
    """창 안의 키라도 **지문이 다르면** 넘기지 않는다 — 다른 시각의 이름·연결이 「그때 모습」이 된다."""
    s3 = _gap_bucket("sha256:someone-else")
    _install(monkeypatch, tmp_path, s3)
    seen = _record_structures(monkeypatch)
    h.handler({"time": EVENT_TIME})
    assert seen and len(seen[0]) == 1, "짝이 아닌 누적 구조를 넘겼다"
    assert "누적 스냅샷에도 없다" in capsys.readouterr().out


def test_paired_pointer_means_no_archive_listing(monkeypatch, tmp_path):
    """평소(직전 관측이 지금 구조와 짝) 5분 차례는 누적 스냅샷을 **나열하지도 않는다**(비용 0)."""
    s3 = _gap_bucket("sha256:old")
    s3.objects[h.STRUCTURE_LATEST_KEY] = _struct("sha256:old", "2026-09-24T03:21:23Z")
    _install(monkeypatch, tmp_path, s3)
    h.handler({"time": EVENT_TIME})
    assert getattr(s3, "lists", []) == []


def test_archive_window_crosses_midnight_and_keeps_order() -> None:
    """키 시각 = 구조 단 post_build 벽시계(수집 시작보다 늦다) — 자정을 넘으면 다음 날 프리픽스도 본다 · 오래된 순."""
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
    """포인터가 누적 문서와 다른 바이트면 이력이 자기 자신과 어긋난다."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    h.handler({"time": EVENT_TIME})

    assert s3.objects[STATE_KEY] == s3.objects[h.STATE_LATEST_KEY] == OBSERVATION
    encrypted = [extra.get("ServerSideEncryption") for _, _, extra in s3.puts]
    assert encrypted == ["AES256", "AES256"], "암호화 없이 올렸다"


def test_workdir_is_removed_between_cycles(monkeypatch, tmp_path):
    """🔴 워밍된 컨테이너의 `/tmp` 는 다음 호출까지 산다 — 지난 주기 파일이 남으면 사고다."""
    s3 = _ready_bucket()
    _install(monkeypatch, tmp_path, s3)

    h.handler({"time": EVENT_TIME})

    assert list(tmp_path.iterdir()) == []


# ── 「누르면 바로 깨우기」(2026-10-06) — 제어 키를 쓴 S3 이벤트 ─────────────────────────


def _wake(time: str = EVENT_TIME) -> dict[str, Any]:
    """S3 → EventBridge 의 「Object Created」 모양(필요한 칸만). 핸들러가 읽는 것은 `source`·`time` 이다."""
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
    """켠 순간의 S3 이벤트 — 스케줄과 같은 길로 돌고, 키는 그 이벤트의 시각이다."""
    s3 = _ready_bucket()
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler(_wake("2026-09-24T07:07:41Z"))

    assert len(calls) == 1
    assert result["trigger"] == "wake"
    key = "snapshots/state/2026/09/24/0707Z/observation.json"
    assert result["key"] == key and s3.objects[key] == OBSERVATION
    assert s3.objects[h.STATE_LATEST_KEY] == OBSERVATION


def test_a_lease_renewal_wake_does_not_observe_again(monkeypatch, tmp_path):
    """화면은 켠 동안 5분마다 같은 키를 다시 쓴다 — 직전 관측이 6분 안이고 층이 같으면 돌지 않는다(호출 = 돈).

    살아 있는 짝: 같은 버킷 · 같은 이벤트를 **스케줄**로 보내면 돈다(건너뛰기가 깨우기에만 걸린다).
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
        # 모니터링만 보다가 사용률을 켰다 — 사용자가 방금 고른 것이 늦게 오면 안 된다.
        ({"utilization": True}, {}, "사용률을 새로 켰다"),
        ({"layer2": True, "utilization": False}, {}, "2층을 새로 켰다"),
        # 2층을 내렸다 — 다음 문서에서 흐름 판정이 빠져야 화면이 「모니터링 꺼짐」과 맞는다.
        ({"layer2": False, "utilization": False}, {"metric": True}, "2층을 내렸다"),
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
    """6분보다 오래된 문서는 화면이 「지금」으로 받지 않는다 — 층이 같아도 돈다(경계 바로 바깥)."""
    s3 = _ready_bucket()
    s3.objects[h.STATE_LATEST_KEY] = _previous("2026-09-24T06:58:59Z")
    calls = _install(monkeypatch, tmp_path, s3)

    h.handler(_wake())

    assert len(calls) == 1


# ── 「구조를 따라간다」(QA B P3 · 사용자 결정 2026-10-14) — 새 구조 포인터 이벤트 · 옛 구조의 문서 ─────────────────

DG_OLD = "sha256:" + "a" * 64
DG_NEW = "sha256:" + "b" * 64


def _structure_event(time: str = EVENT_TIME) -> dict[str, Any]:
    """구조 단이 `structure-latest.json` 을 올린 순간의 S3 이벤트."""
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
    """새 구조가 올라온 순간 = 직전 관측이 옛 구조의 것이 된 순간이다. 6분 안 · 같은 층이어도 **건너뛰지 않는다** —
    건너뛰면 열어 둔 화면이 «최신 화면 열기» 로 넘어간 뒤 다음 5분 차례까지 「짝 안 맞음」으로 선다(B-13 · B-09).

    살아 있는 짝: 같은 버킷 · 같은 신선한 문서를 **제어 키** 이벤트로 보내면 건너뛴다(지문이 같다).
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
    """구조 이벤트를 놓쳤거나(옛 스택) 막 켠 화면이 새 구조일 때 — 직전 문서가 신선해도 **지금 구조와 짝이 아니면** 돈다."""
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
        # 지문이 한쪽에만 있다 = 모른다 → 건너뛰기 판정 그대로(화면의 `pairOf` 와 같은 규칙)
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
    """끄기도 같은 키를 쓴다 — 그 깨우기는 꺼짐 판정에서 끝난다(관측·업로드 0)."""
    s3 = _ready_bucket()
    s3.objects[h.CONTROL_KEY] = b'{"live": false}'
    calls = _install(monkeypatch, tmp_path, s3)

    result = h.handler(_wake())

    assert result["live"] is False and calls == [] and s3.puts == []


def test_external_id_is_passed_to_the_engine_only_when_set(monkeypatch, tmp_path):
    """스택 A `MemberExternalId` → 환경변수 `MEMBER_EXTERNAL_ID` → 엔진 `--external-id`. 비우면 인자를 넣지 않는다.

    빠지면 멤버 역할이 `sts:ExternalId` 조건을 건 계정은 5분마다 상태가 전부 「확인 불가」가 된다(구조 화면은 멀쩡하다).
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
