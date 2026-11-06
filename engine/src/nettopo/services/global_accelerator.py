"""global_accelerator(Global Accelerator 표준 가속기) 수집 — 「이음매 2」 · 배치 D(스키마 2.10.0 · ADR 0002 §12-5 · target-gap #11) · **네 번째 글로벌 리소스**.

CloudFront 가 세운 글로벌 규약을 그대로 쓴다(`cloudfront.py` 머리말): id 의 리전 자리 = `GLOBAL` · `region` = null · 부모 = 계정 노드 ·
어느 쌍에서 봐도 id 가 같아 먼저 만난 것이 남는다.

🔴 **API 는 us-west-2 에만 있다**(AWS 문서 「Global Accelerator 는 글로벌 서비스지만 API 는 미국 서부(오리건) 리전 엔드포인트로만 부른다」).
그래서 `Spec.region = "us-west-2"` — 수집 리전이 무엇이든 클라이언트를 그 리전으로 만든다(`collect._fetch` · `test_relations_d.py` 가 잰다).
IAM 도 스택 B 의 **별 문장**(`ReadGlobalAccelerator` · `aws:RequestedRegion` = us-west-2)이다 — 수집 리전 조건 문장에 넣으면 고객이 us-west-2 를
안 고를 때 가속기만 통째로 「권한 없음」이 된다(CloudFront 와 같은 일 · 이슈 #42).

엣지 `accelerator_endpoint`(가속기 → 엔드포인트 · 방향 있음): `ListListeners` → 리스너마다 `ListEndpointGroups` → 엔드포인트 그룹(리전 하나)의
`EndpointDescriptions[].EndpointId`:
- ALB·NLB ARN → 그 LB(ARN 의 계정·리전·이름 · `EdgeDraft.by_id`)
- `i-…` → EC2(인스턴스 id 색인)
- `eipalloc-…` → 그 탄력적 IP 의 **주인**(3패스 `collect._via_eip` — EIP 는 카드가 아니라 주인의 정보창 줄이다)
같은 대상은 선 하나(`listener_ports`·`endpoint_region`). 🔴 못 이음은 지어내지 않는다 — `unresolved_endpoints` = 「<ref> <까닭> <리전>」:
**수집 범위 밖 리전·계정**(`not_in_scope` — 화면이 「이 리전을 수집 범위에 넣으면 선으로 이어집니다」로 말한다) · 이 스냅샷에 없음(`not_collected` · 3패스) ·
EIP 를 수집했는데 주인이 없음(`eip_no_owner`) · 모르는 꼴(`unknown_type`). 엔드포인트가 0개인 그룹은 속성 `empty_endpoint_regions` 로 남는다.

🔴 구성만 싣는다 — `Status`(배포 전파 상태 · 시시각각) · 헬스(`HealthState` — 상태 단의 몫)는 싣지 않는다. 고정 IP(애니캐스트 · 퍼블릭)는 싣는다(정보창에만).
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import GLOBAL, Node, node_id
from .api_gateway import lb_ref
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    pages,
)
from .base import sorted_strs as _sorted

API_REGION = "us-west-2"
SPEC = Spec(
    "globalaccelerator",
    "list_accelerators",
    "Accelerators",
    "globalaccelerator:ListAccelerators",
    region=API_REGION,
)
LISTENERS_API = "globalaccelerator:ListListeners"
GROUPS_API = "globalaccelerator:ListEndpointGroups"
_EPS = "_Endpoints"
_PORTS = "_Ports"
_EMPTY = "_EmptyRegions"
WHY_ATTR = "unresolved_endpoints"


def _ports(listener: dict[str, Any]) -> list[str]:
    proto = str(listener.get("Protocol") or "")
    out = []
    for r in listener.get("PortRanges") or []:
        a, b = r.get("FromPort"), r.get("ToPort")
        out.append(f"{proto} {a}" if a == b else f"{proto} {a}-{b}")
    return out


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for it in items:
        arn = str(it.payload.get("AcceleratorArn") or "")
        reasons = list(it.reasons)
        try:
            listeners = pages(client, "list_listeners", "Listeners", AcceleratorArn=arn)
        except (ClientError, BotoCoreError) as exc:
            reasons.append(classify(exc, LISTENERS_API)[1])
            out.append(Fetched(it.payload, tuple(reasons)))
            continue
        eps: set[tuple[str, str, str]] = (
            set()
        )  # (리전, 엔드포인트 id, 리스너 포트 글자)
        ports: set[str] = set()
        empty: set[str] = set()
        failed = False
        for ls in sorted(listeners, key=lambda x: str(x.get("ListenerArn"))):
            lp = _ports(ls)
            ports.update(lp)
            try:
                groups = pages(
                    client,
                    "list_endpoint_groups",
                    "EndpointGroups",
                    ListenerArn=str(ls.get("ListenerArn")),
                )
            except (ClientError, BotoCoreError) as exc:
                r = classify(exc, GROUPS_API)[1]
                if r not in reasons:
                    reasons.append(r)
                failed = True
                continue
            for g in groups:
                region = str(g.get("EndpointGroupRegion") or "")
                ds = [
                    str(d["EndpointId"])
                    for d in g.get("EndpointDescriptions") or []
                    if d.get("EndpointId")
                ]
                if not ds:
                    empty.add(region)
                for eid in ds:
                    for p in lp or [""]:
                        eps.add((region, eid, p))
        payload = {**it.payload, _PORTS: sorted(ports), _EMPTY: sorted(empty)}
        # 엔드포인트 그룹을 하나라도 못 읽었으면 엔드포인트 목록은 「일부」다 — 키를 만들지 않는다(선 없이 사유만 · 불변식 ④)
        if not failed:
            payload[_EPS] = sorted(eps)
        out.append(Fetched(payload, tuple(reasons)))
    return out


_LB_KIND = {"app": "alb", "net": "nlb", "gwy": "gwlb"}


def _kind_of(eid: str) -> str:
    """엔드포인트 id → 종류 글자(정보창 몫 · 스칼라)."""
    if lb_ref(eid) is not None:
        res = str(eid).split(":", 5)[5].split("/")
        return _LB_KIND.get(res[1] if len(res) > 1 else "", "lb")
    if eid.startswith("i-"):
        return "ec2"
    if eid.startswith("eipalloc-"):
        return "eip"
    return "unknown"


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    arn = str(item["AcceleratorArn"])
    native = arn.rsplit("/", 1)[-1]
    nid = node_id("global_accelerator", owner, GLOBAL, native)
    attributes: dict[str, Any] = {
        "enabled": item.get("Enabled"),
        "ip_address_type": item.get("IpAddressType"),
        "dns_name": item.get("DnsName"),
        "static_ips": _sorted(
            ip for s in item.get("IpSets") or [] for ip in s.get("IpAddresses") or []
        ),
    }
    if item.get(_PORTS) is not None:
        attributes["listener_ports"] = list(item[_PORTS])
    if item.get(_EMPTY):
        attributes["empty_endpoint_regions"] = list(item[_EMPTY])
    edges: list[EdgeDraft] = []
    eps = item.get(_EPS)
    if eps is not None:
        attributes["endpoint_regions"] = _sorted(r for r, _, _ in eps)
        # 엔드포인트 그룹마다 등록된 대상(「<리전> <종류> <이름·id>」 · 2026-10-21 사용자 요청 — 정보창이 리전마다 한 줄로 말한다:
        #   그린 대상 · 수집하지 않은 리전의 대상 수·종류 · 못 찾은 대상). 종류 = alb·nlb·gwlb(ARN 의 `app`·`net`·`gwy`) · ec2 · eip · unknown
        attributes["endpoint_targets"] = _sorted(
            f"{region} {_kind_of(eid)} {(lb_ref(eid) or (None, None, eid))[2]}"
            for region, eid, _ in eps
        )
        per: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        why: set[str] = set()
        for region, eid, port in eps:
            ref = lb_ref(eid)
            if ref is not None:
                r, account, name = ref
                key = (node_id("load_balancer", account, r, name), None, name)
                in_scope = account in ctx.scope_accounts and r in ctx.scope_regions
                kind = "lb"
            elif eid.startswith("i-"):
                key, in_scope, kind = (
                    (eid, "ec2_instance", eid),
                    region in ctx.scope_regions,
                    "ec2",
                )
            elif eid.startswith("eipalloc-"):
                key = (node_id("elastic_ip", owner, region, eid), None, eid)
                in_scope, kind = region in ctx.scope_regions, "eip"
            else:
                why.add(f"{eid} unknown_type {region}")
                continue
            if not in_scope:
                why.add(f"{key[2]} not_in_scope {region}")
                continue
            slot = per.setdefault(key, {"ports": set(), "region": region, "kind": kind})
            if port:
                slot["ports"].add(port)
        for (target, ttype, ref), v in sorted(
            per.items(), key=lambda x: (x[0][0], x[0][2])
        ):
            edges.append(
                EdgeDraft(
                    "accelerator_endpoint",
                    nid,
                    target,
                    ttype,
                    True,  # 가속기 → 그 엔드포인트로 넘긴다
                    {
                        "endpoint_type": v["kind"],
                        "endpoint_region": v["region"],
                        "listener_ports": sorted(v["ports"]),
                    },
                    by_id=ttype is None,
                    ref=ref,
                    why_attr=WHY_ATTR,
                )
            )
        if why:
            attributes[WHY_ATTR] = sorted(why)
    node = Node(
        id=nid,
        type="global_accelerator",
        account_id=owner,
        region=None,
        native_id=native,
        arn=arn,
        name=item.get("Name") or native,
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
