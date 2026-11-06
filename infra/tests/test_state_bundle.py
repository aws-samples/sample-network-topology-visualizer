"""상태 단 Lambda 꾸러미 — `infra/build-state-lambda.sh` + `infra/state-lambda/requirements.lock` (클린룸 C5).

🔴 **이 검사가 지키는 것 = 「고객 PC 에서 꾸러미가 만들어진다」와 ADR 0004 D2-1 조건 1·5.**
- 조건 1: zip 에 든 boto3·botocore 가 **잠금 파일에 적힌 그것**이고, 그 버전이 zip 안에 기록된다.
  boto3 만 고정하면 botocore 가 범위로 끌려와 설치하는 날의 최신이 들어간다(실측 2026-10-01:
  boto3 1.43.93 고정 → botocore 1.43.105). `awsguard.py` 가 훅을 거는 것은 botocore 다.
- 조건 5: 같은 입력 → 같은 바이트 → 같은 해시 키.

스크립트를 **진짜로 돌린다.** PyPI 대신 여기서 만든 가짜 휠 폴더를 쓴다(`PIP_NO_INDEX`·`PIP_FIND_LINKS`)
— 네트워크 없이 돌고, 가짜 버전(9.9.9)이라 실제 번들과 헷갈리지 않는다.

🔵 **재지 않는 것**: 진짜 boto3 로 만든 꾸러미가 Lambda 런타임에서 임포트되는지 · 맥과 리눅스에서 해시가
같은지 — 둘 다 2026-10-01 에 따로 쟀다(Lambda 공식 python3.12 arm64 이미지 · Amazon Linux 2023,
`infra/README.md` 「상태 단」).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parent.parent
BUILD = INFRA / "build-state-lambda.sh"
LOCK = INFRA / "state-lambda" / "requirements.lock"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+) --hash=sha256:([0-9a-f]{64})$")


def _python_with_pip() -> str | None:
    # 🔵 uv 로 만든 가상환경에는 pip 이 없다 — 스크립트는 pip 을 쓰므로 pip 이 있는 파이썬을 고른다.
    for cand in (sys.executable, shutil.which("python3")):
        if (
            cand
            and subprocess.run(
                [cand, "-m", "pip", "--version"], capture_output=True
            ).returncode
            == 0
        ):
            return cand
    return None


PY = _python_with_pip()
needs_pip = pytest.mark.skipif(
    PY is None, reason="pip 이 있는 파이썬이 없다 — 꾸러미 빌드를 재지 못했다"
)


def _wheel(
    dest: Path,
    name: str,
    version: str,
    files: dict[str, str],
    *,
    tag: str = "py3-none-any",
    requires: tuple[str, ...] = (),
) -> str:
    """최소 휠을 만들고 sha256 을 돌려준다(pip 이 설치할 수 있는 모양 — METADATA·WHEEL·RECORD)."""
    dist = f"{name}-{version}.dist-info"
    meta = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n" + "".join(
        f"Requires-Dist: {r}\n" for r in requires
    )
    body = {
        **files,
        f"{dist}/METADATA": meta,
        f"{dist}/WHEEL": f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {tag}\n",
    }
    body[f"{dist}/RECORD"] = "".join(f"{k},,\n" for k in body) + f"{dist}/RECORD,,\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in body.items():
            z.writestr(k, v)
    path = dest / f"{name}-{version}-{tag}.whl"
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(
    tmp: Path, *, boto3_file_version: str = "9.9.9", drop: str = "", bad_hash: str = ""
) -> Path:
    """가짜 휠 폴더 + 잠금 파일. boto3 는 botocore 를 요구하고, `fakec` 는 **Lambda 용 컴파일 휠만** 있다."""
    wh = tmp / "wheelhouse"
    wh.mkdir()
    pins = {
        "boto3": _wheel(
            wh,
            "boto3",
            "9.9.9",
            {"boto3/__init__.py": f"__version__ = '{boto3_file_version}'\n"},
            requires=("botocore==9.9.9",),
        ),
        "botocore": _wheel(
            wh, "botocore", "9.9.9", {"botocore/__init__.py": "__version__ = '9.9.9'\n"}
        ),
        "fakec": _wheel(
            wh,
            "fakec",
            "1.0",
            {"fakec/__init__.py": ""},
            tag="cp312-cp312-manylinux2014_aarch64",
        ),
    }
    lines = []
    for name, sha in pins.items():
        if name == drop:
            continue
        ver = "1.0" if name == "fakec" else "9.9.9"
        lines.append(
            f"{name}=={ver} --hash=sha256:{'0' * 64 if name == bad_hash else sha}"
        )
    lock = tmp / "requirements.lock"
    lock.write_text("# 검사용 가짜 잠금\n" + "\n".join(lines) + "\n")
    return lock


def _build(tmp: Path, lock: Path, out: str = "out") -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp),
        "TMPDIR": str(tmp),
        "PY": PY or "python3",
        "LOCK": str(lock),
        "OUT": str(tmp / out),
        "PIP_NO_INDEX": "1",
        "PIP_FIND_LINKS": str(tmp / "wheelhouse"),
        "PIP_CONFIG_FILE": os.devnull,
    }
    return subprocess.run(
        ["bash", str(BUILD)], env=env, capture_output=True, text=True, timeout=300
    )


def _only_zip(folder: Path) -> Path:
    zips = list(folder.glob("*.zip"))
    assert len(zips) == 1, zips
    return zips[0]


def test_lock_pins_every_package_with_a_hash_including_botocore() -> None:
    lines = [
        ln for ln in LOCK.read_text().splitlines() if ln and not ln.startswith("#")
    ]
    bad = [ln for ln in lines if not PIN.match(ln)]
    assert not bad, f"== 고정 + sha256 해시가 아닌 줄: {bad}"
    names = {PIN.match(ln).group(1) for ln in lines}
    assert {"boto3", "botocore"} <= names, (
        "botocore 가 잠금에 없다 — boto3 가 범위로 끌어와 설치하는 날의 최신이 번들된다(검사한 그것이 아니다)"
    )
    for dep in ("jmespath", "python-dateutil", "s3transfer", "six", "urllib3"):
        assert dep in names, (
            f"{dep} 가 잠금에 없다 — 해시 검사 모드에서 빌드가 실패한다"
        )


@needs_pip
def test_build_bundles_exactly_the_locked_versions_reproducibly(tmp_path: Path) -> None:
    lock = _fixture(tmp_path)
    first = _build(tmp_path, lock, "out1")
    assert first.returncode == 0, first.stdout[-800:] + first.stderr[-800:]
    second = _build(tmp_path, lock, "out2")
    assert second.returncode == 0, second.stderr[-800:]
    z1, z2 = _only_zip(tmp_path / "out1"), _only_zip(tmp_path / "out2")
    assert z1.read_bytes() == z2.read_bytes(), (
        "같은 입력에서 다른 바이트 — 해시 키가 흔들린다(조건 5)"
    )

    sha = hashlib.sha256(z1.read_bytes()).hexdigest()
    assert z1.name == f"state-observer-{sha[:12]}.zip", (
        "파일 이름의 해시가 내용 해시가 아니다(조건 5)"
    )
    assert f"lambda/state-observer-{sha[:12]}.zip" in first.stdout, (
        "스크립트가 찍는 S3 키가 파일과 다르다"
    )

    with zipfile.ZipFile(z1) as z:
        names = z.namelist()
        assert json.loads(z.read("bundled-versions.json")) == {
            "boto3": "9.9.9",
            "botocore": "9.9.9",
        }
        infos = z.infolist()
    for must in (
        "handler.py",
        "signer.py",
        "nettopo/cli.py",
        "nettopo/awsguard.py",
        "boto3/__init__.py",
        "botocore/__init__.py",
        "fakec/__init__.py",
    ):
        assert must in names, f"{must} 가 zip 루트에 없다"
    junk = [
        n
        for n in names
        if ".dist-info/" in n
        or "__pycache__" in n
        or n.endswith(".pyc")
        or n.startswith("bin/")
    ]
    assert not junk, f"설치 부산물이 zip 에 남았다(해시를 흔든다): {junk[:5]}"
    assert names == sorted(names), (
        "zip 목록이 정렬돼 있지 않다 — 파일 시스템 순서에 따라 해시가 흔들린다"
    )
    assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}, (
        "zip 시각이 고정돼 있지 않다"
    )
    assert {i.external_attr >> 16 for i in infos} == {0o100644}, (
        "zip 권한이 고정돼 있지 않다(umask 따라 흔들린다)"
    )


@needs_pip
def test_build_fails_when_the_installed_version_differs_from_the_lock(
    tmp_path: Path,
) -> None:
    r = _build(tmp_path, _fixture(tmp_path, boto3_file_version="9.9.8"))
    assert r.returncode != 0, (
        "설치본 9.9.8 을 잠금 9.9.9 로 기록했다 — 기록이 실물과 갈린다"
    )
    assert "기록이 실물과 갈린다" in r.stdout + r.stderr
    assert not list((tmp_path / "out").glob("*.zip")), "실패했는데 zip 이 남았다"


@needs_pip
@pytest.mark.parametrize(
    ("drop", "bad_hash", "why"),
    [
        ("botocore", "", "boto3 가 끌어오는 botocore 가 잠금에 없다"),
        ("", "botocore", "botocore 휠 바이트가 잠금 해시와 다르다"),
    ],
)
def test_build_refuses_anything_the_lock_does_not_pin(
    tmp_path: Path, drop: str, bad_hash: str, why: str
) -> None:
    r = _build(tmp_path, _fixture(tmp_path, drop=drop, bad_hash=bad_hash))
    assert r.returncode != 0, f"{why} — 그런데 꾸러미가 만들어졌다"
    assert not list((tmp_path / "out").glob("*.zip"))


@needs_pip
def test_build_fetches_wheels_for_lambda_not_for_this_machine(tmp_path: Path) -> None:
    # `fakec` 는 cp312 · manylinux2014_aarch64 휠만 있다. 로컬 기준으로 받으면(맥·x86 리눅스) 찾지 못해 실패한다.
    r = _build(tmp_path, _fixture(tmp_path))
    assert r.returncode == 0, (
        "Lambda 용 휠을 받지 못했다 — 로컬 플랫폼 기준으로 받고 있다: "
        + r.stderr[-600:]
    )
    with zipfile.ZipFile(_only_zip(tmp_path / "out")) as z:
        assert "fakec/__init__.py" in z.namelist()


def test_build_needs_only_python_and_pip() -> None:
    # 클린룸 C5 — 새 clone·CloudShell 에 없는 것을 전제하면 여기서 멈춘다.
    code = "\n".join(
        ln for ln in BUILD.read_text().splitlines() if not ln.lstrip().startswith("#")
    )
    for absent, why in (
        (r"\buv\b", "uv"),
        (r"\.venv/", "리포 안 가상환경"),
        (r"^\s*\(?cd [^\n]*&& zip\b|\bzip -", "zip 명령(AL2023 기본 이미지에 없다)"),
        (r"\bshasum\b", "shasum(AL2023 에 없다)"),
        (r"mktemp -d -t\b", "`mktemp -t 이름`(GNU 는 X 없는 템플릿을 거부한다)"),
    ):
        assert not re.search(absent, code, re.M), f"꾸러미 빌드가 {why} 를 전제한다"
