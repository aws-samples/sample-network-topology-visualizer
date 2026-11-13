"""Signing tier: fills the one live-address slot in the unsigned page the build
produced, then publishes the result as `index.html`.

Only two things invoke it: the 5-minute schedule rule, and the rule that fires
when the structure build (CodeBuild) succeeds.

1. Read the unsigned page (`unsigned/index.html`, written only by the build).
2. Check that there is exactly one address slot (`SLOT`). If not, write nothing.
3. Sign four URLs: read state (`GET state-latest.json`), write and read the
   control key (`PUT` and `GET control/desired.json`, the read being for pages
   following along in another tab), and open the latest page
   (`GET index.html`, a navigation URL used only when someone clicks "open the
   latest page", never fetched by the page itself).
4. Write the document back to `index.html` with only the slot's JSON replaced.

The key people actually open (`index.html`) never holds an unsigned revision,
not even briefly. The build used to overwrite `index.html` with an empty slot so
this function could patch it in place a few seconds later; across 64 measured
builds that gap was 2 to 9 seconds (median 6), and a page loaded during it could
not turn monitoring on. Now this function is the only writer of `index.html`
(IAM blocks the build, see `OnlyTheSignerPublishesThePage` in the template).
What the conditional write used to guarantee, never overwriting a new build with
older content, is now guaranteed by serial execution: concurrency is capped at
one (`ReservedConcurrentExecutions`) and every invocation re-reads the source, so
a later invocation always writes the same or a newer page. If this function
fails, `index.html` stays on the previous revision, which is better than an
unsigned one, and the next 5-minute cycle publishes again.

This function's permissions are the URLs' permissions: S3 evaluates a presigned
request with the authority of whoever signed it. That is why this function needs
write access to the control key, and why its role is kept separate from the
observation function's (`handler.py`). Merged, the observation function could
flip its own switch on, and the server-side "off by default" would be gone.

URL lifetime is min(the `ExpiresIn` requested, the signing credentials' remaining
lifetime). Lambda does not expose when its credentials expire, so the ceiling is
the first time this execution environment saw those credentials plus the session
length (measured at 720 minutes in CloudTrail), minus a margin. The page shows
`expires_at` so that a stall has a visible reason rather than looking broken.

There is no bundle check (`handler.verify_bundle`) here: this function never
enters a target account and does not use the read-only hooks in `awsguard.py`.
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
# The unsigned page uploaded by the structure build (post_build in
# `infra/stack-a-collector.yaml`). Only this function reads it; nobody opens it.
UNSIGNED_KEY = "unsigned/index.html"

# Must match the tag in `renderer/flow.html`, since `build-standalone.mjs`
# applies the same regex at build time to check there is exactly one. The body is
# narrowed to `\{[^<]*\}` so that inlined JS mentioning this tag inside a comment
# is not counted as a slot unless a JSON object and `</script>` follow directly.
SLOT = re.compile(
    r'(<script id="nettopo-live" type="application/json">)(\{[^<]*\})(</script>)'
)

# The lifetime we ask for. The real lifetime is the lower of this and the
# credential ceiling below.
URL_TTL = timedelta(hours=12)
# Measured in CloudTrail: the Lambda role's sessions all had
# `durationSeconds=43200`.
SESSION = timedelta(minutes=720)
# "First seen" is later than the moment the credentials were issued (issue, then
# environment start, then first invocation). This margin covers one 5-minute
# cycle plus a cold start. Understating the lifetime is better than overstating
# it: a page that dies before the time it promised reads as broken.
SESSION_MARGIN = timedelta(minutes=15)
# Below this we do not sign at all, since the URL would expire before the person
# who opened the page could press the switch.
MIN_TTL = timedelta(minutes=5)

# Access key id to the first time this execution environment saw it; module state
# on a warm environment survives until the next invocation. A changed key means
# rotated credentials, so timing restarts: measuring a new key's lifetime from the
# old key's timestamp would understate it.
_FIRST_SEEN: dict[str, datetime] = {}


def credential_deadline(access_key: str | None, now: datetime) -> datetime:
    """A conservative estimate of when the signing credentials expire. With no
    key (running locally) just use the requested lifetime."""
    if not access_key:
        return now + URL_TTL
    first = _FIRST_SEEN.setdefault(access_key, now)
    return first + SESSION - SESSION_MARGIN


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slot_json(payload: dict[str, Any]) -> str:
    """The JSON that goes into the slot. `<` becomes `\\u003c`: presigned URLs do
    not contain it, but a single one would close the document early at
    `</script>`, and from the next cycle on `SLOT` would no longer find the slot,
    leaving the switch permanently disabled."""
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def rewrite(html: str, payload: dict[str, Any]) -> str | None:
    """With exactly one slot, return the document with only that JSON replaced;
    otherwise return `None`."""
    if len(SLOT.findall(html)) != 1:
        return None
    return SLOT.sub(
        lambda m: m.group(1) + slot_json(payload) + m.group(3), html, count=1
    )


def _client(region: str) -> Any:
    # Sign against the regional endpoint with virtual-host addressing. In
    # `us-east-1` the default is the global endpoint (`s3.amazonaws.com`), and
    # reaching a bucket in another Region through it returns a 307 redirect.
    # Browsers do not follow redirects on CORS requests, so the page would fail
    # with no visible reason.
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
        # The structure build has never succeeded under this publishing scheme;
        # the build-success event will call this function again. We do not fall
        # back to reading `index.html` and patching it in place, because that key
        # is this function's output. A page left over from the older scheme keeps
        # working until its signatures expire, and the next build takes over.
        print(f"서명 건너뜀 — {UNSIGNED_KEY} 이 아직 없다 · 호출 {source}")
        return {"result": "no-unsigned"}
    content_type = got.get("ContentType") or "text/html"
    html = got["Body"].read().decode("utf-8")

    slots = len(SLOT.findall(html))
    if slots != 1:
        # Write nothing. Inventing a slot, or picking one of two, risks the page
        # code reading the other one. The hourly build regenerates the page from
        # scratch, so waiting is the recovery path.
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
    # `ContentType` is deliberately left out of the signature. Signing it would
    # require the browser to send exactly the same header, and a mismatch returns
    # a 403 that is indistinguishable from a permission failure. The only signed
    # header is `host`.
    control_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": CONTROL_KEY},
        ExpiresIn=seconds,
        HttpMethod="PUT",
    )
    # Reading the control key lets a second tab follow along instead of fighting
    # over the switch. It needs no new permission: this function already holds
    # GetObject on the control key (`SignControlConditionalWrite`, added because
    # S3 also evaluates `If-Match` PUTs against it), and it is the same bucket
    # origin, so the CSP `connect-src` is unchanged. A page served by an older
    # signer finds this field missing and keeps the old behaviour: when two tabs
    # collide, it switches itself off.
    control_get_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": CONTROL_KEY},
        ExpiresIn=seconds,
        HttpMethod="GET",
    )
    # A navigation URL: the page never fetches it, so it is outside the CSP
    # `connect-src`. When the structure changes under an open page, clicking
    # "open the latest page" navigates that tab here. Because the key is always
    # `index.html`, it opens whatever the newest published revision is. No new
    # permission either; this function can already read `index.html`.
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
    assert new_html is not None  # the single-slot check above guarantees this

    # Failures (permissions, throttling) are not swallowed: two retries, then the
    # DLQ. Meanwhile `index.html` stays on its previous revision. The metadata
    # records which unsigned source this was published from, so two
    # `head-object` calls after a deployment can confirm they are the same build.
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
