"""테스트 전역 전제 — 실제 자격증명·프로필·리전이 새어 들어오지 못하게 막는다."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 내부 문서 목록(`internal_docs` — 공개 트리에 없는 문서는 그 대조만 건너뛴다)이 schema/tests 에 있다
SCHEMA_TESTS = Path(__file__).resolve().parents[2] / "schema" / "tests"
if str(SCHEMA_TESTS) not in sys.path:
    sys.path.insert(0, str(SCHEMA_TESTS))


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    """실제 자격증명·프로필을 쓰지 않는다.

    🔴 개발자 셸에는 `AWS_PROFILE`(관리자 프로필일 수 있다)이 박혀 있을 수 있다. 지우지 않으면 테스트가
    그 프로필을 해석하려 들고, 그 침묵이 「자격증명 없이 돈다」는 전제를 무력화한다.
    셸 기본 리전(`AWS_REGION`)도 지운다 — 리전이 코드에서 오는지 재기 위해서다.
    """
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "dummy")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")  # pragma: allowlist secret
    monkeypatch.setenv("AWS_SESSION_TOKEN", "dummy")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
