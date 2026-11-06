"""cloudfront(CloudFront 배포) 수집 — 「이음매 2」, **두 번째 글로벌 리소스**.

DXGW(ADR 0010)가 세운 글로벌 규약을 그대로 쓴다: **id 의 리전 자리는 `GLOBAL`**, `region` 은
null, 부모는 **계정 노드**. 어느 리전에서 봐도 id 가 같으므로 `collect._resolve` 의 「먼저 만난
것을 남긴다」가 중복 제거가 되고, coverage 는 리전마다 남는다(호출이 실제로 리전마다 일어났고
각 호출의 성공·실패는 다른 사실이다).

🔴 **DXGW 와 다른 점 — 엔드포인트가 정말로 하나다.** DXGW 는 *리전* 엔드포인트가 우연히 같은
목록을 주는 것이고, CloudFront 는 `cloudfront.amazonaws.com` **한 곳**이다. 실측 2026-09-30
(botocore 1.4x, 자격증명 없이 서명만):

- `client("cloudfront", region_name="ap-northeast-2")` → `client.meta.region_name` 이
  **`aws-global`** 로 접히고 엔드포인트는 `https://cloudfront.amazonaws.com` 이다.
- 그런데 SigV4 **credential scope 의 리전은 `us-east-1`** 이다
  (`…/20260930/us-east-1/cloudfront/aws4_request`).

여기서 나오는 것이 아래 두 가지다.

🔴 **① IAM 의 `aws:RequestedRegion` 조건이 이 호출을 막을 수 있다.** 스택 A·B 의
`ReadNetworkTopology` 는 수집 리전 목록으로 조건을 걸고 있으므로(`TargetRegions`), 고객이
`us-east-1` 을 목록에 넣지 않으면 CloudFront 만 거부될 수 있다. 화면에서 일어나는 일: 리전
레인은 다 정상인데 CloudFront 카드만 통째로 없고 coverage 만 `denied` 다. **어떤 값이 실제로
비교되는지(`us-east-1` 인가 `aws-global` 인가)는 아직 실계정에서 재지 않았다** — 추측으로 정책을
쓰지 않기 위해 `docs/required-permissions.md` §1 에 🔴 로 적고, 템플릿 변경은 그 실측 뒤로 미룬다.

🔴 **② 리전 수만큼 같은 엔드포인트를 때린다.** DXGW 는 리전별 엔드포인트라 throttle 버킷이
갈리지만 CloudFront 는 한 버킷이다. 30계정 × 3리전이면 같은 곳에 90번이다. 지금은 거부돼도
그 (계정,리전) coverage 만 `throttled` 가 되고 노드는 남으므로(불변식 ④) **고치지 않고 적어
둔다** — 리전 1개에서만 부르는 최적화는 `collect.py` 의 곱집합을 건드리는 일이라 별 PR 이다.

🔴 **구성만 싣는다.** `Status`(`Deployed`/`InProgress`)는 **배포 상태**이고 `LastModifiedTime` 은
wall-clock 이다 → 둘 다 담지 않는다(불변식 ②·관측 계약 ADR 0006 소관). `ETag` 도 버린다.
`Enabled` 는 사람이 정한 **설정**이므로 담는다 — ASG 의 `desired_capacity` 와 같은 취급이다.
「켜져 있다」와 「배포가 끝났다」를 한 칸에 합치지 마라.

~~🔴 엣지는 만들지 않는다(#39)~~ → 🔁 **원본이 로드밸런서면 `origin` 엣지를 만든다(#43).** 오리진은
`Origins.Items[].DomainName` = **DNS 이름**이고 LB 의 `native_id` 는 **LB 이름**이라(`load_balancer.py`)
native id 색인으로는 100% 못 찾는다 → `EdgeDraft.by_dns` 로 3패스의 **DNS 이름 색인**(LB 의 `dns_name`
속성)에서 찾는다. DNS 이름은 AWS 전체에서 하나라 **다른 계정의 LB 도** 같은 색인에서 찾는다.

🔴 **엣지를 만드는 원본은 LB 주소 꼴뿐이다**(`_LB_DNS`). S3·사용자 도메인·API Gateway 원본에도 만들면
색인에 없어 전부 `unresolved_refs` 가 된다 — 「못 찾았다」가 아니라 「우리가 그리는 종류가 아니다」인데
같은 칸에 쌓여 그 칸이 아무것도 말하지 않게 된다. 사용자 도메인이 뒤에서 ALB 를 가리켜도 **DNS 를 풀지
않는다**(엔진은 AWS API 만 읽는다) — 그 원본은 도메인 글자로만 남는다. LB 꼴인데 색인에 없으면(수집하지
않은 계정·리전 · Classic LB) 그때만 `unresolved_refs` 다 — 그것은 정보다.

🔵 **VPC 원본**(2.8.0 · 사용자 결정 2026-10-20 P2-3) — 원본이 `VpcOriginConfig.VpcOriginId` 를 들면 `cloudfront:ListVpcOrigins`(후속 호출 ·
VPC 원본이 있는 쌍에서만 · `Marker` 로 끝까지)의 `OriginEndpointArn` 이 사설 ALB·NLB·EC2 를 말한다 → `origin` 엣지(`via` = `vpc_origin`).
거부 = VPC 원본을 쓰는 배포에 사유(불변식 ④). S3 원본 · 사용자 도메인 → Route 53 별칭 → LB 는 3패스가 잇는다(`nettopo/origins.py`).

🔵 **WAF 는 여기의 한 속성이다**(`WebACLId`) — 🔁 2.12.0(ADR 0014 · 사용자 결정 2026-10-23)부터 web ACL 은 따로 **카드**가 되고
(`services/waf_web_acl_global.py`), 3패스가 이 속성으로 web ACL → 배포 선(`waf_protection`)을 잇는다(`relations.waf_protections`). 속성은 그대로 둔다.
"""

from __future__ import annotations

import re
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
    dns_key,
    draft,
)
from .base import sorted_strs as _sorted

# ELBv2 의 DNS 이름 꼴 두 가지 — ALB `<이름>-<숫자>.<리전>.elb.amazonaws.com` ·
# NLB `<이름>-<해시>.elb.<리전>.amazonaws.com`(중국 리전은 끝에 `.cn`). `dns_key()` 로 접은 뒤 잰다.
_LB_DNS = re.compile(r"\.elb\.(?:[a-z0-9-]+\.)?amazonaws\.com(?:\.cn)?$")

SPEC = Spec(
    "cloudfront",
    "list_distributions",
    # 🔴 점이 있는 것이 계약이다 — 항목이 `DistributionList.Items` 에 **중첩**돼 있고
    #    boto3 페이지네이터의 `result_key` 자신이 이 점 표기다(실측 2026-09-30).
    #    평평한 키로 적으면 `base.dig()` 가 빈 목록을 주고 「배포가 없다」로 그려진다.
    "DistributionList.Items",
    "cloudfront:ListDistributions",
)


VPC_ORIGINS_API = "cloudfront:ListVpcOrigins"
_VPC_ORIGINS = "_VpcOrigins"


def _vpc_origin_ids(item: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str((o.get("VpcOriginConfig") or {}).get("VpcOriginId"))
            for o in _items(item.get("Origins"))
            if isinstance(o, dict)
            and (o.get("VpcOriginConfig") or {}).get("VpcOriginId")
        }
    )


CACHE_POLICIES_API = "cloudfront:ListCachePolicies"
OACS_API = "cloudfront:ListOriginAccessControls"
_CACHE_POLICY_NAMES = "_CachePolicyNames"
_OAC_NAMES = "_OacNames"


def _behaviors(item: dict[str, Any]) -> list[dict[str, Any]]:
    """평가 순서대로의 동작(Behaviors) — 경로 패턴이 있는 것들(설정 순서 = AWS 가 고르는 순서) 다음에 기본 동작."""
    out = [b for b in _items(item.get("CacheBehaviors")) if isinstance(b, dict)]
    default = item.get("DefaultCacheBehavior")
    if isinstance(default, dict):
        out.append({**default, "PathPattern": ""})
    return out


def _cache_policy_ids(item: dict[str, Any]) -> list[str]:
    return sorted(
        {str(b["CachePolicyId"]) for b in _behaviors(item) if b.get("CachePolicyId")}
    )


def _oac_ids(item: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(o["OriginAccessControlId"])
            for o in _items(item.get("Origins"))
            if isinstance(o, dict) and o.get("OriginAccessControlId")
        }
    )


def _list_names(client: Any, method: str, list_key: str, pick: Any) -> dict[str, str]:
    """CloudFront 목록 API 하나를 `Marker` 로 끝까지 읽어 id → 이름. 실패는 부른 쪽이 사유로 바꾼다."""
    found: dict[str, str] = {}
    params: dict[str, Any] = {}
    seen: set[str] = set()
    while True:
        page = (getattr(client, method)(**params) or {}).get(list_key) or {}
        for v in page.get("Items") or []:
            got = pick(v)
            if got and got[0] and got[1]:
                found[str(got[0])] = str(got[1])
        marker = page.get("NextMarker")
        if (
            not marker or marker in seen
        ):  # 같은 표지가 되돌아오면 멈춘다(끝없는 반복 방지)
            break
        seen.add(marker)
        params = {"Marker": marker}
    return found


def _enrich_names(client: Any, items: list[Fetched]) -> list[Fetched]:
    """캐시 정책 이름(`ListCachePolicies`) · 원본 접근 제어 이름(`ListOriginAccessControls`) — 콘솔 「동작」·「원본」 표의
    이름 칸(사용자 결정 2026-10-24). 그 id 를 쓰는 배포가 있을 때만 한 번(끝까지) 읽는다. 🔴 거부 = 그 id 를 쓰는 배포에
    사유(불변식 ④) — 이름을 비워 두면 화면이 id 만 보이고 「못 읽었다」가 사라진다.
    """
    jobs = (
        (
            _cache_policy_ids,
            "list_cache_policies",
            "CachePolicyList",
            CACHE_POLICIES_API,
            _CACHE_POLICY_NAMES,
            lambda v: (
                (v.get("CachePolicy") or {}).get("Id"),
                ((v.get("CachePolicy") or {}).get("CachePolicyConfig") or {}).get(
                    "Name"
                ),
            ),
        ),
        (
            _oac_ids,
            "list_origin_access_controls",
            "OriginAccessControlList",
            OACS_API,
            _OAC_NAMES,
            lambda v: (v.get("Id"), v.get("Name")),
        ),
    )
    for want_of, method, list_key, api, key, pick in jobs:
        if not any(want_of(it.payload) for it in items):
            continue
        reason = None
        found: dict[str, str] = {}
        try:
            found = _list_names(client, method, list_key, pick)
        except (ClientError, BotoCoreError) as exc:
            reason = classify(exc, api)[1]
        out: list[Fetched] = []
        for it in items:
            want = want_of(it.payload)
            if not want:
                out.append(it)
            elif reason is not None:
                out.append(Fetched(it.payload, (*it.reasons, reason)))
            else:
                out.append(
                    Fetched(
                        {**it.payload, key: {k: found[k] for k in want if k in found}},
                        it.reasons,
                    )
                )
        items = out
    return items


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """VPC 원본 엔드포인트 ARN(아래) + 캐시 정책·원본 접근 제어 이름(`_enrich_names`)."""
    return _enrich_names(client, _enrich_vpc_origins(client, items))


def _enrich_vpc_origins(client: Any, items: list[Fetched]) -> list[Fetched]:
    """VPC 원본을 쓰는 배포가 있을 때만 `ListVpcOrigins` 를 한 번(끝까지) 읽어 원본 id → 엔드포인트 ARN 을 붙인다."""
    if not any(_vpc_origin_ids(it.payload) for it in items):
        return items
    found: dict[str, str] = {}
    reason = None
    try:
        params: dict[str, Any] = {}
        while True:
            page = (client.list_vpc_origins(**params) or {}).get("VpcOriginList") or {}
            for v in page.get("Items") or []:
                if v.get("Id") and v.get("OriginEndpointArn"):
                    found[str(v["Id"])] = str(v["OriginEndpointArn"])
            marker = page.get("NextMarker")
            if not marker:
                break
            params = {"Marker": marker}
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, VPC_ORIGINS_API)[1]
    out: list[Fetched] = []
    for it in items:
        want = _vpc_origin_ids(it.payload)
        if not want:
            out.append(it)
        elif reason is not None:
            out.append(Fetched(it.payload, it.reasons + (reason,)))
        else:
            out.append(
                Fetched(
                    {
                        **it.payload,
                        _VPC_ORIGINS: {k: found[k] for k in want if k in found},
                    },
                    it.reasons,
                )
            )
    return out


def _vpc_origin_edges(source: str, item: dict[str, Any]) -> list[EdgeDraft]:
    """VPC 원본 → 사설 ALB·NLB(이름) · EC2(인스턴스 id). 모르는 ARN 꼴은 짓지 않는다."""
    out: list[EdgeDraft] = []
    for arn in sorted(set((item.get(_VPC_ORIGINS) or {}).values())):
        parts = arn.split(":", 5)
        res = parts[5] if len(parts) == 6 else ""
        if res.startswith("loadbalancer/") and len(res.split("/")) > 2:
            target, kind = res.split("/")[2], "load_balancer"
        elif res.startswith("instance/"):
            target, kind = res.split("/", 1)[1], "ec2_instance"
        else:
            continue
        out.append(
            EdgeDraft("origin", source, target, kind, True, {"via": "vpc_origin"})
        )
    return out


def _items(value: Any) -> list[Any]:
    """CloudFront 응답의 `{Quantity, Items}` 꼴 → 목록. `Items` 가 없으면 0개다.

    🔴 `Quantity` 를 믿지 않는다 — 개수는 `Items` 의 길이가 정본이다. 둘이 어긋나면 응답을
    믿는 쪽이 맞고, `Quantity` 만 읽으면 실제로 받은 항목과 다른 수를 그린다.
    """
    if not isinstance(value, dict):
        return []
    return list(value.get("Items") or [])


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # CloudFront API 는 계정-로컬이라 소유자 필드를 주지 않는다 → 조회 계정이 소유자다(ASG 와 같다).
    owner = ctx.account_id
    native = item["Id"]
    origins = _items(item.get("Origins"))
    node = Node(
        id=node_id("cloudfront", owner, GLOBAL, native),
        type="cloudfront",
        account_id=owner,
        region=None,
        native_id=native,
        arn=item.get("ARN"),
        # 이름표는 배포 도메인이다 — CloudFront 에는 Name 태그 개념이 응답에 없고, 사람이 화면에서
        # 배포를 알아보는 이름이 `d….cloudfront.net` 이다(렌더러 픽스처도 그 값을 이름으로 쓴다).
        name=item.get("DomainName"),
        attributes={
            "domain_name": item.get("DomainName"),
            # 사용자가 붙인 대체 도메인(CNAME). 어떤 이름으로 이 배포에 들어오는지가 구성이다.
            "aliases": _sorted(_items(item.get("Aliases"))),
            # 오리진이 어디를 가리키나 — 글자 그대로. LB 꼴은 아래 `origin` 엣지가 같이 든다(파일 머리말).
            "origin_domain_names": _sorted(o.get("DomainName") for o in origins),
            "price_class": item.get("PriceClass"),
            "enabled": item.get("Enabled"),
            "is_ipv6_enabled": item.get("IsIPV6Enabled"),
            "http_version": item.get("HttpVersion"),
            # WAF 는 종류가 아니라 이 한 줄이다(파일 머리말 🔵). 붙지 않은 배포는 빈 문자열을 준다
            # → falsy 를 None 으로 접어 「없다」를 한 모양으로 만든다(불변식 ②).
            "web_acl_id": item.get("WebACLId") or None,
            # 콘솔 목록의 「설명」 — 사람이 배포를 알아보는 이름이다(도메인 `d….cloudfront.net` 만으로는 못 가른다 ·
            # 사용자 결정 2026-10-24). 비어 있으면 None(「없다」를 한 모양으로).
            "comment": item.get("Comment") or None,
            "origins": _origin_rows(origins, item.get(_OAC_NAMES)),
            "behaviors": _behavior_rows(item, item.get(_CACHE_POLICY_NAMES)),
        },
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
        # 🔴 VPC 원본 먼저 — 실제 VPC 원본의 도메인은 그 ALB 의 DNS 라 주소 꼴 엣지와 id 가 같다. 먼저 생긴 것이 남으니
        #    (`collect._resolve_edges`) 뒤에 두면 `via: vpc_origin` 이 조용히 사라진다(P2-3 · ADR 0002 §7-1 ④)
        edges=_vpc_origin_edges(node.id, item) + _origin_edges(node.id, origins),
    )


def _origin_type(o: dict[str, Any]) -> str:
    """콘솔 「원본 유형」의 재료 — `vpc`(VPC 원본) · `s3`(S3 원본 설정) · `custom`(사용자 정의) · 모르면 빈 칸."""
    if (o.get("VpcOriginConfig") or {}).get("VpcOriginId"):
        return "vpc"
    if "S3OriginConfig" in o:
        return "s3"
    if "CustomOriginConfig" in o:
        return "custom"
    return ""


def _origin_rows(origins: list[Any], oac_names: Any) -> list[str]:
    """콘솔 「원본」 표 한 줄 = 「원본 이름(Id) \t 도메인 \t 유형 \t 원본 경로 \t 원본 접근 제어 id \t 그 이름」 — 설정 순서.

    이름을 못 읽었으면(`ListOriginAccessControls` 거부 · 노드 partial) 이름 칸이 빈 칸이다 — 사유는 노드가 든다.
    """
    names = oac_names if isinstance(oac_names, dict) else {}
    rows = []
    for o in origins:
        if not isinstance(o, dict):
            continue
        oac = str(o.get("OriginAccessControlId") or "")
        rows.append(
            "\t".join(
                (
                    str(o.get("Id") or ""),
                    str(o.get("DomainName") or ""),
                    _origin_type(o),
                    str(o.get("OriginPath") or ""),
                    oac,
                    str(names.get(oac, "")) if oac else "",
                )
            )
        )
    return rows


def _behavior_rows(item: dict[str, Any], policy_names: Any) -> list[str]:
    """콘솔 「동작」 표 한 줄 = 「우선순위 \t 경로 패턴 \t 원본 \t 뷰어 프로토콜 정책 \t 캐시 정책 id \t 그 이름」.

    순서 = 우선순위(AWS 가 고르는 순서) · 기본 동작은 맨 끝이고 경로 패턴 칸이 빈 칸이다(「Default (*)」는 화면 말).
    캐시 정책 없이 옛 `ForwardedValues` 를 쓰는 동작은 정책 칸이 빈 칸이다.
    """
    names = policy_names if isinstance(policy_names, dict) else {}
    rows = []
    for n, b in enumerate(_behaviors(item)):
        policy = str(b.get("CachePolicyId") or "")
        rows.append(
            "\t".join(
                (
                    str(n),
                    str(b.get("PathPattern") or ""),
                    str(b.get("TargetOriginId") or ""),
                    str(b.get("ViewerProtocolPolicy") or ""),
                    policy,
                    str(names.get(policy, "")) if policy else "",
                )
            )
        )
    return rows


def _origin_edges(source: str, origins: list[Any]) -> list[EdgeDraft]:
    """CloudFront → 원본 LB. 키 순으로 하나씩 — 같은 LB 를 두 꼴로 적은 원본은 선 하나다."""
    keys = {
        dns_key(o["DomainName"])
        for o in origins
        if isinstance(o, dict) and isinstance(o.get("DomainName"), str)
    }
    return [
        EdgeDraft(
            type="origin",
            source=source,
            target_native=key,
            target_type="load_balancer",
            directed=True,  # 요청은 배포에서 원본으로 간다
            attributes={},
            by_dns=True,
        )
        for key in sorted(keys)
        if _LB_DNS.search(key)
    ]


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
