"""흐름 로그 판독기(S5 U6) — 왕복 짝 · 귀속 · 못 읽은 이유 · 고르는 순서.

규칙의 정본은 ADR 0008 「D4b 개정」(①~⑥ · ⑤-1 U6 확정값)과 ADR 0009 D2-4 · D5 추가 표다. 조합표의 칸
번호(1.1 · 11.4 …)를 테스트 이름 옆에 적는다 — 칸마다 최소 한 검사가 있어야 한다.

🔴 이 파일의 대부분은 **판독기가 초록 근거를 만들지 않아야 하는** 경우다. 「짝이 있으면 초록」만 재면
「아무 기록이나 초록」인 판독기도 통과한다(실패할 수 없는 어서션).
"""

from __future__ import annotations

import gzip
import io
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from botocore.exceptions import EndpointConnectionError
from fakes import (
    ACCOUNT,
    APP_IGW,
    APP_VPC,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    FakeAws,
    FakeLogs,
    FakeS3,
    client_error,
    factory,
    gzip_log,
)
from internal_docs import read_or_skip
from nettopo import flowlogs
from nettopo.awsguard import ReadOnlyViolation
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.flowlogs import (
    API_CWL,
    API_S3,
    WHY,
    FlowLogReading,
    Proof,
    read_flow_logs,
    why_of,
)
from nettopo.metrics import _window
from nettopo.model import Edge, Node, Scope, Snapshot, node_id
from nettopo.sources import V2_FIELDS, FlowLog

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schema/observation.schema.json").read_text())

AT = "2026-09-29T01:15:00Z"
WS, WE = (int(t.timestamp()) for t in _window(AT))
T = WS + 60  # 창 안의 기본 시작 시각
# 실제로 읽는 범위의 시작(VPC 로그 · CWL · 60초 간격) = 판정 창 시작 − 집계 간격(ADR 0008 「D4c」 ④). 짝은 [WS, WE) 만 쓴다.
RS = WS - 60 - flowlogs.NODATA_SLACK_SECONDS["cloud-watch-logs"] - 60

VPC = "vpc-0aaa1111bbbb0001"
PEER_VPC = "vpc-0bbb1111bbbb0002"
SUB1 = "subnet-0aaa1111bbbb0011"
SUB2 = "subnet-0aaa1111bbbb0012"
SUB3 = "subnet-0aaa1111bbbb0013"  # 메인 테이블을 쓴다(명시 연관 없음)
RTB_PUB = "rtb-0aaa1111bbbb0021"
RTB_MAIN = "rtb-0aaa1111bbbb0022"
IGW = "igw-0aaa1111bbbb0031"
NAT = "nat-0aaa1111bbbb0032"
PCX = "pcx-0aaa1111bbbb0033"
VPCE = "vpce-0aaa1111bbbb0034"
ACL = "acl-0aaa1111bbbb0035"
ENI1 = "eni-0aaa1111bbbb0041"
ENI2 = "eni-0aaa1111bbbb0042"
GROUP = "grp-flow-a"
BUCKET = "bkt-flow-a"

FIELDS: tuple[str, ...] = (
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
    "vpc-id",
    "subnet-id",
    "flow-direction",
    "traffic-path",
)
NO_SUBNET = tuple(f for f in FIELDS if f != "subnet-id")
TGW_FIELDS: tuple[str, ...] = (
    "version",
    "resource-type",
    "account-id",
    "tgw-id",
    "tgw-attachment-id",
    "tgw-src-vpc-account-id",
    "tgw-dst-vpc-account-id",
    "tgw-src-vpc-id",
    "tgw-dst-vpc-id",
    "tgw-src-subnet-id",
    "tgw-dst-subnet-id",
    "tgw-src-eni",
    "tgw-dst-eni",
    "tgw-src-az-id",
    "tgw-dst-az-id",
    "tgw-pair-attachment-id",
    "srcaddr",
    "dstaddr",
    "srcport",
    "dstport",
    "protocol",
    "packets",
    "bytes",
    "start",
    "end",
    "log-status",
    "type",
    "packets-lost-no-route",
    "packets-lost-blackhole",
    "packets-lost-mtu-exceeded",
    "packets-lost-ttl-expired",
    "tcp-flags",
    "region",
    "flow-direction",
    "pkt-src-aws-service",
    "pkt-dst-aws-service",
)


def nid(node_type: str, native: str) -> str:
    return node_id(node_type, ACCOUNT, REGION, native)


def route_id(rtb: str, target_type: str, target: str) -> str:
    return f"route|{nid('route_table', rtb)}|{nid(target_type, target)}"


IGW_ROUTE = route_id(RTB_PUB, "internet_gateway", IGW)
PCX_ROUTE = route_id(RTB_PUB, "vpc_peering_connection", PCX)
MAIN_PCX_ROUTE = route_id(RTB_MAIN, "vpc_peering_connection", PCX)


# ---- 구조 --------------------------------------------------------------------------------------


def world(
    *,
    pub_routes: dict[tuple[str, str], list[str]] | None = None,
    main_routes: dict[tuple[str, str], list[str]] | None = None,
    unresolved: tuple[str, ...] = (),
    assoc_state: str = "associated",
) -> Snapshot:
    """VPC 1개(10.0.0.0/16) · 서브넷 3개 · 라우트 테이블 2개. SUB1·SUB2 = 퍼블릭 테이블(명시 연관),
    SUB3 = 메인 테이블. 퍼블릭: 0/0·::/0 → IGW, 10.1/16 → pcx. 메인: 0/0 → NAT, 10.1/16 → pcx."""
    if pub_routes is None:
        pub_routes = {
            ("internet_gateway", IGW): ["0.0.0.0/0", "::/0"],
            ("vpc_peering_connection", PCX): ["10.1.0.0/16"],
        }
    if main_routes is None:
        main_routes = {
            ("nat_gateway", NAT): ["0.0.0.0/0"],
            ("vpc_peering_connection", PCX): ["10.1.0.0/16"],
        }
    vpc = nid("vpc", VPC)

    def node(node_type: str, native: str, **kwargs: Any) -> Node:
        return Node(
            id=nid(node_type, native),
            type=node_type,
            account_id=ACCOUNT,
            region=REGION,
            native_id=native,
            **kwargs,
        )

    nodes = [
        node("vpc", VPC, attributes={"cidr_blocks": ["10.0.0.0/16"]}),
        node("vpc", PEER_VPC, attributes={"cidr_blocks": ["10.1.0.0/16"]}),
        node("subnet", SUB1, parent_id=vpc, attributes={"cidr_block": "10.0.1.0/24"}),
        node("subnet", SUB2, parent_id=vpc, attributes={"cidr_block": "10.0.2.0/24"}),
        node("subnet", SUB3, parent_id=vpc, attributes={"cidr_block": "10.0.3.0/24"}),
        node(
            "route_table",
            RTB_PUB,
            parent_id=vpc,
            attributes={"main": False, "unresolved_refs": list(unresolved)},
        ),
        node("route_table", RTB_MAIN, parent_id=vpc, attributes={"main": True}),
        node("internet_gateway", IGW, parent_id=vpc),
        node("nat_gateway", NAT, parent_id=vpc),
        node("vpc_peering_connection", PCX),
        node("vpc_endpoint", VPCE, parent_id=vpc),
        node("network_acl", ACL, parent_id=vpc),
    ]
    edges = []
    for rtb, routes in ((RTB_PUB, pub_routes), (RTB_MAIN, main_routes)):
        for (target_type, target), destinations in routes.items():
            edges.append(
                Edge(
                    id=route_id(rtb, target_type, target),
                    type="route",
                    source=nid("route_table", rtb),
                    target=nid(target_type, target),
                    attributes={"destinations": destinations, "blackhole": False},
                )
            )
    # 네트워크 ACL 연관도 같은 `association` 종류로 들어온다(실제 수집 모양) — 판독기가 섞으면 안 된다.
    for subnet in (SUB1, SUB2, SUB3):
        source, target = nid("network_acl", ACL), nid("subnet", subnet)
        edges.append(
            Edge(
                id=f"association|{source}|{target}",
                type="association",
                source=source,
                target=target,
                attributes={"state": "associated"},
            )
        )
    for subnet in (SUB1, SUB2):
        source, target = nid("route_table", RTB_PUB), nid("subnet", subnet)
        edges.append(
            Edge(
                id=f"association|{source}|{target}",
                type="association",
                source=source,
                target=target,
                attributes={"state": assoc_state},
            )
        )
    return Snapshot(
        collected_at=AT,
        tool_version="0",
        scope=Scope(
            account_ids=(ACCOUNT,),
            regions=(REGION,),
            resource_types=SUPPORTED_RESOURCE_TYPES,
        ),
        nodes=tuple(nodes),
        edges=tuple(edges),
        coverage=(),
    )


# ---- 기록 --------------------------------------------------------------------------------------


def _values(**kw: Any) -> dict[str, str]:
    values = {
        "version": "5",
        "account-id": ACCOUNT,
        "interface-id": ENI1,
        "srcaddr": "10.0.1.10",
        "dstaddr": "8.8.8.8",
        "srcport": "40000",
        "dstport": "443",
        "protocol": "6",
        "packets": "1",
        "bytes": "60",
        "start": str(T),
        "end": str(T + 60),
        "action": "ACCEPT",
        "log-status": "OK",
        "vpc-id": VPC,
        "subnet-id": SUB1,
        "flow-direction": "egress",
        "traffic-path": "8",
    }
    values.update({k.replace("_", "-"): str(v) for k, v in kw.items()})
    return values


def line(fields: tuple[str, ...] = FIELDS, **kw: Any) -> str:
    values = _values(**kw)
    return " ".join(values[f] for f in fields)


def reply(fields: tuple[str, ...] = FIELDS, **kw: Any) -> str:
    """`kw` 로 만든 나간 기록의 답 — 같은 ENI, 5-튜플 뒤집음, 들어옴."""
    out = _values(**kw)
    back = {
        **kw,
        "srcaddr": out["dstaddr"],
        "dstaddr": out["srcaddr"],
        "srcport": out["dstport"],
        "dstport": out["srcport"],
        "flow_direction": "ingress",
        "traffic_path": "-",
    }
    return line(fields, **back)


def trip(fields: tuple[str, ...] = FIELDS, **kw: Any) -> list[str]:
    return [line(fields, **kw), reply(fields, **kw)]


# ---- 로그 설정 · 가짜 --------------------------------------------------------------------------


def fl(
    log_id: str = "fl-0000000000000001",
    *,
    level: str = "vpc",
    target: str = VPC,
    vpc_id: str | None = VPC,
    traffic: str | None = "ALL",
    dest_type: str = "cloud-watch-logs",
    dest: str | None = GROUP,
    fields: tuple[str, ...] = FIELDS,
    **kw: Any,
) -> FlowLog:
    return FlowLog(
        id=log_id,
        level=level,
        target=target,
        vpc_id=vpc_id,
        traffic_type=traffic,
        destination_type=dest_type,
        destination=dest,
        fields=fields,
        interval=kw.pop("interval", 60),
        account_id=kw.pop("account_id", ACCOUNT),
        region=kw.pop("region", REGION),
        **kw,
    )


def s3log(log_id: str = "fl-0000000000000002", **kw: Any) -> FlowLog:
    kw.setdefault("dest", f"arn:aws:s3:::{BUCKET}")
    kw.setdefault("file_format", "plain-text")
    return fl(log_id, dest_type="s3", **kw)


def cwl(
    *streams: list[str], group: str = GROUP, last: int | None = None, **kw: Any
) -> FakeLogs:
    """스트림마다 줄 목록. 이벤트 시각 = 그 기록의 start(ms)."""
    stamp = (WE - 60) * 1000 if last is None else last
    return FakeLogs(
        {
            group: {
                "streams": {
                    f"s-{i:02d}": {
                        "last": stamp,
                        "created": 0,
                        "events": [(_start_ms(msg), msg) for msg in lines],
                    }
                    for i, lines in enumerate(streams)
                }
            }
        },
        **kw,
    )


def _start_ms(message: str) -> int:
    tokens = message.split()
    # 필드 순서를 모르는 곳에서도 시각을 뽑는다 — 10자리 정수 중 창 근처 값.
    stamps = [int(t) for t in tokens if t.isdigit() and len(t) == 10]
    return (stamps[0] if stamps else T) * 1000


def s3_key(
    log: FlowLog,
    stamp: str = "20260929T0105Z",
    *,
    prefix: str = "",
    day: str = "2026/09/29",
    region: str = REGION,
) -> str:
    return (
        f"{prefix}AWSLogs/{ACCOUNT}/vpcflowlogs/{region}/{day}/"
        f"{ACCOUNT}_vpcflowlogs_{region}_{log.id}_{stamp}_abcd1234.log.gz"
    )


def s3_object(lines: list[str], fields: tuple[str, ...] = FIELDS, **kw: Any) -> bytes:
    return gzip_log([" ".join(fields), *lines], **kw)


class Clients:
    """서비스·리전별 가짜를 주는 팩토리. 부른 (계정, 리전, 서비스)를 적는다."""

    def __init__(
        self,
        logs: FakeLogs | None = None,
        s3: FakeS3 | None = None,
        *,
        fail: dict[str, Exception] | None = None,
    ) -> None:
        self.logs = logs or FakeLogs()
        self.s3 = s3 or FakeS3()
        self.fail = dict(fail or {})
        self.made: list[tuple[str, str, str]] = []

    def __call__(self, account_id: str, region: str, service: str) -> Any:
        self.made.append((account_id, region, service))
        if service in self.fail:
            raise self.fail[service]
        if service == "logs":
            return self.logs
        if service == "s3":
            return self.s3.client(region)
        raise AssertionError(f"판독기가 부를 서비스가 아니다: {service}")


def read(
    logs: list[FlowLog], clients: Clients, snapshot: Snapshot | None = None
) -> FlowLogReading:
    return read_flow_logs(snapshot or world(), logs, clients, at=AT)


def result(reading: FlowLogReading, log_id: str) -> Any:
    return next(r for r in reading.resources if r.id == log_id)


def igw_proof(pairs: int = 1, subnet: str = SUB1, api: str = API_CWL) -> Proof:
    return Proof(
        nid("internet_gateway", IGW), nid("subnet", subnet), IGW_ROUTE, pairs, api
    )


# ---- 계약 --------------------------------------------------------------------------------------


def test_why_keys_equal_schema_enum() -> None:
    """은퇴 코드(D4c — Parquet·TGW 대기)는 스키마에 남지만 엔진은 더 내지 않는다."""
    enum = set(SCHEMA["$defs"]["flow_log_why_code"]["enum"])
    assert set(WHY) | flowlogs.RETIRED_WHY == enum
    assert not set(WHY) & flowlogs.RETIRED_WHY


def test_adr_d5_codes_equal_schema_enum() -> None:
    """ADR 0009 D5 「추가 2026-09-28」 표가 이유 코드의 정본이다 — 취소선 줄은 뺀다."""
    text = read_or_skip("docs/adr/0009-optional-evidence-sources.md")
    section = text.split("**추가 2026-09-28 (S5 1단계 · 자원 단위 이유 코드).**", 1)[1]
    table = section.split("\n\n", 2)[1]
    codes = set(re.findall(r"^\| `([a-z0-9_]+)` \|", table, flags=re.MULTILINE))
    assert codes == set(SCHEMA["$defs"]["flow_log_why_code"]["enum"])


# API 이름과 정책 이름이 다른 것만(나머지는 `<서비스>:<API 이름>` 그대로다).
_POLICY_NAME = {"s3:ListObjectsV2": "s3:ListBucket"}


def test_required_actions_equal_the_calls_this_reader_makes() -> None:
    """U9 — 스택 템플릿·권한 문서가 대조하는 `REQUIRED_ACTIONS` 가 코드가 실제로 부르는 호출과 같은가.

    목록만 따로 고치면 두 방향으로 틀린다: 호출을 더했는데 목록에 없으면 고객이 파라미터를 켜도 그 로그가
    「권한 없음」이 되고, 목록에만 남은 액션은 쓰지 않는 권한을 고객 계정에 연다."""
    import ast

    import botocore.session
    from botocore import xform_name

    session = botocore.session.get_session()
    operations = {
        xform_name(name): f"{service}:{name}"
        for service in ("logs", "s3")
        for name in session.get_service_model(service).operation_names
    }
    tree = ast.parse((ROOT / "engine/src/nettopo/flowlogs.py").read_text())
    called = {
        operations[node.func.attr]
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in operations
    }
    assert len(called) >= 6, f"호출을 못 읽었다(대조군): {sorted(called)}"
    assert {_POLICY_NAME.get(a, a) for a in called} == set(flowlogs.REQUIRED_ACTIONS)
    assert list(flowlogs.REQUIRED_ACTIONS) == sorted(flowlogs.REQUIRED_ACTIONS)


def _validate(doc: dict[str, Any]) -> None:
    jsonschema.validate(
        doc, {"$ref": "#/$defs/flow_log_resource", "$defs": SCHEMA["$defs"]}
    )


def test_every_resource_validates_and_leaks_no_destination() -> None:
    """모든 이유 코드를 한 번씩 만들어 스키마로 검사한다. 로그 그룹·버킷 이름은 어디에도 없어야 한다."""
    logs = [
        fl("fl-01"),
        fl("fl-02", vpc_id=PEER_VPC, target=PEER_VPC, fields=V2_FIELDS),
        fl("fl-03", level="transit_gateway", target="tgw-0aaa", vpc_id=None),
        fl(
            "fl-04",
            level="network_interface",
            target=ENI1,
            vpc_id=None,
            fields=NO_SUBNET,
        ),
        s3log("fl-05", file_format="parquet", vpc_id="vpc-0zzz", target="vpc-0zzz"),
    ]
    reading = read(logs, Clients(cwl(trip())))
    for res in reading.resources:
        doc = res.to_dict()
        _validate(doc)
        blob = json.dumps(doc, ensure_ascii=False)
        assert GROUP not in blob and BUCKET not in blob
    for code in WHY:
        assert GROUP not in why_of(code) and "arn:" not in why_of(code)


# ---- 왕복 짝(조합표 11) ------------------------------------------------------------------------


def test_round_trip_is_a_subnet_proof_on_the_route_row() -> None:
    """11.1 — 나감(경로 8) + 같은 ENI 로 돌아온 답 → 그 서브넷 · 그 라우트 선의 근거."""
    clients = Clients(cwl(trip()))
    reading = read([fl()], clients)
    assert reading.proofs == (igw_proof(),)
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code, res.detail) == (True, None, None)
    assert res.attached_to == nid("vpc", VPC)
    assert res.to_dict() == {
        "id": "fl-0000000000000001",
        "level": "vpc",
        "attached_to": nid("vpc", VPC),
        "used": True,
    }
    assert reading.calls == len(clients.logs.calls)


def test_egress_without_reply_is_not_a_proof() -> None:
    """11.4 — 공인 IP 없는 인스턴스 · 한 방향 UDP. 🔴 핵심 거짓 초록 대조군."""
    reading = read([fl()], Clients(cwl([line()])))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "나간 기록만 1"


def test_ingress_alone_is_not_a_proof() -> None:
    """11.8"""
    reading = read([fl()], Clients(cwl([reply()])))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail is None


def test_reply_on_another_eni_is_not_a_pair() -> None:
    back = reply().replace(ENI1, ENI2)
    reading = read([fl()], Clients(cwl([line(), back])))
    assert reading.proofs == ()


def test_reply_with_other_ports_is_not_a_pair() -> None:
    back = reply(srcport=40001)
    reading = read([fl()], Clients(cwl([line(), back])))
    assert reading.proofs == ()


@pytest.mark.parametrize(
    ("start", "counted"),
    [(WS, True), (WE - 1, True), (WS - 1, False), (WE, False)],
)
def test_window_is_start_inclusive_end_exclusive(start: int, counted: bool) -> None:
    """11.12 — 창은 [ws, we). 이벤트 시각은 창 안(가짜가 CWL 필터를 통과시킨다)이고 레코드 start 만 다르다."""
    lines = trip(start=start)
    logs = FakeLogs(
        {
            GROUP: {
                "streams": {
                    "s": {"last": WE * 1000, "events": [(T * 1000, m) for m in lines]}
                }
            }
        }
    )
    reading = read([fl()], Clients(logs))
    assert reading.proofs == ((igw_proof(),) if counted else ())


def test_reject_is_never_a_flow() -> None:
    lines = [line(action="REJECT"), reply()]
    reading = read([fl()], Clients(cwl(lines)))
    assert reading.proofs == ()


def test_skipdata_counted_nodata_ignored() -> None:
    """4.7"""
    skip = line(
        log_status="SKIPDATA", srcaddr="-", dstaddr="-", srcport="-", dstport="-"
    )
    nodata = line(log_status="NODATA", srcaddr="-", dstaddr="-")
    reading = read([fl()], Clients(cwl([skip, nodata, *trip()])))
    assert reading.proofs == (igw_proof(),)
    assert result(reading, "fl-0000000000000001").detail == "SKIPDATA 1"


def test_same_record_twice_counts_once() -> None:
    """같은 기록이 두 스트림에 있어도 1 쌍(ENI·start·5-튜플이 같다)."""
    reading = read([fl()], Clients(cwl(trip(), trip())))
    assert reading.proofs == (igw_proof(1),)


def test_two_flows_are_two_pairs() -> None:
    reading = read([fl()], Clients(cwl([*trip(), *trip(srcport=40001)])))
    assert reading.proofs == (igw_proof(2),)


def test_direction_inferred_without_flow_direction_field() -> None:
    fields = tuple(f for f in FIELDS if f != "flow-direction")
    reading = read([fl(fields=fields)], Clients(cwl(trip(fields))))
    assert reading.proofs == (igw_proof(),)


def test_dash_fields_are_skipped() -> None:
    lines = [line(srcport="-"), reply(srcport="-")]
    reading = read([fl()], Clients(cwl(lines)))
    assert reading.proofs == ()


# 같은 로그 그룹에 쓰는 다른 흐름 로그의 줄 — 기본 형식 v2(14필드). 시각은 창 안이다(창 밖이면 읽기 전에 걸러져 미측정).
V2_LINE = line(V2_FIELDS)


def test_cwl_other_format_lines_are_skipped_and_counted() -> None:
    """#36 리뷰 1 — 같은 스트림에 다른 흐름 로그의 줄이 섞여도 그 로그를 버리지 않는다(ADR 0008 ⑤).
    폭이 다른 줄 · 빈 줄 · 폭은 같은데 순서가 다른 줄을 건너뛰고, 건너뛴 수를 `detail` 에 적는다."""
    shuffled = line(tuple(reversed(FIELDS)))
    assert len(shuffled.split()) == len(FIELDS)
    reading = read([fl()], Clients(cwl([*trip(), V2_LINE, "", shuffled])))
    assert reading.proofs == (igw_proof(),)
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, None)
    assert res.detail == "형식 다른 줄 2"


def test_cwl_all_lines_other_format_is_a_mismatch() -> None:
    """맞는 줄이 하나도 없으면 선언이 틀렸다 — 조용히 「기록 없음」으로 두지 않는다."""
    reading = read([fl()], Clients(cwl([V2_LINE, line() + " extra"])))
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (False, "format_mismatch")


def test_s3_token_count_mismatch_discards_the_log() -> None:
    """S3 는 머리줄로 형식을 확인했다 — 그 뒤 필드 수가 다른 줄은 칸을 잘못 자른 것이므로 로그 전체를 버린다."""
    log = s3log()
    body = s3_object([*trip(), line() + " extra"])
    reading = read([log], _s3_world(log, {s3_key(log): body}))
    assert reading.proofs == ()
    res = result(reading, log.id)
    assert (res.used, res.why_code) == (False, "format_mismatch")


# ---- 귀속(조합표 11 · 1) -----------------------------------------------------------------------


def test_path2_with_gateway_endpoint_route_is_ambiguous() -> None:
    """11.2 — 경로 2 는 IGW 또는 게이트웨이 엔드포인트. 그 테이블에 `pl-` → vpce 가 있으면 세지 않는다."""
    routes = {
        ("internet_gateway", IGW): ["0.0.0.0/0"],
        ("vpc_endpoint", VPCE): ["pl-0aaa1111"],
    }
    reading = read([fl()], Clients(cwl(trip(traffic_path=2))), world(pub_routes=routes))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "모호 1"


def test_path2_without_gateway_endpoint_route_counts() -> None:
    reading = read([fl()], Clients(cwl(trip(traffic_path=2))))
    assert reading.proofs == (igw_proof(),)


@pytest.mark.parametrize("path", ["1", "3", "6", "7"])
def test_other_paths_are_ignored(path: str) -> None:
    """11.3 · 11.11"""
    reading = read([fl()], Clients(cwl(trip(traffic_path=path))))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail is None


def test_path_disagreeing_with_route_is_mismatch() -> None:
    """11.10 — 경로 8(IGW)인데 목적지의 최장 일치는 피어링."""
    reading = read([fl()], Clients(cwl(trip(dstaddr="10.1.0.5"))))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "어긋남 1"


def test_destination_inside_vpc_is_mismatch() -> None:
    reading = read([fl()], Clients(cwl(trip(dstaddr="10.0.2.7"))))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "어긋남 1"


def test_unknown_subnet_is_counted_not_attributed() -> None:
    reading = read([fl()], Clients(cwl(trip(subnet_id="subnet-0fff"))))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "구조에 없는 서브넷 1"


def test_main_table_subnet_uses_main_routes() -> None:
    """명시 연관이 없는 서브넷은 메인 테이블 — 0/0 이 NAT 이므로 경로 8 은 어긋남, 경로 4 는 증명."""
    lines = [
        *trip(subnet_id=SUB3, srcaddr="10.0.3.9"),
        *trip(subnet_id=SUB3, srcaddr="10.0.3.9", dstaddr="10.1.0.5", traffic_path=4),
    ]
    reading = read([fl()], Clients(cwl(lines)))
    assert reading.proofs == (
        Proof(
            nid("vpc_peering_connection", PCX),
            nid("subnet", SUB3),
            MAIN_PCX_ROUTE,
            1,
            API_CWL,
        ),
    )
    assert result(reading, "fl-0000000000000001").detail == "어긋남 1"


def test_dead_association_means_unknown_table() -> None:
    """명시 연관이 있는데 `associated` 가 아니면 메인으로 넘기지 않는다 — 어느 테이블인지 모른다."""
    reading = read([fl()], Clients(cwl(trip())), world(assoc_state="disassociated"))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "어긋남 1"


def test_unresolved_peering_makes_path4_ambiguous() -> None:
    """구조에 없는 `pcx-` 로 가는 라우트가 있으면 경로 4 가 어느 피어링인지 모른다."""
    lines = trip(dstaddr="10.1.0.5", traffic_path=4)
    reading = read([fl()], Clients(cwl(lines)), world(unresolved=("pcx-0fff",)))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "모호 1"


def test_unresolved_egress_only_igw_does_not_blur_ipv4() -> None:
    reading = read([fl()], Clients(cwl(trip())), world(unresolved=("eigw-0fff",)))
    assert reading.proofs == (igw_proof(),)


def test_unresolved_egress_only_igw_blurs_ipv6() -> None:
    """11.5 — IPv6 경로 8 은 송신 전용 IGW 일 수도 있다(수집하지 않아 늘 unresolved)."""
    lines = trip(srcaddr="2600:1f18::10", dstaddr="2600:9000::1")
    reading = read([fl()], Clients(cwl(lines)), world(unresolved=("eigw-0fff",)))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "모호 1"


def test_ipv6_longest_match() -> None:
    """11.5"""
    lines = trip(srcaddr="2600:1f18::10", dstaddr="2600:9000::1")
    reading = read([fl()], Clients(cwl(lines)))
    assert reading.proofs == (igw_proof(),)


def test_longest_prefix_picks_the_line() -> None:
    """11.9 — 같은 테이블에서 8.8.8.8 은 IGW, 10.1.0.5 는 피어링."""
    lines = [*trip(), *trip(dstaddr="10.1.0.5", traffic_path=5)]
    reading = read([fl()], Clients(cwl(lines)))
    assert reading.proofs == (
        igw_proof(),
        Proof(
            nid("vpc_peering_connection", PCX),
            nid("subnet", SUB1),
            PCX_ROUTE,
            1,
            API_CWL,
        ),
    )


def test_subnetless_vpc_log_proves_peering_only() -> None:
    """1.1 — `subnet-id` 없는 VPC 로그: 모든 테이블이 같은 pcx 로 보내면 VPC 단위 피어링 근거, IGW 는 건너뛴다."""
    lines = [*trip(NO_SUBNET), *trip(NO_SUBNET, dstaddr="10.1.0.5", traffic_path=4)]
    reading = read([fl(fields=NO_SUBNET)], Clients(cwl(lines)))
    assert reading.proofs == (
        Proof(nid("vpc_peering_connection", PCX), nid("vpc", VPC), None, 1, API_CWL),
    )
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, "subnet_unattributable")
    assert res.why == why_of("subnet_unattributable", "vpc")
    assert res.detail is None


def test_subnetless_vpc_log_needs_every_table_to_agree() -> None:
    main = {("nat_gateway", NAT): ["0.0.0.0/0"]}
    lines = trip(NO_SUBNET, dstaddr="10.1.0.5", traffic_path=4)
    reading = read([fl(fields=NO_SUBNET)], Clients(cwl(lines)), world(main_routes=main))
    assert reading.proofs == ()
    assert result(reading, "fl-0000000000000001").detail == "어긋남 1"


def test_subnet_level_log_attributes_to_its_target() -> None:
    """1.2 — 서브넷 단위 로그는 로그 대상이 서브넷이다(필드가 없어도)."""
    log = fl(level="subnet", target=SUB1, fields=NO_SUBNET)
    reading = read([log], Clients(cwl(trip(NO_SUBNET))))
    assert reading.proofs == (igw_proof(),)
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code, res.attached_to) == (
        True,
        None,
        nid("subnet", SUB1),
    )


def test_eni_log_with_subnet_id_is_read() -> None:
    """1.3"""
    log = fl(level="network_interface", target=ENI1, vpc_id=None)
    reading = read([log], Clients(cwl(trip())))
    assert reading.proofs == (igw_proof(),)
    assert result(reading, "fl-0000000000000001").attached_to is None


# ---- 읽기 전 판정(API 0) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("log", "code", "detail"),
    [
        # TGW 로그(D4c ③) — VPC 형식 필드로는 짝을 못 찾는다 · 구조에 없는 TGW 는 남의 것이다
        (
            fl(level="transit_gateway", target="tgw-0aaa", vpc_id=None),
            "fields_missing",
            "빠진 필드 tgw-attachment-id, tgw-pair-attachment-id",
        ),
        (
            fl(
                level="transit_gateway_attachment",
                target="tgw-attach-0a",
                vpc_id=None,
                fields=TGW_FIELDS,
            ),
            "shared_participant",
            None,
        ),
        (
            fl(
                level="transit_gateway",
                target="tgw-0aaa",
                vpc_id=None,
                fields=TGW_FIELDS,
                dest_type="kinesis-data-firehose",
            ),
            "destination_firehose",
            None,
        ),
        (fl(dest_type="kinesis-data-firehose"), "destination_firehose", None),
        (fl(traffic="REJECT"), "traffic_reject_only", None),
        (fl(fields=V2_FIELDS), "format_v2", None),
        (fl(fields=()), "format_v2", None),
        (
            fl(fields=tuple(f for f in FIELDS if f != "traffic-path")),
            "fields_missing",
            "빠진 필드 traffic-path",
        ),
        (
            fl(level="network_interface", target=ENI1, vpc_id=None, fields=NO_SUBNET),
            "subnet_unattributable",
            None,
        ),
        (fl(target="vpc-0fff", vpc_id="vpc-0fff"), "shared_participant", None),
        (
            fl(level="subnet", target="subnet-0fff", vpc_id=None),
            "shared_participant",
            None,
        ),
        (fl(dest=None), "not_found", None),
        (fl(dest_type="elsewhere"), "read_error", "모르는 목적지 종류 elsewhere"),
    ],
)
def test_static_verdicts_make_no_calls(
    log: FlowLog, code: str, detail: str | None
) -> None:
    """1.5 · 2.3 · 3.6 · 3.7 · 4.1 · 4.3 · 4.4 · 10.3 — 읽기 전에 정해지고 API 를 부르지 않는다."""
    clients = Clients()
    reading = read([log], clients)
    res = result(reading, log.id)
    assert (res.used, res.why_code, res.detail) == (False, code, detail)
    assert clients.made == [] and reading.calls == 0
    _validate(res.to_dict())


def test_tgw_log_does_not_hide_vpc_use() -> None:
    """1.5 · 12.1 — TGW 로그가 못 읽음이어도 VPC 로그는 읽는다."""
    tgw = fl(
        "fl-0000000000000009", level="transit_gateway", target="tgw-0aaa", vpc_id=None
    )
    reading = read([tgw, fl()], Clients(cwl(trip())))
    assert result(reading, tgw.id).why_code == "fields_missing"
    assert result(reading, tgw.id).kind == "tgw_flow_log"
    assert result(reading, "fl-0000000000000001").used is True
    assert reading.proofs == (igw_proof(),)


# ---- 고르는 순서 · 덮음(조합표 8 · 1.4) --------------------------------------------------------


def test_cwl_first_then_s3_duplicate_covered() -> None:
    """8.1 — CWL + S3 둘 다 좋으면 CWL(사용자 결정 2026-09-29). S3 는 부르지도 않는다. id 가 아니라 순서 규칙이 고른다."""
    clients = Clients(cwl(trip()))
    reading = read([fl("fl-0000000000000009"), s3log("fl-0000000000000001")], clients)
    assert result(reading, "fl-0000000000000009").used is True
    dup = result(reading, "fl-0000000000000001")
    assert (dup.used, dup.why_code, dup.why) == (
        False,
        "duplicate_covered",
        why_of("duplicate_covered", "vpc"),
    )
    assert clients.s3.calls == []


def test_subnet_field_beats_destination() -> None:
    """D2-4 ①-2 — `subnet-id` 있는 S3 로그가 없는 CWL 로그보다 먼저다(서브넷 선을 가린다)."""
    key = s3_key(s3log())
    s3 = FakeS3({BUCKET: {"region": REGION, "objects": {key: s3_object(trip())}}})
    clients = Clients(cwl(trip(NO_SUBNET)), s3)
    reading = read([fl(fields=NO_SUBNET), s3log()], clients)
    assert result(reading, "fl-0000000000000002").used is True
    assert result(reading, "fl-0000000000000001").why_code == "duplicate_covered"
    assert clients.logs.calls == []
    assert reading.proofs == (igw_proof(api=API_S3),)


def test_all_beats_accept() -> None:
    accept = fl("fl-0000000000000001", traffic="ACCEPT")
    everything = fl("fl-0000000000000009", dest="grp-other")
    logs = FakeLogs(
        {
            "grp-other": {"streams": {"s": {"last": WE * 1000, "events": []}}},
            GROUP: {"streams": {"s": {"last": WE * 1000, "events": []}}},
        }
    )
    reading = read([accept, everything], Clients(logs))
    assert result(reading, everything.id).used is True
    assert result(reading, accept.id).why_code == "duplicate_covered"


def test_failed_candidate_keeps_its_reason_and_next_is_read() -> None:
    """7.5 — CWL 읽기 거부 → 이유는 CWL 문장(스택 파라미터 이름), 다음 후보 S3 를 읽는다."""
    key = s3_key(s3log())
    s3 = FakeS3({BUCKET: {"region": REGION, "objects": {key: s3_object(trip())}}})
    denied = FakeLogs(
        errors={
            "describe_log_groups": (
                1,
                client_error("AccessDeniedException", "DescribeLogGroups"),
            )
        }
    )
    reading = read([fl(), s3log()], Clients(denied, s3))
    cw = result(reading, "fl-0000000000000001")
    assert (cw.used, cw.why_code, cw.detail) == (False, "access_denied", None)
    assert "FlowLogGroupNamePrefixes" in cw.why
    assert result(reading, "fl-0000000000000002").used is True
    assert reading.proofs == (igw_proof(api=API_S3),)


def test_static_sibling_of_used_log_is_covered_with_its_problem() -> None:
    v2 = fl("fl-0000000000000009", fields=V2_FIELDS)
    reading = read([fl(), v2], Clients(cwl(trip())))
    res = result(reading, v2.id)
    assert (res.why_code, res.detail) == ("duplicate_covered", "자체 문제 format_v2")


def test_all_candidates_fail_each_keeps_reason() -> None:
    v2 = fl("fl-0000000000000009", fields=V2_FIELDS)
    denied = FakeLogs(
        errors={
            "describe_log_groups": (
                1,
                client_error("AccessDeniedException", "DescribeLogGroups"),
            )
        }
    )
    reading = read([fl(), v2], Clients(denied))
    assert result(reading, v2.id).why_code == "format_v2"
    assert result(reading, "fl-0000000000000001").why_code == "access_denied"


def test_vpc_log_covers_subnet_and_region_eni_logs() -> None:
    """1.4 — `subnet-id` 있는 VPC 로그를 읽었으면 그 VPC 의 서브넷 로그·(리전 서브넷을 다 덮으면) ENI 로그는 안 읽는다."""
    sub = fl("fl-0000000000000002", level="subnet", target=SUB1, dest="grp-sub")
    eni = fl(
        "fl-0000000000000003",
        level="network_interface",
        target=ENI1,
        vpc_id=None,
        dest="grp-eni",
    )
    clients = Clients(cwl(trip()))
    reading = read([fl(), sub, eni], clients)
    assert (result(reading, sub.id).why_code, result(reading, sub.id).why) == (
        "duplicate_covered",
        why_of("duplicate_covered", "subnet"),
    )
    assert (result(reading, eni.id).why_code, result(reading, eni.id).why) == (
        "duplicate_covered",
        why_of("duplicate_covered", "region"),
    )
    groups = {
        kw.get("logGroupName") or kw.get("logGroupNamePrefix")
        for _, kw in clients.logs.calls
    }
    assert groups == {GROUP}
    assert reading.proofs == (igw_proof(),)


def test_subnetless_vpc_log_does_not_cover_and_dedupes_toward_subnet() -> None:
    """1.4 — 서브넷 필드 없는 VPC 로그는 아무것도 덮지 않는다. 같은 기록이 서브넷 로그에도 있으면 한 번만,
    서브넷을 가린 쪽으로 센다(피어링 선 VPC 근거와 서브넷 근거가 같은 패킷을 두 번 세지 않는다)."""
    peering = {"dstaddr": "10.1.0.5", "traffic_path": 4}
    vpc_lines = trip(NO_SUBNET, **peering)
    logs = FakeLogs(
        {
            GROUP: {
                "streams": {
                    "s": {
                        "last": WE * 1000,
                        "events": [(T * 1000, m) for m in vpc_lines],
                    }
                }
            },
            "grp-sub": {
                "streams": {
                    "s": {
                        "last": WE * 1000,
                        "events": [(T * 1000, m) for m in trip(NO_SUBNET, **peering)],
                    }
                }
            },
        }
    )
    sub = fl(
        "fl-0000000000000002",
        level="subnet",
        target=SUB1,
        dest="grp-sub",
        fields=NO_SUBNET,
    )
    reading = read([fl(fields=NO_SUBNET), sub], Clients(logs))
    assert result(reading, sub.id).used is True
    assert reading.proofs == (
        Proof(
            nid("vpc_peering_connection", PCX),
            nid("subnet", SUB1),
            PCX_ROUTE,
            1,
            API_CWL,
        ),
    )


def test_two_logs_on_one_eni_read_once() -> None:
    first = fl(
        "fl-0000000000000001", level="network_interface", target=ENI1, vpc_id=None
    )
    second = fl(
        "fl-0000000000000002",
        level="network_interface",
        target=ENI1,
        vpc_id=None,
        dest="grp-2",
    )
    reading = read([first, second], Clients(cwl(trip())))
    assert result(reading, second.id).why == why_of(
        "duplicate_covered", "network_interface"
    )
    assert reading.proofs == (igw_proof(),)


def test_eni_log_drops_records_of_covered_subnets() -> None:
    """서브넷 로그가 SUB1 을 덮으면 ENI 로그의 SUB1 기록은 버리고 SUB2 기록만 센다."""
    sub = fl("fl-0000000000000001", level="subnet", target=SUB1, vpc_id=VPC)
    eni = fl(
        "fl-0000000000000002",
        level="network_interface",
        target=ENI1,
        vpc_id=None,
        dest="grp-eni",
    )
    sub2 = {"subnet_id": SUB2, "srcaddr": "10.0.2.4"}
    eni_lines = [*trip(), *trip(**sub2)]
    logs = FakeLogs(
        {
            GROUP: {
                "streams": {
                    "s": {"last": WE * 1000, "events": [(T * 1000, m) for m in trip()]}
                }
            },
            "grp-eni": {
                "streams": {
                    "s": {
                        "last": WE * 1000,
                        "events": [(T * 1000, m) for m in eni_lines],
                    }
                }
            },
        }
    )
    reading = read([sub, eni], Clients(logs))
    assert result(reading, eni.id).used is True
    assert reading.proofs == (igw_proof(1), igw_proof(1, SUB2))


# ---- 오류 -------------------------------------------------------------------------------------


def test_throttle_before_listing_moves_on() -> None:
    """9.2 — 목록 전에 스로틀이면 읽지 못한 것이다. 다음 후보를 읽는다."""
    key = s3_key(s3log())
    s3 = FakeS3({BUCKET: {"region": REGION, "objects": {key: s3_object(trip())}}})
    logs = cwl(
        trip(),
        errors={
            "describe_log_streams": (
                1,
                client_error("ThrottlingException", "DescribeLogStreams"),
            )
        },
    )
    reading = read([fl(), s3log()], Clients(logs, s3))
    assert (
        result(reading, "fl-0000000000000001").used,
        result(reading, "fl-0000000000000001").why_code,
    ) == (
        False,
        "throttled",
    )
    assert result(reading, "fl-0000000000000002").used is True


def test_throttle_after_listing_keeps_what_was_found() -> None:
    """9.2 — 목록 뒤 스로틀: 읽은 것(첫 스트림의 짝)은 쓰고 `throttled` 로 적는다. 다음 후보는 안 읽는다."""
    logs = cwl(
        trip(),
        trip(srcport=40001),
        errors={
            "get_log_events": (3, client_error("ThrottlingException", "GetLogEvents"))
        },
    )
    clients = Clients(logs)
    reading = read([fl(), s3log()], clients)
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, "throttled")
    assert reading.proofs == (igw_proof(),)
    assert result(reading, "fl-0000000000000002").why_code == "duplicate_covered"
    assert clients.s3.calls == []
    _validate(res.to_dict())


@pytest.mark.parametrize(
    ("error", "code", "detail"),
    [
        (
            client_error(
                "AccessDeniedException", "GetLogEvents", "no access to KMS key"
            ),
            "kms",
            None,
        ),
        (client_error("KMS.DisabledException", "GetLogEvents"), "kms", None),
        (client_error("ResourceNotFoundException", "GetLogEvents"), "not_found", None),
        (
            client_error("WeirdError", "GetLogEvents", "arn:aws:logs:secret"),
            "read_error",
            "오류 코드 WeirdError",
        ),
        (
            EndpointConnectionError(endpoint_url="https://x"),
            "read_error",
            "오류 코드 EndpointConnectionError",
        ),
    ],
)
def test_error_mapping(error: Exception, code: str, detail: str | None) -> None:
    """6.2 · 6.3 · 3.7 — AWS 원문은 어디에도 안 들어간다(코드만)."""
    reading = read([fl()], Clients(cwl(trip(), errors={"get_log_events": (1, error)})))
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code, res.detail) == (False, code, detail)
    assert reading.proofs == ()
    assert "secret" not in json.dumps(res.to_dict())


def test_entry_failure_is_not_access_denied() -> None:
    """크로스계정 진입 실패를 `access_denied` 로 적으면 사람을 스택 파라미터로 보낸다(metrics.py 의 같은 함정)."""
    clients = Clients(
        cwl(trip()), fail={"logs": client_error("AccessDenied", "AssumeRole")}
    )
    reading = read([fl()], clients)
    res = result(reading, "fl-0000000000000001")
    assert (res.why_code, res.detail) == (
        "read_error",
        "진입 실패 sts:AssumeRole AccessDenied",
    )


def test_read_only_violation_is_never_swallowed() -> None:
    clients = Clients(
        cwl(trip()), fail={"logs": ReadOnlyViolation("logs:PutLogEvents")}
    )
    with pytest.raises(ReadOnlyViolation):
        read([fl()], clients)
    logs = cwl(trip(), errors={"get_log_events": (1, ReadOnlyViolation("x"))})
    with pytest.raises(ReadOnlyViolation):
        read([fl()], Clients(logs))


# ---- CWL 목록 규칙 · 상한(조합표 7.1 · 9.1) ----------------------------------------------------


def test_infrequent_access_group_is_not_read() -> None:
    """7.1"""
    logs = cwl(trip())
    logs.groups[GROUP]["class"] = "INFREQUENT_ACCESS"
    reading = read([fl()], Clients(logs))
    assert result(reading, "fl-0000000000000001").why_code == "log_class_infrequent"
    assert [m for m, _ in logs.calls] == ["describe_log_groups"]


def test_group_found_on_a_later_page_and_missing_group_is_not_found() -> None:
    logs = cwl(trip(), group_page=1)
    logs.groups[GROUP + "-0"] = {"streams": {}}
    logs.groups[GROUP[:-1]] = {"streams": {}}
    reading = read([fl()], Clients(logs))
    assert reading.proofs == (igw_proof(),)
    missing = read([fl(dest="grp-none")], Clients(cwl(trip())))
    assert result(missing, "fl-0000000000000001").why_code == "not_found"


def test_stream_listing_stops_at_first_stale_stream() -> None:
    """마지막 이벤트가 (읽는 범위 시작 − 3시간)보다 오래된 첫 스트림에서 목록을 멈춘다."""
    cutoff_ms = (RS - flowlogs.STREAM_SLACK_SECONDS) * 1000
    logs = FakeLogs(
        {
            GROUP: {
                "streams": {
                    "fresh": {
                        "last": WE * 1000,
                        "events": [(T * 1000, m) for m in trip()],
                    },
                    "stale": {
                        "last": cutoff_ms - 1,
                        "events": [(T * 1000, m) for m in trip(srcport=1)],
                    },
                    "older": {
                        "last": cutoff_ms - 2,
                        "events": [(T * 1000, m) for m in trip(srcport=2)],
                    },
                }
            }
        },
        stream_page=1,
    )
    reading = read([fl()], Clients(logs))
    assert reading.proofs == (igw_proof(),)
    assert [m for m, _ in logs.calls].count("describe_log_streams") == 2
    read_streams = {
        kw["logStreamName"] for m, kw in logs.calls if m == "get_log_events"
    }
    assert read_streams == {"fresh"}


def test_stream_just_inside_slack_is_read() -> None:
    cutoff_ms = (RS - flowlogs.STREAM_SLACK_SECONDS) * 1000
    logs = FakeLogs(
        {
            GROUP: {
                "streams": {
                    "s": {"last": cutoff_ms, "events": [(T * 1000, m) for m in trip()]}
                }
            }
        }
    )
    assert read([fl()], Clients(logs)).proofs == (igw_proof(),)


@pytest.mark.parametrize(("created_delta", "counted"), [(0, True), (-1, False)])
def test_stream_without_last_event_uses_creation_time(
    created_delta: int, counted: bool
) -> None:
    cutoff_ms = (RS - flowlogs.STREAM_SLACK_SECONDS) * 1000
    logs = FakeLogs(
        {
            GROUP: {
                "streams": {
                    "new": {
                        "last": None,
                        "created": cutoff_ms + created_delta,
                        "events": [(T * 1000, m) for m in trip()],
                    }
                }
            }
        }
    )
    assert read([fl()], Clients(logs)).proofs == ((igw_proof(),) if counted else ())


def test_stream_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """9.1 — 스트림 상한 초과 → 찾은 것까지 쓰고 `capped`. 정확히 상한이면 capped 가 아니다."""
    monkeypatch.setattr(flowlogs, "MAX_STREAMS_PER_LOG", 1)
    one = read([fl()], Clients(cwl(trip())))
    assert result(one, "fl-0000000000000001").why_code is None
    two = read([fl()], Clients(cwl(trip(), trip(srcport=40001))))
    res = result(two, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, "capped")
    assert two.proofs == (igw_proof(1),)


def test_page_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flowlogs, "MAX_PAGES_PER_STREAM", 2)
    reading = read([fl()], Clients(cwl([*trip(), *trip(srcport=40001)], event_page=1)))
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, "capped")
    assert reading.proofs == (igw_proof(1),)


def test_event_paging_until_token_repeats() -> None:
    logs = cwl([*trip(), *trip(srcport=40001)], event_page=1)
    reading = read([fl()], Clients(logs))
    assert reading.proofs == (igw_proof(2),)
    assert result(reading, "fl-0000000000000001").why_code is None
    # 4 이벤트 = 4 페이지 + 끝 확인 1
    assert [m for m, _ in logs.calls].count("get_log_events") == 5


def test_exactly_the_page_cap_is_not_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """#36 리뷰 — CWL 의 끝은 내용 페이지 다음 한 번 더의 「같은 토큰」이다. 정확히 상한만큼인 스트림을 끝을 보고도
    「다 못 읽음」으로 적으면 안 된다. 대조군 = `test_page_cap`(상한 + 1 페이지면 `capped`)."""
    monkeypatch.setattr(flowlogs, "MAX_PAGES_PER_STREAM", 2)
    logs = cwl(trip(), event_page=1)
    reading = read([fl()], Clients(logs))
    assert result(reading, "fl-0000000000000001").why_code is None
    assert reading.proofs == (igw_proof(1),)
    assert [m for m, _ in logs.calls].count("get_log_events") == 3


def test_call_cap_before_listing_is_not_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flowlogs, "MAX_CALLS", 1)
    reading = read([fl()], Clients(cwl(trip())))
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (False, "capped")
    _validate(res.to_dict())


def test_record_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flowlogs, "MAX_RECORDS", 2)
    reading = read([fl()], Clients(cwl([*trip(), *trip(srcport=40001)])))
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, "capped")
    assert reading.proofs == (igw_proof(1),)


# ---- S3 텍스트 경로(조합표 3 · 4) --------------------------------------------------------------


def _s3_world(
    log: FlowLog, objects: dict[str, bytes], region: str = REGION, **kw: Any
) -> Clients:
    return Clients(s3=FakeS3({BUCKET: {"region": region, "objects": objects}}, **kw))


def test_s3_default_layout() -> None:
    """3.2"""
    log = s3log()
    clients = _s3_world(log, {s3_key(log): s3_object(trip())})
    reading = read([log], clients)
    assert reading.proofs == (igw_proof(api=API_S3),)
    assert result(reading, log.id).used is True
    assert all(body.closed for body in clients.s3.bodies) and clients.s3.bodies


def test_s3_custom_prefix() -> None:
    """3.3"""
    log = s3log(dest=f"arn:aws:s3:::{BUCKET}/team/flow/")
    clients = _s3_world(log, {s3_key(log, prefix="team/flow/"): s3_object(trip())})
    assert read([log], clients).proofs == (igw_proof(api=API_S3),)


def test_s3_hive_per_hour_layout() -> None:
    """4.5 · 4.6 · 최근 폴더부터 모은다(B-07 — 한도에 닿으면 오래된 쪽을 버린다)."""
    log = s3log(hive=True, per_hour=True)
    key = (
        f"AWSLogs/aws-account-id={ACCOUNT}/aws-service=vpcflowlogs/aws-region={REGION}/"
        f"year=2026/month=09/day=29/hour=01/{ACCOUNT}_vpcflowlogs_{REGION}_{log.id}_20260929T0105Z_ab.log.gz"
    )
    clients = _s3_world(log, {key: s3_object(trip())})
    assert read([log], clients).proofs == (igw_proof(api=API_S3),)
    prefixes = [kw["Prefix"] for m, _, kw in clients.s3.calls if m == "list_objects_v2"]
    assert prefixes == [
        f"AWSLogs/aws-account-id={ACCOUNT}/aws-service=vpcflowlogs/aws-region={REGION}/year=2026/month=09/day=29/hour=01/",
        f"AWSLogs/aws-account-id={ACCOUNT}/aws-service=vpcflowlogs/aws-region={REGION}/year=2026/month=09/day=29/hour=00/",
    ]


@pytest.mark.parametrize("bucket_region", ["eu-west-1", "ap-northeast-2", REGION])
def test_s3_bucket_in_another_region_is_read_with_its_region(
    bucket_region: str,
) -> None:
    """3.4 — `GetBucketLocation` 은 로그 리전에서 먼저. 목록·객체는 버킷 리전 클라이언트로만 부른다(가짜는 틀린
    리전 · Head* 에 터진다). eu-west-1 은 가짜가 옛 값 `EU` 로 답한다."""
    log = s3log()
    clients = _s3_world(log, {s3_key(log): s3_object(trip())}, region=bucket_region)
    reading = read([log], clients)
    assert reading.proofs == (igw_proof(api=API_S3),)
    assert clients.s3.calls[0][:2] == ("get_bucket_location", REGION)
    assert {r for m, r, _ in clients.s3.calls[1:]} == {bucket_region}


@pytest.mark.parametrize(
    ("constraint", "region"),
    [
        (None, "us-east-1"),
        ("", "us-east-1"),
        ("EU", "eu-west-1"),
        ("ap-south-1", "ap-south-1"),
    ],
)
def test_bucket_location_constraint(constraint: str | None, region: str) -> None:
    assert flowlogs._bucket_region(constraint) == region


def test_s3_only_files_in_the_name_window_are_fetched() -> None:
    log = s3log()
    other = replace(log, id="fl-0000000000000003")
    # low = 읽는 범위 시작(01:00 − 판정 지연 660초 − 간격 60초 = 00:48) − 300 − 60 = 00:42(D4c ④ 로 넓혔다)
    assert flowlogs.reading_range(log, (WS, WE))[0] - 300 - 60 == WS - 18 * 60
    objects = {
        s3_key(log, "20260929T0041Z"): s3_object(trip()),  # low(00:42) 앞
        s3_key(log, "20260929T0042Z"): s3_object(trip(srcport=1)),
        s3_key(log, "20260929T0116Z"): s3_object(trip(srcport=2)),  # high(01:16)
        s3_key(log, "20260929T0117Z"): s3_object(trip(srcport=3)),  # 뒤
        s3_key(other, "20260929T0105Z"): s3_object(trip(srcport=4)),  # 다른 로그
    }
    clients = _s3_world(log, objects)
    reading = read([log], clients)
    fetched = sorted(kw["Key"] for m, _, kw in clients.s3.calls if m == "get_object")
    assert fetched == sorted(
        [s3_key(log, "20260929T0042Z"), s3_key(log, "20260929T0116Z")]
    )
    assert reading.proofs == (igw_proof(2, api=API_S3),)


def test_s3_window_crossing_midnight_lists_two_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = s3log(interval=600)
    at = "2026-09-29T00:10:00Z"
    ws, _ = (int(t.timestamp()) for t in _window(at))
    lines = trip(start=ws + 30)
    key = s3_key(log, "20260928T2355Z", day="2026/09/28")
    clients = _s3_world(log, {key: s3_object(lines)})
    reading = read_flow_logs(world(), [log], clients, at=at)
    assert len(reading.proofs) == 1
    prefixes = [kw["Prefix"] for m, _, kw in clients.s3.calls if m == "list_objects_v2"]
    assert [p.rsplit("/", 4)[1:4] for p in prefixes] == [
        ["2026", "09", "29"],
        ["2026", "09", "28"],
    ]


def test_s3_shuffled_field_order() -> None:
    """4.2 — 필수 필드만 다 있으면 순서는 자유 — 선언 순서대로 자른다."""
    fields = tuple(reversed(FIELDS))
    log = s3log(fields=fields)
    clients = _s3_world(log, {s3_key(log): s3_object(trip(fields), fields)})
    assert read([log], clients).proofs == (igw_proof(api=API_S3),)


def test_s3_multi_member_and_small_reads() -> None:
    log = s3log()
    body = s3_object([*trip(), *trip(srcport=40001)], members=3)
    clients = _s3_world(log, {s3_key(log): body}, chunk=7)
    assert read([log], clients).proofs == (igw_proof(2, api=API_S3),)


@pytest.mark.parametrize(
    "body",
    [
        s3_object(trip(), tuple(reversed(FIELDS))),  # 머리줄이 선언과 다르다
        b"PAR1" + b"\0" * 32,
        s3_object(trip())[:-9],  # 끊긴 gzip
        b"not gzip at all",
        gzip.compress(
            " ".join(FIELDS).encode() + b"\n\xff\xfe\n", mtime=0
        ),  # UTF-8 아님
    ],
)
def test_s3_format_mismatch(body: bytes) -> None:
    """4.2 · 4.4 — 선언과 다른 내용은 칸을 잘못 자른다 → 그 로그 전체를 버린다."""
    log = s3log()
    reading = read([log], _s3_world(log, {s3_key(log): body}))
    res = result(reading, log.id)
    assert (res.used, res.why_code) == (False, "format_mismatch")
    assert reading.proofs == ()


def test_s3_parquet_object_under_text_declaration() -> None:
    log = s3log()
    key = s3_key(log).replace(".log.gz", ".parquet")
    reading = read([log], _s3_world(log, {key: b"PAR1"}))
    assert result(reading, log.id).why_code == "format_mismatch"


def test_s3_object_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flowlogs, "MAX_OBJECTS_PER_LOG", 1)
    log = s3log()
    objects = {
        s3_key(log, "20260929T0101Z"): s3_object(trip()),
        s3_key(log, "20260929T0102Z"): s3_object(trip(srcport=40001)),
    }
    reading = read([log], _s3_world(log, objects))
    res = result(reading, log.id)
    assert (res.used, res.why_code) == (True, "capped")
    assert reading.proofs == (igw_proof(1, api=API_S3),)


def test_s3_uncompressed_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flowlogs, "MAX_UNCOMPRESSED_BYTES", 10)
    log = s3log()
    reading = read([log], _s3_world(log, {s3_key(log): s3_object(trip())}))
    res = result(reading, log.id)
    assert (res.used, res.why_code) == (True, "capped")


def test_gunzip_never_inflates_more_than_one_chunk_at_a_time() -> None:
    """#36 리뷰 — 풀린 크기 예산을 사후에 보면 잘 눌린 1 MiB 청크 하나가 수백 배로 부푼 뒤에야 멈춘다.
    한 번에 풀리는 양이 읽기 청크를 넘지 않아야 예산이 메모리보다 먼저 본다."""
    plain = b"0" * (8 * flowlogs._READ_CHUNK)
    body = io.BytesIO(gzip.compress(plain) + gzip.compress(b"tail"))
    assert len(body.getvalue()) < flowlogs._READ_CHUNK  # 전제: 한 번 읽기에 다 들어온다
    pieces = list(flowlogs._gunzip(body, flowlogs._Budget()))
    assert max(len(p) for p in pieces) <= flowlogs._READ_CHUNK
    assert b"".join(pieces) == plain + b"tail"


def test_s3_list_paging() -> None:
    log = s3log()
    objects = {
        s3_key(log, f"20260929T01{m:02d}Z"): s3_object(trip(srcport=40000 + m))
        for m in range(5)
    }
    clients = _s3_world(log, objects, list_page=2)
    assert read([log], clients).proofs == (igw_proof(5, api=API_S3),)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (client_error("NoSuchBucket", "GetBucketLocation"), "not_found"),
        (client_error("AccessDenied", "GetBucketLocation"), "access_denied"),
    ],
)
def test_s3_errors(error: Exception, code: str) -> None:
    """3.5 · 3.7 — S3 거부 문장은 버킷 쪽 할 일(중앙 버킷 정책)을 말한다."""
    log = s3log()
    clients = Clients(
        s3=FakeS3(
            {BUCKET: {"region": REGION, "objects": {}}},
            errors={"get_bucket_location": (1, error)},
        )
    )
    res = result(read([log], clients), log.id)
    assert (res.used, res.why_code) == (False, code)
    if code == "access_denied":
        assert "FlowLogCentralBucketArns" in res.why


# ---- 참고 문구(조합표 5.2 · 7.2) ---------------------------------------------------------------


def test_slow_interval_and_delivery_failure_notes() -> None:
    reading = read([fl(interval=600, delivered=False)], Clients(cwl([line()])))
    assert result(reading, "fl-0000000000000001").detail == (
        "나간 기록만 1 · 집계 간격 600초 — 최근 흐름은 늦게 보인다 · 전달 실패가 있다 — 기록이 빠졌을 수 있다"
    )


# ---- 결정론 · 실제 수집 모양 --------------------------------------------------------------------


def test_deterministic_regardless_of_input_order() -> None:
    logs = [
        fl(
            "fl-0000000000000009",
            level="transit_gateway",
            target="tgw-0aaa",
            vpc_id=None,
        ),
        fl(),
        s3log(),
        fl("fl-0000000000000005", level="subnet", target=SUB2, dest="grp-sub"),
    ]
    first = read(
        logs, Clients(cwl([*trip(), *trip(subnet_id=SUB2, srcaddr="10.0.2.4")]))
    )
    second = read(
        list(reversed(logs)),
        Clients(cwl([*trip(), *trip(subnet_id=SUB2, srcaddr="10.0.2.4")])),
    )
    assert first == second
    assert [r.id for r in first.resources] == sorted(r.id for r in first.resources)


def test_collected_structure_shape() -> None:
    """🔴 손으로 만든 구조가 실제 수집 모양과 어긋나면 위 검사가 전부 거짓이 된다 — 실제 빌더가 만든 스냅샷으로 한 번 잰다.
    퍼블릭 서브넷(명시 연관) · 프라이빗 서브넷(메인 테이블 — 같은 테이블)의 경로 8 은 IGW, 경로 2 는 `pl-`→vpce 때문에 모호."""
    snapshot = collect(
        Scope(
            account_ids=(ACCOUNT,),
            regions=(REGION,),
            resource_types=SUPPORTED_RESOURCE_TYPES,
        ),
        factory(FakeAws()),
        clock=lambda: AT,
    )
    lines = [
        *trip(subnet_id=PUBLIC_SUBNET, srcaddr="10.10.1.5", vpc_id=APP_VPC),
        *trip(subnet_id=PRIVATE_SUBNET, srcaddr="10.10.2.5", vpc_id=APP_VPC),
        *trip(
            subnet_id=PUBLIC_SUBNET, srcaddr="10.10.1.6", vpc_id=APP_VPC, traffic_path=2
        ),
    ]
    log = fl(target=APP_VPC, vpc_id=APP_VPC)
    reading = read_flow_logs(snapshot, [log], Clients(cwl(lines)), at=AT)
    igw = nid("internet_gateway", APP_IGW)
    routes = [e for e in snapshot.edges if e.type == "route" and e.target == igw]
    assert len(routes) == 1
    assert reading.proofs == (
        Proof(igw, nid("subnet", PUBLIC_SUBNET), routes[0].id, 1, API_CWL),
        Proof(igw, nid("subnet", PRIVATE_SUBNET), routes[0].id, 1, API_CWL),
    )
    assert result(reading, log.id).detail == "모호 1"


# ---- 상한에 닿으면 오래된 쪽을 버린다(2026-10-15 QA B B-07) --------------------------------------------


def _old_lines(n: int) -> list[str]:
    """읽는 범위 안 · 지표 창 앞(「안 흐른다」 근거 범위)의 기록 — 짝이 아니다. 한 스트림에 줄이 몰린 모양."""
    return [line(start=WS - 120, srcport=50000 + i) for i in range(n)]


def test_page_cap_keeps_the_newest_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-10-15 QA B B-07 — egress 로그 그룹에 3분에 약 31만 줄이 몰려 스트림당 쪽 한도에 닿았고, 앞(옛 기록)부터 읽어
    판정 창의 최근 왕복이 버려졌다 → 흐르는 IGW 선이 초록을 잃었다(B-10). 최근 기록부터 읽어 버리는 쪽이 오래된 기록이어야
    한다. 대조 = 같은 스트림을 앞에서부터 읽으면 앞 두 쪽이 옛 줄이라 왕복 0 이다(브레이크 `CWL앞에서부터읽음`)."""
    monkeypatch.setattr(flowlogs, "MAX_PAGES_PER_STREAM", 2)
    logs = cwl([*_old_lines(4), *trip()], event_page=1)
    reading = read([fl()], Clients(logs))
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, "capped")
    assert reading.proofs == (igw_proof(1),)
    # 무엇을 버렸는지 적는다 — 최근 왕복이 판정 창 시작보다 뒤라 창 앞부분은 못 읽었을 수 있다.
    assert "쪽 한도(2쪽)" in res.detail and "판정 창 앞부분이 빠졌다" in res.detail
    calls = [kw for m, kw in logs.calls if m == "get_log_events"]
    assert [kw["startFromHead"] for kw in calls] == [False] * 3
    _validate(res.to_dict())


def test_page_cap_detail_says_the_window_was_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-07 — 한도에 닿아도 판정 창은 다 읽었으면 그렇게 적는다(빠진 것은 창 앞의 옛 기록뿐 — 초록 근거는 온전하다)."""
    monkeypatch.setattr(flowlogs, "MAX_PAGES_PER_STREAM", 3)
    reading = read([fl()], Clients(cwl([*_old_lines(4), *trip()], event_page=1)))
    res = result(reading, "fl-0000000000000001")
    assert (res.used, res.why_code) == (True, "capped")
    assert reading.proofs == (igw_proof(1),)
    from datetime import datetime, timezone

    stamp = datetime.fromtimestamp(WS - 120, tz=timezone.utc)
    assert (
        f"{stamp:%Y-%m-%dT%H:%M:%SZ} 까지의 오래된 기록 일부를 읽지 않았다"
        in res.detail
    )
    assert "판정 창 15분은 다 읽었다" in res.detail


def test_s3_object_cap_keeps_the_newest_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-07 의 S3 짝 — 객체 한도에 닿으면 오래된 객체를 버린다(최근 객체에 판정 창의 왕복이 있다)."""
    monkeypatch.setattr(flowlogs, "MAX_OBJECTS_PER_LOG", 1)
    log = s3log()
    objects = {
        s3_key(log, "20260929T0101Z"): s3_object(_old_lines(3)),
        s3_key(log, "20260929T0105Z"): s3_object(trip()),
    }
    reading = read([log], _s3_world(log, objects))
    res = result(reading, log.id)
    assert (res.used, res.why_code) == (True, "capped")
    assert reading.proofs == (igw_proof(1, api=API_S3),)
    assert "객체 한도(1개)" in res.detail
