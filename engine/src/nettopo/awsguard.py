"""Create AWS sessions and clients with read-only API checks.

Check operation names at before-parameter-build and before-call events.
Allow only resource APIs starting with Describe, Get, List, or Search.
Resolve and refresh credentials in a separate session. Allow only
AssumeRole on the dedicated STS client.
"""

from __future__ import annotations

from typing import Any

import boto3
import botocore.session
from botocore.config import Config

from . import __version__

# Allow resource API operations only with these prefixes.
READ_VERBS = ("Describe", "Get", "List", "Search")

# Allow AssumeRole only in a separate STS session.
ASSUME_ONLY_OPERATIONS = frozenset({"AssumeRole"})

_GUARD_EVENTS = ("before-parameter-build", "before-call")


class ReadOnlyViolation(RuntimeError):
    """A resource API call violated the read-only allowlist."""


def default_config() -> Config:
    """Return boto3 client settings with standard retries and the tool
    version.
    """
    return Config(
        retries={"max_attempts": 5, "mode": "standard"},
        user_agent_extra=f"nettopo/{__version__}",
    )


def is_read_only(operation: str) -> bool:
    """Check whether an API operation starts with a READ_VERBS prefix.

    operation: AWS API operation name (for example, DescribeVpcs)

    This checks operation names, not IAM permissions.
    """
    return operation.startswith(READ_VERBS)


def _operation_name(model: Any, event_name: str) -> str:
    name = getattr(model, "name", None)
    if name:
        return str(name)
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
    """Limit S3 redirect retries to prevent an additional HeadBucket
    call.

    Keep normal redirect handling when the response includes a region.
    Otherwise, skip HeadBucket so the original API error reaches the
    caller.
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
    """Resolve and refresh credentials outside the resource API guard.

    Allow the APIs needed for SSO token refresh and role-based
    credential
    resolution. Reuse this session's credential providers in the
    read-only
    resource session.
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
    session.get_component("event_emitter").register_first(
        "needs-retry.s3", _no_head_bucket_redirect
    )
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
    """Return a boto3.Session with read-only API checks."""
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
    """Create a boto3 service client from the specified session.

    service: boto3 service name (for example, ec2)
    session: Session to use (create a guarded session if omitted)
    profile_name: AWS profile for a new session
    region_name: Client region
    endpoint_url: API endpoint for the client
    config: Client configuration (use default_config() if omitted)

    Use a supplied session as-is. Its read-only API checks must already
    be
    installed.
    """
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
    """Return a read-only boto3.Session using the supplied credentials.
    """
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
    """Create an STS client that allows only AssumeRole."""
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
    """Return a read-only session with credentials obtained through
    AssumeRole.
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
