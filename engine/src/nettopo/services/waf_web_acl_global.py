"""waf_web_acl_global(WAF 웹 ACL · **CloudFront 범위**) 수집 — 「이음매 2」 · 스키마 2.12.0 · ADR 0014 · **여섯째 글로벌 리소스**.

노드 종류는 리전 범위와 같은 `waf_web_acl`(속성 `scope` = `cloudfront`)이고 짓는 법은 `waf_web_acl.py` 가 든다 — 이 파일은 **범위와 리전**만 다르다.

🔴 **API 리전 = us-east-1 고정**(`Spec.region`). AWS: 「CloudFront 범위 web ACL 은 us-east-1 엔드포인트에서 `Scope=CLOUDFRONT` 로 부른다」
(WAF 개발자 안내서 ARN 절 「For AWS WAF resources that you use to protect Amazon CloudFront distributions, set this to us-east-1」).
수집 리전으로 클라이언트를 만들면 서울만 고른 고객은 CloudFront 범위 web ACL 이 통째로 `WAFInvalidParameterException` 이다.
IAM 은 CloudFront 배포와 같은 us-east-1 고정 문장(`ReadCloudFrontGlobal` · `aws:RequestedRegion` = us-east-1)에 `ListWebACLs`·`GetWebACL` 을 더했다 —
고객이 수집 리전에 us-east-1 을 안 넣어도 이 카드가 「권한 없음」이 되지 않는다(이슈 #42 와 같은 처분 · 스택 A 본문 한도 때문에 새 문장 대신 액션만 더했다).

글로벌 규약(CloudFront·Global Accelerator 와 같다): id 의 리전 자리 = `global` · region null · 부모 = 계정 · 어느 수집 쌍에서 봐도 id 가 같아
먼저 만난 것이 남는다(`collect._resolve`). 보호 대상(CloudFront 배포)은 여기서 묻지 않는다 — 배포의 `web_acl_id` 를 3패스가 잇는다
(`relations.waf_protections` · `ListResourcesForWebACL` 는 CloudFront 범위를 지원하지 않는다).
"""

from __future__ import annotations

from typing import Any

from .base import Ctx, NodeDraft, Service, Spec
from .waf_web_acl import LIST_API, build_for, enrich_for

API_REGION = "us-east-1"
SPEC = Spec(
    "wafv2",
    "list_web_acls",
    "WebACLs",
    LIST_API,
    params=(("Scope", "CLOUDFRONT"),),
    region=API_REGION,
)

_BUILD = build_for("CLOUDFRONT")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # 빌더는 이 파일에 있어야 한다(「파일 하나 = 수집 종류 하나」 · `test_registry_module_matches_type`) — 짓는 법은 리전 범위와 같다
    return _BUILD(item, ctx)


SERVICE = Service(
    spec=SPEC,
    build=build,
    enrich=enrich_for("CLOUDFRONT", resources=False),
)
