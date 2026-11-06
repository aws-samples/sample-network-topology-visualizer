"""사용률(#27 · **ADR 0011 의 구현**) — 「얼마나 차 있나 · 한도에 부딪힌 적이 있나」.

흐름 판정(`metrics.py` · ADR 0008)과 **다른 축**이다. 판정은 「흐르나 · 막혔나」를 15분 창 하나로 말하고
status 를 만든다. 사용률은 status 를 바꾸지 않고 관측 문서의 최상위 `utilization` 에만 실린다(ADR 0011 D7).

세 갈래(D3):

- **rate**  — CloudWatch 의 값을 기간 6개(15분 · 1시간 · 24시간 · 1주 · 1달 · 3달)로 접는다. 배지 = 기간 안
  **가장 바빴던 단위**(D2) · 평균은 둘째 칸. 단위는 CloudWatch 보관 해상도가 정한다(1주까지 60초 · 1달 300 · 3달 3600).
- **events** — 거부·드롭 수. %가 아니라 사실이다(D1) — 기간마다 합만.
- **count** — 개수 한도. 기간이 없다 — 지금 값. 구조 문서(SG · NACL · 라우트 · VPC · TGW · DXGW)와 이번 상태
  스냅샷(서브넷 남은 IP)에서 센다 — CloudWatch 를 부르지 않는다.

🔴 **창의 끝은 관측 시각이다**(D6 · 불변식 ② — `collect.utc_now()` 가 유일한 wall-clock 예외다).
🔴 **긴 묶음(1주 이상)과 서비스 한도는 1시간마다** 다시 읽고 그 사이에는 직전 관측 문서의 값을 이월한다(D5 · D6).
   이월 판단은 입력(직전 문서의 `computed_at` · 이번 관측 시각)만으로 정해진다 — 같은 입력이면 같은 바이트다.
🔴 못 읽음 ≠ 0(D1 · 불변식 ④) — 못 읽은 measure 는 `unread` 를 싣고 값을 싣지 않는다.
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

# (기간 분, 단위 초) — ADR 0011 D2 · 스키마 `utilization_window`. 단위 = CloudWatch 보관 해상도
# (1분 기록 15일 · 5분 63일 · 1시간 455일).
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

# 한 번의 `GetMetricData` 응답이 담는 점의 상한(AWS) · 쿼리 수 상한. 점 상한을 넘기면 `NextToken` 이 온다 —
# 그래도 끝까지 따른다(아래 `_read_batch`).
MAX_POINTS_PER_CALL = 100_800
MAX_QUERIES_PER_CALL = 500

BITS_PER_BYTE = 8
GBPS = 1_000_000_000

# 값의 모양. 🔴 통계가 무엇이냐가 「가장 바빴던 단위」와 「평균」의 계산을 가른다.
#   sum   — 단위마다의 합(바이트 · 패킷) → 속도 = 합 × 배수 ÷ 단위 초 · 평균 = 기간 합 × 배수 ÷ 덮은 초
#   level — 이미 속도(DX `VirtualInterfaceBps*` = 평균 bit/s · RDS 처리량 = 평균 B/s) → 최대 = 값의 최대 ·
#           평균 = 점들의 평균(덮은 구간 안의 시간 평균)
#   (peak — 단위 안의 최대 · NAT·NLB `PeakBytesPerSecond` · ALB `CapacityUtilization` 는 2026-10-26 사용량 개정으로 뺐다:
#    합을 낼 수 없어 「얼마나 썼나」를 말하지 못한다 · NAT·NLB 는 바이트 합 지표로 바꿨다 — ADR 0011 개정)
VALUE_SUM = "sum"
VALUE_LEVEL = "level"

# 발행 조건(metrics.py 의 `reporting=` 과 같은 뜻). always = 매 단위 점이 온다(빈 단위 = 그 자원이 아직 없었다) ·
# nonzero = 값이 있을 때만 온다(빈 단위 = 0). 덮은 시간(D2 「어린 선」)이 여기서 갈린다.
# 🔴 #128 · 불변식 ④ — nonzero 라도 **기간 전체에 점이 하나도 없으면** 0 이 아니다. 트래픽이 정말 없었을 수도 있지만
#    새 자원 · 발행되지 않는 지표(ALB 대상 미등록 등)도 같은 빈 응답이다 → 그 창은 `no_points: true`(값 없음)로 싣고
#    값을 싣지 않는다. 점이 하나라도 있으면 그 사이 빈 단위는 그대로 0 이다(창 전체로 나눈다).
# 🔁 2026-10-26 사용량(사용자 결정 ④) — **사용량 측정**(바이트 · 요청 · 호출 = `RateSpec.empty_zero`)은 위 예외를 접는다:
#    빈 창 = 0. 「값이 있을 때만 보낸다」는 AWS 문서 문장 자체가 「없으면 0」이라는 뜻이고(ALB `ProcessedBytes` ·
#    Lambda `Invocations` · API GW `Count`), 화면이 「얼마나 썼나」를 묻는데 「값 없음」은 답이 아니다. 새 자원·미발행 지표와
#    가르지 못하는 대가는 그대로 남는다(ADR 0011 개정). 사건 수(거부 · 드롭 · 포트 할당 오류)는 #128 그대로 `no_points`.
REPORT_ALWAYS = "always"
REPORT_NONZERO = "nonzero"

# 서비스 한도(D3 · D5). (서비스 코드, 한도 코드, 문서 기본값). 🔵 코드는 AWS 문서의 콘솔 링크에서 읽었다 —
# 권한이 붙은 첫 실행에서 응답의 `QuotaName` 으로 대조한다(D5 미측정).
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

# 늘릴 수 없는 한도(`fixed`) · Service Quotas 에 없는 한도(`default_quota` + 코드 없음). 근거 = ADR 0011 D3.
FIXED_PROPAGATED_ROUTES = 100
FIXED_TGW_VPC_ATTACHMENT_BPS = 100 * GBPS  # 「Up to 100 Gbps each direction」 · AZ 마다
FIXED_TGW_VPC_ATTACHMENT_PPS = 7_500_000
FIXED_NFW_BPS = 100 * GBPS  # AZ 마다 · 「drops any traffic over the limit」
FIXED_DXGW_VIFS = 30
FIXED_DXGW_VGWS = 20
FIXED_DXGW_TGWS = 6
DEFAULT_TGW_ROUTES = 10_000

# 이 툴이 쓰는 권한(`docs/required-permissions.md` §1-5) — 템플릿 테스트가 대조한다.
REQUIRED_ACTIONS = (
    "cloudwatch:GetMetricData",
    "servicequotas:GetServiceQuota",
    "servicequotas:GetAWSDefaultServiceQuota",
)

# CloudFront 지표는 us-east-1 에만 있다(문서 「you must use the US East (N. Virginia) Region」) · 차원 `Region=Global`.
CLOUDFRONT_METRIC_REGION = "us-east-1"
# EC2 기본 모니터링은 5분 점이다(실측 2026-10-04: 60초로 물어도 점 간격 300). 1분으로 접으면 5분 합을 60으로
# 나눠 5배로 말한다 → EC2 는 단위를 300 보다 잘게 두지 않는다(상세 모니터링이어도 300 으로 묻는 것은 정확하다).
EC2_MIN_STEP = 300

_NFW_ENGINES = ("Stateful", "Stateless")
_ALWAYS_REPORTED_TGW = (
    REPORT_ALWAYS  # 「All metrics are always reported」(metrics.py 표)
)


# ─────────────────────────────────────────────────────────────────────────────
# 무엇을 읽나
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class Series:
    """CloudWatch 계열 1개. (계정, 리전)은 클라이언트를 고르고 나머지가 계열을 고른다."""

    account_id: str
    region: str
    namespace: str
    metric: str
    stat: str
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Cell:
    """나눌 칸 하나(방향 × AZ). 계열이 여럿이면 **시각마다 최대**로 합친다(방화벽 엔진 — 아래 `_nfw`)."""

    series: tuple[Series, ...]
    direction: str | None = None
    az: str | None = None


@dataclass(frozen=True, slots=True)
class RateSpec:
    target_id: str
    measure: str
    kind: str  # rate · events
    unit: str
    cells: tuple[Cell, ...]
    value: str  # VALUE_*
    factor: float  # 바이트 → 비트 = 8
    reporting: str
    min_step: int = 60
    limit: float | None = None
    limit_source: str | None = None
    limit_scope: str | None = None
    # 사용량(사용자 결정 2026-10-26 「사용률 = 사용량」 · 관측 1.18.0) — 창의 `total` = 기간 합(이 단위) · None = 합이 뜻이 없다
    total_unit: str | None = None
    # 「값이 있을 때만 보내는」 지표가 기간 전체에 점이 없으면 **0** 으로 읽는다(사용자 결정 2026-10-26 ④ — 「값 없음」이
    #   「확인하지 못함」처럼 보인다). #128 의 「값 없음」은 events(거부·드롭)와 이 표시가 없는 rate 에만 남는다.
    empty_zero: bool = False
    # 이 값에 섞인 것(화면이 문구로 알린다) — tunnel_health_checks = VPN 터널 점검·ARP·BGP 바이트(AWS 문서 「can report network
    #   usage even when the tunnel is down」)
    includes: str | None = None


@dataclass(frozen=True, slots=True)
class CountSpec:
    target_id: str
    measure: str
    used: int | None
    part: str | None = None
    # 한도: 고정 값이면 (값, 출처) · 서비스 한도면 quota=(서비스, 코드, 기본값) · 둘 다 없으면 「한도 ?」
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
    """차원 값이 하나라도 비면 `None` — 빈 값으로 부르면 「점 0개」가 와서 「흐름 없음」과 같아진다(§O7-1 함정 1)."""
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
    """DX 연결의 `bandwidth`(「50Mbps」 · 「10Gbps」) → bit/s. 모르는 모양이면 `None`(= 한도 ?)."""
    match = _BANDWIDTH.match(str(value or ""))
    if not match:
        return None
    number = float(match.group(1))
    return number * (GBPS if match.group(2).upper() == "G" else 1_000_000)


@dataclass
class _Graph:
    """구조 문서를 한 번만 훑어 만든 색인."""

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


# ─────────────────────────────────────────────────────────────────────────────
# rate · events — 노드 종류마다 무엇을 읽나(ADR 0011 D3)
# ─────────────────────────────────────────────────────────────────────────────


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
    """사용량 = 바이트(사용자 결정 2026-10-26). 배지·목록 = 기간 합(`total` · 바이트) · 가장 바빴던 단위 · 평균(bit/s).

    🔴 한도(분모)를 두지 않는다 — 「얼마나 썼나」이지 「얼마나 찼나」가 아니다(ADR 0011 D3 개정 2026-10-26).
    「값이 있을 때만 보내는」 지표는 빈 창 = 0(`empty_zero` · 결정 ④).
    """
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
    """요청·호출 수(바이트가 없는 서비스 — Lambda · API Gateway). 기간 합 = 횟수 · 가장 바빴던 단위 = 초당 횟수."""
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
        # 피어링 · DXGW · Connect · VPN — AZ 별 지표가 없다. 🔴 VPN 어태치먼트의 바이트가 그 VPN 의 사용량이다(결정 ⑤ —
        #   터널 지표 `TunnelData*` 는 점검 바이트 때문에 0 이 되지 않는다 · 아래 `_vpn` 은 VGW VPN 만 읽는다).
        return [
            _bytes(
                node,
                _two_way(
                    node, "AWS/TransitGateway", "Sum", "BytesIn", "BytesOut", base
                ),
                _ALWAYS_REPORTED_TGW,
            )
        ]
    # 🔴 AZ 차원 = **AZ ID**(실측). 어태치먼트의 서브넷 목록은 수집하지 않으므로 그 VPC 의 서브넷 AZ 를 전부 묻는다 —
    # 어태치먼트가 없는 AZ 는 점이 0개이고 고르는 데서 빠진다(「항상 보낸다」 지표라 있는 AZ 는 반드시 점이 온다).
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
    # 🔵 1분 점(실측 2026-10-04 · 60분에 59점) · Average = 그 분의 평균 bit/s. 발행 조건 문장은 문서에 없다 →
    # 점이 온 구간만 덮은 것으로 본다(always 취급 — 빈 분을 0 으로 채우지 않는다). 회선 대역폭 분모는 뺐다(2026-10-26).
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
    # 🔴 AZ 차원 = **AZ 이름**(실측). `Engine` 은 `Stateless` · `Stateful` 둘(list-metrics 2026-10-04).
    # 합하지 않고 시각마다 **큰 쪽**을 쓴다 — 상태 비저장 엔진이 모든 패킷을 먼저 받고 상태 저장 엔진은 그중 넘겨받은
    # 것만 받으므로 합하면 같은 바이트를 두 번 센다. 🔵 두 엔진 값이 겹치는지는 188 에서 둘 다 0 이라 미측정이다.
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
        # 🔴 `CapacityUtilization` 은 뺐다 — 188 의 ALB 5개가 트래픽과 무관하게 최소 20 · 최대 40(요청 0건 ALB 와 671건 ALB 가
        #   같다 · 실측 2026-10-26) → 사용량을 말하지 못한다(사용자 결정 2026-10-26 「ALB 는 ProcessedBytes」).
        #   `ProcessedBytes` 는 「There is a nonzero value」일 때만 온다(AWS 문서) → 빈 창 = 0(결정 ④).
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
        # 🔵 `ProcessedBytes` 는 0 도 보낸다(실측 2026-10-26 · 1438점 중 127점만 0 아님). 옛 `PeakBytesPerSecond` 는 거의 놀아도
        #   30~96 B/s 로 0 이 되지 않아 「쓰고 있음」처럼 보였다.
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
        # 🔵 188 에 GWLB 가 없어 미측정 — AWS 문서 기준(`AWS/GatewayELB` · `LoadBalancer = gwy/<이름>/<해시>`).
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
    # in = 목적지에서 받은 바이트(`BytesInFromDestination`) · out = 목적지로 보낸 바이트(`BytesOutToDestination`) — 둘 다 「most
    #   useful statistic is Sum」(AWS 문서) · 0 도 보낸다(실측 1440/1440). 받는 쪽이 8배 컸다(실측 2026-10-26 · 238 MB 대 30 MB).
    # 🔵 지역형(Regional) NAT 은 `NatGatewayId + AvailabilityZone` 차원을 쓴다(AWS 문서) — AZ 값이 이름인지 ID 인지 문서가
    #   말하지 않고 188 에 지역형이 없어 미측정이다. 지금은 영역형(Zonal) 차원만 묻는다 — 지역형이면 점이 오지 않아 「사용률 ?」.
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
        return []  # 게이트웨이 엔드포인트는 지표가 없다(D3 EXCLUDE)
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
        # 보조 — 연결 수. 🔴 처리량은 연결이 0 이어도 ≈ 100 kbps 다(모니터링·복제 트래픽 · 실측 2026-10-26) → 「아무도 안 쓴다」는
        #   연결 수가 말한다. 가장 바빴던 분의 최대 연결 · 평균 = 분마다 최대의 평균.
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
    # in = 시청자가 올린 것(BytesUploaded) · out = 내려준 것(BytesDownloaded).
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
    """VGW 에 붙은 VPN 만(결정 ⑤). TGW 에 붙은 VPN 은 그 TGW 어태치먼트(`resource_type = vpn`)의 바이트가 사용량이다."""
    attrs = node.attributes
    if attrs.get("transit_gateway_id") or not attrs.get("vpn_gateway_id"):
        return []
    if not alive(attrs.get("state")):
        return []
    dims = _dims(("VpnId", node.native_id))
    if dims is None:
        return []
    # 「Use the Sum statistic to show the total number of bytes」(AWS 문서) · 0 도 보낸다(실측 1440/1440).
    # 🔴 「These metrics can report network usage even when the tunnel is down … periodic status checks … ARP and BGP」 —
    #   점검 바이트가 섞인다 → `includes` 로 화면이 알린다(0 이 아니어도 「쓰고 있다」가 아닐 수 있다).
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
    # 「equals the number of requests billed」(AWS 문서) · 호출이 있을 때만 온다(실측 24h 7점) → 빈 창 = 호출 0(결정 ④).
    return [
        _calls(
            node, "invocations", _one(node, "AWS/Lambda", "Invocations", "Sum", dims)
        )
    ]


def _api_gateway(node: Node, graph: _Graph) -> list[RateSpec]:
    protocol = str(node.attributes.get("protocol") or "").upper()
    if protocol == "REST":
        # REST = `ApiName` 하나만으로 된 계열이 있다(list-metrics 2026-10-26). 「The SampleCount statistic represents this
        #   metric」(AWS 문서) — Sum 과 같은 값이었다(실측 282 = 282).
        dims = _dims(("ApiName", node.name))
        if dims is None:
            return []
        return [
            _calls(node, "requests", _one(node, "AWS/ApiGateway", "Count", "Sum", dims))
        ]
    if protocol == "HTTP":
        # HTTP API = `ApiId` 하나만으로 된 계열이 있다(list-metrics 2026-10-26 · Stage 붙은 계열과 따로). 바이트 = `DataProcessed`.
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
    return []  # WebSocket 은 지표가 다르다(ConnectCount · MessageCount) — 이번 범위 밖


def _elasticache(node: Node, graph: _Graph) -> list[RateSpec]:
    """노드형 클러스터만(서버리스는 수집하지 않는다). 🔵 188 에 노드형이 없어 미측정 — AWS 문서 기준(호스트 지표 =
    `CacheClusterId` + `CacheNodeId` · 노드 ID 는 `0001` 부터 순서대로)."""
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


# Global Accelerator 지표는 us-west-2 에서만 조회된다(AWS 문서 · 실측 2026-10-26 list-metrics). 차원 = `Accelerator`(ARN 끝 UUID).
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


# ─────────────────────────────────────────────────────────────────────────────
# count — 구조 문서 + 상태 스냅샷(서브넷 남은 IP)
# ─────────────────────────────────────────────────────────────────────────────


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _largest(cells: dict[str, int | None]) -> tuple[str, int] | None:
    """가장 찬 칸(같으면 이름 순서가 앞인 칸). 칸 하나라도 모르면 `None` — 모르는 칸이 가장 찼을 수 있다."""
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
        return []  # 수집기가 한도 단위 개수를 아직 안 실었다(구조 수집기 갱신 전) — 배지 없음
    part, used = found
    # 🔴 접두사 목록 규칙은 그 목록의 최대 항목 수만큼 센다(문서) — 그 수를 수집하지 않는다 → 「한도 ?」(D3).
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
    # IPv6 CIDR 은 수집하지 않는다(`vpc.py` 는 IPv4 연결만 싣는다) → measure 를 만들지 않는다.
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
    # 라우트는 테이블 노드가 아니라 `route` 엣지(대상 어태치먼트별)에 실린다 — 블랙홀만 노드 속성.
    # ECMP 면 같은 목적지가 여러 엣지에 실리므로 테이블마다 목적지 집합으로 센다.
    # `blackhole_destinations` 키가 없으면 라우트 검색이 실패한 테이블이다 → 합을 지어내지 않는다.
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
        # Service Quotas 에 없다(SA·TAM 으로만) → 문서 기본값 · `default_quota` · 코드 없음(D3).
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
        ):  # 후속 호출이 실패하면 키가 없다(= 못 읽음) → measure 를 만들지 않는다
            out.append(
                CountSpec(node.id, measure, len(ids), limit=limit, limit_source="fixed")
            )
    return out


def _subnet_limit(cidr: Any) -> int | None:
    """`/p` → 2^(32−p) − 5(AWS 가 서브넷마다 5개를 잡는다). 모르는 모양이면 `None`."""
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
                continue  # 이번에 그 서브넷이 없다(지워짐) — 관측이 말할 것이 없다
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


# ─────────────────────────────────────────────────────────────────────────────
# CloudWatch 읽기
# ─────────────────────────────────────────────────────────────────────────────

Points = list[tuple[datetime, float]]


def _parse_at(at: str) -> datetime:
    return datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc)


def _fmt(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stop(end: datetime, step: int) -> datetime:
    """창의 끝 = 관측 시각을 단위로 **내린** 시각. 지금 진행 중인 단위는 덜 찼다 — 넣으면 마지막 단위가 낮게
    나오고, 「항상 보낸다」 지표의 덮은 시간이 기간보다 짧아진다."""
    raw = int(end.timestamp())
    return datetime.fromtimestamp(raw - raw % step, tz=timezone.utc)


def _start(end: datetime, minutes: int, step: int) -> datetime:
    """창의 시작 = 끝 − 기간. 끝이 단위에 맞으므로 시작도 맞다 — 창 = 꽉 찬 단위 `기간 ÷ 단위` 개."""
    return _stop(end, step) - timedelta(minutes=minutes)


@dataclass(frozen=True, slots=True, order=True)
class _Read:
    """한 번에 읽는 범위. 짧은 묶음 셋은 24시간 읽기 하나에서 나온다(D6)."""

    minutes: int
    step: int


def _reads_for(minutes: int, min_step: int) -> tuple[_Read, int]:
    """기간 → (그 기간을 담는 읽기, 그 기간의 단위)."""
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
        except Exception as exc:  # noqa: BLE001 — 자격증명 실패(metrics.read_metrics 의 같은 자리 · sts 로 적는다)
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
        except Exception as exc:  # noqa: BLE001 — ClientError·BotoCoreError 둘 다
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
                # 어느 값이 어느 단위인지 모른다 — 고르지 않는다(가장 바빴던 때를 지어내지 않는다).
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
            # 같은 단위가 두 장에 걸쳐 오면 하나만 남긴다(시각이 키다) · 정렬은 시각 오름차순.
            out.points[(s, read)] = sorted(dict(collected[key]).items())


# ─────────────────────────────────────────────────────────────────────────────
# 접기 — 가장 바빴던 단위 · 평균 · 덮은 시간(ADR 0011 D2)
# ─────────────────────────────────────────────────────────────────────────────


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
    """기간 합(사용량 · `spec.total_unit` 단위). 점이 없으면 `None`(항상 보내는 지표의 빈 창 = 모름).

    sum   — 단위마다의 합을 더한다(바이트 · 패킷 · 요청 수). bit/s 측정이면 `factor` 가 8(바이트 → 비트)이므로 바이트 그대로다.
    level — 이미 속도다(DX = 평균 bit/s · RDS = 평균 B/s) → 점마다 속도 × 단위 초 = 그 단위의 양 → 바이트로(÷ 8 × factor).
    그 밖(연결 수 같은 수준값) → `None`.
    """
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
    """칸 하나 · 기간 하나 → 창 칸(방향·AZ 는 고른 뒤에 붙인다)."""
    start = _start(end, minutes, step)
    inside = [(t, v) for t, v in points if start <= t < _stop(end, step)]
    window_seconds = minutes * 60
    row: dict[str, Any] = {"minutes": minutes, "step_seconds": step}
    if not inside and spec.reporting == REPORT_NONZERO and spec.empty_zero:
        # 읽었고(호출 성공) · 점이 0개 · 사용량 지표 — **0**(사용자 결정 2026-10-26 ④). 트래픽이 없으면 AWS 가 점을 보내지 않는다
        #   (ALB `ProcessedBytes` 「Reporting criteria: There is a nonzero value」). 덮은 시간 = 창 전체(빈 단위 = 0 과 같은 규칙).
        row["peak"] = 0.0
        row["average"] = 0.0
        if spec.total_unit is not None:
            row["total"] = 0.0
        row["covered_seconds"] = window_seconds
        row["computed_at"] = _fmt(end)
        return row
    if not inside and spec.reporting == REPORT_NONZERO:
        # 읽었고(호출 성공) · 점이 0개 — 「값 없음」(#128). 0 으로 적으면 「정말 0」과 화면에서 같아진다.
        if spec.kind != "events":
            row["peak"] = None
            row["average"] = None
        row["no_points"] = True
        row["covered_seconds"] = 0
        row["computed_at"] = _fmt(end)
        return row
    if spec.reporting == REPORT_NONZERO:
        covered = window_seconds  # 빈 단위 = 0 — 창 전체로 나눈다(D2)
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
            # always 계열만 여기 온다(nonzero 의 빈 창은 위에서 값 없음으로 돌아갔다)
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
            else:  # VALUE_LEVEL
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
        # 점이 있는 칸만 더한다 — 모든 칸이 비었으면 그 창은 값 없음(#128 · 0 이 아니다)
        seen = [r for _c, r in rows if not r.get("no_points")]
        if not seen:
            return rows[0][1]
        return {
            **seen[0],
            "sum": sum(r["sum"] for r in seen),
            "covered_seconds": max(r["covered_seconds"] for r in seen),
        }
    # 칸(방향 × AZ) 중 **가장 높은 칸** 하나(D2) — 같으면 칸 순서(방향 in < out · AZ 이름 순)가 앞인 것.
    measured = [(c, r) for c, r in rows if r["peak"] is not None]
    if not measured:
        return rows[0][1]
    cell, row = max(measured, key=lambda cr: cr[1]["peak"])
    row = dict(row)
    if spec.total_unit is not None:
        # 사용량 = 모든 칸(방향 × AZ)의 합 — 가장 바빴던 칸 하나가 아니다(「얼마나 썼나」는 들어온 것 + 나간 것 전부)
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


# ─────────────────────────────────────────────────────────────────────────────
# 서비스 한도(D5)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class QuotaValue:
    limit: float
    source: str  # account_quota · default_quota
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
    """(계정, 리전, 서비스, 코드, 기본값) → 한도. 계정 값 → AWS 기본값 → 문서 기본값 순서(D5)."""
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
                # 「적용 값이 없다」는 실패가 아니다(문서: 「the quota is not retrieved」) — 이유를 달지 않는다.
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


# ─────────────────────────────────────────────────────────────────────────────
# 이월(D5 · D6)
# ─────────────────────────────────────────────────────────────────────────────


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
    """긴 묶음 · 서비스 한도를 이번에 다시 읽나. 직전 문서의 가장 새 긴 묶음이 1시간 이상 지났으면 읽는다."""
    _rows, _quotas, newest = _previous_index(previous)
    return (
        newest is None or (_parse_at(at) - newest).total_seconds() >= LONG_EVERY_SECONDS
    )


# ─────────────────────────────────────────────────────────────────────────────
# 조립
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class UtilizationResult:
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
    """관측 문서의 `utilization` 배열을 만든다. `metric_factory` · `quota_factory` = `(계정, 리전, 서비스) → 클라이언트`.

    `previous` = 직전 관측 문서(`--previous`) — 긴 묶음 · 한도 이월의 재료. 없으면 전부 지금 읽는다.
    """
    end = _parse_at(at)
    prev_long, prev_quotas, _newest = _previous_index(previous)
    due = long_is_due(previous, at)
    rates = plan_rates(topology)
    counts = plan_counts(topology, snapshot)

    # 긴 묶음: 이번에 읽거나(due · 직전 값 없음) 이월한다 — measure 마다.
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

    # 서비스 한도: 이번에 읽거나 이월한다 — (계정, 리전, 코드)마다.
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
