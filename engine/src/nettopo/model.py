"""`schema/topology.schema.json` v2.8.0 의 파이썬 표현.

스키마가 정본이다 — 여기에 필드를 더하려면 스키마를 먼저 바꾼다(불변식 ③, 승인 2명).
정렬은 `to_document()` 한 곳에서만 한다: 여러 곳에서 정렬하면 한 곳을 빼먹었을 때
조용히 결정론이 깨진다(불변식 ②).

🔴 v2.0.0 은 `partial`·`directed`·`item_count` 를 **생략할 수 없게** 했다. JSON Schema 의
`default` 는 주석이고 검증기가 값을 채워 주지 않으므로, 생략을 허용하면 (1) 같은 사실에
두 가지 바이트 표현이 생기고(불변식 ②) (2) 엔진과 렌더러가 각자 기본값을 하드코딩해
계약 밖 지식이 양쪽으로 샌다(불변식 ③). 그래서 **거짓이어도 항상 적는다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "2.13.0"

# 컨테이너 2종은 수집 대상(scope.resource_types)이 아니라 계층 표현이다.
CONTAINER_TYPES = ("account", "region")
# 파생 노드(2.7.0) — 목록 API 가 없고 LB 의 등록 대상에서만 생긴다(`lb_targets.py`). 수집 요청의 단위가 아니다.
# 🔵 2.8.0(P2-6) — `lambda_function` 은 수집 종류가 됐다(`services/lambda_function.py`). LB 대상 해소는 그 종류를 수집하지 않았거나
#    못 읽었을 때만 예전처럼 최소 노드를 짓는다(`DERIVABLE_TYPES`) — 같은 id 라 둘이 겹치지 않는다.
#    `s3_bucket`(2.8.0 · P2-3) = CloudFront S3 원본에서만 짓는다(S3 는 수집하지 않는다 · `origins.py`).
DERIVED_TYPES = ("external_ip", "s3_bucket")
DERIVABLE_TYPES = ("lambda_function", "external_ip", "s3_bucket")

# 글로벌 리소스(Direct Connect 게이트웨이)의 id 리전 자리. 어느 리전에서 조회해도 같은 id 가
# 나와야 `_resolve` 의 「먼저 만난 것을 남긴다」가 중복 제거로 동작한다(ADR 0010). 노드의
# `region` 필드는 null 이다 — 이 리터럴은 id 문법 안에서만 쓴다.
GLOBAL = "global"


def node_id(
    node_type: str,
    account_id: str,
    region: str | None = None,
    native_id: str | None = None,
) -> str:
    """`<type>/<account_id>[/<region>[/<native_id>]]` — 스키마 node_id 패턴.

    글로벌 리소스는 `region` 자리에 `GLOBAL` 을 넘긴다(스키마 node.id description).
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
    """`node_id()` 의 역함수 — `(종류, 계정, 리전, 원래 id)`. 없는 조각은 `None`.

    🔴 **역함수를 여기 두는 이유**: 관측 이력(`history.py`)은 「이 대상이 어느 (계정,리전,종류)
    칸에 속하나」를 알아야 하는데, 이전 관측 문서에는 그 칸이 적혀 있지 않고 id 만 있다. 그
    문법을 다른 파일에서 다시 쪼개면 조립(`node_id`)과 분해가 조용히 갈라진다 — 그래서 같은
    파일에 두고 `test_model.py` 가 **왕복**(조립 → 분해 → 같은 값)을 잰다.
    """
    parts = value.split("/")
    if len(parts) < 2 or not all(parts):
        raise ValueError(f"노드 id 가 아니다: {value!r}")
    region = parts[2] if len(parts) > 2 else None
    # 글로벌 리소스(DXGW)는 id 의 리전 자리에 리터럴 `global` 이 있고 노드의 `region` 은 `null` 이다
    # (스키마 node.id description · ADR 0010) — 되돌릴 때 같은 값으로 접어야 왕복이 성립한다.
    if region == GLOBAL:
        region = None
    native_id = "/".join(parts[3:]) if len(parts) > 3 else None
    return parts[0], parts[1], region, native_id


def split_edge_id(value: str) -> tuple[str, str, str, str | None]:
    """`collect._edge_id()` 의 역함수 — `(종류, source, target, 구분자)`.

    노드 id 는 `|` 를 쓰지 않으므로 파싱이 유일하다(`collect.py:262` 의 주석이 그 보장이다).
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
            # partial 을 세웠으면 사유가 반드시 붙는다(스키마 allOf 가 강제한다).
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
            # 0 과 「수집 실패」는 전혀 다른 사실이다 — null 을 0 으로 바꾸지 마라.
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
        """스키마를 만족하는 dict. 배열 정렬은 여기서만 일어난다(불변식 ②)."""
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
