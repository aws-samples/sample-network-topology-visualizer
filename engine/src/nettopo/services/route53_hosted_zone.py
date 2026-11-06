"""route53_hosted_zone(Route 53 호스팅 영역) 수집 — 「이음매 2」 · 2.8.0(사용자 결정 2026-10-20 P2-4) · **세 번째 글로벌 리소스**.

「도메인 이름 → 어느 LB · 어느 CloudFront」 진입점과 「사설 영역이 붙은 VPC」를 그린다. CloudFront 가 세운 글로벌 규약을 그대로 쓴다
(`cloudfront.py` 머리말): id 의 리전 자리 = `GLOBAL` · `region` = null · 부모 = 계정 노드 · 엔드포인트가 하나라(`route53.amazonaws.com` ·
서명 리전 us-east-1) 스택의 `aws:RequestedRegion` 조건 밖 절(`ReadGlobalServices`)로 읽는다.

엣지 둘(3패스가 잇는다):
- `dns_alias` — 영역 → 별칭(`AliasTarget`) 레코드가 가리키는 **수집한 LB · CloudFront 배포**(DNS 이름 색인 · `EdgeDraft.by_dns` · 대상마다 하나 ·
  속성 `record_names` = 그 대상을 가리키는 레코드 이름들). 🔴 LB·CloudFront 꼴(`…elb.amazonaws.com` · `….cloudfront.net`)만 — S3 웹사이트·API Gateway·
  다른 레코드 별칭은 우리가 그리는 종류가 아니다(CloudFront 원본과 같은 규칙 · 미해결로 쌓으면 그 칸이 아무것도 말하지 않는다).
- `zone_association` — **사설** 영역 → 연결된 VPC(`GetHostedZone` 의 `VPCs` · 다른 계정 VPC 도 native id 로 찾는다 · 없으면 `unresolved_refs`).

🔴 **구성만 싣는다.** 레코드의 값(IP·CNAME)은 싣지 않는다 — 고객 도메인 전체 목록을 산출물에 굽지 않는다(민감 · 별칭 이름만). `ChangeInfo`·
`CallerReference` 는 버린다. 🔴 레코드 페이지네이션은 `pages()` 가 다 돈다(큰 영역도 끝까지 · 부분 결과를 성공으로 내지 않는다).
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
    pages,
)
from .base import sorted_strs as _sorted

SPEC = Spec("route53", "list_hosted_zones", "HostedZones", "route53:ListHostedZones")
RECORDS_API = "route53:ListResourceRecordSets"
ZONE_API = "route53:GetHostedZone"
_ALIASES = "_Aliases"
_VPCS = "_Vpcs"
# 별칭 대상 중 우리가 그리는 꼴 — ELBv2(ALB·NLB) DNS · CloudFront 배포 도메인(`dns_key()` 로 접은 뒤 잰다)
_TARGET_DNS = re.compile(
    r"(\.elb\.(?:[a-z0-9-]+\.)?amazonaws\.com(?:\.cn)?|\.cloudfront\.net)$"
)


def _zone_id(raw: str) -> str:
    """`/hostedzone/Z123` → `Z123`(노드 id 세그먼트에 `/` 를 넣지 않는다)."""
    return str(raw).rsplit("/", 1)[-1]


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """영역마다 별칭 레코드(LB·CloudFront 꼴만)와 사설 영역의 VPC 를 읽는다 — 실패는 그 영역의 사유(불변식 ④ · 키를 만들지 않는다)."""
    out: list[Fetched] = []
    for it in items:
        zid = str(it.payload.get("Id") or "")
        payload = dict(it.payload)
        reasons = list(it.reasons)
        try:
            records = pages(
                client,
                "list_resource_record_sets",
                "ResourceRecordSets",
                HostedZoneId=zid,
            )
        except (ClientError, BotoCoreError) as exc:
            reasons.append(classify(exc, RECORDS_API)[1])
        else:
            aliases: dict[str, set[str]] = {}
            for r in records:
                target = (r.get("AliasTarget") or {}).get("DNSName")
                if not target or r.get("Type") not in ("A", "AAAA"):
                    continue
                key = dns_key(str(target))
                if _TARGET_DNS.search(key):
                    aliases.setdefault(key, set()).add(
                        dns_key(str(r.get("Name") or ""))
                    )
            payload[_ALIASES] = {k: sorted(v) for k, v in sorted(aliases.items())}
        if (it.payload.get("Config") or {}).get("PrivateZone"):
            try:
                got = client.get_hosted_zone(Id=zid)
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, ZONE_API)[1])
            else:
                payload[_VPCS] = sorted(
                    (str(v.get("VPCId")), str(v.get("VPCRegion") or ""))
                    for v in got.get("VPCs") or []
                    if v.get("VPCId")
                )
        out.append(Fetched(payload, tuple(reasons)))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = _zone_id(item["Id"])
    nid = node_id("route53_hosted_zone", owner, GLOBAL, native)
    private = bool((item.get("Config") or {}).get("PrivateZone"))
    attributes: dict[str, Any] = {
        "zone_name": str(item.get("Name") or "").rstrip("."),
        "private": private,
        "record_count": item.get("ResourceRecordSetCount"),
    }
    edges: list[EdgeDraft] = []
    aliases = item.get(_ALIASES)
    if aliases is not None:
        # 별칭 레코드 이름(LB·CloudFront 를 가리키는 것만 · 정렬) — 키가 없으면 레코드를 못 읽었다
        attributes["alias_record_names"] = _sorted(
            n for names in aliases.values() for n in names
        )
        for key, names in aliases.items():
            edges.append(
                EdgeDraft(
                    "dns_alias",
                    nid,
                    key,
                    None,
                    True,  # 이름 → 그 자원으로 간다
                    {"record_names": list(names)},
                    by_dns=True,
                )
            )
    vpcs = item.get(_VPCS)
    if vpcs is not None:
        attributes["vpc_ids"] = _sorted(v for v, _ in vpcs)
        for vpc_id, vpc_region in vpcs:
            edges.append(
                EdgeDraft(
                    "zone_association",
                    nid,
                    vpc_id,
                    "vpc",
                    False,
                    {"vpc_region": vpc_region},
                )
            )
    node = Node(
        id=nid,
        type="route53_hosted_zone",
        account_id=owner,
        region=None,
        native_id=native,
        name=attributes["zone_name"] or native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
