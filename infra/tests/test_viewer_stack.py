"""스택 C(뷰어)와 그것을 위한 스택 A 두 자리 · 링크 발급 스크립트 (ADR 0003 D2-2).

🔴 **이 검사가 지키는 것 = 「서명 없는 접근 0」과 「CloudFront 로 들어오는 쓰기 0」.** CloudFront 는
동작(behavior) 하나에 `TrustedKeyGroups` 를 빠뜨리면 **그 경로만 서명 없이** 열리고, 허용 메서드를 넓히면
OAC 가 그 요청을 **버킷 권한으로 서명해** 보낸다. 두 실수 모두 `cfn-lint` 는 통과한다.

🔵 **재지 않는 것**: 실제 403·만료·긴 수명 링크는 배포에서 잰다(런북 5단계 · `infra/README.md` 「스택 C」).
여기서는 템플릿 문자열과, 스크립트가 만든 서명이 **공개키로 검증되는지**만 본다(오프라인).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from test_stack_template import _resource, _statement

INFRA = Path(__file__).resolve().parent.parent
STACK_A = INFRA / "stack-a-collector.yaml"
STACK_C = INFRA / "stack-c-viewer.yaml"
ISSUE = INFRA / "issue-viewer-link.sh"
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"


@pytest.fixture(autouse=True)
def _ledger_in_tmp(monkeypatch, tmp_path: Path) -> None:
    """링크 발급 기록(#141)이 실제 사용자 폴더(~/.local/state)에 쌓이지 않게 — 서명 스크립트는 이 환경을 물려받는다."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def test_viewer_serves_only_get_and_head_through_one_signed_behavior() -> None:
    dist = _resource(STACK_C.read_text(), "ViewerDistribution")
    assert "CacheBehaviors" not in dist, (
        "경로별 동작이 생겼다 — 그 동작에도 TrustedKeyGroups 가 없으면 그 경로가 서명 없이 열린다"
    )
    assert dist.count("DefaultCacheBehavior:") == 1
    assert "TrustedKeyGroups: [!Ref ViewerKeyGroup]" in dist
    assert "AllowedMethods: [GET, HEAD]" in dist, (
        "쓰기 메서드를 열면 OAC 가 버킷 권한으로 서명해 보낸다"
    )
    assert "CachedMethods: [GET, HEAD]" in dist
    assert "ViewerProtocolPolicy: https-only" in dist


def test_viewer_never_caches_the_page_or_leaks_the_link() -> None:
    """캐시하면 옛 index.html(옛 S3 서명 URL)이 나가 실시간이 멈춘다 · 링크가 딴 요청에 딸려 가면 새는 것이다."""
    text = STACK_C.read_text()
    dist = _resource(text, "ViewerDistribution")
    assert f"CachePolicyId: {CACHING_DISABLED}" in dist
    assert "OriginRequestPolicyId" not in dist, (
        "쿼리 문자열(서명)을 오리진에 넘기면 S3 로그에 링크가 남는다"
    )
    headers = _resource(text, "ViewerResponseHeaders")
    assert "ReferrerPolicy: {ReferrerPolicy: no-referrer, Override: true}" in headers
    assert "{Header: Cache-Control, Value: no-store, Override: true}" in headers


def test_viewer_reads_the_bucket_only_through_oac() -> None:
    text = STACK_C.read_text()
    dist = _resource(text, "ViewerDistribution")
    assert "OriginAccessControlId: !GetAtt ViewerOriginAccessControl.Id" in dist
    assert 'S3OriginConfig: {OriginAccessIdentity: ""}' in dist
    # 리전 엔드포인트 = 스택 A 와 같은 리전에 올린다는 전제(ADR 0003 D2-2).
    assert (
        'DomainName: !Sub "${ArtifactBucketName}.s3.${AWS::Region}.${AWS::URLSuffix}"'
        in dist
    )
    oac = _resource(text, "ViewerOriginAccessControl")
    assert (
        "SigningBehavior: always" in oac and "OriginAccessControlOriginType: s3" in oac
    )


def test_access_logs_are_private_encrypted_and_acl_capable() -> None:
    text = STACK_C.read_text()
    bucket = _resource(text, "AccessLogBucket")
    for flag in (
        "BlockPublicAcls",
        "BlockPublicPolicy",
        "IgnorePublicAcls",
        "RestrictPublicBuckets",
    ):
        assert f"{flag}: true" in bucket
    # 표준 로그는 ACL 로 쓴다 — Enforced 면 배포가 거절된다.
    assert "ObjectOwnership: BucketOwnerPreferred" in bucket
    assert "SSEAlgorithm: AES256" in bucket
    deny = _statement(_resource(text, "AccessLogBucketPolicy"), "DenyInsecureTransport")
    assert '"aws:SecureTransport": "false"' in deny and "Effect: Deny" in deny
    assert "Bucket: !GetAtt AccessLogBucket.DomainName" in _resource(
        text, "ViewerDistribution"
    )


# 로그 읽기 거부(#81)가 덮는 액션 = 객체 읽기 2 · 목록 2. 쓰기·ACL 은 하나도 없어야 표준 로그 전달이 산다.
_LOG_READ_ACTIONS = {
    "s3:GetObject",
    "s3:GetObjectVersion",
    "s3:ListBucket",
    "s3:ListBucketVersions",
}
# 표준 로그(레거시) 전달이 쓰는 길 — 전달 계정의 PutObject(버킷 ACL) · 로그를 켜는 쪽의 버킷 ACL 읽기·쓰기.
_LOG_DELIVERY_ACTIONS = {
    "s3:PutObject",
    "s3:PutObjectAcl",
    "s3:GetBucketAcl",
    "s3:PutBucketAcl",
}
_ROOT = "arn:${AWS::Partition}:iam::${AWS::AccountId}:root"
_READ_DENY = "DenyLogReadsExceptListedReaders"


def _flow_list(stmt: str, key: str) -> list[str]:
    """`Key: [a, b]` 한 줄의 원소. 여러 줄 목록이면 못 읽은 것이다 — 그때는 검사가 FAIL 해야 한다."""
    m = re.search(rf"^\s*{key}: \[([^\]]*)\]$", stmt, flags=re.M)
    assert m, f"{key} 를 한 줄 목록으로 못 읽었다:\n{stmt}"
    return [item.strip().strip('"') for item in m.group(1).split(",")]


def _log_reader_exceptions(text: str, readers: str) -> set[str]:
    """거부 문장의 예외(`aws:PrincipalArn` 가 맞으면 거부하지 않는 ARN)를 파라미터 값 하나로 **풀어 본다**.

    템플릿 문자열을 그대로 비교하면 「조건 이름은 맞는데 가지가 바뀐」 판을 못 잡는다 — 그래서 `!If` 의 조건을
    파라미터 값으로 평가하고 고른 가지의 `!Split`/`!Sub` 를 직접 계산한다(PyYAML 없이 이 모양만 안다).
    """
    stmt = _statement(_resource(text, "AccessLogBucketPolicy"), _READ_DENY)
    lines = [line.strip() for line in stmt.splitlines()]
    at = lines.index("ArnNotLike:")
    assert lines[at + 1] == "aws:PrincipalArn: !If", lines[at + 1 : at + 2]
    cond, when_true, when_false = (
        line.removeprefix("- ") for line in lines[at + 2 : at + 5]
    )
    assert lines[at + 5 :] in ([], [""]), "예외 목록 뒤에 다른 조건이 붙었다"
    m = re.search(rf"^  {cond}: (.*)$", text.split("\nConditions:\n")[1], flags=re.M)
    assert m, f"조건 {cond} 정의가 없다"
    assert m.group(1) == '!Not [!Equals [!Ref LogReaderPrincipalArns, ""]]', m.group(1)
    branch = when_true if readers else when_false

    def sub(value: str) -> str:
        return value.replace("${LogReaderPrincipalArns}", readers)

    if m2 := re.fullmatch(r'!Split \[",", !Sub "([^"]*)"\]', branch):
        return set(sub(m2.group(1)).split(","))
    if m2 := re.fullmatch(r'\[!Sub "([^"]*)"\]', branch):
        return {sub(m2.group(1))}
    raise AssertionError(f"모르는 예외 가지 모양: {branch}")


def test_access_log_reads_are_denied_to_everyone_but_root_and_listed_readers() -> None:
    """🔴 #81 — 로그 한 줄에 서명 링크 전체가 있다. 읽을 수 있는 사람 = 그 링크를 다시 쓸 수 있는 사람이다.

    예외는 **정확히** 계정 루트 + `LogReaderPrincipalArns` 다. 루트는 지울 수도 이름이 바뀔 수도 없는 유일한
    주체라서 고객이 자기 로그에서 잠기지 않는다(근거는 템플릿 주석). 비우면 루트 하나만 남는다.
    """
    text = STACK_C.read_text()
    policy = _resource(text, "AccessLogBucketPolicy")
    assert f"- Sid: {_READ_DENY}" in policy, (
        "로그 읽기 거부 문장이 없다 — 계정 안의 누구나 로그로 링크를 줍는다"
    )
    stmt = _statement(policy, _READ_DENY)
    assert "Effect: Deny" in stmt and 'Principal: "*"' in stmt
    actions = _flow_list(stmt, "Action")
    assert set(actions) == _LOG_READ_ACTIONS and len(actions) == len(
        _LOG_READ_ACTIONS
    ), actions
    assert "- !GetAtt AccessLogBucket.Arn" in stmt  # 목록(ListBucket*)은 버킷에
    assert '- !Sub "${AccessLogBucket.Arn}/*"' in stmt  # 객체 읽기(GetObject*)는 객체에
    assert _log_reader_exceptions(text, "") == {_ROOT}
    a = "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/us-east-1/AWSReservedSSO_LogReader_0123456789abcdef"
    b = "arn:aws:iam::111122223333:user/auditor"
    assert _log_reader_exceptions(text, a) == {_ROOT, a}
    assert _log_reader_exceptions(text, f"{a},{b}") == {_ROOT, a, b}
    # 이 정책은 아무것도 허용하지 않는다 — 허용 문장이 생기면 「읽기 제한」이 아니라 「읽기 부여」가 된다.
    assert "Effect: Allow" not in _resource(text, "AccessLogBucketPolicy")


def test_access_log_read_deny_never_touches_log_delivery() -> None:
    """표준 로그(레거시)는 전달 계정(`awslogsdelivery`)이 **버킷 ACL** 로 쓴다 — 쓰기·ACL 액션을 거부하면 로그가 끊긴다.

    v2(전달 서비스 주체)로 바꾸면 쓰는 주체가 달라진다 — 그때는 이 검사와 템플릿 주석을 같이 고친다.
    """
    text = STACK_C.read_text()
    policy = _resource(text, "AccessLogBucketPolicy")
    assert f"- Sid: {_READ_DENY}" in policy, "로그 읽기 거부 문장이 없다"
    stmt = _statement(policy, _READ_DENY)
    actions = set(_flow_list(stmt, "Action"))
    assert not actions & _LOG_DELIVERY_ACTIONS, sorted(actions & _LOG_DELIVERY_ACTIONS)
    for action in actions:
        assert action.startswith(("s3:Get", "s3:List")), action
        assert "*" not in action and "?" not in action, f"와일드카드 액션: {action}"
    # 평문 거부만 `s3:*` 다 — TLS 아닌 요청 한정이라 전달(HTTPS)을 막지 않는다. 다른 거부 문장은 두지 않는다.
    sids = re.findall(r"^\s*- Sid: (\S+)$", policy, flags=re.M)
    assert sids == ["DenyInsecureTransport", _READ_DENY], sids
    dist = _resource(text, "ViewerDistribution")
    assert (
        "        Logging:\n          Bucket: !GetAtt AccessLogBucket.DomainName" in dist
    )
    assert "Type: AWS::Logs::Delivery" not in text, (
        "v2 전달이 생겼다 — 쓰는 주체가 바뀐다"
    )


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("", True),
        ("arn:aws:iam::111122223333:role/LogReader", True),
        (
            "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/us-east-1/AWSReservedSSO_X_0123abcd,"
            "arn:aws:iam::111122223333:user/auditor",
            True,
        ),
        (
            "arn:aws:iam::111122223333:role/*",
            False,
        ),  # 와일드카드 = 예외가 계정 전체로 번진다
        ("arn:aws:iam::111122223333:role/Log?eader", False),
        (
            "arn:aws:sts::111122223333:assumed-role/LogReader/session",
            False,
        ),  # 세션 ARN 은 aws:PrincipalArn 값이 아니다
        ("arn:aws:iam::111122223333:root", False),  # 루트는 이미 예외다
        ("arn:aws:iam::111122223333:role/A, arn:aws:iam::111122223333:role/B", False),
        ("arn:aws:iam::111122223333:role/A,", False),
    ],
)
def test_log_reader_parameter_takes_exact_role_or_user_arns(
    value: str, ok: bool
) -> None:
    pattern = re.search(
        r'^    AllowedPattern: "(.*)"$',
        _resource(STACK_C.read_text(), "LogReaderPrincipalArns"),
        flags=re.M,
    ).group(1)
    assert bool(re.fullmatch(pattern.replace("\\\\", "\\"), value)) is ok, (
        pattern,
        value,
    )


def test_stack_a_lets_cloudfront_read_only_the_page() -> None:
    """버킷 정책 한 문장이 CloudFront 에 여는 것 = 같은 계정 배포 · `index.html` 한 키 · 읽기."""
    policy = _resource(STACK_A.read_text(), "ArtifactBucketPolicy")
    stmt = _statement(policy, "AllowViewerDistributionReadIndex")
    assert "Principal: {Service: cloudfront.amazonaws.com}" in stmt
    assert "Action: s3:GetObject\n" in stmt + "\n"
    assert 'Resource: !Sub "${ArtifactBucket.Arn}/index.html"' in stmt
    # 고정값 조건이 있어야 S3 공개 차단이 이 문장을 「공개」로 보지 않는다.
    assert 'StringEquals: {"aws:SourceAccount": !Ref "AWS::AccountId"}' in stmt
    # 기본 = 이 계정의 아무 배포(스택 C 를 만들기 전이라 ID 를 모른다) · `ViewerDistributionId` 를 주면 그 배포 하나로 좁힌다.
    #   🔴 좁히지 않으면 이 계정에서 배포를 만들 수 있는 사람은 누구나 서명 없이 `index.html` 을 게시할 수 있다(2026-10-28 점검).
    assert (
        "aws:SourceArn: !If\n"
        "                  - HasViewerDistribution\n"
        '                  - !Sub "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${ViewerDistributionId}"\n'
        '                  - !Sub "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/*"'
        in stmt
    )
    text = STACK_A.read_text()
    assert re.search(
        r'^  ViewerDistributionId:\n    Type: String\n    Default: ""\n', text, re.M
    )
    assert (
        'HasViewerDistribution: !Not [!Equals [!Ref ViewerDistributionId, ""]]' in text
    )
    assert policy.count("cloudfront.amazonaws.com") == 1, (
        "CloudFront 에 여는 문장은 하나뿐이어야 한다"
    )


def test_key_rotation_moves_to_the_other_slot() -> None:
    """CloudFront 는 올린 공개키의 값을 못 바꾼다. 같은 리소스의 값만 바꾸면 CloudFormation 이 제자리 수정을
    계획하고 CloudFront 가 InvalidRequest 로 거절한다(실측 2026-10-01) — 교체는 다른 논리 ID 로 옮기기다."""
    text = STACK_C.read_text()
    assert "AllowedValues: [k1, k2]" in _resource(text, "KeyLabel")
    assert "  KeyInSlot1: !Equals [!Ref KeyLabel, k1]\n" in text
    assert "  KeyInSlot2: !Equals [!Ref KeyLabel, k2]\n" in text
    assert text.count("Type: AWS::CloudFront::PublicKey") == 2
    # 두 자리는 자리 번호만 다르다 — 한쪽만 고치면 교체할 때마다 다른 모양의 키가 선다.
    one = _resource(text, "ViewerPublicKey").splitlines()[1:]
    two = _resource(text, "ViewerPublicKeyK2").splitlines()[1:]
    assert [line.replace("1", "2") for line in one] == two


# ── 겹침 교체(#141) — 템플릿을 파라미터 값으로 **풀어서** 잰다 ─────────────────────────────────────
# 문자열 비교로는 「조건 이름은 맞는데 가지가 바뀐」 판을 못 잡는다. 그래서 이 템플릿이 쓰는 짧은 함수
# (`!Ref !If !Equals !Not !Or !Condition !Sub`)만 아는 작은 평가기로 파라미터 조합마다 실제로 서는 리소스·
# 키 그룹·출력을 계산하고, 교체 전후를 CloudFormation 처럼 비교한다(같은 논리 ID 의 속성이 바뀌면 = 제자리 수정).
# PyYAML 이 `.venv` 에 없어(test_stack_template.py 머리말) 한 줄 흐름 표기만 읽는다 — 여러 줄로 바꾸면 FAIL 한다.

_TOKEN = re.compile(r'\s*(!\w+|\[|\]|,|"[^"]*"|[^\s\[\],]+)')
_STACK = "nettopo-viewer"
_SLOTS = {"ViewerPublicKey": "k1", "ViewerPublicKeyK2": "k2"}
PEM_A, PEM_B, PEM_C = (
    f"-----BEGIN PUBLIC KEY-----\n{c * 8}\n-----END PUBLIC KEY-----" for c in "ABC"
)


def _parse(src: str):
    if not src.startswith(("!", "[")):
        return src  # 평문 스칼라(Comment 등)
    tokens = _TOKEN.findall(src)

    def value(i: int):
        tok = tokens[i]
        if tok.startswith("!"):
            arg, j = value(i + 1)
            return (tok, arg), j
        if tok == "[":
            items, i = [], i + 1
            while tokens[i] != "]":
                item, i = value(i)
                items.append(item)
                if tokens[i] == ",":
                    i += 1
            return items, i + 1
        assert tok not in ("]", ","), src
        return tok.strip('"'), i + 1

    node, end = value(0)
    assert end == len(tokens), f"한 줄 흐름 표기로 다 읽지 못했다: {src}"
    return node


class _Absent(AssertionError):
    pass


def _eval(node, params: dict, conds: dict, present: set):
    if isinstance(node, list):
        return [_eval(n, params, conds, present) for n in node]
    if isinstance(node, str):
        return node
    tag, arg = node
    if tag == "!Ref":
        if arg in params:
            return params[arg]
        if arg not in present:
            raise _Absent(f"서지 않는 리소스 {arg} 를 가리킨다")
        return ("res", arg)
    if tag == "!Condition":
        return conds[arg]
    if tag == "!If":
        cond, yes, no = arg
        return _eval(yes if conds[cond] else no, params, conds, present)
    if tag == "!Equals":
        a, b = (_eval(n, params, conds, present) for n in arg)
        return a == b
    if tag == "!Not":
        return not _eval(arg[0], params, conds, present)
    if tag == "!Or":
        return any(_eval(n, params, conds, present) for n in arg)
    if tag == "!Sub":
        out = arg.replace("${AWS::StackName}", _STACK)
        for k, v in params.items():
            out = out.replace(f"${{{k}}}", v)
        return out
    raise AssertionError(f"평가기가 모르는 함수 {tag}")


def _line_value(block: str, key: str) -> str:
    m = re.search(rf"^\s*{key}: (.+)$", block, flags=re.M)
    assert m, f"{key} 가 없다:\n{block}"
    return m.group(1)


def _deployed(label: str, pem: str, previous: str = "") -> dict:
    """파라미터 한 벌 → 서는 공개키(논리 ID → 속성) · 키 그룹 · 출력. 리소스가 없는 것을 가리키면 _Absent."""
    text = STACK_C.read_text()
    params = {
        "KeyLabel": label,
        "ViewerPublicKeyPem": pem,
        "PreviousPublicKeyPem": previous,
        "CustomDomainName": "",
        "LogReaderPrincipalArns": "",
    }
    conds: dict[str, bool] = {}
    block = text.split("\nConditions:\n")[1].split("\n\n")[0]
    for line in block.splitlines():
        name, expr = line.strip().split(": ", 1)
        conds[name] = bool(_eval(_parse(expr), params, conds, set()))
    keys, present = {}, set()
    for lid in _SLOTS:
        res = _resource(text, lid)
        if not conds[_line_value(res, "Condition")]:
            continue
        present.add(lid)
        keys[lid] = {
            k: _eval(_parse(_line_value(res, k)), params, conds, present)
            for k in ("CallerReference", "Name", "EncodedKey", "Comment")
        }
    items = _eval(
        _parse(_line_value(_resource(text, "ViewerKeyGroup"), "Items")),
        params,
        conds,
        present,
    )
    outputs = {}
    out_text = text.split("\nOutputs:\n")[1]
    for name in ("ViewerKeyPairId", "PreviousKeyPairId"):
        block = _resource(out_text, name)
        cond = re.search(r"^    Condition: (\w+)$", block, flags=re.M)
        if cond and not conds[cond.group(1)]:
            continue
        outputs[name] = _eval(
            _parse(_line_value(block, "Value")), params, conds, present
        )[1]
    return {
        "keys": keys,
        "group": [ref[1] for ref in items],
        "outputs": outputs,
        "conds": conds,
    }


def _changes(before: dict, after: dict) -> dict[str, set[str]]:
    """CloudFormation 처럼 — 사라진 논리 ID · 새 논리 ID · 속성이 바뀐 논리 ID(= 공개키는 제자리 수정 → 거절)."""
    b, a = before["keys"], after["keys"]
    return {
        "removed": set(b) - set(a),
        "added": set(a) - set(b),
        "modified": {lid for lid in set(a) & set(b) if a[lid] != b[lid]},
    }


_COMBOS = [
    ("k1", PEM_B, ""),
    ("k2", PEM_B, ""),
    ("k1", PEM_B, PEM_A),
    ("k2", PEM_B, PEM_A),
]


@pytest.mark.parametrize(
    ("label", "pem", "previous"),
    _COMBOS,
    ids=["k1", "k2", "k1+old", "k2+old"],
)
def test_every_parameter_combination_builds_a_consistent_key_group(
    label: str, pem: str, previous: str
) -> None:
    """키 그룹 = 서는 공개키 **전부, 그것만**(고아 키 0 · 없는 키 참조 0). 새 링크의 ID = KeyLabel 자리의 새 키."""
    got = _deployed(label, pem, previous)
    keys, group, outputs = got["keys"], got["group"], got["outputs"]
    assert sorted(group) == sorted(keys), (
        "키 그룹과 서는 공개키가 어긋난다(고아 키 또는 빠진 키)",
        group,
        sorted(keys),
    )
    assert len(group) == (2 if previous else 1), group
    for lid, props in keys.items():
        # 이름·참조값은 논리 ID 에 묶인다 — KeyLabel 을 따라 바뀌면 겹침 교체가 옛 키를 제자리 수정한다.
        assert props["Name"] == props["CallerReference"] == f"{_STACK}-{_SLOTS[lid]}"
    active = outputs["ViewerKeyPairId"]
    assert _SLOTS[active] == label and keys[active]["EncodedKey"] == pem, (
        "새 링크의 Key-Pair-Id 가 새 키 자리를 가리키지 않는다 — 서명 스크립트가 엉뚱한 키 ID 를 붙인다"
    )
    if previous:
        old = outputs["PreviousKeyPairId"]
        assert old != active and keys[old]["EncodedKey"] == previous
    else:
        assert "PreviousKeyPairId" not in outputs


@pytest.mark.parametrize("label", ["k1", "k2"])
def test_default_parameters_reproduce_the_one_key_stack_exactly(label: str) -> None:
    """🔴 하위 호환 — 새 파라미터를 기본값(빈 값)으로 두면 이 기능 전의 판(origin/main 2026-10-21)과 **똑같이** 선다.

    옛 판: KeyLabel 자리에만 공개키 하나 · 이름/참조값 `<스택>-<KeyLabel>` · 값 = ViewerPublicKeyPem · 키 그룹 = 그 하나.
    여기가 어긋나면 이미 선 스택을 이 판으로 갱신하기만 해도 키가 바뀌어 나눠 준 링크가 전부 끊긴다.
    """
    lid = next(k for k, v in _SLOTS.items() if v == label)
    got = _deployed(label, PEM_A)
    assert got["keys"] == {
        lid: {
            "CallerReference": f"{_STACK}-{label}",
            "Name": f"{_STACK}-{label}",
            "EncodedKey": PEM_A,
            "Comment": "NetworkTopology viewer link signing key",
        }
    }
    assert got["group"] == [lid]
    assert got["outputs"] == {"ViewerKeyPairId": lid}


@pytest.mark.parametrize(
    ("before", "after", "want"),
    [
        # 겹침 교체: 옛 자리는 손대지 않고(제자리 수정 0 · 삭제 0) 새 자리만 더한다 → 옛 링크가 산다.
        (("k1", PEM_A, ""), ("k2", PEM_B, PEM_A), {"added": {"ViewerPublicKeyK2"}}),
        (("k2", PEM_B, ""), ("k1", PEM_C, PEM_B), {"added": {"ViewerPublicKey"}}),
        # 옛 키 빼기: 새 키는 그대로, 옛 자리만 사라진다.
        (("k2", PEM_B, PEM_A), ("k2", PEM_B, ""), {"removed": {"ViewerPublicKey"}}),
        # 겹침 없는 교체(유출): 새 자리를 더하고 옛 자리를 같은 갱신에서 지운다.
        (
            ("k1", PEM_A, ""),
            ("k2", PEM_B, ""),
            {"added": {"ViewerPublicKeyK2"}, "removed": {"ViewerPublicKey"}},
        ),
        # 겹침 중 다시 실행(기본 실행은 KeyLabel·PreviousPublicKeyPem 을 넘기지 않는다) — 아무것도 안 바뀐다.
        (("k2", PEM_B, PEM_A), ("k2", PEM_B, PEM_A), {}),
    ],
    ids=["overlap-k1-k2", "overlap-k2-k1", "retire", "no-overlap", "rerun-in-overlap"],
)
def test_rotation_transitions_keep_or_drop_the_old_key_as_asked(
    before: tuple, after: tuple, want: dict
) -> None:
    b, a = _deployed(*before), _deployed(*after)
    got = _changes(b, a)
    assert got == {
        "removed": want.get("removed", set()),
        "added": want.get("added", set()),
        "modified": set(),
    }, got
    if before == after:  # 다시 실행 — 키 그룹·출력이 그대로다
        assert (a["group"], a["outputs"]) == (b["group"], b["outputs"])
    elif after[2]:  # 겹침 — 교체 전 새 링크의 키가 그대로 키 그룹에 있다
        assert b["outputs"]["ViewerKeyPairId"] in a["group"]
        assert a["outputs"]["PreviousKeyPairId"] == b["outputs"]["ViewerKeyPairId"]
    elif (
        before[1] != after[1]
    ):  # 겹침 없는 교체 — 옛 키는 키 그룹에 없다(옛 링크 전부 403)
        assert a["group"] == [a["outputs"]["ViewerKeyPairId"]]
        assert b["outputs"]["ViewerKeyPairId"] not in a["group"]


def test_overlap_cannot_reuse_a_full_slot() -> None:
    """겹침 중에 또 겹쳐 교체하면 옛 자리의 값이 바뀐다 = 제자리 수정 → CloudFront 가 거절(롤백).

    deploy.sh 는 이 경우를 갱신 전에 멈추고(옛 키를 먼저 빼라), 유출 대응은 두 번의 갱신으로 나눈다.
    """
    got = _changes(_deployed("k2", PEM_B, PEM_A), _deployed("k1", PEM_C, PEM_B))
    assert got["modified"] == {"ViewerPublicKey"}, got


def test_key_conditions_are_all_defined_and_used() -> None:
    """고아 조건 0 · 없는 조건 참조 0(cfn-lint W8001/E 와 같은 축 — `.venv` 에 cfn-lint 가 없어 여기서 잰다)."""
    text = STACK_C.read_text()
    block = text.split("\nConditions:\n")[1].split("\n\n")[0]
    defined = [line.strip().split(":")[0] for line in block.splitlines()]
    assert len(defined) >= 7, defined  # 블록을 못 읽으면 아래가 빈 집합끼리 비교된다
    rest = text.replace(block, "")
    used = (
        set(re.findall(r"^\s*Condition: (\w+)$", rest, flags=re.M))
        | set(re.findall(r"!If \[(\w+),", rest))
        | set(re.findall(r"!If\n\s*- (\w+)", rest))
        | set(re.findall(r"!Condition (\w+)", block))
    )
    assert not (set(defined) - used), f"쓰지 않는 조건: {set(defined) - used}"
    assert not (used - set(defined)), f"정의 없는 조건: {used - set(defined)}"


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("", True),
        (PEM_A, True),
        ("-----BEGIN PUBLIC KEY----- AAAA -----END PUBLIC KEY-----", True),
        # 비밀키 머리(글자를 나눠 적는다 — 그대로 적으면 비밀키 탐지 훅이 이 파일을 막는다)
        (PEM_A.replace("PUBLIC", "RSA " + "PRIVATE"), False),
        ("k1", False),
    ],
    ids=["empty", "pem", "pem-one-line", "private-key", "slot-name"],
)
def test_previous_key_parameter_takes_empty_or_a_public_pem(
    value: str, ok: bool
) -> None:
    text = STACK_C.read_text()
    block = _resource(text, "PreviousPublicKeyPem")
    assert '    Default: ""' in block, (
        "기본값이 비어 있지 않으면 기존 스택 갱신이 겹침으로 바뀐다"
    )
    pattern = re.search(r'^    AllowedPattern: "(.*)"$', block, flags=re.M).group(1)
    assert bool(re.fullmatch(pattern.replace("\\\\", "\\"), value)) is ok


def test_previous_key_must_differ_from_the_new_key() -> None:
    """같은 PEM 을 두 자리에 넣으면 교체가 아니다 — 변경 세트 전에 규칙이 거절한다."""
    rule = _resource(STACK_C.read_text(), "PreviousKeyIsNotTheNewKey")
    assert 'RuleCondition: !Not [!Equals [!Ref PreviousPublicKeyPem, ""]]' in rule
    assert (
        "Assert: !Not [!Equals [!Ref PreviousPublicKeyPem, !Ref ViewerPublicKeyPem]]"
        in rule
    )


def test_issue_link_reads_the_outputs_the_stack_declares() -> None:
    outputs = STACK_C.read_text().split("\nOutputs:\n")[1]
    script = ISSUE.read_text()
    for key in ("ViewerDomain", "ViewerKeyPairId"):
        assert f"  {key}:\n" in outputs and f'"{key}"' in script


needs_openssl = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl 없음"
)


def _keypair(tmp: Path) -> tuple[Path, Path]:
    key, pub = tmp / "k.pem", tmp / "pub.pem"
    subprocess.run(
        ["openssl", "genrsa", "-out", key, "2048"], check=True, capture_output=True
    )
    subprocess.run(
        ["openssl", "rsa", "-in", key, "-pubout", "-out", pub],
        check=True,
        capture_output=True,
    )
    return key, pub


def _issue(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(ISSUE), *args], capture_output=True, text=True)


def _verifies(pub: Path, policy: str, signature: str, tmp: Path) -> bool:
    raw = base64.b64decode(signature.translate(str.maketrans("-_~", "+=/")))
    (tmp / "sig").write_bytes(raw)
    (tmp / "policy").write_text(policy)
    run = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            pub,
            "-signature",
            tmp / "sig",
            tmp / "policy",
        ],
        capture_output=True,
    )
    return run.returncode == 0


@needs_openssl
def test_issued_link_verifies_with_the_public_key_and_opens_one_path(
    tmp_path: Path,
) -> None:
    key, pub = _keypair(tmp_path)
    expires = 4102444800  # 2100-01-01 — 긴 수명(CloudFront 서명에는 7일 상한이 없다 · 배포에서 잰다)
    run = _issue(
        "--key",
        str(key),
        "--domain",
        "d111.cloudfront.net",
        "--key-pair-id",
        "KTEST",
        "--expires",
        str(expires),
    )
    assert run.returncode == 0, run.stderr
    url = run.stdout.strip()
    assert "\n" not in url and "만료" in run.stderr
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == (
        "https",
        "d111.cloudfront.net",
        "/index.html",
    )
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert (
        set(q) == {"Expires", "Signature", "Key-Pair-Id", "Hash-Algorithm"}
        and q["Key-Pair-Id"] == "KTEST"
    )
    # 🔴 SHA-256 서명은 이 매개변수가 있어야 CloudFront 가 SHA-256 으로 검증한다(없으면 SHA-1 로 검증해 403).
    assert q["Hash-Algorithm"] == "SHA256"
    assert q["Expires"] == str(expires)
    policy = (
        '{"Statement":[{"Resource":"https://d111.cloudfront.net/index.html",'
        f'"Condition":{{"DateLessThan":{{"AWS:EpochTime":{expires}}}}}}}]}}'
    )
    assert _verifies(pub, policy, q["Signature"], tmp_path)
    # 대조군 — 다른 경로의 정책으로는 같은 서명이 검증되지 않아야 이 검사가 무엇이든 잰다.
    assert not _verifies(
        pub,
        policy.replace("/index.html", "/state-latest.json"),
        q["Signature"],
        tmp_path,
    )


@needs_openssl
@pytest.mark.parametrize(
    "extra",
    [[], ["--days", "0"], ["--days", "abc"], ["--expires", str(int(time.time()) - 60)]],
    ids=["no-lifetime", "zero-days", "not-a-number", "already-expired"],
)
def test_issue_link_refuses_without_a_sane_explicit_lifetime(
    tmp_path: Path, extra: list[str]
) -> None:
    key, _ = _keypair(tmp_path)
    run = _issue(
        "--key",
        str(key),
        "--domain",
        "d111.cloudfront.net",
        "--key-pair-id",
        "KTEST",
        *extra,
    )
    assert run.returncode == 2 and run.stdout == "", (run.returncode, run.stdout[:40])


# ── 서명 스크립트 × 겹침 교체(#141) — 가짜 aws 로 스택을 조회하는 길 ─────────────────────────────────
_FAKE_STACK_AWS = r"""
import json, os, sys
argv = sys.argv[1:]
st = json.load(open(os.environ["FAKE_STACK"]))
q = argv[argv.index("--query") + 1]
if "Outputs" in q:
    # 실제 CLI 처럼 질의가 이름 붙인 출력만 돌려준다(필터에 없는 PreviousKeyPairId 는 나오지 않는다).
    for k, v in st["outputs"].items():
        if f"`{k}`" in q:
            print(f"{k}\t{v}")
else:
    key = q.split("ParameterKey=='")[1].split("'")[0]
    print(st["params"].get(key, "None"))
"""


def _stack_mode_issue(
    tmp: Path, key: Path, stack: dict
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "aws"
    fake.write_text(f"#!{sys.executable}\n{_FAKE_STACK_AWS}")
    fake.chmod(0o755)
    (tmp / "stack.json").write_text(json.dumps(stack))
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp),
        "FAKE_STACK": str(tmp / "stack.json"),
    }
    return subprocess.run(
        ["bash", str(ISSUE), "--stack", "nettopo-viewer", "--region", "eu-central-1",
         "--profile", "hub-test", "--key", str(key), "--days", "7"],
        capture_output=True, text=True, env=env,
    )  # fmt: skip


@needs_openssl
@pytest.mark.parametrize("signer", ["new", "old", "stranger"])
def test_issue_link_during_overlap_signs_only_with_the_new_slot_key(
    tmp_path: Path, signer: str
) -> None:
    """키 그룹에 두 키가 있는 동안 — 링크의 Key-Pair-Id 는 새 자리 키, 서명도 그 키. 옛 키·남의 키면 링크를 안 낸다.

    옛 키로 서명하면 오늘은 열리지만 옛 키를 빼는 날 끊기고, ID 와 키가 어긋나면 처음부터 403 이다.
    """
    keys = {}
    for name in ("new", "old", "stranger"):
        (tmp_path / name).mkdir()
        keys[name] = _keypair(tmp_path / name)
    stack = {
        "outputs": {
            "ViewerDomain": "d111.cloudfront.net",
            "ViewerKeyPairId": "KNEWSLOT",
            "PreviousKeyPairId": "KOLDSLOT",
        },
        "params": {
            "KeyLabel": "k2",
            "ViewerPublicKeyPem": keys["new"][1].read_text(),
            "PreviousPublicKeyPem": keys["old"][1].read_text(),
        },
    }
    key, pub = keys[signer]
    run = _stack_mode_issue(tmp_path, key, stack)
    if signer != "new":
        assert run.returncode == 2 and run.stdout == "", (
            run.returncode,
            run.stdout[:60],
        )
        want = "물러나는 옛 키" if signer == "old" else "짝이 아니다"
        assert want in run.stderr, run.stderr
        assert not (tmp_path / ".local/state/nettopo/viewer-links.log").exists(), (
            "내지 않은 링크를 기록했다"
        )
        return
    assert run.returncode == 0, run.stderr
    q = {k: v[0] for k, v in parse_qs(urlsplit(run.stdout.strip()).query).items()}
    assert q["Key-Pair-Id"] == "KNEWSLOT"
    policy = (
        '{"Statement":[{"Resource":"https://d111.cloudfront.net/index.html",'
        f'"Condition":{{"DateLessThan":{{"AWS:EpochTime":{q["Expires"]}}}}}}}]}}'
    )
    assert _verifies(pub, policy, q["Signature"], tmp_path)


@needs_openssl
def test_issue_link_records_expiry_and_key_id_but_never_the_link(
    tmp_path: Path,
) -> None:
    """발급 기록 = deploy.sh 가 「옛 키를 빼도 되는 때」를 읽는 곳. 링크·서명이 들어가면 기록이 곧 링크 사본이다."""
    key, _ = _keypair(tmp_path)
    for expires in (4102444800, 4102444000):
        run = _issue("--key", str(key), "--domain", "d111.cloudfront.net",
                     "--key-pair-id", "KTEST", "--expires", str(expires))  # fmt: skip
        assert run.returncode == 0, run.stderr
    ledger = tmp_path / "state" / "nettopo" / "viewer-links.log"
    assert not list(tmp_path.glob("*.log")), (
        "기록이 비밀키 옆(저장소 루트일 수 있다)에 생겼다"
    )
    text = ledger.read_text()
    rows = [line.split("\t") for line in text.splitlines()]
    assert [r[:2] for r in rows] == [["4102444800", "KTEST"], ["4102444000", "KTEST"]]
    assert all(len(r) == 3 and r[2].isdigit() for r in rows)
    sig = parse_qs(urlsplit(run.stdout.strip()).query)["Signature"][0]
    assert sig not in text and "Signature" not in text and "https://" not in text
    assert (ledger.stat().st_mode & 0o077) == 0, "발급 기록이 남에게 열려 있다"
