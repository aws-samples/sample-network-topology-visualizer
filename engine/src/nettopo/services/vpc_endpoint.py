"""Collect VPC endpoints and optional endpoint service ownership.

Link interface endpoints to subnets and use route links for gateway
endpoints. Classify Bedrock only for listed service names owned by
amazon.
Omit classification attributes if ownership or service identity is
unknown.
"""

from __future__ import annotations

import re
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
    alive,
    classify,
    draft,
    gone,
    pages,
    tags_of,
)
from .base import sorted_strs as _sorted

SPEC = Spec("ec2", "describe_vpc_endpoints", "VpcEndpoints", "ec2:DescribeVpcEndpoints")
# Classify only after checking the listed service name and amazon owner.
BEDROCK_SUFFIXES = frozenset(
    {
        "bedrock",
        "bedrock-fips",
        "bedrock-runtime",
        "bedrock-runtime-fips",
        "bedrock-agent",
        "bedrock-agent-runtime",
        "bedrock-agentcore",
        "bedrock-agentcore-control",
        "bedrock-agentcore.gateway",
        "bedrock-data-automation",
        "bedrock-data-automation-fips",
        "bedrock-data-automation-runtime",
        "bedrock-data-automation-runtime-fips",
        "bedrock-mantle",
    }
)
_AWS_NAME = re.compile(r"^com\.amazonaws\.([a-z]{2}(?:-[a-z]+)+-\d+)\.(.+)$")
_OWNER = "_ServiceOwner"


def bedrock_region(name: Any) -> str | None:
    m = _AWS_NAME.match(str(name or ""))
    return m.group(1) if m and m.group(2) in BEDROCK_SUFFIXES else None


SERVICE_API = "ec2:DescribeVpcEndpointServices"
_CUSTOMER_SERVICE = ".vpce-svc-"


ENI_API = "ec2:DescribeNetworkInterfaces"
_ENIS = "_Enis"
_CHUNK = 100


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query endpoint ENIs and required service ownership details.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    return _enrich_enis(client, _enrich_owners(client, items))


def _eni_ids(item: dict[str, Any]) -> list[str]:
    return sorted({str(e) for e in item.get("NetworkInterfaceIds") or [] if e})


def _enrich_enis(client: Any, items: list[Fetched]) -> list[Fetched]:
    want = sorted({e for it in items for e in _eni_ids(it.payload)})
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
            Fetched(it.payload, it.reasons + (reason,)) if _eni_ids(it.payload) else it
            for it in items
        ]
    by = {
        str(e.get("NetworkInterfaceId")): e for e in got if e.get("NetworkInterfaceId")
    }
    out: list[Fetched] = []
    for it in items:
        ids = _eni_ids(it.payload)
        if not ids:
            out.append(it)
            continue
        out.append(
            Fetched({**it.payload, _ENIS: [by[e] for e in ids if e in by]}, it.reasons)
        )
    return out


def _subnet_rows(item: dict[str, Any]) -> list[str]:
    rows = []
    for e in item.get(_ENIS) or []:
        rows.append(
            "\t".join(
                (
                    str(e.get("SubnetId") or ""),
                    str(e.get("AvailabilityZone") or ""),
                    str(e.get("PrivateIpAddress") or ""),
                    str(e.get("NetworkInterfaceId") or ""),
                )
            )
        )
    return sorted(rows)


def _enrich_owners(client: Any, items: list[Fetched]) -> list[Fetched]:
    here = getattr(getattr(client, "meta", None), "region_name", None)
    names = sorted(
        {
            str(it.payload.get("ServiceName"))
            for it in items
            if _CUSTOMER_SERVICE in str(it.payload.get("ServiceName") or "")
            or (
                it.payload.get("VpcEndpointType") == "Interface"
                and (r := bedrock_region(it.payload.get("ServiceName")))
                and (here is None or r == here)
            )
        }
    )
    owners: dict[str, str] = {}
    failed: dict[str, Any] = {}
    for name in names:
        try:
            got = client.describe_vpc_endpoint_services(ServiceNames=[name])
        except ClientError as exc:
            if (exc.response.get("Error") or {}).get("Code") == "InvalidServiceName":
                continue
            failed[name] = classify(exc, SERVICE_API)[1]
            continue
        except BotoCoreError as exc:
            failed[name] = classify(exc, SERVICE_API)[1]
            continue
        for detail in got.get("ServiceDetails") or []:
            if detail.get("ServiceName") == name and detail.get("Owner"):
                owners[name] = str(detail["Owner"])
    out: list[Fetched] = []
    for it in items:
        name = str(it.payload.get("ServiceName") or "")
        if name in owners:
            out.append(Fetched({**it.payload, _OWNER: owners[name]}, it.reasons))
        elif name in failed:
            out.append(Fetched(it.payload, it.reasons + (failed[name],)))
        else:
            out.append(it)
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpc_endpoint", item.get("State")):
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["VpcEndpointId"]
    nid = node_id("vpc_endpoint", owner, ctx.region, native)
    state = str(item.get("State") or "")
    subnets = _sorted(item.get("SubnetIds") or [])
    edges = [
        EdgeDraft(
            "endpoint_connection",
            nid,
            subnet,
            "subnet",
            False,
            {"state": state},
            live=alive(state),
        )
        for subnet in subnets
    ]
    node = Node(
        id=nid,
        type="vpc_endpoint",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": state,
            "endpoint_type": item.get("VpcEndpointType"),
            "service_name": item.get("ServiceName"),
            "private_dns_enabled": bool(item.get("PrivateDnsEnabled", False)),
            "subnet_ids": subnets,
            "route_table_ids": _sorted(item.get("RouteTableIds") or []),
            "security_groups": sorted(
                f"{g.get('GroupId')}\t{g.get('GroupName') or ''}"
                for g in item.get("Groups") or []
                if g.get("GroupId")
            ),
            "network_interface_ids": _eni_ids(item),
            "ip_address_type": item.get("IpAddressType"),
            **({"subnet_addresses": _subnet_rows(item)} if _ENIS in item else {}),
            **({"service_owner": item[_OWNER]} if _OWNER in item else {}),
            **_bedrock(item, ctx),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(
            node_id("vpc", owner, ctx.region, str(item.get("VpcId"))),
            ctx.region_node(owner),
        ),
        parent_types=("vpc", "region"),
        edges=edges,
    )


def _bedrock(item: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    if (
        item.get(_OWNER) == "amazon"
        and bedrock_region(item.get("ServiceName")) == ctx.region
    ):
        return {"aws_service": "bedrock"}
    return {}


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
