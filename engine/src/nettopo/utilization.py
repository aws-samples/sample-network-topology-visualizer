"""Calculate utilization from resource metrics, counts, and quotas.

Choose measurement windows relative to observation time, separately from
state and traffic evaluation. Reuse previous long-window measurements
and
quotas until refresh is due. Do not report unread measurements as zero.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .metrics import METRIC_API, _endpoint_values, _lb_dimension, _lb_kind, _stamp
from .model import Node, Reason, Snapshot, split_node_id
from .services.base import alive, classify

QUOTA_API = "servicequotas:GetServiceQuota"
DEFAULT_QUOTA_API = "servicequotas:GetAWSDefaultServiceQuota"

WINDOWS: tuple[tuple[int, int], ...] = (
    (15, 60),
    (60, 60),
    (1440, 60),
    (10080, 60),
    (43200, 300),
    (129600, 3600),
)
SHORT_MINUTES = frozenset({15, 60, 1440})
LONG_MINUTES = frozenset({10080, 43200, 129600})
LONG_EVERY_SECONDS = 3600

MAX_POINTS_PER_CALL = 100_800
MAX_QUERIES_PER_CALL = 500

BITS_PER_BYTE = 8
GBPS = 1_000_000_000

VALUE_SUM = "sum"
VALUE_LEVEL = "level"

REPORT_ALWAYS = "always"
REPORT_NONZERO = "nonzero"

QUOTA_SG_RULES = ("vpc", "L-0EA8095F", 60)
QUOTA_NACL_ENTRIES = ("vpc", "L-2AEEBF1A", 20)
QUOTA_ROUTES = ("vpc", "L-93826ACB", 500)
QUOTA_VPC_IPV4_CIDRS = ("vpc", "L-83CA0A9D", 5)
QUOTA_VPC_SUBNETS = ("vpc", "L-407747CB", 200)
QUOTA_VPC_NACLS = ("vpc", "L-B4A6D682", 200)
QUOTA_VPC_ROUTE_TABLES = ("vpc", "L-589F43AA", 200)
QUOTA_VPC_INTERFACE_ENDPOINTS = ("vpc", "L-29B6F2EB", 50)
QUOTA_VPC_PEERINGS = ("vpc", "L-7E9ECCDB", 50)
QUOTA_TGW_ATTACHMENTS = ("ec2", "L-E0233F82", 5000)
QUOTA_TGW_ROUTE_TABLES = ("ec2", "L-43872EB7", 20)
QUOTA_TGW_PEERINGS = ("ec2", "L-A1B5A36F", 50)

FIXED_PROPAGATED_ROUTES = 100
FIXED_TGW_VPC_ATTACHMENT_BPS = 100 * GBPS
FIXED_TGW_VPC_ATTACHMENT_PPS = 7_500_000
FIXED_NFW_BPS = 100 * GBPS
FIXED_DXGW_VIFS = 30
FIXED_DXGW_VGWS = 20
FIXED_DXGW_TGWS = 6
DEFAULT_TGW_ROUTES = 10_000

REQUIRED_ACTIONS = (
    "cloudwatch:GetMetricData",
    "servicequotas:GetServiceQuota",
    "servicequotas:GetAWSDefaultServiceQuota",
)

CLOUDFRONT_METRIC_REGION = "us-east-1"
EC2_MIN_STEP = 300

_NFW_ENGINES = ("Stateful", "Stateless")
_ALWAYS_REPORTED_TGW = (
    REPORT_ALWAYS
)




@dataclass(frozen=True, slots=True, order=True)
class Series:

    """Define a CloudWatch metric query for usage calculation.

    account_id: Account where metrics are queried
    region: Region where metrics are queried
    namespace: CloudWatch namespace
    metric: Metric name
    stat: Requested API statistic
    dimensions: Dimension names and values
    """
    account_id: str
    region: str
    namespace: str
    metric: str
    stat: str
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Cell:

    """Group metrics compared within the same direction and Availability
    Zone.

    series: Metrics whose largest value is used at each timestamp
    direction: Traffic direction
    az: Availability Zone
    """
    series: tuple[Series, ...]
    direction: str | None = None
    az: str | None = None


@dataclass(frozen=True, slots=True)
class RateSpec:
    """Define usage, rate, and limit calculations for each time window.

    target_id: Target resource's Node.id
    measure: Output measurement name
    kind: Measurement kind (for example, rate or events)
    unit: Measurement unit
    cells: Query metrics grouped by direction and Availability Zone
    value: Rule distinguishing interval totals from direct measurements
    factor: Multiplier for conversion to the output unit
    reporting: Metric reporting behavior
    min_step: Minimum metric aggregation interval in seconds
    limit: Limit used for comparison
    limit_source: Source of the limit
    limit_scope: Scope where the limit applies
    total_unit: Unit of the interval total (omit totals if absent)
    empty_zero: Whether to use zero for empty queries of nonzero-only
        metrics
    includes: Description of traffic or other values included
    """
    target_id: str
    measure: str
    kind: str
    unit: str
    cells: tuple[Cell, ...]
    value: str
    factor: float
    reporting: str
    min_step: int = 60
    limit: float | None = None
    limit_source: str | None = None
    limit_scope: str | None = None
    total_unit: str | None = None
    empty_zero: bool = False
    includes: str | None = None


@dataclass(frozen=True, slots=True)
class CountSpec:
    """Store current counts and limits for resource count calculations.

    target_id: Target resource's Node.id
    measure: Output measurement name
    used: Known current usage count
    part: Measured part of the resource
    limit: Limit from configuration or specifications
    limit_source: Source of the limit
    quota: (Service name, quota code, fallback limit if unread)
    limit_scope: Scope where the limit applies
    unread: Reason the current value is unknown
    """
    target_id: str
    measure: str
    used: int | None
    part: str | None = None
    limit: float | None = None
    limit_source: str | None = None
    quota: tuple[str, str, int] | None = None
    limit_scope: str | None = "resource"
    unread: Reason | None = None


def _node(raw: dict[str, Any]) -> Node:
    return Node(
        id=raw["id"],
        type=raw["type"],
        account_id=raw["account_id"],
        region=raw.get("region"),
        native_id=raw.get("native_id"),
        arn=raw.get("arn"),
        name=raw.get("name"),
        parent_id=raw.get("parent_id"),
        attributes=dict(raw.get("attributes") or {}),
    )


def _dims(*pairs: tuple[str, Any]) -> tuple[tuple[str, str], ...] | None:
    if any(not value for _name, value in pairs):
        return None
    return tuple((name, str(value)) for name, value in pairs)


def _series(
    node: Node,
    namespace: str,
    metric: str,
    stat: str,
    dims: Any,
    *,
    region: str | None = None,
) -> Series:
    return Series(
        node.account_id, region or str(node.region), namespace, metric, stat, dims
    )


_BANDWIDTH = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([MG])bps\s*$", re.IGNORECASE)


def parse_bandwidth(value: Any) -> float | None:
    """Convert a Direct Connect bandwidth string to bits per second.

    Return None if the format is unsupported.
    """
    match = _BANDWIDTH.match(str(value or ""))
    if not match:
        return None
    number = float(match.group(1))
    return number * (GBPS if match.group(2).upper() == "G" else 1_000_000)


@dataclass
class _Graph:

    nodes: dict[str, Node]
    children: dict[str, list[Node]]
    by_type: dict[str, list[Node]]
    edges: list[dict[str, Any]]

    @classmethod
    def of(cls, topology: dict[str, Any]) -> _Graph:
        nodes = {raw["id"]: _node(raw) for raw in topology.get("nodes") or []}
        children: dict[str, list[Node]] = {}
        by_type: dict[str, list[Node]] = {}
        for node in sorted(nodes.values(), key=lambda n: n.id):
            if node.parent_id:
                children.setdefault(node.parent_id, []).append(node)
            by_type.setdefault(node.type, []).append(node)
        return cls(nodes, children, by_type, list(topology.get("edges") or []))

    def kids(self, parent: str, node_type: str) -> list[Node]:
        return [n for n in self.children.get(parent, []) if n.type == node_type]

    def of_type(self, node_type: str) -> list[Node]:
        return self.by_type.get(node_type, [])




def _two_way(
    node: Node,
    namespace: str,
    stat: str,
    metric_in: str,
    metric_out: str,
    dims: Any,
    *,
    az: str | None = None,
    region: str | None = None,
) -> tuple[Cell, Cell]:
    return (
        Cell(
            (_series(node, namespace, metric_in, stat, dims, region=region),), "in", az
        ),
        Cell(
            (_series(node, namespace, metric_out, stat, dims, region=region),),
            "out",
            az,
        ),
    )


def _one(
    node: Node,
    namespace: str,
    metric: str,
    stat: str,
    dims: Any,
    *,
    region: str | None = None,
) -> tuple[Cell]:
    return (Cell((_series(node, namespace, metric, stat, dims, region=region),)),)


def _bytes(
    node: Node,
    cells: tuple[Cell, ...],
    reporting: str,
    *,
    value: str = VALUE_SUM,
    factor: float = BITS_PER_BYTE,
    min_step: int = 60,
    includes: str | None = None,
) -> RateSpec:
    return RateSpec(
        node.id,
        "bandwidth",
        "rate",
        "bit_per_second",
        cells,
        value,
        factor,
        reporting,
        min_step=min_step,
        total_unit="byte",
        empty_zero=reporting == REPORT_NONZERO,
        includes=includes,
    )


def _calls(node: Node, measure: str, cells: tuple[Cell, ...]) -> RateSpec:
    return RateSpec(
        node.id,
        measure,
        "rate",
        "count_per_second",
        cells,
        VALUE_SUM,
        1,
        REPORT_NONZERO,
        total_unit="count",
        empty_zero=True,
    )


def _events(node: Node, measure: str, cells: tuple[Cell, ...]) -> RateSpec:
    return RateSpec(
        node.id, measure, "events", "count", cells, VALUE_SUM, 1, REPORT_NONZERO
    )


def _tgw_attachment(node: Node, graph: _Graph) -> list[RateSpec]:
    attrs = node.attributes
    if not alive(attrs.get("state")):
        return []
    base = _dims(
        ("TransitGateway", attrs.get("transit_gateway_id")),
        ("TransitGatewayAttachment", node.native_id),
    )
    if base is None:
        return []
    if attrs.get("resource_type") != "vpc":
        return [
            _bytes(
                node,
                _two_way(
                    node, "AWS/TransitGateway", "Sum", "BytesIn", "BytesOut", base
                ),
                _ALWAYS_REPORTED_TGW,
            )
        ]
    vpcs = sorted(
        {
            e["target"]
            for e in graph.edges
            if e.get("type") == "attachment"
            and e.get("source") == node.id
            and str(e.get("target", "")).startswith("vpc/")
        }
    )
    azs = sorted(
        {
            str(s.attributes.get("availability_zone_id"))
            for vpc in vpcs
            for s in graph.kids(vpc, "subnet")
            if s.attributes.get("availability_zone_id")
        }
    )
    if not azs:
        return []
    bytes_cells: list[Cell] = []
    packet_cells: list[Cell] = []
    for az in azs:
        dims = (("AvailabilityZone", az), *base)
        bytes_cells += _two_way(
            node, "AWS/TransitGateway", "Sum", "BytesIn", "BytesOut", dims, az=az
        )
        packet_cells += _two_way(
            node, "AWS/TransitGateway", "Sum", "PacketsIn", "PacketsOut", dims, az=az
        )
    return [
        _bytes(node, tuple(bytes_cells), REPORT_ALWAYS),
        RateSpec(
            node.id,
            "packet_rate",
            "rate",
            "packet_per_second",
            tuple(packet_cells),
            VALUE_SUM,
            1,
            REPORT_ALWAYS,
            total_unit="packet",
        ),
    ]


def _dx_vif(node: Node, graph: _Graph) -> list[RateSpec]:
    attrs = node.attributes
    if not alive(attrs.get("state")):
        return []
    dims = _dims(
        ("ConnectionId", attrs.get("connection_id")),
        ("VirtualInterfaceId", node.native_id),
    )
    if dims is None:
        return []
    return [
        _bytes(
            node,
            _two_way(
                node,
                "AWS/DX",
                "Average",
                "VirtualInterfaceBpsIngress",
                "VirtualInterfaceBpsEgress",
                dims,
            ),
            REPORT_ALWAYS,
            value=VALUE_LEVEL,
            factor=1,
        )
    ]


def _nfw(node: Node, graph: _Graph) -> list[RateSpec]:
    name = node.native_id
    subnets = [
        graph.nodes.get(f"subnet/{node.account_id}/{node.region}/{sid}")
        for sid in node.attributes.get("subnet_ids") or []
    ]
    azs = sorted(
        {
            str(s.attributes.get("availability_zone"))
            for s in subnets
            if s is not None and s.attributes.get("availability_zone")
        }
    )
    if not name or not azs:
        return []
    cells = tuple(
        Cell(
            tuple(
                _series(
                    node,
                    "AWS/NetworkFirewall",
                    "ReceivedBytes",
                    "Sum",
                    (
                        ("AvailabilityZone", az),
                        ("Engine", engine),
                        ("FirewallName", name),
                    ),
                )
                for engine in _NFW_ENGINES
            ),
            None,
            az,
        )
        for az in azs
    )
    return [_bytes(node, cells, REPORT_NONZERO)]


def _load_balancer(node: Node, graph: _Graph) -> list[RateSpec]:
    dims = _dims(("LoadBalancer", _lb_dimension(node)))
    if dims is None:
        return []
    kind = _lb_kind(node)
    if kind == "application":
        return [
            _bytes(
                node,
                _one(node, "AWS/ApplicationELB", "ProcessedBytes", "Sum", dims),
                REPORT_NONZERO,
            ),
            _events(
                node,
                "rejected_connections",
                _one(
                    node, "AWS/ApplicationELB", "RejectedConnectionCount", "Sum", dims
                ),
            ),
        ]
    if kind == "network":
        return [
            _bytes(
                node,
                _one(node, "AWS/NetworkELB", "ProcessedBytes", "Sum", dims),
                REPORT_ALWAYS,
            ),
            _events(
                node,
                "port_allocation_errors",
                _one(node, "AWS/NetworkELB", "PortAllocationErrorCount", "Sum", dims),
            ),
        ]
    if kind == "gateway":
        return [
            _bytes(
                node,
                _one(node, "AWS/GatewayELB", "ProcessedBytes", "Sum", dims),
                REPORT_NONZERO,
            )
        ]
    return []


def _nat(node: Node, graph: _Graph) -> list[RateSpec]:
    dims = _dims(("NatGatewayId", node.native_id))
    if dims is None or not alive(node.attributes.get("state")):
        return []
    return [
        _bytes(
            node,
            _two_way(
                node,
                "AWS/NATGateway",
                "Sum",
                "BytesInFromDestination",
                "BytesOutToDestination",
                dims,
            ),
            REPORT_ALWAYS,
        ),
        _events(
            node,
            "port_allocation_errors",
            _one(node, "AWS/NATGateway", "ErrorPortAllocation", "Sum", dims),
        ),
    ]


def _endpoint(node: Node, graph: _Graph) -> list[RateSpec]:
    if str(node.attributes.get("endpoint_type") or "").casefold() != "interface":
        return []
    values = _endpoint_values(node)
    dims = _dims(
        *zip(
            ("VPC Id", "VPC Endpoint Id", "Endpoint Type", "Service Name"),
            values,
            strict=True,
        )
    )
    if dims is None:
        return []
    return [
        _bytes(
            node,
            _one(node, "AWS/PrivateLinkEndpoints", "BytesProcessed", "Sum", dims),
            REPORT_NONZERO,
        ),
        _events(
            node,
            "packets_dropped",
            _one(node, "AWS/PrivateLinkEndpoints", "PacketsDropped", "Sum", dims),
        ),
    ]


def _ec2(node: Node, graph: _Graph) -> list[RateSpec]:
    dims = _dims(("InstanceId", node.native_id))
    if dims is None or not alive(node.attributes.get("state")):
        return []
    return [
        _bytes(
            node,
            _two_way(node, "AWS/EC2", "Sum", "NetworkIn", "NetworkOut", dims),
            REPORT_ALWAYS,
            min_step=EC2_MIN_STEP,
        )
    ]


def _rds(node: Node, graph: _Graph) -> list[RateSpec]:
    dims = _dims(("DBInstanceIdentifier", node.native_id))
    if dims is None:
        return []
    return [
        _bytes(
            node,
            _two_way(
                node,
                "AWS/RDS",
                "Average",
                "NetworkReceiveThroughput",
                "NetworkTransmitThroughput",
                dims,
            ),
            REPORT_ALWAYS,
            value=VALUE_LEVEL,
        ),
        RateSpec(
            node.id,
            "connections",
            "rate",
            "count",
            _one(node, "AWS/RDS", "DatabaseConnections", "Maximum", dims),
            VALUE_LEVEL,
            1,
            REPORT_ALWAYS,
        ),
    ]


def _cloudfront(node: Node, graph: _Graph) -> list[RateSpec]:
    dims = _dims(("DistributionId", node.native_id), ("Region", "Global"))
    if dims is None:
        return []
    return [
        _bytes(
            node,
            _two_way(
                node,
                "AWS/CloudFront",
                "Sum",
                "BytesUploaded",
                "BytesDownloaded",
                dims,
                region=CLOUDFRONT_METRIC_REGION,
            ),
            REPORT_NONZERO,
        )
    ]


def _vpn(node: Node, graph: _Graph) -> list[RateSpec]:
    attrs = node.attributes
    if attrs.get("transit_gateway_id") or not attrs.get("vpn_gateway_id"):
        return []
    if not alive(attrs.get("state")):
        return []
    dims = _dims(("VpnId", node.native_id))
    if dims is None:
        return []
    return [
        _bytes(
            node,
            _two_way(node, "AWS/VPN", "Sum", "TunnelDataIn", "TunnelDataOut", dims),
            REPORT_ALWAYS,
            includes="tunnel_health_checks",
        )
    ]


def _lambda(node: Node, graph: _Graph) -> list[RateSpec]:
    dims = _dims(("FunctionName", node.native_id))
    if dims is None:
        return []
    return [
        _calls(
            node, "invocations", _one(node, "AWS/Lambda", "Invocations", "Sum", dims)
        )
    ]


def _api_gateway(node: Node, graph: _Graph) -> list[RateSpec]:
    protocol = str(node.attributes.get("protocol") or "").upper()
    if protocol == "REST":
        dims = _dims(("ApiName", node.name))
        if dims is None:
            return []
        return [
            _calls(node, "requests", _one(node, "AWS/ApiGateway", "Count", "Sum", dims))
        ]
    if protocol == "HTTP":
        dims = _dims(("ApiId", node.native_id))
        if dims is None:
            return []
        return [
            _bytes(
                node,
                _one(node, "AWS/ApiGateway", "DataProcessed", "Sum", dims),
                REPORT_NONZERO,
            ),
            _calls(
                node, "requests", _one(node, "AWS/ApiGateway", "Count", "Sum", dims)
            ),
        ]
    return []


def _elasticache(node: Node, graph: _Graph) -> list[RateSpec]:
    count = _int(node.attributes.get("num_cache_nodes"))
    if not node.native_id or not count:
        return []
    cells: list[Cell] = []
    for index in range(1, count + 1):
        dims = (("CacheClusterId", node.native_id), ("CacheNodeId", f"{index:04d}"))
        cells += _two_way(
            node, "AWS/ElastiCache", "Sum", "NetworkBytesIn", "NetworkBytesOut", dims
        )
    return [_bytes(node, tuple(cells), REPORT_ALWAYS)]


GLOBAL_ACCELERATOR_METRIC_REGION = "us-west-2"


def _global_accelerator(node: Node, graph: _Graph) -> list[RateSpec]:
    dims = _dims(("Accelerator", node.native_id))
    if dims is None:
        return []
    return [
        _bytes(
            node,
            _two_way(
                node,
                "AWS/GlobalAccelerator",
                "Sum",
                "ProcessedBytesIn",
                "ProcessedBytesOut",
                dims,
                region=GLOBAL_ACCELERATOR_METRIC_REGION,
            ),
            REPORT_NONZERO,
        )
    ]


_RATE_BUILDERS: dict[str, Callable[[Node, _Graph], list[RateSpec]]] = {
    "transit_gateway_attachment": _tgw_attachment,
    "direct_connect_virtual_interface": _dx_vif,
    "network_firewall": _nfw,
    "load_balancer": _load_balancer,
    "nat_gateway": _nat,
    "vpc_endpoint": _endpoint,
    "ec2_instance": _ec2,
    "rds_db_instance": _rds,
    "cloudfront": _cloudfront,
    "vpn_connection": _vpn,
    "lambda_function": _lambda,
    "api_gateway": _api_gateway,
    "elasticache_cluster": _elasticache,
    "global_accelerator": _global_accelerator,
}


def plan_rates(topology: dict[str, Any]) -> list[RateSpec]:
    graph = _Graph.of(topology)
    specs: list[RateSpec] = []
    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        build = _RATE_BUILDERS.get(node.type)
        if build is not None:
            specs.extend(build(node, graph))
    return specs




def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _largest(cells: dict[str, int | None]) -> tuple[str, int] | None:
    """Return the entry with the largest value as (name, value).

    Return None for an empty list or if any entry's value is None.
    """
    if not cells or any(v is None for v in cells.values()):
        return None
    part = min(cells, key=lambda k: (-int(cells[k] or 0), k))
    return part, int(cells[part] or 0)


def _security_group(node: Node) -> list[CountSpec]:
    a = node.attributes
    cells = {
        f"{d}_{f}": _int(a.get(f"{d}_{f}_rule_count"))
        for d in ("ingress", "egress")
        for f in ("ipv4", "ipv6")
    }
    found = _largest(cells)
    if found is None:
        return []
    part, used = found
    prefix = (_int(a.get("ingress_prefix_list_rule_count")) or 0) + (
        _int(a.get("egress_prefix_list_rule_count")) or 0
    )
    if prefix:
        return [CountSpec(node.id, "security_group_rules", used, part)]
    return [
        CountSpec(node.id, "security_group_rules", used, part, quota=QUOTA_SG_RULES)
    ]


def _network_acl(node: Node) -> list[CountSpec]:
    a = node.attributes
    found = _largest(
        {
            "ingress": _int(a.get("ingress_entry_count")),
            "egress": _int(a.get("egress_entry_count")),
        }
    )
    if found is None:
        return []
    return [
        CountSpec(
            node.id, "network_acl_entries", found[1], found[0], quota=QUOTA_NACL_ENTRIES
        )
    ]


def _route_table(node: Node) -> list[CountSpec]:
    a = node.attributes
    out: list[CountSpec] = []
    for measure, key in (
        ("routes_ipv4", "ipv4_route_count"),
        ("routes_ipv6", "ipv6_route_count"),
    ):
        used = _int(a.get(key))
        if used is not None:
            out.append(CountSpec(node.id, measure, used, quota=QUOTA_ROUTES))
    propagated = _int(a.get("propagated_route_count"))
    if propagated is not None:
        out.append(
            CountSpec(
                node.id,
                "routes_propagated",
                propagated,
                limit=FIXED_PROPAGATED_ROUTES,
                limit_source="fixed",
            )
        )
    return out


def _vpc(node: Node, graph: _Graph) -> list[CountSpec]:
    cidrs = node.attributes.get("cidr_blocks")
    endpoints = [
        e
        for e in graph.kids(node.id, "vpc_endpoint")
        if str(e.attributes.get("endpoint_type") or "").casefold()
        in ("interface", "gatewayloadbalancer")
        and alive(e.attributes.get("state"))
    ]
    peerings = {
        e["source"]
        for e in graph.edges
        if e.get("type") == "peering"
        and e.get("target") == node.id
        and str((e.get("attributes") or {}).get("state") or "") == "active"
    }
    out = [
        CountSpec(
            node.id,
            "vpc_subnets",
            len(graph.kids(node.id, "subnet")),
            quota=QUOTA_VPC_SUBNETS,
        ),
        CountSpec(
            node.id,
            "vpc_network_acls",
            len(graph.kids(node.id, "network_acl")),
            quota=QUOTA_VPC_NACLS,
        ),
        CountSpec(
            node.id,
            "vpc_route_tables",
            len(graph.kids(node.id, "route_table")),
            quota=QUOTA_VPC_ROUTE_TABLES,
        ),
        CountSpec(
            node.id,
            "vpc_interface_endpoints",
            len(endpoints),
            quota=QUOTA_VPC_INTERFACE_ENDPOINTS,
        ),
        CountSpec(
            node.id, "vpc_active_peerings", len(peerings), quota=QUOTA_VPC_PEERINGS
        ),
    ]
    if isinstance(cidrs, list):
        out.append(
            CountSpec(node.id, "vpc_ipv4_cidrs", len(cidrs), quota=QUOTA_VPC_IPV4_CIDRS)
        )
    return out


def _tgw(node: Node, graph: _Graph) -> list[CountSpec]:
    mine = [
        a
        for a in graph.of_type("transit_gateway_attachment")
        if a.attributes.get("transit_gateway_id") == node.native_id
        and a.region == node.region
        and alive(a.attributes.get("state"))
    ]
    tables = [
        t
        for t in graph.of_type("transit_gateway_route_table")
        if t.attributes.get("transit_gateway_id") == node.native_id
        and t.region == node.region
    ]
    peering = [
        a
        for a in mine
        if str(a.attributes.get("resource_type") or "") in ("peering", "tgw-peering")
    ]
    destinations: dict[str, set[str]] = {
        t.id: set(t.attributes["blackhole_destinations"])
        for t in tables
        if isinstance(t.attributes.get("blackhole_destinations"), list)
    }
    for e in graph.edges:
        if e.get("type") == "route" and e.get("source") in destinations:
            destinations[e["source"]].update(
                (e.get("attributes") or {}).get("destinations") or []
            )
    out = [
        CountSpec(node.id, "tgw_attachments", len(mine), quota=QUOTA_TGW_ATTACHMENTS),
        CountSpec(
            node.id, "tgw_route_tables", len(tables), quota=QUOTA_TGW_ROUTE_TABLES
        ),
        CountSpec(
            node.id, "tgw_peering_attachments", len(peering), quota=QUOTA_TGW_PEERINGS
        ),
    ]
    if len(destinations) == len(tables):
        out.append(
            CountSpec(
                node.id,
                "tgw_routes",
                sum(len(d) for d in destinations.values()),
                limit=DEFAULT_TGW_ROUTES,
                limit_source="default_quota",
            )
        )
    return out


def _dxgw(node: Node, graph: _Graph) -> list[CountSpec]:
    vifs = [
        v
        for v in graph.of_type("direct_connect_virtual_interface")
        if v.attributes.get("direct_connect_gateway_id") == node.native_id
        and alive(v.attributes.get("state"))
    ]
    out = [
        CountSpec(
            node.id,
            "dxgw_virtual_interfaces",
            len(vifs),
            limit=FIXED_DXGW_VIFS,
            limit_source="fixed",
        )
    ]
    for measure, key, limit in (
        (
            "dxgw_virtual_private_gateways",
            "associated_virtual_private_gateway_ids",
            FIXED_DXGW_VGWS,
        ),
        ("dxgw_transit_gateways", "associated_transit_gateway_ids", FIXED_DXGW_TGWS),
    ):
        ids = node.attributes.get(key)
        if isinstance(
            ids, list
        ):
            out.append(
                CountSpec(node.id, measure, len(ids), limit=limit, limit_source="fixed")
            )
    return out


def _subnet_limit(cidr: Any) -> int | None:
    try:
        prefix = int(str(cidr).split("/", 1)[1])
    except (IndexError, ValueError):
        return None
    usable = 2 ** (32 - prefix) - 5 if 0 <= prefix <= 32 else 0
    return usable if usable > 0 else None


def _subnets(graph: _Graph, snapshot: Snapshot | None) -> list[CountSpec]:
    if snapshot is None:
        return []
    state = {n.id: n for n in snapshot.nodes if n.type == "subnet"}
    failed = {
        (c.account_id, c.region): c.reason
        for c in snapshot.coverage
        if c.resource_type == "subnet" and c.status not in ("ok", "skipped")
    }
    out: list[CountSpec] = []
    for node in graph.of_type("subnet"):
        limit = _subnet_limit(node.attributes.get("cidr_block"))
        if limit is None:
            continue
        here = state.get(node.id)
        free = (
            _int(here.attributes.get("available_ip_address_count"))
            if here is not None
            else None
        )
        if free is None:
            reason = failed.get((node.account_id, node.region))
            if reason is None:
                continue
            out.append(
                CountSpec(
                    node.id,
                    "ip_addresses",
                    None,
                    limit=limit,
                    limit_source="arithmetic",
                    unread=reason,
                )
            )
            continue
        out.append(
            CountSpec(
                node.id,
                "ip_addresses",
                max(limit - free, 0),
                limit=limit,
                limit_source="arithmetic",
            )
        )
    return out


def plan_counts(topology: dict[str, Any], snapshot: Snapshot | None) -> list[CountSpec]:
    graph = _Graph.of(topology)
    out: list[CountSpec] = []
    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        if node.type == "security_group":
            out += _security_group(node)
        elif node.type == "network_acl":
            out += _network_acl(node)
        elif node.type == "route_table":
            out += _route_table(node)
        elif node.type == "vpc":
            out += _vpc(node, graph)
        elif node.type == "transit_gateway":
            out += _tgw(node, graph)
        elif node.type == "direct_connect_gateway":
            out += _dxgw(node, graph)
    return out + _subnets(graph, snapshot)



Points = list[tuple[datetime, float]]


def _parse_at(at: str) -> datetime:
    return datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc)


def _fmt(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stop(end: datetime, step: int) -> datetime:
    """Floor time to a step-second boundary for a completed-window query
    end.
    """
    raw = int(end.timestamp())
    return datetime.fromtimestamp(raw - raw % step, tz=timezone.utc)


def _start(end: datetime, minutes: int, step: int) -> datetime:
    return _stop(end, step) - timedelta(minutes=minutes)


@dataclass(frozen=True, slots=True, order=True)
class _Read:

    minutes: int
    step: int


def _reads_for(minutes: int, min_step: int) -> tuple[_Read, int]:
    step = max(dict(WINDOWS)[minutes], min_step)
    if minutes in SHORT_MINUTES:
        return _Read(1440, step), step
    return _Read(minutes, step), step


@dataclass
class _Fetched:
    points: dict[tuple[Series, _Read], Points] = field(default_factory=dict)
    failed: dict[tuple[Series, _Read], Reason] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _batches(series: list[Series], read: _Read) -> Iterable[list[Series]]:
    per = max(
        1,
        min(
            MAX_QUERIES_PER_CALL,
            MAX_POINTS_PER_CALL // max(1, read.minutes * 60 // read.step),
        ),
    )
    for i in range(0, len(series), per):
        yield series[i : i + per]


def _fetch(
    wanted: dict[_Read, set[Series]], metric_factory: Any, end: datetime
) -> _Fetched:
    out = _Fetched()
    places: dict[tuple[str, str], dict[_Read, list[Series]]] = {}
    for read, series in wanted.items():
        for s in series:
            places.setdefault((s.account_id, s.region), {}).setdefault(read, []).append(
                s
            )
    for (account_id, region), reads in sorted(places.items()):
        try:
            client = metric_factory(account_id, region, "cloudwatch")
        except Exception as exc:  # noqa: BLE001
            # Record credential errors without replacing measurements
            # with zero.
            operation = getattr(exc, "operation_name", None)
            _status, reason = classify(
                exc, f"sts:{operation}" if operation else "nettopo:client_factory"
            )
            for read, series in reads.items():
                for s in series:
                    out.failed[(s, read)] = reason
            continue
        for read, series in sorted(reads.items()):
            for batch in _batches(sorted(series), read):
                _read_batch(client, batch, read, end, out)
    return out


def _read_batch(
    client: Any, batch: list[Series], read: _Read, end: datetime, out: _Fetched
) -> None:
    ids = {f"u{i}": s for i, s in enumerate(batch)}
    collected: dict[str, Points] = {key: [] for key in ids}
    kwargs: dict[str, Any] = {
        "MetricDataQueries": [
            {
                "Id": key,
                "MetricStat": {
                    "Metric": {
                        "Namespace": s.namespace,
                        "MetricName": s.metric,
                        "Dimensions": [
                            {"Name": n, "Value": v} for n, v in s.dimensions
                        ],
                    },
                    "Period": read.step,
                    "Stat": s.stat,
                },
                "ReturnData": True,
            }
            for key, s in ids.items()
        ],
        "StartTime": _start(end, read.minutes, read.step),
        "EndTime": _stop(end, read.step),
        "ScanBy": "TimestampAscending",
    }
    while True:
        try:
            response = client.get_metric_data(**kwargs)
        except Exception as exc:  # noqa: BLE001
            # Distinguish API and SDK errors from an observed zero.
            _status, reason = classify(exc, METRIC_API)
            for s in batch:
                out.failed[(s, read)] = reason
            return
        for result in response.get("MetricDataResults") or []:
            key = str(result.get("Id"))
            if key not in collected:
                continue
            values = list(result.get("Values") or [])
            stamps = list(result.get("Timestamps") or [])
            if len(values) != len(stamps):
                out.failed[(ids[key], read)] = Reason(
                    "api_error", METRIC_API, "값과 시각의 개수가 다르다"
                )
                continue
            for stamp, value in zip(stamps, values, strict=True):
                moment = _stamp(stamp)
                if moment is not None:
                    collected[key].append((moment, float(value)))
        token = response.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    for key, s in ids.items():
        if (s, read) not in out.failed:
            out.points[(s, read)] = sorted(dict(collected[key]).items())




def _cell_points(cell: Cell, read: _Read, fetched: _Fetched) -> Points | Reason:
    merged: dict[datetime, float] = {}
    for s in cell.series:
        reason = fetched.failed.get((s, read))
        if reason is not None:
            return reason
        for moment, value in fetched.points.get((s, read), []):
            merged[moment] = max(merged.get(moment, value), value)
    return sorted(merged.items())


def _round(value: float) -> float:
    return round(value, 3)


def _total(spec: RateSpec, inside: Points, step: int) -> float | None:
    if not inside:
        return None
    if spec.value == VALUE_SUM:
        total = sum(v for _t, v in inside)
        if spec.unit == "bit_per_second":
            total = total * spec.factor / BITS_PER_BYTE
        return _round(total)
    if spec.value == VALUE_LEVEL and spec.unit == "bit_per_second":
        return _round(sum(v for _t, v in inside) * step * spec.factor / BITS_PER_BYTE)
    return None


def _fold(
    spec: RateSpec, points: Points, minutes: int, step: int, end: datetime
) -> dict[str, Any]:
    start = _start(end, minutes, step)
    inside = [(t, v) for t, v in points if start <= t < _stop(end, step)]
    window_seconds = minutes * 60
    row: dict[str, Any] = {"minutes": minutes, "step_seconds": step}
    if not inside and spec.reporting == REPORT_NONZERO and spec.empty_zero:
        row["peak"] = 0.0
        row["average"] = 0.0
        if spec.total_unit is not None:
            row["total"] = 0.0
        row["covered_seconds"] = window_seconds
        row["computed_at"] = _fmt(end)
        return row
    if not inside and spec.reporting == REPORT_NONZERO:
        if spec.kind != "events":
            row["peak"] = None
            row["average"] = None
        row["no_points"] = True
        row["covered_seconds"] = 0
        row["computed_at"] = _fmt(end)
        return row
    if spec.reporting == REPORT_NONZERO:
        covered = window_seconds
    elif inside:
        covered = min(
            window_seconds,
            max(0, int((_stop(end, step) - inside[0][0]).total_seconds())),
        )
    else:
        covered = 0
    if spec.kind == "events":
        row["sum"] = round(sum(v for _t, v in inside))
    else:
        if not inside:
            peak, peak_at, average = None, None, None
        else:
            top_at, top = min(inside, key=lambda p: (-p[1], p[0]))
            if spec.value == VALUE_SUM:
                peak = top * spec.factor / step
                average = (
                    sum(v for _t, v in inside) * spec.factor / covered
                    if covered
                    else None
                )
            else:
                peak = top * spec.factor
                average = sum(v for _t, v in inside) * spec.factor / len(inside)
            peak_at = _fmt(top_at)
        row["peak"] = None if peak is None else _round(peak)
        if peak_at is not None:
            row["peak_at"] = peak_at
        row["average"] = None if average is None else _round(min(average, peak or 0.0))
        if spec.total_unit is not None:
            row["total"] = _total(spec, inside, step)
    row["covered_seconds"] = covered
    row["computed_at"] = _fmt(end)
    return row


def _window(
    spec: RateSpec, minutes: int, fetched: _Fetched, end: datetime
) -> dict[str, Any] | Reason:
    read, step = _reads_for(minutes, spec.min_step)
    rows: list[tuple[Cell, dict[str, Any]]] = []
    for cell in spec.cells:
        points = _cell_points(cell, read, fetched)
        if isinstance(points, Reason):
            return points
        rows.append((cell, _fold(spec, points, minutes, step, end)))
    if spec.kind == "events":
        seen = [r for _c, r in rows if not r.get("no_points")]
        if not seen:
            return rows[0][1]
        return {
            **seen[0],
            "sum": sum(r["sum"] for r in seen),
            "covered_seconds": max(r["covered_seconds"] for r in seen),
        }
    measured = [(c, r) for c, r in rows if r["peak"] is not None]
    if not measured:
        return rows[0][1]
    cell, row = max(measured, key=lambda cr: cr[1]["peak"])
    row = dict(row)
    if spec.total_unit is not None:
        totals = [r["total"] for _c, r in rows if r.get("total") is not None]
        row["total"] = _round(sum(totals)) if totals else None
    if cell.direction is not None:
        row["peak_direction"] = cell.direction
    if cell.az is not None:
        row["peak_az"] = cell.az
    return row


_WINDOW_KEYS = (
    "minutes",
    "step_seconds",
    "peak",
    "peak_at",
    "peak_direction",
    "peak_az",
    "average",
    "total",
    "sum",
    "no_points",
    "covered_seconds",
    "computed_at",
)


def _ordered(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in _WINDOW_KEYS if k in row}




@dataclass(frozen=True, slots=True)
class QuotaValue:
    """Store a limit, its source, and any query failure reason.

    limit: Limit used to compare usage
    source: Applied quota, AWS default, or code default used
    reason: Quota query failure Reason
    """
    limit: float
    source: str
    reason: Reason | None = None


def _quota_number(response: Any) -> float | None:
    value = ((response or {}).get("Quota") or {}).get("Value")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))


def read_quotas(
    keys: set[tuple[str, str, str, str, int]], quota_factory: Any
) -> dict[tuple[str, str, str], QuotaValue]:
    """Query applied quotas and use defaults if absent or queries fail.

    Use the code default if querying the AWS default still yields no
    value.
    Record query failure reasons in QuotaValue.reason.
    """
    out: dict[tuple[str, str, str], QuotaValue] = {}
    by_place: dict[tuple[str, str], list[tuple[str, str, int]]] = {}
    for account_id, region, service, code, default in keys:
        by_place.setdefault((account_id, region), []).append((service, code, default))
    for (account_id, region), wanted in sorted(by_place.items()):
        try:
            client = quota_factory(account_id, region, "service-quotas")
        except Exception as exc:  # noqa: BLE001
            operation = getattr(exc, "operation_name", None)
            _s, reason = classify(
                exc, f"sts:{operation}" if operation else "nettopo:client_factory"
            )
            for _service, code, default in wanted:
                out[(account_id, region, code)] = QuotaValue(
                    float(default), "default_quota", reason
                )
            continue
        for service, code, default in sorted(set(wanted)):
            reason: Reason | None = None
            try:
                applied = _quota_number(
                    client.get_service_quota(ServiceCode=service, QuotaCode=code)
                )
            except Exception as exc:  # noqa: BLE001
                applied = None
                if _error_code(exc) != "NoSuchResourceException":
                    _s, reason = classify(exc, QUOTA_API)
            if applied is not None:
                out[(account_id, region, code)] = QuotaValue(applied, "account_quota")
                continue
            try:
                fallback = _quota_number(
                    client.get_aws_default_service_quota(
                        ServiceCode=service, QuotaCode=code
                    )
                )
            except Exception as exc:  # noqa: BLE001
                fallback = None
                if reason is None:
                    _s, reason = classify(exc, DEFAULT_QUOTA_API)
            out[(account_id, region, code)] = QuotaValue(
                fallback or float(default), "default_quota", reason
            )
    return out




def _previous_index(
    previous: dict[str, Any] | None,
) -> tuple[
    dict[tuple[str, str], list[dict[str, Any]]],
    dict[tuple[str, str, str], QuotaValue],
    datetime | None,
]:
    long_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    quotas: dict[tuple[str, str, str], QuotaValue] = {}
    newest: datetime | None = None
    for item in (previous or {}).get("utilization") or []:
        target = item.get("target_id")
        try:
            _type, account_id, region, _native = split_node_id(str(target))
        except ValueError:
            continue
        for m in item.get("measures") or []:
            rows = [
                w for w in m.get("windows") or [] if w.get("minutes") in LONG_MINUTES
            ]
            if rows:
                long_rows[(target, m.get("measure"))] = rows
                for w in rows:
                    try:
                        moment = _parse_at(str(w["computed_at"]))
                    except (KeyError, ValueError):
                        continue
                    newest = moment if newest is None or moment > newest else newest
            code = m.get("quota_code")
            if (
                code
                and m.get("limit_source") in ("account_quota", "default_quota")
                and m.get("limit")
            ):
                raw = m.get("limit_reason")
                reason = (
                    Reason(
                        raw.get("code", "api_error"), raw.get("api"), raw.get("message")
                    )
                    if raw
                    else None
                )
                quotas[(account_id, str(region), code)] = QuotaValue(
                    float(m["limit"]), m["limit_source"], reason
                )
    return long_rows, quotas, newest


def long_is_due(previous: dict[str, Any] | None, at: str) -> bool:
    """Return whether long-window measurements and quotas need a
    refresh.

    Return True if no previous measurement time exists or an hour has
    passed.
    """
    _rows, _quotas, newest = _previous_index(previous)
    return (
        newest is None or (_parse_at(at) - newest).total_seconds() >= LONG_EVERY_SECONDS
    )




@dataclass(frozen=True, slots=True)
class UtilizationResult:
    """Store usage and utilization results for the observation document.

    items: Measurements by target and calculation results by time window
    long_refreshed: Whether long-window results were refreshed this time
    warnings: Metric query warnings
    """
    items: list[dict[str, Any]]
    long_refreshed: bool
    warnings: tuple[str, ...] = ()


def _measure_doc(
    measure: str,
    kind: str,
    unit: str,
    limit: float | None,
    source: str | None,
    *,
    quota_code: str | None = None,
    limit_reason: Reason | None = None,
    limit_scope: str | None = None,
    part: str | None = None,
    total_unit: str | None = None,
    includes: str | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "measure": measure,
        "kind": kind,
        "unit": unit,
        "limit": None
        if limit is None
        else (int(limit) if float(limit).is_integer() else limit),
        "limit_source": source if limit is not None else None,
    }
    if quota_code and source in ("account_quota", "default_quota"):
        doc["quota_code"] = quota_code
    if limit_reason is not None and source == "default_quota":
        doc["limit_reason"] = limit_reason.to_dict()
    if limit_scope and limit is not None:
        doc["limit_scope"] = limit_scope
    if part:
        doc["part"] = part
    if total_unit:
        doc["total_unit"] = total_unit
    if includes:
        doc["includes"] = includes
    return doc


def compute_utilization(
    topology: dict[str, Any],
    snapshot: Snapshot | None,
    *,
    metric_factory: Any,
    quota_factory: Any,
    at: str,
    previous: dict[str, Any] | None = None,
) -> UtilizationResult:
    """Query metrics and quotas and return a UtilizationResult.

    topology: Topology JSON for resource configuration and limit
        calculations
    snapshot: Current usage data (for example, available subnet IP
        addresses)
    metric_factory: Function creating CloudWatch clients by account and
        region
    quota_factory: Function creating Service Quotas clients by
        account/region
    at: End time of the metric query interval
    previous: Previous observation JSON for reusing long-window results

    Reuse previous long-window results until refresh is due.
    """
    end = _parse_at(at)
    prev_long, prev_quotas, _newest = _previous_index(previous)
    due = long_is_due(previous, at)
    rates = plan_rates(topology)
    counts = plan_counts(topology, snapshot)

    carry = {
        (s.target_id, s.measure): prev_long[(s.target_id, s.measure)]
        for s in rates
        if not due and (s.target_id, s.measure) in prev_long
    }
    wanted: dict[_Read, set[Series]] = {}
    for spec in rates:
        for minutes, _step in WINDOWS:
            if minutes in LONG_MINUTES and (spec.target_id, spec.measure) in carry:
                continue
            read, _ = _reads_for(minutes, spec.min_step)
            for cell in spec.cells:
                wanted.setdefault(read, set()).update(cell.series)
    fetched = _fetch(wanted, metric_factory, end) if wanted else _Fetched()

    quota_keys: set[tuple[str, str, str, str, int]] = set()
    for spec in counts:
        if spec.quota is not None:
            _type, account_id, region, _native = split_node_id(spec.target_id)
            service, code, default = spec.quota
            if due or (account_id, str(region), code) not in prev_quotas:
                quota_keys.add((account_id, str(region), service, code, default))
    quotas = dict(prev_quotas) if not due else {}
    if quota_keys:
        quotas.update(read_quotas(quota_keys, quota_factory))

    measures: dict[str, list[dict[str, Any]]] = {}
    for spec in rates:
        doc = _measure_doc(
            spec.measure,
            spec.kind,
            spec.unit,
            spec.limit,
            spec.limit_source,
            limit_scope=spec.limit_scope,
            total_unit=spec.total_unit,
            includes=spec.includes,
        )
        windows: list[dict[str, Any]] = []
        unread: Reason | None = None
        for minutes, _step in WINDOWS:
            if minutes in LONG_MINUTES and (spec.target_id, spec.measure) in carry:
                windows += [
                    w
                    for w in carry[(spec.target_id, spec.measure)]
                    if w.get("minutes") == minutes
                ]
                continue
            row = _window(spec, minutes, fetched, end)
            if isinstance(row, Reason):
                unread = row
                break
            windows.append(_ordered(row))
        if unread is not None:
            doc["unread"] = unread.to_dict()
        else:
            doc["windows"] = windows
        measures.setdefault(spec.target_id, []).append(doc)

    for spec in counts:
        limit, source, code, reason = spec.limit, spec.limit_source, None, None
        if spec.quota is not None:
            _type, account_id, region, _native = split_node_id(spec.target_id)
            value = quotas.get((account_id, str(region), spec.quota[1]))
            if value is not None:
                limit, source, code, reason = (
                    value.limit,
                    value.source,
                    spec.quota[1],
                    value.reason,
                )
        doc = _measure_doc(
            spec.measure,
            "count",
            "count",
            limit,
            source,
            quota_code=code,
            limit_reason=reason,
            limit_scope=spec.limit_scope,
            part=spec.part,
        )
        if spec.unread is not None:
            doc["unread"] = spec.unread.to_dict()
        else:
            doc["now"] = {"used": int(spec.used or 0)}
        measures.setdefault(spec.target_id, []).append(doc)

    items = [
        {"target_id": target, "measures": sorted(ms, key=lambda m: m["measure"])}
        for target, ms in sorted(measures.items())
    ]
    return UtilizationResult(
        items=items, long_refreshed=due, warnings=tuple(fetched.warnings)
    )
