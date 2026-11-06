"""GWLB 엔드포인트 → 그 뒤의 제공자 — `endpoint_provider` 엣지(스키마 2.8.0 · ADR 0002 §9 · 사용자 결정 2026-10-20 P1-3).

검사 경로 「서브넷 → (라우트) → GWLB 엔드포인트 → GWLB → 어플라이언스」의 가운데 마디다. 앞 마디(라우트 → 엔드포인트)는
`route` 엣지가 이미 잇고(`vpce-` 대상 = `vpc_endpoint` 노드 · `route_table.py`), 뒷 마디(GWLB → 어플라이언스)는
`load_balancer_target`(#116)이 잇는다. 이 파일은 그 사이 — 엔드포인트가 트래픽을 넘기는 곳 — 를 잇는다.

엔드포인트(소비자 쪽)는 `ServiceName` 만 안다. 그 서비스를 **제공하는 계정**의 `DescribeVpcEndpointServiceConfigurations`
가 「이 서비스 뒤의 GWLB」를 말한다(LB 초안이 그 이름을 `endpoint_service_names` 로 든다 · `services/load_balancer.py`
`join_endpoint_services`). 3패스에서 이름으로 잇는다 — 제공자가 **다른 계정**이어도 그 계정을 수집했으면 잇는다
(서비스 이름에 리전이 들어 있어 계정 사이에 겹치지 않는다).

🔵 **Network Firewall 엔드포인트**(P1-4)도 같은 엣지다 — 방화벽 엔드포인트는 AWS 가 관리하는(`RequesterManaged`) GWLB 엔드포인트이고
그 서비스는 AWS 계정 것이라 위 열쇠로는 못 잇는다. 대신 방화벽의 `firewall_endpoint_ids`(`FirewallStatus.SyncStates[az].Attachment.EndpointId`
· `services/network_firewall.py`)가 그 엔드포인트 id 를 말한다 → `resolved_by` = `firewall_endpoint`. 방화벽 쪽 열쇠가 먼저 이긴다(id 가 정확하다).
그러면 「이 서브넷 트래픽은 방화벽을 지난다」가 라우트 선(서브넷 → 방화벽 엔드포인트) + 이 선(엔드포인트 → 방화벽)으로 보인다.

🔵 **인터페이스 엔드포인트 → NLB**(남의 PrivateLink · 2.8.0 · 사용자 결정 2026-10-20 P2-1)도 같은 열쇠다 — 고객·파트너가 만든
서비스(`…vpce-svc-…`)를 쓰는 인터페이스 엔드포인트만 본다(AWS 서비스는 뒤에 우리가 그리는 LB 가 없다). 제공자 계정을 수집하지 않았으면
선이 없고, 엔드포인트의 `service_owner`(소비자 쪽 `DescribeVpcEndpointServices` · `services/vpc_endpoint.py`)가 「어느 계정의 서비스인가」를 말한다.
🔴 못 찾으면 **지어내지 않는다** — 엔드포인트의 `unresolved_refs` 에 서비스 이름을 남긴다(ADR 0002 §5). 제공자를 수집하지 않았거나
(다른 계정 · 범위 밖) 그 LB 의 엔드포인트 서비스를 못 읽은 것이다(그 LB 는 `partial` + 사유 · 불변식 ④).
🔴 결정론(불변식 ②) — 엔드포인트 id 순으로 보고, 엣지는 id 순이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Edge
from .services.base import NodeDraft, alive

EDGE_TYPE = "endpoint_provider"
# 엔드포인트 종류(AWS `VpcEndpointType`) 중 이 엣지를 내는 것 — GWLB 엔드포인트(P1-3) · 인터페이스 엔드포인트(P2-1 · 고객·파트너 서비스만)
_GWLB_ENDPOINT = "GatewayLoadBalancer"
_INTERFACE = "Interface"
_CUSTOMER_SERVICE = ".vpce-svc-"


@dataclass(slots=True)
class Joined:
    edges: list[Edge]
    unresolved: dict[str, set[str]]


def _edge(src: str, dst: str, service: str, how: str) -> Edge:
    return Edge(
        id="|".join((EDGE_TYPE, src, dst)),
        type=EDGE_TYPE,
        source=src,
        target=dst,
        directed=True,
        attributes={"service_name": service, "resolved_by": how},
    )


def join(kept: list[NodeDraft], ids: set[str]) -> Joined:
    """GWLB 엔드포인트마다 그 서비스를 제공하는 LB 하나(이름이 맞는 LB 가 하나일 때만) → 엣지."""
    providers: dict[str, set[str]] = {}
    # (계정, 리전, 엔드포인트 id) → 방화벽 노드 id — 방화벽 엔드포인트는 방화벽과 같은 계정·리전에 산다
    firewalls: dict[tuple[str, str, str], set[str]] = {}
    for d in kept:
        n = d.node
        if n.type == "network_firewall":
            for eid in n.attributes.get("firewall_endpoint_ids") or []:
                firewalls.setdefault(
                    (n.account_id, n.region or "", str(eid)), set()
                ).add(n.id)
            continue
        if n.type != "load_balancer" or not alive(n.attributes.get("state")):
            continue
        for name in n.attributes.get("endpoint_service_names") or []:
            providers.setdefault(str(name), set()).add(n.id)
    edges: list[Edge] = []
    unresolved: dict[str, set[str]] = {}
    for d in sorted(kept, key=lambda x: x.node.id):
        n = d.node
        a = n.attributes
        if n.type != "vpc_endpoint" or a.get("endpoint_type") not in (
            _GWLB_ENDPOINT,
            _INTERFACE,
        ):
            continue
        if a.get("endpoint_type") == _INTERFACE and _CUSTOMER_SERVICE not in str(
            a.get("service_name") or ""
        ):
            continue  # AWS 서비스(S3·SSM …)의 인터페이스 엔드포인트 — 뒤에 우리가 그리는 LB 가 없다(P2-1 범위 밖)
        if not alive(a.get("state")):
            continue  # 죽은 엔드포인트는 넘기지 않는다(ADR 0002 §3)
        service = str(a.get("service_name") or "")
        # ① 방화벽 엔드포인트(P1-4) — 엔드포인트 id 가 방화벽의 `firewall_endpoint_ids` 에 있다
        fws = sorted(
            x
            for x in firewalls.get(
                (n.account_id, n.region or "", n.native_id or ""), set()
            )
            if x in ids
        )
        if len(fws) == 1:
            edges.append(_edge(n.id, fws[0], service, "firewall_endpoint"))
            continue
        if not service:
            continue
        # ② 엔드포인트 서비스를 제공하는 GWLB(P1-3)
        lbs = sorted(x for x in providers.get(service, set()) if x in ids)
        if len(lbs) == 1:
            edges.append(_edge(n.id, lbs[0], service, "endpoint_service"))
        else:
            unresolved.setdefault(n.id, set()).add(service)
    return Joined(edges=edges, unresolved=unresolved)
