"""Discover regions and resource owner accounts from TGW attachments.

Select enabled regions from DescribeRegions. Discover accounts from
attachment ResourceOwnerId values and include the calling account.
Record successes and failures separately in Discovery and
RegionDiscovery.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .model import Reason
from .services.base import ClientFactory, alive, classify, pages

DISCOVERY_API = "ec2:DescribeTransitGatewayAttachments"
_METHOD = "describe_transit_gateway_attachments"
_RESULT_KEY = "TransitGatewayAttachments"
_OWNER_KEY = "ResourceOwnerId"
_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")

REGION_DISCOVERY_API = "ec2:DescribeRegions"
_REGION_METHOD = "describe_regions"
_REGION_RESULT_KEY = "Regions"
_REGION_NAME = re.compile(r"^[a-z]{2}-[a-z-]+-[0-9]+$")

BOOTSTRAP_REGIONS = {
    "aws": "us-east-1",
    "aws-cn": "cn-north-1",
    "aws-us-gov": "us-gov-west-1",
}


def bootstrap_region(caller_arn: str) -> str | None:
    """Return a discovery region for the caller ARN's AWS partition.

    Return None if the ARN format or partition is unknown.
    """
    parts = caller_arn.split(":")
    return BOOTSTRAP_REGIONS.get(parts[1]) if len(parts) > 3 else None


@dataclass(frozen=True, slots=True)
class Failure:

    """Store the scope and reason of an account or region discovery
    failure.

    region: Region where the lookup failed
    status: Failure status
    reason: Failure reason and API name
    """
    region: str
    status: str
    reason: Reason


@dataclass(frozen=True, slots=True)
class Discovery:
    """Store accounts found through TGW attachments and regional
    results.

    accounts: Caller and discovered account IDs (sorted and
        deduplicated)
    discovered: Discovered account IDs, excluding the caller
    looked: Regions queried successfully
    failures: Lookup failures by region
    """
    accounts: tuple[str, ...]

    discovered: tuple[str, ...]

    looked: tuple[str, ...]

    failures: tuple[Failure, ...] = ()

    @property
    def blind(self) -> bool:
        """Return whether discovery failed in every requested region."""
        return not self.looked


@dataclass(frozen=True, slots=True)
class RegionDiscovery:
    """Store the result of querying available regions.

    regions: Regions available to the account (sorted and deduplicated)
    asked: Region used to query the region list
    failure: Lookup failure details (None on success)
    """
    regions: tuple[str, ...]

    asked: str

    failure: Failure | None = None

    @property
    def blind(self) -> bool:
        return not self.regions


def discover_regions(
    *, hub_account: str, bootstrap: str, client: ClientFactory
) -> RegionDiscovery:
    """Return regions available to the querying account.

    hub_account: Account ID used to query regions
    bootstrap: Region where DescribeRegions is called
    client: Function that creates EC2 clients by account and region

    Exclude regions that require opt-in but are not enabled. Record
    lookup
    failures in failure.
    """
    try:
        items: list[dict[str, Any]] = pages(
            client(hub_account, bootstrap, "ec2"), _REGION_METHOD, _REGION_RESULT_KEY
        )
    except (ClientError, BotoCoreError) as exc:
        status, reason = classify(exc, REGION_DISCOVERY_API)
        return RegionDiscovery(
            regions=(),
            asked=bootstrap,
            failure=Failure(region=bootstrap, status=status, reason=reason),
        )
    names = {
        str(item.get("RegionName") or "")
        for item in items
        if item.get("OptInStatus") != "not-opted-in"
    }
    return RegionDiscovery(
        regions=tuple(sorted(n for n in names if _REGION_NAME.match(n))),
        asked=bootstrap,
    )


def discover_accounts(
    *,
    hub_account: str,
    regions: Sequence[str],
    client: ClientFactory,
) -> Discovery:
    """Find accounts from ResourceOwnerId in non-deleted TGW
    attachments.

    hub_account: Calling account ID where discovery starts
    regions: Regions whose TGW attachments to query
    client: Function that creates regional EC2 clients for the caller

    Include the caller in accounts. Record lookup failures in failures.
    """
    owners: set[str] = set()
    looked: list[str] = []
    failures: list[Failure] = []

    for region in dict.fromkeys(regions):
        try:
            items: list[dict[str, Any]] = pages(
                client(hub_account, region, "ec2"), _METHOD, _RESULT_KEY
            )
        except (ClientError, BotoCoreError) as exc:
            status, reason = classify(exc, DISCOVERY_API)
            failures.append(Failure(region=region, status=status, reason=reason))
            continue
        looked.append(region)
        for item in items:
            if not alive(item.get("State")):
                continue
            raw = item.get(_OWNER_KEY)
            owner = str(raw) if raw else ""
            if _ACCOUNT_ID.match(owner):
                owners.add(owner)

    discovered = tuple(sorted(owners - {hub_account}))
    return Discovery(
        accounts=tuple(sorted(owners | {hub_account})),
        discovered=discovered,
        looked=tuple(looked),
        failures=tuple(failures),
    )
