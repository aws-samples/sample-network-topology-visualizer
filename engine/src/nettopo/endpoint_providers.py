"""Link VPC endpoints to the load balancers or firewalls providing them.

Resolve Network Firewall endpoint IDs first, then service names.
If the provider is not uniquely identified, omit the relationship and
record its service name in Joined.unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Edge
from .services.base import NodeDraft, alive

EDGE_TYPE = "endpoint_provider"
_GWLB_ENDPOINT = "GatewayLoadBalancer"
_INTERFACE = "Interface"
_CUSTOMER_SERVICE = ".vpce-svc-"


@dataclass(slots=True)
class Joined:
    """Store links between VPC endpoints and collected service
    providers.

    edges: Relationships between endpoints and service providers
    unresolved: Unresolved service names by endpoint Node.id
    """
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
    """Return relationships between VPC endpoints and collected
    providers.

    kept: Collected endpoints, load balancers, and Network Firewalls
    ids: Node.id values included in the collection result

    Record ambiguous or missing providers in Joined.unresolved by
    service
    name. Do not make additional API calls.
    """
    providers: dict[str, set[str]] = {}
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
            continue
        if not alive(a.get("state")):
            continue
        service = str(a.get("service_name") or "")
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
        lbs = sorted(x for x in providers.get(service, set()) if x in ids)
        if len(lbs) == 1:
            edges.append(_edge(n.id, lbs[0], service, "endpoint_service"))
        else:
            unresolved.setdefault(n.id, set()).add(service)
    return Joined(edges=edges, unresolved=unresolved)
