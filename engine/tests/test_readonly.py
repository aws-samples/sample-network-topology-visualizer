"""불변식 ① — 읽기 전용. 런타임 축과 정적(AST) 축을 둘 다 잰다.

네트워크를 타지 않는다:
- 금지 verb 는 가드가 네트워크 **전에** 끊으므로 애초에 나가지 않는다.
- 허용 verb 는 죽은 엔드포인트(`127.0.0.1:1`)로 보내 즉시 연결 거부를 받는다. 「가드가
  막지 않았다」를 증명하려면 호출이 네트워크까지 도달했다는 증거가 필요하다 —
  `EndpointConnectionError` 가 그 증거다. `botocore.stub.Stubber` 는 자기도
  `before-call` 에 핸들러를 걸어 우리 훅과 순서 경쟁을 만들 수 있어 쓰지 않는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from nettopo import awsguard
from nettopo.awsguard import ReadOnlyViolation

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "nettopo"

DEAD_ENDPOINT = "http://127.0.0.1:1"
NO_RETRY = Config(
    retries={"max_attempts": 1, "mode": "standard"},
    connect_timeout=1,
    read_timeout=1,
)

# 이 문자열들이 소스에 나타나면(awsguard.py 밖) 가드를 거치지 않은 클라이언트가 생긴다.
FORBIDDEN_CALLS = frozenset(
    {
        "boto3.client",
        "boto3.resource",
        "boto3.Session",
        "boto3.session.Session",
        "botocore.client.BaseClient",
        "BaseClient",
    }
)


def guarded_ec2():
    return awsguard.read_only_client(
        "ec2", region_name="us-east-1", endpoint_url=DEAD_ENDPOINT, config=NO_RETRY
    )


# --- 런타임 축 -------------------------------------------------------------


def test_write_call_is_blocked():
    with pytest.raises(ReadOnlyViolation) as exc:
        guarded_ec2().create_vpc(CidrBlock="10.0.0.0/16")
    assert "CreateVpc" in str(exc.value)


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.delete_vpc(VpcId="vpc-0aaa1111bbbb2222"),
        lambda c: c.create_tags(Resources=["vpc-0aaa1111bbbb2222"], Tags=[]),
        lambda c: c.modify_vpc_attribute(VpcId="vpc-0aaa1111bbbb2222"),
        lambda c: c.create_flow_logs(ResourceIds=[], ResourceType="VPC"),
    ],
)
def test_other_write_shapes_are_blocked(call):
    """태깅·플로우로그 생성처럼 「편의를 위한 write」도 예외가 아니다."""
    with pytest.raises(ReadOnlyViolation):
        call(guarded_ec2())


def test_read_call_reaches_the_network():
    with pytest.raises(EndpointConnectionError):
        guarded_ec2().describe_vpcs()


def test_presigned_write_is_blocked():
    """presign 은 요청을 보내지 않으므로 `before-call` 을 타지 않는다.

    이 테스트가 `before-parameter-build` 훅의 존재 이유다 — 「탄다고 믿었다」로 두지 않는다.
    """
    with pytest.raises(ReadOnlyViolation):
        guarded_ec2().generate_presigned_url(
            "create_vpc", Params={"CidrBlock": "10.0.0.0/16"}
        )


def test_presigned_read_still_works():
    """위 테스트의 대조군 — presign 경로 자체가 죽은 게 아니라 write 만 막힌 것이다."""
    url = guarded_ec2().generate_presigned_url("describe_vpcs")
    assert url.startswith(DEAD_ENDPOINT)


def _s3_with_answers(*answers: tuple[int, dict[str, str], bytes]):
    """가드 세션의 실제 botocore S3 클라이언트 — 보내기 직전에 가로채 정해 둔 응답을 돌려준다(네트워크 0)."""
    from botocore.awsrequest import AWSResponse

    session = awsguard.session_from_credentials(
        {"AccessKeyId": "AKIAFAKE", "SecretAccessKey": "x", "SessionToken": "t"},
        region_name="ap-northeast-2",
    )
    client = session.client("s3", region_name="ap-northeast-2")
    sent: list[str] = []

    def before_send(request, **_):
        status, headers, body = answers[min(len(sent), len(answers) - 1)]
        sent.append(request.url.split("//", 1)[1].split("/", 1)[0])
        response = AWSResponse(request.url, status, headers, None)
        response._content = body
        return response

    client.meta.events.register("before-send.s3", before_send)
    return client, sent


_REDIRECT = b"<Error><Code>PermanentRedirect</Code><Message>m</Message></Error>"
_LOCATION = b'<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/">us-west-2</LocationConstraint>'


def test_s3_redirect_never_calls_head_bucket():
    """#36 리뷰 — 리전 단서 없는 301 에서 botocore 리다이렉터는 스스로 `HeadBucket` 을 부른다. 가드가 막는 건
    맞지만 `ReadOnlyViolation` 이 판독기 밖으로 나가 실행 전체가 죽는다(불변식 ④). 원래 오류로 올라와야 한다."""
    client, sent = _s3_with_answers((301, {}, _REDIRECT))
    with pytest.raises(ClientError) as caught:
        client.get_bucket_location(Bucket="bkt-flow-a")
    assert caught.value.response["Error"]["Code"] == "PermanentRedirect"
    assert len(sent) == 1


def test_s3_redirect_with_a_region_hint_still_follows():
    """위의 대조군 — 헤더로 리전을 알려 주면 리다이렉트(읽기 경로)는 그대로 돈다."""
    client, sent = _s3_with_answers(
        (301, {"x-amz-bucket-region": "us-west-2"}, _REDIRECT), (200, {}, _LOCATION)
    )
    client.get_bucket_location(Bucket="bkt-flow-a")
    assert len(sent) == 2 and "us-west-2" in sent[1]


def test_verb_allowlist_is_prefix_only():
    assert awsguard.is_read_only("DescribeVpcs")
    assert awsguard.is_read_only("GetCallerIdentity")
    assert awsguard.is_read_only("ListAccounts")
    assert awsguard.is_read_only("SearchTransitGatewayRoutes")
    assert not awsguard.is_read_only("CreateVpc")
    assert not awsguard.is_read_only("ModifyVpcAttribute")
    assert not awsguard.is_read_only("AssumeRole")
    # 서비스별 예외 목록을 두지 않는다 — 그 목록이 구멍이 되는 지점이다.
    assert not hasattr(awsguard, "SERVICE_EXCEPTIONS")


# --- sts:AssumeRole 예외가 좁은가 ------------------------------------------


def assume_only():
    return awsguard.assume_only_client(
        region_name="us-east-1", endpoint_url=DEAD_ENDPOINT, config=NO_RETRY
    )


def test_assume_only_client_rejects_everything_else():
    """읽기 API 조차 이 클라이언트에서는 막혀야 한다 — 예외의 범위가 좁다는 증거."""
    with pytest.raises(ReadOnlyViolation):
        assume_only().get_caller_identity()


def test_assume_only_client_allows_assume_role():
    with pytest.raises(EndpointConnectionError):
        assume_only().assume_role(
            RoleArn="arn:aws:iam::111122223333:role/NetworkReadOnlyRole",
            RoleSessionName="nettopo",
        )


def test_assume_role_session_sends_external_id_only_when_given(monkeypatch):
    """`ExternalId` 는 멤버 역할 신뢰 정책에 조건이 있을 때만 보낸다 — 비우면 키 자체를 넣지 않는다."""
    calls = []

    class FakeSts:
        def assume_role(self, **kwargs):
            calls.append(kwargs)
            return {"Credentials": {}}

    monkeypatch.setattr(awsguard, "assume_only_client", lambda **_: FakeSts())
    monkeypatch.setattr(awsguard, "session_from_credentials", lambda creds, **_: creds)
    role = "arn:aws:iam::111122223333:role/NetworkReadOnlyRole"
    awsguard.assume_role_session(
        role_arn=role, session_name="nettopo", region_name="us-east-1"
    )
    awsguard.assume_role_session(
        role_arn=role,
        session_name="nettopo",
        region_name="us-east-1",
        external_id="ext-123",
    )
    assert "ExternalId" not in calls[0]
    assert calls[1]["ExternalId"] == "ext-123"


def test_assumed_session_inherits_the_read_only_guard():
    """대상 계정 세션이 가드를 잃으면 크로스계정 경로 전체가 무방비가 된다."""
    session = awsguard.session_from_credentials(
        {
            "AccessKeyId": "dummy",
            "SecretAccessKey": "dummy",  # pragma: allowlist secret
            "SessionToken": "dummy",
        },
        region_name="us-east-1",
    )
    client = awsguard.read_only_client(
        "ec2",
        session=session,
        region_name="us-east-1",
        endpoint_url=DEAD_ENDPOINT,
        config=NO_RETRY,
    )
    with pytest.raises(ReadOnlyViolation):
        client.create_vpc(CidrBlock="10.0.0.0/16")


# --- 자격증명 해석은 가드 대상이 아니다 ------------------------------------


def chained_profile(tmp_path, monkeypatch) -> str:
    """자격증명 해석이 `sts:AssumeRole` 을 부르는 프로필(역할 체이닝)을 만든다.

    SSO 토큰 갱신(`sso-oidc:CreateToken`)과 같은 경로다 — 둘 다 「인증」이고, 둘 다
    읽기 verb 가 아니다. SSO 는 오프라인에서 재현할 수 없으니 같은 성질의 API 로 잰다.
    """
    config = tmp_path / "config"
    config.write_text(
        "[profile chained]\n"
        "role_arn = arn:aws:iam::111122223333:role/Whatever\n"
        "source_profile = base\n"
        "\n"
        "[profile base]\n"
        "aws_access_key_id = dummy\n"
        "aws_secret_access_key = dummy\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_ENDPOINT_URL_STS", DEAD_ENDPOINT)
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return "chained"


def test_credential_resolution_is_not_blocked_by_the_guard(tmp_path, monkeypatch):
    """🔴 실측 2026-09-14: IdC 프로필의 토큰 갱신(`sso-oidc:CreateToken`)이 가드에 막혀
    수집이 죽었다. 가드는 **대상 계정의 리소스 API** 를 지키는 것이고 자기 자격증명을
    얻는 경로가 아니다. 여기서 나와야 하는 예외는 `ReadOnlyViolation` 이 아니라
    「네트워크까지 갔다」는 증거다.
    """
    profile = chained_profile(tmp_path, monkeypatch)
    session = awsguard.read_only_session(profile_name=profile, region_name="us-east-1")
    with pytest.raises(EndpointConnectionError):
        session.get_credentials().get_frozen_credentials()


def test_the_guard_still_blocks_writes_on_that_session(tmp_path, monkeypatch):
    """위 완화가 리소스 API 축을 열어 준 게 아님을 보이는 대조군."""
    profile = chained_profile(tmp_path, monkeypatch)
    session = awsguard.read_only_session(profile_name=profile, region_name="us-east-1")
    client = session.client(
        "ec2", endpoint_url=DEAD_ENDPOINT, config=NO_RETRY, region_name="us-east-1"
    )
    with pytest.raises(ReadOnlyViolation):
        client.create_vpc(CidrBlock="10.0.0.0/16")


# --- 정적(AST) 축 ----------------------------------------------------------


def _dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def direct_client_calls(source: str) -> list[str]:
    calls = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name in FORBIDDEN_CALLS:
                calls.append(f"{node.lineno}:{name}")
    return calls


def test_scanner_has_discriminating_power():
    """스캐너가 아무것도 못 읽고 0건으로 통과하는 함정을 막는 대조군."""
    planted = "import boto3\nsession = boto3.Session()\nc = boto3.client('ec2')\n"
    assert len(direct_client_calls(planted)) == 2
    assert direct_client_calls("from nettopo.awsguard import read_only_client\n") == []


def test_no_direct_client_creation_outside_awsguard():
    sources = sorted(p for p in SRC_DIR.rglob("*.py") if p.name != "awsguard.py")
    # 파일을 하나도 못 읽었으면 이 테스트는 통과가 아니라 미측정이다.
    assert len(sources) >= 25, sources
    # 🔴 `glob` 은 비재귀라 `services/` 폴더 전체가 스캔 **밖**이었다 — 수집 종류 하나가
    #    파일 하나인 구조(「이음매 2」)에서 이 구멍은 곧 「불변식 ①이 수집기에 대해 재지지
    #    않는다」는 뜻이다(실측 2026-09-28: `services/` 에 `boto3.client` 를 심어도 탐지 0).
    #    개수 하한만으로는 못 잡는다(최상위 파일이 늘어도 통과한다) → 하위 폴더가 범위에
    #    들어왔는지를 **따로** 잰다.
    assert [p for p in sources if p.parent.name == "services"], sources
    # 🔴 키는 **상대 경로**다. `path.name` 이면 `services/__init__.py` 가 `nettopo/__init__.py` 의
    #    결과를 덮어써 최상위 `__init__.py` 에 심은 클라이언트가 탐지 0 이었다(리뷰 2026-10-01).
    offenders = {
        str(path.relative_to(SRC_DIR)): direct_client_calls(
            path.read_text(encoding="utf-8")
        )
        for path in sources
    }
    assert len(offenders) == len(sources), (
        "같은 키로 겹친 파일이 있다 — 일부가 검사 밖이다"
    )
    assert {k: v for k, v in offenders.items() if v} == {}


def test_awsguard_is_the_only_place_that_touches_boto3_directly():
    guard = (SRC_DIR / "awsguard.py").read_text(encoding="utf-8")
    assert direct_client_calls(guard), (
        "가드가 세션을 만들지 않으면 이 파일이 가드가 아니다"
    )
