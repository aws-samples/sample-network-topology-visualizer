"""Collect traffic evidence for topology paths from flow logs.

Confirm round-trip traffic with matching forward and reverse records.
Check for no traffic on internet gateway and VPC peering paths. Require
NODATA for every relevant ENI across the entire evaluation window.
Missing records alone do not establish no traffic or blocking.
"""

from __future__ import annotations

import ipaddress
import zlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .awsguard import ReadOnlyViolation
from .metrics import WINDOW_SECONDS, _window
from .model import Snapshot, split_node_id
from .services.base import STATUS_BY_ERROR_CODE
from .sources import (
    TGW_FLOW_LOG,
    TGW_REQUIRED_FIELDS,
    VPC_FLOW_LOG,
    VPC_LEVELS,
    FlowLog,
    is_default_format,
    missing_fields,
)


STREAM_SLACK_SECONDS = 10_800
MAX_STREAMS_PER_LOG = 500
MAX_PAGES_PER_STREAM = 20
MAX_OBJECTS_PER_LOG = 300
MAX_UNCOMPRESSED_BYTES = 2 * 1024**3
MAX_RECORDS = 500_000
MAX_CALLS = 3_000
S3_FILE_EARLY_SECONDS = 300
S3_FILE_LATE_SECONDS = 60
DEFAULT_INTERVAL = 600
_READ_CHUNK = 1 << 20

NODATA_SLACK_SECONDS: dict[str, int] = {"cloud-watch-logs": 180, "s3": 600}
NODATA_GAP_SECONDS = 10
STRUCTURE_ACTIONS: tuple[str, ...] = ("ec2:DescribeNetworkInterfaces",)
API_ENI = "ec2:DescribeNetworkInterfaces"

REQUIRED_ACTIONS: tuple[str, ...] = (
    "logs:DescribeLogGroups",
    "logs:DescribeLogStreams",
    "logs:GetLogEvents",
    "s3:GetBucketLocation",
    "s3:GetObject",
    "s3:ListBucket",
)

API_CWL = "logs:GetLogEvents"
API_S3 = "s3:GetObject"
CWL = "cloud-watch-logs"
S3 = "s3"
FIREHOSE = "kinesis-data-firehose"

IGW = "internet_gateway"
EIGW = "egress_only_internet_gateway"
VPCE = "vpc_endpoint"
PCX = "vpc_peering_connection"
PATH_NAMES: dict[str, frozenset[str]] = {
    "8": frozenset({IGW, EIGW}),
    "2": frozenset({IGW, VPCE}),
    "4": frozenset({PCX}),
    "5": frozenset({PCX}),
}
COUNTABLE: dict[str, str] = {"8": IGW, "2": IGW, "4": PCX, "5": PCX}
PEERING_PATHS = frozenset({"4", "5"})
UNRESOLVED_KIND: tuple[tuple[str, str], ...] = (
    ("eigw-", EIGW),
    ("igw-", IGW),
    ("pcx-", PCX),
    ("vpce-", VPCE),
)

_DENIED = frozenset(
    code for code, (status, _) in STATUS_BY_ERROR_CODE.items() if status == "denied"
)
_THROTTLED = frozenset(
    code for code, (status, _) in STATUS_BY_ERROR_CODE.items() if status == "throttled"
) | {"SlowDown"}
_NOT_FOUND = frozenset({"NoSuchBucket", "NoSuchKey", "ResourceNotFoundException"})


WHY: dict[str, str] = {
    "access_denied": "이 로그를 읽을 권한이 없다 — 스택 파라미터로 읽기를 허용한다",
    "capped": "읽기 한도에 닿아 거기까지 읽은 기록만 쓴다",
    "destination_firehose": "Firehose 로 보내는 로그는 도구가 읽을 경로가 없다",
    "duplicate_covered": "같은 곳을 덮는 다른 흐름 로그를 읽었다",
    "fields_missing": "사용자 지정 형식에 판정에 필요한 필드가 빠졌다 — 빠진 필드를 넣어 다시 만든다",
    "format_mismatch": "로그 내용이 선언된 형식과 다르다 — 도구 결함으로 신고한다",
    "format_v2": "기본 형식(v2)이라 경로 번호(traffic-path)·서브넷 필드가 없다 — 사용자 지정 형식으로 다시 만든다",
    "kms": "로그를 암호화한 KMS 키로 풀 수 없다 — S3 버킷의 고객 키는 스택 파라미터 FlowLogKmsKeyArns 에 적고 키 정책을 확인한다",
    "log_class_infrequent": "드문 접근(Infrequent Access) 등급 로그 그룹이라 읽을 수 없다 — 표준 등급 그룹으로 보낸다",
    "not_found": "로그 목적지(로그 그룹·버킷)가 없다 — 흐름 로그 설정을 고친다",
    "read_error": "읽는 중 예상하지 못한 오류가 났다",
    "shared_participant": "이 로그를 건 대상이 수집한 구조에 없어 어느 선의 근거인지 가릴 수 없다(공유 VPC 의 다른 계정 로그)",
    "subnet_unattributable": "서브넷 필드(subnet-id)가 없어 어느 서브넷 선인지 가릴 수 없다 — subnet-id 필드를 넣는다",
    "throttled": "AWS 가 요청이 많다며 거절했다 — 거기까지 읽은 기록만 쓴다",
    "traffic_reject_only": "거부(REJECT) 기록만 남기는 로그라 흐른다는 근거가 되지 않는다",
}
RETIRED_WHY = frozenset({"format_parquet", "reader_pending_tgw"})
_WHY_VARIANT: dict[tuple[str, str], str] = {
    (
        "kms",
        S3,
    ): "버킷 객체를 암호화한 KMS 키로 풀 권한이 없다 — 스택 파라미터 FlowLogKmsKeyArns 에 그 키를 넣고 키 정책도 수집 역할에 복호를 연다",
    (
        "kms",
        CWL,
    ): "로그 그룹을 암호화한 KMS 키를 CloudWatch Logs 가 쓸 수 없다 — 키 정책에 logs 서비스 권한을 연다",
    (
        "shared_participant",
        "transit_gateway",
    ): "이 로그를 건 TGW 가 수집한 구조에 없어 어느 선의 근거인지 가릴 수 없다(공유 TGW 의 다른 계정 로그)",
    (
        "shared_participant",
        "transit_gateway_attachment",
    ): "이 로그를 건 어태치먼트가 수집한 구조에 없어 어느 선의 근거인지 가릴 수 없다(공유 TGW 의 다른 계정 로그)",
    (
        "access_denied",
        CWL,
    ): "이 로그 그룹을 읽을 권한이 없다 — 스택 파라미터 FlowLogGroupNamePrefixes 에 이 로그 그룹 이름 접두를 넣는다",
    (
        "access_denied",
        S3,
    ): "이 버킷의 흐름 로그를 읽을 권한이 없다 — 다른 계정 버킷이면 스택 파라미터 FlowLogCentralBucketArns 와 그 버킷 정책에 읽기를 연다",
    (
        "subnet_unattributable",
        "vpc",
    ): "서브넷 필드(subnet-id)가 없어 서브넷 선은 가리지 못하고 피어링 선만 판정한다 — subnet-id 필드를 넣는다",
    (
        "subnet_unattributable",
        "network_interface",
    ): "이 ENI 로그에 서브넷 필드(subnet-id)가 없어 어느 서브넷 선인지 가릴 수 없다 — subnet-id 필드를 넣는다",
    ("duplicate_covered", "vpc"): "같은 VPC 의 다른 흐름 로그를 읽었다",
    ("duplicate_covered", "subnet"): "이 서브넷을 덮는 다른 흐름 로그를 읽었다",
    ("duplicate_covered", "network_interface"): "같은 ENI 의 다른 흐름 로그를 읽었다",
    (
        "duplicate_covered",
        "region",
    ): "이 리전의 서브넷을 모두 덮는 다른 흐름 로그를 읽었다",
}

_SLOW_NOTE = "집계 간격 600초 — 최근 흐름은 늦게 보인다"
_UNDELIVERED_NOTE = "전달 실패가 있다 — 기록이 빠졌을 수 있다"
_PEERING_ONLY_NOTE = "서브넷 필드 없음 — 피어링 선만 판정"
_ENI_UNREAD_NOTE = "ENI 목록 못 읽음 — 흐름 없음 판정 안 함"
_SHARED_UNREAD_NOTE = (
    "로그 그룹을 다른 흐름 로그와 같이 쓰는데 ENI 목록 못 읽음 — 이 로그 줄은 세지 않음"
)
_COUNTERS: tuple[tuple[str, str], ...] = (
    ("skipdata", "SKIPDATA"),
    ("ambiguous", "모호"),
    ("mismatch", "어긋남"),
    ("egress_only", "나간 기록만"),
    ("unknown_subnet", "구조에 없는 서브넷"),
    ("other_format", "형식 다른 줄"),
    ("other_log", "다른 로그 줄"),
    ("lost_blackhole", "버림(블랙홀)"),
    ("lost_no_route", "버림(경로 없음)"),
    ("lost_mtu", "버림(MTU 초과)"),
    ("lost_ttl", "버림(TTL 만료)"),
    ("tgw_one_way", "한쪽 기록만"),
)
_TGW_LOST: tuple[tuple[str, str], ...] = (
    ("packets-lost-blackhole", "lost_blackhole"),
    ("packets-lost-no-route", "lost_no_route"),
    ("packets-lost-mtu-exceeded", "lost_mtu"),
    ("packets-lost-ttl-expired", "lost_ttl"),
)


def why_of(code: str, variant: str | None = None) -> str:
    return _WHY_VARIANT.get((code, variant or ""), WHY[code])




@dataclass(frozen=True, slots=True)
class LogResult:

    id: str
    level: str
    used: bool
    kind: str
    account_id: str = ""
    region: str = ""
    attached_to: str | None = None
    why_code: str | None = None
    why: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"id": self.id, "level": self.level}
        if self.attached_to is not None:
            doc["attached_to"] = self.attached_to
        doc["used"] = self.used
        if self.why_code is not None:
            doc["why_code"] = self.why_code
            doc["why"] = self.why or WHY[self.why_code]
        if self.detail is not None:
            doc["detail"] = self.detail
        return doc


@dataclass(frozen=True, slots=True)
class Proof:

    target_id: str
    observed_on: str
    route_id: str | None
    pairs: int
    api: str


@dataclass(frozen=True, slots=True)
class Crossing:

    attachments: tuple[str, ...]
    pairs: int
    api: str


@dataclass(frozen=True, slots=True)
class FlowLogReading:
    resources: tuple[LogResult, ...] = ()
    proofs: tuple[Proof, ...] = ()
    calls: int = 0
    window_seconds: int = WINDOW_SECONDS
    silences: tuple[Proof, ...] = ()
    crossings: tuple[Crossing, ...] = ()



_Net = ipaddress.IPv4Network | ipaddress.IPv6Network
_Addr = ipaddress.IPv4Address | ipaddress.IPv6Address
_Attr = tuple[str, tuple[str, str, str | None] | None]


@dataclass(frozen=True, slots=True)
class _Route:
    edge_id: str
    target_id: str
    kind: str
    native: str
    nets: tuple[_Net, ...]
    unknown: bool


class _Structure:

    def __init__(self, snapshot: Snapshot) -> None:
        nodes = {node.id: node for node in snapshot.nodes}
        self.by_native: dict[tuple[str, str, str], str] = {}
        self.subnet_vpc: dict[str, str] = {}
        self.vpc_subnets: dict[str, list[str]] = {}
        self.region_subnets: dict[str, set[str]] = {}
        self.vpc_cidrs: dict[str, tuple[_Net, ...]] = {}
        attachments: dict[str, set[str]] = {}
        mains: dict[str, list[str]] = {}
        for node in sorted(snapshot.nodes, key=lambda n: n.id):
            if node.region is not None and node.native_id:
                self.by_native.setdefault(
                    (node.type, node.region, node.native_id), node.id
                )
            if node.type == "subnet" and (node.parent_id or "").startswith("vpc/"):
                self.subnet_vpc[node.id] = str(node.parent_id)
                self.vpc_subnets.setdefault(str(node.parent_id), []).append(node.id)
                self.region_subnets.setdefault(node.region or "", set()).add(node.id)
            elif node.type == "vpc":
                self.vpc_cidrs[node.id] = tuple(
                    _nets(node.attributes.get("cidr_blocks") or ())
                )
            elif node.type == "route_table" and node.attributes.get("main") is True:
                mains.setdefault(node.parent_id or "", []).append(node.id)
            elif node.type == "transit_gateway_attachment" and node.native_id:
                attachments.setdefault(node.native_id, set()).add(node.id)
        self.attachments = {
            native: next(iter(ids))
            for native, ids in attachments.items()
            if len(ids) == 1
        }
        self.main = {vpc: ids[0] for vpc, ids in mains.items() if len(ids) == 1}
        self.explicit: dict[str, list[tuple[str, str]]] = {}
        routes: dict[str, list[_Route]] = {}
        for edge in sorted(snapshot.edges, key=lambda e: e.id):
            if not edge.source.startswith("route_table/"):
                continue
            if edge.type == "association" and edge.target.startswith("subnet/"):
                state = str(edge.attributes.get("state") or "")
                self.explicit.setdefault(edge.target, []).append((edge.source, state))
            elif edge.type == "route":
                kind, _, _, native = split_node_id(edge.target)
                destinations = [
                    str(d) for d in edge.attributes.get("destinations") or ()
                ]
                nets = tuple(_nets(destinations))
                routes.setdefault(edge.source, []).append(
                    _Route(
                        edge_id=edge.id,
                        target_id=edge.target,
                        kind=kind,
                        native=native or "",
                        nets=nets,
                        unknown=len(nets) < len(destinations),
                    )
                )
        self.routes = {rtb: tuple(r) for rtb, r in routes.items()}
        self.unresolved: dict[str, tuple[tuple[str, str], ...]] = {}
        for node_id, node in nodes.items():
            if node.type != "route_table":
                continue
            refs = []
            for ref in sorted(
                str(r) for r in node.attributes.get("unresolved_refs") or ()
            ):
                kind = next((k for p, k in UNRESOLVED_KIND if ref.startswith(p)), None)
                if kind is not None:
                    refs.append((ref, kind))
            if refs:
                self.unresolved[node_id] = tuple(refs)

    def node(self, node_type: str, region: str, native: str | None) -> str | None:
        if not native:
            return None
        return self.by_native.get((node_type, region, native))

    def table_of(self, subnet_id: str) -> str | None:
        explicit = self.explicit.get(subnet_id)
        if explicit:
            live = sorted(rtb for rtb, state in explicit if state == "associated")
            return live[0] if len(live) == 1 else None
        return self.main.get(self.subnet_vpc.get(subnet_id, ""))

    def vpc_tables(self, vpc_id: str) -> list[str] | None:
        subnets = self.vpc_subnets.get(vpc_id) or []
        tables: set[str] = set()
        for subnet in subnets:
            table = self.table_of(subnet)
            if table is None:
                return None
            tables.add(table)
        return sorted(tables) or None

    def _match(self, rtb: str, path: str, dst: _Addr) -> tuple[str, _Route | None]:
        best: _Route | None = None
        best_len = -1
        for route in self.routes.get(rtb, ()):
            for net in route.nets:
                if (
                    net.version == dst.version
                    and dst in net
                    and net.prefixlen > best_len
                ):
                    best, best_len = route, net.prefixlen
        named = PATH_NAMES[path]
        for route in self.routes.get(rtb, ()):
            if not route.unknown or route.kind not in named:
                continue
            if route.kind == EIGW and dst.version == 4:
                continue
            if best is None or route.target_id != best.target_id:
                return "ambiguous", None
        for native, kind in self.unresolved.get(rtb, ()):
            if kind not in named or (kind == EIGW and dst.version == 4):
                continue
            if best is None or native != best.native:
                return "ambiguous", None
        if best is None or best.kind != COUNTABLE[path]:
            return "mismatch", None
        return "ok", best

    def _outside_vpc(self, vpc_id: str, dst: _Addr) -> bool:
        return dst.version != 4 or not any(
            dst in net for net in self.vpc_cidrs.get(vpc_id, ())
        )

    def attribute(self, subnet_id: str, path: str, dst_raw: str) -> _Attr:
        vpc_id = self.subnet_vpc.get(subnet_id, "")
        dst = _addr(dst_raw)
        table = self.table_of(subnet_id)
        if dst is None or table is None or not self._outside_vpc(vpc_id, dst):
            return "mismatch", None
        outcome, route = self._match(table, path, dst)
        if route is None:
            return outcome, None
        return "ok", (route.target_id, subnet_id, route.edge_id)

    def attribute_vpc(self, vpc_id: str, path: str, dst_raw: str) -> _Attr:
        if path not in PEERING_PATHS:
            return "skip", None
        dst = _addr(dst_raw)
        tables = self.vpc_tables(vpc_id)
        if dst is None or tables is None or not self._outside_vpc(vpc_id, dst):
            return "mismatch", None
        targets: set[str] = set()
        for table in tables:
            outcome, route = self._match(table, path, dst)
            if route is None:
                return outcome, None
            targets.add(route.target_id)
        if len(targets) != 1:
            return "ambiguous", None
        return "ok", (targets.pop(), vpc_id, None)


def _nets(values: Iterable[Any]) -> Iterator[_Net]:
    for value in values:
        text = str(value)
        if text.startswith("pl-"):
            continue
        try:
            yield ipaddress.ip_network(text, strict=False)
        except ValueError:
            continue


def _addr(value: str) -> _Addr | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None




class _Stop(Exception):
    """Signal that a read limit or throttling stopped log reading.

    The caller retains records read before the stop.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Failed(Exception):
    """Signal a log read failure.

    The caller discards records temporarily read from the affected log.
    """

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class _Budget:
    calls: int = 0
    uncompressed: int = 0
    records: int = 0

    def call(self) -> None:
        if self.calls >= MAX_CALLS:
            raise _Stop("capped")
        self.calls += 1

    def inflate(self, size: int) -> None:
        if self.uncompressed + size > MAX_UNCOMPRESSED_BYTES:
            raise _Stop("capped")
        self.uncompressed += size

    def record(self) -> None:
        if self.records >= MAX_RECORDS:
            raise _Stop("capped")
        self.records += 1


@dataclass(frozen=True, slots=True)
class _Egress:
    subnet_id: str | None
    attr: tuple[str, str, str | None]
    api: str
    log_id: str


_EgressKey = tuple[str, str, int, str, str, str, str, str]
_PairKey = tuple[str, str, str, str, str, str, str]
_TgwKey = tuple[str, str, str, str, str, str, str]


@dataclass(slots=True)
class _Buffer:
    """Temporarily store traffic records and NODATA intervals from one
    log.
    """

    egress: dict[_EgressKey, _Egress] = field(default_factory=dict)
    ingress: set[_PairKey] = field(default_factory=set)
    counts: dict[str, int] = field(default_factory=dict)
    listed: bool = False
    capped: bool = False
    fitted: bool = False
    nodata: dict[str, list[int]] = field(default_factory=dict)
    active: set[str] = field(default_factory=set)
    skipped: bool = False
    tgw: dict[_TgwKey, str] = field(default_factory=dict)
    page_capped: int = 0
    kept_after_ms: int | None = None
    stream_capped: bool = False
    object_capped: bool = False
    spent: bool = False

    def bump(self, counter: str, n: int = 1) -> None:
        self.counts[counter] = self.counts.get(counter, 0) + n


def _keep(store: dict[_EgressKey, _Egress], key: _EgressKey, record: _Egress) -> None:
    existing = store.get(key)
    if existing is None or (
        existing.subnet_id is None and record.subnet_id is not None
    ):
        store[key] = record


_LOG_STATUS = frozenset({"OK", "NODATA", "SKIPDATA"})
_ACTIONS = frozenset({"ACCEPT", "REJECT", "-"})


class _Parser:
    """Read VPC flow logs in log.fields order and validate source and
    format.
    """

    def __init__(
        self,
        log: FlowLog,
        structure: _Structure,
        window: tuple[int, int],
        budget: _Budget,
        *,
        drop: frozenset[str],
        api: str,
        read_start: int | None = None,
        owned: frozenset[str] | None = None,
    ) -> None:
        self.log = log
        self.structure = structure
        self.owned = owned
        self.start, self.end = window
        self.read_start = self.start if read_start is None else read_start
        self.budget = budget
        self.drop = drop
        self.api = api
        self.index = {name: i for i, name in enumerate(log.fields)}
        self.width = len(log.fields)
        self.vpc_node = (
            structure.node("vpc", log.region, log.vpc_id)
            if log.level == "vpc"
            else None
        )

    def _get(self, tokens: Sequence[str], name: str) -> str | None:
        at = self.index.get(name)
        return None if at is None else tokens[at]

    def _foreign(self, tokens: Sequence[str]) -> bool:
        log = self.log
        if log.level == "network_interface":
            return tokens[self.index["interface-id"]] != log.target
        if log.level == "subnet":
            subnet = self._get(tokens, "subnet-id")
            if subnet not in (None, "-"):
                return subnet != log.target
        else:
            vpc = self._get(tokens, "vpc-id")
            if vpc not in (None, "-"):
                return vpc != log.vpc_id
            subnet = self._get(tokens, "subnet-id")
            if subnet not in (None, "-"):
                node = self.structure.node("subnet", log.region, subnet)
                return (
                    node is not None
                    and self.structure.subnet_vpc.get(node) != self.vpc_node
                )
        if self.owned is not None:
            return tokens[self.index["interface-id"]] not in self.owned
        return False

    def _fits(self, tokens: Sequence[str]) -> bool:
        if len(tokens) != self.width:
            return False
        eni = tokens[self.index["interface-id"]]
        return (
            tokens[self.index["log-status"]] in _LOG_STATUS
            and tokens[self.index["action"]] in _ACTIONS
            and (eni == "-" or eni.startswith("eni-"))
            and _int(tokens[self.index["start"]]) is not None
        )

    def feed_cwl(self, tokens: Sequence[str], buf: _Buffer) -> None:
        if not tokens:
            return
        if not self._fits(tokens):
            self.budget.record()
            buf.bump("other_format")
            return
        buf.fitted = True
        self.feed(tokens, buf)

    def feed(self, tokens: Sequence[str], buf: _Buffer) -> None:
        self.budget.record()
        if len(tokens) != self.width:
            raise _Failed("format_mismatch", "필드 수가 선언과 다르다")
        status = tokens[self.index["log-status"]]
        if status == "SKIPDATA":
            start = _int(tokens[self.index["start"]])
            if start is not None and self.start <= start < self.end:
                buf.bump("skipdata")
            if start is not None and self.read_start <= start < self.end:
                buf.skipped = True
            return
        if status == "NODATA":
            start = _int(tokens[self.index["start"]])
            eni = tokens[self.index["interface-id"]]
            if start is not None and eni != "-":
                buf.nodata.setdefault(eni, []).append(start)
            return
        if status != "OK":
            return
        if self._foreign(tokens):
            buf.bump("other_log")
            return
        start = _int(tokens[self.index["start"]])
        if start is not None and self.read_start <= start < self.end:
            active = tokens[self.index["interface-id"]]
            if active != "-":
                buf.active.add(active)
        if tokens[self.index["action"]] != "ACCEPT":
            return
        if start is None:
            raise _Failed("format_mismatch", "start 가 수가 아니다")
        if not self.start <= start < self.end:
            return
        eni, proto, src, sport, dst, dport = (
            tokens[self.index[name]]
            for name in (
                "interface-id",
                "protocol",
                "srcaddr",
                "srcport",
                "dstaddr",
                "dstport",
            )
        )
        if "-" in (eni, proto, src, sport, dst, dport):
            return
        path = tokens[self.index["traffic-path"]]
        direction = self._get(tokens, "flow-direction") or (
            "egress" if path != "-" else "ingress"
        )
        region = self.log.region
        if direction == "ingress":
            buf.ingress.add((region, eni, proto, dst, dport, src, sport))
            return
        if direction != "egress" or path not in PATH_NAMES:
            return
        attr = self._attribute(tokens, path, dst, buf)
        if attr is None:
            return
        subnet_id, proof = attr
        key = (region, eni, start, proto, src, sport, dst, dport)
        _keep(buf.egress, key, _Egress(subnet_id, proof, self.api, self.log.id))

    def _attribute(
        self, tokens: Sequence[str], path: str, dst: str, buf: _Buffer
    ) -> tuple[str | None, tuple[str, str, str | None]] | None:
        log = self.log
        if log.level == "subnet":
            native: str | None = log.target
        else:
            native = self._get(tokens, "subnet-id")
        if native is None:
            if self.vpc_node is None:
                return None
            outcome, proof = self.structure.attribute_vpc(self.vpc_node, path, dst)
            subnet_id = None
        else:
            if native == "-":
                return None
            subnet_id = self.structure.node("subnet", log.region, native)
            if subnet_id is None:
                buf.bump("unknown_subnet")
                return None
            if subnet_id in self.drop:
                return None
            outcome, proof = self.structure.attribute(subnet_id, path, dst)
        if outcome == "skip":
            return None
        if proof is None:
            buf.bump(outcome)
            return None
        return subnet_id, proof


class _TgwParser:
    """Read TGW packets-lost-* fields and count discarded packets by
    cause.
    """

    def __init__(
        self, log: FlowLog, window: tuple[int, int], budget: _Budget, *, api: str
    ) -> None:
        self.log = log
        self.start, self.end = window
        self.budget = budget
        self.api = api
        self.index = {name: i for i, name in enumerate(log.fields)}
        self.width = len(log.fields)

    def _get(self, tokens: Sequence[str], name: str) -> str | None:
        at = self.index.get(name)
        return None if at is None else tokens[at]

    def _fits(self, tokens: Sequence[str]) -> bool:
        if len(tokens) != self.width:
            return False
        attachment = tokens[self.index["tgw-attachment-id"]]
        return (
            tokens[self.index["log-status"]] in _LOG_STATUS
            and (attachment == "-" or attachment.startswith("tgw-attach-"))
            and _int(tokens[self.index["start"]]) is not None
        )

    def feed_cwl(self, tokens: Sequence[str], buf: _Buffer) -> None:
        if not tokens:
            return
        if not self._fits(tokens):
            self.budget.record()
            buf.bump("other_format")
            return
        buf.fitted = True
        self.feed(tokens, buf)

    def feed(self, tokens: Sequence[str], buf: _Buffer) -> None:
        self.budget.record()
        if len(tokens) != self.width:
            raise _Failed("format_mismatch", "필드 수가 선언과 다르다")
        status = tokens[self.index["log-status"]]
        start = _int(tokens[self.index["start"]])
        if status == "SKIPDATA":
            if start is not None and self.start <= start < self.end:
                buf.bump("skipdata")
            return
        if status != "OK":
            return
        if start is None:
            raise _Failed("format_mismatch", "start 가 수가 아니다")
        if not self.start <= start < self.end:
            return
        for name, counter in _TGW_LOST:
            lost = _int(self._get(tokens, name) or "-")
            if lost:
                buf.bump(counter, lost)
        key = tuple(
            tokens[self.index[name]]
            for name in (
                "tgw-attachment-id",
                "tgw-pair-attachment-id",
                "protocol",
                "srcaddr",
                "srcport",
                "dstaddr",
                "dstport",
            )
        )
        if "-" in key:
            return
        tgw_key: _TgwKey = key  # type: ignore[assignment]
        existing = buf.tgw.get(tgw_key)
        buf.tgw[tgw_key] = self.api if existing is None else min(existing, self.api)


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None




def _error_why(exc: ClientError | BotoCoreError) -> tuple[str, str | None]:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {}) or {}
        code = str(error.get("Code") or "")
        message = str(error.get("Message") or "").lower()
        if code in _DENIED:
            return ("kms" if "kms" in message else "access_denied"), None
        if code.startswith("KMS."):
            return "kms", None
        if code in _NOT_FOUND:
            return "not_found", None
        if code in _THROTTLED:
            return "throttled", None
        return "read_error", f"오류 코드 {code or '없음'}"
    return "read_error", f"오류 코드 {type(exc).__name__}"


class _Clients:
    def __init__(self, factory: Callable[[str, str, str], Any]) -> None:
        self._factory = factory
        self._cache: dict[tuple[str, str, str], Any] = {}

    def get(self, account_id: str, region: str, service: str) -> Any:
        key = (account_id, region, service)
        if key not in self._cache:
            try:
                self._cache[key] = self._factory(account_id, region, service)
            except ReadOnlyViolation:
                raise
            except Exception as exc:
                operation = getattr(exc, "operation_name", None)
                label = f"sts:{operation}" if operation else "nettopo:client_factory"
                code = (
                    exc.response.get("Error", {}).get("Code")
                    if isinstance(exc, ClientError)
                    else type(exc).__name__
                )
                raise _Failed("read_error", f"진입 실패 {label} {code}") from exc
        return self._cache[key]




def _read_cwl(
    log: FlowLog,
    clients: _Clients,
    budget: _Budget,
    window: tuple[int, int],
    parser: _Parser | _TgwParser,
    buf: _Buffer,
) -> None:
    name = str(log.destination)
    client = clients.get(log.account_id, log.region, "logs")
    group = _find_group(client, budget, name)
    if str(group.get("logGroupClass") or "").upper() == "INFREQUENT_ACCESS":
        raise _Failed("log_class_infrequent")
    streams = _streams(client, budget, name, window[0] - STREAM_SLACK_SECONDS, buf)
    for stream in sorted(streams):
        _events(client, budget, name, stream, window, parser, buf)
    if buf.counts.get("other_format") and not buf.fitted:
        raise _Failed("format_mismatch", "모든 줄이 선언된 형식과 다르다")


def _find_group(client: Any, budget: _Budget, name: str) -> dict[str, Any]:
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"logGroupNamePrefix": name}
        if token:
            kwargs["nextToken"] = token
        budget.call()
        response = client.describe_log_groups(**kwargs)
        for group in response.get("logGroups") or []:
            if group.get("logGroupName") == name:
                return dict(group)
        following = response.get("nextToken")
        if not following or following == token:
            raise _Failed("not_found")
        token = following


def _streams(
    client: Any, budget: _Budget, name: str, cutoff: int, buf: _Buffer
) -> list[str]:
    cutoff_ms = cutoff * 1000
    found: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "logGroupName": name,
            "orderBy": "LastEventTime",
            "descending": True,
        }
        if token:
            kwargs["nextToken"] = token
        budget.call()
        response = client.describe_log_streams(**kwargs)
        buf.listed = True
        for stream in response.get("logStreams") or []:
            last = stream.get("lastEventTimestamp")
            if last is None:
                if int(stream.get("creationTime") or 0) < cutoff_ms:
                    continue
            elif int(last) < cutoff_ms:
                return found
            if len(found) == MAX_STREAMS_PER_LOG:
                buf.capped = True
                buf.stream_capped = True
                return found
            found.append(str(stream.get("logStreamName")))
        following = response.get("nextToken")
        if not following or following == token:
            return found
        token = following


def _events(
    client: Any,
    budget: _Budget,
    name: str,
    stream: str,
    window: tuple[int, int],
    parser: _Parser | _TgwParser,
    buf: _Buffer,
) -> None:
    """Read CloudWatch Logs for the requested interval, newest pages
    first.

    Limit pages per log stream using MAX_PAGES_PER_STREAM. Record unread
    logs
    if the limit is reached before the query finishes.
    """
    start, end = window
    token: str | None = None
    oldest_ms: int | None = None
    for page in range(MAX_PAGES_PER_STREAM + 1):
        kwargs: dict[str, Any] = {
            "logGroupName": name,
            "logStreamName": stream,
            "startTime": start * 1000,
            "endTime": end * 1000,
            "startFromHead": False,
        }
        if token:
            kwargs["nextToken"] = token
        budget.call()
        response = client.get_log_events(**kwargs)
        following = response.get("nextBackwardToken")
        done = not following or following == token
        if page == MAX_PAGES_PER_STREAM and not done:
            buf.capped = True
            buf.page_capped += 1
            cut = end * 1000 if oldest_ms is None else oldest_ms
            if buf.kept_after_ms is None or cut > buf.kept_after_ms:
                buf.kept_after_ms = cut
            return
        for event in response.get("events") or []:
            stamp = event.get("timestamp")
            if isinstance(stamp, int) and (oldest_ms is None or stamp < oldest_ms):
                oldest_ms = stamp
            parser.feed_cwl(str(event.get("message") or "").split(), buf)
        if done:
            return
        token = following




def _bucket(arn: str) -> tuple[str, str] | None:
    if ":::" not in arn:
        return None
    rest = arn.split(":::", 1)[1]
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        return None
    prefix = prefix.strip("/")
    return bucket, f"{prefix}/" if prefix else ""


def _bucket_region(constraint: Any) -> str:
    if not constraint:
        return "us-east-1"
    if constraint == "EU":
        return "eu-west-1"
    return str(constraint)


def _folders(log: FlowLog, low: datetime, high: datetime, prefix: str) -> list[str]:
    account, region = log.account_id, log.region
    if log.hive:
        root = (
            f"{prefix}AWSLogs/aws-account-id={account}/aws-service=vpcflowlogs/"
            f"aws-region={region}/"
        )
    else:
        root = f"{prefix}AWSLogs/{account}/vpcflowlogs/{region}/"
    step = timedelta(hours=1) if log.per_hour else timedelta(days=1)
    at = low.replace(minute=0, second=0, microsecond=0)
    if not log.per_hour:
        at = at.replace(hour=0)
    folders = []
    while at <= high:
        if log.hive:
            folder = f"{root}year={at:%Y}/month={at:%m}/day={at:%d}/"
            if log.per_hour:
                folder += f"hour={at:%H}/"
        else:
            folder = f"{root}{at:%Y}/{at:%m}/{at:%d}/"
            if log.per_hour:
                folder += f"{at:%H}/"
        folders.append(folder)
        at += step
    return folders


def _read_s3(
    log: FlowLog,
    clients: _Clients,
    budget: _Budget,
    window: tuple[int, int],
    parser: _Parser | _TgwParser,
    buf: _Buffer,
) -> None:
    located = _bucket(str(log.destination or ""))
    if located is None:
        raise _Failed("not_found")
    bucket, prefix = located
    budget.call()
    location = clients.get(log.account_id, log.region, "s3").get_bucket_location(
        Bucket=bucket
    )
    client = clients.get(
        log.account_id, _bucket_region(location.get("LocationConstraint")), "s3"
    )
    start, end = window
    interval = log.interval or DEFAULT_INTERVAL
    low = datetime.fromtimestamp(
        start - S3_FILE_EARLY_SECONDS - interval, tz=timezone.utc
    )
    high = datetime.fromtimestamp(end + S3_FILE_LATE_SECONDS, tz=timezone.utc)
    stem = f"{log.account_id}_vpcflowlogs_{log.region}_{log.id}_"
    keys: list[str] = []
    span = (f"{low:%Y%m%dT%H%M}", f"{high:%Y%m%dT%H%M}")
    for folder in reversed(_folders(log, low, high, prefix)):
        keys = _list(client, budget, bucket, folder, stem, span, buf) + keys
        if len(keys) > MAX_OBJECTS_PER_LOG:
            buf.capped = True
            buf.object_capped = True
            keys = keys[-MAX_OBJECTS_PER_LOG:]
            break
    for key in reversed(keys):
        if key.endswith(".parquet"):
            raise _Failed("format_mismatch", "텍스트라는데 Parquet 객체다")
        budget.call()
        body = client.get_object(Bucket=bucket, Key=key)["Body"]
        try:
            _read_object(body, budget, parser, buf, log.fields)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()


def _list(
    client: Any,
    budget: _Budget,
    bucket: str,
    folder: str,
    stem: str,
    span: tuple[str, str],
    buf: _Buffer,
) -> list[str]:
    low, high = span
    keys: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": folder}
        if token:
            kwargs["ContinuationToken"] = token
        else:
            kwargs["StartAfter"] = f"{folder}{stem}{low}"
        budget.call()
        response = client.list_objects_v2(**kwargs)
        buf.listed = True
        for item in response.get("Contents") or []:
            key = str(item.get("Key") or "")
            name = key[len(folder) :]
            if not name.startswith(stem):
                return keys
            if name[len(stem) : len(stem) + 13] > high:
                return keys
            keys.append(key)
        following = response.get("NextContinuationToken")
        if not response.get("IsTruncated") or not following or following == token:
            return keys
        token = following


def _gunzip(body: Any, budget: _Budget) -> Iterator[bytes]:
    inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
    first = True
    try:
        while True:
            chunk = body.read(_READ_CHUNK)
            if first:
                first = False
                if chunk[:4] == b"PAR1":
                    raise _Failed("format_mismatch", "텍스트라는데 Parquet 객체다")
            if not chunk:
                break
            while chunk:
                data = inflater.decompress(chunk, _READ_CHUNK)
                if data:
                    budget.inflate(len(data))
                    yield data
                if inflater.eof:
                    chunk = inflater.unused_data
                    if chunk:
                        inflater = zlib.decompressobj(zlib.MAX_WBITS | 16)
                else:
                    chunk = inflater.unconsumed_tail
        tail = inflater.flush()
        if tail:
            budget.inflate(len(tail))
            yield tail
        if not inflater.eof:
            raise _Failed("format_mismatch", "gzip 이 중간에 끊겼다")
    except zlib.error as exc:
        raise _Failed("format_mismatch", "gzip 이 아니다") from exc


def _read_object(
    body: Any,
    budget: _Budget,
    parser: _Parser | _TgwParser,
    buf: _Buffer,
    fields: tuple[str, ...],
) -> None:
    pending = b""
    header = True
    for data in _gunzip(body, budget):
        pending += data
        *lines, pending = pending.split(b"\n")
        for line in lines:
            header = _line(line, header, parser, buf, fields)
    if pending.strip():
        _line(pending, header, parser, buf, fields)


def _line(
    raw: bytes,
    header: bool,
    parser: _Parser | _TgwParser,
    buf: _Buffer,
    fields: tuple[str, ...],
) -> bool:
    try:
        tokens = raw.decode("utf-8").split()
    except UnicodeDecodeError as exc:
        raise _Failed("format_mismatch", "글자가 UTF-8 이 아니다") from exc
    if not tokens:
        return header
    if header:
        if tuple(tokens) != fields:
            raise _Failed("format_mismatch", "머리줄이 선언된 필드 순서와 다르다")
        return False
    parser.feed(tokens, buf)
    return False




@dataclass(slots=True)
class _Outcome:
    used: bool
    why_code: str | None = None
    detail: str | None = None
    buf: _Buffer = field(default_factory=_Buffer)


def _read_one(
    log: FlowLog,
    structure: _Structure,
    clients: _Clients,
    budget: _Budget,
    window: tuple[int, int],
    drop: frozenset[str],
    owned: frozenset[str] | None = None,
) -> _Outcome:
    buf = _Buffer()
    cwl = log.destination_type == CWL
    api = API_CWL if cwl else API_S3
    parser: _Parser | _TgwParser
    read_range = reading_range(log, window)
    if log.level in VPC_LEVELS:
        parser = _Parser(
            log,
            structure,
            window,
            budget,
            drop=drop,
            api=api,
            read_start=read_range[0],
            owned=owned,
        )
    else:
        parser = _TgwParser(log, window, budget, api=api)
    reader = _read_cwl if cwl else _read_s3
    try:
        reader(log, clients, budget, read_range, parser, buf)
    except _Stop as stop:
        buf.spent = True
        return _Outcome(used=buf.listed, why_code=stop.code, buf=buf)
    except _Failed as failed:
        return _Outcome(used=False, why_code=failed.code, detail=failed.detail)
    except (ClientError, BotoCoreError) as exc:
        code, detail = _error_why(exc)
        if code == "throttled":
            return _Outcome(used=buf.listed, why_code=code, buf=buf)
        return _Outcome(used=False, why_code=code, detail=detail)
    return _Outcome(used=True, why_code="capped" if buf.capped else None, buf=buf)


def _cap_notes(buf: _Buffer, window: tuple[int, int]) -> list[str]:
    notes: list[str] = []
    if buf.page_capped and buf.kept_after_ms is not None:
        cut = datetime.fromtimestamp(buf.kept_after_ms // 1000, tz=timezone.utc)
        whole = buf.kept_after_ms < window[0] * 1000
        notes.append(
            f"스트림 {buf.page_capped}개가 쪽 한도({MAX_PAGES_PER_STREAM}쪽)에 닿아 최근 기록부터 읽고 "
            f"{cut:%Y-%m-%dT%H:%M:%SZ} 까지의 오래된 기록 일부를 읽지 않았다 — "
            + (
                f"판정 창 {(window[1] - window[0]) // 60}분은 다 읽었다"
                if whole
                else "판정 창 앞부분이 빠졌다"
            )
        )
    if buf.stream_capped:
        notes.append(
            f"스트림 한도({MAX_STREAMS_PER_LOG}개)에 닿아 마지막 기록이 더 오래된 스트림은 읽지 않았다"
        )
    if buf.object_capped:
        notes.append(
            f"객체 한도({MAX_OBJECTS_PER_LOG}개)에 닿아 최근 객체부터 읽고 더 오래된 객체는 읽지 않았다"
        )
    if buf.spent:
        notes.append(
            "실행 1회 읽기 예산(호출·기록·풀린 크기)이 다 떨어져 거기서 멈췄다 — 남은 기록은 읽지 않았다"
        )
    return notes




def _static(log: FlowLog, structure: _Structure) -> tuple[str, str | None] | None:
    if log.level not in VPC_LEVELS:
        return _static_tgw(log, structure)
    if log.destination_type == FIREHOSE:
        return "destination_firehose", None
    if (log.traffic_type or "").upper() == "REJECT":
        return "traffic_reject_only", None
    if is_default_format(log.fields):
        return "format_v2", None
    missing = missing_fields(log.fields)
    if missing:
        return "fields_missing", f"빠진 필드 {', '.join(missing)}"
    if log.level == "network_interface" and "subnet-id" not in log.fields:
        return "subnet_unattributable", None
    if log.level in ("vpc", "subnet") and _attached(log, structure) is None:
        return "shared_participant", None
    if not log.destination:
        return "not_found", None
    if log.destination_type not in (CWL, S3):
        return "read_error", f"모르는 목적지 종류 {log.destination_type or '없음'}"
    return None


def _static_tgw(log: FlowLog, structure: _Structure) -> tuple[str, str | None] | None:
    if log.destination_type == FIREHOSE:
        return "destination_firehose", None
    missing = missing_fields(log.fields, TGW_REQUIRED_FIELDS)
    if missing:
        return "fields_missing", f"빠진 필드 {', '.join(missing)}"
    if _attached(log, structure) is None:
        return "shared_participant", None
    if not log.destination:
        return "not_found", None
    if log.destination_type not in (CWL, S3):
        return "read_error", f"모르는 목적지 종류 {log.destination_type or '없음'}"
    return None


def _interval(log: FlowLog) -> int:
    return log.interval or DEFAULT_INTERVAL


def judged_window(log: FlowLog, window: tuple[int, int]) -> tuple[int, int]:
    lag = _interval(log) + NODATA_SLACK_SECONDS.get(log.destination_type, 600)
    return window[0] - lag, window[1] - lag


def reading_range(log: FlowLog, window: tuple[int, int]) -> tuple[int, int]:
    """Return query start and end times for the flow log type.

    Extend VPC flow log queries into the earlier interval needed to
    check
    NODATA. Keep the traffic evaluation window unchanged.
    """
    if log.level not in VPC_LEVELS:
        return window
    return judged_window(log, window)[0] - _interval(log), window[1]


def _covers(starts: Iterable[int], span: tuple[int, int], interval: int) -> bool:
    """Check whether NODATA covers the evaluation window within allowed
    gaps.
    """
    cursor, end = span
    for start in sorted(starts):
        if start + interval <= cursor:
            continue
        if start > cursor + NODATA_GAP_SECONDS:
            return False
        cursor = max(cursor, start + interval)
        if cursor >= end:
            return True
    return cursor >= end


_ATTACHED_TYPE = {
    "vpc": "vpc",
    "subnet": "subnet",
    "transit_gateway": "transit_gateway",
    "transit_gateway_attachment": "transit_gateway_attachment",
}


def _attached(log: FlowLog, structure: _Structure) -> str | None:
    node_type = _ATTACHED_TYPE.get(log.level)
    if node_type is None:
        return None
    return structure.node(node_type, log.region, log.target)


def _rank(log: FlowLog) -> tuple[bool, bool, bool, str]:
    return (
        "subnet-id" not in log.fields,
        (log.traffic_type or "").upper() != "ALL",
        log.destination_type != CWL,
        log.id,
    )




def read_flow_logs(
    snapshot: Snapshot,
    flow_logs: Sequence[FlowLog],
    client_factory: Callable[[str, str, str], Any],
    *,
    at: str,
) -> FlowLogReading:
    """Read flow logs and return traffic evidence and log query results.

    Check traffic over the same interval as metrics. Evaluate NODATA in
    a
    separate interval that accounts for log delivery delay.
    """
    structure = _Structure(snapshot)
    ws, we = _window(at)
    window = (int(ws.timestamp()), int(we.timestamp()))
    budget = _Budget()
    clients = _Clients(client_factory)
    run = _Run(structure, clients, budget, window)

    logs = sorted(flow_logs, key=lambda log: (log.account_id, log.region, log.id))
    groups: dict[tuple[str, str, str], int] = {}
    for log in logs:
        if log.level in VPC_LEVELS and log.destination_type == CWL:
            key = (log.account_id, log.region, str(log.destination))
            groups[key] = groups.get(key, 0) + 1
    run.shared = {key for key, n in groups.items() if n > 1}
    by_level: dict[str, dict[tuple[str, str], list[FlowLog]]] = {}
    tgw_logs: list[FlowLog] = []
    for log in logs:
        if log.level not in VPC_LEVELS:
            tgw_logs.append(log)
            continue
        group = log.vpc_id if log.level == "vpc" else log.target
        by_level.setdefault(log.level, {}).setdefault(
            (log.region, group or log.id), []
        ).append(log)

    for (region, _), group in sorted(by_level.get("vpc", {}).items()):
        run.group(group, variant="vpc", covered=False)
    for (region, subnet_native), group in sorted(by_level.get("subnet", {}).items()):
        subnet = structure.node("subnet", region, subnet_native)
        vpc = structure.subnet_vpc.get(subnet or "")
        run.group(group, variant="subnet", covered=vpc in run.covered_vpcs)
    for (region, _), group in sorted(by_level.get("network_interface", {}).items()):
        everything = structure.region_subnets.get(region, set())
        whole = bool(everything) and everything <= run.covered_subnets()
        run.group(group, variant="network_interface", covered=whole)
    for log in tgw_logs:
        run.tgw(log)
    run.silence()
    return run.finish()


class _Run:
    def __init__(
        self,
        structure: _Structure,
        clients: _Clients,
        budget: _Budget,
        window: tuple[int, int],
    ) -> None:
        self.structure = structure
        self.clients = clients
        self.budget = budget
        self.window = window
        self.results: dict[str, LogResult] = {}
        self.counts: dict[str, dict[str, int]] = {}
        self.egress: dict[_EgressKey, _Egress] = {}
        self.ingress: set[_PairKey] = set()
        self.covered_vpcs: set[str] = set()
        self._covered_subnets: set[str] = set()
        self.notes: dict[str, list[str]] = {}
        self.used_logs: dict[str, FlowLog] = {}
        self.nodata: dict[str, dict[str, list[int]]] = {}
        self.skipped: set[str] = set()
        self.active: set[str] = set()
        self.tgw_records: dict[_TgwKey, tuple[str, str]] = {}
        self.silences: list[Proof] = []
        self.shared: set[tuple[str, str, str]] = set()

    def covered_subnets(self) -> set[str]:
        subnets = set(self._covered_subnets)
        for vpc in self.covered_vpcs:
            subnets.update(self.structure.vpc_subnets.get(vpc, ()))
        return subnets

    def settle(
        self,
        log: FlowLog,
        *,
        used: bool,
        why_code: str | None,
        variant: str | None = None,
        detail: Sequence[str] = (),
    ) -> None:
        kind = VPC_FLOW_LOG if log.level in VPC_LEVELS else TGW_FLOW_LOG
        self.results[log.id] = LogResult(
            id=log.id,
            level=log.level,
            used=used,
            kind=kind,
            account_id=log.account_id,
            region=log.region,
            attached_to=_attached(log, self.structure),
            why_code=why_code,
            why=None if why_code is None else why_of(why_code, variant),
            detail=" · ".join(detail) or None,
        )

    def group(self, logs: list[FlowLog], *, variant: str, covered: bool) -> None:
        static = {log.id: _static(log, self.structure) for log in logs}
        dup_variant = (
            "region" if covered and variant == "network_interface" else variant
        )
        used: FlowLog | None = None
        if not covered:
            candidates = sorted(
                (log for log in logs if static[log.id] is None), key=_rank
            )
            drop = frozenset(self.covered_subnets())
            for log in candidates:
                if used is not None:
                    self.settle(
                        log,
                        used=False,
                        why_code="duplicate_covered",
                        variant=dup_variant,
                    )
                    continue
                outcome = _read_one(
                    log,
                    self.structure,
                    self.clients,
                    self.budget,
                    self.window,
                    drop,
                    self._owned(log),
                )
                if not outcome.used:
                    self.settle(
                        log,
                        used=False,
                        why_code=outcome.why_code,
                        variant=log.destination_type,
                        detail=[outcome.detail] if outcome.detail else [],
                    )
                    continue
                used = log
                self._merge(log, outcome)
        for log in logs:
            problem = static[log.id]
            if log.id in self.results:
                continue
            if covered or used is not None:
                detail = []
                if problem is not None:
                    detail.append(f"자체 문제 {problem[0]}")
                    if problem[1]:
                        detail.append(problem[1])
                self.settle(
                    log,
                    used=False,
                    why_code="duplicate_covered",
                    variant=dup_variant,
                    detail=detail,
                )
            elif problem is not None:
                self.settle(
                    log,
                    used=False,
                    why_code=problem[0],
                    variant=log.level,
                    detail=[problem[1]] if problem[1] else [],
                )

    def _merge(self, log: FlowLog, outcome: _Outcome) -> None:
        buf = outcome.buf
        for key, record in buf.egress.items():
            _keep(self.egress, key, record)
        self.ingress |= buf.ingress
        self.used_logs[log.id] = log
        self.nodata[log.id] = buf.nodata
        self.active |= buf.active
        if buf.skipped:
            self.skipped.add(log.id)
        self.counts[log.id] = dict(buf.counts)
        why_code = outcome.why_code
        notes: list[str] = _cap_notes(buf, self.window)
        attributable = log.level == "subnet" or "subnet-id" in log.fields
        if not attributable:
            if why_code is None:
                why_code = "subnet_unattributable"
            else:
                notes.append(_PEERING_ONLY_NOTE)
        if (log.interval or 0) >= 600:
            notes.append(_SLOW_NOTE)
        if not log.delivered:
            notes.append(_UNDELIVERED_NOTE)
        self.notes[log.id] = [*self.notes.get(log.id, ()), *notes]
        if log.level == "vpc" and attributable:
            vpc = self.structure.node("vpc", log.region, log.vpc_id)
            if vpc is not None:
                self.covered_vpcs.add(vpc)
        elif log.level == "subnet":
            subnet = self.structure.node("subnet", log.region, log.target)
            if subnet is not None:
                self._covered_subnets.add(subnet)
        self.results[log.id] = LogResult(
            id=log.id,
            level=log.level,
            used=True,
            kind=VPC_FLOW_LOG,
            account_id=log.account_id,
            region=log.region,
            attached_to=_attached(log, self.structure),
            why_code=why_code,
            why=None if why_code is None else why_of(why_code, log.level),
        )

    def tgw(self, log: FlowLog) -> None:
        problem = _static(log, self.structure)
        if problem is not None:
            self.settle(
                log,
                used=False,
                why_code=problem[0],
                variant=log.level,
                detail=[problem[1]] if problem[1] else [],
            )
            return
        outcome = _read_one(
            log, self.structure, self.clients, self.budget, self.window, frozenset()
        )
        if not outcome.used:
            self.settle(
                log,
                used=False,
                why_code=outcome.why_code,
                variant=log.destination_type,
                detail=[outcome.detail] if outcome.detail else [],
            )
            return
        buf = outcome.buf
        for key, api in buf.tgw.items():
            existing = self.tgw_records.get(key)
            if existing is None or (api, log.id) < existing:
                self.tgw_records[key] = (api, log.id)
        self.counts[log.id] = dict(buf.counts)
        self.notes[log.id] = [
            *_cap_notes(buf, self.window),
            *([] if log.delivered else [_UNDELIVERED_NOTE]),
        ]
        self.results[log.id] = LogResult(
            id=log.id,
            level=log.level,
            used=True,
            kind=TGW_FLOW_LOG,
            account_id=log.account_id,
            region=log.region,
            attached_to=_attached(log, self.structure),
            why_code=outcome.why_code,
            why=None
            if outcome.why_code is None
            else why_of(outcome.why_code, log.level),
        )

    def silence(self) -> None:
        """Add no-traffic evidence for paths using NODATA records.

        Require NODATA for every relevant ENI across the evaluation
        window.
        Do not add evidence if an ENI has traffic records or missing
        logs.
        """
        covering: dict[str, list[FlowLog]] = {}
        for log in self.used_logs.values():
            if log.level == "vpc":
                vpc = self.structure.node("vpc", log.region, log.vpc_id)
                subnets = self.structure.vpc_subnets.get(vpc or "", [])
            elif log.level == "subnet":
                subnet = self.structure.node("subnet", log.region, log.target)
                subnets = [subnet] if subnet else []
            else:
                continue
            for subnet in subnets:
                covering.setdefault(subnet, []).append(log)
        for subnet in sorted(covering):
            logs = covering[subnet]
            if len(logs) != 1:
                continue
            log = logs[0]
            result = self.results[log.id]
            if (
                result.why_code not in (None, "subnet_unattributable")
                or not log.delivered
                or (log.traffic_type or "").upper() != "ALL"
                or log.id in self.skipped
                or not self.nodata.get(log.id)
            ):
                continue
            table = self.structure.table_of(subnet)
            routes = [
                r
                for r in self.structure.routes.get(table or "", ())
                if r.kind in (IGW, PCX)
            ]
            if not routes:
                continue
            enis = self._enis(subnet, log)
            if not enis:
                continue
            span = judged_window(log, self.window)
            nodata = self.nodata[log.id]
            if any(eni in self.active for eni in enis) or not all(
                _covers(nodata.get(eni, ()), span, _interval(log)) for eni in enis
            ):
                continue
            api = API_CWL if log.destination_type == CWL else API_S3
            for route in routes:
                self.silences.append(
                    Proof(route.target_id, subnet, route.edge_id, 0, api)
                )

    def _owned(self, log: FlowLog) -> frozenset[str] | None:
        if log.destination_type != CWL or log.level == "network_interface":
            return None
        if (log.account_id, log.region, str(log.destination)) not in self.shared:
            return None
        if log.level == "subnet":
            if "subnet-id" in log.fields:
                return None
            name, native = "subnet-id", log.target
        else:
            if "vpc-id" in log.fields or "subnet-id" in log.fields:
                return None
            name, native = "vpc-id", log.vpc_id
        found = self._list_enis(log.account_id, log.region, name, native or "")
        if found is None:
            self.notes.setdefault(log.id, []).append(_SHARED_UNREAD_NOTE)
            return frozenset()
        return frozenset(found)

    def _enis(self, subnet: str, log: FlowLog) -> list[str]:
        _, account, region, native = split_node_id(subnet)
        found = self._list_enis(account or "", region or "", "subnet-id", native)
        if found is None:
            notes = self.notes.setdefault(log.id, [])
            if _ENI_UNREAD_NOTE not in notes:
                notes.append(_ENI_UNREAD_NOTE)
            return []
        return found

    def _list_enis(
        self, account: str, region: str, name: str, native: str
    ) -> list[str] | None:
        found: list[str] = []
        token: str | None = None
        try:
            client = self.clients.get(account, region, "ec2")
            while True:
                kwargs: dict[str, Any] = {
                    "Filters": [{"Name": name, "Values": [native]}]
                }
                if token:
                    kwargs["NextToken"] = token
                self.budget.call()
                response = client.describe_network_interfaces(**kwargs)
                found.extend(
                    str(eni.get("NetworkInterfaceId"))
                    for eni in response.get("NetworkInterfaces") or []
                    if eni.get("NetworkInterfaceId")
                )
                following = response.get("NextToken")
                if not following or following == token:
                    return sorted(found)
                token = following
        except (_Stop, _Failed, ClientError, BotoCoreError):
            return None

    def _crossings(self) -> list[Crossing]:
        found: dict[tuple[str, str], list[Any]] = {}
        for key in sorted(self.tgw_records):
            api, log_id = self.tgw_records[key]
            att, pair, proto, src, sport, dst, dport = key
            reply = (proto, dst, dport, src, sport)
            if (att, pair, *reply) in self.tgw_records or (
                pair,
                att,
                *reply,
            ) in self.tgw_records:
                ends = (min(att, pair), max(att, pair))
                slot = found.setdefault(ends, [0, api])
                slot[0] += 1
                slot[1] = min(slot[1], api)
            else:
                counts = self.counts.setdefault(log_id, {})
                counts["tgw_one_way"] = counts.get("tgw_one_way", 0) + 1
        crossings = []
        for ends, (pairs, api) in sorted(found.items()):
            nodes = tuple(
                sorted(
                    n
                    for n in (self.structure.attachments.get(e) for e in ends)
                    if n is not None
                )
            )
            if nodes:
                crossings.append(Crossing(nodes, pairs, api))
        return crossings

    def finish(self) -> FlowLogReading:
        proofs: dict[tuple[str, str, str | None], list[Any]] = {}
        for key in sorted(self.egress):
            record = self.egress[key]
            pair: _PairKey = (key[0], key[1], key[3], key[4], key[5], key[6], key[7])
            if pair in self.ingress:
                slot = proofs.setdefault(record.attr, [0, record.api])
                slot[0] += 1
                slot[1] = min(slot[1], record.api)
            else:
                counts = self.counts.setdefault(record.log_id, {})
                counts["egress_only"] = counts.get("egress_only", 0) + 1
        crossings = self._crossings()
        resources = []
        for log_id in sorted(self.results):
            result = self.results[log_id]
            if result.used:
                counts = self.counts.get(log_id, {})
                parts = [
                    f"{label} {counts[name]}"
                    for name, label in _COUNTERS
                    if counts.get(name)
                ]
                parts.extend(self.notes.get(log_id, ()))
                result = replace(result, detail=" · ".join(parts) or None)
            resources.append(result)
        return FlowLogReading(
            resources=tuple(resources),
            proofs=tuple(
                Proof(target, observed, route, slot[0], slot[1])
                for (target, observed, route), slot in sorted(
                    proofs.items(),
                    key=lambda item: (item[0][0], item[0][1], item[0][2] or ""),
                )
            ),
            calls=self.budget.calls,
            silences=tuple(
                sorted(
                    self.silences,
                    key=lambda p: (p.target_id, p.observed_on, p.route_id or ""),
                )
            ),
            crossings=tuple(crossings),
        )
