"""Collect EIP allocations and retain data needed to resolve owners.

Resolve instance associations directly. Resolve other owners using ENI
details in temporary data. Exclude raw ENI responses from topology.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import (
    Ctx,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    pages,
    tags_of,
)

SPEC = Spec("ec2", "describe_addresses", "Addresses", "ec2:DescribeAddresses")
ENI_API = "ec2:DescribeNetworkInterfaces"
_ENI = "_Eni"
_CHUNK = 100


def _eni_wanted(item: dict[str, Any]) -> str | None:
    eni = item.get("NetworkInterfaceId")
    return str(eni) if eni and not item.get("InstanceId") else None


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query attached ENIs to resolve Elastic IP owner resources.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    want = sorted({e for it in items if (e := _eni_wanted(it.payload))})
    if not want:
        return items
    got: list[dict[str, Any]] = []
    try:
        for i in range(0, len(want), _CHUNK):
            got.extend(
                pages(
                    client,
                    "describe_network_interfaces",
                    "NetworkInterfaces",
                    Filters=[
                        {"Name": "network-interface-id", "Values": want[i : i + _CHUNK]}
                    ],
                )
            )
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, ENI_API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,))
            if _eni_wanted(it.payload)
            else it
            for it in items
        ]
    by = {
        str(e.get("NetworkInterfaceId")): e for e in got if e.get("NetworkInterfaceId")
    }
    out: list[Fetched] = []
    for it in items:
        eid = _eni_wanted(it.payload)
        if eid and eid in by:
            out.append(Fetched({**it.payload, _ENI: by[eid]}, it.reasons))
        else:
            out.append(it)
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    tags = tags_of(item)
    native = str(item.get("AllocationId") or item.get("PublicIp"))
    nid = node_id("elastic_ip", ctx.account_id, ctx.region, native)
    associated = bool(
        item.get("AssociationId")
        or item.get("InstanceId")
        or item.get("NetworkInterfaceId")
    )
    attributes: dict[str, Any] = {
        "public_ip": item.get("PublicIp"),
        "domain": item.get("Domain"),
        "network_border_group": item.get("NetworkBorderGroup"),
        "public_ipv4_pool": item.get("PublicIpv4Pool"),
        "associated": associated,
    }
    for key, attr in (
        ("ServiceManaged", "service_managed"),
        ("InstanceId", "instance_id"),
        ("NetworkInterfaceId", "network_interface_id"),
        ("PrivateIpAddress", "private_ip_address"),
    ):
        if item.get(key):
            attributes[attr] = str(item[key])
    node = Node(
        id=nid,
        type="elastic_ip",
        account_id=ctx.account_id,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes=attributes,
        tags=tags,
    )
    eni = item.get(_ENI)
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(),),
        parent_types=("region",),
        private={"eni": eni} if eni else None,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
