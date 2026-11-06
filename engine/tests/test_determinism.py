"""불변식 ② — 같은 입력 스냅샷 → 바이트 동일 산출물.

`run.collected_at` 만 예외이므로, 시각이 다른 두 실행은 **원문 바이트는 달라야 하고**
`digest_excluding_collected_at` 은 같아야 한다. 둘 중 하나만 재면 절반이 미측정이다.
"""

from __future__ import annotations

import copy
import json
import re
import time
from typing import Any

from fakes import (
    ACCOUNT,
    PARTNER,
    REGION,
    SUBNETS,
    VPCS,
    FakeEc2,
    client_error,
    factory,
)
from nettopo.collect import collect
from nettopo.model import Scope
from nettopo.serialize import content_digest, digest_excluding_collected_at, to_json

SCOPE = Scope(
    account_ids=(ACCOUNT,), regions=(REGION,), resource_types=("vpc", "subnet")
)
# 쌍이 4개여야 「완료 순서 ≠ 요청 순서」를 만들 수 있다(쌍 하나는 스레드를 쓰지 않는다).
PAIR_SCOPE = Scope(
    account_ids=(ACCOUNT, PARTNER),
    regions=(REGION, "us-west-2"),
    resource_types=("vpc", "subnet"),
)
FIXED_CLOCK = "2026-01-02T03:04:05Z"


def run(*, vpcs=None, subnets=None, clock=FIXED_CLOCK) -> str:
    client = FakeEc2(vpcs=vpcs, subnets=subnets)
    return to_json(collect(SCOPE, factory(client), clock=lambda: clock))


def test_two_runs_are_byte_identical():
    assert run() == run()


def test_input_order_does_not_change_output():
    """AWS 응답 순서는 보장되지 않는다. 정렬이 코드에 없으면 여기서 깨진다."""
    forward = run()
    reversed_input = run(vpcs=list(reversed(VPCS)), subnets=list(reversed(SUBNETS)))
    assert forward == reversed_input


def test_tag_order_does_not_change_output():
    shuffled = copy.deepcopy(VPCS)
    for vpc in shuffled:
        vpc["Tags"] = list(reversed(vpc.get("Tags", [])))
    assert run() == run(vpcs=shuffled)


def test_only_collected_at_differs_between_runs():
    early = run(clock="2026-01-02T03:04:05Z")
    late = run(clock="2026-03-04T05:06:07Z")
    assert early != late, "collected_at 이 산출물에 들어가지 않았다"
    assert digest_excluding_collected_at(early) == digest_excluding_collected_at(late)


def test_digest_detects_a_real_content_change():
    """위 해시가 상수 함수가 아님을 보이는 대조군."""
    changed = copy.deepcopy(VPCS)
    changed[0]["CidrBlockAssociationSet"] = [{"CidrBlock": "10.99.0.0/16"}]
    assert digest_excluding_collected_at(run()) != digest_excluding_collected_at(
        run(vpcs=changed)
    )


def test_volatile_fields_are_not_collected():
    """`AvailableIpAddressCount` 는 초 단위로 변한다 — 담으면 결정론이 조용히 깨진다."""
    baseline = run()
    drifted = copy.deepcopy(SUBNETS)
    for subnet in drifted:
        subnet["AvailableIpAddressCount"] = subnet["AvailableIpAddressCount"] - 7
    assert baseline == run(subnets=drifted)


def _pairs_of(scope: Scope) -> list[tuple[str, str]]:
    return [(a, r) for a in scope.account_ids for r in scope.regions]


def test_pair_order_survives_pairs_finishing_out_of_order():
    """🔴 쌍을 **동시에** 부르므로 완료 순서는 네트워크 지연이 정한다 — 조립은 그것을 보면 안 된다.

    사람이 보는 결과: 같은 계정·같은 리전을 두 번 수집했는데 산출물 바이트가 달라진다. 그러면
    「무엇이 바뀌었나」(ADR 0004 D4 의 변경 이력)가 **매번 전부 바뀌었다**고 말하고 화면의 이력
    목록이 쓰레기로 찬다. 여기서는 **첫 쌍을 가장 느리게** 만들어 완료 순서를 뒤집는다.
    """
    pairs = _pairs_of(PAIR_SCOPE)
    assert len(pairs) == 4
    # 첫 쌍 0.24s … 마지막 쌍 0.06s → 완료 순서는 요청 순서의 역이다.
    delay = {pair: 0.06 * (len(pairs) - i) for i, pair in enumerate(pairs)}

    def slow(account_id: str, region: str, service: str) -> Any:
        time.sleep(delay[(account_id, region)])
        return FakeEc2()

    def quick(account_id: str, region: str, service: str) -> Any:
        return FakeEc2()

    slow_run = to_json(collect(PAIR_SCOPE, slow, clock=lambda: FIXED_CLOCK))
    quick_run = to_json(collect(PAIR_SCOPE, quick, clock=lambda: FIXED_CLOCK))
    assert slow_run == quick_run

    # 바이트가 같아도 「어느 순서로 적혔나」를 따로 말해 둔다 — FAIL 메시지가 읽히도록.
    seen: list[tuple[str, str]] = []
    for entry in collect(PAIR_SCOPE, slow, clock=lambda: FIXED_CLOCK).coverage:
        key = (entry.account_id, entry.region or "")
        if key not in seen:
            seen.append(key)
    assert seen == pairs


def test_pagination_is_merged():
    """가짜 클라이언트는 응답을 2장으로 쪼갠다 — 병합을 빼먹으면 개수가 준다."""
    document = collect(
        SCOPE, factory(FakeEc2()), clock=lambda: FIXED_CLOCK
    ).to_document()
    counts = {c["resource_type"]: c["item_count"] for c in document["coverage"]}
    assert counts == {"vpc": len(VPCS), "subnet": len(SUBNETS)}


def test_content_digest_is_written_and_ignores_only_the_collection_time():
    """관측 문서가 짝을 맞추는 값(`run.content_digest`)은 **내용이 같으면 같아야** 한다.

    다르면 사람이 보는 결과: 내용이 같은 매시 재수집마다 화면이 「구조가 바뀌었다 — 새 파일을
    받아라」를 띄운다(실측 2026-09-27: 연속 4회 수집이 `run` 밖 바이트 동일).
    """
    early = json.loads(run(clock="2026-01-02T03:04:05Z"))
    late = json.loads(run(clock="2026-03-04T05:06:07Z"))
    assert early["run"]["collected_at"] != late["run"]["collected_at"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", early["run"]["content_digest"])
    assert early["run"]["content_digest"] == late["run"]["content_digest"]
    # 적힌 값이 곧 다시 잰 값이다(지문 자신을 빼고 잰다 — 넣으면 자기 참조로 영영 안 맞는다).
    assert content_digest(early) == early["run"]["content_digest"]


def test_content_digest_ignores_what_could_not_be_read_when_the_picture_is_the_same():
    """#32 리뷰 2 (2026-09-28) — 못 읽은 것은 **구성 변경이 아니다**.

    다르면 사람이 보는 결과: 매시 재수집 한 번이 빈 자원 종류에서 AccessDenied·스로틀을 맞으면(불변식 ④ 의
    정상 경로) 카드·선은 그대로인데 실시간 화면이 「구성이 바뀌었다 — 파일을 다시 받아라」를 띄우고 상태를
    내린다. 다음 회차에 읽히면 또 띄운다.
    """

    def once(errors=None) -> dict:
        client = FakeEc2(subnets=[], errors=errors)
        return json.loads(
            to_json(collect(SCOPE, factory(client), clock=lambda: FIXED_CLOCK))
        )

    clean = once()
    denied = once({"describe_subnets": client_error("AccessDenied", "DescribeSubnets")})
    # 전제: 카드·선은 같고 읽기 결과만 다르다 — 아니면 이 테스트는 아무것도 재지 않는다.
    assert (clean["nodes"], clean["edges"]) == (denied["nodes"], denied["edges"])
    assert clean["coverage"] != denied["coverage"]
    assert clean["run"]["content_digest"] == denied["run"]["content_digest"]


def test_content_digest_changes_when_the_network_changes():
    """대조군 — 지문이 상수면 서브넷이 생겨도 화면이 옛 구조에 새 상태를 겹쳐 그린다."""
    changed = copy.deepcopy(VPCS)
    changed[0]["CidrBlockAssociationSet"] = [{"CidrBlock": "10.99.0.0/16"}]
    assert (
        json.loads(run())["run"]["content_digest"]
        != json.loads(run(vpcs=changed))["run"]["content_digest"]
    )
