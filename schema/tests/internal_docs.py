"""Documents that exist only in the internal repository and are left out of the
public tree.

A few tests read a table in one of these documents as the source of truth and
compare it against the code or the schema. The documents are absent from the
public tree, so those comparisons run **only when the document is there** and
otherwise record a reason and skip (`read_or_skip`). A document is read inside
the test that uses it rather than at import time, so that a missing one cannot
take the rest of the file down with it.

This list is the only thing that decides when a comparison may be skipped. If a
document disappeared from the internal repository, that comparison would quietly
turn into a skip, so `scripts/tests/test_internal_docs_present.py` checks that
they are all present there (the public tree has no `scripts/`, so that guard does
not ship with it). Documents that ship to everyone, such as the install guide, do
not belong in this list: a missing one should fail.

On imports: pytest adds `schema/tests` to the path when the tests in this folder
run. Tests in other folders (engine, infra, scripts) add this folder to
`sys.path` from their own conftest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Paths relative to the repository root, each with the reason recorded on a skip
INTERNAL_DOCS: dict[str, str] = {
    "docs/adr/0006-observation-schema.md": "docs/adr/0006 is an internal design document and is not in the public tree",
    "docs/adr/0008-flow-verdict.md": "docs/adr/0008 is an internal design document and is not in the public tree",
    "docs/adr/0009-optional-evidence-sources.md": "docs/adr/0009 is an internal design document and is not in the public tree",
    "docs/contract-seams.md": "docs/contract-seams.md is an internal design document and is not in the public tree",
    "infra/README.md": "infra/README.md records deployments in our development account and is not in the public tree",
}


def skip_if_missing(rel: str) -> Path:
    """The path of a listed internal document. If it is absent, record the reason
    and skip that test."""
    if rel not in INTERNAL_DOCS:
        raise KeyError(
            f"{rel} is not in INTERNAL_DOCS; a document outside the list is never skipped"
        )
    path = ROOT / rel
    if not path.exists():
        pytest.skip(INTERNAL_DOCS[rel])
    return path


def read_or_skip(rel: str) -> str:
    return skip_if_missing(rel).read_text(encoding="utf-8")
