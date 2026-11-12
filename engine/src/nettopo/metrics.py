"""Query CloudWatch metrics and aggregate traffic evidence by target.

Probe defines namespaces, dimensions, and metrics. MetricRef defines
statistics and aggregation. Distinguish query failures and missing data
points from observed zero values.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from .model import Reason, Snapshot
from .services.base import classify

METRIC_API = "cloudwatch:GetMetricData"

PERIOD_SECONDS = 60
WINDOW_SECONDS = 900

QUERIES_PER_CALL = 100

ROLE_FLOW = "flow"
ROLE_DROP = "drop"
ROLE_HEALTH = "health"
ROLE_STATE = "state"

AGG_SUM = "sum"
AGG_MAX = "max"
AGG_MIN = "min"

REPORT_ALWAYS = "always"
REPORT_NONZERO_ONLY = "nonzero_only"
REPORT_CONDITIONAL = "conditional"
REPORT_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MetricRef:
    """Define metric statistics and aggregation rules for traffic
    evaluation.

    name: CloudWatch metric name
    statistic: API statistic (for example, Sum or Maximum)
    aggregate: How to combine values in the queried interval (sum, min,
        max)
    role: Role in traffic, drop, state, or health evaluation
    reporting: Whether the metric reports continuously or only when
        nonzero
    blocking: Whether drop or error values can establish blocking
    arrival_only: Whether the metric shows arrivals without confirming
        passage
    empty_is_zero: Whether a completed empty query is treated as zero
    """

    name: str
    statistic: str
    aggregate: str
    role: str
    reporting: str
    blocking: bool = False
    arrival_only: bool = False
    empty_is_zero: bool = False


def _flow(
    name: str,
    *,
    reporting: str,
    statistic: str = "Sum",
    aggregate: str = AGG_SUM,
    arrival_only: bool = False,
    empty_is_zero: bool = False,
) -> MetricRef:
    return MetricRef(
        name,
        statistic,
        aggregate,
        ROLE_FLOW,
        reporting,
        arrival_only=arrival_only,
        empty_is_zero=empty_is_zero,
    )


def _drop(name: str, *, reporting: str, blocking: bool) -> MetricRef:
    return MetricRef(name, "Sum", AGG_SUM, ROLE_DROP, reporting, blocking)


def _health(name: str, *, reporting: str = REPORT_CONDITIONAL) -> MetricRef:
    return MetricRef(name, "Minimum", AGG_MIN, ROLE_HEALTH, reporting)


def _state(name: str, *, reporting: str = REPORT_ALWAYS) -> MetricRef:
    return MetricRef(name, "Maximum", AGG_MAX, ROLE_STATE, reporting)


@dataclass(frozen=True, slots=True)
class Probe:
    """Define CloudWatch metrics to query for a resource or
    relationship.

    key: Name identifying the query rules
    namespace: CloudWatch namespace
    dimensions: Dimension names, paired with values in order
    metrics: Metrics and aggregation rules to query
    """

    key: str
    namespace: str
    dimensions: tuple[str, ...]
    metrics: tuple[MetricRef, ...]



NODE_PROBES: dict[str, Probe] = {
    "nat_gateway": Probe(
        "nat_gateway",
        "AWS/NATGateway",
        ("NatGatewayId",),
        (
            _flow("BytesOutToDestination", reporting=REPORT_ALWAYS),
            _flow("BytesInFromSource", reporting=REPORT_ALWAYS),
            _drop("PacketsDropCount", reporting=REPORT_ALWAYS, blocking=False),
            _drop("ErrorPortAllocation", reporting=REPORT_UNKNOWN, blocking=True),
        ),
    ),
    "transit_gateway": Probe(
        "transit_gateway",
        "AWS/TransitGateway",
        ("TransitGateway",),
        (
            _flow("BytesIn", reporting=REPORT_ALWAYS, arrival_only=True),
            _flow("BytesOut", reporting=REPORT_ALWAYS),
            _drop("PacketDropCountBlackhole", reporting=REPORT_ALWAYS, blocking=True),
            _drop("PacketDropCountNoRoute", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
    "transit_gateway_attachment": Probe(
        "transit_gateway_attachment",
        "AWS/TransitGateway",
        ("TransitGateway", "TransitGatewayAttachment"),
        (
            _flow("BytesIn", reporting=REPORT_ALWAYS, arrival_only=True),
            _flow("BytesOut", reporting=REPORT_ALWAYS),
            _drop("PacketDropCountBlackhole", reporting=REPORT_ALWAYS, blocking=True),
            _drop("PacketDropCountNoRoute", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
    "vpc_endpoint": Probe(
        "vpc_endpoint",
        "AWS/PrivateLinkEndpoints",
        ("VPC Id", "VPC Endpoint Id", "Endpoint Type", "Service Name"),
        (
            _flow("BytesProcessed", reporting=REPORT_NONZERO_ONLY),
            _flow("NewConnections", reporting=REPORT_NONZERO_ONLY),
            _drop("PacketsDropped", reporting=REPORT_NONZERO_ONLY, blocking=True),
        ),
    ),
    "load_balancer:application": Probe(
        "load_balancer:application",
        "AWS/ApplicationELB",
        ("LoadBalancer",),
        (
            _flow("ProcessedBytes", reporting=REPORT_NONZERO_ONLY, empty_is_zero=True),
            _flow("RequestCount", reporting=REPORT_CONDITIONAL),
        ),
    ),
    "load_balancer:network": Probe(
        "load_balancer:network",
        "AWS/NetworkELB",
        ("LoadBalancer",),
        (
            _flow("ProcessedBytes", reporting=REPORT_ALWAYS),
            _flow("NewFlowCount", reporting=REPORT_ALWAYS),
            _flow(
                "ActiveFlowCount",
                reporting=REPORT_ALWAYS,
                statistic="Average",
                aggregate=AGG_MAX,
            ),
            _drop("RejectedFlowCount", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
}

EDGE_PROBES: dict[str, Probe] = {
    "attachment": Probe(
        "attachment",
        "AWS/TransitGateway",
        ("TransitGateway", "TransitGatewayAttachment"),
        (
            _flow("BytesIn", reporting=REPORT_ALWAYS, arrival_only=True),
            _flow("BytesOut", reporting=REPORT_ALWAYS),
            _drop("PacketDropCountBlackhole", reporting=REPORT_ALWAYS, blocking=True),
            _drop("PacketDropCountNoRoute", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
    "endpoint_connection": Probe(
        "endpoint_connection",
        "AWS/PrivateLinkEndpoints",
        ("VPC Id", "VPC Endpoint Id", "Endpoint Type", "Service Name", "Subnet Id"),
        (
            _flow("BytesProcessed", reporting=REPORT_NONZERO_ONLY),
            _flow("NewConnections", reporting=REPORT_NONZERO_ONLY),
            _drop("PacketsDropped", reporting=REPORT_NONZERO_ONLY, blocking=True),
        ),
    ),
    "load_balancer_placement:application": Probe(
        "load_balancer_placement:application",
        "AWS/ApplicationELB",
        ("LoadBalancer", "AvailabilityZone"),
        (
            _flow("ProcessedBytes", reporting=REPORT_NONZERO_ONLY, empty_is_zero=True),
            _flow("RequestCount", reporting=REPORT_CONDITIONAL),
        ),
    ),
    "load_balancer_placement:network": Probe(
        "load_balancer_placement:network",
        "AWS/NetworkELB",
        ("LoadBalancer", "AvailabilityZone"),
        (
            _flow("ProcessedBytes", reporting=REPORT_ALWAYS),
            _flow("NewFlowCount", reporting=REPORT_ALWAYS),
            _drop("RejectedFlowCount", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
}

_ENDPOINT_ACCEPTED = "available"
_ENDPOINT_ZERO_FILLED = "BytesProcessed"


def _accepted_variant(probe: Probe) -> Probe:
    return replace(
        probe,
        metrics=tuple(
            replace(ref, empty_is_zero=True)
            if ref.name == _ENDPOINT_ZERO_FILLED
            else ref
            for ref in probe.metrics
        ),
    )


ACCEPTED_ENDPOINT_PROBES: dict[str, Probe] = {
    "vpc_endpoint": _accepted_variant(NODE_PROBES["vpc_endpoint"]),
    "endpoint_connection": _accepted_variant(EDGE_PROBES["endpoint_connection"]),
}

NO_METRIC_TYPES = frozenset(
    {
        "vpc",
        "subnet",
        "route_table",
        "internet_gateway",
        "vpc_peering_connection",
        "transit_gateway_route_table",
        "sagemaker_notebook_instance",
        "sagemaker_domain",
        "ec2_instance_connect_endpoint",
        "vpn_gateway",
        "customer_gateway",
        "direct_connect_gateway",
    }
)

METRIC_PENDING_TYPES = frozenset(
    {
        "rds_db_instance",
        "ecs_service",
        "sagemaker_endpoint",
        "ec2_instance",
        "eks_cluster",
        "network_firewall",
        "vpn_connection",
        "elasticache_cluster",
        "rds_db_cluster",
        "opensearch_domain",
        "msk_cluster",
        "direct_connect_connection",
        "direct_connect_virtual_interface",
    }
)

_INTERFACE = "interface"


@dataclass(frozen=True, slots=True)
class MetricQuery:
    """Store an observation target and its CloudWatch query parameters.

    target_id: Observed Node.id or Edge.id
    target_kind: Target kind (node or edge)
    account_id: Account where metrics are queried
    region: Region where metrics are queried
    resource_type: Resource type used to record query status
    probe: Rules defining metrics and dimension names
    values: Dimension values in Probe.dimensions order
    observed_on: Measured resource's Node.id if it differs from the
        target
    """

    target_id: str
    target_kind: str
    account_id: str
    region: str
    resource_type: str
    probe: Probe
    values: tuple[str, ...]
    observed_on: str | None = None

    def dimensions(self) -> list[dict[str, str]]:
        return [
            {"Name": name, "Value": value}
            for name, value in zip(self.probe.dimensions, self.values, strict=True)
        ]


@dataclass(frozen=True, slots=True)
class Reading:
    """Store one metric's aggregate and data point counts.

    metric: Metric name
    role: Role in traffic, drop, state, or health evaluation
    aggregate: How values in the interval are combined
    points: Number of returned data points
    nonzero: Number of nonzero data points
    value: Aggregate value (None if unknown)
    reporting: Metric reporting behavior
    blocking: Whether the metric can establish blocking
    first_nonzero_at: Time of the first nonzero value
    last_nonzero_at: Time of the last nonzero value
    arrival_only: Whether the metric shows only arriving traffic
    zero_filled: Whether a completed empty query was treated as zero by
        rule

    value=None differs from an observed zero.
    """

    metric: str
    role: str
    aggregate: str
    points: int
    nonzero: int
    value: float | None
    reporting: str
    blocking: bool = False
    first_nonzero_at: str | None = None
    last_nonzero_at: str | None = None
    arrival_only: bool = False
    zero_filled: bool = False

    @property
    def measured(self) -> bool:
        return self.points > 0 or self.zero_filled

    @property
    def silence_is_expected(self) -> bool:
        """Return whether reporting is configured to a value other than
        always.

        True does not establish that missing data points are actual
        zeros.
        """
        return self.reporting != REPORT_ALWAYS


@dataclass(frozen=True, slots=True)
class Verdict:
    """Combine target metrics into traffic, drop, error, and blocking
    evidence.

    metric_names: Names of queried metrics
    flow_names: Metric names used for traffic evaluation
    flow_metric: Metric selected for traffic evaluation
    flow_value: Aggregate value of the selected traffic metric
    flow_points: Nonzero data point count for the selected traffic
        metric
    drop_metric: Metric selected as drop or error evidence
    drop_value: Aggregate value of the selected drop or error metric
    block_drop_metric: Drop or error metric that can establish blocking
    block_drop_value: Aggregate value of that blocking metric
    blocked_metric: Metric whose state value establishes blocking
    measured: Whether any metric supplied a value or a rule-based zero
    arrival_metric: Metric showing only arriving traffic
    arrival_value: Aggregate value of the selected arrival metric
    """

    metric_names: tuple[str, ...]
    flow_names: tuple[str, ...]
    flow_metric: str | None
    flow_value: float
    flow_points: int
    drop_metric: str | None
    drop_value: float
    block_drop_metric: str | None
    block_drop_value: float
    blocked_metric: str | None
    measured: bool
    arrival_metric: str | None = None
    arrival_value: float = 0.0

    @property
    def flowing(self) -> bool:
        return self.flow_metric is not None and self.flow_value > 0

    @property
    def blocked(self) -> bool:
        """Return True for blocking evidence without evidence of passing
        traffic.

        blocked_metric or a positive block_drop_value establishes
        blocking.
        """
        if self.flowing:
            return False
        return self.blocked_metric is not None or self.block_drop_value > 0

    @property
    def arrival_unproven(self) -> bool:
        return not self.flowing and not self.blocked and self.arrival_value > 0

    @property
    def arrival_why(self) -> str | None:
        if not self.arrival_unproven:
            return None
        return (
            f"{self.arrival_metric} = {self.arrival_value:g} 로 들어왔지만 나간 통과량이 없고 "
            f"차단 드롭을 측정하지 못했습니다 — 건너갔는지 버려졌는지 알 수 없습니다 (창 {WINDOW_SECONDS}초)"
        )

    @property
    def drops(self) -> int | None:
        """Return the displayed drop or error aggregate as an integer.

        Return None if the metric is unknown. This value alone does not
        establish
        blocking.
        """
        if self.drop_metric is None:
            return None
        return round(self.drop_value)

    @property
    def block_reason(self) -> str | None:
        if not self.blocked:
            return None
        if self.blocked_metric is not None:
            return f"{self.blocked_metric} = 0 (창 {WINDOW_SECONDS}초)"
        arrived = (
            f"들어옴 {self.arrival_metric} = {self.arrival_value:g} · "
            if self.arrival_metric is not None and self.arrival_value > 0
            else ""
        )
        return (
            f"통과량 0 · {arrived}{self.block_drop_metric} = "
            f"{self.block_drop_value:g} (창 {WINDOW_SECONDS}초)"
        )

    @property
    def report_metric(self) -> str | None:
        if self.flowing:
            return self.flow_metric
        if self.blocked_metric is not None:
            return self.blocked_metric
        if self.block_drop_metric is not None and self.block_drop_value > 0:
            return self.block_drop_metric
        if self.arrival_unproven:
            return self.arrival_metric
        if self.flow_metric is not None:
            return self.flow_metric
        if self.flow_names:
            return self.flow_names[0]
        return self.metric_names[0] if self.metric_names else None


def _fold(readings: dict[str, Reading], *, complete: bool = True) -> Verdict:
    names = tuple(sorted(readings))
    measured_flow = [r for r in readings.values() if r.role == ROLE_FLOW and r.measured]
    flow = [r for r in measured_flow if not r.arrival_only]
    arrivals = [r for r in measured_flow if r.arrival_only]
    drops = [r for r in readings.values() if r.role == ROLE_DROP and r.measured]
    blocked = [
        r
        for r in readings.values()
        if r.role in (ROLE_STATE, ROLE_HEALTH) and r.measured and (r.value or 0) <= 0
    ]
    best_flow = max(
        flow, key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]), default=None
    )
    best_drop = max(
        drops, key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]), default=None
    )
    best_block_drop = max(
        (r for r in drops if r.blocking),
        key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]),
        default=None,
    )
    first_block = min(blocked, key=lambda r: r.metric, default=None)
    best_arrival = max(
        arrivals,
        key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]),
        default=None,
    )
    arrival_value = float(best_arrival.value or 0.0) if best_arrival else 0.0
    blocking = [r for r in readings.values() if r.role == ROLE_DROP and r.blocking]
    block_drops_zero = (
        complete
        and bool(blocking)
        and all(r.measured and (r.value or 0.0) <= 0 for r in blocking)
    )
    crossed = best_flow is not None and (best_flow.value or 0.0) > 0
    if not crossed and arrival_value > 0 and block_drops_zero:
        best_flow = best_arrival
    return Verdict(
        metric_names=names,
        flow_names=tuple(
            sorted(r.metric for r in readings.values() if r.role == ROLE_FLOW)
        ),
        flow_metric=best_flow.metric if best_flow else None,
        flow_value=float(best_flow.value or 0.0) if best_flow else 0.0,
        flow_points=best_flow.nonzero if best_flow else 0,
        drop_metric=best_drop.metric if best_drop else None,
        drop_value=float(best_drop.value or 0.0) if best_drop else 0.0,
        block_drop_metric=best_block_drop.metric if best_block_drop else None,
        block_drop_value=(
            float(best_block_drop.value or 0.0) if best_block_drop else 0.0
        ),
        blocked_metric=first_block.metric if first_block else None,
        measured=any(r.measured for r in readings.values()),
        arrival_metric=best_arrival.metric if best_arrival else None,
        arrival_value=arrival_value,
    )


@dataclass(frozen=True, slots=True)
class MetricLayer:
    """Pass metric query results and failures to observation processing.

    enabled: Whether metric queries are enabled
    window_seconds: Length of the metric aggregation interval in seconds
    readings: Results by (target ID, metric name)
    planned: Target IDs with planned queries
    failures: Failure status and Reason by (account ID, region, resource
        type)
    queried: Queried (account ID, region, resource type) tuples
    unbuildable: Target IDs and reasons for missing required dimensions
    observed_on: Mapping from target IDs to measured resource Node.id
    incomplete: Targets whose requested metric results are absent from
        the API
    warnings: Query warnings
    """

    enabled: bool
    window_seconds: int | None
    readings: dict[tuple[str, str], Reading]
    planned: frozenset[str]
    failures: dict[tuple[str, str, str], tuple[str, Reason]]
    queried: frozenset[tuple[str, str, str]]
    unbuildable: dict[str, str]
    observed_on: dict[str, str]
    incomplete: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()

    @classmethod
    def off(cls) -> MetricLayer:
        return cls(
            enabled=False,
            window_seconds=None,
            readings={},
            planned=frozenset(),
            failures={},
            queried=frozenset(),
            unbuildable={},
            observed_on={},
        )

    def cell(self, account_id: str, region: str, resource_type: str) -> tuple[str, Any]:
        if not self.enabled:
            return "skipped", None
        failed = self.failures.get((account_id, region, resource_type))
        if failed is not None:
            return failed[0], failed[1].to_dict()
        return "ok", None

    def readings_for(self, target_id: str) -> dict[str, Reading]:
        return {
            metric: reading
            for (tid, metric), reading in self.readings.items()
            if tid == target_id
        }

    def verdict(self, target_id: str) -> Verdict | None:
        readings = self.readings_for(target_id)
        if not readings:
            return None
        return _fold(readings, complete=target_id not in self.incomplete)

    def answered(self) -> frozenset[str]:
        return frozenset(
            tid for (tid, _metric), r in self.readings.items() if r.measured
        )


def probe_for(node: Any) -> Probe | None:
    attributes = node.attributes or {}
    if node.type == "load_balancer":
        return NODE_PROBES.get(f"load_balancer:{_lb_kind(node)}")
    if node.type == "vpc_endpoint":
        if str(attributes.get("endpoint_type") or "").casefold() != _INTERFACE:
            return None
        return _endpoint_probe(node, "vpc_endpoint")
    return NODE_PROBES.get(node.type)


def _endpoint_probe(endpoint: Any, key: str) -> Probe:
    state = str((endpoint.attributes or {}).get("state") or "").casefold()
    if state == _ENDPOINT_ACCEPTED:
        return ACCEPTED_ENDPOINT_PROBES[key]
    return NODE_PROBES[key] if key in NODE_PROBES else EDGE_PROBES[key]


def _lb_kind(node: Any) -> str:
    return str((node.attributes or {}).get("load_balancer_type") or "").casefold()


def _lb_dimension(node: Any) -> str | None:
    arn = node.arn or ""
    marker = ":loadbalancer/"
    index = arn.find(marker)
    if index < 0:
        return None
    return arn[index + len(marker) :] or None


def _vpc_of(node: Any) -> str | None:
    parent = node.parent_id or ""
    return parent.rsplit("/", 1)[-1] if parent.startswith("vpc/") else None


def _endpoint_values(node: Any) -> tuple[str | None, ...]:
    attributes = node.attributes or {}
    return (
        _vpc_of(node),
        node.native_id,
        attributes.get("endpoint_type"),
        attributes.get("service_name"),
    )


def dimension_values(node: Any, probe: Probe) -> tuple[str, ...] | None:
    """Return a node's dimension values in Probe.dimensions order.

    Return None for an unsupported type or missing required dimension.
    """
    attributes = node.attributes or {}
    if node.type == "nat_gateway" or node.type == "transit_gateway":
        values: tuple[str | None, ...] = (node.native_id,)
    elif node.type == "transit_gateway_attachment":
        values = (attributes.get("transit_gateway_id"), node.native_id)
    elif node.type == "vpc_endpoint":
        values = _endpoint_values(node)
    elif node.type == "load_balancer":
        values = (_lb_dimension(node),)
    else:
        return None
    return _checked(values, probe)


def _checked(values: tuple[Any, ...], probe: Probe) -> tuple[str, ...] | None:
    if len(values) != len(probe.dimensions) or any(not v for v in values):
        return None
    return tuple(str(v) for v in values)


def edge_probe_for(edge: Any, nodes: dict[str, Any]) -> Probe | None:
    if edge.type == "attachment":
        source = nodes.get(edge.source)
        if source is None or source.type != "transit_gateway_attachment":
            return None
        return EDGE_PROBES["attachment"]
    if edge.type == "endpoint_connection":
        source = nodes.get(edge.source)
        if source is None or probe_for(source) is None:
            return None
        return _endpoint_probe(source, "endpoint_connection")
    if edge.type == "load_balancer_placement":
        source = nodes.get(edge.source)
        if source is None or source.type != "load_balancer":
            return None
        return EDGE_PROBES.get(f"load_balancer_placement:{_lb_kind(source)}")
    return None


def edge_dimension_values(
    edge: Any, nodes: dict[str, Any], probe: Probe
) -> tuple[str, ...] | None:
    """Return metric dimension values from an edge's source and target.

    Return None for an unsupported relationship or missing required
    value.
    """
    source = nodes.get(edge.source)
    target = nodes.get(edge.target)
    if source is None:
        return None
    if edge.type == "attachment":
        own = (source.attributes or {}).get("transit_gateway_id")
        values: tuple[Any, ...] = (
            target.native_id if _far_side(edge, nodes) else own,
            source.native_id,
        )
    elif edge.type == "endpoint_connection":
        values = (*_endpoint_values(source), target.native_id if target else None)
    elif edge.type == "load_balancer_placement":
        zone = (target.attributes or {}).get("availability_zone") if target else None
        values = (_lb_dimension(source), zone)
    else:
        return None
    return _checked(values, probe)


def lands_on_another_tgw(
    edge_type: str, own_tgw: Any, target_type: Any, target_native: Any
) -> bool:
    return (
        edge_type == "attachment"
        and target_type == "transit_gateway"
        and bool(own_tgw)
        and target_native != own_tgw
    )


def _far_side(edge: Any, nodes: dict[str, Any]) -> bool:
    source = nodes.get(edge.source)
    target = nodes.get(edge.target)
    if source is None or target is None:
        return False
    own = (source.attributes or {}).get("transit_gateway_id")
    return lands_on_another_tgw(edge.type, own, target.type, target.native_id)


def _resource_of_edge(edge: Any, nodes: dict[str, Any]) -> str | None:
    source = nodes.get(edge.source)
    return source.type if source is not None else None


def plan(snapshot: Snapshot) -> tuple[list[MetricQuery], list[tuple[str, str]]]:
    """Return planned metric queries and targets missing required
    dimensions.

    snapshot: Collected resources and relationships to observe

    Return (MetricQuery list, list of target IDs and missing-value
    reasons).
    """
    queries: list[MetricQuery] = []
    unbuildable: list[tuple[str, str]] = []
    nodes = {n.id: n for n in snapshot.nodes}

    for node in sorted(snapshot.nodes, key=lambda n: n.id):
        probe = probe_for(node)
        if probe is None or node.region is None:
            continue
        values = dimension_values(node, probe)
        if values is None:
            unbuildable.append((node.id, _unbuildable_why(probe)))
            continue
        queries.append(
            MetricQuery(
                target_id=node.id,
                target_kind="node",
                account_id=node.account_id,
                region=node.region,
                resource_type=node.type,
                probe=probe,
                values=values,
            )
        )

    for edge in sorted(snapshot.edges, key=lambda e: e.id):
        probe = edge_probe_for(edge, nodes)
        if probe is None:
            continue
        at = nodes.get(edge.target if _far_side(edge, nodes) else edge.source)
        resource_type = _resource_of_edge(edge, nodes)
        if at is None or at.region is None or resource_type is None:
            continue
        values = edge_dimension_values(edge, nodes, probe)
        if values is None:
            unbuildable.append((edge.id, _unbuildable_why(probe)))
            continue
        queries.append(
            MetricQuery(
                target_id=edge.id,
                target_kind="edge",
                account_id=at.account_id,
                region=at.region,
                resource_type=resource_type,
                probe=probe,
                values=values,
                observed_on=_deduced_from(edge, nodes),
            )
        )
    return queries, unbuildable


def _deduced_from(edge: Any, nodes: dict[str, Any]) -> str | None:
    target = nodes.get(edge.target)
    if edge.type == "attachment" and target is not None and target.type == "vpc":
        return edge.source
    return None


def _unbuildable_why(probe: Probe) -> str:
    return (
        f"2층 차원({' · '.join(probe.dimensions)})을 1층 응답에서 만들 수 없습니다 "
        "— 구조 수집기가 그 값을 담지 않았습니다(개발)"
    )


def _stamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def _nonzero_span(
    numbers: list[float], timestamps: list[Any] | None
) -> tuple[str | None, str | None]:
    if timestamps is None or len(timestamps) != len(numbers):
        return None, None
    stamped = [_stamp(t) for t, v in zip(timestamps, numbers, strict=True) if v != 0]
    moments = [m for m in stamped if m is not None]
    if not moments or len(moments) != len(stamped):
        return None, None
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return min(moments).strftime(fmt), max(moments).strftime(fmt)


def read_series(
    values: list[Any], ref: MetricRef, timestamps: list[Any] | None = None
) -> Reading:
    """Aggregate metric data points over the interval using MetricRef
    rules.

    values: Metric values returned by the API
    ref: Statistics, aggregation, and traffic evaluation role
    timestamps: Observation times corresponding to values

    An empty input gives Reading.value=None. read_metrics() uses query
    completion and MetricRef.empty_is_zero to decide whether to use
    zero.
    """
    numbers = [float(v) for v in values]
    first_at, last_at = _nonzero_span(numbers, timestamps)
    if not numbers:
        folded: float | None = None
    elif ref.aggregate == AGG_SUM:
        folded = sum(numbers)
    elif ref.aggregate == AGG_MIN:
        folded = min(numbers)
    else:
        folded = max(numbers)
    return Reading(
        metric=ref.name,
        role=ref.role,
        aggregate=ref.aggregate,
        points=len(numbers),
        nonzero=sum(1 for v in numbers if v != 0),
        value=folded,
        reporting=ref.reporting,
        blocking=ref.blocking,
        first_nonzero_at=first_at,
        last_nonzero_at=last_at,
        arrival_only=ref.arrival_only,
    )


def _window(at: str) -> tuple[datetime, datetime]:
    end = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc)
    return end - timedelta(seconds=WINDOW_SECONDS), end


@dataclass(frozen=True, slots=True)
class _Slot:

    namespace: str
    ref: MetricRef
    dimensions: tuple[tuple[str, str], ...]
    targets: tuple[MetricQuery, ...]

    def as_dimensions(self) -> list[dict[str, str]]:
        return [{"Name": name, "Value": value} for name, value in self.dimensions]


def _expand(queries: list[MetricQuery]) -> list[_Slot]:
    grouped: dict[tuple[Any, ...], list[MetricQuery]] = {}
    for query in queries:
        for ref in query.probe.metrics:
            key = (
                query.probe.namespace,
                ref,
                tuple(zip(query.probe.dimensions, query.values, strict=True)),
            )
            grouped.setdefault(key, []).append(query)
    return [
        _Slot(namespace, ref, dimensions, tuple(targets))
        for (namespace, ref, dimensions), targets in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1].name, item[0][2])
        )
    ]


def _batches(slots: list[_Slot]) -> list[list[_Slot]]:
    return [
        slots[i : i + QUERIES_PER_CALL] for i in range(0, len(slots), QUERIES_PER_CALL)
    ]


def read_metrics(
    snapshot: Snapshot,
    client_factory: Any,
    *,
    at: str,
) -> MetricLayer:
    """Return CloudWatch query results and failures in a MetricLayer.

    snapshot: Collected targets and metric dimension values
    client_factory: Function that creates CloudWatch clients by
        account/region
    at: End time of the metric query interval

    Use the returned status and failures to build observation JSON
    coverage.
    """
    queries, unbuildable = plan(snapshot)
    start, end = _window(at)
    readings: dict[tuple[str, str], Reading] = {}
    failures: dict[tuple[str, str, str], tuple[str, Reason]] = {}
    queried = {(q.account_id, q.region, q.resource_type) for q in queries}
    observed_on = {
        q.target_id: q.observed_on for q in queries if q.observed_on is not None
    }
    warnings = [f"{target}: {why}" for target, why in unbuildable]
    incomplete: set[str] = set()

    by_place: dict[tuple[str, str], list[MetricQuery]] = {}
    for query in queries:
        by_place.setdefault((query.account_id, query.region), []).append(query)

    for (account_id, region), group in sorted(by_place.items()):
        try:
            client = client_factory(account_id, region, "cloudwatch")
        except Exception as exc:  # noqa: BLE001
            # Record per-target failures for accounts and regions
            # without a client.
            operation = getattr(exc, "operation_name", None)
            api = f"sts:{operation}" if operation else "nettopo:client_factory"
            status, reason = classify(exc, api)
            for cell in {(account_id, region, q.resource_type) for q in group}:
                failures[cell] = (status, reason)
            continue
        for batch in _batches(_expand(group)):
            ids = {f"m{index}": slot for index, slot in enumerate(batch)}
            try:
                response = client.get_metric_data(
                    MetricDataQueries=[
                        {
                            "Id": key,
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": slot.namespace,
                                    "MetricName": slot.ref.name,
                                    "Dimensions": slot.as_dimensions(),
                                },
                                "Period": PERIOD_SECONDS,
                                "Stat": slot.ref.statistic,
                            },
                            "ReturnData": True,
                        }
                        for key, slot in ids.items()
                    ],
                    StartTime=start,
                    EndTime=end,
                    ScanBy="TimestampDescending",
                )
            except Exception as exc:  # noqa: BLE001
                # Record failures for this request's targets in
                # MetricLayer.failures.
                status, reason = classify(exc, METRIC_API)
                for cell in {
                    (account_id, region, q.resource_type)
                    for slot in batch
                    for q in slot.targets
                }:
                    failures[cell] = (status, reason)
                continue
            returned: set[str] = set()
            for result in response.get("MetricDataResults") or []:
                key = str(result.get("Id"))
                slot = ids.get(key)
                if slot is None:
                    continue
                returned.add(key)
                reading = read_series(
                    list(result.get("Values") or []),
                    slot.ref,
                    list(result.get("Timestamps") or []),
                )
                if (
                    slot.ref.empty_is_zero
                    and reading.points == 0
                    and result.get("StatusCode") == "Complete"
                ):
                    reading = replace(reading, value=0.0, zero_filled=True)
                for query in slot.targets:
                    readings[(query.target_id, slot.ref.name)] = reading
            for key, slot in ids.items():
                if key in returned:
                    continue
                for query in slot.targets:
                    incomplete.add(query.target_id)
                    warnings.append(
                        f"{query.target_id}: {slot.ref.name} 쿼리를 보냈지만 응답에 없다(개발)"
                    )

    targets = {tid for tid, _metric in readings}
    answered = {tid for (tid, _metric), r in readings.items() if r.points > 0}
    if targets and not answered:
        reason = Reason(
            code="api_error",
            api=METRIC_API,
            message=(
                f"2층 대상 {len(targets)}개 전부 지점 0개다 — 차원 이름·값이 틀렸을 때와 "
                "「정말 아무 흐름도 없다」가 같은 응답으로 온다(ADR 0006 §O7-1 함정 1+3). "
                "결함으로 본다"
            ),
        )
        for cell in queried:
            failures.setdefault(cell, ("error", reason))
        warnings.append(
            f"2층 대상 {len(targets)}개 전부 지점 0개 — 차원 상수를 확인하라(§O7-1)"
        )

    return MetricLayer(
        enabled=True,
        window_seconds=WINDOW_SECONDS,
        readings=readings,
        planned=frozenset(q.target_id for q in queries),
        failures=failures,
        queried=frozenset(queried),
        unbuildable=dict(unbuildable),
        observed_on=observed_on,
        incomplete=frozenset(incomplete),
        warnings=tuple(warnings),
    )
