"""1층 상태 관측 — `schema/observation.schema.json` 의 파이썬 표현 + ADR 0006 §O7 판정 규칙.

**구조 문서를 다시 만들지 않는다**(ADR 0004 D1 = 구조와 상태는 다른 파일). 대신 D2-2 함정 ① 이
적은 사실을 그대로 따른다 — 「**「상태만 부르는 API」는 없다**」. 상태 필드는 구조와 **같은**
`Describe*` 응답에서 오므로 `collect()` 를 **상태 있는 종류로만** 돌리고 그 스냅샷에서 판정만 뽑는다.
종류가 줄어도(route_table·SG·NACL 은 1층 근거가 없다) 시간이 배수로 줄지는 않는다.

🔴 **표에 없는 값이 오면 조용히 `up` 으로 만들지 않는다.** 아래 값 집합은 실계정 실측
(2026-09-23, 2계정 × 3리전 = 263 노드 · 169 엣지)과 AWS 문서를 합친 것이고, **실측에 나온 값은
전부 정상 쪽이었다** — `down` 값은 **0건**이다. 그래서:

- 정상 값이 타입마다 다르다: `available`(VPC·Subnet·NAT·TGW·TGW첨부·엔드포인트) ·
  `attached`(IGW — `Attachments[].State`) · `active`(LB·피어링) · `associated`(연관 엣지).
  하나로 뭉치면 틀린다.
- **`down` 경로의 정본은 픽스처·테스트다.** 「실계정에서 전부 `up` 이 나왔다」는 구현이 맞다는
  증거가 아니다 — 판정이 통째로 죽어 있어도 같은 그림이 나온다(ADR 0006 §O7-1 함정 1+3 과 같은
  계열: 구현 버그와 「정상」이 바이트 단위로 같다).
- 어느 집합에도 없는 값은 **`none` + `partial`** 이고 `why` 에 실제 값을 적는다. 캔버스 §H1 의
  세 번째 모습(「그릴 규칙이 없다」 → 개발)이 이것이고, §O7 이 「표에 행이 없는 타입은 결함」이라
  한 것의 **값 수준 버전**이다. 조용히 삼키면 사람이 오판한다(불변식 ④).
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

# ADR 0006 「승인된 기본값」 표(O4, 승인 2026-09-20)의 사본. 🔴 정본은 그 표이고
# `test_history_defaults_match_the_adr` 가 일치를 강제한다 — 여기만 고치면 픽스처·스키마
# 테스트와 갈라진다.
DEFAULT_RESOLUTION_SECONDS = 300
DEFAULT_RETENTION_SECONDS = 604800
DEFAULT_FLAP_COLLAPSE_THRESHOLD = 4
# 대장 19(승인 2026-10-02). 시간 창과 둘 다 걸린다 — 넘으면 오래된 줄부터 버린다(history.apply_history).
DEFAULT_MAX_CHANGES = 5000


@dataclass(frozen=True, slots=True)
class _Rule:
    """한 타입의 1층 판정 규칙. `attribute` = 구조 수집기가 그 값을 넣어 둔 키."""

    api: str
    up: frozenset[str]
    down: frozenset[str]
    # 🔴 **지우는 중**(AWS 가 「이 자원을 지우고 있다」고 보고한 값) — `up` 도 `down` 도 아니다(사용자 결정 2026-10-21 ·
    # QA C). 일부러 지운 엔드포인트·NAT·어태치먼트가 「없어짐」 전에 빨간 「끊김 · 정상 → 이상」을 먼저 남겼다 — 고객은
    # 그것을 장애로 읽는다. 판정 = `none` + `deleting: true`(스키마 1.12.0) · 이력은 그 전이를 적지 않는다(`history.py`).
    deleting: frozenset[str]
    attribute: str = "state"


def _rule(
    api: str,
    *,
    up: tuple[str, ...],
    down: tuple[str, ...] = (),
    # 🔴 **기본값이 없다** — 종류마다 「지우는 중」 어휘가 다르다(`deleting` · `shutting-down` · `delete-in-progress` ·
    # `DELETING` · `Deleting` · ECS `draining`). 정규식 하나로 뭉치면 `delete-failed`(실패 = 이상)까지 삼킨다.
    # 지우는 중 상태가 없는 종류(VPC·서브넷·LB …)는 `()` 를 **적는다** — 새 종류가 이 칸을 잊으면 임포트가 깨진다.
    deleting: tuple[str, ...],
    attribute: str = "state",
) -> _Rule:
    # 🔴 비교는 casefold 로 한다 — 서비스 파일은 AWS 값을 **그대로** 담는다
    # (`services/vpc_endpoint.py:28`·`load_balancer.py:111` = `str(...)`, 정규화 0). AWS 문서의
    # 엔드포인트 상태는 `Available` 대문자인데 실측은 소문자였다 → 어느 쪽이 와도 「모르는 값」으로
    # 떨어지면 화면이 통째로 `?` 가 된다. 선례는 `services/base.py:354` 의 `alive()` 다.
    rule = _Rule(
        api=api,
        up=frozenset(s.casefold() for s in up),
        down=frozenset(s.casefold() for s in down),
        deleting=frozenset(s.casefold() for s in deleting),
        attribute=attribute,
    )
    # 한 값이 두 칸에 있으면 `_judge` 의 검사 순서가 판정을 정한다 — 표가 말하지 않는 규칙이 된다.
    if (
        (rule.up & rule.down)
        or (rule.up & rule.deleting)
        or (rule.down & rule.deleting)
    ):
        raise ValueError(f"{api}: up·down·deleting 이 겹친다")
    return rule


# ADR 0006 §O7 「노드」 표의 「1층(`Describe*`) 근거」 열. 🔴 표가 정본이고
# `test_o7_node_table_matches_the_rules` 가 키 집합의 일치를 강제한다 — 표에 행을 더하고
# 여기에 규칙을 안 더하면 그 타입은 상태가 조용히 안 붙는다.
NODE_RULES: dict[str, _Rule] = {
    # 🔴 전환 상태(만드는 중 · 수락 대기)는 `down` 이다(ADR 0006 W3 규칙 2 — `up` 은 「지금 받는다」가 보장되는 값만).
    #    목록에 없으면 `none`(「관측 불가」)으로 떨어져 이력에 「관측 불가 → 정상」이 남는다(QA B B-08 · B-12 실측 2026-10-13).
    # 🔴 **지우는 중은 예외다**(사용자 결정 2026-10-21 · QA C) — `deleting` 칸. `down` 이면 일부러 지운 자원마다 「없어짐」
    #    앞에 빨간 「끊김」이 남는다. 실패(`failed` · `delete-failed` · `Delete_Failed` …)는 그대로 `down` 이다.
    #    `deleted`·`terminated` 같은 「다 지워졌다」 값은 구조가 노드째 뺀다(`services/base.py` `GONE_STATES`) —
    #    그래도 이 칸에 둔다: 그 거름이 빠지는 날 「다 지운 자원」이 빨갛게 되지 않는다.
    "vpc": _rule(
        "ec2:DescribeVpcs", up=("available",), down=("pending",), deleting=()
    ),  # AWS `VpcState` = pending · available 뿐 — 지운 VPC 는 목록에서 바로 빠진다
    "subnet": _rule(
        "ec2:DescribeSubnets",
        up=("available",),
        down=("pending", "unavailable", "failed", "failed-insufficient-capacity"),
        deleting=(),  # 지운 서브넷은 목록에서 바로 빠진다
    ),
    # 🔴 IGW 의 값은 AWS 원본이 아니다 — `services/internet_gateway.py:51-53` 이
    # `any(alive(...))` 로 접어 **`attached`/`detached` 두 값만** 만든다(실측 `attached` 8건).
    # 그래서 `attaching`·`detaching` 을 down 에 적으면 **도달할 수 없는 설정**이 된다(어서션은
    # 아니지만 같은 병이다 — 커버리지처럼 보이는 죽은 줄). 적지 않는다.
    # 🔴 대신 **알려진 한계**: `alive()` 의 `DEAD_STATES`(`services/base.py:116-133`)에 `attaching`
    # 이 없으므로 **부착 중인 IGW 도 `attached`=`up`** 이다. 부착 중을 끊김으로 그리지는 않지만
    # 「아직 안 붙었다」를 「붙었다」로 그린다 — 5분 주기에서 한 사이클 안에 끝나는 상태라
    # 지금은 받아들인다. 고치려면 서비스 파일이 3값을 내야 한다(구조 계약 변경).
    "internet_gateway": _rule(
        "ec2:DescribeInternetGateways",
        up=("attached",),
        down=("attaching", "detaching", "detached"),
        deleting=(),  # 값이 `attached`/`detached` 둘뿐이다(위 🔴) — 떼기(`detaching`)는 삭제가 아니라 구성 변경이다
    ),
    "nat_gateway": _rule(
        "ec2:DescribeNatGateways",
        up=("available",),
        down=("pending", "failed"),
        deleting=("deleting", "deleted"),
    ),
    "transit_gateway": _rule(
        "ec2:DescribeTransitGateways",
        # `modifying` = 설정을 바꾸는 중 · 그동안에도 경로를 실어 나른다
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
        # AWS 문서는 `Deleting`·`Deleted`(첫 글자 대문자) · 실측은 소문자 — casefold 로 둘 다 받는다
        deleting=("deleting", "deleted"),
    ),
    # `State.Code` 가 1차 판정이고, 대상 상태(`DescribeTargetHealth`, 상태 단 후속 호출)가 `active` 를
    # `down` 으로 내릴 수 있다 — 「LB 는 살아 있는데 대상이 전부 비정상」(아래 `_with_targets` · ADR 0006 W3②).
    "load_balancer": _rule(
        "elasticloadbalancing:DescribeLoadBalancers",
        # `active_impaired` = 일부 AZ 가 손상됐지만 남은 AZ 로 받는다(이유 문구가 그 사실을 말한다 — `_FOLDED_WHY`)
        up=("active", "active_impaired"),
        down=("provisioning", "failed"),
        deleting=(),  # `State.Code` = active · provisioning · active_impaired · failed 뿐 — 지운 LB 는 목록에서 바로 빠진다
    ),
    # ── W3 (ADR 0006 §O7 「W2 → W3 판정 규칙」). 🔴 `up` = AWS 가 「지금 요청을 받는다」고 보장하는
    # 값만이다. 전환 중은 `down` — `why` 에 AWS 값이 그대로 적히므로 사람은 「재시작 중」을 읽는다.
    # 앞 다섯 종류의 값은 구조가 아니라 **상태 탐침**(`Service.state`)에서 온다 — 구조 문서에는 없다.
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
    # 값은 탐침이 `status` + 태스크 수로 접은 것이다(`services/ecs_service.py` 의 `state`).
    "ecs_service": _rule(
        "ecs:DescribeServices",
        up=("active", "active-under-desired"),
        down=("active-no-running-tasks", "active-desired-zero"),
        # `DRAINING` = `DeleteService` 뒤 태스크를 빼는 중(AWS 문서 「the service is being deleted」) · `INACTIVE` = 지워졌다
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
        deleting=("Deleting",),  # `Delete_Failed` 는 실패다 — `down` 에 남는다
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
    # EIC 는 구조가 이미 `state` 를 싣는다(`services/ec2_instance_connect_endpoint.py` 머리말).
    # ── W3 · 2차 수집기 3종(§O7 「2차 수집기 4종」 표). EC2 는 구조가 `state` 를 싣고, EKS·FW 는 탐침이다.
    "ec2_instance": _rule(
        "ec2:DescribeInstances",
        up=("running",),
        down=("pending", "stopping", "stopped"),
        # 종료(terminate)의 두 단계 — 중지(`stopping`·`stopped`)는 삭제가 아니다 → `down` 에 남는다
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
            "delete-failed",  # 지우다 실패했다 = 이상
        ),
        deleting=("delete-in-progress", "delete-complete"),
    ),
    # ── 2.6.0 · 2차 Site-to-Site VPN 3종(§O7 「Site-to-Site VPN 3종」 표). 게이트웨이 둘은 구조가
    # `state` 를 싣고(AWS 원문), VPN 연결은 탐침이 터널 원격 측정을 접는다(`services/vpn_connection.py`).
    # 🔴 터널 UP/DOWN 은 구조 문서에 없다(상태 금지) — 탐침 값으로만 온다.
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
    # 터널 하나만 내려간 연결은 **통한다**(AWS 가 두 터널을 주는 이유가 그것이다) — `up` 에 두고
    # 이유 문구가 「한 터널이 내려갔다」를 말한다. 둘 다 내려가면 `down`.
    "vpn_connection": _rule(
        "ec2:DescribeVpnConnections",
        up=("available", "available-one-tunnel-down"),
        down=("available-all-tunnels-down", "pending"),
        deleting=("deleting", "deleted"),
    ),
    # ── 2차 Direct Connect 3종(§O7 노드 표 DX 행 · ADR 0010 「결과」). W3 규칙 2 그대로: `up` = 「지금 받는다」를
    # 보장하는 값만 · 전환 중은 `down` · AWS 의 `unknown` 은 **일부러 어디에도 없다**(→ `none` + 이유 = 「모른다」).
    # 연결·DXGW 는 구조가 `state` 를 싣고(AWS 원문), VIF 는 탐침이 BGP 피어 up/down 을 접는다
    # (`services/direct_connect_virtual_interface.py` 의 `state`). 🔴 BGP 상태는 구조 문서에 없다(상태 금지).
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
    # 피어 일부만 down 인 VIF 는 **통한다**(남은 피어로) — VPN 「터널 일부 DOWN」과 같은 정책이다.
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
    # 🔴 **글로벌 노드**(`region` = null) — 판정 칸은 아래 `build_observation` 의 「글로벌 자리」 규칙이 정한다.
    "direct_connect_gateway": _rule(
        "directconnect:DescribeDirectConnectGateways",
        up=("available",),
        down=("pending",),
        deleting=("deleting", "deleted"),
    ),
    # ── #114(2.9.0 · ADR 0013) — VPC 안 IP 를 쓰는 서비스(§O7 「#114 4종」 표). 값은 전부 **상태 탐침**이다(구조 문서에 없다).
    # W3 규칙 2 그대로 — `up` = 연결을 받은 채 도는 값만(설정 변경·스냅샷) · 만드는 중·지우는 중·재부팅·네트워크 불일치 = `down`.
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
    # 탐침이 `DomainProcessingStatus`(없으면 Deleted·Created·Processing 불리언)를 한 값으로 접는다(`services/opensearch_domain.py`).
    # 블루/그린 갱신(Modifying·UpgradingEngineVersion·UpdatingServiceSoftware) 중에도 옛 노드가 요청을 받는다 → `up`.
    "opensearch_domain": _rule(
        "es:DescribeDomains",
        up=("Active", "Modifying", "UpgradingEngineVersion", "UpdatingServiceSoftware"),
        down=("Creating", "Isolated"),
        deleting=("Deleting",),
    ),
    # 롤링 갱신·유지 보수(UPDATING·MAINTENANCE)는 브로커를 하나씩 돌린다 — 클러스터는 받는다 → `up`.
    # 치유(HEALING)·브로커 재부팅은 그 사이 일부 파티션이 리더를 잃는다 → `down`(이유 문구에 AWS 값이 그대로 적힌다).
    "msk_cluster": _rule(
        "kafka:ListClustersV2",
        up=("ACTIVE", "UPDATING", "MAINTENANCE"),
        down=("CREATING", "FAILED", "HEALING", "REBOOTING_BROKER"),
        deleting=("DELETING",),
    ),
}

# ECS 탐침이 만든 값은 AWS 원문이 아니다 — `why` 에 「AWS 가 보고한 state = …」라고 적으면 거짓이 된다.
_FOLDED_WHY: dict[str, str] = {
    "active_impaired": "로드 밸런서의 일부 AZ 가 손상됐습니다(active_impaired) — 나머지 AZ 로 요청을 받습니다",
    "active-no-running-tasks": "서비스는 ACTIVE 이지만 실행 중인 태스크가 0개입니다(원하는 수 > 0)",
    "active-desired-zero": "원하는 태스크 수가 0입니다 — 서비스가 멈춰 있습니다",
    # VPN 탐침이 터널 원격 측정(`VgwTelemetry[].Status`)을 접은 값.
    "available-one-tunnel-down": "VPN 연결은 available 이지만 터널 일부가 DOWN 입니다 — 나머지 터널로 연결됩니다",
    "available-all-tunnels-down": "VPN 연결은 available 이지만 터널이 전부 DOWN 입니다 — 온프레미스로 연결되지 않습니다",
    # DX VIF 탐침이 BGP 피어 상태(`bgpPeers[].bgpStatus`)를 접은 값.
    "available-some-bgp-down": "VIF 는 available 이지만 BGP 피어 일부가 down 입니다 — 나머지 피어로 연결됩니다",
    "available-all-bgp-down": "VIF 는 available 이지만 BGP 피어가 전부 down 입니다 — 이 VIF 로는 온프레미스 경로가 없습니다",
}

# §O7 「엣지」 표. `route` 는 불린이라 규칙 대신 아래 `_observe_route()` 가 든다.
#
# 🔴 **`association` 은 두 수집기에서 나오고 한쪽에는 근거가 없다.**
#   - `services/route_table.py:66-72` — AWS 의 `AssociationState.State` (기본값 `associated`)
#   - `services/network_acl.py:25-35` — **상수 `"associated"`**. AWS 응답을 보지 않는다.
# 타입만 보고 판정하면 NACL↔서브넷 선이 **영원히 `up`** 이 된다 — 판정이 통째로 죽어 있어도
# 같은 그림이다. 그래서 아래 `_association_has_evidence()` 가 **source 노드의 타입**으로 갈라
# NACL 발 연관은 `none`(관측 수단 없음)으로 보낸다.
EDGE_RULES: dict[str, _Rule] = {
    "association": _rule(
        "ec2:DescribeRouteTables",
        up=("associated",),
        down=("failed", "disassociated"),
        # 연관에는 「지우는 중」이 없다 — 떼는 중(`disassociating`)·뗀(`disassociated`) 연관은 선 자체가 안 생긴다
        # (`services/base.py` `DEAD_STATES`). `disassociated` 를 위 `down` 에 둔 것은 옛 판 그대로다(도달하지 않는다).
        deleting=(),
    ),
}

ROUTE_API = "ec2:DescribeRouteTables"
TGW_ROUTE_API = "ec2:SearchTransitGatewayRoutes"

# 위 🔴 의 구현. 값은 「source 노드 타입 → 근거가 있나」.
ASSOCIATION_EVIDENCE: dict[str, bool] = {"route_table": True, "network_acl": False}
NO_MEANS_NACL_ASSOCIATION = (
    # 🔴 화면 「사유」 칸에 그대로 나온다 — 고객 말(합니다체 · docs/qa/copy-a.md). 근거(`services/network_acl.py:31` 이
    #    상수 `associated` 를 넣는다 → `up` 으로 읽으면 판정이 죽어도 같은 그림)는 위 주석이 든다.
    "네트워크 ACL 과 서브넷의 연결에는 AWS 가 상태를 제공하지 않습니다 — 이 연결의 상태는 판정하지 않습니다"
)

# §O7 의 **`none`** 행 = 항목을 만들고 `?` 를 붙인다(「관측 수단이 없다」를 사람에게 말해야 한다).
# 「상태 없음」 행(route_table·peering·account·region)과 섞지 마라 — 그쪽은 **항목을 만들지
# 않는다**. 「2층 전용」 행(아래 `METRIC_ONLY_EDGES`)과도 섞지 마라 — 그쪽은 2층이 답했을 때만
# 항목이 생긴다.
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

# §O7 의 「상태 없음」 행. observations 에 **항목을 만들지 않는다**.
NO_STATE_NODES = frozenset(
    {
        "account",
        "region",
        "route_table",
        # TGW 라우트 테이블도 route_table 과 같은 자리다 — 노드는 상태 없음(그림은 TGW 가 든다),
        # 경로(`route` 엣지)는 판정한다. 그래서 아래 `OBSERVED_RESOURCE_TYPES` 에는 **있다**.
        "transit_gateway_route_table",
        # CloudFront 는 지표 리전 축이 안 맞아 규칙을 미룬다(아래 `OBSERVED_RESOURCE_TYPES` 주석).
        # 관측이 부르지 않으므로 이 줄은 §O7 표와의 대조만을 위한 것이다.
        "cloudfront",
        # 파생 노드 2종(2.7.0 · LB 대상 해소가 짓는다) — 자기 상태 API 가 없다. 대상 헬스는 선(`load_balancer_target`)이 든다.
        "lambda_function",
        "external_ip",
        # Route 53 호스팅 영역(2.8.0 · P2-4) — 영역에는 「살아 있나」가 없다(레코드 구성뿐 · 상태 단은 부르지 않는다)
        "route53_hosted_zone",
        # S3 버킷(2.8.0 · P2-3 · 파생 노드) — S3 를 읽지 않는다(CloudFront 원본 이름뿐)
        "s3_bucket",
        # 배치 D(2.10.0 · ADR 0006 §O7 「배치 D」 행) — 넷 다 상태 없음(지금은). API·프록시·가속기의 1층·2층은 후속이다
        "elastic_ip",
        "api_gateway",
        "rds_db_proxy",
        "global_accelerator",
        # #129(2.11.0 · ADR 0006 §O7 「#129」 행) — Cloud WAN 코어 네트워크 · VPC Lattice 서비스 네트워크·서비스 = 상태 없음(지금은 · 1층·2층은 후속)
        "core_network",
        "lattice_service_network",
        "lattice_service",
        # 2.12.0(ADR 0014 · ADR 0006 §O7 「WAF」 행) — WAF 웹 ACL = 상태 없음(구성만 · 허용·차단 수 지표는 후속)
        "waf_web_acl",
        # Direct Connect 3종은 2026-10-13 에 1층 규칙이 붙어 이 집합에서 빠졌다(위 `NODE_RULES` · §O7 DX 행).
        # 2층 프로브는 여전히 후속이다(`metrics.METRIC_PENDING_TYPES` · DXGW 는 지표가 없다).
    }
)

# 🆕 #133(2026-10-21 · ADR 0006 결정 14) — **상태 없는 카드의 생김·없어짐은 구조 문서 두 장에서 온다.**
# 위 「상태 없음」 노드는 관측 항목이 없어서, 관측 문서 두 장만 보던 이력에 한 줄도 남지 않았다(QA C — 시험 스택을 지웠는데
# API Gateway · Global Accelerator 등 8종의 「없어짐」이 없었다). 그래서 이력(`history.apply_history`)이 **두 관측이 짝지어
# 그려진 구조 문서**의 카드 목록을 비교한다 — 관측 항목도 AWS 호출도 늘지 않는다(`?` 알약 · 5분 함수의 호출 수 그대로).
# 값 = 그 카드를 구조 문서에 나타나게 한 **수집 종류**. 없는 쪽 구조가 그 칸을 `ok` 로 읽었어야 「없어졌다/생겼다」를 믿는다
# (못 읽었으면 「못 읽은 칸」으로 센다 — 불변식 ④). 둘 이상이면 **전부** `ok` 여야 한다(어느 응답에서 왔는지 id 로는 모른다).
# 🔴 일부러 뺀 것: `account`·`region`(컨테이너) · `route_table`·`transit_gateway_route_table`(그림은 경로·연관 선이 들고 그 선은
# 관측 항목이 있다) · `external_ip`(LB 에 등록된 IP 일 뿐이다 — 등록의 생김·없어짐은 `load_balancer_target` 선이 이미 말한다).
PRESENCE_ONLY_NODES: dict[str, tuple[str, ...]] = {
    "cloudfront": ("cloudfront",),
    # CloudFront S3 원본에서만 짓는다(`origins.py` · S3 는 읽지 않는다) — 카드가 생겼다/없어졌다이지 버킷을 만들었다/지웠다가 아니다.
    "s3_bucket": ("cloudfront",),
    "route53_hosted_zone": ("route53_hosted_zone",),
    "lambda_function": ("lambda_function",),
    "elastic_ip": ("elastic_ip",),
    # REST(`api_gateway`)와 HTTP·WebSocket(`api_gateway_v2`)이 같은 카드 종류를 짓는다.
    "api_gateway": ("api_gateway", "api_gateway_v2"),
    "rds_db_proxy": ("rds_db_proxy",),
    # #129(2.11.0) — 셋 다 자기 수집 종류가 카드를 짓는다(Lattice 서비스 네트워크의 서비스 연관은 선뿐 · 카드를 짓지 않는다).
    "core_network": ("core_network",),
    "lattice_service_network": ("lattice_service_network",),
    "lattice_service": ("lattice_service",),
    "global_accelerator": ("global_accelerator",),
    # 2.12.0(ADR 0014) — 리전 범위·CloudFront 범위 두 수집 종류가 같은 카드 종류를 짓는다(API Gateway 와 같은 모양).
    "waf_web_acl": ("waf_web_acl", "waf_web_acl_global"),
}
# 피어링은 2026-09-28 아래 「흐름 로그 전용」으로 옮겼다. `origin`(#43 · CloudFront → LB)은 상태가
# 없다: CloudFront 는 오리진별 상태를 주지 않고, 지표에도 오리진 차원이 없다(배포 단위뿐).
# §O7 대조(`test_edge_rules_match_the_o7_table`)가 이 이름으로 「표의 상태 없음 엣지 == 코드의
# 상태 없음 엣지」를 잰다 — 한쪽만 고치면 그 검사가 빨개진다.
# `endpoint_provider`(2.8.0 · GWLB 엔드포인트 → GWLB)도 같다 — 「어디로 넘기도록 설정했나」다. 엔드포인트의 상태는 노드가 들고(같은
#   `State`) 흐름은 `endpoint_connection` 2층이 잰다. 선 하나에 따로 붙일 응답이 없다.
# `dns_alias`·`zone_association`(2.8.0 · Route 53)도 같다 — 이름이 어디를 가리키도록 설정했나 · 그 VPC 에서 풀리도록 설정했나다.
NO_STATE_EDGES: frozenset[str] = frozenset(
    {"origin", "endpoint_provider", "dns_alias", "zone_association"}
    # 배치 D(2.10.0) — 넷 다 「어디로 넘기도록 설정했나」 · 대상 헬스(프록시·가속기)는 후속
    | {"eip_association", "vpc_link", "proxy_target", "accelerator_endpoint"}
    # #129(2.11.0) — Connect 가 올라탄 전송 · Cloud WAN 연결 · Lattice 연결·대상 = 「어떻게 이어 두었나」(구성). Connect 어태치먼트 자신의 상태는
    #   그 노드(transit_gateway_attachment 규칙)가 든다 · 연결·대상 헬스는 후속
    | {
        "connect_transport",
        "core_network_attachment",
        "lattice_vpc_association",
        "lattice_service_association",
        "lattice_target",
    }
    # 2.12.0(ADR 0014) — web ACL 이 그 자원 앞에서 거른다 = 「어떻게 설정했나」(구성)
    | {"waf_protection"}
)

# §O7 의 **「흐름 로그 전용」** 행 (ADR 0008 「D4b 개정」 · S5 1단계 · 선택). 1층 근거는 노드가 들고
# (같은 `Status.Code`) 2층 지표도 없다(피어링 namespace 가 없다 — ADR 0008 D2 마지막 행). **고객이
# 흐름 로그를 켰고 우리가 읽어서 왕복 짝을 찾았을 때만** 항목이 생긴다: `live` · B · `observed_on`.
# 짝이 없거나 못 읽었으면 **행이 없다** — 1단계는 흐른다만 말한다.
# 🔴 `METRIC_ONLY_EDGES` 와 합치지 마라. 면제(행 없음 ≠ 결함)는 같지만 근거가 다르다 — 합치면 2층
# 원장(`item_count`)이 흐름 로그 행을 지표로 센다. 행을 **만드는** 곳은 `_flow_log_rows` 하나다(S5 U7 ·
# ADR 0008 「D4b 개정」 ⑤-2). 1층 루프는 피어링을 건너뛰고, 1층 원장은 이 행을 세지 않는다.
FLOW_LOG_ONLY_EDGES = frozenset({"peering"})

# 흐름 로그가 구간으로 이름 대는 대상 두 종류(경로 번호 8·2 → IGW · 4·5 → 피어링). 라우트 행 `by_subnet` 은 이
# 대상으로 가는 행에만 붙는다(스키마 `by_subnet` 주석).
FLOW_LOG_TARGETS = ("internet_gateway/", "vpc_peering_connection/")
FLOW_LOG_METRIC = "flow_log"
# 출처 행 이유 — 켜진 로그를 하나도 못 읽었는데 로그마다 이유가 다를 때. 🔴 한 줄로 뭉개지 않는다 — 이유는 로그 줄에 있다.
FLOW_LOG_NONE_READ = "켜진 흐름 로그를 하나도 읽지 못했다 — 이유는 로그마다 적었다"

# §O7 의 **「2층 전용」** 행 (ADR 0008 **D8-1**). 이 세 엣지는 **2층이 그 구간을 실제로 답했을
# 때만** 항목이 생긴다 — 답하지 못했으면 **행이 없다**(= D1 의 E 등급, 화면에서는 근거 없는 선).
#
# 🔴 **1층 규칙을 붙이지 않는 것이 결정이다.** 세 엣지는 소유 자원의 `state` 를 그대로 들고
# 있지만(`services/transit_gateway_attachment.py:32-56`·`vpc_endpoint.py:32-38`·
# `load_balancer.py:115-122`) 그 값으로 판정하면 두 가지가 깨진다:
#  ① §O7 규칙 1 — 같은 API 필드를 노드와 엣지에 **둘 다** 싣는 것이 옛 종단 추론의 형태다.
#     어태치먼트 노드가 이미 `available` 로 `up` 을 말하는데 그 선까지 같은 값으로 `up` 을 말하면
#     「구간을 쟀다」와 「자원이 살아 있다」가 화면에서 구별되지 않는다.
#  ② `attachment` 엣지는 **두 수집기**에서 나오고 정상 어휘가 다르다(TGW=`available` /
#     IGW=`attached`) — `association` 이 겪은 어휘 분기와 같은 함정이다(위 🔴).
# 그래서 이 세 종류의 `status` 는 2층만 만든다: `live`(통과>0) · `down`(통과 0 + 차단 신호) ·
# `up`(쟀고 0). 2층이 꺼져 있으면 세 종류는 통째로 조용하다.
METRIC_ONLY_EDGES = frozenset(
    {"attachment", "endpoint_connection", "load_balancer_placement"}
)

# 🔴 **엣지의 coverage 종류 = 그 엣지를 만든 응답의 종류다**(ADR 0006 O10). 끝점 노드의 종류로
# 판정하면 틀린다 — `route` 엣지의 source 는 route_table 이고 target 은 NAT·IGW·TGW 인데,
# 그 대상 종류가 거부돼도 경로 자체는 route_table 응답 안에 그대로 들어 있다.
# `association`·`attachment` 는 여기 없다 — 아래 `EDGE_RESOURCE_FROM_SOURCE` 가 든다.
EDGE_SOURCE_RESOURCE: dict[str, str] = {
    "security_group_reference": "security_group",
    # 2층 전용 엣지 2종(위 `METRIC_ONLY_EDGES`). 🔴 여기 없으면 `readable()` 이 종류를 몰라
    # **조용히 False** 를 돌려주고 행이 영원히 생기지 않는다 — 2층이 답을 줘도 버려진다.
    "endpoint_connection": "vpc_endpoint",
    "load_balancer_placement": "load_balancer",
    # 흐름 로그 전용 엣지(위 `FLOW_LOG_ONLY_EDGES`). 선은 피어링 응답에서 나온다(`vpc_peering_connection.py`
    # — source = `pcx-` 노드). 🔴 같은 이유로 빠지면 흐름 로그가 짝을 찾아도 행이 영원히 안 생긴다.
    "peering": "vpc_peering_connection",
    # LB → 대상(2.7.0 · ADR 0002 §8) — 선은 LB 의 `DescribeTargetHealth` 응답에서 나온다(`lb_targets.py`).
    #   🔴 빠지면 `readable()` 이 종류를 몰라 조용히 False → 대상 선의 헬스가 영원히 안 붙는다.
    "load_balancer_target": "load_balancer",
}

# 🔴 **한 종류가 여러 수집기에서 나오면 종류를 고정할 수 없다** → source 노드 타입으로 읽는다.
#   - `association` — route_table 또는 network_acl
#   - `attachment` — transit_gateway_attachment(TGW 쪽·VPC 쪽 두 선) 또는 internet_gateway(IGW↔VPC)
#   - `route` — route_table 또는 transit_gateway_route_table(W2). 고정 매핑(`route_table`)이던 동안
#     TGW 표의 경로가 **VPC 라우트 표 칸**을 봤다 — 그 칸이 거부되면 TGW 경로가 이유 없이 사라지고,
#     TGW 표 칸이 거부돼도 경로가 남는다(발견 W2 · 고침 W3).
# 고정 매핑을 쓰면 IGW 발 어태치먼트가 **엉뚱한 종류의 coverage 칸**을 보고, 그 칸이 거부된
# 계정에서 조용히 사라진다. 이 집합은 `metrics._resource_of_edge()`(= source 노드 종류)와
# **같은 규칙**이어야 한다 — 갈라지면 2층이 답한 칸과 1층 coverage 칸이 어긋난다.
EDGE_RESOURCE_FROM_SOURCE = frozenset({"association", "attachment", "route"})

# `observe` 가 **다시 부를** 종류 = 1층 근거가 있는 것만.
#
# 🔴 `security_group`·`network_acl` 은 **일부러 없다.** 둘은 `none` 밖에 낼 수 없고(위
# `NO_MEANS_NODES`), `none` 은 AWS 응답이 아니라 「AWS 가 그런 것을 보고하지 않는다」는 우리
# 판단이다 → 부를 이유가 0이다. id 는 구조 문서에 이미 있으므로 거기서 읽어 항목만 만든다.
# 반대로 `route_table` 은 상태가 없는데도 **있다** — route/association 엣지가 그 응답에서만
# 나오고, 그 둘에는 상태가 있다.
#
# 수집기 4종(`ec2_instance`·`auto_scaling_group`·`eks_cluster`·`network_firewall`)은 W3 에서 1층
# 판정이 붙었다(ADR 0006 §O7 「2차 수집기 4종」 표). 🔴 `auto_scaling_group` 만 **일부러 없다** —
# 논리 묶음이라 `none` 밖에 못 내고(`NO_MEANS_NODES`), 부를 이유가 0이다(SG·NACL 과 같다).
#
# 🔴 `cloudfront` 는 **일부러 없다.** 이유가 ASG 와 다르다 — 지표가 없어서가 아니라 **리전이
# 안 맞아서**다. CloudFront 지표는 `AWS/CloudFront` 네임스페이스에 있고 그것은 **us-east-1 에만**
# 있는데, `observe` 는 (계정,리전) 곱집합을 돌며 그 리전의 CloudWatch 에 묻는다. 지금 넣으면
# us-east-1 을 수집 범위에 넣지 않은 고객에게 **전 배포가 영원히 「확인 불가」** 가 된다(칸이 하나가
# 아니라 CloudFront 전부라서 사람은 「기능이 안 된다」로 읽는다 — 불변식 ④). 지표 축을 us-east-1 로
# 고정하는 것은 `metrics.py` 의 좌표 규칙 변경이라 별 PR 이다. 🔵 실측은 아직 안 했다.
# 생김·없어짐은 이 목록과 무관하게 이력에 남는다 — 구조 문서 두 장에서 온다(위 `PRESENCE_ONLY_NODES` · #133).
#
# 🔴 그래서 30종 중 **26종**을 다시 부른다(2026-10-13 에 DX 3종 · 2.6.0 에 VPN 3종 · W3 에 W2 7종 + 2차 3종이
# 더해졌다 · 원래 17종 중 10종). ADR 0004 D2-2 함정 ①(「상태만 부르는 API 는 없다 …
# 배수로 줄지 않는다」)의 실측 수치가 이것이다 — 2종은 종류 수의 17%이고, 5분 주기의 마진을
# 만들어 주지 않는다. 마진은 병렬화나 scope 캡에서 와야 한다(D2-2 선행 2건).
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
    # W3 — W2 수집 7종. TGW 라우트 테이블은 경로 때문에 부른다(위 `NO_STATE_NODES` 주석).
    "transit_gateway_route_table",
    "rds_db_instance",
    "ecs_service",
    "sagemaker_notebook_instance",
    "sagemaker_domain",
    "sagemaker_endpoint",
    "ec2_instance_connect_endpoint",
    # W3 — 2차 수집기 3종(ASG 는 위 주석대로 부르지 않는다).
    "ec2_instance",
    "eks_cluster",
    "network_firewall",
    # 2.6.0 — Site-to-Site VPN 3종.
    "vpn_gateway",
    "customer_gateway",
    "vpn_connection",
    # 2차 Direct Connect 3종(1층만 · 2026-10-13). DXGW 는 글로벌이다 — 리전마다 같은 목록을 다시 읽는다
    # (ADR 0010 §2 「coverage 는 리전마다」). 판정 칸은 `build_observation` 의 「글로벌 자리」 규칙.
    "direct_connect_connection",
    "direct_connect_virtual_interface",
    "direct_connect_gateway",
    # #114(2.9.0) — 1층만(탐침 값). 2층(`AWS/ElastiCache` 등)은 후속이다(`metrics.METRIC_PENDING_TYPES`).
    "elasticache_cluster",
    "rds_db_cluster",
    "opensearch_domain",
    "msk_cluster",
)

# coverage status 어휘 6개. 스냅샷이 어휘 밖 값을 들고 오면 `ok` 로 흘리지 않고 `error` 로
# 떨어뜨린다(불변식 ④ — 모르는 것을 성공으로 읽지 마라).
#
# 🔴 여기 `partial` 이 **없는 것이 의도다**(ADR 0006 O10). 「일부만 읽었다」는 status 값이 아니라
# **같은 (계정,리전)에 `ok` 행과 실패 행이 함께 있는 것**으로 표현한다 — 값 하나로는 「무엇을
# 읽었나」를 말하지 못해서 어느 대상에 상태를 붙여도 되는지 판정할 수 없다.
_COVERAGE_VOCAB = frozenset(
    {"ok", "denied", "throttled", "unsupported", "error", "skipped"}
)


@dataclass(frozen=True, slots=True)
class Observation:
    """🔴 **`partial` 은 저장하지 않고 사유에서 파생한다.**

    스키마는 `partial: true` 에 사유 **1개 이상**을 요구한다(`observation.schema.json:177-180`).
    둘을 따로 들면 「`partial=True` + 사유 없음」이라는 스키마 위반을 코드가 만들 수 있고, 실제로
    만들었다 — `_judge` 의 「모르는 값」과 `_no_rule` 이 그랬다(실측 2026-09-24: VPC `State` 를
    모르는 값으로 두고 문서를 검증하니 `'partial_reasons' is a required property`). 그 두 경로는
    **어떤 테스트도 partial=True 문서를 만들지 않아서** 통과가 아니라 미측정이었다(검증 규칙).
    파생으로 바꾸면 그 위반을 **표현할 수 없다**.
    """

    target_id: str
    target_kind: str
    status: str
    why: str | None
    evidence: dict[str, Any] | None
    error_count: int | None
    partial_reasons: tuple[Reason, ...] = ()
    # 🔴 **`evidence` 안이 아니라 행 자신의 칸이다**(`observation.schema.json` `drops`) — 렌더러가
    # `row.drops` 로 읽어 「드롭 N」을 그린다 — 선 배지(`renderer/flow.js` `wireBadge`)와 선 패널(`showWirePanel`).
    # `None` = 「못 쟀거나 드롭 지표가 없다」이고 그때는 **키를 만들지 않는다**(아래 `to_dict`).
    drops: int | None = None
    # 라우트 행 전용 · 서브넷별 흐름 로그 근거(스키마 `by_subnet` · ADR 0008 「D4b 개정」 ④). 행 `status` 는 1층
    # 그대로다. 비어 있으면 키를 만들지 않는다 — 흐름 로그를 끈 문서는 U6 까지와 바이트가 같아야 한다.
    by_subnet: tuple[dict[str, Any], ...] = ()
    # LB 행 전용 · 대상 수(스키마 `targets` · ADR 0006 W3②). 못 읽었으면 `None` 이고 키를 만들지 않는다 —
    # 「대상 0개」(읽었는데 없다)와 「못 읽음」이 갈린다.
    targets: dict[str, int] | None = None
    # DX VIF 행 전용 · BGP 피어 수(스키마 `bgp` · ADR 0006 §O7 DX 행). 못 읽었으면 `None` 이고 키를 만들지 않는다 —
    # 판정(`status`)은 탐침이 접은 값 그대로다. 이 칸은 「몇 개 중 몇 개」만 더한다(화면 「BGP 일부 down 1/2」).
    bgp: dict[str, int] | None = None
    # VPN 연결 행 전용 · 터널별 상태(스키마 `tunnels` · 1.17.0 · 사용자 결정 2026-10-24 「콘솔의 터널 세부 정보」). 판정은
    # 탐침이 접은 값 그대로다 — 이 칸은 「어느 터널이 DOWN 인가」만 더한다. 못 읽었으면 빈 튜플이고 키를 만들지 않는다.
    tunnels: tuple[dict[str, Any], ...] = ()
    # 1층 값이 「지우는 중」이었다(스키마 `deleting` · 1.12.0 · 사용자 결정 2026-10-21 QA C). `status` 는 `none` 이다.
    # 🔴 이력이 **이 칸으로** 「삭제가 만든 전이」를 가린다(`history.py`) — `why` 글자로 가리면 문구를 고치는 날 조용히 죽는다.
    # 거짓이면 키를 만들지 않는다 — 1.11.0 문서와 바이트가 같아야 한다.
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
        # 비어 있으면 키를 만들지 않는다 — `[]` 는 「사유가 없는 partial」로 읽힐 자리를 남긴다.
        if self.partial_reasons:
            doc["partial_reasons"] = [r.to_dict() for r in self.partial_reasons]
        # 🔴 **`0` 과 「키 없음」만 쓴다.** 스키마는 `null` 도 허용하지만 `null` 과 부재가 **같은 뜻**
        # 이라(「못 읽었거나 드롭 지표가 없다」) 두 표현을 다 쓰면 읽는 쪽이 없는 차이를 찾는다.
        # 진짜 구분은 `0`(쟀고 0 이었다) ↔ 그 밖이다(ADR 0007).
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
        """2층 결과를 얹는다. `evidence` 를 **덮지 않고** 2층 칸만 채운다.

        `observed_on` = 지표를 **실제로 잰 대상**(ADR 0008 D8-2). `target_id` 와 다르면 그 행은
        B(연역)이고 렌더러가 배지로 그렇게 말한다. 🔴 같을 때는 키를 만들지 않는다 — 모든 행에
        자기 id 를 적으면 「연역」 표시가 의미를 잃는다.

        `first_flow_at`·`last_flow_at` = 창 안에서 그 지표가 0 이 아니었던 첫·마지막 분(ADR 0008
        D6 보강). 🔴 모르면 키를 만들지 않는다 — 「모름」의 바이트 표현을 부재 하나로 둔다(옛 문서도
        부재다).
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
    """상태 단 속성의 다섯 갈래(헬스 체크 꺼짐 · 등록 중 · 빼는 중 · 사용되지 않음 · 그 밖의 unavailable) → 관측 키(1.15.0 · #137).

    🔴 다섯이 다 있을 때만 옮긴다 — 하나라도 없으면 `{}`(옛 상태 단 · 일부만 옮기면 화면이 모르는 갈래를 0 으로 읽는다).
    판정은 이 수를 보지 않는다 — 헬스 체크를 끈 Lambda 대상은 이상이 아니다.
    """
    got = {obs: attrs.get(attr) for attr, obs in TARGET_HEALTH_DETAIL}
    if not all(isinstance(v, int) for v in got.values()):
        return {}
    return {k: int(v) for k, v in got.items()}


def _with_targets(judged: Observation, node: Any) -> Observation:
    """LB 행에 대상 수를 얹고, 「살아 있는데 대상이 전부 비정상」이면 `down` 으로 내린다(ADR 0006 W3②).

    🔴 `down` 은 **넷이 다 맞을 때만**이다: `State.Code` 가 `active`(1차 판정 `up`) · 대상이 있다 ·
    정상 0 · AWS 가 비정상이라고 한 것이 1개 이상. 등록 중(`initial`)뿐인 LB 를 `down` 으로 그리면 배포할
    때마다 빨갛게 된다. 대상 상태를 못 읽었으면 수를 지어내지 않고 그 사유를 행에 붙인다 — 판정은
    `State.Code` 그대로다(「LB 가 죽었다」고 말할 근거가 없다).
    """
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
    """LB → 대상 선(2.7.0 · ADR 0006 §O7 엣지 표 `load_balancer_target` 행) — 그 선으로 묶인 등록들의 헬스.

    `targets` = 그 선의 등록 수(구조 `target_count`) + 상태 단 헬스 수(`healthy_target_count`·`unhealthy_target_count`).
    `down` 은 LB 노드와 같은 규칙이다: 등록이 있고 · 정상 0 · AWS 가 비정상이라고 한 것 ≥ 1. 등록 중(`initial`)뿐인
    선을 `down` 으로 그리면 배포할 때마다 빨갛게 된다.
    🔴 그 LB 의 대상 그룹 하나라도 헬스를 못 읽었으면 수를 짓지 않는다 — 행은 `up`(등록은 구조가 읽었다 · 「고장」을
    주장할 근거가 없다) + 그 사유(`partial`)이고 `targets` 키가 없다(LB 노드 `_with_targets` 와 같은 규칙).
    """
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
    """DX VIF 행에 BGP 피어 수를 얹는다(스키마 `bgp` · 1.6.0). **판정은 바꾸지 않는다** — 「전부 down = `down`」은
    탐침이 이미 접었다(`services/direct_connect_virtual_interface.py` `state`). 수가 없으면(상태 단이 아니거나
    `bgpPeers` 를 못 읽음) 행 그대로 — 수를 짓지 않는다.

    🔴 VIF 가 `available` 이 아니면(미수락·삭제 중 등) BGP 수를 싣지 않는다 — 탐침이 BGP 를 보지 않고 수명주기로
    판정한 행에 「BGP up 0/1」을 붙이면 사람이 판정 이유를 BGP 로 읽는다.
    """
    attrs = node.attributes or {}
    total = attrs.get("bgp_peer_count")
    if total is None or not str(attrs.get("state") or "").startswith("available"):
        return judged
    return replace(
        judged, bgp={"total": int(total), "up": int(attrs.get("bgp_up_count") or 0)}
    )


def _with_tunnels(judged: Observation, node: Any) -> Observation:
    """VPN 연결 행에 터널별 상태를 얹는다(스키마 `tunnels` · 1.17.0). **판정은 바꾸지 않는다** — 탐침이 이미 접었다
    (`services/vpn_connection.py` `state`). 줄이 없으면(상태 단이 아니거나 `VgwTelemetry` 를 못 읽음) 행 그대로 — 짓지 않는다.
    """
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


# 🔴 「판정할 규칙이 없다」의 사유 코드. 열거(`observation.schema.json:379-387`)에 「모르는 값」이
# 없고 그 열거는 topology 와 **바이트 동일**해야 하므로(두 파일 + 테스트 2개가 묶여 있다) 새 코드를
# 만들지 않는다. `api_error` 는 「그 API 의 응답을 우리가 쓸 수 없었다」로 읽으면 참이다.
_NO_RULE_CODE = "api_error"


def _evidence(
    api: str,
    *,
    metric: str | None = None,
    datapoints: int | None = None,
    window_seconds: int | None = None,
) -> dict[str, Any]:
    """1층 근거. 2층 칸은 `MetricLayer` 가 있을 때만 채워진다(기본 off = ADR 0006 D4)."""
    return {
        "api": api,
        "metric": metric,
        "datapoints": datapoints,
        "window_seconds": window_seconds,
    }


def _association_evidence(source_type: str | None) -> bool | None:
    """`True`=판정한다 · `False`=`none` 을 낸다 · `None`=출처를 모른다(결함).

    세 번째 값이 있는 이유: 나중에 다른 수집기가 `association` 엣지를 만들면 근거 유무를 모른다.
    그때 `False` 로 떨어뜨리면 「AWS 가 상태를 안 준다」는 **거짓말**이 되고, `True` 로 떨어뜨리면
    근거 없이 `up` 이 된다. 둘 다 아니라고 말해야 개발이 불린다.
    """
    return ASSOCIATION_EVIDENCE.get(source_type or "")


def _judge(rule: _Rule, value: Any, target_id: str, kind: str) -> Observation:
    # 비교는 casefold 로 하고 사람에게는 **AWS 가 준 그대로** 보인다(콘솔의 `CREATING` 을 `creating` 으로
    # 바꿔 적으면 콘솔에서 찾을 수 없다 — W3 에서 대문자 값 종류가 처음 들어왔다).
    raw = value
    value = value.casefold() if isinstance(value, str) else value
    if value in rule.up:
        # 접힌 값 중 `up` 인 것(VPN 「터널 일부 DOWN」)은 이유를 말한다 — 초록이지만 이중화가 없다는 사실을
        # 화면이 말해야 한다. AWS 원문 `up` 값에는 이유가 없다(정상에 사족을 달지 않는다).
        return Observation(
            target_id, kind, "up", _FOLDED_WHY.get(value), _evidence(rule.api), 0
        )
    if value in rule.deleting:
        # 🔴 지우는 중 = 「이상」이 아니다(사용자 결정 2026-10-21 · QA C) — 고객이 일부러 지운 자원이 빨간 「끊김」을 남기면
        # 장애로 읽힌다. 「정상」도 아니다(지금 받는다는 보장이 없다 — W3 규칙 2). 그래서 판정하지 않는 칸(`none`)에
        # 「삭제 중」이라는 사실을 붙인다. `partial` 이 아니다 — 근거가 모자란 것이 아니라 AWS 가 정확히 말했다.
        # `error_count` = 0 — 에러가 아니다(`null` 이면 화면이 「세지 못했다」를 말한다).
        return Observation(
            target_id,
            kind,
            "none",
            # 🔴 화면 「사유」 칸에 그대로 나온다 — 고객 말(합니다체 · docs/qa/copy-a.md ⑬ · #130).
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
    # 🔴 읽었지만 판정할 규칙이 없다. `up` 으로 삼키면 고장을 정상으로 그리고, `down` 으로
    # 삼키면 거짓 경보다. 둘 다 사람을 오판하게 한다 → 「모른다」를 말한다(캔버스 §H1).
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
    """§O7 규칙 1 의 유일한 예외 — `route_table` 노드에는 상태 필드가 없고 개별 route 만 든다.

    구조 수집기가 `Routes[].State` 를 `blackhole` 불린으로 접어 뒀다(실측: `False` 21건).
    🔴 **이것이 제품에서 가장 값비싼 상태다** — 구조만 보면 이어져 보이는데 실제로는 끊긴
    유일한 경우이므로, 키가 없으면 `up` 으로 때우지 않고 「모른다」를 낸다.

    🔴 **알려진 한계 2개 — 둘 다 구조 수집기의 성질이고 여기서 고칠 수 없다**(실측 2026-09-23):
    ① `services/route_table.py:81-90` 은 같은 (라우트테이블, 대상) 의 경로들을 **엣지 하나로
       합치고** `blackhole` 을 **OR** 한다. 그래서 같은 NAT 로 가는 경로가 둘이고 하나만
       blackhole 이면 **정상 경로까지 `down` 으로 보고된다**(실증: `10.98.0.0/16` active +
       `10.99.0.0/16` blackhole → 엣지 1개 `blackhole: True`). 어느 목적지가 죽었는지는
       `attributes["destinations"]` 를 사람이 봐야 알 수 있다 → `why` 에 그 목록을 적는다.
    ② 대상이 **삭제돼** blackhole 이 된 가장 흔한 경우는 대상 노드가 없어 엣지가 **아예 만들어지지
       않는다**(`collect.py:352-355` unresolved_refs) → 화면·관측에서 통째로 사라진다.
    """
    if source_type == "transit_gateway_route_table":
        # 🔴 TGW 표의 경로 선은 `State` = `active` 인 경로에서만 만들어진다(`services/
        # transit_gateway_route_table.py` 의 `_route_edges` — 블랙홀은 갈 곳이 없어 노드 속성).
        # 그래서 이 선에는 `blackhole` 키가 **원래 없다** — VPC 경로의 「(개발)」 분기로 보내면 거짓이다.
        # 🔵 알려진 한계: 블랙홀이 된 경로는 선이 **사라진다**(VPC 경로 한계 ② 와 같다) — `down` 은 못 낸다.
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
            # 2.13.0 — 구조 수집기가 이 선의 **죽은 목적지만** 따로 싣는다(위 한계 ① 해소) → 어느 목적지인지 말한다.
            return Observation(
                edge_id,
                "edge",
                "down",
                f"경로가 blackhole 입니다 — 대상이 사라졌는데 경로만 남았습니다. 이 선의 목적지 중 "
                f"[{', '.join(str(d) for d in dead)}] 가 blackhole 입니다",
                _evidence(ROUTE_API),
                1,
            )
        # 🔴 「이 선의 목적지 **중 하나 이상**」이다 — 위 한계 ① 때문에 「전부」라고 쓰면 거짓이 된다(옛 구조 문서).
        destinations = attributes.get("destinations") or []
        listed = (
            ", ".join(str(d) for d in destinations) if destinations else "목적지 미기록"
        )
        return Observation(
            edge_id,
            "edge",
            "down",
            # 화면 「사유」 칸에 그대로 나온다 — 고객 말(합니다체) · 마크다운 강조(`**`)는 화면에 별표로 찍힌다.
            f"경로가 blackhole 입니다 — 대상이 사라졌는데 경로만 남았습니다. 이 선의 목적지 "
            f"[{listed}] 중 하나 이상이 blackhole 이며, 목적지별 상태를 수집하지 않아 "
            "어느 목적지인지는 알 수 없습니다",
            _evidence(ROUTE_API),
            1,
        )
    return Observation(edge_id, "edge", "up", None, _evidence(ROUTE_API), 0)


def _no_means(target_id: str, kind: str, why: str) -> Observation:
    """`none` 행 — 항목은 만들고 근거는 없다(영원히 관측 불가이므로 `evidence` 도 `null`).

    🔴 `partial` 이 아니다 — 근거가 **불완전한** 것이 아니라 근거가 **존재하지 않는다**. 이것을
    partial 로 적으면 「스택 B 를 배포하면 보이나?」의 답이 「예」처럼 보인다(§H1 회색 vs `?`).
    """
    return Observation(target_id, kind, "none", why, None, None)


def _no_rule(target_id: str, kind: str, type_name: str) -> Observation:
    """§O7 에 행이 없는 타입. 조용히 건너뛰면 그 화면이 「관측 불가」와 구별되지 않는다."""
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
    """1층 coverage 를 **접지 않고** (계정, 리전, 종류)로 그대로 옮긴다.

    🔴 이전 판(`_fold_describe`)은 (계정,리전)으로 접고 「나쁜 쪽이 이긴다」를 적용했다. 그
    접기가 「일부만 읽었다」를 지웠다 — 한 종류라도 거부되면 자리 전체가 `denied` 가 되어,
    **실제로 읽은** 종류의 상태를 주장할 근거까지 사라졌다(실측 2026-09-23: 역할 정책이 14개
    액션 중 4개만 허용 → VPC 는 읽혔는데 자리째 침묵). ADR 0006 O10 이 종류축을 되살렸고,
    층축(`layer`)은 그대로 살아 있다 — 2층은 여전히 층 단위로 죽는다.

    `region is None` 행은 건너뛴다(리전 발견 실패 같은 계정 단위 사실이고, 관측 대상에는
    리전이 반드시 있다). 어휘 밖 status 는 `error` 로 떨어뜨린다.
    """
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
    """id 의 리전 자리가 `global` 인 노드(DXGW · CloudFront — ADR 0010 §2). 계정 컨테이너(`account/<id>`)는 아니다."""
    parts = target_id.split("/")
    return "|" not in target_id and len(parts) > 3 and parts[2] == GLOBAL


def global_home(
    describe: dict[tuple[str, str, str], tuple[str, Any]],
    account_id: str,
    resource_type: str,
) -> tuple[str, str] | None:
    """🔴 **글로벌 노드의 자리** = 그 계정에서 그 종류의 describe 가 `ok` 인 리전 중 **이름순 첫 리전**.

    DXGW 는 `region` 이 null 이지만 호출은 리전마다 일어나고 **어느 리전에서 읽어도 같은 목록**이다
    (ADR 0010 §2). 그래서 「읽었나」는 「그 계정의 어느 한 리전이라도 그 종류를 `ok` 로 읽었나」이고, 그 칸
    **하나**에 행을 센다(`item_count` — 리전마다 세면 하나가 리전 수만큼 늘어 원장 등식이 깨진다).
    한 리전이 거부되고 다른 리전이 성공하면 노드는 판정되고 거부 칸은 `denied` 로 남는다 — 그게 맞다.
    전부 실패면 `None` = 판정하지 않는다(불변식 ④ · 그 사실은 coverage 가 말한다).
    🔴 이름순인 이유: scope 순서·응답 순서에 기대면 같은 입력에서 칸이 흔들린다(불변식 ②).
    🔵 `coverage.region` 이 null 인 단일 호출 글로벌 서비스(CloudFront)는 여기서 다루지 않는다 —
    `_describe_by_type` 이 그 행을 건너뛰므로 자리가 `None` 이 된다(CloudFront 는 지금 「상태 없음」이다).
    """
    regions = sorted(
        region
        for (account, region, kind), (status, _reason) in describe.items()
        if account == account_id and kind == resource_type and status == "ok"
    )
    return (account_id, regions[0]) if regions else None


# 2층 전용 엣지에서는 이 문장이 행의 `why` 가 된다(`_metric_only_edge`) — 선 정보창 「사유」 칸의 title 원문(#150) → 「~합니다」.
_MISSING_RESULT = (
    "2층 응답에 이 대상의 결과가 없습니다 — 쿼리 id 대응이 깨졌습니다(개발)"
)


def _metric_answer(
    layer: MetricLayer, target_id: str, cell: tuple[str, str, str]
) -> tuple[Verdict | None, Reason | None, bool]:
    """(판정 재료, 사유, **개발 결함인가**). 세 값 전부 `None`/False = 「이 대상엔 지표가 없다」.

    2층이 「확인 불가」인 이유가 네 갈래이고 화면에서 서로 달라야 한다:
    - **지표가 없는 종류·모양** → `(None, None, False)`. `planned` 에 없다는 것이 그 증거다.
      🔴 `readings` 만 보면 이 경우와 아래 마지막 경우가 **같은 빈 dict** 다.
    - **차원을 못 만들었다** → 개발 결함. 1층 응답에 있어야 할 값이 없다.
    - **그 칸이 막혔다·throttle** → API 사유. 사람이 권한을 고치면 보인다.
    - **물었는데 응답에 없다** → 개발 결함. 조용히 「지표 없음」으로 두면 사라진다.
    """
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
        # 🔴 **판정은 섰지만 근거가 덜 왔다.** 대상이 지표를 여러 개 갖게 된 뒤로는 한 계열만
        # 빠져도 나머지로 판정이 서기 때문에 위 `verdict is None` 이 이 경우를 못 잡는다. 사유를
        # 같이 돌려주고 판정은 그대로 쓴다 — 얹은 판정이 틀린 것은 아니고(불변식 ④) 근거가 얇다.
        return (
            verdict,
            Reason(code="api_error", api=METRIC_API, message=_MISSING_RESULT),
            True,
        )
    return verdict, None, False


def _report_reading(
    layer: MetricLayer, target_id: str, verdict: Verdict
) -> Reading | None:
    """`evidence.metric` 으로 적을 **그 지표의** 결과. 없으면 `None`.

    🔴 지표 이름과 숫자가 어긋나면 사람이 재현할 수 없다 — 드롭으로 `down` 을 말했으면
    그 숫자는 **드롭의** 지점 수여야 하고, 0 을 적으면 「드롭도 없었다」로 읽힌다.
    """
    metric = verdict.report_metric
    if metric is None:
        return None
    return layer.readings_for(target_id).get(metric)


def _report_points(reading: Reading | None) -> int | None:
    """`evidence.datapoints` — **이름 붙인 그 지표를 실제로 쟀을 때만** 숫자를 적는다.

    🔴 **0 을 적는 것은 「쟀고 0 이었다」는 주장이다**(ADR 0009 ④). 옛 판은 그 지표의 결과가
    없거나 빈 배열일 때도 `0` 을 적었고, 같은 대상의 **다른 계열**이 지점을 줬으면
    `verdict.measured` 가 참이어서 위쪽의 `null` 경로에도 걸리지 않았다. 화면에서는 그 칸이
    회색 확정 = 「이 구간에 흐름이 없었다」로 읽히는데, 실제로 일어난 일은 **그 지표를 한 점도
    받지 못한 것**이다 — 「트래픽이 없었다」와 「관측하지 못했다」가 한 색으로 뭉갠 자리다.
    """
    if reading is None or not reading.measured:
        return None
    return reading.nonzero


def _quiet_why(reading: Reading | None) -> str | None:
    """통과량을 못 쟀을 때 **사람이 다음에 무엇을 할지**를 정하는 한 줄(ADR 0009 D3).

    같은 「확인 불가」가 두 가지 조치로 갈린다:
    - 값 0 을 발행하지 않는 지표(대부분) → **고객이 켤 것도 우리가 고칠 것도 없다.** 트래픽이
      없었을 뿐일 수 있고, 그 사실과 관측 실패를 AWS 가 구별해 주지 않는다.
    - 값 0 도 발행하는 지표(`always`) → **우리 쪽을 의심해야 한다.** 차원 조합이 틀리면 에러가
      아니라 빈 배열이 오므로(§O7-1 함정 1) 이 조용함은 신고다.
    """
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
    """2층 판정을 행에 적는다 — ADR 0008 D1 의 A·B·D 와 「쟀고 0」이 여기서 갈린다.

    🔴 **「창 안에 통과량 0」은 `down` 이 아니다**(D1·D3). 트래픽이 없는 것은 장애가 아니므로
    `up` + `datapoints: 0` 이고, 그것과 `null`(안 쟀음)은 계속 다른 사실이다.
    🔴 **드롭 > 0 도 그 자체로는 `down` 이 아니다** — `Verdict.blocked` 가 통과 0 을 함께
    요구한다(D3). 통과와 드롭이 동시에 양수인 구간은 「흐르면서 일부를 버리는 중」이다.
    🔴 **통과 0 + 드롭 > 0 도 아직 부족하다**(정정 2026-09-26) — 그 드롭이 D3 표의 **차단 신호**로
    선언된 것이어야 한다(`MetricRef.blocking`). 갓 만든 NAT 은 트래픽 0 인데 생성 직후 3분에
    패킷을 버리므로, 이 조건 없이는 **정상인 새 NAT 이 빨갛게 된다**(랩 A1 실측 · 정정 5).

    🔴 **드롭 수는 네 경로 전부에 싣는다**(S2-2 ⓑ · 2026-09-26). 등급과 **독립적인 사실**이기
    때문이다 — `live`(흐르면서 일부를 버린다) · `down`(막혔다) · 「흐름 없음」(통과는 0 인데 버림은
    있었다) 어디서나 화면이 「드롭 N」을 말할 수 있어야 하고, 한 경로만 빼면 그 등급의 행에서 배지가
    **조용히 사라진다**. 첫 경로(하나도 못 쟀다)에서는 드롭도 정의상 `None` 이지만 같은 인자를
    넘긴다 — 경로마다 다른 규칙을 두면 나중에 어느 경로가 비었는지 코드를 읽어야 알게 된다.
    """
    metric = verdict.report_metric
    window = layer.window_seconds
    observed_on = layer.observed_on.get(obs.target_id)
    reading = _report_reading(layer, obs.target_id, verdict)
    points = _report_points(reading)
    drops = verdict.drops
    if not verdict.measured:
        # 🔴 **빈 배열은 `partial` 이 아니다.** 지표가 조용한 것과 지표를 못 읽은 것은 다른
        # 사실이고, 전자를 「근거 불완전」으로 적으면 화면이 「권한을 고치면 보인다」고
        # 거짓말한다(§H1 회색 vs `?`). 이름만 남기고 `datapoints` 는 `null`(= 안 쟀음)이다.
        return obs.with_metric(
            metric=metric, window_seconds=window, why=_quiet_why(reading), drops=drops
        )
    if verdict.flowing:
        return obs.with_metric(
            status="live",
            # 🔴 `api` 를 2층으로 바꾼다 — `live` 라고 말한 근거는 지표이고, 스키마도 `live` 에
            # `metric`·`datapoints ≥ 1` 을 요구한다(`observation.schema.json:164-175`).
            api=METRIC_API,
            metric=metric,
            datapoints=points,
            window_seconds=window,
            observed_on=observed_on,
            # 🔴 **`live` 에만 싣는다.** 이력이 「멈춘 분」을 이전 문서의 `last_flow_at` 에서,
            # 「시작한 분」을 이번 문서의 `first_flow_at` 에서 읽는다(`history.py` 의 `_occurred_at`).
            # 시각도 `metric` 으로 이름 붙인 **그 지표의** 것이다 — 이름과 시각이 어긋나면 사람이
            # 콘솔에서 재현할 수 없다(`_report_reading` 과 같은 이유).
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
        # 🔴 **들어왔는데 건너갔는지 모른다**(ADR 0008 정정 1-2) — 아래 「흐름 없음」으로 떨어지면
        # 들어온 트래픽을 없었다고 단정하고, 흐름으로 올리면 블랙홀이 초록이다. 수치를 적지 않는다
        # (`datapoints: null` = 판정에 쓸 숫자를 못 얻었다)과 그 이유를 사람 말로 남긴다.
        return obs.with_metric(
            metric=metric,
            window_seconds=window,
            why=verdict.arrival_why,
            observed_on=observed_on,
            drops=drops,
        )
    # 통과량 0 = 「이 창에 흐름 없음」(회색) — 단 **그 지표를 실제로 쟀을 때만** 그렇게 말한다.
    # 못 쟀으면 숫자가 `null` 이고 사유가 왜 못 쟀는지를 말한다(ADR 0009 D3).
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
    """1층 판정에 2층을 얹는다. 🔴 **`up` 에서만 올라간다.**

    `down` 은 1층이 확정했고(blackhole 경로 같은 것) 거기에 지표를 얹을 어휘가 없다 —
    「흐르는데 고장」을 그리려면 다섯 번째 어휘가 필요하고 ADR 0008 D1 이 그것을 금지한다.
    `none`(관측 수단 없음)에 지표를 얹지 않는 이유도 같다.

    🔴 2층이 `down` 을 **만들 수는** 있다(ADR 0008 D3) — 1층 `up` + 통과 0 + 차단 신호일 때다.
    옛 판은 2층이 `live` 만 만들 수 있었고, 그래서 지표가 「막혔다」고 말해도 화면은 정상이었다.
    """
    if not layer.enabled or obs.status != "up":
        return obs
    verdict, reason, _defect = _metric_answer(layer, obs.target_id, cell)
    if verdict is None:
        return obs if reason is None else obs.with_metric(reason=reason)
    row = _with_verdict(obs, layer, verdict)
    # 응답에서 계열이 빠졌으면 판정과 **함께** 사유를 남긴다(위 `_metric_answer` 의 🔴).
    return row if reason is None else row.with_metric(reason=reason)


def _metric_only_edge(
    edge_id: str, cell: tuple[str, str, str], layer: MetricLayer
) -> Observation | None:
    """`METRIC_ONLY_EDGES` 의 행 — **2층이 그 구간을 답했을 때만** 생긴다(ADR 0008 D8-1).

    🔴 **못 쟀으면 행이 없다**(D1 의 E). 여기서 `up` 을 만들면 근거 0 인 주장이 되고, 그것이
    이 ADR 이 폐기한 종단 추론의 형태다 — 화면은 그 구간을 「경로만 확인」으로 말해야 한다.
    🔴 예외는 **개발 결함** 둘(차원 실패·응답 누락)이다. 그때는 coverage 가 `ok` 라서 아무 데도
    드러나지 않으므로 `none` + 사유로 행을 만든다(`_no_rule` 과 같은 모양).
    """
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
    # 🔴 씨앗의 `evidence.api` 는 **2층**이다 — 이 행에는 1층 근거가 없고, 스키마가 `evidence` 에
    # `api` 를 요구한다(`observation.schema.json:196-201`). 1층 API 를 적으면 「Describe 로
    # 구간을 쟀다」는 거짓이 된다.
    row = _with_verdict(
        Observation(edge_id, "edge", "up", None, _evidence(METRIC_API), 0),
        layer,
        verdict,
    )
    return row if reason is None else row.with_metric(reason=reason)


def _tables_in_use(topology: dict[str, Any]) -> dict[str, str]:
    """서브넷 → 그 서브넷이 **실제로 쓰는** 라우트 테이블(구조 문서 기준 · ADR 0008 「D4b 개정」 ⑤-2).

    명시 연관(라우트 테이블 → 서브넷)이 정확히 하나면 그것, 없으면 같은 VPC 의 메인 테이블이다(메인 연관은 선을
    만들지 않는다 — `route_table.py:59`). 🔴 네트워크 ACL 연관도 같은 `association` 종류다 — source 가 라우트
    테이블인 것만 센다. 🔴 모르면(명시 연관 둘 · 메인 둘) 빼고, 뺀 서브넷의 증명은 붙지 않는다 — 옆 서브넷의
    기록으로 이 선을 초록으로 만드는 것보다 근거 없는 선이 낫다.
    """
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
    """IGW 카드·피어링 선 — 서브넷(또는 VPC) 증명에서 모은 연역(B). 수 = 짝 합 · 본 곳 = 가장 작은 id."""
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
    """판독 결과를 행에 붙인다(ADR 0008 「D4b 개정」 ⑤-2). 돌려주는 것 = 새 행 목록 + 구조에 없어 버린 것.

    🔴 **흐름 로그는 `live` 만 만든다**(②). 1층이 `up` 인 행에만 올라가고, `down`·`none`·행 없음은 그대로 둔다.
    유일한 예외 = 서브넷 항목의 `up`(「안 흐른다」 · D4c ④) — 행 상태는 그대로이고 `by_subnet` 에만 적는다.
    🔴 판독기는 **상태 스냅샷**으로 귀속했다 — 여기서는 **구조 문서**(관측이 묶이는 쪽)로 다시 거른다. 두 장이
    어긋나면 증명을 버리고 버린 대상에 넣는다(조용히 삼키지 않는다).
    """
    window = reading.window_seconds
    by_id = {obs.target_id: obs for obs in observations}
    edges = {e["id"]: e for e in topology["edges"]}
    nodes = {n["id"]: n for n in topology["nodes"]}
    tables = _tables_in_use(topology)
    dropped: list[str] = []

    # ① 라우트 행 `by_subnet` — 서브넷별로만. 행 상태에 쓰면 같은 테이블의 서브넷 선이 전부 초록이 된다(④).
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
                # B — 경로 번호 4·5 는 「피어링」까지만 말한다. 어느 `pcx-` 인지는 최장 일치로 가렸다.
                evidence["observed_on"] = proof.observed_on
            items.append(
                {"subnet_id": proof.observed_on, "status": "live", "evidence": evidence}
            )
            backing.setdefault(proof.target_id, []).append(proof)
        by_id[route_id] = replace(by_id[route_id], by_subnet=tuple(items))

    # ①-2 「안 흐른다」(D4c ④) — 같은 서브넷에 `live` 항목이 있으면 붙이지 않는다(조건 4 가 막지만 두 번 지킨다).
    #     IGW 카드·피어링 선은 받치지 않는다 — 한 서브넷의 침묵은 그 대상의 침묵이 아니다.
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

    # ② IGW 카드 — 실제로 쓴 서브넷 항목에서만(스키마 교차 검사 ⑤).
    for target_id in sorted(backing):
        row = by_id.get(target_id)
        if (
            target_id.startswith("internet_gateway/")
            and row is not None
            and row.status == "up"
        ):
            by_id[target_id] = _flow_live(row, backing[target_id], window)

    # ③ 피어링 선 — 서브넷 필드 없는 VPC 로그의 증명은 세 조건이 다 참일 때만 받친다(⑤-2).
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
        # 🔴 씨앗의 `api` 는 흐름 로그 호출이다 — 이 행에 1층 근거는 없다(피어링 노드가 든다).
        seed = Observation(edge["id"], "edge", "up", None, {}, 0)
        by_id[edge["id"]] = _flow_live(seed, proofs, window)

    # ④ TGW 어태치먼트 선(D4c ③) — 건넌 어태치먼트의 `attachment` 선. → TGW 는 A · → VPC 등은 B(본 곳 = 어태치먼트).
    #    1·2층이 `up` 인 행만 — `arrival_unproven`·데이터포인트 0 도 왕복이 이긴다. 근거는 **새로** 쓴다(지표 근거와
    #    섞으면 「흐름 로그 N건」 옆에 지표 이름이 남는다).
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
    """출처 행에 로그 줄을 붙인다(ADR 0008 「D4b 개정」 ⑤-2 · 스키마 `sources[].resources`)."""
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
        # 🔴 판독기가 이미 id 순으로 주지만 그 순서에 기대지 않는다 — 넘겨받은 순서가 바이트가 되면 불변식 ② 가 호출자 몫이 된다.
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
    """관측 문서 + **구조 문서에 없어서 버린 대상 id 목록**을 돌려준다.

    `flow_logs` = 흐름 로그 판독 결과(S5 · 기본 없음). 없으면 문서에 흐름 로그 키가 하나도 없다. 🔴 2층과 출처
    탐지가 둘 다 켜져 있어야 한다(ADR 0008 「D4b 개정」 ⑤-2) — 아니면 `ValueError`.

    🔴 매달린 참조는 「없어진 리소스에 상태가 붙은 그림」이고 조용히 틀린 그림이다
    (`schema/tests/test_observation_schema.py` 의 `dangling_observations`). 그래서 이번에 읽은
    것 중 구조 문서에 **없는** 대상은 넣지 않는다 — 그 사실 자체는 `changes[].resource_added`
    의 재료이고 S4 의 몫이다. 버린 개수를 호출자가 사람에게 말해야 한다(삼키지 마라).

    항목의 출처가 **둘**이다. 상태가 있는 것은 방금 읽은 `snapshot` 에서, `none` 밖에 못 내는
    것(SG·NACL·SG 참조)은 **구조 문서에서** 온다 — 후자는 AWS 를 부르지 않으므로 이번에
    읽히지 않았고, 그래서 위치·존재를 구조 문서가 말해 준다.
    """
    known_nodes = {n["id"] for n in topology["nodes"]}
    known_edges = {e["id"] for e in topology["edges"]}
    # LB → 대상 선의 판정이 그 LB 의 헬스 거부 사유를 본다(`_observe_lb_target`)
    nodes_by_id = {n.id: n for n in snapshot.nodes}
    describe = _describe_by_type(snapshot)
    layer = metrics if metrics is not None else MetricLayer.off()
    scan = sources if sources is not None else SourceScan.not_scanned()
    if flow_logs is not None and not layer.enabled:
        # 계약이 `layers.flow_log` 에 `metric: true` 를 요구한다 — 「metric=false 면 live 없음」이 흐름 로그 행에도 참이어야 한다.
        raise ValueError("흐름 로그 판독 결과는 2층(지표)이 켜져 있을 때만 붙인다")
    # 사용률은 2층과 **독립**이다(스키마 1.5.0 · ADR 0011 D7 개정 2026-10-06) — 제 지표·한도를 따로 읽으므로 2층을 꺼도 붙인다.
    if utilization is not None and any(
        i["target_id"] not in known_nodes for i in utilization
    ):
        # 사용률은 구조 문서에서 계획한다(`utilization.plan_*`) — 여기 걸리면 호출자가 다른 구조 문서를 줬다.
        raise ValueError("사용률 대상이 구조 문서에 없다")
    if flow_logs is not None and not scan.scanned:
        raise ValueError(
            "흐름 로그 판독 결과는 출처를 탐지했을 때만 붙인다(판독기의 입력이 탐지 결과다)"
        )

    # 위치는 **구조 문서**가 정본이다 — 스냅샷에 없는 대상(SG·NACL)도 셈해야 한다.
    place: dict[str, tuple[str, str] | None] = {}
    node_type: dict[str, str] = {}
    for raw in topology["nodes"]:
        region = raw.get("region")
        if region:
            place[raw["id"]] = (raw["account_id"], region)
        elif is_global_node_id(raw["id"]):
            place[raw["id"]] = global_home(describe, raw["account_id"], raw["type"])
        else:
            place[raw["id"]] = None  # 계정 컨테이너 — 칸에 속하지 않는다
        node_type[raw["id"]] = raw["type"]
    raw_nodes = {raw["id"]: raw for raw in topology["nodes"]}
    for raw in topology["edges"]:
        # 🔴 엣지의 자리 = 그 엣지를 낸 응답의 자리 = 보통 source. 병합된 피어링의 상대편 선만
        #    도착 TGW 의 자리다 — `metrics.plan()` 이 그 자리에서 재므로 실패도 그 칸에서 찾는다.
        source = raw_nodes.get(raw["source"], {})
        target = raw_nodes.get(raw["target"], {})
        far = lands_on_another_tgw(
            raw["type"],
            (source.get("attributes") or {}).get("transit_gateway_id"),
            target.get("type"),
            target.get("native_id"),
        )
        place[raw["id"]] = place.get(raw["target"] if far else raw["source"])

    # 대상 → 「그 대상을 읽어 온 응답의 종류」. 노드는 자기 타입이고, 엣지는 위
    # `EDGE_SOURCE_RESOURCE`(+ association 은 source 노드 타입)다.
    resource_of: dict[str, str] = dict(node_type)
    for raw in topology["edges"]:
        if raw["type"] in EDGE_RESOURCE_FROM_SOURCE:
            source_type = node_type.get(raw["source"])
            if source_type is not None:
                resource_of[raw["id"]] = source_type
        elif raw["type"] in EDGE_SOURCE_RESOURCE:
            resource_of[raw["id"]] = EDGE_SOURCE_RESOURCE[raw["type"]]

    # 「이번에 그 자리에 도달했다」 = 그 (계정,리전)에 `ok` 행이 하나 이상. `none` 항목의
    # 조건이다(ADR 0006 O10 — `none` 은 권한과 무관하게 참인 판단이므로 종류별로 볼 필요가 없고,
    # 다만 **아무것도 못 읽은 자리**에는 항목을 만들지 않는다 = 스키마 규칙 2).
    reached = {
        (account_id, region)
        for (account_id, region, _type), (status, _reason) in describe.items()
        if status == "ok"
    }

    def readable(target_id: str) -> bool:
        """🔴 **그 대상의 종류를** 그 (계정,리전)에서 읽었나. 아니면 판정을 주장하지 않는다.

        계약이 명시적이다: 그 칸의 describe 가 `ok` 가 아니면 `up`·`down`·`live` 를 주장할 수
        없다(`docs/contract-seams.md` 「이음매 3」 = 「모름」 → 「고장」 순서, 검사
        `schema/tests/test_observation_schema.py` 의 `claims_without_reading`).

        🔴 **자리 단위가 아니라 종류 단위인 것이 O10 이다.** 자리 단위로 막았던 판(2026-09-23
        오전)은 12종 중 4종만 읽힌 실계정에서 **실제로 읽은 VPC·TGW첨부까지 버렸다** — 사람이
        화면에서 이미 확인된 것을 다시 확인해야 했다. 종류 단위면 읽은 종류는 남고 못 읽은
        종류만 침묵한다.

        🔴 종류를 **모르는** 대상(구조 문서에 없거나 엣지 매핑이 없는 것)은 읽지 않은 것으로
        취급한다 — 기본값을 「읽었다」로 두면 새 타입이 들어올 때 조용히 상태를 주장한다.
        """
        at = place.get(target_id)
        resource_type = resource_of.get(target_id)
        if at is None or resource_type is None:
            return False
        return describe.get((at[0], at[1], resource_type), ("", None))[0] == "ok"

    def cell_of(target_id: str) -> tuple[str, str, str]:
        """그 대상의 coverage 칸 = (계정, 리전, 종류).

        🔴 `readable()`·`describe`·`MetricLayer.failures` 와 **같은 키**여야 한다 — 갈라지면
        2층 실패를 엉뚱한 칸에서 찾아 「막혔는데 조용하다」가 된다. 그래서 `metrics.plan()` 도
        엣지의 종류를 source 노드에서 뽑는다(`_resource_of_edge`).
        """
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
            # 🔴 상세 응답이 거부돼 상태 값이 없다 — 「규칙이 없다(개발)」가 아니라 「못 읽었다」다
            # (§O7 「W2 → W3」 규칙 3). 사유는 노드가 이미 들고 있는 그대로 옮긴다.
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
        # 🔴 노드와 **같은** 검사가 필요하다(노드 쪽만 막았을 때 거부된 자리에 `route … up` 이
        # 남았다 — 2026-09-23). 이 줄을 재는 검사는
        # `test_a_denied_route_table_silences_the_edges_it_would_have_made` 하나뿐이고, 그 검사는
        # 브레이크(`prove-breaks.sh` ㊿)가 FAIL 0을 내놓은 뒤에야 생겼다 — 그 전까지 이 게이트는
        # 통과가 아니라 미측정이었다. 다만 엣지의 종류는 **끝점이 아니라 그 엣지를
        # 만든 응답**이다(위 `EDGE_SOURCE_RESOURCE`) — NAT 가 거부돼도 route_table 을 읽었으면
        # 그 경로는 읽은 것이고, 반대로 route_table 이 거부되면 끝점이 다 읽혔어도 모른다.
        if not readable(edge.id):
            continue
        # 🔴 2층 전용 엣지(ADR 0008 D8-1). 1층 규칙을 **묻지 않는다** — 소유 자원의 `state` 가
        # 속성에 있어도 그것으로 판정하면 노드와 같은 근거를 두 번 그린다(위 `METRIC_ONLY_EDGES`).
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
                continue  # 아래 구조 패스가 `none` 으로 낸다
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

    # 🔴 `none` 항목은 구조 문서에서 만든다 — 단, **그 (계정,리전)에 이번에 도달한 경우에만**이다.
    # 스키마 규칙 2: 「못 읽은 (계정,리전)의 대상에는 항목을 만들지 않는다. `none` 을 쓰지 마라」
    # — 「영원히 관측 불가」와 「이번에 못 읽었다」가 화면에서 같은 `?` 가 되면 안 된다.
    # 종류축(O10)이 생겼어도 여기는 **자리 단위**다: SG·NACL 은 부르지 않으므로 그 종류의 행이
    # 애초에 없고, `none` 은 권한이 아니라 「AWS 가 그런 것을 보고하지 않는다」는 우리 판단이다.
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

    # 흐름 로그(S5 · ADR 0008 「D4b 개정」 ⑤-2). 🔴 로그가 붙은 대상이 구조에 없으면 `attached_to` 를 뺀다 —
    # 스키마 설명 그대로(「대상이 이 구조에 없으면 없다」). 로그 줄 자체는 남긴다: 켜진 로그를 숨기지 않는다.
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

    # 🔴 `item_count` 는 「그 (계정,리전,종류,층) 칸에서 판정한 대상 수」다. 그래서 **어느 칸에도
    # 속하지 않는 항목이 있다** — SG·NACL·SG 참조의 `none` 은 부르지 않은 종류이므로 describe 행이
    # 없다. 즉 `sum(item_count) ≤ len(observations)` 이고, 차이는 그 `none` 개수다
    # (`schema/tests/test_observation_schema.py` 가 그 등식을 잰다).
    #
    # 🔴 **2층 전용 엣지는 1층 칸에서 세지 않는다**(ADR 0008 D8-1). 그 행은 describe 가 만든 것이
    # 아니라 지표가 만든 것이다 — 세면 2층 토글을 켰다 끌 때마다 **1층의 숫자가 움직인다**.
    # 사람이 보는 결과: 로드밸런서 1대인데 「1층에서 3개를 읽었다」로 요약되고, 모니터링을 끄면
    # 같은 계정이 1개로 줄어든다.
    counts: dict[tuple[str, str, str], int] = {}
    for obs in observations:
        # 흐름 로그 전용 행도 같다 — 그 행은 흐름 로그가 만든 것이다(위 `FLOW_LOG_ONLY_EDGES`).
        if obs.target_id.split("|")[0] in METRIC_ONLY_EDGES | FLOW_LOG_ONLY_EDGES:
            continue
        at = place.get(obs.target_id)
        resource_type = resource_of.get(obs.target_id)
        if at is None or resource_type is None:
            continue
        key = (at[0], at[1], resource_type)
        if key in describe:
            counts[key] = counts.get(key, 0) + 1

    # 2층의 `item_count` = 「그 칸에서 **지표가 답을 준** 대상 수」. 빈 배열로 온 대상은 세지
    # 않는다 — 그것을 세면 「읽었다」와 「조용하다」가 같은 숫자가 된다(§O7-1 함정 1+3 의 계열).
    #
    # 🔴 **대상 단위로 센다**(`layer.answered()`). 한 대상에 지표가 여러 개가 되면서(ADR 0008 D2)
    # `readings` 는 (대상, 지표) 키가 됐다 — 그것을 그대로 세면 NAT 하나가 3으로 잡혀 「대상 수」가
    # 아니라 「쿼리 수」가 되고, 1층 `item_count` 와 비교할 수 없는 숫자가 된다.
    # 또 **행을 만들지 못한 대상은 세지 않는다**(구조 문서에 없어 버린 것) — 없는 그림의 근거를
    # 셈하면 두 문서가 갈라진다.
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
            # 🔴 1층이 막히면 대상 id 를 모르므로 **2층을 부르지 않았다**. 여기서 `ok` 를 적으면
            # 「읽었고 아무것도 없었다」는 거짓이 되고, describe 와 같은 실패 사유를 복사하면
            # 부르지도 않은 API 의 거부를 주장하게 된다. 안 한 것은 안 했다고 적는다.
            metric_status, metric_reason = "skipped", None
        elif layer.enabled and resource_type in METRIC_PENDING_TYPES:
            # 🔴 프로브를 아직 안 만든 종류(`metrics.METRIC_PENDING_TYPES`) — 부르지 않았다. `ok`+0 은
            # 「쟀고 조용하다」라는 거짓이다.
            metric_status, metric_reason = "skipped", None
        coverage.append(
            {
                "account_id": account_id,
                "region": region,
                "resource_type": resource_type,
                "layer": "describe",
                "status": status,
                "reason": reason,
                # 못 읽은 칸의 0 과 「읽었고 대상이 없다」의 0 은 다른 사실이다.
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
                # 2층은 기본 off(ADR 0006 D4) — 「막혔다」가 아니라 「안 켰다」다.
                # 2층도 종류마다 행을 낸다: 켜지면 NAT 지표는 되고 TGW 지표는 없는 식으로
                # **종류별로** 갈린다(그때 「층 단위로 죽는다」는 전 행이 같은 값이 되는 것).
                "status": metric_status,
                "reason": metric_reason,
                # 지표가 **없는** 종류도 `ok`+0 이다(픽스처 정본). 「미지원」은 리전 축의 어휘이고
                # 「이 종류엔 지표가 없다」는 우리 판단이므로 실패로 적으면 사람이 권한을 고치러 간다.
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
            # 🔴 이 값이 관측을 구조에 묶는다 — 두 문서의 scope 가 갈라질 수 없는 이유다.
            "topology_collected_at": topology["run"]["collected_at"],
            # 짝은 **내용 지문**이 우선이다(`serialize.content_digest`). 옛 구조 문서에는 없으므로
            # 키째 뺀다 — 그러면 화면은 위 시각으로 짝을 맞춘다(모름을 「같다」로 읽지 않는다).
            **(
                {"topology_content_digest": topology["run"]["content_digest"]}
                if topology["run"].get("content_digest")
                else {}
            ),
        },
        "scope": {
            "account_ids": list(topology["scope"]["account_ids"]),
            "regions": list(topology["scope"]["regions"]),
            # 🔴 「요청」이다 — 관측이 **부른** 종류(위 `OBSERVED_RESOURCE_TYPES` 로 수집된
            # 스냅샷의 scope). coverage 는 「결과」이므로 요청이 없으면 「그 종류가 실패했다」와
            # 「그 종류를 안 불렀다」가 구별되지 않는다(ADR 0006 O10).
            "resource_types": sorted(snapshot.scope.resource_types),
        },
        # `flow_log` 는 켰을 때만 키가 있다 — 꺼짐의 표현은 부재 하나다(스키마 `layers.flow_log`).
        "layers": {
            "describe": True,
            "metric": layer.enabled,
            **({"flow_log": True} if flow_logs is not None else {}),
            **({"utilization": True} if utilization is not None else {}),
        },
        # 🔴 `layers` 와 **다른 축**이다(ADR 0009 D2): `layers` 는 «우리가» 그 층을 켰나이고
        # `sources` 는 «고객이» 그 출처를 켰나다. 빈 배열은 「전부 꺼짐」이 아니라 「탐지하지
        # 않았다」이고, 그때 화면은 알약을 띄울 자격이 없다(꺼졌다고 말할 근거가 없다).
        "sources": (
            _merge_flow_log_sources(scan.to_list(), flow_resources)
            if flow_logs is not None
            else scan.to_list()
        ),
        # 정렬은 여기 한 곳에서만 한다(불변식 ②).
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
            # 이력이 없으면 기준선은 이번 관측이다. S4 가 이전 문서를 받으면 여기가 바뀐다.
            "baseline_at": observed_at,
            "resolution_seconds": DEFAULT_RESOLUTION_SECONDS,
            "retention_seconds": DEFAULT_RETENTION_SECONDS,
            "truncated": False,
            "flap_collapse_threshold": DEFAULT_FLAP_COLLAPSE_THRESHOLD,
            "max_changes": DEFAULT_MAX_CHANGES,
        },
        # S4 의 몫. 🔴 빈 배열이 「변경 없음」을 뜻하지 않는다 — 기준선이 이번 관측이므로
        # 비교 대상이 아예 없다는 뜻이다(D3 ②「빈 배열은 모름」과 같은 계열).
        "changes": [],
        # 켰을 때만 키가 있다(`layers.utilization` 과 짝). 정렬은 `utilization.compute_utilization` 이 한다.
        **({"utilization": utilization} if utilization is not None else {}),
    }
    return document, sorted(dropped)
