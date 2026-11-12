"""Query configuration APIs to check log sources for observations.

Do not read log content. Record unknown and a reason if configuration
cannot be checked. Store log query settings in SourceScan.flow_logs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .model import Scope, Snapshot
from .services.base import ClientFactory, classify, pages

VPC_FLOW_LOG = "vpc_flow_log"
TGW_FLOW_LOG = "tgw_flow_log"
LOG_DELIVERY = "log_delivery"
CONTAINER_INSIGHTS = "container_insights"
EKS_NETWORK_POLICY_LOG = "eks_network_policy_log"
EC2_DETAILED_MONITORING = "ec2_detailed_monitoring"
VPN_TUNNEL_BGP_LOG = "vpn_tunnel_bgp_log"
RESOLVER_TARGET_NS_METRICS = "resolver_target_ns_metrics"

KINDS: tuple[str, ...] = (
    CONTAINER_INSIGHTS,
    EC2_DETAILED_MONITORING,
    EKS_NETWORK_POLICY_LOG,
    LOG_DELIVERY,
    RESOLVER_TARGET_NS_METRICS,
    TGW_FLOW_LOG,
    VPC_FLOW_LOG,
    VPN_TUNNEL_BGP_LOG,
)

UNIT: dict[str, str] = {
    CONTAINER_INSIGHTS: "ecs_cluster",
    EC2_DETAILED_MONITORING: "ec2_instance",
    EKS_NETWORK_POLICY_LOG: "eks_cluster",
    RESOLVER_TARGET_NS_METRICS: "resolver_endpoint",
    TGW_FLOW_LOG: "transit_gateway",
    VPC_FLOW_LOG: "vpc",
    VPN_TUNNEL_BGP_LOG: "vpn_connection",
}

_NOT_READ_BY_KIND: dict[str, str] = {
    TGW_FLOW_LOG: (
        "흐름 로그 읽기를 켜지 않았다 — 켜면 TGW 어태치먼트 선의 흐름을 이 로그의 왕복 기록으로 확인한다"
        "(스택 파라미터 FlowLogReading · 로컬은 --flow-logs)"
    ),
    VPC_FLOW_LOG: (
        "흐름 로그 읽기를 켜지 않았다 — 켜면 인터넷·피어링 선의 흐름을 이 로그의 왕복 기록으로 확인한다"
        "(스택 파라미터 FlowLogReading · 로컬은 --flow-logs)"
    ),
}
_NOT_READ = "이 버전은 이 출처를 탐지만 한다 — 읽기는 다음 단계(ADR 0009 2차)"

V2_FIELDS: tuple[str, ...] = (
    "version",
    "account-id",
    "interface-id",
    "srcaddr",
    "dstaddr",
    "srcport",
    "dstport",
    "protocol",
    "packets",
    "bytes",
    "start",
    "end",
    "action",
    "log-status",
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "interface-id",
    "srcaddr",
    "dstaddr",
    "srcport",
    "dstport",
    "protocol",
    "start",
    "action",
    "log-status",
    "traffic-path",
)

TGW_REQUIRED_FIELDS: tuple[str, ...] = (
    "tgw-attachment-id",
    "tgw-pair-attachment-id",
    "srcaddr",
    "dstaddr",
    "srcport",
    "dstport",
    "protocol",
    "start",
    "log-status",
)

_LEVELS: tuple[tuple[str, str], ...] = (
    ("tgw-attach-", "transit_gateway_attachment"),
    ("tgw-", "transit_gateway"),
    ("subnet-", "subnet"),
    ("eni-", "network_interface"),
    ("vpc-", "vpc"),
)
VPC_LEVELS = frozenset({"network_interface", "subnet", "vpc"})

NETWORK_LOG_TYPES = frozenset(
    {"ALB_ACCESS_LOGS", "EVENT_LOGS", "FLOW_LOGS", "NLB_ACCESS_LOGS", "WAF_LOGS"}
)


@dataclass(frozen=True, slots=True)
class Source:

    """Store a log source's configuration state and observation use.

    kind: Log source type
    state: Configuration state (on, off, unknown)
    used: Whether logs were used for observation
    unit: Unit used to count configuration targets
    enabled_ids: Number of targets with logging enabled
    total_ids: Total targets whose configuration is compared
    detail: Additional configuration details
    why: Reason configuration or log use could not be determined
    """
    kind: str
    state: str
    used: bool = False
    unit: str | None = None
    enabled_ids: int | None = None
    total_ids: int | None = None
    detail: str | None = None
    why: str | None = None

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "kind": self.kind,
            "state": self.state,
            "used": self.used,
        }
        if self.unit is not None and self.total_ids is not None:
            doc["scope"] = {
                "unit": self.unit,
                "enabled_ids": self.enabled_ids or 0,
                "total_ids": self.total_ids,
            }
        if self.detail is not None:
            doc["detail"] = self.detail
        why = self.why
        if why is None and self.state == "on" and not self.used:
            why = _NOT_READ_BY_KIND.get(self.kind, _NOT_READ)
        if why is not None:
            doc["why"] = why
        return doc


@dataclass(frozen=True, slots=True)
class FlowLog:
    """Store flow log settings used by flowlogs.py to query logs.

    id: Flow log ID
    level: Target level (VPC, subnet, ENI, or TGW)
    target: Log target resource ID
    vpc_id: VPC ID containing the log target
    traffic_type: Traffic type to record
    destination_type: Log destination type
    destination: CloudWatch Logs group or S3 location
    fields: Log field names in record order
    file_format: S3 log file format
    hive: Whether S3 paths use Hive-compatible prefixes
    per_hour: Whether S3 paths use hourly partitions
    interval: Maximum log aggregation interval in seconds
    delivered: Whether log delivery succeeded
    account_id: Account where flow log settings were queried
    region: Region where flow log settings were queried
    """

    id: str
    level: str
    target: str
    vpc_id: str | None = None
    traffic_type: str | None = None
    destination_type: str = ""
    destination: str | None = field(default=None, repr=False)
    fields: tuple[
        str, ...
    ] = ()
    file_format: str | None = None
    hive: bool = False
    per_hour: bool = False
    interval: int | None = None
    delivered: bool = True
    account_id: str = ""
    region: str = ""


@dataclass(frozen=True, slots=True)
class SourceScan:
    """Store log source checks and flow log query settings.

    scanned: Whether log source configuration was queried
    found: Configuration state by log source type
    flow_logs: Flow log settings for reading actual records
    """

    scanned: bool
    found: tuple[Source, ...] = ()
    flow_logs: tuple[FlowLog, ...] = ()

    @classmethod
    def not_scanned(cls) -> SourceScan:
        return cls(scanned=False)

    def to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in sorted(self.found, key=lambda s: s.kind)]




@dataclass(frozen=True, slots=True)
class _Population:

    vpc: int = 0
    transit_gateway: int = 0
    subnet_vpc: Mapping[str, str] = field(default_factory=dict)


def _population(
    snapshot: Snapshot,
) -> tuple[dict[tuple[str, str], _Population], dict[str, dict[str, str]]]:
    counts: dict[tuple[str, str], dict[str, int]] = {}
    subnets: dict[str, dict[str, str]] = {}
    for node in snapshot.nodes:
        if node.region is None:
            continue
        if node.type == "subnet":
            parent = node.parent_id or ""
            if node.native_id and parent.startswith("vpc/"):
                subnets.setdefault(node.region, {})[node.native_id] = parent.rsplit(
                    "/", 1
                )[-1]
            continue
        if node.type not in ("vpc", "transit_gateway"):
            continue
        cell = counts.setdefault((node.account_id, node.region), {})
        cell[node.type] = cell.get(node.type, 0) + 1
    places = {
        place: _Population(
            vpc=c.get("vpc", 0),
            transit_gateway=c.get("transit_gateway", 0),
            subnet_vpc=subnets.get(place[1], {}),
        )
        for place, c in counts.items()
    }
    return places, subnets




@dataclass(frozen=True, slots=True)
class _Cell:
    enabled: int
    total: int
    detail: str | None = None
    trust_total: bool = True
    undetermined: str | None = None
    finer_on: int = 0
    flow_logs: tuple[FlowLog, ...] = ()


_Scan = Callable[[Any, _Population], dict[str, _Cell]]


def flow_log_fields(log_format: str | None) -> tuple[str, ...]:
    """Return log_format's ${...} field names in declared order.

    Return an empty tuple if no format is specified.
    """
    if not log_format:
        return ()
    return tuple(
        token.strip("${} ") for token in log_format.split() if token.startswith("${")
    )


def is_default_format(fields: tuple[str, ...]) -> bool:
    """Return whether all supplied fields belong to the default
    V2_FIELDS.

    Ignore field order. A subset of the default fields also returns
    True.
    """
    return frozenset(fields) <= frozenset(V2_FIELDS)


def missing_fields(
    fields: tuple[str, ...], required: tuple[str, ...] = REQUIRED_FIELDS
) -> tuple[str, ...]:
    have = frozenset(fields)
    return tuple(f for f in required if f not in have)


def is_parquet(log: dict[str, Any]) -> bool:
    options = log.get("DestinationOptions") or {}
    return (log.get("LogDestinationType") or "").lower() == "s3" and str(
        options.get("FileFormat") or ""
    ).lower() == "parquet"


def flow_log_level(resource_id: str) -> str | None:
    for prefix, level in _LEVELS:
        if resource_id.startswith(prefix):
            return level
    return None


def _config(log: dict[str, Any], level: str, vpc_id: str | None) -> FlowLog:
    options = log.get("DestinationOptions") or {}
    destination_type = (log.get("LogDestinationType") or "").lower()
    interval = log.get("MaxAggregationInterval")
    return FlowLog(
        id=str(log.get("FlowLogId") or ""),
        level=level,
        target=str(log.get("ResourceId") or ""),
        vpc_id=vpc_id,
        traffic_type=(
            str(log["TrafficType"]).upper() if log.get("TrafficType") else None
        ),
        destination_type=destination_type,
        destination=(
            log.get("LogGroupName") or log.get("LogDestination")
            if destination_type == "cloud-watch-logs"
            else log.get("LogDestination")
        ),
        fields=flow_log_fields(log.get("LogFormat")),
        file_format=(
            str(options["FileFormat"]).lower() if options.get("FileFormat") else None
        ),
        hive=bool(options.get("HiveCompatiblePartitions", False)),
        per_hour=bool(options.get("PerHourPartition", False)),
        interval=int(interval) if interval is not None else None,
        delivered=(log.get("DeliverLogsStatus") or "SUCCESS").upper() == "SUCCESS",
    )


def _flow_logs(client: Any, pop: _Population) -> dict[str, _Cell]:
    logs = pages(client, "describe_flow_logs", "FlowLogs")
    vpc_ids: set[str] = set()
    tgw_ids: set[str] = set()
    attachment_ids: set[str] = set()
    subnet_logs = 0
    orphan_subnets: set[str] = set()
    eni_ids: set[str] = set()
    default_format = 0
    fields_short = 0
    undelivered = {VPC_FLOW_LOG: 0, TGW_FLOW_LOG: 0}
    configs: dict[str, list[FlowLog]] = {VPC_FLOW_LOG: [], TGW_FLOW_LOG: []}
    for log in logs:
        if (log.get("FlowLogStatus") or "").upper() != "ACTIVE" or is_parquet(log):
            continue
        resource_id = log.get("ResourceId") or ""
        level = flow_log_level(resource_id)
        if level is None:
            continue
        kind = VPC_FLOW_LOG if level in VPC_LEVELS else TGW_FLOW_LOG
        vpc_id: str | None = None
        if level == "vpc":
            vpc_id = resource_id
            vpc_ids.add(resource_id)
        elif level == "subnet":
            subnet_logs += 1
            vpc_id = pop.subnet_vpc.get(resource_id)
            if vpc_id is None:
                orphan_subnets.add(resource_id)
            else:
                vpc_ids.add(vpc_id)
        elif level == "network_interface":
            eni_ids.add(resource_id)
        elif level == "transit_gateway_attachment":
            attachment_ids.add(resource_id)
        else:
            tgw_ids.add(resource_id)
        config = _config(log, level, vpc_id)
        if kind == VPC_FLOW_LOG:
            if is_default_format(config.fields):
                default_format += 1
            elif missing_fields(config.fields):
                fields_short += 1
        if not config.delivered:
            undelivered[kind] += 1
        configs[kind].append(config)
    notes: list[str] = []
    if default_format:
        notes.append(
            f"logFormat v2 기본 {default_format}건 — 구간을 특정하는 필드가 없다"
        )
    if fields_short:
        notes.append(
            f"필수 필드가 빠진 사용자 형식 {fields_short}건 — 그 로그로는 구간을 증명할 수 없다"
        )
    if subnet_logs:
        notes.append(f"서브넷 단위 {subnet_logs}건 — 그 서브넷만 덮는다")
    if orphan_subnets:
        notes.append(
            f"구조에 없는 서브넷의 로그 {len(orphan_subnets)}건 — 어느 VPC 인지 모른다"
        )
    if eni_ids:
        notes.append(
            f"ENI 단위 {len(eni_ids)}건 — VPC 전체를 덮지 않아 VPC 수에 세지 않는다"
        )
    if undelivered[VPC_FLOW_LOG]:
        notes.append(
            f"전달 실패 {undelivered[VPC_FLOW_LOG]}건 — 레코드 부재가 트래픽 부재가 아니다"
        )
    tgw_notes: list[str] = []
    if tgw_ids or attachment_ids:
        tgw_notes.append(
            "기본 포맷이 v2~v6 전부 — 양끝(tgw-pair-attachment-id)이 옵션 없이 들어온다"
        )
    if attachment_ids:
        tgw_notes.append(
            f"어태치먼트 단위 {len(attachment_ids)}건 — 어느 TGW 의 것인지는 이 응답에 없다"
        )
    if undelivered[TGW_FLOW_LOG]:
        tgw_notes.append(
            f"전달 실패 {undelivered[TGW_FLOW_LOG]}건 — 레코드 부재가 트래픽 부재가 아니다"
        )
    return {
        VPC_FLOW_LOG: _Cell(
            len(vpc_ids),
            pop.vpc,
            "; ".join(notes) or None,
            trust_total=not orphan_subnets,
            finer_on=len(orphan_subnets) + len(eni_ids),
            flow_logs=tuple(configs[VPC_FLOW_LOG]),
        ),
        TGW_FLOW_LOG: _Cell(
            len(tgw_ids) + len(attachment_ids),
            pop.transit_gateway,
            "; ".join(tgw_notes) or None,
            trust_total=not attachment_ids,
            flow_logs=tuple(configs[TGW_FLOW_LOG]),
        ),
    }


def _delivery_sources(client: Any, _pop: _Population) -> dict[str, _Cell]:
    found = pages(client, "describe_delivery_sources", "deliverySources")
    matched = sorted(
        {
            (src.get("logType") or "").upper()
            for src in found
            if (src.get("logType") or "").upper() in NETWORK_LOG_TYPES
        }
    )
    return {
        LOG_DELIVERY: _Cell(len(matched), 0, ", ".join(matched) if matched else None)
    }


def _ecs_clusters(client: Any, _pop: _Population) -> dict[str, _Cell]:
    arns = pages(client, "list_clusters", "clusterArns")
    if not arns:
        return {CONTAINER_INSIGHTS: _Cell(0, 0)}
    enabled = 0
    for start in range(0, len(arns), 100):
        described = client.describe_clusters(clusters=arns[start : start + 100])
        for cluster in described.get("clusters", []):
            for setting in cluster.get("settings", []):
                if (setting.get("name") or "") == "containerInsights" and (
                    setting.get("value") or ""
                ).lower() in ("enabled", "enhanced"):
                    enabled += 1
                    break
    return {CONTAINER_INSIGHTS: _Cell(enabled, len(arns))}


def _eks_network_policy(client: Any, _pop: _Population) -> dict[str, _Cell]:
    """Check enableNetworkPolicy in the EKS vpc-cni add-on
    configuration.

    Record the cluster's setting as unknown if add-on configuration
    cannot be
    read.
    """
    names = pages(client, "list_clusters", "clusters")
    if not names:
        return {EKS_NETWORK_POLICY_LOG: _Cell(0, 0)}
    enabled = 0
    unmanaged = 0
    for name in names:
        try:
            addon = client.describe_addon(clusterName=name, addonName="vpc-cni")
        except Exception:  # noqa: BLE001
            # The add-on may be installed through Helm without a managed
            # add-on.
            unmanaged += 1
            continue
        raw = (addon.get("addon") or {}).get("configurationValues") or "{}"
        try:
            config = json.loads(raw)
        except ValueError:
            unmanaged += 1
            continue
        if config.get("enableNetworkPolicy") in (True, "true"):
            enabled += 1
    detail = (
        f"{unmanaged}개 클러스터는 vpc-cni add-on 밖(Helm 설치 가능) — 꺼짐이 아니라 모름"
        if unmanaged
        else None
    )
    return {
        EKS_NETWORK_POLICY_LOG: _Cell(
            enabled,
            len(names),
            detail,
            undetermined=(
                "vpc-cni add-on 을 읽을 수 없는 클러스터가 있다(Helm 설치 가능) — "
                "켜짐·꺼짐을 판정할 수 없다"
                if unmanaged
                else None
            ),
        )
    }


def _instances(client: Any, _pop: _Population) -> dict[str, _Cell]:
    reservations = pages(client, "describe_instances", "Reservations")
    total = 0
    enabled = 0
    for reservation in reservations:
        for instance in reservation.get("Instances", []):
            if (instance.get("State") or {}).get("Name") == "terminated":
                continue
            total += 1
            if (instance.get("Monitoring") or {}).get("State") == "enabled":
                enabled += 1
    return {EC2_DETAILED_MONITORING: _Cell(enabled, total)}


def _vpn_connections(client: Any, _pop: _Population) -> dict[str, _Cell]:
    connections = pages(client, "describe_vpn_connections", "VpnConnections")
    total = 0
    enabled = 0
    with_bgp = 0
    for connection in connections:
        if (connection.get("State") or "") in ("deleted", "deleting"):
            continue
        total += 1
        tunnels = (connection.get("Options") or {}).get("TunnelOptions") or []
        options = [
            (tunnel.get("LogOptions") or {}).get("CloudWatchLogOptions") or {}
            for tunnel in tunnels
        ]
        if any(o.get("LogEnabled") for o in options):
            enabled += 1
        if any(o.get("BgpLogEnabled") for o in options):
            with_bgp += 1
    detail = (
        f"BGP 로그는 {with_bgp}/{total}개 연결에서만 켜져 있다 — 구간의 양끝은 BGP 쪽에만 있다"
        if total
        else None
    )
    return {VPN_TUNNEL_BGP_LOG: _Cell(enabled, total, detail)}


def _resolver_endpoints(client: Any, _pop: _Population) -> dict[str, _Cell]:
    endpoints = pages(client, "list_resolver_endpoints", "ResolverEndpoints")
    live = [e for e in endpoints if (e.get("Status") or "") != "DELETING"]
    enabled = sum(1 for e in live if e.get("TargetNameServerMetricsEnabled"))
    return {RESOLVER_TARGET_NS_METRICS: _Cell(enabled, len(live))}


PROBES: tuple[tuple[str, str, _Scan], ...] = (
    ("ec2", "ec2:DescribeFlowLogs", _flow_logs),
    ("ec2", "ec2:DescribeInstances", _instances),
    ("ec2", "ec2:DescribeVpnConnections", _vpn_connections),
    ("ecs", "ecs:DescribeClusters", _ecs_clusters),
    ("eks", "eks:DescribeAddon", _eks_network_policy),
    ("logs", "logs:DescribeDeliverySources", _delivery_sources),
    ("route53resolver", "route53resolver:ListResolverEndpoints", _resolver_endpoints),
)

REQUIRED_ACTIONS: tuple[str, ...] = (
    "ec2:DescribeFlowLogs",
    "ec2:DescribeInstances",
    "ec2:DescribeVpnConnections",
    "ecs:DescribeClusters",
    "ecs:ListClusters",
    "eks:DescribeAddon",
    "eks:ListClusters",
    "logs:DescribeDeliverySources",
    "route53resolver:ListResolverEndpoints",
)

_FILLS: dict[str, tuple[str, ...]] = {
    "_flow_logs": (VPC_FLOW_LOG, TGW_FLOW_LOG),
    "_instances": (EC2_DETAILED_MONITORING,),
    "_vpn_connections": (VPN_TUNNEL_BGP_LOG,),
    "_ecs_clusters": (CONTAINER_INSIGHTS,),
    "_eks_network_policy": (EKS_NETWORK_POLICY_LOG,),
    "_delivery_sources": (LOG_DELIVERY,),
    "_resolver_endpoints": (RESOLVER_TARGET_NS_METRICS,),
}


def detect_sources(
    scope: Scope, client_factory: ClientFactory, *, snapshot: Snapshot
) -> SourceScan:
    """Query log configuration by account and region and return source
    states.

    scope: Accounts and regions whose configuration to query
    client_factory: Function creating clients by account, region, and
        service
    snapshot: Collected resources used for target counts and
        configuration

    Do not read log content. Keep query failures distinct from disabled
    logs.
    Store settings needed to read flow logs in SourceScan.flow_logs.
    """
    population, subnets = _population(snapshot)
    enabled: dict[str, int] = {}
    finer: dict[str, int] = {}
    flow_logs: list[FlowLog] = []
    total: dict[str, int] = {}
    distrusted: set[str] = set()
    notes: dict[str, list[str]] = {}
    failures: dict[str, tuple[str, str]] = {}
    failed_cells: dict[str, int] = {}
    undetermined: dict[str, str] = {}

    for account_id in sorted(scope.account_ids):
        for region in sorted(scope.regions):
            pop = population.get(
                (account_id, region), _Population(subnet_vpc=subnets.get(region, {}))
            )
            for service, api, scan in PROBES:
                kinds = _FILLS[scan.__name__]
                try:
                    client = client_factory(account_id, region, service)
                    result = scan(client, pop)
                except Exception as exc:  # noqa: BLE001
                    # Leave the source state unknown if the query fails.
                    _, reason = classify(exc, api)
                    for kind in kinds:
                        failed_cells[kind] = failed_cells.get(kind, 0) + 1
                        failures.setdefault(kind, (api, reason.message))
                    continue
                for kind, cell in result.items():
                    enabled[kind] = enabled.get(kind, 0) + cell.enabled
                    finer[kind] = finer.get(kind, 0) + cell.finer_on
                    total[kind] = total.get(kind, 0) + cell.total
                    flow_logs.extend(
                        replace(log, account_id=account_id, region=region)
                        for log in cell.flow_logs
                    )
                    if not cell.trust_total:
                        distrusted.add(kind)
                    if cell.undetermined is not None:
                        undetermined.setdefault(kind, cell.undetermined)
                    if cell.detail:
                        notes.setdefault(kind, []).append(cell.detail)

    found: list[Source] = []
    for kind in KINDS:
        unit = UNIT.get(kind)
        on_count = enabled.get(kind, 0)
        seen = total.get(kind, 0)
        detail = "; ".join(dict.fromkeys(notes.get(kind, []))) or None
        if on_count > 0 or finer.get(kind, 0) > 0:
            state, why = "on", None
        elif kind in failures:
            api, message = failures[kind]
            state = "unknown"
            why = f"{api}: {message}"
        elif kind in undetermined:
            state, why = "unknown", undetermined[kind]
        else:
            state, why = "off", None
        if state == "unknown" and failed_cells.get(kind, 0) > 1:
            detail = "; ".join(
                filter(None, [detail, f"{failed_cells[kind]}개 자리에서 탐지 실패"])
            )
        show_scope = (
            unit is not None
            and state != "unknown"
            and kind not in distrusted
            and on_count <= seen
            and not (state == "on" and on_count == 0)
        )
        if unit is not None and not show_scope and state == "on" and on_count > 0:
            detail = "; ".join(
                filter(
                    None,
                    [
                        detail,
                        f"켜진 자원 {on_count}개 — 분모(구조 스냅샷의 자원 수 {seen})를 "
                        "믿을 수 없어 범위를 적지 않는다",
                    ],
                )
            )
        found.append(
            Source(
                kind=kind,
                state=state,
                used=False,
                unit=unit if show_scope else None,
                enabled_ids=on_count,
                total_ids=seen if show_scope else None,
                detail=detail,
                why=why,
            )
        )
    return SourceScan(
        scanned=True,
        found=tuple(found),
        flow_logs=tuple(
            sorted(flow_logs, key=lambda log: (log.account_id, log.region, log.id))
        ),
    )
