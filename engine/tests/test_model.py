"""id 문법 축 — **조립과 분해가 갈라지지 않는가.**

🔴 `node_id()`·`collect._edge_id()` 로 만든 id 를 나중에 되쪼개는 곳이 생겼다(이력이 「이 대상이
어느 (계정,리전,종류) 칸이냐」를 물어야 한다 — 이전 관측 문서에는 그 칸이 적혀 있지 않고 id 만
있다). 분해를 다른 파일에서 다시 구현하면 문법이 조용히 갈라지고, 그때 이력은 **틀린 칸을 보고
「읽었다/못 읽었다」를 판단한다** — 권한 상실이 삭제로 둔갑하는 경로가 다시 열린다. 그래서 왕복을
여기서 잰다.

왕복은 **실제 스냅샷의 id 전수**로 잰다. 손으로 쓴 예시만 재면 `native_id` 에 `/` 가 들어가는
종류(LB ARN)를 영원히 못 본다.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import pytest
from fakes import ACCOUNT, REGION, FakeAws, factory
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope, node_id, split_edge_id, split_node_id

COLLECTED_AT = "2026-09-01T00:00:00Z"


def _snapshot() -> Any:
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )
    return collect(scope, factory(FakeAws()), clock=lambda: COLLECTED_AT)


def test_every_collected_node_id_round_trips():
    """🔴 마지막 조각은 `native_id` 와 **바이트 동일이 아니다** — 인코딩을 한 겹 벗겨야 한다.

    ASG 는 AWS 가 이름에 공백·`|`·`/` 를 허용하므로 `node_id` 세그먼트를 퍼센트 인코딩한다
    (`services/auto_scaling_group.py` `_id_segment()`, 리뷰 결정 2026-09-28). `native_id` 필드에는
    AWS 원문이 그대로 남는다 — 그것이 이 두 값이 갈라지는 **유일한** 이유다. `_id_segment` 가
    단사(`quote`)이므로 `unquote` 가 정확한 역함수고, 인코딩하지 않는 종류에 `unquote` 는
    항등이다. 🔵 그래서 이 검사는 **여전히 전수**이고 ASG 를 예외로 빼지 않는다 — 예외로 빼면
    다음에 인코딩을 도입하는 종류가 검사 밖에서 작성된다.
    """
    document = _snapshot().to_document()
    assert len(document["nodes"]) > 10
    for node in document["nodes"]:
        node_type, account, region, segment = split_node_id(node["id"])
        assert (node_type, account, region) == (
            node["type"],
            node["account_id"],
            node["region"],
        ), node["id"]
        # 🔴 「둘 다 없다」와 「한쪽만 없다」를 구분한다 — `or` 만 쓰면 세그먼트가 사라진 id 가
        #    통과한다(실패할 수 없는 어서션 = 미측정).
        assert (segment is None) == (node["native_id"] is None), node["id"]
        if segment is not None:
            assert unquote(segment) == node["native_id"], node["id"]


def test_a_native_id_containing_a_slash_survives_the_round_trip():
    """🔴 마지막 조각은 **다시 붙여야** 한다 — `parts[3]` 만 읽으면 조용히 잘린다.

    지금 수집하는 12종에는 `/` 가 든 `native_id` 가 **없다**(실측 2026-09-24: 위 전수 왕복
    검사의 대상 중 0건 — LB 는 ARN 이 아니라 이름을 쓴다). 그래서 이것은 회귀 검사가 아니라
    **앞선 검사**다: ALB 타깃그룹(`targetgroup/name/id`)이나 LB ARN(`app/name/id`)을 언젠가
    `native_id` 로 쓰면 그날 잘린 id 가 이력에서 「칸 미상」으로 조용히 빠진다.
    """
    native = "app/my-load-balancer/50dc6c495c0c9188"
    value = node_id("load_balancer", ACCOUNT, REGION, native)
    assert split_node_id(value) == ("load_balancer", ACCOUNT, REGION, native)


def test_every_collected_edge_id_round_trips():
    document = _snapshot().to_document()
    assert len(document["edges"]) > 10
    for edge in document["edges"]:
        edge_type, source, target, _disc = split_edge_id(edge["id"])
        assert (edge_type, source, target) == (
            edge["type"],
            edge["source"],
            edge["target"],
        ), edge["id"]
        # 노드 id 에 `|` 가 없는 것이 파싱 유일성의 근거다(`collect.py:258`).
        assert "|" not in edge["source"] and "|" not in edge["target"]


def test_container_ids_round_trip_with_missing_pieces():
    assert split_node_id(node_id("account", ACCOUNT)) == (
        "account",
        ACCOUNT,
        None,
        None,
    )
    assert split_node_id(node_id("region", ACCOUNT, REGION)) == (
        "region",
        ACCOUNT,
        REGION,
        None,
    )


@pytest.mark.parametrize(
    "value", ["", "vpc", "vpc/", "/111122223333", "vpc//us-east-1"]
)
def test_a_malformed_node_id_raises_instead_of_guessing(value: str):
    """🔴 빈 조각을 통과시키면 그것이 **없는 칸**으로 흘러가 「못 읽었다」로 읽힌다."""
    with pytest.raises(ValueError):
        split_node_id(value)


@pytest.mark.parametrize("value", ["", "route", "route|a", "route||b", "a|b|c|d|e"])
def test_a_malformed_edge_id_raises_instead_of_guessing(value: str):
    with pytest.raises(ValueError):
        split_edge_id(value)
