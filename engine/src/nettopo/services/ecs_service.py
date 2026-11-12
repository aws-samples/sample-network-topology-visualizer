"""Collect ECS services through cluster listings and service
descriptions.

Raise ListingFailed if service listing fails and the resource set is
unknown. Keep nodes with known details if description fails. Represent
multi-subnet placement with subnet attributes rather than an arbitrary
subnet parent.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .base import (
    Ctx,
    Fetched,
    ListingFailed,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    gone,
)
from .base import pages as _pages
from .base import sorted_strs as _sorted

SPEC = Spec("ecs", "list_clusters", "clusterArns", "ecs:ListClusters")

_LIST_API = "ecs:ListServices"
_DESCRIBE_API = "ecs:DescribeServices"
_BATCH = 10


def _last(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """List and describe services by cluster to build collection items.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        cluster_arn = str(item.payload)
        try:
            service_arns = sorted(
                {
                    str(a)
                    for a in _pages(
                        client, "list_services", "serviceArns", cluster=cluster_arn
                    )
                }
            )
        except (ClientError, BotoCoreError) as exc:
            raise ListingFailed(exc, _LIST_API) from exc
        for start in range(0, len(service_arns), _BATCH):
            batch = service_arns[start : start + _BATCH]
            try:
                resp = client.describe_services(cluster=cluster_arn, services=batch)
            except (ClientError, BotoCoreError) as exc:
                reason = classify(exc, _DESCRIBE_API)[1]
                out.extend(
                    Fetched(
                        payload={"serviceArn": arn, "clusterArn": cluster_arn},
                        reasons=item.reasons + (reason,),
                    )
                    for arn in batch
                )
                continue
            described = {
                str(s.get("serviceArn")): s for s in resp.get("services") or []
            }
            failed = {str(f.get("arn")): f for f in resp.get("failures") or []}
            for arn in batch:
                if arn in described:
                    payload = dict(described[arn])
                    payload.setdefault("clusterArn", cluster_arn)
                    out.append(Fetched(payload=payload, reasons=item.reasons))
                    continue
                f = failed.get(arn) or {}
                message = " ".join(
                    str(p) for p in (f.get("reason"), f.get("detail")) if p
                )
                out.append(
                    Fetched(
                        payload={"serviceArn": arn, "clusterArn": cluster_arn},
                        reasons=item.reasons
                        + (
                            Reason(
                                code="api_error",
                                api=_DESCRIBE_API,
                                message=message or "describe_services 응답에 없다",
                            ),
                        ),
                    )
                )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("ecs_service", item.get("status")):
        return None
    owner = ctx.account_id
    service_arn = str(item["serviceArn"])
    cluster_name = _last(str(item.get("clusterArn") or ""))
    service_name = str(item.get("serviceName") or _last(service_arn))
    native = f"{cluster_name}/{service_name}"
    attributes: dict[str, Any] = {"cluster_name": cluster_name}
    if "serviceName" in item:
        awsvpc = (item.get("networkConfiguration") or {}).get(
            "awsvpcConfiguration"
        ) or {}
        attributes.update(
            {
                "launch_type": item.get("launchType"),
                "capacity_providers": _sorted(
                    s.get("capacityProvider")
                    for s in item.get("capacityProviderStrategy") or []
                ),
                "subnet_ids": _sorted(awsvpc.get("subnets") or []),
                "security_group_ids": _sorted(awsvpc.get("securityGroups") or []),
                "assign_public_ip": awsvpc.get("assignPublicIp"),
                "target_group_arns": _sorted(
                    lb.get("targetGroupArn") for lb in item.get("loadBalancers") or []
                ),
            }
        )
    node = Node(
        id=node_id("ecs_service", owner, ctx.region, quote(native, safe="")),
        type="ecs_service",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=service_arn,
        name=service_name,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


def state(item: dict[str, Any]) -> str | None:
    status = str(item.get("status") or "").lower()
    if not status:
        return None
    if status != "active":
        return status
    running, desired = item.get("runningCount"), item.get("desiredCount")
    if not isinstance(running, int) or not isinstance(desired, int):
        return None
    if desired == 0:
        return "active-desired-zero"
    if running == 0:
        return "active-no-running-tasks"
    if running < desired:
        return "active-under-desired"
    return "active"


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
