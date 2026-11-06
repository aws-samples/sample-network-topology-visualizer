#!/usr/bin/env bash
# 상태 단 Lambda 배포 zip 을 만든다 (ADR 0004 D2-1 조건 1·5).
#
#   ./infra/build-state-lambda.sh                 # out/state-observer-<sha12>.zip
#
# 🔵 **필요한 것 = `python3` + `pip` + 인터넷(PyPI) 뿐이다**(클린룸 C5). 가상환경·`uv`·`zip` 명령은
#    쓰지 않는다 — 고객 PC·CloudShell 에 없는 것을 전제하면 새 clone 에서 여기서 멈춘다.
#    로컬 파이썬 버전은 결과에 영향이 없다(휠을 Lambda 의 파이썬·플랫폼 기준으로 받는다).
#
# 🔴 **조건 1 — boto3·botocore 를 번들한다.** 런타임 제공 boto3 를 쓰면 AWS 가 예고 없이 올리고,
#    그때 `engine/src/nettopo/awsguard.py` 가 훅을 거는 botocore 이벤트 2개
#    (`before-parameter-build`·`before-call`)의 계약이 우리가 검사한 그것이 아니게 된다 —
#    읽기 전용 방어선(불변식 ①)이 **조용히** 헐거워진다. 그래서 버전을 zip 안에 기록하고
#    핸들러가 시작할 때 대조한다(`handler.verify_bundle` · 검사 3개 · 브레이크 `번들검사우회`).
#    무엇을 번들하는지는 `infra/state-lambda/requirements.lock` 이 정한다(전부 == 고정 + 해시).
#
# 🔴 **조건 5 — 키에 해시를 넣는다.** Lambda 소스가 S3 zip 이면 `S3Key` 가 그대로일 때 코드가
#    갱신되지 않는다(CFN 이 변경을 못 본다). 파일 이름에 내용 해시를 넣어 **다른 코드 = 다른 키**로
#    만든다. 같은 입력이면 같은 해시가 나와야 하므로 시각·권한을 고정하고 목록을 정렬해 담는다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-python3}
OUT=${OUT:-out}
LOCK=${LOCK:-infra/state-lambda/requirements.lock}
PY_VERSION=${PY_VERSION:-3.12}
# 🔴 스택 A 의 `StateFunction.Architectures` 와 **같이 바꿔야 한다**(arm64 ↔ aarch64).
LAMBDA_PLATFORM=${LAMBDA_PLATFORM:-manylinux2014_aarch64}

if ! command -v "$PY" > /dev/null; then
  echo "🔴 $PY 가 없다 — python3 를 설치하거나 PY=<파이썬 경로> 로 준다" >&2
  exit 2
fi
if ! "$PY" -m pip --version > /dev/null 2>&1; then
  echo "🔴 $PY 에 pip 이 없다 — '$PY -m ensurepip --user' 또는 OS 패키지(python3-pip)로 설치한다" >&2
  exit 2
fi

# 🔵 `mktemp -t 이름` 은 macOS 에서만 된다(GNU 는 X 가 없으면 거부한다) — 템플릿을 직접 준다.
BUILD=$(mktemp -d "${TMPDIR:-/tmp}/nettopo-state-lambda.XXXXXX")
trap 'rm -rf "$BUILD"' EXIT
PKG="$BUILD/pkg"

echo "== 번들 설치 ($LOCK · cp$PY_VERSION · $LAMBDA_PLATFORM) =="
# 🔴 **맥이 아니라 Lambda 를 대상으로 받는다**(`--platform`·`--python-version`·`--only-binary`).
#    지금 의존성은 전부 순수 파이썬 휠이라 결과가 같지만, 컴파일된 의존성이 하나 들어오는 날
#    이 플래그가 없으면 로컬용 `.so` 가 zip 에 들어가 배포 후 첫 호출에서야 죽는다.
# 🔴 잠금 파일에 해시가 있으므로 pip 이 해시 검사 모드로 돈다 — 잠금에 없는 것이 딸려 오거나
#    휠 바이트가 다르면 **실패한다**(범위로 끌려오는 botocore 가 이것으로 막힌다).
# 🔴 **받기와 설치를 나눈다.** 옛 pip(21.x — macOS 기본 파이썬 3.9 에 딸린 것)은 `install` 에서
#    휠의 `Requires-Python` 을 `--python-version` 이 아니라 **로컬 파이썬**과 대조해 거부한다
#    (실측: `boto3 requires a different Python: 3.9.6 not in '>=3.10'`). `download` 는 대상(3.12)과
#    대조한다 → 받기에서 대상 기준으로 검사하고, 설치는 받은 파일만 쓰며 그 검사를 다시 하지 않는다.
WHEELS="$BUILD/wheels"
TARGET_FLAGS=(--only-binary=:all: --platform "$LAMBDA_PLATFORM" --python-version "$PY_VERSION" --implementation cp)
"$PY" -m pip download --quiet --disable-pip-version-check \
  --require-hashes "${TARGET_FLAGS[@]}" -d "$WHEELS" -r "$LOCK"
"$PY" -m pip install --quiet --disable-pip-version-check --no-compile \
  --no-index --find-links "$WHEELS" --no-deps --ignore-requires-python \
  --require-hashes "${TARGET_FLAGS[@]}" --target "$PKG" -r "$LOCK"
# 엔진은 우리 소스고 순수 파이썬이라 그대로 복사한다. 설치하지 않는 이유 = 엔진은 파이썬 3.12
# 이상을 요구해서 로컬 파이썬이 그보다 낮으면(CloudShell 등) pip 이 설치를 거부한다.
cp -R engine/src/nettopo "$PKG/nettopo"
# zip 루트에 평평하게 둔다 — Lambda 핸들러 이름이 `handler.handler` · `signer.handler` 다.
# 🔵 서명 함수도 **같은 zip** 이다 — 키 이름(`CONTROL_KEY` 등)을 `handler.py` 에서 가져오므로 한 벌이
#    둘로 갈라지지 않고, 해시 키 하나로 두 함수가 같은 코드 판을 가리킨다(조건 5).
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

# 🔴 기록은 **설치된 트리에서** 읽는다 — 런타임의 `verify_bundle` 이 대조하는 바로 그 값
# (`<패키지>/__init__.py` 의 `__version__`)이다. 임포트하지 않는 이유 = 로컬 파이썬이 Lambda 와
# 다를 수 있다(여기서 임포트가 되는지는 Lambda 에서 도는지와 무관하다).
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

# 바이트코드·설치 메타데이터·실행 스크립트를 지운다(해시를 흔들고 zip 만 키운다 · 핸들러는 안 쓴다).
for junk in [*pkg.rglob("__pycache__"), *pkg.glob("*.dist-info"), pkg / "bin"]:
    if junk.is_dir():
        shutil.rmtree(junk)
for pyc in pkg.rglob("*.pyc"):
    pyc.unlink()

# 시각 1980-01-01 · 권한 0644 · 이름 정렬 → 같은 입력이면 어느 OS 에서든 같은 바이트, 같은 해시.
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
