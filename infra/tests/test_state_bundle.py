"""The state-tier Lambda bundle: `infra/build-state-lambda.sh` together with
`infra/state-lambda/requirements.lock`.

These tests protect two things: that the bundle can be built on a customer's own
machine, and that what ends up inside it is pinned.

- The boto3 and botocore in the zip are the ones named in the lock file, and
  their versions are recorded inside the zip. Pinning boto3 alone is not enough:
  botocore would be resolved by range and whatever is newest that day gets
  bundled (we measured boto3 1.43.93 pulling in botocore 1.43.105), and botocore
  is what `awsguard.py` hooks.
- The same input produces the same bytes, and therefore the same hashed key.

The script is really executed. Instead of PyPI it installs from a fake wheelhouse
built here (`PIP_NO_INDEX`, `PIP_FIND_LINKS`), so the tests need no network, and
the fake version (9.9.9) cannot be confused with a real bundle.

Two things are deliberately out of scope here: whether a bundle built from the
real boto3 imports cleanly in the Lambda runtime, and whether macOS and Linux
produce the same hash. Both were checked separately against the official
python3.12 arm64 Lambda image and Amazon Linux 2023.
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
    # A virtualenv created by uv has no pip, and the script uses pip, so pick an
    # interpreter that has it.
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
    PY is None, reason="no interpreter with pip, so the bundle build is untested"
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
    """Build a minimal wheel pip can install (METADATA, WHEEL, RECORD) and
    return its sha256."""
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
    """A fake wheelhouse and lock file. boto3 requires botocore, and `fakec`
    exists only as a compiled wheel for the Lambda platform."""
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
    lock.write_text("# fake lock file for tests\n" + "\n".join(lines) + "\n")
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
    assert not bad, f"lines that are not an == pin with a sha256 hash: {bad}"
    names = {PIN.match(ln).group(1) for ln in lines}
    assert {"boto3", "botocore"} <= names, (
        "botocore is missing from the lock: boto3 would resolve it by range and "
        "bundle whatever is newest that day, not the version we verified"
    )
    for dep in ("jmespath", "python-dateutil", "s3transfer", "six", "urllib3"):
        assert dep in names, (
            f"{dep} is missing from the lock, so the build fails in hash-checking mode"
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
        "different bytes from the same input, so the hashed key is unstable"
    )

    sha = hashlib.sha256(z1.read_bytes()).hexdigest()
    assert z1.name == f"state-observer-{sha[:12]}.zip", (
        "the hash in the file name is not the hash of its contents"
    )
    assert f"lambda/state-observer-{sha[:12]}.zip" in first.stdout, (
        "the S3 key the script prints does not match the file it produced"
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
        assert must in names, f"{must} is not at the zip root"
    junk = [
        n
        for n in names
        if ".dist-info/" in n
        or "__pycache__" in n
        or n.endswith(".pyc")
        or n.startswith("bin/")
    ]
    assert not junk, f"install leftovers in the zip destabilise the hash: {junk[:5]}"
    assert names == sorted(names), (
        "the zip listing is unsorted, so the hash follows file system order"
    )
    assert {i.date_time for i in infos} == {(1980, 1, 1, 0, 0, 0)}, (
        "zip timestamps are not fixed"
    )
    assert {i.external_attr >> 16 for i in infos} == {0o100644}, (
        "zip permissions are not fixed, so they vary with the umask"
    )


@needs_pip
def test_build_fails_when_the_installed_version_differs_from_the_lock(
    tmp_path: Path,
) -> None:
    r = _build(tmp_path, _fixture(tmp_path, boto3_file_version="9.9.8"))
    assert r.returncode != 0, (
        "an installed 9.9.8 was recorded as the locked 9.9.9, so the record no "
        "longer matches what is bundled"
    )
    assert "기록이 실물과 갈린다" in r.stdout + r.stderr
    assert not list((tmp_path / "out").glob("*.zip")), "the build failed yet left a zip"


@needs_pip
@pytest.mark.parametrize(
    ("drop", "bad_hash", "why"),
    [
        ("botocore", "", "the botocore that boto3 requires is not in the lock"),
        ("", "botocore", "the botocore wheel's bytes differ from the locked hash"),
    ],
)
def test_build_refuses_anything_the_lock_does_not_pin(
    tmp_path: Path, drop: str, bad_hash: str, why: str
) -> None:
    r = _build(tmp_path, _fixture(tmp_path, drop=drop, bad_hash=bad_hash))
    assert r.returncode != 0, f"{why}, yet the bundle was built anyway"
    assert not list((tmp_path / "out").glob("*.zip"))


@needs_pip
def test_build_fetches_wheels_for_lambda_not_for_this_machine(tmp_path: Path) -> None:
    # `fakec` exists only as a cp312 manylinux2014_aarch64 wheel, so downloading
    # for the local platform (macOS, x86 Linux) finds nothing and fails.
    r = _build(tmp_path, _fixture(tmp_path))
    assert r.returncode == 0, (
        "the Lambda wheels were not fetched, so the download is using the local "
        "platform: " + r.stderr[-600:]
    )
    with zipfile.ZipFile(_only_zip(tmp_path / "out")) as z:
        assert "fakec/__init__.py" in z.namelist()


def test_build_needs_only_python_and_pip() -> None:
    # Assuming a tool that a fresh clone or CloudShell does not have would stop
    # the build right there.
    code = "\n".join(
        ln for ln in BUILD.read_text().splitlines() if not ln.lstrip().startswith("#")
    )
    for absent, why in (
        (r"\buv\b", "uv"),
        (r"\.venv/", "a virtualenv inside the repository"),
        (
            r"^\s*\(?cd [^\n]*&& zip\b|\bzip -",
            "the zip command, absent from the AL2023 image",
        ),
        (r"\bshasum\b", "shasum, absent from AL2023"),
        (r"mktemp -d -t\b", "`mktemp -t name`, which GNU mktemp rejects"),
    ):
        assert not re.search(absent, code, re.M), f"the bundle build assumes {why}"
