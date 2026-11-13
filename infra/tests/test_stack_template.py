"""Tests for the places in the stack templates where two things have to agree.

Why these tests exist separately: the two defects that prompted them passed every
pre-commit hook and `cfn-lint`. The syntax was valid and the property names were
right; what was wrong was that two values disagreed with each other.

| Defect | The two places | What it looked like on screen |
|---|---|---|
| `MemberRoleName` changed only the IAM scope | BuildRole's `sts:AssumeRole` scope against the `--role-name` buildspec passes to the engine | An account visible yesterday is entirely grey today |
| A missing key returns 403 | StateRole's S3 permissions against the handler's `NoSuchKey` branch | Failures pile up in the DLQ every 5 minutes even though live monitoring was never enabled |

What these tests do not do is parse the templates as YAML: PyYAML is not
installed, and reading CloudFormation short tags (`!Sub`, `!GetAtt`) needs a
custom loader. Structural validation is cfn-lint's job. Instead each check is
narrowed to one block, because grepping the whole file would be satisfied by the
same action in a different role.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from internal_docs import skip_if_missing  # in schema/tests; conftest adds the path

INFRA = Path(__file__).resolve().parent.parent
STACK_A = INFRA / "stack-a-collector.yaml"
STACK_B = INFRA / "stack-b-member-role.yaml"
# The collector build spec. It ships at the root of the deployment package and
# CollectorProject reads it as `BuildSpec: buildspec.yml`, so it lives outside the
# template itself.
BUILDSPEC = INFRA.parent / "buildspec.yml"


def _resource(text: str, logical_id: str) -> str:
    """From `  <LogicalId>:` to the next resource at the same depth, so that no
    check has to grep the whole file."""
    lines = text.splitlines()
    head = f"  {logical_id}:"
    start = lines.index(head)
    for offset, line in enumerate(lines[start + 1 :], start=start + 1):
        if line and not line.startswith("   ") and line.startswith("  "):
            return "\n".join(lines[start:offset])
    return "\n".join(lines[start:])


def _statement(text: str, sid: str) -> str:
    """A single `- Sid: <sid>` statement, so a check can tell whether an action
    and a resource really sit in the same statement."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == f"- Sid: {sid}")
    indent = len(lines[start]) - len(lines[start].lstrip())
    for offset, line in enumerate(lines[start + 1 :], start=start + 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) - len(line.lstrip()) <= indent:
            return "\n".join(lines[start:offset])
    return "\n".join(lines[start:])


def test_structure_collection_uses_the_role_name_the_iam_scope_allows() -> None:
    """`MemberRoleName` has to change both the IAM scope and the name actually
    passed to the engine.

    What happened once: the buildspec had no `--role-name`, so the engine used its
    default while only the scope was narrowed. The moment that parameter was
    changed from its default, every call in that account was denied (36 of 36) and
    the node count dropped from 263 to 231.
    """
    text = STACK_A.read_text()
    project = _resource(text, "CollectorProject")
    spec = BUILDSPEC.read_text()
    # Checked per command, because the structure build runs `observe` after
    # `collect`. A check for "somewhere in the project" would pass with the flag
    # on only one of them, and the other would fall back to the engine's default
    # role name and be denied.
    joined = re.sub(r"\\\n\s*", " ", spec)
    cmds = [
        ln.strip()
        for ln in joined.splitlines()
        if re.match(r"\s*(?:\$VENV/bin/)?nettopo (collect|observe)\b", ln)
    ]
    assert cmds, "the buildspec has no nettopo collect/observe command"
    missing = [c for c in cmds if '--role-name "$MEMBER_ROLE_NAME"' not in c]
    assert not missing, (
        "these buildspec commands do not pass `--role-name`, so they use the "
        "engine's default while BuildRole's sts:AssumeRole is scoped to "
        f"MemberRoleName: {missing}"
    )
    assert (
        "{Name: MEMBER_ROLE_NAME, Type: PLAINTEXT, Value: !Ref MemberRoleName}"
        in project
    ), (
        "the MEMBER_ROLE_NAME environment variable does not come from the MemberRoleName parameter"
    )
    scope = _statement(_resource(text, "BuildRole"), "EnterMemberAccounts")
    assert "role/${MemberRoleName}" in scope, (
        "BuildRole's sts:AssumeRole scope does not use the same parameter; once "
        "the two values diverge, a change meant to widen access narrows it"
    )


def test_state_role_can_tell_a_missing_key_from_a_denied_one() -> None:
    """Without `s3:ListBucket`, S3 answers 403 for a key that does not exist, to
    avoid revealing whether it does.

    That makes the handler's `NoSuchKey` branches unreachable in a real
    deployment: "no control key means monitoring is off" and "no previous
    observation means this is the first cycle". Locally, and under moto, S3
    returns 404, so no other test can catch this.
    """
    statement = _statement(
        _resource(STACK_A.read_text(), "StateRole"), "DistinguishMissingKeyFromDenied"
    )
    assert "Action: s3:ListBucket" in statement, (
        "StateRole lacks s3:ListBucket, so a missing key returns 403 and the "
        "monitoring-off path crashes"
    )
    assert "Resource: !GetAtt ArtifactBucket.Arn" in statement, (
        "the listing permission is not confined to the artifact bucket"
    )


def test_member_role_trusts_all_four_hub_principals() -> None:
    """The state tier arrives under a different role from CodeBuild, because its
    trusted principal is Lambda and the two cannot be merged.

    Leave one out and the structure still appears in full while every "is traffic
    flowing" verdict for that account is unknown. Not one tile but all of them,
    which reads to a person as the feature being broken.
    """
    trust = _resource(STACK_B.read_text(), "MemberRole")
    for wanted, why in (
        (
            "AWSReservedSSO_${HubPermissionSetName}_*",
            "a person collecting structure with the read-only profile",
        ),
        (
            "AWSReservedSSO_${HubPermissionSetNameExtra}_*",
            "a person checking metrics with the metrics profile",
        ),
        ("role/${HubCollectorRoleNamePattern}", "the structure tier's CodeBuild"),
        ("role/${HubStateRoleNamePattern}", "the state tier's Lambda, every 5 minutes"),
    ):
        assert wanted in trust, f"a hub principal is missing: {why}"


def test_member_role_trusts_no_person_unless_asked() -> None:
    """The human path, a hub Identity Center permission set, enters the trust
    policy only when someone names it.

    Nothing about this is visible on screen, which is why it is tested. The old
    default was a permission set name from our own development account, so
    operators silently carried a trust entry that matched nothing in their
    accounts. And interpolating an empty name verbatim yields
    `AWSReservedSSO__*`, which would admit any permission set whose name starts
    with an underscore.
    """
    text = STACK_B.read_text()
    param = re.search(r"^  HubPermissionSetName:\n(.*?)(?=^  \S)", text, re.M | re.S)
    assert param, "the HubPermissionSetName parameter was not found in Stack B"
    assert re.search(r'^    Default: ""$', param.group(1), re.M), (
        "the permission set default is not empty, so an internal name would land "
        "in an operator's trust policy"
    )
    assert 'NoPermissionSet: !Equals [!Ref HubPermissionSetName, ""]' in text
    trust = _resource(text, "MemberRole")
    assert re.search(
        r'- !If\n\s+- NoPermissionSet\n\s+- !Ref "AWS::NoValue"\n'
        r"\s+- !Sub .*AWSReservedSSO_\$\{HubPermissionSetName\}_\*",
        trust,
    ), "an empty permission set name is interpolated verbatim (`AWSReservedSSO__*`)"


def test_console_templates_carry_no_internal_names() -> None:
    """The templates are what someone reads in the console: descriptions,
    defaults and outputs. None of it should name our internal profiles or
    permission sets.

    This happened: the member role's Outputs tab showed
    `--profile <an internal read-only profile> --region us-east-1` verbatim.
    Anyone copying that command stops at "profile not found". All three templates
    are checked, since there is one English set of them.
    """
    pattern = re.compile(
        r"\bant-(?:dev|deploy|metrics)\b|\bAnt[A-Z][A-Za-z]*(?:ReadOnly|Developer)\b"
    )
    files = sorted(INFRA.glob("stack-*.yaml"))
    assert len(files) == 3, f"there are not three templates: {[f.name for f in files]}"
    found = [
        f"{f.name}:{n}: {line.strip()[:80]}"
        for f in files
        for n, line in enumerate(f.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert not found, "internal names in a console template:\n" + "\n".join(found)


def test_evidence_detection_actions_match_the_engine_and_the_doc() -> None:
    """The detection actions are written in three places: the code, this template
    and the permissions document. Change one and it breaks quietly.

    What it looks like: an account has flow logs enabled, yet the badge at the top
    of the page never comes down from "evidence unconfirmed for N types". The
    cause is one action missing from the policy, and since the diagram itself
    renders fine, nothing raises an error, precisely because unknown is never
    recorded as off.

    ECS and EKS use different actions for listing and for detail, so copying only
    the representative name from `PROBES` leaves half of them out.
    """
    from nettopo.sources import REQUIRED_ACTIONS

    statement = _statement(
        _resource(STACK_B.read_text(), "MemberRole"), "DetectOptionalEvidence"
    )
    # Only `- <service>:<Action>` lines. The `Condition: !If` list in the same
    # statement also starts with `- `, so the shape is what filters it out;
    # counting those would put non-actions into the comparison.
    in_template = set(
        re.findall(r"^\s+- ([a-z0-9]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE)
    )
    assert in_template == set(REQUIRED_ACTIONS), (
        "Stack B's detection actions differ from `nettopo.sources.REQUIRED_ACTIONS`"
    )

    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    # Cut at the next subsection (`### 1-5.`): cutting only at `## ` would count
    # the table rows of later subsections as part of this one.
    section = doc.split("### 1-4. ")[1].split("\n## ")[0].split("\n### ")[0]
    rows = {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and ":" in line.split("`")[1]
    }
    assert rows == set(REQUIRED_ACTIONS), (
        "the §1-4 table in `docs/required-permissions.md` differs from the code, "
        "so someone looking at an AccessDenied cannot tell which action is missing"
    )
    # The document no longer carries a copy of the policy JSON: the template is
    # the single source, and `test_permission_doc_lists_exactly_the_template_actions`
    # compares the whole table against it.


def test_state_role_detects_evidence_in_the_hub_account_too() -> None:
    """The hub account's own tiles are read through the state tier's role, not the
    member role, so the detection actions have to exist there too.

    What it looks like: member account tiles settle on "N additional evidence
    types" while the hub account's tiles stay on "evidence unconfirmed" forever.
    We measured eight types denied across all three places in the hub's state
    document, because the statement existed only in Stack B and the test above
    only looked at Stack B.
    """
    from nettopo.sources import REQUIRED_ACTIONS

    statement = _statement(
        _resource(STACK_A.read_text(), "StateRole"), "DetectOptionalEvidence"
    )
    in_template = set(
        re.findall(r"^\s+- ([a-z0-9]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE)
    )
    assert in_template == set(REQUIRED_ACTIONS), (
        "the detection actions on Stack A's state-tier role differ from "
        "`nettopo.sources.REQUIRED_ACTIONS`"
    )
    assert "aws:RequestedRegion" in statement, "detection is not scoped by Region"


def _collect_actions(logical_id: str, template: Path = STACK_A) -> set[str]:
    """Only the collection actions from one role's `ReadNetworkTopology`
    statement.

    Some action names contain a hyphen (`network-firewall:ListFirewalls`), which
    the detection regex above (`[a-z0-9]+:`) silently drops, so the service name
    here allows `-`.
    """
    role = _resource(template.read_text(), logical_id)
    # CloudFront lives in its own statement outside the Region condition (see the
    # test below), so both statements have to be joined to see one tier.
    # API Gateway (its own statement scoped by path ARN) and Global Accelerator
    # (its own us-west-2 statement) belong to the same tier.
    statement = "\n".join(
        _statement(role, sid)
        for sid in (
            "ReadNetworkTopology",
            "ReadCloudFrontGlobal",
            "ReadApiGateway",
            "ReadGlobalAccelerator",
            # Cloud WAN, also its own us-west-2 statement, is the same tier
            "ReadCloudWan",
            # So is the account name (its own us-east-1 statement, see
            # `engine/src/nettopo/account_name.py`), but only on the
            # structure-collecting role: the 5-minute observation never reads
            # names, keeping StateRole minimal.
            "ReadAccountName",
        )
        if sid != "ReadAccountName" or f"- Sid: {sid}" in role
    )
    return set(
        re.findall(r"^\s+- ([a-z0-9-]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE)
    )


def test_cloudfront_is_read_outside_the_collection_region_condition() -> None:
    """CloudFront is read from its own statement, pinned to us-east-1, outside the
    collection-Region condition.

    CloudFront has a global endpoint, so `aws:RequestedRegion` evaluates to
    us-east-1 whichever Region is asked for. We measured this: a hub collecting
    us-west-2 and ap-northeast-2 was denied the list of its own distributions,
    while the two member roles whose lists included us-east-1 were allowed. What
    it looks like: if us-east-1 is not among the collected Regions, every Region
    lane renders fine but the CloudFront card is missing entirely, and the top of
    the page reports an account it could not read, indistinguishable from an
    account with no distributions.
    """
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_A, "StateRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        regional = _statement(role, "ReadNetworkTopology")
        assert not re.search(r"^\s+- (cloudfront|route53):", regional, re.MULTILINE), (
            f"{logical_id}: CloudFront or Route 53 sits inside the "
            "collection-Region statement"
        )
        cf = _statement(role, "ReadCloudFrontGlobal")
        actions = re.findall(r"^\s+- ([a-z0-9-]+:[A-Z][A-Za-z]+)$", cf, re.MULTILINE)
        # Route 53 also has a global endpoint signed in us-east-1, so it belongs
        # in this statement. Put it in the collection-Region statement and the
        # whole area reports "no permission" whenever us-east-1 is not collected,
        # exactly as with CloudFront.
        assert actions == [
            "cloudfront:ListDistributions",
            "cloudfront:ListVpcOrigins",
            # For the console-style info panel: cache policy names in the
            # behaviors table and OAC names in the origins table. CloudFront, so
            # the same statement.
            "cloudfront:ListCachePolicies",
            "cloudfront:ListOriginAccessControls",
            "route53:ListHostedZones",
            "route53:ListResourceRecordSets",
            "route53:GetHostedZone",
            # CloudFront-scope WAF web ACLs are also called only in us-east-1
            # (`waf_web_acl_global`, see `Spec.region`), so same reason, same
            # statement.
            "wafv2:ListWebACLs",
            "wafv2:GetWebACL",
        ], (logical_id, actions)
        assert re.search(r"^\s+aws:RequestedRegion: us-east-1$", cf, re.MULTILINE), (
            f"{logical_id}: the CloudFront statement is not pinned to us-east-1"
        )


def test_account_name_is_read_in_its_own_us_east_1_statement() -> None:
    """Reading the account name (see `engine/src/nettopo/account_name.py`) is its
    own statement, one action, pinned to us-east-1.

    Account Management has a global endpoint signed in us-east-1, as CloudFront
    does. Put it in the collection-Region statement, or pin it elsewhere, and the
    account frames keep showing numeric IDs even though the permission was
    granted. A denial here is not a failure, only an absent name, so there is not
    even a banner, which makes it harder to notice. The test also checks it is not
    mixed into another statement, so a reviewer doing least-privilege review can
    read what this one line opens in a single statement.
    """
    # The 5-minute observation (StateRole) never reads names; only structure
    # collection calls this, which keeps that role minimal.
    assert "account:GetAccountInformation" not in _resource(
        STACK_A.read_text(), "StateRole"
    )
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        statement = _statement(role, "ReadAccountName")
        # The target is this role's own account and nothing else, not "*"; called
        # without an account ID, the API answers for the caller's account.
        assert (
            'Resource: !Sub "arn:${AWS::Partition}:account::${AWS::AccountId}:account"'
            in statement
        ), f"{logical_id}: the account-name target is not this account alone"
        actions = re.findall(
            r"^\s+- ([a-z0-9-]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE
        )
        assert actions == ["account:GetAccountInformation"], (logical_id, actions)
        assert re.search(
            r"^\s+aws:RequestedRegion: us-east-1$", statement, re.MULTILINE
        ), f"{logical_id}: the account-name statement is not pinned to us-east-1"
        assert role.count("account:GetAccountInformation") == 1, (
            f"{logical_id}: the account-name action also appears in another statement"
        )


def test_waf_web_acls_are_read_in_the_collection_regions_and_cloudfront_scope_in_us_east_1() -> (
    None
):
    """WAF web ACLs come in two scopes. The regional scope (listing, and the
    resources it protects) belongs in the collection-Region statement; the
    CloudFront scope (listing and detail) belongs in the us-east-1 statement.

    If us-east-1 is not among the collected Regions and only the regional
    statement exists, the CloudFront-scope web ACL card reports "no permission"
    in full, as CloudFront distributions do. Drop `ListWebACLs` from the regional
    statement and the regional card does the same; drop
    `ListResourcesForWebACL` and every web ACL shows as unconfirmed, with no line
    to API Gateway. Four actions, no wildcards.
    """
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_A, "StateRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        regional = _statement(role, "ReadNetworkTopology")
        for action in (
            "wafv2:ListWebACLs",
            "wafv2:ListResourcesForWebACL",
            "wafv2:GetWebACL",
        ):
            assert re.search(rf"^\s+- {action}$", regional, re.MULTILINE), (
                logical_id,
                action,
            )
        cf = _statement(role, "ReadCloudFrontGlobal")
        for action in ("wafv2:ListWebACLs", "wafv2:GetWebACL"):
            assert re.search(rf"^\s+- {action}$", cf, re.MULTILINE), (
                logical_id,
                "us-east-1",
                action,
            )
        assert not re.search(r"^\s+- wafv2:\*", role, re.MULTILINE), logical_id


API_GATEWAY_PATHS = [
    "/restapis",
    "/restapis/*/resources",
    "/vpclinks",
    "/apis",
    "/apis/*/integrations",
]


def test_api_gateway_is_read_only_on_the_named_paths() -> None:
    """`apigateway:GET` is granted only on specific path ARNs: no wildcard action,
    no `Resource: "*"`.

    API Gateway splits reads from writes by HTTP verb (`GET`, `POST`, …) and its
    resources are paths, so `Resource: "*"` would also expose API keys, policies
    and stage variables, which is not least privilege. Exactly the five paths the
    collector calls belong here: the REST list, resources (with
    `embed=methods`), VPC links, the HTTP list, and integrations. Miss one and the
    API card still renders but has no VPC link line and reports itself as
    partially unreadable.
    """
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_A, "StateRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        st = _statement(role, "ReadApiGateway")
        actions = re.findall(r"^\s+- ([a-z0-9-]+:[A-Za-z*]+)$", st, re.MULTILINE)
        assert actions == ["apigateway:GET"], (logical_id, actions)
        paths = re.findall(r'apigateway:\*::(/[^"]+)"', st)
        assert paths == API_GATEWAY_PATHS, (logical_id, paths)
        assert 'Resource: "*"' not in st, (
            f"{logical_id}: the API Gateway statement uses Resource *"
        )
        assert "aws:RequestedRegion" in st, (
            f"{logical_id}: the API Gateway statement has no Region condition"
        )
        regional = _statement(role, "ReadNetworkTopology")
        assert not re.search(
            r"^\s+- (apigateway|globalaccelerator):", regional, re.MULTILINE
        ), logical_id


def test_global_accelerator_is_read_in_us_west_2_only() -> None:
    """The Global Accelerator API exists only in us-west-2 (see `Spec.region` in
    the engine; `test_relations_d.py` checks the client Region).

    In the collection-Region statement, accelerators would report "no permission"
    in full whenever us-west-2 is not collected, exactly as with CloudFront.
    """
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_A, "StateRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        st = _statement(role, "ReadGlobalAccelerator")
        actions = re.findall(r"^\s+- ([a-z0-9-]+:[A-Za-z*]+)$", st, re.MULTILINE)
        assert actions == [
            "globalaccelerator:ListAccelerators",
            "globalaccelerator:ListListeners",
            "globalaccelerator:ListEndpointGroups",
        ], (logical_id, actions)
        assert re.search(r"^\s+aws:RequestedRegion: us-west-2$", st, re.MULTILINE), (
            logical_id
        )


def test_cloud_wan_is_read_in_us_west_2_only() -> None:
    """The Cloud WAN (Network Manager) API exists only in us-west-2 (see
    `core_network.Spec.region` in the engine; `test_relations_129.py` checks the
    client Region).

    In the collection-Region statement, core networks would report "no
    permission" in full whenever us-west-2 is not collected, as with Global
    Accelerator. VPC Lattice is a regional service and belongs in the
    collection-Region statement, not here.
    """
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_A, "StateRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        st = _statement(role, "ReadCloudWan")
        actions = re.findall(r"^\s+- ([a-z0-9-]+:[A-Za-z*]+)$", st, re.MULTILINE)
        assert actions == [
            "networkmanager:ListCoreNetworks",
            "networkmanager:ListAttachments",
        ], (logical_id, actions)
        assert re.search(r"^\s+aws:RequestedRegion: us-west-2$", st, re.MULTILINE), (
            logical_id
        )
        regional = _statement(role, "ReadNetworkTopology")
        assert not re.search(r"^\s+- networkmanager:", regional, re.MULTILINE), (
            logical_id
        )
        assert re.search(
            r"^\s+- vpc-lattice:ListServiceNetworks$", regional, re.MULTILINE
        ), f"{logical_id}: VPC Lattice is not in the collection-Region statement"


def test_cloudfront_metrics_are_read_outside_the_collection_region_condition() -> None:
    """CloudFront metrics are read the same way: their own statement, pinned to
    us-east-1, outside the collection-Region condition.

    `AWS/CloudFront` metrics exist only in us-east-1. We measured two
    AccessDenied errors from `StateRole`'s `GetMetricData` on a hub collecting
    us-west-2. What it looks like: with monitoring on, every other resource shows
    numbers while that account's CloudFront bandwidth shows "?" every cycle. The
    distribution listing had already been fixed, but metric reads still lived only
    in the collection-Region statement (`ReadFlowMetrics`).
    """
    for template, logical_id in ((STACK_A, "StateRole"), (STACK_B, "MemberRole")):
        role = _resource(template.read_text(), logical_id)
        st = _statement(role, "ReadCloudFrontMetrics")
        actions = re.findall(
            r"(?:Action:|^\s+-) (cloudwatch:[A-Za-z]+)$", st, re.MULTILINE
        )
        assert actions == ["cloudwatch:GetMetricData"], (logical_id, actions)
        # us-east-1 for CloudFront, us-west-2 for Global Accelerator usage, since
        # `AWS/GlobalAccelerator` can only be queried there.
        assert re.search(
            r"^\s+aws:RequestedRegion: \[us-east-1, us-west-2\]$", st, re.MULTILINE
        ), (
            f"{logical_id}: the pinned-Region metric statement is not us-east-1 and us-west-2"
        )


def test_both_stack_a_roles_read_the_same_resource_types() -> None:
    """The structure tier (CodeBuild) and the state tier (Lambda) read the same
    resource types, and widening only one of them diverges quietly.

    For a long time this was recorded only in a comment above `StateRole` saying
    its action count matched `BuildRole`. A comment that drifts fails nothing, and
    actions really were added to `BuildRole` alone once.

    What it looks like: a card that appears in the hourly structure collection is
    covered by "unconfirmed" on every 5-minute observation, because the
    observation has to re-read what is attached to know what to ask metrics
    about. Structure and state then describe different diagrams, and a person
    cannot tell which to believe.
    """
    # The account name is the one exception: only structure collection reads it,
    # keeping the 5-minute observation's role minimal.
    build = _collect_actions("BuildRole") - {"account:GetAccountInformation"}
    state = _collect_actions("StateRole")
    # Discriminating power: if the regex matched nothing, `set() == set()` would
    # pass and the test would be measuring nothing at all.
    assert len(build) > 14, (
        f"failed to read the collection actions (bad regex): {sorted(build)}"
    )
    assert build == state, (
        "BuildRole and StateRole in Stack A read different resource types - "
        f"only in BuildRole: {sorted(build - state)} · only in StateRole: {sorted(state - build)}"
    )


def test_member_role_reads_the_same_resource_types_as_the_hub() -> None:
    """The member role (Stack B) reads the same resource types as the hub
    (Stack A), and adding to Stack A alone diverges quietly.

    That happened: actions for four collectors went into Stack A only, so a real
    collection was denied Auto Scaling and Network Firewall in the member
    accounts. What it looks like: ASGs and firewalls appear in the hub account's
    frame but drop out of every other account's frame as "could not read this
    account". Since most of the accounts people care about are member accounts,
    it reads as "I installed it and my firewall is missing".
    """
    hub = _collect_actions("BuildRole")
    member = _collect_actions("MemberRole", STACK_B)
    assert len(member) > 14, (
        f"failed to read the collection actions (bad regex): {sorted(member)}"
    )
    assert member == hub, (
        "the Stack B member role and Stack A read different resource types - "
        f"only in Stack A: {sorted(hub - member)} · only in Stack B: {sorted(member - hub)}"
    )


def test_the_hub_grants_exactly_the_apis_the_collectors_call() -> None:
    """The APIs the collectors call (the `"svc:Action"` names in the service
    files) are exactly what Stack A grants.

    The two tests above only compare roles against each other, so if all three
    were missing the same action, nothing would fail. Add a collector without
    adding its permission and the deployment and collection both succeed while
    not one new card appears, with only "could not read this account" at the top.
    In the other direction, an action no collector calls is not least privilege.
    The API names are the literal strings used by `classify(exc, api)` and
    `Spec.api`.
    """
    services = INFRA.parent / "engine" / "src" / "nettopo" / "services"
    # The account name is not a collected resource type but the name of an account
    # node, so it lives in one file outside `services/`; read it too.
    called = {
        m
        for path in [*services.glob("*.py"), services.parent / "account_name.py"]
        for m in re.findall(r'"([a-z0-9-]+:[A-Z][A-Za-z]+)"', path.read_text())
    }
    assert len(called) > 30, (
        f"failed to read API names from the service files: {sorted(called)}"
    )
    hub = _collect_actions("BuildRole")
    assert called == hub, (
        "the APIs the collectors call differ from what Stack A grants - "
        f"not granted: {sorted(called - hub)} · granted but never called: {sorted(hub - called)}"
    )


def _write_resources(role: str) -> set[str]:
    """The resource keys (the path after the bucket ARN) of every statement in a
    role that carries `s3:PutObject`."""
    keys: set[str] = set()
    for sid in re.findall(r"- Sid: (\w+)", role):
        statement = _statement(role, sid)
        if "s3:PutObject" in statement:
            keys |= set(re.findall(r"\$\{ArtifactBucket\.Arn\}/([^\"]+)\"", statement))
    return keys


def test_observer_cannot_write_its_own_switch_or_the_page() -> None:
    """If the observation function could write the control key, it could switch
    itself on, and the server-side "off by default" would be gone.

    When signing moved into the 5-minute tier, adding one more PutObject line to
    the observation role was the easiest path available; this test is what closes
    it. Signing is done by a separate role (`SignerRole`).
    """
    writes = _write_resources(_resource(STACK_A.read_text(), "StateRole"))
    assert writes == {"snapshots/state/*", "state-latest.json"}, writes


def test_observer_reads_exactly_its_four_named_inputs() -> None:
    """The observation role reads four named keys plus one archive prefix for
    structures, and opens no other past snapshot.

    When recording what a deleted resource looked like began to need the older
    structure, the structure tier started keeping one copy aside
    (`structure-previous.json`). Later, because the copy is itself a different
    structure once the structure changed more than once while monitoring was off,
    a read of `snapshots/structure/*` was added for exactly that prefix. The state
    archive (`snapshots/state/*`), the whole bucket (`*`) and `index.html` are
    still not readable, and this test is what keeps them that way.
    """
    role = _resource(STACK_A.read_text(), "StateRole")
    statement = _statement(role, "ReadInputs")
    reads = set(re.findall(r"\$\{ArtifactBucket\.Arn\}/([^\"]+)\"", statement))
    assert reads == {
        "control/desired.json",
        "structure-latest.json",
        "structure-previous.json",
        "state-latest.json",
        "snapshots/structure/*",
    }, reads
    # The structure tier writes the copy before overwriting the pointer; the
    # other order would leave the copy identical to the new structure.
    text = BUILDSPEC.read_text()
    assert text.index('structure-previous.json" --sse AES256') < text.index(
        '"s3://$ARTIFACT_BUCKET/structure-latest.json" --sse AES256'
    )


def test_signer_signs_and_writes_exactly_the_three_keys() -> None:
    """The signer role writes `index.html`, which carries the address slot, and
    the control key the page writes, and nothing else. It reads the unsigned
    source (`unsigned/index.html`, the only key this function actually reads) plus
    whatever the four presigned URLs need, including reading the control key so a
    second tab can follow along.

    This role's permissions are the permissions of the URLs baked into the page,
    so widening it widens what the page, meaning anyone holding the file, can
    write.
    """
    role = _resource(STACK_A.read_text(), "SignerRole")
    assert _write_resources(role) == {"index.html", "control/desired.json"}
    reads = set()
    for sid in re.findall(r"- Sid: (\w+)", role):
        statement = _statement(role, sid)
        if "s3:GetObject" in statement:
            reads |= set(re.findall(r"\$\{ArtifactBucket\.Arn\}/([^\"]+)\"", statement))
    # GetObject on the control key is what S3 evaluates for the lease-extending
    # `If-Match` PUT. Without it, every extension returns 403.
    assert reads == {
        "unsigned/index.html",
        "index.html",
        "state-latest.json",
        "control/desired.json",
    }, reads
    # The unsigned source is read-only here: modifying it would make the next
    # cycle sign the modified page. Only the build writes it.
    assert "s3:PutObject" not in _statement(role, "ReadUnsignedPage")
    conditional = _statement(role, "SignControlConditionalWrite")
    assert "Action: s3:GetObject" in conditional
    assert "s3:PutObject" not in conditional
    # Requests arriving through a presigned URL also get 403 for a missing key,
    # which would turn "waiting for the first observation" right after switching
    # on into "could not read".
    listing = _statement(role, "ScreenSeesMissingStateAs404")
    assert "Action: s3:ListBucket" in listing
    assert "Resource: !GetAtt ArtifactBucket.Arn" in listing
    assert "sts:AssumeRole\n" not in role.split("Policies:")[1], (
        "the signer never enters a target account"
    )
    function = _resource(STACK_A.read_text(), "SignerFunction")
    assert "Role: !GetAtt SignerRole.Arn" in function
    assert "Handler: signer.handler" in function


def test_signer_is_called_only_by_its_two_rules() -> None:
    """No inbound endpoint: there is no backend for a browser to call, so the only
    callers must appear by name in exactly two Lambda permissions."""
    text = STACK_A.read_text()
    sources = set()
    for logical_id in re.findall(
        r"^  (\w+):\n    Type: AWS::Lambda::Permission", text, re.M
    ):
        block = _resource(text, logical_id)
        if "SignerFunction" in block:
            sources |= set(re.findall(r"SourceArn: !GetAtt (\w+)\.Arn", block))
            assert "Principal: events.amazonaws.com" in block
    assert sources == {"SignerScheduleRule", "SignerAfterBuildRule"}, sources
    assert "AWS::Lambda::Url" not in text


def test_observer_is_called_only_by_its_two_rules() -> None:
    """No inbound endpoint here either: the observation function is invoked by the
    5-minute schedule and by "the control key was just written", and by nothing
    else.

    The wake-up rule must match exactly two keys, the control key and the
    structure pointer. Widen the pattern and the function would wake on the
    `state-latest.json` it writes itself, forming a loop that invokes itself.
    """
    text = STACK_A.read_text()
    sources = set()
    for logical_id in re.findall(
        r"^  (\w+):\n    Type: AWS::Lambda::Permission", text, re.M
    ):
        block = _resource(text, logical_id)
        if "StateFunction" in block:
            sources |= set(re.findall(r"SourceArn: !GetAtt (\w+)\.Arn", block))
            assert "Principal: events.amazonaws.com" in block
    assert sources == {"StateScheduleRule", "StateWakeRule"}, sources
    wake = _resource(text, "StateWakeRule")
    assert "source: [aws.s3]" in wake
    assert "detail-type: [Object Created]" in wake
    assert "bucket: {name: [!Ref ArtifactBucket]}" in wake
    # Exactly two keys wake it: the control key and the structure pointer. The
    # observation function can write neither, so there is no self-invoking loop;
    # adding `state-latest.json` here would create one.
    assert "object: {key: [control/desired.json, structure-latest.json]}" in wake, (
        "the wake-up rule is not exactly the control key and the structure pointer"
    )
    assert "Arn: !GetAtt StateFunction.Arn" in wake
    # The bucket only emits events, and without the state tier it emits none.
    bucket = _resource(text, "ArtifactBucket")
    assert "EventBridgeConfiguration: {EventBridgeEnabled: true}" in bucket
    assert "- StateTierEnabled" in bucket.split("NotificationConfiguration:")[1][:80]


def test_cors_opens_only_the_two_methods_to_the_file_origin() -> None:
    """CORS grants no permissions, but a wide CORS configuration is something a
    security review will raise, so the methods and origins stay narrow.

    There are two origins: `file://` (which sends `null`) and a page opened
    through a Stack C link (`https://*.cloudfront.net`, narrowed to that one
    distribution when `ViewerOrigin` is set, see
    `test_bucket_cors_can_be_narrowed_to_the_viewer_origin`).
    """
    bucket = _resource(STACK_A.read_text(), "ArtifactBucket")
    # `AllowedOrigins: [...]` also appears in a comment, so read only
    # non-comment lines.
    line = next(
        x for x in bucket.splitlines() if x.strip().startswith("AllowedOrigins: [")
    )
    # The origins line is pinned whole: the nested `!If` cannot be split on commas
    # into a set, and widening it is a decision worth reviewing.
    assert line.strip() == (
        'AllowedOrigins: ["null", !If [HasViewerOrigin, !Ref ViewerOrigin, "https://*.cloudfront.net"]]'
    ), line
    assert "AllowedMethods: [GET, PUT]" in bucket
    assert "ExposedHeaders: [ETag]" in bucket
    # Without `if-match`, the page's lease-extending PUT is blocked at the
    # preflight and the page falls back to its older behaviour of one four-hour
    # lease, which means the backend keeps running for four hours after the window
    # is closed. Losing the exposed `ETag` has the same effect. The browser
    # harness cannot observe preflights, so this header is only checked here, and
    # widening the set is worth reviewing, hence pinning it as a set.
    headers = bucket.split("AllowedHeaders: [")[1].split("]")[0]
    assert {h.strip() for h in headers.split(",")} == {
        "content-type",
        "if-none-match",
        "if-match",
    }
    assert bucket.count("- Id: ") == 3, (
        "there should be one CORS rule and two lifecycle rules (index.html and "
        "unsigned/)"
    )


def test_build_bakes_csp_connect_to_the_bucket_origin() -> None:
    """The `connect-src` in the page's CSP is baked by the build, and it has to be
    the same origin the signer signs (virtual-host addressing against the
    regional endpoint, see `_client` in `state-lambda/signer.py`). Missing or
    mismatched, turning the live switch on gets the request blocked by the
    browser and the page reports that it could not read the state, with the error
    visible only in the console of whoever opened the file.

    It is opened only on stacks that have a signer. Without one the slot stays
    empty forever, so the value is `'none'`; opening it anyway would make the page
    claim indefinitely that this is an unsigned page to be re-downloaded shortly.
    """
    project = _resource(STACK_A.read_text(), "CollectorProject")
    build = (
        BUILDSPEC.read_text()
        .split("set -eu\n        CONNECT=")[1]
        .split("post_build:")[0]
    )
    assert build.startswith('""\n')
    assert (
        'if [ "$LIVE_CHANNEL" = ENABLED ]; then '
        'CONNECT="https://$ARTIFACT_BUCKET.s3.$AWS_REGION.amazonaws.com"; fi' in build
    )
    assert 'CSP_CONNECT="$CONNECT" node build-standalone.mjs' in build
    assert build.count("node build-standalone.mjs") == 1
    assert (
        "{Name: LIVE_CHANNEL, Type: PLAINTEXT, Value: !If [StateTierEnabled, ENABLED, DISABLED]}"
        in project
    )
    signer = (INFRA / "state-lambda" / "signer.py").read_text()
    assert '"addressing_style": "virtual"' in signer
    assert '"us_east_1_regional_endpoint": "regional"' in signer


def test_old_page_versions_expire_but_snapshots_do_not() -> None:
    """A new revision lands every 5 minutes, 288 a day, so the lifecycle rule has
    to target `index.html` only. With an empty prefix it would also delete older
    versions of the archived snapshots after a day."""
    bucket = _resource(STACK_A.read_text(), "ArtifactBucket")
    rule = bucket.split("LifecycleConfiguration:")[1]
    assert "Prefix: index.html\n" in rule
    assert "NoncurrentVersionExpiration: {NoncurrentDays: 1}" in rule
    assert "ExpirationInDays" not in rule, "the current revision is never deleted"


def test_build_never_publishes_an_unsigned_page_where_the_signer_exists() -> None:
    """The `index.html` people open must never hold an unsigned revision, one with
    an empty address slot, not even briefly.

    The build used to overwrite `index.html` with an empty slot for the signer to
    patch seconds later; across 64 measured builds that gap was 2 to 9 seconds,
    median 6. A page loaded during it, or reached through "open the latest page",
    could not turn monitoring on, and nothing anywhere reported an error.

    Now, when the live channel exists, the build uploads only to
    `unsigned/index.html` and the signer publishes, while BuildRole is explicitly
    denied writes to `index.html` on that stack, so the window cannot reopen even
    if the buildspec regresses. On a stack without the live channel there is no
    signer, so the build writes `index.html` directly, and the CSP is `'none'`.
    """
    text = STACK_A.read_text()
    post = BUILDSPEC.read_text().split("post_build:")[1]
    live = post.split('if [ "$LIVE_CHANNEL" = ENABLED ]; then')[1].split("else")[0]
    assert '"s3://$ARTIFACT_BUCKET/unsigned/index.html"' in live
    assert '"s3://$ARTIFACT_BUCKET/index.html"' not in live
    plain = (
        post.split('if [ "$LIVE_CHANNEL" = ENABLED ]; then')[1]
        .split("else")[1]
        .split("fi")[0]
    )
    assert '"s3://$ARTIFACT_BUCKET/index.html" --sse AES256' in plain
    assert post.count('"s3://$ARTIFACT_BUCKET/index.html" --sse') == 1
    # IAM enforces the same promise, denying it only on stacks that have the
    # state tier, and therefore a signer.
    role = _resource(text, "BuildRole")
    deny = role.split("- Sid: OnlyTheSignerPublishesThePage")[1].split(
        "- !Ref AWS::NoValue"
    )[0]
    assert (
        "- StateTierEnabled\n"
        in role.split("- Sid: OnlyTheSignerPublishesThePage")[0][-60:]
    )
    assert "Effect: Deny" in deny
    assert "Action: s3:PutObject" in deny
    assert 'Resource: !Sub "${ArtifactBucket.Arn}/index.html"' in deny


def test_signer_runs_one_at_a_time() -> None:
    """With the signer as the only writer of `index.html`, ordering is preserved by
    serial execution, which is the price of dropping the conditional write.

    Were two invocations to overlap, the one that read the source first, carrying
    the old structure, could write last and revert the new structure to the old.
    """
    function = _resource(STACK_A.read_text(), "SignerFunction")
    assert "\n      ReservedConcurrentExecutions: 1\n" in function


def test_signer_runs_right_after_a_successful_build_of_this_project() -> None:
    """Each build bakes a fresh unsigned source (`unsigned/index.html`) with an
    empty address slot. If this rule pointed at a different project, `index.html`
    would keep the old structure for up to 5 minutes after every hourly build,
    with no error anywhere."""
    rule = _resource(STACK_A.read_text(), "SignerAfterBuildRule")
    assert "build-status: [SUCCEEDED]" in rule
    assert "project-name: [!Ref CollectorProject]" in rule
    assert "Arn: !GetAtt SignerFunction.Arn" in rule


# ── Flow-log reading permissions (see `docs/required-permissions.md` §1-3) ───
# The same rule is written separately in both stacks: Stack A for the hub's own
# tiles, Stack B for member tiles. Change one and that side's accounts report
# "enabled but unreadable, permissions", or worse, one side opens up application
# logs while the other does not.
_FLOW_POLICIES = {
    STACK_A: (
        "StateFlowLogFindPolicy",
        "StateFlowLogGroupsPolicy",
        "StateFlowLogCentralPolicy",
    ),
    STACK_B: (
        "MemberFlowLogFindPolicy",
        "MemberFlowLogGroupsPolicy",
        "MemberFlowLogCentralPolicy",
    ),
}
_FLOW_CONDITIONS = ("FlowLogsOn", "HasGroupPrefixes", "HasCentralBuckets")
_OWN_FLOW_KEYS = (
    '"arn:${AWS::Partition}:s3:::*AWSLogs/${AWS::AccountId}/vpcflowlogs/*"',
    '"arn:${AWS::Partition}:s3:::*AWSLogs/aws-account-id=${AWS::AccountId}/aws-service=vpcflowlogs/*"',
)
_ACTION = re.compile(r"(?:Action: |^\s+- )([a-z0-9]+:[A-Z][A-Za-z]+)$", re.MULTILINE)


def _parameter(text: str, name: str) -> str:
    """One `  <name>:` block under `Parameters:`, searched only within that
    section so a resource of the same name cannot be picked up."""
    section = text.split("\nParameters:\n")[1].split("\nConditions:\n")[0]
    return _resource("\n" + section, name)


def _pattern(text: str, name: str) -> re.Pattern[str]:
    raw = re.search(r'AllowedPattern: "(.*)"$', _parameter(text, name), re.MULTILINE)
    assert raw, (
        f"{name} has no AllowedPattern, so a `*` would reach the policy verbatim"
    )
    return re.compile(raw.group(1).replace("\\\\", "\\"))


def test_flow_log_reading_is_off_by_default_in_both_stacks() -> None:
    """Until it is switched on, the permissions have to stay exactly as they are:
    the default is off and all three policies are conditional resources.

    If this broke, a security team would discover new log-read permissions after
    what was supposed to be a routine stack update, having been told these only
    appear when the parameter enables them.
    """
    for stack, names in _FLOW_POLICIES.items():
        text = stack.read_text()
        param = _parameter(text, "FlowLogReading")
        assert "Default: DISABLED" in param, (
            f"{stack.name}: FlowLogReading does not default to disabled"
        )
        assert "AllowedValues: [ENABLED, DISABLED]" in param
        for name, condition in zip(names, _FLOW_CONDITIONS, strict=True):
            assert f"Condition: {condition}\n" in _resource(text, name) + "\n", (
                f"{stack.name}: {name} is created without the {condition} condition"
            )
        conditions = text.split("\nConditions:\n")[1].split("\nResources:\n")[0]
        assert "FlowLogsOn: " in conditions
        on = next(line for line in conditions.splitlines() if "FlowLogsOn: " in line)
        assert "!Equals [!Ref FlowLogReading, ENABLED]" in on
        for prefixed, param_name in (
            ("HasGroupPrefixes", "FlowLogGroupNamePrefixes"),
            ("HasCentralBuckets", "FlowLogCentralBucketArns"),
        ):
            line = next(ln for ln in conditions.splitlines() if f"{prefixed}: " in ln)
            # If the policy were created with an empty value, `!Split` would
            # produce `log-group:*`, opening every log group in the account.
            assert (
                "!Condition FlowLogsOn" in line
                and f'!Equals [!Ref {param_name}, ""]' in line
            ), (
                f"{stack.name}: {prefixed} does not screen out both an empty value and the off state"
            )
    # In Stack A there is no role to attach to without the state tier, since
    # StateRole is itself a conditional resource.
    a_conditions = STACK_A.read_text().split("\nConditions:\n")[1]
    assert "FlowLogsOn: !And [!Condition StateTierEnabled," in a_conditions, (
        "Stack A: without the state tier the flow-log policy still points at "
        "StateRole, so the deployment fails"
    )


def test_flow_log_actions_match_the_reader_and_the_doc() -> None:
    """The flow-log actions are written in four places: the reader's code,
    Stack A, Stack B and §1-3 of the permissions document."""
    from nettopo.flowlogs import REQUIRED_ACTIONS

    for stack, names in _FLOW_POLICIES.items():
        text = stack.read_text()
        found = set()
        for name in names:
            found |= set(_ACTION.findall(_resource(text, name)))
        assert found == set(REQUIRED_ACTIONS), (
            f"{stack.name}: the flow-log policy's actions differ from "
            "`nettopo.flowlogs.REQUIRED_ACTIONS`: "
            f"missing {sorted(set(REQUIRED_ACTIONS) - found)} · extra {sorted(found - set(REQUIRED_ACTIONS))}"
        )
    # Discriminating power: a role that was not switched on must carry no
    # log-content action, since mixing them into the inline policy would make the
    # condition meaningless.
    for stack, role in (
        (STACK_A, "StateRole"),
        (STACK_A, "BuildRole"),
        (STACK_B, "MemberRole"),
    ):
        block = _resource(stack.read_text(), role)
        for action in (
            "logs:DescribeLogGroups",
            "logs:DescribeLogStreams",
            "logs:GetLogEvents",
            "s3:GetBucketLocation",
        ):
            assert action not in block, (
                f"{stack.name} {role} carries {action} unconditionally"
            )

    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    section = doc.split("### 1-3. ")[1].split("### 1-4. ")[0]
    rows = {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and ":" in line.split("`")[1]
    }
    assert rows == set(REQUIRED_ACTIONS), (
        "the §1-3 table in `docs/required-permissions.md` differs from the reader"
    )


def test_log_group_prefixes_reject_wildcards_and_empty_elements() -> None:
    """The stack screens the log group list at the input stage, because a single
    `*` would open every application log."""
    for stack in _FLOW_POLICIES:
        text = stack.read_text()
        groups = _pattern(text, "FlowLogGroupNamePrefixes")
        for ok in ("", "/vpc/flow", "vpc-flow-logs", "/a/b,/c/d", "flow#1.x_y-z"):
            assert groups.fullmatch(ok), (
                f"{stack.name}: rejects the valid group name {ok!r}"
            )
        for bad in ("*", "/", "/aws/*", "a,,b", "a,", ",a", "/,a", "a b", "a*b"):
            assert not groups.fullmatch(bad), (
                f"{stack.name}: accepts {bad!r}, which widens the policy"
            )
        buckets = _pattern(text, "FlowLogCentralBucketArns")
        for ok in (
            "",
            "arn:aws:s3:::log-archive",
            "arn:aws:s3:::a-b.c,arn:aws-cn:s3:::xyz",
        ):
            assert buckets.fullmatch(ok), (
                f"{stack.name}: rejects the valid bucket ARN {ok!r}"
            )
        for bad in (
            "*",
            "arn:aws:s3:::*",
            "arn:aws:s3:::b/*",
            "arn:aws:s3:::b,",
            "log-archive",
        ):
            assert not buckets.fullmatch(bad), f"{stack.name}: accepts {bad!r}"
    # Both stacks must enforce the same input rule; if one is looser, only that
    # side's accounts widen.
    a, b = STACK_A.read_text(), STACK_B.read_text()
    for name in ("FlowLogGroupNamePrefixes", "FlowLogCentralBucketArns"):
        assert _pattern(a, name).pattern == _pattern(b, name).pattern, (
            f"the input rule for {name} differs between the two stacks"
        )


def test_own_account_flow_reads_touch_only_flow_log_keys() -> None:
    """In this account's own buckets, only the two flow-log key shapes are
    readable: not the artifact objects, and not application data in the same
    account."""
    for stack, (find, _, _) in _FLOW_POLICIES.items():
        policy = _resource(stack.read_text(), find)
        read = _statement(policy, "FlowLogOwnObjectRead")
        for key in _OWN_FLOW_KEYS:
            assert key in read, (
                f"{stack.name}: GetObject is missing the flow-log key {key}"
            )
        assert 's3:::*"' not in read, f"{stack.name}: GetObject opens every object"
        assert 's3:ResourceAccount: !Ref "AWS::AccountId"' in read
        listing = _statement(policy, "FlowLogOwnBucketList")
        assert 's3:ResourceAccount: !Ref "AWS::AccountId"' in listing
        for prefix in (
            '"*AWSLogs/${AWS::AccountId}/vpcflowlogs/*"',
            '"*AWSLogs/aws-account-id=${AWS::AccountId}/aws-service=vpcflowlogs/*"',
        ):
            assert prefix in listing, (
                f"{stack.name}: the listing prefix {prefix} is missing"
            )
        assert 's3:ResourceAccount: !Ref "AWS::AccountId"' in _statement(
            policy, "FlowLogOwnBucketMeta"
        )


def test_log_group_reads_are_named_and_bound_to_this_account() -> None:
    """Log content is readable only for the group names that were supplied, and
    only in this account; "all log groups" would open application logs."""
    for stack, (find, groups, _) in _FLOW_POLICIES.items():
        text = stack.read_text()
        policy = _resource(text, groups)
        allow = _statement(policy, "FlowLogReadGroups")
        assert "!Ref FlowLogGroupNamePrefixes" in allow, (
            f"{stack.name}: the group names do not come from the parameter"
        )
        assert "log-group:*" not in allow, f"{stack.name}: opens every log group"
        assert "aws:RequestedRegion" in allow, f"{stack.name}: not scoped by Region"
        deny = _statement(policy, "FlowLogReadGroupsOwnAccountOnly")
        assert "Effect: Deny" in deny, (
            f"{stack.name}: nothing denies the `*` in the Allow's account position"
        )
        assert (
            'NotResource: !Sub "arn:${AWS::Partition}:logs:*:${AWS::AccountId}:log-group:*"'
            in deny
        )
        assert set(_ACTION.findall(deny)) == set(_ACTION.findall(allow)), (
            f"{stack.name}: the Deny does not cover every action the Allow grants"
        )
        # `log-group:*` may appear in exactly two places: the discovery listing
        # and the Deny above.
        find_stmt = _statement(_resource(text, find), "FlowLogFindGroups")
        assert "Action: logs:DescribeLogGroups\n" in find_stmt + "\n"
        everywhere = text.count("log-group:*")
        assert (
            everywhere
            == find_stmt.count("log-group:*") + deny.count("log-group:*")
            == 2
        ), (
            f"{stack.name}: `log-group:*` appears outside the discovery listing and the Deny"
        )


def test_central_buckets_yield_only_this_accounts_flow_keys() -> None:
    """Even in another account's bucket, only this account's flow-log keys are
    readable: not other accounts' logs in a log archive, and not CloudTrail."""
    for stack, (_, _, central) in _FLOW_POLICIES.items():
        policy = _resource(stack.read_text(), central)
        assert "!Ref FlowLogCentralBucketArns" in _statement(
            policy, "FlowLogCentralObjectRead"
        )
        deny = _statement(policy, "FlowLogCentralOwnKeysOnly")
        assert "Effect: Deny" in deny and "Action: s3:GetObject\n" in deny + "\n"
        for key in _OWN_FLOW_KEYS:
            assert key in deny, f"{stack.name}: the Deny's exceptions are missing {key}"
        # Without the account condition this Deny would also block reads of our
        # own artifact bucket (the control key and structure snapshots), stopping
        # live monitoring entirely.
        assert (
            'StringNotEquals:\n                s3:ResourceAccount: !Ref "AWS::AccountId"'
            in deny
        ), f"{stack.name}: the Deny has no other-account condition"


def test_state_function_gets_the_same_switch_the_policies_use() -> None:
    """The handler's switch (`FLOW_LOGS`) and the permission condition
    (`FlowLogsOn`) come from the same parameter."""
    env = _resource(STACK_A.read_text(), "StateFunction")
    assert "FLOW_LOGS: !Ref FlowLogReading" in env, (
        "the state function never receives the flow-log switch, so the permission "
        "exists and is never used"
    )


# The largest template CloudFormation accepts inline as `TemplateBody`. Anything
# bigger has to be uploaded to S3 and passed by URL.
_TEMPLATE_BODY_LIMIT = 51_200


# The two guides that carry deployment commands: our own development notes and
# the install guide people follow.
_INSTALL_GUIDE = INFRA.parent / "docs" / "install-guide.md"
_INSTALL_GUIDE_EN = INFRA.parent / "docs" / "install-guide.en.md"
_INSTALL_GUIDES = [_INSTALL_GUIDE, _INSTALL_GUIDE_EN]
# The development notes (infra/README.md) are not part of the public tree, so that
# entry is only checked when present (see `internal_docs`). The install guide has
# to exist in every tree, or reading it fails.
_DEPLOY_DOCS = [INFRA / "README.md", *_INSTALL_GUIDES]
# There is one English Stack A template. It currently fits inline, so it is
# uploaded without `--s3-bucket`; should it exceed the limit again, this test is
# what forces the documented commands to go through S3.
_STACK_A_TEMPLATES = ("infra/stack-a-collector.yaml",)


def _sh_lines(doc: Path) -> list[str]:
    """Extract one command per line from a guide's sh blocks, joining
    continuations (`\\`)."""
    text = doc.read_text()
    lines = []
    for block in re.findall(r"```sh\n(.*?)```", text, flags=re.S):
        lines += re.sub(r"\\\n\s*", " ", block).splitlines()
    return lines


def _readme_cfn_commands(doc: Path = INFRA / "README.md") -> list[str]:
    """The CloudFormation deployment commands in a guide's sh blocks."""
    return [
        command
        for command in _sh_lines(doc)
        if re.match(
            r"\s*aws cloudformation (deploy|create-stack|update-stack|create-change-set)\b",
            command,
        )
    ]


@pytest.mark.parametrize("doc", _DEPLOY_DOCS, ids=lambda d: d.name)
def test_readme_deploys_oversized_templates_through_s3(doc: Path) -> None:
    """A guide's deployment commands have to match the template's size: anything
    over the inline limit goes through S3.

    This happened: after the flow-log parameters were added, Stack A reached
    58,600 bytes, and the two `deploy --template-file` lines in the guide would
    have been rejected by CloudFormation, stopping anyone following the document
    at the very first deployment step. Neither pre-commit nor cfn-lint sees this
    mismatch, because the template itself is fine.
    """
    if doc == INFRA / "README.md":
        skip_if_missing("infra/README.md")
    commands = _readme_cfn_commands(doc)
    by_template = {}
    for command in commands:
        m = re.search(r"--template-(?:file |body file://)(\S+)", command)
        if m:
            by_template.setdefault(m.group(1), []).append(command)
    # If the parser found no commands, the loop below would run zero times and
    # pass silently, so count what was found first.
    stack_a_commands = sum(len(by_template.get(t, [])) for t in _STACK_A_TEMPLATES)
    assert stack_a_commands >= 2, (
        f"no Stack A deployment command found in the guide (found {sorted(by_template)})"
    )
    for template, found in by_template.items():
        size = (INFRA.parent / template).stat().st_size
        if size <= _TEMPLATE_BODY_LIMIT:
            continue
        for command in found:
            assert "--template-body" not in command and "--s3-bucket" in command, (
                f"{template} is {size} B, over the {_TEMPLATE_BODY_LIMIT} B inline "
                f"limit, yet the documented command does not go through S3, so "
                f"CloudFormation rejects it: {command[:120]}"
            )


@pytest.mark.parametrize("guide", _INSTALL_GUIDES, ids=lambda d: d.name)
def test_install_guide_names_profile_and_region_and_no_internal_names(
    guide: Path,
) -> None:
    """People copy the install guide's commands verbatim, so two things must not
    leak into it.

    First, every `aws` command spells out `--profile` and `--region`. Running
    against the shell's defaults would quietly create stacks in another account
    or Region, and we cannot know what those defaults are. Second, no internal
    profile or permission set names: they do not exist in someone else's account,
    so the command either fails or, passed to Stack B, trusts a human path that
    does not exist.
    """
    lines = _sh_lines(guide)
    # `aws configure …` (setting up an SSO login, say) only touches local config
    # and calls no collection API, and has no notion of a Region, so it is exempt.
    aws_lines = [
        line
        for line in lines
        if re.search(r"(?:^|\$\()\s*aws ", line)
        and not re.search(r"\baws configure\b", line)
    ]
    # Again: with no commands found, the loop below would pass silently, so check
    # the count first (the total across all six steps).
    assert len(aws_lines) >= 12, (
        f"only {len(aws_lines)} aws commands were found in the guide"
    )
    for line in aws_lines:
        assert "--profile " in line and "--region " in line, (
            f"command without a profile or Region: {line.strip()[:120]}"
        )
    text = guide.read_text()
    internal = re.findall(
        r"\bant-(?:dev|deploy|metrics)\b|\bAnt[A-Z][A-Za-z]*(?:ReadOnly|Developer)\b",
        text,
    )
    assert not internal, f"internal names in the install guide: {sorted(set(internal))}"


# The quick start in both root READMEs (README.md in English, README.ko.md in
# Korean) summarises steps 1 to 3 of the guide, and is the first command anyone
# sees.
_ROOT_READMES = [INFRA.parent / "README.md", INFRA.parent / "README.ko.md"]
_INTERNAL_NAME = re.compile(
    r"\bant-(?:dev|deploy|metrics)\b|\bAnt[A-Z][A-Za-z]*(?:ReadOnly|Developer)\b"
)


@pytest.mark.parametrize("readme", _ROOT_READMES, ids=lambda d: d.name)
def test_root_readme_quick_start_is_copyable(readme: Path) -> None:
    """The root READMEs' quick start is held to the same rules as the guide; there
    are simply fewer commands.

    It has a Stack A deployment command which goes through S3 if the template is
    over the inline limit, every `aws` command spells out `--profile` and
    `--region`, and no internal profile or permission set names appear. It is not
    simply added to `_DEPLOY_DOCS` because that test requires at least two
    Stack A commands (step 2 plus the step 5 update) while the quick start shows
    only step 2.
    """
    stack_a = [
        command
        for command in _readme_cfn_commands(readme)
        if any(template in command for template in _STACK_A_TEMPLATES)
    ]
    assert stack_a, f"no Stack A deployment command in {readme.name}'s quick start"
    for command in stack_a:
        template = next(t for t in _STACK_A_TEMPLATES if t in command)
        if (INFRA.parent / template).stat().st_size > _TEMPLATE_BODY_LIMIT:
            assert "--template-body" not in command and "--s3-bucket" in command, (
                f"{readme.name}: {template} is over the inline limit yet the "
                f"command does not go through S3: {command[:120]}"
            )
    aws_lines = [
        line
        for line in _sh_lines(readme)
        if re.search(r"(?:^|\$\()\s*aws ", line)
        and not re.search(r"\baws configure\b", line)
    ]
    # Count first, since zero commands found would make the loop below pass
    # silently (the total across steps 0 to 4).
    assert len(aws_lines) >= 5, (
        f"only {len(aws_lines)} aws commands were found in {readme.name}"
    )
    for line in aws_lines:
        assert "--profile " in line and "--region " in line, (
            f"{readme.name}: command without a profile or Region: {line.strip()[:120]}"
        )
    internal = _INTERNAL_NAME.findall(readme.read_text())
    assert not internal, f"internal names in {readme.name}: {sorted(set(internal))}"


def test_kms_keys_open_decrypt_only_through_s3_on_the_given_keys() -> None:
    """For buckets encrypted with a customer-managed KMS key: decrypt is granted
    only on the keys that were given, only when S3 is the one decrypting, and
    with the parameter empty the policy resource does not exist at all.

    If this broke, a role in an account that named no keys would gain "decrypt
    any key" through `Resource: "*"`, or naming the keys would still leave the
    flow logs unreadable because the KMS key cannot be used.
    """
    from nettopo.flowlogs import REQUIRED_ACTIONS, WHY

    patterns = []
    for stack, name in (
        (STACK_A, "StateFlowLogKmsPolicy"),
        (STACK_B, "MemberFlowLogKmsPolicy"),
    ):
        text = stack.read_text()
        block = _resource(text, name)
        assert "Condition: HasKmsKeys\n" in block + "\n", (
            f"{stack.name}: {name} is created unconditionally"
        )
        conditions = text.split("\nConditions:\n")[1].split("\nResources:\n")[0]
        line = next(ln for ln in conditions.splitlines() if "HasKmsKeys: " in ln)
        assert (
            "!Condition FlowLogsOn" in line
            and '!Equals [!Ref FlowLogKmsKeyArns, ""]' in line
        )
        assert set(_ACTION.findall(block)) == {"kms:Decrypt"}
        assert 'Resource: !Split [",", !Ref FlowLogKmsKeyArns]' in block
        assert (
            'kms:ViaService: "s3.*.amazonaws.com"' in block and "StringLike:" in block
        )
        param = _parameter(text, "FlowLogKmsKeyArns")
        assert 'Default: ""' in param
        keys = _pattern(text, "FlowLogKmsKeyArns")
        for ok in (
            "",
            "arn:aws:kms:us-east-1:111122223333:key/1234abcd-12ab-34cd-56ef-1234567890ab",
            "arn:aws:kms:us-east-1:111122223333:key/mrk-1234abcd12ab34cd56ef1234567890ab,"
            "arn:aws-cn:kms:cn-north-1:111122223333:key/0a0b",
        ):
            assert keys.fullmatch(ok), f"{stack.name}: rejects the valid key ARN {ok!r}"
        for bad in (
            "*",
            "arn:aws:kms:us-east-1:111122223333:key/*",
            "arn:aws:kms:us-east-1:111122223333:alias/aws/s3",
            "arn:aws:kms:*:111122223333:key/0a0b",
            "1234abcd-12ab-34cd-56ef-1234567890ab",
            "arn:aws:kms:us-east-1:111122223333:key/0a0b,",
        ):
            assert not keys.fullmatch(bad), f"{stack.name}: accepts {bad!r}"
        patterns.append(keys.pattern)
        # It must not be mixed unconditionally into another role. In Stack A the
        # statement that decrypts the source zip belongs to BuildRole, not
        # StateRole.
        role = _resource(text, "StateRole" if stack == STACK_A else "MemberRole")
        assert "kms:Decrypt" not in role, (
            f"{stack.name}: the role body carries kms:Decrypt unconditionally"
        )
    assert patterns[0] == patterns[1], (
        "the input rule for FlowLogKmsKeyArns differs between the two stacks"
    )
    # The engine never calls KMS; finding it in the reader's action list would mean
    # it calls a verb outside the guard.
    assert not [a for a in REQUIRED_ACTIONS if a.startswith("kms:")]
    assert "FlowLogKmsKeyArns" in WHY["kms"]


def test_the_flow_reader_structure_calls_are_granted_where_it_runs() -> None:
    """Concluding "no traffic in this window" requires listing a subnet's ENIs, so
    both roles that read flow logs (Stack A's state tier and Stack B's member
    role) need that action unconditionally. Without it, every quiet subnet in an
    account with flow logs enabled is judged on a denied ENI listing instead."""
    from nettopo.flowlogs import STRUCTURE_ACTIONS

    for stack, role in ((STACK_A, "StateRole"), (STACK_B, "MemberRole")):
        block = _resource(stack.read_text(), role)
        for action in STRUCTURE_ACTIONS:
            assert re.search(rf"^\s+- {re.escape(action)}$", block, re.MULTILINE), (
                f"{stack.name} {role} is missing {action}"
            )


def test_service_quota_actions_match_the_engine_the_doc_and_both_roles() -> None:
    """Reading service quotas is written in four places: the engine, Stack B's
    member role, Stack A's state role and the permissions document.

    What it looks like: miss one and that account's utilisation list stays on
    default limits rather than the account's own. For an account whose quotas were
    raised, the percentages then read higher than reality, and since the diagram
    renders normally, nothing reports an error.
    """
    from nettopo.utilization import REQUIRED_ACTIONS

    wanted = {a for a in REQUIRED_ACTIONS if a.startswith("servicequotas:")}
    assert len(wanted) == 2, "precondition: the engine requires two quota-read verbs"
    pattern = r"^\s+- ([a-z0-9]+:[A-Z][A-Za-z]+)$"
    member = _statement(
        _resource(STACK_B.read_text(), "MemberRole"), "ReadServiceQuotas"
    )
    assert set(re.findall(pattern, member, re.MULTILINE)) == wanted
    state = _statement(_resource(STACK_A.read_text(), "StateRole"), "ReadServiceQuotas")
    assert set(re.findall(pattern, state, re.MULTILINE)) == wanted
    # The collector (the structure tier) never uses quotas, so granting it the
    # permission would be privilege it does not need.
    assert "servicequotas:" not in _resource(STACK_A.read_text(), "BuildRole")

    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    section = doc.split("### 1-5. ")[1].split("\n## ")[0].split("\n### ")[0]
    rows = {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and ":" in line.split("`")[1]
    }
    assert rows == wanted


# ── The permissions document matches the templates ───────────────────────────
_IAM_ACTION = re.compile(r"^[a-z0-9-]+:[A-Za-z0-9]+$")


def _policy_actions(template: Path) -> set[str]:
    """The actions a template's IAM roles and role policies grant, excluding trust
    policies. Bucket and queue policies are left out, since they are not role
    permissions."""
    text = template.read_text()
    body = text.split("\nResources:\n")[1].split("\nOutputs:\n")[0]
    actions: set[str] = set()
    for logical_id in re.findall(r"^  ([A-Za-z0-9]+):$", body, re.MULTILINE):
        block = _resource(text, logical_id)
        kind = re.search(r"^    Type: (\S+)", block, re.MULTILINE)
        if not kind or kind.group(1) not in ("AWS::IAM::Role", "AWS::IAM::RolePolicy"):
            continue
        # For a role, start at `Policies:`; what precedes it is the trust policy,
        # the receiving side of `sts:AssumeRole`, which grants nothing.
        policies = (
            block.split("\n      Policies:\n")[1]
            if kind.group(1) == "AWS::IAM::Role"
            else block
        )
        actions |= set(
            re.findall(r"^\s+- ([a-z0-9-]+:[A-Za-z0-9*]+)\s*$", policies, re.MULTILINE)
        )
        actions |= set(
            re.findall(
                r"Action: ([a-z0-9-]+:[A-Za-z0-9*]+)\s*$", policies, re.MULTILINE
            )
        )
        for listed in re.findall(r"Action: \[([^\]]*)\]", policies):
            actions |= {a.strip().strip('"') for a in listed.split(",")}
    return actions


def _doc_rows(section: str) -> set[str]:
    return {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and ":" in line.split("`")[1]
    }


def test_permission_doc_lists_exactly_the_template_actions() -> None:
    """Whether the tables in `docs/required-permissions.md`, which a security
    reviewer reads, list exactly what the templates grant.

    Section 1 covers Stack B's member role; Stack A is section 1 (reading its own
    account) plus section 2 (collector-specific); Stack C creates no roles.
    Change one side only and a reviewer either approves permissions that are not
    actually granted, or permissions get deployed that the document never
    mentions. The absence of wildcards in Allow actions is checked here too; the
    `s3:*` in a denial lives in a bucket policy, not a role.
    """
    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    stack_b = doc.split("\n## 1. ")[1].split("\n## 2. ")[0]
    stack_a = doc.split("\n## 2. ")[1].split("\n## 3. ")[0]
    in_b = _policy_actions(STACK_B)
    in_a = _policy_actions(STACK_A)
    # If the parser found nothing, two empty sets would compare equal, so check
    # the sizes first.
    assert len(in_b) >= 100 and len(in_a) >= 100, (len(in_b), len(in_a))
    doc_b = _doc_rows(stack_b)
    assert doc_b == in_b, (
        f"Stack B - only in the document: {sorted(doc_b - in_b)} · only in the "
        f"template: {sorted(in_b - doc_b)}"
    )
    doc_a = doc_b | _doc_rows(stack_a)
    assert doc_a == in_a, (
        f"Stack A - only in the document: {sorted(doc_a - in_a)} · only in the "
        f"template: {sorted(in_a - doc_a)}"
    )
    assert _policy_actions(INFRA / "stack-c-viewer.yaml") == set(), (
        "Stack C creates no IAM roles (see §3 of the document)"
    )
    wild = sorted(a for a in in_a | in_b if not _IAM_ACTION.match(a))
    assert not wild, f"wildcard in an Allow action: {wild}"


# A size budget for Stack A. CloudFormation's inline limit is 51,200 bytes, and
# the manual installation command in the guide uploads the body without
# `--s3-bucket`, so the day the template exceeds the limit that command fails in
# front of whoever is installing. The budget leaves headroom to catch it first.
STACK_A_MAX_BYTES = 50_000


def test_stack_a_template_stays_under_the_body_size_budget() -> None:
    """Whether Stack A is within the 50,000 byte budget, which leaves headroom
    under the 51,200 byte limit. Over it, trim descriptions and comments rather
    than changing behaviour."""
    size = len(STACK_A.read_bytes())
    assert size <= STACK_A_MAX_BYTES, (
        f"Stack A is {size} B > {STACK_A_MAX_BYTES} B, so the guide's inline "
        "installation approaches the 51,200 B limit"
    )


def test_the_structure_build_also_reads_layer_one_state_for_the_snapshot() -> None:
    """Tunnel state "as of collection time": whether the structure build runs one
    resource-state `observe` right after `collect` and hands it to the renderer's
    adapter as `SNAPOBS`. Without it, a page with monitoring off shows a VPN as
    simply available even while its tunnels are down, which contradicts the
    console.

    It is the resource-state layer only (no `--metrics`, `--flow-logs` or
    `--utilization`, and `--no-sources`), because BuildRole has no metric or
    evidence permissions. A failure must not kill the structure page: `|| rm -f`
    lets the adapter render "could not confirm" instead.
    """
    text = BUILDSPEC.read_text()
    m = re.search(r"nettopo observe (.+?)\n(?:\s+(.+?)\n)?", text)
    assert m, "the structure build has no `nettopo observe`"
    cmd = " ".join(g for g in m.groups() if g)
    assert "--topology out/topology.json" in cmd and "--no-sources" in cmd, cmd
    assert not re.search(r"--(metrics|flow-logs|utilization)\b", cmd), (
        f"the structure build's observe goes beyond resource state: {cmd}"
    )
    assert "|| rm -f out/obs-snap.json" in cmd, (
        f"a failing observe kills the build, taking the structure page with it: {cmd}"
    )
    assert text.index("nettopo collect") < text.index("nettopo observe"), (
        "observe runs before collect, so there is no topology yet"
    )
    assert "SNAPOBS=../out/obs-snap.json" in text, (
        "the collection-time state is not handed to the adapter"
    )


_ENGINE_LOCK = INFRA.parent / "engine" / "requirements.lock"
_ENGINE_BUILD_LOCK = INFRA.parent / "engine" / "build-requirements.lock"


def test_collector_installs_engine_dependencies_from_the_hashed_lock() -> None:
    """The collector installs only the dependency versions that were verified:
    `pip install ./engine` would pull whatever boto3 and botocore are newest on
    the day of the build.

    Nothing about this is visible on screen, which is why it is tested. What the
    read-only guard (`awsguard.py`) hooks is botocore, so an hourly collection
    running on a botocore nobody verified means the guard is running against
    different code from the one we checked.
    """
    build = BUILDSPEC.read_text()
    # Installing straight into the build image's Python skips versions already
    # present, which bypasses hash verification, and overriding that with
    # `--ignore-installed` leaves the old install metadata behind so logs and
    # metadata report the old numbers. Install into a fresh virtualenv instead.
    venv = re.search(r"^\s+VENV: (/\S+)$", build, re.M)
    assert venv, "the buildspec has no absolute VENV path"
    assert '- python -m venv "$VENV"' in build
    assert (
        "$VENV/bin/python -m pip install --require-hashes -r engine/requirements.lock"
        " -r engine/build-requirements.lock" in build
    )
    # With build isolation left on, pip fetches the build backend (hatchling) at
    # whatever version is newest that day, and the build lock does nothing.
    assert (
        "$VENV/bin/python -m pip install --no-build-isolation --no-deps ./engine"
        in build
    )
    assert "--ignore-installed" not in build, (
        "a fresh virtualenv has nothing to override, so this option remaining "
        "means the build reverted to installing into the image's Python"
    )
    # Later steps must use the same environment, called by absolute path so a
    # different shell cannot change it. A bare name (`nettopo`, `python -c`) would
    # run the image's copy instead.
    assert not re.search(r"(?<![/\w])nettopo (collect|observe)\b", build), (
        "calls a nettopo outside the virtualenv"
    )
    assert not re.search(r"(?<![/\w])python (-c|-m pip)\b", build), (
        "calls a python outside the virtualenv"
    )
    assert "boto3.__version__" in build and "botocore.__version__" in build, (
        "the installed SDK versions are not recorded right after installation"
    )
    assert not re.search(r"pip install (--upgrade|-U)\b", build), (
        "the buildspec upgrades pip itself, which is an unpinned install"
    )
    assert not re.search(r"pip install \./engine\b", build), (
        "installs the engine with its dependencies, bypassing the lock"
    )
    pins = [
        line
        for line in _ENGINE_LOCK.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    build_pins = [
        line
        for line in _ENGINE_BUILD_LOCK.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert "hatchling" in {line.split("==", 1)[0] for line in build_pins}, build_pins
    # An empty lock would make the install above pin nothing and still pass, so
    # check the two that matter are present.
    names = {line.split("==", 1)[0] for line in pins}
    assert {"boto3", "botocore"} <= names, names
    for line in pins + build_pins:
        assert re.fullmatch(
            r"[a-z0-9-]+==[0-9][^ ]* --hash=sha256:[0-9a-f]{64}", line
        ), line


def test_collector_reads_the_buildspec_file_that_every_package_carries() -> None:
    """The build spec is `buildspec.yml`, outside the template, so every way of
    building a package has to include that file.

    Miss it in one of them and a package built that way fails collection at the
    first step, reading the spec after DOWNLOAD_SOURCE. The diagram then stays on
    the last successful revision, and the only way to learn why is to open the
    CodeBuild logs.
    """
    project = _resource(STACK_A.read_text(), "CollectorProject")
    assert "        BuildSpec: buildspec.yml\n" in project + "\n", (
        "CollectorProject does not read the package's buildspec.yml"
    )
    assert "version: 0.2" in BUILDSPEC.read_text()
    root = INFRA.parent
    deploy = (root / "deploy.sh").read_text()
    assert 'archive --format=zip -o "$zip" HEAD engine renderer buildspec.yml' in deploy
    for doc in (
        "README.md",
        "README.ko.md",
        "docs/install-guide.md",
        "docs/install-guide.en.md",
    ):
        text = (root / doc).read_text()
        archives = re.findall(
            r"git archive --format=zip -o out/nettopo-source\.zip HEAD ([^\n]+)", text
        )
        assert archives and all("buildspec.yml" in a.split() for a in archives), (
            doc,
            archives,
        )
    # The PACKAGE lists in the bundling and public-tree scripts are checked by
    # scripts/tests/test_package_lists.py; `scripts/` is not in the public tree.


def test_member_role_trust_hardening_is_optional_and_off_by_default() -> None:
    """The optional hardening of Stack B's trust policy: `ExternalId`
    (`sts:ExternalId`) and `PrincipalOrgId` (`aws:PrincipalOrgID`).

    Left empty, which is the default, the condition key must not be present at
    all: a condition on an empty value would deny every assume and report that
    whole account as lacking permission. Filled in, the value has to land in the
    StringEquals of the same trust statement; in a different statement it would
    leave a second, unconditional Allow.
    """
    text = STACK_B.read_text()
    for name, pattern in (
        ("ExternalId", "^$|^[-\\\\w+=,.@:/]{2,1224}$"),
        ("PrincipalOrgId", "^$|^o-[a-z0-9]{10,32}$"),
    ):
        param = re.search(rf"^  {name}:\n(.*?)(?=^  \S)", text, re.M | re.S)
        assert param, f"Stack B has no {name} parameter"
        assert re.search(r'^    Default: ""$', param.group(1), re.M), (
            f"{name} does not default to empty"
        )
        assert f'AllowedPattern: "{pattern}"' in param.group(1), name
    trust = _resource(text, "MemberRole").split("Policies:")[0]
    assert "- HasTrustStringCondition" in trust
    assert (
        'sts:ExternalId: !If [HasExternalId, !Ref ExternalId, !Ref "AWS::NoValue"]'
        in trust
    )
    assert (
        'aws:PrincipalOrgID: !If [HasPrincipalOrgId, !Ref PrincipalOrgId, !Ref "AWS::NoValue"]'
        in trust
    )
    assert (
        "HasTrustStringCondition: !Or [!Condition HasExternalId, !Condition HasPrincipalOrgId]"
        in text
    )
    assert trust.count("- Sid:") == 1, (
        "the trust policy has more than one statement, and an unconditional Allow "
        "left among them makes the hardening useless"
    )


def test_member_external_id_reaches_every_assume_path() -> None:
    """The hub enters a member role by two paths: the structure build (CodeBuild
    running collect and observe) and the 5-minute observation (StateFunction).

    Pass it to only one and just that path is denied: either the structure page
    renders while every state reads as unknown, or the other way round.
    """
    text = STACK_A.read_text()
    assert re.search(
        r'^  MemberExternalId:\n    Type: String\n    Default: ""\n', text, re.M
    )
    assert (
        "{Name: MEMBER_EXTERNAL_ID, Type: PLAINTEXT, Value: !Ref MemberExternalId}"
        in _resource(text, "CollectorProject")
    )
    assert "MEMBER_EXTERNAL_ID: !Ref MemberExternalId" in _resource(
        text, "StateFunction"
    )
    spec = BUILDSPEC.read_text()
    assert (
        "printf '%s' \"--external-id $MEMBER_EXTERNAL_ID\" > /tmp/external-id-arg"
        in spec
    )
    joined = re.sub(r"\\\n\s*", " ", spec)
    cmds = [
        ln
        for ln in joined.splitlines()
        if re.search(r"nettopo (collect|observe)\b", ln)
    ]
    assert len(cmds) == 2, cmds
    assert all("$(cat /tmp/external-id-arg)" in c for c in cmds), cmds


def test_bucket_cors_can_be_narrowed_to_the_viewer_origin() -> None:
    """CORS on the artifact bucket: left empty it behaves as it does today
    (`null` for `file://` plus any CloudFront domain), and given a Stack C
    address it narrows to that one.

    CORS is a rule browsers enforce, and without a presigned URL nothing can be
    read anyway, but narrowing it still closes the path where a presigned URL
    obtained through some other CloudFront distribution is used against this
    bucket, which is a question a reviewer will ask.
    """
    text = STACK_A.read_text()
    assert re.search(
        r'^  ViewerOrigin:\n    Type: String\n    Default: ""\n', text, re.M
    )
    # Stack C also accepts a custom domain, in which case the page's origin is
    # not a cloudfront.net address.
    assert 'AllowedPattern: "^$|^https://[a-z0-9][a-z0-9.-]*[a-z0-9]$"' in text
    cors = (
        _resource(text, "ArtifactBucket")
        .split("CorsRules:")[1]
        .split("NotificationConfiguration")[0]
    )
    assert (
        'AllowedOrigins: ["null", !If [HasViewerOrigin, !Ref ViewerOrigin, "https://*.cloudfront.net"]]'
        in cors
    )
    assert 'HasViewerOrigin: !Not [!Equals [!Ref ViewerOrigin, ""]]' in text


@pytest.mark.parametrize(
    "role", ["BuildRole", "ScheduleRole", "StateRole", "SignerRole"]
)
def test_service_roles_trust_only_their_own_account(role: str) -> None:
    """Roles assumed by a service principal (CodeBuild, EventBridge, Lambda) trust
    only requests from this account, which is what prevents a confused deputy.

    Nothing about this is visible on screen, which is why it is tested; a review
    found the condition on BuildRole alone.
    """
    trust = _resource(STACK_A.read_text(), role).split("Policies:")[0]
    assert 'StringEquals: {"aws:SourceAccount": !Ref "AWS::AccountId"}' in trust, (
        f"{role}'s trust policy has no SourceAccount condition"
    )


def test_artifact_bucket_statements_pin_the_bucket_owner() -> None:
    """Every Allow statement that reads or writes the artifact bucket requires the
    bucket to be owned by this account (`s3:ResourceAccount`).

    The bucket survives stack deletion (`Retain`), so if that name later belongs
    to someone else's account, an unconditional statement would write state and
    presigned URLs into their bucket. Miss one statement and only that path opens.
    """
    text = STACK_A.read_text()
    missing = []
    for role in ("BuildRole", "StateRole", "SignerRole"):
        body = _resource(text, role)
        for block in body.split("- Sid: ")[1:]:
            sid = block.split("\n", 1)[0]
            # Reading the deployment package bucket (ReadSourceZip) is the same
            # case, and more exposed: that bucket lives outside the stack, so its
            # name changes hands more easily.
            if "Effect: Allow" in block and (
                "ArtifactBucket" in block or sid == "ReadSourceZip"
            ):
                if '"s3:ResourceAccount": !Ref "AWS::AccountId"' not in block:
                    missing.append(f"{role}.{sid}")
    assert not missing, missing
    # If no statement were found, the loop above would run zero times and pass
    # silently, so count them too.
    assert (
        text.count(
            'Condition: {StringEquals: {"s3:ResourceAccount": !Ref "AWS::AccountId"}}'
        )
        >= 11
    )
    assert "- Sid: ReadSourceZip" in _resource(text, "BuildRole")


def test_state_lambda_bundle_has_no_known_vulnerable_urllib3() -> None:
    """The urllib3 in the state Lambda bundle is the same version as the engine's
    lock; 2.7.0 carried three pip-audit advisories."""
    root = INFRA.parent

    def pin(path: Path) -> str:
        return next(
            x for x in path.read_text().splitlines() if x.startswith("urllib3==")
        )

    assert pin(root / "infra" / "state-lambda" / "requirements.lock") == pin(
        root / "engine" / "requirements.lock"
    )
