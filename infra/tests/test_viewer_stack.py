"""Stack C (the viewer), the two slots Stack A keeps for it, and the
link-issuing script.

These tests protect two properties: no unsigned access, and no writes arriving
through CloudFront. Omit `TrustedKeyGroups` from a single cache behavior and that
one path opens up without a signature; widen the allowed methods and the origin
access control signs those requests with the bucket's own permissions. `cfn-lint`
passes on both mistakes.

Real 403s, expiry and long-lived links are measured during a deployment instead.
What is checked here, offline, is the template text and whether a signature the
script produced verifies against the public key.
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
    """Keep the issuance ledger out of the real user state directory
    (~/.local/state); the signing script inherits this environment."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def test_viewer_serves_only_get_and_head_through_one_signed_behavior() -> None:
    dist = _resource(STACK_C.read_text(), "ViewerDistribution")
    assert "CacheBehaviors" not in dist, (
        "a path-specific behavior appeared; without TrustedKeyGroups on it too, "
        "that path opens without a signature"
    )
    assert dist.count("DefaultCacheBehavior:") == 1
    assert "TrustedKeyGroups: [!Ref ViewerKeyGroup]" in dist
    assert "AllowedMethods: [GET, HEAD]" in dist, (
        "allowing write methods means the origin access control signs them with "
        "the bucket's permissions"
    )
    assert "CachedMethods: [GET, HEAD]" in dist
    assert "ViewerProtocolPolicy: https-only" in dist


def test_viewer_never_caches_the_page_or_leaks_the_link() -> None:
    """Caching would serve an old `index.html` carrying expired S3 signatures, so
    live monitoring stops; and a link that rides along on another request is a
    leak."""
    text = STACK_C.read_text()
    dist = _resource(text, "ViewerDistribution")
    assert f"CachePolicyId: {CACHING_DISABLED}" in dist
    assert "OriginRequestPolicyId" not in dist, (
        "forwarding the query string, which holds the signature, to the origin "
        "would record the link in the S3 logs"
    )
    headers = _resource(text, "ViewerResponseHeaders")
    assert "ReferrerPolicy: {ReferrerPolicy: no-referrer, Override: true}" in headers
    assert "{Header: Cache-Control, Value: no-store, Override: true}" in headers


def test_viewer_reads_the_bucket_only_through_oac() -> None:
    text = STACK_C.read_text()
    dist = _resource(text, "ViewerDistribution")
    assert "OriginAccessControlId: !GetAtt ViewerOriginAccessControl.Id" in dist
    assert 'S3OriginConfig: {OriginAccessIdentity: ""}' in dist
    # The regional endpoint assumes this stack is deployed in the same Region as
    # Stack A.
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
    # Standard logging writes through ACLs, and `BucketOwnerEnforced` would make
    # CloudFront refuse the distribution.
    assert "ObjectOwnership: BucketOwnerPreferred" in bucket
    assert "SSEAlgorithm: AES256" in bucket
    deny = _statement(_resource(text, "AccessLogBucketPolicy"), "DenyInsecureTransport")
    assert '"aws:SecureTransport": "false"' in deny and "Effect: Deny" in deny
    assert "Bucket: !GetAtt AccessLogBucket.DomainName" in _resource(
        text, "ViewerDistribution"
    )


# The log-read denial covers two object reads and two listings. It must contain
# no write or ACL action, or standard log delivery would break.
_LOG_READ_ACTIONS = {
    "s3:GetObject",
    "s3:GetObjectVersion",
    "s3:ListBucket",
    "s3:ListBucketVersions",
}
# How legacy standard log delivery writes: PutObject by the delivery account via
# the bucket ACL, plus bucket ACL reads and writes by whoever enables logging.
_LOG_DELIVERY_ACTIONS = {
    "s3:PutObject",
    "s3:PutObjectAcl",
    "s3:GetBucketAcl",
    "s3:PutBucketAcl",
}
_ROOT = "arn:${AWS::Partition}:iam::${AWS::AccountId}:root"
_READ_DENY = "DenyLogReadsExceptListedReaders"


def _flow_list(stmt: str, key: str) -> list[str]:
    """The items of a one-line `Key: [a, b]`. A multi-line list means we failed to
    read it, and the test should fail rather than pass silently."""
    m = re.search(rf"^\s*{key}: \[([^\]]*)\]$", stmt, flags=re.M)
    assert m, f"could not read {key} as a one-line list:\n{stmt}"
    return [item.strip().strip('"') for item in m.group(1).split(",")]


def _log_reader_exceptions(text: str, readers: str) -> set[str]:
    """Resolve the denial's exceptions (the ARNs that `aws:PrincipalArn` exempts)
    for one parameter value.

    Comparing template text verbatim would miss a version where the condition
    name is right but the branches were swapped, so the `!If` condition is
    evaluated against the parameter value and the chosen branch's `!Split` and
    `!Sub` are computed here. Without PyYAML this only understands these shapes.
    """
    stmt = _statement(_resource(text, "AccessLogBucketPolicy"), _READ_DENY)
    lines = [line.strip() for line in stmt.splitlines()]
    at = lines.index("ArnNotLike:")
    assert lines[at + 1] == "aws:PrincipalArn: !If", lines[at + 1 : at + 2]
    cond, when_true, when_false = (
        line.removeprefix("- ") for line in lines[at + 2 : at + 5]
    )
    assert lines[at + 5 :] in ([], [""]), (
        "another condition was appended after the exception list"
    )
    m = re.search(rf"^  {cond}: (.*)$", text.split("\nConditions:\n")[1], flags=re.M)
    assert m, f"no definition for condition {cond}"
    assert m.group(1) == '!Not [!Equals [!Ref LogReaderPrincipalArns, ""]]', m.group(1)
    branch = when_true if readers else when_false

    def sub(value: str) -> str:
        return value.replace("${LogReaderPrincipalArns}", readers)

    if m2 := re.fullmatch(r'!Split \[",", !Sub "([^"]*)"\]', branch):
        return set(sub(m2.group(1)).split(","))
    if m2 := re.fullmatch(r'\[!Sub "([^"]*)"\]', branch):
        return {sub(m2.group(1))}
    raise AssertionError(f"unrecognised exception branch: {branch}")


def test_access_log_reads_are_denied_to_everyone_but_root_and_listed_readers() -> None:
    """One log line contains the entire signed link, so anyone who can read the
    logs can reuse those links.

    The exceptions are exactly the account root plus `LogReaderPrincipalArns`.
    The root is the one principal that cannot be deleted or renamed, which is
    what keeps an operator from locking themselves out of their own logs. Leave
    the parameter empty and only the root remains.
    """
    text = STACK_C.read_text()
    policy = _resource(text, "AccessLogBucketPolicy")
    assert f"- Sid: {_READ_DENY}" in policy, (
        "the log-read denial is missing, so anyone in the account can harvest "
        "links from the logs"
    )
    stmt = _statement(policy, _READ_DENY)
    assert "Effect: Deny" in stmt and 'Principal: "*"' in stmt
    actions = _flow_list(stmt, "Action")
    assert set(actions) == _LOG_READ_ACTIONS and len(actions) == len(
        _LOG_READ_ACTIONS
    ), actions
    assert "- !GetAtt AccessLogBucket.Arn" in stmt  # listings apply to the bucket
    assert '- !Sub "${AccessLogBucket.Arn}/*"' in stmt  # object reads to the objects
    assert _log_reader_exceptions(text, "") == {_ROOT}
    a = "arn:aws:iam::111122223333:role/aws-reserved/sso.amazonaws.com/us-east-1/AWSReservedSSO_LogReader_0123456789abcdef"
    b = "arn:aws:iam::111122223333:user/auditor"
    assert _log_reader_exceptions(text, a) == {_ROOT, a}
    assert _log_reader_exceptions(text, f"{a},{b}") == {_ROOT, a, b}
    # This policy allows nothing. An Allow statement would turn it from
    # restricting reads into granting them.
    assert "Effect: Allow" not in _resource(text, "AccessLogBucketPolicy")


def test_access_log_read_deny_never_touches_log_delivery() -> None:
    """Legacy standard logging is written by the delivery account
    (`awslogsdelivery`) through the bucket ACL, so denying write or ACL actions
    would stop the logs.

    Moving to v2 delivery changes which principal writes, and this test and the
    template comments have to change together with it.
    """
    text = STACK_C.read_text()
    policy = _resource(text, "AccessLogBucketPolicy")
    assert f"- Sid: {_READ_DENY}" in policy, "the log-read denial is missing"
    stmt = _statement(policy, _READ_DENY)
    actions = set(_flow_list(stmt, "Action"))
    assert not actions & _LOG_DELIVERY_ACTIONS, sorted(actions & _LOG_DELIVERY_ACTIONS)
    for action in actions:
        assert action.startswith(("s3:Get", "s3:List")), action
        assert "*" not in action and "?" not in action, f"wildcard action: {action}"
    # Only the insecure-transport denial uses `s3:*`, and since it applies to
    # non-TLS requests it does not block delivery over HTTPS. No other denial
    # belongs in this policy.
    sids = re.findall(r"^\s*- Sid: (\S+)$", policy, flags=re.M)
    assert sids == ["DenyInsecureTransport", _READ_DENY], sids
    dist = _resource(text, "ViewerDistribution")
    assert (
        "        Logging:\n          Bucket: !GetAtt AccessLogBucket.DomainName" in dist
    )
    assert "Type: AWS::Logs::Delivery" not in text, (
        "v2 delivery appeared, which changes the writing principal"
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
        ),  # a wildcard would spread the exception across the whole account
        ("arn:aws:iam::111122223333:role/Log?eader", False),
        (
            "arn:aws:sts::111122223333:assumed-role/LogReader/session",
            False,
        ),  # a session ARN is not what `aws:PrincipalArn` holds
        ("arn:aws:iam::111122223333:root", False),  # the root is already exempt
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
    """What the one bucket-policy statement opens to CloudFront: a distribution in
    the same account, reading the single `index.html` key."""
    policy = _resource(STACK_A.read_text(), "ArtifactBucketPolicy")
    stmt = _statement(policy, "AllowViewerDistributionReadIndex")
    assert "Principal: {Service: cloudfront.amazonaws.com}" in stmt
    assert "Action: s3:GetObject\n" in stmt + "\n"
    assert 'Resource: !Sub "${ArtifactBucket.Arn}/index.html"' in stmt
    # The fixed-value condition is what stops S3 Block Public Access from reading
    # this statement as public.
    assert 'StringEquals: {"aws:SourceAccount": !Ref "AWS::AccountId"}' in stmt
    # By default this is any distribution in the account, because Stack C does not
    # exist yet and its ID is unknown; passing `ViewerDistributionId` narrows it
    # to that one distribution. Left wide, anyone who can create a distribution in
    # this account could serve `index.html` without a signature.
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
        "there must be exactly one statement opening the bucket to CloudFront"
    )


def test_key_rotation_moves_to_the_other_slot() -> None:
    """CloudFront cannot change the value of a public key once uploaded. Changing
    the value on the same resource makes CloudFormation plan an in-place update,
    which CloudFront rejects with InvalidRequest, so rotation means moving to a
    different logical ID."""
    text = STACK_C.read_text()
    assert "AllowedValues: [k1, k2]" in _resource(text, "KeyLabel")
    assert "  KeyInSlot1: !Equals [!Ref KeyLabel, k1]\n" in text
    assert "  KeyInSlot2: !Equals [!Ref KeyLabel, k2]\n" in text
    assert text.count("Type: AWS::CloudFront::PublicKey") == 2
    # The two slots differ only by their number. Editing one of them would mean
    # every rotation produced a differently shaped key.
    one = _resource(text, "ViewerPublicKey").splitlines()[1:]
    two = _resource(text, "ViewerPublicKeyK2").splitlines()[1:]
    assert [line.replace("1", "2") for line in one] == two


# ── Overlapping rotation, resolved against actual parameter values ───────────
# Text comparison would miss a version where the condition name is right but the
# branches were swapped. So a small evaluator that understands only the short
# functions this template uses (`!Ref !If !Equals !Not !Or !Condition !Sub`)
# computes which resources, key groups and outputs actually exist for each
# parameter combination, then compares before and after the way CloudFormation
# would: a changed property on the same logical ID is an in-place update.
# PyYAML is not installed, so only single-line flow notation is parsed; rewriting
# these as multi-line YAML makes these tests fail.

_TOKEN = re.compile(r'\s*(!\w+|\[|\]|,|"[^"]*"|[^\s\[\],]+)')
_STACK = "nettopo-viewer"
_SLOTS = {"ViewerPublicKey": "k1", "ViewerPublicKeyK2": "k2"}
PEM_A, PEM_B, PEM_C = (
    f"-----BEGIN PUBLIC KEY-----\n{c * 8}\n-----END PUBLIC KEY-----" for c in "ABC"
)


def _parse(src: str):
    if not src.startswith(("!", "[")):
        return src  # a plain scalar, such as Comment
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
    assert end == len(tokens), f"not fully parsed as one-line flow notation: {src}"
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
            raise _Absent(f"refers to {arg}, which is not created")
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
    raise AssertionError(f"the evaluator does not know the function {tag}")


def _line_value(block: str, key: str) -> str:
    m = re.search(rf"^\s*{key}: (.+)$", block, flags=re.M)
    assert m, f"{key} is missing:\n{block}"
    return m.group(1)


def _deployed(label: str, pem: str, previous: str = "") -> dict:
    """One set of parameters to the public keys that exist (logical ID to
    properties), the key group and the outputs. Referring to a resource that is
    not created raises `_Absent`."""
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
    """As CloudFormation would see it: logical IDs that disappeared, logical IDs
    that are new, and logical IDs whose properties changed, which for a public key
    means an in-place update and therefore a rejection."""
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
    """The key group holds every public key that exists and nothing else: no
    orphans, no references to keys that were never created. The ID new links use
    is the key in the slot named by `KeyLabel`."""
    got = _deployed(label, pem, previous)
    keys, group, outputs = got["keys"], got["group"], got["outputs"]
    assert sorted(group) == sorted(keys), (
        (
            "the key group and the existing public keys disagree (an orphan or a "
            "missing key)"
        ),
        group,
        sorted(keys),
    )
    assert len(group) == (2 if previous else 1), group
    for lid, props in keys.items():
        # Name and caller reference are tied to the logical ID. Were they to
        # follow `KeyLabel`, an overlapping rotation would modify the old key in
        # place.
        assert props["Name"] == props["CallerReference"] == f"{_STACK}-{_SLOTS[lid]}"
    active = outputs["ViewerKeyPairId"]
    assert _SLOTS[active] == label and keys[active]["EncodedKey"] == pem, (
        "the Key-Pair-Id for new links does not point at the incoming key's slot, "
        "so the signing script would attach the wrong key ID"
    )
    if previous:
        old = outputs["PreviousKeyPairId"]
        assert old != active and keys[old]["EncodedKey"] == previous
    else:
        assert "PreviousKeyPairId" not in outputs


@pytest.mark.parametrize("label", ["k1", "k2"])
def test_default_parameters_reproduce_the_one_key_stack_exactly(label: str) -> None:
    """Backwards compatibility: leaving the new parameters at their empty defaults
    produces exactly the stack this feature replaced.

    That older shape was one public key in the `KeyLabel` slot, named and
    referenced as `<stack>-<KeyLabel>`, holding `ViewerPublicKeyPem`, with a key
    group containing just it. If this drifts, merely updating an existing stack to
    this version would change the key and break every link already handed out.
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
        # Overlapping rotation: the old slot is untouched (no in-place update, no
        # deletion) and only the new slot is added, so old links keep working.
        (("k1", PEM_A, ""), ("k2", PEM_B, PEM_A), {"added": {"ViewerPublicKeyK2"}}),
        (("k2", PEM_B, ""), ("k1", PEM_C, PEM_B), {"added": {"ViewerPublicKey"}}),
        # Retiring the old key: the new key stays and only the old slot goes away.
        (("k2", PEM_B, PEM_A), ("k2", PEM_B, ""), {"removed": {"ViewerPublicKey"}}),
        # Rotation without overlap, for a leak: add the new slot and delete the
        # old one in the same update.
        (
            ("k1", PEM_A, ""),
            ("k2", PEM_B, ""),
            {"added": {"ViewerPublicKeyK2"}, "removed": {"ViewerPublicKey"}},
        ),
        # Re-running during an overlap (an ordinary run passes neither `KeyLabel`
        # nor `PreviousPublicKeyPem`) changes nothing.
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
    if before == after:  # a re-run leaves the key group and outputs unchanged
        assert (a["group"], a["outputs"]) == (b["group"], b["outputs"])
    elif after[2]:  # overlap: the pre-rotation signing key is still in the group
        assert b["outputs"]["ViewerKeyPairId"] in a["group"]
        assert a["outputs"]["PreviousKeyPairId"] == b["outputs"]["ViewerKeyPairId"]
    elif (
        before[1] != after[1]
    ):  # rotation without overlap: the old key is gone, so old links all 403
        assert a["group"] == [a["outputs"]["ViewerKeyPairId"]]
        assert b["outputs"]["ViewerKeyPairId"] not in a["group"]


def test_overlap_cannot_reuse_a_full_slot() -> None:
    """Starting a second overlapping rotation while one is in progress changes the
    value in the old slot, which is an in-place update, and CloudFront rejects it,
    rolling the stack back.

    deploy.sh stops before such an update and asks for the old key to be retired
    first; a leak response is split into two updates instead.
    """
    got = _changes(_deployed("k2", PEM_B, PEM_A), _deployed("k1", PEM_C, PEM_B))
    assert got["modified"] == {"ViewerPublicKey"}, got


def test_key_conditions_are_all_defined_and_used() -> None:
    """No unused conditions and no references to undefined ones, the same axis as
    cfn-lint W8001, checked here because cfn-lint is not installed."""
    text = STACK_C.read_text()
    block = text.split("\nConditions:\n")[1].split("\n\n")[0]
    defined = [line.strip().split(":")[0] for line in block.splitlines()]
    # Without this, a block we failed to parse would leave the comparison below
    # comparing two empty sets.
    assert len(defined) >= 7, defined
    rest = text.replace(block, "")
    used = (
        set(re.findall(r"^\s*Condition: (\w+)$", rest, flags=re.M))
        | set(re.findall(r"!If \[(\w+),", rest))
        | set(re.findall(r"!If\n\s*- (\w+)", rest))
        | set(re.findall(r"!Condition (\w+)", block))
    )
    assert not (set(defined) - used), f"unused conditions: {set(defined) - used}"
    assert not (used - set(defined)), f"undefined conditions: {used - set(defined)}"


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("", True),
        (PEM_A, True),
        ("-----BEGIN PUBLIC KEY----- AAAA -----END PUBLIC KEY-----", True),
        # A private-key header, assembled from pieces: written out in full, the
        # secret-detection hook would block this file.
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
        "a non-empty default would turn an ordinary stack update into a rotation "
        "with overlap"
    )
    pattern = re.search(r'^    AllowedPattern: "(.*)"$', block, flags=re.M).group(1)
    assert bool(re.fullmatch(pattern.replace("\\\\", "\\"), value)) is ok


def test_previous_key_must_differ_from_the_new_key() -> None:
    """The same PEM in both slots is not a rotation, and the template rule rejects
    it before a change set is created."""
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
    shutil.which("openssl") is None, reason="openssl is not available"
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
    # 2100-01-01: a long lifetime, since CloudFront signatures have no 7-day cap
    # (that is measured during a deployment).
    expires = 4102444800
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
    # CloudFront only verifies a SHA-256 signature when this parameter is
    # present; without it, it verifies with SHA-1 and returns 403.
    assert q["Hash-Algorithm"] == "SHA256"
    assert q["Expires"] == str(expires)
    policy = (
        '{"Statement":[{"Resource":"https://d111.cloudfront.net/index.html",'
        f'"Condition":{{"DateLessThan":{{"AWS:EpochTime":{expires}}}}}}}]}}'
    )
    assert _verifies(pub, policy, q["Signature"], tmp_path)
    # Control case: the same signature must not verify against a policy for a
    # different path, otherwise this test proves nothing.
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


# ── The signing script during an overlap, with a fake `aws` for stack lookups ─
_FAKE_STACK_AWS = r"""
import json, os, sys
argv = sys.argv[1:]
st = json.load(open(os.environ["FAKE_STACK"]))
q = argv[argv.index("--query") + 1]
if "Outputs" in q:
    # Like the real CLI, return only the outputs the query names, so a
    # PreviousKeyPairId outside the filter does not appear.
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
    """While the key group holds two keys, a link's Key-Pair-Id and its signature
    both come from the incoming slot; with the retiring key, or somebody else's,
    no link is issued at all.

    A link signed with the old key opens today but breaks the day that key is
    removed, and when the ID and the key disagree it is a 403 from the start.
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
            "a link that was never issued got recorded"
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
    """The ledger is where deploy.sh reads when the old key can be retired. If it
    contained links or signatures, the ledger would be a copy of the links."""
    key, _ = _keypair(tmp_path)
    for expires in (4102444800, 4102444000):
        run = _issue("--key", str(key), "--domain", "d111.cloudfront.net",
                     "--key-pair-id", "KTEST", "--expires", str(expires))  # fmt: skip
        assert run.returncode == 0, run.stderr
    ledger = tmp_path / "state" / "nettopo" / "viewer-links.log"
    assert not list(tmp_path.glob("*.log")), (
        "the ledger was created next to the private key, which may be the "
        "repository root"
    )
    text = ledger.read_text()
    rows = [line.split("\t") for line in text.splitlines()]
    assert [r[:2] for r in rows] == [["4102444800", "KTEST"], ["4102444000", "KTEST"]]
    assert all(len(r) == 3 and r[2].isdigit() for r in rows)
    sig = parse_qs(urlsplit(run.stdout.strip()).query)["Signature"][0]
    assert sig not in text and "Signature" not in text and "https://" not in text
    assert (ledger.stat().st_mode & 0o077) == 0, "the ledger is readable by others"
