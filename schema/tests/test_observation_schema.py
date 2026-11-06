"""observation.schema.json 의 대조군.

JSON Schema 로 표현할 수 없는 것이 이 계약에서는 특히 많다. 세 가지가 전부 불변식이다:

- **다른 문서를 참조한다.** `observations[].target_id` 는 `topology.json` 의 노드·엣지 id 다.
  매달린 참조는 «없어진 리소스에 상태가 붙은 그림» 이 되고, 그건 조용히 틀린 그림이다(불변식 ③).
- **판정 순서가 고정이다.** 「모름」 → 「고장」(docs/contract-seams.md 「이음매 3」). 못 읽은 (계정,리전)에
  `down` 을 적으면 거짓이다. 스키마는 평가 순서를 표현할 수 없으므로 여기서 강제한다(불변식 ④).
- **정렬.** 불변식 ②의 실제 내용이다.

모든 양성 어서션에는 **반드시 실패하는 짝**을 둔다. 실패할 수 없는 어서션은 통과가 아니라
미측정이다. 이 파일에서 `_detects_` 로 끝나는 테스트가 그 짝이다.
"""

from __future__ import annotations

import copy
import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import cache
from pathlib import Path

import pytest
from internal_docs import read_or_skip
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schema" / "observation.schema.json"
TOPOLOGY_SCHEMA_PATH = ROOT / "schema" / "topology.schema.json"
SEAMS_PATH = ROOT / "docs" / "contract-seams.md"
ADR_PATH = ROOT / "docs" / "adr" / "0006-observation-schema.md"
ADR9_PATH = ROOT / "docs" / "adr" / "0009-optional-evidence-sources.md"
TOPOLOGY_FIXTURE_DIR = ROOT / "fixtures"
FIXTURE_DIR = TOPOLOGY_FIXTURE_DIR / "observation"

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
TOPOLOGY_SCHEMA = json.loads(TOPOLOGY_SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
TOPOLOGY_VALIDATOR = Draft202012Validator(TOPOLOGY_SCHEMA)
FIXTURES = sorted(FIXTURE_DIR.glob("*.json"))
# 흐름 로그 근거의 표지(`$defs/flow_log_evidence`). 지표 이름 자리에 들어가지만 **지표가 아니다** —
# 2층 원장·2층 전용 검사·「live 는 metric 층이 ok」 검사가 이 값을 지표로 세면 안 된다.
FLOW_LOG = "flow_log"

REGION_RE = re.compile(r"^[a-z]{2}(-[a-z]+)+-[0-9]$")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _doc(path: Path) -> str:
    """내부 설계 문서(ADR·이음매)를 읽는다 — 공개 트리에는 없어서 그 대조만 사유를 적고 건너뛴다.

    🔴 import 때 읽지 않는다. 표에서 뽑는 값(`approved_defaults()` · `o7_vocabulary()` …)은 전부 처음 쓰는
    테스트 안에서 읽고 캐시한다 — 모듈에서 읽으면 이 파일을 오라클로 불러 쓰는 엔진 테스트 파일까지 통째로
    수집 오류가 된다(공개 트리 실측 2026-10-27: 11개 파일).
    """
    return read_or_skip(path.relative_to(ROOT).as_posix())


@cache
def approved_defaults() -> dict[str, int]:
    """ADR 0006 「승인된 기본값」 표를 파싱한다 — 수치의 정본은 스키마가 아니라 그 표다.

    스키마에 `const` 로 박으면 고객별 조정이 계약 위반이 되고, `default` 로 적으면 검증기가
    읽지 않는 주석이 된다. 그래서 값은 문서에 두고 픽스처와의 일치만 여기서 잰다.
    """
    text = _doc(ADR_PATH)
    parts = text.split("## 승인된 기본값")
    assert len(parts) == 2, (
        f"{ADR_PATH.name} 에 「승인된 기본값」 절이 없다 — 수치의 정본이 사라지면 "
        "아래 어서션들은 빈 표와 비교해 조용히 통과한다"
    )
    section = parts[1].split("\n## ")[0]
    return {
        key: int(value)
        for key, value in re.findall(
            r"^\| `([a-z_]+)` \| `([0-9]+)` \|", section, re.MULTILINE
        )
    }


@cache
def adr9_source_kinds(text: str | None = None) -> set[str]:
    """ADR 0009 D4 「1차」 채택표의 1열 = 옵션형 근거 출처 어휘의 정본.

    D2 가 「어휘를 늘릴 때는 채택표에 줄을 먼저 추가한다」고 적었으므로 그 순서를 테스트가
    강제한다. 반대 방향도 막는다 — 표에만 있고 enum 에 없으면 엔진이 탐지할 자리가 없고,
    enum 에만 있으면 «왜 그 출처를 읽는가» 의 근거가 없는 호출이 생긴다.

    🔴 「1차 보너스」 표는 일부러 제외한다. 그쪽은 고객이 켜고 끄는 옵션이 아니라 **이미 켜져
    있는 쌍 지표**이고 ADR 0008 의 A급 고정표로 가는 항목이라서 `sources[]` 어휘가 아니다.

    `text` 는 판별력 실증용이다(`test_adr9_kind_parser_reads_rows_not_prose`).
    """
    text = _doc(ADR9_PATH) if text is None else text
    parts = text.split("#### 1차 — ")
    assert len(parts) == 2, (
        f"{ADR9_PATH.name} 에 D4 「1차」 절이 없다 — 절이 사라지면 아래 어서션은 빈 집합끼리 "
        "비교해 조용히 통과한다"
    )
    section = parts[1].split("\n#### ")[0]
    # 🔴 `[a-z_]+` 로 쓰면 `ec2_detailed_monitoring` 이 조용히 빠진다(숫자 포함) — 실측 2026-09-25.
    rows = re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.MULTILINE)
    return set(rows) - {"kind"}


def source_sort_keys(doc: dict) -> list[str]:
    return [s["kind"] for s in doc["sources"]]


def sources_over_total(doc: dict) -> list[str]:
    """🔴 `enabled_ids ≤ total_ids` 는 JSON Schema 로 표현할 수 없다(형제 값 비교) —
    스키마 `$comment` 가 이 검사를 약속했다. 넘으면 화면이 「9/7 VPC」를 그린다."""
    return [
        s["kind"]
        for s in doc["sources"]
        if "scope" in s and s["scope"]["enabled_ids"] > s["scope"]["total_ids"]
    ]


def sources_used_while_off(doc: dict) -> list[str]:
    """스키마 `allOf` 가 이미 막지만, 막고 있다는 사실을 재는 검사가 따로 있어야 한다
    (`test_source_check_detects_a_source_used_while_off` 가 판별력을 실증한다)."""
    return [s["kind"] for s in doc["sources"] if s["state"] == "off" and s["used"]]


@cache
def o7_state_source_types(text: str | None = None) -> tuple[set[str], set[str]]:
    """ADR 0006 §O7 「타입별 상태 출처」 표의 1열 = 상태를 붙일 수 있는 타입의 정본.

    §O7 은 「이 표에 행이 없는 타입은 `none` 이 아니라 결함이다 … 2차 목록이 들어오면 행을
    먼저 더한다」고 적고 그 일치를 **테스트가 강제한다**고 선언했는데, 그 테스트가 없었다
    (실측 2026-09-23 — ADR 을 파싱하는 것은 위 `approved_defaults()` 하나뿐이었다).
    행 없이 수집만 늘면 그 타입은 상태가 조용히 안 붙고, 화면에서 「관측 불가」와 구별되지 않는다.

    `text` 는 판별력 실증용이다 — 파서 자체가 행을 읽는지(산문을 긁는 게 아닌지)를
    합성 문서로 재려면 파일을 건드리지 않아야 한다(`test_o7_parser_reads_rows_not_prose`).
    """
    text = _doc(ADR_PATH) if text is None else text
    parts = text.split("### O7 — ")
    assert len(parts) == 2, (
        f"{ADR_PATH.name} 에 §O7 절이 없다 — 절이 사라지면 아래 어서션들은 빈 집합끼리 "
        "비교해 조용히 통과한다"
    )
    section = parts[1].split("\n### ")[0]
    _, _, after_nodes = section.partition("**노드**")
    node_block, _, edge_block = after_nodes.partition("**엣지**")

    def first_column_types(block: str) -> set[str]:
        """🔴 행 이름의 수식어는 **기본형으로 접는다**(§O7 「행 이름 문법」, 승인 2026-09-23).

        `association←route_table` 의 `←` 뒤는 타입이 아니라 「그 대상을 만든 응답의 종류」다.
        접지 않으면 enum 대조가 양방향으로 빨개진다 — `association` 행이 없다고 하고(①),
        동시에 enum 에 없는 행이 둘 있다고 한다(②).

        🔴 문자군에 `0-9` 를 넣어야 한다 — `[a-z_]+` 로 쓰면 `ec2_instance` 행이 조용히 안 읽히고
        「표에 행이 없다」로 FAIL 한다(실측 2026-09-26, 수집기 4종 중 이것만 빨갰다).
        같은 함정을 `adr9_source_kinds()` 가 이미 한 번 밟았다(`:102`).
        """
        found: set[str] = set()
        for row in re.findall(r"^\|([^|]+)\|", block, re.MULTILINE):
            found |= {
                name.split("←")[0]
                for name in re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", row)
            }
        return found - {"node_type", "edge_type"}

    return first_column_types(node_block), first_column_types(edge_block)


@cache
def o7_vocabulary(text: str | None = None) -> dict[str, set[str]]:
    """§O7 표의 **마지막 열** = 타입별로 낼 수 있는 상태의 정본. 빈 집합 = 항목 자체를 만들지 않는다.

    §O7 의 🔴 주석이 셋을 가른다: 「상태 없음」 = `observations` 에 **항목을 만들지 않는다**(그릴
    것이 없다) / `none` = 항목을 만들고 `?` 를 붙인다 / 어휘(`up`·`down`·`live`) = 판정을 적는다.
    사람이 보는 차이가 이 셋이다 — 아무것도 없는 선 / `?` 가 붙은 카드 / 색이 붙은 카드.

    마지막 열만 읽는다 — 행 전체에서 「상태 없음」을 찾으면 `attachment` 행의 1층 근거
    (「없음 — 노드가 든다」)처럼 다른 열의 문구에 걸려 분류가 흔들린다.

    🔴 **키는 수식어를 그대로 유지한다**(`association←network_acl`). 같은 엣지 타입이 만들어진
    응답에 따라 낼 수 있는 어휘가 다르기 때문이다 — 접어 버리면 NACL 발 연관에 `up` 을 적어도
    통과한다. 기본형으로 조회해야 하는 자리(수식어를 모르는 검사)는 `o7_allowed()` 를 쓴다.
    """
    text = _doc(ADR_PATH) if text is None else text
    section = text.split("### O7 — ")[1].split("\n### ")[0]
    _, _, after_nodes = section.partition("**노드**")
    node_block, _, edge_block = after_nodes.partition("**엣지**")
    words = {"live", "up", "down", "none"}
    out: dict[str, set[str]] = {}
    for block in (node_block, edge_block):
        for line in block.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            # 🔴 `0-9` 필수 — 빼면 `ec2_instance` 가 이 표에 없는 타입이 되고, 그 노드는
            # 「행 없음」(=결함)으로 떨어져 상태가 조용히 안 붙는다(`:156` 과 같은 함정).
            types = set(re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", cells[0])) - {
                "node_type",
                "edge_type",
            }
            allowed: set[str] = (
                set()
                if "상태 없음" in cells[-1]
                else set(re.findall(r"`([a-z]+)`", cells[-1])) & words
            )
            for t in types:
                out[t] = allowed
    return out


@cache
def o7_entry_bearing() -> frozenset[str]:
    # 🔴 기본형으로 접는다 — 이 집합은 `node_type`·`edge_type` enum 과 비교되고, 「항목이 있어야 하나」
    # 를 타입 이름만으로 묻는 자리(`silent_where_read`)에서 쓰인다. 수식어 행이 하나라도 어휘를 내면
    # 그 타입은 항목을 만드는 타입이다.
    return frozenset(
        t.split("←")[0] for t, allowed in o7_vocabulary().items() if allowed
    )


def o7_metric_only(text: str | None = None) -> set[str]:
    """§O7 표가 「**2층 전용**」이라고 적은 행 이름(ADR 0008 D8-1).

    네 번째 모습이다. 1층은 그 구간에 대해 **아무 말도 하지 않고**(그 값은 노드가 든다), 2층(구간
    지표)이 실제로 답했을 때만 행이 생긴다. 그래서 **행이 없는 것이 결함이 아니다** — 2층은 기본
    꺼짐이고(제품 대장 7), 꺼진 동안 이 종류는 통째로 조용한 것이 정상이다.

    🔴 그 면제가 「엣지는 영원히 조용해도 된다」로 넓어지면 폐기된 종단 추론으로 되돌아간다. 그래서
    빼는 대신 판별력을 두 검사로 옮긴다 — 픽스처 최소 1개가 이 종류의 행을 들고 있고
    (`test_some_fixture_carries_a_metric_only_edge`), 그 행의 근거가 **지표**여야 한다
    (`test_metric_only_edges_must_cite_a_metric`).
    """
    text = _doc(ADR_PATH) if text is None else text
    section = text.split("### O7 — ")[1].split("\n### ")[0]
    out: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue  # 표 밖의 산문에도 이 말이 나온다 — 행만 읽는다
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or "2층 전용" not in cells[-1]:
            continue
        out |= set(re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", cells[0])) - {
            "node_type",
            "edge_type",
        }
    return out


@cache
def o7_metric_only_types() -> frozenset[str]:
    return frozenset(t.split("←")[0] for t in o7_metric_only())


def o7_flow_log_only(text: str | None = None) -> set[str]:
    """§O7 표가 「**흐름 로그 전용**」이라고 적은 행 이름(ADR 0008 「D4b 개정」 · 2026-09-28).

    1층은 노드가 들고 2층 지표도 없다 — **고객이 흐름 로그를 켜 두었고 우리가 읽어서 왕복 짝을
    찾았을 때만** 행이 생긴다. 「2층 전용」과 같은 면제(행이 없어도 결함이 아니다)를 받지만 이유가
    다르다: 2층은 우리가 켜는 층이고 이것은 고객이 켜는 출처다.

    🔴 그래서 따로 센다. 한 집합으로 합치면 「2층 전용 행의 근거는 지표여야 한다」
    (`metric_only_without_a_metric`)가 피어링 행의 흐름 로그 근거를 거부하거나, 그것을 통과시키려고
    그 검사를 느슨하게 만들게 된다 — 후자가 되면 어태치먼트 선이 흐름 로그 표지만 달고 지표 없이
    초록이 될 수 있다. 행만 읽는 규칙은 `o7_metric_only` 와 같다(산문에도 이 말이 나온다).
    """
    text = _doc(ADR_PATH) if text is None else text
    section = text.split("### O7 — ")[1].split("\n### ")[0]
    out: set[str] = set()
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or "흐름 로그 전용" not in cells[-1]:
            continue
        out |= set(re.findall(r"`([a-z_]+(?:←[a-z_]+)?)`", cells[0])) - {
            "node_type",
            "edge_type",
        }
    return out


@cache
def o7_flow_log_only_types() -> frozenset[str]:
    return frozenset(t.split("←")[0] for t in o7_flow_log_only())


@cache
def o7_required_entry() -> frozenset[str]:
    # 🔴 「항목을 만들 수 있다」와 「항목이 **있어야** 한다」는 다르다. 2층 전용·흐름 로그 전용 행은
    # 앞쪽이고 뒤쪽이 아니다 — 아래 `silent_where_read` 에 주는 집합은 뒤쪽이어야 한다.
    return o7_entry_bearing() - o7_metric_only_types() - o7_flow_log_only_types()


def o7_allowed(o7_type: str | None) -> set[str] | None:
    """행 이름 → 낼 수 있는 상태. **`None` = 표에 행이 없다**(= 결함), 빈 집합 = 「상태 없음」.

    수식어를 아는 자리는 정확한 행 이름(`association←network_acl`)을 주고, 모르는 자리는 기본형
    (`association`)을 준다 — 후자는 같은 타입의 수식어 행들을 **합집합**으로 본다. 그 자리의
    질문이 「항목이 있어도 되나」뿐이기 때문이다. 판정 검사는 반드시 수식어를 붙인 쪽을 쓴다
    (합집합으로 판정하면 NACL 발 연관의 `up` 이 통과한다).

    🔴 `dict.get(x, {"x"})` 꼴로 쓰면 「행 없음」과 「상태 없음」이 한 값으로 섞인다 — 그 둘은
    사람이 보는 결과가 다르다(전자는 고쳐야 하는 결함, 후자는 의도된 침묵).
    """
    if o7_type is None:
        return None
    rows = [
        allowed
        for name, allowed in o7_vocabulary().items()
        if name == o7_type or name.startswith(f"{o7_type}←")
    ]
    if not rows:
        return None
    return set().union(*rows)


def o7_gaps(documented: set[str], declared: set[str]) -> tuple[set[str], set[str]]:
    """(표에 행이 없는 enum 값, enum 에 없는 표의 행). 둘 다 비어야 한다."""
    return declared - documented, documented - declared


def errors(doc: dict, validator: Draft202012Validator = VALIDATOR) -> list[str]:
    return [
        f"{e.json_path}: {e.message}"
        for e in sorted(validator.iter_errors(doc), key=lambda e: e.json_path)
    ]


def topology_for(path: Path) -> Path:
    """관측 픽스처가 붙는 구조 픽스처. 파일명의 첫 구획이 짝을 정한다.

    `minimal.metric-off.json` → `fixtures/minimal.json`. 두 구조 픽스처가 같은
    `collected_at` 을 쓰고 있어 그 값으로는 짝을 고를 수 없다 — 이름이 짝을 정하고,
    `run.topology_collected_at` 이 그 짝과 어긋나지 않는지를 아래 테스트가 잰다.
    """
    return TOPOLOGY_FIXTURE_DIR / f"{path.name.split('.')[0]}.json"


def place_of(node_id: str) -> tuple[str, str | None] | None:
    """노드 id 에서 (계정, 리전). 모르면 None.

    id 형식(`<type>/<account>/<region>/…`)은 topology.schema.json 의 description 에만 있고
    구획 수는 pattern 이 강제하지 않는다. 그래서 파싱은 방어적으로 한다 — 못 읽으면 None 을 주고,
    호출부가 «못 읽었다» 를 실패로 다룬다(조용히 통과시키지 않는다).

    🔴 **엣지 id 를 받으면 거절한다.** 엣지 id 는 `association|route_table/<acct>/<region>/…|…`
    꼴이어서 `/` 로 자르면 구획 1·2 가 계정·리전처럼 **읽힌다** — 아래 방어 없이는 「노드 id 에서」
    라는 이 함수의 계약이 깨진 채로 통과한다(2026-09-23: 그 사실이 안 보였던 이유는 어서션이
    엔진이 만들지 않는 가짜 엣지 id 를 쓰고 있었기 때문이다).
    """
    if "|" in node_id:
        return None
    parts = node_id.split("/")
    if parts[0] == "account" and len(parts) == 2:
        return (parts[1], None)
    if parts[0] == "region" and len(parts) == 3:
        return (parts[1], parts[2])
    # 글로벌 노드(DXGW · ADR 0010 §2) — 리전 자리가 리터럴 `global` 이다. 자리를 「리전 없음」으로 접지 않고
    # 표지 그대로 돌려준다 — 접으면 계정 컨테이너와 같은 값이 되어 아래 검사들이 그 노드를 **건너뛴다**.
    if (
        len(parts) >= 4
        and re.fullmatch(r"[0-9]{12}", parts[1])
        and parts[2] == GLOBAL_SLOT
    ):
        return (parts[1], GLOBAL_SLOT)
    if (
        len(parts) >= 4
        and re.fullmatch(r"[0-9]{12}", parts[1])
        and REGION_RE.match(parts[2])
    ):
        return (parts[1], parts[2])
    return None


GLOBAL_SLOT = "global"


def home_cell(
    doc: dict, place: tuple[str, str | None] | None, resource_type: str | None
) -> tuple[str, str, str] | None:
    """그 대상을 판정할 수 있는 1층 칸 (계정, 리전, 종류). 칸에 속하지 않으면 None.

    🔴 **글로벌 자리**(`GLOBAL_SLOT`)는 리전이 없다. 규칙(ADR 0006 §O7 「DX 3종」 결정 2): 그 계정에서 그 종류의
    describe 가 `ok` 인 리전 중 **이름순 첫 리전**이 그 칸이다 — 어느 리전에서 읽어도 같은 목록이므로 하나라도 `ok` 면
    읽은 것이고, 원장은 한 칸에만 센다. `ok` 가 하나도 없으면 칸이 없다(= 판정을 주장할 수 없다).
    엔진 함수(`observe.global_home`)를 부르지 않고 여기서 따로 적는다 — 오라클이 제품 코드를 부르면 같이 틀린다.
    """
    if place is None or place[1] is None or resource_type is None:
        return None
    account, region = place
    if region != GLOBAL_SLOT:
        return (account, region, resource_type)
    regions = sorted(
        c["region"]
        for c in doc["coverage"]
        if c["layer"] == "describe"
        and c["status"] == "ok"
        and c["account_id"] == account
        and c["resource_type"] == resource_type
        and c["region"] is not None
    )
    return (account, regions[0], resource_type) if regions else None


def topology_index(topo: dict) -> dict[str, str]:
    """target_id → target_kind."""
    index = {n["id"]: "node" for n in topo["nodes"]}
    for edge in topo["edges"]:
        index[edge["id"]] = "edge"
    return index


def places(topo: dict) -> dict[str, tuple[str, str | None] | None]:
    """target_id → (계정, 리전). 엣지는 source 노드의 자리를 쓴다.

    🔴 예외 하나 — 어태치먼트 선이 **어태치먼트 자기 TGW 가 아닌** TGW 에 닿으면(병합된 피어링의
    상대편 선) 그 선은 상대편 응답에서 왔으므로 **도착 TGW** 의 자리다(#31 리뷰 ①). 엔진 함수를
    가져다 쓰지 않고 여기서 따로 적는다 — 오라클이 제품 코드를 부르면 같이 틀린다.
    """
    by_id = {n["id"]: n for n in topo["nodes"]}
    out: dict[str, tuple[str, str | None] | None] = {
        n["id"]: place_of(n["id"]) for n in topo["nodes"]
    }
    for edge in topo["edges"]:
        source = by_id.get(edge["source"], {})
        target = by_id.get(edge["target"], {})
        own = (source.get("attributes") or {}).get("transit_gateway_id")
        far = (
            edge["type"] == "attachment"
            and target.get("type") == "transit_gateway"
            and own
            and target.get("native_id") != own
        )
        out[edge["id"]] = out.get(edge["target"] if far else edge["source"])
    return out


def resource_types(topo: dict) -> dict[str, str]:
    """target_id → 「그 대상을 읽어 온 응답의 종류」(ADR 0006 O10 의 coverage 세 번째 키).

    노드는 자기 종류다. 엣지는 **source 노드의 종류**다 — O10 이 열거한 네 경우가 모두 그
    값과 일치한다(`route`·`association←route_table` → `route_table` /
    `association←network_acl` → `network_acl` / `security_group_reference` →
    `security_group`). 수집기가 「그 응답에서 나온 노드」를 source 로 놓기 때문이고, 위
    `places()` 가 자리를 정하는 규약과 같다.

    🔴 **끝점(target) 종류로 읽으면 틀린다** — `route` 엣지의 target 은 NAT·IGW·TGW 이고, 그
    종류가 거부돼도 경로 자체는 route_table 응답 안에 들어 있다. 그 자리에 「NAT 를 못 읽었다」와
    「그 NAT 로 가는 경로는 살아 있다」가 같이 적히는 것은 모순이 아니라 **사실**이다.
    """
    out: dict[str, str] = {n["id"]: n["type"] for n in topo["nodes"]}
    for edge in topo["edges"]:
        source_type = out.get(edge["source"])
        if source_type is not None:
            out[edge["id"]] = source_type
    return out


# ---- JSON Schema 가 못 보는 것: 순수 함수로 뽑아 두고 짝으로 판별력을 고정한다 ----------


def unsorted(values: list) -> bool:
    return values != sorted(values)


def observation_sort_keys(doc: dict) -> list[str]:
    return [o["target_id"] for o in doc["observations"]]


# 이력 줄의 정렬 키 — 스키마 `history.max_changes` 의 $comment 가 적은 순서 그대로다(엔진 `history.py` 의
# `sorted(..., key=(at, kind, target_id))` 와 같다). 🔴 `kind` 를 빼면 한 관측에 생김·없어짐이 같이 있는
# **바르게 정렬된** 문서를 「어긋났다」고 판정한다(2026-10-15 고객 데모 생성 중 발견 — 옛 키 `(at, target_id)`).
CHANGE_SORT_FIELDS = ("at", "kind", "target_id")


def change_sort_keys(doc: dict) -> list[tuple[str, str, str]]:
    return [(c["at"], c["kind"], c["target_id"]) for c in doc["changes"]]


def coverage_sort_keys(doc: dict) -> list[tuple[str, str, str, str]]:
    """🔴 키가 **4개**다(ADR 0006 O10) — 종류축이 빠지면 같은 자리의 여러 종류가 한 칸으로
    보이고, 「VPC 는 읽었고 서브넷은 못 읽었다」를 적을 자리가 사라진다."""
    return [
        (c["account_id"], c["region"], c["resource_type"], c["layer"])
        for c in doc["coverage"]
    ]


def fold_start_after_last(doc: dict) -> list[str]:
    """접힌 반복의 시작(`first_at`)이 마지막(`at`)보다 앞이 아닌 줄 — JSON Schema 가 못 재는 순서(ADR 0006 결정 12).

    반복은 서로 다른 관측 시각 두 개 이상이다(`repeat_count` ≥ 2) → 시작은 마지막보다 **엄격히** 앞이다.
    """

    def ts(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return [
        c["target_id"]
        for c in doc["changes"]
        if isinstance(c.get("first_at"), str) and ts(c["first_at"]) >= ts(c["at"])
    ]


def dangling_observations(doc: dict, topo: dict) -> list[str]:
    index = topology_index(topo)
    return [
        o["target_id"]
        for o in doc["observations"]
        if index.get(o["target_id"]) != o["target_kind"]
    ]


def dangling_changes(doc: dict, topo: dict) -> list[str]:
    """`resource_removed` 는 예외다 — 사라진 것은 구조 스냅샷에 없는 것이 정상이다.

    이 예외가 없으면 「지워졌다」를 기록할 방법이 사라진다. 반대로 예외를 넓히면
    오타 난 id 가 조용히 통과한다 — 그래서 kind 별로만 면제한다.
    """
    index = topology_index(topo)
    return [
        c["target_id"]
        for c in doc["changes"]
        if c["kind"] != "resource_removed"
        and index.get(c["target_id"]) != c["target_kind"]
    ]


def requested_cells(doc: dict) -> set[tuple[str, str, str, str]]:
    """scope 가 요청한 칸 전부 = 계정 × 리전 × **종류** × 층(ADR 0006 O10).

    종류축이 생기면서 `scope.resource_types` 가 필수가 됐다 — 요청이 없으면 「그 종류가
    실패했다」와 「그 종류를 아예 부르지 않았다」가 구별되지 않는다.
    """
    return {
        (a, r, t, layer)
        for a in doc["scope"]["account_ids"]
        for r in doc["scope"]["regions"]
        for t in doc["scope"]["resource_types"]
        for layer in ("describe", "metric")
    }


def coverage_holes(doc: dict) -> list[tuple[str, str, str, str]]:
    return sorted(requested_cells(doc) - set(coverage_sort_keys(doc)))


def unrequested_coverage(doc: dict) -> list[tuple[str, str, str, str]]:
    return sorted(set(coverage_sort_keys(doc)) - requested_cells(doc))


def layer_status(doc: dict) -> dict[tuple[str, str, str, str], str]:
    return {
        (c["account_id"], c["region"], c["resource_type"], c["layer"]): c["status"]
        for c in doc["coverage"]
    }


def item_count_ledger(doc: dict, topo: dict) -> tuple[int, int, int]:
    """(describe `ok` 칸에 적힌 합, 1층 칸에 속한 항목 수, 어느 칸에도 없는 항목 수).

    `item_count` 는 「그 (계정,리전,종류,층) 칸에서 판정한 대상 수」다(`observe.py:518`). 그래서
    **어느 칸에도 속하지 않는 항목이 있다** — SG·NACL·SG 참조는 관측이 부르지 않는 종류이므로
    describe 행이 없고, 그 `none` 항목은 구조 문서에서 온다. 등식은
    `합 == 칸에 속한 수` 이고 `칸에 속한 수 + 칸 밖 수 + 2층 전용 수 == 전체` 다.

    🔴 차이를 상수로 박으면 안 된다 — 픽스처에 SG 를 한 장 더 넣는 날 조용히 틀린다.

    🔴 **2층 전용 엣지는 1층 원장 밖이다**(ADR 0008 D8-1 · `observe.py` 의 `counts` 루프와 같은
    규칙). 그 행은 describe 가 아니라 지표가 만든 것이므로 1층 칸에 세면 **모니터링을 켰다 끌
    때마다 1층의 숫자가 움직인다**. 대신 `metric_item_count_ledger()` 가 2층 칸에서 잰다.
    """
    seen = places(topo)
    kinds = resource_types(topo)
    rows = {
        (c["account_id"], c["region"], c["resource_type"]): c
        for c in doc["coverage"]
        if c["layer"] == "describe"
    }
    priced = unpriced = 0
    for obs in doc["observations"]:
        if (
            obs["target_id"].split("|")[0]
            in o7_metric_only_types() | o7_flow_log_only_types()
        ):
            continue
        key = home_cell(doc, seen.get(obs["target_id"]), kinds.get(obs["target_id"]))
        if key in rows:
            priced += 1
        else:
            unpriced += 1
    written = sum(c["item_count"] or 0 for c in rows.values() if c["status"] == "ok")
    return written, priced, unpriced


def metric_item_count_ledger(doc: dict) -> tuple[int, int]:
    """(metric `ok` 칸에 적힌 합, 실제로 지표를 근거로 든 항목 수).

    2층은 1층과 원장이 따로다 — 1층 칸에 속한 대상 중 일부만 지표가 답하고, 2층 전용 엣지는
    1층 칸에 아예 없다. 이 등식이 없으면 **2층의 숫자는 아무 것과도 비교되지 않는다**(요약 알약이
    「흐름 12」라고 말하는데 화면에 초록이 3개여도 조용하다).

    🔴 「근거가 지표다」의 판정은 `evidence.metric` 이 비어 있지 않은 것이다. `datapoints` 를 쓰면
    「쟀고 0」(`up` + `datapoints: 0`)이 빠지고, 그 행은 2층이 **답한** 행이다.

    🔴 칸에 속하는지를 여기서 걸러내지 **않는다.** 걸러내면 2층 행이 있는데 그 칸의 coverage 행이
    없는 경우(= 근거를 댈 수 없는 초록)가 조용히 통과한다 — 그때는 `written` 이 작아져서 잡힌다.

    🔴 TGW 흐름 로그가 올린 어태치먼트 선(ADR 0008 「D4c」 ③)도 2층이 답한 행이다 — 흐름 로그는 2층이 `up` 으로 만든
    행에만 `live` 를 얹고 근거를 흐름 로그로 바꾼다. 2층 전용 엣지라 그 행은 2층 없이는 생길 수 없다.
    """
    rows = [c for c in doc["coverage"] if c["layer"] == "metric"]
    cited = sum(
        1
        for obs in doc["observations"]
        if (obs.get("evidence") or {}).get("metric") not in (None, "", FLOW_LOG)
        or _tgw_flow_lifted(obs)
    )
    written = sum(c["item_count"] or 0 for c in rows if c["status"] == "ok")
    return written, cited


def _tgw_flow_lifted(obs: dict) -> bool:
    """TGW 흐름 로그 왕복이 `live` 로 올린 어태치먼트 선(D4c ③) — 2층 행 위에만 얹힌다."""
    return (
        obs["target_id"].startswith("attachment|transit_gateway_attachment/")
        and (obs.get("evidence") or {}).get("metric") == FLOW_LOG
        and obs["status"] == "live"
    )


def metric_only_without_a_metric(doc: dict) -> list[str]:
    """2층 전용 엣지인데 근거가 지표가 아닌 관측(있으면 결함).

    🔴 이 함수가 이 릴리스의 핵심을 지킨다. §O7 이 세 엣지 타입을 「행이 없어도 된다」고 면제한
    대가로, **행이 있을 때는 지표를 들고 있어야 한다**. 지표 없이 만든 구간 판정은 정의상
    「양단이 살아 있으니 사이도 흐를 것이다」이고 그것이 폐기된 종단 추론이다.

    셋을 따로 본다: 근거 객체 자체가 없는 경우 · `metric` 이 빈 경우 · 1층 API 를 적은 경우.
    마지막이 특히 조용하다 — `ec2:DescribeTransitGatewayAttachments` 를 적으면 「구간을 Describe
    로 쟀다」가 되고, 그런 API 는 없다.
    """
    out = []
    for obs in doc["observations"]:
        if obs["target_id"].split("|")[
            0
        ] not in o7_metric_only_types() or _tgw_flow_lifted(obs):
            continue
        evidence = obs.get("evidence") or {}
        if not evidence.get("metric"):
            out.append(f"{obs['target_id']}: `evidence.metric` 이 없다")
        elif evidence.get("api") != "cloudwatch:GetMetricData":
            out.append(
                f"{obs['target_id']}: 근거 API 가 2층이 아니다({evidence.get('api')})"
            )
        elif obs["status"] not in {"live", "up", "down"}:
            out.append(
                f"{obs['target_id']}: §O7 이 허용한 어휘가 아니다({obs['status']})"
            )
    return out


def route_table_users(topo: dict) -> dict[str, set[str]]:
    """라우트 테이블 id → 그 테이블을 **실제로 쓰는** 서브넷 id 집합.

    둘 중 하나다: 명시 연관 선(`association|route_table/…|subnet/…`)이 있다 / 같은 VPC 의 메인
    테이블(`attributes.main` — `route_table.py:114`)이고 그 서브넷에 명시 연관이 없다. 메인 연관은
    선을 만들지 않으므로(`route_table.py:59`) 뒤쪽을 빼면 메인 테이블을 쓰는 서브넷은 증명할 수
    없게 되고, 반대로 「같은 VPC 면 된다」로 느슨하게 두면 **옆 서브넷의 기록이 이 선을 초록으로
    만든다** — 서브넷별 근거를 둔 이유 자체가 사라진다.

    🔴 오라클이다 — 엔진의 귀속 코드를 부르지 않는다(같이 틀린다).
    """
    nodes = {n["id"]: n for n in topo["nodes"]}
    explicit: dict[str, str] = {}
    for edge in topo["edges"]:
        if (
            edge["type"] == "association"
            and nodes.get(edge["source"], {}).get("type") == "route_table"
            and nodes.get(edge["target"], {}).get("type") == "subnet"
        ):
            explicit[edge["target"]] = edge["source"]
    users: dict[str, set[str]] = {
        n["id"]: set() for n in nodes.values() if n["type"] == "route_table"
    }
    for subnet, table in explicit.items():
        users.setdefault(table, set()).add(subnet)
    mains = {
        n.get("parent_id"): n["id"]
        for n in nodes.values()
        if n["type"] == "route_table"
        and (n.get("attributes") or {}).get("main") is True
    }
    for n in nodes.values():
        if (
            n["type"] == "subnet"
            and n["id"] not in explicit
            and n.get("parent_id") in mains
        ):
            users[mains[n["parent_id"]]].add(n["id"])
    return users


def flow_log_problems(doc: dict, topo: dict) -> list[str]:
    """흐름 로그 근거가 구조·출처와 맞지 않는 곳(ADR 0008 「D4b 개정」 · 스키마 `$comment` 가 약속).

    JSON Schema 는 한 행 안의 모양만 본다. 아래는 **문서 두 장과 행 여러 개를 맞대야** 보이는
    거짓 초록이다 — 하나라도 빠지면 사람은 흐르지 않는 선을 초록으로 본다.

    ① 층이 꺼져 있는데 흐름 로그 근거·로그 목록이 있다(끈 화면이 켠 화면과 달라야 한다).
    ② `by_subnet` 이 `subnet_id` 오름차순·유일이 아니다(불변식 ② · 같은 서브넷 두 번 = 두 번 센다).
    ③ 그 서브넷이 **그 라우트 테이블을 쓰지 않는다**(`route_table_users`). 🔴 이것이 핵심이다 —
       같은 테이블을 쓰는 서브넷 선이 전부 초록이 되는 것을 막으려고 행을 서브넷별로 갈랐다.
    ④ 피어링 라우트 항목의 `observed_on` 이 그 서브넷이 아니다(B 등급의 「어디서 봤나」가 거짓).
    ⑤ IGW 카드·피어링 선의 흐름 로그 행을 **받치는 서브넷 증명이 없다**. IGW = 그 IGW 로 가는
       라우트의 `by_subnet` 에 `observed_on` 서브넷이 있어야 한다. 피어링 = 그 `pcx` 로 가는
       라우트의 `by_subnet` 에 있거나, VPC 로 적었으면 그 VPC 가 피어링의 한쪽이고 서브넷을
       못 가리는(`subnet_unattributable`) 「읽음」 VPC 로그가 그 VPC 에 붙어 있어야 한다.
    ⑥ 로그 목록이 id 오름차순·유일이 아니다 / 출처의 「사용」이 목록의 「읽음」과 어긋난다 /
       `attached_to` 가 구조에 없다 / 증명한 서브넷을 덮는 「읽음」 VPC 흐름 로그가 없다.
    """
    out: list[str] = []
    nodes = {n["id"]: n for n in topo["nodes"]}
    edges = {e["id"]: e for e in topo["edges"]}
    users = route_table_users(topo)
    resources = [
        (source["kind"], r)
        for source in doc["sources"]
        for r in source.get("resources", [])
    ]
    on = doc["layers"].get("flow_log") is True

    # ①
    if not on:
        for obs in doc["observations"]:
            if (
                "by_subnet" in obs
                or (obs.get("evidence") or {}).get("metric") == FLOW_LOG
            ):
                out.append(
                    f"{obs['target_id']}: 흐름 로그 층이 꺼져 있는데 흐름 로그 근거"
                )
        for source in doc["sources"]:
            if "resources" in source:
                out.append(f"{source['kind']}: 흐름 로그 층이 꺼져 있는데 로그 목록")

    # ②③④
    proven: dict[str, set[str]] = {}
    claimed: set[str] = set()
    for obs in doc["observations"]:
        items = obs.get("by_subnet")
        if not items:
            continue
        ids = [i["subnet_id"] for i in items]
        if ids != sorted(set(ids)):
            out.append(f"{obs['target_id']}: by_subnet 이 오름차순·유일이 아니다")
        edge = edges.get(obs["target_id"])
        if edge is None:
            out.append(f"{obs['target_id']}: by_subnet 을 단 행의 선이 구조에 없다")
            continue
        for item in items:
            subnet = item["subnet_id"]
            if subnet not in users.get(edge["source"], set()):
                out.append(
                    f"{obs['target_id']}: {subnet} 은 이 라우트 테이블을 쓰지 않는다"
                )
                continue
            if (
                edge["target"].startswith("vpc_peering_connection/")
                and item["evidence"].get("observed_on") != subnet
            ):
                out.append(
                    f"{obs['target_id']}: {subnet} 항목의 observed_on 이 그 서브넷이 아니다"
                )
            proven.setdefault(edge["target"], set()).add(subnet)
            claimed.add(subnet)

    # ⑤
    for obs in doc["observations"]:
        evidence = obs.get("evidence") or {}
        if evidence.get("metric") != FLOW_LOG:
            continue
        target_id, seen_on = obs["target_id"], evidence.get("observed_on")
        if target_id.startswith("internet_gateway/"):
            if seen_on not in proven.get(target_id, set()):
                out.append(
                    f"{target_id}: {seen_on} 에서 이 IGW 로 가는 서브넷 증명이 없다"
                )
        elif target_id.startswith("peering|"):
            pcx = target_id.split("|")[1]
            if str(seen_on).startswith("vpc/"):
                sides = {
                    e["target"]
                    for e in topo["edges"]
                    if e["type"] == "peering" and e["source"] == pcx
                }
                backed = any(
                    kind == "vpc_flow_log"
                    and r["used"]
                    and r.get("attached_to") == seen_on
                    and r.get("why_code") == "subnet_unattributable"
                    for kind, r in resources
                )
                if seen_on not in sides or not backed:
                    out.append(
                        f"{target_id}: {seen_on} 는 이 피어링의 한쪽에서 서브넷을 못 가린 "
                        "「읽음」 로그가 아니다"
                    )
            elif seen_on not in proven.get(pcx, set()):
                out.append(
                    f"{target_id}: {seen_on} 에서 이 피어링으로 가는 서브넷 증명이 없다"
                )

    # ⑥
    for source in doc["sources"]:
        listed = source.get("resources")
        if listed is None:
            continue
        ids = [r["id"] for r in listed]
        if ids != sorted(set(ids)):
            out.append(f"{source['kind']}: 로그 목록이 id 오름차순·유일이 아니다")
        if source["used"] != any(r["used"] for r in listed):
            out.append(f"{source['kind']}: 「사용」이 목록의 「읽음」과 어긋난다")
        for r in listed:
            if r.get("attached_to") is not None and r["attached_to"] not in nodes:
                out.append(
                    f"{source['kind']}: {r['id']} 가 붙은 {r['attached_to']} 가 구조에 없다"
                )
    read = [r for kind, r in resources if kind == "vpc_flow_log" and r["used"]]
    for subnet in sorted(claimed):
        vpc = nodes.get(subnet, {}).get("parent_id")
        if not any(
            r.get("attached_to") == subnet
            or (
                r.get("why_code") != "subnet_unattributable"
                and (r.get("attached_to") == vpc or r["level"] == "network_interface")
            )
            for r in read
        ):
            out.append(f"{subnet}: 이 서브넷을 덮는 「읽음」 VPC 흐름 로그가 없다")
    return out


def claims_without_reading(doc: dict, topo: dict) -> list[str]:
    """못 읽은 곳에 대해 판정을 주장한 관측.

    「모름」 → 「고장」 순서의 실제 내용이다(docs/contract-seams.md 「이음매 3」). describe 층이
    ok 가 아니면 up·down·live 를 주장할 수 없다(1층이 판정의 바닥이다). metric 층이
    ok 가 아니면 live 를 주장할 수 없다. `none` 은 «구조적으로 관측 불가» 라서 권한과
    무관하게 항상 참일 수 있다 — 그것만 면제한다.
    """
    seen = places(topo)
    kinds = resource_types(topo)
    status = layer_status(doc)
    bad = []
    for obs in doc["observations"]:
        if obs["status"] == "none":
            continue
        place = seen.get(obs["target_id"])
        if place is None:
            bad.append(f"{obs['target_id']}: 자리를 못 읽었다")
            continue
        if place[1] is None:
            continue  # 계정 컨테이너 — 칸에 속하지 않는다(상태도 없다)
        # 🔴 종류까지 봐야 한다(ADR 0006 O10). 자리만 보면 「서브넷은 못 읽었지만 VPC 는 읽었다」
        # 인 자리에서 **서브넷 상태를 주장해도** 통과한다 — 종류를 모르는 대상도 실패로 다룬다.
        resource_type = kinds.get(obs["target_id"])
        if resource_type is None:
            bad.append(f"{obs['target_id']}: 종류를 못 읽었다")
            continue
        # 🔴 글로벌 노드(DXGW)는 **건너뛰지 않는다**(2026-10-13 전까지는 건너뛰었다 — 그 동안 「어느 리전에서도
        # 못 읽었는데 up」이 통과했다). 칸이 없으면 = 그 계정의 어느 리전도 그 종류를 `ok` 로 읽지 못했다.
        cell = home_cell(doc, place, resource_type)
        if cell is None:
            bad.append(
                f"{obs['target_id']}: 글로벌 {resource_type} 를 어느 리전에서도 읽지 못했는데 "
                f"{obs['status']}"
            )
            continue
        if status.get((*cell, "describe")) != "ok":
            bad.append(
                f"{obs['target_id']}: {resource_type} describe 가 ok 가 아닌데 "
                f"{obs['status']}"
            )
        # 🔴 흐름 로그 근거는 2층 칸에 묶이지 않는다 — 그 행을 받치는 것은 지표 칸이 아니라
        # `sources[].resources[]` 의 「읽음」이고, 그 대조는 `flow_log_problems` 가 한다.
        # 면제를 여기서 빼면 피어링 행은 영원히 적을 수 없다(피어링 지표 칸은 없다).
        flow_log = (obs.get("evidence") or {}).get("metric") == FLOW_LOG
        if (
            obs["status"] == "live"
            and not flow_log
            and status.get((*cell, "metric")) != "ok"
        ):
            bad.append(
                f"{obs['target_id']}: {resource_type} metric 이 ok 가 아닌데 live"
            )
    return bad


def silent_where_read(doc: dict, topo: dict, state_bearing: set[str]) -> list[str]:
    """🔴 O10 검사의 **반대 방향**: 읽은 종류의 대상에 항목이 없다.

    억제 방향(`claims_without_reading`)만 재면 「전부 침묵」도 통과한다 — 실제로 2026-09-23
    오전의 구현이 그랬다(자리 하나가 부분 실패면 그 자리에서 **읽은 것까지** 버렸다). 사람이
    보는 결과는 「권한이 있는 종류를 다시 확인하라는 화면」이었다.

    `state_bearing` 은 §O7 표가 「상태 없음」이라고 적지 **않은** 타입 = 항목이 있어야 하는
    타입이다. 표에서 뽑으므로 이 검사는 정본과 같이 움직인다.
    """
    seen = places(topo)
    kinds = resource_types(topo)
    status = layer_status(doc)
    have = {o["target_id"] for o in doc["observations"]}
    missing = []
    for target_id, place in seen.items():
        if target_id in have or place is None or place[1] is None:
            continue
        resource_type = kinds.get(target_id)
        node_or_edge_type = (
            target_id.split("|")[0] if "|" in target_id else resource_type
        )
        if node_or_edge_type not in state_bearing:
            continue
        # 글로벌 자리도 같은 질문이다 — 그 계정의 어느 리전이든 `ok` 로 읽었으면 항목이 있어야 한다.
        cell = home_cell(doc, place, resource_type)
        if cell is not None and status.get((*cell, "describe")) == "ok":
            missing.append(target_id)
    return sorted(missing)


def o7_type_of(target_id: str, topo: dict) -> str | None:
    """§O7 표의 행 이름. 엣지는 **엣지 타입**이다(coverage 의 종류와 다르다).

    coverage 의 종류는 「그 대상을 읽어 온 응답」(`route` → `route_table`)이고, §O7 의 행은
    「그 대상에 상태를 붙일 수 있나」다. 두 축을 섞으면 `route` 엣지가 route_table 행(상태 없음)을
    보고 「항목을 만들지 마라」가 된다 — §O7 규칙 1 의 유일한 예외를 정확히 뒤집는 것이다.

    🔴 **예외 하나: `association` 은 엣지 타입만으로 행이 정해지지 않는다**(§O7 B안, 승인
    2026-09-23). 같은 타입이 두 응답에서 나오고 한쪽(NACL)에는 상태 필드가 아예 없기 때문에
    행이 둘이다 — 그래서 source 노드의 종류로 행 이름을 수식한다(`association←network_acl`).
    수식한 행이 없으면 기본형 행으로 떨어지고, 그것도 없으면 `None`(= 표에 행이 없다)이다.
    """
    if "|" in target_id:
        edge_type = target_id.split("|")[0]
        source = {e["id"]: e["source"] for e in topo["edges"]}.get(target_id)
        source_type = {n["id"]: n["type"] for n in topo["nodes"]}.get(source or "")
        qualified = f"{edge_type}←{source_type}"
        if qualified in o7_vocabulary():
            return qualified
        return edge_type if edge_type in o7_vocabulary() else None
    return {n["id"]: n["type"] for n in topo["nodes"]}.get(target_id)


def states_outside_the_table(doc: dict, topo: dict) -> list[str]:
    """§O7 표의 마지막 열이 허락하지 않은 상태를 적은 항목.

    세 가지를 한 번에 잡는다:
    - 「상태 없음」 타입에 항목이 있다(§O7 규칙 1) — 같은 사실이 두 곳에 실려 갈라질 수 있다.
    - 표가 `none` 만 준 타입에 판정이 붙었다(또는 그 반대: 읽을 수 있는 타입에 `?` 를 붙였다).
      사람이 보는 차이가 크다 — `?` 는 「우리가 볼 방법이 없다」인데, 볼 수 있는 것에 붙으면 거짓이다.
    - 표에 행이 없는 타입에 항목이 있다(§O7: 「행 없는 타입은 `none` 이 아니라 결함이다」).

    ✅ 해소된 알려진 한 칸(2026-09-23): 엔진은 **NACL↔서브넷 `association`** 에 `none` 을 낸다
    (수집기가 상수를 넣기 때문 — `observe.py:143`). 예전 `association` 행은 `up`·`down` 만
    적었으므로 그 경우를 담은 픽스처가 오면 이 검사가 **코드가 옳은데** 빨개졌다. 표를
    `association←route_table` · `association←network_acl` 두 행으로 가른 것이 정답이었고
    (사용자 승인, B안), 이제 이 검사는 같은 타입의 두 경우를 **따로** 잰다.
    """
    bad = []
    for obs in doc["observations"]:
        o7_type = o7_type_of(obs["target_id"], topo)
        allowed = o7_allowed(o7_type)
        if o7_type is None or allowed is None:
            bad.append(f"{obs['target_id']}: §O7 에 행이 없는 타입")
            continue
        if not allowed:
            bad.append(
                f"{obs['target_id']}: {o7_type} 은 「상태 없음」인데 항목이 있다"
            )
        elif obs.get("deleting") is True:
            # 🔴 지우는 중(1.12.0 · ADR 0006 §O7 「지우는 중은 `down` 이 아니다」) — 표의 어휘 열 밖이다(판정이 아니라
            # 「판정하지 않는다 + 까닭」). 대신 둘을 잰다: `none` 이어야 하고(삭제가 다시 「끊김」이 되지 않게), 1층 판정이
            # 있는 종류여야 한다(`none` 만 내는 종류 — SG·NACL — 에는 지울 수명주기를 읽는 응답이 없다).
            if obs["status"] != "none":
                bad.append(
                    f"{obs['target_id']}: 지우는 중인데 {obs['status']} — none 이어야 한다"
                )
            elif not allowed & {"up", "down"}:
                bad.append(
                    f"{obs['target_id']}: {o7_type} 은 1층 판정이 없는데 「지우는 중」이다"
                )
        elif obs["status"] not in allowed:
            bad.append(
                f"{obs['target_id']}: {o7_type} 이 낼 수 있는 것은 "
                f"{sorted(allowed)} 인데 {obs['status']}"
            )
    return bad


def live_without_metric_layer(doc: dict) -> list[str]:
    if doc["layers"]["metric"]:
        return []
    return [o["target_id"] for o in doc["observations"] if o["status"] == "live"]


def metric_rows_not_skipped_while_off(doc: dict) -> list[tuple[str, str, str, str]]:
    if doc["layers"]["metric"]:
        return []
    return [
        k for k, v in layer_status(doc).items() if k[3] == "metric" and v != "skipped"
    ]


def collapsed_below_threshold(doc: dict) -> list[str]:
    """접는 기준 아래에서 접힌 행. 접힘은 임계값의 함수여야 한다(ADR 0004 「플래핑 중복 제거」).

    `repeat_count` 2 인 행이 임계값 4 와 함께 있으면, 화면은 「2회 반복」 을 보여 주는데
    정본은 4회부터 접는다고 말한 것이다 — 규칙과 데이터가 어긋난 상태다.
    """
    threshold = doc["history"]["flap_collapse_threshold"]
    return [c["target_id"] for c in doc["changes"] if 1 < c["repeat_count"] < threshold]


def out_of_window_changes(doc: dict) -> list[str]:
    """기준 시각 앞이나 관측 시각 뒤의 변경점.

    기준 앞은 소급 불가이므로(ADR 0004 D4 한계 ③) 그 앞의 변경점은 존재할 수 없다.
    관측 시각 뒤의 변경점은 미래다.
    """
    lo = ts(doc["history"]["baseline_at"])
    hi = ts(doc["run"]["observed_at"])
    return [c["target_id"] for c in doc["changes"] if not lo <= ts(c["at"]) <= hi]


def window_start(doc: dict) -> datetime:
    """목록이 덮는 구간의 시작. 보존 창이 기준 시각보다 늦으면 창이 이긴다."""
    h = doc["history"]
    return max(
        ts(h["baseline_at"]),
        ts(doc["run"]["observed_at"]) - timedelta(seconds=h["retention_seconds"]),
    )


def outside_retention_window(doc: dict) -> list[str]:
    """보존 창보다 오래된 변경점.

    창을 넘은 변경점은 산출물에서 **떨어져야** 한다(ADR 0004 「보존 창」). 남아 있으면 화면이
    「이 목록은 7일을 덮는다」고 말하면서 8일 전 줄을 보여주는 상태다.
    """
    lo = window_start(doc)
    return [c["target_id"] for c in doc["changes"] if ts(c["at"]) < lo]


def truncation_mismatch(doc: dict) -> str | None:
    """`truncated` 와 두 상한(보존 창 · 항목 수)의 어긋남 — 문서 한 장으로 잴 수 있는 쪽만.

    ① 기록이 창 밖에서 시작했으면 반드시 `truncated: true` 다 — 아니면 화면이 「이게 처음」이라고
    거짓을 말한다. ② 목록은 `max_changes` 줄을 넘지 않는다 — 넘으면 상한이 실려 있기만 한 숫자다.
    🔴 반대 방향(창 안인데 `true`)은 이제 **정상일 수 있다**: 상한으로 한 번 버린 사실은 이어받는다
    (ADR 0006 O4 · 2026-10-02 개정). 그 근거는 문서에 없으므로 엔진 테스트가 함수 관계로 잰다
    (`test_the_change_cap_keeps_the_newest_rows_and_says_so`).
    """
    h = doc["history"]
    dropped = ts(h["baseline_at"]) < ts(doc["run"]["observed_at"]) - timedelta(
        seconds=h["retention_seconds"]
    )
    if dropped and not h["truncated"]:
        return "창 밖에서 시작한 기록인데 truncated=false"
    if len(doc["changes"]) > h["max_changes"]:
        return f"목록 {len(doc['changes'])}줄 > 상한 {h['max_changes']}"
    return None


def defaults_drift(doc: dict, approved: dict[str, int]) -> dict[str, tuple[int, int]]:
    """픽스처의 수치가 승인값과 다른 칸. (픽스처값, 승인값)."""
    return {
        key: (doc["history"][key], value)
        for key, value in approved.items()
        if doc["history"][key] != value
    }


def out_of_scope_observations(doc: dict, topo: dict) -> list[str]:
    seen = places(topo)
    accounts = set(doc["scope"]["account_ids"])
    regions = set(doc["scope"]["regions"])
    bad = []
    for obs in doc["observations"]:
        place = seen.get(obs["target_id"])
        if place is None:
            bad.append(obs["target_id"])
            continue
        account, region = place
        # 글로벌 자리는 리전 축 밖이다 — 계정만 scope 안이면 된다.
        if account not in accounts or (
            region not in (None, GLOBAL_SLOT) and region not in regions
        ):
            bad.append(obs["target_id"])
    return bad


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


DAY = timedelta(days=1)


@pytest.fixture
def doc() -> dict:
    """minimal.down.json 사본. 음성 테스트는 여기서 한 곳만 망가뜨린다.

    `minimal.json` 이 아니라 이쪽을 쓴다 — `changes[]` 가 비어 있지 않은 유일한 픽스처라서
    이력 쪽 음성 테스트가 «배열이 비어서 통과» 로 끝나지 않는다.
    """
    return load(FIXTURE_DIR / "minimal.down.json")


@pytest.fixture
def topo() -> dict:
    return load(TOPOLOGY_FIXTURE_DIR / "minimal.json")


# ---- 스키마 자체 --------------------------------------------------------------


def test_schema_itself_is_a_valid_json_schema():
    Draft202012Validator.check_schema(SCHEMA)


def test_fixture_dir_is_not_empty():
    # 픽스처가 0개면 아래 파라미터화 테스트 전부가 조용히 통과한다(미측정).
    assert FIXTURES, "fixtures/observation/*.json 이 하나도 없다"


def test_observation_fixtures_are_outside_the_topology_glob():
    """구조 픽스처 디렉터리에 관측 문서를 두면 안 된다.

    `schema/tests/test_schema.py` 는 `fixtures/*.json` 전부를 topology 스키마로 검증한다
    (비재귀 glob). 관측 문서를 그 옆에 두면 그 테스트가 FAIL 한다 — 그게 정상이고, 그래서
    하위 디렉터리에 둔다. 여기서 고정해 두지 않으면 나중에 누가 옮겨 놓고 원인을 못 찾는다.
    """
    assert FIXTURE_DIR.parent == TOPOLOGY_FIXTURE_DIR
    assert not sorted(TOPOLOGY_FIXTURE_DIR.glob("observation*.json"))


def test_status_enum_is_the_four_words_of_the_canonical_doc():
    """상태 어휘의 정본은 `docs/contract-seams.md` 「이음매 3」 표다.

    "다섯 번째 어휘를 만들지 마라" 는 문장이 문서에만 있으면 지켜지지 않는다. 스키마 enum 과
    그 표를 여기서 맞춰 놓으면, 어휘를 늘리는 PR 은 정본을 같이 고쳐야 통과한다(§12 절차).
    """
    text = _doc(SEAMS_PATH)
    section = text.split("## 이음매 3")[1].split("\n## ")[0]
    documented = set(re.findall(r"^\| `([a-z]+)` \|", section, re.MULTILINE))
    assert (
        set(SCHEMA["$defs"]["status"]["enum"])
        == documented
        == {
            "live",
            "up",
            "down",
            "none",
        }
    )


def test_reason_code_enum_matches_topology():
    """두 파일이 같은 사유 열거를 손으로 중복 유지한다 — 어긋남을 여기서 잡는다.

    JSON Schema 에 다른 파일의 $defs 를 안전하게 끌어오는 방법이 없다. 한쪽만 고치면
    엔진이 적은 사유가 관측 문서에서는 무효가 된다.
    """
    assert (
        SCHEMA["$defs"]["reason"]["properties"]["code"]["enum"]
        == TOPOLOGY_SCHEMA["$defs"]["reason"]["properties"]["code"]["enum"]
    )


def test_resource_type_enum_matches_topology():
    """`$defs/resource_type` 를 두 파일이 손으로 중복 유지한다 — `$comment` 가 약속한 검사다.

    어긋나면 두 문서의 coverage 가 **다른 종류 집합**을 말하게 된다: 구조 쪽은 「이 종류를
    못 읽었다」를 적을 수 있는데 관측 쪽은 그 종류를 적을 수 없는(또는 반대) 상태가 되고,
    화면은 한쪽 문서만 믿게 된다.
    """
    assert (
        SCHEMA["$defs"]["resource_type"]["enum"]
        == TOPOLOGY_SCHEMA["$defs"]["resource_type"]["enum"]
    )


def test_coverage_status_enum_matches_topology():
    assert (
        SCHEMA["$defs"]["coverage_entry"]["properties"]["status"]["enum"]
        == TOPOLOGY_SCHEMA["$defs"]["coverage_entry"]["properties"]["status"]["enum"]
    )


def test_adr9_kind_table_is_parseable():
    """표를 못 읽으면 아래 대조가 빈 집합과 비교해 **실패할 수 없다**(통과가 아니라 미측정)."""
    assert len(adr9_source_kinds()) >= 8, adr9_source_kinds()


def test_adr9_kind_parser_reads_rows_not_prose():
    """판별력 — 표에서 행을 지우면 그 출처가 집합에서 사라져야 한다."""
    text = _doc(ADR9_PATH)
    without = "\n".join(
        line for line in text.splitlines() if not line.startswith("| `vpc_flow_log` |")
    )
    assert "vpc_flow_log" not in adr9_source_kinds(without)
    assert "vpc_flow_log" in adr9_source_kinds()


def test_source_kind_enum_matches_adr9():
    """어휘의 정본은 스키마가 아니라 ADR 0009 D4 채택표다(D2 가 그렇게 약속했다).

    🔴 이 대조가 없으면 **읽기가 유료인 출처**(X-Ray · Cost Explorer)가 enum 에 조용히
    들어올 수 있다. 기각표에 있는 것을 어휘에서 빼 두는 것이 「실수로 과금되지 않는다」의
    유일한 구조적 장치다(스키마 `kind` 의 `$comment`).
    """
    assert set(SCHEMA["$defs"]["source"]["properties"]["kind"]["enum"]) == (
        adr9_source_kinds()
    )


def test_source_kind_enum_is_sorted():
    """어휘가 정렬돼 있지 않으면 위 집합 대조가 통과해도 diff 가 매번 흔들린다."""
    enum = SCHEMA["$defs"]["source"]["properties"]["kind"]["enum"]
    assert enum == sorted(enum)


def test_source_scope_unit_is_not_the_resource_type_vocabulary():
    """🔴 두 어휘를 합치면 **관측 출처를 적기 위해 토폴로지 지원 자원 목록이 늘어난다**(ADR 0009 D2-3).

    `$defs/resource_type` 는 `topology.schema.json` 과 바이트 동일이 강제되므로
    (`test_resource_type_enum_matches_topology`) 거기에 `ecs_cluster` 를 넣는 순간 구조 문서도
    ECS 클러스터를 그릴 수 있다고 말하게 된다 — 불변식 ③ 위반이다. 「고객이 켜는 단위」와
    「우리가 그리는 자원」은 우연히 겹칠 뿐 같은 축이 아니다.
    """
    scope = SCHEMA["$defs"]["source"]["properties"]["scope"]
    assert scope["properties"]["unit"] == {"$ref": "#/$defs/source_scope_unit"}
    units = set(SCHEMA["$defs"]["source_scope_unit"]["enum"])
    structural = set(SCHEMA["$defs"]["resource_type"]["enum"])
    assert units != structural
    # 재사용이 불가능했던 이유 자체를 고정한다 — 이 둘은 구조 어휘에 **없다**.
    #
    # 🔴 정정 2026-09-26(수집기 4종 추가): 원래 이 집합은 넷이었고 `ec2_instance`·`eks_cluster` 도
    # 「구조 어휘에 없다」의 증거였다. 이제 둘은 **그린다** → 구조 어휘에 들어갔다. 겹침이 생긴 것은
    # 결함이 아니라 이 검사가 말하던 「우연히 겹칠 뿐 같은 축이 아니다」의 실례다. 두 어휘가 다른
    # 축이라는 사실은 **여전히 겹치지 않는 둘**이 증명한다(ECS 클러스터·Resolver 엔드포인트는
    # 고객이 근거를 켜는 단위이지만 우리가 그리는 자원이 아니다).
    only_here = {"ecs_cluster", "resolver_endpoint"}
    assert only_here <= units
    assert not (only_here & structural)


def test_o7_table_is_parseable():
    """표를 못 읽으면 아래 두 어서션이 빈 집합끼리 비교해 **실패할 수 없다**(통과가 아니라 미측정)."""
    assert len(o7_state_source_types()[0]) >= 14, o7_state_source_types()[0]
    assert len(o7_state_source_types()[1]) >= 7, o7_state_source_types()[1]


def test_o7_parser_reads_rows_not_prose():
    """판별력 — 표에서 행을 지우면 그 타입이 집합에서 사라져야 한다.

    실측 2026-09-23: ADR 에서 실제 행 1개(`account`·`region`)를 지우자 아래 네 검사가 FAIL 했고,
    **헤더 행**을 지웠을 때는 통과했다(`first_column_types` 가 `node_type` 을 빼기 때문이고 그게
    정답이다). 그 실증을 합성 문서로 굳힌다 — 리팩터가 파서를 「산문에서 백틱을 긁는」 것으로
    바꿔 놓으면 위 두 검사는 계속 초록인 채 의미를 잃는다(통과가 아니라 미측정).
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 근거 |\n|---|---|\n"
        "| `vpc` | 있다 |\n| `subnet` · `nat_gateway` | 있다 |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 |\n|---|---|\n| `route` | 있다 |\n\n"
        "### 다음 절 — 여기 `peering` 이 산문으로 나온다\n"
    )
    nodes, edges = o7_state_source_types(full)
    assert nodes == {"vpc", "subnet", "nat_gateway"}, nodes
    assert edges == {"route"}, edges

    nodes2, _ = o7_state_source_types(
        full.replace("| `subnet` · `nat_gateway` | 있다 |\n", "")
    )
    assert nodes2 == {"vpc"}, (
        f"행을 지웠는데 파서가 {sorted(nodes2)} 를 냈다 — 표의 1열이 아니라 산문·다른 열에서 "
        "타입을 긁고 있다면 §O7 커버리지 검사는 영원히 초록이다"
    )


def test_o7_parser_reads_row_names_that_contain_digits():
    """판별력 — 타입 이름의 숫자를 못 읽으면 그 노드는 조용히 「행 없음」이 된다.

    실측 2026-09-26: 문자군이 `[a-z_]+` 였던 동안 §O7 에 `ec2_instance` 행을 **정확히 적어도**
    커버리지 검사가 「행이 없다」로 FAIL 했다(같은 함정을 `adr9_source_kinds()` 가 2026-09-25 에
    이미 밟았다). 반대 방향이 더 위험하다 — 행 이름 오타를 숫자 뒤에 내면 파서가 아무 말도 못 한다.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `ec2_instance` | `State.Name` | 없음 | `live`·`up`·`down` |\n"
        "| `eks_cluster` | `status` | 없음 | `up`·`down` |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 | 최대 어휘 |\n|---|---|---|\n"
        "| `route` | 있다 | `up` |\n\n### 다음 절\n"
    )
    nodes, _ = o7_state_source_types(full)
    assert nodes == {"ec2_instance", "eks_cluster"}, nodes
    assert o7_vocabulary(full)["ec2_instance"] == {"live", "up", "down"}


def test_o7_entry_bearing_parser_reads_the_last_column():
    """판별력 — 「상태 없음」은 **마지막 열**에서만 읽어야 한다.

    합성 표에 함정을 둔다: `attachment` 행은 1층 근거 열에 「없음 — 노드가 든다」를, 마지막 열에
    「상태 없음」을 쓴다. 반대로 `peering` 행은 1층 근거 열에 「상태 없음 같은 문구」가 있지만
    마지막 열은 어휘다 — 행 전체를 긁는 파서는 이 행을 빠뜨린다.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `account` · `region` | — | — | **상태 없음**(컨테이너) |\n"
        "| `vpc` | `State` | 없음 | `up` |\n"
        "| `security_group` | 없음 | 없음 | **`none`** |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 | 최대 어휘 |\n|---|---|---|\n"
        "| `attachment` | 없음 — 노드가 든다 | **상태 없음**(규칙 1) |\n"
        "| `peering` | 상태 없음 같은 문구 | `up`·`down` |\n"
        "| `association←route_table` | `AssociationState.State` | `up`·`down` |\n"
        "| `association←network_acl` | 없음 | **`none`** |\n"
        "\n### 다음 절\n"
    )
    table = o7_vocabulary(full)
    assert table == {
        "account": set(),
        "region": set(),
        "vpc": {"up"},
        "security_group": {"none"},
        "attachment": set(),
        "peering": {"up", "down"},
        # 🔴 수식어 행은 **갈라진 채로** 들어와야 한다. 한 키로 뭉치면 뒤에 오는 행이 앞 행을
        # 덮어써서 NACL 발 연관에 `up` 을 적어도(또는 route_table 발에 `?` 를 적어도) 통과한다.
        "association←route_table": {"up", "down"},
        "association←network_acl": {"none"},
    }, table
    # 마지막 열만 읽었나 — 행 전체를 긁었다면 `peering` 이 빈 집합(=항목 없음)으로 떨어진다.
    assert {t for t, allowed in table.items() if allowed} == {
        "vpc",
        "security_group",
        "peering",
        "association←route_table",
        "association←network_acl",
    }


def test_o7_metric_only_parser_reads_only_table_rows():
    """판별력 — 「2층 전용」은 **표의 행**에서만 읽어야 한다(ADR 0008 D8-1).

    합성 표에 함정을 셋 둔다: ① 산문에 같은 말이 나온다(§O7 의 실제 문서가 그렇다) ② 1층 근거 열에
    나오는 행(`peering`)은 2층 전용이 **아니다** ③ 어휘 열에 나오는 행(`attachment`)만 2층 전용이다.
    산문까지 긁으면 세 종류 전부가 면제 대상이 되고, 그러면 **엣지가 영원히 조용해도** 아무 검사도
    빨개지지 않는다 — 폐기된 종단 추론이 돌아오는 정확한 경로다.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `vpc` | `State` | 없음 | `up` |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `attachment` | 쓰지 않는다 | `BytesIn` | **2층 전용** · `live`·`up`·`down` |\n"
        "| `peering` | 2층 전용 같은 문구 | 없음 | **상태 없음**(규칙 1) |\n"
        "| `route` | `Routes[].State` | 없음 | `up`·`down` |\n\n"
        "🔴 2층 전용 행은 산문에서도 이렇게 불린다 — 이 줄이 걸리면 파서가 틀렸다.\n"
        "\n### 다음 절\n"
    )
    assert o7_metric_only(full) == {"attachment"}
    # 같은 표에서 세 모습이 실제로 갈라지는지 — 2층 전용도 어휘를 내는 행이다(빈 집합이 아니다).
    table = o7_vocabulary(full)
    assert table["attachment"] == {"live", "up", "down"}
    assert table["peering"] == set()
    assert table["route"] == {"up", "down"}


def test_o7_flow_log_only_parser_reads_only_table_rows():
    """판별력 — 「흐름 로그 전용」도 **표의 행 마지막 열**에서만 읽는다(`o7_metric_only` 와 같은 함정).

    함정: ① 산문에 같은 말 ② 다른 열(1층 근거)에 같은 말이 나오는 행(`route`) ③ 「2층 전용」 행
    (`attachment`)은 흐름 로그 전용이 **아니다**. 산문까지 긁으면 라우트 선이 면제되어 **1층을
    읽고도 항목이 없는 라우트 선**이 조용히 통과한다.
    """
    full = (
        "### O7 — 제목\n\n**노드**\n\n"
        "| `node_type` | 1층 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `vpc` | `State` | 없음 | `up` |\n\n"
        "**엣지**\n\n| `edge_type` | 1층 근거 | 2층 | 최대 어휘 |\n|---|---|---|---|\n"
        "| `attachment` | 쓰지 않는다 | `BytesIn` | **2층 전용** · `live`·`up`·`down` |\n"
        "| `peering` | 노드가 든다 | 없음 | **흐름 로그 전용** · `live` |\n"
        "| `route` | 흐름 로그 전용 같은 문구 | 없음 | `up`·`down` |\n\n"
        "🔴 흐름 로그 전용 행은 산문에서도 이렇게 불린다 — 이 줄이 걸리면 파서가 틀렸다.\n"
        "\n### 다음 절\n"
    )
    assert o7_flow_log_only(full) == {"peering"}
    assert o7_metric_only(full) == {"attachment"}
    table = o7_vocabulary(full)
    assert table["peering"] == {"live"}
    assert table["route"] == {"up", "down"}


def test_o7_entry_bearing_set_is_not_everything():
    """정본에서 뽑은 집합이 「전부」거나 「빈 것」이면 아래 ② 방향 검사가 미측정이다."""
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"]) | set(
        TOPOLOGY_SCHEMA["$defs"]["edge_type"]["enum"]
    )
    assert o7_entry_bearing() < declared, "「상태 없음」 행이 하나도 안 걸러졌다"
    assert len(o7_entry_bearing()) >= 12, sorted(o7_entry_bearing())
    # §O7 이 「상태 없음」으로 정한 것들 — 이게 들어오면 항목 없는 카드가 결함이 된다
    assert not o7_entry_bearing() & {"account", "region", "route_table"}
    # 🔴 ADR 0008 D8-1 — 2층 전용 엣지는 **항목을 만들 수 있고**(`o7_entry_bearing()`)
    # **없어도 된다**(`o7_required_entry()` 밖). 둘이 같아지면 어느 한쪽이 거짓이다:
    # 앞쪽에서 빠지면 어태치먼트 선이 측정돼도 표가 금지하고, 뒤쪽에 들어가면 모니터링을 끈
    # 화면이 통째로 결함으로 보고된다.
    assert o7_metric_only_types() == {
        "attachment",
        "endpoint_connection",
        "load_balancer_placement",
    }, sorted(o7_metric_only_types())
    assert o7_metric_only_types() <= o7_entry_bearing()
    assert not o7_metric_only_types() & o7_required_entry()
    # 🔴 ADR 0008 「D4b 개정」 — 피어링 선은 「상태 없음」에서 「흐름 로그 전용」으로 옮겼다.
    # 같은 두 방향이다: 항목을 만들 수 있고(읽어서 왕복 짝을 찾았을 때) 없어도 된다(고객이 로그를
    # 안 켰거나 우리가 못 읽었을 때 — 그 화면이 통째로 결함으로 보고되면 안 된다).
    assert o7_flow_log_only_types() == {"peering"}, sorted(o7_flow_log_only_types())
    assert o7_flow_log_only_types() <= o7_entry_bearing()
    assert not o7_flow_log_only_types() & o7_required_entry()
    # 두 면제는 섞이지 않는다 — 섞이면 `metric_only_without_a_metric` 이 피어링의 흐름 로그 근거를
    # 거부하거나, 그걸 피하려고 느슨해진 검사가 지표 없는 어태치먼트 선을 통과시킨다.
    assert not o7_flow_log_only_types() & o7_metric_only_types()
    assert o7_required_entry(), "요구 집합이 비면 침묵 검사가 통째로 미측정이다"


def test_o7_covers_every_node_type():
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"])
    missing_rows, orphan_rows = o7_gaps(o7_state_source_types()[0], declared)
    assert not missing_rows, (
        f"ADR 0006 §O7 표에 행이 없는 node_type: {sorted(missing_rows)} — §O7 은 「행이 없는 "
        "타입은 none 이 아니라 결함이다. 2차 목록이 들어오면 행을 먼저 더한다」다. 행 없이 "
        "수집만 늘면 그 타입은 상태가 조용히 안 붙고 화면이 「관측 불가」와 구별하지 못한다"
    )
    assert not orphan_rows, (
        f"§O7 에 행은 있으나 node_type enum 에 없는 타입: {sorted(orphan_rows)} — "
        "표가 코드보다 앞서 갔거나 타입 이름이 바뀌었다"
    )


def test_o7_covers_every_edge_type():
    declared = set(TOPOLOGY_SCHEMA["$defs"]["edge_type"]["enum"])
    missing_rows, orphan_rows = o7_gaps(o7_state_source_types()[1], declared)
    assert not missing_rows, f"§O7 표에 행이 없는 edge_type: {sorted(missing_rows)}"
    assert not orphan_rows, (
        f"§O7 에 있으나 edge_type enum 에 없는 타입: {sorted(orphan_rows)}"
    )


def test_o7_splits_association_by_the_response_that_made_it():
    """§O7 B안(사용자 승인 2026-09-23) — 같은 엣지 타입이 두 행을 갖는다.

    사람이 보는 차이가 이 분할의 이유다. 라우트테이블↔서브넷 연관은 AWS 가 상태를 주므로
    끊기면 **빨간 선**이 되어야 하고, NACL↔서브넷 연관은 AWS 가 상태를 주지 않으므로 항상
    **`?` 가 붙은 선**이어야 한다. 한 행으로 뭉치면 둘 중 하나가 거짓이 된다 — 전자를 `?` 로
    퇴화시키면 「끊김」을 못 그리고, 후자를 `up` 으로 읽으면 판정이 통째로 죽어도 같은 그림이다.
    """
    assert o7_vocabulary()["association←route_table"] == {"up", "down"}
    assert o7_vocabulary()["association←network_acl"] == {"none"}
    assert "association" not in o7_vocabulary(), (
        "기본형 행이 남아 있으면 수식어를 못 읽은 엣지가 조용히 그 행으로 떨어진다"
    )
    # enum 대조는 기본형으로 접는다 — 아래 두 검사가 그 접힘에 의존한다.
    assert "association" in o7_state_source_types()[1]
    assert not {t for t in o7_state_source_types()[1] if "←" in t}, sorted(
        o7_state_source_types()[1]
    )
    assert "association" in o7_entry_bearing(), "연관선은 항목을 만드는 타입이다"
    # 「행 없음」(None)과 「상태 없음」(빈 집합)은 섞이면 안 된다 — 전자는 결함, 후자는 의도된 침묵.
    assert o7_allowed("association") == {"up", "down", "none"}
    # 「상태 없음」의 대표는 이제 노드다 — 피어링은 흐름 로그 전용으로 옮겨 `live` 를 낸다(ADR 0008 「D4b 개정」).
    assert o7_allowed("route_table") == set()
    assert o7_allowed("peering") == {"live"}
    assert o7_allowed("mystery") is None


def test_o7_gap_check_detects_a_type_without_a_row():
    """판별력 — 타입을 늘리고 §O7 에 행을 안 더하면 FAIL 해야 한다.

    실측 2026-09-23: 이 검사가 없던 동안 대기 중 PR 2건이 `node_type` 을 각각 4종·3종
    늘리면서 §O7 을 건드리지 않았고, 아무 검사도 빨갛지 않았다.
    """
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"]) | {
        "gateway_load_balancer"
    }
    missing_rows, orphan_rows = o7_gaps(o7_state_source_types()[0], declared)
    assert missing_rows == {"gateway_load_balancer"}
    assert not orphan_rows


def test_o7_gap_check_detects_an_orphan_row():
    """반대 방향 — 표에만 있고 스키마에 없는 행도 잡아야 한다(이름 변경이 이렇게 온다)."""
    declared = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"])
    missing_rows, orphan_rows = o7_gaps(
        o7_state_source_types()[0] | {"vpc_endpoint_service"}, declared
    )
    assert orphan_rows == {"vpc_endpoint_service"}
    assert not missing_rows


def test_the_two_contracts_reject_each_other():
    """관측 문서와 구조 문서가 서로의 스키마를 통과하면 «계약이 둘» 이라는 말이 거짓이 된다."""
    observation = load(FIXTURE_DIR / "minimal.json")
    topology = load(TOPOLOGY_FIXTURE_DIR / "minimal.json")
    assert errors(observation, TOPOLOGY_VALIDATOR), "관측 문서가 구조 스키마를 통과했다"
    assert errors(topology, VALIDATOR), "구조 문서가 관측 스키마를 통과했다"


# ---- 픽스처는 계약을 만족한다 -------------------------------------------------


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_validates(path: Path):
    assert errors(load(path)) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_has_a_topology_pair(path: Path):
    pair = topology_for(path)
    assert pair.exists(), f"{path.name} 의 짝 {pair.name} 이 없다"
    assert (
        load(path)["run"]["topology_collected_at"] == load(pair)["run"]["collected_at"]
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_is_sorted(path: Path):
    doc = load(path)
    assert not unsorted(observation_sort_keys(doc)), (
        "observations 는 target_id 오름차순"
    )
    assert not unsorted(change_sort_keys(doc)), (
        "changes 는 (at, kind, target_id) 오름차순"
    )
    assert not unsorted(coverage_sort_keys(doc)), (
        "coverage 는 (계정,리전,종류,층) 오름차순"
    )
    assert not unsorted(source_sort_keys(doc)), "sources 는 kind 오름차순"
    assert not unsorted(doc["scope"]["account_ids"])
    assert not unsorted(doc["scope"]["regions"])
    assert not unsorted(doc["scope"]["resource_types"])


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_sources_are_coherent(path: Path):
    """ADR 0009 D2 의 두 규율 — 켜진 수가 전체를 넘지 않고, 꺼진 것을 썼다고 적지 않는다."""
    doc = load(path)
    assert sources_over_total(doc) == [], "enabled_ids 가 total_ids 를 넘었다"
    assert sources_used_while_off(doc) == [], "state=off 인데 used=true 다"
    assert len({s["kind"] for s in doc["sources"]}) == len(doc["sources"]), (
        "같은 kind 가 두 줄 있다 — 알약이 같은 출처를 두 번 센다"
    )


def test_the_fixture_set_exercises_every_source_state():
    """ADR 0009 D6 알약 4상태가 전부 픽스처로 재현되지 않으면 그 분기는 미측정이다."""
    seen = {(s["state"], s["used"]) for p in FIXTURES for s in load(p)["sources"]}
    assert ("off", False) in seen, "「지표 기반만」 경로"
    assert ("on", True) in seen, "「추가 근거 N종」 경로"
    assert ("on", False) in seen, (
        "「⚠ 근거 못 읽음」 경로(켜져 있는데 우리가 못 읽었다)"
    )
    assert ("unknown", False) in seen, "「근거 확인 못함」 경로"


def test_some_fixture_reports_a_partial_scope():
    """「3/7 VPC」를 그리는 경로 — 전부/전무만 있으면 D2-2 가 미측정이다."""
    partial = [
        s
        for p in FIXTURES
        for s in load(p)["sources"]
        if "scope" in s and 0 < s["scope"]["enabled_ids"] < s["scope"]["total_ids"]
    ]
    assert partial, "일부만 켜진 scope 를 가진 픽스처가 없다"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_references_resolve(path: Path):
    doc, topo = load(path), load(topology_for(path))
    assert dangling_observations(doc, topo) == []
    assert dangling_changes(doc, topo) == []


def misshapen_was(doc: dict) -> list[str]:
    """`resource_removed.was` 가 «그 줄의 대상» 의 모습이 아닌 곳.

    스키마는 화면이 기대는 키만 요구한다(교차 파일 `$ref` 를 쓰지 않으려고 — `was.$comment`).
    전체 모양·정체는 여기서 잰다. 틀리면 이력 비교 화면(`canvas-principles.md` §3j)이 **다른 카드**를
    「없어짐」 으로 되살리거나, 배치 함수가 모르는 모양을 받아 그때 그림이 조용히 비어 나온다.
    """
    defs = TOPOLOGY_SCHEMA["$defs"]
    shape = {
        key: Draft202012Validator({"$defs": defs, "$ref": f"#/$defs/{key}"})
        for key in ("node", "edge")
    }
    bad = []
    for c in doc["changes"]:
        was = c.get("was")
        if was is None:
            continue
        tid = c["target_id"]
        for key, items in (("node", was["nodes"]), ("edge", was["edges"])):
            for item in items:
                bad += [
                    f"{tid}: {key} {e.message}" for e in shape[key].iter_errors(item)
                ]
        if [e["id"] for e in was["edges"]] != sorted(e["id"] for e in was["edges"]):
            bad.append(f"{tid}: edges 가 id 순이 아니다")
        if c["target_kind"] == "node":
            if [n["id"] for n in was["nodes"]] != [tid]:
                bad.append(f"{tid}: nodes 가 그 카드 하나가 아니다")
            bad += [
                f"{tid}: 붙어 있지 않은 연결 {e['id']}"
                for e in was["edges"]
                if tid not in (e["source"], e["target"])
            ]
        elif was["nodes"] or [e["id"] for e in was["edges"]] != [tid]:
            bad.append(f"{tid}: 연결 줄의 모습은 그 연결 하나다")
    return bad


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_was_is_the_shape_of_its_row(path: Path):
    assert misshapen_was(load(path)) == []


def test_some_fixture_carries_a_was_for_a_card_and_for_a_line():
    """위 검사가 「모습이 하나도 없어서 통과」 로 끝나지 않게 한다 — 카드 줄·연결 줄 둘 다."""
    kinds = {
        c["target_kind"]
        for path in FIXTURES
        for c in load(path)["changes"]
        if c.get("was") and (c["target_kind"] != "node" or c["was"]["edges"])
    }
    assert kinds == {"node", "edge"}, kinds


@pytest.fixture
def hist() -> dict:
    """minimal.history.json 사본 — `was` 가 있는 유일한 픽스처(이력 비교 화면 · 검사 114)."""
    return load(FIXTURE_DIR / "minimal.history.json")


def _was_row(doc: dict, kind: str) -> dict:
    return next(c for c in doc["changes"] if c.get("was") and c["target_kind"] == kind)


def test_was_check_detects_another_cards_shape(hist):
    _was_row(hist, "node")["was"]["nodes"][0]["id"] = (
        "nat_gateway/111122223333/us-east-1/nat-0aaa1111bbbb0000"
    )
    assert misshapen_was(hist)


def test_was_check_detects_a_line_that_did_not_touch_the_card(hist):
    edge = _was_row(hist, "node")["was"]["edges"][0]
    edge["target"] = edge["id"] = (
        "subnet/111122223333/us-east-1/subnet-0aaa1111bbbb4444"
    )
    assert misshapen_was(hist)


def test_was_check_detects_a_shape_the_topology_contract_rejects(hist):
    del _was_row(hist, "node")["was"]["nodes"][0]["account_id"]
    assert misshapen_was(hist)


def test_was_check_detects_a_line_row_carrying_a_card(hist):
    row = _was_row(hist, "edge")
    row["was"]["nodes"] = [_was_row(hist, "node")["was"]["nodes"][0]]
    assert misshapen_was(hist)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_coverage_matches_scope(path: Path):
    doc = load(path)
    assert coverage_holes(doc) == [], "scope × 층 중 coverage 에 없는 칸"
    assert unrequested_coverage(doc) == [], "요청하지 않은 칸이 coverage 에 있다"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_does_not_claim_what_it_could_not_read(path: Path):
    doc, topo = load(path), load(topology_for(path))
    assert claims_without_reading(doc, topo) == []
    assert live_without_metric_layer(doc) == []
    assert metric_rows_not_skipped_while_off(doc) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_says_something_about_what_it_did_read(path: Path):
    """② 방향(ADR 0006 O10) — 읽은 종류의 대상은 화면에서 비어 있으면 안 된다.

    ① 방향만 재면 「전부 침묵」이 만점이다. 사람이 보는 차이가 크다: 권한이 있는 종류까지
    회색으로 비면, 화면은 「아무것도 못 읽었다」와 「서브넷만 못 읽었다」를 똑같이 보여 준다.
    """
    doc, topo = load(path), load(topology_for(path))
    assert silent_where_read(doc, topo, o7_required_entry()) == KNOWN_SILENT.get(
        path.name, []
    )


# 🔴 엔진 산출물 픽스처가 드러낸 **알려진 빈자리** — 고치면 이 표에서 지운다(정확히 같아야 통과한다 · 늘어도 줄어도 FAIL).
#   지금 비어 있다. `lb-targets.json` 의 LB → VPC Lambda(`edge-nlb → orders-fn`) 선이 #137 때 여기 있었고
#   #152 가 고쳤다(상태 단이 구조 문서의 함수로 공유 ENI 를 가린다 → 그 선의 행이 선다 · 픽스처는 엔진이 다시 썼다).
KNOWN_SILENT: dict[str, list[str]] = {}


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_states_stay_inside_the_o7_table(path: Path):
    """픽스처가 §O7 표 밖의 상태를 적지 않는다.

    이 게이트가 없던 동안 픽스처는 표와 **반대되는** 것을 5건 들고 있었고(2026-09-23), 그 데이터로
    `?` 경로가 「측정됐다」고 보였다. 사람이 보는 결과는 「AWS 가 상태를 준 연결에 ? 가 붙은 화면」과
    「상태 없는 첨부선에 초록이 붙은 화면」이다 — 둘 다 없는 사실을 그린 것이다.
    """
    doc, topo = load(path), load(topology_for(path))
    assert states_outside_the_table(doc, topo) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_item_counts_add_up(path: Path):
    """coverage 의 숫자와 실제 항목 수가 맞는다.

    안 맞으면 사람은 「12개 중 9개를 읽었다」는 요약과 9개가 아닌 화면을 동시에 본다 — 어느 쪽이
    맞는지 화면만 보고는 알 수 없고, 보통 요약을 믿는다.
    """
    doc, topo = load(path), load(topology_for(path))
    written, priced, unpriced = item_count_ledger(doc, topo)
    # 2층 전용·흐름 로그 전용 행은 1층 칸의 수에 들지 않는다(원장이 건너뛴다) — 여기서 따로 더한다.
    exempt = sum(
        1
        for o in doc["observations"]
        if o["target_id"].split("|")[0]
        in o7_metric_only_types() | o7_flow_log_only_types()
    )
    assert written == priced, f"coverage 합 {written} ≠ 칸에 속한 항목 {priced}"
    assert priced + unpriced + exempt == len(doc["observations"])


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_metric_item_counts_add_up(path: Path):
    """2층의 숫자도 실제 항목 수와 맞는다.

    1층만 재던 동안 2층의 `item_count` 는 **아무 것과도 비교되지 않았다** — 요약이 「지표로 확인
    12개」라고 말하면서 화면에 근거를 든 카드가 3개여도 조용했다.
    """
    written, cited = metric_item_count_ledger(load(path))
    assert written == cited, (
        f"metric coverage 합 {written} ≠ 지표를 근거로 든 항목 {cited}"
    )


def test_some_fixture_carries_a_metric_only_edge():
    """판별력 ① — 2층 전용 엣지 행을 든 픽스처가 **하나라도** 있어야 한다(ADR 0008 D8-1).

    이 종류는 `silent_where_read` 의 요구 집합에서 빠졌다(2층이 꺼지면 정상적으로 조용하다).
    그 면제 때문에 「구간 지표로 엣지를 판정한다」는 이 릴리스의 본체가 **픽스처에서 한 번도
    지나지 않을 수 있다** — 그러면 통과는 미측정이다. 사람이 보는 결과: 모니터링을 켰는데
    어태치먼트·엔드포인트·로드밸런서 배치 선이 영원히 회색이고, 아무 검사도 빨갛지 않다.
    """
    carried = {
        path.name: sorted(
            {
                o["target_id"].split("|")[0]
                for o in load(path)["observations"]
                if o["target_id"].split("|")[0] in o7_metric_only_types()
            }
        )
        for path in FIXTURES
    }
    assert any(carried.values()), carried


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_metric_only_edges_must_cite_a_metric(path: Path):
    """판별력 ② — 2층 전용 엣지 행의 근거는 **지표**여야 한다.

    🔴 이 검사가 이 릴리스의 핵심을 지킨다. 지표 없이 만든 2층 전용 엣지 행은 정의상
    「양단이 살아 있으니 사이도 흐를 것이다」이고, 그것이 사용자가 2026-09-24 에 「우리가 하려고
    했던 방식이 아니다」라고 판정한 종단 추론이다.
    """
    assert metric_only_without_a_metric(load(path)) == []


def test_metric_only_check_detects_an_edge_made_without_a_metric():
    """판별력 — 위 검사가 실제로 잡는지 세 가지 방식으로 망가뜨려 본다.

    🔴 이것이 없으면 위 검사는 「2층 전용 행이 하나도 없으면 통과」와 구별되지 않는다. 사람이 보는
    결과로 말하면: 모니터링을 켰을 때 어태치먼트 선이 초록인데 그 초록의 근거가 「양쪽 끝이 살아
    있다」뿐인 화면 — 그것이 되돌아오면 이 검사가 빨개져야 한다.
    """
    base = load(FIXTURE_DIR / "minimal.json")
    metric_only = [
        o
        for o in base["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    ]
    assert metric_only, (
        "minimal.json 이 2층 전용 엣지를 잃었다 — 이 음성 테스트가 미측정이 된다"
    )

    no_evidence = deepcopy(base)
    target = next(
        o
        for o in no_evidence["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    )
    target["evidence"] = None
    assert metric_only_without_a_metric(no_evidence), "근거 없는 구간 판정을 놓쳤다"

    describe_api = deepcopy(base)
    target = next(
        o
        for o in describe_api["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    )
    target["evidence"]["api"] = "elasticloadbalancing:DescribeLoadBalancers"
    assert metric_only_without_a_metric(describe_api), (
        "1층 API 로 위장한 구간 판정을 놓쳤다"
    )

    wrong_word = deepcopy(base)
    target = next(
        o
        for o in wrong_word["observations"]
        if o["target_id"].split("|")[0] in o7_metric_only_types()
    )
    target["status"] = "none"
    assert metric_only_without_a_metric(wrong_word), "§O7 어휘 밖의 상태를 놓쳤다"


def test_metric_ledger_check_detects_a_drifted_item_count():
    """판별력 — 2층 원장이 실제로 어긋남을 잡는지 확인한다.

    🔴 `item_count` 를 손으로 고쳐도 조용하던 것이 이 릴리스 전의 상태다. 사람이 보는 결과:
    상단 알약이 「지표로 확인 12」라고 말하는데 화면에서 근거를 든 선은 3개인 화면.
    """
    base = load(FIXTURE_DIR / "minimal.json")
    assert metric_item_count_ledger(base)[0] > 0, (
        "minimal.json 의 2층 칸이 전부 0 이면 아래 변조가 「0 → 1」이 되어 판별력이 약하다"
    )
    drifted = deepcopy(base)
    row = next(c for c in drifted["coverage"] if c["layer"] == "metric")
    row["item_count"] = (row["item_count"] or 0) + 1
    written, cited = metric_item_count_ledger(drifted)
    assert written != cited


def test_some_fixture_has_an_entry_outside_every_cell():
    """판별력 — 「칸 밖 항목」이 0 이면 위 등식의 둘째 항은 미측정이다.

    칸 밖 항목 = SG·NACL·SG 참조의 `?`. 이게 없는 픽스처만 있으면 등식은 `합 == 전체` 로 퇴화하고,
    부르지 않는 종류가 들어오는 날 조용히 깨진다.
    """
    outside = {
        path.name: item_count_ledger(load(path), load(topology_for(path)))[2]
        for path in FIXTURES
    }
    assert any(n > 0 for n in outside.values()), outside


def test_the_fixture_set_exercises_all_three_columns_of_o7():
    """판별력 — 위 게이트가 세 갈래를 **다** 지나야 한다. 한 갈래만 있으면 그 통과는 미측정이다.

    ① 판정(`up`·`down`·`live`) ② `none`(항목은 있고 `?`) ③ 「상태 없음」(항목 자체가 없다).
    ③ 은 없는 것으로 확인한다 — 표가 「상태 없음」이라고 적은 타입이 **구조에는 있는데** 관측에는
    없어야 한다(그게 규칙 1 이다).
    """
    judged, silent_types = set(), set()
    for path in FIXTURES:
        doc, topo = load(path), load(topology_for(path))
        for obs in doc["observations"]:
            judged.add(obs["status"])
        have = {o["target_id"] for o in doc["observations"]}
        for target_id, o7_type in ((n["id"], n["type"]) for n in topo["nodes"]):
            if o7_allowed(o7_type) == set() and target_id not in have:
                silent_types.add(o7_type)
        for edge in topo["edges"]:
            # 🔴 수식어를 모르는 자리다 — 기본형 조회의 합집합을 본다. `association` 은 수식어 행
            # 하나가 어휘를 내므로 「상태 없음」이 아니고, 여기서 침묵으로 세지지 않는 것이 맞다.
            if o7_allowed(edge["type"]) == set() and edge["id"] not in have:
                silent_types.add(edge["type"])
    assert {"up", "down", "live"} <= judged, sorted(judged)
    assert "none" in judged, (
        "어느 픽스처도 `?` 를 들고 있지 않다 — `none` 경로는 미측정이다"
    )
    assert len(silent_types) >= 3, sorted(silent_types)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_history_is_consistent(path: Path):
    doc = load(path)
    assert ts(doc["history"]["baseline_at"]) <= ts(doc["run"]["observed_at"])
    assert out_of_window_changes(doc) == []
    assert collapsed_below_threshold(doc) == []
    assert outside_retention_window(doc) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_marks_truncation_consistently(path: Path):
    """`truncated` 는 보존 창·항목 수 상한과 맞아야 한다(문서로 잴 수 있는 방향 — `truncation_mismatch`).

    보존 창은 지금까지 «실려 있기만 한 숫자» 였다. 어떤 어서션도 읽지 않았으므로
    `truncated: true` 와 창 안의 `baseline_at` 이 함께 있는 모순이 통과하고 있었다.
    """
    assert truncation_mismatch(load(path)) is None


def test_the_approved_defaults_table_is_parseable():
    """표를 못 읽으면 위 테스트들이 «빈 dict 와 비교해 통과» 로 끝난다(미측정).

    그래서 네 칸이 다 있는지, 그리고 스키마가 그 필드들을 실제로 요구하는지 같이 잰다.
    """
    assert set(approved_defaults()) == {
        "resolution_seconds",
        "retention_seconds",
        "flap_collapse_threshold",
        "max_changes",
    }
    assert set(approved_defaults()) <= set(SCHEMA["$defs"]["history"]["required"])


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_uses_the_approved_defaults(path: Path):
    """승인된 수치(ADR 0006 O4)와 픽스처가 어긋나면 대조군이 승인 사실을 배신한다."""
    assert defaults_drift(load(path), approved_defaults()) == {}


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_observations_are_in_scope(path: Path):
    doc, topo = load(path), load(topology_for(path))
    assert out_of_scope_observations(doc, topo) == []


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_timestamps_are_rfc3339(path: Path):
    doc = load(path)
    ts(doc["run"]["observed_at"])
    ts(doc["run"]["topology_collected_at"])
    ts(doc["history"]["baseline_at"])
    for change in doc["changes"]:
        ts(change["at"])
        if "occurred_at" in change:
            ts(change["occurred_at"])
    for obs in doc["observations"]:
        evidence = obs.get("evidence") or {}
        for key in ("first_flow_at", "last_flow_at"):
            if key in evidence:
                ts(evidence[key])


def test_some_fixture_exercises_every_status():
    """네 어휘가 전부 픽스처에 있어야 한다 — 없는 어휘는 렌더러에서 미측정으로 남는다."""
    seen = {o["status"] for path in FIXTURES for o in load(path)["observations"]}
    assert seen == {"live", "up", "down", "none"}


def test_some_fixture_exercises_every_change_kind():
    seen = {c["kind"] for path in FIXTURES for c in load(path)["changes"]}
    assert seen == {"resource_added", "resource_removed", "status_changed"}


def test_some_fixture_turns_the_metric_layer_off():
    """2층 off 축이 없으면 그 축의 규칙 전부가 통과가 아니라 미측정이다."""
    assert any(not load(path)["layers"]["metric"] for path in FIXTURES)


def test_some_fixture_collapses_a_flap():
    assert any(
        c["repeat_count"] > 1 for path in FIXTURES for c in load(path)["changes"]
    )


def test_some_fixture_is_truncated():
    assert any(load(path)["history"]["truncated"] for path in FIXTURES)


# ---- 스키마 어서션의 판별력 ---------------------------------------------------


def test_schema_rejects_unknown_top_level_key(doc):
    doc["latest"] = {}
    assert errors(doc)


def test_schema_rejects_a_fifth_status_word(doc):
    doc["observations"][0]["status"] = "unknown"
    assert errors(doc), "다섯 번째 어휘가 통과했다"


def test_schema_rejects_live_without_metric_evidence(doc):
    obs = doc["observations"][0]
    obs["status"] = "live"
    obs["evidence"] = {
        "api": "ec2:DescribeRouteTables",
        "metric": None,
        "datapoints": None,
    }
    assert errors(doc), "근거 없는 live 가 통과했다"


def test_schema_rejects_live_with_zero_datapoints(doc):
    obs = doc["observations"][0]
    obs["status"] = "live"
    obs["evidence"] = {
        "api": "cloudwatch:GetMetricData",
        "metric": "BytesIn",
        "datapoints": 0,
    }
    assert errors(doc), "데이터포인트 0 인 live 가 통과했다(그건 up 이다)"


def test_schema_rejects_none_without_why(doc):
    # 🔴 픽스처에서 `none` 을 찾지 않는다 — 어떤 픽스처가 `?` 를 들고 있는지에 이 검사의 생사가
    # 걸리면, §O7 정리로 `none` 이 사라지는 날 조용히 죽는다(2026-09-23 실제로 죽었다).
    obs = doc["observations"][0]
    obs["status"] = "none"
    obs["evidence"] = None
    obs["error_count"] = None
    obs["why"] = "AWS 가 이 대상의 상태를 보고하지 않는다"
    assert not errors(doc), (
        "대조군이 이미 반려됐다 — 아래 어서션은 `why` 를 재지 못한다"
    )
    obs["why"] = None
    assert errors(doc), "사유 없는 `?` 가 통과했다"


def test_schema_rejects_partial_without_reasons(doc):
    doc["observations"][0]["partial"] = True
    assert errors(doc)


def test_schema_rejects_missing_error_count(doc):
    del doc["observations"][0]["error_count"]
    assert errors(doc), "생략이 통과하면 렌더러가 부재를 0 으로 읽는다"


def test_schema_rejects_missing_repeat_count(doc):
    del doc["changes"][0]["repeat_count"]
    assert errors(doc)


def _folded(doc: dict) -> dict:
    """접힌 상태 줄 하나(minimal.down.json 의 `repeat_count` 6 줄 — 시작이 없는 옛 판 모양)."""
    return next(c for c in doc["changes"] if c["repeat_count"] > 1)


def test_schema_accepts_a_folded_row_with_or_without_its_start(doc):
    """시작은 **선택 키**다 — 없으면 「모른다」(1.7.0 전에 접힌 줄), 있으면 둘이 함께(ADR 0006 결정 12)."""
    assert errors(doc) == []  # 옛 판 모양(시작 없음)도 유효하다
    row = _folded(doc)
    row["first_at"], row["first_from"] = "2026-01-02T02:49:05Z", "down"
    assert errors(doc) == []
    assert fold_start_after_last(doc) == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda r: r.pop("first_from"), id="시작시각만"),
        pytest.param(lambda r: r.pop("first_at"), id="시작상태만"),
        pytest.param(lambda r: r.update(first_from="flapping"), id="상태아닌값"),
        pytest.param(lambda r: r.update(first_at=None), id="null"),
        pytest.param(lambda r: r.update(repeat_count=1), id="접지않은줄"),
    ],
)
def test_schema_rejects_a_malformed_fold_start(doc, mutate):
    row = _folded(doc)
    row["first_at"], row["first_from"] = "2026-01-02T02:49:05Z", "down"
    mutate(row)
    assert errors(doc), "접힌 반복의 시작이 잘못된 모양인데 통과했다"


def test_schema_rejects_a_fold_start_on_a_configuration_change(doc):
    row = next(c for c in doc["changes"] if c["kind"] == "resource_added")
    row["first_at"], row["first_from"] = "2026-01-02T02:49:05Z", "up"
    assert errors(doc), "구성 변경에 반복의 시작이 붙었다"


def test_fold_start_check_detects_a_start_not_before_the_last(doc):
    row = _folded(doc)
    row["first_at"], row["first_from"] = row["at"], "down"
    assert fold_start_after_last(doc) == [row["target_id"]]
    assert (
        errors(doc) == []
    )  # JSON Schema 는 이 순서를 못 잰다 — 그래서 순수 함수가 있다


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_fold_starts_before_its_last_change(path: Path):
    assert fold_start_after_last(load(path)) == []


def test_some_fixture_carries_a_fold_start():
    """전제 — 시작이 있는 접힌 줄이 픽스처에 하나는 있어야 위 검사가 미측정이 아니다(화면 검사 114 의 입력)."""
    assert any("first_at" in c for path in FIXTURES for c in load(path)["changes"])


def test_schema_rejects_status_change_without_both_ends(doc):
    change = next(c for c in doc["changes"] if c["kind"] == "status_changed")
    change["from"] = None
    assert errors(doc), "앞이 없는 상태 변화가 통과했다"


def test_schema_rejects_config_change_carrying_a_status(doc):
    change = next(c for c in doc["changes"] if c["kind"] == "resource_added")
    change["to"] = "up"
    assert errors(doc), "구성 변경에 상태가 붙었다"


def test_schema_rejects_describe_layer_off(doc):
    doc["layers"]["describe"] = False
    assert errors(doc), "1층 없는 관측이 통과했다"


# ---- 옵션형 근거 출처(ADR 0009) ----------------------------------------------


def a_source(doc: dict, kind: str) -> dict:
    return next(s for s in doc["sources"] if s["kind"] == kind)


def test_schema_rejects_missing_sources(doc):
    """생략이 통과하면 「탐지하지 않았다」와 「전부 꺼져 있다」가 같은 문서가 된다.

    전자는 알약을 띄울 자격이 없고 후자는 「지표 기반만」을 띄워야 한다 — 빈 배열로라도
    적게 만들어야 그 둘이 갈린다.
    """
    del doc["sources"]
    assert errors(doc)


def test_schema_rejects_a_ninth_source_kind(doc):
    """어휘 밖 출처가 통과하면 **읽기가 유료인 소스**가 조용히 들어올 수 있다(ADR 0009 비용)."""
    a_source(doc, "vpc_flow_log")["kind"] = "xray_traces"
    assert errors(doc)


def test_schema_rejects_a_fourth_source_state(doc):
    a_source(doc, "vpc_flow_log")["state"] = "partial"
    assert errors(doc), "상태 어휘가 3값이 아니게 됐다"


def test_schema_rejects_an_unknown_source_without_why(doc):
    del a_source(doc, "eks_network_policy_log")["why"]
    assert errors(doc), "사유 없는 unknown 이 통과하면 화면의 `?` 가 장식이 된다"


def test_schema_rejects_a_source_used_while_off(doc):
    a_source(doc, "container_insights")["used"] = True
    assert errors(doc), "꺼진 출처를 「썼다」고 적을 수 있으면 알약이 거짓말을 한다"


def test_schema_rejects_an_unread_source_without_why(doc):
    """`state: on` + `used: false` 는 **고객이 할 일이 정반대인 두 경우**를 담는다(D2-1).

    「버킷이 막혔다」면 정책을 열면 되고 「이 버전은 안 읽는다」면 할 일이 없다. 사유가 없으면
    알약이 경고를 띄우면서 무엇을 고쳐야 하는지 말하지 못한다.
    """
    del a_source(doc, "tgw_flow_log")["why"]
    assert errors(doc)


def test_schema_rejects_a_source_on_with_nothing_enabled(doc):
    """「켜져 있다」면서 켜진 자원이 0 개면 화면이 「0/1 VPC 켜짐」을 그린다."""
    a_source(doc, "vpc_flow_log")["scope"]["enabled_ids"] = 0
    assert errors(doc)


def test_schema_rejects_a_resource_type_as_a_source_scope_unit(doc):
    """구조 자원 어휘를 scope 단위로 쓰면 안 된다 — 두 어휘는 다른 축이다(D2-3)."""
    a_source(doc, "vpc_flow_log")["scope"]["unit"] = "security_group"
    assert errors(doc)


def test_schema_rejects_a_scope_without_the_total(doc):
    """분모가 없으면 「3 VPC 켜짐」만 남고 사람은 그게 전부인지 알 수 없다."""
    del a_source(doc, "vpc_flow_log")["scope"]["total_ids"]
    assert errors(doc)


def test_source_sort_check_detects_disorder(doc):
    doc["sources"].reverse()
    assert unsorted(source_sort_keys(doc))


def test_source_scope_check_detects_more_enabled_than_total(doc):
    """스키마가 못 잡는 형제 값 비교 — 이 검사가 유일한 방어선이다."""
    a_source(doc, "vpc_flow_log")["scope"]["enabled_ids"] = 99
    assert errors(doc) == [], "스키마가 잡아 버리면 이 검사의 판별력이 측정되지 않는다"
    assert sources_over_total(doc) == ["vpc_flow_log"]


def test_source_off_check_detects_a_used_source(doc):
    """`sources_used_while_off` 의 판별력 — 스키마 밖에서도 같은 규율을 잰다."""
    a_source(doc, "container_insights")["used"] = True
    assert sources_used_while_off(doc) == ["container_insights"]


def test_schema_rejects_a_wrong_schema_version(doc):
    doc["schema_version"] = "2.0.0"
    assert errors(doc)


def test_schema_rejects_missing_history(doc):
    del doc["history"]
    assert errors(doc), "차분의 전제 없이 changes 만 실을 수 없다"


def test_schema_rejects_a_resource_type_as_a_layer(doc):
    doc["coverage"][0]["layer"] = "vpc"
    assert errors(doc), "층 자리에 리소스 종류가 들어갔다"


def test_schema_rejects_failure_without_reason(doc):
    doc["coverage"][0]["status"] = "denied"
    doc["coverage"][0]["reason"] = None
    assert errors(doc)


# ---- 구조 검사의 판별력(이게 없으면 위 구조 테스트는 미측정) -------------------


def test_sort_check_detects_disorder(doc):
    doc["observations"].reverse()
    assert unsorted(observation_sort_keys(doc))


def test_change_sort_check_detects_disorder(doc):
    doc["changes"].reverse()
    assert unsorted(change_sort_keys(doc))


def _one_run_add_and_remove(at: str) -> list[dict]:
    """한 관측에 생김 1 + 없어짐 1 — 없어진 쪽 id 가 **더 작다**(그래야 `kind` 축이 순서를 정한다)."""
    row = {"at": at, "target_kind": "node", "from": None, "to": None, "repeat_count": 1}
    return [
        {
            **row,
            "kind": "resource_added",
            "target_id": "nat_gateway/111122223333/us-east-1/nat-0fff9999",
        },
        {
            **row,
            "kind": "resource_removed",
            "target_id": "nat_gateway/111122223333/us-east-1/nat-0aaa1111",
        },
    ]


def test_change_sort_key_is_the_documented_order():
    """헬퍼의 키 = 스키마가 적은 `(at, kind, target_id)`. 문서와 헬퍼가 갈라지면 여기서 FAIL 한다."""
    comment = SCHEMA["$defs"]["history"]["properties"]["max_changes"]["$comment"]
    assert f"({', '.join(CHANGE_SORT_FIELDS)})" in comment
    rows = _one_run_add_and_remove("2026-09-01T00:05:00Z")
    assert change_sort_keys({"changes": rows}) == [
        tuple(c[f] for f in CHANGE_SORT_FIELDS) for c in rows
    ]


def test_change_sort_accepts_an_add_and_a_remove_in_one_run(doc):
    """엔진 순서로 바르게 정렬된 문서(한 관측에 생김 + 없어짐)는 정렬 검사를 **통과해야** 한다.

    옛 키 `(at, target_id)` 는 이 문서를 「어긋났다」고 했다 — 없어진 쪽 id 가 더 작아서다.
    대조군: 같은 두 줄을 뒤집으면(없어짐이 먼저) 어긋났다고 판정한다.
    """
    at = doc["changes"][-1]["at"]
    later = (
        datetime.fromisoformat(at.replace("Z", "+00:00")) + timedelta(minutes=5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = _one_run_add_and_remove(later)
    doc["changes"] = doc["changes"] + rows
    assert not unsorted(change_sort_keys(doc))
    doc["changes"] = doc["changes"][:-2] + rows[::-1]
    assert unsorted(change_sort_keys(doc))


def test_coverage_sort_check_detects_disorder(doc):
    doc["coverage"].reverse()
    assert unsorted(coverage_sort_keys(doc))


def test_reference_check_detects_a_dangling_observation(doc, topo):
    doc["observations"][0]["target_id"] = "route/rtb-0aaa1111bbbb6666/10.0.0.0_8"
    assert dangling_observations(doc, topo)


def test_reference_check_detects_a_mislabelled_kind(doc, topo):
    doc["observations"][0]["target_kind"] = "node"
    assert dangling_observations(doc, topo), "엣지를 노드라고 적은 것이 통과했다"


def test_reference_check_detects_a_dangling_added_resource(doc, topo):
    change = next(c for c in doc["changes"] if c["kind"] == "resource_added")
    change["target_id"] = "subnet/111122223333/us-east-1/subnet-0nonexistent"
    assert dangling_changes(doc, topo)


def test_reference_check_tolerates_a_removed_resource(doc, topo):
    """사라진 것은 구조 스냅샷에 없다 — 그것까지 매달림으로 잡으면 「지워졌다」를 기록할 수 없다."""
    removed = [c for c in doc["changes"] if c["kind"] == "resource_removed"]
    assert removed, "면제 경로를 재려면 resource_removed 가 한 건은 있어야 한다"
    assert all(c["target_id"] not in topology_index(topo) for c in removed)
    assert dangling_changes(doc, topo) == []


def test_coverage_check_detects_a_hole(doc):
    doc["coverage"].pop()
    assert coverage_holes(doc)


def test_coverage_check_detects_an_unrequested_cell(doc):
    doc["coverage"].append(dict(doc["coverage"][0], region="eu-west-1"))
    assert unrequested_coverage(doc)


def a_claimed_type(doc: dict, topo: dict) -> str:
    """판정을 적은 항목이 실제로 있는 종류 하나. 없으면 아래 대조군이 미측정이다.

    🔴 종류축이 생긴 뒤로는 「coverage 첫 행을 망가뜨린다」가 **대조군을 조용히 죽인다** —
    첫 행의 종류에 항목이 하나도 없으면 억제 검사는 볼 것이 없어 통과한다. 그래서 망가뜨릴
    칸을 항목에서 거꾸로 고른다.
    """
    kinds = resource_types(topo)
    for obs in doc["observations"]:
        if obs["status"] != "none" and obs["target_id"] in kinds:
            return kinds[obs["target_id"]]
    raise AssertionError(
        "판정을 적은 항목이 없다 — 이 픽스처로는 억제 방향을 잴 수 없다"
    )


def test_order_of_judgement_check_detects_a_claim_on_a_denied_region(doc, topo):
    """「모름」 → 「고장」 의 판별력. 못 읽은 종류에 판정을 적으면 잡혀야 한다."""
    kind = a_claimed_type(doc, topo)
    describe = next(
        c
        for c in doc["coverage"]
        if c["layer"] == "describe" and c["resource_type"] == kind
    )
    describe["status"] = "denied"
    describe["reason"] = {
        "code": "access_denied",
        "api": "sts:AssumeRole",
        "message": None,
    }
    assert claims_without_reading(doc, topo)


def test_order_of_judgement_check_detects_live_without_metric_coverage(doc, topo):
    obs = next(
        o
        for o in doc["observations"]
        if resource_types(topo).get(o["target_id"]) == a_claimed_type(doc, topo)
    )
    metric = next(
        c
        for c in doc["coverage"]
        if c["layer"] == "metric"
        and c["resource_type"] == resource_types(topo)[obs["target_id"]]
    )
    metric["status"] = "throttled"
    metric["reason"] = {
        "code": "throttled",
        "api": "cloudwatch:GetMetricData",
        "message": None,
    }
    obs["status"] = "live"
    obs["evidence"] = {
        "api": "cloudwatch:GetMetricData",
        "metric": "BytesIn",
        "datapoints": 3,
        "window_seconds": 300,
    }
    assert claims_without_reading(doc, topo)


def test_order_of_judgement_check_exempts_structurally_unobservable(doc, topo):
    """`none` 은 권한과 무관하게 참일 수 있다. 면제가 없으면 회색과 `?` 가 섞인다."""
    describe = next(c for c in doc["coverage"] if c["layer"] == "describe")
    describe["status"] = "denied"
    describe["reason"] = {
        "code": "access_denied",
        "api": "sts:AssumeRole",
        "message": None,
    }
    doc["observations"] = [o for o in doc["observations"] if o["status"] == "none"]
    assert claims_without_reading(doc, topo) == []


def test_entry_check_detects_silence_where_it_read(doc, topo):
    """② 방향의 판별력 — 읽은 종류의 항목을 지우면 잡혀야 한다.

    이 짝이 없으면 위 `test_fixture_says_something_about_what_it_did_read` 는 「대상이 애초에
    없어서」 통과할 수 있다(억제 방향의 미측정 함정과 같은 모양이다).
    """
    dropped = next(
        o for o in doc["observations"] if o["target_id"].startswith("subnet/")
    )
    doc["observations"] = [
        o for o in doc["observations"] if o["target_id"] != dropped["target_id"]
    ]
    assert silent_where_read(doc, topo, o7_required_entry()) == [dropped["target_id"]]


def test_entry_check_tolerates_silence_where_it_could_not_read(doc, topo):
    """대조 — 같은 항목을 지우면서 그 종류를 denied 로 바꾸면 침묵이 **정답**이다.

    사람이 보는 차이가 여기서 갈린다: 권한이 없어 비어 있는 자리는 「확인하지 못했다」로 표시돼야
    하고, 권한이 있는데 비어 있으면 그건 결함이다. 두 검사가 같은 데이터에서 반대로 움직여야 한다.
    """
    dropped = next(
        o for o in doc["observations"] if o["target_id"].startswith("subnet/")
    )
    doc["observations"] = [
        o for o in doc["observations"] if o["target_id"] != dropped["target_id"]
    ]
    for row in doc["coverage"]:
        if row["resource_type"] == "subnet" and row["layer"] == "describe":
            row["status"] = "denied"
            row["reason"] = {
                "code": "access_denied",
                "api": "ec2:DescribeSubnets",
                "message": None,
            }
            row["item_count"] = None
    assert silent_where_read(doc, topo, o7_required_entry()) == []


def test_a_line_to_a_stateless_lambda_still_needs_its_row(doc, topo):
    """#152 — LB → Lambda 선(`load_balancer_target`)은 끝 카드(`lambda_function`)가 「상태 없음」이어도 **행이 있어야 한다.**

    선의 칸은 그 LB 의 `load_balancer` 칸이고(그 LB 의 `DescribeTargetHealth` 가 근거) 끝 카드의 종류가 아니다. 끝 카드 종류로
    면제하면 엔진이 그 선을 다시 잃어도(상태 단이 Lambda 를 읽지 않는다) 계약 쪽 검사가 조용하다 — 고객은 모니터링을 켜도
    그 선에서 정상·이상을 못 본다. 반대로 함수 카드 자신에는 행이 없어야 한다(§O7 「상태 없음」).
    """
    lb_id = next(n["id"] for n in topo["nodes"] if n["type"] == "load_balancer")
    lb_node = next(n for n in topo["nodes"] if n["id"] == lb_id)
    fn = f"lambda_function/{lb_node['account_id']}/{lb_node['region']}/orders-fn"
    topo["nodes"].append(
        {
            "id": fn,
            "type": "lambda_function",
            "account_id": lb_node["account_id"],
            "region": lb_node["region"],
            "parent_id": f"region/{lb_node['account_id']}/{lb_node['region']}",
            "native_id": "orders-fn",
            "name": "orders-fn",
            "attributes": {"function_name": "orders-fn"},
            "partial": False,
        }
    )
    line = f"load_balancer_target|{lb_id}|{fn}"
    topo["edges"].append(
        {
            "id": line,
            "type": "load_balancer_target",
            "source": lb_id,
            "target": fn,
            "directed": True,
            "attributes": {"target_count": 1},
            "partial": False,
        }
    )
    assert silent_where_read(doc, topo, o7_required_entry()) == [line]
    doc["observations"].append(
        {
            "target_id": line,
            "target_kind": "edge",
            "status": "up",
            "why": None,
            "evidence": {
                "api": "elasticloadbalancing:DescribeTargetHealth",
                "metric": None,
                "datapoints": None,
                "window_seconds": None,
            },
            "error_count": 0,
            "partial": False,
            "targets": {"total": 1, "healthy": 0, "unhealthy": 0},
        }
    )
    doc["observations"].sort(key=lambda o: o["target_id"])
    assert silent_where_read(doc, topo, o7_required_entry()) == []
    assert states_outside_the_table(doc, topo) == []
    assert errors(doc) == []
    # 함수 카드에 행을 붙이면 §O7 「상태 없음」 위반이다 — 선과 카드가 같은 근거를 두 번 말한다
    doc["observations"].append(
        {**doc["observations"][-1], "target_id": fn, "target_kind": "node"}
    )
    assert [p.split(":")[0] for p in states_outside_the_table(doc, topo)] == [fn]


def test_ledger_check_detects_a_drifted_item_count(doc, topo):
    """숫자만 1 올려도 잡혀야 한다 — 항목을 지우면 양쪽이 같이 줄어 등식은 살아 있다."""
    row = next(
        c for c in doc["coverage"] if c["layer"] == "describe" and c["status"] == "ok"
    )
    row["item_count"] += 1
    written, priced, _ = item_count_ledger(doc, topo)
    assert written != priced


def test_table_check_detects_a_state_on_a_no_state_type(doc, topo):
    """§O7 규칙 1 — 「상태 없음」 타입에 항목을 만들면 잡힌다.

    사람이 보는 결과: 같은 사실이 노드 카드와 첨부선 **두 곳**에 실리고, 한쪽만 갱신되면 화면이
    스스로와 어긋난다(어느 쪽이 맞는지 화면만 보고는 알 수 없다).
    """
    no_state = next(n["id"] for n in topo["nodes"] if o7_allowed(n["type"]) == set())
    doc["observations"][0]["target_id"] = no_state
    doc["observations"][0]["target_kind"] = "node"
    assert states_outside_the_table(doc, topo)


def test_table_check_detects_a_word_the_table_did_not_allow(doc, topo):
    """표가 `up` 만 준 타입에 `live` 를 적으면 잡힌다 — 어휘는 타입마다 다르다."""
    subnet = next(
        o for o in doc["observations"] if o["target_id"].startswith("subnet/")
    )
    # 2026-10-14(QA B P2) 서브넷에 전환 상태 `down` 이 생겼다 — 대조군의 뜻(「표에 `live` 가 없다」)은 그대로다
    assert "live" not in o7_vocabulary()["subnet"], (
        "전제가 바뀌었다 — 이 대조군을 다시 골라라"
    )
    subnet["status"] = "live"
    assert states_outside_the_table(doc, topo)


def _association(doc: dict, source_type: str) -> dict:
    """그 종류의 응답에서 나온 연관선 항목 하나. 없으면 어서션이 무력해지므로 여기서 죽는다."""
    found = [
        o
        for o in doc["observations"]
        if o["target_id"].startswith(f"association|{source_type}/")
    ]
    assert found, (
        f"픽스처에 {source_type} 발 연관선 항목이 없다 — 아래 대조군이 「항목이 없어서 통과」 로 "
        "끝난다(2026-09-23 이전이 그 상태였다: NACL 은 어느 픽스처에도 0회였다)"
    )
    return found[0]


def test_table_check_detects_a_question_mark_on_a_route_table_association(doc, topo):
    """§O7 B안의 한쪽 방향 — 판정할 수 있는 연관선을 `?` 로 퇴화시키면 잡힌다.

    사람이 보는 결과: 라우트테이블 연관이 끊겨도 화면은 계속 「확인 불가」라고 말한다. 끊김을
    그릴 수 있는 선이 영원히 회색이 되는 것이고, 판정 코드가 죽은 것과 화면상 구별되지 않는다.
    A 안(두 경우를 `none`·`up`·`down` 한 행으로 합치기)을 버린 이유가 이 방향이다 — 합친 행은
    이 퇴화를 통과시킨다.
    """
    _association(doc, "route_table")["status"] = "none"
    assert states_outside_the_table(doc, topo)


def test_table_check_detects_a_judgement_on_a_nacl_association(doc, topo):
    """반대 방향 — AWS 가 상태를 주지 않는 연관선에 판정을 적으면 잡힌다.

    사람이 보는 결과: NACL↔서브넷 선이 **영원히 초록**이다. 수집기가 넣은 상수(`associated`)를
    읽은 것이므로, 실제로 무슨 일이 일어나도 그 선은 바뀌지 않는다.
    """
    _association(doc, "network_acl")["status"] = "up"
    assert states_outside_the_table(doc, topo)


def _deleting_subnet(doc: dict) -> dict:
    """서브넷 행 하나를 「지우는 중」으로 바꾼다(1.12.0 · ADR 0006 §O7 「지우는 중은 `down` 이 아니다」)."""
    row = next(o for o in doc["observations"] if o["target_id"].startswith("subnet/"))
    row.update(
        status="none",
        why="삭제 중 — AWS 가 보고한 state = 'deleting'",
        error_count=0,
        deleting=True,
    )
    return row


def test_a_deleting_row_is_none_and_passes_the_table(doc, topo):
    """지우는 중 = `none` + `deleting` — 스키마와 §O7 오라클 둘 다 통과한다(대조군)."""
    _deleting_subnet(doc)
    assert errors(doc) == []
    assert states_outside_the_table(doc, topo) == []


@pytest.mark.parametrize("status", ["down", "up"])
def test_a_deleting_row_cannot_carry_a_judgement(doc, topo, status):
    """🔴 `down` 이면 일부러 지운 자원이 다시 「끊김」이 되고(QA C), `up` 이면 지우는 자원을 「살아 있다」로 그린다."""
    _deleting_subnet(doc)["status"] = status
    assert errors(doc)
    assert any("지우는 중인데" in row for row in states_outside_the_table(doc, topo))


def test_deleting_false_is_not_a_second_spelling(doc):
    """부재 = 지우는 중이 아니다 — `false` 를 받으면 같은 사실에 바이트 표현이 둘이 된다."""
    _deleting_subnet(doc)["deleting"] = False
    assert errors(doc)


def test_a_deleting_row_on_a_none_only_type_is_caught(doc, topo):
    """`none` 밖에 못 내는 종류(NACL)에는 지울 수명주기를 읽는 응답이 없다 — 「지우는 중」은 지어낸 것이다."""
    nacl = next(
        o for o in doc["observations"] if o["target_id"].startswith("network_acl/")
    )
    nacl["deleting"] = True
    assert any(
        "1층 판정이 없는데" in row for row in states_outside_the_table(doc, topo)
    )


def test_table_check_detects_a_type_without_a_row(doc, topo):
    """표에 행이 없는 타입은 `none` 이 아니라 **결함**이다(§O7).

    조용히 넘기면 새 리소스 종류가 들어온 날 그 종류만 상태 없이 그려지고, 화면은 그것을
    「관측 수단이 없다」와 구별해 주지 못한다.
    """
    doc["observations"][0]["target_id"] = "mystery|a|b"
    doc["observations"][0]["target_kind"] = "edge"
    assert any("행이 없는 타입" in row for row in states_outside_the_table(doc, topo))


def test_metric_layer_check_detects_live_while_off(doc):
    doc["layers"]["metric"] = False
    doc["observations"][0]["status"] = "live"
    assert live_without_metric_layer(doc)


def test_metric_layer_check_detects_a_non_skipped_row_while_off(doc):
    doc["layers"]["metric"] = False
    assert metric_rows_not_skipped_while_off(doc), (
        "2층을 껐는데 ok 로 적힌 칸이 통과했다"
    )


def test_window_check_detects_a_change_before_the_baseline(doc):
    # 픽스처의 시각에서 계산한다 — 날짜를 박아 두면 baseline 을 옮기는 순간 이 짝이 조용히 죽는다
    # (실제로 한 번 죽었다: 보존 창 어서션을 넣으며 baseline 을 창 밖으로 옮겼을 때).
    doc["changes"][0]["at"] = stamp(ts(doc["history"]["baseline_at"]) - DAY)
    assert out_of_window_changes(doc)


def test_window_check_detects_a_change_after_the_observation(doc):
    doc["changes"][0]["at"] = "2027-01-01T00:00:00Z"
    assert out_of_window_changes(doc)


def test_retention_check_detects_a_change_older_than_the_window(doc):
    # 기준 시각은 창 밖이므로(minimal.down) 창이 하한을 정한다 — 그 앞의 변경점은 떨어져야 했다.
    doc["changes"][0]["at"] = stamp(window_start(doc) - DAY)
    assert outside_retention_window(doc)


def test_truncation_check_detects_a_list_longer_than_the_cap(doc):
    # 상한을 지금 줄 수보다 1 작게 — 넘친 줄이 남아 있으면 상한이 실려 있기만 한 숫자다.
    # (옛 짝 「창 안인데 true」는 2026-10-02 에 정상 경로가 됐다 — 상한으로 버린 사실은 이어받는다.)
    assert doc["changes"], "줄이 0이면 이 짝은 판별력이 없다"
    doc["history"]["max_changes"] = len(doc["changes"]) - 1 or 1
    if len(doc["changes"]) == 1:
        doc["changes"].append(copy.deepcopy(doc["changes"][0]))
    assert truncation_mismatch(doc)


def test_truncation_check_allows_a_cap_flag_inside_the_window(doc):
    # 판별력의 반대편: 상한으로 버린 기록은 창 안이어도 true 다 — 이것을 거부하면 엔진이 막힌다.
    doc["history"]["baseline_at"] = doc["changes"][0]["at"]
    doc["history"]["truncated"] = True
    assert truncation_mismatch(doc) is None


def test_truncation_check_detects_a_missing_flag(doc):
    # 반대 방향: 창 밖에서 기록이 시작됐는데 truncated 가 false 다.
    doc["history"]["truncated"] = False
    assert truncation_mismatch(doc)


def test_defaults_check_detects_a_drifted_number(doc):
    # 승인값에서 파생시킨다 — 숫자를 박으면 그 숫자가 승인되는 날 이 짝이 조용히 죽는다.
    doc["history"]["retention_seconds"] = approved_defaults()["retention_seconds"] + 1
    assert defaults_drift(doc, approved_defaults())


def test_flap_check_detects_a_collapse_below_the_threshold(doc):
    doc["changes"][0]["repeat_count"] = 2
    assert collapsed_below_threshold(doc)


def test_scope_check_detects_an_observation_outside_scope(doc, topo):
    doc["scope"]["regions"] = ["eu-west-1"]
    assert out_of_scope_observations(doc, topo)


def test_place_parser_reports_failure_instead_of_guessing():
    """자리를 못 읽으면 None 이다. 0 이나 빈 튜플로 뭉개면 위 검사들이 조용히 통과한다."""
    assert place_of("account/111122223333") == ("111122223333", None)
    assert place_of("region/111122223333/us-east-1") == ("111122223333", "us-east-1")
    assert place_of("vpc/111122223333/us-east-1/vpc-0aaa") == (
        "111122223333",
        "us-east-1",
    )
    # 🔴 **엔진이 실제로 만드는** 엣지 id 여야 판별력이 있다. 예전 어서션은 가짜 슬래시 꼴
    # (`association/rtb-0aaa/subnet-0bbb`)이라 구획이 3개뿐이어서 어떤 구현이든 None 을 냈다.
    assert (
        place_of(
            "association|route_table/111122223333/us-east-1/rtb-0aaa"
            "|subnet/111122223333/us-east-1/subnet-0bbb"
        )
        is None
    )


def test_pair_resolution_detects_a_wrong_topology_timestamp(doc):
    doc["run"]["topology_collected_at"] = "2020-01-01T00:00:00Z"
    assert (
        doc["run"]["topology_collected_at"]
        != load(TOPOLOGY_FIXTURE_DIR / "minimal.json")["run"]["collected_at"]
    )


# ------------------------------------------------- id 문법 (2026-09-23 결함의 자리)
#
# 🔴 이 절이 없던 동안 무엇이 미측정이었나: `target_id` 가 **노드 id 패턴만** 강제했고
# (`^[a-z0-9_]+/…`), 엔진의 엣지 id 는 `|` 구분이므로 **엣지 관측은 하나도 검증을 통과할 수
# 없었다.** 그런데 이 파일은 187 그린이었다 — 픽스처가 엔진이 만들지 않는 슬래시 꼴
# (`association/rtb-…/subnet-…`)을 적어 둬서 픽스처와 스키마가 **서로만** 맞았기 때문이다.
# 결함은 engine/tests/test_observe.py 가 실제 산출물을 검증해서야 드러났다.
# 교훈: 계약 픽스처가 생산 산출과 다른 문법이면 그 그린은 미측정이다.

NODE_ID_RE = re.compile(SCHEMA["$defs"]["node_id"]["pattern"])
EDGE_ID_RE = re.compile(SCHEMA["$defs"]["edge_id"]["pattern"])


def engine_edge_id(edge: dict) -> str:
    """engine/src/nettopo/collect.py:246 `_edge_id()` 의 사본.

    🔴 계약 층은 생산 코드를 import 하지 않는다(그래서 사본이다). 대신 엔진 쪽 절반을
    engine/tests/test_observe.py 의 `test_observation_satisfies_the_schema` 가 든다 — 그쪽은
    실제 산출물을 이 스키마로 검증하므로, 두 문법이 갈라지면 한쪽이 반드시 FAIL 한다.
    """
    parts = [edge["type"], edge["source"], edge["target"]]
    tail = edge["id"].split("|")[3:]
    return "|".join(parts + tail)


def test_id_patterns_match_the_topology_contract():
    """두 스키마의 `node_id`·`edge_id` 는 **바이트 동일**해야 한다.

    JSON Schema 는 파일 경계를 넘는 `$ref` 를 resolver 없이 못 쓰므로 사본이다. 한쪽만 고치면
    「구조는 받는데 관측은 거절한다」가 되고, 증상은 엣지 관측만 조용히 사라지는 것이다.
    """
    for name in ("node_id", "edge_id"):
        assert name in TOPOLOGY_SCHEMA["$defs"], f"topology 계약에 {name} 정의가 없다"
        assert (
            SCHEMA["$defs"][name]["pattern"]
            == TOPOLOGY_SCHEMA["$defs"][name]["pattern"]
        ), f"{name} 패턴이 두 스키마에서 다르다"
    # 판별력: 두 패턴이 실제로 갈라져야 한다. 같으면 아래 kind 검사가 전부 무력해진다.
    node = "vpc/111122223333/us-east-1/vpc-0aaa"
    edge = f"association|{node}|{node}"
    assert NODE_ID_RE.fullmatch(node) and not EDGE_ID_RE.fullmatch(node)
    assert EDGE_ID_RE.fullmatch(edge) and not NODE_ID_RE.fullmatch(edge)


@pytest.mark.parametrize(
    "path", sorted(TOPOLOGY_FIXTURE_DIR.glob("*.json")), ids=lambda p: p.name
)
def test_topology_fixture_edge_ids_are_what_the_engine_produces(path: Path):
    """픽스처 엣지 id = `<type>|<source>|<target>[|<qualifier>]`.

    🔴 이것이 없던 동안 fixtures/minimal.json 의 엣지 3개는 손으로 쓴 값이었고,
    fixtures/multi-account.json 은 **id 가 중복**이었다(계정이 달라도 같은 문자열 → 엔진 문법에서는
    갈린다). 중복 id 는 `topology_index()` 의 dict 에서 조용히 하나로 접혀 참조 검사를 무력화한다.
    """
    edges = load(path)["edges"]
    wrong = [
        (e["id"], engine_edge_id(e)) for e in edges if e["id"] != engine_edge_id(e)
    ]
    assert wrong == [], f"엔진 문법과 다른 엣지 id {len(wrong)}개: {wrong[:2]}"
    ids = [e["id"] for e in edges]
    assert len(ids) == len(set(ids)), (
        "엣지 id 가 중복이다 — 참조 검사가 조용히 무력해진다"
    )
    assert all(EDGE_ID_RE.fullmatch(i) for i in ids)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_target_id_syntax_agrees_with_target_kind(path: Path):
    """`target_kind` 가 말하는 것과 id 문법이 어긋나면 잡는다 — 양방향으로 잰다."""
    doc = load(path)
    rows = [
        (row["target_id"], row["target_kind"])
        for row in doc["observations"] + doc["changes"]
    ]
    kinds = {kind for _, kind in rows}
    # 🔴 판별력 가드: 한 종류만 있으면 아래 루프가 절반만 돈다(= 미측정).
    assert kinds == {"node", "edge"}, f"{path.name} 에 {sorted(kinds)} 만 있다"
    for target_id, kind in rows:
        expected = NODE_ID_RE if kind == "node" else EDGE_ID_RE
        other = EDGE_ID_RE if kind == "node" else NODE_ID_RE
        assert expected.fullmatch(target_id), f"{kind} 인데 문법이 다르다: {target_id}"
        assert not other.fullmatch(target_id), f"두 문법 다 통과한다: {target_id}"


def test_schema_rejects_a_node_pattern_only_target_id(doc):
    """🔴 2026-09-23 결함의 회귀 브레이크 — 엣지 관측이 거절되면 FAIL 해야 한다.

    스키마를 되돌려(`target_id` → node_id 만) 그 상태가 실제로 픽스처를 깨는지 잰다. 깨지지
    않으면 픽스처에 엣지 관측이 없다는 뜻이고, 그때는 이 파일이 다시 미측정이 된다.
    """
    edges = [o for o in doc["observations"] if o["target_kind"] == "edge"]
    assert edges, "엣지 관측이 없으면 이 브레이크가 아무것도 재지 않는다"
    broken = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    broken["$defs"]["target_id"] = {"$ref": "#/$defs/node_id"}
    assert Draft202012Validator(broken).iter_errors(doc), (
        "node_id 만 허용해도 픽스처가 통과했다 — 엣지 id 문법이 노드와 같아졌다"
    )


def test_schema_rejects_an_edge_id_with_a_pipe_in_a_node_segment():
    """구분자 유일성의 대조군 — 노드 id 에 `|` 가 들어오면 엣지 id 파싱이 모호해진다."""
    assert not NODE_ID_RE.fullmatch("vpc/111122223333/us-east-1/vpc-0a|b")
    assert not EDGE_ID_RE.fullmatch(
        "association|vpc/111122223333/us-east-1/vpc-0a|b|vpc/111122223333/us-east-1/v"
    )


def test_place_of_check_detects_an_edge_id(doc, topo):
    """`place_of` 가 엣지 id 를 노드처럼 읽으면 잡힌다 — 그 방어를 지운 상태를 흉내낸다."""
    edge = next(e["id"] for e in topo["edges"])
    assert place_of(edge) is None
    # 방어(`"|" in node_id`)가 없다면 `/` 파싱이 계정·리전을 **읽어낸다** — 그것이 이 검사의 이유다.
    parts = edge.split("/")
    assert re.fullmatch(r"[0-9]{12}", parts[1]) and REGION_RE.match(parts[2]), (
        "엣지 id 가 계정·리전처럼 읽히지 않으면 이 브레이크는 죽은 줄이다"
    )


# ---- 흐름 로그 근거(ADR 0008 「D4b 개정」 · 1단계 = 흐른다만 말한다) -----------------------
#
# 사람이 보는 결과로 말하면: 퍼블릭 서브넷 → IGW 선이 초록이면 **그 서브넷에서** 나가고 돌아온
# 기록이 있었다는 뜻이어야 한다. 같은 라우트 테이블을 쓰는 옆 서브넷의 기록으로 켜진 초록 ·
# 로그를 끈 화면에 남은 초록 · 못 읽은 로그로 켜진 초록은 전부 거짓이다. 아래는 그 셋을 스키마
# (한 행 안의 모양)와 `flow_log_problems`(문서 두 장을 맞댐)가 각각 잡는지 잰다.


def _row(doc: dict, prefix: str) -> dict:
    return next(o for o in doc["observations"] if o["target_id"].startswith(prefix))


def _resource(doc: dict, rid: str) -> dict:
    return next(
        r for s in doc["sources"] for r in s.get("resources", []) if r["id"] == rid
    )


@pytest.fixture
def mfl() -> tuple[dict, dict]:
    """IGW 모양 — 퍼블릭 서브넷 하나가 IGW 로 왕복했다 · 같은 VPC 에 로그 둘(하나는 중복)."""
    return load(FIXTURE_DIR / "minimal.flow-log.json"), load(
        TOPOLOGY_FIXTURE_DIR / "minimal.json"
    )


@pytest.fixture
def pfl() -> tuple[dict, dict]:
    """피어링 모양 — 명시 연관 서브넷(SA1)·메인 테이블 서브넷(SA2)·반대편(SB1, 기록 없음)."""
    return load(FIXTURE_DIR / "peering.flow-log.json"), load(
        TOPOLOGY_FIXTURE_DIR / "peering.json"
    )


PFL_SA1 = "subnet/111122223333/us-east-1/subnet-0bbb1111cccc0011"
PFL_SA2 = "subnet/111122223333/us-east-1/subnet-0bbb1111cccc0012"
PFL_VA = "vpc/111122223333/us-east-1/vpc-0bbb1111cccc0001"
PFL_RA0 = "route|route_table/111122223333/us-east-1/rtb-0bbb1111cccc0030|"
PFL_RA1 = "route|route_table/111122223333/us-east-1/rtb-0bbb1111cccc0031|"
PFL_RB0 = "route|route_table/111122223333/us-east-1/rtb-0bbb1111cccc0040|"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_flow_log_evidence_agrees_with_structure(path: Path):
    """흐름 로그 근거가 구조·로그 목록과 맞는다 — 끈 픽스처는 근거가 없어서, 켠 픽스처는 맞아서."""
    assert flow_log_problems(load(path), load(topology_for(path))) == []


def test_some_fixture_carries_every_flow_log_shape():
    """판별력 — 모양마다 픽스처가 하나씩 있어야 위 검사가 미측정이 아니다.

    IGW 라우트의 서브넷 근거 · IGW 카드 흐름(B) · 피어링 라우트의 서브넷 근거(명시 연관 + 메인
    테이블) · 피어링 선 · 로그 목록의 「읽음」과 「못 읽음」 · 중복으로 덮인 로그 · ENI 단위 로그.
    하나라도 빠지면 그 모양을 망가뜨려도 아무 검사도 빨개지지 않는다.
    """
    seen: set[str] = set()
    for path in FIXTURES:
        doc, topo = load(path), load(topology_for(path))
        edges = {e["id"]: e for e in topo["edges"]}
        for o in doc["observations"]:
            if o.get("by_subnet"):
                target = edges[o["target_id"]]["target"].split("/")[0]
                seen.add(f"by_subnet→{target}")
                users = route_table_users(topo)[edges[o["target_id"]]["source"]]
                explicit = {
                    e["target"]
                    for e in topo["edges"]
                    if e["type"] == "association"
                    and e["source"] == edges[o["target_id"]]["source"]
                }
                for item in o["by_subnet"]:
                    assert item["subnet_id"] in users
                    seen.add("explicit" if item["subnet_id"] in explicit else "main")
                # 검사 119 의 전제 — 같은 테이블을 쓰는데 항목이 없는 옆 서브넷(그 선은 그대로여야 한다)
                if users - {i["subnet_id"] for i in o["by_subnet"]}:
                    seen.add("sibling_without_entry")
            if (o.get("evidence") or {}).get("metric") == FLOW_LOG:
                seen.add(o["target_id"].split("/")[0].split("|")[0] + ":flow_log")
        for r in (r for s in doc["sources"] for r in s.get("resources", [])):
            seen.add(f"used={r['used']}")
            seen.add(f"level={r['level']}")
            if r.get("why_code"):
                seen.add(r["why_code"])
    assert {
        "by_subnet→internet_gateway",
        "by_subnet→vpc_peering_connection",
        "explicit",
        "main",
        "internet_gateway:flow_log",
        "peering:flow_log",
        "used=True",
        "used=False",
        "level=vpc",
        "level=network_interface",
        "duplicate_covered",
        "sibling_without_entry",
    } <= seen, sorted(seen)


def test_flow_log_fixtures_are_the_off_documents_plus_the_reader():
    """끈 화면은 켜기 전과 같다 — 켠 픽스처에서 판독기 몫을 걷어내면 끈 픽스처가 된다.

    🔴 설계 결정(1): 기능을 꺼 둔 고객의 화면은 오늘과 같다. 켠 픽스처가 끈 픽스처와 **판독기 몫
    말고도** 다르면(행 상태·항목 수·출처 문장) 「켜면 화면이 이것만큼 바뀐다」를 이 픽스처로 말할 수
    없다.
    """
    on, off = (
        load(FIXTURE_DIR / "minimal.flow-log.json"),
        load(FIXTURE_DIR / "minimal.json"),
    )
    del on["layers"]["flow_log"]
    for o in on["observations"]:
        o.pop("by_subnet", None)
    igw = _row(on, "internet_gateway/")
    igw.update(_row(off, "internet_gateway/"))
    on["sources"] = off["sources"]
    assert on == off


def test_flow_log_check_detects_evidence_while_the_layer_is_off(mfl):
    """① 끈 문서에 흐름 로그 근거·로그 목록이 남아 있다 — 끈 고객에게 초록이 보인다."""
    doc, topo = mfl
    del doc["layers"]["flow_log"]
    assert errors(doc) == [], (
        "층 키가 없는 것은 스키마가 허용한다 — 그래서 이 검사가 필요하다"
    )
    problems = flow_log_problems(doc, topo)
    assert any("흐름 로그 근거" in p for p in problems), problems
    assert any("로그 목록" in p for p in problems), problems


def test_flow_log_check_detects_a_list_while_the_layer_is_off(mfl):
    """① 근거는 걷었는데 로그 목록만 남았다 — 알약이 「근거 사용」을 말한다."""
    doc, topo = mfl
    off = load(FIXTURE_DIR / "minimal.json")
    del doc["layers"]["flow_log"]
    for o in doc["observations"]:
        o.pop("by_subnet", None)
    _row(doc, "internet_gateway/").update(_row(off, "internet_gateway/"))
    assert flow_log_problems(doc, topo) == [
        "vpc_flow_log: 흐름 로그 층이 꺼져 있는데 로그 목록"
    ]


def test_flow_log_check_detects_a_repeated_subnet(pfl):
    """② 같은 서브넷을 두 번 적었다 — 왕복 수가 두 번 더해진다."""
    doc, topo = pfl
    row = _row(doc, PFL_RA1)
    row["by_subnet"] = row["by_subnet"] * 2
    assert any("오름차순·유일" in p for p in flow_log_problems(doc, topo))


def test_flow_log_check_detects_a_subnet_that_does_not_use_the_table(pfl):
    """③ 🔴 핵심 — 다른 테이블을 쓰는 서브넷의 기록으로 이 테이블 선을 켰다.

    SA1 은 RA1 을 **명시 연관**으로 쓴다. 같은 VPC 의 메인 테이블 RA0 에 SA1 을 적으면 거짓이다 —
    메인 테이블은 「명시 연관이 없는 서브넷」만 쓴다. 이 경우를 놓치면 VPC 안 모든 서브넷이 메인
    테이블 선을 초록으로 켤 수 있다.
    """
    doc, topo = pfl
    row = _row(doc, PFL_RA0)
    row["by_subnet"][0]["subnet_id"] = PFL_SA1
    row["by_subnet"][0]["evidence"]["observed_on"] = PFL_SA1
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert any("이 라우트 테이블을 쓰지 않는다" in p for p in problems), problems


def test_flow_log_check_detects_a_subnet_of_the_other_vpc(pfl):
    """③ 반대편 VPC 의 메인 테이블에 이쪽 서브넷을 적었다."""
    doc, topo = pfl
    row = _row(doc, PFL_RB0)
    row["by_subnet"] = deepcopy(_row(doc, PFL_RA0)["by_subnet"])
    assert any(
        "이 라우트 테이블을 쓰지 않는다" in p for p in flow_log_problems(doc, topo)
    )


def test_flow_log_check_detects_a_pcx_item_seen_elsewhere(pfl):
    """④ 피어링 라우트 항목의 「어디서 봤나」가 그 서브넷이 아니다 — B 등급 설명이 거짓이 된다."""
    doc, topo = pfl
    _row(doc, PFL_RA1)["by_subnet"][0]["evidence"]["observed_on"] = PFL_SA2
    assert errors(doc) == []
    assert any(
        "observed_on 이 그 서브넷이 아니다" in p for p in flow_log_problems(doc, topo)
    )


def test_flow_log_check_detects_an_igw_card_without_a_subnet_proof(mfl):
    """⑤ IGW 카드가 초록인데 그 IGW 로 가는 서브넷 선은 하나도 증명되지 않았다."""
    doc, topo = mfl
    for o in doc["observations"]:
        o.pop("by_subnet", None)
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert any("이 IGW 로 가는 서브넷 증명이 없다" in p for p in problems), problems


def test_flow_log_check_detects_an_igw_card_seen_on_another_subnet(mfl):
    """⑤ IGW 카드의 「어디서 봤나」가 증명된 서브넷이 아니다."""
    doc, topo = mfl
    _row(doc, "internet_gateway/")["evidence"]["observed_on"] = (
        "subnet/111122223333/us-east-1/subnet-0aaa1111bbbb4444"
    )
    assert errors(doc) == []
    assert any(
        "이 IGW 로 가는 서브넷 증명이 없다" in p for p in flow_log_problems(doc, topo)
    )


def test_flow_log_check_detects_a_peering_line_without_a_subnet_proof(pfl):
    """⑤ 피어링 선이 초록인데 그 피어링으로 가는 서브넷 근거가 없다."""
    doc, topo = pfl
    for o in doc["observations"]:
        o.pop("by_subnet", None)
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert sum("이 피어링으로 가는 서브넷 증명이 없다" in p for p in problems) == 2, (
        problems
    )


def _peering_seen_on_the_vpc(doc: dict) -> dict:
    """서브넷을 못 가리는 VPC 로그로 피어링만 증명한 문서(설계상 허용되는 유일한 VPC 경로)."""
    for o in doc["observations"]:
        o.pop("by_subnet", None)
        if o["target_id"].startswith("peering|"):
            o["evidence"]["observed_on"] = PFL_VA
    log = _resource(doc, "fl-0bbb1111cccc0061")
    log["why_code"] = "subnet_unattributable"
    log["why"] = "로그에 subnet-id 필드가 없어 서브넷 선은 가리지 못한다"
    return doc


def test_flow_log_check_accepts_a_peering_line_seen_on_a_vpc(pfl):
    """⑤ 대조군 — 서브넷을 못 가리는 「읽음」 VPC 로그로 본 피어링은 받아들인다."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    assert errors(doc) == []
    assert flow_log_problems(doc, topo) == []


def test_flow_log_check_detects_a_vpc_proof_without_an_unattributable_log(pfl):
    """⑤ VPC 로 적었는데 그 VPC 로그는 서브넷을 가릴 수 있다 — 그러면 서브넷으로 적었어야 한다."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    log = _resource(doc, "fl-0bbb1111cccc0061")
    del log["why_code"], log["why"]
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert sum("서브넷을 못 가린" in p for p in problems) == 2, problems


def test_flow_log_check_detects_a_vpc_proof_from_an_unread_log(pfl):
    """⑤ VPC 로 적었는데 그 VPC 에 붙은 로그를 **못 읽었다** — 못 읽은 로그로 켠 초록."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    log = _resource(doc, "fl-0bbb1111cccc0061")
    log.update(used=False, why_code="access_denied", why="읽기 권한이 없다")
    doc["sources"][0]["used"] = False
    problems = flow_log_problems(doc, topo)
    assert sum("서브넷을 못 가린" in p for p in problems) == 2, problems


def test_flow_log_check_detects_a_vpc_proof_from_a_vpc_off_the_peering(pfl):
    """⑤ VPC 로 적었는데 그 VPC 는 이 피어링의 양쪽 어느 것도 아니다."""
    doc, topo = pfl
    doc = _peering_seen_on_the_vpc(doc)
    elsewhere = "vpc/111122223333/us-east-1/vpc-0bbb1111cccc0009"
    topo["nodes"].append(
        {**next(n for n in topo["nodes"] if n["id"] == PFL_VA), "id": elsewhere}
    )
    for o in doc["observations"]:
        if o["target_id"].startswith("peering|"):
            o["evidence"]["observed_on"] = elsewhere
    _resource(doc, "fl-0bbb1111cccc0061")["attached_to"] = elsewhere
    problems = flow_log_problems(doc, topo)
    assert sum("서브넷을 못 가린" in p for p in problems) == 2, problems


def test_flow_log_check_detects_an_unsorted_log_list(pfl):
    """⑥ 로그 목록 순서 — 불변식 ②(같은 입력 → 같은 바이트)."""
    doc, topo = pfl
    doc["sources"][0]["resources"].reverse()
    assert any("id 오름차순" in p for p in flow_log_problems(doc, topo))


def test_flow_log_check_detects_a_source_used_flag_that_disagrees(pfl):
    """⑥ 출처는 「안 씀」인데 목록엔 읽은 로그가 있다 — 알약과 선이 서로 다른 말을 한다."""
    doc, topo = pfl
    doc["sources"][0]["used"] = False
    doc["sources"][0]["why"] = "읽지 않았다"
    assert errors(doc) == []
    assert any("「사용」이 목록" in p for p in flow_log_problems(doc, topo))


def test_flow_log_check_detects_a_log_attached_to_nothing(pfl):
    """⑥ 로그가 붙은 대상이 구조에 없다 — 화면이 그 로그를 어느 VPC 에도 붙이지 못한다."""
    doc, topo = pfl
    _resource(doc, "fl-0bbb1111cccc0062")["attached_to"] = (
        "vpc/111122223333/us-east-1/vpc-0bbb1111cccc0009"
    )
    assert errors(doc) == []
    assert any("구조에 없다" in p for p in flow_log_problems(doc, topo))


def test_flow_log_check_detects_a_subnet_proof_without_a_log_that_covers_it(mfl):
    """⑥ 서브넷을 증명했는데 그 서브넷을 가릴 수 있는 「읽음」 로그가 없다.

    VPC 로그가 `subnet-id` 필드 없이 읽혔으면(`subnet_unattributable`) 그 로그로는 서브넷 선을 켤 수
    없다 — 켜면 같은 VPC 의 서브넷 전부가 초록 후보가 된다.
    """
    doc, topo = mfl
    log = _resource(doc, "fl-0aaa1111bbbb0a01")
    log["why_code"] = "subnet_unattributable"
    log["why"] = "로그에 subnet-id 필드가 없다"
    assert errors(doc) == []
    problems = flow_log_problems(doc, topo)
    assert any("덮는 「읽음」 VPC 흐름 로그가 없다" in p for p in problems), problems


def test_flow_log_check_accepts_an_eni_log_that_carries_the_subnet(pfl):
    """⑥ 대조군 — ENI 단위 로그도 subnet-id 필드가 있으면 서브넷을 가린다(조합표 1.3)."""
    doc, topo = pfl
    eni, vpc = (
        _resource(doc, "fl-0bbb1111cccc0060"),
        _resource(doc, "fl-0bbb1111cccc0061"),
    )
    eni["used"] = True
    del eni["why_code"], eni["why"]
    vpc.update(
        used=False, why_code="format_v2", why="기본 형식(v2)이라 경로 번호 필드가 없다"
    )
    assert errors(doc) == []
    assert flow_log_problems(doc, topo) == []


# ---- 흐름 로그 — 스키마가 한 행 안에서 거절하는 모양 -------------------------------------


def test_schema_rejects_by_subnet_off_a_route_row(pfl):
    """라우트 행이 아닌 곳(연관)에 서브넷별 근거 — 연관 선은 흐름 로그로 판정하지 않는다."""
    doc, _ = pfl
    _row(doc, "association|")["by_subnet"] = deepcopy(_row(doc, PFL_RA1)["by_subnet"])
    assert errors(doc)


def test_schema_rejects_by_subnet_on_a_route_to_another_target(pfl):
    """IGW·피어링이 아닌 대상으로 가는 라우트 — 1단계 판독기가 증명하지 않는 구간이다."""
    doc, _ = pfl
    row = _row(doc, PFL_RA1)
    row["target_id"] = row["target_id"].split("|vpc_peering_connection/")[0] + (
        "|transit_gateway/111122223333/us-east-1/tgw-0bbb1111cccc0070"
    )
    assert errors(doc)


def test_schema_rejects_by_subnet_on_a_down_route(mfl):
    """1층이 「막힘」인 행에 흐름 근거 — 1층이 이긴다(초록과 빨강이 한 선에 겹치지 않는다)."""
    doc, _ = mfl
    down = load(FIXTURE_DIR / "minimal.down.json")
    route = _row(doc, "route|")
    route.update({k: v for k, v in _row(down, "route|").items() if k != "target_id"})
    assert route["status"] == "down", (
        "minimal.down.json 이 막힌 라우트를 잃었다 — 대조군이 죽는다"
    )
    assert "by_subnet" in route, (
        "위 update 가 서브넷 근거까지 지웠다 — 이 검사가 재는 것이 없다"
    )
    control = deepcopy(doc)
    del _row(control, "route|")["by_subnet"]
    assert errors(control) == []
    assert errors(doc)


def test_schema_rejects_an_igw_item_with_observed_on(mfl):
    """IGW 라우트 항목은 A 등급이다 — 「어디서 봤나」가 붙으면 B 로 읽힌다."""
    doc, _ = mfl
    item = _row(doc, "route|")["by_subnet"][0]
    item["evidence"]["observed_on"] = item["subnet_id"]
    assert errors(doc)


def test_schema_rejects_a_pcx_item_without_observed_on(pfl):
    """피어링 라우트 항목은 B 등급이다 — 한쪽에서 봤다는 사실이 빠지면 A 로 읽힌다."""
    doc, _ = pfl
    del _row(doc, PFL_RA1)["by_subnet"][0]["evidence"]["observed_on"]
    assert errors(doc)


def test_schema_rejects_a_peering_row_without_flow_log_evidence(pfl):
    """피어링 선은 흐름 로그 전용이다 — 1층 API 로 적은 피어링 초록은 종단 추론이다."""
    doc, _ = pfl
    _row(doc, "peering|")["evidence"] = {
        "api": "ec2:DescribeVpcPeeringConnections",
        "metric": None,
        "datapoints": None,
        "window_seconds": None,
    }
    assert errors(doc)


def test_schema_rejects_a_flow_log_row_that_is_not_live(mfl):
    """흐름 로그는 「흐른다」만 말한다 — 흐름 로그 근거로 up·down 을 적을 수 없다."""
    doc, _ = mfl
    _row(doc, "internet_gateway/")["status"] = "up"
    assert errors(doc)


def test_schema_rejects_a_flow_log_row_that_is_not_an_igw_or_a_peering(mfl):
    """흐름 로그 근거는 IGW 카드·피어링 선에만 — 서브넷 카드를 흐름 로그로 켜지 않는다."""
    doc, _ = mfl
    subnet = _row(doc, "subnet/")
    subnet["status"] = "live"
    subnet["evidence"] = deepcopy(_row(doc, "internet_gateway/")["evidence"])
    assert errors(doc)


def test_schema_rejects_the_layer_written_as_false(mfl):
    """끈 층은 키가 **없다** — `false` 를 허용하면 끈 문서의 바이트가 두 가지가 된다."""
    doc, _ = mfl
    doc["layers"]["flow_log"] = False
    assert errors(doc)


def test_schema_rejects_the_flow_log_layer_without_the_metric_layer(mfl):
    """흐름 로그는 2층 위에 얹힌다 — 지표를 끄고 흐름 로그만 켠 조합은 없다(U9 스위치)."""
    doc, _ = mfl
    doc["layers"]["metric"] = False
    assert [e for e in errors(doc) if "layers" in e]


def test_schema_rejects_a_log_list_on_an_off_source():
    """꺼진 출처에 로그 목록 — 「꺼짐」인데 읽은 로그가 있는 문서."""
    doc = load(FIXTURE_DIR / "minimal.json")
    source = next(s for s in doc["sources"] if s["kind"] == "vpc_flow_log")
    assert source["state"] == "off"
    source["resources"] = [
        {
            "id": "fl-0aaa1111bbbb0a01",
            "level": "vpc",
            "used": False,
            "why_code": "format_v2",
            "why": "x",
        }
    ]
    assert errors(doc)


def test_schema_rejects_an_unread_log_without_a_reason(pfl):
    """못 읽었는데 이유가 없다 — 고객이 정책을 열어야 하는지 도구를 기다려야 하는지 모른다."""
    doc, _ = pfl
    del _resource(doc, "fl-0bbb1111cccc0062")["why_code"]
    assert errors(doc)


def test_schema_rejects_a_read_log_with_a_cannot_read_reason(pfl):
    """「읽음」인데 이유가 Parquet — 한 줄도 못 읽는 이유를 단 「읽음」은 모순이다."""
    doc, _ = pfl
    log = _resource(doc, "fl-0bbb1111cccc0061")
    log.update(why_code="format_parquet", why="Parquet")
    assert errors(doc)


def test_schema_rejects_an_eni_log_attached_to_a_node(pfl):
    """ENI 단위 로그에 붙은 대상 — 엔진은 ENI 를 구조에 그리지 않는다."""
    doc, _ = pfl
    _resource(doc, "fl-0bbb1111cccc0060")["attached_to"] = PFL_SA1
    assert errors(doc)


def test_schema_rejects_a_log_attached_to_the_wrong_kind_of_node(pfl):
    """VPC 단위라면서 서브넷에 붙었다 — 화면이 그 로그를 엉뚱한 선의 설명에 쓴다."""
    doc, _ = pfl
    _resource(doc, "fl-0bbb1111cccc0061")["attached_to"] = PFL_SA1
    assert errors(doc)


def test_schema_rejects_a_tgw_level_line_in_the_vpc_source(pfl):
    """VPC 흐름 로그 출처에 TGW 단위 줄 — 알약이 한 로그를 두 출처에서 센다."""
    doc, _ = pfl
    log = _resource(doc, "fl-0bbb1111cccc0060")
    log["level"] = "transit_gateway"
    assert errors(doc)


def test_schema_rejects_the_withdrawn_reader_off_code(pfl):
    """`reader_off` 는 철회했다(설계 편차 1) — 끈 화면은 로그 목록 자체가 없다."""
    doc, _ = pfl
    _resource(doc, "fl-0bbb1111cccc0062")["why_code"] = "reader_off"
    assert errors(doc)
    assert "reader_off" not in SCHEMA["$defs"]["flow_log_why_code"]["enum"]


# ---- 사용률(ADR 0011 · 스키마 1.4.0) -------------------------------------------------
#
# JSON Schema 가 못 보는 것: 정렬·유일 · 그 노드가 구조에 있나 · layers.utilization 과의 짝 ·
# 기간과 단위의 짝 · measure 와 노드 종류·단위의 짝 · 평균 ≤ 최대. 순수 함수로 뽑고 음성 짝으로 판별력을 고정한다.

UTIL_FIXTURE = FIXTURE_DIR / "minimal.utilization.json"

# 기간(분) → 가장 바빴던 때를 재는 단위(초) = CloudWatch 보관 해상도(ADR 0011 D2).
STEP_FOR_MINUTES = {15: 60, 60: 60, 1440: 60, 10080: 60, 43200: 300, 129600: 3600}
# 1분마다 다시 계산하는 짧은 묶음(ADR 0011 D6) — 이 칸의 computed_at 은 그 관측의 observed_at 이다.
SHORT_WINDOWS = {15, 60, 1440}
# EC2 기본 모니터링 = 5분 점 → EC2 는 어느 기간이든 단위가 300 보다 잘지 않다(스키마 `step_seconds` 설명).
EC2_MIN_STEP = 300

# measure → (붙을 수 있는 노드 종류, 단위). 값만(한도 ?) 인 것도 여기 있다 — ADR 0011 D3 의 세 표.
MEASURE_TABLE: dict[str, tuple[set[str], str]] = {
    "bandwidth": (
        {
            "transit_gateway_attachment",
            "direct_connect_connection",
            "direct_connect_virtual_interface",
            "network_firewall",
            "nat_gateway",
            "vpc_endpoint",
            "load_balancer",
            "ec2_instance",
            "rds_db_instance",
            "cloudfront",
            "vpn_connection",
            "api_gateway",
            "elasticache_cluster",
            "global_accelerator",
        },
        "bit_per_second",
    ),
    "packet_rate": ({"transit_gateway_attachment"}, "packet_per_second"),
    # 1.18.0 · 사용량(사용자 결정 2026-10-26) — 바이트가 없는 서비스는 횟수 · RDS 는 연결 수(보조)
    "invocations": ({"lambda_function"}, "count_per_second"),
    "requests": ({"api_gateway"}, "count_per_second"),
    "connections": ({"rds_db_instance"}, "count"),
    "capacity": ({"load_balancer"}, "percent"),
    "rejected_connections": ({"load_balancer"}, "count"),
    "port_allocation_errors": ({"nat_gateway", "load_balancer"}, "count"),
    "packets_dropped": ({"vpc_endpoint"}, "count"),
    "ip_addresses": ({"subnet"}, "count"),
    "security_group_rules": ({"security_group"}, "count"),
    "network_acl_entries": ({"network_acl"}, "count"),
    "routes_ipv4": ({"route_table"}, "count"),
    "routes_ipv6": ({"route_table"}, "count"),
    "routes_propagated": ({"route_table"}, "count"),
    "vpc_ipv4_cidrs": ({"vpc"}, "count"),
    "vpc_ipv6_cidrs": ({"vpc"}, "count"),
    "vpc_subnets": ({"vpc"}, "count"),
    "vpc_network_acls": ({"vpc"}, "count"),
    "vpc_route_tables": ({"vpc"}, "count"),
    "vpc_interface_endpoints": ({"vpc"}, "count"),
    "vpc_active_peerings": ({"vpc"}, "count"),
    "tgw_attachments": ({"transit_gateway"}, "count"),
    "tgw_route_tables": ({"transit_gateway"}, "count"),
    "tgw_peering_attachments": ({"transit_gateway"}, "count"),
    "tgw_routes": ({"transit_gateway"}, "count"),
    "dxgw_virtual_interfaces": ({"direct_connect_gateway"}, "count"),
    "dxgw_virtual_private_gateways": ({"direct_connect_gateway"}, "count"),
    "dxgw_transit_gateways": ({"direct_connect_gateway"}, "count"),
}


def _measures(doc: dict):
    for item in doc.get("utilization", []):
        for m in item["measures"]:
            yield item["target_id"], m


def utilization_order_problems(doc: dict) -> list[str]:
    """정렬·유일 — 대상은 target_id · measure 는 이름 · 기간은 minutes 오름차순."""
    out: list[str] = []
    targets = [i["target_id"] for i in doc.get("utilization", [])]
    if unsorted(targets) or len(set(targets)) != len(targets):
        out.append("utilization: target_id 오름차순 · 유일")
    for item in doc.get("utilization", []):
        names = [m["measure"] for m in item["measures"]]
        if unsorted(names) or len(set(names)) != len(names):
            out.append(f"{item['target_id']}: measure 오름차순 · 유일")
        for m in item["measures"]:
            mins = [w["minutes"] for w in m.get("windows", [])]
            if unsorted(mins) or len(set(mins)) != len(mins):
                out.append(
                    f"{item['target_id']}/{m['measure']}: minutes 오름차순 · 유일"
                )
    return out


def dangling_utilization(doc: dict, topo: dict) -> list[str]:
    ids = {n["id"] for n in topo["nodes"]}
    return [
        i["target_id"] for i in doc.get("utilization", []) if i["target_id"] not in ids
    ]


def utilization_layer_mismatch(doc: dict) -> bool:
    """layers.utilization 이 있으면 배열도 있고, 없으면 배열도 없다(flow_log 와 같은 규율)."""
    return ("utilization" in doc["layers"]) != ("utilization" in doc)


def utilization_table_problems(doc: dict, topo: dict) -> list[str]:
    """measure 가 그 노드 종류에 붙을 수 있나 · 단위가 맞나 · %는 AWS 가 준 것만."""
    types = {n["id"]: n["type"] for n in topo["nodes"]}
    out: list[str] = []
    for target, m in _measures(doc):
        allowed, unit = MEASURE_TABLE[m["measure"]]
        if types.get(target) not in allowed:
            out.append(
                f"{target}: {m['measure']} 는 {types.get(target)} 에 붙지 않는다"
            )
        if m["unit"] != unit:
            out.append(f"{target}/{m['measure']}: 단위 {m['unit']} ≠ {unit}")
        if m["unit"] == "percent" and (
            m["limit"] != 100 or m["limit_source"] != "aws_computed"
        ):
            out.append(
                f"{target}/{m['measure']}: %는 AWS 가 준 값 — 한도 100 · 출처 aws_computed"
            )
    return out


def utilization_window_problems(doc: dict) -> list[str]:
    """기간과 단위의 짝 · 덮은 시간 ≤ 기간 · 평균 ≤ 최대 · 계산 시각 ≤ 관측 시각(짧은 묶음은 같다)."""
    observed = ts(doc["run"]["observed_at"])
    out: list[str] = []
    for target, m in _measures(doc):
        for w in m.get("windows", []):
            here = f"{target}/{m['measure']}/{w['minutes']}"
            step = STEP_FOR_MINUTES[w["minutes"]]
            if target.startswith("ec2_instance/"):
                step = max(step, EC2_MIN_STEP)
            if w["step_seconds"] != step:
                out.append(f"{here}: 단위 {w['step_seconds']} ≠ {step}")
            if w["covered_seconds"] > w["minutes"] * 60:
                out.append(f"{here}: 덮은 시간이 기간보다 길다")
            peak, avg = w.get("peak"), w.get("average")
            if peak is not None and avg is not None and avg > peak:
                out.append(f"{here}: 평균 {avg} > 최대 {peak}")
            at = ts(w["computed_at"])
            if at > observed or (w["minutes"] in SHORT_WINDOWS and at != observed):
                out.append(f"{here}: computed_at {w['computed_at']}")
            if w.get("peak_at") is not None:
                # 이월된 긴 묶음의 기간은 그것을 계산한 시각에서 잰다 — 관측 시각에서 재면 1시간 전 몰림이 「기간 밖」이 된다.
                stop = at.timestamp() - at.timestamp() % w["step_seconds"]
                start = datetime.fromtimestamp(stop, tz=timezone.utc) - timedelta(
                    minutes=w["minutes"]
                )
                if not (start <= ts(w["peak_at"]) <= at):
                    out.append(f"{here}: peak_at 이 기간 밖")
    return out


@pytest.fixture
def util() -> dict:
    return load(UTIL_FIXTURE)


def test_the_fixture_set_exercises_utilization():
    """한 픽스처는 사용률을 싣는다 — 세 모양(rate·events·count) · 값만 · 못 읽음 · 기본 한도+이유 · 계정 한도."""
    doc = load(UTIL_FIXTURE)
    ms = [m for _, m in _measures(doc)]
    assert {m["kind"] for m in ms} == {"rate", "events", "count"}
    assert any(m["limit"] is None for m in ms)
    assert any("unread" in m for m in ms)
    assert any(m["limit_source"] == "default_quota" and "limit_reason" in m for m in ms)
    assert any(
        m["limit_source"] == "default_quota" and "limit_reason" not in m for m in ms
    )
    assert any(m["limit_source"] == "account_quota" for m in ms)
    assert any(
        w["computed_at"] != doc["run"]["observed_at"]
        for m in ms
        for w in m.get("windows", [])
    )
    # 몰림이 짧으면 1주 칸의 가장 바빴던 분과 평균이 크게 다르다(ADR 0011 D2 의 근거 그대로) · 사용량 = 기간 합(1.18.0).
    bw = next(m for m in ms if m["measure"] == "bandwidth")
    assert bw["total_unit"] == "byte" and bw["limit"] is None
    week = next(w for w in bw["windows"] if w["minutes"] == 10080)
    assert week["peak"] > 1000 * week["average"] and week["total"] > 0


def test_the_fixture_set_tells_no_points_from_a_real_zero():
    """#128 · 불변식 ④ — 한 픽스처가 두 모양을 다 싣는다: 「값이 있을 때만 보내는」 거부 수(events)가 점을 하나도 안 보낸 창
    (no_points · 값 없음)과 점이 왔는데 0 인 창(정말 0). 렌더 하네스(검사 144)가 이 픽스처로 둘을 화면에서 가른다.
    1.18.0 — 사용량(rate)의 빈 창은 0 이다(사용자 결정 2026-10-26 ④) → rate 에는 no_points 가 없다."""
    doc = load(UTIL_FIXTURE)
    wins = [(m, w) for _, m in _measures(doc) for w in m.get("windows", [])]
    empty = [(m, w) for m, w in wins if w.get("no_points")]
    assert {m["kind"] for m, _ in empty} == {"events"}
    assert any(
        m["kind"] == "rate" and w.get("peak") == 0 and w.get("total") == 0
        for m, w in wins
    )
    assert all(
        w.get("peak") is None and "sum" not in w and w["covered_seconds"] == 0
        for _, w in empty
    )
    assert any(m["kind"] == "rate" and w.get("peak") == 0 for m, w in wins)
    assert any(m["kind"] == "events" and w.get("sum") == 0 for m, w in wins)


def _no_points_window(util: dict) -> dict:
    ev = util["utilization"][0]["measures"][1]
    return next(w for w in ev["windows"] if w.get("no_points"))


def test_schema_rejects_a_no_points_window_that_carries_a_zero(util):
    """값 없음인데 0 을 실으면 화면이 「정말 0」과 가르지 못한다(#128)."""
    _no_points_window(util)["peak"] = 0.0
    assert errors(util)


def test_schema_rejects_a_no_points_window_with_a_total(util):
    """1.18.0 — 값 없음 창은 기간 합도 싣지 않는다(0 이 아니다)."""
    _no_points_window(util)["total"] = 0.0
    assert errors(util)


def test_schema_rejects_a_no_points_event_window_with_a_sum(util):
    ev = util["utilization"][0]["measures"][1]
    next(w for w in ev["windows"] if w.get("no_points"))["sum"] = 0
    assert errors(util)


def test_schema_rejects_a_false_no_points(util):
    """키는 있을 때만 뜻이 있다 — false 는 「값 있음」을 두 가지로 쓰게 만든다."""
    cap = util["utilization"][0]["measures"][0]
    next(w for w in cap["windows"] if w.get("peak") == 0)["no_points"] = False
    assert errors(util)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_utilization_is_coherent(path: Path):
    doc = load(path)
    topo = load(topology_for(path))
    assert not utilization_layer_mismatch(doc)
    assert utilization_order_problems(doc) == []
    assert dangling_utilization(doc, topo) == []
    assert utilization_table_problems(doc, topo) == []
    assert utilization_window_problems(doc) == []


def test_measure_table_covers_the_schema_enum():
    """MEASURE_TABLE 과 스키마 열거가 어긋나면 새 measure 가 아무 검사 없이 들어온다."""
    enum = SCHEMA["$defs"]["utilization_measure"]["properties"]["measure"]["enum"]
    assert set(MEASURE_TABLE) == set(enum)
    node_types = set(TOPOLOGY_SCHEMA["$defs"]["node_type"]["enum"])
    assert all(
        t in node_types for allowed, _ in MEASURE_TABLE.values() for t in allowed
    )


def test_utilization_is_not_required():
    """1.3.0 문서(사용률 없음)는 그대로 유효하다 — minor."""
    assert "utilization" not in SCHEMA["required"]
    assert all("utilization" not in load(p) for p in FIXTURES if p != UTIL_FIXTURE)


# 음성 짝 — 한 곳만 망가뜨린다.


def test_table_detects_a_percent_with_a_limit_other_than_100(util):
    """값은 원래 단위다 — 0.84 를 쓰면 화면이 「0.84%」를 그린다. 그래서 %는 limit 100 과 짝이고, 그건 위 표 검사가 잰다."""
    m = util["utilization"][0]["measures"][0]
    m["measure"], m["unit"], m["limit"], m["limit_source"] = (
        "capacity",
        "percent",
        1,
        "aws_computed",
    )
    assert utilization_table_problems(util, load(TOPOLOGY_FIXTURE_DIR / "minimal.json"))


def test_schema_rejects_an_unknown_limit_without_a_null_source(util):
    util["utilization"][0]["measures"][1]["limit_source"] = "fixed"
    assert errors(util)


def test_schema_rejects_a_limit_reason_on_an_account_quota(util):
    vpc = util["utilization"][4]["measures"]
    vpc[1]["limit_reason"] = {"code": "access_denied", "api": None, "message": None}
    assert errors(util)


def test_schema_rejects_a_quota_code_on_a_fixed_limit(util):
    nacl = util["utilization"][1]["measures"][0]
    nacl["limit_source"] = "fixed"
    assert errors(util)


def test_schema_rejects_a_count_with_windows(util):
    sub = util["utilization"][2]["measures"][0]
    sub["windows"] = deepcopy(util["utilization"][0]["measures"][0]["windows"])
    assert errors(util)


def test_schema_rejects_a_rate_with_a_current_count(util):
    util["utilization"][0]["measures"][0]["now"] = {"used": 1}
    assert errors(util)


def test_schema_rejects_an_events_measure_with_a_limit(util):
    """거부·드롭 수는 사실이다 — %로 나누지 않는다(ADR 0011 D1)."""
    m = util["utilization"][0]["measures"][1]
    m["limit"], m["limit_source"] = 100, "fixed"
    assert errors(util)


def test_schema_rejects_an_unread_measure_that_still_has_a_value(util):
    util["utilization"][3]["measures"][0]["now"] = {"used": 3}
    assert errors(util)


def test_schema_rejects_a_read_count_without_a_value(util):
    del util["utilization"][2]["measures"][0]["now"]
    assert errors(util)


def test_schema_rejects_an_unknown_window_length(util):
    util["utilization"][0]["measures"][0]["windows"][0]["minutes"] = 30
    assert errors(util)


def test_schema_accepts_utilization_without_the_metric_layer(util):
    """1.5.0 — 사용률은 2층과 독립이다(「사용률만 보고 싶은 고객」 · ADR 0011 D7 개정 2026-10-06)."""
    util["layers"]["metric"] = False
    assert errors(util) == []
    # 살아 있는 짝: 흐름 로그는 여전히 2층을 요구한다(같은 allOf 가 지워지지 않았다).
    util["layers"]["flow_log"] = True
    assert errors(util)


def test_schema_rejects_an_edge_target(util):
    """사용률은 노드에만 붙는다 — 그릴 선은 렌더러가 고른다(불변식 ③)."""
    util["utilization"][0]["target_id"] = util["observations"][0]["target_id"]
    assert errors(util)


def test_layer_pairing_detects_both_halves(util):
    lone = deepcopy(util)
    del lone["layers"]["utilization"]
    assert utilization_layer_mismatch(lone)
    del util["utilization"]
    assert utilization_layer_mismatch(util)


def test_order_detects_unsorted_targets_measures_and_windows(util):
    a = deepcopy(util)
    a["utilization"].reverse()
    assert utilization_order_problems(a)
    b = deepcopy(util)
    b["utilization"][4]["measures"].reverse()
    assert utilization_order_problems(b)
    c = deepcopy(util)
    c["utilization"][0]["measures"][0]["windows"].reverse()
    assert utilization_order_problems(c)
    d = deepcopy(util)
    d["utilization"].append(deepcopy(d["utilization"][-1]))
    assert utilization_order_problems(d)


def test_dangling_detects_a_target_missing_from_the_topology(util, topo):
    util["utilization"][4]["target_id"] = (
        "vpc/111122223333/us-east-1/vpc-0fff1111bbbb9999"
    )
    assert dangling_utilization(util, topo)


def test_table_detects_a_measure_on_the_wrong_node_type(util, topo):
    util["utilization"][2]["measures"][0]["measure"] = "vpc_subnets"
    assert utilization_table_problems(util, topo)


def test_table_detects_a_wrong_unit(util, topo):
    util["utilization"][0]["measures"][0]["unit"] = "packet_per_second"
    assert utilization_table_problems(util, topo)


def test_window_detects_a_step_that_outlives_retention(util):
    """1달을 1분 단위로 적으면 거짓이다 — CloudWatch 는 15일 넘은 1분 점을 주지 않는다."""
    util["utilization"][0]["measures"][0]["windows"][4]["step_seconds"] = 60
    assert utilization_window_problems(util)


def test_window_detects_coverage_longer_than_the_window(util):
    w = util["utilization"][0]["measures"][0]["windows"][0]
    w["covered_seconds"] = 15 * 60 + 1
    assert utilization_window_problems(util)


def test_window_detects_an_average_above_the_peak(util):
    """평균을 배지 자리에 넣고 최대를 평균 자리에 넣으면(뒤바뀜) 평균 > 최대가 된다."""
    w = util["utilization"][0]["measures"][0]["windows"][3]
    w["peak"], w["average"] = w["average"], w["peak"]
    assert utilization_window_problems(util)


def test_window_detects_a_stale_short_window(util):
    """짧은 묶음은 1분마다 다시 계산한다 — 이월된 1시간 전 값이면 화면의 「지금」이 거짓이다."""
    util["utilization"][0]["measures"][0]["windows"][0]["computed_at"] = (
        "2026-01-02T02:10:05Z"
    )
    assert utilization_window_problems(util)


def test_window_detects_a_peak_outside_the_window(util):
    util["utilization"][0]["measures"][0]["windows"][0]["peak_at"] = (
        "2026-01-02T01:00:00Z"
    )
    assert utilization_window_problems(util)


# ---- LB 대상 다섯 갈래(1.15.0 · #137) -------------------------------------------

TARGET_DETAIL = (
    "health_check_off",
    "initial",
    "draining",
    "unused",
    "other_unavailable",
)


def _targets(total: int, healthy: int, unhealthy: int, **detail: int) -> dict:
    return {
        "total": total,
        "healthy": healthy,
        "unhealthy": unhealthy,
        **{k: detail.get(k, 0) for k in TARGET_DETAIL},
    }


def targets_over_total(doc: dict) -> list[str]:
    """healthy + unhealthy + 다섯 갈래 ≤ total — 한 대상은 한 상태다(JSON Schema 로 못 쓰는 합 제약)."""
    bad = []
    for o in doc["observations"]:
        t = o.get("targets")
        if not t:
            continue
        parts = t["healthy"] + t["unhealthy"] + sum(t.get(k, 0) for k in TARGET_DETAIL)
        if parts > t["total"]:
            bad.append(o["target_id"])
    return bad


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_target_parts_never_exceed_the_total(path: Path):
    assert targets_over_total(load(path)) == []


def test_the_engine_fixture_carries_a_health_check_off_count():
    """미측정 막이 — 엔진이 낸 관측 픽스처(`lb-targets.json`)에 헬스 체크 꺼짐 > 0 인 LB 행이 있어야 렌더 하네스(검사 129 `lbt`)가 잰다."""
    doc = load(FIXTURE_DIR / "lb-targets.json")
    rows = [
        o
        for o in doc["observations"]
        if (o.get("targets") or {}).get("health_check_off")
    ]
    assert any(o["target_id"].startswith("load_balancer/") for o in rows)
    assert all(o["status"] == "up" for o in rows)  # 헬스 체크 꺼짐은 이상이 아니다


def _lb_row(doc: dict) -> dict:
    return next(o for o in doc["observations"] if o.get("targets"))


def test_target_parts_come_together_or_not_at_all(doc):
    """다섯 갈래는 함께 온다(dependentRequired) — 일부만 오면 화면이 빠진 갈래를 0 으로 읽는다. 옛 셋만도 유효하다."""
    row = _lb_row(doc)
    row["targets"] = {"total": 3, "healthy": 2, "unhealthy": 0}
    assert errors(doc) == []  # 1.13.0 모양(다섯 없음) — 그대로 유효
    row["targets"] = _targets(3, 2, 0, health_check_off=1)
    assert errors(doc) == []
    for k in TARGET_DETAIL:
        row["targets"] = _targets(3, 2, 0, health_check_off=1)
        del row["targets"][k]
        assert errors(doc), f"{k} 하나가 빠졌는데 통과했다"
    row["targets"] = _targets(3, 2, 0)
    row["targets"]["unhealthy_draining"] = 1
    assert errors(doc), "모르는 갈래 키를 받았다(additionalProperties)"
    row["targets"] = _targets(3, 2, 0, initial=-1)
    assert errors(doc), "음수 갈래를 받았다"


def test_target_parts_over_the_total_are_caught(doc):
    row = _lb_row(doc)
    row["targets"] = _targets(2, 2, 0, health_check_off=1)
    assert targets_over_total(doc) == [row["target_id"]]
    row["targets"] = _targets(3, 2, 0, health_check_off=1)
    assert targets_over_total(doc) == []
