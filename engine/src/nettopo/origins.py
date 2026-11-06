"""CloudFront 원본 잇기 — LB 꼴이 아닌 원본(스키마 2.8.0 · ADR 0002 §7-1 · 사용자 결정 2026-10-20 P2-3).

#43 의 `origin` 엣지는 원본 도메인이 **LB 주소 꼴**일 때만 생긴다(`services/cloudfront.py`). 원본의 대부분(실측: 7개 중 6개)은
S3 이고, 사용자 도메인 원본은 DNS 를 풀지 않으니 이을 수 없었다. 3패스에서 두 가지를 더 잇는다:

1. **S3 원본** — 도메인 꼴(`<버킷>.s3[.<리전>].amazonaws.com` · `<버킷>.s3-website[-.]<리전>.amazonaws.com` · 옛 `<버킷>.s3-<리전>…`)에서 버킷
   이름을 읽어 **파생 노드** `s3_bucket`(이 파일이 짓는다 · 부모 = 그 배포의 계정 · 글로벌)을 짓고 `origin` 엣지(`via` = `s3`)로 잇는다.
   🔴 S3 를 수집하지 않는다 — 버킷의 주인 계정·리전·정책은 모른다(이름과 「웹사이트 엔드포인트인가」만 · 도메인에 리전이 있으면 그것).
   같은 계정의 배포 둘이 같은 버킷을 쓰면 노드 하나다.
2. **사용자 도메인 원본 → Route 53 별칭 → LB** — 원본 도메인이 수집한 호스팅 영역의 **별칭 레코드 이름**이고 그 별칭이 수집한 LB 를 가리키면
   `origin` 엣지(`via` = `dns_alias` · `alias` = 그 이름)로 그 LB 에 잇는다(엔진은 DNS 를 풀지 않는다 — AWS 응답의 별칭만 따라간다).
   별칭이 없으면 예전처럼 선도 미해결도 없다(우리가 그리는 종류가 아닐 수 있다).

VPC 원본(`VpcOriginConfig` → `cloudfront:ListVpcOrigins` 의 엔드포인트 ARN)은 1패스 빌더가 `origin` 엣지 초안으로 낸다(`cloudfront.py`).
🔴 결정론 — 배포·도메인 순으로 보고, 노드·엣지는 id 순이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import GLOBAL, Edge, Node, node_id
from .services.base import NodeDraft, dns_key

DERIVED = "s3_bucket"
# S3 원본 도메인 꼴(접은 키 기준) — REST(`.s3.` · `.s3.<리전>.` · 옛 `.s3-<리전>.`) · 웹사이트(`.s3-website-<리전>.` · `.s3-website.<리전>.`)
_S3 = re.compile(
    r"^(?P<bucket>[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9])\.s3"
    r"(?:(?P<web>-website)?(?:[.\-](?P<region>[a-z]{2}(?:-[a-z]+)+-\d))?)?\.amazonaws\.com(?:\.cn)?$"
)


@dataclass(slots=True)
class Joined:
    nodes: list[NodeDraft]
    edges: list[Edge]


def _s3(domain: str) -> tuple[str, bool, str | None] | None:
    m = _S3.match(dns_key(domain))
    if not m:
        return None
    return m.group("bucket"), bool(m.group("web")), m.group("region")


def join(
    kept: list[NodeDraft], ids: set[str], by_dns: dict[str, str], existing: set[str]
) -> Joined:
    """S3 원본 → 파생 노드 + 엣지 · 사용자 도메인 → Route 53 별칭 → LB 엣지. `existing` = 이미 있는 엣지 id(같은 LB 를 두 길로 적으면 선 하나)."""
    # 별칭 레코드 이름 → 별칭 대상(접은 DNS) — 수집한 호스팅 영역의 `dns_alias` 초안에서(대상이 LB 일 때만 쓴다)
    alias: dict[str, set[str]] = {}
    for d in kept:
        if d.node.type != "route53_hosted_zone":
            continue
        for e in d.edges:
            if e.type == "dns_alias":
                for name in e.attributes.get("record_names") or []:
                    alias.setdefault(str(name), set()).add(e.target_native)
    buckets: dict[str, dict[str, object]] = {}
    edges: dict[str, Edge] = {}
    for d in sorted(kept, key=lambda x: x.node.id):
        n = d.node
        if n.type != "cloudfront":
            continue
        for raw in sorted(
            str(x) for x in n.attributes.get("origin_domain_names") or []
        ):
            s3 = _s3(raw)
            if s3:
                bucket, web, region = s3
                bid = node_id(DERIVED, n.account_id, GLOBAL, bucket)
                slot = buckets.setdefault(
                    bid,
                    {
                        "account": n.account_id,
                        "bucket": bucket,
                        "web": False,
                        "regions": set(),
                    },
                )
                slot["web"] = bool(slot["web"]) or web
                if region:
                    slot["regions"].add(region)  # type: ignore[union-attr]
                _add(edges, existing, n.id, bid, {"via": "s3"})
                continue
            key = dns_key(raw)
            targets = sorted(
                t
                for t in (by_dns.get(x) for x in alias.get(key, set()))
                if t and t.startswith("load_balancer/") and t in ids
            )
            if len(targets) == 1:
                _add(
                    edges,
                    existing,
                    n.id,
                    targets[0],
                    {"via": "dns_alias", "alias": key},
                )
    nodes = [
        NodeDraft(
            node=Node(
                id=bid,
                type=DERIVED,
                account_id=str(s["account"]),
                region=None,
                native_id=str(s["bucket"]),
                name=str(s["bucket"]),
                parent_id=node_id("account", str(s["account"])),
                attributes={
                    "bucket_name": s["bucket"],
                    "website": bool(s["web"]),
                    "regions": sorted(s["regions"]),  # type: ignore[arg-type]
                },
            ),
            account_id=str(s["account"]),
            region="",
            parents=(node_id("account", str(s["account"])),),
            parent_types=("account",),
        )
        for bid, s in sorted(buckets.items())
    ]
    return Joined(nodes=nodes, edges=[edges[k] for k in sorted(edges)])


def _add(
    edges: dict[str, Edge],
    existing: set[str],
    src: str,
    dst: str,
    attrs: dict[str, object],
) -> None:
    eid = "|".join(("origin", src, dst))
    if eid in existing or eid in edges:
        return  # 같은 대상을 두 꼴로 적은 원본 = 선 하나(먼저 생긴 것을 남긴다)
    edges[eid] = Edge(
        id=eid, type="origin", source=src, target=dst, directed=True, attributes=attrs
    )
