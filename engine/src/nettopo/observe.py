"""Build observations for resources and relationships in the topology.

Apply resource-specific rules to collected current state. Record reasons
for unknown states without treating them as healthy. Add metric and flow
log evidence without modifying the input topology.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .flowlogs import FlowLogReading, LogResult, Proof
from .metrics import (
    METRIC_API,
    METRIC_PENDING_TYPES,
    WINDOW_SECONDS,
    MetricLayer,
    Reading,
    Verdict,
    lands_on_another_tgw,
)
from .model import GLOBAL, Reason, Snapshot
from .services.base import TARGET_HEALTH_DETAIL
from .sources import TGW_FLOW_LOG, VPC_FLOW_LOG, SourceScan

SCHEMA_VERSION = "1.18.0"

DEFAULT_RESOLUTION_SECONDS = 300
DEFAULT_RETENTION_SECONDS = 604800
DEFAULT_FLAP_COLLAPSE_THRESHOLD = 4
DEFAULT_MAX_CHANGES = 5000


@dataclass(frozen=True, slots=True)
class _Rule:

    api: str
    up: frozenset[str]
    down: frozenset[str]
    deleting: frozenset[str]
    attribute: str = "state"


def _rule(
    api: str,
    *,
    up: tuple[str, ...],
    down: tuple[str, ...] = (),
    deleting: tuple[str, ...],
    attribute: str = "state",
) -> _Rule:
    rule = _Rule(
        api=api,
        up=frozenset(s.casefold() for s in up),
        down=frozenset(s.casefold() for s in down),
        deleting=frozenset(s.casefold() for s in deleting),
        attribute=attribute,
    )
    if (
        (rule.up & rule.down)
        or (rule.up & rule.deleting)
        or (rule.down & rule.deleting)
    ):
        raise ValueError(f"{api}: up·down·deleting 이 겹친다")
    return rule


NODE_RULES: dict[str, _Rule] = {
    "vpc": _rule(
        "ec2:DescribeVpcs", up=("available",), down=("pending",), deleting=()
    ),
    "subnet": _rule(
        "ec2:DescribeSubnets",
        up=("available",),
        down=("pending", "unavailable", "failed", "failed-insufficient-capacity"),
        deleting=(),
    ),
    "internet_gateway": _rule(
        "ec2:DescribeInternetGateways",
        up=("attached",),
        down=("attaching", "detaching", "detached"),
        deleting=(),
    ),
    "nat_gateway": _rule(
        "ec2:DescribeNatGateways",
        up=("available",),
        down=("pending", "failed"),
        deleting=("deleting", "deleted"),
    ),
    "transit_gateway": _rule(
        "ec2:DescribeTransitGateways",
        up=("available", "modifying"),
        down=("pending",),
        deleting=("deleting", "deleted"),
    ),
    "transit_gateway_attachment": _rule(
        "ec2:DescribeTransitGatewayAttachments",
        up=("available", "modifying"),
        down=(
            "initiating",
            "initiatingrequest",
            "pendingacceptance",
            "pending",
            "rollingback",
            "failed",
            "failing",
            "rejected",
            "rejecting",
        ),
        deleting=("deleting", "deleted"),
    ),
    "vpc_peering_connection": _rule(
        "ec2:DescribeVpcPeeringConnections",
        up=("active",),
        down=(
            "initiating-request",
            "pending-acceptance",
            "provisioning",
            "expired",
            "rejected",
            "failed",
        ),
        deleting=("deleting", "deleted"),
    ),
    "vpc_endpoint": _rule(
        "ec2:DescribeVpcEndpoints",
        up=("available",),
        down=(
            "pendingacceptance",
            "pending",
            "partial",
            "failed",
            "rejected",
            "expired",
        ),
        deleting=("deleting", "deleted"),
    ),
    "load_balancer": _rule(
        "elasticloadbalancing:DescribeLoadBalancers",
        up=("active", "active_impaired"),
        down=("provisioning", "failed"),
        deleting=(),
    ),
    "rds_db_instance": _rule(
        "rds:DescribeDBInstances",
        up=(
            "available",
            "backing-up",
            "modifying",
            "configuring-enhanced-monitoring",
            "configuring-iam-database-auth",
            "configuring-log-exports",
            "configuring-activity-stream",
            "converting-to-vpc",
            "moving-to-vpc",
            "renaming",
            "resetting-master-credentials",
            "storage-optimization",
            "storage-initialization",
            "storage-config-upgrade",
            "delete-precheck",
        ),
        down=(
            "creating",
            "starting",
            "rebooting",
            "stopping",
            "stopped",
            "failed",
            "upgrading",
            "maintenance",
            "storage-full",
            "restore-error",
            "insufficient-capacity",
            "incompatible-create",
            "incompatible-network",
            "incompatible-option-group",
            "incompatible-parameters",
            "incompatible-restore",
            "inaccessible-encryption-credentials",
            "inaccessible-encryption-credentials-recoverable",
        ),
        deleting=("deleting",),
    ),
    "ecs_service": _rule(
        "ecs:DescribeServices",
        up=("active", "active-under-desired"),
        down=("active-no-running-tasks", "active-desired-zero"),
        deleting=("draining", "inactive"),
    ),
    "sagemaker_notebook_instance": _rule(
        "sagemaker:ListNotebookInstances",
        up=("InService",),
        down=("Pending", "Stopping", "Stopped", "Failed", "Updating"),
        deleting=("Deleting",),
    ),
    "sagemaker_domain": _rule(
        "sagemaker:ListDomains",
        up=("InService", "Updating"),
        down=("Pending", "Failed", "Delete_Failed", "Update_Failed"),
        deleting=("Deleting",),
    ),
    "sagemaker_endpoint": _rule(
        "sagemaker:ListEndpoints",
        up=("InService", "Updating", "SystemUpdating"),
        down=(
            "Creating",
            "OutOfService",
            "RollingBack",
            "UpdateRollbackFailed",
            "Failed",
        ),
        deleting=("Deleting",),
    ),
    "ec2_instance": _rule(
        "ec2:DescribeInstances",
        up=("running",),
        down=("pending", "stopping", "stopped"),
        deleting=("shutting-down", "terminated"),
    ),
    "eks_cluster": _rule(
        "eks:DescribeCluster",
        up=("ACTIVE", "UPDATING"),
        down=("CREATING", "FAILED", "PENDING"),
        deleting=("DELETING",),
    ),
    "network_firewall": _rule(
        "network-firewall:DescribeFirewall",
        up=("READY",),
        down=("PROVISIONING",),
        deleting=("DELETING",),
    ),
    "ec2_instance_connect_endpoint": _rule(
        "ec2:DescribeInstanceConnectEndpoints",
        up=("create-complete", "update-complete"),
        down=(
            "create-in-progress",
            "create-failed",
            "update-in-progress",
            "update-failed",
            "delete-failed",
        ),
        deleting=("delete-in-progress", "delete-complete"),
    ),
    "vpn_gateway": _rule(
        "ec2:DescribeVpnGateways",
        up=("available",),
        down=("pending",),
        deleting=("deleting", "deleted"),
    ),
    "customer_gateway": _rule(
        "ec2:DescribeCustomerGateways",
        up=("available",),
        down=("pending",),
        deleting=("deleting", "deleted"),
    ),
    "vpn_connection": _rule(
        "ec2:DescribeVpnConnections",
        up=("available", "available-one-tunnel-down"),
        down=("available-all-tunnels-down", "pending"),
        deleting=("deleting", "deleted"),
    ),
    "direct_connect_connection": _rule(
        "directconnect:DescribeConnections",
        up=("available",),
        down=(
            "ordering",
            "requested",
            "pending",
            "down",
            "rejected",
        ),
        deleting=("deleting", "deleted"),
    ),
    "direct_connect_virtual_interface": _rule(
        "directconnect:DescribeVirtualInterfaces",
        up=("available", "available-some-bgp-down"),
        down=(
            "available-all-bgp-down",
            "confirming",
            "verifying",
            "pending",
            "down",
            "testing",
            "rejected",
        ),
        deleting=("deleting", "deleted"),
    ),
    "direct_connect_gateway": _rule(
        "directconnect:DescribeDirectConnectGateways",
        up=("available",),
        down=("pending",),
        deleting=("deleting", "deleted"),
    ),
    "elasticache_cluster": _rule(
        "elasticache:DescribeCacheClusters",
        up=("available", "modifying", "snapshotting"),
        down=(
            "creating",
            "incompatible-network",
            "rebooting cluster nodes",
            "restore-failed",
        ),
        deleting=("deleting", "deleted"),
    ),
    "rds_db_cluster": _rule(
        "rds:DescribeDBClusters",
        up=(
            "available",
            "backing-up",
            "modifying",
            "renaming",
            "resetting-master-credentials",
            "update-iam-db-auth",
            "storage-optimization",
            "backtracking",
        ),
        down=(
            "creating",
            "failing-over",
            "inaccessible-encryption-credentials",
            "inaccessible-encryption-credentials-recoverable",
            "maintenance",
            "migrating",
            "migration-failed",
            "promoting",
            "starting",
            "stopped",
            "stopping",
            "upgrading",
            "cloning-failed",
            "preparing-data-migration",
        ),
        deleting=("deleting",),
    ),
    "opensearch_domain": _rule(
        "es:DescribeDomains",
        up=("Active", "Modifying", "UpgradingEngineVersion", "UpdatingServiceSoftware"),
        down=("Creating", "Isolated"),
        deleting=("Deleting",),
    ),
    "msk_cluster": _rule(
        "kafka:ListClustersV2",
        up=("ACTIVE", "UPDATING", "MAINTENANCE"),
        down=("CREATING", "FAILED", "HEALING", "REBOOTING_BROKER"),
        deleting=("DELETING",),
    ),
}

_FOLDED_WHY: dict[str, str] = {
    "active_impaired": "로드 밸런서의 일부 AZ 가 손상됐습니다(active_impaired) — 나머지 AZ 로 요청을 받습니다",
    "active-no-running-tasks": "서비스는 ACTIVE 이지만 실행 중인 태스크가 0개입니다(원하는 수 > 0)",
    "active-desired-zero": "원하는 태스크 수가 0입니다 — 서비스가 멈춰 있습니다",
    "available-one-tunnel-down": "VPN 연결은 available 이지만 터널 일부가 DOWN 입니다 — 나머지 터널로 연결됩니다",
    "available-all-tunnels-down": "VPN 연결은 available 이지만 터널이 전부 DOWN 입니다 — 온프레미스로 연결되지 않습니다",
    "available-some-bgp-down": "VIF 는 available 이지만 BGP 피어 일부가 down 입니다 — 나머지 피어로 연결됩니다",
    "available-all-bgp-down": "VIF 는 available 이지만 BGP 피어가 전부 down 입니다 — 이 VIF 로는 온프레미스 경로가 없습니다",
}

EDGE_RULES: dict[str, _Rule] = {
    "association": _rule(
        "ec2:DescribeRouteTables",
        up=("associated",),
        down=("failed", "disassociated"),
        deleting=(),
    ),
}

ROUTE_API = "ec2:DescribeRouteTables"
TGW_ROUTE_API = "ec2:SearchTransitGatewayRoutes"

ASSOCIATION_EVIDENCE: dict[str, bool] = {"route_table": True, "network_acl": False}
NO_MEANS_NACL_ASSOCIATION = (
    "네트워크 ACL 과 서브넷의 연결에는 AWS 가 상태를 제공하지 않습니다 — 이 연결의 상태는 판정하지 않습니다"
)

NO_MEANS_NODES: dict[str, str] = {
    "security_group": (
        "보안 그룹은 규칙의 묶음이며 AWS 가 「상태」를 제공하지 않습니다 — 트래픽을 막고 있는지는 "
        "규칙과 트래픽을 함께 봐야 알 수 있고, 이를 관측할 수단이 없습니다"
    ),
    "network_acl": (
        "네트워크 ACL 은 규칙의 묶음이며 AWS 가 「상태」를 제공하지 않습니다 — 트래픽을 막고 있는지는 "
        "규칙과 트래픽을 함께 봐야 알 수 있고, 이를 관측할 수단이 없습니다"
    ),
    "auto_scaling_group": (
        "Auto Scaling 그룹은 인스턴스의 논리적 묶음입니다 — 트래픽은 그룹 안의 인스턴스가 처리하며, "
        "그룹의 인스턴스 수로는 트래픽 흐름을 판단할 수 없습니다"
    ),
}
NO_MEANS_EDGES: dict[str, str] = {
    "security_group_reference": (
        "보안 그룹 참조는 규칙이 만든 관계입니다 — AWS 가 이 관계의 상태를 보고하지 않습니다"
    ),
}

NO_STATE_NODES = frozenset(
    {
        "account",
        "region",
        "route_table",
        "transit_gateway_route_table",
        "cloudfront",
        "lambda_function",
        "external_ip",
        "route53_hosted_zone",
        "s3_bucket",
        "elastic_ip",
        "api_gateway",
        "rds_db_proxy",
        "global_accelerator",
        "core_network",
        "lattice_service_network",
        "lattice_service",
        "waf_web_acl",
    }
)

PRESENCE_ONLY_NODES: dict[str, tuple[str, ...]] = {
    "cloudfront": ("cloudfront",),
    "s3_bucket": ("cloudfront",),
    "route53_hosted_zone": ("route53_hosted_zone",),
    "lambda_function": ("lambda_function",),
    "elastic_ip": ("elastic_ip",),
    "api_gateway": ("api_gateway", "api_gateway_v2"),
    "rds_db_proxy": ("rds_db_proxy",),
    "core_network": ("core_network",),
    "lattice_service_network": ("lattice_service_network",),
    "lattice_service": ("lattice_service",),
    "global_accelerator": ("global_accelerator",),
    "waf_web_acl": ("waf_web_acl", "waf_web_acl_global"),
}
NO_STATE_EDGES: frozenset[str] = frozenset(
    {"origin", "endpoint_provider", "dns_alias", "zone_association"}
    | {"eip_association", "vpc_link", "proxy_target", "accelerator_endpoint"}
    | {
        "connect_transport",
        "core_network_attachment",
        "lattice_vpc_association",
        "lattice_service_association",
        "lattice_target",
    }
    | {"waf_protection"}
)

FLOW_LOG_ONLY_EDGES = frozenset({"peering"})

FLOW_LOG_TARGETS = ("internet_gateway/", "vpc_peering_connection/")
FLOW_LOG_METRIC = "flow_log"
FLOW_LOG_NONE_READ = "켜진 흐름 로그를 하나도 읽지 못했다 — 이유는 로그마다 적었다"

METRIC_ONLY_EDGES = frozenset(
    {"attachment", "endpoint_connection", "load_balancer_placement"}
)

EDGE_SOURCE_RESOURCE: dict[str, str] = {
    "security_group_reference": "security_group",
    "endpoint_connection": "vpc_endpoint",
    "load_balancer_placement": "load_balancer",
    "peering": "vpc_peering_connection",
    "load_balancer_target": "load_balancer",
}

EDGE_RESOURCE_FROM_SOURCE = frozenset({"association", "attachment", "route"})

OBSERVED_RESOURCE_TYPES: tuple[str, ...] = (
    "vpc",
    "subnet",
    "route_table",
    "internet_gateway",
    "nat_gateway",
    "transit_gateway",
    "transit_gateway_attachment",
    "vpc_peering_connection",
    "vpc_endpoint",
    "load_balancer",
    "transit_gateway_route_table",
    "rds_db_instance",
    "ecs_service",
    "sagemaker_notebook_instance",
    "sagemaker_domain",
    "sagemaker_endpoint",
    "ec2_instance_connect_endpoint",
    "ec2_instance",
    "eks_cluster",
    "network_firewall",
    "vpn_gateway",
    "customer_gateway",
    "vpn_connection",
    "direct_connect_connection",
    "direct_connect_virtual_interface",
    "direct_connect_gateway",
    "elasticache_cluster",
    "rds_db_cluster",
    "opensearch_domain",
    "msk_cluster",
)

_COVERAGE_VOCAB = frozenset(
    {"ok", "denied", "throttled", "unsupported", "error", "skipped"}
)


@dataclass(frozen=True, slots=True)
class Observation:
    """Store target state, traffic evidence, and reasons for unknown
    details.

    target_id: Observed Node.id or Edge.id
    target_kind: Target kind (node or edge)
    status: Verdict based on target state and traffic evidence
    why: Reason for the verdict or for an unknown state
    evidence: APIs, metrics, observation times, and other verdict
        evidence
    error_count: Observed error count
    partial_reasons: Reasons for incomplete information
    drops: Drop or error metric aggregate
    by_subnet: State details by subnet
    targets: Load balancer target counts by health state
    bgp: BGP peer counts by state
    tunnels: VPN tunnel state details
    deleting: Whether the resource is being deleted

    partial is True when partial_reasons is nonempty.
    """

    target_id: str
    target_kind: str
    status: str
    why: str | None
    evidence: dict[str, Any] | None
    error_count: int | None
    partial_reasons: tuple[Reason, ...] = ()
    drops: int | None = None
    by_subnet: tuple[dict[str, Any], ...] = ()
    targets: dict[str, int] | None = None
    bgp: dict[str, int] | None = None
    tunnels: tuple[dict[str, Any], ...] = ()
    deleting: bool = False

    @property
    def partial(self) -> bool:
        return bool(self.partial_reasons)

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "status": self.status,
            "why": self.why,
            "evidence": self.evidence,
            "error_count": self.error_count,
            "partial": self.partial,
        }
        if self.partial_reasons:
            doc["partial_reasons"] = [r.to_dict() for r in self.partial_reasons]
        if self.drops is not None:
            doc["drops"] = self.drops
        if self.by_subnet:
            doc["by_subnet"] = [dict(item) for item in self.by_subnet]
        if self.targets is not None:
            doc["targets"] = dict(self.targets)
        if self.bgp is not None:
            doc["bgp"] = dict(self.bgp)
        if self.tunnels:
            doc["tunnels"] = [dict(t) for t in self.tunnels]
        if self.deleting:
            doc["deleting"] = True
        return doc

    def with_metric(
        self,
        *,
        status: str | None = None,
        api: str | None = None,
        metric: str | None = None,
        datapoints: int | None = None,
        window_seconds: int | None = None,
        observed_on: str | None = None,
        first_flow_at: str | None = None,
        last_flow_at: str | None = None,
        why: str | None = None,
        error_count: int | None = None,
        reason: Reason | None = None,
        drops: int | None = None,
    ) -> Observation:
        """Return a new Observation with metric evidence and the
        requested state.

        Copy existing evidence before applying API and metric details.
        Add reason
        to partial_reasons when present.
        """
        evidence = dict(self.evidence) if self.evidence else {}
        if api is not None:
            evidence["api"] = api
        if metric is not None:
            evidence["metric"] = metric
            evidence["datapoints"] = datapoints
            evidence["window_seconds"] = window_seconds
        if observed_on is not None and observed_on != self.target_id:
            evidence["observed_on"] = observed_on
        if first_flow_at is not None:
            evidence["first_flow_at"] = first_flow_at
        if last_flow_at is not None:
            evidence["last_flow_at"] = last_flow_at
        return replace(
            self,
            status=status or self.status,
            why=why if why is not None else self.why,
            evidence=evidence or None,
            error_count=error_count if error_count is not None else self.error_count,
            partial_reasons=self.partial_reasons + ((reason,) if reason else ()),
            drops=drops if drops is not None else self.drops,
        )


TARGET_HEALTH_API = "elasticloadbalancing:DescribeTargetHealth"


def _target_detail(attrs: dict[str, Any]) -> dict[str, int]:
    got = {obs: attrs.get(attr) for attr, obs in TARGET_HEALTH_DETAIL}
    if not all(isinstance(v, int) for v in got.values()):
        return {}
    return {k: int(v) for k, v in got.items()}


def _with_targets(judged: Observation, node: Any) -> Observation:
    attrs = node.attributes or {}
    reasons = tuple(r for r in node.partial_reasons or () if r.api == TARGET_HEALTH_API)
    total = attrs.get("target_count")
    if total is None:
        if not reasons:
            return judged
        return replace(judged, partial_reasons=judged.partial_reasons + reasons)
    healthy = int(attrs.get("healthy_target_count") or 0)
    unhealthy = int(attrs.get("unhealthy_target_count") or 0)
    targets = {
        "total": int(total),
        "healthy": healthy,
        "unhealthy": unhealthy,
        **_target_detail(attrs),
    }
    if judged.status == "up" and total > 0 and healthy == 0 and unhealthy > 0:
        return replace(
            judged,
            status="down",
            why=f"대상 {int(total)}개 중 정상 0 — AWS 가 비정상 {unhealthy}개로 보고",
            evidence=_evidence(TARGET_HEALTH_API),
            targets=targets,
        )
    return replace(judged, targets=targets)


def _observe_lb_target(
    edge_id: str, attributes: dict[str, Any], source: Any
) -> Observation:
    reasons = tuple(
        r
        for r in (getattr(source, "partial_reasons", None) or ())
        if r.api == TARGET_HEALTH_API
    )
    total = attributes.get("target_count")
    healthy = attributes.get("healthy_target_count")
    unhealthy = attributes.get("unhealthy_target_count")
    if reasons or not isinstance(total, int) or healthy is None or unhealthy is None:
        return Observation(
            edge_id, "edge", "up", None, _evidence(TARGET_HEALTH_API), 0, reasons
        )
    targets = {
        "total": int(total),
        "healthy": int(healthy),
        "unhealthy": int(unhealthy),
        **_target_detail(attributes),
    }
    if total > 0 and healthy == 0 and unhealthy > 0:
        return replace(
            Observation(
                edge_id,
                "edge",
                "down",
                f"이 선의 대상 {int(total)}개 중 정상 0 — AWS 가 비정상 {int(unhealthy)}개로 보고",
                _evidence(TARGET_HEALTH_API),
                1,
            ),
            targets=targets,
        )
    return replace(
        Observation(edge_id, "edge", "up", None, _evidence(TARGET_HEALTH_API), 0),
        targets=targets,
    )


def _with_bgp(judged: Observation, node: Any) -> Observation:
    attrs = node.attributes or {}
    total = attrs.get("bgp_peer_count")
    if total is None or not str(attrs.get("state") or "").startswith("available"):
        return judged
    return replace(
        judged, bgp={"total": int(total), "up": int(attrs.get("bgp_up_count") or 0)}
    )


def _with_tunnels(judged: Observation, node: Any) -> Observation:
    rows = (node.attributes or {}).get("tunnel_status")
    if not isinstance(rows, list) or not rows:
        return judged
    tunnels = []
    for row in rows:
        ip, status, last, message, accepted = (str(row).split("\t") + [""] * 5)[:5]
        t: dict[str, Any] = {"outside_ip": ip, "status": status}
        if last:
            t["last_status_change"] = last
        if message:
            t["status_message"] = message
        if accepted.isdigit():
            t["accepted_route_count"] = int(accepted)
        tunnels.append(t)
    return replace(judged, tunnels=tuple(tunnels))


_NO_RULE_CODE = "api_error"


def _evidence(
    api: str,
    *,
    metric: str | None = None,
    datapoints: int | None = None,
    window_seconds: int | None = None,
) -> dict[str, Any]:
    return {
        "api": api,
        "metric": metric,
        "datapoints": datapoints,
        "window_seconds": window_seconds,
    }


def _association_evidence(source_type: str | None) -> bool | None:
    return ASSOCIATION_EVIDENCE.get(source_type or "")


def _judge(rule: _Rule, value: Any, target_id: str, kind: str) -> Observation:
    raw = value
    value = value.casefold() if isinstance(value, str) else value
    if value in rule.up:
        return Observation(
            target_id, kind, "up", _FOLDED_WHY.get(value), _evidence(rule.api), 0
        )
    if value in rule.deleting:
        return Observation(
            target_id,
            kind,
            "none",
            f"삭제 중 — AWS 가 보고한 {rule.attribute} = {raw!r}입니다. 삭제 중인 리소스는 이상으로 판정하지 않습니다"
            "(삭제가 끝나면 이력에 「없어짐」으로 남습니다)",
            _evidence(rule.api),
            0,
            deleting=True,
        )
    if value in rule.down:
        return Observation(
            target_id,
            kind,
            "down",
            _FOLDED_WHY.get(value, f"AWS 가 보고한 {rule.attribute} = {raw!r}"),
            _evidence(rule.api),
            1,
        )
    why = (
        f"AWS 가 보고한 {rule.attribute} = {raw!r} 를 판정할 규칙이 ADR 0006 §O7 에 없습니다 "
        "— 표에 값을 더해야 합니다(개발)"
    )
    return Observation(
        target_id,
        kind,
        "none",
        why,
        _evidence(rule.api),
        None,
        (Reason(code=_NO_RULE_CODE, api=rule.api, message=why),),
    )


def _observe_route(
    edge_id: str, attributes: dict[str, Any], source_type: str | None = None
) -> Observation | None:
    """Evaluate a route relationship using its blackhole attribute.

    Treat TGW routes as up. For other routes with unknown blackhole
    state,
    return none with a reason.
    """
    if source_type == "transit_gateway_route_table":
        return Observation(edge_id, "edge", "up", None, _evidence(TGW_ROUTE_API), 0)
    if "blackhole" not in attributes:
        why = "이 경로에 blackhole 값이 없습니다 — 구조 수집기가 그 필드를 담지 않았습니다(개발)"
        return Observation(
            edge_id,
            "edge",
            "none",
            why,
            _evidence(ROUTE_API),
            None,
            (Reason(code=_NO_RULE_CODE, api=ROUTE_API, message=why),),
        )
    if attributes["blackhole"]:
        dead = attributes.get("blackhole_destinations")
        if isinstance(dead, list) and dead:
            return Observation(
                edge_id,
                "edge",
                "down",
                f"경로가 blackhole 입니다 — 대상이 사라졌는데 경로만 남았습니다. 이 선의 목적지 중 "
                f"[{', '.join(str(d) for d in dead)}] 가 blackhole 입니다",
                _evidence(ROUTE_API),
                1,
            )
        destinations = attributes.get("destinations") or []
        listed = (
            ", ".join(str(d) for d in destinations) if destinations else "목적지 미기록"
        )
        return Observation(
            edge_id,
            "edge",
            "down",
            f"경로가 blackhole 입니다 — 대상이 사라졌는데 경로만 남았습니다. 이 선의 목적지 "
            f"[{listed}] 중 하나 이상이 blackhole 이며, 목적지별 상태를 수집하지 않아 "
            "어느 목적지인지는 알 수 없습니다",
            _evidence(ROUTE_API),
            1,
        )
    return Observation(edge_id, "edge", "up", None, _evidence(ROUTE_API), 0)


def _no_means(target_id: str, kind: str, why: str) -> Observation:
    return Observation(target_id, kind, "none", why, None, None)


def _no_rule(target_id: str, kind: str, type_name: str) -> Observation:
    why = (
        f"{kind} 타입 {type_name!r} 에 1층 판정 규칙이 없습니다 — ADR 0006 §O7 에 행을 "
        "먼저 더해야 합니다(개발)"
    )
    return Observation(
        target_id,
        kind,
        "none",
        why,
        None,
        None,
        (Reason(code=_NO_RULE_CODE, api=None, message=why),),
    )


def _describe_by_type(
    snapshot: Snapshot,
) -> dict[tuple[str, str, str], tuple[str, Any]]:
    out: dict[tuple[str, str, str], tuple[str, Any]] = {}
    for entry in snapshot.coverage:
        if entry.region is None:
            continue
        status = entry.status if entry.status in _COVERAGE_VOCAB else "error"
        out[(entry.account_id, entry.region, entry.resource_type)] = (
            status,
            entry.reason.to_dict() if entry.reason else None,
        )
    return out


def is_global_node_id(target_id: str) -> bool:
    parts = target_id.split("/")
    return "|" not in target_id and len(parts) > 3 and parts[2] == GLOBAL


def global_home(
    describe: dict[tuple[str, str, str], tuple[str, Any]],
    account_id: str,
    resource_type: str,
) -> tuple[str, str] | None:
    """Return an (account ID, region) with a successful global resource
    query.

    describe: Query status and results by (account ID, region, resource
        type)
    account_id: Resource account ID
    resource_type: Resource type

    Choose the first successful region in name order, or return None.
    """
    regions = sorted(
        region
        for (account, region, kind), (status, _reason) in describe.items()
        if account == account_id and kind == resource_type and status == "ok"
    )
    return (account_id, regions[0]) if regions else None


_MISSING_RESULT = (
    "2층 응답에 이 대상의 결과가 없습니다 — 쿼리 id 대응이 깨졌습니다(개발)"
)


def _metric_answer(
    layer: MetricLayer, target_id: str, cell: tuple[str, str, str]
) -> tuple[Verdict | None, Reason | None, bool]:
    unbuildable = layer.unbuildable.get(target_id)
    if unbuildable is not None:
        return None, Reason(code="api_error", api=METRIC_API, message=unbuildable), True
    if target_id not in layer.planned:
        return None, None, False
    failed = layer.failures.get(cell)
    if failed is not None:
        return None, failed[1], False
    verdict = layer.verdict(target_id)
    if verdict is None:
        return (
            None,
            Reason(code="api_error", api=METRIC_API, message=_MISSING_RESULT),
            True,
        )
    if target_id in layer.incomplete:
        return (
            verdict,
            Reason(code="api_error", api=METRIC_API, message=_MISSING_RESULT),
            True,
        )
    return verdict, None, False


def _report_reading(
    layer: MetricLayer, target_id: str, verdict: Verdict
) -> Reading | None:
    metric = verdict.report_metric
    if metric is None:
        return None
    return layer.readings_for(target_id).get(metric)


def _report_points(reading: Reading | None) -> int | None:
    if reading is None or not reading.measured:
        return None
    return reading.nonzero


def _quiet_why(reading: Reading | None) -> str | None:
    if reading is None:
        return None
    if reading.silence_is_expected:
        return (
            f"{reading.metric}: 값이 0 이면 발행되지 않는 지표입니다 — "
            f"창 {WINDOW_SECONDS}초 안에 데이터포인트가 없었고, "
            "「흐름이 없었다」와 「관측하지 못했다」를 구별할 수 없습니다"
        )
    return (
        f"{reading.metric}: 값 0 도 발행하는 지표인데 창 {WINDOW_SECONDS}초 안에 "
        "데이터포인트가 없었습니다 — 차원 조합 또는 발행 시작을 확인해야 합니다"
    )


def _with_verdict(
    obs: Observation, layer: MetricLayer, verdict: Verdict
) -> Observation:
    metric = verdict.report_metric
    window = layer.window_seconds
    observed_on = layer.observed_on.get(obs.target_id)
    reading = _report_reading(layer, obs.target_id, verdict)
    points = _report_points(reading)
    drops = verdict.drops
    if not verdict.measured:
        return obs.with_metric(
            metric=metric, window_seconds=window, why=_quiet_why(reading), drops=drops
        )
    if verdict.flowing:
        return obs.with_metric(
            status="live",
            api=METRIC_API,
            metric=metric,
            datapoints=points,
            window_seconds=window,
            observed_on=observed_on,
            first_flow_at=reading.first_nonzero_at if reading else None,
            last_flow_at=reading.last_nonzero_at if reading else None,
            drops=drops,
        )
    if verdict.blocked:
        return obs.with_metric(
            status="down",
            api=METRIC_API,
            metric=metric,
            datapoints=points,
            window_seconds=window,
            why=verdict.block_reason,
            error_count=1,
            observed_on=observed_on,
            drops=drops,
        )
    if verdict.arrival_unproven:
        return obs.with_metric(
            metric=metric,
            window_seconds=window,
            why=verdict.arrival_why,
            observed_on=observed_on,
            drops=drops,
        )
    return obs.with_metric(
        metric=metric,
        datapoints=points,
        window_seconds=window,
        why=_quiet_why(reading) if points is None else None,
        observed_on=observed_on,
        drops=drops,
    )


def _apply_metric(
    obs: Observation, cell: tuple[str, str, str], layer: MetricLayer
) -> Observation:
    """Apply metric query results and traffic verdicts to an up
    observation.
    """
    if not layer.enabled or obs.status != "up":
        return obs
    verdict, reason, _defect = _metric_answer(layer, obs.target_id, cell)
    if verdict is None:
        return obs if reason is None else obs.with_metric(reason=reason)
    row = _with_verdict(obs, layer, verdict)
    return row if reason is None else row.with_metric(reason=reason)


def _metric_only_edge(
    edge_id: str, cell: tuple[str, str, str], layer: MetricLayer
) -> Observation | None:
    if not layer.enabled:
        return None
    verdict, reason, defect = _metric_answer(layer, edge_id, cell)
    if verdict is None:
        if not defect or reason is None:
            return None
        return Observation(
            edge_id, "edge", "none", reason.message, None, None, (reason,)
        )
    if not verdict.measured:
        return None
    row = _with_verdict(
        Observation(edge_id, "edge", "up", None, _evidence(METRIC_API), 0),
        layer,
        verdict,
    )
    return row if reason is None else row.with_metric(reason=reason)


def _tables_in_use(topology: dict[str, Any]) -> dict[str, str]:
    nodes = {n["id"]: n for n in topology["nodes"]}
    explicit: dict[str, set[str]] = {}
    for edge in topology["edges"]:
        if (
            edge["type"] == "association"
            and nodes.get(edge["source"], {}).get("type") == "route_table"
            and nodes.get(edge["target"], {}).get("type") == "subnet"
        ):
            explicit.setdefault(edge["target"], set()).add(edge["source"])
    mains: dict[str, list[str]] = {}
    for node in nodes.values():
        if (
            node["type"] == "route_table"
            and (node.get("attributes") or {}).get("main") is True
        ):
            mains.setdefault(node.get("parent_id") or "", []).append(node["id"])
    tables: dict[str, str] = {}
    for node in nodes.values():
        if node["type"] != "subnet":
            continue
        candidates = explicit.get(node["id"])
        if candidates is None:
            candidates = set(mains.get(node.get("parent_id") or "", ()))
        if len(candidates) == 1:
            tables[node["id"]] = next(iter(candidates))
    return tables


def _flow_live(row: Observation, backing: list[Proof], window: int) -> Observation:
    first = min(backing, key=lambda p: (p.observed_on, p.api))
    return row.with_metric(
        status="live",
        api=first.api,
        metric=FLOW_LOG_METRIC,
        datapoints=sum(p.pairs for p in backing),
        window_seconds=window,
        observed_on=first.observed_on,
    )


def _flow_log_rows(
    observations: list[Observation],
    reading: FlowLogReading,
    resources: tuple[LogResult, ...],
    topology: dict[str, Any],
    readable: Any,
) -> tuple[list[Observation], list[str]]:
    window = reading.window_seconds
    by_id = {obs.target_id: obs for obs in observations}
    edges = {e["id"]: e for e in topology["edges"]}
    nodes = {n["id"]: n for n in topology["nodes"]}
    tables = _tables_in_use(topology)
    dropped: list[str] = []

    written: dict[str, list[Proof]] = {}
    vpc_proofs: list[Proof] = []
    for proof in reading.proofs:
        if proof.route_id is None:
            vpc_proofs.append(proof)
            continue
        edge = edges.get(proof.route_id)
        if edge is None or edge["target"] != proof.target_id:
            dropped.append(proof.route_id)
            continue
        row = by_id.get(proof.route_id)
        if (
            row is None
            or row.status != "up"
            or not proof.target_id.startswith(FLOW_LOG_TARGETS)
        ):
            continue
        if tables.get(proof.observed_on) != edge["source"]:
            dropped.append(f"{proof.route_id}#{proof.observed_on}")
            continue
        written.setdefault(proof.route_id, []).append(proof)
    backing: dict[str, list[Proof]] = {}
    for route_id in sorted(written):
        proofs = sorted(written[route_id], key=lambda p: p.observed_on)
        peering = proofs[0].target_id.startswith("vpc_peering_connection/")
        items = []
        for proof in proofs:
            evidence: dict[str, Any] = {
                "api": proof.api,
                "metric": FLOW_LOG_METRIC,
                "datapoints": proof.pairs,
                "window_seconds": window,
            }
            if peering:
                evidence["observed_on"] = proof.observed_on
            items.append(
                {"subnet_id": proof.observed_on, "status": "live", "evidence": evidence}
            )
            backing.setdefault(proof.target_id, []).append(proof)
        by_id[route_id] = replace(by_id[route_id], by_subnet=tuple(items))

    quiet: dict[str, list[Proof]] = {}
    for proof in reading.silences:
        edge = edges.get(proof.route_id or "")
        row = by_id.get(proof.route_id or "")
        if (
            edge is None
            or edge["target"] != proof.target_id
            or row is None
            or row.status != "up"
            or not proof.target_id.startswith(FLOW_LOG_TARGETS)
            or tables.get(proof.observed_on) != edge["source"]
            or any(i["subnet_id"] == proof.observed_on for i in row.by_subnet)
        ):
            continue
        quiet.setdefault(str(proof.route_id), []).append(proof)
    for route_id in sorted(quiet):
        peering = quiet[route_id][0].target_id.startswith("vpc_peering_connection/")
        added = []
        for proof in quiet[route_id]:
            evidence = {
                "api": proof.api,
                "metric": FLOW_LOG_METRIC,
                "datapoints": 0,
                "window_seconds": window,
            }
            if peering:
                evidence["observed_on"] = proof.observed_on
            added.append(
                {"subnet_id": proof.observed_on, "status": "up", "evidence": evidence}
            )
        row = by_id[route_id]
        by_id[route_id] = replace(
            row,
            by_subnet=tuple(
                sorted((*row.by_subnet, *added), key=lambda i: i["subnet_id"])
            ),
        )

    for target_id in sorted(backing):
        row = by_id.get(target_id)
        if (
            target_id.startswith("internet_gateway/")
            and row is not None
            and row.status == "up"
        ):
            by_id[target_id] = _flow_live(row, backing[target_id], window)

    sides: dict[str, set[str]] = {}
    for edge in topology["edges"]:
        if edge["type"] == "peering":
            sides.setdefault(edge["source"], set()).add(edge["target"])
    unattributable = {
        r.attached_to
        for r in resources
        if r.kind == VPC_FLOW_LOG
        and r.used
        and r.why_code == "subnet_unattributable"
        and r.attached_to is not None
    }
    for proof in vpc_proofs:
        pcx, vpc = proof.target_id, proof.observed_on
        if (
            not pcx.startswith("vpc_peering_connection/")
            or vpc not in sides.get(pcx, set())
            or vpc not in unattributable
        ):
            dropped.append(f"{pcx}#{vpc}")
            continue
        subnets = [
            n["id"]
            for n in nodes.values()
            if n["type"] == "subnet" and n.get("parent_id") == vpc
        ]
        if not subnets or any(s not in tables for s in subnets):
            dropped.append(f"{pcx}#{vpc}")
            continue
        route_ids = sorted({f"route|{tables[s]}|{pcx}" for s in subnets})
        if any(r not in edges for r in route_ids):
            dropped.append(f"{pcx}#{vpc}")
            continue
        if all(by_id.get(r) is not None and by_id[r].status == "up" for r in route_ids):
            backing.setdefault(pcx, []).append(proof)
    for edge in sorted(topology["edges"], key=lambda e: e["id"]):
        if edge["type"] not in FLOW_LOG_ONLY_EDGES:
            continue
        proofs = backing.get(edge["source"])
        pcx_row = by_id.get(edge["source"])
        if (
            not proofs
            or pcx_row is None
            or pcx_row.status != "up"
            or not readable(edge["id"])
        ):
            continue
        seed = Observation(edge["id"], "edge", "up", None, {}, 0)
        by_id[edge["id"]] = _flow_live(seed, proofs, window)

    crossed: dict[str, list[Any]] = {}
    for crossing in reading.crossings:
        for attachment in crossing.attachments:
            slot = crossed.setdefault(attachment, [0, crossing.api])
            slot[0] += crossing.pairs
            slot[1] = min(slot[1], crossing.api)
    for edge in sorted(topology["edges"], key=lambda e: e["id"]):
        if edge["type"] != "attachment" or edge["source"] not in crossed:
            continue
        row = by_id.get(edge["id"])
        if row is None or row.status != "up":
            continue
        pairs, api = crossed[edge["source"]]
        evidence = {
            "api": api,
            "metric": FLOW_LOG_METRIC,
            "datapoints": pairs,
            "window_seconds": window,
        }
        if not edge["target"].startswith("transit_gateway/"):
            evidence["observed_on"] = edge["source"]
        by_id[edge["id"]] = replace(row, status="live", why=None, evidence=evidence)

    return list(by_id.values()), dropped


def _merge_flow_log_sources(
    rows: list[dict[str, Any]], resources: tuple[LogResult, ...]
) -> list[dict[str, Any]]:
    by_kind: dict[str, list[LogResult]] = {}
    for result in resources:
        by_kind.setdefault(result.kind, []).append(result)
    unknown = set(by_kind) - {VPC_FLOW_LOG, TGW_FLOW_LOG}
    if unknown:
        raise ValueError(f"흐름 로그 줄의 종류를 모른다: {sorted(unknown)}")
    out = []
    for row in rows:
        listed = by_kind.pop(row["kind"], None)
        if listed is None:
            out.append(row)
            continue
        if row["state"] != "on":
            raise ValueError(
                f"{row['kind']}: 로그 줄이 있는데 출처가 켜짐이 아니다({row['state']}) — 탐지와 판독이 갈라졌다"
            )
        merged = dict(row)
        merged["resources"] = [r.to_dict() for r in sorted(listed, key=lambda r: r.id)]
        merged["used"] = any(r.used for r in listed)
        if merged["used"]:
            merged.pop("why", None)
        else:
            whys = {entry["why"] for entry in merged["resources"]}
            merged["why"] = whys.pop() if len(whys) == 1 else FLOW_LOG_NONE_READ
        out.append(merged)
    if by_kind:
        raise ValueError(
            f"로그 줄이 있는데 출처 행이 없다: {sorted(by_kind)} — 탐지와 판독이 갈라졌다"
        )
    return out


def build_observation(
    snapshot: Snapshot,
    *,
    topology: dict[str, Any],
    observed_at: str,
    tool_version: str,
    metrics: MetricLayer | None = None,
    sources: SourceScan | None = None,
    flow_logs: FlowLogReading | None = None,
    utilization: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return an observation document and IDs excluded from the
    topology.

    snapshot: Collected current resource state
    topology: Topology JSON defining the resources to observe
    observed_at: Observation time
    tool_version: Tool version used for the observation
    metrics: CloudWatch metric query results
    sources: Log source configuration results
    flow_logs: Traffic evidence from flow logs
    utilization: Usage and utilization query results

    Include only nodes and edges present in topology. Copy the
    topology's
    content digest to run.topology_content_digest when present;
    otherwise,
    omit that field.
    """
    known_nodes = {n["id"] for n in topology["nodes"]}
    known_edges = {e["id"] for e in topology["edges"]}
    nodes_by_id = {n.id: n for n in snapshot.nodes}
    describe = _describe_by_type(snapshot)
    layer = metrics if metrics is not None else MetricLayer.off()
    scan = sources if sources is not None else SourceScan.not_scanned()
    if flow_logs is not None and not layer.enabled:
        raise ValueError("흐름 로그 판독 결과는 2층(지표)이 켜져 있을 때만 붙인다")
    if utilization is not None and any(
        i["target_id"] not in known_nodes for i in utilization
    ):
        raise ValueError("사용률 대상이 구조 문서에 없다")
    if flow_logs is not None and not scan.scanned:
        raise ValueError(
            "흐름 로그 판독 결과는 출처를 탐지했을 때만 붙인다(판독기의 입력이 탐지 결과다)"
        )

    place: dict[str, tuple[str, str] | None] = {}
    node_type: dict[str, str] = {}
    for raw in topology["nodes"]:
        region = raw.get("region")
        if region:
            place[raw["id"]] = (raw["account_id"], region)
        elif is_global_node_id(raw["id"]):
            place[raw["id"]] = global_home(describe, raw["account_id"], raw["type"])
        else:
            place[raw["id"]] = None
        node_type[raw["id"]] = raw["type"]
    raw_nodes = {raw["id"]: raw for raw in topology["nodes"]}
    for raw in topology["edges"]:
        source = raw_nodes.get(raw["source"], {})
        target = raw_nodes.get(raw["target"], {})
        far = lands_on_another_tgw(
            raw["type"],
            (source.get("attributes") or {}).get("transit_gateway_id"),
            target.get("type"),
            target.get("native_id"),
        )
        place[raw["id"]] = place.get(raw["target"] if far else raw["source"])

    resource_of: dict[str, str] = dict(node_type)
    for raw in topology["edges"]:
        if raw["type"] in EDGE_RESOURCE_FROM_SOURCE:
            source_type = node_type.get(raw["source"])
            if source_type is not None:
                resource_of[raw["id"]] = source_type
        elif raw["type"] in EDGE_SOURCE_RESOURCE:
            resource_of[raw["id"]] = EDGE_SOURCE_RESOURCE[raw["type"]]

    reached = {
        (account_id, region)
        for (account_id, region, _type), (status, _reason) in describe.items()
        if status == "ok"
    }

    def readable(target_id: str) -> bool:
        at = place.get(target_id)
        resource_type = resource_of.get(target_id)
        if at is None or resource_type is None:
            return False
        return describe.get((at[0], at[1], resource_type), ("", None))[0] == "ok"

    def cell_of(target_id: str) -> tuple[str, str, str]:
        account, region = place.get(target_id) or ("", "")
        return (account, region, resource_of.get(target_id, ""))

    observations: list[Observation] = []
    dropped: list[str] = []

    for node in snapshot.nodes:
        if node.type in NO_STATE_NODES or node.type in NO_MEANS_NODES:
            continue
        if node.id not in known_nodes:
            dropped.append(node.id)
            continue
        if not readable(node.id):
            continue
        rule = NODE_RULES.get(node.type)
        value = (node.attributes or {}).get(rule.attribute) if rule else None
        if rule is not None and value is None and node.partial:
            judged = Observation(
                node.id,
                "node",
                "none",
                "상세 응답을 읽지 못해 상태 값이 없습니다 — 사유는 이 리소스의 거부 사유와 같습니다",
                _evidence(rule.api),
                None,
                tuple(node.partial_reasons),
            )
        elif rule is not None:
            judged = _judge(rule, value, node.id, "node")
        else:
            judged = _no_rule(node.id, "node", node.type)
        if node.type == "load_balancer":
            judged = _with_targets(judged, node)
        elif node.type == "direct_connect_virtual_interface":
            judged = _with_bgp(judged, node)
        elif node.type == "vpn_connection":
            judged = _with_tunnels(judged, node)
        observations.append(_apply_metric(judged, cell_of(node.id), layer))

    for edge in snapshot.edges:
        if (
            edge.type in NO_STATE_EDGES
            or edge.type in NO_MEANS_EDGES
            or edge.type in FLOW_LOG_ONLY_EDGES
        ):
            continue
        if edge.id not in known_edges:
            dropped.append(edge.id)
            continue
        if not readable(edge.id):
            continue
        if edge.type in METRIC_ONLY_EDGES:
            metric_only = _metric_only_edge(edge.id, cell_of(edge.id), layer)
            if metric_only is not None:
                observations.append(metric_only)
            continue
        if edge.type == "load_balancer_target":
            observations.append(
                _observe_lb_target(
                    edge.id, edge.attributes or {}, nodes_by_id.get(edge.source)
                )
            )
            continue
        if edge.type == "route":
            observations.append(
                _observe_route(
                    edge.id, edge.attributes or {}, node_type.get(edge.source)
                )
            )
            continue
        if edge.type == "association":
            evidence = _association_evidence(node_type.get(edge.source))
            if evidence is False:
                continue
            if evidence is None:
                observations.append(
                    _no_rule(
                        edge.id, "edge", f"association←{node_type.get(edge.source)}"
                    )
                )
                continue
        rule = EDGE_RULES.get(edge.type)
        observations.append(
            _judge(rule, (edge.attributes or {}).get(rule.attribute), edge.id, "edge")
            if rule is not None
            else _no_rule(edge.id, "edge", edge.type)
        )

    for raw in topology["nodes"]:
        why = NO_MEANS_NODES.get(raw["type"])
        at = place.get(raw["id"])
        if why is not None and at is not None and at in reached:
            observations.append(_no_means(raw["id"], "node", why))
    for raw in topology["edges"]:
        if (
            raw["type"] == "association"
            and _association_evidence(node_type.get(raw["source"])) is False
        ):
            why = NO_MEANS_NACL_ASSOCIATION
        else:
            why = NO_MEANS_EDGES.get(raw["type"])
        at = place.get(raw["id"])
        if why is not None and at is not None and at in reached:
            observations.append(_no_means(raw["id"], "edge", why))

    flow_resources: tuple[LogResult, ...] = ()
    if flow_logs is not None:
        flow_resources = tuple(
            r
            if r.attached_to is None or r.attached_to in known_nodes
            else replace(r, attached_to=None)
            for r in flow_logs.resources
        )
        observations, flow_dropped = _flow_log_rows(
            observations, flow_logs, flow_resources, topology, readable
        )
        dropped.extend(flow_dropped)

    counts: dict[tuple[str, str, str], int] = {}
    for obs in observations:
        if obs.target_id.split("|")[0] in METRIC_ONLY_EDGES | FLOW_LOG_ONLY_EDGES:
            continue
        at = place.get(obs.target_id)
        resource_type = resource_of.get(obs.target_id)
        if at is None or resource_type is None:
            continue
        key = (at[0], at[1], resource_type)
        if key in describe:
            counts[key] = counts.get(key, 0) + 1

    emitted = {obs.target_id for obs in observations}
    metric_counts: dict[tuple[str, str, str], int] = {}
    for target_id in sorted(layer.answered() & emitted):
        at = place.get(target_id)
        resource_type = resource_of.get(target_id)
        if at is None or resource_type is None:
            continue
        key = (at[0], at[1], resource_type)
        metric_counts[key] = metric_counts.get(key, 0) + 1

    coverage: list[dict[str, Any]] = []
    for (account_id, region, resource_type), (status, reason) in describe.items():
        metric_status, metric_reason = layer.cell(account_id, region, resource_type)
        if layer.enabled and status != "ok":
            metric_status, metric_reason = "skipped", None
        elif layer.enabled and resource_type in METRIC_PENDING_TYPES:
            metric_status, metric_reason = "skipped", None
        coverage.append(
            {
                "account_id": account_id,
                "region": region,
                "resource_type": resource_type,
                "layer": "describe",
                "status": status,
                "reason": reason,
                "item_count": counts.get((account_id, region, resource_type), 0)
                if status == "ok"
                else None,
            }
        )
        coverage.append(
            {
                "account_id": account_id,
                "region": region,
                "resource_type": resource_type,
                "layer": "metric",
                "status": metric_status,
                "reason": metric_reason,
                "item_count": metric_counts.get((account_id, region, resource_type), 0)
                if metric_status == "ok"
                else None,
            }
        )

    document = {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "observed_at": observed_at,
            "tool_version": tool_version,
            "topology_collected_at": topology["run"]["collected_at"],
            **(
                {"topology_content_digest": topology["run"]["content_digest"]}
                if topology["run"].get("content_digest")
                else {}
            ),
        },
        "scope": {
            "account_ids": list(topology["scope"]["account_ids"]),
            "regions": list(topology["scope"]["regions"]),
            "resource_types": sorted(snapshot.scope.resource_types),
        },
        "layers": {
            "describe": True,
            "metric": layer.enabled,
            **({"flow_log": True} if flow_logs is not None else {}),
            **({"utilization": True} if utilization is not None else {}),
        },
        "sources": (
            _merge_flow_log_sources(scan.to_list(), flow_resources)
            if flow_logs is not None
            else scan.to_list()
        ),
        "observations": [
            o.to_dict() for o in sorted(observations, key=lambda o: o.target_id)
        ],
        "coverage": sorted(
            coverage,
            key=lambda r: (
                r["account_id"],
                r["region"],
                r["resource_type"],
                r["layer"],
            ),
        ),
        "history": {
            "baseline_at": observed_at,
            "resolution_seconds": DEFAULT_RESOLUTION_SECONDS,
            "retention_seconds": DEFAULT_RETENTION_SECONDS,
            "truncated": False,
            "flap_collapse_threshold": DEFAULT_FLAP_COLLAPSE_THRESHOLD,
            "max_changes": DEFAULT_MAX_CHANGES,
        },
        "changes": [],
        **({"utilization": utilization} if utilization is not None else {}),
    }
    return document, sorted(dropped)
