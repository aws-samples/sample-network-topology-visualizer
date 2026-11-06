"""배치 D 3패스 잇기(스키마 2.10.0 · ADR 0002 §12) — 탄력적 IP 의 주인.

`collect._resolve` 가 전체 노드 id 가 확정된 뒤 부른다. 🔴 읽기 전용 · 호출 0 — 재료는 빌더가 `NodeDraft.private` 에 넣어 둔
ENI 응답(`services/elastic_ip.py` `enrich`)과 이미 확정된 초안들이다. 🔴 결정론 — 초안을 id 순으로 돌고 엣지를 id 순으로 낸다.

주인 규칙(앞이 이긴다 · `docs/canvas-principles.md` §17.34):
1. 인스턴스 연결(`instance_id`) = 그 EC2(native 색인 — 인스턴스 id 는 계정 사이에 겹치지 않는다).
2. ENI 만 있는 연결 = 그 ENI 의 주인 — NAT 게이트웨이(ENI 종류 `nat_gateway` · 설명 「Interface for NAT Gateway nat-…」) →
   LB IP 대상과 같은 함수(`lb_targets._eni_owner` — 엔드포인트 · RDS · ElastiCache · EKS · EC2 · 다른 LB(`ELB app|net/<이름>/…`) · Lambda …).
   🔴 두 길이 다른 순서로 주인을 정하면 같은 ENI 가 화면 두 곳에서 다른 자원이 된다 — 그래서 같은 함수를 쓴다.
3. 못 찾으면 지어내지 않는다 — EIP 노드 속성 `association_unresolved` = 「<인스턴스·ENI id> <까닭>」(까닭 = `not_collected`·`no_eni`·`eni_unread`·
   서비스 코드). 연결이 아예 없으면 줄도 엣지도 없다(`associated` = false 가 사실이다).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lb_targets import _eni_of, _eni_owner, _Index
from .model import Edge, node_id
from .services.base import NodeDraft
from .services.waf_web_acl import EDGE as WAF_EDGE
from .services.waf_web_acl import acl_node_id

EIP_EDGE = "eip_association"
EIP_WHY_ATTR = "association_unresolved"
ENI_API = "ec2:DescribeNetworkInterfaces"
_NAT_TOKEN = re.compile(r"\b(nat-[0-9a-f]{8,})\b")


@dataclass(slots=True)
class EipOwners:
    edges: list[Edge] = field(default_factory=list)
    # EIP 노드 id → 주인 노드 id(Global Accelerator 의 EIP 엔드포인트가 이 표로 그 주인에 잇는다)
    owner: dict[str, str] = field(default_factory=dict)
    # EIP 노드 id → 「<ref> <까닭>」
    why: dict[str, str] = field(default_factory=dict)


def _nat_owner(eni_type: str, desc: str, ix: _Index) -> str | None:
    if eni_type != "nat_gateway" and not desc.startswith("Interface for NAT Gateway"):
        return None
    m = _NAT_TOKEN.search(desc)
    return ix.native(m.group(1), "nat_gateway") if m else None


def eip_owners(
    kept: list[NodeDraft], ids: set[str], by_native: dict[str, str]
) -> EipOwners:
    out = EipOwners()
    eips = sorted(
        (d for d in kept if d.node.type == "elastic_ip"), key=lambda d: d.node.id
    )
    if not eips:
        return out
    ix = _Index(kept, ids, by_native)
    for d in eips:
        a = d.node.attributes
        if not a.get("associated"):
            continue
        hit: str | None = None
        code: str | None = None
        inst = a.get("instance_id")
        eni_id = a.get("network_interface_id")
        if inst:
            ref = str(inst)
            hit = ix.native(ref, "ec2_instance")
            code = None if hit else "not_collected"
        elif eni_id:
            ref = str(eni_id)
            raw = d.private.get("eni")
            if raw is None:
                denied = any(r.api == ENI_API for r in d.node.partial_reasons)
                code = "eni_unread" if denied else "no_eni"
            else:
                vid = node_id(
                    "vpc", d.account_id, d.region, str(raw.get("VpcId") or "")
                )
                eni = _eni_of(raw, d.account_id, d.region, vid)
                hit = _nat_owner(eni.interface_type, eni.description, ix)
                if hit is None:
                    hit, code = _eni_owner(eni, ix)
        else:
            continue
        if hit:
            out.owner[d.node.id] = hit
            attrs = {
                k: str(a[k])
                for k in ("network_interface_id", "private_ip_address")
                if a.get(k)
            }
            out.edges.append(
                Edge(
                    id="|".join([EIP_EDGE, d.node.id, hit]),
                    type=EIP_EDGE,
                    source=d.node.id,
                    target=hit,
                    directed=True,  # 이 IP 는 그 자원의 것이다
                    attributes=attrs,
                )
            )
        else:
            out.why[d.node.id] = f"{ref} {code or 'not_collected'}"
    out.edges.sort(key=lambda e: e.id)
    return out


# 보호 대상 쪽이 이미 든 web ACL(2.12.0 · ADR 0014 결정 3) — ALB = `web_acl_arn`(`GetWebACLForResource`) · CloudFront = `web_acl_id`(목록 응답).
_WAF_ATTR = {"load_balancer": "web_acl_arn", "cloudfront": "web_acl_id"}


def waf_protections(kept: list[NodeDraft], ids: set[str]) -> list[Edge]:
    """web ACL → 그것이 거르는 ALB·CloudFront 배포(2.12.0 · ADR 0014). 🔴 호출 0 · 결정론(id 순).

    **보호 대상 쪽의 사실 하나만** 쓴다(`services/waf_web_acl.py` 머리말 — ALB 에 `ListResourcesForWebACL` 을 묻지 않는 이유).
    web ACL 노드가 이 스냅샷에 없으면(그 쌍의 WAF 목록이 거부됐다 · 수집 종류에서 뺐다 · WAF Classic id) **선을 짓지 않는다** —
    대상 카드의 `web_acl_arn`·`web_acl_id` 줄은 그대로라 정보창은 이름을 말하고, 「확인하지 못함」은 그 쌍의 WAF coverage 가 말한다.
    """
    out: list[Edge] = []
    for d in sorted(kept, key=lambda d: d.node.id):
        attr = _WAF_ATTR.get(d.node.type)
        if attr is None:
            continue
        acl = acl_node_id(str(d.node.attributes.get(attr) or ""))
        if acl is None or acl not in ids:
            continue
        out.append(
            Edge(
                id="|".join([WAF_EDGE, acl, d.node.id]),
                type=WAF_EDGE,
                source=acl,
                target=d.node.id,
                directed=True,  # web ACL 이 그 자원 앞에서 거른다
            )
        )
    return out
