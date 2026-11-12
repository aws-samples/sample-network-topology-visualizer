"""Collect Network Firewall configuration through listing and detail
APIs.

Include SyncStates endpoint IDs in topology to resolve route targets.
Store per-AZ attachment readiness in observations.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec(
    "network-firewall",
    "list_firewalls",
    "Firewalls",
    "network-firewall:ListFirewalls",
)

_DESCRIBE_API = "network-firewall:DescribeFirewall"


# Store per-AZ SyncStates endpoint IDs separately from readiness.
def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Describe each firewall to enrich configuration and per-AZ
    attachments.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        arn = item.payload["FirewallArn"]
        name = item.payload["FirewallName"]
        try:
            resp = client.describe_firewall(FirewallArn=arn)
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(
                    payload={"FirewallName": name, "FirewallArn": arn},
                    reasons=item.reasons + (classify(exc, _DESCRIBE_API)[1],),
                )
            )
            continue
        status = resp.get("FirewallStatus") or {}
        out.append(
            Fetched(
                payload={
                    "FirewallName": name,
                    "FirewallArn": arn,
                    "Firewall": resp.get("Firewall") or {},
                    "SyncStates": status.get("SyncStates"),
                    "_status": status.get("Status"),
                },
                reasons=item.reasons,
            )
        )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["FirewallName"]
    nid = node_id("network_firewall", owner, ctx.region, native)

    firewall = item.get("Firewall")
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if firewall is not None:
        attributes = {
            "firewall_policy_arn": firewall.get("FirewallPolicyArn"),
            "subnet_ids": _sorted(
                m.get("SubnetId") for m in firewall.get("SubnetMappings") or []
            ),
            "firewall_endpoint_ids": _sorted(
                att.get("EndpointId")
                for sync in (item.get("SyncStates") or {}).values()
                if (att := sync.get("Attachment"))
            ),
            "delete_protection": firewall.get("DeleteProtection"),
        }
        vpc_id = firewall.get("VpcId")
        if vpc_id:
            parents.append(node_id("vpc", owner, ctx.region, str(vpc_id)))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=nid,
        type="network_firewall",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("FirewallArn"),
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
    value = item.get("_status")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
