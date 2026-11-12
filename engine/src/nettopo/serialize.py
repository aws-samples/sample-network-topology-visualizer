"""Convert collected data to JSON and calculate comparison SHA-256
hashes.

Store the content digest in topology JSON at run.content_digest. The
renderer uses it to check that observations match the current topology.
cli.py prints the collection comparison hash to the terminal.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .model import Snapshot

# Use a fixed value to exclude collection time from comparisons.
_FIXED_TIME = "<collected_at>"


def to_json(snapshot: Snapshot) -> str:
    """Return a topology JSON string with its content digest."""
    document = snapshot.to_document()
    document["run"]["content_digest"] = content_digest(document)
    return dumps(document)


def dumps(document: dict[str, Any]) -> str:
    """Return JSON with sorted keys and two-space indentation."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def digest(text: str) -> str:
    """Return the SHA-256 hash of UTF-8 text as a hexadecimal string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    """Return JSON with sorted keys and no separator whitespace."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_excluding_collected_at(text: str) -> str:
    """Return a whole-topology comparison hash excluding collection
    time.

    Replace run.collected_at with a fixed value before hashing the
    entire
    JSON document. Include scope and coverage in the comparison.
    """
    document = json.loads(text)
    if "run" in document and isinstance(document["run"], dict):
        document["run"]["collected_at"] = _FIXED_TIME
    return digest(_canonical(document))


def content_digest(document: dict[str, Any]) -> str:
    """Calculate the content digest of resource configuration and links.

    Hash schema_version, run.tool_version, nodes, and edges. Exclude
    collection
    time, scope, and coverage. Equal included fields produce the same
    digest
    even when collection time or coverage differs.
    """
    run = document.get("run")
    content = {
        "schema_version": document.get("schema_version"),
        "tool_version": run.get("tool_version") if isinstance(run, dict) else None,
        "nodes": document.get("nodes"),
        "edges": document.get("edges"),
    }
    canonical = _canonical(content)
    return "sha256:" + digest(canonical)
