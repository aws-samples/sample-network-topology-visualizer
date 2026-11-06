"""서명 단 — 「주소 칸만 바꾼다」는 약속이 사고 상황에서도 지켜지는지 재는 검사.

여기서 재는 것은 「서명이 된다」가 아니라 **무엇을 쓰지 않는가**다:

- 주소 칸이 0개·2개면 **아무것도 쓰지 않는가**(장치 ① — 추측으로 칸을 고르지 않는다)
- 사람이 받는 `index.html` 을 **원본으로 읽지 않는가**(장치 ④ — 서명 전 원본 `unsigned/index.html` 에서만 게시한다 ·
  QA B 2차 2026-10-17: 빌드가 `index.html` 을 빈 칸으로 덮던 2~9초 창을 없앴다)
- 칸 밖의 바이트가 **한 글자도** 바뀌지 않는가
- 서명하는 키가 화면이 부르는 **두 경로 + 이동 주소 하나뿐**인가(불변식 ⑥ 의 예외 2경로 + 클릭 전용 이동 · 2026-10-14)
- 화면에 약속하는 만료가 **자격증명의 수명을 넘지 않는가**

🔴 실제 S3 를 부르지 않는다. 서명은 botocore 가 로컬에서 계산하므로 가짜 자격증명으로 실제 URL 을 만든다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import botocore
import pytest
import signer as sg

BUCKET = "nettopo-artifacts-test"
REGION = "us-east-1"
UNSIGNED = "unsigned/index.html"
FLOW_HTML = Path(__file__).resolve().parents[2] / "renderer" / "flow.html"
EMPTY_SLOT = (
    '<script id="nettopo-live" type="application/json">'
    '{"v":1,"state_url":null,"control_url":null,"signed_at":null,"expires_at":null}'
    "</script>"
)
PAGE = (
    "<!doctype html><html><head><title>t</title></head><body>\n"
    "<div id=a>계정 111122223333 · 10.0.0.0/16</div>\n"
    f"{EMPTY_SLOT}\n"
    "<script>\n/* ===== flow.js ===== */\nconst x = 1\n</script>\n</body></html>\n"
)


class _NoSuchKey(Exception):
    pass


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    """S3 대역. 서명은 진짜 botocore 클라이언트에 맡기고(로컬 계산), 읽기·쓰기만 가로챈다."""

    class exceptions:  # noqa: N801 — botocore 클라이언트의 모양을 따른다
        NoSuchKey = _NoSuchKey

    def __init__(self, real: Any, objects: dict[str, bytes]) -> None:
        self._real = real
        self.objects = dict(objects)
        self.etag = '"etag-1"'
        self.gets: list[str] = []
        self.puts: list[tuple[str, bytes, dict[str, Any]]] = []
        self.signed: list[tuple[str, str, int]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        assert Bucket == BUCKET
        self.gets.append(Key)
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {
            "Body": _Body(self.objects[Key]),
            "ETag": self.etag,
            "ContentType": "text/html",
        }

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, **extra: Any
    ) -> dict[str, Any]:  # noqa: N803
        assert Bucket == BUCKET
        self.objects[Key] = Body
        self.puts.append((Key, Body, extra))
        return {}

    def generate_presigned_url(
        self, op: str, *, Params: dict[str, Any], ExpiresIn: int, HttpMethod: str
    ) -> str:  # noqa: N803
        self.signed.append((op, Params["Key"], ExpiresIn))
        return self._real.generate_presigned_url(
            op, Params=Params, ExpiresIn=ExpiresIn, HttpMethod=HttpMethod
        )


@pytest.fixture(autouse=True)
def _fake_env(monkeypatch):
    monkeypatch.setenv("ARTIFACT_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key-a")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "fake-secret-for-tests")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "fake-token")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    sg._FIRST_SEEN.clear()


def _install(monkeypatch, objects: dict[str, bytes]) -> _FakeS3:
    real = sg._client(REGION)
    fake = _FakeS3(real, objects)
    monkeypatch.setattr(sg, "_client", lambda region: fake)
    return fake


def _slot_payload(html: str) -> dict[str, Any]:
    (body,) = [m.group(2) for m in sg.SLOT.finditer(html)]
    return json.loads(body)


def test_the_repo_page_has_exactly_the_slot_the_signer_looks_for():
    """`flow.html` 의 태그와 `SLOT` 이 어긋나면 배포된 화면의 스위치가 **영영** 비활성이다.

    빌드는 통과하고(칸이 없다고 렌더가 실패하지 않는다) 서명 함수는 「칸 0개」를 로그에 남기고
    끝난다 — 사람이 화면에서 보는 것은 「실시간을 쓸 수 없다」 한 줄뿐이다.
    """
    html = FLOW_HTML.read_text()
    assert len(sg.SLOT.findall(html)) == 1
    assert _slot_payload(html)["state_url"] is None, "리포의 칸은 비어 있어야 한다"


def test_rewrites_only_the_slot(monkeypatch):
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    result = sg.handler({"source": "aws.events"})
    assert result["result"] == "signed"
    ((key, body, extra),) = fake.puts
    assert key == "index.html"
    assert extra["ServerSideEncryption"] == "AES256"
    # 어느 원본에서 게시했는지 — 배포 뒤 `head-object` 두 번으로 대조한다(런북).
    assert extra["Metadata"] == {"unsigned-etag": "etag-1"}
    # 원본은 그대로다 — 다음 5분 주기가 같은 원본에서 다시 게시한다.
    assert fake.objects[UNSIGNED] == PAGE.encode()
    new = body.decode()
    # 칸 밖은 한 글자도 바뀌지 않는다.
    assert sg.SLOT.sub("", new) == sg.SLOT.sub("", PAGE)
    payload = _slot_payload(new)
    assert payload["v"] == 1
    assert payload["state_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/state-latest.json?"
    )
    assert payload["control_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/control/desired.json?"
    )
    # 🆕 따라 보기(2026-10-28) — 같은 버킷 오리진의 `/control/desired.json` 읽기. 화면은 오리진·키가 다르면 칸을 버린다.
    assert payload["control_get_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/control/desired.json?"
    )
    # 🆕 최신 화면 열기(QA B P3) — 같은 버킷 오리진의 `/index.html`. 화면은 오리진·키가 다르면 버튼을 내지 않는다.
    assert payload["index_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/index.html?"
    )


def test_signs_exactly_the_three_screen_paths_and_the_page(monkeypatch):
    """불변식 ⑥ 의 예외는 **세 경로**(상태 읽기 · 제어 키 읽기·쓰기 — 🆕 제어 읽기 = 따라 보기, 사용자 결정 2026-10-28) +
    **클릭 전용 이동 주소 하나**(`index.html` 읽기 · 사용자 결정 2026-10-14 QA B P3)다. 다섯 번째 URL 이 생기면 문서
    (ADR 0003 D2 · ADR 0005 D4 보강 4 · ADR 0006 O6 · 불변식 ⑥ 「외부 요청 = 실시간 상태 읽기·제어 키 읽기·쓰기」)가 먼저 바뀌어야 한다."""
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    sg.handler({})
    assert sorted((op, key) for op, key, _ in fake.signed) == [
        ("get_object", "control/desired.json"),
        ("get_object", "index.html"),
        ("get_object", "state-latest.json"),
        ("put_object", "control/desired.json"),
    ]
    # 네 주소의 수명은 같다 — 이동 주소만 먼저 죽으면 버튼이 403 화면으로 보내고, 읽기 주소만 먼저 죽으면 따라 보기가 멈춘다.
    assert len({seconds for _, _, seconds in fake.signed}) == 1


def test_signature_covers_host_only(monkeypatch):
    """`content-type` 을 서명에 넣으면 브라우저가 한 글자 다른 헤더를 보낼 때 403 이 「권한 없음」과
    구별되지 않는다. 서명 헤더는 `host` 하나여야 한다."""
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    sg.handler({})
    payload = _slot_payload(fake.objects["index.html"].decode())
    for url in (
        payload["state_url"],
        payload["control_url"],
        payload["control_get_url"],
        payload["index_url"],
    ):
        query = parse_qs(urlsplit(url).query)
        assert query["X-Amz-SignedHeaders"] == ["host"]


@pytest.mark.parametrize("slots", [0, 2])
def test_wrong_slot_count_writes_nothing(monkeypatch, slots):
    page = PAGE.replace(EMPTY_SLOT, EMPTY_SLOT * slots)
    fake = _install(monkeypatch, {UNSIGNED: page.encode()})
    result = sg.handler({})
    assert result == {"result": "slot-count", "slots": slots}
    assert fake.puts == []
    assert fake.signed == [], "칸을 못 찾았는데 서명부터 했다 — 순서가 바뀌었다"


def test_tag_mentioned_in_inlined_code_is_not_a_slot(monkeypatch):
    """인라인된 JS 주석이 태그 문자열을 품어도 칸으로 세지 않는다(뒤에 JSON 객체가 바로 안 온다)."""
    page = PAGE.replace(
        "const x = 1",
        '// <script id="nettopo-live" type="application/json"> 을 읽는다\nconst x = 1',
    )
    fake = _install(monkeypatch, {UNSIGNED: page.encode()})
    assert sg.handler({})["result"] == "signed"
    assert len(fake.puts) == 1


def test_missing_unsigned_page_writes_nothing(monkeypatch):
    """원본이 없으면(첫 빌드 전 · 옛 방식에서 갓 올린 스택) **아무것도 쓰지 않는다** — 남아 있는 `index.html` 을
    원본으로 삼아 그 자리에서 고치지 않는다(그 키는 이 함수의 출력이다)."""
    old = PAGE.replace("10.0.0.0/16", "옛 판").encode()
    fake = _install(monkeypatch, {"index.html": old})
    assert sg.handler({}) == {"result": "no-unsigned"}
    assert fake.puts == []
    assert fake.signed == []
    assert fake.gets == [UNSIGNED]
    assert fake.objects["index.html"] == old


def test_publishes_from_the_unsigned_build_never_from_the_page(monkeypatch):
    """사람이 받는 `index.html` 에 서명 전 판이 한 순간도 없어야 한다(QA B 2차 2026-10-17).

    그러려면 빌드는 원본 키에만 올리고 이 함수가 **원본에서** 게시해야 한다. `index.html` 을 읽어 고치면 빌드가 다시
    `index.html` 을 덮어야 새 구조가 보인다 — 그 덮는 순간이 바로 「모니터링을 켤 수 없는 화면」이다. 원본이 바뀌면
    다음 실행이 **새 원본**을 게시하고, 원본이 그대로면 같은 원본에 새 서명만 붙는다.
    """
    old = PAGE.replace("10.0.0.0/16", "옛 판").encode()
    fake = _install(monkeypatch, {"index.html": old, UNSIGNED: PAGE.encode()})
    assert sg.handler({})["result"] == "signed"
    assert fake.gets == [UNSIGNED], "원본 말고 다른 키를 읽었다"
    assert [key for key, _, _ in fake.puts] == ["index.html"], (
        "index.html 말고 다른 키를 썼다"
    )
    first = fake.objects["index.html"].decode()
    assert sg.SLOT.sub("", first) == sg.SLOT.sub("", PAGE)
    assert _slot_payload(first)["state_url"], "게시한 판의 칸이 비어 있다"
    # 다음 빌드가 원본만 바꿨다 → 다음 실행이 새 구조를 게시한다.
    newer = PAGE.replace("10.0.0.0/16", "10.9.0.0/16")
    fake.objects[UNSIGNED] = newer.encode()
    fake.etag = '"etag-2"'
    assert sg.handler({})["result"] == "signed"
    second = fake.objects["index.html"].decode()
    assert sg.SLOT.sub("", second) == sg.SLOT.sub("", newer)
    assert fake.puts[-1][2]["Metadata"] == {"unsigned-etag": "etag-2"}
    assert fake.objects[UNSIGNED] == newer.encode(), "원본을 고쳤다"


def test_other_write_errors_are_not_swallowed(monkeypatch):
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})

    def denied(**_: Any) -> dict[str, Any]:
        raise botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}}, "PutObject"
        )

    fake.put_object = denied  # type: ignore[method-assign]
    with pytest.raises(botocore.exceptions.ClientError):
        sg.handler({})


def test_resigning_replaces_the_previous_signature(monkeypatch):
    """5분마다 같은 문서를 다시 서명한다 — 두 번째 서명이 **칸을 하나 더 만들면** 세 번째 주기부터
    `slot-count` 로 멈춘다(= 배포 15분 뒤 스위치가 죽는다)."""
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    sg.handler({})
    first = fake.objects["index.html"].decode()
    assert sg.handler({})["result"] == "signed"
    second = fake.objects["index.html"].decode()
    assert len(sg.SLOT.findall(second)) == 1
    assert sg.SLOT.sub("", second) == sg.SLOT.sub("", first)


def test_slot_json_cannot_close_the_script_element():
    text = sg.slot_json({"v": 1, "state_url": "https://x/</script><b>"})
    assert "<" not in text
    page = PAGE.replace(EMPTY_SLOT, EMPTY_SLOT)
    out = sg.rewrite(page, {"v": 1, "state_url": "https://x/</script>"})
    assert out is not None and len(sg.SLOT.findall(out)) == 1


def test_promised_expiry_never_exceeds_the_credential(monkeypatch):
    """화면이 「HH:MM 까지」라고 말한 시각보다 URL 이 먼저 죽으면 사람은 고장으로 읽는다.

    같은 키를 11시간 전에 처음 봤다면 남은 수명은 12시간 − 11시간 − 여유다(요청 수명 12시간이 아니다).
    """
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    now = datetime.now(timezone.utc)
    sg._FIRST_SEEN["test-access-key-a"] = now - timedelta(hours=11)
    sg.handler({})
    (
        (_, _, seconds_get),
        (_, _, seconds_put),
        (_, _, seconds_ctl_get),
        (_, _, seconds_index),
    ) = fake.signed
    ceiling = (sg.SESSION - timedelta(hours=11) - sg.SESSION_MARGIN).total_seconds()
    assert seconds_get == seconds_put == seconds_ctl_get == seconds_index
    assert seconds_get <= ceiling + 1
    payload = _slot_payload(fake.objects["index.html"].decode())
    expires = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    signed = datetime.fromisoformat(payload["signed_at"].replace("Z", "+00:00"))
    assert (expires - signed).total_seconds() == pytest.approx(seconds_get, abs=1)
    assert parse_qs(urlsplit(payload["state_url"]).query)["X-Amz-Expires"] == [
        str(seconds_get)
    ]


def test_new_credential_starts_a_new_clock(monkeypatch):
    """자격증명이 바뀌었는데 옛 키의 시각으로 재면 **짧게** 틀린다 — 새 키는 새로 센다."""
    now = datetime.now(timezone.utc)
    sg._FIRST_SEEN["test-access-key-old"] = now - timedelta(hours=11)
    deadline = sg.credential_deadline("test-access-key-new", now)
    assert deadline == now + sg.SESSION - sg.SESSION_MARGIN


def test_nearly_dead_credential_bakes_nothing(monkeypatch):
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    now = datetime.now(timezone.utc)
    sg._FIRST_SEEN["test-access-key-a"] = now - sg.SESSION
    result = sg.handler({})
    assert result["result"] == "credential-too-short"
    assert fake.puts == []


def test_signer_never_imports_the_engine():
    """서명 함수는 대상 계정에 들어가지 않는다 — 엔진을 임포트하면 그 경계가 흐려진다."""
    source = Path(sg.__file__).read_text()
    assert not re.search(r"^\s*(from|import)\s+nettopo", source, re.MULTILINE)
