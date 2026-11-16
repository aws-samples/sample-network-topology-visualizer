"""The signing tier, tested for whether "only the address slot changes" still
holds when things go wrong.

What these tests measure is not that signing works, but what the signer refuses
to write:

- With zero or two address slots, nothing is written at all; the slot is never
  guessed.
- The page people open (`index.html`) is never read as the source. Publishing
  always starts from the unsigned build at `unsigned/index.html`, which is what
  removed the 2 to 9 second window where the build had overwritten
  `index.html` with an empty slot.
- Not a single byte outside the slot changes.
- Only three URLs the page calls are signed, plus one navigation URL.
- The expiry the page promises never outlives the signing credentials.

S3 is never contacted. botocore computes signatures locally, so fake credentials
are enough to produce real URLs.
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
    """Stands in for S3. Signing is delegated to a real botocore client, which
    computes it locally; only reads and writes are intercepted."""

    class exceptions:  # noqa: N801 - mirrors the shape of a botocore client
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
    """If the tag in `flow.html` and `SLOT` drift apart, the switch on the
    deployed page is disabled permanently.

    The build still passes, since a missing slot does not fail rendering, and the
    signer just logs that it found zero slots and stops. All anyone sees on the
    page is one line saying live monitoring is unavailable.
    """
    html = FLOW_HTML.read_text()
    assert len(sg.SLOT.findall(html)) == 1
    assert _slot_payload(html)["state_url"] is None, (
        "the slot in the repository should be empty"
    )


def test_rewrites_only_the_slot(monkeypatch):
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    result = sg.handler({"source": "aws.events"})
    assert result["result"] == "signed"
    ((key, body, extra),) = fake.puts
    assert key == "index.html"
    assert extra["ServerSideEncryption"] == "AES256"
    # Which unsigned source this was published from; two `head-object` calls
    # after a deployment confirm they are the same build.
    assert extra["Metadata"] == {"unsigned-etag": "etag-1"}
    # The source is untouched, so the next 5-minute cycle republishes from it.
    assert fake.objects[UNSIGNED] == PAGE.encode()
    new = body.decode()
    # Not a single byte outside the slot changes.
    assert sg.SLOT.sub("", new) == sg.SLOT.sub("", PAGE)
    payload = _slot_payload(new)
    assert payload["v"] == 1
    assert payload["state_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/state-latest.json?"
    )
    assert payload["control_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/control/desired.json?"
    )
    # Following along: a read of `/control/desired.json` on the same bucket
    # origin. The page discards the slot if the origin or key differs.
    assert payload["control_get_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/control/desired.json?"
    )
    # "Open the latest page": `/index.html` on the same bucket origin. The page
    # hides the button if the origin or key differs.
    assert payload["index_url"].startswith(
        f"https://{BUCKET}.s3.{REGION}.amazonaws.com/index.html?"
    )


def test_signs_exactly_the_three_screen_paths_and_the_page(monkeypatch):
    """The page is allowed exactly three request paths (reading state, and
    reading and writing the control key) plus one click-only navigation URL
    (reading `index.html`). A fifth URL means the documented contract has to
    change first."""
    fake = _install(monkeypatch, {UNSIGNED: PAGE.encode()})
    sg.handler({})
    assert sorted((op, key) for op, key, _ in fake.signed) == [
        ("get_object", "control/desired.json"),
        ("get_object", "index.html"),
        ("get_object", "state-latest.json"),
        ("put_object", "control/desired.json"),
    ]
    # All four URLs share one lifetime. If the navigation URL expired first the
    # button would lead to a 403; if the read URL expired first, following along
    # would stop.
    assert len({seconds for _, _, seconds in fake.signed}) == 1


def test_signature_covers_host_only(monkeypatch):
    """Signing `content-type` would mean that a browser sending a header one
    character different gets a 403 indistinguishable from a permission failure.
    The only signed header must be `host`."""
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
    assert fake.signed == [], (
        "signing happened before the slot was found, so the order is wrong"
    )


def test_tag_mentioned_in_inlined_code_is_not_a_slot(monkeypatch):
    """Inlined JS mentioning the tag inside a comment is not counted as a slot,
    because no JSON object follows it directly."""
    page = PAGE.replace(
        "const x = 1",
        '// reads <script id="nettopo-live" type="application/json">\nconst x = 1',
    )
    fake = _install(monkeypatch, {UNSIGNED: page.encode()})
    assert sg.handler({})["result"] == "signed"
    assert len(fake.puts) == 1


def test_missing_unsigned_page_writes_nothing(monkeypatch):
    """With no unsigned source (before the first build, or on a stack just moved
    off the older scheme) nothing is written. A leftover `index.html` is never
    treated as the source and patched in place, because that key is this
    function's output."""
    old = PAGE.replace("10.0.0.0/16", "older revision").encode()
    fake = _install(monkeypatch, {"index.html": old})
    assert sg.handler({}) == {"result": "no-unsigned"}
    assert fake.puts == []
    assert fake.signed == []
    assert fake.gets == [UNSIGNED]
    assert fake.objects["index.html"] == old


def test_publishes_from_the_unsigned_build_never_from_the_page(monkeypatch):
    """The page people open must never hold an unsigned revision, not even
    briefly.

    That requires the build to upload only to the unsigned key and this function
    to publish from it. Were the signer to read and patch `index.html` instead,
    the build would have to overwrite `index.html` for a new structure to appear,
    and during that overwrite the page cannot turn monitoring on. When the source
    changes, the next run publishes the new one; when it does not, the same
    source is republished with fresh signatures.
    """
    old = PAGE.replace("10.0.0.0/16", "older revision").encode()
    fake = _install(monkeypatch, {"index.html": old, UNSIGNED: PAGE.encode()})
    assert sg.handler({})["result"] == "signed"
    assert fake.gets == [UNSIGNED], "a key other than the unsigned source was read"
    assert [key for key, _, _ in fake.puts] == ["index.html"], (
        "a key other than index.html was written"
    )
    first = fake.objects["index.html"].decode()
    assert sg.SLOT.sub("", first) == sg.SLOT.sub("", PAGE)
    assert _slot_payload(first)["state_url"], "the published revision has an empty slot"
    # The next build changed only the unsigned source, so the next run publishes
    # the new structure.
    newer = PAGE.replace("10.0.0.0/16", "10.9.0.0/16")
    fake.objects[UNSIGNED] = newer.encode()
    fake.etag = '"etag-2"'
    assert sg.handler({})["result"] == "signed"
    second = fake.objects["index.html"].decode()
    assert sg.SLOT.sub("", second) == sg.SLOT.sub("", newer)
    assert fake.puts[-1][2]["Metadata"] == {"unsigned-etag": "etag-2"}
    assert fake.objects[UNSIGNED] == newer.encode(), "the unsigned source was modified"


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
    """The same document is re-signed every 5 minutes. If the second signature
    added a second slot, the third cycle would stop with `slot-count`, so the
    switch would die 15 minutes after deployment."""
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
    """A URL that dies before the time the page promised reads as a malfunction.

    If this key was first seen 11 hours ago, the remaining lifetime is 12 hours
    minus 11 hours minus the margin, not the 12 hours requested.
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
    """Measuring rotated credentials from the old key's timestamp would
    understate the lifetime, so a new key restarts the clock."""
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
    """The signer never enters a target account, and importing the engine would
    blur that boundary."""
    source = Path(sg.__file__).read_text()
    assert not re.search(r"^\s*(from|import)\s+nettopo", source, re.MULTILINE)
