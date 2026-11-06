"""load_balancer(ELB — ALB/NLB/GWLB) 수집 — 「이음매 2」의 **참조 구현**.

파일 하나가 무엇을 소유하는지 보여주는 본보기다: 목록 호출(`SPEC`) · 후속 호출(`enrich`) ·
초안 작성(`build`). `collect.py` 는 이 파일의 이름을 모른다 — `services/__init__.py` 의 레지스트리만 본다.

파일명이 `elb.py` 가 아니라 **수집 종류**(`load_balancer`)인 이유는 `services/__init__.py` 머리말에
있다: EC2 하나가 종류 11개를 낳으므로 파일 단위를 AWS 서비스로 잡을 수 없다. boto3 서비스 이름
(`elbv2`)과 IAM 접두(`elasticloadbalancing`)는 아래 `SPEC` 이 들고 있다.

**ELB 를 참조로 고른 이유**는 1차 12종 중 유일하게 목록 호출 하나로 끝나지 않기 때문이다. 리스너와
타깃 그룹은 별 API(`DescribeListeners`·`DescribeTargetGroups`)이고, 그래서 이 파일이 후속 호출의
실패 처리(불변식 ④)와 결정론(불변식 ②)을 한 번에 보여준다.

🔴 **헬스는 구조에 담지 않는다 · 등록 대상 수는 담는다**(사용자 결정 2026-10-16 · ADR 0006 「W3② LB 대상
상태」 개정). `DescribeTargetHealth` 는 **구조 후속 호출**로 대상 그룹마다 한 번 부르고, 구조에는 그 응답의
**개수**(`TargetHealthDescriptions` 의 길이 = 등록된 대상 수)만 싣는다 — `registered_target_count`(LB 합) ·
`target_group_registered_target_counts`(`target_groups` 와 같은 순서 · 못 읽은 그룹 = `null`). 대상을
등록·해제하는 것은 **구성 변경**이므로 구조 지문이 바뀌는 것이 맞다(열린 화면은 「최신 화면 열기」로 따라간다).
healthy/unhealthy 는 시시각각 바뀌므로 `topology.json` 에 넣으면 매 수집이 「구조 바뀜」이 된다(불변식 ② ·
`collect.py` 의 `AvailableIpAddressCount` 주석과 같은 이유) → 상태 수(`target_count`·`healthy_target_count`·
`unhealthy_target_count`)는 **상태 단에서만** 싣는다(`state_attributes` — `collect(with_state=True)` 일 때만).
같은 응답을 두 번 묻지 않는다: 상태 단도 구조 후속 호출이 읽어 둔 응답(`_TargetHealth`)을 센다.
🔴 한 그룹이라도 못 읽었으면 LB 합(`registered_target_count`)도 상태 수도 싣지 않는다 — 일부만 센 수는 틀린
수다. 그 LB 는 `partial` + 사유이고 노드는 남는다(불변식 ④).

🔵 **ALB 의 web ACL 은 여기의 한 속성이다** — 🔁 2.12.0(ADR 0014)부터 web ACL 은 따로 카드가 되고 3패스가 이 속성으로
web ACL → ALB 선을 잇는다(`relations.waf_protections` · ALB 에 `ListResourcesForWebACL` 은 묻지 않는다). 옛 판단: 캔버스 정본이 WAF 를 「카드 안의 한
줄」로 정했다(`docs/canvas-principles.md` §8.1 (d) 속성 줄 · §8.2 「자기 위치가 없다 — ALB·CloudFront
배포에 붙는 설정이다」). CloudFront 쪽은 목록 응답이 `WebACLId` 를 같이 줘서 추가 호출이 0인데
(`services/cloudfront.py`), **ALB 는 `wafv2:GetWebACLForResource` 를 LB 당 1회 더 불러야** 안다.
다른 서비스(`wafv2`)를 부르므로 RDS → EC2 ENI 와 같은 문 `Service.peer_enrich` 로 받는다(`join_web_acl`).

🔴 **ALB 만 부른다.** NLB·GWLB 에는 web ACL 을 붙일 수 없다 → 전부 부르면 LB 마다 쓸모없는 거부가
쌓이고 그것이 `partial` 로 보인다(없는 결함을 만든다). 분기는 `Type == "application"` 이다.

🔵 **대상으로 가는 엣지(`load_balancer_target`)는 3패스가 만든다**(사용자 결정 2026-10-20 · ADR 0007 결정 6 개정 ·
ADR 0002 §8). 이 파일은 같은 `DescribeTargetHealth` 응답에서 **대상의 정체**(`Target.Id`·`Port`·`AvailabilityZone`)와
그 그룹의 `TargetType` 을 초안의 비공개 칸(`NodeDraft.lb_targets`)에 남길 뿐이다 — 대상 노드(EC2·ECS·RDS·다른 LB …)는
다른 수집 종류이고, 사설 IP 의 주인(ENI)은 **다른 (계정, 리전)** 에 있을 수도 있어서 한 쌍을 도는 동안 확정할 수 없다
(`nettopo/lb_targets.py`). 등록·해제는 구성 변경이므로 구조 지문이 바뀌는 것이 맞다. 헬스는 여전히 상태 단에만 있다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    TARGET_HEALTH_DETAIL,
    Spec,
    TargetGroupRef,
    TargetRef,
    alive,
    classify,
    draft,
    health_detail,
    pages,
)
from .base import sorted_strs as _sorted

# boto3 서비스 이름은 `elbv2`, IAM 액션 접두는 `elasticloadbalancing` — 다른 이름이다.
SPEC = Spec(
    "elbv2",
    "describe_load_balancers",
    "LoadBalancers",
    "elasticloadbalancing:DescribeLoadBalancers",
)

# 후속 호출 2개. (응답 키, 메서드, IAM 액션) — `docs/required-permissions.md` 와 같이 움직인다.
_FOLLOWUPS: tuple[tuple[str, str, str], ...] = (
    ("Listeners", "describe_listeners", "elasticloadbalancing:DescribeListeners"),
    (
        "TargetGroups",
        "describe_target_groups",
        "elasticloadbalancing:DescribeTargetGroups",
    ),
)


# ALB 의 web ACL — **다른 서비스**(`wafv2`)다. 페이지네이터가 없어 직접 호출한다
# (`can_paginate("get_web_acl_for_resource")` = False, 실측 2026-10-02).
_WEB_ACL_API = "wafv2:GetWebACLForResource"
# 🔴 부르지는 않지만 **권한은 필요하다** — `GetWebACLForResource` 는 붙은 web ACL 이 있으면 그 ACL 에 대해
#    `wafv2:GetWebACL` 을 한 번 더 인가한다. 없으면 **WAF 가 붙은 ALB 만** 거부되고 안 붙은 ALB 는 성공한다
#    (실측 2026-10-04: 안 붙은 ALB 6개 성공 · 붙은 ALB 1개 「not authorized to perform: wafv2:GetWebACL」).
#    화면에서는 보호 중인 ALB 가 전부 「확인 못 함」이 된다. 스택 템플릿 검사가 이 글자로 권한을 대조한다.
_WEB_ACL_DEPENDENT_API = "wafv2:GetWebACL"
# 🔵 ALB 만 해당한다(파일 머리말). GWLB(`gateway`)·NLB(`network`)는 부르지 않는다.
_WEB_ACL_TYPES = frozenset({"application"})


def _web_acl(client: Any, arn: str) -> tuple[str | None, Reason | None]:
    """(web ACL ARN 또는 None, 실패 사유 또는 None).

    🔴 **세 가지를 가른다.** ⓐ 붙어 있다 → ARN ⓑ **붙지 않았다** → 응답에 `WebACL` 키가 없거나
    비었다 → `(None, None)` ⓒ **못 읽었다** → 사유. ⓑ와 ⓒ를 같은 값으로 접으면 「보호되지 않음」과
    「확인 못함」이 한 모양이 된다(불변식 ④).
    """
    try:
        got = client.get_web_acl_for_resource(ResourceArn=arn)
    except (ClientError, BotoCoreError) as exc:
        return None, classify(exc, _WEB_ACL_API)[1]
    acl = got.get("WebACL") or {}
    return (acl.get("ARN") or None), None


# 콘솔 「리스너 및 규칙」 표의 두 칸(사용자 결정 2026-10-24) — 리스너마다 후속 호출. 🔴 거부 = 그 LB 에 사유(불변식 ④) ·
#   칸은 비운다(「규칙 0」·「인증서 없음」으로 적으면 거짓이다).
RULES_API = "elasticloadbalancing:DescribeRules"
LISTENER_CERTS_API = "elasticloadbalancing:DescribeListenerCertificates"
_RULE_COUNTS = "_RuleCounts"
_LISTENER_CERTS = "_ListenerCerts"
# 규칙은 ALB 리스너에만 있다(NLB·GWLB 리스너는 기본 동작 하나뿐) · 인증서는 TLS 를 끝내는 리스너에만
_RULE_TYPES = frozenset({"application"})
_CERT_PROTOCOLS = frozenset({"HTTPS", "TLS"})


def _listener_extras(
    client: Any, lb_type: str, listeners: list[dict[str, Any]]
) -> tuple[dict[str, int], dict[str, list[str]], list[Reason]]:
    """(리스너 ARN → 규칙 수, 리스너 ARN → 인증서 ARN 목록(기본 먼저), 사유). 리스너 하나가 거부돼도 나머지는 센다."""
    counts: dict[str, int] = {}
    certs: dict[str, list[str]] = {}
    failed: list[Reason] = []
    for listener in listeners:
        arn = listener.get("ListenerArn")
        if not arn:
            continue
        if lb_type in _RULE_TYPES:
            try:
                counts[str(arn)] = len(
                    pages(client, "describe_rules", "Rules", ListenerArn=arn)
                )
            except (ClientError, BotoCoreError) as exc:
                failed.append(classify(exc, RULES_API)[1])
        if str(listener.get("Protocol") or "").upper() in _CERT_PROTOCOLS:
            try:
                got = pages(
                    client,
                    "describe_listener_certificates",
                    "Certificates",
                    ListenerArn=arn,
                )
                certs[str(arn)] = [
                    str(c["CertificateArn"])
                    for c in sorted(
                        got,
                        key=lambda c: (
                            not c.get("IsDefault"),
                            str(c.get("CertificateArn") or ""),
                        ),
                    )
                    if c.get("CertificateArn")
                ]
            except (ClientError, BotoCoreError) as exc:
                failed.append(classify(exc, LISTENER_CERTS_API)[1])
    return counts, certs, failed


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """LB 마다 리스너·타깃 그룹을 더 읽고, 그 그룹마다 등록 대상 수(`DescribeTargetHealth`)를 센다.
    실패는 그 LB 의 `reasons` 로만 남는다.

    거부가 나도 목록을 줄이지 않는다 — 항목이 사라지면 「LB 가 없다」가 되고, 그건 거짓이다.
    응답 키를 payload 에 그대로 합친다(`Listeners`·`TargetGroups`): AWS 가 준 이름이므로
    빌더가 계약 밖 이름을 배우지 않는다.
    """
    out: list[Fetched] = []
    for item in items:
        arn = item.payload.get("LoadBalancerArn")
        payload = dict(item.payload)
        reasons: list[Reason] = list(item.reasons)
        for key, method, api in _FOLLOWUPS:
            if (
                not arn
            ):  # ARN 이 없으면 후속 호출 자체가 불가능하다 — 없는 값을 지어내지 않는다
                reasons.append(
                    Reason(code="api_error", api=api, message="LoadBalancerArn 없음")
                )
                continue
            try:
                payload[key] = pages(client, method, key, LoadBalancerArn=arn)
            except (ClientError, BotoCoreError) as exc:
                # 🔴 키를 만들지 않는 것이 핵심이다. `[]` 를 넣으면 「없음」과 「못 읽음」이 같아진다.
                reasons.append(classify(exc, api)[1])
        listeners = payload.get("Listeners")
        if listeners:
            counts, certs, failed_extras = _listener_extras(
                client, str(payload.get("Type") or ""), listeners
            )
            payload[_RULE_COUNTS] = counts
            payload[_LISTENER_CERTS] = certs
            reasons.extend(failed_extras)
        groups = payload.get("TargetGroups")
        if groups is not None:
            # 그룹 목록을 못 읽었으면(키 없음) 부르지 않는다 — 어느 그룹을 물을지 모르는데 「대상 0개」라고 적으면 거짓이다.
            refs: list[TargetRef] = []
            counted, found, failed = _registered(client, groups, refs)
            payload[_REGISTERED] = counted
            # 🔵 읽은 그룹의 대상 정체는 **그룹 하나가 거부돼도** 남긴다 — 나머지 그룹의 선은 참이다(불변식 ④).
            payload[_TARGETS] = tuple(sorted(refs, key=_ref_key))
            if found is not None:
                payload[_TARGET_HEALTH] = found
            reasons.extend(failed)
            # 2.8.0(P2-5) — 대상 그룹 태그의 EKS 클러스터(LB Controller 가 단다). 그룹이 있을 때만 묻는다.
            clusters, tag_failed = _group_clusters(client, groups)
            if tag_failed is not None:
                reasons.append(tag_failed)
            else:
                payload[_TG_CLUSTERS] = clusters
        out.append(Fetched(payload=payload, reasons=tuple(reasons)))
    return out


# 대상 그룹 태그(2.8.0 · P2-5) — `elbv2:DescribeTags` 는 ARN 20개까지 한 번에 받는다(페이지네이터 없음).
TAGS_API = "elasticloadbalancing:DescribeTags"
_TAGS_CHUNK = 20
# AWS Load Balancer Controller 가 자기가 만든 대상 그룹에 다는 태그(그 클러스터 이름) — 이 태그가 「이 LB 는 이 클러스터의
#   Ingress/Service 다」의 근거다. 사람이 손으로 단 태그도 같은 뜻으로 읽는다(값이 클러스터 이름이 아니면 노드를 못 찾아 선이 없다).
_EKS_TG_TAG = "elbv2.k8s.aws/cluster"
_TG_CLUSTERS = "_TgClusters"


def _group_clusters(
    client: Any, groups: list[dict[str, Any]]
) -> tuple[dict[str, str], Reason | None]:
    """대상 그룹 ARN → 그 그룹을 든 EKS 클러스터 이름(태그가 있는 것만) · 실패면 ({}, 사유).

    🔴 읽기 전용(불변식 ①) — `Describe*` 하나다. 🔴 한 묶음이라도 거부되면 전부 버린다 — 일부 그룹만 클러스터를 알면
    어느 그룹이 「태그 없음」이고 어느 그룹이 「못 읽음」인지 갈리지 않는다(LB 에 사유 · 불변식 ④).
    """
    arns = sorted({str(g["TargetGroupArn"]) for g in groups if g.get("TargetGroupArn")})
    out: dict[str, str] = {}
    try:
        for i in range(0, len(arns), _TAGS_CHUNK):
            got = client.describe_tags(ResourceArns=arns[i : i + _TAGS_CHUNK])
            for desc in got.get("TagDescriptions") or []:
                for tag in desc.get("Tags") or []:
                    if tag.get("Key") == _EKS_TG_TAG and tag.get("Value"):
                        out[str(desc.get("ResourceArn"))] = str(tag["Value"])
    except (ClientError, BotoCoreError) as exc:
        return {}, classify(exc, TAGS_API)[1]
    return out, None


TARGET_HEALTH_API = "elasticloadbalancing:DescribeTargetHealth"
# 엔진이 붙이는 payload 키 — AWS 응답 키와 섞이지 않게 밑줄로 시작한다.
#   `_Registered` = [(그룹 이름, 등록 대상 수 | None)] · `_TargetHealth` = 전 그룹의 응답(전부 읽었을 때만)
_REGISTERED = "_Registered"
_TARGET_HEALTH = "_TargetHealth"
#   `_Targets` = 읽은 그룹의 등록 대상 정체(`TargetRef` 튜플 · 정렬) — 3패스의 대상 잇기 재료(구조 문서에 그대로 안 싣는다)
_TARGETS = "_Targets"


def _ref_key(ref: TargetRef) -> tuple[str, str, str, int]:
    # 결정론(불변식 ②) — 응답 순서가 아니라 (그룹 이름, 그룹 ARN, 대상 id, 포트) 순.
    return (ref.target_group, ref.target_group_arn, ref.target_id, ref.port or -1)


def _group_refs(
    groups: list[dict[str, Any]], clusters: dict[str, str] | None = None
) -> tuple[TargetGroupRef, ...]:
    """읽은 대상 그룹 → 비공개 칸(`NodeDraft.lb_groups` · 2.8.0). 등록 0 인 그룹도 남긴다 — ECS 서비스·ASG 가 든 그룹이면
    등록이 없어도 「이 LB 뒤에 그 자원이 있다」는 선이 된다(`nettopo/lb_targets.py`). ARN 순(결정론 · 불변식 ②)."""
    out = {
        str(g["TargetGroupArn"]): TargetGroupRef(
            name=str(g.get("TargetGroupName") or ""),
            arn=str(g["TargetGroupArn"]),
            target_type=str(g.get("TargetType") or "").lower(),
            port=int(g["Port"]) if isinstance(g.get("Port"), int) else None,
            cluster=(clusters or {}).get(str(g["TargetGroupArn"])),
        )
        for g in groups
        if g.get("TargetGroupArn")
    }
    return tuple(out[k] for k in sorted(out))


def _target_refs(group: dict[str, Any], got: list[dict[str, Any]]) -> list[TargetRef]:
    """그룹 하나의 응답 → 등록 대상 정체. `TargetType` 은 그룹이 말한다(대상 응답에는 없다).

    포트는 대상의 `Port`(인스턴스·IP 는 그룹 포트를 덮어쓸 수 있다) → 없으면 그룹의 `Port`. Lambda 는 포트가 없다.
    """
    kind = str(group.get("TargetType") or "").lower()
    tg_port = group.get("Port")
    out: list[TargetRef] = []
    for desc in got:
        target = desc.get("Target") or {}
        tid = target.get("Id")
        if not tid:
            continue  # 정체가 없는 응답 줄은 선으로 이을 수 없다 — 수(`_Registered`)에는 이미 들어 있다
        port = target.get("Port", tg_port if kind != "lambda" else None)
        out.append(
            TargetRef(
                target_group=str(group.get("TargetGroupName") or ""),
                target_group_arn=str(group.get("TargetGroupArn") or ""),
                target_type=kind,
                target_id=str(tid),
                port=int(port) if isinstance(port, int) else None,
                zone=str(target["AvailabilityZone"])
                if target.get("AvailabilityZone")
                else None,
                health=str((desc.get("TargetHealth") or {}).get("State") or "") or None,
                health_reason=str((desc.get("TargetHealth") or {}).get("Reason") or "")
                or None,
            )
        )
    return out


def _registered(
    client: Any, groups: list[dict[str, Any]], refs: list[TargetRef]
) -> tuple[list[tuple[str, int | None]], list[dict[str, Any]] | None, list[Reason]]:
    """대상 그룹마다 `DescribeTargetHealth` 한 번 → ((이름, 등록 수 | None) 목록, 전체 응답 | None, 사유).

    🔴 한 그룹이 실패해도 **나머지 그룹은 계속 센다**(그룹별 수는 읽은 만큼 참이다). 대신 전체 응답은 `None` —
    LB 합과 상태 수는 전 그룹을 읽었을 때만 참이다. 실패 사유는 그룹마다 하나씩 남는다(불변식 ④).
    읽은 그룹의 대상 정체는 `refs` 에 붙인다(같은 응답 — 다시 묻지 않는다).
    """
    counted: list[tuple[str, int | None]] = []
    found: list[dict[str, Any]] = []
    failed: list[Reason] = []
    for group in groups:
        name = str(group.get("TargetGroupName") or "")
        arn = group.get("TargetGroupArn")
        if not arn:
            failed.append(Reason("api_error", TARGET_HEALTH_API, "TargetGroupArn 없음"))
            counted.append((name, None))
            continue
        try:
            # 페이지네이터가 없고 토큰도 없다 — 한 번에 그룹의 대상 전부를 준다(boto3 elbv2 · `can_paginate` False).
            got = (
                client.describe_target_health(TargetGroupArn=arn).get(
                    "TargetHealthDescriptions"
                )
                or []
            )
        except (ClientError, BotoCoreError) as exc:
            failed.append(classify(exc, TARGET_HEALTH_API)[1])
            counted.append((name, None))
            continue
        counted.append((name, len(got)))
        found.extend(got)
        refs.extend(_target_refs(group, got))
    return counted, (None if failed else found), failed


# GWLB·NLB 를 뒤에 둔 **엔드포인트 서비스**(PrivateLink 제공자 쪽 · 2.8.0 · P1-3) — `ec2` 의 API 다(`peer_enrich`).
#   그 계정·리전에서 **이 계정이 제공하는** 서비스 구성만 돌려준다 → LB 를 수집한 쌍에서 묻는다(LB 와 서비스는 같은 계정·리전이다).
#   GWLB 엔드포인트(`vpc_endpoint` · 소비자 쪽)는 `ServiceName` 만 안다 — 3패스가 그 이름으로 이 LB 를 찾는다(`nettopo/endpoint_providers.py`).
ENDPOINT_SERVICE_API = "ec2:DescribeVpcEndpointServiceConfigurations"
_ENDPOINT_SERVICES = "_EndpointServices"
# 엔드포인트 서비스를 뒤에 둘 수 있는 종류(AWS — GWLB·NLB 만). ALB 는 묻지 않는다(쓸모없는 거부가 「일부 못 읽음」이 된다).
_PROVIDER_TYPES = frozenset({"gateway", "network"})


def join_endpoint_services(
    client: Any, items: list[Fetched], failed: Reason | None
) -> list[Fetched]:
    """GWLB·NLB 마다 그 LB 를 뒤에 둔 엔드포인트 서비스 이름(정렬) — 그 쌍에서 **한 번** 묻는다(LB 가 없으면 안 묻는다).

    🔴 세 갈래(web ACL 과 같은 규율 · 불변식 ④): 읽었고 있다 = 이름 목록 · 읽었고 없다 = `[]` · 못 읽었다 = 키 없음 + 그 LB 에 사유.
    🔴 읽기 전용(불변식 ①) — `Describe*` 하나다.
    """
    want = [
        i
        for i, it in enumerate(items)
        if str(it.payload.get("Type") or "") in _PROVIDER_TYPES
    ]
    if not want:
        return items
    names: dict[str, set[str]] | None = None
    reason: Reason | None = None
    if client is None:
        reason = failed or Reason(
            "api_error", ENDPOINT_SERVICE_API, "ec2 클라이언트 없음"
        )
    else:
        try:
            got = pages(
                client,
                "describe_vpc_endpoint_service_configurations",
                "ServiceConfigurations",
            )
        except (ClientError, BotoCoreError) as exc:
            reason = classify(exc, ENDPOINT_SERVICE_API)[1]
        else:
            names = {}
            for cfg in got:
                name = cfg.get("ServiceName")
                if not name:
                    continue
                for arn in [
                    *(cfg.get("GatewayLoadBalancerArns") or []),
                    *(cfg.get("NetworkLoadBalancerArns") or []),
                ]:
                    names.setdefault(str(arn), set()).add(str(name))
    out = list(items)
    for i in want:
        it = out[i]
        if names is None:
            out[i] = Fetched(it.payload, it.reasons + (reason,)) if reason else it
            continue
        arn = str(it.payload.get("LoadBalancerArn") or "")
        out[i] = Fetched(
            {**it.payload, _ENDPOINT_SERVICES: sorted(names.get(arn, set()))},
            it.reasons,
        )
    return out


def join_web_acl(
    client: Any, items: list[Fetched], failed: Reason | None
) -> list[Fetched]:
    """ALB 마다 붙은 web ACL 을 읽는다 — `wafv2` 는 **다른 서비스**라 `Service.peer_enrich` 로 받는다.

    `collect` 가 `wafv2` 진입에 실패했으면 클라이언트 대신 `None` + 그 사유가 온다 → 그 ALB 들에 사유만
    붙이고 키는 만들지 않는다(「붙지 않았다」로 적지 않는다 · 불변식 ④). NLB·GWLB 는 건드리지 않는다.
    """
    out: list[Fetched] = []
    for item in items:
        if str(item.payload.get("Type") or "") not in _WEB_ACL_TYPES:
            out.append(item)
            continue
        arn = item.payload.get("LoadBalancerArn")
        if client is None:
            reason = failed or Reason(
                "api_error", _WEB_ACL_API, "wafv2 클라이언트 없음"
            )
        elif not arn:
            reason = Reason("api_error", _WEB_ACL_API, "LoadBalancerArn 없음")
        else:
            acl, reason = _web_acl(client, str(arn))
            if reason is None:
                # 🔵 읽었으면 키를 만든다 — 값이 `None` 이면 「붙지 않았다」는 **사실**이다.
                out.append(Fetched({**item.payload, "WebAclArn": acl}, item.reasons))
                continue
        out.append(Fetched(item.payload, item.reasons + (reason,)))
    return out


def state_attributes(item: dict[str, Any]) -> dict[str, Any]:
    """대상 수 셋(정수) — **상태 단에서만** 노드 속성이 된다(`collect(with_state=True)`). 못 읽었으면 `{}`
    — 키가 없다 = 「모른다」(불변식 ④). 응답은 구조 후속 호출(`enrich`)이 읽어 둔 것이다 — 다시 묻지 않는다.

    🔴 `unhealthy` 는 AWS 가 `unhealthy` 라고 한 것만 센다. `initial`(등록 중)·`draining`(빼는 중)·
    `unused`(쓰지 않는 AZ)·`unavailable`(헬스 체크 꺼짐)은 정상도 비정상도 아니다 — 비정상으로 세면 배포
    중인 서비스가 빨갛게 된다.

    그 넷은 따로도 센다(관측 1.15.0 · #137 — 화면이 「헬스 체크 꺼짐 1개」를 「그 밖」과 갈라 말한다): `unavailable` 은
    `Reason` 으로 둘로 나눈다(`Target.HealthCheckDisabled` = 헬스 체크 꺼짐 · 그 밖 = `other_unavailable`). 다섯 키는
    늘 함께 싣는다(0 포함) — 「셌고 0」과 「옛 문서라 안 셌다」가 갈린다. 판정은 이 수를 보지 않는다(`observe.py` `_with_targets`).
    """
    found = item.get(_TARGET_HEALTH)
    if found is None:
        return {}
    pairs = [
        (
            str((t.get("TargetHealth") or {}).get("State") or ""),
            (t.get("TargetHealth") or {}).get("Reason"),
        )
        for t in found
    ]
    states = [s for s, _ in pairs]
    out: dict[str, Any] = {
        "target_count": len(states),
        "healthy_target_count": states.count("healthy"),
        "unhealthy_target_count": states.count("unhealthy"),
    }
    for key, _ in TARGET_HEALTH_DETAIL:
        out[key] = 0
    for state, reason in pairs:
        key = health_detail(state, reason)
        if key is not None:
            out[key] += 1
    return out


def _listener_labels(listeners: list[dict[str, Any]]) -> list[str]:
    """`["HTTP:80", "HTTPS:443"]`. 포트는 숫자로 정렬한다 — 문자열 정렬은 8080 을 80 앞에 놓는다."""
    labels: dict[tuple[str, int], str] = {}
    for listener in listeners:
        protocol = str(listener.get("Protocol") or "").upper()
        port = listener.get("Port")
        if not protocol or port is None:
            continue  # 둘 중 하나가 없으면 「리스너 하나」라고 말할 수 없다
        labels[(protocol, int(port))] = f"{protocol}:{int(port)}"
    return [labels[key] for key in sorted(labels)]


def _arn_tail(arn: Any) -> str:
    """`…:targetgroup/<이름>/<해시>` → 이름 · `…:certificate/<id>` → id. 모르는 꼴은 그대로."""
    text = str(arn or "")
    if ":targetgroup/" in text:
        return text.split(":targetgroup/", 1)[1].split("/", 1)[0]
    return text.rsplit("/", 1)[-1] if "/" in text else text


def _action_text(actions: list[dict[str, Any]]) -> str:
    """콘솔 「기본 작업」의 재료 — 순서(Order)대로 「<유형>:<대상>」을 「;」로 잇는다.

    forward = 대상 그룹 이름(가중치 있으면 「이름=가중치」 · 쉼표) · redirect = 「<프로토콜>://<호스트>:<포트><경로> <코드>」(AWS 원문 ·
    `#{host}` 같은 자리표시자 그대로) · fixed-response = 상태 코드 · authenticate-* = 유형만.
    """
    parts = []
    for a in sorted(actions, key=lambda a: int(a.get("Order") or 0)):
        kind = str(a.get("Type") or "")
        if kind == "forward":
            groups = ((a.get("ForwardConfig") or {}).get("TargetGroups")) or []
            if groups:
                body = ",".join(
                    f"{_arn_tail(g.get('TargetGroupArn'))}={g.get('Weight')}"
                    if len(groups) > 1
                    else _arn_tail(g.get("TargetGroupArn"))
                    for g in groups
                )
            else:
                body = _arn_tail(a.get("TargetGroupArn"))
        elif kind == "redirect":
            r = a.get("RedirectConfig") or {}
            body = f"{r.get('Protocol', '')}://{r.get('Host', '')}:{r.get('Port', '')}{r.get('Path', '')} {r.get('StatusCode', '')}".strip()
        elif kind == "fixed-response":
            body = str((a.get("FixedResponseConfig") or {}).get("StatusCode") or "")
        else:
            body = ""
        parts.append(f"{kind}:{body}" if body else kind)
    return ";".join(parts)


def _listener_rows(item: dict[str, Any]) -> list[str]:
    """콘솔 「리스너 및 규칙」 표 한 줄 = 「프로토콜 \t 포트 \t 기본 작업 \t 규칙 수 \t SSL 정책 \t 인증서(기본 먼저 · 쉼표)」.

    규칙 수·인증서를 못 읽었으면(거부 · LB partial) 그 칸이 빈 칸이다 — 0 과 다르다. 포트 숫자 순.
    """
    counts = item.get(_RULE_COUNTS) or {}
    certs = item.get(_LISTENER_CERTS) or {}
    rows = []
    for listener in item.get("Listeners") or []:
        arn = str(listener.get("ListenerArn") or "")
        port = listener.get("Port")
        cert_list = certs.get(arn)
        if cert_list is None:
            cert_list = [
                str(c["CertificateArn"])
                for c in listener.get("Certificates") or []
                if c.get("CertificateArn")
            ]
        rows.append(
            (
                int(port) if isinstance(port, int) else -1,
                "\t".join(
                    (
                        str(listener.get("Protocol") or ""),
                        "" if port is None else str(port),
                        _action_text(listener.get("DefaultActions") or []),
                        str(counts[arn]) if arn in counts else "",
                        str(listener.get("SslPolicy") or ""),
                        ",".join(cert_list),
                    )
                ),
            )
        )
    return [r[1] for r in sorted(rows)]


def _target_group_rows(groups: list[dict[str, Any]]) -> list[str]:
    """콘솔 대상 그룹 「세부 정보」·「상태 검사」 한 줄 = 「이름 \t 프로토콜 \t 포트 \t 대상 유형 \t 상태 검사 프로토콜 \t
    상태 검사 포트 \t 상태 검사 경로 \t 정상 임계 \t 비정상 임계 \t 간격(초) \t 제한 시간(초) \t 성공 코드 \t 상태 검사 켜짐(true/false)」 — 이름 순.
    """
    rows = []
    for g in groups:
        matcher = g.get("Matcher") or {}
        rows.append(
            "\t".join(
                str(v) if v is not None else ""
                for v in (
                    g.get("TargetGroupName"),
                    g.get("Protocol"),
                    g.get("Port"),
                    g.get("TargetType"),
                    g.get("HealthCheckProtocol"),
                    g.get("HealthCheckPort"),
                    g.get("HealthCheckPath"),
                    g.get("HealthyThresholdCount"),
                    g.get("UnhealthyThresholdCount"),
                    g.get("HealthCheckIntervalSeconds"),
                    g.get("HealthCheckTimeoutSeconds"),
                    matcher.get("HttpCode") or matcher.get("GrpcCode"),
                    None
                    if g.get("HealthCheckEnabled") is None
                    else ("true" if g.get("HealthCheckEnabled") else "false"),
                )
            )
        )
    return sorted(rows)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # elbv2 는 태그를 같이 주지 않는다(`DescribeTags` 는 별도 권한) → 이름은 LoadBalancerName.
    # native_id 에 ARN 을 쓸 수 없다: node_id 는 `/` 를 구분자로 쓴다(스키마 패턴).
    owner = ctx.account_id
    native = item["LoadBalancerName"]
    nid = node_id("load_balancer", owner, ctx.region, native)
    zones = item.get("AvailabilityZones") or []
    state = str((item.get("State") or {}).get("Code") or "")
    subnets = _sorted(z.get("SubnetId") for z in zones)
    edges = [
        EdgeDraft(
            "load_balancer_placement",
            nid,
            subnet,
            "subnet",
            False,
            {"state": state},
            live=alive(state),
        )
        for subnet in subnets
    ]
    attributes: dict[str, Any] = {
        "state": state,
        "scheme": item.get("Scheme"),
        "load_balancer_type": item.get("Type"),
        "dns_name": item.get("DNSName"),
        "availability_zones": _sorted(z.get("ZoneName") for z in zones),
        "subnet_ids": subnets,
        "security_group_ids": _sorted(item.get("SecurityGroups") or []),
    }
    # 후속 호출이 실패하면 키 자체가 없다(= 못 읽었다). 읽었는데 0개면 `[]`(= 없다).
    listeners = item.get("Listeners")
    if listeners is not None:
        attributes["listeners"] = _listener_labels(listeners)
        attributes["listener_details"] = _listener_rows(item)
    # 🔴 키가 있으면 읽은 것이다(값 `None` = 「붙지 않았다」). 키가 없으면 ALB 가 아니거나 못 읽었다.
    if "WebAclArn" in item:
        attributes["web_acl_arn"] = item["WebAclArn"]
    # 2.8.0 — 이 LB 를 뒤에 둔 엔드포인트 서비스 이름(GWLB·NLB 만 · 키가 없으면 못 읽었거나 묻지 않는 종류). GWLB 엔드포인트 → 이 LB 선의 열쇠
    if _ENDPOINT_SERVICES in item:
        attributes["endpoint_service_names"] = list(item[_ENDPOINT_SERVICES])
    target_groups = item.get("TargetGroups")
    if target_groups is not None:
        attributes["target_groups"] = _sorted(
            tg.get("TargetGroupName") for tg in target_groups
        )
        attributes["target_group_details"] = _target_group_rows(target_groups)
    registered = item.get(_REGISTERED)
    if registered is not None:
        # 등록 대상 수(구조 · 사용자 결정 2026-10-16). 그룹별 수는 `target_groups`(이름 오름차순)와 **같은 자리**다 —
        # 스키마 속성은 스칼라·스칼라 배열뿐이라(중첩 객체 금지) 이름 → 수 사전 대신 나란한 배열로 싣는다.
        by_name = {name: n for name, n in registered if name}
        attributes["target_group_registered_target_counts"] = [
            by_name[name] for name in sorted(by_name)
        ]
        counts = [n for _, n in registered]
        if all(n is not None for n in counts):
            # 🔴 한 그룹이라도 못 읽었으면 합을 싣지 않는다 — 키가 없다 = 「모른다」(0 은 「대상 없음」이라는 사실이다).
            attributes["registered_target_count"] = sum(
                n for n in counts if n is not None
            )
    node = Node(
        id=nid,
        type="load_balancer",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("LoadBalancerArn"),
        name=native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(
            node_id("vpc", owner, ctx.region, str(item.get("VpcId"))),
            ctx.region_node(owner),
        ),
        parent_types=("vpc", "region"),
        edges=edges,
        # 대상 정체(구조에 그대로 싣지 않는다) — 3패스가 대상 노드마다 선 하나로 묶는다(`nettopo/lb_targets.py`).
        lb_targets=tuple(item.get(_TARGETS) or ()),
        # 읽은 대상 그룹(등록 0 포함 · 2.8.0) — 그룹을 든 ECS 서비스·ASG 를 등록 수와 무관하게 잇는다. 못 읽었으면 비었다.
        lb_groups=_group_refs(target_groups, item.get(_TG_CLUSTERS))
        if target_groups is not None
        else (),
    )


SERVICE = Service(
    spec=SPEC,
    build=build,
    enrich=enrich,
    state_attributes=state_attributes,
    peer_enrich=(("wafv2", join_web_acl), ("ec2", join_endpoint_services)),
)
