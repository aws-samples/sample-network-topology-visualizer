"""CLI 축 — 신원 확인이 대상 리전과 독립인가, 실패를 어떻게 보고하는가, 계정ID 를 가리는가.

AWS 를 타지 않는다: `cli.read_only_client` 를 가짜로 바꿔 STS·EC2 응답을 주입한다.
`read_only_session` 은 그대로 두어도 네트워크를 타지 않는다(세션 생성은 오프라인이고,
자격증명은 `conftest.py` 의 더미 env 로 해석된다).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from fakes import (
    ACCOUNT,
    ACCOUNT_NAME,
    EGRESS_NAT,
    NAT_GATEWAYS,
    RESOURCE_NODE_COUNT,
    TRANSIT_GATEWAY_ATTACHMENTS,
    FakeCloudWatch,
    FakeEc2,
    client_error,
    series_key,
)
from nettopo import cli
from nettopo.metrics import PERIOD_SECONDS, WINDOW_SECONDS
from nettopo.sources import Source, SourceScan


class _FakeSession:
    """`read_only_session` 대역 — 어느 프로필로 만든 세션인지만 들고 있다."""

    def __init__(self, profile: str | None) -> None:
        self.profile = profile


class _FakeSts:
    def __init__(self, account: str = ACCOUNT) -> None:
        self._account = account

    def get_caller_identity(self):
        return {"Account": self._account, "Arn": "arn:aws:sts::secret:secret"}


def _patch_clients(monkeypatch, *, ec2=None, sts=None, calls=None, cloudwatch=None):
    def fake_read_only_client(
        service, *, session=None, profile_name=None, region_name=None, **_
    ):
        if calls is not None:
            calls.append((service, region_name))
        if service == "sts":
            return sts or _FakeSts()
        if service == "cloudwatch":
            # 🔴 `cloudwatch` 를 EC2 가짜로 돌려주면 `get_metric_data` 가 AttributeError 가 되고
            # 그것이 「2층 실패」로 기록된다 — 2층을 안 켠 것과 구별되지 않는 초록이 된다.
            assert cloudwatch is not None, "2층을 켰는데 가짜 CloudWatch 가 없다"
            return cloudwatch
        return ec2 or FakeEc2()

    monkeypatch.setattr(cli, "read_only_client", fake_read_only_client)


def _run(tmp_path, region="ap-east-1", accounts=ACCOUNT):
    out = tmp_path / "snap.json"
    rc = cli.main(
        ["collect", "--region", region, "--accounts", accounts, "--out", str(out)]
    )
    return rc, out


def test_explicit_accounts_always_include_the_hub(monkeypatch, tmp_path):
    """`--accounts` 에 허브(호출자) 계정이 없어도 범위에 들어간다 — 자동 발견과 같은 규칙.

    실측 2026-10-14: 스택 A `TargetAccounts=<소스 계정>` 만 주고 수집하니 허브 리소스가 통째로
    사라졌다(노드 201 → 20). 사람은 이 파라미터를 「더할 계정」으로 적는다(ADR 0005 · 런북) —
    교체 목록으로 읽으면 자기 계정을 빠뜨리는 것이 가장 흔한 실수가 된다.
    """
    other = "222233334444"
    monkeypatch.setattr(
        cli,
        "assume_role_session",
        lambda **kwargs: _FakeSession(kwargs.get("profile_name")),
    )
    _patch_clients(monkeypatch)

    rc, out = _run(tmp_path, accounts=other)

    assert rc == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert set(document["scope"]["account_ids"]) == {ACCOUNT, other}
    accounts_drawn = {n["id"] for n in document["nodes"] if n["type"] == "account"}
    assert len(accounts_drawn) == 2


# ---------------------------------------------------------------- 명시 목록 ∪ 자동 발견(C-2)
# 🔴 사용자 결정 2026-10-20: `--accounts` 는 **추가 목록**이다(ADR 0005). 목록을 줘도 TGW 발견을
# 돌리고 합집합을 수집한다 — 목록이 발견을 끄면 계정 하나를 더하려던 사람이 TGW 가 아는 다른 멤버를
# 조용히 잃는다. 발견이 모든 리전에서 실패해도 목록이 있으면 멈추지 않고 경고만 남긴다.

_NAMED = "222233334444"  # 사람이 `--accounts` 로 적은 계정
_FOUND = "333344445555"  # TGW 어태치먼트만 아는 계정(목록에는 없다)


def _member_attachment(owner: str) -> dict[str, Any]:
    return {
        "TransitGatewayAttachmentId": "tgw-attach-0eee5555ffff1111",
        "TransitGatewayId": TRANSIT_GATEWAY_ATTACHMENTS[0]["TransitGatewayId"],
        "TransitGatewayOwnerId": ACCOUNT,
        "ResourceOwnerId": owner,
        "ResourceType": "vpc",
        "ResourceId": "vpc-0eee5555ffff1111",
        "State": "available",
        "Tags": [],
    }


def _blind_ec2() -> FakeEc2:
    return FakeEc2(
        errors={
            "describe_transit_gateway_attachments": client_error(
                "AccessDenied", "DescribeTransitGatewayAttachments"
            )
        }
    )


def _patch_members(monkeypatch, ec2: FakeEc2) -> None:
    monkeypatch.setattr(
        cli,
        "assume_role_session",
        lambda **kwargs: _FakeSession(kwargs.get("profile_name")),
    )
    _patch_clients(monkeypatch, ec2=ec2)


def test_explicit_accounts_still_run_discovery_and_collect_the_union(
    monkeypatch, tmp_path, capsys
):
    """(a) 목록 ∪ 발견 ∪ 허브 — 목록에 없는 TGW 멤버도 수집 범위에 들어간다(정렬)."""
    _patch_members(
        monkeypatch,
        FakeEc2(
            transit_gateway_attachments=[
                *TRANSIT_GATEWAY_ATTACHMENTS,
                _member_attachment(_FOUND),
            ]
        ),
    )

    rc, out = _run(tmp_path, accounts=_NAMED)

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["scope"]["account_ids"] == sorted({ACCOUNT, _NAMED, _FOUND})
    assert "허브 1 + 명시 1개 + 발견으로 더한 계정 1개" in captured.out
    assert cli._mask(_FOUND) in captured.out
    for raw in (ACCOUNT, _NAMED, _FOUND):
        assert raw not in captured.out + captured.err, "계정ID 가 그대로 찍혔다"


def test_blind_discovery_with_explicit_accounts_collects_the_list_and_warns(
    monkeypatch, tmp_path, capsys
):
    """(b) 발견이 전 리전에서 실패해도 목록이 있으면 멈추지 않는다 — 목록 ∪ 허브 + 경고."""
    _patch_members(monkeypatch, _blind_ec2())

    rc, out = _run(tmp_path, accounts=_NAMED)

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["scope"]["account_ids"] == sorted({ACCOUNT, _NAMED})
    assert (
        "⚠ 계정 자동 발견이 모든 리전에서 실패했다 — "
        "명시한 계정 목록(1개 + 허브)으로만 수집한다"
    ) in captured.err
    # 리전별 실패 줄은 오늘처럼 그대로 나온다(어느 리전이 왜 막혔는지).
    assert "⚠ 계정 자동 발견 실패 ap-east-1" in captured.err
    assert "오류:" not in captured.err


def test_blind_discovery_without_a_list_still_stops(monkeypatch, tmp_path, capsys):
    """(c) 대조군 — 목록이 없으면 오늘과 같이 멈춘다(허브 하나뿐이라고 거짓말하지 않는다)."""
    _patch_members(monkeypatch, _blind_ec2())
    out = tmp_path / "snap.json"

    rc = cli.main(["collect", "--region", "ap-east-1", "--out", str(out)])

    err = capsys.readouterr().err
    assert rc == 1
    assert "오류: 모든 리전에서 계정 자동 발견이 실패했다" in err
    assert "명시한 계정 목록" not in err
    assert not out.exists()


def test_explicit_accounts_when_discovery_adds_nothing(monkeypatch, tmp_path, capsys):
    """(d) 발견이 목록 밖의 계정을 못 찾으면 범위는 목록 ∪ 허브 그대로다(입력 순서 무관 · 정렬)."""
    _patch_members(monkeypatch, FakeEc2())

    rc, out = _run(tmp_path, accounts=f"{_NAMED},{ACCOUNT}")

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["scope"]["account_ids"] == sorted({ACCOUNT, _NAMED})
    assert "허브 1 + 명시 1개 + 발견으로 더한 계정 0개" in captured.out
    assert "⚠ 계정 자동 발견" not in captured.err


def test_caller_identity_does_not_depend_on_target_region(monkeypatch, tmp_path):
    """🔴 옵트인 리전의 리전별 STS 엔드포인트는 계정이 그 리전을 켜지 않으면 토큰을 거부한다
    (실측 2026-09-14: `--region ap-east-1` → `InvalidClientTokenId`). 대상 리전 선택이
    신원 확인을 깨뜨려서는 안 되므로 STS 는 전역 엔드포인트로 부른다.
    """
    calls: list[tuple[str, str | None]] = []
    _patch_clients(monkeypatch, calls=calls)

    rc, out = _run(tmp_path, region="ap-east-1")

    assert rc == 0
    assert [r for s, r in calls if s == "sts"] == ["aws-global"]
    assert ("ec2", "ap-east-1") in calls
    document = json.loads(out.read_text(encoding="utf-8"))
    # 컨테이너 2개(account·region) + 1차 리소스 12종 전부. CLI 기본 `--resource-types` 가
    # 한 종류라도 빠지면 이 수가 줄어든다.
    # + 파생 노드 S3 버킷 1(2.8.0 · 기본 가짜 CloudFront 의 S3 원본 `assets` — 두 배포가 같은 버킷 · `origins.py`)
    assert len(document["nodes"]) == 2 + RESOURCE_NODE_COUNT + 1


def test_credential_failure_is_reported_without_a_traceback(
    monkeypatch, tmp_path, capsys
):
    def boom(service, **_):
        raise client_error(
            "InvalidClientTokenId", "GetCallerIdentity", "토큰이 유효하지 않다"
        )

    monkeypatch.setattr(cli, "read_only_client", boom)

    rc, out = _run(tmp_path)

    assert rc == 1
    err = capsys.readouterr().err
    assert "InvalidClientTokenId" in err
    assert not out.exists(), "신원도 모르는데 산출물을 남겼다"


def test_observe_also_reports_credential_failure_without_a_traceback(
    monkeypatch, tmp_path, capsys
):
    """🔴 같은 처리가 `observe` 쪽에도 있지만 **재는 검사가 없었다**(2026-09-23).

    브레이크 ⑩(`신원예외방치`)이 「치환 실패」로 SKIP 된 것이 단서였다 — 같은 `except` 줄이
    `collect`·`observe` 두 곳으로 늘어난 것이다. 위 검사는 `collect` 만 지나가므로 `observe`
    쪽 처리는 통과가 아니라 미측정이었다.

    사람이 겪는 일: SSO 세션이 끊긴 채로 주기 관측을 돌리면 화면에 파이썬 트레이스백이 아니라
    「신원 확인 실패 + 프로필·SSO 세션을 확인하라」가 떠야 하고, 반쪽 산출물이 남지 않아야 한다.
    """
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    def boom(service, **_):
        raise client_error("ExpiredToken", "GetCallerIdentity", "세션이 만료됐다")

    monkeypatch.setattr(cli, "read_only_client", boom)
    out = tmp_path / "obs.json"
    capsys.readouterr()

    rc = cli.main(["observe", "--topology", str(snapshot), "--out", str(out)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "ExpiredToken" in err
    assert "Traceback" not in err
    assert not out.exists(), "신원도 모르는데 산출물을 남겼다"


def test_observe_says_which_kind_failed_and_how_much_it_read(
    monkeypatch, tmp_path, capsys
):
    """🔴 「일부만 읽었다」가 사람에게 그대로 보여야 한다 (ADR 0006 O10).

    실측 2026-09-23: 종류 없이 `…/us-east-1/describe = denied` 만 6줄 찍혔고, 같은 자리에서
    4종은 실제로 읽혔다. 사람은 그 화면을 「이 계정은 못 읽었다」로 읽는다 — O10 이 없애려던
    오독을 CLI 가 되살린 것이다. 그래서 ① 종류를 적고 ② 자리마다 「N종 중 M종」을 먼저 말한다.
    """
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    ec2 = FakeEc2(
        errors={"describe_subnets": client_error("AccessDenied", "DescribeSubnets")}
    )
    _patch_clients(monkeypatch, ec2=ec2)
    capsys.readouterr()
    rc = cli.main(
        [
            "observe",
            "--topology",
            str(snapshot),
            "--out",
            str(tmp_path / "obs.json"),
        ]
    )

    assert rc == 0  # 부분 실패는 정상 경로다(불변식 ④)
    err = capsys.readouterr().err
    assert ACCOUNT not in err, "계정ID 가 그대로 찍혔다"
    assert "/subnet/describe = denied" in err, err
    # 분모가 보여야 한다 — 실패한 종류만 세면 「전부 실패」와 구별되지 않는다.
    assert f"{len(cli.OBSERVED_RESOURCE_TYPES)}종 중" in err, err
    assert f"중 {len(cli.OBSERVED_RESOURCE_TYPES) - 1}종만 읽었다" in err, err


def _observe_args(snapshot, out, *extra):
    return ["observe", "--topology", str(snapshot), "--out", str(out), *extra]


def test_metrics_layer_is_off_unless_asked(monkeypatch, tmp_path, capsys):
    """🔴 기본 off(ADR 0006 D4) — 2층은 권한이 더 필요하고, 안 켠 것은 실패가 아니다.

    사람이 겪는 일: 지금까지처럼 `observe` 만 돌리면 CloudWatch 를 **한 번도 부르지 않고**,
    화면의 「확인 불가」는 「안 켰다」라는 뜻이다(권한을 의심하게 만들지 않는다).
    """
    calls: list[tuple[str, str | None]] = []
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    _patch_clients(monkeypatch, calls=calls)
    out = tmp_path / "obs.json"
    capsys.readouterr()

    assert cli.main(_observe_args(snapshot, out)) == 0

    assert [s for s, _r in calls if s == "cloudwatch"] == []
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["layers"] == {"describe": True, "metric": False}
    assert "2층" not in capsys.readouterr().out


def test_a_metrics_profile_without_the_metrics_flag_says_so(
    monkeypatch, tmp_path, capsys
):
    """🔴 「안 켠 것은 실패가 아니다」의 **반대편 구멍** — 안 켠 것을 안 켰다고 말하지 않는 경우.

    위 검사는 「`--metrics` 없이 돌리면 CloudWatch 를 안 부른다」를 지킨다. 그런데 `--metrics-profile`
    만 주고 `--metrics` 를 잊으면 **켤 생각이었던 사람에게도** 같은 침묵이 돌아온다. 실측
    2026-09-24: `--metrics-profile example-metrics` 만 주고 실계정에서 두 번 돌려 1층 숫자(`live` 0)를
    받았고, 산출물의 `layers.metric` 을 직접 열어 보기 전까지 그 사실을 알 수 없었다.

    사람이 겪는 일: 주기 배치에 지표 자격증명을 넣어 두고 「흐름 있음」이 뜨기를 기다리는데, 화면은
    매 실행 전부 「확인 불가」다. 로그에 실패가 없으니 의심할 곳이 권한밖에 없고, 실제로는 스위치를
    안 켠 것이다. 그래서 **경고를 내고 계속** 돈다 — 1층 관측은 완전히 유효하므로 죽이면 지표 없이
    상태만 보려는 사람까지 막힌다(불변식 ④).
    """
    calls: list[tuple[str, str | None]] = []
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    _patch_clients(monkeypatch, calls=calls)
    out = tmp_path / "obs.json"
    capsys.readouterr()

    rc = cli.main(
        _observe_args(snapshot, out, "--metrics-profile", "example-metrics"),
    )

    assert rc == 0  # 죽지 않는다 — 1층은 그대로 나온다
    captured = capsys.readouterr()
    assert "--metrics" in captured.err, captured.err
    assert [s for s, _r in calls if s == "cloudwatch"] == []
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["layers"] == {"describe": True, "metric": False}

    # 대조군 ① 둘 다 안 주면 경고가 없다 — 「기본 off」는 정상 경로이고 잔소리할 일이 아니다.
    capsys.readouterr()
    assert cli.main(_observe_args(snapshot, tmp_path / "plain.json")) == 0
    assert "--metrics" not in capsys.readouterr().err

    # 대조군 ② 의도한 조합(둘 다)에서도 경고가 없고 2층이 실제로 읽힌다.
    monkeypatch.setattr(
        cli, "read_only_session", lambda **kw: _FakeSession(kw.get("profile_name"))
    )
    _patch_clients(monkeypatch, calls=calls, cloudwatch=FakeCloudWatch({}))
    calls.clear()
    capsys.readouterr()
    both = tmp_path / "both.json"
    assert (
        cli.main(
            _observe_args(
                snapshot, both, "--metrics", "--metrics-profile", "example-metrics"
            )
        )
        == 0
    )
    assert "--metrics-profile 을 줬지만" not in capsys.readouterr().err
    assert [s for s, _r in calls if s == "cloudwatch"] != []
    assert json.loads(both.read_text(encoding="utf-8"))["layers"]["metric"] is True


def test_metrics_flag_reads_cloudwatch_and_splits_the_numbers(
    monkeypatch, tmp_path, capsys
):
    """`--metrics` 를 주면 흐름/조용함/확인 불가/못 읽음을 **따로** 말한다(§O7-1 함정 1 의 방어).

    사람이 겪는 일: 흐르는 NAT 는 `live` 로 올라가고, 지표가 안 오는 대상은 「확인 불가」로
    남는다. 네 수를 한 줄에 합쳐 버리면 차원 상수가 통째로 틀린 날에도 화면이 똑같아 보인다.

    같은 실행에서 **차원을 못 만든 대상**(1층이 `TransitGatewayId` 를 안 준 첨부)도 한 개 넣는다 —
    그 경고에는 대상 id 가 들어가고 그 안에 계정ID 가 있다(§0: 화면에 그대로 찍지 않는다).
    """
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    attachments = copy.deepcopy(TRANSIT_GATEWAY_ATTACHMENTS)
    del attachments[0]["TransitGatewayId"]
    cloudwatch = FakeCloudWatch(
        {
            series_key(
                "AWS/NATGateway",
                "BytesOutToDestination",
                {"NatGatewayId": EGRESS_NAT},
            ): [8192.0, 0.0, 4096.0]
        }
    )
    calls: list[tuple[str, str | None]] = []
    _patch_clients(
        monkeypatch,
        calls=calls,
        cloudwatch=cloudwatch,
        ec2=FakeEc2(transit_gateway_attachments=attachments),
    )
    out = tmp_path / "obs.json"
    capsys.readouterr()

    assert cli.main(_observe_args(snapshot, out, "--metrics")) == 0

    assert ("cloudwatch", "ap-east-1") in calls
    captured = capsys.readouterr()
    # 🔴 창 길이를 숫자로 박지 않는다 — ADR 0008 D6 이 3시간에서 15분으로 줄인 날 이 줄만 빨개져야
    # 하는데, 실제로는 옛 숫자를 박아 둬서 **화면 문구가 바뀐 것**과 구별되지 않았다(2026-09-24 실측).
    assert f"2층 지표 창 {WINDOW_SECONDS}s/{PERIOD_SECONDS}s" in captured.out, (
        captured.out
    )
    assert "흐름 1" in captured.out
    # 나머지 대상은 계열을 등록하지 않았으니 「확인 불가」다 — 0 이면 네 수가 안 갈린 것이다.
    assert "확인 불가 0" not in captured.out, captured.out
    # 차원을 못 만든 첨부는 **묻지도 못한** 대상이다 — 「조용함」·「확인 불가」에 섞이면 우리 쪽
    # 결함이 「흐름 없음」으로 보인다. 🔴 수가 **2** 인 이유: 그 첨부 하나 때문에 화면에서 회색이
    # 되는 것은 첨부 박스 1개와 **그 박스에서 VPC 로 가는 선 1개**다(ADR 0008 D8 로 선도 2층을
    # 받게 됐다 — 선까지 세지 않으면 「박스만 못 읽었다」로 읽힌다). TGW 로 가는 선은 그 id 로
    # 그리는 선이라 구조에 아예 없다.
    assert "못 읽음 2" in captured.out, captured.out
    assert "⚠ 2층" in captured.err, captured.err
    assert f"{ACCOUNT[:3]}…{ACCOUNT[-3:]}" in captured.err
    assert ACCOUNT not in captured.out + captured.err, "계정ID 가 그대로 찍혔다"
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["layers"] == {"describe": True, "metric": True}
    live = [o for o in document["observations"] if o["status"] == "live"]
    assert [o["target_id"].rsplit("/", 1)[-1] for o in live] == [EGRESS_NAT]


def test_a_blocked_metric_layer_is_not_reported_as_nothing_to_see(
    monkeypatch, tmp_path, capsys
):
    """🔴 실계정이 찾은 제품 결함(2026-09-23) — 2층이 통째로 막혔는데 세 수가 모두 0 이었다.

    실측: `--metrics-profile` 없이(= 1층 프로필로 CloudWatch 를 부르면) 돌렸더니
    `2층 지표 창 10800s/60s — 흐름 0 · 조용함 0 · 확인 불가 0` 이 찍혔고, 같은 실행의 대상 줄은
    `partial 21` 이었다. 막힌 21개가 어느 수에도 안 세진 것이다.

    사람이 겪는 일: 권한이 없어서 **아무것도 못 본** 실행과, 지표를 다 읽었는데 **볼 것이 없던**
    실행의 화면이 같아진다. 전자는 권한을 고쳐야 하고 후자는 고칠 것이 없다 — 화면이 둘을
    구별해 주지 않으면 사람은 「이 계정은 조용하다」로 읽고 넘어간다.

    그리고 2층이 막혀도 1층 판정(`up`)은 그대로 서 있어야 한다(불변식 ④).
    """
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    _patch_clients(
        monkeypatch,
        cloudwatch=FakeCloudWatch(
            {}, error=client_error("AccessDenied", "GetMetricData")
        ),
    )
    out = tmp_path / "obs.json"
    capsys.readouterr()

    assert cli.main(_observe_args(snapshot, out, "--metrics")) == 0

    document = json.loads(out.read_text(encoding="utf-8"))
    blocked = [
        o
        for o in document["observations"]
        if any(r["api"] == cli.METRIC_API for r in o.get("partial_reasons") or ())
    ]
    assert len(blocked) >= 1, (
        "2층을 전부 막았는데 막힌 대상이 없다 = 검사가 못 재고 있다"
    )
    captured = capsys.readouterr()
    assert f"못 읽음 {len(blocked)}" in captured.out, captured.out
    assert "흐름 0 · 조용함 0 · 확인 불가 0" in captured.out, captured.out
    # 1층은 살아 있다 — 2층 실패가 판정을 끌어내리거나 지어내서는 안 된다.
    statuses = {o["status"] for o in document["observations"]}
    assert "up" in statuses and "live" not in statuses, statuses
    # 왜 막혔는지는 종류별로 한 번 더 말한다(사람이 고칠 곳 = 권한).
    assert "/metric = denied (access_denied, cloudwatch:GetMetricData)" in captured.err


def test_metrics_can_read_with_a_second_profile(monkeypatch, tmp_path, capsys):
    """🔴 개발 계정에서 2층을 **재현 가능하게** 확인하려면 층마다 프로필이 달라야 한다.

    실측된 제약(`docs/required-permissions.md` §1-2 대조군 표): `example-readonly` + `ListMetrics` =
    AccessDenied, `example-metrics` + `ec2:DescribeVpcs` = UnauthorizedOperation. 분리는 일부러
    만든 것이므로(「1층은 2층 권한 없이 돈다」의 대조군) 한 프로필로 두 층을 읽을 수는 없다.

    사람이 겪는 일: 명령 하나로 「자원 목록은 example-readonly, 지표는 example-metrics」가 된다. 이 갈림이
    없으면 실계정 확인이 일회성 스크립트로 흘러가고, 다음 사람이 재현할 수 없다.
    """
    sessions: list[tuple[str, str | None]] = []

    def fake_session(*, profile_name=None, region_name=None, **_):
        return _FakeSession(profile_name)

    def fake_client(service, *, session=None, profile_name=None, region_name=None, **_):
        if service == "sts":
            return _FakeSts()
        sessions.append((service, getattr(session, "profile", profile_name)))
        if service == "cloudwatch":
            return FakeCloudWatch({})
        return FakeEc2()

    monkeypatch.setattr(cli, "read_only_session", fake_session)
    monkeypatch.setattr(cli, "read_only_client", fake_client)
    out = tmp_path / "snap.json"
    assert (
        cli.main(
            [
                "collect",
                "--region",
                "ap-east-1",
                "--accounts",
                ACCOUNT,
                "--profile",
                "example-readonly",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    sessions.clear()
    capsys.readouterr()

    rc = cli.main(
        _observe_args(
            out,
            tmp_path / "obs.json",
            "--metrics",
            "--profile",
            "example-readonly",
            "--metrics-profile",
            "example-metrics",
        )
    )

    assert rc == 0
    assert ("cloudwatch", "example-metrics") in sessions, sessions
    # 1층은 그대로 읽기 프로필이어야 한다 — 2층 프로필로 1층을 부르면 실계정에서 전부 막힌다.
    assert {p for s, p in sessions if s != "cloudwatch"} == {"example-readonly"}, (
        sessions
    )


# ---- 흐름 로그 스위치(S5 U9) -------------------------------------------------------------------


def _no_flow_reader(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("--flow-logs 없이 흐름 로그 판독기를 불렀다")

    monkeypatch.setattr(cli, "read_flow_logs", refuse)


def test_flow_logs_are_off_unless_asked(monkeypatch, tmp_path, capsys):
    """🔴 기본 off — 2층을 켜도 흐름 로그는 읽지 않는다. 끈 고객의 산출물·요약은 U9 이전과 같다."""
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    _no_flow_reader(monkeypatch)
    _patch_clients(monkeypatch, cloudwatch=FakeCloudWatch({}))
    out = tmp_path / "obs.json"
    capsys.readouterr()

    assert cli.main(_observe_args(snapshot, out, "--metrics")) == 0

    assert "flow_log" not in json.loads(out.read_text(encoding="utf-8"))["layers"]
    captured = capsys.readouterr()
    assert "흐름 로그" not in captured.out + captured.err


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        (("--flow-logs",), "--metrics"),
        (("--flow-logs", "--metrics", "--no-sources"), "--no-sources"),
    ],
)
def test_flow_logs_refuse_a_run_that_cannot_judge(
    monkeypatch, tmp_path, capsys, extra, needle
):
    """🔴 경고가 아니라 **거부**다(`--metrics-profile` 경고와 반대). 2층 없이는 판정 창이 없고 출처 탐지 없이는 읽을
    로그 목록이 없다 — 진행하면 「켰는데 초록이 하나도 없다」가 되고 사람은 그것을 「기록 없음」으로 읽는다.
    AWS 를 한 번도 부르지 않고 파일도 쓰지 않는다."""
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    calls: list[tuple[str, str | None]] = []
    _no_flow_reader(monkeypatch)
    _patch_clients(monkeypatch, calls=calls, cloudwatch=FakeCloudWatch({}))
    out = tmp_path / "obs.json"
    capsys.readouterr()

    assert cli.main(_observe_args(snapshot, out, *extra)) == 1

    assert needle in capsys.readouterr().err
    assert calls == []
    assert not out.exists()


def test_a_flow_logs_profile_without_the_flag_says_so(monkeypatch, tmp_path, capsys):
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    _no_flow_reader(monkeypatch)
    capsys.readouterr()

    rc = cli.main(
        _observe_args(
            snapshot, tmp_path / "obs.json", "--flow-logs-profile", "example-flow"
        )
    )

    assert rc == 0  # 경고만 — 1층 관측은 그대로 유효하다
    assert "--flow-logs 가 없다" in capsys.readouterr().err
    # 대조군: 프로필 없이 끈 기본 실행에는 경고가 없다
    assert cli.main(_observe_args(snapshot, tmp_path / "plain.json")) == 0
    assert "--flow-logs" not in capsys.readouterr().err


def test_flow_logs_flag_reads_and_splits_the_numbers(monkeypatch, tmp_path, capsys):
    """켜면 판독기를 **흐름 로그 프로필**로 부르고, 요약이 「사용 · 못 읽음 · 중복으로 덮임」을 가른다.

    중복으로 덮인 로그를 못 읽음에 합치면 같은 VPC 에 로그를 두 개 켠 고객에게 「하나를 못 읽었다」고 말한다.
    못 읽은 이유는 코드별 수만 찍는다 — 로그 id·목적지(버킷·로그 그룹 이름)는 요약에 나오지 않는다."""
    from nettopo.flowlogs import FlowLogReading, LogResult

    sessions: list[tuple[str, str | None]] = []

    def fake_client(service, *, session=None, profile_name=None, region_name=None, **_):
        if service == "sts":
            return _FakeSts()
        sessions.append((service, getattr(session, "profile", profile_name)))
        if service == "cloudwatch":
            return FakeCloudWatch({})
        return FakeEc2()

    monkeypatch.setattr(
        cli, "read_only_session", lambda **kw: _FakeSession(kw.get("profile_name"))
    )
    monkeypatch.setattr(cli, "read_only_client", fake_client)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    _scan(
        monkeypatch,
        SourceScan(
            scanned=True,
            found=(
                Source(
                    kind="vpc_flow_log",
                    state="on",
                    unit="vpc",
                    enabled_ids=1,
                    total_ids=1,
                ),
                Source(
                    kind="tgw_flow_log",
                    state="on",
                    unit="transit_gateway",
                    enabled_ids=1,
                    total_ids=1,
                ),
            ),
        ),
    )
    asked: list[Any] = []

    def fake_reader(snap, flow_logs, factory, *, at):
        factory(ACCOUNT, "ap-east-1", "logs")
        asked.append(at)
        vpc = {"level": "vpc", "kind": "vpc_flow_log"}
        return FlowLogReading(
            resources=(
                LogResult(id="fl-00000001", used=True, **vpc),
                LogResult(
                    id="fl-00000002", used=False, why_code="access_denied", **vpc
                ),
                LogResult(
                    id="fl-00000003", used=False, why_code="duplicate_covered", **vpc
                ),
                LogResult(
                    id="fl-00000004",
                    level="transit_gateway",
                    kind="tgw_flow_log",
                    used=False,
                    why_code="fields_missing",
                ),
            ),
            calls=7,
        )

    monkeypatch.setattr(cli, "read_flow_logs", fake_reader)
    sessions.clear()
    out = tmp_path / "obs.json"
    capsys.readouterr()

    rc = cli.main(
        _observe_args(
            snapshot,
            out,
            "--metrics",
            "--flow-logs",
            "--profile",
            "example-readonly",
            "--flow-logs-profile",
            "example-flow",
        )
    )

    assert rc == 0
    assert ("logs", "example-flow") in sessions, sessions
    assert {p for s, p in sessions if s not in ("logs",)} == {"example-readonly"}, (
        sessions
    )
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["layers"]["flow_log"] is True
    assert asked == [
        document["run"]["observed_at"]
    ]  # 창의 끝 = 스냅샷 시각(벽시계 아님)
    captured = capsys.readouterr()
    assert (
        "흐름 로그 4개 — 사용 1 · 못 읽음 2 · 중복으로 덮임 1 · 왕복 근거 0(서브넷 선 0) · 호출 7"
        in captured.out
    ), captured.out
    assert (
        "⚠ 흐름 로그 못 읽은 이유 — access_denied 1 · fields_missing 1" in captured.err
    )
    assert "fl-0000000" not in captured.out + captured.err


def _scan(monkeypatch, scan):
    """탐지 결과를 그대로 주입한다 — 이 축이 재는 것은 탐지가 아니라 **요약 줄**이다.

    `sources.py` 의 탐지 경로는 자기 테스트(`test_sources.py`)가 잰다. 여기서 가짜 EC2 로
    네 상태를 다 만들려면 VPN·EKS·ECS 응답까지 조립해야 하고, 그러면 이 테스트가 깨질 때
    원인이 요약 줄인지 탐지인지 구별할 수 없다.
    """
    monkeypatch.setattr(cli, "detect_sources", lambda *a, **k: scan)


_FOUR_STATES = SourceScan(
    scanned=True,
    found=(
        # 켜짐이지만 이번 버전은 읽지 않는다 → `used=False` + 사유 자동
        Source(kind="vpc_flow_log", state="on", unit="vpc", enabled_ids=3, total_ids=7),
        # 꺼짐 — 분모가 있다(켤 대상이 실제로 있다)
        Source(
            kind="tgw_flow_log",
            state="off",
            unit="transit_gateway",
            enabled_ids=0,
            total_ids=2,
        ),
        # 꺼짐 — scope 자체가 없는 계정 단위 사실
        Source(kind="container_insights", state="off"),
        # 확인 못함 — 사유 필수(D2-1)
        Source(
            kind="eks_network_policy_log",
            state="unknown",
            why="eks:DescribeAddon AccessDenied",
        ),
        # 🔴 분모 0 = 「해당 자원이 없다」. 꺼짐과 같이 세면 안 된다
        Source(
            kind="vpn_tunnel_bgp_log",
            state="off",
            unit="vpn_connection",
            enabled_ids=0,
            total_ids=0,
        ),
    ),
)


def test_source_summary_splits_off_from_unknown_and_from_absent(
    monkeypatch, tmp_path, capsys
):
    """🔴 「안 켰다」·「확인 못했다」·「그 자원이 없다」를 한 수로 합치면 사람이 틀린 조치를 한다.

    사람이 겪는 일: 관측을 돌리면 마지막에 출처 한 줄이 찍힌다. 그 줄이 「꺼짐 3」이라고만
    말하면, 그중 하나는 우리가 권한이 없어서 못 본 것이고 하나는 그 계정에 VPN 연결이 아예
    없어서 켤 수가 없는 것인데도 사람은 셋 다 「고객에게 켜라고 하자」로 읽는다. 실측
    2026-09-25(읽기 프로필): VPN 연결 0개·Resolver 엔드포인트 0개인 계정에서 그 둘이 「꺼져
    있는 근거」로 열거됐다(화면 쪽은 검사 100 이 잡는다).
    """
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    _patch_clients(monkeypatch)
    _scan(monkeypatch, _FOUR_STATES)
    capsys.readouterr()

    rc = cli.main(_observe_args(snapshot, tmp_path / "obs.json"))

    assert rc == 0
    captured = capsys.readouterr()
    line = next(
        (ln for ln in captured.out.splitlines() if ln.startswith("출처 ")),
        None,
    )
    assert line is not None, captured.out
    # 분모 0 인 한 줄은 꺼짐(2)에 들어가지 않고 자기 칸으로 간다.
    assert "출처 5종" in line, line
    assert "켜짐 1(우리가 씀 0)" in line, line
    assert "꺼짐 2" in line, line
    assert "확인 못함 1" in line, line
    assert "해당 자원 없음 1" in line, line
    # 확인 못함은 **사유와 함께** 눈에 띄는 쪽으로 나간다 — 사유가 없으면 무엇을 고칠지 모른다.
    assert "eks_network_policy_log = 확인 못함" in captured.err, captured.err
    assert "eks:DescribeAddon AccessDenied" in captured.err, captured.err


def test_a_silent_source_scan_is_not_read_as_everything_off(
    monkeypatch, tmp_path, capsys
):
    """🔴 출처가 0종인 두 경우는 뜻이 정반대다 — 하나는 정상, 하나는 우리 결함이다.

    사람이 겪는 일: `--no-sources` 로 껐으면 「건너뜀」이라고 말해야 하고, 켜고 돌렸는데도
    0종이면 그것은 「고객이 아무것도 안 켰다」가 아니라 **탐지가 통째로 안 돌았다**는 뜻이다.
    후자를 조용히 넘기면 화면에 알약이 사라지고 사람은 「이 계정은 켤 게 없구나」로 읽는다.
    """
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0

    _patch_clients(monkeypatch)
    _scan(monkeypatch, SourceScan(scanned=True, found=()))
    capsys.readouterr()
    assert cli.main(_observe_args(snapshot, tmp_path / "obs-empty.json")) == 0
    empty = capsys.readouterr()
    assert "출처 0종" in empty.out, empty.out
    assert "탐지가 아무것도 돌려주지 않았다" in empty.err, empty.err

    capsys.readouterr()
    assert (
        cli.main(_observe_args(snapshot, tmp_path / "obs-skip.json", "--no-sources"))
        == 0
    )
    skipped = capsys.readouterr()
    assert "출처 탐지 건너뜀" in skipped.out, skipped.out
    # 건너뛴 것은 결함이 아니다 — 같은 경고를 띄우면 사람이 매번 없는 버그를 찾는다.
    assert "돌려주지 않았다" not in skipped.err, skipped.err


def _fixed_clock(monkeypatch, times: list[str]) -> None:
    """관측 시각을 주입한다 — `collect(clock=…)` 로 넘기는 것이 생산 경로와 같은 자리다.

    🔴 `utc_now()` 는 **초 단위**다. 같은 초에 `observe` 를 두 번 돌리면 이력은 「이전 관측이
    이번보다 앞서지 않는다」로 거부한다(그것이 정답이다 — 간격 0 인 두 문서 사이에는 구간이 없고,
    그대로 차분하면 변경점의 시각이 이전 관측보다 앞서서 목록의 순서가 거짓이 된다). 그래서
    CLI 축에서 이력을 재려면 시각을 주입해야 한다.
    """
    real = cli.collect

    def fake(scope, client_factory, **kwargs):
        kwargs["clock"] = lambda: times.pop(0)
        return real(scope, client_factory, **kwargs)

    monkeypatch.setattr(cli, "collect", fake)


def test_observe_accumulates_history_from_the_previous_document(
    monkeypatch, tmp_path, capsys
):
    """🔴 `--previous` 와 `--out` 에 **같은 경로**를 주는 것이 주기 배치의 모양이다.

    사람이 겪는 일: 배치는 매번 똑같은 명령으로 돈다. 첫 실행에는 직전 관측 파일이 아예 없으니
    「여기서부터 기록이 시작된다」가 뜨고 실행은 성공해야 한다(여기서 죽으면 상태 화면이 영원히
    비어 있다). 두 번째 실행부터는 같은 파일을 읽어 비교하고 **같은 파일에 덮어쓴다** — 읽기가
    쓰기보다 먼저 일어나지 않으면 자기 자신을 기준으로 삼아 매 실행 「변경 0건」이 된다.

    그리고 목록의 수를 갈라 말하는지까지 본다(새로 본 것 · 승계 · 창 밖 · 접힘 · 제외 4종).
    """
    _patch_clients(monkeypatch)
    _fixed_clock(
        monkeypatch,
        [
            "2026-09-01T00:00:00Z",  # collect(구조)
            "2026-09-01T00:05:00Z",  # 1회차 관측
            "2026-09-01T00:10:00Z",  # 2회차 관측
        ],
    )
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    out = tmp_path / "obs.json"
    capsys.readouterr()

    assert cli.main(_observe_args(snapshot, out, "--previous", str(out))) == 0

    first = json.loads(out.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert first["history"]["baseline_at"] == "2026-09-01T00:05:00Z"
    assert first["changes"] == []
    assert "이력 기준선 = 이번 관측" in captured.out, captured.out
    # 파일이 없는 것은 오류가 아니다 — 경고로만 말하고 rc 0 이다.
    assert "⚠ 이력 직전 관측 파일이 없다" in captured.err, captured.err

    # 생성에 실패해 있던 NAT 하나가 목록에서 빠졌다(경로가 걸려 있지 않아 선은 안 딸려 온다).
    _patch_clients(monkeypatch, ec2=FakeEc2(nat_gateways=NAT_GATEWAYS[:1]))
    capsys.readouterr()

    assert cli.main(_observe_args(snapshot, out, "--previous", str(out))) == 0

    second = json.loads(out.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    # 🔴 이 줄이 「읽기가 쓰기보다 먼저」의 증거다 — 순서가 뒤바뀌면 기준이 자기 자신이 되고
    # 목록이 0건이 된다(그때 화면은 「변경 없음」이라고 거짓말을 한다).
    assert [row["kind"] for row in second["changes"]] == ["resource_removed"]
    assert second["changes"][0]["at"] == "2026-09-01T00:10:00Z"
    assert second["history"]["baseline_at"] == "2026-09-01T00:05:00Z"
    assert second["history"]["resolution_seconds"] == 300
    assert second["history"]["truncated"] is False
    assert (
        "이력 새 변경 1 · 목록 1(승계 0 · 창 밖 0 · 상한 0 · 접힘 0)" in captured.out
    ), captured.out
    # 🔴 제외는 **0 도 찍는다** — 안 찍으면 「못 읽어서 조용한 화면」과 구별되지 않는다.
    assert (
        "이력 제외 0 — 못 읽은 칸 0 · 2층 차이 0 · 범위 밖 0 · 칸 미상 0 · 값 없는 연결 0 · 삭제 중 0"
        in captured.out
    ), captured.out
    assert ACCOUNT not in captured.out + captured.err, "계정ID 가 그대로 찍혔다"
    # 구조는 그대로인데 관측이 삭제를 봤다(흔한 경우) → 모습은 `--topology` 에서 나온다.
    removed = second["changes"][0]
    assert removed["was"]["nodes"][0]["id"] == removed["target_id"], removed
    assert "이력 없어진 것 1 — 모습 적음 1 · 모습 없음 0" in captured.out, captured.out


def test_observe_takes_an_optional_previous_structure(monkeypatch, tmp_path, capsys):
    """`--previous-topology` 는 선택 입력이다 — 없으면 조용히, 깨졌으면 경고하고 **관측은 계속한다**.

    사람이 겪는 일: 스택 A 의 첫 구조 수집 뒤에는 직전 구조가 없다. 그때 5분 관측이 멈추면 실시간
    화면이 영원히 비어 있다. 깨진 파일은 경고로 말한다 — 없어진 줄이 모습 없이 적히는 이유가 된다.
    """
    _patch_clients(monkeypatch)
    _fixed_clock(
        monkeypatch,
        ["2026-09-01T00:00:00Z", "2026-09-01T00:05:00Z", "2026-09-01T00:10:00Z"],
    )
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    capsys.readouterr()

    missing = tmp_path / "no-such-structure.json"
    out = tmp_path / "obs.json"
    assert (
        cli.main(_observe_args(snapshot, out, "--previous-topology", str(missing))) == 0
    )
    assert "직전 구조" not in capsys.readouterr().err

    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert (
        cli.main(_observe_args(snapshot, out, "--previous-topology", str(broken))) == 0
    )
    assert "⚠ 이력 직전 구조가 JSON 이 아니다" in capsys.readouterr().err


def test_observe_takes_several_previous_structures_and_uses_the_paired_one(
    monkeypatch, tmp_path, capsys
):
    """🆕 `--previous-topology` 는 여러 번 줄 수 있다 — 짝이 맞는 장이 **둘째**여도 모습이 붙는다(QA B 2차 N-05).

    사람이 겪는 일: 실시간을 몇 시간 끈 사이 구조가 두 번 넘게 바뀌면 직전 관측의 구조는 사본
    (`structure-previous.json`)에도 없다. 상태 함수가 누적 스냅샷에서 그 구조를 한 장 더 넘기는데, 엔진이 첫 장만
    보면 지운 리소스의 이력 줄이 이름 없이 id 로 남는다(6단계 실측: EIC·EC2 21개).
    """
    _patch_clients(monkeypatch)
    _fixed_clock(
        monkeypatch,
        [
            "2026-09-01T00:00:00Z",
            "2026-09-01T00:05:00Z",
            "2026-09-01T00:10:00Z",
            "2026-09-01T00:15:00Z",
        ],
    )
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    out = tmp_path / "obs.json"
    assert cli.main(_observe_args(snapshot, out)) == 0
    previous = tmp_path / "prev.json"
    previous.write_bytes(out.read_bytes())

    # 지금 구조 = 그 NAT 가 빠진 **다른 내용**(지문·시각이 다르다) · 미끼 = 또 다른 구조 · 짝 = 처음 구조
    paired = json.loads(snapshot.read_text(encoding="utf-8"))
    gone = sorted(n["id"] for n in paired["nodes"] if n["type"] == "nat_gateway")[-1]

    def variant(name: str, digest: str, at: str):
        doc = json.loads(snapshot.read_text(encoding="utf-8"))
        doc["nodes"] = [n for n in doc["nodes"] if n["id"] != gone]
        doc["edges"] = [
            e for e in doc["edges"] if gone not in (e["source"], e["target"])
        ]
        doc["run"]["content_digest"] = digest
        doc["run"]["collected_at"] = at
        path = tmp_path / name
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    now = variant("now.json", "sha256:" + "a" * 64, "2026-09-01T00:07:00Z")
    decoy = variant("decoy.json", "sha256:" + "b" * 64, "2026-09-01T00:06:00Z")

    def removed_rows(*extra: str) -> list[dict]:
        capsys.readouterr()
        args = [
            "observe",
            "--topology",
            str(now),
            "--out",
            str(out),
            "--previous",
            str(previous),
            *extra,
        ]
        assert cli.main(args) == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        return [
            c
            for c in doc["changes"]
            if c["kind"] == "resource_removed" and c["target_id"] == gone
        ]

    rows = removed_rows(
        "--previous-topology", str(decoy), "--previous-topology", str(snapshot)
    )
    assert len(rows) == 1 and rows[0]["was"]["nodes"][0]["id"] == gone, rows
    # 대조군 — 미끼만 주면 짝이 없다 → 모습 없이 적고 그렇다고 말한다
    rows = removed_rows("--previous-topology", str(decoy))
    assert len(rows) == 1 and "was" not in rows[0], rows
    assert "직전 관측이 그려진 구조 문서를 못 찾았다" in capsys.readouterr().err


def test_account_id_is_masked_in_failure_output(monkeypatch, tmp_path, capsys):
    ec2 = FakeEc2(
        errors={"describe_vpcs": client_error("AccessDenied", "DescribeVpcs")}
    )
    _patch_clients(monkeypatch, ec2=ec2)

    rc, _ = _run(tmp_path)

    assert rc == 0  # 부분 실패는 정상 경로다(불변식 ④)
    captured = capsys.readouterr()
    assert ACCOUNT not in captured.out + captured.err, "계정ID 가 그대로 찍혔다"
    assert f"{ACCOUNT[:3]}…{ACCOUNT[-3:]}" in captured.err
    assert "access_denied" in captured.err


def test_only_observe_turns_the_state_probe_on(monkeypatch, tmp_path, capsys):
    """🔴 상태 탐침은 **관측에서만** 돈다(ADR 0006 §O7 W3 규칙 1).

    구조 수집이 켜면 RDS 가 백업에 들어갈 때마다 구조 해시가 바뀌어 「구조가 바뀌었다」가 거짓으로 뜨고,
    관측이 끄면 RDS·ECS·SageMaker·EKS·방화벽 카드가 전부 「규칙에 없는 값(개발)」이 된다.
    """
    _patch_clients(monkeypatch)
    seen: list[bool] = []
    real = cli.collect

    def spy(scope, client_factory, **kwargs):
        seen.append(bool(kwargs.get("with_state")))
        return real(scope, client_factory, **kwargs)

    monkeypatch.setattr(cli, "collect", spy)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    assert cli.main(_observe_args(snapshot, tmp_path / "obs.json")) == 0
    capsys.readouterr()
    assert seen == [False, True], seen


def test_source_warning_masks_account_ids(monkeypatch, tmp_path, capsys):
    """출처 탐지가 거부되면 사유에 AWS 오류 원문이 들어간다 — 거기엔 호출자 ARN(계정 ID)이 있다(2026-10-28 점검).

    2층 경고와 같은 규칙으로 가린다. 안 가리면 고객 CodeBuild 로그에 계정 ID 가 그대로 남는다.
    """
    _patch_clients(monkeypatch)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    _patch_clients(monkeypatch)
    denied = SourceScan(
        scanned=True,
        found=(
            Source(
                kind="eks_network_policy_log",
                state="unknown",
                why=f"eks:DescribeAddon: User: arn:aws:sts::{ACCOUNT}:assumed-role/x is not authorized",
            ),
        ),
    )
    _scan(monkeypatch, denied)
    capsys.readouterr()
    assert cli.main(_observe_args(snapshot, tmp_path / "obs.json")) == 0
    captured = capsys.readouterr()
    assert "⚠ 출처 eks_network_policy_log = 확인 못함" in captured.err + captured.out
    assert ACCOUNT not in captured.out + captured.err, (
        "출처 경고에 계정 ID 가 그대로 찍혔다"
    )


# ---------------------------------------------------------------- 계정 이름(사용자 결정 2026-10-28 · `account_name.py`)


def test_structure_collect_reads_account_names_and_observe_does_not(
    monkeypatch, tmp_path, capsys
):
    """구조 수집은 계정마다 `account:GetAccountInformation`(us-east-1) 을 부르고, 5분 관측은 부르지 않는다.

    🔴 이름 자체는 로그에 찍지 않는다 — 이메일 주소를 이름으로 쓴 계정이 있다(실측 2026-10-28). 개수만 찍는다.
    """
    calls: list[tuple[str, str]] = []
    _patch_clients(monkeypatch, calls=calls)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    document = json.loads(snapshot.read_text(encoding="utf-8"))
    account = next(n for n in document["nodes"] if n["type"] == "account")
    assert account["name"] == ACCOUNT_NAME
    assert ("account", "us-east-1") in calls
    captured = capsys.readouterr()
    assert "계정 이름 1/1개 읽음" in captured.out
    assert ACCOUNT_NAME not in captured.out + captured.err
    calls.clear()
    assert cli.main(_observe_args(snapshot, tmp_path / "obs.json")) == 0
    assert calls and all(service != "account" for service, _ in calls), calls


def test_a_denied_account_name_is_a_masked_warning_not_a_failure(
    monkeypatch, tmp_path, capsys
):
    """거부 = 실행 성공 · 경고 한 줄(가린 id · 사유 코드 · API) · 네트워크 칸은 전부 그대로 「읽음」."""
    ec2 = FakeEc2(
        errors={
            "get_account_information": client_error(
                "AccessDeniedException",
                "GetAccountInformation",
                f"User: arn:aws:sts::{ACCOUNT}:assumed-role/Reader/s is not authorized",
            )
        }
    )
    _patch_clients(monkeypatch, ec2=ec2)
    rc, snapshot = _run(tmp_path)
    assert rc == 0
    captured = capsys.readouterr()
    assert ACCOUNT not in captured.out + captured.err, "계정ID 가 그대로 찍혔다"
    assert (
        f"⚠ 계정 이름 {ACCOUNT[:3]}…{ACCOUNT[-3:]} = access_denied"
        " (account:GetAccountInformation)" in captured.err
    )
    assert "계정 이름 0/1개 읽음" in captured.out
    document = json.loads(snapshot.read_text(encoding="utf-8"))
    assert all(c["status"] == "ok" for c in document["coverage"])
    account = next(n for n in document["nodes"] if n["type"] == "account")
    assert account["name"] is None and account["partial"] is False
