#!/usr/bin/env bash
# Builds the deployment zip for the state-tier Lambda functions.
#
#   ./infra/build-state-lambda.sh                 # out/state-observer-<sha12>.zip
#
# All it needs is `python3`, `pip` and access to PyPI. It deliberately avoids
# virtualenvs, `uv` and the `zip` command: assuming tools that a customer laptop
# or CloudShell may not have would stop a fresh clone right here. The local
# Python version does not affect the result, because wheels are downloaded for
# the Lambda runtime's Python and platform.
#
# boto3 and botocore are bundled rather than taken from the runtime. AWS upgrades
# the runtime copy without notice, and when it does, the contract of the two
# botocore events that `engine/src/nettopo/awsguard.py` hooks
# (`before-parameter-build` and `before-call`) is no longer the one we verified,
# so the read-only guard would loosen with nothing to report it. The bundled
# versions are therefore recorded inside the zip and checked when the handler
# starts (`handler.verify_bundle`). What gets bundled is pinned, with hashes, in
# `infra/state-lambda/requirements.lock`.
#
# The content hash goes in the key because a Lambda whose source is an S3 zip does
# not pick up new code while `S3Key` stays the same: CloudFormation sees no
# change. Different code therefore has to mean a different key. For the same
# input to produce the same hash, timestamps and permissions are fixed and the
# file list is sorted.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-python3}
OUT=${OUT:-out}
LOCK=${LOCK:-infra/state-lambda/requirements.lock}
PY_VERSION=${PY_VERSION:-3.12}
# Change this together with `StateFunction.Architectures` in Stack A
# (arm64 there, aarch64 here).
LAMBDA_PLATFORM=${LAMBDA_PLATFORM:-manylinux2014_aarch64}

if ! command -v "$PY" > /dev/null; then
  echo "🔴 $PY 가 없다 — python3 를 설치하거나 PY=<파이썬 경로> 로 준다" >&2
  exit 2
fi
if ! "$PY" -m pip --version > /dev/null 2>&1; then
  echo "🔴 $PY 에 pip 이 없다 — '$PY -m ensurepip --user' 또는 OS 패키지(python3-pip)로 설치한다" >&2
  exit 2
fi

# `mktemp -t name` only works on macOS; GNU mktemp rejects a template without
# trailing Xs, so the template is spelled out.
BUILD=$(mktemp -d "${TMPDIR:-/tmp}/nettopo-state-lambda.XXXXXX")
trap 'rm -rf "$BUILD"' EXIT
PKG="$BUILD/pkg"

echo "== 번들 설치 ($LOCK · cp$PY_VERSION · $LAMBDA_PLATFORM) =="
# Download for Lambda, not for this machine (`--platform`, `--python-version`,
# `--only-binary`). Today every dependency is a pure-Python wheel so the result
# would be the same either way, but the day a compiled dependency arrives, these
# flags are what keeps a local `.so` out of the zip; without them the failure
# would only show up on the first invocation after deployment.
#
# The lock file carries hashes, so pip runs in hash-checking mode and fails if
# anything not in the lock is pulled in or a wheel's bytes differ. That is what
# stops botocore from being resolved by range.
#
# Download and install are separate steps because older pip (21.x, the one
# shipped with macOS's system Python 3.9) checks a wheel's `Requires-Python`
# against the local interpreter rather than `--python-version` during `install`
# and refuses (`boto3 requires a different Python: 3.9.6 not in '>=3.10'`).
# `download` checks it against the target, so the target-based check happens
# there, and the install only consumes the files already downloaded.
WHEELS="$BUILD/wheels"
TARGET_FLAGS=(--only-binary=:all: --platform "$LAMBDA_PLATFORM" --python-version "$PY_VERSION" --implementation cp)
"$PY" -m pip download --quiet --disable-pip-version-check \
  --require-hashes "${TARGET_FLAGS[@]}" -d "$WHEELS" -r "$LOCK"
"$PY" -m pip install --quiet --disable-pip-version-check --no-compile \
  --no-index --find-links "$WHEELS" --no-deps --ignore-requires-python \
  --require-hashes "${TARGET_FLAGS[@]}" --target "$PKG" -r "$LOCK"
# The engine is our own pure-Python source, so it is copied rather than
# installed: it requires Python 3.12 or newer, and pip would refuse to install it
# wherever the local interpreter is older (CloudShell, for instance).
cp -R engine/src/nettopo "$PKG/nettopo"
# Both modules sit flat at the zip root, because the Lambda handler names are
# `handler.handler` and `signer.handler`. The signer ships in the same zip so
# that it can import key names such as `CONTROL_KEY` from `handler.py` instead of
# keeping a second copy, and so one hashed key points both functions at the same
# revision of the code.
cp infra/state-lambda/handler.py "$PKG/handler.py"
cp infra/state-lambda/signer.py "$PKG/signer.py"

echo "== 번들 버전 기록 · zip (재현 가능하게) =="
"$PY" - "$PKG" "$LOCK" "$OUT" << 'PY' | tee "$BUILD/result"
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

pkg, lock, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])

# Read the versions out of the installed tree, since `__version__` in
# `<package>/__init__.py` is exactly what `verify_bundle` compares at runtime.
# Importing the packages instead would prove nothing: the local interpreter may
# differ from Lambda's, so whether an import works here says nothing about there.
pins = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", lock.read_text(), re.M))
versions = {}
for name in ("boto3", "botocore"):
    src = (pkg / name / "__init__.py").read_text()
    found = re.findall(r"^__version__ = ['\"]([^'\"]+)['\"]", src, re.M)
    if len(found) != 1:
        raise SystemExit(f"{name}/__init__.py 에서 __version__ 을 한 번 찾지 못했다: {found}")
    if found[0] != pins.get(name):
        raise SystemExit(f"{name} 설치본 {found[0]} ≠ 잠금 {pins.get(name)} — 기록이 실물과 갈린다")
    versions[name] = found[0]
(pkg / "bundled-versions.json").write_text(json.dumps(versions, indent=2, sort_keys=True) + "\n")
print(json.dumps(versions, sort_keys=True))

# Drop bytecode, install metadata and console scripts: the handlers never use
# them, and they would only destabilise the hash and enlarge the zip.
for junk in [*pkg.rglob("__pycache__"), *pkg.glob("*.dist-info"), pkg / "bin"]:
    if junk.is_dir():
        shutil.rmtree(junk)
for pyc in pkg.rglob("*.pyc"):
    pyc.unlink()

# Fixed timestamp of 1980-01-01, mode 0644 and a sorted file list: the same input
# then produces the same bytes, and the same hash, on any operating system.
files = sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file())
tmp = pkg.parent / "bundle.zip"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for rel in files:
        info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(info, (pkg / rel).read_bytes(), compresslevel=9)
sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
out.mkdir(parents=True, exist_ok=True)
dest = out / f"state-observer-{sha[:12]}.zip"
shutil.move(str(tmp), dest)
print(f"ZIP={dest}\nSHA={sha}\nFILES={len(files)}\nBYTES={dest.stat().st_size}")
PY

ZIP=$(sed -n 's/^ZIP=//p' "$BUILD/result")
SHA=$(sed -n 's/^SHA=//p' "$BUILD/result")
KEY="lambda/state-observer-${SHA:0:12}.zip"
cat << INFO

== 완료 ==
zip     : $ZIP ($(sed -n 's/^FILES=//p' "$BUILD/result")개 파일 · $(sed -n 's/^BYTES=//p' "$BUILD/result") B)
sha256  : $SHA
S3 키   : $KEY   ← 스택 파라미터 StateLambdaSourceKey 에 이 값을 넣는다

다음 (프로필·리전은 항상 명시한다):
  1) 소스 버킷에 올린다  — SourceLocation 의 버킷과 같은 버킷을 쓴다
  2) 스택 A 를 업데이트하고 StateLambdaSourceKey 에 위 키를 넣는다

🔴 키가 그대로면 코드가 갱신되지 않는다(CFN 이 변경을 못 본다) — 해시가 든 이 키를 그대로 쓴다.
INFO
