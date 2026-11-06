"""`cli._SessionCache` — 병렬 수집의 유일한 경합점(ADR 0004 D2-6/D2-7).

이 클래스는 실계정 실측이 두 번 지목한 자리다. 그런데 검사가 **하나도 없었다** — 그래서
「계정당 한 번만 assume 한다」도 「리전이 클라이언트마다 따로 간다」도 통과가 아니라 미측정이었다.
여기서 재는 것은 셋이다:

1. **assume 횟수** — 리전 수만큼 부르면 그것이 상태 단 예산의 천장이 된다(D2-6: 크로스계정 쌍이
   자기 계정 쌍보다 3.3배 비쌌던 원인).
2. **리전이 클라이언트마다 따로 간다** — 세션을 공유하게 만든 대가로 이 계약이 깨지면 화면이
   **다른 리전의 자원을 그 리전 것으로** 그린다. 부분 실패보다 나쁘다(있는데 없다고 그리는 것의
   쌍둥이다 — 없는데 있다고 그린다).
3. **잠금** — 같은 계정을 여러 스레드가 동시에 요구해도 assume 는 한 번이다.

🔴 실제 AWS 를 부르지 않는다. `cli` 모듈이 들고 있는 세 이름(`read_only_session`·
`assume_role_session`·`read_only_client`)을 갈아끼워 **호출 자체**를 센다.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from nettopo import cli

HUB = "111122223333"
MEMBER = "222233334444"
OTHER_MEMBER = "333344445555"


class _Recorder:
    """세션·클라이언트 생성을 기록하는 대역. 세션은 「어느 리전에서 만들었나」만 들고 있다."""

    def __init__(self, *, assume_delay: float = 0.0) -> None:
        self.assumed: list[tuple[str, str]] = []  # (role_arn, region)
        self.external_ids: list[str | None] = []  # assume 마다 넘긴 ExternalId
        self.local_sessions: list[str] = []  # 자기 계정 세션을 만든 리전
        self.clients: list[tuple[str, str, object]] = []  # (service, region, session)
        self._assume_delay = assume_delay
        self._lock = threading.Lock()

    def read_only_session(self, *, profile_name=None, region_name=None) -> object:  # noqa: ARG002
        with self._lock:
            self.local_sessions.append(region_name)
        return object()

    def assume_role_session(
        self,
        *,
        role_arn,
        session_name,
        region_name,
        profile_name=None,  # noqa: ARG002
        external_id=None,
    ) -> object:
        self.external_ids.append(external_id)
        # 지연을 넣는 것은 잠금 검사 때문이다 — 실제 assume 도 네트워크 왕복이다.
        time.sleep(self._assume_delay)
        with self._lock:
            self.assumed.append((role_arn, region_name))
        return object()

    def read_only_client(self, service, *, session=None, region_name=None, **_) -> Any:
        with self._lock:
            self.clients.append((service, region_name, session))
        return object()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Recorder:
        monkeypatch.setattr(cli, "read_only_session", self.read_only_session)
        monkeypatch.setattr(cli, "assume_role_session", self.assume_role_session)
        monkeypatch.setattr(cli, "read_only_client", self.read_only_client)
        return self


def _cache() -> Any:
    return cli._SessionCache(
        profile="example-readonly", role_name="NetworkReadOnlyRole", caller_account=HUB
    )


def _rec(monkeypatch: pytest.MonkeyPatch, **kwargs) -> _Recorder:
    return _Recorder(**kwargs).install(monkeypatch)


def test_one_assume_per_account_not_per_region(monkeypatch: pytest.MonkeyPatch):
    """🔴 D2-7 의 계약. 리전이 3개여도 대상 계정에 들어가는 것은 **한 번**이다.

    사람이 보는 결과: 계정 30개를 3리전으로 보는 화면이 한 바퀴 도는 시간. 리전마다 들어가면
    진입 호출이 90번이고 그것이 한 줄로 서서 예산(4분)을 거의 다 먹는다.
    """
    rec = _rec(monkeypatch)
    cache = _cache()
    for region in ("us-east-1", "us-west-2", "ap-northeast-2"):
        cache.client(MEMBER, region, "ec2")

    assert len(rec.assumed) == 1, f"리전마다 assume 했다: {rec.assumed}"
    assert rec.assumed[0][0].endswith(f":{MEMBER}:role/NetworkReadOnlyRole")
    # 클라이언트는 리전마다 따로 만들어진다 — 세션만 공유한다.
    assert len(rec.clients) == 3
    assert {s for _, _, s in rec.clients} == {rec.clients[0][2]}


def test_each_client_carries_its_own_region(monkeypatch: pytest.MonkeyPatch):
    """🔴 세션을 공유하는 대가. 리전이 클라이언트마다 따로 가지 않으면 **전 리전이 한 리전으로
    접힌다** — 화면은 다른 리전의 자원을 그 리전 것으로 그리고, 사람은 그것을 알 방법이 없다.
    """
    rec = _rec(monkeypatch)
    cache = _cache()
    wanted = ("us-east-1", "us-west-2", "ap-northeast-2")
    for region in wanted:
        cache.client(MEMBER, region, "ec2")

    assert tuple(region for _, region, _ in rec.clients) == wanted


def test_each_account_gets_its_own_session(monkeypatch: pytest.MonkeyPatch):
    """계정을 합치면 한 계정의 자격증명으로 다른 계정을 읽는다(= 조용히 빈 결과)."""
    rec = _rec(monkeypatch)
    cache = _cache()
    cache.client(MEMBER, "us-east-1", "ec2")
    cache.client(OTHER_MEMBER, "us-east-1", "ec2")
    cache.client(HUB, "us-east-1", "ec2")

    assert len(rec.assumed) == 2
    assert [arn.split(":")[4] for arn, _ in rec.assumed] == [MEMBER, OTHER_MEMBER]
    # 자기 계정은 assume 하지 않는다(불변식 ① — `sts:AssumeRole` 은 크로스계정 전용 예외다).
    assert rec.local_sessions == ["us-east-1"]
    assert len({s for _, _, s in rec.clients}) == 3


def test_concurrent_callers_assume_once(monkeypatch: pytest.MonkeyPatch):
    """🔴 잠금. 쌍을 동시에 돌리면 같은 계정을 여러 스레드가 **동시에** 요구한다.

    잠금이 없으면 8개 스레드가 전부 빈 dict 를 보고 각자 assume 한다 — STS 호출이 8배가 되고
    throttle 에 가까워진다. 지연 0.05s 는 그 겹침을 확실하게 만들기 위한 것이다.
    """
    rec = _rec(monkeypatch, assume_delay=0.05)
    cache = _cache()
    regions = [f"r-{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=len(regions)) as pool:
        list(pool.map(lambda r: cache.client(MEMBER, r, "ec2"), regions))

    assert len(rec.assumed) == 1, f"동시 호출이 assume 를 {len(rec.assumed)}번 했다"
    assert len(rec.clients) == 8


def test_external_id_reaches_every_assume_and_only_when_given(
    monkeypatch: pytest.MonkeyPatch,
):
    """스택 B `ExternalId` 를 준 멤버 역할은 같은 값을 넘겨야만 들어간다 — 빠지면 그 계정 칸 전부가 「권한 없음」이다.

    비운 기본(None)은 예전과 같아야 한다 — 조건이 없는 역할에 빈 문자열을 넘기면 STS 가 거절한다.
    """
    rec = _rec(monkeypatch)
    cli._SessionCache(
        profile=None,
        role_name="NetworkReadOnlyRole",
        caller_account=HUB,
        external_id="ext-123",
    ).client(MEMBER, "us-east-1", "ec2")
    _cache().client(OTHER_MEMBER, "us-east-1", "ec2")
    assert rec.external_ids == ["ext-123", None], rec.external_ids


@pytest.mark.parametrize("command", ["collect", "observe"])
def test_external_id_option_exists_on_both_commands(command: str):
    """구조 빌드는 collect 와 observe 를 둘 다 돌린다 — 한쪽에만 있으면 그 단계만 거부된다."""
    base = {"collect": ["--region", "us-east-1"], "observe": ["--topology", "t.json"]}[
        command
    ]
    args = cli.build_parser().parse_args(
        [command, *base, "--out", "o.json", "--external-id", "ext-123"]
    )
    assert args.external_id == "ext-123"
    assert (
        cli.build_parser().parse_args([command, *base, "--out", "o.json"]).external_id
        is None
    )
