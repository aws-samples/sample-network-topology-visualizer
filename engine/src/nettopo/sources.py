"""옵션형 근거 출처 탐지 — ADR 0009 D4 「1차」. **탐지만 하고 아무것도 읽지 않는다.**

고객이 흐름 로그·Container Insights 같은 것을 켰을 수도, 안 켰을 수도 있다. 켜져 있으면 더 정확한
판정의 재료가 되고 꺼져 있으면 화면이 「지표 기반의 결과만 도출되었다」를 말해야 한다(사용자 지시
2026-09-24). 그 «말할 근거» 를 만드는 것이 이 모듈이다.

🔴 **여기서 로그를 한 줄도 읽지 않는다.** 이번 단계는 전부 `Describe*`/`List*` 이고 데이터 0바이트다
(ADR 0009 비용 절). 그래서:

- `used` 는 여기서 **항상 false** 다 — 이 모듈은 판정하지 않는다(VPC 흐름 로그를 읽어 `used` 를 켜는 것은
  판독기 `flowlogs.py` 와 `observe` 다 · 기본 꺼짐). 스키마가
  `state=on ∧ used=false ⇒ why 필수` 를 강제하므로 그 사유를 `_NOT_READ_BY_KIND` 한 곳에서 붙인다.
  사유를 안 적으면 화면이 「⚠ 근거 못 읽음」을 띄우면서 **고객이 무엇을 고쳐야 하는지 말하지
  못한다**(버킷 정책을 열면 되는 건지, 우리가 아직 안 읽는 건지).
- 「켜라」고 권고하지도, 켜지도 않는다(불변식 ① · ADR 0009 D7).

흐름 로그는 **로그마다 설정 전부**(`FlowLog`)를 `SourceScan.flow_logs` 로 돌려준다 — 판독기가 이것만 보고
읽을지·어떻게 읽을지 정한다(ADR 0009 D2-4). 🔴 이 목록은 `to_list()` 가 모른다 — 판독기를 켜지 않은
문서에는 한 글자도 안 나간다(ADR 0008 「D4b 개정」 12.2).

🔴 **「모른다」를 「꺼짐」으로 적지 마라.** 탐지가 거부되면 `unknown` + 사유다. 꺼짐으로 적으면
화면이 「켜세요」라고 잘못 권고하고, 고객은 이미 켠 것을 또 켜러 간다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from .model import Scope, Snapshot
from .services.base import ClientFactory, classify, pages

# 🔴 어휘의 정본은 이 파일이 아니라 ADR 0009 D4 채택표 + `schema/observation.schema.json` 의
# `$defs/source.kind` 다. `schema/tests/test_observation_schema.py::test_source_kind_enum_matches_adr9`
# 가 둘의 일치를 강제하고, 아래 `test_sources.py` 가 이 모듈이 그 8개를 전부 내는지 잰다.
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
    # log_delivery 는 계정 단위 배선이라 분모가 없다 — scope 를 만들지 않는다(ADR 0009 D2).
}

# 켜져 있는데 판정에 쓰지 않은 출처의 사유 — 판독기를 **거치지 않은** 행만이다. 흐름 로그를 읽은 행은 판독기가
# 로그마다 사유를 채운다(`observe._merge_flow_log_sources`). 🔴 VPC 흐름 로그는 이제 도구가 읽을 수 있으므로
# 「탐지만 한다」고 적으면 거짓이다 — 끈 사람에게 켜는 스위치 이름을 말한다(U9).
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

# VPC 흐름 로그 기본 형식(v2)의 14필드. 🔴 선언된 필드가 **전부 이 안**이면 기본 형식이다 — 구간을
# 이름 대는 `traffic-path` 가 없다(ADR 0009 D4: `traffic-path`·`pkt-srcaddr` 등은 사용자 형식 전용).
# 「흐름 로그가 켜져 있다」만으로 구간을 증명할 수 있다고 적으면 거짓이다.
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

# 판독기가 왕복 짝을 맞추려면 있어야 하는 필드. 정본 = ADR 0008 「D4b 개정」 조합표 아래 「필수 필드(4.3)」 줄
# (`test_sources.py` 가 그 줄과 대조한다). `subnet-id` 는 여기 없다 — 없으면 서브넷 선은 못 가리지만
# 피어링 선은 여전히 증명한다(`subnet_unattributable`, 조합표 1.1).
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

# TGW 흐름 로그 판독에 필요한 필드(ADR 0008 「D4c」 ③). 기본 형식(36필드)에 전부 있다 — 사용자 형식에서 뺐을 때만
# `fields_missing` 이다. `action` 은 TGW 기록에 없다(버린 것은 `packets-lost-*` 가 센다).
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

# 로그를 건 대상 id 접두 → 단위(`resources[].level` 어휘). 🔴 순서가 규칙이다 — `tgw-attach-` 가
# `tgw-` 보다 먼저 와야 어태치먼트 로그가 TGW 로그로 읽히지 않는다.
_LEVELS: tuple[tuple[str, str], ...] = (
    ("tgw-attach-", "transit_gateway_attachment"),
    ("tgw-", "transit_gateway"),
    ("subnet-", "subnet"),
    ("eni-", "network_interface"),
    ("vpc-", "vpc"),
)
VPC_LEVELS = frozenset({"network_interface", "subnet", "vpc"})

# `logs:DescribeDeliverySources` 의 `logType` 중 **네트워크 관측에 쓸 수 있는 것만** 센다.
# 🔴 전체를 세면 Lambda 로그 배선 하나로 「근거가 있다」고 말하게 된다.
NETWORK_LOG_TYPES = frozenset(
    {"ALB_ACCESS_LOGS", "EVENT_LOGS", "FLOW_LOGS", "NLB_ACCESS_LOGS", "WAF_LOGS"}
)


@dataclass(frozen=True, slots=True)
class Source:
    """`sources[]` 한 줄. 정렬·직렬화는 `SourceScan.to_list()` 한 곳에서만 한다(불변식 ②)."""

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
        # 🔴 켜져 있는데 쓰지 않았으면 사유가 **반드시** 있어야 한다(스키마 allOf). 이번 버전은
        # 어떤 출처도 읽지 않으므로 사유를 못 채우면 문서 전체가 스키마를 어긴다.
        if why is None and self.state == "on" and not self.used:
            why = _NOT_READ_BY_KIND.get(self.kind, _NOT_READ)
        if why is not None:
            doc["why"] = why
        return doc


@dataclass(frozen=True, slots=True)
class FlowLog:
    """`DescribeFlowLogs` 한 줄의 설정 전부 — ACTIVE 인 것만. 판독기(`flowlogs.py`)의 유일한 입력이다.

    🔴 `destination` 은 로그 그룹 이름이나 버킷 ARN 이다 — 산출물·화면·커밋에 내보내지 않는다(버킷·로그
    그룹 이름은 식별자다). 그래서 `repr` 에서도 뺀다(테스트 실패 출력에 찍히지 않게).
    """

    id: str
    level: str
    target: str  # 로그를 건 자원의 native id
    # 이 로그가 덮는 VPC(native id). ENI 단위 · TGW 계열 · 구조에 없는 서브넷이면 None.
    vpc_id: str | None = None
    traffic_type: str | None = None  # ALL · ACCEPT · REJECT (TGW 계열은 없다)
    destination_type: str = ""  # cloud-watch-logs · s3 · kinesis-data-firehose
    destination: str | None = field(default=None, repr=False)
    fields: tuple[
        str, ...
    ] = ()  # 선언된 순서 그대로 — S3 머리줄·CWL 메시지를 이 순서로 자른다
    file_format: str | None = None  # plain-text (S3 만 · Parquet 은 탐지에서 빠진다)
    hive: bool = False
    per_hour: bool = False
    interval: int | None = None  # MaxAggregationInterval(초)
    delivered: bool = True
    account_id: str = ""
    region: str = ""


@dataclass(frozen=True, slots=True)
class SourceScan:
    """탐지 결과 전부. `observe.build_observation` 이 이것만 보고 `sources[]` 를 적는다."""

    scanned: bool
    found: tuple[Source, ...] = ()
    # 🔴 직렬화하지 않는다(모듈 docstring). (계정, 리전, id) 순으로 정렬돼 있다(불변식 ②).
    flow_logs: tuple[FlowLog, ...] = ()

    @classmethod
    def not_scanned(cls) -> SourceScan:
        """탐지를 아예 하지 않은 상태 — 빈 배열이 된다.

        🔴 빈 배열은 「전부 꺼져 있다」가 **아니다**(스키마 `$comment`). 화면은 이때 알약을
        띄우지 않아야 한다 — 꺼졌다고 말할 근거가 없다.
        """
        return cls(scanned=False)

    def to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in sorted(self.found, key=lambda s: s.kind)]


# ---- 자원 모집단(분모) --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Population:
    """그 (계정,리전)에 **구조 스냅샷이 본** 자원 수 = scope 의 분모.

    🔴 분모를 여기서 또 세지 않는다 — 1층이 이미 센 것을 쓴다. 따로 세면 「7개 VPC 중 3개」의
    7 과 화면의 VPC 개수가 갈라지고, 사람은 어느 쪽이 맞는지 알 수 없다.
    """

    vpc: int = 0
    transit_gateway: int = 0
    # 서브넷 → 그 VPC(둘 다 native id). 서브넷 단위 흐름 로그를 VPC 로 세는 근거다(ADR 0009 D2-4).
    # 🔴 **리전 단위**다 — 공유 VPC 의 서브넷 노드는 소유 계정 아래에 있지만 참가자 계정의 로그가 그
    # 서브넷을 가리킬 수 있다. 구조에 없는 서브넷은 여기 없다 → 켜짐으로는 세되 범위를 적지 않는다.
    subnet_vpc: Mapping[str, str] = field(default_factory=dict)


def _population(
    snapshot: Snapshot,
) -> tuple[dict[tuple[str, str], _Population], dict[str, dict[str, str]]]:
    """(자리별 분모, 리전별 서브넷 → VPC). 둘 다 1층이 본 것만 쓴다."""
    counts: dict[tuple[str, str], dict[str, int]] = {}
    subnets: dict[str, dict[str, str]] = {}
    for node in snapshot.nodes:
        if node.region is None:
            continue
        if node.type == "subnet":
            # 부모가 VPC 가 아니면(1층이 VPC 를 못 읽어 리전에 붙었다) 어느 VPC 인지 모른다.
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


# ---- 한 자리(계정,리전)의 탐지 결과 -------------------------------------------


@dataclass(frozen=True, slots=True)
class _Cell:
    enabled: int
    total: int
    detail: str | None = None
    # 🔴 분모를 믿을 수 있나. 못 믿으면 **범위를 적지 않는다** — 「3/0 VPC」·「9/7 VPC」는
    # 화면에서 읽을 수 없는 수치이고, 억지로 분모를 분자에 맞추면 없는 자원을 있다고 말한다.
    trust_total: bool = True
    # 🔴 켜짐을 **판정할 수 없었다**(호출은 성공했지만 답이 없는 자리). EKS 네트워크 정책 로그를
    # Helm 으로 깔면 add-on API 에 안 잡히는 것이 이 경우다 — 꺼짐으로 적으면 이미 켠 고객에게
    # 「켜세요」라고 말한다(ADR 0009 D2). 분자가 0 이면 이것이 `unknown` 으로 올라간다.
    undetermined: str | None = None
    # 켜져 있지만 **분자(단위 수)에 세지 않은** 것 — ENI 단위 로그 · VPC 를 모르는 서브넷 로그. 이것만
    # 있어도 출처는 `on` 이다(🔴 꺼짐으로 적으면 켠 고객에게 「켜세요」가 된다 · ADR 0009 D2-4).
    finer_on: int = 0
    flow_logs: tuple[FlowLog, ...] = ()


# 자리 하나에서 한 API 가 여러 kind 를 채운다(흐름 로그 호출 하나가 VPC·TGW 둘을 준다).
_Scan = Callable[[Any, _Population], dict[str, _Cell]]


def flow_log_fields(log_format: str | None) -> tuple[str, ...]:
    """`${...}` 토큰을 **선언된 순서대로**. 판독기가 레코드를 이 순서로 자른다."""
    if not log_format:
        return ()
    return tuple(
        token.strip("${} ") for token in log_format.split() if token.startswith("${")
    )


def is_default_format(fields: tuple[str, ...]) -> bool:
    """🔴 **필드 집합**으로 가른다(ADR 0009 D2-4). `DescribeFlowLogs` 는 기본 형식도 펼친 문자열로
    돌려주므로 「형식 문자열이 비었나」는 실계정에서 한 번도 참이 되지 않는다 — 그렇게 가르면 기본 형식
    로그가 「필수 필드가 빠진 사용자 형식」으로 나가고 고객은 없는 설정 실수를 찾으러 간다.
    형식을 모르면(빈 튜플) AWS 기본값이 v2 다.
    """
    return frozenset(fields) <= frozenset(V2_FIELDS)


def missing_fields(
    fields: tuple[str, ...], required: tuple[str, ...] = REQUIRED_FIELDS
) -> tuple[str, ...]:
    """필수 필드 중 선언에 없는 것 — `required` 순서(기본 = VPC 흐름 로그)."""
    have = frozenset(fields)
    return tuple(f for f in required if f not in have)


def is_parquet(log: dict[str, Any]) -> bool:
    """S3 Parquet 로그 — **스펙 밖**이다(ADR 0008 「D4c」 ①). 탐지 단계에서 없는 것으로 친다(켜짐 수에도 안 센다)."""
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
    """`ec2:DescribeFlowLogs` 한 번으로 VPC·TGW 둘을 채우고, 로그마다 설정을 돌려준다.

    🔴 「켜져 있다」의 조건이 `FlowLogStatus == ACTIVE` **하나가 아니다** — 전달이 죽어 있으면
    레코드 부재가 트래픽 부재가 아니다(ADR 0009 D1-1 조건 ①). 그래서 전달 실패를 `detail` 에
    적는다. 켜짐 자체는 ACTIVE 로 세고(고객은 켰다), 쓸 수 있는지는 판독기의 몫이다.

    🔴 VPC 는 **VPC 단위 + 서브넷 단위** 로그로 센다(서브넷 → VPC 는 구조에서 · ADR 0009 D2-4). 옛 코드는
    `vpc-` 접두만 세서 서브넷·ENI 단위로 켠 고객에게 「꺼짐」을 말했다.
    """
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
            # 어태치먼트에 걸린 흐름 로그도 「TGW 흐름 로그가 켜져 있다」는 사실이다. 다만
            # 🔴 어느 TGW 의 것인지는 이 응답만으로 모른다 → 분모를 믿을 수 없다.
            attachment_ids.add(resource_id)
        else:
            tgw_ids.add(resource_id)
        config = _config(log, level, vpc_id)
        # TGW 는 기본 포맷이 v2~v6 전부라 형식 경고가 필요 없다(ADR 0009 D4) — VPC 계열만 센다.
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
    """`logs:DescribeDeliverySources` — 로그 0바이트로 계정의 관측 배선 지도를 본다.

    🔴 S2S VPN·Resolver 쿼리 로그는 **V1 전달 모델이라 여기 안 잡힌다**(ADR 0009 D4) —
    여기 없다는 것을 「꺼져 있다」로 읽으면 안 되므로 그 둘은 개별 API 로 따로 탐지한다.
    """
    found = pages(client, "describe_delivery_sources", "deliverySources")
    matched = sorted(
        {
            (src.get("logType") or "").upper()
            for src in found
            if (src.get("logType") or "").upper() in NETWORK_LOG_TYPES
        }
    )
    # 분모가 없다 — 「배선이 몇 개 있어야 맞다」는 기준이 존재하지 않는다.
    return {
        LOG_DELIVERY: _Cell(len(matched), 0, ", ".join(matched) if matched else None)
    }


def _ecs_clusters(client: Any, _pop: _Population) -> dict[str, _Cell]:
    """Container Insights. 🔴 **구간 판정에는 쓰지 않는다**(ADR 0009 D4: 지표 차원·성능 로그
    양쪽 모두 상대방을 가리키는 필드가 0 개). 엔드포인트가 살아 있다는 판정까지만이다.
    """
    arns = pages(client, "list_clusters", "clusterArns")
    if not arns:
        # 🔴 클러스터가 0 개면 「꺼짐」이 아니라 **해당 없음**이다 — 분모 0 이 그것을 말한다.
        # `unknown` 으로 적으면 ECS 를 안 쓰는 고객에게 「근거 확인 못함」 경고가 뜬다.
        return {CONTAINER_INSIGHTS: _Cell(0, 0)}
    enabled = 0
    for start in range(0, len(arns), 100):  # DescribeClusters 는 한 번에 100개
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
    """🔴 `eks:DescribeCluster` → `logging.clusterLogging[].types` 에는 **절대 안 잡힌다**
    (그 목록은 컨트롤 플레인 5종뿐) — VPC CNI add-on 의 설정값을 봐야 한다(ADR 0009 D4).

    add-on 이 없으면 Helm 설치일 수 있으므로 **꺼짐이 아니라 모름**이다. 여기서는 그 자리를
    분자에서 빼고 `detail` 에 남긴다 — 자리 단위 `unknown` 은 표현할 칸이 없기 때문이다.
    """
    names = pages(client, "list_clusters", "clusters")
    if not names:
        return {EKS_NETWORK_POLICY_LOG: _Cell(0, 0)}
    enabled = 0
    unmanaged = 0
    for name in names:
        try:
            addon = client.describe_addon(clusterName=name, addonName="vpc-cni")
        except Exception:  # noqa: BLE001 — add-on 부재도 「모름」이다(Helm 설치)
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
    """상세 모니터링. 꺼져 있으면 해상도가 5분이므로 2층을 `period=300` 으로 물어야 한다
    (ADR 0008 D6) — 켜짐/꺼짐이 **판정의 정확도**를 바꾸는 유일한 출처다.
    """
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
    """🔴 스위치가 **2개**고 둘 다 기본 꺼짐이다(`LogEnabled`·`BgpLogEnabled`).

    BGP 로그만이 VPN 구간의 prefix·피어·`DENIED due to` 를 준다 — 터널 로그만 켜져 있으면
    구간의 양끝은 여전히 모른다. 그래서 켜짐은 터널 로그로 세고 BGP 는 `detail` 에 적는다.
    """
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
    """네 번째 pair 지표 축(ADR 0009 1차 보너스) — `EndpointID`+`TargetNameServerIP` 차원이
    아웃바운드 리졸버 ↔ 온프렘 DNS 구간을 직접 이름 댄다. 기본 꺼짐이고 켜기는 권한 밖이다.
    """
    endpoints = pages(client, "list_resolver_endpoints", "ResolverEndpoints")
    live = [e for e in endpoints if (e.get("Status") or "") != "DELETING"]
    enabled = sum(1 for e in live if e.get("TargetNameServerMetricsEnabled"))
    return {RESOLVER_TARGET_NS_METRICS: _Cell(enabled, len(live))}


# (서비스, 권한 문서에 적는 API 이름, 스캔 함수). 🔴 순서가 고정이어야 결정론이 성립한다.
PROBES: tuple[tuple[str, str, _Scan], ...] = (
    ("ec2", "ec2:DescribeFlowLogs", _flow_logs),
    ("ec2", "ec2:DescribeInstances", _instances),
    ("ec2", "ec2:DescribeVpnConnections", _vpn_connections),
    ("ecs", "ecs:DescribeClusters", _ecs_clusters),
    ("eks", "eks:DescribeAddon", _eks_network_policy),
    ("logs", "logs:DescribeDeliverySources", _delivery_sources),
    ("route53resolver", "route53resolver:ListResolverEndpoints", _resolver_endpoints),
)

# 🔴 **정책에 적어야 하는 액션은 `PROBES` 보다 많다.** `PROBES` 의 이름은 실패를 사람에게
# 설명할 «대표» API 하나지만, ECS·EKS 는 목록과 상세가 **다른 액션**이라 둘 다 없으면 탐지가
# 통째로 거부된다. 그 차이를 코드 밖(문서·CFN)에만 두면 화면에서 이렇게 나온다: 고객이 스택 B 를
# 새로 배포했는데도 상단 알약이 「근거 확인 못함 8종」에서 내려오지 않고, 원인은 정책에서 빠진
# 액션 2개다. `test_sources.py` 가 이 목록을 `docs/required-permissions.md` §1-4 표와
# `infra/stack-b-member-role.yaml` 양쪽에 대고 잰다.
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

# 그 스캔이 채우는 kind 들 — 실패했을 때 **어느 kind 를 `unknown` 으로 만들지** 알아야 한다.
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
    """옵션형 근거 출처를 탐지한다. 전부 `Describe*`/`List*` = 무료 · 데이터 0바이트.

    실패는 **kind × 자리 단위**로 온다 — 한 자리에서 거부돼도 다른 자리는 유효하므로 죽이지
    않는다(불변식 ④). 자리 하나라도 켜져 있으면 `on`, 아무 곳도 안 켜졌고 실패가 있으면
    `unknown`, 그 외 `off` 다.
    """
    population, subnets = _population(snapshot)
    enabled: dict[str, int] = {}
    finer: dict[str, int] = {}
    flow_logs: list[FlowLog] = []
    total: dict[str, int] = {}
    distrusted: set[str] = set()
    notes: dict[str, list[str]] = {}
    failures: dict[str, tuple[str, str]] = {}  # kind → (api, message) 첫 실패
    failed_cells: dict[str, int] = {}
    # 호출은 됐지만 켜짐·꺼짐을 판정할 수 없던 자리의 사유 — 분자가 0 이면 `unknown` 이 된다.
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
                except Exception as exc:  # noqa: BLE001 — 탐지 실패는 「모름」이고 정상 경로다
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
            # 탐지가 거부됐다 — 「모름」의 1순위 사유. 권한 문제는 고객이 고칠 수 있다.
            api, message = failures[kind]
            state = "unknown"
            why = f"{api}: {message}"
        elif kind in undetermined:
            # 호출은 됐는데 켜짐을 판정할 칸이 없었다(Helm 설치 EKS). 🔴 여기서 `off` 로 적으면
            # 이미 켠 고객에게 「켜세요」라고 말한다(ADR 0009 D2).
            state, why = "unknown", undetermined[kind]
        else:
            state, why = "off", None
        if state == "unknown" and failed_cells.get(kind, 0) > 1:
            detail = "; ".join(
                filter(None, [detail, f"{failed_cells[kind]}개 자리에서 탐지 실패"])
            )
        # 🔴 범위를 적을 자격 — 단위가 있고(계정 단위 배선이 아니고) · 탐지에 성공했고
        # (`unknown` 의 「0/7」은 「7개 다 꺼져 있다」로 읽힌다) · 분모가 분자보다 작지 않고
        # (1층이 그 자원을 못 읽었을 수 있다) · 분모를 믿을 수 있고 · 켜짐인데 센 단위가 0 이
        # 아닐 때만이다(ENI 단위 로그만 있으면 「켜짐 0/7 VPC」가 된다 — ADR 0009 D2-4).
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
