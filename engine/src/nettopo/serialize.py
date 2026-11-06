"""불변식 ② — 결정론 직렬화. 산출물 바이트를 만드는 곳은 이 파일뿐이다.

`run.collected_at` 은 wall-clock 의 유일한 예외이므로, 두 실행의 동일성을 재는 해시는
그 필드를 **제외**하고 계산한다(`digest_excluding_collected_at`).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .model import Snapshot

# collected_at 을 지우는 대신 고정 문자열로 바꾼다 — 키가 사라지면 「필드를 안 쓴 산출물」과
# 「시각만 다른 산출물」이 같은 해시가 된다.
_FIXED_TIME = "<collected_at>"


def to_json(snapshot: Snapshot) -> str:
    document = snapshot.to_document()
    document["run"]["content_digest"] = content_digest(document)
    return dumps(document)


def dumps(document: dict[str, Any]) -> str:
    # sort_keys: dict 삽입 순서에 의존하지 않는다. ensure_ascii=False: 한글 값을 그대로 둔다.
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    """지문용 정규형 — **한 벌**만 둔다(#32 리뷰 4). 두 지문(결정론 검사 · 구조 짝)이 각자 규칙을 들고
    있으면 한쪽에만 규칙이 붙을 때 `test_determinism` 과 짝 맞추기 지문이 조용히 갈라진다."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest_excluding_collected_at(text: str) -> str:
    # `json.loads` 가 이미 새 객체다 — 호출자의 것을 건드리지 않으므로 복사하지 않는다.
    document = json.loads(text)
    if "run" in document and isinstance(document["run"], dict):
        document["run"]["collected_at"] = _FIXED_TIME
    return digest(_canonical(document))


def content_digest(document: dict[str, Any]) -> str:
    """구조 **내용**의 지문 — 그림에 앉는 것(카드·선)과 그것을 만든 도구 판의 정규형 sha256.

    관측 문서가 「어느 구조를 보고 만들어졌나」를 이 값으로 말한다(`observation` 의
    `run.topology_content_digest`). 🔴 시각으로만 짝을 맞추면 **내용이 같은 매시 재수집**마다 짝이
    어긋난다 — 실측(2026-09-27, 188): 연속 4회 수집이 `run` 밖은 바이트 동일했는데 화면은 매시
    34분에 겹쳐 그리기를 거부했다(= 실시간을 켠 사람이 1시간마다 파일을 다시 받아야 했다).

    🔴 넣는 것 = `schema_version` · `run.tool_version` · `nodes` · `edges` 뿐이다. `coverage`(무엇을 읽었나)·
    `scope`(무엇을 보라고 했나)는 **넣지 않는다**(#32 리뷰 2, 2026-09-28): 매시 재수집 한 번이 빈 자원 종류에서
    스로틀·AccessDenied 를 맞으면(불변식 ④ 의 정상 경로) 카드·선은 그대로인데 지문이 바뀌어, 화면이 실패를
    「구성이 바뀌었다 — 파일을 다시 받아라」로 말하고 `structure-previous.json` 도 한 번 더 밀렸다. 짝이 지키는
    것은 「관측의 대상 id 가 이 그림의 카드·선에 그대로 앉는가」이고, 그것은 카드·선만으로 정해진다.
    못 읽어 카드가 **빠진** 회차는 여전히 다른 지문이다 — 그림이 실제로 다르다(화면 문장은 원인을 단정하지 않는다).

    `tool_version` 은 지문에 **넣는다** — 같은 계정이라도 도구가 바뀌면 같은 구조라고 단정하지 않는다.
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
