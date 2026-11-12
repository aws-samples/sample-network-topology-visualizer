"""Collect OpenSearch network configuration with batch descriptions.

Use the VPC parent and multi-subnet placement for VPC domains. Use
regional
placement when a successful description lacks VPCOptions. Leave
placement
unknown if description fails.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec("opensearch", "list_domain_names", "DomainNames", "es:ListDomainNames")
_DESCRIBE_API = "es:DescribeDomains"
_BATCH = 5


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Batch-describe listed domains to enrich network details.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    names = sorted(
        {str(f.payload["DomainName"]) for f in items if f.payload.get("DomainName")}
    )
    details: dict[str, dict[str, Any]] = {}
    failed: dict[str, Any] = {}
    for i in range(0, len(names), _BATCH):
        chunk = names[i : i + _BATCH]
        try:
            got = (
                client.describe_domains(DomainNames=chunk).get("DomainStatusList") or []
            )
        except (ClientError, BotoCoreError) as exc:
            reason = classify(exc, _DESCRIBE_API)[1]
            for n in chunk:
                failed[n] = reason
            continue
        for d in got:
            if d.get("DomainName"):
                details[str(d["DomainName"])] = d
    out: list[Fetched] = []
    for f in items:
        name = str(f.payload.get("DomainName") or "")
        if name in details:
            out.append(
                Fetched({**f.payload, **details[name], "_described": True}, f.reasons)
            )
        elif name in failed:
            out.append(
                Fetched({**f.payload, "_described": False}, f.reasons + (failed[name],))
            )
        else:
            out.append(Fetched({**f.payload, "_described": False}, f.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["DomainName"])
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if item.get("_described"):
        cluster = item.get("ClusterConfig") or {}
        vpc = item.get("VPCOptions") or {}
        endpoints = item.get("Endpoints") or {}
        attributes = {
            "engine_version": item.get("EngineVersion"),
            "instance_type": cluster.get("InstanceType"),
            "instance_count": cluster.get("InstanceCount"),
            "dedicated_master_enabled": cluster.get("DedicatedMasterEnabled"),
            "zone_awareness_enabled": cluster.get("ZoneAwarenessEnabled"),
            "endpoint": endpoints.get("vpc") or item.get("Endpoint"),
            "encryption_at_rest_enabled": (
                item.get("EncryptionAtRestOptions") or {}
            ).get("Enabled"),
            "node_to_node_encryption_enabled": (
                item.get("NodeToNodeEncryptionOptions") or {}
            ).get("Enabled"),
            "enforce_https": (item.get("DomainEndpointOptions") or {}).get(
                "EnforceHTTPS"
            ),
            "subnet_ids": _sorted(vpc.get("SubnetIds") or []),
            "availability_zones": _sorted(vpc.get("AvailabilityZones") or []),
            "security_group_ids": _sorted(vpc.get("SecurityGroupIds") or []),
        }
        if vpc.get("VPCId"):
            parents.append(node_id("vpc", owner, ctx.region, str(vpc["VPCId"])))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=node_id("opensearch_domain", owner, ctx.region, native),
        type="opensearch_domain",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("ARN"),
        name=native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    if not item.get("_described"):
        return None
    value = item.get("DomainProcessingStatus")
    if value:
        return str(value)
    if item.get("Deleted"):
        return "Deleting"
    if item.get("Created") is False:
        return "Creating"
    return "Modifying" if item.get("Processing") else "Active"


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
