"""Global test setup: keep real credentials, profiles and Regions out of the tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# `internal_docs`, the list of documents that are absent from the public tree so
# their cross-checks are skipped, lives in schema/tests.
SCHEMA_TESTS = Path(__file__).resolve().parents[2] / "schema" / "tests"
if str(SCHEMA_TESTS) not in sys.path:
    sys.path.insert(0, str(SCHEMA_TESTS))


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    """Run without any real credentials or profile.

    A developer shell may well have `AWS_PROFILE` set, possibly to an admin
    profile. If it is left in place the tests try to resolve that profile, and
    because they do so silently the assumption that they run without
    credentials stops holding. The shell's default Region (`AWS_REGION`) is
    cleared too, so that the Region under test can only come from the code.
    """
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "dummy")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")  # pragma: allowlist secret
    monkeypatch.setenv("AWS_SESSION_TOKEN", "dummy")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
