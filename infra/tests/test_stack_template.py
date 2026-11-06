"""스택 템플릿의 「두 곳이 같이 맞아야 하는」 자리만 잰다 (실배포가 잡은 결함 2개, 2026-09-24).

🔴 **왜 이 검사가 따로 필요한가**: 두 결함은 pre-commit 16개 훅과 `cfn-lint` 를 **전부 통과**했다.
문법도 속성 이름도 맞았고, 틀린 것은 **두 곳의 값이 서로 어긋난 것**이었다:

| 결함 | 어긋난 두 곳 | 화면에서 보이는 일 |
|---|---|---|
| `MemberRoleName` 이 IAM 스코프만 바꿨다 | BuildRole 의 `sts:AssumeRole` 스코프 ↔ buildspec 이 엔진에 넘기는 `--role-name` | 어제는 보이던 계정이 오늘 통째로 회색 |
| 없는 키가 403 으로 온다 | StateRole 의 S3 권한 ↔ 핸들러의 `NoSuchKey` 분기 | 실시간을 켠 적 없는데 5분마다 DLQ 에 실패가 쌓인다 |

🔵 **이 검사가 재지 않는 것**: 템플릿을 YAML 로 파싱하지 않는다(`.venv` 에 pyyaml 이 없고, CFN 짧은
태그(`!Sub`·`!GetAtt`)를 읽으려면 로더를 따로 써야 한다). 구조 검증은 `uvx cfn-lint` 의 몫이다.
대신 **블록 단위로 좁혀서** 본다 — 파일 전체 grep 이면 다른 role 의 같은 액션에 속는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from internal_docs import skip_if_missing  # schema/tests — 경로는 conftest 가 붙인다

INFRA = Path(__file__).resolve().parent.parent
STACK_A = INFRA / "stack-a-collector.yaml"
STACK_B = INFRA / "stack-b-member-role.yaml"
# 수집기 빌드 명세 — 배포 패키지 루트에 실리고 CollectorProject 가 `BuildSpec: buildspec.yml` 로 읽는다(템플릿 본문 밖)
BUILDSPEC = INFRA.parent / "buildspec.yml"


def _resource(text: str, logical_id: str) -> str:
    """`  <LogicalId>:` 부터 같은 깊이의 다음 리소스까지. 파일 전체 grep 을 쓰지 않기 위한 것이다."""
    lines = text.splitlines()
    head = f"  {logical_id}:"
    start = lines.index(head)
    for offset, line in enumerate(lines[start + 1 :], start=start + 1):
        if line and not line.startswith("   ") and line.startswith("  "):
            return "\n".join(lines[start:offset])
    return "\n".join(lines[start:])


def _statement(text: str, sid: str) -> str:
    """`- Sid: <sid>` 한 문장만. 액션과 리소스가 **같은 문장 안에** 있는지를 재기 위한 것이다."""
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
    """`MemberRoleName` 은 **IAM 스코프와 실제로 넘기는 이름을 동시에** 바꿔야 한다.

    실측 2026-09-24: buildspec 에 `--role-name` 이 없어 엔진 기본값으로 들어갔고, 스코프만 좁혀져
    이 파라미터를 기본값에서 바꾸는 순간 그 계정이 36/36 `denied` 가 됐다(노드 263 → 231).
    """
    text = STACK_A.read_text()
    project = _resource(text, "CollectorProject")
    spec = BUILDSPEC.read_text()
    # 🔴 명령마다 잰다 — 구조 빌드는 `collect` 뒤에 1층 `observe` 도 돌린다(ADR 0006 O13). 한쪽에만 있으면
    #    「프로젝트 안 어딘가에 있다」로는 통과해 버려, 빠진 쪽이 엔진 기본 이름으로 들어가 거부된다(브레이크 `역할이름미전달`).
    joined = re.sub(r"\\\n\s*", " ", spec)
    cmds = [
        ln.strip()
        for ln in joined.splitlines()
        if re.match(r"\s*(?:\$VENV/bin/)?nettopo (collect|observe)\b", ln)
    ]
    assert cmds, "buildspec 에 nettopo collect/observe 명령이 없다"
    missing = [c for c in cmds if '--role-name "$MEMBER_ROLE_NAME"' not in c]
    assert not missing, (
        "buildspec 의 이 명령이 `--role-name` 을 넘기지 않는다 — 엔진 기본 이름으로 들어가는데 "
        f"BuildRole 의 sts:AssumeRole 은 MemberRoleName 으로 좁혀져 있다: {missing}"
    )
    assert (
        "{Name: MEMBER_ROLE_NAME, Type: PLAINTEXT, Value: !Ref MemberRoleName}"
        in project
    ), "환경변수 MEMBER_ROLE_NAME 이 MemberRoleName 파라미터에서 오지 않는다"
    scope = _statement(_resource(text, "BuildRole"), "EnterMemberAccounts")
    assert "role/${MemberRoleName}" in scope, (
        "BuildRole 의 sts:AssumeRole 스코프가 같은 파라미터를 쓰지 않는다 — "
        "두 값이 갈리면 「넓히려고 바꿨는데 좁아졌다」가 된다"
    )


def test_state_role_can_tell_a_missing_key_from_a_denied_one() -> None:
    """`s3:ListBucket` 이 없으면 S3 는 **없는 키를 403** 으로 답한다(존재 여부를 숨기려고).

    그러면 핸들러의 `NoSuchKey` 분기 — 「제어 키가 없으면 꺼짐」·「지난 관측이 없으면 첫 주기」 —
    가 **배포에서는 도달 불가능**해진다. 로컬·moto 는 404 를 주므로 다른 어떤 검사도 이걸 못 본다.
    """
    statement = _statement(
        _resource(STACK_A.read_text(), "StateRole"), "DistinguishMissingKeyFromDenied"
    )
    assert "Action: s3:ListBucket" in statement, (
        "StateRole 에 s3:ListBucket 이 없다 — 없는 키가 403 으로 와서 「꺼짐」 경로가 크래시한다"
    )
    assert "Resource: !GetAtt ArtifactBucket.Arn" in statement, (
        "키 목록 읽기가 산출물 버킷 하나로 한정돼 있지 않다"
    )


def test_member_role_trusts_all_four_hub_principals() -> None:
    """상태 단은 CodeBuild 와 **다른 role** 로 들어온다(신뢰 주체가 Lambda 라서 합칠 수 없다).

    빠지면 구조는 전부 보이는데 다른 계정의 「지금 흐르나」만 전부 「확인 불가」가 된다 —
    한 칸이 아니라 그 계정의 칸 전부라서 사람은 「기능이 안 된다」로 읽는다.
    """
    trust = _resource(STACK_B.read_text(), "MemberRole")
    for wanted, why in (
        ("AWSReservedSSO_${HubPermissionSetName}_*", "사람의 구조 수집(읽기 프로필)"),
        (
            "AWSReservedSSO_${HubPermissionSetNameExtra}_*",
            "사람의 2층 지표 확인(지표 프로필)",
        ),
        ("role/${HubCollectorRoleNamePattern}", "구조 단 CodeBuild"),
        ("role/${HubStateRoleNamePattern}", "상태 단 Lambda(5분 관측)"),
    ):
        assert wanted in trust, f"허브 주체가 빠졌다: {why}"


def test_member_role_trusts_no_person_unless_asked() -> None:
    """사람 경로(허브 IdC 퍼미션셋)는 **고객이 이름을 줄 때만** 신뢰 정책에 들어간다(클린룸 C7).

    화면에는 아무 차이가 없다 — 그래서 검사로 잰다. 예전 기본값은 개발 계정의 퍼미션셋 이름이라
    고객 계정에는 아무것도 매치하지 않는 신뢰 항목이 조용히 들어 있었다. 빈 이름을 문자열로 그대로
    끼우면 `AWSReservedSSO__*` 가 되어 이름이 밑줄로 시작하는 아무 퍼미션셋이나 멤버 계정에 들어온다.
    """
    text = STACK_B.read_text()
    param = re.search(r"^  HubPermissionSetName:\n(.*?)(?=^  \S)", text, re.M | re.S)
    assert param, "스택 B 에서 HubPermissionSetName 파라미터를 못 찾았다"
    assert re.search(r'^    Default: ""$', param.group(1), re.M), (
        "사람 경로 퍼미션셋 기본값이 비어 있지 않다 — 고객 계정 신뢰 정책에 사내 이름이 들어간다"
    )
    assert 'NoPermissionSet: !Equals [!Ref HubPermissionSetName, ""]' in text
    trust = _resource(text, "MemberRole")
    assert re.search(
        r'- !If\n\s+- NoPermissionSet\n\s+- !Ref "AWS::NoValue"\n'
        r"\s+- !Sub .*AWSReservedSSO_\$\{HubPermissionSetName\}_\*",
        trust,
    ), "빈 퍼미션셋 이름이 신뢰 정책에 문자열로 들어간다(`AWSReservedSSO__*`)"


def test_console_templates_carry_no_internal_names() -> None:
    """템플릿 = 고객이 콘솔에서 읽는 설명·기본값·출력이다 — 사내 프로필·퍼미션셋 이름이 없어야 한다.

    클린룸 C7: 멤버 역할의 출력 탭이 `--profile <사내 읽기 프로필> --region us-east-1` 을 그대로 보여 줬다.
    고객은 그 명령을 복사해 실행하고 「프로필이 없다」로 멈춘다. 2026-10-19 부터 템플릿은 영어 한 벌이라
    (한글본·생성기 제거) 세 장 전부를 잰다. 개발 계정 기록은 `infra/NOTES.md`·`infra/README.md` 에 둔다.
    """
    pattern = re.compile(
        r"\bant-(?:dev|deploy|metrics)\b|\bAnt[A-Z][A-Za-z]*(?:ReadOnly|Developer)\b"
    )
    files = sorted(INFRA.glob("stack-*.yaml"))
    assert len(files) == 3, f"템플릿이 세 장이 아니다: {[f.name for f in files]}"
    found = [
        f"{f.name}:{n}: {line.strip()[:80]}"
        for f in files
        for n, line in enumerate(f.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert not found, "콘솔용 템플릿에 사내 이름이 있다:\n" + "\n".join(found)


def test_evidence_detection_actions_match_the_engine_and_the_doc() -> None:
    """탐지 액션은 **세 곳**에 적혀 있다 — 코드·이 템플릿·권한 문서. 한 곳만 고치면 조용히 깨진다.

    화면에서 보이는 일: 고객이 흐름 로그를 켜 둔 계정인데 상단 알약이 「⚠ 근거 확인 못함 N종」에서
    내려오지 않는다. 원인은 정책에서 빠진 액션 한 줄이고, 그림은 정상으로 서 있으므로 아무 에러도
    나지 않는다(ADR 0009 D2: 모름을 꺼짐으로 적지 않기 때문에 오히려 조용하다).

    🔴 ECS·EKS 는 **목록과 상세가 다른 액션**이라 `PROBES` 의 대표 이름만 옮기면 절반이 빠진다.
    """
    from nettopo.sources import REQUIRED_ACTIONS

    statement = _statement(
        _resource(STACK_B.read_text(), "MemberRole"), "DetectOptionalEvidence"
    )
    # `- <service>:<Action>` 만. 🔴 같은 문장의 `Condition: !If` 목록도 `- ` 로 시작하므로
    # 모양으로 걸러낸다(그것까지 세면 액션이 아닌 것이 비교 대상에 들어온다).
    in_template = set(
        re.findall(r"^\s+- ([a-z0-9]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE)
    )
    assert in_template == set(REQUIRED_ACTIONS), (
        "스택 B 의 탐지 액션이 `nettopo.sources.REQUIRED_ACTIONS` 와 다르다"
    )

    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    # 🔴 다음 절(`### 1-5.`)에서 끊는다 — `## ` 에서만 끊으면 뒤 절의 표 행까지 이 절의 것으로 센다.
    section = doc.split("### 1-4. ")[1].split("\n## ")[0].split("\n### ")[0]
    rows = {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and ":" in line.split("`")[1]
    }
    assert rows == set(REQUIRED_ACTIONS), (
        "`docs/required-permissions.md` §1-4 표가 코드와 다르다 — 사람은 "
        "AccessDenied 를 보고도 어느 액션이 빠졌는지 알 수 없다"
    )
    # 문서의 정책 JSON 사본은 고객판(2026-10-23)에서 뺐다 — 정책 정본은 템플릿이고, 문서 표 전체와 템플릿의 대조는
    # `test_permission_doc_lists_exactly_the_template_actions` 가 한다.


def test_state_role_detects_evidence_in_the_hub_account_too() -> None:
    """허브 계정 자기 칸은 멤버 역할이 아니라 **상태 단 role** 로 읽는다 — 탐지 액션이 거기에도 있어야 한다.

    화면에서 보이는 일: 멤버 계정 칸은 「추가 근거 N종」으로 내려오는데 허브 계정 칸만 영원히
    「⚠ 근거 확인 못함」이다. 실측 2026-09-27: 허브 상태 문서의 8종이 3자리 전부 거부(스택 B 에만
    절이 있었고 위 검사도 스택 B 만 봤다).
    """
    from nettopo.sources import REQUIRED_ACTIONS

    statement = _statement(
        _resource(STACK_A.read_text(), "StateRole"), "DetectOptionalEvidence"
    )
    in_template = set(
        re.findall(r"^\s+- ([a-z0-9]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE)
    )
    assert in_template == set(REQUIRED_ACTIONS), (
        "스택 A 상태 단 role 의 탐지 액션이 `nettopo.sources.REQUIRED_ACTIONS` 와 다르다"
    )
    assert "aws:RequestedRegion" in statement, "탐지를 리전으로 좁히지 않았다"


def _collect_actions(logical_id: str, template: Path = STACK_A) -> set[str]:
    """한 role 에서 `ReadNetworkTopology` 문장의 수집 액션만.

    🔴 액션 이름에 하이픈이 든다(`network-firewall:ListFirewalls`) — 위 탐지 검사의 정규식
    (`[a-z0-9]+:`)은 그것을 **말없이 빼먹는다.** 여기서는 서비스 이름에 `-` 를 허용한다.
    """
    role = _resource(template.read_text(), logical_id)
    # CloudFront 는 리전 조건 밖 별 문장이다(이슈 #42 · 아래 검사) — 두 문장을 합쳐야 「같은 1층」이다.
    # 배치 D(2.10.0) — API Gateway(경로 ARN 으로 좁힌 별 문장) · Global Accelerator(us-west-2 별 문장)도 같은 1층이다
    statement = "\n".join(
        _statement(role, sid)
        for sid in (
            "ReadNetworkTopology",
            "ReadCloudFrontGlobal",
            "ReadApiGateway",
            "ReadGlobalAccelerator",
            # #129(2.11.0) — Cloud WAN(us-west-2 별 문장)도 같은 1층이다
            "ReadCloudWan",
            # 계정 이름(2026-10-28 · us-east-1 별 문장 · `engine/src/nettopo/account_name.py`)도 같은 1층이다
            # — 단 구조 수집 역할에만 있다(5분 관측 StateRole 은 이름을 읽지 않는다 · 최소 권한)
            "ReadAccountName",
        )
        if sid != "ReadAccountName" or f"- Sid: {sid}" in role
    )
    return set(
        re.findall(r"^\s+- ([a-z0-9-]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE)
    )


def test_cloudfront_is_read_outside_the_collection_region_condition() -> None:
    """🔴 CloudFront 는 수집 리전 조건 밖의 **별 문장**에서 us-east-1 로 고정해 읽는다(이슈 #42).

    CloudFront 는 글로벌 엔드포인트라 어느 리전을 주든 `aws:RequestedRegion` 이 us-east-1 로 평가된다
    (실측 2026-10-01: 수집 리전이 us-west-2·ap-northeast-2 인 허브는 자기 계정 배포 목록이 거부 ·
    목록에 us-east-1 이 있는 두 멤버 역할은 허용). 화면에서 보이는 일: 고객이 수집 리전에 us-east-1 을
    안 고르면 리전 레인은 다 정상인데 **CloudFront 카드만 통째로 없고** 상단에 「권한 없어 못 읽은
    계정」이 뜬다 — 배포가 없는 계정과 구별되지 않는다.
    """
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_A, "StateRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        regional = _statement(role, "ReadNetworkTopology")
        assert not re.search(r"^\s+- (cloudfront|route53):", regional, re.MULTILINE), (
            f"{logical_id}: CloudFront·Route 53 이 수집 리전 조건 문장 안에 있다"
        )
        cf = _statement(role, "ReadCloudFrontGlobal")
        actions = re.findall(r"^\s+- ([a-z0-9-]+:[A-Z][A-Za-z]+)$", cf, re.MULTILINE)
        # 2.8.0(P2-4) — Route 53 도 글로벌 엔드포인트(서명 리전 us-east-1)라 같은 문장이다. 수집 리전 문장에 넣으면 고객이
        #   us-east-1 을 안 고를 때 영역만 통째로 「권한 없음」이 된다(CloudFront 와 같은 일)
        assert actions == [
            "cloudfront:ListDistributions",
            "cloudfront:ListVpcOrigins",
            # 콘솔식 정보창(사용자 결정 2026-10-24) — 동작 표의 캐시 정책 이름 · 원본 표의 OAC 이름. CloudFront 라 같은 문장
            "cloudfront:ListCachePolicies",
            "cloudfront:ListOriginAccessControls",
            "route53:ListHostedZones",
            "route53:ListResourceRecordSets",
            "route53:GetHostedZone",
            # 2.12.0(ADR 0014) — CloudFront 범위 WAF 웹 ACL 도 us-east-1 에서만 부른다(`waf_web_acl_global` · `Spec.region`) — 같은 이유로 같은 문장
            "wafv2:ListWebACLs",
            "wafv2:GetWebACL",
        ], (logical_id, actions)
        assert re.search(r"^\s+aws:RequestedRegion: us-east-1$", cf, re.MULTILINE), (
            f"{logical_id}: CloudFront 문장이 us-east-1 고정이 아니다"
        )


def test_account_name_is_read_in_its_own_us_east_1_statement() -> None:
    """🔴 계정 이름(사용자 결정 2026-10-28 · `engine/src/nettopo/account_name.py`) = **별 문장** · 액션 하나 · us-east-1 고정.

    Account Management 는 글로벌 엔드포인트라 us-east-1 로 서명한다(CloudFront 와 같다). 수집 리전 문장에 넣거나 다른 리전으로
    고정하면 화면에서 보이는 일: 권한을 줬는데 계정 상자가 계속 숫자 id 다(거부는 실패가 아니라 이름 없음이라 배너도 없다 — 더
    안 보인다). 다른 문장에 섞이지 않는 것도 잰다 — 최소 권한 검토자가 「이 한 줄이 무엇을 여나」를 한 문장으로 읽게.
    """
    # 🔴 5분 관측(StateRole)은 이름을 읽지 않는다 — 구조 수집만 부른다(`cli._run_collect`) · 최소 권한(2026-10-28 사용자 결정)
    assert "account:GetAccountInformation" not in _resource(
        STACK_A.read_text(), "StateRole"
    )
    for template, logical_id in (
        (STACK_A, "BuildRole"),
        (STACK_B, "MemberRole"),
    ):
        role = _resource(template.read_text(), logical_id)
        statement = _statement(role, "ReadAccountName")
        # 대상 = 이 역할이 있는 계정 자신 하나("*" 아님 · AccountId 없이 부르면 자기 계정이다)
        assert (
            'Resource: !Sub "arn:${AWS::Partition}:account::${AWS::AccountId}:account"'
            in statement
        ), f"{logical_id}: 계정 이름 대상이 자기 계정 하나가 아니다"
        actions = re.findall(
            r"^\s+- ([a-z0-9-]+:[A-Z][A-Za-z]+)$", statement, re.MULTILINE
        )
        assert actions == ["account:GetAccountInformation"], (logical_id, actions)
        assert re.search(
            r"^\s+aws:RequestedRegion: us-east-1$", statement, re.MULTILINE
        ), f"{logical_id}: 계정 이름 문장이 us-east-1 고정이 아니다"
        assert role.count("account:GetAccountInformation") == 1, (
            f"{logical_id}: 계정 이름 액션이 다른 문장에도 있다"
        )


def test_waf_web_acls_are_read_in_the_collection_regions_and_cloudfront_scope_in_us_east_1() -> (
    None
):
    """🔴 2.12.0(ADR 0014) — WAF 웹 ACL 은 두 범위다. 리전 범위(목록 · 보호 대상) = 수집 리전 문장 · CloudFront 범위(목록 · 상세) = us-east-1 고정 문장.

    고객이 수집 리전에 us-east-1 을 안 넣으면: 리전 문장만 있으면 CloudFront 범위 web ACL 카드가 통째로 「권한 없음」이다(CloudFront 배포와 같은 일 ·
    이슈 #42). 리전 문장에서 `ListWebACLs` 가 빠지면 리전 범위 카드가 통째로 「권한 없음」이고, `ListResourcesForWebACL` 이 빠지면 web ACL 마다
    「확인하지 못함」이다(API Gateway 선이 없다). 와일드카드 없이 넷이다.
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
    """🔴 배치 D(2.10.0) — `apigateway:GET` 은 **경로 ARN** 으로만 준다(와일드카드 액션 · 리소스 `*` 없음).

    API Gateway 는 읽기·쓰기가 HTTP 동사(`GET`·`POST`…)로 갈리고 리소스가 경로다 — `Resource: "*"` 로 주면 API 키·정책·스테이지
    변수까지 읽힌다(최소 권한 아님). 수집기가 부르는 다섯 경로(REST 목록 · 리소스(embed=methods) · VPC 링크 · HTTP 목록 · 통합)만
    정확히 있어야 한다. 화면에서 보이는 일(빠뜨리면): API 카드는 서는데 VPC 링크 선이 없고 그 API 가 「일부 못 읽음」이다.
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
            f"{logical_id}: API Gateway 문장이 리소스 * 다"
        )
        assert "aws:RequestedRegion" in st, (
            f"{logical_id}: API Gateway 문장에 리전 조건이 없다"
        )
        regional = _statement(role, "ReadNetworkTopology")
        assert not re.search(
            r"^\s+- (apigateway|globalaccelerator):", regional, re.MULTILINE
        ), logical_id


def test_global_accelerator_is_read_in_us_west_2_only() -> None:
    """🔴 배치 D(2.10.0) — Global Accelerator 의 API 는 us-west-2 에만 있다(엔진 `Spec.region` · `test_relations_d.py` 가 클라이언트 리전을 잰다).

    수집 리전 문장에 넣으면 고객이 us-west-2 를 안 고를 때 가속기만 통째로 「권한 없음」이다(CloudFront 의 #42 와 같은 일).
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
    """🔴 #129(2.11.0) — Cloud WAN(Network Manager)의 API 는 us-west-2 에만 있다(엔진 `core_network.Spec.region` · `test_relations_129.py` 가 클라이언트 리전을 잰다).

    수집 리전 문장에 넣으면 고객이 us-west-2 를 안 고를 때 코어 네트워크만 통째로 「권한 없음」이다(Global Accelerator 와 같은 일).
    VPC Lattice 는 리전 서비스라 수집 리전 문장에 있다(여기 섞이면 안 된다).
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
        ), f"{logical_id}: VPC Lattice 가 수집 리전 문장에 없다"


def test_cloudfront_metrics_are_read_outside_the_collection_region_condition() -> None:
    """🔴 CloudFront **지표**도 수집 리전 조건 밖의 별 문장에서 us-east-1 로 고정해 읽는다(이슈 #42 후속).

    `AWS/CloudFront` 지표는 us-east-1 에만 있다. 실측 2026-10-05: 수집 리전이 us-west-2 인 허브에서
    `StateRole` 의 `GetMetricData` 가 AccessDenied 2건 — 화면에서 보이는 일: 모니터링을 켜면 다른 자원은
    숫자가 서는데 **그 계정 CloudFront 대역폭만 매 주기 「?」(못 읽음)** 다. 배포 목록은 #42 로 고쳤지만
    지표 읽기는 수집 리전 문장(`ReadFlowMetrics`)에만 있었다.
    """
    for template, logical_id in ((STACK_A, "StateRole"), (STACK_B, "MemberRole")):
        role = _resource(template.read_text(), logical_id)
        st = _statement(role, "ReadCloudFrontMetrics")
        actions = re.findall(
            r"(?:Action:|^\s+-) (cloudwatch:[A-Za-z]+)$", st, re.MULTILINE
        )
        assert actions == ["cloudwatch:GetMetricData"], (logical_id, actions)
        # us-east-1 = CloudFront · us-west-2 = Global Accelerator 사용량(`AWS/GlobalAccelerator` 는 us-west-2 에서만 조회 · 2026-10-26)
        assert re.search(
            r"^\s+aws:RequestedRegion: \[us-east-1, us-west-2\]$", st, re.MULTILINE
        ), f"{logical_id}: 고정 리전 지표 문장이 us-east-1 · us-west-2 가 아니다"


def test_both_stack_a_roles_read_the_same_resource_types() -> None:
    """🔴 구조 단(CodeBuild)과 상태 단(Lambda)은 **같은 1층을 읽는다** — 한쪽만 늘리면 조용히 갈린다.

    지금까지 이 사실은 `StateRole` 위의 주석 한 줄(「1층 N개 = BuildRole 과 같다」)로만 있었다.
    주석은 어긋나도 아무것도 FAIL 하지 않는다 — 실제로 `BuildRole` 에만 액션을 더한 적이 있다.

    화면에서 보이는 일: 시간 단위 구조 수집에는 나오는 카드가 5분 주기 관측에서는 **매 주기
    「확인 못함」**으로 덮인다(관측도 「무엇이 붙어 있나」를 다시 읽어야 지표를 물을 대상을 안다).
    구조와 상태가 서로 다른 그림을 말하므로 사람은 어느 쪽을 믿을지 알 수 없다.
    """
    # 계정 이름(`account:GetAccountInformation`)만 예외 — 구조 수집만 읽고 5분 관측은 읽지 않는다(최소 권한 · 2026-10-28).
    build = _collect_actions("BuildRole") - {"account:GetAccountInformation"}
    state = _collect_actions("StateRole")
    # 판별력: 정규식이 0개를 뽑아도 `set() == set()` 은 통과한다 = 미측정.
    assert len(build) > 14, f"수집 액션을 못 읽었다(정규식이 틀렸다): {sorted(build)}"
    assert build == state, (
        "스택 A 의 BuildRole 과 StateRole 이 읽는 1층이 다르다 — "
        f"BuildRole 에만: {sorted(build - state)} · StateRole 에만: {sorted(state - build)}"
    )


def test_member_role_reads_the_same_resource_types_as_the_hub() -> None:
    """🔴 멤버 역할(스택 B)도 허브(스택 A)와 **같은 1층을 읽는다** — 스택 A 에만 더하면 조용히 갈린다.

    실제로 그랬다: 수집기 4종의 액션이 스택 A 에만 들어가(#38) 실계정 수집에서 멤버 계정의 Auto
    Scaling·Network Firewall 이 거부됐다(2026-10-01). 화면에서 보이는 일: 허브 계정 칸에는 나오는
    ASG·방화벽이 **다른 계정 칸에만** 「⚠ 권한 없어 못 읽은 계정」으로 빠진다 — 고객 계정은 대개
    멤버 쪽이므로 「설치했는데 내 방화벽이 안 보인다」가 된다.
    """
    hub = _collect_actions("BuildRole")
    member = _collect_actions("MemberRole", STACK_B)
    assert len(member) > 14, f"수집 액션을 못 읽었다(정규식이 틀렸다): {sorted(member)}"
    assert member == hub, (
        "스택 B 멤버 역할과 스택 A 가 읽는 1층이 다르다 — "
        f"스택 A 에만: {sorted(hub - member)} · 스택 B 에만: {sorted(member - hub)}"
    )


def test_the_hub_grants_exactly_the_apis_the_collectors_call() -> None:
    """🔴 수집기가 부르는 API(서비스 파일의 `"svc:Action"` 이름)와 스택 A 가 주는 1층이 **같다**.

    위 두 검사는 역할끼리만 대조한다 — 셋이 **같이 빠지면** 아무것도 FAIL 하지 않는다. 수집기를 하나
    더하고 권한을 안 더하면(W2 에서 17개) 화면에서 보이는 일: 배포·수집이 성공하는데 새 카드가 하나도
    없고 상단에 「⚠ 권한 없어 못 읽은 계정」만 뜬다. 반대로 수집기가 안 부르는 액션이 남아 있으면 최소
    권한이 아니다(§2). API 이름은 `classify(exc, api)` 사유와 `Spec.api` 가 쓰는 글자 그대로다.
    """
    services = INFRA.parent / "engine" / "src" / "nettopo" / "services"
    # 계정 이름(2026-10-28)은 수집 종류가 아니라 계정 노드의 이름이라 `services/` 밖 파일 하나다 — 같이 읽는다
    called = {
        m
        for path in [*services.glob("*.py"), services.parent / "account_name.py"]
        for m in re.findall(r'"([a-z0-9-]+:[A-Z][A-Za-z]+)"', path.read_text())
    }
    assert len(called) > 30, f"서비스 파일에서 API 이름을 못 읽었다: {sorted(called)}"
    hub = _collect_actions("BuildRole")
    assert called == hub, (
        "수집기가 부르는 API 와 스택 A 의 1층이 다르다 — "
        f"권한 없음: {sorted(called - hub)} · 안 부르는데 줌: {sorted(hub - called)}"
    )


def _write_resources(role: str) -> set[str]:
    """role 블록 안에서 `s3:PutObject` 를 가진 문장들의 리소스 키(버킷 ARN 뒤 경로)."""
    keys: set[str] = set()
    for sid in re.findall(r"- Sid: (\w+)", role):
        statement = _statement(role, sid)
        if "s3:PutObject" in statement:
            keys |= set(re.findall(r"\$\{ArtifactBucket\.Arn\}/([^\"]+)\"", statement))
    return keys


def test_observer_cannot_write_its_own_switch_or_the_page() -> None:
    """관측 함수가 제어 키를 쓸 수 있으면 **자기 스위치를 켤 수 있다**(ADR 0005 D4 서버측 기본 꺼짐).

    서명을 5분 단으로 옮기면서(결정 2026-09-27) 「관측 role 에 PutObject 를 한 줄 더」가 가장 쉬운
    길이었다 — 그 길을 막는 것이 이 검사다. 서명은 별 role(`SignerRole`)이 한다.
    """
    writes = _write_resources(_resource(STACK_A.read_text(), "StateRole"))
    assert writes == {"snapshots/state/*", "state-latest.json"}, writes


def test_observer_reads_exactly_its_four_named_inputs() -> None:
    """관측 role 의 읽기 = 이름이 있는 키 4개 + **구조** 누적 프리픽스 하나. 그 밖의 과거 스냅샷은 열지 않는다.

    🔴 없어진 리소스의 모습(`change.was`)에 옛 구조가 필요해졌을 때 구조 단이 사본 하나
    (`structure-previous.json`)를 뒀다. 🆕 2026-10-17(QA B 2차 N-05): 실시간을 끈 사이 구조가 두 번 넘게 바뀌면
    사본도 다른 구조라 `snapshots/structure/*` 읽기를 **정확히 그 프리픽스만** 더했다 — 상태 누적(`snapshots/state/*`)·
    버킷 전체(`*`)·`index.html` 은 여전히 0 이다(이 검사가 그 길을 막는다).
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
    # 구조 단이 사본을 **포인터를 덮기 전에** 만든다 — 순서가 바뀌면 사본이 새 구조와 같아진다.
    text = BUILDSPEC.read_text()
    assert text.index('structure-previous.json" --sse AES256') < text.index(
        '"s3://$ARTIFACT_BUCKET/structure-latest.json" --sse AES256'
    )


def test_signer_signs_and_writes_exactly_the_three_keys() -> None:
    """서명 role 의 쓰기 = 주소 칸이 있는 `index.html` + 화면이 쓰는 제어 키. 그 밖은 0이다.
    읽기 = 서명 전 원본(`unsigned/index.html` · 이 함수가 실제로 읽는 유일한 키) + 서명 URL 4개의 권한(제어 키 읽기 = 따라 보기 · 2026-10-28).

    🔴 이 role 의 권한이 곧 화면에 구워지는 URL 의 권한이다 — 여기가 넓어지면 화면(= 파일을 가진
    누구나)이 쓸 수 있는 곳이 넓어진다.
    """
    role = _resource(STACK_A.read_text(), "SignerRole")
    assert _write_resources(role) == {"index.html", "control/desired.json"}
    reads = set()
    for sid in re.findall(r"- Sid: (\w+)", role):
        statement = _statement(role, sid)
        if "s3:GetObject" in statement:
            reads |= set(re.findall(r"\$\{ArtifactBucket\.Arn\}/([^\"]+)\"", statement))
    # 제어 키 GetObject = 연장 PUT(`If-Match`)의 판정 권한이다. 없으면 연장이 전부 403(실측 2026-09-28).
    assert reads == {
        "unsigned/index.html",
        "index.html",
        "state-latest.json",
        "control/desired.json",
    }, reads
    # 원본은 **읽기만** 한다 — 원본을 고치면 다음 주기가 고친 판에 또 서명한다(빌드만 원본을 쓴다).
    assert "s3:PutObject" not in _statement(role, "ReadUnsignedPage")
    conditional = _statement(role, "SignControlConditionalWrite")
    assert "Action: s3:GetObject" in conditional
    assert "s3:PutObject" not in conditional
    # URL 로 오는 요청도 없는 키를 403 으로 받는다 — 켠 직후 「첫 관측을 기다리는 중」이 「읽지 못함」이 된다.
    listing = _statement(role, "ScreenSeesMissingStateAs404")
    assert "Action: s3:ListBucket" in listing
    assert "Resource: !GetAtt ArtifactBucket.Arn" in listing
    assert "sts:AssumeRole\n" not in role.split("Policies:")[1], (
        "서명 함수는 대상 계정에 들어가지 않는다"
    )
    function = _resource(STACK_A.read_text(), "SignerFunction")
    assert "Role: !GetAtt SignerRole.Arn" in function
    assert "Handler: signer.handler" in function


def test_signer_is_called_only_by_its_two_rules() -> None:
    """인바운드 엔드포인트 0(브라우저가 부르는 백엔드를 두지 않는다) — 부르는 곳이 권한 2개에 **이름으로** 나타나야 한다."""
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
    """인바운드 엔드포인트 0(브라우저가 부르는 백엔드를 두지 않는다) — 관측 함수를 부르는 것은 5분 주기와 「제어 키를 쓰면 바로」 둘뿐이다.

    ADR 0004 D2-1 조건 3 의 검사(전에는 없었다). 깨우기 규칙은 **제어 키 · 새 구조 포인터 두 키**에만 맞아야 한다
    (새 구조 포인터 = QA B P3 · 2026-10-14) — 패턴이 넓어지면 관측 함수가 자기가 쓰는 `state-latest.json` 에 깨어나
    스스로를 부르는 고리가 생긴다.
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
    # 🔴 깨우는 키는 **정확히 둘**이다 — 제어 키 · 새 구조 포인터(QA B P3 · 2026-10-14). 둘 다 관측 함수가 쓸 수 없는
    #    키라 자기 자신을 부르는 고리가 없다(`state-latest.json` 이 끼면 고리다).
    assert "object: {key: [control/desired.json, structure-latest.json]}" in wake, (
        "깨우기가 제어 키 · 새 구조 포인터 두 키와 다르다"
    )
    assert "Arn: !GetAtt StateFunction.Arn" in wake
    # 버킷은 이벤트를 **보내기만** 한다 — 상태 단이 없으면 보내지도 않는다.
    bucket = _resource(text, "ArtifactBucket")
    assert "EventBridgeConfiguration: {EventBridgeEnabled: true}" in bucket
    assert "- StateTierEnabled" in bucket.split("NotificationConfiguration:")[1][:80]


def test_cors_opens_only_the_two_methods_to_the_file_origin() -> None:
    """CORS 는 권한을 주지 않지만, 넓히면 보안 검토의 지적 항목이 된다 — 메서드·출처를 좁게 둔다.

    출처는 두 개다: `file://`(`null`) · 스택 C 링크로 연 화면(`https://*.cloudfront.net`, ADR 0003 D2-2 —
    `ViewerOrigin` 을 주면 그 배포 주소 하나로 좁아진다 · `test_bucket_cors_can_be_narrowed_to_the_viewer_origin`).
    """
    bucket = _resource(STACK_A.read_text(), "ArtifactBucket")
    # 주석에도 `AllowedOrigins: [...]` 가 적혀 있다 — 주석 아닌 줄만 읽는다.
    line = next(
        x for x in bucket.splitlines() if x.strip().startswith("AllowedOrigins: [")
    )
    # 출처 줄은 통째로 고정한다 — 중첩 `!If` 라 쉼표로 쪼개 집합을 만들 수 없다. 늘리는 것도 검토 대상이다.
    assert line.strip() == (
        'AllowedOrigins: ["null", !If [HasViewerOrigin, !Ref ViewerOrigin, "https://*.cloudfront.net"]]'
    ), line
    assert "AllowedMethods: [GET, PUT]" in bucket
    assert "ExposedHeaders: [ETag]" in bucket
    # 🔴 `if-match` 가 빠지면 화면의 연장 PUT 이 사전 요청에서 막혀 화면이 옛 동작(켤 때 4시간 한 번)으로
    #    떨어진다 — **창을 닫아도 4시간 동안 서버가 돈다**(ADR 0005 D4 보강). `ETag` 노출이 빠져도 같다.
    #    하네스는 사전 요청을 못 보므로(Playwright `route` 실측) 이 헤더는 여기서만 잰다. 늘리는 것도 검토
    #    대상이다 — 집합으로 고정한다.
    headers = bucket.split("AllowedHeaders: [")[1].split("]")[0]
    assert {h.strip() for h in headers.split(",")} == {
        "content-type",
        "if-none-match",
        "if-match",
    }
    assert bucket.count("- Id: ") == 3, (
        "CORS 규칙 1개 + 수명 규칙 2개(index.html · unsigned/)여야 한다"
    )


def test_build_bakes_csp_connect_to_the_bucket_origin() -> None:
    """산출물 CSP 의 `connect-src` 는 빌드가 굽는다(렌더러 검사 117). 서명 함수가 굽는 주소
    (가상 호스트 + 리전 엔드포인트, `state-lambda/signer.py:94-106`)와 **같은 오리진**이어야 한다 —
    빠지거나 어긋나면 실시간 스위치를 켜도 요청이 브라우저에서 막히고 화면은 「상태를 읽지 못했다」.
    에러는 받은 사람의 브라우저 콘솔에만 남는다.

    🆕 2026-10-17: 서명 함수가 **있는 스택에서만** 연다. 없는 스택은 칸이 영영 비므로 `'none'` 이다 — 열어 두면
    화면이 「서명 전 화면 파일 — 잠시 뒤 다시 받으세요」(버킷 CSP + 빈 칸)를 거짓으로 영원히 말한다."""
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
    """5분마다 새 판이 쌓인다(하루 288판). 수명 규칙이 **`index.html` 만** 겨눠야 한다 —
    접두어를 비우면 누적 스냅샷(ADR 0004)의 옛 판까지 하루 뒤 사라진다."""
    bucket = _resource(STACK_A.read_text(), "ArtifactBucket")
    rule = bucket.split("LifecycleConfiguration:")[1]
    assert "Prefix: index.html\n" in rule
    assert "NoncurrentVersionExpiration: {NoncurrentDays: 1}" in rule
    assert "ExpirationInDays" not in rule, "현재 판은 지우지 않는다"


def test_build_never_publishes_an_unsigned_page_where_the_signer_exists() -> None:
    """사람이 받는 `index.html` 에 서명 전 판(주소 칸이 빈 판)이 한 순간도 없어야 한다(QA B 2차 2026-10-17).

    예전에는 빌드가 `index.html` 을 빈 칸으로 덮고 서명 함수가 몇 초 뒤 고쳤다 — QA 실측 64빌드에서 2~9초(중앙 6초).
    그 사이 받은 화면·«최신 화면 열기» 로 넘어간 화면은 모니터링을 켤 수 없었고 에러는 어디에도 없었다.
    지금: 실시간 채널이 있으면 빌드는 `unsigned/index.html` 에만 올리고(서명 함수가 게시) · BuildRole 은 그 스택에서
    `index.html` 쓰기를 **명시적으로 거부**당한다(buildspec 이 옛 모양으로 돌아가도 창이 다시 열리지 않는다).
    실시간 채널이 없는 스택에는 서명 함수가 없으므로 빌드가 `index.html` 을 직접 쓴다(그때 CSP 는 `'none'`).
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
    # IAM 이 같은 약속을 지킨다 — 상태 단(= 서명 함수)이 있는 스택에서만 거부한다.
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
    """`index.html` 의 작성자가 서명 함수 하나뿐이라 순서를 지키는 것은 **직렬 실행**이다(조건부 쓰기를 뗀 대가).

    두 실행이 겹치면 먼저 원본을 읽은 실행(옛 구조)이 나중에 써서 새 구조를 옛 구조로 되돌릴 수 있다.
    """
    function = _resource(STACK_A.read_text(), "SignerFunction")
    assert "\n      ReservedConcurrentExecutions: 1\n" in function


def test_signer_runs_right_after_a_successful_build_of_this_project() -> None:
    """빌드는 주소 칸을 비운 서명 전 원본(`unsigned/index.html`)을 새로 굽는다. 이 규칙이 다른 프로젝트를 겨누면
    **매시 빌드 직후 최대 5분 동안** `index.html` 이 옛 구조로 남는다 — 에러는 어디에도 없다."""
    rule = _resource(STACK_A.read_text(), "SignerAfterBuildRule")
    assert "build-status: [SUCCEEDED]" in rule
    assert "project-name: [!Ref CollectorProject]" in rule
    assert "Arn: !GetAtt SignerFunction.Arn" in rule


# ── 흐름 로그 판독 권한 (S5 U9 · `docs/required-permissions.md` §1-3) ─────────────────────────────
# 🔴 두 스택이 **같은 규칙**을 따로 적는다(스택 A = 허브 칸 · 스택 B = 멤버 칸). 한쪽만 고치면 그 쪽 계정
#    칸만 「켜져 있지만 못 읽음 — 권한」이 되거나, 더 나쁘게는 한쪽만 앱 로그를 연다.
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
    """`Parameters:` 아래 `  <name>:` 한 블록. 리소스와 이름이 겹치지 않게 파라미터 절 안에서만 찾는다."""
    section = text.split("\nParameters:\n")[1].split("\nConditions:\n")[0]
    return _resource("\n" + section, name)


def _pattern(text: str, name: str) -> re.Pattern[str]:
    raw = re.search(r'AllowedPattern: "(.*)"$', _parameter(text, name), re.MULTILINE)
    assert raw, f"{name} 에 AllowedPattern 이 없다 — `*` 가 그대로 정책에 들어간다"
    return re.compile(raw.group(1).replace("\\\\", "\\"))


def test_flow_log_reading_is_off_by_default_in_both_stacks() -> None:
    """켜기 전 권한은 **지금과 같아야 한다** — 기본값 꺼짐 + 정책 3장이 전부 조건부 자원.

    화면에서 보이는 일(깨졌다면): 스택을 갱신만 했는데 고객 보안팀이 「로그 읽기 권한이 새로 생겼다」를
    먼저 발견한다. 우리는 「파라미터로 켤 때만」이라고 안내했다.
    """
    for stack, names in _FLOW_POLICIES.items():
        text = stack.read_text()
        param = _parameter(text, "FlowLogReading")
        assert "Default: DISABLED" in param, (
            f"{stack.name}: FlowLogReading 기본값이 꺼짐이 아니다"
        )
        assert "AllowedValues: [ENABLED, DISABLED]" in param
        for name, condition in zip(names, _FLOW_CONDITIONS, strict=True):
            assert f"Condition: {condition}\n" in _resource(text, name) + "\n", (
                f"{stack.name}: {name} 이 {condition} 조건 없이 만들어진다"
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
            # 🔴 빈 값인데 정책이 생기면 `!Split` 이 `log-group:*` 를 만든다 = **모든 로그 그룹**이 열린다.
            assert (
                "!Condition FlowLogsOn" in line
                and f'!Equals [!Ref {param_name}, ""]' in line
            ), f"{stack.name}: {prefixed} 가 빈 값·꺼짐을 둘 다 거르지 않는다"
    # 스택 A 는 상태 단이 없으면 붙일 역할 자체가 없다(StateRole 이 조건부 자원이다).
    a_conditions = STACK_A.read_text().split("\nConditions:\n")[1]
    assert "FlowLogsOn: !And [!Condition StateTierEnabled," in a_conditions, (
        "스택 A: 상태 단 없이 흐름 로그 정책이 StateRole 을 가리킨다 — 배포가 실패한다"
    )


def test_flow_log_actions_match_the_reader_and_the_doc() -> None:
    """흐름 로그 액션은 **네 곳**에 적혀 있다 — 판독기 코드·스택 A·스택 B·권한 문서 §1-3."""
    from nettopo.flowlogs import REQUIRED_ACTIONS

    for stack, names in _FLOW_POLICIES.items():
        text = stack.read_text()
        found = set()
        for name in names:
            found |= set(_ACTION.findall(_resource(text, name)))
        assert found == set(REQUIRED_ACTIONS), (
            f"{stack.name} 흐름 로그 정책의 액션이 `nettopo.flowlogs.REQUIRED_ACTIONS` 와 다르다: "
            f"빠짐 {sorted(set(REQUIRED_ACTIONS) - found)} · 더 있음 {sorted(found - set(REQUIRED_ACTIONS))}"
        )
    # 판별력: 켜지 않은 역할에는 로그 **내용** 액션이 없어야 한다(인라인 정책에 섞이면 조건이 무의미하다).
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
                f"{stack.name} {role} 에 {action} 이 조건 없이 있다"
            )

    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    section = doc.split("### 1-3. ")[1].split("### 1-4. ")[0]
    rows = {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and ":" in line.split("`")[1]
    }
    assert rows == set(REQUIRED_ACTIONS), (
        "`docs/required-permissions.md` §1-3 표가 판독기와 다르다"
    )


def test_log_group_prefixes_reject_wildcards_and_empty_elements() -> None:
    """로그 그룹 목록은 스택이 **입력 단계에서** 거른다 — `*` 한 글자가 앱 로그 전부를 연다."""
    for stack in _FLOW_POLICIES:
        text = stack.read_text()
        groups = _pattern(text, "FlowLogGroupNamePrefixes")
        for ok in ("", "/vpc/flow", "vpc-flow-logs", "/a/b,/c/d", "flow#1.x_y-z"):
            assert groups.fullmatch(ok), (
                f"{stack.name}: 정상 그룹 이름 {ok!r} 을 거부한다"
            )
        for bad in ("*", "/", "/aws/*", "a,,b", "a,", ",a", "/,a", "a b", "a*b"):
            assert not groups.fullmatch(bad), (
                f"{stack.name}: {bad!r} 를 받아들인다 — 정책이 넓어진다"
            )
        buckets = _pattern(text, "FlowLogCentralBucketArns")
        for ok in (
            "",
            "arn:aws:s3:::log-archive",
            "arn:aws:s3:::a-b.c,arn:aws-cn:s3:::xyz",
        ):
            assert buckets.fullmatch(ok), (
                f"{stack.name}: 정상 버킷 ARN {ok!r} 을 거부한다"
            )
        for bad in (
            "*",
            "arn:aws:s3:::*",
            "arn:aws:s3:::b/*",
            "arn:aws:s3:::b,",
            "log-archive",
        ):
            assert not buckets.fullmatch(bad), f"{stack.name}: {bad!r} 를 받아들인다"
    # 두 스택이 같은 입력 규칙이어야 한다 — 한쪽만 느슨하면 그 쪽 계정만 넓어진다.
    a, b = STACK_A.read_text(), STACK_B.read_text()
    for name in ("FlowLogGroupNamePrefixes", "FlowLogCentralBucketArns"):
        assert _pattern(a, name).pattern == _pattern(b, name).pattern, (
            f"{name} 입력 규칙이 두 스택에서 다르다"
        )


def test_own_account_flow_reads_touch_only_flow_log_keys() -> None:
    """자기 계정 버킷은 **흐름 로그 키 두 모양만** — 같은 계정의 산출물·앱 데이터 객체는 못 읽는다."""
    for stack, (find, _, _) in _FLOW_POLICIES.items():
        policy = _resource(stack.read_text(), find)
        read = _statement(policy, "FlowLogOwnObjectRead")
        for key in _OWN_FLOW_KEYS:
            assert key in read, f"{stack.name}: GetObject 에 흐름 로그 키 {key} 가 없다"
        assert 's3:::*"' not in read, f"{stack.name}: GetObject 가 모든 객체를 연다"
        assert 's3:ResourceAccount: !Ref "AWS::AccountId"' in read
        listing = _statement(policy, "FlowLogOwnBucketList")
        assert 's3:ResourceAccount: !Ref "AWS::AccountId"' in listing
        for prefix in (
            '"*AWSLogs/${AWS::AccountId}/vpcflowlogs/*"',
            '"*AWSLogs/aws-account-id=${AWS::AccountId}/aws-service=vpcflowlogs/*"',
        ):
            assert prefix in listing, f"{stack.name}: 목록 접두 {prefix} 가 없다"
        assert 's3:ResourceAccount: !Ref "AWS::AccountId"' in _statement(
            policy, "FlowLogOwnBucketMeta"
        )


def test_log_group_reads_are_named_and_bound_to_this_account() -> None:
    """로그 **내용**은 준 그룹 이름으로만 · 자기 계정으로만 — 「전체 로그 그룹」은 앱 로그를 연다."""
    for stack, (find, groups, _) in _FLOW_POLICIES.items():
        text = stack.read_text()
        policy = _resource(text, groups)
        allow = _statement(policy, "FlowLogReadGroups")
        assert "!Ref FlowLogGroupNamePrefixes" in allow, (
            f"{stack.name}: 그룹 이름을 파라미터에서 받지 않는다"
        )
        assert "log-group:*" not in allow, f"{stack.name}: 로그 그룹 전체를 연다"
        assert "aws:RequestedRegion" in allow, f"{stack.name}: 리전으로 좁히지 않았다"
        deny = _statement(policy, "FlowLogReadGroupsOwnAccountOnly")
        assert "Effect: Deny" in deny, (
            f"{stack.name}: Allow 의 계정 자리 `*` 를 묶는 Deny 가 없다"
        )
        assert (
            'NotResource: !Sub "arn:${AWS::Partition}:logs:*:${AWS::AccountId}:log-group:*"'
            in deny
        )
        assert set(_ACTION.findall(deny)) == set(_ACTION.findall(allow)), (
            f"{stack.name}: Deny 가 Allow 의 액션을 다 덮지 않는다"
        )
        # `log-group:*` 가 나오는 자리는 둘뿐이다 — 찾기(목록)와 위 Deny.
        find_stmt = _statement(_resource(text, find), "FlowLogFindGroups")
        assert "Action: logs:DescribeLogGroups\n" in find_stmt + "\n"
        everywhere = text.count("log-group:*")
        assert (
            everywhere
            == find_stmt.count("log-group:*") + deny.count("log-group:*")
            == 2
        ), f"{stack.name}: `log-group:*` 가 찾기·Deny 밖에도 있다"


def test_central_buckets_yield_only_this_accounts_flow_keys() -> None:
    """다른 계정 버킷에서도 **자기 계정 흐름 로그 키만** — 로그 아카이브의 남의 로그·CloudTrail 은 못 읽는다."""
    for stack, (_, _, central) in _FLOW_POLICIES.items():
        policy = _resource(stack.read_text(), central)
        assert "!Ref FlowLogCentralBucketArns" in _statement(
            policy, "FlowLogCentralObjectRead"
        )
        deny = _statement(policy, "FlowLogCentralOwnKeysOnly")
        assert "Effect: Deny" in deny and "Action: s3:GetObject\n" in deny + "\n"
        for key in _OWN_FLOW_KEYS:
            assert key in deny, f"{stack.name}: Deny 의 예외에 {key} 가 없다"
        # 🔴 계정 조건이 빠지면 이 Deny 가 **자기 산출물 버킷 읽기**(제어 키·구조 스냅샷)까지 막는다
        #    = 실시간 모니터링 전체가 멈춘다.
        assert (
            'StringNotEquals:\n                s3:ResourceAccount: !Ref "AWS::AccountId"'
            in deny
        ), f"{stack.name}: Deny 에 다른 계정 조건이 없다"


def test_state_function_gets_the_same_switch_the_policies_use() -> None:
    """핸들러 스위치(`FLOW_LOGS`)와 권한 조건(`FlowLogsOn`)은 **같은 파라미터**에서 온다."""
    env = _resource(STACK_A.read_text(), "StateFunction")
    assert "FLOW_LOGS: !Ref FlowLogReading" in env, (
        "상태 함수가 흐름 로그 스위치를 받지 않는다 — 권한만 생기고 한 번도 읽지 않는다"
    )


# CloudFormation 이 본문(`TemplateBody`)으로 받는 템플릿 상한. 넘으면 S3 에 올려 URL 로 줘야 한다.
_TEMPLATE_BODY_LIMIT = 51_200


# 배포 명령이 적힌 안내서 두 장 — 개발 계정 기록(사내 프로필)과 고객용 설치 안내서(C7).
_INSTALL_GUIDE = INFRA.parent / "docs" / "install-guide.md"
_INSTALL_GUIDE_EN = INFRA.parent / "docs" / "install-guide.en.md"
_INSTALL_GUIDES = [_INSTALL_GUIDE, _INSTALL_GUIDE_EN]
# 개발 계정 기록(infra/README.md)은 공개 트리(scripts/make-public-tree.sh)에 없다 — 그 칸만 **있을 때만** 잰다
# (`internal_docs`). 고객 설치 안내서는 어느 트리에서든 반드시 있어야 한다(없으면 읽다가 실패한다).
_DEPLOY_DOCS = [INFRA / "README.md", *_INSTALL_GUIDES]
# 스택 A 템플릿은 영어 한 벌이다(2026-10-19 — 한글본·`infra/en/` 생성본 제거). 지금은 본문 한도 안이라
# `--s3-bucket` 없이 올라가지만, 다시 한도를 넘으면 안내서 명령이 S3 를 거치도록 이 검사가 잡는다.
# 🔴 한도 안인 동안 크기 가지는 잠들어 있다 — 브레이크(`안내서템플릿본문으로` · `빠른시작템플릿본문으로`)가
#    크기 조건을 켜서 그 가지를 잰다(#131). 크기 조건 줄을 고치면 그 브레이크 앵커도 같이 고친다.
_STACK_A_TEMPLATES = ("infra/stack-a-collector.yaml",)


def _sh_lines(doc: Path) -> list[str]:
    """안내서의 sh 블록을 줄 이음(`\\`)까지 합쳐 한 명령 한 줄로 꺼낸다."""
    text = doc.read_text()
    lines = []
    for block in re.findall(r"```sh\n(.*?)```", text, flags=re.S):
        lines += re.sub(r"\\\n\s*", " ", block).splitlines()
    return lines


def _readme_cfn_commands(doc: Path = INFRA / "README.md") -> list[str]:
    """안내서의 sh 블록에서 CloudFormation 배포 명령을 꺼낸다."""
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
    """안내서의 배포 명령이 **템플릿 크기와 같이** 맞아야 한다 — 한도를 넘는 템플릿은 S3 를 거친다.

    실측 2026-09-29: 흐름 로그 파라미터가 들어간 뒤 스택 A 가 58,600 B 가 됐다. 안내서의
    `deploy --template-file` 두 줄은 그대로면 CloudFormation 이 거절한다 — 문서만 보고 따라 한
    사람은 배포 첫 단계에서 멈춘다. pre-commit·cfn-lint 는 이 어긋남을 못 본다(템플릿은 멀쩡하다).
    """
    if doc == INFRA / "README.md":
        skip_if_missing("infra/README.md")
    commands = _readme_cfn_commands(doc)
    by_template = {}
    for command in commands:
        m = re.search(r"--template-(?:file |body file://)(\S+)", command)
        if m:
            by_template.setdefault(m.group(1), []).append(command)
    # 🔴 파서가 명령을 못 찾으면 아래 반복이 0회라 조용히 통과한다 — 찾은 수부터 잰다.
    stack_a_commands = sum(len(by_template.get(t, [])) for t in _STACK_A_TEMPLATES)
    assert stack_a_commands >= 2, (
        f"안내서에서 스택 A 배포 명령을 못 찾았다(찾은 것 {sorted(by_template)})"
    )
    for template, found in by_template.items():
        size = (INFRA.parent / template).stat().st_size
        if size <= _TEMPLATE_BODY_LIMIT:
            continue
        for command in found:
            assert "--template-body" not in command and "--s3-bucket" in command, (
                f"{template} 는 {size} B 로 본문 한도 {_TEMPLATE_BODY_LIMIT} B 를 넘는데 안내서 명령이 "
                f"S3 를 거치지 않는다 — CloudFormation 이 거절한다: {command[:120]}"
            )


@pytest.mark.parametrize("guide", _INSTALL_GUIDES, ids=lambda d: d.name)
def test_install_guide_names_profile_and_region_and_no_internal_names(
    guide: Path,
) -> None:
    """고객용 설치 안내서(C7)는 고객이 그대로 복사해 실행한다 — 두 가지가 새면 안 된다.

    ① 모든 `aws` 명령이 `--profile`·`--region` 을 적는다. 셸 기본 프로필·리전으로 조용히 돌면 다른 계정·리전에
       스택이 선다(고객 셸 기본값은 우리가 모른다). ② 사내 프로필·퍼미션셋 이름이 없다 — 고객 계정에는 그 이름이
       없어 명령이 실패하거나, 스택 B 에 넘기면 존재하지 않는 사람 경로를 신뢰한다(2026-10-01 C7).
    """
    lines = _sh_lines(guide)
    # `aws configure …`(sso 로그인 설정 등)는 로컬 설정만 쓰고 수집 API 를 부르지 않는다 — 리전 개념이 없어 예외다.
    aws_lines = [
        line
        for line in lines
        if re.search(r"(?:^|\$\()\s*aws ", line)
        and not re.search(r"\baws configure\b", line)
    ]
    # 🔴 파서가 명령을 못 찾으면 아래가 0회라 조용히 통과한다 — 찾은 수부터 잰다(1~6단계 합계).
    assert len(aws_lines) >= 12, (
        f"안내서에서 aws 명령을 {len(aws_lines)}개밖에 못 찾았다"
    )
    for line in aws_lines:
        assert "--profile " in line and "--region " in line, (
            f"프로필·리전이 빠진 명령: {line.strip()[:120]}"
        )
    text = guide.read_text()
    internal = re.findall(
        r"\bant-(?:dev|deploy|metrics)\b|\bAnt[A-Z][A-Za-z]*(?:ReadOnly|Developer)\b",
        text,
    )
    assert not internal, f"고객 안내서에 사내 이름이 있다: {sorted(set(internal))}"


# 루트 README 두 판(영어 = README.md · 한글 = README.ko.md)의 「빠른 시작」= 안내서 1~3단계의 요약. 고객이 첫 번째로 보는 명령이다.
_ROOT_READMES = [INFRA.parent / "README.md", INFRA.parent / "README.ko.md"]
_INTERNAL_NAME = re.compile(
    r"\bant-(?:dev|deploy|metrics)\b|\bAnt[A-Z][A-Za-z]*(?:ReadOnly|Developer)\b"
)


@pytest.mark.parametrize("readme", _ROOT_READMES, ids=lambda d: d.name)
def test_root_readme_quick_start_is_copyable(readme: Path) -> None:
    """루트 README 「빠른 시작」은 안내서와 **같은 규칙**으로 잰다 — 명령 수만 적을 뿐이다.

    ① 스택 A 배포 명령이 있고, 한도를 넘는 템플릿이면 S3 를 거친다(위 안내서 테스트와 같은 이유).
    ② 모든 `aws` 명령에 `--profile`·`--region` 이 있다. ③ 사내 프로필·퍼미션셋 이름이 없다.
    안내서 테스트(`_DEPLOY_DOCS`)에 그냥 넣지 않은 이유: 그 테스트는 스택 A 명령 2개 이상(2단계 + 5단계
    갱신)을 요구하는데 빠른 시작은 2단계 하나만 보여 준다.
    """
    stack_a = [
        command
        for command in _readme_cfn_commands(readme)
        if any(template in command for template in _STACK_A_TEMPLATES)
    ]
    assert stack_a, f"{readme.name} 빠른 시작에서 스택 A 배포 명령을 못 찾았다"
    for command in stack_a:
        template = next(t for t in _STACK_A_TEMPLATES if t in command)
        if (INFRA.parent / template).stat().st_size > _TEMPLATE_BODY_LIMIT:
            assert "--template-body" not in command and "--s3-bucket" in command, (
                f"{readme.name}: {template} 는 본문 한도를 넘는데 S3 를 거치지 않는다: {command[:120]}"
            )
    aws_lines = [
        line
        for line in _sh_lines(readme)
        if re.search(r"(?:^|\$\()\s*aws ", line)
        and not re.search(r"\baws configure\b", line)
    ]
    # 🔴 파서가 명령을 못 찾으면 아래가 0회라 조용히 통과한다 — 찾은 수부터 잰다(0~4단계 합계).
    assert len(aws_lines) >= 5, (
        f"{readme.name} 에서 aws 명령을 {len(aws_lines)}개밖에 못 찾았다"
    )
    for line in aws_lines:
        assert "--profile " in line and "--region " in line, (
            f"{readme.name}: 프로필·리전이 빠진 명령: {line.strip()[:120]}"
        )
    internal = _INTERNAL_NAME.findall(readme.read_text())
    assert not internal, f"{readme.name} 에 사내 이름이 있다: {sorted(set(internal))}"


def test_kms_keys_open_decrypt_only_through_s3_on_the_given_keys() -> None:
    """W3③ D4c ② — SSE-KMS 고객 키 버킷. 준 키에만 · S3 가 풀 때만 · 비우면 정책 자원 자체가 없다.

    화면에서 보이는 일(깨졌다면): 키를 안 적은 고객의 역할에 「아무 키나 복호」가 생기거나(Resource `*`),
    키를 적어도 S3 로그가 여전히 「KMS 키로 풀 수 없음」이다.
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
            f"{stack.name}: {name} 이 조건 없이 생긴다"
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
            assert keys.fullmatch(ok), f"{stack.name}: 정상 키 ARN {ok!r} 을 거부한다"
        for bad in (
            "*",
            "arn:aws:kms:us-east-1:111122223333:key/*",
            "arn:aws:kms:us-east-1:111122223333:alias/aws/s3",
            "arn:aws:kms:*:111122223333:key/0a0b",
            "1234abcd-12ab-34cd-56ef-1234567890ab",
            "arn:aws:kms:us-east-1:111122223333:key/0a0b,",
        ):
            assert not keys.fullmatch(bad), f"{stack.name}: {bad!r} 를 받아들인다"
        patterns.append(keys.pattern)
        # 다른 역할에 조건 없이 섞이지 않는다(스택 A 의 소스 zip 복호 문장은 StateRole 이 아니라 BuildRole 이다).
        role = _resource(text, "StateRole" if stack == STACK_A else "MemberRole")
        assert "kms:Decrypt" not in role, (
            f"{stack.name}: 역할 본문에 kms:Decrypt 가 조건 없이 있다"
        )
    assert patterns[0] == patterns[1], (
        "FlowLogKmsKeyArns 입력 규칙이 두 스택에서 다르다"
    )
    # 엔진은 kms 를 부르지 않는다 — 판독기 액션 목록에 들어가면 가드 밖 동사를 부른다는 뜻이다.
    assert not [a for a in REQUIRED_ACTIONS if a.startswith("kms:")]
    assert "FlowLogKmsKeyArns" in WHY["kms"]


def test_the_flow_reader_structure_calls_are_granted_where_it_runs() -> None:
    """W3③ — 「이 창에 흐름 없음」은 서브넷 ENI 목록을 묻는다. 흐름 로그를 읽는 두 역할(스택 A 상태 단 · 스택 B 멤버)에
    그 액션이 **조건 없이** 있어야 한다 — 없으면 켠 고객의 모든 조용한 서브넷이 「ENI 목록 거부」로 판정되지 않는다."""
    from nettopo.flowlogs import STRUCTURE_ACTIONS

    for stack, role in ((STACK_A, "StateRole"), (STACK_B, "MemberRole")):
        block = _resource(stack.read_text(), role)
        for action in STRUCTURE_ACTIONS:
            assert re.search(rf"^\s+- {re.escape(action)}$", block, re.MULTILINE), (
                f"{stack.name} {role} 에 {action} 이 없다"
            )


def test_service_quota_actions_match_the_engine_the_doc_and_both_roles() -> None:
    """서비스 한도 읽기(ADR 0011 D5)는 **네 곳**에 적혀 있다 — 엔진 · 스택 B 멤버 역할 · 스택 A 상태 역할 · 권한 문서.

    화면에서 보이는 일: 한 곳이 빠지면 그 계정의 사용률 목록이 「계정 한도」 대신 「기본 한도」로 남는다. 한도를 늘려 둔
    계정이면 실제보다 높은 %를 말하고, 그림은 정상으로 서 있으므로 아무 에러도 나지 않는다.
    """
    from nettopo.utilization import REQUIRED_ACTIONS

    wanted = {a for a in REQUIRED_ACTIONS if a.startswith("servicequotas:")}
    assert len(wanted) == 2, "전제: 엔진이 한도 읽기 두 동사를 요구한다"
    pattern = r"^\s+- ([a-z0-9]+:[A-Z][A-Za-z]+)$"
    member = _statement(
        _resource(STACK_B.read_text(), "MemberRole"), "ReadServiceQuotas"
    )
    assert set(re.findall(pattern, member, re.MULTILINE)) == wanted
    state = _statement(_resource(STACK_A.read_text(), "StateRole"), "ReadServiceQuotas")
    assert set(re.findall(pattern, state, re.MULTILINE)) == wanted
    # 수집기(구조 단)는 한도를 쓰지 않는다 — 넣으면 필요 없는 권한이다(최소권한).
    assert "servicequotas:" not in _resource(STACK_A.read_text(), "BuildRole")

    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    section = doc.split("### 1-5. ")[1].split("\n## ")[0].split("\n### ")[0]
    rows = {
        line.split("`")[1]
        for line in section.splitlines()
        if line.startswith("| `") and ":" in line.split("`")[1]
    }
    assert rows == wanted


# ── 권한 문서(고객판) = 템플릿 ─────────────────────────────────────────────────────────────────
_IAM_ACTION = re.compile(r"^[a-z0-9-]+:[A-Za-z0-9]+$")


def _policy_actions(template: Path) -> set[str]:
    """템플릿의 IAM 역할·역할 정책이 **주는** 액션(신뢰 정책 제외). 버킷·큐 정책은 역할 권한이 아니라 뺀다."""
    text = template.read_text()
    body = text.split("\nResources:\n")[1].split("\nOutputs:\n")[0]
    actions: set[str] = set()
    for logical_id in re.findall(r"^  ([A-Za-z0-9]+):$", body, re.MULTILINE):
        block = _resource(text, logical_id)
        kind = re.search(r"^    Type: (\S+)", block, re.MULTILINE)
        if not kind or kind.group(1) not in ("AWS::IAM::Role", "AWS::IAM::RolePolicy"):
            continue
        # 역할은 `Policies:` 부터 — 그 앞의 신뢰 정책(`sts:AssumeRole` 을 **받는** 쪽)은 권한이 아니다
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
    """고객 보안 검토자가 읽는 `docs/required-permissions.md` 의 표가 **템플릿이 주는 액션 그대로**인가.

    스택 B 문서 절(§1) = 멤버 역할 · 스택 A = §1(자기 계정 읽기) + §2(수집기 고유) · 스택 C = 역할 없음.
    한쪽만 고치면 검토자는 실제로 주지 않는 권한을 승인하거나, 문서에 없는 권한이 배포된다.
    허용 액션에 와일드카드가 없다는 것도 여기서 잰다(거부 문장의 `s3:*` 는 역할이 아니라 버킷 정책에 있다).
    """
    doc = (INFRA.parent / "docs" / "required-permissions.md").read_text()
    stack_b = doc.split("\n## 1. ")[1].split("\n## 2. ")[0]
    stack_a = doc.split("\n## 2. ")[1].split("\n## 3. ")[0]
    in_b = _policy_actions(STACK_B)
    in_a = _policy_actions(STACK_A)
    # 🔴 파서가 못 찾으면 빈 집합끼리 같아진다 — 크기부터 잰다.
    assert len(in_b) >= 100 and len(in_a) >= 100, (len(in_b), len(in_a))
    doc_b = _doc_rows(stack_b)
    assert doc_b == in_b, (
        f"스택 B — 문서에만: {sorted(doc_b - in_b)} · 템플릿에만: {sorted(in_b - doc_b)}"
    )
    doc_a = doc_b | _doc_rows(stack_a)
    assert doc_a == in_a, (
        f"스택 A — 문서에만: {sorted(doc_a - in_a)} · 템플릿에만: {sorted(in_a - doc_a)}"
    )
    assert _policy_actions(INFRA / "stack-c-viewer.yaml") == set(), (
        "스택 C 는 IAM 역할을 만들지 않는다(문서 §3)"
    )
    wild = sorted(a for a in in_a | in_b if not _IAM_ACTION.match(a))
    assert not wild, f"허용 액션에 와일드카드: {wild}"


# 스택 A 본문 크기 상한(정보창 개편 2026-10-24) — CloudFormation 본문 한도는 51,200 B 다. 안내서의 수동 설치 명령은
# `--s3-bucket` 없이 본문으로 올린다 → 한도를 넘는 날 그 명령이 고객 앞에서 실패한다. 한도에 닿기 전에 잡으려고 여유를 둔다.
STACK_A_MAX_BYTES = 50_000


def test_stack_a_template_stays_under_the_body_size_budget() -> None:
    """🔴 스택 A 가 본문 상한(50,000 B · 한도 51,200 B 의 여유) 안인가 — 넘으면 설명·주석을 줄이고 동작은 바꾸지 않는다."""
    size = len(STACK_A.read_bytes())
    assert size <= STACK_A_MAX_BYTES, (
        f"스택 A {size} B > {STACK_A_MAX_BYTES} B — 안내서 본문 설치가 한도(51,200 B)에 닿는다"
    )


def test_the_structure_build_also_reads_layer_one_state_for_the_snapshot() -> None:
    """🔴 「수집 시각 기준」 터널 상태(사용자 결정 2026-10-24 · ADR 0006 O13) — 구조 빌드가 `collect` 직후 1층 `observe` 를 한 번 돌리고
    렌더러 어댑터에 `SNAPOBS` 로 넘기는가. 빠지면 모니터링을 끈 화면은 터널이 Down 이어도 VPN 「사용 가능」만 보인다(콘솔과 다르다).

    1층만이다(`--metrics`·`--flow-logs`·`--utilization` 없음 · `--no-sources`) — 구조 빌드 역할(BuildRole)에 지표·근거 권한이 없다.
    실패는 구조 화면을 죽이지 않는다(`|| rm -f` — 어댑터가 「확인하지 못했습니다」를 굽는다 · 불변식 ④).
    """
    text = BUILDSPEC.read_text()
    m = re.search(r"nettopo observe (.+?)\n(?:\s+(.+?)\n)?", text)
    assert m, "구조 빌드에 `nettopo observe` 가 없다"
    cmd = " ".join(g for g in m.groups() if g)
    assert "--topology out/topology.json" in cmd and "--no-sources" in cmd, cmd
    assert not re.search(r"--(metrics|flow-logs|utilization)\b", cmd), (
        f"구조 빌드 observe 가 1층을 넘는다: {cmd}"
    )
    assert "|| rm -f out/obs-snap.json" in cmd, (
        f"observe 실패가 빌드를 죽인다(구조 화면까지 없어진다): {cmd}"
    )
    assert text.index("nettopo collect") < text.index("nettopo observe"), (
        "observe 가 collect 보다 먼저 돈다(토폴로지가 없다)"
    )
    assert "SNAPOBS=../out/obs-snap.json" in text, (
        "어댑터에 수집 시각 기준 상태를 넘기지 않는다"
    )


_ENGINE_LOCK = INFRA.parent / "engine" / "requirements.lock"
_ENGINE_BUILD_LOCK = INFRA.parent / "engine" / "build-requirements.lock"


def test_collector_installs_engine_dependencies_from_the_hashed_lock() -> None:
    """수집기는 검사를 통과한 의존성 판만 설치한다 — `pip install ./engine` 은 빌드하는 날의 최신 boto3·botocore 를 끌어온다.

    화면에는 아무 차이가 없다 — 그래서 검사로 잰다. 읽기 전용 가드(`awsguard.py`)가 훅을 거는 것이 botocore 라,
    검사한 적 없는 botocore 가 매시 수집을 돌면 그 가드가 검사한 것과 다른 코드 위에서 돈다.
    """
    build = BUILDSPEC.read_text()
    # 🔴 빌드 이미지의 파이썬에 바로 설치하면 이미 깔린 같은 판은 받지 않고(해시 검증 밖), `--ignore-installed` 로 덮으면
    #    옛 판 설치 정보가 남아 로그·메타데이터가 옛 번호를 말한다(실측 2026-10-27). 빌드 안에서 **새 가상환경**에만 설치한다.
    venv = re.search(r"^\s+VENV: (/\S+)$", build, re.M)
    assert venv, "buildspec 에 절대 경로 VENV 가 없다"
    assert '- python -m venv "$VENV"' in build
    assert (
        "$VENV/bin/python -m pip install --require-hashes -r engine/requirements.lock"
        " -r engine/build-requirements.lock" in build
    )
    # 🔴 빌드 격리를 켜 두면 pip 가 빌드 백엔드(hatchling)를 그날의 최신 판으로 받아 온다 — 빌드 lock 이 잠든다.
    assert (
        "$VENV/bin/python -m pip install --no-build-isolation --no-deps ./engine"
        in build
    )
    assert "--ignore-installed" not in build, (
        "가상환경에는 덮어쓸 설치본이 없다 — 옵션이 남으면 이미지 파이썬에 까는 모양으로 되돌아간 것이다"
    )
    # 이후 단계가 **같은 환경**을 쓴다 — 셸이 달라도 절대 경로로 부른다. 맨 이름(`nettopo` · `python -c`)이 남으면 이미지 쪽이 돈다.
    assert not re.search(r"(?<![/\w])nettopo (collect|observe)\b", build), (
        "venv 밖의 nettopo 를 부른다"
    )
    assert not re.search(r"(?<![/\w])python (-c|-m pip)\b", build), (
        "venv 밖의 python 을 부른다"
    )
    assert "boto3.__version__" in build and "botocore.__version__" in build, (
        "설치 직후 SDK 판을 남기지 않는다"
    )
    assert not re.search(r"pip install (--upgrade|-U)\b", build), (
        "buildspec 이 pip 를 최신으로 올린다(고정되지 않은 설치)"
    )
    assert not re.search(r"pip install \./engine\b", build), (
        "엔진을 의존성째 설치한다 — lock 을 우회한다"
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
    # 🔴 lock 이 비면 위 설치는 아무것도 고정하지 않고 통과한다 — 핵심 둘이 있는지부터 잰다.
    names = {line.split("==", 1)[0] for line in pins}
    assert {"boto3", "botocore"} <= names, names
    for line in pins + build_pins:
        assert re.fullmatch(
            r"[a-z0-9-]+==[0-9][^ ]* --hash=sha256:[0-9a-f]{64}", line
        ), line


def test_collector_reads_the_buildspec_file_that_every_package_carries() -> None:
    """빌드 명세는 템플릿 밖 `buildspec.yml` 이다 — 패키지를 만드는 모든 길이 그 파일을 실어야 한다.

    하나라도 빠지면 그 길로 만든 패키지로는 수집이 첫 단계(DOWNLOAD_SOURCE 뒤 명세 읽기)에서 실패한다 — 다이어그램은
    마지막 성공 판에 멈추고, 고객은 CodeBuild 로그를 열어야 이유를 안다.
    """
    project = _resource(STACK_A.read_text(), "CollectorProject")
    assert "        BuildSpec: buildspec.yml\n" in project + "\n", (
        "CollectorProject 가 패키지의 buildspec.yml 을 읽지 않는다"
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
    # 묶음·공개 트리 스크립트의 PACKAGE 는 scripts/tests/test_package_lists.py 가 잰다(scripts/ 는 공개 트리에 없다).


def test_member_role_trust_hardening_is_optional_and_off_by_default() -> None:
    """스택 B 신뢰 정책의 선택 보강 — `ExternalId`(sts:ExternalId) · `PrincipalOrgId`(aws:PrincipalOrgID).

    비우면(기본) 조건 키 자체가 없어야 한다 — 빈 값으로 조건을 걸면 **모든** assume 이 거부되어 그 계정 칸이 통째로
    「권한 없음」이 된다. 채우면 그 값이 신뢰 정책 같은 문장의 StringEquals 에 들어가야 한다(다른 문장이면 Allow 가 둘이 된다).
    """
    text = STACK_B.read_text()
    for name, pattern in (
        ("ExternalId", "^$|^[-\\\\w+=,.@:/]{2,1224}$"),
        ("PrincipalOrgId", "^$|^o-[a-z0-9]{10,32}$"),
    ):
        param = re.search(rf"^  {name}:\n(.*?)(?=^  \S)", text, re.M | re.S)
        assert param, f"스택 B 에 {name} 파라미터가 없다"
        assert re.search(r'^    Default: ""$', param.group(1), re.M), (
            f"{name} 기본값이 비어 있지 않다"
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
        "신뢰 정책에 문장이 둘 이상이다 — 조건이 없는 Allow 가 남으면 보강이 무력하다"
    )


def test_member_external_id_reaches_every_assume_path() -> None:
    """허브가 멤버 역할에 들어가는 길은 둘이다 — 구조 빌드(CodeBuild: collect + observe) · 5분 관측(StateFunction).

    한 길에만 넘기면 그 길만 거부된다: 구조 화면은 서는데 상태가 전부 「확인 불가」거나, 그 반대.
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
    """산출물 버킷 CORS — 비우면 지금과 같고(`file://` 의 null + 아무 CloudFront 도메인), 스택 C 주소를 주면 그 하나로 좁힌다.

    CORS 는 브라우저가 지키는 규칙이라 서명 URL 없이는 어차피 못 읽는다 — 그래도 좁혀 두면 다른 CloudFront 배포에서
    받은 서명 URL 로 이 버킷을 부르는 길이 닫힌다(리뷰어 질문 대비).
    """
    text = STACK_A.read_text()
    assert re.search(
        r'^  ViewerOrigin:\n    Type: String\n    Default: ""\n', text, re.M
    )
    # 스택 C 는 사용자 도메인(CustomDomainName)도 받는다 — 그때 화면 출처는 cloudfront.net 이 아니다.
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
    """서비스 주체(CodeBuild·EventBridge·Lambda)가 맡는 역할은 **이 계정**에서 온 요청만 신뢰한다(혼동된 대리인 방지).

    화면에는 아무 차이가 없다 — 그래서 검사로 잰다. 2026-10-28 점검(cse)에서 BuildRole 만 조건이 있었다.
    """
    trust = _resource(STACK_A.read_text(), role).split("Policies:")[0]
    assert 'StringEquals: {"aws:SourceAccount": !Ref "AWS::AccountId"}' in trust, (
        f"{role} 신뢰 정책에 SourceAccount 가 없다"
    )


def test_artifact_bucket_statements_pin_the_bucket_owner() -> None:
    """산출물 버킷을 읽고 쓰는 Allow 문장 = 버킷 소유 계정이 이 계정일 때만(`s3:ResourceAccount`).

    버킷은 스택을 지워도 남는다(Retain) — 그 이름이 언젠가 다른 계정 것이 되면 조건 없는 문장은 그 버킷에 상태·서명 URL 을
    쓴다. 문장 하나라도 빠지면 그 길만 열린다(2026-10-28 점검).
    """
    text = STACK_A.read_text()
    missing = []
    for role in ("BuildRole", "StateRole", "SignerRole"):
        body = _resource(text, role)
        for block in body.split("- Sid: ")[1:]:
            sid = block.split("\n", 1)[0]
            # 배포 패키지 버킷 읽기(ReadSourceZip)도 같다 — 패키지 버킷은 스택 밖이라 이름이 넘어가기 더 쉽다(2026-10-28).
            if "Effect: Allow" in block and (
                "ArtifactBucket" in block or sid == "ReadSourceZip"
            ):
                if '"s3:ResourceAccount": !Ref "AWS::AccountId"' not in block:
                    missing.append(f"{role}.{sid}")
    assert not missing, missing
    # 🔴 문장을 못 찾으면 위가 0회라 조용히 통과한다 — 센 수부터 잰다.
    assert (
        text.count(
            'Condition: {StringEquals: {"s3:ResourceAccount": !Ref "AWS::AccountId"}}'
        )
        >= 11
    )
    assert "- Sid: ReadSourceZip" in _resource(text, "BuildRole")


def test_state_lambda_bundle_has_no_known_vulnerable_urllib3() -> None:
    """상태 Lambda 꾸러미의 urllib3 = 엔진 lock 과 같은 판(2.7.0 은 pip-audit 경보 3건 · 2026-10-28)."""
    root = INFRA.parent

    def pin(path: Path) -> str:
        return next(
            x for x in path.read_text().splitlines() if x.startswith("urllib3==")
        )

    assert pin(root / "infra" / "state-lambda" / "requirements.lock") == pin(
        root / "engine" / "requirements.lock"
    )
