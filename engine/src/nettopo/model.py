"""Define topology data structures for collected resources and links.

Node holds resource details; Edge holds relationships between resources.
Scope defines the collection scope, and CoverageEntry stores collection
status by resource type. Snapshot.to_document() builds a topology
document
dictionary from these objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "2.13.0"

CONTAINER_TYPES = ("account", "region")

# Node types created only from other resources' collection results.
# external_ip: External target IP registered with ELB
# s3_bucket: Bucket specified as a CloudFront S3 origin
DERIVED_TYPES = ("external_ip", "s3_bucket")
DERIVABLE_TYPES = ("lambda_function", "external_ip", "s3_bucket")

# Use "global" in Node.id's region component and output region as null.
GLOBAL = "global"


def node_id(
    node_type: str,
    account_id: str,
    region: str | None = None,
    native_id: str | None = None,
) -> str:
    """Build a string for Node.id.

    Pattern: <type>/<account_id>[/<region>[/<native_id>]]
    """
    parts = [node_type, account_id]
    if region is not None:
        parts.append(region)
    if native_id is not None:
        if region is None:
            raise ValueError("native_id 를 쓰려면 region 이 있어야 한다")
        parts.append(native_id)
    return "/".join(parts)


def split_node_id(value: str) -> tuple[str, str, str | None, str | None]:
    """Split Node.id into (type, account_id, region, native_id).

    Return None for omitted region and native_id components. Return
    region as
    None when the region component is "global". Preserve "/" in
    native_id.
    """
    parts = value.split("/")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"노드 id 가 아니다: {value!r}")
    region = parts[2] if len(parts) > 2 else None
    if region == GLOBAL:
        region = None
    native_id = "/".join(parts[3:]) if len(parts) > 3 else None
    return parts[0], parts[1], region, native_id


def split_edge_id(value: str) -> tuple[str, str, str, str | None]:
    """Split Edge.id into (type, source, target, disc).

    disc distinguishes relationships between the same resource pair. For
    example, security group references use ingress or egress. Return
    None
    when disc is omitted.
    """
    parts = value.split("|")
    if len(parts) not in (3, 4) or not all(parts[:3]):
        raise ValueError(f"엣지 id 가 아니다: {value!r}")
    return parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else None


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    api: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "api": self.api, "message": self.message}


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    type: str
    account_id: str
    region: str | None = None
    native_id: str | None = None
    arn: str | None = None
    name: str | None = None
    parent_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    partial: bool = False
    partial_reasons: tuple[Reason, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "account_id": self.account_id,
            "region": self.region,
            "native_id": self.native_id,
            "arn": self.arn,
            "name": self.name,
            "parent_id": self.parent_id,
            "attributes": dict(self.attributes),
            "partial": self.partial,
        }
        if self.tags:
            doc["tags"] = dict(self.tags)
        if self.partial:
            doc["partial_reasons"] = [r.to_dict() for r in self.partial_reasons]
        return doc


@dataclass(frozen=True, slots=True)
class Edge:
    id: str
    type: str
    source: str
    target: str
    directed: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)
    partial: bool = False
    partial_reasons: tuple[Reason, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "target": self.target,
            "directed": self.directed,
            "attributes": dict(self.attributes),
            "partial": self.partial,
        }
        if self.partial:
            doc["partial_reasons"] = [r.to_dict() for r in self.partial_reasons]
        return doc


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    account_id: str
    region: str | None
    resource_type: str
    status: str
    reason: Reason | None = None
    item_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "region": self.region,
            "resource_type": self.resource_type,
            "status": self.status,
            "reason": self.reason.to_dict() if self.reason else None,
            "item_count": self.item_count,
        }

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.account_id, self.region or "", self.resource_type)


@dataclass(frozen=True, slots=True)
class Scope:
    account_ids: tuple[str, ...]
    regions: tuple[str, ...]
    resource_types: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_ids": list(self.account_ids),
            "regions": list(self.regions),
            "resource_types": list(self.resource_types),
        }


@dataclass(frozen=True, slots=True)
class Snapshot:
    collected_at: str
    tool_version: str
    scope: Scope
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    coverage: tuple[CoverageEntry, ...]

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run": {
                "collected_at": self.collected_at,
                "tool_version": self.tool_version,
            },
            "scope": self.scope.to_dict(),
            "nodes": [n.to_dict() for n in sorted(self.nodes, key=lambda n: n.id)],
            "edges": [e.to_dict() for e in sorted(self.edges, key=lambda e: e.id)],
            "coverage": [
                c.to_dict() for c in sorted(self.coverage, key=lambda c: c.sort_key)
            ],
        }
