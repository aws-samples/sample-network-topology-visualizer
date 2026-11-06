"""내부 리포에만 있는 문서 — 공개 트리(`scripts/make-public-tree.sh`)에서 빠진다.

테스트 몇 개는 이 문서의 표를 정본으로 읽어 코드·스키마와 대조한다. 공개 트리에는 문서가 없으므로
그 대조만 **있을 때만** 하고, 없으면 사유를 적고 건너뛴다(`read_or_skip`). 다른 테스트까지 같이
죽지 않게 문서는 import 때가 아니라 쓰는 테스트 안에서 읽는다.

🔴 이 목록은 「있을 때만」의 유일한 정본이다. 내부 리포에서 문서가 사라지면 대조가 조용히 「건너뜀」이
   되므로 `scripts/tests/test_internal_docs_present.py` 가 내부 리포에 전부 있는지 잰다(공개 트리에는
   scripts/ 가 없어 그 가드가 실리지 않는다). 고객 문서(설치 안내서 등)는 여기에 넣지 않는다 — 없으면 실패해야 한다.

임포트: schema/tests 는 이 폴더의 테스트가 돌 때 pytest 가 경로에 붙인다. 다른 폴더(engine·infra·scripts 테스트)는
각자 `sys.path` 에 이 폴더를 붙인다(conftest).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# 리포 루트 기준 경로 → 건너뛸 때 적는 사유
INTERNAL_DOCS: dict[str, str] = {
    "docs/adr/0006-observation-schema.md": "docs/adr/0006 은 내부 설계 문서라 공개 트리에 없다",
    "docs/adr/0008-flow-verdict.md": "docs/adr/0008 은 내부 설계 문서라 공개 트리에 없다",
    "docs/adr/0009-optional-evidence-sources.md": "docs/adr/0009 는 내부 설계 문서라 공개 트리에 없다",
    "docs/contract-seams.md": "docs/contract-seams.md 는 내부 설계 문서라 공개 트리에 없다",
    "infra/README.md": "infra/README.md 는 개발 계정 배포 기록이라 공개 트리에 없다",
}


def skip_if_missing(rel: str) -> Path:
    """목록에 있는 내부 문서의 경로. 없으면 사유를 적고 그 테스트를 건너뛴다."""
    if rel not in INTERNAL_DOCS:
        raise KeyError(
            f"{rel} 는 INTERNAL_DOCS 에 없다 — 목록 밖의 문서는 건너뛰지 않는다"
        )
    path = ROOT / rel
    if not path.exists():
        pytest.skip(INTERNAL_DOCS[rel])
    return path


def read_or_skip(rel: str) -> str:
    return skip_if_missing(rel).read_text(encoding="utf-8")
