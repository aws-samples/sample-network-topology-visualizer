"""불변식 ① — 읽기 전용 엔진의 런타임 강제 지점.

이 모듈이 **유일한 클라이언트 생성 지점**이다. 다른 모듈에서 `boto3.client` /
`boto3.Session` / `BaseClient` 를 직접 부르면 가드를 거치지 않은 클라이언트가 생기므로
`tests/test_readonly.py` 의 AST 축이 그것을 금지한다.

설계 근거 세 가지:

1. **클라이언트가 아니라 세션에 훅을 건다.** 클라이언트마다 걸면 페이지네이터·재생성
   경로에서 빠진다. 🔴 `boto3.Session` 에는 `.events` 가 없다 — botocore 세션에만 있다.
2. **`before-call` 하나로는 부족하다.** presign 경로(`generate_presigned_url`)는
   요청을 보내지 않으므로 `before-call` 을 타지 않고 `before-parameter-build` 만 탄다.
   그래서 두 이벤트에 같은 훅을 건다. 「탄다고 믿었다」로 두지 않기 위해
   `tests/test_readonly.py::test_presigned_write_is_blocked` 가 이 사실을 잰다.
3. **allowlist 는 verb prefix 뿐이고 서비스별 예외 목록을 두지 않는다.** 예외 목록은
   구멍이 되는 지점이다. `sts:AssumeRole` 은 `assume_only_client()` 라는 별도 세션에서만
   허용하고, 그렇게 얻은 대상 계정 세션에는 읽기 전용 훅을 그대로 상속시킨다.
"""

from __future__ import annotations

from typing import Any

import boto3
import botocore.session
from botocore.config import Config

from . import __version__

# 대상 계정에 허용되는 유일한 API 형태(읽기 전용 불변식).
READ_VERBS = ("Describe", "Get", "List", "Search")

# 불변식 ①의 명시적 예외. 전역 allowlist 가 아니라 전용 세션에서만 쓴다.
ASSUME_ONLY_OPERATIONS = frozenset({"AssumeRole"})

_GUARD_EVENTS = ("before-parameter-build", "before-call")


class ReadOnlyViolation(RuntimeError):
    """allowlist 밖의 호출. fail-closed — 이 예외를 삼키지 마라."""


def default_config() -> Config:
    return Config(
        retries={"max_attempts": 5, "mode": "standard"},
        user_agent_extra=f"nettopo/{__version__}",
    )


def is_read_only(operation: str) -> bool:
    return operation.startswith(READ_VERBS)


def _operation_name(model: Any, event_name: str) -> str:
    name = getattr(model, "name", None)
    if name:
        return str(name)
    # 이벤트 이름의 마지막 조각이 Operation 이다(model 이 없는 경로의 폴백).
    return event_name.rsplit(".", 1)[-1]


def _deny_writes(model: Any = None, event_name: str = "", **_: Any) -> None:
    operation = _operation_name(model, event_name)
    if not is_read_only(operation):
        raise ReadOnlyViolation(
            f"읽기 전용 엔진이 {operation} 를 호출하려 했다 — "
            f"허용 verb 는 {', '.join(READ_VERBS)} 뿐이다(불변식 ①)."
        )


def _deny_all_but_assume_role(
    model: Any = None, event_name: str = "", **_: Any
) -> None:
    operation = _operation_name(model, event_name)
    if operation not in ASSUME_ONLY_OPERATIONS:
        raise ReadOnlyViolation(
            f"assume 전용 클라이언트가 {operation} 를 호출하려 했다 — "
            "이 클라이언트에는 AssumeRole 만 허용된다."
        )


def _no_head_bucket_redirect(
    request_dict: Any = None, response: Any = None, **_: Any
) -> None:
    """botocore S3 리전 리다이렉터의 `HeadBucket` 폴백을 끈다 — 그 경우에만.

    🔴 리다이렉터(`botocore.utils.S3RegionRedirectorv2.get_bucket_region`)는 틀린 리전 응답(301 등)에서 새 리전을
    ① `x-amz-bucket-region` 헤더 ② 오류 본문의 `Region` 에서 찾고, 둘 다 없으면 **스스로 `head_bucket` 을
    부른다.** 가드가 그 호출을 막는 것은 맞다(불변식 ①) — 그러나 `ReadOnlyViolation` 이 판독기 밖으로 나가
    한 로그의 「못 읽음」이 실행 전체의 죽음이 된다(불변식 ④, #36 리뷰 · 2026-09-30 실제 botocore 로 재현).
    그래서 리전 단서가 없는 응답이면 리다이렉트를 건너뛰게 표시한다 → 원래 오류가 `ClientError` 로 올라와
    부른 쪽이 「못 읽음」으로 적는다. 단서가 있으면 손대지 않는다(리다이렉트는 읽기 경로다).
    `tests/test_readonly.py::test_s3_redirect_never_calls_head_bucket` 가 실제 botocore 로 잰다.
    """
    if response is None or not isinstance(request_dict, dict):
        return
    parsed = response[1] or {}
    headers = parsed.get("ResponseMetadata", {}).get("HTTPHeaders", {})
    if "x-amz-bucket-region" in headers or parsed.get("Error", {}).get("Region"):
        return
    redirect = request_dict.get("context", {}).get("s3_redirect")
    if isinstance(redirect, dict):
        redirect["redirected"] = True


def _credential_source(
    profile_name: str | None = None, region_name: str | None = None
) -> Any:
    """자격증명 **해석 전용** 세션. 가드를 걸지 않는다.

    🔴 인증 흐름 자체는 읽기 verb 가 아니다. 실측(2026-09-14, 읽기 전용 IdC 프로필):

    - IdC/SSO 프로필은 캐시된 토큰을 갱신할 때 `sso-oidc:CreateToken` 을 부른다
      → 가드가 「읽기 전용 엔진이 CreateToken 를 호출하려 했다」로 수집을 죽였다.
    - 역할 자격증명은 `sso:GetRoleCredentials`, 역할 체이닝 프로필은 `sts:AssumeRole`
      을 부른다 → assume 전용 세션에서는 후자만 통과해 전자가 막혔다.

    가드가 지켜야 하는 것은 **대상 계정의 리소스 API** 이고 자기 자격증명을 얻는 경로가
    아니다. 그래서 자격증명 해석만 이 세션이 맡고, **서비스 클라이언트는 절대 여기서
    만들지 않는다**(그것이 이 예외를 좁게 유지하는 조건이다).
    """
    session = botocore.session.get_session()
    if profile_name:
        session.set_config_variable("profile", profile_name)
    if region_name:
        session.set_config_variable("region", region_name)
    return session


def _guarded_botocore_session(
    handler: Any, profile_name: str | None = None, region_name: str | None = None
) -> Any:
    session = botocore.session.get_session()
    if profile_name:
        session.set_config_variable("profile", profile_name)
    for event in _GUARD_EVENTS:
        session.register(event, handler)
    # 리다이렉터보다 먼저 돌아야 한다 — 클라이언트는 세션의 처리기를 복사한 뒤 리다이렉터를 붙인다.
    session.get_component("event_emitter").register_first(
        "needs-retry.s3", _no_head_bucket_redirect
    )
    # 자격증명 해석·갱신은 가드 밖의 provider 가 한다(`_credential_source` 주석 참고).
    # provider 를 통째로 넘기므로 장시간 실행 중 갱신도 같은 경로를 탄다.
    session.register_component(
        "credential_provider",
        _credential_source(profile_name, region_name).get_component(
            "credential_provider"
        ),
    )
    return session


def read_only_session(
    *, profile_name: str | None = None, region_name: str | None = None
) -> boto3.Session:
    """읽기 전용 훅이 걸린 세션. 수집 경로의 모든 클라이언트는 여기서 나온다."""
    botocore_session = _guarded_botocore_session(
        _deny_writes, profile_name, region_name
    )
    return boto3.Session(botocore_session=botocore_session, region_name=region_name)


def read_only_client(
    service: str,
    *,
    session: boto3.Session | None = None,
    profile_name: str | None = None,
    region_name: str | None = None,
    endpoint_url: str | None = None,
    config: Config | None = None,
) -> Any:
    session = session or read_only_session(
        profile_name=profile_name, region_name=region_name
    )
    return session.client(
        service,
        region_name=region_name,
        endpoint_url=endpoint_url,
        config=config or default_config(),
    )


def _set_credentials(botocore_session: Any, credentials: dict[str, str]) -> None:
    botocore_session.set_credentials(
        credentials["AccessKeyId"],
        credentials["SecretAccessKey"],
        credentials.get("SessionToken"),
    )


def session_from_credentials(
    credentials: dict[str, str], *, region_name: str | None = None
) -> boto3.Session:
    """assume 로 받은 임시 자격증명으로 **읽기 전용 훅이 걸린** 세션을 만든다."""
    botocore_session = _guarded_botocore_session(_deny_writes)
    _set_credentials(botocore_session, credentials)
    return boto3.Session(botocore_session=botocore_session, region_name=region_name)


def assume_only_client(
    *,
    profile_name: str | None = None,
    region_name: str | None = None,
    endpoint_url: str | None = None,
    config: Config | None = None,
) -> Any:
    """`AssumeRole` **하나만** 허용되는 STS 클라이언트(불변식 ①의 유일한 예외).

    자격증명 해석은 이 훅을 타지 않는다 — `_credential_source` 주석 참고.
    """
    botocore_session = _guarded_botocore_session(
        _deny_all_but_assume_role, profile_name, region_name
    )
    session = boto3.Session(botocore_session=botocore_session, region_name=region_name)
    return session.client(
        "sts",
        region_name=region_name,
        endpoint_url=endpoint_url,
        config=config or default_config(),
    )


def assume_role_session(
    *,
    role_arn: str,
    session_name: str,
    region_name: str,
    profile_name: str | None = None,
    duration_seconds: int = 3600,
    external_id: str | None = None,
) -> boto3.Session:
    """크로스계정 진입. 받은 자격증명으로 만드는 세션은 다시 읽기 전용이다.

    `external_id` = 멤버 역할(스택 B `ExternalId`)이 신뢰 정책에 `sts:ExternalId` 조건을 걸었을 때만 준다.
    비우면 인자 자체를 넣지 않는다 — 조건이 없는 역할은 예전과 똑같이 들어간다.
    """
    sts = assume_only_client(profile_name=profile_name, region_name=region_name)
    extra = {"ExternalId": external_id} if external_id else {}
    response = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=duration_seconds,
        **extra,
    )
    return session_from_credentials(response["Credentials"], region_name=region_name)
