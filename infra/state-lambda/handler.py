"""상태 단 실행체 — EventBridge 가 부르는 얇은 핸들러 (ADR 0004 D2-1, 조건 5개).

부르는 것은 둘이다: 5분 스케줄 · S3 이벤트 — 화면이 제어 키를 쓴 순간(「누르면 바로 깨우기」 2026-10-06)과
구조 단이 새 구조 포인터를 올린 순간(「구조를 따라간다」 QA B P3 · 사용자 결정 2026-10-14).

이 파일이 하는 일은 넷뿐이다:

1. **번들 검사**(조건 1) — 우리가 담은 boto3·botocore 가 실제로 임포트됐는지 본다.
2. **시간 버전 키**(조건 2) — 키를 **EventBridge 이벤트의 `time`** 에서 만든다. 실행 시각이 아니다.
3. **관측 실행** — `nettopo observe` 를 **로컬 파일로만** 돌린다(조건 4 — 엔진은 S3 를 모른다).
4. **업로드** — 산출물을 `put_object` 로 올린다. 누적 키는 **덮어쓰지 않는다**(ADR 0004 D1).

🔴 관측 논리는 한 줄도 여기 없다. `engine/src/` 에 `s3`·`put_object` 가 **0건**인 경계를
유지하는 것이 조건 4 의 목적이고, 그 경계가 무너지면 「읽기 전용 엔진」(불변식 ①)의 검사 표면이
엔진 밖으로 새어 나간다.

🔴 **실패는 조용히 성공으로 바꾸지 않는다.** 번들이 어긋나거나 이벤트에 `time` 이 없거나 관측이
0 이 아닌 코드로 끝나면 **예외로 죽는다** → 비동기 호출의 재시도 2회 뒤 DLQ 로 간다. 반쯤 만들어진
관측 문서를 올리는 것이 최악이다 — 화면은 그것을 「지금 상태」로 그린다(불변식 ④).
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
# 패키징 스크립트(`build-state-lambda.sh`)가 zip 안에 써 넣는다. 배포 시각의 사실이므로
# 템플릿 파라미터로 받지 않는다 — 파라미터는 사람이 잊어도 배포가 되고, 이 파일은 안 된다.
BUNDLED_VERSIONS = PACKAGE_ROOT / "bundled-versions.json"

# UI 가 쓰는 유일한 객체(ADR 0005). 없으면 「실시간이 켜진 적이 없다」로 읽는다.
CONTROL_KEY = "control/desired.json"
# 구조 단(CodeBuild)이 매 빌드 끝에 덮어쓰는 포인터. 목록 조회를 O(1) 로 만든다.
STRUCTURE_LATEST_KEY = "structure-latest.json"
# 그 포인터가 가리키던 **직전 내용**의 구조. 구조 단이 내용이 바뀔 때만 옮겨 둔다(buildspec).
# 없어진 리소스의 모습(`change.was`)을 이력에 적는 데만 쓴다 — 없으면 모습 없이 적는다.
STRUCTURE_PREVIOUS_KEY = "structure-previous.json"
# 화면이 폴링하는 것 = **직전 관측 문서 그 자체**다. 그래서 이력 입력(`--previous`)도 이 키다.
STATE_LATEST_KEY = "state-latest.json"
STATE_PREFIX = "snapshots/state"
# 구조 단의 누적 키(`snapshots/structure/YYYY/MM/DD/HHMMZ/topology.json`). 🆕 직전 관측의 구조가 지금 포인터에도 사본에도
# 없을 때만 여기서 **그 한 장**을 찾는다(QA B 2차 N-05 · ADR 0004 D4 보강 개정 2026-10-17) — 실시간을 끈 사이 구조 내용이 두 번 넘게
# 바뀌면 사본(`structure-previous.json`)이 이미 다른 구조로 덮여 있어서, 지운 리소스의 이력 줄이 이름 없이 id 로 남았다.
STRUCTURE_ARCHIVE_PREFIX = "snapshots/structure"
# 🔴 누적 키의 시각은 구조 단 **post_build 의 벽시계**다(`stack-a-collector.yaml` `STAMP="$(date -u …)"`) — 수집 시작 시각
#    (`run.collected_at` = 관측의 `topology_collected_at`)보다 늦다. 그래서 키를 그 시각에서 **정확히** 만들 수 없고, 그 시각의
#    날짜 프리픽스를 나열해 [수집 시각(분 내림), + 창] 안의 키를 오래된 순으로 최대 몇 장만 받아 **지문으로 확인**한다.
ARCHIVE_WINDOW = timedelta(hours=2)
ARCHIVE_TRIES = 3
_ARCHIVE_KEY = re.compile(
    r"^snapshots/structure/(\d{4})/(\d{2})/(\d{2})/(\d{2})(\d{2})Z/topology\.json$"
)

# ADR 0005 D4. 브라우저가 보낸 `expiresAt` 을 서버측에서 자르는 상한.
MAX_TTL = timedelta(hours=8)
# 깨우기(제어 키 쓰기)인데 직전 관측이 이만큼 안이고 층이 같으면 돌지 않는다 — 화면은 켠 동안 5분마다 임대를
# 연장하느라 같은 키를 다시 쓴다. 6분 = 화면의 `LIVE_FRESH_MS`(켜기 전 6분 안의 문서는 「지금」으로 받는다)와 같은 값이다:
# 이 안의 문서면 화면이 이미 그것을 얹으므로, 다시 돌아도 사용자가 보는 것은 같고 호출(2층 = 돈)만 는다.
WAKE_FRESH = timedelta(minutes=6)
WAKE_SOURCE = "aws.s3"
TRIGGER_WORD = {"schedule": "주기", "wake": "깨우기", "structure": "새 구조"}


def _tmp_root() -> Path:
    # 🔵 상수가 아니라 함수다 — 임포트 시각에 굳으면 검사가 `/tmp` 를 진짜로 쓰게 된다.
    return Path(os.environ.get("NETTOPO_TMP", "/tmp"))  # noqa: S108


class BundleError(RuntimeError):
    """번들이 어긋났다 — 읽기 전용 방어선이 우리가 검사한 그 botocore 가 아니다."""


class ScheduleError(RuntimeError):
    """이벤트에 시각이 없다(스케줄·S3 이벤트 둘 다 `time` 을 준다) — 시간 버전 키를 만들 수 없다."""


class ObserveError(RuntimeError):
    """엔진이 0 이 아닌 코드로 끝났다 — 올리지 않는다."""


class MissingInputError(RuntimeError):
    """관측의 입력(구조 스냅샷)이 없다 — 구조 단이 한 번도 성공하지 않았다."""


def verify_bundle(
    *,
    package_root: Path,
    recorded: dict[str, str] | None,
    installed: dict[str, str],
    module_paths: dict[str, Path],
    in_lambda: bool,
) -> None:
    """조건 1 — 번들이 우리가 만든 그 번들인지 본다. 아니면 **죽는다**.

    왜 버전 하나를 이렇게까지 보는가: `awsguard.py` 가 botocore **이벤트 2개**
    (`before-parameter-build`·`before-call`)에 훅을 걸어 읽기 전용을 런타임 강제한다. 이벤트
    이름과 발생 순서는 botocore 내부 규약이고 AWS 는 Lambda 런타임의 번들 boto3 를 **예고 없이**
    올린다. 런타임 것이 임포트되면 훅은 조용히 다른 계약 위에서 돌고 **방어선이 헐거워진 것을
    아무도 모른다.** 그래서 모르는 번들로는 한 번도 돌지 않는다.

    🔵 로컬(`in_lambda=False`)에서는 번들이 존재하지 않으므로 검사할 대상이 없다 — 이 함수는
    Lambda 안에서만 판정한다. 검사는 `in_lambda=True` 를 주고 재는 것이 정답이다.
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
    """조건 2 — 키를 **이벤트 시각**에서 만든다. 실행 시각으로 만들면 안 되는 이유가 있다.

    비동기 호출은 **자동 재시도 2회**다. 실행 시각으로 키를 만들면 같은 주기의 재시도가
    **다른 키**로 앉아 ADR 0004 D1(덮어쓰기 금지)과 싸운다 — 한 주기에 두 개의 「지금 상태」가
    생기고 둘 중 무엇이 맞는지 아무도 모른다. 이벤트 시각이면 재시도가 **멱등**이다.
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
    # 사용률(ADR 0011) — 2층과 **독립**이다(사용자 결정 2026-10-06 「사용률만 보고 싶은 고객은 사용률만」 · 관측 스키마 1.5.0)
    utilization: bool = False


def decide(
    control: dict[str, Any] | None,
    *,
    control_modified: datetime | None,
    now: datetime,
) -> Decision:
    """제어 키를 **서버측에서** 해석한다(ADR 0005 D4 — 브라우저 값을 믿지 않는다).

    🔴 만료 상한의 기준 시각은 **제어 객체의 `LastModified`** 다. 「켠 시각」은 브라우저만 알고
    위조할 수 있지만 `LastModified` 는 S3 가 쓴다. 그래서 `expiresAt` 이 아무리 먼 미래여도
    **마지막 PUT + 8시간**을 넘지 못한다 — 연장은 다시 PUT 하는 것으로만 되고(그러면
    `LastModified` 가 갱신된다) 탭을 닫은 채 과금이 이어지는 경로가 사라진다.

    🔵 기본은 **꺼짐**이다. 제어 키가 없거나 읽히지 않으면 돈이 드는 쪽으로 기울지 않는다.
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
    # 🔴 키가 **없으면** 옛 화면 파일이 쓴 제어 키다 — 그 화면은 「지표까지」를 고르면 사용률도 같이 받았다(1.4.0 까지는
    #    2층과 같이 돌았다). 그 판을 이어 가려면 없음 = 2층을 따른다. 키가 있으면 명시적 true 만 켜짐이다(돈이 드는 층).
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
    """깨우기가 할 일이 없으면 그 이유를, 돌아야 하면 `None` 을 준다.

    할 일이 없다 = 직전 관측이 `WAKE_FRESH` 안이고 **켜 달라는 층이 그 문서의 층과 같다.** 층이 하나라도 다르면
    (모니터링만 보다가 사용률을 켰다 · 2층을 내렸다) 돈다 — 사용자가 방금 고른 것이 화면에 늦게 오는 것이 이 규칙이
    없애려는 일이다. 직전 문서가 없거나 읽을 수 없으면 돈다(모르면 사용자 쪽으로 기운다).
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
    """S3 → EventBridge 「Object Created」 이벤트의 객체 키. 다른 모양이면 `None`."""
    detail = event.get("detail")
    obj = detail.get("object") if isinstance(detail, dict) else None
    key = obj.get("key") if isinstance(obj, dict) else None
    return key if isinstance(key, str) else None


def pair_moved(
    previous: dict[str, Any] | None, structure: dict[str, Any] | None
) -> str | None:
    """직전 관측이 **지금 구조 포인터와 짝이 아니면** 그 이유를, 짝이면(또는 판정할 재료가 없으면) `None` 을 준다.

    🔴 「직전 관측이 6분 안이다」만 보고 깨우기를 건너뛰면, 구조가 막 바뀐 뒤 «모니터링»을 켠 화면(새 구조)이
    옛 구조의 문서를 받아 다음 5분 차례까지 「짝 안 맞음」으로 선다(QA B-09). 짝은 화면과 같은 규칙이다 —
    내용 지문(`run.topology_content_digest` ↔ 구조의 `run.content_digest`)이 **양쪽에 다 있을 때만** 비교한다
    (`renderer/flow.js` `pairOf`). 한쪽이라도 없으면 「모른다」 → 건너뛰기 판정을 그대로 둔다.
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
    """없으면 `(None, None)`. **다른 오류는 삼키지 않는다** — 권한 문제를 「없다」로 바꾸면
    화면이 「사용자가 끈 것」과 「우리가 못 읽은 것」을 구분하지 못한다."""
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
    """선택 입력 — 읽기가 거부돼도 **주기를 죽이지 않는다**(불변식 ④). 대신 로그에 남긴다.

    🔴 `_download` 와 다른 이유: 필수 입력(구조·직전 관측)은 없으면 관측이 거짓이 되지만, 이
    입력은 없어도 관측은 그대로 참이고 없어진 줄에 모습이 안 붙을 뿐이다. 권한이 빠진 채 배포된
    스택(옛 템플릿 + 새 코드)에서 5분 관측 전체가 멈추는 것은 그 대가보다 크다.
    """
    try:
        return _download(s3, bucket, key, dest)
    except botocore.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "?")
        print(f"선택 입력 {key} 를 못 읽었다({code}) — 없어진 것의 모습 없이 관측한다")
        return False


def structure_pairs(previous: Any, structure: Any) -> bool:
    """직전 관측이 그 구조와 **짝**인가 — 엔진 `history._paired_structure` 와 같은 규칙이다.

    지문(`run.topology_content_digest` ↔ `run.content_digest`)이 양쪽에 다 있으면 지문, 아니면 수집 시각.
    🔴 규칙이 엔진과 갈라지면 이 함수가 「짝이 있다」고 건너뛴 회차에 엔진은 짝을 못 찾는다(= 모습 없는 줄).
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
    """그 수집 시각에서 창 끝까지의 날짜 프리픽스(대개 하나 · 자정을 넘으면 둘)."""
    start = _minute_floor(collected_at)
    days = dict.fromkeys(
        t.strftime(f"{STRUCTURE_ARCHIVE_PREFIX}/%Y/%m/%d/")
        for t in (start, start + ARCHIVE_WINDOW)
    )
    return list(days)


def archive_candidates(collected_at: str, keys: list[str]) -> list[str]:
    """누적 키 중 [수집 시각(분 내림), + 창] 안의 것 — **오래된 순**(결정론) · 최대 `ARCHIVE_TRIES` 장."""
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
    """직전 관측과 짝인 구조를 누적 스냅샷에서 찾아 `dest` 에 쓴다. 못 찾으면 `False` — **주기는 계속 돈다**.

    🔴 선택 입력이다(`_download_optional` 과 같은 이유): 없어도 관측은 참이고 없어진 줄에 모습이 안 붙을 뿐이다.
    🔴 받은 장은 **지문으로 확인한 뒤에만** 쓴다 — 창 안의 다른 구조(그 뒤 수동 수집 등)를 짝으로 넘기면 엔진은 짝을
       못 찾고 끝나지만, 규칙이 갈라지는 날 다른 시각의 이름·연결이 「그때 모습」이 된다.
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
    """누적 키에 쓴다. **이미 있으면 그대로 둔다**(ADR 0004 D1 — 덮어쓰기 금지).

    조건부 쓰기(`IfNoneMatch="*"`)가 412 를 주는 것은 **재시도가 멱등하게 끝났다**는 뜻이므로
    오류가 아니다. 이것이 조건 2(이벤트 시각 키)와 짝이다 — 같은 주기의 재시도는 같은 키를
    겨누고, 먼저 성공한 문서가 이긴다.
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
    """내려받은 입력을 짝 판정용으로 읽는다. 깨졌으면 `None` — 판정은 엔진이 다시 한다(여기서 죽지 않는다)."""
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
    # 🔴 `subprocess` 가 아니라 **같은 프로세스**에서 부른다. 셸을 끼우면 인자 따옴표 규칙이
    #    하나 더 생기고(구조 단 buildspec 에서 실제로 물린 자리다) 콜드스타트도 늘어난다.
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
    # 🔴 스택 파라미터 `FlowLogReading` 이 그대로 온다(기본 `DISABLED`). 정확히 `ENABLED` 일 때만 켠다 — 권한 정책도
    #    같은 값에서만 만들어지므로(스택 A `FlowLogsOn`), 다른 값을 켜짐으로 읽으면 매 주기 「권한 없음」만 쌓인다.
    flow_logs = os.environ.get("FLOW_LOGS", "DISABLED") == "ENABLED"
    s3 = boto3.client("s3")

    control, modified = _get_json(s3, bucket, CONTROL_KEY)
    decision = decide(control, control_modified=modified, now=event_time)
    if not decision.live:
        # 🔵 **아무것도 올리지 않는다.** 꺼진 주기에 문서를 쓰면 화면은 그것을 「지금 상태」로
        #    읽는다. 꺼져 있다는 사실은 화면이 마지막 문서의 시각으로 말한다(ADR 0006).
        print(f"실시간 꺼짐 — {decision.reason} · 주기 {stamp}")
        return {"stamp": stamp, "live": False, "reason": decision.reason}
    # 🔴 세 갈래다: 5분 차례(`schedule`) · 제어 키 쓰기(`wake`) · 새 구조 포인터(`structure`). 구조 포인터 이벤트는
    #    **건너뛰지 않는다** — 그 이벤트가 온 이유가 곧 「직전 관측이 옛 구조의 것이 됐다」이고, 열어 둔 화면이 새 화면
    #    파일로 넘어갔을 때 그 구조의 상태가 기다림 없이 와 있어야 한다(QA B-13 · B-09).
    trigger = "schedule"
    if event.get("source") == WAKE_SOURCE:
        trigger = "structure" if event_key(event) == STRUCTURE_LATEST_KEY else "wake"
    if trigger == "wake":
        previous_doc, _ = _get_json(s3, bucket, STATE_LATEST_KEY)
        redundant = wake_is_redundant(
            previous_doc, decision, flow_logs=flow_logs, now=event_time
        )
        if redundant is not None:
            # 신선하고 층도 같다 — 그래도 **옛 구조의 문서**면 돈다. 구조를 읽는 것은 건너뛰려는 순간뿐이다
            # (임대 연장 쓰기 = 5분마다 1회) — 도는 경우는 어차피 아래에서 같은 키를 내려받는다.
            structure_doc, _ = _get_json(s3, bucket, STRUCTURE_LATEST_KEY)
            moved = pair_moved(previous_doc, structure_doc)
            if moved:
                print(f"깨우기 건너뛰지 않음 — {moved} · 주기 {stamp}")
                redundant = None
        if redundant:
            # 🔵 아무것도 올리지 않는다 — 화면은 이미 그 문서를 얹고 있다. 다음 관측은 5분 주기가 낸다.
            print(f"깨우기 건너뜀 — {redundant} · 주기 {stamp}")
            return {
                "stamp": stamp,
                "live": True,
                "trigger": trigger,
                "skipped": redundant,
            }

    # 🔴 워밍된 컨테이너의 `/tmp` 는 **다음 호출까지 살아 있다.** 주기마다 새 디렉터리를 쓰고
    #    끝나면 지운다 — 지난 주기의 파일이 남아 있으면 「올렸는데 옛 문서」가 될 수 있다.
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
        # 스택 B `ExternalId` 를 준 멤버 역할은 같은 값을 넘겨야 들어간다(스택 A `MemberExternalId`) — 비우면 넘기지 않는다.
        external_id = os.environ.get("MEMBER_EXTERNAL_ID", "")
        if external_id:
            argv += ["--external-id", external_id]
        if decision.layer2:
            argv.append("--metrics")
            # 흐름 로그 근거는 2층과 같은 창에서만 판정한다 — 2층을 끈 주기에 붙이면 엔진이 거부한다(rc 1).
            if flow_logs:
                argv.append("--flow-logs")
        if decision.utilization:
            # 사용률(#27 · ADR 0011)은 2층과 **따로** 켠다(관측 스키마 1.5.0 — 2층 없이도 엔진이 받는다). 긴 묶음·서비스 한도의
            # 1시간 이월은 아래 `--previous`(직전 관측 문서)에서 읽는다. 한도 읽기 권한이 없는 옛 스택은 「기본 한도」로 계속 돈다.
            argv.append("--utilization")
        previous_topology = workdir / "previous-topology.json"
        if _download_optional(s3, bucket, STRUCTURE_PREVIOUS_KEY, previous_topology):
            # 🔴 선택 입력이다. 삭제는 대개 5분 관측이 매시 구조 수집보다 먼저 보므로 모습은
            #    `--topology` 에 있다. 그 사이에 구조가 새로 수집됐을 때만 이 파일이 필요하다.
            argv += ["--previous-topology", str(previous_topology)]
        previous = workdir / "previous.json"
        if _download(s3, bucket, STATE_LATEST_KEY, previous):
            # 이력의 입력은 직전 관측 문서 하나다(ADR 0004 D4). 없으면 기준선이 이번 관측이
            # 되고 그것은 오류가 아니다 — 첫 주기에는 파일이 없다.
            argv += ["--previous", str(previous)]
            # 🆕 직전 관측의 구조가 지금 포인터에도 사본에도 없으면 누적 스냅샷에서 그 한 장을 더 넘긴다(N-05).
            #    두 장 중 하나와 짝이면 **목록 조회도 하지 않는다**(평소 5분 차례의 비용 0).
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
    # 화면이 폴링하는 포인터. 누적 키와 **같은 바이트**라 이력 입력(`--previous`)으로 그대로 쓴다.
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
