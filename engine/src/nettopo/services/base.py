"""Define shared data structures and helpers for service collectors.

pages() combines paginated API results into one list. classify()
classifies
query errors and stores the failure code, API, and message in Reason.
Use NodeDraft and EdgeDraft to resolve parents and targets after
collection.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from botocore.exceptions import ClientError, NoCredentialsError

from ..model import Node, Reason, node_id

ClientFactory = Callable[[str, str, str], Any]


@dataclass(frozen=True, slots=True)
class Spec:
    """Define a resource listing API and the path to its result list.

    service: boto3 service name (for example, ec2)
    method: boto3 client method name (for example, describe_vpcs)
    result_key: Result list key path (for example, Vpcs)
    api: API name for Reason.api on failure (for example,
        ec2:DescribeVpcs)
    params: Request parameter names and values for the listing API
    region: Client region for APIs with a single global endpoint
    """

    service: str
    method: str
    result_key: str
    api: str
    params: tuple[tuple[str, Any], ...] = ()
    region: str | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """Store an API response with failures from additional queries.

    payload: Listed item's API response and additional query results
    reasons: Additional query failures (empty tuple if none)
    """

    payload: dict[str, Any]
    reasons: tuple[Reason, ...] = ()


Enrich = Callable[[Any, list[Fetched]], list[Fetched]]


class ListingFailed(Exception):
    """A follow-up listing failed, leaving the resource set unknown."""

    def __init__(self, exc: Exception, api: str) -> None:
        """Classify the query error and store its status and reason."""
        super().__init__(str(exc))
        self.status, self.reason = classify(exc, api)


@dataclass(frozen=True, slots=True)
class Service:
    """Define listing, node-building, enrichment, and state functions.

    spec: Listing API and result list key path
    build: Function that builds NodeDraft from payload and Ctx (omit if
        None)
    enrich: Function that enriches Fetched items with same-service
        queries
    state: Function that reads current state from payload
    state_attributes: Function that returns state-related payload
        attributes
    peer_enrich: Other service name paired with its Fetched enrichment
        function
    """

    spec: Spec
    build: Callable[[dict[str, Any], Ctx], NodeDraft | None]
    enrich: Enrich | None = None
    state: Callable[[dict[str, Any]], str | None] | None = None
    state_attributes: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    peer_enrich: tuple[
        tuple[str, Callable[[Any, list[Fetched], Reason | None], list[Fetched]]], ...
    ] = ()


STATUS_BY_ERROR_CODE: dict[str, tuple[str, str]] = {
    "AccessDenied": ("denied", "access_denied"),
    "AccessDeniedException": ("denied", "access_denied"),
    "UnauthorizedOperation": ("denied", "access_denied"),
    "Throttling": ("throttled", "throttled"),
    "ThrottlingException": ("throttled", "throttled"),
    "RequestLimitExceeded": ("throttled", "throttled"),
    "TooManyRequestsException": ("throttled", "throttled"),
    "AuthFailure": ("unsupported", "unsupported_region"),
    "OptInRequired": ("unsupported", "unsupported_region"),
    "UnsupportedOperation": ("unsupported", "unsupported_region"),
    "InvalidClientTokenId": ("error", "credentials_unavailable"),
}

MESSAGE_LIMIT = 500

DEAD_STATES = frozenset(
    {
        "deleted",
        "deleting",
        "deleted-failed",
        "detached",
        "detaching",
        "disassociated",
        "disassociating",
        "expired",
        "failed",
        "failing",
        "rejected",
        "rejecting",
        "rollingback",
    }
)


GONE_STATES: dict[str, frozenset[str]] = {
    "ec2_instance": frozenset({"terminated"}),
    "nat_gateway": frozenset({"deleted"}),
    "transit_gateway": frozenset({"deleted"}),
    "transit_gateway_route_table": frozenset({"deleted"}),
    "transit_gateway_attachment": frozenset({"deleted", "rejected"}),
    "vpc_peering_connection": frozenset({"deleted", "rejected", "expired"}),
    "vpc_endpoint": frozenset({"deleted", "rejected", "expired"}),
    "vpn_connection": frozenset({"deleted"}),
    "vpn_gateway": frozenset({"deleted"}),
    "customer_gateway": frozenset({"deleted"}),
    "direct_connect_connection": frozenset({"deleted", "rejected"}),
    "direct_connect_virtual_interface": frozenset({"deleted", "rejected"}),
    "direct_connect_gateway": frozenset({"deleted"}),
    "ec2_instance_connect_endpoint": frozenset({"delete-complete"}),
    "ecs_service": frozenset({"inactive"}),
}


def gone(resource_type: str, state: Any) -> bool:
    """Return True if the resource state is excluded from collection.

    resource_type: Resource type
    state: Resource state from the API response

    GONE_STATES defines excluded states by type. Compare states without
    case
    sensitivity. Return False if state is absent or is not excluded.
    """
    return str(state or "").lower() in GONE_STATES.get(resource_type, frozenset())


def classify(exc: Exception, api: str) -> tuple[str, Reason]:
    """Classify a query error and return collection status and a Reason.

    exc: Exception raised during the query
    api: Name of the failed query's API

    Classify ClientError using STATUS_BY_ERROR_CODE. Missing credentials
    give
    status error and Reason.code credentials_unavailable. Unclassified
    errors
    give status error and Reason.code api_error. Store the code, API
    name, and
    error message in Reason. Normalize whitespace, omit encoded
    authorization
    tokens, and limit the message to MESSAGE_LIMIT characters.
    """
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        status, reason_code = STATUS_BY_ERROR_CODE.get(code, ("error", "api_error"))
    elif isinstance(exc, NoCredentialsError):
        status, reason_code = "error", "credentials_unavailable"
    else:
        status, reason_code = "error", "api_error"
    message = _ENCODED.sub(r"\1(생략)", " ".join(str(exc).split()))[:MESSAGE_LIMIT]
    return status, Reason(code=reason_code, api=api, message=message)


_ENCODED = re.compile(r"(Encoded authorization failure message: )\S+")


_TOKEN_KEYS = ("NextToken", "nextToken", "NextMarker")


def _manual_pages(
    client: Any, method: str, kwargs: dict[str, Any]
) -> Iterable[dict[str, Any]]:
    """Query API responses using page tokens and yield one page at a
    time.

    client: boto3 client for the query
    method: boto3 client method name
    kwargs: Request parameters for the API call

    Stop when the next token is absent or repeats.
    """
    call = getattr(client, method)
    params = dict(kwargs)
    seen: set[str] = set()
    while True:
        page = call(**params)
        yield page
        token_key = next((k for k in _TOKEN_KEYS if page.get(k)), None)
        if token_key is None or str(page[token_key]) in seen:
            return
        seen.add(str(page[token_key]))
        params = {**kwargs, token_key: page[token_key]}


def dig(page: dict[str, Any], result_key: str) -> list[Any]:
    """Return the response list identified by result_key.

    page: One API response page
    result_key: Result list key path (for example,
        DistributionList.Items)

    Return an empty list for a missing key, a non-dict intermediate
    value, or
    a non-list final value.
    """
    node: Any = page
    for part in result_key.split("."):
        if not isinstance(node, dict):
            return []
        node = node.get(part)
        if node is None:
            return []
    return list(node) if isinstance(node, list) else []


def pages(
    client: Any, method: str, result_key: str, **kwargs: Any
) -> list[dict[str, Any]]:
    """Combine paginated API query results into one list.

    client: boto3 client for the query
    method: boto3 client method name
    result_key: Result list key path within each response
    kwargs: Request parameters for the API call

    Use response page tokens when boto3 does not provide a paginator.
    """
    if client.can_paginate(method):
        pages: Iterable[dict[str, Any]] = client.get_paginator(method).paginate(
            **kwargs
        )
    else:
        pages = _manual_pages(client, method, kwargs)
    items: list[dict[str, Any]] = []
    for page in pages:
        items.extend(dig(page, result_key))
    return items


@dataclass(frozen=True, slots=True)
class Ctx:
    """Store query context and scope for resource account and region
    placement.

    account_id: AWS account ID used to query the API
    region: Region of the query
    scope_accounts: Account IDs included in the collection scope
    scope_regions: Regions included in the collection scope
    """

    account_id: str
    region: str
    scope_accounts: frozenset[str]
    scope_regions: frozenset[str]

    def attribute(
        self, item: dict[str, Any], key: str = "OwnerId"
    ) -> tuple[str, dict[str, Any]]:
        """Return the resource node's account ID and owner account
        attributes.

        item: API response item containing the owner account ID
        key: Response key for the owner account ID (default: OwnerId)

        Use the owner account if it is in scope; otherwise, use the
        querying
        account. Record an out-of-scope owner in owner_account_id when
        it differs
        from the querying account.
        """
        raw = item.get(key)
        owner = str(raw) if raw else None
        if owner and owner in self.scope_accounts:
            return owner, {}
        if owner and owner != self.account_id:
            return self.account_id, {"owner_account_id": owner}
        return self.account_id, {}

    def region_node(self, account_id: str | None = None) -> str:
        """Return the region node's Node.id for an account and the query
        region.

        account_id: Account ID to use (self.account_id if omitted)
        """
        return node_id("region", account_id or self.account_id, self.region)

    def canonical_region(self, info: dict[str, Any], owner: str) -> str:
        """Return a region consistent with collection scope and resource
        ownership.

        info: API response containing Region and OwnerId
        owner: Account ID to use for the resource node

        Use the query region if the response region is absent or outside
        scope.
        Also use it when an explicit OwnerId differs from owner.
        """
        raw_region = info.get("Region")
        raw_owner = info.get("OwnerId")
        region = str(raw_region) if raw_region else None
        if region is None or region not in self.scope_regions:
            return self.region
        if raw_owner and str(raw_owner) != owner:
            return self.region
        return region


@dataclass(frozen=True, slots=True)
class EdgeDraft:
    """Store a relationship whose target has not yet been resolved to
    Node.id.

    type: Relationship type (for example, route or association)
    source: Source resource's Node.id
    target_native: Native ID, DNS name, or Node.id used to find the
        target
    target_type: Target resource type (skip type checks if None)
    directed: Whether the relationship points from source to target
    attributes: Values to include in Edge.attributes
    disc: Extra value distinguishing a resource pair's links (for
        example, ingress)
    live: Whether to use this relationship for linking (omit if False)
    by_dns: Whether to resolve the target by DNS name
    by_id: Whether to resolve by Node.id (takes precedence over by_dns)
    ref: Name to record for an unresolved target (target_native if
        absent)
    why_attr: Source Node.attributes key for the unresolved reason
    """

    type: str
    source: str
    target_native: str
    target_type: str | None
    directed: bool
    attributes: dict[str, Any]
    disc: str | None = None
    live: bool = True
    by_dns: bool = False
    by_id: bool = False
    ref: str | None = None
    why_attr: str | None = None


def dns_key(name: str) -> str:
    """Normalize a DNS name for comparison.

    name: DNS name to compare

    Strip surrounding whitespace and the trailing dot, convert to
    lowercase,
    and remove a leading dualstack. prefix.
    """
    key = name.strip().lower().rstrip(".")
    return key.removeprefix("dualstack.")


@dataclass(frozen=True, slots=True)
class TargetRef:
    """Store a registered load balancer target and its health for
    observation.

    target_group: Target group name
    target_group_arn: Target group ARN
    target_type: Registered type (for example, instance, ip, lambda, or
        alb)
    target_id: Native target ID (for example, EC2 ID, IP address, or
        Lambda ARN)
    port: Target port (fall back to the group port, except for Lambda)
    zone: Registration AvailabilityZone (for example, ap-northeast-2a or
        all)
    health: Target health state (None if unknown)
    health_reason: TargetHealth.Reason code from the API response
    """

    target_group: str
    target_group_arn: str
    target_type: str
    target_id: str
    port: int | None
    zone: str | None
    health: str | None = None
    health_reason: str | None = None


TARGET_HEALTH_DETAIL: tuple[tuple[str, str], ...] = (
    ("health_check_off_target_count", "health_check_off"),
    ("initial_target_count", "initial"),
    ("draining_target_count", "draining"),
    ("unused_target_count", "unused"),
    ("other_unavailable_target_count", "other_unavailable"),
)
HEALTH_CHECK_DISABLED = "Target.HealthCheckDisabled"
_DETAIL_BY_STATE = {
    "initial": "initial_target_count",
    "draining": "draining_target_count",
    "unused": "unused_target_count",
}


def health_detail(state: str | None, reason: str | None) -> str | None:
    """Return the count category for a load balancer target's health
    state.

    state: Target health state (for example, initial or unavailable)
    reason: TargetHealth.Reason code from the API response

    Distinguish unavailable using Target.HealthCheckDisabled. Return
    None if
    no count category applies.
    """
    if state == "unavailable":
        return (
            "health_check_off_target_count"
            if reason == HEALTH_CHECK_DISABLED
            else "other_unavailable_target_count"
        )
    return _DETAIL_BY_STATE.get(state or "")


@dataclass(frozen=True, slots=True)
class TargetGroupRef:
    """Store target group configuration regardless of target
    registration.

    name: Target group name
    arn: Target group ARN
    target_type: Registered target type (for example, instance or ip)
    port: Target group port (None if absent)
    cluster: EKS cluster name from the group's elbv2.k8s.aws/cluster tag
    """

    name: str
    arn: str
    target_type: str
    port: int | None
    cluster: str | None = None


@dataclass(slots=True)
class NodeDraft:
    """Store a resource node whose parent and links need collection-wide
    data.

    node: Resource node whose parent and links to resolve
    account_id: Account ID used to query this resource
    region: Region of the resource query
    parents: Candidate parent Node.id values in priority order
    parent_types: Resource types corresponding to parents
    edges: Relationships whose targets are not yet resolved to Node.id
    lb_targets: Registered load balancer targets and their health
    lb_groups: Load balancer target group configuration
    asg_members: Set of EC2 instance IDs in the Auto Scaling group
    shared_native: Whether to merge views with the same type and native
        ID
    private: Temporary data for later processing (for example, EIP owner
        ENIs)
    """

    node: Node
    account_id: str
    region: str
    parents: tuple[str, ...]
    parent_types: tuple[str, ...]
    edges: list[EdgeDraft] = field(default_factory=list)
    lb_targets: tuple[TargetRef, ...] = ()
    lb_groups: tuple[TargetGroupRef, ...] = ()
    asg_members: frozenset[str] = frozenset()
    shared_native: bool = False
    private: dict[str, Any] = field(default_factory=dict)


def alive(state: Any) -> bool:
    """Return True for states eligible for relationship and metric
    queries.

    state: Resource state to check

    DEAD_STATES defines excluded states. Absent and unknown states
    return True.
    """
    return str(state or "").lower() not in DEAD_STATES


def tags_of(
    item: dict[str, Any],
    field_name: str = "Tags",
    key_name: str = "Key",
    value_name: str = "Value",
) -> dict[str, str]:
    """Convert an API tag list to a key-value dictionary.

    item: API response item containing the tag list
    field_name: Tag list response key (for example, Tags or TagList)
    key_name: Tag name key (for example, Key)
    value_name: Tag value key (for example, Value)

    Exclude tags missing a key or value field. Return an empty dict if
    the tag
    list is absent.
    """
    return {
        t[key_name]: t[value_name]
        for t in item.get(field_name) or []
        if key_name in t and value_name in t
    }


def sorted_strs(values: Iterable[Any]) -> list[str]:
    """Convert values to strings, deduplicate them, and return a sorted
    list.

    values: Values to convert to strings

    Exclude falsy values such as None, False, 0, and the empty string.
    """
    return sorted({str(v) for v in values if v})


def draft(
    *,
    node: Node,
    ctx: Ctx,
    parents: tuple[str, ...],
    parent_types: tuple[str, ...],
    edges: list[EdgeDraft] | None = None,
    shared_native: bool = False,
    lb_targets: tuple[TargetRef, ...] = (),
    lb_groups: tuple[TargetGroupRef, ...] = (),
    asg_members: frozenset[str] = frozenset(),
    private: dict[str, Any] | None = None,
) -> NodeDraft:
    """Create a NodeDraft from a resource node and collection context.

    node: Collected resource node
    ctx: Query account, region, and collection scope
    parents: Candidate parent Node.id values in priority order (at least
        one)
    parent_types: Resource types corresponding to parents
    edges: Relationships with unresolved targets
    shared_native: Whether to merge later views with the same type and
        native ID
    lb_targets: Registered load balancer targets and their health
    lb_groups: Load balancer target group configuration
    asg_members: Set of EC2 instance IDs in the Auto Scaling group
    private: Temporary data for later processing (stored as a dict copy)

    Set Node.parent_id to the first entry in parents.
    """
    return NodeDraft(
        node=replace(node, parent_id=parents[0]),
        account_id=ctx.account_id,
        region=ctx.region,
        parents=parents,
        parent_types=parent_types,
        edges=edges or [],
        shared_native=shared_native,
        lb_targets=lb_targets,
        lb_groups=lb_groups,
        asg_members=asg_members,
        private=dict(private or {}),
    )
