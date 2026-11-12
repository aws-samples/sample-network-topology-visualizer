"""Collect VPC Lattice service networks and VPC and service
associations.

Exclude failed or deleting associations from links. On additional query
failure, omit the affected attribute and record its reason in
partial_reasons.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
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

SPEC = Spec(
    "vpc-lattice",
    "list_service_networks",
    "items",
    "vpc-lattice:ListServiceNetworks",
)
VPC_ASSOC_API = "vpc-lattice:ListServiceNetworkVpcAssociations"
SVC_ASSOC_API = "vpc-lattice:ListServiceNetworkServiceAssociations"
_VPCS = "_VpcAssociations"
_SVCS = "_ServiceAssociations"
VPC_WHY = "unresolved_vpc_associations"
SVC_WHY = "unresolved_service_associations"
DEAD = frozenset({"DELETE_IN_PROGRESS", "CREATE_FAILED"})


def arn_account(arn: str) -> str | None:
    parts = str(arn).split(":", 5)
    return parts[4] if len(parts) == 6 and parts[4] else None


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query VPCs and services associated with a service network.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for it in items:
        sid = str(it.payload["id"])
        payload = dict(it.payload)
        reasons = list(it.reasons)
        for method, api, key in (
            ("list_service_network_vpc_associations", VPC_ASSOC_API, _VPCS),
            ("list_service_network_service_associations", SVC_ASSOC_API, _SVCS),
        ):
            try:
                payload[key] = pages(
                    client, method, "items", serviceNetworkIdentifier=sid
                )
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, api)[1])
        out.append(Fetched(payload, tuple(reasons)))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    raw_owner = arn_account(str(item.get("arn") or ""))
    if raw_owner and raw_owner != ctx.account_id and raw_owner in ctx.scope_accounts:
        return None
    owner, extra = ctx.attribute({"OwnerId": raw_owner})
    native = str(item["id"])
    nid = node_id("lattice_service_network", owner, ctx.region, native)
    attributes: dict[str, Any] = {**extra}
    edges: list[EdgeDraft] = []
    vpcs = item.get(_VPCS)
    if vpcs is not None:
        live = [
            a for a in vpcs if str(a.get("status") or "") not in DEAD and a.get("vpcId")
        ]
        attributes["vpc_association_count"] = len({str(a["vpcId"]) for a in live})
        for a in sorted(live, key=lambda x: str(x["vpcId"])):
            edges.append(
                EdgeDraft(
                    "lattice_vpc_association",
                    nid,
                    str(a["vpcId"]),
                    "vpc",
                    False,
                    {"state": str(a.get("status") or "").lower()},
                    disc=None,
                    why_attr=VPC_WHY,
                )
            )
    svcs = item.get(_SVCS)
    if svcs is not None:
        live = [
            s
            for s in svcs
            if str(s.get("status") or "") not in DEAD and s.get("serviceId")
        ]
        attributes["service_association_count"] = len(
            {str(s["serviceId"]) for s in live}
        )
        for s in sorted(live, key=lambda x: str(x["serviceId"])):
            edges.append(
                EdgeDraft(
                    "lattice_service_association",
                    nid,
                    str(s["serviceId"]),
                    "lattice_service",
                    False,
                    {"state": str(s.get("status") or "").lower()},
                    ref=str(s.get("serviceName") or s["serviceId"]),
                    why_attr=SVC_WHY,
                )
            )
    node = Node(
        id=nid,
        type="lattice_service_network",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("arn"),
        name=item.get("name") or native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
