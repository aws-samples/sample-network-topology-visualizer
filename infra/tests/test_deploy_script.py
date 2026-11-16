"""Tests for the root `deploy.sh`, the script that performs steps 1 to 3, 5 and 6
of the guide, plus deletion, on the operator's behalf.

There are two kinds of test here:

- Static. Reading the script text to check the same invariants as the guide tests
  (`test_install_guide_names_profile_and_region_…`): every `aws` command spells
  out `--profile` and `--region`, no default profile or Region, no account ID
  literals, and `--destroy` deletes nothing without a typed confirmation.
- Executed. Running the script for real with a fake `aws` first on `PATH`, then
  checking from the call log which `aws` commands each operation issues, in what
  order, and that none is missing `--profile` or `--region`. Real AWS is never
  called, and these tests are no substitute for a real deployment.
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
FAKE_ACCOUNT = "111122223333"  # an example account ID; never a real one


# ── Static checks ────────────────────────────────────────────────────────────


def _joined_lines(*, heredoc: bool = True) -> list[str]:
    """One command per line, joining continuations (`\\`) and dropping comment
    lines. With `heredoc=False`, here-doc bodies (printed text) are dropped
    too."""
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
# The `aws` calls the script actually executes: those in command position (start
# of line, `$(`, `|`, `;`, `&&`, `||`, `then`, `do`, `if`).
_AWS_EXECUTED = re.compile(r"(?:^|\$\(|[|;&]|\bthen\b|\bdo\b|\bif\b|!)\s*aws\s")


def test_every_aws_command_names_profile_and_region() -> None:
    """Executed commands carry `--profile "$PROFILE" --region "$REGION"`, and so
    do the commands the script prints for someone to run."""
    lines = [line for line in _joined_lines() if _AWS_COMMAND.search(line)]
    executed = [
        line
        for line in _joined_lines(heredoc=False)
        if _AWS_COMMAND.search(line) and _AWS_EXECUTED.search(line)
    ]
    # With no commands found, the loops below would run zero times and pass
    # silently, so count what was found first.
    assert len(executed) >= 25, f"only {len(executed)} executed aws commands were found"
    assert len(lines) > len(executed), (
        "no aws commands printed for the operator to run (Stack B and so on) were found"
    )
    for line in executed:
        assert '--profile "$PROFILE"' in line and '--region "$REGION"' in line, (
            f"aws call without a profile or Region: {line.strip()[:140]}"
        )
    for line in lines:
        assert "--profile " in line and "--region " in line, (
            f"printed command without a profile or Region: {line.strip()[:140]}"
        )
    # The link-issuing helper receives the same two values, since the aws calls
    # inside it use them.
    issuer = [line for line in _joined_lines() if '"$LINK_ISSUER"' in line]
    assert issuer and all(
        '--profile "$PROFILE"' in line and '--region "$REGION"' in line
        for line in issuer
    )


def test_no_default_profile_region_or_account_literal() -> None:
    text = DEPLOY.read_text()
    for name in ("PROFILE", "REGION"):
        values = set(re.findall(rf"(?<![A-Z_]){name}=([^\s;]*)", text))
        assert values <= {'""', "$2"}, f"{name} has a default value: {values}"
        assert f"${{{name}:-" not in text and f"${{{name}:=" not in text
    assert not re.search(r"\bAWS_(?:PROFILE|REGION|DEFAULT_REGION)\b", text), (
        "reads the shell environment's default profile or Region"
    )
    for line in text.splitlines():
        if re.search(r"\b[a-z]{2}(?:-gov)?-[a-z]+-\d\b", line):
            # The one exception: a bucket in us-east-1 takes no
            # LocationConstraint, which is what these two lines decide.
            assert "us-east-1" in line and (
                '"$REGION" = us-east-1' in line or "LocationConstraint" in line
            ), f"Region literal: {line.strip()[:120]}"
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", text), "12-digit account ID literal"


def test_destroy_has_no_way_around_the_confirmation() -> None:
    text = DEPLOY.read_text()
    body = text[text.index("destroy() {") :]
    body = body[: body.index("\n}\n")]
    prompt = body.index("read -r answer")
    first_delete = min(
        body.index("delete_stack "), body.index("empty_and_delete_bucket ")
    )
    assert prompt < first_delete, "a deletion happens before the confirmation prompt"
    assert '[ "$answer" = "$token" ] ||' in body
    assert not re.search(r"--(?:yes|force|no-confirm)\)", text), (
        "an option that skips the confirmation"
    )


def test_state_key_parsing_matches_the_builder() -> None:
    """deploy.sh parses the S3 key out of the builder's output, so a change to the
    key's shape is caught here."""
    builder = (ROOT / "infra" / "build-state-lambda.sh").read_text()
    assert 'KEY="lambda/state-observer-${SHA:0:12}.zip"' in builder
    assert "lambda/state-observer-[0-9a-f]{12}\\.zip" in DEPLOY.read_text()


_BASH_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _failure_rows() -> set[str]:
    """The troubleshooting row names referenced by `fail … "<row>"` and
    `cfn_deploy <step> <what> "<row>"`, excluding rows built from variables."""
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
    """The first column (the symptom) of the guide's troubleshooting table, with
    backticks stripped since they are not part of what a reader sees."""
    text = guide.read_text()
    m = re.search(r"\n## (?:문제 해결|Troubleshooting)\n(.*?)\n## ", text, flags=re.S)
    assert m, f"{guide.name} has no troubleshooting section"
    return [
        line.split("|")[1].strip().replace("`", "")
        for line in m.group(1).splitlines()
        if line.startswith("| ")
        and not line.startswith("| 증상")
        and not line.startswith("| Symptom")
    ]


def test_every_failure_row_is_in_the_install_guide() -> None:
    """When a failure line points at a row of the guide's troubleshooting table,
    that row has to exist under exactly that name.

    We measured this while preparing for a customer deployment: of the 14 rows the
    script pointed at, exactly one ("pip is missing") existed in the guide, so
    anyone who hit a failure and opened the guide found nothing. The Korean guide
    carries the same string, since the script's output is Korean and the link
    points there; the English guide quotes that string inside the cell.
    """
    rows = _failure_rows()
    # With no rows found, the checks below would pass silently, so count first.
    assert len(rows) >= 14, (
        f"only {len(rows)} troubleshooting rows were found in deploy.sh: {sorted(rows)}"
    )
    anchor = re.search(
        r'GUIDE_TROUBLE="docs/install-guide\.md#([^"]+)"', DEPLOY.read_text()
    )
    assert anchor and anchor.group(1) == "문제-해결", (
        "the guide section anchor deploy.sh points at has changed"
    )
    ko = set(_trouble_symptoms(ROOT / "docs" / "install-guide.md"))
    missing = sorted(rows - ko)
    assert not missing, f"rows absent from the Korean guide's table: {missing}"
    en = _trouble_symptoms(ROOT / "docs" / "install-guide.en.md")
    missing_en = sorted(r for r in rows if not any(cell.startswith(r) for cell in en))
    assert not missing_en, f"rows absent from the English guide's table: {missing_en}"


# ── Executed, against a fake `aws` ───────────────────────────────────────────

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
    elif "OutputKey==`ViewerDomain`" in q:  # issue-viewer-link.sh asks for both outputs at once, unlike deploy.sh
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
        # A model of Stack C's two key slots: the template's rules plus
        # CloudFront's refusal to change an uploaded public key's value.
        # Values are compared byte for byte: one differing character in the old
        # slot's PEM means an in-place update, a rejection and a rollback that
        # keeps the old value.
        label, prev = merged.get("KeyLabel", "k1"), merged.get("PreviousPublicKeyPem", "")
        if state.get("viewer_template") == "one-key":  # a deployed template that predates overlapping rotation
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
    assert calls, "aws was never called"
    for c in calls:
        assert "--profile" in c and c[c.index("--profile") + 1] == PROFILE, c
        assert "--region" in c and c[c.index("--region") + 1] == region, c


def _assert_no_full_account_id(fake: Fake, proc: subprocess.CompletedProcess) -> None:
    assert FAKE_ACCOUNT not in proc.stdout + proc.stderr, (
        "the full account ID was printed to the screen"
    )
    for path in fake.out.rglob("*") if fake.out.exists() else []:
        if path.is_file():
            assert FAKE_ACCOUNT.encode() not in path.read_bytes(), (
                f"account ID in a file: {path}"
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
        # Stack C's AllowedPattern rejects wildcards and session ARNs; the script
        # stops earlier still, before calling aws at all.
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
        # Key rotation: an invalid combination of flags stops before calling aws.
        ([*_base(), "--rotate-key", "--days", "7"], "--viewer"),
        ([*_VIEWER_7_DAYS, "--no-overlap"], "--rotate-key"),
        ([*_base(), "--viewer", "--rotate-key"], "--days"),
        ([*_VIEWER_7_DAYS, "--retire-old-key"], "--retire-old-key"),
        ([*_VIEWER_7_DAYS, "--rotate-key", "--retire-old-key"], "함께 쓸 수 없습니다"),
        ([*_VIEWER_7_DAYS, "--rotate-key", "--bucket", "pkg-bucket"], "스택 C 만"),
        # Optional hardening: anything not matching the template's AllowedPattern
        # stops before calling aws.
        ([*_base(), "--external-id", "x"], "--external-id 는"),  # under 2 characters
        ([*_base(), "--external-id", "x" * 1225], "--external-id 는"),  # over 1224
        ([*_base(), "--external-id", "has space"], "--external-id 는"),
        ([*_base(), "--external-id", "quote'd"], "--external-id 는"),
        (
            [*_base(), "--external-id", f"ext-{FAKE_ACCOUNT}"],
            "12자리 숫자",
        ),  # masked on screen, so the printed command would be wrong
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


@pytest.mark.skipif(
    shutil.which("aws") is None, reason="the real AWS CLI is not installed"
)
def test_real_cli_with_a_missing_profile_fails_fast(tmp_path: Path) -> None:
    """With the real AWS CLI and a profile that does not exist, the script stops at
    step 0, before any network call, using an empty config file."""
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
    assert mutating == [], f"--dry-run made a mutating call: {mutating}"
    deploys = [c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"]]
    assert len(deploys) == 1 and "--no-execute-changeset" in deploys[0]
    assert "AWS::S3::Bucket" in proc.stdout  # shows what the change set contains
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
    # The source-account commands are printed with their values filled in, and the
    # account ID masked to its last four digits.
    assert "--stack-name nettopo-member-role" in proc.stdout
    assert f"AllowedRegions={region}" in proc.stdout and "…3333" in proc.stdout
    assert "create-stack-set" in proc.stdout
    _assert_profile_region_on_every_call(fake, region)
    _assert_no_full_account_id(fake, proc)


def test_printed_stackset_commands_tell_delegated_admins_about_call_as(
    tmp_path: Path,
) -> None:
    """The printed StackSet commands assume the management account, so typed
    verbatim from a delegated administrator account they are rejected.

    While preparing for a customer deployment we found that section 4-C of the
    guide mentions `--call-as DELEGATED_ADMIN` but the script's output did not, so
    an organisation administrator copying the output gets blocked without knowing
    why. That line has to sit directly above the command.
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
    assert hint, (
        "no delegated-administrator note (--call-as DELEGATED_ADMIN) above the "
        "StackSet command"
    )


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
    # An installation with a signer confirms the page was published before
    # downloading it.
    assert "s3api head-object" in fake.ops()
    _assert_profile_region_on_every_call(fake)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not available")
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
        "the private key is readable by others"
    )
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout
    _assert_profile_region_on_every_call(fake)
    # Without --log-readers nothing is passed, so the template default applies
    # (empty, meaning the account root only) and an update keeps the prior value.
    assert "LogReaderPrincipalArns" not in _viewer_parameters(fake)[-1]


def _viewer_parameters(fake: Fake) -> list[dict[str, str]]:
    """The --parameter-overrides of each Stack C deploy call, as a dict."""
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


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not available")
def test_viewer_passes_log_readers_only_when_given(tmp_path: Path) -> None:
    """Stack C's LogReaderPrincipalArns: the value given is passed through, and
    omitting it on a later run passes nothing, so the prior value remains."""
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
        "omitting it overwrote the value, erasing the readers that were listed"
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
    """Under macOS bash 3.2 with a UTF-8 locale, `$DAYS일` is read as a variable
    named `DAYS` plus the first byte of the character after it, which trips
    `set -u`.

    We hit this once: `--viewer` ended in `DAYS\ufffd: unbound variable` instead of
    printing a link. A variable immediately followed by a non-ASCII character
    has to be written as `${NAME}`.
    """
    src = DEPLOY.read_text(encoding="utf-8")
    bad = [
        (n, line.strip())
        for n, line in enumerate(src.splitlines(), 1)
        if not line.lstrip().startswith("#")
        and re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", line)
    ]
    assert bad == [], (
        f"a variable is glued to a non-ASCII character; wrap it in braces: {bad}"
    )


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not available")
def test_viewer_prints_the_link_under_a_utf8_locale(tmp_path: Path) -> None:
    """On a Mac with a Korean locale, `--viewer` prints the link and exits
    successfully; this is the executed counterpart of the defect above."""
    fake = Fake(tmp_path)
    proc = fake.run(
        *_base(), "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), "--viewer", "--days", "1",
        locale="ko_KR.UTF-8",
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "보는 링크 (1일" in proc.stdout
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not available")
def test_a_new_stack_c_inherits_stack_a_tags(tmp_path: Path) -> None:
    """Adding --viewer on a second run without --tags gives the new Stack C the
    tags from Stack A; we measured it arriving with `Tags=[]`."""
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


# ── Rotating Stack C's signing key: overlap, retire, and no-overlap (a leak) ──

needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is not available"
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
    """Exactly the shape deploy.sh passes: `$(openssl rsa -pubout)` has no
    trailing newline."""
    return subprocess.run(
        ["openssl", "rsa", "-in", path, "-pubout"],
        check=True, capture_output=True, text=True,
    ).stdout.rstrip("\n")  # fmt: skip


def _viewer_state(out: Path, *, overlap: bool = False, trailing_newline: bool = False):
    """A deployed Stack C (as state for the fake aws) plus the private key in
    out/. With `overlap`, the new key sits in slot k2 and the old one in k1, mid
    rotation."""
    out.mkdir(parents=True, exist_ok=True)
    a = _openssl_key(out / "viewer-k1.pem") + ("\n" if trailing_newline else "")
    params = {
        "ArtifactBucketName": "art-bucket",
        "KeyLabel": "k1",
        "ViewerPublicKeyPem": a,
        # Real CloudFormation returns a parameter left at its default as an empty
        # string, not null. Modelling it as null would hide the macOS bash 3.2
        # defect where `printf -v X '%b' ""` fails to empty X.
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
    """The issuance ledger: written by issue-viewer-link.sh and read by deploy.sh,
    here under a fake HOME with no XDG_STATE_HOME."""
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
    """Verify a link's signature against that private key's public half and return
    the Key-Pair-Id, or an empty string if verification fails."""
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
    """Key rotation touches Stack C only, not Stack A, the collection or the
    buckets, so responding to a leak never waits on a collection."""
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
    """Overlapping rotation: the new key goes into the empty slot (k2) and the old
    one stays in its slot (k1) with an identical value, so old links keep working
    until they expire.

    If the old slot's PEM differs from the stored value by even one byte, trailing
    newline included, CloudFormation attempts an in-place update and is rejected.
    So this also checks that the stored value is read and passed byte for byte,
    including a stored value that ends with a newline.
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
        "the old key's value is not byte-identical to the stored one, so the old "
        "slot gets updated in place"
    )
    st = _viewer_stack(fake)
    assert st["keys"]["k1"] == {"pem": pems["A"], "id": "K1EXAMPLE0"}, (
        "the old key changed, which breaks the old links"
    )
    new_id = st["outputs"]["ViewerKeyPairId"]
    assert new_id != "K1EXAMPLE0" and st["outputs"]["PreviousKeyPairId"] == "K1EXAMPLE0"
    assert _link_signed_by(_link(proc), new_key, tmp_path) == new_id, (
        "the new link was not signed with the incoming slot's key"
    )
    assert (fake.out / "viewer-k1.pem").read_bytes() == old_key
    # The old key can be retired once the latest expiry among links issued with
    # its ID has passed; ledger entries for other IDs do not count.
    assert f"링크 2개 · 가장 늦은 만료 {_utc(far)}" in proc.stdout, proc.stdout
    assert "--retire-old-key" in proc.stdout
    assert not list(fake.out.glob(".viewer-*")), "a temporary key was left behind"
    _only_stack_c(fake)
    _assert_profile_region_on_every_call(fake)
    _assert_no_full_account_id(fake, proc)


@needs_openssl
def test_rotate_key_says_so_when_the_old_key_did_not_stay(tmp_path: Path) -> None:
    """If the update succeeded but the old key is not in the key group (an older
    template that knows nothing of overlap, say), the old links are already
    broken, and that is not passed over silently."""
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
        "we would hand out new links while the old ones are already broken"
    )
    assert (fake.out / "viewer-k2.pem").is_file(), (
        "deleted the private key of the new key that is now in the stack"
    )


@needs_openssl
def test_rerun_during_overlap_keeps_both_keys_and_signs_with_the_new_slot(tmp_path: Path) -> None:  # fmt: skip
    """An ordinary re-run during an overlap passes neither KeyLabel nor
    PreviousPublicKeyPem, so both keys remain and the default key file is the
    incoming slot's."""
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
    assert "https://" not in proc.stdout, "retiring the old key issues no link"
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
    """Responding to a leak happens in one step: move to the new key and remove
    the old one in the same update, so every old link returns 403."""
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
    """A leak during an overlap: with no free slot, the retiring key is removed
    first (update one), then the move to the new key removes the current one
    (update two)."""
    state, pems = _viewer_state(tmp_path / "out", overlap=True)
    fake = Fake(tmp_path, state)
    proc = fake.run(*_NO_OVERLAP, stdin=_CONFIRM)
    assert proc.returncode == 0, proc.stderr
    first, second = _viewer_parameters(fake)
    assert first == {"PreviousPublicKeyPem": ""}
    new_key = fake.out / "viewer-k1.pem"
    new_pub = _pub(new_key)
    assert new_pub not in (pems["A"], pems["B"]), (
        "reused the retiring private key as it was"
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
        "the private key file changed"
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
    proc = fake.run(*args, "--dry-run")  # no input: a prompt would stop on empty input
    assert proc.returncode == 0, proc.stderr
    deploys = [c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"]]
    assert deploys and all("--no-execute-changeset" in c for c in deploys)
    assert [op for op in fake.ops() if _MUTATING.match(op)] == []
    assert {p.name: p.read_bytes() for p in fake.out.iterdir()} == before, (
        "a dry run modified the key files"
    )
    assert _viewer_stack(fake) == state["stacks"]["nettopo-viewer"]
    assert "https://" not in proc.stdout


def test_link_cutting_key_changes_have_no_way_around_the_confirmation() -> None:
    text = DEPLOY.read_text()
    for fn in ("rotate_key", "retire_old_key"):
        body = text[text.index(f"\n{fn}() {{") :]
        body = body[: body.index("\n}\n")]
        assert body.index("confirm_key_change ") < body.index("cfn_deploy "), (
            f"{fn}: the stack update comes before the confirmation prompt"
        )
    confirm = text[text.index("confirm_key_change() {") :]
    confirm = confirm[: confirm.index("\n}\n")]
    assert '[ "$answer" = "$STACK_C" ] ||' in confirm and "exit 1" in confirm


def test_an_empty_stored_parameter_is_read_as_empty_on_every_bash() -> None:
    """On macOS /bin/bash 3.2, `printf -v X '%b' ""` leaves X at its previous
    value rather than emptying it.

    That is why `--rotate-key` was rejected as "already mid-rotation" on a
    freshly installed stack. An empty value is assigned directly, bypassing
    printf.
    """
    src = DEPLOY.read_text(encoding="utf-8")
    body = src[src.index("stack_parameter_exact() {") :]
    body = body[: body.index("\n}\n")]
    assert "printf -v EXACT" in body
    assert re.search(
        r'if \[ -n "\$out" \]; then printf -v EXACT .%b. "\$out"; else EXACT=""; fi',
        body,
    ), "passes an empty value to printf -v, which keeps the previous value on bash 3.2"


# ── Operator-facing polish: register, CloudShell, --download ─────────────────


# A plain-form sentence ending: a Hangul syllable followed by 「다」 at a sentence
# boundary (space, full stop, bracket, quote, end of line and so on) where that
# syllable is not 「니」.
_PLAIN_END = re.compile(r"([가-힣])다(?=$|[\s.)\"'·—:,」])")
# Screen text that quotes someone else's words verbatim (a builder's message) is
# out of scope for this check.
_PLAIN_OK = ("pip 이 없다",)


def _screen_texts() -> list[str]:
    """The text deploy.sh prints: the strings in say/die/echo/step/printf, the
    reason field of `fail`, and the bodies of the help and guidance here-docs."""
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
                )  # the reason field only: the row name must match the guide's symptom cell
    return out


def test_screen_output_is_polite() -> None:
    """The Korean text people read uses the polite 「~합니다」 register, so there is
    no plain-form phrasing such as 「배포 패키지를 쓴다」."""
    texts = _screen_texts()
    # If the parser collected nothing, the check below would pass silently, so
    # count what was collected first.
    assert len(texts) >= 120, f"only {len(texts)} pieces of screen text were collected"
    plain = [
        t for t in texts
        if any(m.group(1) not in "니마" for m in _PLAIN_END.finditer(t))  # 마다 is a particle
        and not any(ok in t for ok in _PLAIN_OK)
    ]  # fmt: skip
    assert not plain, "screen text ending in the plain form: " + " | ".join(
        t[:80] for t in plain[:8]
    )


_CLOUDSHELL = {"AWS_EXECUTION_ENV": "CloudShell"}


def test_cloudshell_install_needs_no_profile_and_says_how_to_get_the_file(
    tmp_path: Path,
) -> None:
    """CloudShell has no named profiles, so installation runs without `--profile`
    and no aws call carries an empty `--profile`.

    The closing guidance points at Actions, Download file and the S3 console,
    because the `out/index.html` inside CloudShell cannot be opened in a browser
    from there. Someone reading "(open it in a browser)" asked where.
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
    # An empty `--profile ` left in a printed command would stop that command for
    # anyone who types it as shown.
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
    """`--download` only fetches the newest index.html from S3: it builds nothing,
    deploys nothing and uploads nothing."""
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


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl is not available")
def test_cloudshell_viewer_issues_a_link_without_a_profile(tmp_path: Path) -> None:
    """`--viewer` in CloudShell: the link-issuing helper (issue-viewer-link.sh)
    also reads Stack C without a profile."""
    fake = Fake(tmp_path)
    proc = fake.run(
        "--region", REGION, "--bucket", "pkg-bucket", "--regions", REGION,
        "--package", str(fake.package), "--viewer", "--days", "7", extra_env=_CLOUDSHELL,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout
    assert all("--profile" not in c for c in fake.calls())
    # The "issue again" command must not be left with an empty --profile.
    again = next(x for x in proc.stdout.splitlines() if x.startswith("다시 발급:"))
    assert "--profile" not in again and f"--region {REGION}" in again


# ── Optional hardening: --external-id, --org-id, --narrow-viewer ─────────────
#    All three are opt-in. Without them, no new parameter is passed to Stack A
#    and the output is identical to what it was before the feature existed.

_EXT = "nettopo-ext-0a1b2c3d"
_ORG = "o-abcdef1234"
_HARDENING_KEYS = ("MemberExternalId", "ViewerOrigin", "ViewerDistributionId")


def _deploy_parameters(fake: Fake, stack: str) -> list[dict[str, str]]:
    """The --parameter-overrides of each deploy call for that stack, as dicts in
    call order."""
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
    """The `--parameter-overrides` and `--parameters` lines of the two printed
    Stack B commands: the one-line CLI form and the StackSet creation."""
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
    """Opt-in: a first install without the flags, a re-run, and a `--viewer` run
    all pass the new parameters nowhere."""
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
    assert len(a_calls) == 3, "updated Stack A again without --narrow-viewer"
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
    )  # tells someone using the console which fields to fill in
    # The value is printed in those two commands only, never repeated in error or
    # progress lines.
    assert (proc.stdout + proc.stderr).count(_EXT) == 2
    _assert_profile_region_on_every_call(fake)
    _assert_no_full_account_id(fake, proc)


def test_rerun_without_external_id_keeps_it_on_stack_a_and_in_the_printed_commands(
    tmp_path: Path,
) -> None:
    """A re-run that omits it passes no MemberExternalId, so the prior value
    remains and the printed Stack B commands still carry it.

    Were it missing from the output, source accounts added afterwards would get a
    role with no external-ID condition, a quiet weakening.
    """
    fake = Fake(tmp_path)
    assert _install(fake, "--external-id", _EXT).returncode == 0
    again = fake.run(*_base(), "--package", str(fake.package))
    assert again.returncode == 0, again.stderr
    last = _deploy_parameters(fake, "nettopo-collector")[-1]
    assert "MemberExternalId" not in last, (
        "omitting it overwrote the value, erasing what was there"
    )
    state = json.loads(fake.state.read_text())
    assert state["stacks"]["nettopo-collector"]["params"]["MemberExternalId"] == _EXT
    direct, stackset = _stack_b_commands(again.stdout)
    assert f" ExternalId={_EXT} " in direct
    assert _stackset_parameters(stackset)["ExternalId"] == _EXT


def test_org_id_goes_only_into_the_printed_stack_b_commands(tmp_path: Path) -> None:
    """Stack A has no organisation parameter: PrincipalOrgId appears only in the
    two printed Stack B commands."""
    fake = Fake(tmp_path)
    proc = _install(fake, "--org-id", _ORG, "--external-id", _EXT)
    assert proc.returncode == 0, proc.stderr
    assert not any(
        _ORG in a
        for c in fake.calls()
        if c[:2] == ["cloudformation", "deploy"]
        for a in c
    ), "the organisation ID reached the Stack A deployment"
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
    """1224 characters, the template's maximum. macOS treats `{2,1224}` as an
    invalid regex and rejects every value, so length is checked separately."""
    fake = Fake(tmp_path)
    ext = "a-" + "b" * 1222
    proc = _install(fake, "--external-id", ext, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    deploy = next(c for c in fake.calls() if c[:2] == ["cloudformation", "deploy"])
    assert f"MemberExternalId={ext}" in deploy


@needs_openssl
def test_narrow_viewer_updates_stack_a_with_stack_c_outputs(tmp_path: Path) -> None:
    """After deploying Stack C, Stack A is updated once more with that stack's
    ViewerDomain and DistributionId, and no other parameter is passed."""
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
    assert (
        params["SourceLocation"] == "pkg-bucket/nettopo-source.zip"
    )  # prior value kept
    assert params["TargetRegions"] == REGION
    assert "https://d1234.cloudfront.net/index.html?Expires=" in proc.stdout
    # Omitted on a re-run, nothing is passed, so the narrowed value stays.
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
    """If the outputs cannot be read, the script stops rather than updating
    Stack A with empty values: an empty ViewerDistributionId means any
    distribution in this account."""
    fake = Fake(tmp_path, {"viewer_no_distribution_id": True})
    proc = _install(fake, "--viewer", "--days", "7", "--narrow-viewer")
    assert proc.returncode == 1
    line = next(x for x in proc.stderr.splitlines() if "실패:" in x)
    assert (
        line.startswith("🔴 6단계 스택 C 출력 읽기 실패:") and "DistributionId" in line
    )
    assert len(_deploy_parameters(fake, "nettopo-collector")) == 1, (
        "updated Stack A with empty values"
    )
    assert "index.html?Expires=" not in proc.stdout
