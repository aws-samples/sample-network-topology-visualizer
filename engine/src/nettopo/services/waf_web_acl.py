"""waf_web_acl(WAF 웹 ACL · **리전 범위**) 수집 — 「이음매 2」 · 스키마 2.12.0 · ADR 0014.

사용자 결정 2026-10-23: 「캔버스만 보면 WAF 가 없는 것처럼 보인다」 → WAF 웹 ACL 을 **노드**로 만든다. 그전에는 ALB·CloudFront
카드의 속성 한 줄이었다(`load_balancer.web_acl_arn` · `cloudfront.web_acl_id` — 그 둘은 **그대로 둔다**: 정보창 줄과 아래 3패스 잇기의 재료다).

범위가 둘이라 수집 종류도 둘이다(`api_gateway`·`api_gateway_v2` 와 같은 모양 — 목록 API 는 같고 인자·리전이 다르다 · 거부가 따로 보인다):
- **이 파일** = `Scope=REGIONAL` · 수집 리전마다 · 부모 = 리전 노드(화면 = 리전 자원 줄).
- `waf_web_acl_global.py` = `Scope=CLOUDFRONT` · **us-east-1 고정**(`Spec.region` · AWS: CloudFront 범위 web ACL 은 us-east-1 에서만 부른다) ·
  글로벌 규약(id 리전 자리 `global` · region null · 부모 = 계정 · 화면 = 전역 줄).
같은 노드 종류 `waf_web_acl` 이고 속성 `scope`(`regional`·`cloudfront`)가 가른다.

엣지 `waf_protection`(web ACL → 보호 대상 · 방향 있음 · 「이 앞에서 거른다」). 🔴 **대상마다 사실의 출처가 하나다** — 두 길이 다르게 말하면
화면 두 곳이 다른 말을 한다(ADR 0014 결정 3):
- **ALB** = ALB 쪽 `web_acl_arn`(`GetWebACLForResource` · 2026-10-04 실측으로 권한·응답을 잰 길)을 3패스가 잇는다(`relations.waf_protections`).
  `ListResourcesForWebACL(APPLICATION_LOAD_BALANCER)` 는 **부르지 않는다** — AWS 의 「새 권한 설정」에서는 그 호출이
  `elasticloadbalancing:DescribeWebACLAssociation` 을 더 요구한다(WAF 개발자 안내서 「Permissions for ListResourcesForWebACL」) —
  아직 실계정에서 재지 않은 권한에 선을 걸지 않는다. 같은 사실을 두 번 묻지도 않는다.
- **CloudFront** = 배포 쪽 `web_acl_id`(목록 응답에 같이 온다 · 추가 호출 0)를 3패스가 잇는다. `ListResourcesForWebACL` 는 CloudFront 범위를
  지원하지 않는다(botocore 모델 문서 「For Amazon CloudFront, don't use this call」 · 실측 2026-10-23).
- **API Gateway(REST 스테이지)** = 여기서 `ListResourcesForWebACL(ResourceType=API_GATEWAY)` 를 web ACL 마다 1회. 스테이지 ARN
  (`arn:aws:apigateway:<리전>::/restapis/<id>/stages/<스테이지>`) → 그 REST API 노드(같은 계정 · 같은 리전 — web ACL 연결은 같은 계정·리전
  안에서만 된다). 같은 API 의 여러 스테이지는 선 하나(`stages` 정렬). 이 스냅샷에 없는 API 는 지어내지 않는다 —
  `unresolved_protected` = 「<API id> not_collected」(3패스).
- AppSync · Cognito · App Runner · Verified Access · Amplify · AgentCore 는 **묻지 않는다**(그리지 않는 종류 · 서비스마다 권한이 따로다).
  🔴 그래서 선이 하나도 없는 web ACL 을 「어디에도 안 붙었다」고 말하면 안 된다 — 「우리가 그리는 자원(CloudFront·ALB·API Gateway)에는 붙지
  않았다」가 사실이다(화면 문구는 렌더 단계 · ADR 0014 결정 5).

후속 호출 `GetWebACL`(이미 허용된 권한 — `GetWebACLForResource` 의 딸린 권한으로 허브 읽기 권한 세트·스택 B 에 있다): 기본 동작 · 규칙 수 ·
규칙 이름(우선순위 순) · 관리형 규칙 그룹 · Firewall Manager 관리 여부 · WCU. 🔴 구성만 싣는다 — `LockToken`(바뀔 때마다 새 값)·규칙 본문·
`VisibilityConfig`·사용자 응답 본문은 싣지 않는다. 거부 = 그 web ACL 에 사유(`partial`) + 그 속성 키 없음(지어내지 않는다 · 불변식 ④).
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import GLOBAL, Node, node_id
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
)

LIST_API = "wafv2:ListWebACLs"
GET_API = "wafv2:GetWebACL"
RESOURCES_API = "wafv2:ListResourcesForWebACL"
EDGE = "waf_protection"
WHY_ATTR = "unresolved_protected"
SPEC = Spec(
    "wafv2", "list_web_acls", "WebACLs", LIST_API, params=(("Scope", "REGIONAL"),)
)
_DETAIL = "_WebAcl"
_STAGES = "_ApiStages"
# AWS `Scope` → 노드 속성 `scope`(소문자 · 화면이 「리전」·「CloudFront」로 말한다)
_SCOPE_ATTR = {"REGIONAL": "regional", "CLOUDFRONT": "cloudfront"}


def acl_node_id(arn: str) -> str | None:
    """WAFv2 web ACL ARN → 노드 id. 꼴이 아니면 None(WAF Classic 의 id · 빈 값).

    `arn:aws:wafv2:<리전>:<계정>:regional/webacl/<이름>/<id>` → `waf_web_acl/<계정>/<리전>/<id>` ·
    `…:global/webacl/<이름>/<id>`(CloudFront 범위 · 리전 자리는 언제나 us-east-1) → `waf_web_acl/<계정>/global/<id>`.
    """
    parts = str(arn or "").split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[2] != "wafv2":
        return None
    res = parts[5].split("/")
    if len(res) != 4 or res[1] != "webacl" or res[0] not in ("regional", "global"):
        return None
    region = GLOBAL if res[0] == "global" else parts[3]
    return node_id("waf_web_acl", parts[4], region, res[3])


def _api_of(stage_arn: str) -> tuple[str, str] | None:
    """API Gateway 스테이지 ARN → (REST API id, 스테이지 이름). 꼴이 아니면 None."""
    parts = str(stage_arn).split(":", 5)
    if len(parts) != 6 or parts[2] != "apigateway":
        return None
    res = parts[5].split("/")
    # `/restapis/<id>/stages/<스테이지>` → ['', 'restapis', id, 'stages', 스테이지]
    if len(res) != 5 or res[1] != "restapis" or res[3] != "stages":
        return None
    return res[2], res[4]


def enrich_for(scope: str, *, resources: bool) -> Any:
    """범위마다 같은 후속 호출 — `GetWebACL`(+ 리전 범위면 `ListResourcesForWebACL(API_GATEWAY)`)."""

    def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
        out: list[Fetched] = []
        for it in items:
            p = it.payload
            reasons = list(it.reasons)
            payload = dict(p)
            try:
                got = client.get_web_acl(
                    Name=str(p.get("Name")), Scope=scope, Id=str(p.get("Id"))
                )
                payload[_DETAIL] = got.get("WebACL") or {}
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, GET_API)[1])
            if resources:
                try:
                    arns = client.list_resources_for_web_acl(
                        WebACLArn=str(p.get("ARN")), ResourceType="API_GATEWAY"
                    ).get("ResourceArns")
                    payload[_STAGES] = sorted(str(a) for a in arns or [])
                except (ClientError, BotoCoreError) as exc:
                    # 🔴 못 읽음 ≠ 안 붙음 — 키를 만들지 않는다(선 없이 사유만 · 불변식 ④)
                    reasons.append(classify(exc, RESOURCES_API)[1])
            out.append(Fetched(payload, tuple(reasons)))
        return out

    return enrich


def _action(a: dict[str, Any] | None) -> str | None:
    """`DefaultAction` = `{"Allow": {...}}` 또는 `{"Block": {...}}` → 「allow」·「block」. 모르면 None."""
    keys = sorted(k for k in (a or {}) if k in ("Allow", "Block"))
    return keys[0].lower() if len(keys) == 1 else None


_RULE_ACTIONS = ("Allow", "Block", "Count", "Captcha", "Challenge")


def _rule_action(r: dict[str, Any]) -> str:
    """콘솔 「규칙」 표의 「작업」 칸 재료 — 규칙 동작(`Action`)이면 그 이름 · 규칙 그룹이면 `OverrideAction`
    (`None` = 「그룹의 동작 그대로」 → 「none」 · `Count` = 「재정의: 개수」 → 「override:count」). 모르면 빈 칸."""
    action = r.get("Action") or {}
    keys = [k for k in _RULE_ACTIONS if k in action]
    if keys:
        return keys[0].lower()
    override = r.get("OverrideAction") or {}
    if "Count" in override:
        return "override:count"
    if "None" in override:
        return "none"
    return ""


def _detail_attrs(detail: dict[str, Any]) -> dict[str, Any]:
    rules = sorted(
        detail.get("Rules") or [],
        key=lambda r: (int(r.get("Priority") or 0), str(r.get("Name") or "")),
    )
    managed = {
        f"{m.get('VendorName')}/{m.get('Name')}"
        for r in rules
        for m in [(r.get("Statement") or {}).get("ManagedRuleGroupStatement")]
        if m and m.get("VendorName") and m.get("Name")
    }
    attrs: dict[str, Any] = {
        "rule_count": len(rules),
        # 우선순위 순(= AWS 가 평가하는 순서) — 정보창이 그 순서로 보여 준다. 이름 정렬로 바꾸지 마라
        "rule_names": [str(r.get("Name")) for r in rules if r.get("Name")],
        # 콘솔 「규칙」 표 한 줄 = 「우선순위 \t 이름 \t 작업」(우선순위 순 · 사용자 결정 2026-10-24) — 작업은 `_rule_action`
        "rules": [
            "\t".join(
                (
                    str(int(r.get("Priority") or 0)),
                    str(r.get("Name") or ""),
                    _rule_action(r),
                )
            )
            for r in rules
        ],
        "managed_rule_groups": sorted(managed),
        "firewall_manager": bool(detail.get("ManagedByFirewallManager")),
    }
    action = _action(detail.get("DefaultAction"))
    if action is not None:
        attrs["default_action"] = action
    if isinstance(detail.get("Capacity"), int):
        attrs["capacity"] = detail["Capacity"]
    return attrs


def build_for(scope: str) -> Any:
    def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
        owner = ctx.account_id
        native = str(item["Id"])
        glob = scope == "CLOUDFRONT"
        nid = node_id("waf_web_acl", owner, GLOBAL if glob else ctx.region, native)
        attributes: dict[str, Any] = {"scope": _SCOPE_ATTR[scope]}
        if _DETAIL in item:
            attributes.update(_detail_attrs(item[_DETAIL]))
        edges: list[EdgeDraft] = []
        stages = item.get(_STAGES)
        if stages is not None:
            per: dict[str, set[str]] = {}
            for arn in stages:
                got = _api_of(arn)
                if got is not None:
                    per.setdefault(got[0], set()).add(got[1])
            for api in sorted(per):
                edges.append(
                    EdgeDraft(
                        EDGE,
                        nid,
                        node_id("api_gateway", owner, ctx.region, api),
                        None,
                        True,  # web ACL 이 그 API 앞에서 거른다
                        {"stages": sorted(per[api])},
                        by_id=True,
                        ref=api,
                        why_attr=WHY_ATTR,
                    )
                )
        node = Node(
            id=nid,
            type="waf_web_acl",
            account_id=owner,
            region=None if glob else ctx.region,
            native_id=native,
            arn=item.get("ARN"),
            name=item.get("Name") or native,
            attributes=attributes,
        )
        return draft(
            node=node,
            ctx=ctx,
            parents=(node_id("account", owner),) if glob else (ctx.region_node(owner),),
            parent_types=("account",) if glob else ("region",),
            edges=edges,
        )

    return build


SERVICE = Service(
    spec=SPEC,
    build=build_for("REGIONAL"),
    enrich=enrich_for("REGIONAL", resources=True),
)
