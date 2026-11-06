"""이력 — 앞뒤 **관측 문서 두 개**의 차분(`changes[]` · `history`).

정본은 ADR 0004 **D4**(무엇을 「변경」으로 셈하나 · 캡 2개)와 ADR 0006 **결정 5·7·8·9·12·13** 다.

🔴 **입력이 문서 두 개뿐인 것이 설계다.** AWS 를 다시 부르지 않고 `now()` 도 읽지 않는다 —
그래서 「같은 스냅샷 쌍 → 바이트 동일」(결정 5)이 함수의 성질로 참이 된다. 시각은 전부 입력에서
온다: 새 변경점의 `at` 은 **이번 문서의 `run.observed_at`**(= 「변경을 **본** 시각」,
`observation.schema.json:406`)이고, `baseline_at` 은 이전 문서에서 그대로 이어받는다. 흐름의
`live ↔ up` 만은 **실제로 멈춘/시작한 분**을 `occurred_at` 에 따로 적는다 — 그 분도 문서 안
(`evidence.first_flow_at`·`last_flow_at`)에서 온다(`_occurred_at`).

🔴 **이 파일의 절반은 「변경이 아닌 것을 변경으로 적지 않기」다.** 집합 차분만 하면 아래 셋이
전부 구성 변경·상태 변경으로 둔갑한다. 셋 다 이 리포가 실측으로 밟은 경로다.

| 실제로 일어난 일 | 차분이 적고 싶어 하는 것 | 왜 거짓인가 |
|---|---|---|
| 권한이 사라져 그 종류를 못 읽었다 | `resource_removed` N건 | `observe` 는 못 읽은 종류의 항목을 **아예 만들지 않는다**(`observe.py:590`·`:613`) → 지워진 것과 바이트가 같다 |
| 2층 토글이 꺼졌다(또는 켜졌다) | `status_changed live→up` N건 | 흐름이 멈춘 것이 아니라 **묻기를 멈췄다**. 세션 토글은 기본 +4시간이라 **반드시** 일어난다(ADR 0006 §O8) |
| scope 에서 계정·리전·**종류**를 뺐다(또는 엔진이 종류를 새로 지원했다) | `resource_removed`(또는 `resource_added`) 전량 | 그 자리를 보지 않았을 뿐이다(계정은 UI 에서 늘고 줄 수 있다 — ADR 0005. 종류는 엔진 버전이 늘린다 — 실측 2026-09-24) |
| 2층 전용 연결(엔드포인트 연결·LB 배치·어태치먼트)이 이번엔 값이 없었다 | `resource_removed`(또는 `resource_added`) | 그 행은 **쟀을 때만** 생긴다(`observe.py:706`) — 연결은 구조 문서에 그대로 있다. 실시간을 켜고 끌 때마다 17건씩, 흐름이 멈출 때마다 1~2건(배포본 실측 2026-09-27) |

🔴 **없어진 것은 목록에 id 만 남기면 화면이 그릴 수 없다**(사용자 피드백 2026-09-27 — 이력 줄을
누르면 「그때 모습」을 그려야 하는데, 없어진 카드의 이름·들어 있던 곳·연결은 지금 구조 문서에
없다). 그래서 새 `resource_removed` 줄에 **직전 관측이 그려진 구조 문서**에서 그 카드와 붙어 있던
연결을 그대로 옮겨 `was` 에 적는다(`_Shapes`). 그 구조를 못 찾으면 **적지 않고 센다**(`unshaped`) —
다른 시각의 구조에서 꺼낸 모습은 「그때 모습」이 아니다.

🔴 **상태 없는 카드(CloudFront · API Gateway · 탄력적 IP 등 `observe.PRESENCE_ONLY_NODES`)는 관측 항목이 없다** —
관측 문서 두 장만 비교하던 동안 이 8종의 생김·없어짐은 이력에 한 줄도 안 남았다(#133 · QA C 2026-10-21). 그래서 두 관측이
**서로 다른 구조**를 기준으로 했을 때만 그 두 구조의 카드 목록을 비교한다(`_presence_rows` · ADR 0006 결정 14). 구조 문서도
입력이므로 「AWS 를 다시 부르지 않는다 · 같은 입력 → 바이트 동일」은 그대로다. 짝 구조를 하나라도 못 찾으면 적지 않고 말한다.

그래서 판정 전에 **「이번에도·지난번에도 그 칸을 읽었나」를 먼저 묻는다** — 결정 4 의 「모름」 →
「고장」 순서를 이력에 적용한 것이다. 읽지 않은 칸의 침묵은 `changes` 에 넣지 않고 **보고서의
「제외」 수**로 내보낸다. 🔴 그 수를 안 세면 「못 읽어서 조용한 화면」과 「정말 변경이 없는 화면」이
같아진다 — S3 에서 실계정이 잡은 결함과 **같은 양식**이다(2층 요약의 네 번째 수 「못 읽음」).
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from .model import GLOBAL, split_edge_id, split_node_id
from .observe import (
    EDGE_RESOURCE_FROM_SOURCE,
    EDGE_SOURCE_RESOURCE,
    FLOW_LOG_METRIC,
    FLOW_LOG_ONLY_EDGES,
    METRIC_ONLY_EDGES,
    PRESENCE_ONLY_NODES,
    SCHEMA_VERSION,
)

_DESCRIBE = "describe"
_METRIC = "metric"

# (계정, 리전, 종류) — `coverage` 의 한 칸. 「그 대상을 읽었나」의 단위가 이것이다(ADR 0006 O10).
Cell = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class HistoryReport:
    """사람에게 말할 것. 🔴 **0 도 값이다** — 안 세면 「없었다」와 구별되지 않는다."""

    diffed: bool = False
    new_rows: int = 0
    carried: int = 0
    dropped: int = 0
    collapsed: int = 0
    # 항목 수 상한(`max_changes`)으로 이번에 버린 오래된 줄 수. 창 밖(`dropped`)과 다른 원인이다.
    capped: int = 0
    unread: int = 0
    layer_changed: int = 0
    out_of_scope: int = 0
    unknown_cell: int = 0
    unmeasured: int = 0
    # 「지우는 중」으로 바뀌어 적지 않은 상태 전이 수(사용자 결정 2026-10-21 · QA C). 줄은 자원이 구조에서 빠질 때 「없어짐」 하나다.
    deleting: int = 0
    # 새 `resource_removed` 줄 중 모습(`was`)을 적은 수 / 못 적은 수. 제외가 아니다 — 줄은 남는다.
    # 🔴 못 적은 수를 안 세면 「그릴 정보가 없는 없어진 것」이 조용히 늘어난다(화면은 글자 목록으로만 말한다).
    shaped: int = 0
    unshaped: int = 0
    # 상태 없는 카드(`observe.PRESENCE_ONLY_NODES`)의 생김·없어짐 줄 수 — 구조 문서 두 장에서 왔다(ADR 0006 결정 14).
    # 🔴 `new_rows` 에 들어 있다. 따로 세는 이유: 이 수가 0 인 것과 「구조가 바뀌었는데 비교를 못 했다」(`presence_unpaired`)를 가른다.
    presence_rows: int = 0
    presence_unpaired: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def excluded(self) -> int:
        return (
            self.unread
            + self.layer_changed
            + self.out_of_scope
            + self.unknown_cell
            + self.unmeasured
            + self.deleting
        )


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


# 🔴 **게시 지연 여유(초).** 지표는 몇 분 늦게 게시되므로, 이전 관측은 자기 창의 마지막 몇 분을
# 「아직 없는 것」으로 봤을 수 있다. 이번 창이 그 몇 분까지 거슬러 올라가야 두 관측 사이에 빈틈이 없다.
# 300 = 실측 최장 게시 지연 246초(PrivateLink · ADR 0006 O8)를 덮는 값이다.
_PUBLISH_ALLOWANCE_SECONDS = 300


def _minute(value: Any) -> str | None:
    """이전 문서에서 온 시각 문자열을 **검사만** 하고 그대로 돌려준다. 시간대가 없으면 버린다."""
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if moment.tzinfo is not None else None


def _occurred_at(
    before: dict[str, Any], after: dict[str, Any], before_at: str, after_at: str
) -> str | None:
    """흐름이 **실제로** 멈춘/시작한 분(사용자 결정 2026-09-27 · ADR 0008 D6 보강). 모르면 `None`.

    - `live → up`(멈춤) = **이전 문서의** `last_flow_at`. 이번 창에 흐름이 한 점도 없으니, 그 분
      뒤에 흐름이 있었다면 이번 창이 봤어야 한다.
    - `up → live`(시작) = **이번 문서의** `first_flow_at`. 이전 창에 흐름이 없었으니 그 앞은 없다.

    🔴 **둘 다 한 조건 위에 선다 — 간격 ≤ 이번 창 − 게시 지연 여유.** 간격이 더 길면 두 관측 사이에
    **아무도 보지 못한 구간**이 생기고, 이전 문서의 마지막 흐름은 「멈춘 분」이 아니라 「우리가 본
    마지막 분」일 뿐이다 — 3시간 공백 뒤에 3시간 전의 분을 멈춘 시각으로 적게 된다. 창·여유가 기본값
    (900·300)이면 한계는 10분이고, 5분 주기의 정상 실행은 늘 그 안이다.
    🔴 **한쪽이라도 `partial` 이면 적지 않는다.** 한 계열이 빠진 행의 「흐름 없음」은 덜 본 것일 수
    있고, 전이 자체는 적더라도(기존 규칙) 분까지 확정하는 것은 주장이 하나 더 는다.
    """
    if (before["status"], after["status"]) == ("live", "up"):
        source, key = before, "last_flow_at"
    elif (before["status"], after["status"]) == ("up", "live"):
        source, key = after, "first_flow_at"
    else:
        return None
    if before.get("partial") or after.get("partial"):
        return None
    window = (after.get("evidence") or {}).get("window_seconds")
    if not isinstance(window, int) or isinstance(window, bool):
        return None
    gap = (_at(after_at) - _at(before_at)).total_seconds()
    if gap > window - _PUBLISH_ALLOWANCE_SECONDS:
        return None
    return _minute((source.get("evidence") or {}).get(key))


def _node_cell(value: str) -> Cell | None:
    """노드의 칸. 🔴 **글로벌 노드(DXGW)는 리전 자리에 `GLOBAL` 을 둔 칸**이다(2026-10-13).

    옛 판은 `region is None` 을 전부 「계정 컨테이너」로 보고 `None` 을 돌려줬다 — 그래서 DXGW 의 상태 변경과
    VIF → DXGW 선이 **통째로 「칸 미상」**(`unknown_cell`)으로 빠져 이력에 한 줄도 남지 않았다. 글로벌 칸은
    「그 계정의 **어느 리전이든** 그 종류를 `ok` 로 읽었나」로 판정한다(`_Run.covers` · `observe.global_home`
    과 같은 규칙 — 어느 리전에서 읽어도 같은 목록이다, ADR 0010 §2).
    """
    node_type, account_id, region, native = split_node_id(value)
    if region is None:
        if native is None:
            return None  # 계정 컨테이너 — 칸에 속하지 않는다(상태도 없다)
        return account_id, GLOBAL, node_type
    return account_id, region, node_type


def target_cells(target_id: str, target_kind: str) -> tuple[Cell, ...] | None:
    """그 대상이 문서에 **나타나기 위해** 읽혀야 했던 칸 전부. 첫 칸이 그 대상의 자리다.

    🔴 엣지의 종류는 **끝점이 아니라 그 엣지를 만든 응답**이다(`observe.EDGE_SOURCE_RESOURCE`
    와 같은 규칙 — 둘이 갈라지면 이력이 「읽었다」를 잘못 판단한다). 자리는 source 노드의
    (계정,리전)이고 그것도 `observe` 의 `place[edge] = place[source]` 와 같다.

    🔴 **그런데 엣지는 한 칸이 아니다** — 엣지를 만든 응답 + **양 끝 노드의 칸**이다(겹치는 것은
    합쳐진다: `route` 의 source 는 route_table 이므로 보통 2칸이다). `collect._resolve_edges`
    (`collect.py:291`)는
    **양 끝 노드가 이번에 수집된 경우에만** 선을 만든다 — 못 찾은 끝점은 선이 아니라 source 노드의
    `unresolved` 로 남는다. 그래서 NAT 종류만 거부돼도 route_table 은 읽혔는데 그 NAT 로 가는
    경로가 문서에서 사라진다. 실측으로 잡혔다(2026-09-24,
    `test_losing_permission_is_not_recorded_as_deletion`): 엣지를 만든 응답 하나만 물었을 때
    NAT 권한 상실이 **「경로 1건 삭제」**로 목록에 적혔다 — 이 파일이 막으려는 바로 그 거짓이다.
    """
    try:
        if target_kind == "node":
            cell = _node_cell(target_id)
            return None if cell is None else (cell,)
        edge_type, source, target, _disc = split_edge_id(target_id)
        source_cell = _node_cell(source)
        target_cell = _node_cell(target)
    except ValueError:
        return None
    if source_cell is None or target_cell is None:
        return None
    if edge_type in EDGE_RESOURCE_FROM_SOURCE:
        # source 노드 타입이 그대로 그 엣지를 만든 응답이다. 🔴 `observe` 의 집합을 **그대로** 쓴다 —
        # 여기에 `association` 만 박아 두었던 동안 TGW 어태치먼트 선은 전부 「칸 미상」으로 빠져
        # 흐름 멈춤/시작이 이력에 한 줄도 남지 않았다(발견 2026-09-27).
        origin = source_cell
    else:
        mapped = EDGE_SOURCE_RESOURCE.get(edge_type)
        if mapped is None:
            return None
        origin = (source_cell[0], source_cell[1], mapped)
    return tuple(dict.fromkeys((origin, source_cell, target_cell)))


def _ok_cells(document: dict[str, Any], layer: str) -> set[Cell]:
    return {
        (row["account_id"], row["region"], row["resource_type"])
        for row in document["coverage"]
        if row.get("layer") == layer and row.get("status") == "ok"
    }


class _Run:
    """한 관측 문서에서 **「무엇을 읽었나」**만 뽑아 든다."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.observed_at: str = document["run"]["observed_at"]
        self.by_id: dict[str, dict[str, Any]] = {
            o["target_id"]: o for o in document["observations"]
        }
        self._ok = _ok_cells(document, _DESCRIBE)
        self.metric_ok = _ok_cells(document, _METRIC)
        # 흐름 로그 판독기를 켰나(S5). 꺼짐 = 키 부재 하나다(스키마 `layers.flow_log`).
        self.flow_log: bool = document["layers"].get("flow_log") is True
        self._reached = {(account, region) for account, region, _t in self._ok}
        self._requested = set(document["scope"]["resource_types"])
        self.pairs = {
            (account, region)
            for account in document["scope"]["account_ids"]
            for region in document["scope"]["regions"]
        }

    def observable(self, cells: tuple[Cell, ...]) -> bool:
        """그 실행이 이 대상을 **볼 수 있었나.** 필요한 칸이 **전부** 읽혔어야 한다.

        🔴 두 갈래인 것이 `observe` 와 같다: 다시 부르는 20종은 그 종류의 describe 가 `ok` 여야
        하고(O10), 부르지 않는 종류(SG·NACL — `none` 밖에 못 내는 것)는 그 **자리에 도달**했으면
        구조 문서가 존재를 말해 준다(`observe.py:637` 의 규칙). 후자를 빼면 보안 그룹 추가가
        영원히 이력에 안 남고, 전자를 자리 단위로 넓히면 서브넷이 거부된 날 「서브넷 전멸」이 된다.
        """
        return all(
            self.covers(cell)
            or (cell[2] not in self._requested and (cell[0], cell[1]) in self._reached)
            for cell in cells
        )

    def covers(self, cell: Cell) -> bool:
        """그 칸을 `ok` 로 읽었나. 글로벌 칸(`GLOBAL`)은 **그 계정의 어느 리전이든** 그 종류가 `ok` 면 참이다."""
        if cell[1] != GLOBAL:
            return cell in self._ok
        return any(
            (account, kind) == (cell[0], cell[2]) for account, _region, kind in self._ok
        )


def _row(
    at: str,
    kind: str,
    target_id: str,
    target_kind: str,
    before: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    # `from`·`to` 는 구성 변경에서도 **항상 적는다**(`null`). 생략하면 같은 사실에 두 바이트
    # 표현이 생긴다 — `model.py` 머리말의 「거짓이어도 항상 적는다」와 같은 이유다.
    return {
        "at": at,
        "kind": kind,
        "target_id": target_id,
        "target_kind": target_kind,
        "from": before,
        "to": after,
        "repeat_count": 1,
    }


class _Shapes:
    """구조 문서 하나에서 **없어진 것의 모습**을 꺼낸다(스키마 `change.was`).

    모습 = 그 카드 객체 + 그 카드에 붙어 있던 연결 전부(연결이 대상이면 그 연결 하나). 구조
    스키마의 모양을 **그대로** 옮긴다 — 화면이 같은 배치 함수로 다시 그릴 수 있어야 한다.
    🔴 부모 카드는 옮기지 않는다: 부모도 없어졌으면 부모가 **자기 줄**로 모습을 남긴다(관측
    종류 20개에 VPC·서브넷이 들어 있다 — `observe.OBSERVED_RESOURCE_TYPES`).
    """

    def __init__(self, structure: dict[str, Any]) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        for node in structure.get("nodes") or ():
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                self.nodes[node["id"]] = node
        for edge in structure.get("edges") or ():
            if isinstance(edge, dict) and isinstance(edge.get("id"), str):
                self.edges[edge["id"]] = edge
        self.touching: dict[str, list[dict[str, Any]]] = {}
        # id 순으로 붙인다(불변식 ②) — 같은 두 문서면 `was.edges` 의 순서가 바이트까지 같다.
        for edge_id in sorted(self.edges):
            edge = self.edges[edge_id]
            for end in dict.fromkeys((edge.get("source"), edge.get("target"))):
                if isinstance(end, str):
                    self.touching.setdefault(end, []).append(edge)

    def of(self, target_id: str, target_kind: str) -> dict[str, Any] | None:
        if target_kind == "node":
            node = self.nodes.get(target_id)
            if node is None:
                return None
            return {
                "nodes": [deepcopy(node)],
                "edges": [deepcopy(edge) for edge in self.touching.get(target_id, ())],
            }
        edge = self.edges.get(target_id)
        return None if edge is None else {"nodes": [], "edges": [deepcopy(edge)]}


def _paired_structure(
    previous: dict[str, Any], structures: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """직전 관측이 **짝지어 그려진** 구조 문서 — 없어진 것의 모습은 거기에만 있다.

    🔴 관측 문서에는 그 구조에 있는 대상만 들어간다(`observe.py` 의 `dropped`). 그래서 직전 관측에
    있던 대상은 **직전 관측의 구조**에 반드시 있다. 다른 구조(더 옛것·더 새것)에서 같은 id 를 꺼내면
    이름·연결이 다른 시각의 것일 수 있다 → 짝이 맞는 구조만 쓴다. 짝 = 내용 지문이 우선이고
    (`run.topology_content_digest` — 내용이 같은 매시 재수집은 같은 짝이다), 어느 한쪽에 지문이
    없으면 수집 시각이 같아야 한다.
    """
    run = previous["run"]
    digest = run.get("topology_content_digest")
    collected = run.get("topology_collected_at")
    for structure in structures:
        srun = structure.get("run") if isinstance(structure, dict) else None
        if not isinstance(srun, dict):
            continue
        theirs = srun.get("content_digest")
        if digest and theirs:
            if theirs == digest:
                return structure
        elif collected is not None and srun.get("collected_at") == collected:
            return structure
    return None


def _same_structure(previous: dict[str, Any], document: dict[str, Any]) -> bool:
    """두 관측이 **같은 구조 문서**를 기준으로 했나 — 짝 규칙(`_paired_structure`)과 같은 열쇠다.

    지문이 둘 다 있으면 지문, 아니면 수집 시각. 같으면 상태 없는 카드는 **바뀔 수 없다**(카드 목록의 출처가 같은 문서다) —
    그래서 평소 5분 차례는 구조 문서를 열어 보지도 않는다.
    """
    a, b = previous["run"], document["run"]
    da, db = a.get("topology_content_digest"), b.get("topology_content_digest")
    if da and db:
        return bool(da == db)
    return a.get("topology_collected_at") == b.get("topology_collected_at")


class _Drawn:
    """구조 문서 하나에서 **상태 없는 카드의 목록**과 「그 종류를 읽었나」만 뽑아 든다(ADR 0006 결정 14)."""

    def __init__(self, structure: dict[str, Any]) -> None:
        self.ids: set[str] = {
            node["id"]
            for node in structure.get("nodes") or ()
            if isinstance(node, dict)
            and node.get("type") in PRESENCE_ONLY_NODES
            and isinstance(node.get("id"), str)
        }
        # 🔴 구조 coverage 의 리전은 null 일 수 있다(단일 호출 글로벌 서비스 · topology `coverage_entry.region`).
        self._ok = {
            (row.get("account_id"), row.get("region"), row.get("resource_type"))
            for row in structure.get("coverage") or ()
            if isinstance(row, dict) and row.get("status") == "ok"
        }
        scope = structure.get("scope") or {}
        self.requested = {
            t for t in scope.get("resource_types") or () if isinstance(t, str)
        }

    def covers(self, account: str, region: str, resource_type: str) -> bool:
        """그 칸을 `ok` 로 읽었나. 글로벌 카드는 **그 계정의 어느 행이든**(리전 null 포함) — `_Run.covers` 와 같은 규칙."""
        if region != GLOBAL:
            return (account, region, resource_type) in self._ok
        return any((a, t) == (account, resource_type) for a, _r, t in self._ok)


def _presence_rows(
    before: _Drawn,
    after: _Drawn,
    shared: set[tuple[str, str]],
    shared_accounts: set[str],
    skip: set[str],
    observed_at: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """상태 없는 카드의 생김·없어짐 — 두 구조의 카드 목록 차이. `(줄, 범위 밖, 못 읽은 칸)` 을 돌려준다.

    관측 항목의 갈래와 **같은 질문을 같은 순서로** 묻는다: ① 두 관측이 그 자리를 함께 봤나(범위) ② 두 구조가 그 종류를
    함께 물었나(엔진이 종류를 새로 지원한 날 — 원래 있던 카드 전부가 「생김」으로 쏟아진다) ③ 없는 쪽 구조가 그 칸을 `ok` 로
    읽었나(권한 상실은 삭제가 아니다). 셋 중 하나라도 아니면 줄을 적지 않고 센다 — 「제외」 수로 나간다.
    """
    rows: list[dict[str, Any]] = []
    out_of_scope = unread = 0
    for target_id in sorted(before.ids ^ after.ids):
        if target_id in skip:
            continue  # 관측 항목이 있는 대상은 위 갈래가 이미 판정했다(같은 줄을 두 번 적지 않는다)
        try:
            cell = _node_cell(target_id)
        except ValueError:
            cell = None
        if cell is None:
            continue  # 구조 문서의 id 가 깨졌다 — 그 문서의 스키마 검사가 잡는다(여기서 지어내지 않는다)
        account, region, node_type = cell
        if (
            (account not in shared_accounts)
            if region == GLOBAL
            else ((account, region) not in shared)
        ):
            out_of_scope += 1
            continue
        sources = PRESENCE_ONLY_NODES[node_type]
        asked = {s for s in sources if s in before.requested}
        if not asked or asked != {s for s in sources if s in after.requested}:
            out_of_scope += 1
            continue
        added = target_id in after.ids
        blind = before if added else after
        if not all(blind.covers(account, region, s) for s in sorted(asked)):
            unread += 1
            continue
        rows.append(
            _row(
                observed_at,
                "resource_added" if added else "resource_removed",
                target_id,
                "node",
            )
        )
    return rows, out_of_scope, unread


# 이 모듈이 **직접 인덱싱하는** 중첩 키만 적는다 — (배열 이름, (키, 기대 타입) 쌍).
# 🔴 여기 없는 키는 `.get()` 으로 읽는 것이어야 한다. 새로 `row["x"]` 를 쓰면 이 표에 같이 넣어라.
# 🔴 **타입까지 적는 이유**: 키가 있어도 값이 밀리면 아래 코드가 그대로 죽는다(실측 2026-09-24,
# 실계정 바이트로 6자리) — `repeat_count` 가 문자열이면 `_collapse` 의 `sum` 이, `kind`·`target_id`
# 가 문자열이 아니면 최종 `sorted` 가, `coverage`·`scope` 원소가 unhashable 이면 `set` 이 터진다.
_ROW_CONTRACT: tuple[tuple[str, tuple[tuple[str, type], ...]], ...] = (
    ("observations", (("target_id", str), ("target_kind", str), ("status", str))),
    ("coverage", (("account_id", str), ("region", str), ("resource_type", str))),
    (
        "changes",
        (("at", str), ("kind", str), ("target_id", str), ("repeat_count", int)),
    ),
)
_SCOPE_KEYS = ("account_ids", "regions", "resource_types")


def _typed(where: str, value: Any, expected: type) -> str | None:
    """값이 그 타입인가. `bool` 은 `int` 로 쳐 주지 않는다 — 스키마의 `integer` 가 아니다."""
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        return f"{where} 가 {expected.__name__} 이 아니다"
    return None


def _shape_refusal(previous: dict[str, Any]) -> str | None:
    """같은 major 인데 **모양이 다른** 이전 문서인가.

    🔴 major 만 보는 것은 「같은 major 면 읽을 수 있다」는 약속인데, 그 약속을 지키는 코드가
    없었다(2026-09-24 실측: 2.x 문서에서 `scope.resource_types` 하나만 지워도 `apply_history` 가
    `KeyError` 로 죽고 트레이스백 + rc≠0 이 된다 — 불변식 ④ 가 말하는 「경고 + 이번 관측부터 새로
    시작」의 반대다). 가정이 아니라 **이 리포가 실제로 밟은 경로**다: `out/observe-run1.json` 은
    같은 `1.0.0` 인데 `scope` 가 2키뿐이고 다른 `1.0.0` 산출물은 3키다 — 한 major 안에서 우리
    writer 가 이미 모양을 바꿨다. 그때 죽지 않은 이유는 그 뒤에 major 가 올라갔기 때문뿐이다.

    🔴 **「키가 있나」로는 부족하다** — 실계정 바이트로 6자리를 재현했다(2026-09-24 2차): 값의
    타입이 밀리면 키가 다 있어도 `_collapse` 의 `sum`·최종 `sorted`·`set()`·`int()` 가 차례로
    터진다. 그래서 `_ROW_CONTRACT` 가 (키, 타입) 쌍이고 `scope` 원소·`resolution_seconds` 도 본다.

    사람이 겪는 일: 옛 형식(또는 손으로 고친) 파일을 `--previous` 로 물려도 주기 배치는 계속 돌고,
    「이력을 이번 관측부터 새로 시작한다」만 뜬다. 상태 화면이 파이썬 오류로 멈추지 않는다.

    🔴 **값은 메시지에 넣지 않는다** — 경고는 stderr 로 마스킹 없이 나가고 그 안에 계정ID·리소스
    id 가 있을 수 있다(§0). 키 경로와 행 번호만 적는다.
    """
    # `layers` 는 `_Run` 이 `.get` 으로 읽는다(흐름 로그 판독기를 켰나) — 객체가 아니면 거기서 터진다.
    for key in ("run", "scope", "layers", "history"):
        if not isinstance(previous[key], dict):
            return f"{key} 가 객체가 아니다"
    for key in _SCOPE_KEYS:
        values = previous["scope"].get(key)
        if not isinstance(values, list):
            return f"scope.{key} 가 배열이 아니다"
        for index, value in enumerate(values):
            bad = _typed(f"scope.{key}[{index}]", value, str)
            if bad is not None:
                return bad
    for name, keys in _ROW_CONTRACT:
        rows = previous[name]
        if not isinstance(rows, list):
            return f"{name} 가 배열이 아니다"
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                return f"{name}[{index}] 가 객체가 아니다"
            missing = [key for key, _type in keys if key not in row]
            if missing:
                return f"{name}[{index}] 에 {'·'.join(missing)} 가 없다"
            for key, expected in keys:
                bad = _typed(f"{name}[{index}].{key}", row[key], expected)
                if bad is not None:
                    return bad
    # 해상도는 이번 문서의 값과 `max` 로 겨룬다(`apply_history`) → 숫자가 아니면 그 줄이 터진다.
    # 없는 것은 허용한다 — 그쪽은 `or 0` 이 받는다.
    resolution = previous["history"].get("resolution_seconds")
    if resolution is not None:
        bad = _typed("history.resolution_seconds", resolution, int)
        if bad is not None:
            return bad
    # `_at()` 에 들어가는 문자열은 전부 여기서 먼저 파싱해 본다 — 보존 창 필터(`kept`)와
    # `truncated` 계산이 이 값들을 그대로 쓴다. `changes[].at` 의 타입은 위 표가 이미 봤다.
    bad = _typed("history.baseline_at", previous["history"].get("baseline_at"), str)
    if bad is not None:
        return bad
    stamps = [("history.baseline_at", previous["history"]["baseline_at"])]
    stamps += [
        (f"changes[{i}].at", row["at"]) for i, row in enumerate(previous["changes"])
    ]
    for name, value in stamps:
        try:
            _at(value)
        except ValueError:
            return f"{name} 이 RFC3339 가 아니다"
    return None


def _refusal(previous: dict[str, Any], observed_at: str) -> str | None:
    """이어받을 수 없는 이전 문서인가. 이유를 돌려주면 **기준선을 새로 시작한다**.

    🔴 여기서 `rc=1` 로 죽지 않는다 — 주기 배치의 첫 실행에는 이전 문서가 아예 없고, 한 번
    이어받기에 실패했다고 그날의 관측을 버리면 상태 화면까지 같이 멈춘다(불변식 ④). 대신
    이유를 사람에게 말하고 `baseline_at == observed_at` 으로 「여기서부터가 기록의 시작」임을
    문서에 남긴다.
    """
    version = str(previous.get("schema_version", ""))
    if version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        return f"schema_version={version or '없음'} 은 이 엔진이 읽는 계약이 아니다"
    for key in (
        "run",
        "scope",
        "layers",
        "observations",
        "coverage",
        "history",
        "changes",
    ):
        if key not in previous:
            return f"필수 키 {key} 가 없다"
    shape = _shape_refusal(previous)
    if shape is not None:
        return shape
    previous_at = previous["run"].get("observed_at")
    if not isinstance(previous_at, str):
        return "run.observed_at 이 없다"
    try:
        if _at(previous_at) >= _at(observed_at):
            # 같은 파일을 두 번 주거나 순서를 뒤집어 준 경우다. 그대로 차분하면 새 변경점의
            # 시각(`at` = 이번 관측)이 이전 관측보다 앞서서 목록의 순서가 거짓이 된다.
            return f"이전 관측 시각 {previous_at} 이 이번 관측보다 앞서지 않는다"
    except ValueError:
        return "관측 시각이 RFC3339 가 아니다"
    return None


# 반복 하나를 끊는 «조용한 하루»(ADR 0006 결정 13 · QA B 2차 N-04 · 2026-10-17). 같은 대상의 두 상태 변화 사이가
# 이보다 길면 같은 반복이 아니다 — 다른 날의 다른 사건이다. 🔴 보존 창(7일 · `retention_seconds`)보다 **짧아야**
# 뜻이 있다(창 밖 줄은 어차피 떨어진다) · 배치가 몇 시간 멈춘 것(QA 2차 실측 7시간)으로는 끊기지 않게 하루로 둔다.
# 엔진의 접는 규칙이지 계약이 아니다 — 화면은 줄을 받은 그대로 그린다(대상 하나에 접힌 줄이 여럿일 수 있다).
FLAP_JOIN_GAP_SECONDS = 86400


def _collapse(
    rows: list[dict[str, Any]], threshold: int
) -> tuple[list[dict[str, Any]], int]:
    """플래핑 중복 제거(ADR 0004 D4 캡 1) — 같은 대상 · **같은 반복**의 상태 변화가 threshold 이상이면 1줄.

    반복 하나 = 수명 경계(생김·없어짐)도 하루 넘는 공백도 끼지 않은 연속 변화다(`_episodes` · ADR 0006 결정 13).

    🔴 접은 행은 **되돌릴 수 없다**(스키마에 개별 시각을 담는 자리가 없다 — `change` 는
    `additionalProperties: false` 다). 그래서 임계값을 **올리면** 이미 접힌 행이 새 임계값보다
    작은 `repeat_count` 로 남을 수 있다(결정 9 의 어긋남). 임계값 변경은 §12 절차이고, 그때는
    기준선을 새로 시작하는 것이 정직하다.

    접은 행에는 반복의 **시작**도 남긴다(ADR 0006 결정 12 · 스키마 1.7.0) — `first_at` = 접은 줄 중
    가장 이른 `at` · `first_from` = 그 줄의 `from`(반복 전 상태). 마지막 `from` 만 남기면 화면이 그보다
    앞 시각의 상태를 마지막 `from` 으로 되짚어, 짝수 번 뒤집힌 VPN 이 그 전에 「이상」으로 그려졌다
    (2026-10-15 고객 데모 생성 중 발견). 시작을 모르는 줄(1.7.0 전에 접힌 줄)이 하나라도 섞이면 두 키를
    **적지 않는다** — 부재 = 「모른다」(지어낸 시작은 「그때 정상이었다」를 거짓으로 말한다).
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    lifetimes: dict[str, list[str]] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["kind"] == "status_changed":
            groups.setdefault(row["target_id"], []).append(row)
        else:
            out.append(row)
            # 생김·없어짐 = 그 대상의 **수명 경계**다(같은 이름으로 다시 만든 ALB 는 같은 id 를 받는다).
            lifetimes.setdefault(row["target_id"], []).append(row["at"])
    collapsed = 0
    for group in _episodes(groups, lifetimes):
        total = sum(row["repeat_count"] for row in group)
        if total < threshold and all(row["repeat_count"] == 1 for row in group):
            out.extend(group)  # 3회까지는 사람이 개별 시각을 보고 싶어 하는 범위다
            continue
        # 마지막 변화의 앞뒤를 남긴다 — 「N회 반복, 마지막은 down→up」이 사람이 읽는 문장이다.
        latest = max(group, key=lambda row: (row["at"], row["target_id"]))
        folded = {
            key: value
            for key, value in latest.items()
            if key not in ("first_at", "first_from")
        }
        folded["repeat_count"] = total
        start = _fold_start(group)
        if start is not None:
            folded["first_at"], folded["first_from"] = start
        out.append(folded)
        collapsed += len(group) - 1
    return out, collapsed


def _episodes(
    groups: dict[str, list[dict[str, Any]]], lifetimes: dict[str, list[str]]
) -> list[list[dict[str, Any]]]:
    """대상마다 상태 변화를 **반복 하나씩**으로 가른다 — 접힘은 반복 하나 안에서만(ADR 0006 결정 13).

    끊는 자리 둘: ① 그 사이에 같은 대상의 생김·없어짐 줄이 있다(수명 경계) · ② 앞 반복의 마지막 `at` 과
    다음 줄의 시작(접힌 줄이면 `first_at`, 모르면 `at`) 사이가 `FLAP_JOIN_GAP_SECONDS` 보다 길다.
    🔴 사람이 겪은 일(QA B 2차 N-04): 며칠 전 지웠다가 같은 이름으로 다시 만든 ALB 의 새 반복이 옛 줄(1.7.0 전에 접혀
    시작이 없다)과 합쳐져 「8회 반복」 = 1차 + 2차가 되고 시작을 잃었다 → 그 앞 시각 비교가 「그때 상태를 모르는 것」.
    끊으면 옛 줄은 옛 줄대로 남고 새 반복은 제 시작을 가진다. 시작을 모르는 줄의 시작을 `at` 으로 재면 공백이 **길게**
    잡힌다 — 끊는 쪽으로만 기운다(잘못 끊으면 줄이 하나 더 남고, 잘못 이으면 시작을 잃는다).
    순서는 `at` 과 줄 전체로 정한다 — 입력 순서에 기대지 않는다(불변식 ②).
    """
    episodes: list[list[dict[str, Any]]] = []
    for target_id in sorted(groups):
        bounds = lifetimes.get(target_id, [])
        current: list[dict[str, Any]] = []
        for row in sorted(
            groups[target_id],
            key=lambda row: (
                row["at"],
                row["repeat_count"],
                str(row.get("from")),
                str(row.get("to")),
            ),
        ):
            if current:
                end = current[-1]["at"]
                start = row.get("first_at") if row["repeat_count"] > 1 else None
                start = start if isinstance(start, str) else row["at"]
                if any(end < at < row["at"] for at in bounds) or (
                    (_at(start) - _at(end)).total_seconds() > FLAP_JOIN_GAP_SECONDS
                ):
                    episodes.append(current)
                    current = []
            current.append(row)
        episodes.append(current)
    return episodes


def _fold_start(group: list[dict[str, Any]]) -> tuple[str, str] | None:
    """접힌 반복의 시작 `(first_at, first_from)` — 가장 이른 줄의 시각과 그 앞 상태. 모르면 `None`.

    한 건짜리 줄(`repeat_count == 1`)은 제 `at`·`from` 이 시작이다. 이미 접힌 줄은 제 `first_at`·
    `first_from` 을 쓴다 — 🔴 그 둘이 없는(1.7.0 전에 접힌) 줄이 하나라도 있으면 그 앞이 비어 있으므로
    시작을 모른다. `min` 은 튜플 전체로 고른다 — 입력 순서에 기대지 않는다(불변식 ②).
    """
    starts: list[tuple[str, str]] = []
    for row in group:
        if row["repeat_count"] == 1:
            at, before = row["at"], row.get("from")
        else:
            at, before = row.get("first_at"), row.get("first_from")
        if not isinstance(at, str) or not isinstance(before, str):
            return None
        starts.append((at, before))
    return min(starts)


def apply_history(
    document: dict[str, Any],
    previous: dict[str, Any] | None,
    structures: Sequence[dict[str, Any]] = (),
) -> HistoryReport:
    """`document` 의 `history`·`changes` 를 **제자리에서** 채우고 보고서를 돌려준다.

    이전 문서가 없으면 `build_observation` 이 넣어 둔 기준선(= 이번 관측이 기준)을 그대로 둔다.
    `structures` = 없어진 것의 모습을 찾을 구조 문서 후보(이번 구조 · 직전 구조). 그중 **직전 관측과
    짝이 맞는 것 하나**만 쓴다(`_paired_structure`). 비워 두면 없어짐 줄에 `was` 를 적지 않는다.
    """
    history = document["history"]
    observed_at: str = document["run"]["observed_at"]
    retention: int = history["retention_seconds"]
    threshold: int = history["flap_collapse_threshold"]

    if previous is None:
        return HistoryReport()
    refusal = _refusal(previous, observed_at)
    if refusal is not None:
        return HistoryReport(
            warnings=(f"{refusal} — 이력을 이번 관측부터 새로 시작한다",)
        )

    current, before_run = _Run(document), _Run(previous)
    shared = current.pairs & before_run.pairs
    shared_accounts = {account for account, _region in shared}
    # 🔴 **종류도 범위다.** 한쪽만 물어본 종류는 「사라졌다/생겼다」가 아니라 「안 본 자리」다
    # (같은 모듈이라 `_requested` 를 직접 읽는다 — 자리 규칙과 붙어 있어야 갈라지지 않는다).
    # 양쪽 다 묻지 않는 종류(SG·NACL)는 대칭차에 들어가지 않으므로 이 줄이 건드리지 않는다.
    drifted_types = current._requested ^ before_run._requested
    rows: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    unread = layer_changed = out_of_scope = unknown_cell = unmeasured = deleting = 0

    for target_id in sorted(set(current.by_id) | set(before_run.by_id)):
        after = current.by_id.get(target_id)
        before = before_run.by_id.get(target_id)
        target_kind = (after or before)["target_kind"]
        cells = target_cells(target_id, target_kind)
        if cells is None:
            # 🔴 우리 쪽 결함이다(새 타입·오타 난 id). 「변경 없음」으로 흘리지 않고 센다.
            unknown_cell += 1
            continue
        if any(
            # 글로벌 칸은 리전이 없다 — 그 계정이 두 실행에 함께 있으면 같은 자리다(어느 리전이든 같은 목록).
            (cell[0] not in shared_accounts)
            if cell[1] == GLOBAL
            else (cell[0], cell[1]) not in shared
            for cell in cells
        ):
            # 🔴 범위 차이를 **먼저** 묻는다 — 뺀 리전은 「못 읽은 칸」이 아니라 「안 본 자리」다.
            out_of_scope += 1
            continue
        if any(cell[2] in drifted_types for cell in cells):
            # 🔴 **엔진이 종류를 새로 지원한 날**이 이 줄이 막는 가장 잦은 경우다(2차 리소스 종류는
            # 계획돼 있다 — `product-architecture.md` §11). 그날 직전 문서에는 그 종류가 통째로
            # 없으므로, 묻지 않으면 **원래 있던 자원 전부가 「생성됨」**으로 목록에 쏟아진다.
            # 실측(2026-09-24, 실계정 바이트): 종류 하나를 양방향으로 밀었더니 각각 30줄
            # (`resource_added` 30 · `resource_removed` 30), 제외 카운터는 넷 다 0 이었다.
            out_of_scope += 1
            continue
        cell = cells[0]
        if before is None or after is None:
            if target_id.split("|")[0] in METRIC_ONLY_EDGES:
                # 🔴 **2층 전용 연결은 구성 변경 후보가 아니다.** 그 행은 지표가 값을 줬을 때만 생기므로
                # (`observe.py:706-713`) 한쪽에 없다는 것은 「그때 값이 없었다」이지 「연결이 생겼다/
                # 없어졌다」가 아니다. 연결 자체가 정말 사라지면 소유 자원(엔드포인트·LB·어태치먼트)의
                # 행이 그것을 말한다. 흐름의 멈춤/시작도 그 자원 행의 `live ↔ up` 이 말한다.
                # 사람이 보는 결과(배포본 실측 2026-09-27): 실시간을 켤 때 「생겼다」 17줄 ·
                # 끌 때 「없어졌다」 17줄이 목록에 쏟아졌고, 흐름이 멈출 때마다 1~2줄이 더 붙었다.
                if (cell in current.metric_ok) != (cell in before_run.metric_ok):
                    layer_changed += 1
                else:
                    unmeasured += 1
                continue
            if target_id.split("|")[0] in FLOW_LOG_ONLY_EDGES:
                # 🔴 **흐름 로그 전용 연결(피어링)도 같다** — 그 행은 왕복 짝을 찾았을 때만 생긴다(ADR 0008
                # 「D4b 개정」 ⑤-2). 한쪽에 없다는 것은 「그 창에 왕복 기록이 없었다」이지 피어링이 생겼다/
                # 없어졌다가 아니다. 피어링 자체가 없어지면 `pcx-` 노드 행이 그것을 말한다.
                if current.flow_log != before_run.flow_log:
                    layer_changed += 1
                else:
                    unmeasured += 1
                continue
            # 구성 변경 후보 — **없는 쪽 실행이 그것을 볼 수 있었나**를 묻는다. 못 봤으면
            # 「생겼다/사라졌다」가 아니라 「못 읽었다」이고, 그 사실은 coverage 가 이미 말한다.
            blind = before_run if before is None else current
            if not blind.observable(cells):
                unread += 1
                continue
            kind = "resource_added" if before is None else "resource_removed"
            row = _row(observed_at, kind, target_id, target_kind)
            if kind == "resource_removed":
                removed.append(row)
            rows.append(row)
            continue
        if before["status"] == after["status"]:
            continue
        if after.get("deleting") is True:
            # 🔴 **삭제가 만든 전이는 적지 않는다**(사용자 결정 2026-10-21 · QA C). 일부러 지운 엔드포인트가 「없어짐」 앞에
            # 「정상 → 관측 불가」(QA B B-12) · 「끊김 정상 → 이상」(QA C)을 남겼다 — 고객은 그것을 장애로 읽는다. 자원이
            # 구조에서 빠지면 위 갈래가 「없어짐」 한 줄을 적는다. 이미 「이상」이었다면 **그 줄은 이전 목록에 그대로 있다**
            # (이어받는다) — 숨기는 것은 삭제가 만든 새 전이뿐이다. 거꾸로(지우는 중 → 실패)는 이 갈래에 걸리지 않고 적힌다.
            deleting += 1
            continue
        if "live" in (before["status"], after["status"]) and (
            (cell in current.metric_ok) != (cell in before_run.metric_ok)
        ):
            # 2층을 켜거나 끄면 `live ↔ up` 이 전량 흔들린다. 그것은 흐름이 바뀐 것이 아니라
            # **질문이 바뀐 것**이다(1층 판정은 그대로다 — 불변식 ④).
            layer_changed += 1
            continue
        if {before["status"], after["status"]} == {
            "live",
            "up",
        } and FLOW_LOG_METRIC in (
            (before.get("evidence") or {}).get("metric"),
            (after.get("evidence") or {}).get("metric"),
        ):
            # 🔴 흐름 로그의 `up` 은 「이 창에 왕복 기록 없음」이지 「흐름이 멈췄다」가 아니다(ADR 0008
            # 「D4b 개정」 ② · ⑤-2). 이 뒤집힘을 줄로 적으면 1단계가 금지한 「흐름 없음」을 이력이 말한다.
            # `live ↔ down` 은 1층이 바뀐 것이므로 여기 걸리지 않고 그대로 적힌다.
            if current.flow_log != before_run.flow_log:
                layer_changed += 1
            else:
                unmeasured += 1
            continue
        row = _row(
            observed_at,
            "status_changed",
            target_id,
            target_kind,
            before["status"],
            after["status"],
        )
        occurred = _occurred_at(before, after, before_run.observed_at, observed_at)
        if occurred is not None:
            # 🔴 `at` 은 그대로 「본 시각」이다 — 정렬 키이고 화면 행의 정체다. 분은 **옆에** 적는다.
            row["occurred_at"] = occurred
        rows.append(row)

    # 상태 없는 카드(CloudFront · API Gateway · 탄력적 IP …)의 생김·없어짐 — ADR 0006 결정 14 · #133.
    # 🔴 관측 항목이 없으니 위 갈래로는 영원히 한 줄도 안 나온다. 그 카드의 존재는 **구조 문서**가 말하므로 두 관측이 짝지어
    # 그려진 구조 두 장의 카드 목록을 비교한다. 같은 구조면 바뀔 수 없다 → 열어 보지도 않는다(평소 5분 차례 · 비용 0).
    warnings: list[str] = []
    presence_rows = 0
    presence_unpaired = False
    if not _same_structure(previous, document):
        drawn_before = _paired_structure(previous, structures)
        drawn_after = _paired_structure(document, structures)
        if drawn_before is None or drawn_after is None:
            # 🔴 지어내지 않는다 — 다른 시각의 구조로 비교하면 그 사이의 생김·없어짐이 섞이거나 빠진다. 대신 말한다.
            presence_unpaired = True
            warnings.append(
                "구조가 바뀌었는데 두 관측이 그려진 구조 문서를 다 찾지 못했다 — 상태 없는 카드"
                f"({len(PRESENCE_ONLY_NODES)}종 · CloudFront·API Gateway 등)의 생김·없어짐을 이번에는 비교하지 않았다"
            )
        else:
            more, skipped_scope, skipped_unread = _presence_rows(
                _Drawn(drawn_before),
                _Drawn(drawn_after),
                shared,
                shared_accounts,
                set(current.by_id) | set(before_run.by_id),
                observed_at,
            )
            out_of_scope += skipped_scope
            unread += skipped_unread
            presence_rows = len(more)
            rows.extend(more)
            removed.extend(row for row in more if row["kind"] == "resource_removed")

    # 없어진 것의 모습 — **새 줄에만** 적는다. 이어받은 줄은 적힌 그대로 둔다(그 줄의 구조는
    # 이미 지나갔고, 지금 가진 구조에서 다시 꺼내면 다른 시각의 모습이 된다).
    shaped = 0
    if removed:
        paired = _paired_structure(previous, structures)
        shapes = _Shapes(paired) if paired is not None else None
        for row in removed:
            was = shapes.of(row["target_id"], row["target_kind"]) if shapes else None
            if was is not None:
                # 🔴 부재 = 「모른다」다. `null` 을 두지 않는다(같은 사실에 두 바이트 표현).
                row["was"] = was
                shaped += 1
        if paired is None:
            warnings.append(
                f"직전 관측이 그려진 구조 문서를 못 찾았다 — 없어진 {len(removed)}개를 "
                "모습 없이 적는다(화면은 이름 없이 글자로만 말한다)"
            )

    # 보존 창(ADR 0004 D4 캡 2) — 넘은 변경점은 **산출물에서만** 떨어진다(스냅샷은 남는다).
    horizon = _at(observed_at) - timedelta(seconds=retention)
    kept = [row for row in previous["changes"] if _at(row["at"]) >= horizon]
    combined, collapsed = _collapse(kept + rows, threshold)
    # 정렬은 여기 한 곳에서만(불변식 ②). 화면은 최신 우선으로 다시 정렬한다(`flow.js:3746`).
    ordered = sorted(
        combined, key=lambda row: (row["at"], row["kind"], row["target_id"])
    )
    # 항목 수 상한(ADR 0006 O4 · 대장 19) — 창 다음, 접힘 다음. 정렬의 **앞쪽(오래된 쪽)** 부터 버린다.
    cap: int = history["max_changes"]
    capped = max(0, len(ordered) - cap)
    document["changes"] = ordered[capped:]

    baseline_at = previous["history"]["baseline_at"]
    history["baseline_at"] = baseline_at
    # 🔴 해상도는 **가장 거친 쪽**이다(요청 주기 · 이번 실제 간격 · 이전 문서의 값 중 최대).
    # 실제 간격이 3시간인 두 파일을 비교하고도 `300` 을 적으면 화면은 「5분보다 짧게 생겼다 사라진
    # 것만 놓친다」고 말하는데 그것이 거짓이다. 이전 값까지 함께 보는 이유: 목록은 `baseline_at`
    # 부터의 **구간**을 말하므로, 그 구간 안에 한 번이라도 3시간 공백이 있었으면 목록 전체의
    # 해상도가 3시간이다. 촘촘하다고 과장하는 쪽이 사람을 오판하게 하므로 줄지 않게 한다.
    gap = ceil((_at(observed_at) - _at(before_run.observed_at)).total_seconds())
    history["resolution_seconds"] = max(
        history["resolution_seconds"],
        int(previous["history"].get("resolution_seconds") or 0),
        gap,
    )
    # ADR 0006 O4 의 함수 관계 그대로 — 「기록이 시작된 시각」과 「기록을 버린 사실」은 다르다.
    # 🔴 상한으로 버린 사실은 **이어받는다** — 버린 줄은 창 안이라 다음 회차에도 «없다»(ADR 0006 O4).
    history["truncated"] = (
        _at(baseline_at) < horizon
        or capped > 0
        or previous["history"].get("truncated") is True
    )

    return HistoryReport(
        diffed=True,
        new_rows=len(rows),
        carried=len(kept),
        dropped=len(previous["changes"]) - len(kept),
        capped=capped,
        collapsed=collapsed,
        unread=unread,
        layer_changed=layer_changed,
        out_of_scope=out_of_scope,
        unknown_cell=unknown_cell,
        unmeasured=unmeasured,
        deleting=deleting,
        shaped=shaped,
        unshaped=len(removed) - shaped,
        presence_rows=presence_rows,
        presence_unpaired=presence_unpaired,
        warnings=tuple(warnings),
    )
