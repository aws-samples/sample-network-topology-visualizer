"""계정 자동 발견 축 — ADR 0003 D1.

🔴 `fakes.py` 를 건드리지 않는다(다른 두 PR 이 같은 파일을 고치고 있다). 이 축이 필요한 가짜는
**어태치먼트 응답 하나**뿐이라 여기서 만든다 — 가짜 계정ID 는 AWS 예시 계열만 쓴다(불변식 ⑤ ·
목록은 `scripts/check_no_account_ids.py` 의 `ALLOWED`. 그 밖의 12자리는 pre-commit 훅이 막는다).
"""

from __future__ import annotations

import json

from fakes import ACCOUNT, FakeEc2, client_error
from nettopo import cli
from nettopo.discover import bootstrap_region, discover_accounts, discover_regions

HUB = "111122223333"
MEMBER_A = "222233334444"
MEMBER_B = "333344445555"
GONE = "999900001111"  # 죽은 어태치먼트의 소유자 — 발견되면 안 된다


def _attachment(
    owner: str, state: str = "available", aid: str = "tgw-attach-0"
) -> dict:
    return {
        "TransitGatewayAttachmentId": aid,
        "ResourceOwnerId": owner,
        "TransitGatewayOwnerId": HUB,
        "State": state,
        "ResourceType": "vpc",
    }


class _FakeEc2:
    """`describe_transit_gateway_attachments` 만 있는 최소 가짜(리전별 응답·리전별 오류)."""

    def __init__(self, by_region: dict[str, object]) -> None:
        self._by_region = by_region
        self.region: str | None = None
        self.asked: list[str] = []

    def for_region(self, region: str) -> _FakeEc2:
        clone = _FakeEc2(self._by_region)
        clone.region = region
        clone.asked = self.asked
        return clone

    def can_paginate(self, method: str) -> bool:  # noqa: ARG002
        return False

    def describe_transit_gateway_attachments(self, **_):
        self.asked.append(self.region or "?")
        payload = self._by_region[self.region or "?"]
        if isinstance(payload, Exception):
            raise payload
        return {"TransitGatewayAttachments": payload}


def _factory(fake: _FakeEc2):
    def client(account_id: str, region: str, service: str):
        assert account_id == HUB, "발견은 허브에서만 조회한다"
        assert service == "ec2"
        return fake.for_region(region)

    return client


def test_member_accounts_come_from_the_attachment_owner():
    fake = _FakeEc2(
        {
            "us-east-1": [
                _attachment(MEMBER_B, aid="tgw-attach-b"),
                _attachment(HUB, aid="tgw-attach-hub"),
                _attachment(GONE, state="deleting", aid="tgw-attach-gone"),
            ],
            "us-west-2": [
                _attachment(MEMBER_A, aid="tgw-attach-a"),
                _attachment(
                    MEMBER_B, aid="tgw-attach-b2"
                ),  # 같은 계정을 두 번 봐도 하나
            ],
        }
    )

    found = discover_accounts(
        hub_account=HUB, regions=("us-east-1", "us-west-2"), client=_factory(fake)
    )

    # 정렬 + 중복 제거 + 허브 포함(불변식 ②: 같은 입력 → 같은 순서)
    assert found.accounts == (HUB, MEMBER_A, MEMBER_B)
    assert found.discovered == (MEMBER_A, MEMBER_B)
    assert found.looked == ("us-east-1", "us-west-2")
    assert not found.failures
    assert not found.blind
    # 죽은 어태치먼트의 소유자는 들어오지 않는다 — 지워진 경로로 계정을 만들면 assume 만 실패한다.
    assert GONE not in found.accounts


def test_one_region_denied_does_not_lose_the_other():
    """부분 실패는 정상 경로다(불변식 ④) — 못 본 리전은 사유로 남고 본 리전은 그대로 쓴다."""
    fake = _FakeEc2(
        {
            "us-east-1": client_error(
                "UnauthorizedOperation", "DescribeTransitGatewayAttachments"
            ),
            "us-west-2": [_attachment(MEMBER_A)],
        }
    )

    found = discover_accounts(
        hub_account=HUB, regions=("us-east-1", "us-west-2"), client=_factory(fake)
    )

    assert found.accounts == (HUB, MEMBER_A)
    assert found.looked == ("us-west-2",)
    assert [(f.region, f.status, f.reason.code) for f in found.failures] == [
        ("us-east-1", "denied", "access_denied")
    ]
    assert not found.blind


def test_seeing_nothing_anywhere_is_blind_not_empty():
    """🔴 「못 봤다」를 「멤버가 없다」로 바꾸면 스냅샷이 거짓말을 한다 — 그 구별이 `blind` 다."""
    fake = _FakeEc2(
        {
            "us-east-1": client_error(
                "AccessDenied", "DescribeTransitGatewayAttachments"
            ),
            "us-west-2": client_error(
                "AccessDenied", "DescribeTransitGatewayAttachments"
            ),
        }
    )

    found = discover_accounts(
        hub_account=HUB, regions=("us-east-1", "us-west-2"), client=_factory(fake)
    )

    assert found.blind
    assert found.discovered == ()
    assert len(found.failures) == 2
    # 대조군: 한 리전이라도 응답이 오면 blind 가 아니다(위 두 테스트가 그것을 잰다).


def test_garbage_owner_ids_are_ignored():
    fake = _FakeEc2(
        {
            "us-east-1": [
                _attachment("not-an-account"),
                _attachment("1111222233"),  # 10자리
                {"TransitGatewayAttachmentId": "tgw-attach-x", "State": "available"},
                _attachment(MEMBER_A),
            ]
        }
    )

    found = discover_accounts(
        hub_account=HUB, regions=("us-east-1",), client=_factory(fake)
    )

    assert found.accounts == (HUB, MEMBER_A)


# ------------------------------------------------------------- 리전 자동 발견


class _Ec2WithRegions(FakeEc2):
    """12종을 아는 가짜에 `describe_regions` 만 덧댄다(`fakes.py` 는 건드리지 않는다)."""

    def __init__(
        self,
        *,
        regions: tuple[tuple[str, str], ...] = (("us-east-1", "opt-in-not-required"),),
        regions_error: Exception | None = None,
        **kw,
    ) -> None:
        super().__init__(**kw)
        self._regions = regions
        self._regions_error = regions_error

    def can_paginate(self, method: str) -> bool:
        # DescribeRegions 는 페이지네이션이 없다. 나머지는 가짜(`fakes.py`)의 사실을 따른다 — DX 2종도
        # 페이지네이터가 없어(`UNPAGINATED`) 여기서 True 를 돌려주면 없는 페이지네이터를 부른다.
        return method != "describe_regions" and super().can_paginate(method)

    def describe_regions(self, **_):
        if self._regions_error is not None:
            raise self._regions_error
        return {
            "Regions": [
                {"RegionName": name, "OptInStatus": status}
                for name, status in self._regions
            ]
        }


def _plain_factory(client):
    def make(account_id: str, region: str, service: str):
        assert account_id == HUB, "발견은 허브에서만 조회한다"
        assert service == "ec2"
        assert region == "us-east-1", "부트스트랩 리전이 아닌 곳에서 물었다"
        return client

    return make


def test_regions_are_the_enabled_ones_sorted():
    ec2 = _Ec2WithRegions(
        regions=(
            ("us-west-2", "opt-in-not-required"),
            (
                "ap-east-1",
                "not-opted-in",
            ),  # 켜지 않은 리전 — 들어오면 assume 이 거부된다
            ("us-east-1", "opted-in"),
            ("", "opt-in-not-required"),  # 쓰레기
            ("global", "opt-in-not-required"),  # 리전 이름이 아니다
        )
    )

    found = discover_regions(
        hub_account=HUB, bootstrap="us-east-1", client=_plain_factory(ec2)
    )

    assert found.regions == ("us-east-1", "us-west-2")
    assert found.asked == "us-east-1"
    assert found.failure is None
    assert not found.blind


def test_region_discovery_failure_is_blind_with_a_reason():
    ec2 = _Ec2WithRegions(
        regions_error=client_error("UnauthorizedOperation", "DescribeRegions")
    )

    found = discover_regions(
        hub_account=HUB, bootstrap="us-east-1", client=_plain_factory(ec2)
    )

    assert found.blind
    assert found.regions == ()
    assert found.failure is not None
    assert (found.failure.region, found.failure.status) == ("us-east-1", "denied")
    assert found.failure.reason.api == "ec2:DescribeRegions"


def test_bootstrap_region_comes_from_the_partition_not_the_shell():
    """🔴 셸 기본 리전을 쓰면 같은 명령이 사람마다 다른 범위를 수집한다."""
    assert bootstrap_region("arn:aws:sts::111122223333:assumed-role/x/y") == "us-east-1"
    assert bootstrap_region("arn:aws-cn:iam::111122223333:user/x") == "cn-north-1"
    assert (
        bootstrap_region("arn:aws-us-gov:iam::111122223333:user/x") == "us-gov-west-1"
    )
    # 모르는 파티션·망가진 ARN 은 **추측하지 않는다** — 호출자가 `--region` 을 요구받는다.
    assert bootstrap_region("arn:aws-iso-q:iam::111122223333:user/x") is None
    assert bootstrap_region("not-an-arn") is None


# ---------------------------------------------------------------- CLI 연결


class _FakeSts:
    def __init__(self, arn: str = "arn:aws:sts::secret:secret") -> None:
        self._arn = arn

    def get_caller_identity(self):
        return {"Account": ACCOUNT, "Arn": self._arn}


def _patch(monkeypatch, ec2, sts: _FakeSts | None = None):
    def fake_read_only_client(service, **_):
        return (sts or _FakeSts()) if service == "sts" else ec2

    monkeypatch.setattr(cli, "read_only_client", fake_read_only_client)


def test_accounts_flag_is_optional_and_discovery_is_reported(
    monkeypatch, tmp_path, capsys
):
    """`--accounts` 없이 돌아간다 — ADR 0003 D1 의 「목록을 요구하지 않는다」."""
    _patch(monkeypatch, FakeEc2())
    out = tmp_path / "snap.json"

    rc = cli.main(["collect", "--region", "us-east-1", "--out", str(out)])

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "계정 자동 발견" in captured.out
    assert ACCOUNT not in captured.out + captured.err, "계정ID 가 그대로 찍혔다"
    document = json.loads(out.read_text(encoding="utf-8"))
    assert {n["account_id"] for n in document["nodes"] if n.get("account_id")} == {
        ACCOUNT
    }


def test_blind_discovery_stops_instead_of_pretending(monkeypatch, tmp_path, capsys):
    ec2 = FakeEc2(
        errors={
            "describe_transit_gateway_attachments": client_error(
                "AccessDenied", "DescribeTransitGatewayAttachments"
            )
        }
    )
    _patch(monkeypatch, ec2)
    out = tmp_path / "snap.json"

    rc = cli.main(["collect", "--region", "us-east-1", "--out", str(out)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "--accounts" in err
    assert not out.exists(), "발견이 실패했는데 산출물을 남겼다"


# 🔴 「목록을 주면 발견을 돌리지 않는다」(옛 `test_explicit_accounts_skip_discovery`)는 사용자 결정
# 2026-10-20(C-2)으로 뒤집혔다 — 목록을 줘도 발견을 돌리고 합집합을 수집한다. 그 축은
# `test_cli.py` 의 「명시 목록 ∪ 자동 발견」 묶음(a~d)이 잰다.


def test_region_flag_is_optional_and_discovered_regions_are_collected(
    monkeypatch, tmp_path, capsys
):
    """`--region` 없이 돌아간다 — ADR 0003 D1 의 나머지 절반."""
    _patch(
        monkeypatch,
        _Ec2WithRegions(
            regions=(
                ("us-east-1", "opt-in-not-required"),
                ("us-west-2", "opt-in-not-required"),
            )
        ),
    )
    out = tmp_path / "snap.json"

    rc = cli.main(["collect", "--accounts", ACCOUNT, "--out", str(out)])

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "리전 자동 발견" in captured.out
    assert "활성 리전 2개(부트스트랩 us-east-1)" in captured.out
    document = json.loads(out.read_text(encoding="utf-8"))
    # 발견한 리전이 **실제 수집 범위**가 됐는가(보고만 하고 안 쓰면 이 어서션이 깨진다).
    assert document["scope"]["regions"] == ["us-east-1", "us-west-2"]
    assert {n["region"] for n in document["nodes"] if n.get("region")} == {
        "us-east-1",
        "us-west-2",
    }


def test_blind_region_discovery_stops_instead_of_guessing(
    monkeypatch, tmp_path, capsys
):
    _patch(
        monkeypatch,
        _Ec2WithRegions(regions_error=client_error("AccessDenied", "DescribeRegions")),
    )
    out = tmp_path / "snap.json"

    rc = cli.main(["collect", "--accounts", ACCOUNT, "--out", str(out)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "--region" in err
    assert not out.exists(), "리전을 모르는데 산출물을 남겼다"


def test_unknown_partition_asks_for_regions(monkeypatch, tmp_path, capsys):
    """모르는 파티션에서 `us-east-1` 을 추측하면 사람이 그 실패를 권한 문제로 읽는다."""
    _patch(
        monkeypatch,
        _Ec2WithRegions(),
        sts=_FakeSts(arn="arn:aws-iso-q:sts::secret:secret"),
    )
    out = tmp_path / "snap.json"

    rc = cli.main(["collect", "--accounts", ACCOUNT, "--out", str(out)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "--region" in err
    assert not out.exists()


def test_explicit_regions_skip_region_discovery(monkeypatch, tmp_path, capsys):
    _patch(
        monkeypatch,
        _Ec2WithRegions(regions_error=client_error("AccessDenied", "DescribeRegions")),
    )
    out = tmp_path / "snap.json"

    rc = cli.main(
        ["collect", "--region", "us-east-1", "--accounts", ACCOUNT, "--out", str(out)]
    )

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "리전 자동 발견" not in captured.out
