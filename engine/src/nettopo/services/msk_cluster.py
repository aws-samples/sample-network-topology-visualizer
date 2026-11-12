"""Collect provisioned and serverless MSK network configuration.

Record placement in subnet_ids and query provisioned broker ENIs through
ListNodes. Record broker query failures in partial_reasons rather than
using a count of zero.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft, pages
from .base import sorted_strs as _sorted

SPEC = Spec("kafka", "list_clusters_v2", "ClusterInfoList", "kafka:ListClustersV2")
_NODES_API = "kafka:ListNodes"
_BROKERS = "_Brokers"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query broker ENIs for provisioned clusters through ListNodes.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for f in items:
        arn = f.payload.get("ClusterArn")
        if f.payload.get("ClusterType") != "PROVISIONED" or not arn:
            out.append(f)
            continue
        try:
            nodes = pages(client, "list_nodes", "NodeInfoList", ClusterArn=arn)
        except (ClientError, BotoCoreError) as exc:
            out.append(Fetched(f.payload, f.reasons + (classify(exc, _NODES_API)[1],)))
            continue
        brokers = [
            n.get("BrokerNodeInfo") or {} for n in nodes if n.get("BrokerNodeInfo")
        ]
        out.append(Fetched({**f.payload, _BROKERS: brokers}, f.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["ClusterName"])
    kind = item.get("ClusterType")
    attributes: dict[str, Any] = {"cluster_type": kind}
    if kind == "PROVISIONED":
        prov = item.get("Provisioned") or {}
        group = prov.get("BrokerNodeGroupInfo") or {}
        attributes.update(
            {
                "kafka_version": (prov.get("CurrentBrokerSoftwareInfo") or {}).get(
                    "KafkaVersion"
                ),
                "broker_instance_type": group.get("InstanceType"),
                "number_of_broker_nodes": prov.get("NumberOfBrokerNodes"),
                "subnet_ids": _sorted(group.get("ClientSubnets") or []),
                "security_group_ids": _sorted(group.get("SecurityGroups") or []),
                "client_broker_encryption": (
                    (prov.get("EncryptionInfo") or {}).get("EncryptionInTransit") or {}
                ).get("ClientBroker"),
            }
        )
        brokers = item.get(_BROKERS)
        if brokers is not None:
            attributes["broker_eni_ids"] = _sorted(
                b.get("AttachedENIId") for b in brokers
            )
    elif kind == "SERVERLESS":
        vpcs = (item.get("Serverless") or {}).get("VpcConfigs") or []
        attributes["subnet_ids"] = _sorted(
            s for v in vpcs for s in v.get("SubnetIds") or []
        )
        attributes["security_group_ids"] = _sorted(
            g for v in vpcs for g in v.get("SecurityGroupIds") or []
        )

    node = Node(
        id=node_id("msk_cluster", owner, ctx.region, native),
        type="msk_cluster",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("ClusterArn"),
        name=native,
        attributes=attributes,
        tags=dict(item.get("Tags") or {}),
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


def state(item: dict[str, Any]) -> str | None:
    value = item.get("State")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
