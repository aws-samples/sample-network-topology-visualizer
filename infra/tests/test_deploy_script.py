"""루트 `deploy.sh`(#82) — 안내서 1~3·5·6단계와 삭제를 대신 치는 스크립트를 잰다.

두 갈래로 잰다:

① **정적** — 스크립트 본문을 읽어 안내서 테스트(`test_install_guide_names_profile_and_region_…`)와 같은
   불변식을 잰다: 모든 `aws` 명령에 `--profile`·`--region` · 기본 프로필/리전 없음 · 계정 ID 리터럴 없음 ·
   `--destroy` 는 확인 입력 없이 지우지 않는다.
② **실행** — PATH 앞에 가짜 `aws` 를 두고 실제로 돌려, 각 작업이 어떤 `aws` 명령을 어떤 순서로 부르는지와
   `--profile`/`--region` 누락 0 을 호출 기록에서 잰다(이슈 #82 구현 메모). 실제 AWS 는 부르지 않는다 —
   🔴 실계정 배포는 이 검사가 대신하지 못한다(완료 기준 = 실계정 실배포 1회).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy.sh"
PROFILE = "hub-test"
REGION = "eu-central-1"
FAKE_ACCOUNT = "111122223333"  # 가짜 계정 ID(AWS 문서 예시 계열 · 실계정 ID 금지)


# ── ① 정적 ────────────────────────────────────────────────────────────────────────────────────


def _joined_lines(*, heredoc: bool = True) -> list[str]:
    """줄 이음(`\\`)을 합쳐 한 명령 한 줄로. 주석 줄은 뺀다. `heredoc=False` 면 here-doc 본문(출력 글)을 뺀다."""
    text = re.sub(r"\\\n\s*", " ", DEPLOY.read_text())
    lines, end = [], None
    for line in text.splitlines():
        if end is not None:
            if line.strip() == end:
                end = None
            elif heredoc:
                lines.append(line)
            continue
        m = re.search(r"<<-?\s*'?(\w+)'?\s*$", line)
        if m:
            end = m.group(1)
        if not line.lstrip().startswith("#"):
            lines.append(line)
    return lines


_AWS_COMMAND = re.compile(
    r"\baws (?:sts|s3|s3api|cloudformation|codebuild|logs|sso|lambda)\b"
)
# 스크립트가 **실행하는** aws — 명령 자리(줄 머리 · `$(` · `|` · `;` · `&&` · `||` · `then` · `do` · `if`) 에 있다.
_AWS_EXECUTED = re.compile(r"(?:^|\$\(|[|;&]|\bthen\b|\bdo\b|\bif\b|!)\s*aws\s")


def test_every_aws_command_names_profile_and_region() -> None:
    """실행하는 명령은 `--profile "$PROFILE" --region "$REGION"` 을, 출력으로 안내하는 명령도 둘 다를 적는다."""
    lines = [line for line in _joined_lines() if _AWS_COMMAND.search(line)]
    executed = [
        line
        for line in _joined_lines(heredoc=False)
        if _AWS_COMMAND.search(line) and _AWS_EXECUTED.search(line)
    ]
    # 🔴 파서가 명령을 못 찾으면 아래 반복이 0회라 조용히 통과한다 — 찾은 수부터 잰다.
    assert len(executed) >= 25, f"실행하는 aws 명령을 {len(executed)}개밖에 못 찾았다"
    assert len(lines) > len(executed), (
        "출력으로 안내하는 aws 명령(스택 B 등)을 못 찾았다"
    )
    for line in executed:
        assert '--profile "$PROFILE"' in line and '--region "$REGION"' in line, (
            f"프로필·리전이 빠진 aws 호출: {line.strip()[:140]}"
        )
    for line in lines:
        assert "--profile " in line and "--region " in line, (
            f"프로필·리전이 빠진 안내 명령: {line.strip()[:140]}"
        )
    # 링크 발급 도우미도 같은 두 값을 받는다(도우미 안의 aws 호출이 그 값을 쓴다).
    issuer = [line for line in _joined_lines() if '"$LINK_ISSUER"' in line]
    assert issuer and all(
        '--profile "$PROFILE"' in line and '--region "$REGION"' in line
        for line in issuer
    )


def test_no_default_profile_region_or_account_literal() -> None:
    text = DEPLOY.read_text()
    for name in ("PROFILE", "REGION"):
        values = set(re.findall(rf"(?<![A-Z_]){name}=([^\s;]*)", text))
        assert values <= {'""', "$2"}, f"{name} 에 기본값이 있다: {values}"
        assert f"${{{name}:-" not in text and f"${{{name}:=" not in text
    assert not re.search(r"\bAWS_(?:PROFILE|REGION|DEFAULT_REGION)\b", text), (
        "셸 환경의 기본 프로필·리전을 읽는다"
    )
    for line in text.splitlines():
        if re.search(r"\b[a-z]{2}(?:-gov)?-[a-z]+-\d\b", line):
            # 유일한 예외: us-east-1 버킷은 LocationConstraint 를 받지 않는다(그 판별 두 줄).
            assert "us-east-1" in line and (
                '"$REGION" = us-east-1' in line or "LocationConstraint" in line
            ), f"리전 리터럴: {line.strip()[:120]}"
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", text), "12자리 계정 ID 리터럴"


def test_destroy_has_no_way_around_the_confirmation() -> None:
    text = DEPLOY.read_text()
    body = text[text.index("destroy() {") :]
    body = body[: body.index("\n}\n")]
    prompt = body.index("read -r answer")
    first_delete = min(
        body.index("delete_stack "), body.index("empty_and_delete_bucket ")
    )
    assert prompt < first_delete, "확인 입력보다 삭제가 먼저 온다"
    assert '[ "$answer" = "$token" ] ||' in body
    assert not re.search(r"--(?:yes|force|no-confirm)\)", text), "확인을 건너뛰는 옵션"


def test_state_key_parsing_matches_the_builder() -> None:
    """deploy.sh 는 빌더 출력의 「S3 키」를 읽는다 — 빌더가 키 모양을 바꾸면 여기서 잡는다."""
    builder = (ROOT / "infra" / "build-state-lambda.sh").read_text()
    assert 'KEY="lambda/state-observer-${SHA:0:12}.zip"' in builder
    assert "lambda/state-observer-[0-9a-f]{12}\\.zip" in DEPLOY.read_text()


_BASH_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _failure_rows() -> set[str]:
    """`fail … "<행>"` 과 `cfn_deploy <단계> <무엇> "<행>"` 이 가리키는 문제 해결 표의 행 이름(변수 행 제외)."""
    rows = set()
    for line in _joined_lines(heredoc=False):
        if re.search(r"(?:^|[\s;|&{(])fail \"", line):
            row = _BASH_STRING.findall(line[line.index('fail "') :])[-1]
        elif re.search(r"(?:^|\s)cfn_deploy \"", line):
            row = _BASH_STRING.findall(line)[2]
        else:
            continue
        if "$" not in row:
            rows.add(row.replace('\\"', '"'))
    return rows


def _trouble_symptoms(guide: Path) -> list[str]:
    """안내서 「문제 해결」 표의 첫 칸(증상) — 역따옴표는 뺀다(사람 눈에는 보이지 않는 표기다)."""
    text = guide.read_text()
    m = re.search(r"\n## (?:문제 해결|Troubleshooting)\n(.*?)\n## ", text, flags=re.S)
    assert m, f"{guide.name} 에 문제 해결 절이 없다"
    return [
        line.split("|")[1].strip().replace("`", "")
        for line in m.group(1).splitlines()
        if line.startswith("| ")
        and not line.startswith("| 증상")
        and not line.startswith("| Symptom")
    ]


def test_every_failure_row_is_in_the_install_guide() -> None:
    """실패 한 줄이 「안내서 문제 해결 표 「<행>」 행」을 가리키면 그 행이 **그 이름 그대로** 있어야 한다.

    고객 배포 준비(2026-10-23) 실측: 스크립트가 가리키는 행 14개 중 안내서에 있던 것은 「pip 이 없다」 1개뿐이었다 —
    실패한 사람이 안내서를 열어도 찾을 행이 없다. 한글판은 같은 문자열(스크립트 출력이 한글이고 링크가 한글판이다),
    영어판은 그 문자열을 칸 안에 인용한다.
    """
    rows = _failure_rows()
    # 🔴 파서가 행을 못 찾으면 아래가 0회라 조용히 통과한다 — 찾은 수부터 잰다.
    assert len(rows) >= 14, (
        f"deploy.sh 에서 문제 해결 행을 {len(rows)}개밖에 못 찾았다: {sorted(rows)}"
    )
    anchor = re.search(
        r'GUIDE_TROUBLE="docs/install-guide\.md#([^"]+)"', DEPLOY.read_text()
    )
    assert anchor and anchor.group(1) == "문제-해결", (
        "deploy.sh 가 가리키는 안내서 절 앵커가 바뀌었다"
    )
    ko = set(_trouble_symptoms(ROOT / "docs" / "install-guide.md"))
    missing = sorted(rows - ko)
    assert not missing, f"안내서(한글) 문제 해결 표에 없는 행: {missing}"
    en = _trouble_symptoms(ROOT / "docs" / "install-guide.en.md")
    missing_en = sorted(r for r in rows if not any(cell.startswith(r) for cell in en))
    assert not missing_en, f"안내서(영어) 문제 해결 표에 없는 행: {missing_en}"


# ── ② 실행 (가짜 aws) ─────────────────────────────────────────────────────────────────────────

_FAKE_AWS = r"""
import json, os, sys
argv = sys.argv[1:]
log, state_path = os.environ["FAKE_AWS_LOG"], os.environ["FAKE_AWS_STATE"]
with open(log, "a") as f:
    f.write(json.dumps(argv) + "\n")
if argv == ["--version"]:
    print("aws-cli/2.15.0 Python/3.11.6 Darwin/23 source/arm64")
    sys.exit(0)
state = json.load(open(state_path))
def save():
    json.dump(state, open(state_path, "w"))
def opt(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default
def err(msg, code=254):
    sys.stderr.write(msg + "\n"); sys.exit(code)
acct = "111122223333"
svc, op = argv[0], argv[1]
stacks, buckets = state.setdefault("stacks", {}), state.setdefault("buckets", {})
if svc == "sts":
    if state.get("sts_fail"):
        err("aws: [ERROR]: The config profile (x) could not be found", 255)
    print(f"{acct}\tarn:aws:sts::{acct}:assumed-role/Admin/me")
elif svc == "cloudformation" and op == "describe-stacks":
    name, q = opt("--stack-name"), opt("--query", "")
    if name not in stacks:
        err(f"An error occurred (ValidationError) when calling the DescribeStacks operation: Stack with id {name} does not exist")
    st = stacks[name]
    if "StackStatus" in q:
        print(st["status"])
    elif "OutputKey==`ViewerDomain`" in q:  # issue-viewer-link.sh 의 두 출력 한 번에(deploy.sh 의 한 출력 조회와 구별)
        outs = st.get("outputs", {})
        for k in ("ViewerDomain", "ViewerKeyPairId"):
            if k in outs:
                print(f"{k}\t{outs[k]}")
    elif "Outputs" in q:
        key = q.split("OutputKey=='")[1].split("'")[0]
        print(st.get("outputs", {}).get(key, "None"))
    elif "Parameters" in q:
        key = q.split("ParameterKey=='")[1].split("'")[0]
        value = st.get("params", {}).get(key)
        if opt("--output") == "json":
            print(json.dumps(value))
        else:
            print("None" if value is None else value)
    elif "Tags" in q:
        for k, v in st.get("tags", {}).items():
            print(f"{k}\t{v}")
elif svc == "cloudformation" and op == "deploy":
    name = opt("--stack-name")
    if state.get("fail_deploy") == name:
        err("\nFailed to create/update the stack. Run the following command\nto fetch the list of events leading up to the failure\n", 255)
    params = {}
    if "--parameter-overrides" in argv:
        for a in argv[argv.index("--parameter-overrides") + 1:]:
            if a.startswith("--"):
                break
            k, v = a.split("=", 1); params[k] = v
    if "--no-execute-changeset" in argv:
        stacks.setdefault(name, {"status": "REVIEW_IN_PROGRESS"})
        save()
        print("Waiting for changeset to be created..\nChangeset created successfully. Run the following command to review changes:")
        print(f"aws cloudformation describe-change-set --change-set-name arn:aws:cloudformation:{os.environ.get('R', 'r')}:{acct}:changeSet/awscli-cloudformation-package-deploy-1/abc")
        sys.exit(0)
    st = stacks.setdefault(name, {"status": "REVIEW_IN_PROGRESS"})
    merged = {**st.get("params", {}), **params}
    if name == "nettopo-viewer":
        # 스택 C 키 자리 두 개의 모형(#141) — 템플릿 규칙 + CloudFront 의 「올린 공개키 값은 못 바꾼다」(실측).
        # 값은 바이트로 비교한다: 옛 자리의 PEM 이 한 글자라도 다르면 제자리 수정 → 거절 → 롤백(옛 값 유지).
        label, prev = merged.get("KeyLabel", "k1"), merged.get("PreviousPublicKeyPem", "")
        if state.get("viewer_template") == "one-key":  # 겹침을 모르는 템플릿(이 기능 전 판)이 서 있는 경우
            prev = ""
        if prev and prev == merged["ViewerPublicKeyPem"]:
            err("An error occurred (ValidationError) when calling the CreateChangeSet operation: PreviousPublicKeyPem must be the old key", 255)
        want = {label: merged["ViewerPublicKeyPem"]}
        if prev:
            want["k2" if label == "k1" else "k1"] = prev
        keys = {s: k for s, k in st.get("keys", {}).items() if s in want}
        for slot, pem in want.items():
            if slot in keys and keys[slot]["pem"] != pem:
                st["status"] = "UPDATE_ROLLBACK_COMPLETE"
                save()
                err("\nFailed to create/update the stack. Run the following command\nto fetch the list of events leading up to the failure\n", 255)
            if slot not in keys:
                state["key_seq"] = state.get("key_seq", 0) + 1
                keys[slot] = {"pem": pem, "id": f"K{slot[1]}EXAMPLE{state['key_seq']}"}
        st["keys"] = keys
        st["outputs"] = {"AccessLogBucketName": "log-bucket", "ViewerDomain": "d1234.cloudfront.net",
                         "ViewerKeyPairId": keys[label]["id"], "DistributionId": "E1EXAMPLE2345"}
        if prev:
            st["outputs"]["PreviousKeyPairId"] = keys["k2" if label == "k1" else "k1"]["id"]
        if state.get("viewer_no_distribution_id"):
            del st["outputs"]["DistributionId"]
    st["status"] = "UPDATE_COMPLETE" if st["status"] != "REVIEW_IN_PROGRESS" else "CREATE_COMPLETE"
    st["params"] = merged
    if name == "nettopo-collector":
        st["outputs"] = {"ArtifactBucketName": "art-bucket", "ProjectName": "nettopo-collector-collector"}
        if st["params"].get("StateLambdaSourceKey"):
            st["outputs"]["SignerFunctionName"] = "nettopo-collector-signer"
    save()
    print(f"Successfully created/updated stack - {name}")
elif svc == "cloudformation" and op == "describe-stack-events":
    print('BuildRole\tResource handler returned message: "Access Denied (Service: Iam)"')
elif svc == "cloudformation" and op == "describe-change-set":
    print("Add\tArtifactBucket\tAWS::S3::Bucket\tNone")
elif svc == "cloudformation" and op == "delete-stack":
    stacks.pop(opt("--stack-name"), None); save()
elif svc == "s3api" and op == "head-bucket":
    if opt("--bucket") not in buckets:
        err("An error occurred (404) when calling the HeadBucket operation: Not Found")
elif svc == "s3api" and op == "create-bucket":
    buckets[opt("--bucket")] = {"region": os.environ["R"], "lists": 0}; save()
elif svc == "s3api" and op == "get-public-access-block":
    print("True\tTrue\tTrue\tTrue")
elif svc == "s3api" and op == "get-bucket-location":
    print(buckets[opt("--bucket")]["region"])
elif svc == "s3api" and op == "put-object":
    print('"etag"')
elif svc == "s3api" and op == "head-object":
    print("2026-10-21T05:00:00+00:00")
elif svc == "s3api" and op == "list-object-versions":
    b = buckets.setdefault(opt("--bucket"), {"lists": 0})
    b["lists"] = b.get("lists", 0) + 1; save()
    objs = [{"Key": "index.html", "VersionId": "v1"}] if b["lists"] == 1 else []
    print(json.dumps({"Objects": objs, "Quiet": True}))
elif svc == "s3api" and op == "delete-bucket":
    buckets.pop(opt("--bucket"), None); save()
elif svc == "s3" and op == "cp":
    open(argv[3], "w").write("<html>diagram</html>")
elif svc == "codebuild" and op == "start-build":
    print("nettopo-collector-collector:0b1c")
elif svc == "codebuild" and op == "batch-get-builds":
    q = opt("--query", "")
    if "buildStatus" in q:
        print(state.get("build_status", "SUCCEEDED"))
    else:
        print("BUILD\tError while executing command: python -m nettopo collect. Reason: exit status 1")
"""

_FAKE_BUILDER = """#!/usr/bin/env bash
set -eu
mkdir -p "$OUT"
printf 'zip' > "$OUT/state-observer-0123456789ab.zip"
echo "S3 키   : lambda/state-observer-0123456789ab.zip   ← 스택 파라미터 StateLambdaSourceKey"
"""


class Fake:
    def __init__(self, tmp: Path, state: dict | None = None) -> None:
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        self.out = tmp / "out"
        self.log = tmp / "aws.log"
        self.state = tmp / "state.json"
        self.state.write_text(json.dumps(state or {}))
        fake = self.bin / "aws"
        fake.write_text(f"#!{sys.executable}\n{_FAKE_AWS}")
        fake.chmod(0o755)
        builder = tmp / "builder.sh"
        builder.write_text(_FAKE_BUILDER)
        builder.chmod(0o755)
        self.builder = builder
        self.package = tmp / "pkg.zip"
        self.package.write_bytes(b"PK\x03\x04fake")

    def run(
        self,
        *args: str,
        stdin: str = "",
        region: str = REGION,
        locale: str = "C",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        env = {
            "LC_ALL": locale,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "OUT": str(self.out),
            "POLL_SECONDS": "0",
            "STATE_LAMBDA_BUILDER": str(self.builder),
            "FAKE_AWS_LOG": str(self.log),
            "FAKE_AWS_STATE": str(self.state),
            "R": region,
            **(extra_env or {}),
        }
        return subprocess.run(
            ["bash", str(DEPLOY), *args],
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def ops(self) -> list[str]:
        return [" ".join(c[:2]) for c in self.calls() if c != ["--version"]]


def _base(region: str = REGION) -> list[str]:
    return ["--profile", PROFILE, "--region", region]


def _assert_profile_region_on_every_call(fake: Fake, region: str = REGION) -> None:
    calls = [c for c in fake.calls() if c != ["--version"]]
    assert calls, "aws 를 한 번도 부르지 않았다"
    for c in calls:
        assert "--profile" in c and c[c.index("--profile") + 1] == PROFILE, c
        assert "--region" in c and c[c.index("--region") + 1] == region, c


def _assert_no_full_account_id(fake: Fake, proc: subprocess.CompletedProcess) -> None:
    assert FAKE_ACCOUNT not in proc.stdout + proc.stderr, "화면에 계정 ID 전체가 찍혔다"
    for path in fake.out.rglob("*") if fake.out.exists() else []:
        if path.is_file():
            assert FAKE_ACCOUNT.encode() not in path.read_bytes(), (
                f"파일에 계정 ID: {path}"
            )


_MUTATING = re.compile(
    r"^(s3api (create|put|delete)-|s3 (cp|rb)|cloudformation (delete|create|update|execute)-|codebuild start-build)"
)


def test_help_lists_every_operation() -> None:
    proc = subprocess.run(
        ["bash", str(DEPLOY), "--help"], capture_output=True, text=True
    )
    assert proc.returncode == 0
    for word in (
        "--monitoring", "--viewer", "--days", "--collect", "--destroy", "--profile",
        "--region", "--bucket", "--regions", "--accounts", "--tags", "--dry-run", "--package",
        "--log-readers", "--rotate-key", "--retire-old-key", "--no-overlap", "--download",
        "--external-id", "--org-id", "--narrow-viewer",
    ):  # fmt: skip
        assert word in proc.stdout, word


_VIEWER_7_DAYS = [*_base(), "--viewer", "--days", "7"]
_ROTATE_ARGS = [*_VIEWER_7_DAYS, "--rotate-key"]


@pytest.mark.parametrize(
    "args, message",
    [
        ([], "--profile 이 없습니다"),
        (["--profile", PROFILE], "--region 이 없습니다"),
        (["--region", REGION], "--profile 이 없습니다"),
        ([*_base(), "--viewer"], "--days"),
        ([*_base(), "--collect", "--monitoring"], "--collect"),
        (
            [*_base(), "--log-readers", f"arn:aws:iam::{FAKE_ACCOUNT}:role/LogReader"],
            "--viewer",
        ),
        # 와일드카드 · 세션 ARN 은 스택 C 의 AllowedPattern 이 거절한다 — 그 전에, aws 를 부르기 전에 멈춘다.
        (
            [*_VIEWER_7_DAYS, "--log-readers", f"arn:aws:iam::{FAKE_ACCOUNT}:role/*"],
            "--log-readers",
        ),
        (
            [
                *_VIEWER_7_DAYS,
                "--log-readers",
                f"arn:aws:sts::{FAKE_ACCOUNT}:assumed-role/LogReader/me",
            ],
            "--log-readers",
        ),
        # 키 교체(#141) — 조합이 틀리면 aws 를 부르기 전에 멈춘다.
        ([*_base(), "--rotate-key", "--days", "7"], "--viewer"),
        ([*_VIEWER_7_DAYS, "--no-overlap"], "--rotate-key"),
        ([*_base(), "--viewer", "--rotate-key"], "--days"),
        ([*_VIEWER_7_DAYS, "--retire-old-key"], "--retire-old-key"),
        ([*_VIEWER_7_DAYS, "--rotate-key", "--retire-old-key"], "함께 쓸 수 없습니다"),
        ([*_VIEWER_7_DAYS, "--rotate-key", "--bucket", "pkg-bucket"], "스택 C 만"),
        # 선택 보강 — 템플릿 AllowedPattern 과 같은 모양이 아니면 aws 를 부르기 전에 멈춘다.
        ([*_base(), "--external-id", "x"], "--external-id 는"),  # 2자 미만
        ([*_base(), "--external-id", "x" * 1225], "--external-id 는"),  # 1224자 초과
        ([*_base(), "--external-id", "has space"], "--external-id 는"),
        ([*_base(), "--external-id", "quote'd"], "--external-id 는"),
        (
            [*_base(), "--external-id", f"ext-{FAKE_ACCOUNT}"],
            "12자리 숫자",
        ),  # 화면에서 가려져 명령이 틀어진다
        ([*_base(), "--org-id", "o-short"], "--org-id 는"),
        ([*_base(), "--org-id", "o-ABCDEFGHIJ"], "--org-id 는"),
        ([*_base(), "--org-id", "r-abcdefghij"], "--org-id 는"),
        ([*_base(), "--org-id", f"o-{FAKE_ACCOUNT}"], "12자리 숫자"),
        ([*_base(), "--narrow-viewer"], "--narrow-viewer 는 --viewer 와 함께"),
        ([*_base(), "--collect", "--external-id", "ext-id-1"], "--collect 에는"),
        ([*_base(), "--download", "--org-id", "o-abcdefghij"], "--download 에는"),
        ([*_ROTATE_ARGS, "--external-id", "ext-id-1"], "스택 C 만"),
        ([*_ROTATE_ARGS, "--narrow-viewer"], "스택 C 만"),
    ],
)
def test_missing_required_values_stop_before_any_aws_call(
    tmp_path: Path, args: list[str], message: str
) -> None:
    fake = Fake(tmp_path)
    proc = fake.run(*args)
    assert proc.returncode == 2 and message in proc.stderr, proc.stderr
    assert fake.calls() == []


def test_bad_credentials_fail_fast_in_one_line(tmp_path: Path) -> None:
    fake = Fake(tmp_path, {"sts_fail": True})
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION, "--dry-run"
    )
    assert proc.returncode == 1
    line = next(x for x in proc.stderr.splitlines() if "실패:" in x)
    assert "0단계 자격 증명 확인 실패" in line and "could not be found" in line
    assert "문제-해결" in proc.stderr
    assert fake.ops() == ["sts get-caller-identity"]


@pytest.mark.skipif(shutil.which("aws") is None, reason="실제 AWS CLI 없음")
def test_real_cli_with_a_missing_profile_fails_fast(tmp_path: Path) -> None:
    """실제 AWS CLI · 없는 프로필 → 0단계에서 바로 멈춘다(네트워크 호출 전 · 빈 설정 파일)."""
    empty = tmp_path / "config"
    empty.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "AWS_CONFIG_FILE": str(empty),
        "AWS_SHARED_CREDENTIALS_FILE": str(empty),
        "OUT": str(tmp_path / "out"),
    }
    proc = subprocess.run(
        ["bash", str(DEPLOY), "--profile", "nettopo-missing", "--region", REGION,
         "--bucket", "pkg-bucket", "--regions", REGION, "--dry-run"],
        env=env, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert proc.returncode == 1
    assert "0단계 자격 증명 확인 실패" in proc.stderr
    assert "nettopo-missing" in proc.stderr


def test_dry_run_first_install_changes_nothing(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", f"{REGION},us-west-2",
        "--package", str(fake.package), "--dry-run",
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    mutating = [op for op in fake.ops() if _MUTATING.match(op)]
    assert mutating == [], f"--dry-run 이 바꾸는 호출을 했다: {mutating}"
    deploys = [c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"]]
    assert len(deploys) == 1 and "--no-execute-changeset" in deploys[0]
    assert "AWS::S3::Bucket" in proc.stdout  # 변경 세트 내용을 보여 준다
    assert (fake.out / "templates" / "stack-b-member-role.yaml").is_file()
    assert (fake.out / "templates" / "SHA256SUMS").is_file()
    _assert_profile_region_on_every_call(fake)
    _assert_no_full_account_id(fake, proc)


@pytest.mark.parametrize("region", ["us-east-1", REGION])
def test_install_runs_steps_one_to_three_in_order(tmp_path: Path, region: str) -> None:
    fake = Fake(tmp_path)
    proc = fake.run(
        *_base(region), "--bucket", "pkg-bucket", "--regions", region,
        "--package", str(fake.package), region=region,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    ops = fake.ops()
    order = [
        "s3api create-bucket", "s3api put-public-access-block", "s3api put-bucket-encryption",
        "s3api put-bucket-versioning", "s3api put-object", "cloudformation deploy",
        "codebuild start-build", "codebuild batch-get-builds", "s3 cp",
    ]  # fmt: skip
    positions = [ops.index(op) for op in order]
    assert positions == sorted(positions), ops
    create = next(c for c in fake.calls() if c[:2] == ["s3api", "create-bucket"])
    if region == "us-east-1":
        assert "--create-bucket-configuration" not in create
    else:
        assert f"LocationConstraint={region}" in create
    deploy = next(c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"])
    assert (
        "--s3-bucket" in deploy
        and deploy[deploy.index("--s3-bucket") + 1] == "pkg-bucket"
    )
    assert "SourceLocation=pkg-bucket/nettopo-source.zip" in deploy
    assert f"TargetRegions={region}" in deploy
    put = next(c for c in fake.calls() if c[:2] == ["s3api", "put-object"])
    assert put[put.index("--server-side-encryption") + 1] == "AES256"
    assert (fake.out / "index.html").read_text().startswith("<html>")
    # 소스 계정용 명령이 값이 채워진 채로 나온다(계정 ID 는 끝 4자리만).
    assert "--stack-name nettopo-member-role" in proc.stdout
    assert f"AllowedRegions={region}" in proc.stdout and "…3333" in proc.stdout
    assert "create-stack-set" in proc.stdout
    _assert_profile_region_on_every_call(fake, region)
    _assert_no_full_account_id(fake, proc)


def test_printed_stackset_commands_tell_delegated_admins_about_call_as(
    tmp_path: Path,
) -> None:
    """출력하는 StackSet 명령은 관리 계정 기준이다 — 위임 관리자 계정에서 그대로 치면 거절된다.

    고객 배포 준비(2026-10-23): 안내서 4-C 는 `--call-as DELEGATED_ADMIN` 을 적는데 스크립트 출력에는 없었다 —
    출력만 보고 복사한 조직 관리자는 그 이유를 모른 채 막힌다. 명령 바로 위에 그 한 줄이 있어야 한다.
    """
    fake = Fake(tmp_path)
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION, "--package", str(fake.package)
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    at = next(i for i, line in enumerate(lines) if "create-stack-set" in line)
    hint = [
        line
        for line in lines[max(0, at - 3) : at]
        if "--call-as DELEGATED_ADMIN" in line
    ]
    assert hint, "StackSet 명령 위에 위임 관리자 안내(--call-as DELEGATED_ADMIN)가 없다"


def test_rerun_keeps_bucket_and_repasses_existing_tags(tmp_path: Path) -> None:
    fake = Fake(
        tmp_path,
        {
            "buckets": {"pkg-bucket": {"region": REGION}},
            "stacks": {
                "nettopo-collector": {
                    "status": "UPDATE_COMPLETE",
                    "params": {"SourceLocation": "pkg-bucket/nettopo-source.zip", "TargetRegions": REGION},
                    "tags": {"Owner": "net team"},
                    "outputs": {"ArtifactBucketName": "art-bucket", "ProjectName": "p"},
                }
            },
        },
    )  # fmt: skip
    proc = fake.run(*_base(), "--package", str(fake.package))
    assert proc.returncode == 0, proc.stderr
    assert not any(op.startswith("s3api create-bucket") for op in fake.ops())
    deploy = next(c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"])
    assert deploy[deploy.index("--tags") + 1] == "Owner=net team"
    assert not any(a.startswith("TargetRegions=") for a in deploy)  # 이전 값 유지
    _assert_profile_region_on_every_call(fake)


def test_monitoring_uploads_the_state_package_and_sets_its_key(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), "--monitoring",
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    keys = [
        c[c.index("--key") + 1]
        for c in fake.calls()
        if c[:2] == ["s3api", "put-object"]
    ]
    assert keys == ["nettopo-source.zip", "lambda/state-observer-0123456789ab.zip"]
    deploy = next(c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"])
    assert "StateLambdaSourceKey=lambda/state-observer-0123456789ab.zip" in deploy
    # 서명 함수가 있는 설치는 게시를 확인한 뒤 내려받는다.
    assert "s3api head-object" in fake.ops()
    _assert_profile_region_on_every_call(fake)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 없음")
def test_viewer_creates_a_key_deploys_stack_c_and_prints_a_link(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), "--viewer", "--days", "7",
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    stacks = [
        c[c.index("--stack-name") + 1]
        for c in fake.calls()
        if c[:2] == ["cloudformation", "deploy"]
    ]
    assert stacks == ["nettopo-collector", "nettopo-viewer"]
    key = fake.out / "viewer-k1.pem"
    assert key.is_file() and (key.stat().st_mode & 0o077) == 0, (
        "비밀키가 남에게 열려 있다"
    )
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout
    _assert_profile_region_on_every_call(fake)
    # --log-readers 를 안 주면 넘기지 않는다(템플릿 기본 = 빈 값 = 계정 루트만 · 갱신이면 이전 값 유지).
    assert "LogReaderPrincipalArns" not in _viewer_parameters(fake)[-1]


def _viewer_parameters(fake: Fake) -> list[dict[str, str]]:
    """스택 C deploy 호출마다 --parameter-overrides 를 dict 로."""
    out = []
    for c in fake.calls():
        if c[:2] != ["cloudformation", "deploy"] or "nettopo-viewer" not in c:
            continue
        params, i = {}, c.index("--parameter-overrides") + 1
        while i < len(c) and not c[i].startswith("--"):
            k, v = c[i].split("=", 1)
            params[k] = v
            i += 1
        out.append(params)
    return out


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 없음")
def test_viewer_passes_log_readers_only_when_given(tmp_path: Path) -> None:
    """#81 — 스택 C 의 LogReaderPrincipalArns. 준 값은 그대로, 다음 실행에서 생략하면 넘기지 않아 이전 값이 남는다."""
    readers = (
        f"arn:aws:iam::{FAKE_ACCOUNT}:role/aws-reserved/sso.amazonaws.com/{REGION}/AWSReservedSSO_Audit_0123abcd,"
        f"arn:aws:iam::{FAKE_ACCOUNT}:user/log-auditor"
    )
    fake = Fake(tmp_path)
    first = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), "--viewer", "--days", "7", "--log-readers", readers,
    )  # fmt: skip
    assert first.returncode == 0, first.stderr
    again = fake.run(
        *_base(), "--package", str(fake.package), "--viewer", "--days", "7",
    )  # fmt: skip
    assert again.returncode == 0, again.stderr
    calls = _viewer_parameters(fake)
    assert len(calls) == 2
    assert calls[0]["LogReaderPrincipalArns"] == readers
    assert "LogReaderPrincipalArns" not in calls[1], (
        "생략했는데 덮어썼다 — 적어 둔 읽는 사람이 지워진다"
    )
    state = json.loads(fake.state.read_text())
    assert (
        state["stacks"]["nettopo-viewer"]["params"]["LogReaderPrincipalArns"] == readers
    )
    _assert_profile_region_on_every_call(fake)
    for proc in (first, again):
        _assert_no_full_account_id(fake, proc)


def test_stack_failure_names_the_step_and_the_reason_in_one_line(
    tmp_path: Path,
) -> None:
    fake = Fake(tmp_path, {"fail_deploy": "nettopo-collector"})
    proc = fake.run(
        *_base(),
        "--bucket",
        "pkg-bucket",
        "--regions",
        REGION,
        "--package",
        str(fake.package),
    )
    assert proc.returncode == 1
    line = next(x for x in proc.stderr.splitlines() if "실패:" in x)
    assert line.startswith("🔴 2단계 스택 A 배포 실패:") and "Access Denied" in line
    assert "2단계 스택 A 배포 실패」 행" in proc.stderr
    assert "codebuild start-build" not in fake.ops()


def test_build_failure_points_to_the_build_row(tmp_path: Path) -> None:
    fake = Fake(tmp_path, {"build_status": "FAILED"})
    proc = fake.run(
        *_base(),
        "--bucket",
        "pkg-bucket",
        "--regions",
        REGION,
        "--package",
        str(fake.package),
    )
    assert proc.returncode == 1
    assert "3단계 수집 빌드(FAILED) 실패: BUILD" in proc.stderr
    assert "s3 cp" not in fake.ops()


_INSTALLED = {
    "buckets": {"pkg-bucket": {"region": REGION}},
    "stacks": {
        "nettopo-collector": {
            "status": "CREATE_COMPLETE",
            "params": {"SourceLocation": "pkg-bucket/nettopo-source.zip"},
            "outputs": {"ArtifactBucketName": "art-bucket", "ProjectName": "p"},
        },
        "nettopo-viewer": {
            "status": "CREATE_COMPLETE",
            "outputs": {"AccessLogBucketName": "log-bucket"},
        },
    },
}


@pytest.mark.parametrize("answer", ["", "yes", "nettopo-collector"])
def test_destroy_deletes_nothing_without_the_exact_confirmation(
    tmp_path: Path, answer: str
) -> None:
    fake = Fake(tmp_path, _INSTALLED)
    proc = fake.run(*_base(), "--destroy", stdin=answer + "\n")
    assert proc.returncode == 1 and "아무것도 지우지 않았습니다" in proc.stderr
    assert [op for op in fake.ops() if _MUTATING.match(op)] == []


def test_destroy_dry_run_lists_but_never_prompts_or_deletes(tmp_path: Path) -> None:
    fake = Fake(tmp_path, _INSTALLED)
    proc = fake.run(*_base(), "--destroy", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "log-bucket" in proc.stdout and "art-bucket" in proc.stdout
    assert [op for op in fake.ops() if _MUTATING.match(op)] == []


def test_destroy_removes_c_then_a_then_every_bucket_version(tmp_path: Path) -> None:
    fake = Fake(tmp_path, _INSTALLED)
    proc = fake.run(*_base(), "--destroy", stdin="pkg-bucket\n")
    assert proc.returncode == 0, proc.stderr
    deletes = [
        (" ".join(c[:2]), c[c.index("--stack-name") + 1] if "--stack-name" in c else c[c.index("--bucket") + 1])
        for c in fake.calls()
        if len(c) > 1 and c[1] in ("delete-stack", "delete-bucket", "delete-objects")
    ]  # fmt: skip
    assert deletes == [
        ("cloudformation delete-stack", "nettopo-viewer"),
        ("s3api delete-objects", "log-bucket"),
        ("s3api delete-bucket", "log-bucket"),
        ("cloudformation delete-stack", "nettopo-collector"),
        ("s3api delete-objects", "art-bucket"),
        ("s3api delete-bucket", "art-bucket"),
        ("s3api delete-objects", "pkg-bucket"),
        ("s3api delete-bucket", "pkg-bucket"),
    ]
    _assert_profile_region_on_every_call(fake)


def test_no_variable_is_glued_to_a_non_ascii_letter() -> None:
    """`$DAYS일` 은 macOS bash 3.2 + UTF-8 로캘에서 `DAYS<첫 바이트>` 라는 변수로 읽혀 `set -u` 에 걸린다.

    실측 2026-10-22(`--viewer` 가 링크 대신 `DAYS\ufffd: unbound variable` 로 끝났다). 변수 바로 뒤에 한글이
    오면 `${NAME}` 으로 감싸야 한다.
    """
    src = DEPLOY.read_text(encoding="utf-8")
    bad = [
        (n, line.strip())
        for n, line in enumerate(src.splitlines(), 1)
        if not line.lstrip().startswith("#")
        and re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", line)
    ]
    assert bad == [], f"변수 뒤에 바로 비 ASCII 글자가 붙었다(중괄호로 감싸라): {bad}"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 없음")
def test_viewer_prints_the_link_under_a_utf8_locale(tmp_path: Path) -> None:
    """한국어 로캘의 Mac 에서 `--viewer` 가 링크까지 출력하고 성공으로 끝난다(위 결함의 실행 측)."""
    fake = Fake(tmp_path)
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), "--viewer", "--days", "1",
        locale="ko_KR.UTF-8",
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "보는 링크 (1일" in proc.stdout
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 없음")
def test_a_new_stack_c_inherits_stack_a_tags(tmp_path: Path) -> None:
    """두 번째 실행에서 --viewer 를 얹으면(--tags 생략) 새 스택 C 가 스택 A 의 태그를 받는다(실측: Tags=[])."""
    fake = Fake(
        tmp_path,
        {
            "buckets": {"pkg-bucket": {"region": REGION}},
            "stacks": {
                "nettopo-collector": {
                    "status": "UPDATE_COMPLETE",
                    "params": {"SourceLocation": "pkg-bucket/nettopo-source.zip", "TargetRegions": REGION},
                    "tags": {"Owner": "net team"},
                    "outputs": {"ArtifactBucketName": "art-bucket", "ProjectName": "p"},
                }
            },
        },
    )  # fmt: skip
    proc = fake.run(*_base(), "--package", str(fake.package), "--viewer", "--days", "1")
    assert proc.returncode == 0, proc.stderr
    viewer = next(
        c
        for c in fake.calls()
        if c[:2] == ["cloudformation", "deploy"] and "nettopo-viewer" in c
    )
    assert "--tags" in viewer and viewer[viewer.index("--tags") + 1] == "Owner=net team"


# ── 스택 C 서명 키 교체(#141) — 겹침 · 옛 키 빼기 · 겹침 없이(유출) ─────────────────────────────────

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl 없음"
)
_ROTATE = [*_VIEWER_7_DAYS, "--rotate-key"]
_NO_OVERLAP = [*_ROTATE, "--no-overlap"]
_RETIRE = [*_base(), "--viewer", "--retire-old-key"]
_CONFIRM = "nettopo-viewer\n"


def _openssl_key(path: Path) -> str:
    subprocess.run(["openssl", "genrsa", "-out", path, "2048"], check=True, capture_output=True)  # fmt: skip
    path.chmod(0o600)
    return _pub(path)


def _pub(path: Path) -> str:
    """deploy.sh 가 넘기는 모양 그대로 — `$(openssl rsa -pubout)` 는 끝 줄바꿈이 없다."""
    return subprocess.run(
        ["openssl", "rsa", "-in", path, "-pubout"],
        check=True, capture_output=True, text=True,
    ).stdout.rstrip("\n")  # fmt: skip


def _viewer_state(out: Path, *, overlap: bool = False, trailing_newline: bool = False):
    """설치된 스택 C(가짜 aws 의 상태) + out/ 의 비밀키. overlap = k2 자리 새 키 · k1 자리 옛 키(겹침 중)."""
    out.mkdir(parents=True, exist_ok=True)
    a = _openssl_key(out / "viewer-k1.pem") + ("\n" if trailing_newline else "")
    params = {
        "ArtifactBucketName": "art-bucket",
        "KeyLabel": "k1",
        "ViewerPublicKeyPem": a,
        # 실제 CloudFormation 은 기본값으로 둔 파라미터를 null 이 아니라 빈 문자열로 돌려준다(실측 2026-10-23).
        #   null 로 두면 macOS bash 3.2 의 `printf -v X '%b' ""` 가 X 를 비우지 않는 결함이 숨는다.
        "PreviousPublicKeyPem": "",
    }
    keys = {"k1": {"pem": a, "id": "K1EXAMPLE0"}}
    outputs = {"AccessLogBucketName": "log-bucket", "ViewerDomain": "d1234.cloudfront.net",
               "ViewerKeyPairId": "K1EXAMPLE0"}  # fmt: skip
    pems = {"A": a}
    if overlap:
        b = _openssl_key(out / "viewer-k2.pem")
        params.update(KeyLabel="k2", ViewerPublicKeyPem=b, PreviousPublicKeyPem=a)
        keys["k2"] = {"pem": b, "id": "K2EXAMPLE0"}
        outputs.update(ViewerKeyPairId="K2EXAMPLE0", PreviousKeyPairId="K1EXAMPLE0")
        pems["B"] = b
    stack = {
        "status": "UPDATE_COMPLETE",
        "params": params,
        "keys": keys,
        "outputs": outputs,
    }
    return {"stacks": {"nettopo-viewer": stack}}, pems


def _ledger(fake: Fake) -> Path:
    """발급 기록 — issue-viewer-link.sh 가 쓰고 deploy.sh 가 읽는 자리(가짜 HOME 아래 · XDG_STATE_HOME 없음)."""
    path = fake.tmp / ".local" / "state" / "nettopo" / "viewer-links.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _viewer_stack(fake: Fake) -> dict:
    return json.loads(fake.state.read_text())["stacks"]["nettopo-viewer"]


def _link(proc: subprocess.CompletedProcess) -> str:
    (link,) = [
        line for line in proc.stdout.splitlines()
        if line.startswith("https://d1234.cloudfront.net/index.html?")
    ]  # fmt: skip
    return link


def _link_signed_by(link: str, key: Path, tmp: Path) -> str:
    """링크의 서명을 그 비밀키의 공개키로 검증하고 Key-Pair-Id 를 돌려준다(검증 실패면 빈 값)."""
    import base64
    from urllib.parse import parse_qs, urlsplit

    q = {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}
    policy = (
        '{"Statement":[{"Resource":"https://d1234.cloudfront.net/index.html",'
        f'"Condition":{{"DateLessThan":{{"AWS:EpochTime":{q["Expires"]}}}}}}}]}}'
    )
    (tmp / "policy").write_text(policy)
    (tmp / "sig").write_bytes(
        base64.b64decode(q["Signature"].translate(str.maketrans("-_~", "+=/")))
    )
    (tmp / "pub.pem").write_text(_pub(key) + "\n")
    ok = subprocess.run(
        ["openssl", "dgst", "-sha256", "-verify", tmp / "pub.pem", "-signature", tmp / "sig", tmp / "policy"],
        capture_output=True,
    ).returncode == 0  # fmt: skip
    return q["Key-Pair-Id"] if ok else ""


def _utc(epoch: int) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(epoch))


def _only_stack_c(fake: Fake) -> None:
    """키 교체는 스택 C 만 — 스택 A·수집·버킷을 건드리지 않는다(유출 대응이 수집을 기다리지 않게)."""
    names = {
        c[c.index("--stack-name") + 1] for c in fake.calls() if "--stack-name" in c
    }
    assert names == {"nettopo-viewer"}, names
    assert not [op for op in fake.ops() if op.startswith(("codebuild", "s3"))], (
        fake.ops()
    )


@needs_openssl
@pytest.mark.parametrize("trailing_newline", [False, True], ids=["pem", "pem-trailing-newline"])  # fmt: skip
def test_rotate_key_keeps_the_old_key_and_signs_new_links_with_the_new_one(
    tmp_path: Path, trailing_newline: bool
) -> None:
    """겹침 교체 — 새 키를 빈 자리(k2)에, 옛 키는 그 자리(k1)에 **같은 값으로** 남긴다 → 옛 링크는 만료까지 산다.

    옛 자리의 PEM 이 저장값과 한 바이트라도 다르면(끝 줄바꿈 포함) CloudFormation 이 그 자리를 제자리 수정하려다
    거절당한다 — 그래서 저장값을 바이트 그대로 읽어 넘기는지를 끝 줄바꿈이 있는 저장값으로도 잰다.
    """
    import time

    state, pems = _viewer_state(tmp_path / "out", trailing_newline=trailing_newline)
    fake = Fake(tmp_path, state)
    far = int(time.time()) + 30 * 86400
    _ledger(fake).write_text(
        f"{far - 100}\tK1EXAMPLE0\t1\n{far}\tK1EXAMPLE0\t2\n{far + 999}\tKOTHER\t3\n"
    )
    old_key = (fake.out / "viewer-k1.pem").read_bytes()
    proc = fake.run(*_ROTATE)
    assert proc.returncode == 0, proc.stderr
    (params,) = _viewer_parameters(fake)
    new_key = fake.out / "viewer-k2.pem"
    assert new_key.is_file() and (new_key.stat().st_mode & 0o077) == 0
    assert params["KeyLabel"] == "k2" and params["ViewerPublicKeyPem"] == _pub(new_key)
    assert params["PreviousPublicKeyPem"] == pems["A"], (
        "옛 키 값이 저장값과 바이트까지 같지 않다 — 옛 자리가 제자리 수정된다"
    )
    st = _viewer_stack(fake)
    assert st["keys"]["k1"] == {"pem": pems["A"], "id": "K1EXAMPLE0"}, (
        "옛 키가 바뀌었다 — 옛 링크가 끊긴다"
    )
    new_id = st["outputs"]["ViewerKeyPairId"]
    assert new_id != "K1EXAMPLE0" and st["outputs"]["PreviousKeyPairId"] == "K1EXAMPLE0"
    assert _link_signed_by(_link(proc), new_key, tmp_path) == new_id, (
        "새 링크가 새 자리 키로 서명되지 않았다"
    )
    assert (fake.out / "viewer-k1.pem").read_bytes() == old_key
    # 옛 키를 빼도 되는 때 = 옛 키 ID 로 낸 링크 중 가장 늦은 만료(다른 ID 의 기록은 셈하지 않는다).
    assert f"링크 2개 · 가장 늦은 만료 {_utc(far)}" in proc.stdout, proc.stdout
    assert "--retire-old-key" in proc.stdout
    assert not list(fake.out.glob(".viewer-*")), "임시 키가 남았다"
    _only_stack_c(fake)
    _assert_profile_region_on_every_call(fake)
    _assert_no_full_account_id(fake, proc)


@needs_openssl
def test_rotate_key_says_so_when_the_old_key_did_not_stay(tmp_path: Path) -> None:
    """갱신은 성공했는데 옛 키가 키 그룹에 없다(겹침을 모르는 템플릿 등) = 옛 링크가 이미 끊겼다 — 조용히 넘어가지 않는다."""
    state, _ = _viewer_state(tmp_path / "out")
    state["viewer_template"] = "one-key"
    fake = Fake(tmp_path, state)
    proc = fake.run(*_ROTATE)
    assert proc.returncode == 1, proc.stdout[-400:]
    assert (
        "옛 키가 키 그룹에 남지 않았습니다" in proc.stderr
        and "K1EXAMPLE0" in proc.stderr
    )
    assert "https://d1234" not in proc.stdout, (
        "옛 링크가 끊긴 채로 새 링크만 나눠 주게 된다"
    )
    assert (fake.out / "viewer-k2.pem").is_file(), (
        "스택에 올라간 새 키의 비밀키를 지웠다"
    )


@needs_openssl
def test_rerun_during_overlap_keeps_both_keys_and_signs_with_the_new_slot(tmp_path: Path) -> None:  # fmt: skip
    """겹침 중에 평소처럼 다시 실행 — KeyLabel·PreviousPublicKeyPem 을 넘기지 않아 두 키가 남고, 기본 키 파일은 새 자리."""
    state, pems = _viewer_state(tmp_path / "out", overlap=True)
    state["buckets"] = {"pkg-bucket": {"region": REGION}}
    state["stacks"]["nettopo-collector"] = dict(
        _INSTALLED["stacks"]["nettopo-collector"]
    )
    fake = Fake(tmp_path, state)
    proc = fake.run(*_VIEWER_7_DAYS, "--package", str(fake.package))
    assert proc.returncode == 0, proc.stderr
    (params,) = _viewer_parameters(fake)
    assert set(params) == {"ArtifactBucketName", "ViewerPublicKeyPem"}, params
    assert params["ViewerPublicKeyPem"] == pems["B"]
    st = _viewer_stack(fake)
    assert {s: k["id"] for s, k in st["keys"].items()} == {
        "k1": "K1EXAMPLE0",
        "k2": "K2EXAMPLE0",
    }
    assert (
        _link_signed_by(_link(proc), fake.out / "viewer-k2.pem", tmp_path)
        == "K2EXAMPLE0"
    )
    assert "겹침 교체 중" in proc.stdout and "--retire-old-key" in proc.stdout


@needs_openssl
def test_retire_removes_only_the_old_key_after_the_exact_confirmation(tmp_path: Path) -> None:  # fmt: skip
    import time

    state, _ = _viewer_state(tmp_path / "out", overlap=True)
    fake = Fake(tmp_path, state)
    soon = int(time.time()) + 3600
    _ledger(fake).write_text(f"{soon}\tK1EXAMPLE0\t1\n")
    proc = fake.run(*_RETIRE, stdin=_CONFIRM)
    assert proc.returncode == 0, proc.stderr
    assert "아직 만료되지 않은" in proc.stdout and _utc(soon) in proc.stdout
    assert _viewer_parameters(fake) == [{"PreviousPublicKeyPem": ""}]
    st = _viewer_stack(fake)
    assert {s: k["id"] for s, k in st["keys"].items()} == {"k2": "K2EXAMPLE0"}
    assert "PreviousKeyPairId" not in st["outputs"]
    assert "https://" not in proc.stdout, "옛 키 빼기는 링크를 만들지 않는다"
    _only_stack_c(fake)
    _assert_profile_region_on_every_call(fake)


@needs_openssl
def test_retire_without_overlap_changes_nothing(tmp_path: Path) -> None:
    state, _ = _viewer_state(tmp_path / "out")
    fake = Fake(tmp_path, state)
    proc = fake.run(*_RETIRE)
    assert proc.returncode == 0, proc.stderr
    assert "뺄 옛 키가 없습니다" in proc.stdout
    assert not [c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"]]


@needs_openssl
def test_rotate_no_overlap_cuts_every_old_link_in_one_update(tmp_path: Path) -> None:
    """유출 대응은 한 번에 — 새 키로 옮기며 옛 키를 같은 갱신에서 뺀다(옛 링크 전부 403)."""
    state, _ = _viewer_state(tmp_path / "out")
    fake = Fake(tmp_path, state)
    proc = fake.run(*_NO_OVERLAP, stdin=_CONFIRM)
    assert proc.returncode == 0, proc.stderr
    (params,) = _viewer_parameters(fake)
    new_key = fake.out / "viewer-k2.pem"
    assert params == {
        "KeyLabel": "k2",
        "ViewerPublicKeyPem": _pub(new_key),
        "PreviousPublicKeyPem": "",
    }
    st = _viewer_stack(fake)
    assert list(st["keys"]) == ["k2"] and "PreviousKeyPairId" not in st["outputs"]
    assert (
        _link_signed_by(_link(proc), new_key, tmp_path)
        == st["outputs"]["ViewerKeyPairId"]
    )
    _only_stack_c(fake)
    _assert_profile_region_on_every_call(fake)


@needs_openssl
def test_rotate_no_overlap_during_overlap_cuts_both_old_keys(tmp_path: Path) -> None:
    """겹침 중의 유출 — 빈 자리가 없으니 물러나던 키를 먼저 빼고(갱신 1), 새 키로 옮기며 지금 키를 뺀다(갱신 2)."""
    state, pems = _viewer_state(tmp_path / "out", overlap=True)
    fake = Fake(tmp_path, state)
    proc = fake.run(*_NO_OVERLAP, stdin=_CONFIRM)
    assert proc.returncode == 0, proc.stderr
    first, second = _viewer_parameters(fake)
    assert first == {"PreviousPublicKeyPem": ""}
    new_key = fake.out / "viewer-k1.pem"
    new_pub = _pub(new_key)
    assert new_pub not in (pems["A"], pems["B"]), (
        "물러나던 옛 비밀키를 그대로 다시 썼다"
    )
    assert second == {
        "KeyLabel": "k1",
        "ViewerPublicKeyPem": new_pub,
        "PreviousPublicKeyPem": "",
    }
    st = _viewer_stack(fake)
    assert list(st["keys"]) == ["k1"] and st["keys"]["k1"]["id"] not in (
        "K1EXAMPLE0",
        "K2EXAMPLE0",
    )
    assert (
        _link_signed_by(_link(proc), new_key, tmp_path)
        == st["outputs"]["ViewerKeyPairId"]
    )


@needs_openssl
def test_rotate_while_overlapping_stops_before_any_change(tmp_path: Path) -> None:
    state, _ = _viewer_state(tmp_path / "out", overlap=True)
    fake = Fake(tmp_path, state)
    before = {p.name: p.read_bytes() for p in fake.out.iterdir()}
    proc = fake.run(*_ROTATE)
    assert proc.returncode == 1 and "이미 겹침 교체 중" in proc.stderr, proc.stderr
    assert not [c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"]]
    assert {p.name: p.read_bytes() for p in fake.out.iterdir()} == before


@needs_openssl
@pytest.mark.parametrize(("args", "overlap"), [(_NO_OVERLAP, False), (_RETIRE, True)], ids=["no-overlap", "retire"])  # fmt: skip
@pytest.mark.parametrize("answer", ["", "yes", "nettopo-collector"])
def test_link_cutting_key_changes_need_the_exact_stack_name(
    tmp_path: Path, args: list[str], overlap: bool, answer: str
) -> None:
    state, _ = _viewer_state(tmp_path / "out", overlap=overlap)
    fake = Fake(tmp_path, state)
    before = {p.name: p.read_bytes() for p in fake.out.iterdir()}
    proc = fake.run(*args, stdin=answer + "\n")
    assert proc.returncode == 1 and "아무것도 바꾸지 않았습니다" in proc.stderr, (
        proc.stderr
    )
    assert [
        op for op in fake.ops() if _MUTATING.match(op) or op == "cloudformation deploy"
    ] == []
    assert {p.name: p.read_bytes() for p in fake.out.iterdir()} == before, (
        "비밀키 파일이 바뀌었다"
    )


@needs_openssl
@pytest.mark.parametrize(
    ("args", "overlap"),
    [(_ROTATE, False), (_NO_OVERLAP, False), (_NO_OVERLAP, True), (_RETIRE, True)],
    ids=["rotate", "no-overlap", "no-overlap-in-overlap", "retire"],
)
def test_key_dry_runs_change_nothing_and_never_prompt(
    tmp_path: Path, args: list[str], overlap: bool
) -> None:
    state, _ = _viewer_state(tmp_path / "out", overlap=overlap)
    fake = Fake(tmp_path, state)
    before = {p.name: p.read_bytes() for p in fake.out.iterdir()}
    proc = fake.run(*args, "--dry-run")  # 입력 없음 — 확인을 물으면 빈 입력으로 멈춘다
    assert proc.returncode == 0, proc.stderr
    deploys = [c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"]]
    assert deploys and all("--no-execute-changeset" in c for c in deploys)
    assert [op for op in fake.ops() if _MUTATING.match(op)] == []
    assert {p.name: p.read_bytes() for p in fake.out.iterdir()} == before, (
        "dry-run 이 키 파일을 바꿨다"
    )
    assert _viewer_stack(fake) == state["stacks"]["nettopo-viewer"]
    assert "https://" not in proc.stdout


def test_link_cutting_key_changes_have_no_way_around_the_confirmation() -> None:
    text = DEPLOY.read_text()
    for fn in ("rotate_key", "retire_old_key"):
        body = text[text.index(f"\n{fn}() {{") :]
        body = body[: body.index("\n}\n")]
        assert body.index("confirm_key_change ") < body.index("cfn_deploy "), (
            f"{fn}: 확인 입력보다 스택 갱신이 먼저 온다"
        )
    confirm = text[text.index("confirm_key_change() {") :]
    confirm = confirm[: confirm.index("\n}\n")]
    assert '[ "$answer" = "$STACK_C" ] ||' in confirm and "exit 1" in confirm


def test_an_empty_stored_parameter_is_read_as_empty_on_every_bash() -> None:
    """`printf -v X '%b' ""` 는 macOS /bin/bash 3.2 에서 X 를 비우지 않고 이전 값을 남긴다(실측 2026-10-23).

    그 때문에 처음 설치한 스택에서 `--rotate-key` 가 「이미 겹침 중」으로 거절됐다. 빈 값은 printf 를 거치지 않고 비운다.
    """
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("stack_parameter_exact() {") :]
    body = body[: body.index("\n}\n")]
    assert "printf -v EXACT" in body
    assert re.search(
        r'if \[ -n "\$out" \]; then printf -v EXACT .%b. "\$out"; else EXACT=""; fi',
        body,
    ), "빈 값을 printf -v 에 넘긴다 — bash 3.2 에서 이전 값이 남는다"


# ── 고객 배포 다듬기(2026-10-26) — 존댓말 · CloudShell · --download ─────────────────────────────


# 반말 문장 끝: 한글 낱자 + 「다」 바로 뒤가 문장 경계(공백·마침표·괄호·따옴표·줄 끝 등)인데 그 낱자가 「니」가 아닌 것.
_PLAIN_END = re.compile(r"([가-힣])다(?=$|[\s.)\"'·—:,」])")
# 화면 글이지만 남이 쓴 말을 그대로 옮긴 것(빌더 메시지 인용)은 잴 대상이 아니다.
_PLAIN_OK = ("pip 이 없다",)


def _screen_texts() -> list[str]:
    """deploy.sh 가 화면에 찍는 글 — say/die/echo/step/printf 의 문자열 · fail 의 이유 칸 · 도움말·안내 here-doc 본문."""
    text = re.sub(r"\\\n\s*", " ", DEPLOY.read_text())
    out, end = [], None
    for line in text.splitlines():
        if end is not None:
            if line.strip() == end:
                end = None
            else:
                out.append(line)
            continue
        if line.lstrip().startswith("#"):
            continue
        m = re.search(r"<<-?\s*'?(\w+)'?\s*$", line)
        if m:
            end = m.group(1)
            continue
        code = line.split(" # ")[0]
        for kw in ("say ", "die ", "echo ", "step "):
            for m in re.finditer(re.escape(kw) + r'"', code):
                out += _BASH_STRING.findall(code[m.start() :])[:1]
        out += re.findall(r"printf '([^']*)'", code)
        for m in re.finditer(r'(?:^|[\s;|&{(])fail "', code):
            parts = _BASH_STRING.findall(code[m.end() - 1 :])
            if len(parts) >= 3:
                out.append(
                    parts[2]
                )  # 이유 칸만 — 행 이름 칸은 안내서 증상 칸과 글자가 같아야 한다
    return out


def test_screen_output_is_polite() -> None:
    """고객이 보는 글은 「~합니다」 톤이다(사용자 지시 2026-10-26 · docs/qa/copy-a.md) — 「배포 패키지를 쓴다」 같은 반말 0."""
    texts = _screen_texts()
    # 🔴 파서가 글을 못 모으면 아래가 0회라 조용히 통과한다 — 모은 수부터 잰다.
    assert len(texts) >= 120, f"화면 글을 {len(texts)}개밖에 못 모았다"
    plain = [
        t for t in texts
        if any(m.group(1) not in "니마" for m in _PLAIN_END.finditer(t))  # 마다 = 조사
        and not any(ok in t for ok in _PLAIN_OK)
    ]  # fmt: skip
    assert not plain, "반말로 끝나는 화면 글: " + " | ".join(t[:80] for t in plain[:8])


_CLOUDSHELL = {"AWS_EXECUTION_ENV": "CloudShell"}


def test_cloudshell_install_needs_no_profile_and_says_how_to_get_the_file(
    tmp_path: Path,
) -> None:
    """CloudShell 에는 이름 있는 프로필이 없다 — `--profile` 없이 설치되고, 어떤 aws 호출에도 빈 `--profile` 이 붙지 않는다.

    마지막 안내는 「작업 → 파일 다운로드」와 S3 콘솔이다 — CloudShell 안의 out/index.html 은 그 자리에서 브라우저로 못 연다
    (사용자 실측 2026-10-26: 「(브라우저로 연다)」를 보고 어디서 여는지 물었다).
    """
    fake = Fake(tmp_path)
    proc = fake.run(
        "--region", REGION, "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), extra_env=_CLOUDSHELL,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    calls = [c for c in fake.calls() if c != ["--version"]]
    assert calls and all("--profile" not in c for c in calls), [
        c for c in calls if "--profile" in c
    ][:2]
    assert all("--region" in c and c[c.index("--region") + 1] == REGION for c in calls)
    assert "CloudShell 로그인 권한" in proc.stdout
    assert "작업 → 파일 다운로드" in proc.stdout and "S3 콘솔" in proc.stdout
    assert "art-bucket" in proc.stdout and "(브라우저로 여세요)" not in proc.stdout
    # 안내 명령에 빈 `--profile ` 이 남으면 그대로 따라 친 명령이 멈춘다.
    assert "--profile  " not in proc.stdout and "--profile --region" not in proc.stdout
    _assert_no_full_account_id(fake, proc)


def test_outside_cloudshell_the_profile_stays_required_and_the_hint_is_the_local_file(
    tmp_path: Path,
) -> None:
    fake = Fake(tmp_path)
    proc = fake.run("--region", REGION, "--bucket", "pkg-bucket")
    assert proc.returncode == 2 and "--profile 이 없습니다" in proc.stderr
    assert fake.calls() == []
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION, "--package", str(fake.package)
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "(브라우저로 여세요)" in proc.stdout and "파일 다운로드" not in proc.stdout


def _installed() -> dict:
    return {
        "stacks": {
            "nettopo-collector": {
                "status": "CREATE_COMPLETE",
                "params": {"SourceLocation": "pkg-bucket/nettopo-source.zip"},
                "outputs": {"ArtifactBucketName": "art-bucket", "ProjectName": "nettopo-collector-collector"},
            }
        }
    }  # fmt: skip


def test_download_only_fetches_the_latest_diagram(tmp_path: Path) -> None:
    """`--download` = S3 의 최신 index.html 받기만 — 빌드·배포·업로드를 하지 않는다(사용자 요청 2026-10-26)."""
    fake = Fake(tmp_path, _installed())
    proc = fake.run(*_base(), "--download")
    assert proc.returncode == 0, proc.stderr
    ops = fake.ops()
    assert "s3 cp" in ops, ops
    assert not [op for op in ops if op != "s3 cp" and _MUTATING.match(op)], ops
    assert not [
        op for op in ops if op.startswith(("codebuild", "cloudformation deploy"))
    ], ops
    cp = next(c for c in fake.calls() if c[:2] == ["s3", "cp"])
    assert cp[2] == "s3://art-bucket/index.html" and cp[3].endswith("out/index.html")
    assert (fake.out / "index.html").read_text().startswith("<html>")
    assert (
        "최신 다이어그램을 받았습니다" in proc.stdout
        and "(브라우저로 여세요)" in proc.stdout
    )
    _assert_profile_region_on_every_call(fake)


def test_download_in_cloudshell_also_needs_no_profile(tmp_path: Path) -> None:
    fake = Fake(tmp_path, _installed())
    proc = fake.run("--region", REGION, "--download", extra_env=_CLOUDSHELL)
    assert proc.returncode == 0, proc.stderr
    assert all("--profile" not in c for c in fake.calls())
    assert "작업 → 파일 다운로드" in proc.stdout


@pytest.mark.parametrize(
    "extra, message",
    [(["--monitoring"], "--download 에는"), (["--collect"], "함께 쓸 수 없습니다")],
)
def test_download_takes_no_install_options(
    tmp_path: Path, extra: list[str], message: str
) -> None:
    fake = Fake(tmp_path, _installed())
    proc = fake.run(*_base(), "--download", *extra)
    assert proc.returncode == 2 and message in proc.stderr, proc.stderr
    assert fake.calls() == []


def test_download_without_stack_a_points_to_the_install_row(tmp_path: Path) -> None:
    fake = Fake(tmp_path)
    proc = fake.run(*_base(), "--download")
    assert proc.returncode == 1
    assert "2단계 스택 A 배포 실패」 행" in proc.stderr
    assert "먼저 ./deploy.sh 로 설치해 주세요" in proc.stderr
    assert not [op for op in fake.ops() if op.startswith("s3 ")]


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl 없음")
def test_cloudshell_viewer_issues_a_link_without_a_profile(tmp_path: Path) -> None:
    """CloudShell 에서 `--viewer` — 링크 발급 도우미(issue-viewer-link.sh)도 프로필 없이 스택 C 를 읽는다."""
    fake = Fake(tmp_path)
    proc = fake.run(
        "--region", REGION, "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), "--viewer", "--days", "7", extra_env=_CLOUDSHELL,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout
    assert all("--profile" not in c for c in fake.calls())
    # 「다시 발급」 안내 명령에 빈 --profile 이 남지 않는다.
    again = next(x for x in proc.stdout.splitlines() if x.startswith("다시 발급:"))
    assert "--profile" not in again and f"--region {REGION}" in again


# ── 선택 보강(안내서 「선택 보강」) — --external-id · --org-id · --narrow-viewer ─────────────────────────
#    셋 다 opt-in 이다. 주지 않으면 스택 A 에 새 파라미터를 넘기지 않고 출력도 이 기능 전과 같다.

_EXT = "nettopo-ext-0a1b2c3d"
_ORG = "o-abcdef1234"
_HARDENING_KEYS = ("MemberExternalId", "ViewerOrigin", "ViewerDistributionId")


def _deploy_parameters(fake: Fake, stack: str) -> list[dict[str, str]]:
    """그 스택의 deploy 호출마다 --parameter-overrides 를 dict 로(호출 순서)."""
    out = []
    for c in fake.calls():
        if (
            c[:2] != ["cloudformation", "deploy"]
            or c[c.index("--stack-name") + 1] != stack
        ):
            continue
        params, i = {}, c.index("--parameter-overrides") + 1
        while i < len(c) and not c[i].startswith("--"):
            k, v = c[i].split("=", 1)
            params[k] = v
            i += 1
        out.append(params)
    return out


def _stack_b_commands(stdout: str) -> tuple[str, str]:
    """출력된 스택 B 명령 두 개(CLI 한 줄 · StackSet 만들기)의 `--parameter-overrides` 줄과 `--parameters` 줄."""
    lines = stdout.splitlines()
    direct = [x for x in lines if "--parameter-overrides HubAccountId=" in x]
    stackset = [x for x in lines if "--parameters '[" in x]
    assert len(direct) == 1 and len(stackset) == 1, (direct, stackset)
    return direct[0], stackset[0]


def _stackset_parameters(line: str) -> dict[str, str]:
    body = line[line.index("'[") + 1 : line.rindex("]'") + 1]
    return {p["ParameterKey"]: p["ParameterValue"] for p in json.loads(body)}


def _install(fake: Fake, *extra: str) -> subprocess.CompletedProcess:
    return fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), *extra,
    )  # fmt: skip


def test_without_hardening_flags_nothing_new_is_passed_or_printed(
    tmp_path: Path,
) -> None:
    """opt-in — 플래그 없이 처음 설치 · 다시 실행 · --viewer 를 돌려도 새 파라미터가 어디에도 붙지 않는다."""
    fake = Fake(tmp_path)
    procs = [
        _install(fake),
        fake.run(*_base(), "--package", str(fake.package)),
        fake.run(*_base(), "--package", str(fake.package), "--viewer", "--days", "7"),
    ]
    for proc in procs:
        assert proc.returncode == 0, proc.stderr
        direct, stackset = _stack_b_commands(proc.stdout)
        assert direct.rstrip(" \\").endswith(f"AllowedRegions={REGION}"), direct
        assert set(_stackset_parameters(stackset)) == {"HubAccountId", "AllowedRegions"}
        assert "위 두 값 · 「IAM 리소스" in proc.stdout
        for word in (
            "ExternalId",
            "PrincipalOrgId",
            "ViewerOrigin",
            "ViewerDistributionId",
        ):
            assert word not in proc.stdout + proc.stderr, word
    a_calls = _deploy_parameters(fake, "nettopo-collector")
    assert len(a_calls) == 3, "--narrow-viewer 없이 스택 A 를 한 번 더 갱신했다"
    assert not [k for p in a_calls for k in p if k in _HARDENING_KEYS], a_calls
    assert not [k for p in _viewer_parameters(fake) for k in p if k in _HARDENING_KEYS]
    _assert_profile_region_on_every_call(fake)


def test_external_id_goes_to_stack_a_and_both_printed_stack_b_commands(
    tmp_path: Path,
) -> None:
    fake = Fake(tmp_path)
    proc = _install(fake, "--external-id", _EXT)
    assert proc.returncode == 0, proc.stderr
    assert _deploy_parameters(fake, "nettopo-collector")[-1]["MemberExternalId"] == _EXT
    direct, stackset = _stack_b_commands(proc.stdout)
    assert f" ExternalId={_EXT} " in direct
    assert _stackset_parameters(stackset)["ExternalId"] == _EXT
    assert (
        "위 두 값 + ExternalId · " in proc.stdout
    )  # 콘솔로 만드는 사람에게도 칸을 알린다
    # 값은 그 두 명령에만 찍힌다(오류·진행 줄에 되풀이하지 않는다).
    assert (proc.stdout + proc.stderr).count(_EXT) == 2
    _assert_profile_region_on_every_call(fake)
    _assert_no_full_account_id(fake, proc)


def test_rerun_without_external_id_keeps_it_on_stack_a_and_in_the_printed_commands(
    tmp_path: Path,
) -> None:
    """생략한 다시 실행은 MemberExternalId 를 넘기지 않아 이전 값이 남고, 스택 B 명령에는 그 값이 그대로 나온다.

    출력에서 빠지면 그 뒤 붙는 소스 계정만 외부 ID 조건 없이 역할이 선다(조용한 약화).
    """
    fake = Fake(tmp_path)
    assert _install(fake, "--external-id", _EXT).returncode == 0
    again = fake.run(*_base(), "--package", str(fake.package))
    assert again.returncode == 0, again.stderr
    last = _deploy_parameters(fake, "nettopo-collector")[-1]
    assert "MemberExternalId" not in last, "생략했는데 덮어썼다 — 이전 값이 지워진다"
    state = json.loads(fake.state.read_text())
    assert state["stacks"]["nettopo-collector"]["params"]["MemberExternalId"] == _EXT
    direct, stackset = _stack_b_commands(again.stdout)
    assert f" ExternalId={_EXT} " in direct
    assert _stackset_parameters(stackset)["ExternalId"] == _EXT


def test_org_id_goes_only_into_the_printed_stack_b_commands(tmp_path: Path) -> None:
    """스택 A 에는 조직 파라미터가 없다 — 스택 B 명령 두 개에만 PrincipalOrgId 가 붙는다."""
    fake = Fake(tmp_path)
    proc = _install(fake, "--org-id", _ORG, "--external-id", _EXT)
    assert proc.returncode == 0, proc.stderr
    assert not any(
        _ORG in a
        for c in fake.calls()
        if c[:2] == ["cloudformation", "deploy"]
        for a in c
    ), "조직 ID 가 스택 A 배포에 들어갔다"
    direct, stackset = _stack_b_commands(proc.stdout)
    assert direct.rstrip(" \\").endswith(f" ExternalId={_EXT} PrincipalOrgId={_ORG}"), (
        direct
    )
    assert _stackset_parameters(stackset) == {
        "HubAccountId": _stackset_parameters(stackset)["HubAccountId"],
        "AllowedRegions": REGION,
        "ExternalId": _EXT,
        "PrincipalOrgId": _ORG,
    }
    assert "위 두 값 + ExternalId + PrincipalOrgId · " in proc.stdout


def test_longest_external_id_the_template_accepts_is_accepted(tmp_path: Path) -> None:
    """1224자(템플릿 상한) — macOS 정규식은 `{2,1224}` 를 오류로 봐 모든 값을 거절한다(길이는 따로 잰다)."""
    fake = Fake(tmp_path)
    ext = "a-" + "b" * 1222
    proc = _install(fake, "--external-id", ext, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    deploy = next(c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"])
    assert f"MemberExternalId={ext}" in deploy


@needs_openssl
def test_narrow_viewer_updates_stack_a_with_stack_c_outputs(tmp_path: Path) -> None:
    """스택 C 를 올린 뒤 그 ViewerDomain·DistributionId 로 스택 A 를 한 번 더 갱신한다 — 다른 파라미터는 넘기지 않는다."""
    fake = Fake(tmp_path)
    proc = _install(fake, "--viewer", "--days", "7", "--narrow-viewer")
    assert proc.returncode == 0, proc.stderr
    stacks = [
        c[c.index("--stack-name") + 1]
        for c in fake.calls()
        if c[:2] == ["cloudformation", "deploy"]
    ]
    assert stacks == ["nettopo-collector", "nettopo-viewer", "nettopo-collector"], (
        stacks
    )
    assert _deploy_parameters(fake, "nettopo-collector")[-1] == {
        "ViewerOrigin": "https://d1234.cloudfront.net",
        "ViewerDistributionId": "E1EXAMPLE2345",
    }
    params = json.loads(fake.state.read_text())["stacks"]["nettopo-collector"]["params"]
    assert params["SourceLocation"] == "pkg-bucket/nettopo-source.zip"  # 이전 값 유지
    assert params["TargetRegions"] == REGION
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout
    # 다시 실행할 때 생략하면 넘기지 않는다 — 좁힌 값이 그대로 남는다.
    again = fake.run(
        *_base(), "--package", str(fake.package), "--viewer", "--days", "7"
    )
    assert again.returncode == 0, again.stderr
    a_calls = _deploy_parameters(fake, "nettopo-collector")
    assert len(a_calls) == 3 and not set(a_calls[2]) & set(_HARDENING_KEYS), a_calls[2:]
    after = json.loads(fake.state.read_text())["stacks"]["nettopo-collector"]["params"]
    assert after["ViewerOrigin"] == "https://d1234.cloudfront.net"
    assert after["ViewerDistributionId"] == "E1EXAMPLE2345"
    _assert_profile_region_on_every_call(fake)
    _assert_no_full_account_id(fake, proc)


@needs_openssl
def test_narrow_viewer_stops_when_stack_c_has_no_distribution_id(
    tmp_path: Path,
) -> None:
    """출력을 못 읽으면 빈 값으로 스택 A 를 갱신하지 않고 멈춘다 — 빈 ViewerDistributionId 는 「이 계정의 아무 배포」다."""
    fake = Fake(tmp_path, {"viewer_no_distribution_id": True})
    proc = _install(fake, "--viewer", "--days", "7", "--narrow-viewer")
    assert proc.returncode == 1
    line = next(x for x in proc.stderr.splitlines() if "실패:" in x)
    assert (
        line.startswith("🔴 6단계 스택 C 출력 읽기 실패:") and "DistributionId" in line
    )
    assert len(_deploy_parameters(fake, "nettopo-collector")) == 1, (
        "빈 값으로 스택 A 를 갱신했다"
    )
    assert "index.html?Expires=" not in proc.stdout
