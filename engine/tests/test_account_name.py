"""계정 이름 — 계정 상자에 「이름 (가린 id)」(사용자 결정 2026-10-28 · `account_name.py`).

화면에서 보이는 일: 계정 상자 제목이 숫자 12자리 대신 사람이 붙인 이름이 된다. 이름을 못 읽은 계정은 지금처럼 id 로
남고, **그 계정의 VPC·서브넷은 「확인하지 못했다」로 바뀌지 않는다** — 이름 권한 하나 없는 것으로 멀쩡히 읽은
네트워크를 의심하게 만들면 오판이다(불변식 ④의 반대쪽 실패). AWS 를 타지 않는다(가짜 `FakeAws.get_account_information`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from botocore.config import Config
from botocore.exceptions import EndpointConnectionError
from fakes import ACCOUNT, ACCOUNT_NAME, REGION, FakeAws, client_error, factory
from nettopo import account_name, awsguard, cli
from nettopo.collect import collect
from nettopo.model import Scope, node_id
from nettopo.serialize import to_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "topology.schema.json").read_text(encoding="utf-8")
)
MEMBER = "222233334444"
LOCKED = "333344445555"
SCOPE = Scope(
    account_ids=(ACCOUNT,), regions=(REGION,), resource_types=("vpc", "subnet")
)
DENIED_TEXT = f"User: arn:aws:sts::{ACCOUNT}:assumed-role/Reader/s is not authorized"


def _snap(client: FakeAws, *, names: bool = True, scope: Scope = SCOPE) -> Any:
    return collect(
        scope,
        factory(client),
        account_names=names,
        clock=lambda: "2026-10-28T00:00:00Z",
    )


def _account(doc: dict[str, Any], account_id: str = ACCOUNT) -> dict[str, Any]:
    return next(n for n in doc["nodes"] if n["id"] == node_id("account", account_id))


def _valid(doc: dict[str, Any]) -> None:
    assert not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc))


def _without_account(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in doc["nodes"] if n["type"] != "account"]


def test_the_account_box_gets_its_own_name():
    client = FakeAws()
    doc = _snap(client).to_document()
    _valid(doc)
    node = _account(doc)
    assert node["name"] == ACCOUNT_NAME
    assert node["attributes"] == {} and node["partial"] is False
    assert client.account_calls == 1, "계정마다 한 번이다"
    # 이름 말고는 아무것도 바뀌지 않는다
    plain = _snap(FakeAws(), names=False).to_document()
    assert _without_account(doc) == _without_account(plain)
    assert doc["coverage"] == plain["coverage"]


def test_names_are_off_unless_asked():
    """🔴 기본은 끔 — 5분 관측(`with_state`)이 계정마다 이 API 를 부르지 않는다(`cli` 가 구조 수집에서만 켠다)."""
    client = FakeAws()
    node = _account(_snap(client, names=False).to_document())
    assert client.account_calls == 0
    assert node["name"] is None and node["attributes"] == {}


@pytest.mark.parametrize(
    ("code", "expected"),
    [("AccessDeniedException", "access_denied"), ("ThrottlingException", "throttled")],
)
def test_an_unread_name_leaves_the_network_data_alone(code, expected):
    """거부·throttle = 실행은 성공 · 이름 null · 못 읽음은 계정 노드 속성에 · `partial`·`coverage` 는 그대로."""
    client = FakeAws(
        errors={
            "get_account_information": client_error(
                code, "GetAccountInformation", DENIED_TEXT
            )
        }
    )
    snapshot = _snap(client)
    doc = snapshot.to_document()
    _valid(doc)
    node = _account(doc)
    assert node["name"] is None
    assert node["attributes"] == {
        "name_unread": expected,
        "name_unread_api": "account:GetAccountInformation",
    }
    assert node["partial"] is False and "partial_reasons" not in node
    plain = _snap(FakeAws(), names=False).to_document()
    assert doc["coverage"] == plain["coverage"], (
        "이름 실패가 네트워크 칸을 「확인 못함」으로 만들었다"
    )
    assert all(c["status"] == "ok" for c in doc["coverage"])
    assert _without_account(doc) == _without_account(plain)
    # 원문(호출자 ARN)은 싣지 않는다
    assert "not authorized" not in to_json(snapshot)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_an_empty_name_is_no_name_not_a_failure(raw):
    node = _account(_snap(FakeAws(account_name=raw)).to_document())
    assert node["name"] is None
    assert node["attributes"] == {}, (
        "이름이 빈 것은 실패가 아니다 — 못 읽음 표시를 달면 사람이 권한을 고치러 간다"
    )


def test_names_are_deterministic():
    assert to_json(_snap(FakeAws())) == to_json(_snap(FakeAws()))


def test_the_guard_lets_the_name_call_through():
    """불변식 ① — `GetAccountInformation` 은 `Get` 으로 시작하는 읽기다. 가드가 막으면 `ReadOnlyViolation` 이 실행을 죽인다.

    죽은 로컬 엔드포인트(127.0.0.1:1)라 요청은 나가지 않는다 — 가드를 지나 연결 단계까지 갔는지만 잰다.
    """
    client = awsguard.read_only_client(
        account_name.SERVICE,
        region_name=account_name.API_REGION,
        endpoint_url="http://127.0.0.1:1",
        config=Config(retries={"max_attempts": 1}, connect_timeout=1, read_timeout=1),
    )
    with pytest.raises(EndpointConnectionError):
        client.get_account_information()


# ---------------------------------------------------------------- 크로스계정 — 계정마다 그 계정의 세션


class _Session:
    def __init__(self, account_id: str) -> None:
        self.account_id = account_id


def test_each_account_is_asked_with_its_own_session(monkeypatch):
    """허브 = 자기 읽기 세션 · 멤버 = assume 한 멤버 역할로 **자기 자신을** 묻는다. 이름 때문에 assume 이 늘지 않는다.

    역할에 들어가지 못한 계정은 이름 사유가 `sts:AssumeRole` 이다 — `account:` 로 적으면 사람이 이름 권한을 고치러 간다.
    """
    names = {ACCOUNT: ACCOUNT_NAME, MEMBER: "member-app"}
    worlds = {a: FakeAws(account_name=n) for a, n in names.items()}
    assumed: list[str] = []
    made: list[tuple[str, str, str]] = []  # (service, region, 세션의 계정)

    def assume_role_session(*, role_arn, **_):
        account = role_arn.split(":")[4]
        if account == LOCKED:
            raise client_error("AccessDenied", "AssumeRole", DENIED_TEXT)
        assumed.append(account)
        return _Session(account)

    def read_only_client(service, *, session=None, region_name=None, **_):
        made.append((service, region_name, session.account_id))
        return worlds[session.account_id]

    monkeypatch.setattr(cli, "read_only_session", lambda **_: _Session(ACCOUNT))
    monkeypatch.setattr(cli, "assume_role_session", assume_role_session)
    monkeypatch.setattr(cli, "read_only_client", read_only_client)
    cache = cli._SessionCache(
        profile=None, role_name="NetworkReadOnlyRole", caller_account=ACCOUNT
    )
    scope = Scope(
        account_ids=(ACCOUNT, MEMBER, LOCKED),
        regions=(REGION,),
        resource_types=("vpc",),
    )

    doc = collect(scope, cache.client, account_names=True).to_document()

    _valid(doc)
    assert _account(doc, ACCOUNT)["name"] == ACCOUNT_NAME
    assert _account(doc, MEMBER)["name"] == "member-app"
    assert assumed == [MEMBER], "이름을 읽느라 다시 assume 했다"
    asked = sorted(m for m in made if m[0] == account_name.SERVICE)
    assert asked == [
        ("account", "us-east-1", ACCOUNT),
        ("account", "us-east-1", MEMBER),
    ]
    assert worlds[MEMBER].account_calls == 1 and worlds[ACCOUNT].account_calls == 1
    locked = _account(doc, LOCKED)
    assert locked["name"] is None and locked["partial"] is False
    assert locked["attributes"] == {
        "name_unread": "access_denied",
        "name_unread_api": "sts:AssumeRole",
    }
