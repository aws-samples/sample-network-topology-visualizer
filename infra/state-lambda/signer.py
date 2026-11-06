"""서명 단 — 빌드가 올린 서명 전 원본의 실시간 주소 칸 하나만 채워 `index.html` 로 게시한다
(ADR 0003 D2 · 결정 2026-09-27 · 게시 키 분리 2026-10-17).

이 함수를 부르는 것은 둘뿐이다: **5분 스케줄 규칙** · **구조 빌드(CodeBuild) 성공 이벤트 규칙**.

1. 서명 전 원본(`unsigned/index.html` — 구조 빌드만 쓴다)을 읽는다.
2. 주소 칸(`SLOT`)이 **정확히 1개**인지 본다. 아니면 **아무것도 쓰지 않는다**(장치 ①).
3. URL 4개를 서명한다 — 상태 읽기(`GET state-latest.json`) · 제어 쓰기·읽기(`PUT`·`GET control/desired.json` — 읽기 = 따라 보기 2026-10-28) ·
   최신 화면 열기(`GET index.html` — 사람이 «최신 화면 열기» 를 **눌렀을 때만** 쓰는 이동 주소 · QA B P3 ·
   사용자 결정 2026-10-14 · 불변식 ⑥ 의 클릭 전용 이동, 화면이 스스로 부르는 요청이 아니다).
4. 칸의 JSON 만 바꾼 문서를 `index.html` 에 쓴다(장치 ④ — 아래).
   (장치 ② 버전 복구 · ③ 매시 빌드가 처음부터 다시 굽는다 — 는 템플릿과 buildspec 에 있다.)

🔴 **사람이 받는 키(`index.html`)에 서명 전 판이 한 순간도 없다**(장치 ④ · QA B 2차 2026-10-17). 예전에는 빌드가
`index.html` 을 빈 칸으로 덮고 이 함수가 몇 초 뒤 그 자리에서 고쳤다(`IfMatch`) — QA 실측 64빌드에서 그 창은 2~9초
(중앙 6초)였고, 그 사이 받은 화면·«최신 화면 열기» 로 넘어간 화면은 모니터링을 켤 수 없었다. 지금 `index.html` 의
작성자는 **이 함수 하나**다(구조 빌드는 IAM 으로 막혔다 — 템플릿 `OnlyTheSignerPublishesThePage`). 그래서 조건부
쓰기가 지키던 「새 빌드를 옛 내용으로 덮지 않는다」는 **직렬 실행**이 지킨다 — 동시 실행 1(`ReservedConcurrentExecutions`)
이고 실행마다 원본을 새로 읽으므로 나중 실행이 늘 같거나 새 원본을 쓴다. 이 함수가 실패하면 `index.html` 은 직전 판
(서명된 옛 구조)으로 남는다 — 「서명 전」보다 낫고, 다음 5분 주기가 다시 게시한다.

🔴 **이 함수의 권한이 곧 URL 의 권한이다.** S3 는 서명 URL 요청을 **서명한 주체의 권한**으로
판정한다. 그래서 이 함수는 제어 키를 쓸 수 있어야 하고, 관측 함수(`handler.py`)와 **role 을 합치지
않는다** — 합치면 관측 함수가 자기 스위치를 켤 수 있다(ADR 0005 D4 의 서버측 기본 꺼짐이 무너진다).

🔴 **URL 수명 = min(요청한 `ExpiresIn`, 서명한 자격증명의 남은 수명).** Lambda 는 자기 자격증명의
만료 시각을 알려 주지 않는다. 그래서 **이 실행 환경이 그 자격증명을 처음 본 시각 + 세션 길이**
(CloudTrail 실측 720분, 2026-09-27)에서 여유를 뺀 값을 상한으로 쓴다. **가설이다** — 188 왕복에서 잰다.
화면은 `expires_at` 을 「실시간 갱신 — HH:MM 까지」로 보여 준다(없으면 사람이 왜 멈췄는지 추측한다).

🔵 번들 검사(`handler.verify_bundle`)를 하지 않는다 — 이 함수는 대상 계정에 들어가지 않고 읽기 전용
훅(`awsguard.py`)도 쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config
from handler import CONTROL_KEY, STATE_LATEST_KEY

INDEX_KEY = "index.html"
# 구조 빌드가 올리는 서명 전 원본(`infra/stack-a-collector.yaml` post_build). 이 함수만 읽고, 사람이 받는 키가 아니다.
UNSIGNED_KEY = "unsigned/index.html"

# 🔴 `renderer/flow.html` 의 태그와 **같은 모양**이어야 한다(`build-standalone.mjs` 가 같은 정규식으로
#    「정확히 1개」를 빌드에서 먼저 잰다). 본문을 `\{[^<]*\}` 로 좁힌 이유: 인라인된 JS 가 이 태그
#    문자열을 주석에 품어도 그 뒤에 JSON 객체 + `</script>` 가 바로 오지 않으면 칸으로 세지 않는다.
SLOT = re.compile(
    r'(<script id="nettopo-live" type="application/json">)(\{[^<]*\})(</script>)'
)

# 요청하는 수명. 실제 수명은 아래 자격증명 상한과의 최솟값이다.
URL_TTL = timedelta(hours=12)
# CloudTrail 실측(2026-09-27): Lambda 역할 세션 `durationSeconds=43200`(28/28건).
SESSION = timedelta(minutes=720)
# 「처음 본 시각」은 발급 시각보다 **늦다**(발급 → 환경 시작 → 첫 호출). 5분 주기 한 번과 콜드스타트를
# 덮는 여유다. 이만큼 짧게 말하는 쪽이 길게 말하는 쪽보다 낫다 — 화면이 약속한 시각보다 먼저 죽으면
# 사람은 「고장」으로 읽는다.
SESSION_MARGIN = timedelta(minutes=15)
# 이보다 짧게 남은 URL 은 굽지 않는다 — 화면을 연 사람이 스위치를 누르기도 전에 죽는다.
MIN_TTL = timedelta(minutes=5)

# 액세스 키 id → 이 실행 환경이 그 키를 처음 본 시각. 워밍된 환경은 다음 호출까지 모듈 상태가 산다.
# 🔴 키가 바뀌면(자격증명 교체) 새로 센다 — 옛 키의 시각으로 새 키의 수명을 재면 짧게 틀린다.
_FIRST_SEEN: dict[str, datetime] = {}


def credential_deadline(access_key: str | None, now: datetime) -> datetime:
    """서명한 자격증명이 죽는 시각의 **보수적 추정**. 키를 모르면(로컬) 요청 수명을 그대로 쓴다."""
    if not access_key:
        return now + URL_TTL
    first = _FIRST_SEEN.setdefault(access_key, now)
    return first + SESSION - SESSION_MARGIN


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slot_json(payload: dict[str, Any]) -> str:
    """칸에 넣을 JSON. 🔴 `<` 를 `\\u003c` 로 바꾼다 — URL 에는 없지만, 한 글자라도 들어오면
    `</script>` 로 문서가 끊기고 다음 주기에 `SLOT` 이 칸을 못 찾는다(= 스위치가 영영 비활성)."""
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def rewrite(html: str, payload: dict[str, Any]) -> str | None:
    """칸이 정확히 1개면 그 JSON 만 바꾼 문서를, 아니면 `None` 을 돌려준다(장치 ①)."""
    if len(SLOT.findall(html)) != 1:
        return None
    return SLOT.sub(
        lambda m: m.group(1) + slot_json(payload) + m.group(3), html, count=1
    )


def _client(region: str) -> Any:
    # 🔴 **리전 엔드포인트 + 가상 호스트** 로 서명한다. `us-east-1` 의 기본은 전역 엔드포인트
    #    (`s3.amazonaws.com`)이고, 다른 리전 버킷에 그 주소로 가면 307 리다이렉트가 온다 —
    #    브라우저는 CORS 요청의 리다이렉트를 따라가지 않으므로 화면만 조용히 실패한다.
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(
            signature_version="s3v4",
            s3={
                "addressing_style": "virtual",
                "us_east_1_regional_endpoint": "regional",
            },
        ),
    )


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    bucket = os.environ["ARTIFACT_BUCKET"]
    region = os.environ["AWS_REGION"]
    s3 = _client(region)
    now = datetime.now(timezone.utc)
    source = event.get("source") or "manual"

    try:
        got = s3.get_object(Bucket=bucket, Key=UNSIGNED_KEY)
    except s3.exceptions.NoSuchKey:
        # 구조 빌드가 (이 게시 방식으로) 한 번도 성공하지 않았다. 빌드 성공 이벤트가 이 함수를 다시 부른다.
        # 🔴 `index.html` 을 대신 읽어 그 자리에서 고치지 않는다 — 그 키는 이 함수만 쓰는 **출력**이다. 옛 방식의
        #    판이 남아 있으면 그 판의 서명이 죽을 때까지 그대로 두고, 다음 빌드가 원본을 올리면 이어진다.
        print(f"서명 건너뜀 — {UNSIGNED_KEY} 이 아직 없다 · 호출 {source}")
        return {"result": "no-unsigned"}
    content_type = got.get("ContentType") or "text/html"
    html = got["Body"].read().decode("utf-8")

    slots = len(SLOT.findall(html))
    if slots != 1:
        # 🔴 **쓰지 않는다.** 칸을 추측해 만들거나 둘 중 하나를 고르면 화면 코드가 다른 칸을 읽을 수
        #    있다. 매시 빌드가 처음부터 다시 구우므로 기다리는 것이 복구 경로다(장치 ③).
        print(f"서명 건너뜀 — 주소 칸 {slots}개(정확히 1개여야 한다) · 호출 {source}")
        return {"result": "slot-count", "slots": slots}

    credentials = boto3.Session().get_credentials()
    access_key = credentials.access_key if credentials else None
    ttl = min(URL_TTL, credential_deadline(access_key, now) - now)
    if ttl < MIN_TTL:
        print(
            f"서명 건너뜀 — 자격증명 남은 수명 추정 {ttl} < {MIN_TTL} · 호출 {source}"
        )
        return {"result": "credential-too-short", "seconds": int(ttl.total_seconds())}
    seconds = int(ttl.total_seconds())

    state_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": STATE_LATEST_KEY},
        ExpiresIn=seconds,
        HttpMethod="GET",
    )
    # 🔴 `ContentType` 을 서명에 넣지 않는다 — 넣으면 브라우저가 정확히 같은 헤더를 보내야 하고,
    #    어긋나면 403 이 「권한 없음」과 구별되지 않는다. 서명 헤더는 `host` 하나다.
    control_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": CONTROL_KEY},
        ExpiresIn=seconds,
        HttpMethod="PUT",
    )
    # 🆕 제어 키 **읽기**(따라 보기 · 사용자 결정 2026-10-28 · ADR 0005 D4 보강 4). 권한은 이 함수가 이미 갖는 제어 키 GetObject
    #    (`SignControlConditionalWrite` — S3 가 `If-Match` PUT 을 그 권한으로도 판정해 넣었던 1줄) 그대로다(새 권한 0). 같은 버킷 오리진이라
    #    CSP `connect-src` 도 그대로다. 화면은 이 칸이 없으면(옛 서명 함수) 옛 동작 — 다른 창과 부딪히면 이 창만 끈다.
    control_get_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": CONTROL_KEY},
        ExpiresIn=seconds,
        HttpMethod="GET",
    )
    # 🔴 **이동 주소**다 — 화면은 이 주소로 fetch 하지 않는다(CSP `connect-src` 와 무관 · 검사 106). 사람이 열어 둔 화면에서
    #    구조가 바뀌었을 때 «최신 화면 열기» 를 누르면 그 탭이 이 주소로 넘어간다(QA B-13). 키가 `index.html` 하나라 판이
    #    바뀌어도 늘 최신 판을 연다. 권한은 이 함수가 이미 갖는 `index.html` 읽기 그대로다(새 권한 0).
    index_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": INDEX_KEY},
        ExpiresIn=seconds,
        HttpMethod="GET",
    )
    payload = {
        "v": 1,
        "state_url": state_url,
        "control_url": control_url,
        "control_get_url": control_get_url,
        "index_url": index_url,
        "signed_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=seconds)),
    }
    new_html = rewrite(html, payload)
    assert new_html is not None  # 위에서 칸 1개를 확인했다

    # 🔴 실패(권한·스로틀)는 삼키지 않는다 — 재시도 2회 → DLQ 에 남는다. 그동안 `index.html` 은 직전 판 그대로다.
    # 메타데이터 = 어느 원본에서 게시했는지(배포 뒤 `head-object` 두 번으로 「같은 빌드」를 대조한다 · 런북).
    s3.put_object(
        Bucket=bucket,
        Key=INDEX_KEY,
        Body=new_html.encode("utf-8"),
        ContentType=content_type,
        ServerSideEncryption="AES256",
        Metadata={"unsigned-etag": str(got.get("ETag", "")).strip('"')},
    )
    print(
        f"서명 {seconds}초(만료 {payload['expires_at']}) · 칸 1개 채워 {INDEX_KEY} 게시 · 호출 {source}"
    )
    return {
        "result": "signed",
        "seconds": seconds,
        "expires_at": payload["expires_at"],
    }
