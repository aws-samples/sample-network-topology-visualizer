"""W3③ — 흐름 로그 2단계(ADR 0008 「D4c」): TGW 로그 읽기 · 「안 흐른다」(NODATA) · 관측 문서에 올리기.

TGW(③) 네 축: ① 건넜다 = 같은 어태치먼트 쌍의 **왕복**(답 없는 기록은 「한쪽 기록만」) ② 버린 패킷은 단서로만 센다
③ 짝이 `-` 인 기록 · 창 밖 기록은 근거가 아니다 ④ 구조에 없는 어태치먼트는 선을 만들지 않는다.

「안 흐른다」(④) — 조건 하나라도 아니면 **아무것도 만들지 않는다**(E 그대로): 덮는 로그 하나 · 전달됨 · ALL ·
SKIPDATA 없음 · IGW/피어링 경로 · ENI 목록이 비지 않음 · 모든 ENI 가 NODATA 로 판정 창을 덮음 · 기록 있는 ENI 없음.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import jsonschema
import pytest
import test_metrics as tm
import test_observe_flowlogs as tof
from botocore.exceptions import ClientError
from nettopo import flowlogs
from nettopo.flowlogs import (
    API_CWL,
    API_S3,
    Crossing,
    FlowLogReading,
    Proof,
    read_flow_logs,
)
from nettopo.model import Edge, Node
from nettopo.observe import build_observation
from test_flowlogs import (
    ACCOUNT,
    AT,
    ENI1,
    ENI2,
    FIELDS,
    IGW_ROUTE,
    MAIN_PCX_ROUTE,
    NAT,
    PCX,
    PCX_ROUTE,
    REGION,
    SCHEMA,
    NO_SUBNET,
    PEER_VPC,
    SUB1,
    SUB2,
    SUB3,
    TGW_FIELDS,
    WE,
    WS,
    Clients,
    cwl,
    fl,
    igw_proof,
    line,
    nid,
    read,
    reply,
    result,
    s3_key,
    s3_object,
    s3log,
    trip,
    world,
)
from test_flowlogs import IGW as IGW_NATIVE

TGW = "tgw-0aaa1111bbbb0051"
ATT_A = "tgw-attach-0aaa1111bbbb0052"
ATT_B = "tgw-attach-0aaa1111bbbb0053"
ATT_GHOST = "tgw-attach-0fff1111bbbb0054"  # 구조에 없다(남의 계정 · 지운 것)
TGW_LOG = "fl-0000000000000050"
TGW_GROUP = "grp-flow-tgw"
TGW_T = WS + 120


# ---- 세계 · 기록 -------------------------------------------------------------------------------


def tgw_world() -> Any:
    """`world()` + TGW 1개 · VPC 어태치먼트 2개. 어태치먼트 선 = (어태치먼트 → TGW) · (어태치먼트 → VPC)."""
    base = world()

    def node(node_type: str, native: str) -> Node:
        return Node(
            id=nid(node_type, native),
            type=node_type,
            account_id=ACCOUNT,
            region=REGION,
            native_id=native,
        )

    nodes = [node("transit_gateway", TGW)]
    edges = []
    for att in (ATT_A, ATT_B):
        nodes.append(node("transit_gateway_attachment", att))
        for target in (nid("transit_gateway", TGW), nid("vpc", "vpc-0aaa1111bbbb0001")):
            source = nid("transit_gateway_attachment", att)
            edges.append(
                Edge(
                    id=f"attachment|{source}|{target}",
                    type="attachment",
                    source=source,
                    target=target,
                )
            )
    return dataclasses.replace(
        base, nodes=(*base.nodes, *nodes), edges=(*base.edges, *edges)
    )


def tgw_fl(**kw: Any) -> Any:
    kw.setdefault("dest", TGW_GROUP)
    return fl(
        TGW_LOG,
        level="transit_gateway",
        target=TGW,
        vpc_id=None,
        traffic=None,
        fields=TGW_FIELDS,
        **kw,
    )


def tgw_line(**kw: Any) -> str:
    values = {name: "-" for name in TGW_FIELDS}
    values.update(
        {
            "version": "6",
            "resource-type": "TransitGateway",
            "account-id": ACCOUNT,
            "tgw-id": TGW,
            "tgw-attachment-id": ATT_A,
            "tgw-pair-attachment-id": ATT_B,
            "srcaddr": "10.0.1.10",
            "dstaddr": "10.9.0.20",
            "srcport": "40000",
            "dstport": "443",
            "protocol": "6",
            "packets": "1",
            "bytes": "60",
            "start": str(TGW_T),
            "end": str(TGW_T + 60),
            "log-status": "OK",
            "packets-lost-no-route": "0",
            "packets-lost-blackhole": "0",
            "packets-lost-mtu-exceeded": "0",
            "packets-lost-ttl-expired": "0",
            "flow-direction": "ingress",
        }
    )
    values.update({k.replace("_", "-"): str(v) for k, v in kw.items()})
    return " ".join(values[f] for f in TGW_FIELDS)


def tgw_reply(**kw: Any) -> str:
    """같은 쌍의 반대 방향 — 답은 B 쪽 어태치먼트에 (B, A) 로 적힌다."""
    out = dict(
        tgw_attachment_id=ATT_B,
        tgw_pair_attachment_id=ATT_A,
        srcaddr="10.9.0.20",
        dstaddr="10.0.1.10",
        srcport="443",
        dstport="40000",
    )
    out.update(kw)
    return tgw_line(**out)


def tgw_read(*lines: str, log: Any = None, clients: Clients | None = None) -> Any:
    clients = clients or Clients(cwl(list(lines), group=TGW_GROUP))
    return read_flow_logs(tgw_world(), [log or tgw_fl()], clients, at=AT)


ATT_A_ID = nid("transit_gateway_attachment", ATT_A)
ATT_B_ID = nid("transit_gateway_attachment", ATT_B)


# ---- TGW ① 왕복 --------------------------------------------------------------------------------


def test_round_trip_across_two_attachments_is_one_crossing() -> None:
    reading = tgw_read(tgw_line(), tgw_reply())
    assert reading.crossings == (Crossing((ATT_A_ID, ATT_B_ID), 2, API_CWL),)
    res = result(reading, TGW_LOG)
    assert (res.used, res.why_code, res.kind, res.detail) == (
        True,
        None,
        "tgw_flow_log",
        None,
    )
    # TGW 로그는 서브넷 선 증명을 만들지 않는다(그건 VPC 로그 몫).
    assert reading.proofs == () and reading.silences == ()


def test_reply_recorded_under_the_same_attachment_also_proves() -> None:
    """(X, Y, t) 의 답이 (X, Y, t̄) 로 적혀도 왕복이다 — 어태치먼트 한쪽만 로그를 켠 경우."""
    back = tgw_reply(tgw_attachment_id=ATT_A, tgw_pair_attachment_id=ATT_B)
    reading = tgw_read(tgw_line(), back)
    assert reading.crossings == (Crossing((ATT_A_ID, ATT_B_ID), 2, API_CWL),)


def test_one_way_records_are_counted_not_proved() -> None:
    """🔴 한쪽 기록만으로 「흐른다」를 말하면 버려진 패킷도 초록이 된다 — TGW 기록엔 `action` 이 없다."""
    reading = tgw_read(tgw_line(), tgw_line(srcport=40001))
    assert reading.crossings == ()
    assert result(reading, TGW_LOG).detail == "한쪽 기록만 2"


def test_reply_with_other_ports_is_not_a_crossing() -> None:
    reading = tgw_read(tgw_line(), tgw_reply(srcport=8443))
    assert reading.crossings == ()
    assert result(reading, TGW_LOG).detail == "한쪽 기록만 2"


# ---- TGW ② 버린 패킷 ---------------------------------------------------------------------------


def test_lost_packets_are_summed_per_reason() -> None:
    reading = tgw_read(
        tgw_line(packets_lost_blackhole=3),
        tgw_reply(packets_lost_blackhole=2, packets_lost_ttl_expired=1),
        tgw_line(srcport=40009, packets_lost_no_route=4, packets_lost_mtu_exceeded=5),
    )
    assert result(reading, TGW_LOG).detail == (
        "버림(블랙홀) 5 · 버림(경로 없음) 4 · 버림(MTU 초과) 5 · 버림(TTL 만료) 1 · 한쪽 기록만 1"
    )
    assert reading.crossings == (Crossing((ATT_A_ID, ATT_B_ID), 2, API_CWL),)


# ---- TGW ③ 근거가 아닌 기록 --------------------------------------------------------------------


def test_dash_pair_is_neither_proof_nor_one_way() -> None:
    """짝이 `-` = 어느 어태치먼트로 나갔는지 모른다(TGW 가 버렸거나 다른 계정) — 세지도 않는다."""
    reading = tgw_read(
        tgw_line(tgw_pair_attachment_id="-"), tgw_reply(tgw_pair_attachment_id="-")
    )
    assert reading.crossings == ()
    assert result(reading, TGW_LOG).detail is None


@pytest.mark.parametrize(
    ("start", "counted"), [(WS - 1, False), (WS, True), (WE - 1, True), (WE, False)]
)
def test_tgw_window_is_the_metric_window(start: int, counted: bool) -> None:
    """TGW 로그는 판정 창으로 앞을 넓히지 않는다 — 「안 흐른다」를 TGW 로 말하지 않기 때문이다(D 등급 없음)."""
    reading = tgw_read(tgw_line(start=start), tgw_reply(start=start))
    assert bool(reading.crossings) is counted
    assert flowlogs.reading_range(tgw_fl(), (WS, WE)) == (WS, WE)


def test_skipdata_in_the_window_is_counted() -> None:
    reading = tgw_read(tgw_line(), tgw_reply(), tgw_line(log_status="SKIPDATA"))
    assert result(reading, TGW_LOG).detail == "SKIPDATA 1"
    assert reading.crossings  # 기록이 일부 빠졌어도 본 왕복은 왕복이다


def test_vpc_format_lines_in_a_tgw_group_are_other_format() -> None:
    reading = tgw_read(tgw_line(), tgw_reply(), line())
    assert result(reading, TGW_LOG).detail == "형식 다른 줄 1"


# ---- TGW ④ 구조 --------------------------------------------------------------------------------


def test_an_attachment_outside_the_structure_is_dropped_from_the_crossing() -> None:
    reading = tgw_read(
        tgw_line(tgw_pair_attachment_id=ATT_GHOST),
        tgw_reply(tgw_attachment_id=ATT_GHOST),
    )
    assert reading.crossings == (Crossing((ATT_A_ID,), 2, API_CWL),)
    both_ghost = tgw_read(
        tgw_line(tgw_attachment_id=ATT_GHOST, tgw_pair_attachment_id="tgw-attach-0f0f"),
        tgw_reply(
            tgw_attachment_id="tgw-attach-0f0f", tgw_pair_attachment_id=ATT_GHOST
        ),
    )
    assert both_ghost.crossings == ()


def test_tgw_log_reads_s3_with_the_same_key_layout() -> None:
    """🔶 S3 의 TGW 경로가 VPC 와 같은 `vpcflowlogs` 줄기라는 것은 **실측 전 가정**이다(D4c ③)."""
    log = s3log(
        TGW_LOG,
        level="transit_gateway",
        target=TGW,
        vpc_id=None,
        traffic=None,
        fields=TGW_FIELDS,
    )
    from test_flowlogs import FakeS3

    from test_flowlogs import BUCKET, REGION

    body = s3_object([tgw_line(), tgw_reply()], fields=TGW_FIELDS)
    objects = {BUCKET: {"region": REGION, "objects": {s3_key(log): body}}}
    reading = tgw_read(log=log, clients=Clients(s3=FakeS3(objects)))
    assert reading.crossings == (Crossing((ATT_A_ID, ATT_B_ID), 2, API_S3),)


def test_tgw_logs_never_ask_for_enis() -> None:
    clients = EniClients(cwl([tgw_line(), tgw_reply()], group=TGW_GROUP))
    tgw_read(clients=clients)
    assert clients.ec2.calls == []


def test_the_reader_declares_the_eni_listing_it_makes() -> None:
    """필요 권한 표가 이 호출을 빠뜨리면 「안 흐른다」가 배포 계정에서 영원히 안 선다(조용한 거부 → 메모)."""
    assert flowlogs.STRUCTURE_ACTIONS == ("ec2:DescribeNetworkInterfaces",)
    assert flowlogs.API_ENI == "ec2:DescribeNetworkInterfaces"


# ---- 「안 흐른다」 -------------------------------------------------------------------------------


class FakeEc2:
    def __init__(
        self,
        enis: dict[str, list[str]],
        *,
        error: Exception | None = None,
        page: int = 0,
    ) -> None:
        self.enis = enis
        self.error = error
        self.page = page
        self.calls: list[dict[str, Any]] = []

    def describe_network_interfaces(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        [subnet] = kwargs["Filters"][0]["Values"]
        ids = self.enis.get(subnet, [])
        at = int(kwargs.get("NextToken") or 0)
        size = self.page or len(ids) or 1
        out: dict[str, Any] = {
            "NetworkInterfaces": [
                {"NetworkInterfaceId": i} for i in ids[at : at + size]
            ]
        }
        if at + size < len(ids):
            out["NextToken"] = str(at + size)
        return out


class EniClients(Clients):
    def __init__(self, *args: Any, ec2: FakeEc2 | None = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.ec2 = ec2 or FakeEc2({})

    def __call__(self, account_id: str, region: str, service: str) -> Any:
        if service == "ec2":
            self.made.append((account_id, region, service))
            return self.ec2
        return super().__call__(account_id, region, service)


LAG = 60 + flowlogs.NODATA_SLACK_SECONDS["cloud-watch-logs"]  # CWL · 60초 간격
JUDGED = (WS - LAG, WE - LAG)


def nodata(eni: str = ENI1, start: int = WS) -> str:
    """`NODATA` 기록 — 판단에 쓰는 것은 ENI · start · log-status 뿐이다. 나머지는 `-`."""
    keep = {"version", "account-id", "interface-id", "start", "end", "log-status"}
    values = {
        "version": "5",
        "account-id": ACCOUNT,
        "interface-id": eni,
        "start": str(start),
        "end": str(start + 60),
        "log-status": "NODATA",
    }
    return " ".join(values[f] if f in keep else "-" for f in FIELDS)


def quiet(eni: str = ENI1, *, skip: int | None = None) -> list[str]:
    """판정 창 전체를 60초 간격 NODATA 로 덮는다. `skip` = 빠뜨릴 순번."""
    starts = range(JUDGED[0], JUDGED[1], 60)
    return [nodata(eni, s) for i, s in enumerate(starts) if i != skip]


SUB1_ID = nid("subnet", SUB1)
IGW_ID = nid("internet_gateway", IGW_NATIVE)
PCX_ID = nid("vpc_peering_connection", PCX)


def silent(*lines: str, enis: dict[str, list[str]] | None = None, **kw: Any) -> Any:
    clients = EniClients(
        cwl(list(lines)),
        ec2=kw.pop("ec2", None) or FakeEc2({SUB1: [ENI1]} if enis is None else enis),
    )
    snapshot = kw.pop("snapshot", None)
    logs = kw.pop("logs", None) or [fl(**kw)]
    reading = read_flow_logs(snapshot or world(), logs, clients, at=AT)
    return reading, clients


def test_every_eni_silent_through_the_judged_window_is_a_quiet_subnet() -> None:
    reading, clients = silent(*quiet())
    assert reading.silences == (
        Proof(IGW_ID, SUB1_ID, IGW_ROUTE, 0, API_CWL),
        Proof(PCX_ID, SUB1_ID, PCX_ROUTE, 0, API_CWL),
    )
    assert reading.proofs == ()
    # ENI 목록은 NODATA 가 있는 서브넷만 묻는다(SUB2 는 같은 테이블이지만 ENI 가 없다 → 묻고 빈 목록).
    asked = [c["Filters"][0]["Values"] for c in clients.ec2.calls]
    assert [SUB1] in asked
    assert result(reading, "fl-0000000000000001").used is True


def test_judged_window_lags_by_interval_plus_delivery_slack() -> None:
    assert flowlogs.judged_window(fl(), (WS, WE)) == JUDGED
    s3 = s3log()
    assert flowlogs.judged_window(s3, (WS, WE)) == (WS - 660, WE - 660)
    assert flowlogs.reading_range(fl(), (WS, WE)) == (WS - LAG - 60, WE)


def reply_trip(start: int) -> list[str]:
    """판정 창 안 OK 왕복 한 쌍 — 지표 창 밖이라 증명은 아니지만 「기록이 있다」다."""
    return [line(start=start), reply(start=start)]


@pytest.mark.parametrize(
    "case",
    [
        "active_eni",
        "gap",
        "eni_without_nodata",
        "no_enis",
        "skipdata",
        "window_end_uncovered",
        "eni_list_denied",
        "not_delivered",
        "accept_only",
    ],
)
def test_any_failing_condition_makes_no_quiet_subnet(case: str) -> None:
    lines = quiet()
    enis: dict[str, list[str]] = {SUB1: [ENI1]}
    kw: dict[str, Any] = {}
    if case == "active_eni":
        lines = [*lines, *reply_trip(WS - LAG + 30)]
    elif case == "gap":
        lines = quiet(skip=3)
    elif case == "eni_without_nodata":
        enis = {SUB1: [ENI1, ENI2]}
    elif case == "no_enis":
        enis = {SUB1: []}
    elif case == "skipdata":
        lines = [*lines, nodata(ENI2, WS - 100).replace("NODATA", "SKIPDATA")]
    elif case == "window_end_uncovered":
        lines = lines[:-1]
    elif case == "eni_list_denied":
        kw["ec2"] = FakeEc2(
            {},
            error=ClientError(
                {"Error": {"Code": "UnauthorizedOperation", "Message": "x"}},
                "DescribeNetworkInterfaces",
            ),
        )
    elif case == "not_delivered":
        kw["delivered"] = False
    elif case == "accept_only":
        kw["traffic"] = "ACCEPT"
    reading, _ = silent(*lines, enis=enis, **kw)
    assert reading.silences == ()
    if case == "eni_list_denied":
        assert (
            result(reading, "fl-0000000000000001").detail == flowlogs._ENI_UNREAD_NOTE
        )


def test_control_each_negative_case_has_a_positive_twin() -> None:
    """대조군 — 위 표의 경우들이 모두 같은 기본값(quiet · ENI1 하나)에서 출발한다는 것을 잰다."""
    reading, _ = silent(*quiet(), enis={SUB1: [ENI1]})
    assert len(reading.silences) == 2


def test_two_covering_logs_make_no_quiet_subnet() -> None:
    """🔴 서브넷 필드 없는 VPC 로그(피어링만 판정)와 서브넷 로그가 **둘 다** 읽히면 같은 서브넷을 두 로그가 덮는다 —
    한쪽 NODATA 가 다른 쪽 기록을 지우지 못하니 판정하지 않는다."""
    from test_flowlogs import NO_SUBNET

    def lines(fields: tuple[str, ...]) -> list[str]:
        keep = {"version", "account-id", "interface-id", "start", "end", "log-status"}
        out = []
        for raw in quiet():
            values = dict(zip(FIELDS, raw.split(), strict=True))
            out.append(" ".join(values[f] if f in keep else "-" for f in fields))
        return out

    vpc_log = fl(fields=NO_SUBNET)
    sub_log = fl(
        "fl-0000000000000007",
        level="subnet",
        target=SUB1,
        vpc_id=None,
        dest="grp-flow-b",
    )
    logs = cwl(lines(NO_SUBNET))
    logs.groups["grp-flow-b"] = cwl(quiet()).groups[next(iter(logs.groups))]
    clients = EniClients(logs, ec2=FakeEc2({SUB1: [ENI1]}))
    reading = read_flow_logs(world(), [vpc_log, sub_log], clients, at=AT)
    assert [r.used for r in reading.resources] == [True, True], (
        reading.resources
    )  # 전제
    assert reading.silences == ()
    # 대조군 — 서브넷 로그 하나만이면 같은 기록으로 판정한다.
    clients = EniClients(cwl(quiet(), group="grp-flow-b"), ec2=FakeEc2({SUB1: [ENI1]}))
    alone = read_flow_logs(world(), [sub_log], clients, at=AT)
    assert [p.observed_on for p in alone.silences] == [SUB1_ID, SUB1_ID]


def test_a_subnet_without_igw_or_peering_routes_is_not_judged() -> None:
    """메인 테이블에 NAT 만 있으면 그릴 선이 없다 — ENI 도 묻지 않는다."""
    snapshot = world(main_routes={("nat_gateway", NAT): ["0.0.0.0/0"]})
    ec2 = FakeEc2({SUB3: [ENI1]})
    reading, _ = silent(*quiet(), ec2=ec2, snapshot=snapshot)
    assert all(p.observed_on != nid("subnet", SUB3) for p in reading.silences)
    assert [SUB3] not in [c["Filters"][0]["Values"] for c in ec2.calls]


def test_main_table_peering_route_is_judged_for_a_main_table_subnet() -> None:
    """대조군 — 메인 테이블에도 pcx 경로가 있으면 SUB3 의 침묵은 그 피어링 선 하나에 붙는다."""
    reading, _ = silent(*quiet(), enis={SUB3: [ENI1]})
    assert reading.silences == (
        Proof(PCX_ID, nid("subnet", SUB3), MAIN_PCX_ROUTE, 0, API_CWL),
    )


# ---- 남의 줄 거르기(ADR 0008 ⑤ · W3④ 랩 F4·F5 실측) ------------------------------------------------
# 🔴 서브넷 로그 둘이 한 CWL 그룹에 쓰면 두 서브넷의 줄이 같은 그룹에 섞인다. 로그 대상으로만 귀속하면 이웃 서브넷의 왕복이
#    이 로그 대상 것으로 세지고, 같은 기록 키는 먼저 읽은 로그가 가져가 진짜 주인 서브넷이 빠진다(조용한 쪽 초록 · 바쁜 쪽 없음).

SUB2_ID = nid("subnet", SUB2)


def _two_subnet_logs(fields: tuple[str, ...] = FIELDS) -> list[Any]:
    return [
        fl(
            f"fl-00000000000000{i}1",
            level="subnet",
            target=sub,
            vpc_id=None,
            fields=fields,
        )
        for i, sub in ((1, SUB1), (2, SUB2))
    ]


def _busy_and_quiet(busy: str, fields: tuple[str, ...] = FIELDS) -> list[str]:
    """`busy` 서브넷 ENI 의 인터넷 왕복 1쌍 + 다른 서브넷 ENI 의 판정 창 전체 NODATA."""
    eni, other = (ENI1, ENI2) if busy == SUB1 else (ENI2, ENI1)
    lines = trip(fields, interface_id=eni, subnet_id=busy)
    quiet_lines = [
        " ".join(dict(zip(FIELDS, raw.split(), strict=True))[f] for f in fields)
        for raw in quiet(other)
    ]
    return [*lines, *quiet_lines]


@pytest.mark.parametrize("busy", [SUB1, SUB2])
def test_two_subnet_logs_sharing_a_group_credit_only_their_own_lines(busy: str) -> None:
    """랩 F4·F5 재현 — 바쁜 서브넷만 초록, 조용한 서브넷은 「흐름 없음」. 읽는 순서(서브넷 id 순)가 어느 쪽이어도 같다."""
    quiet_sub = SUB2 if busy == SUB1 else SUB1
    clients = EniClients(
        cwl(_busy_and_quiet(busy)), ec2=FakeEc2({SUB1: [ENI1], SUB2: [ENI2]})
    )
    reading = read_flow_logs(world(), _two_subnet_logs(), clients, at=AT)
    assert reading.proofs == (igw_proof(1, busy),)
    assert {p.observed_on for p in reading.silences} == {nid("subnet", quiet_sub)}
    other_log = "fl-0000000000000021" if busy == SUB1 else "fl-0000000000000011"
    assert result(reading, other_log).detail == "다른 로그 줄 2"


def test_shared_group_without_subnet_field_keeps_the_lines_of_its_own_enis() -> None:
    """`subnet-id` 가 없는 형식(기본 v2 가 그렇다)이면 줄로는 못 가린다 — 대상 서브넷의 ENI 목록으로 가린다."""
    clients = EniClients(
        cwl(_busy_and_quiet(SUB2, NO_SUBNET)), ec2=FakeEc2({SUB1: [ENI1], SUB2: [ENI2]})
    )
    reading = read_flow_logs(world(), _two_subnet_logs(NO_SUBNET), clients, at=AT)
    assert reading.proofs == (igw_proof(1, SUB2),)
    assert result(reading, "fl-0000000000000011").detail == "다른 로그 줄 2"


def test_shared_group_without_subnet_field_and_no_eni_list_counts_nothing() -> None:
    """ENI 목록을 못 읽으면 어느 줄이 제 것인지 모른다 — 초록을 지어내지 않고 이유를 적는다."""
    denied = FakeEc2(
        {},
        error=ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "x"}},
            "DescribeNetworkInterfaces",
        ),
    )
    clients = EniClients(cwl(_busy_and_quiet(SUB2, NO_SUBNET)), ec2=denied)
    reading = read_flow_logs(world(), _two_subnet_logs(NO_SUBNET), clients, at=AT)
    assert reading.proofs == ()
    assert flowlogs._SHARED_UNREAD_NOTE in (
        result(reading, "fl-0000000000000021").detail or ""
    )


def test_unshared_group_without_subnet_field_never_asks_for_enis_to_read() -> None:
    """대조군 — 그룹을 혼자 쓰면 줄이 전부 제 것이다. ENI 목록 없이 읽는다(묻는 것은 「흐름 없음」 판정용뿐)."""
    [log] = _two_subnet_logs(NO_SUBNET)[1:]
    clients = EniClients(cwl(trip(NO_SUBNET, interface_id=ENI2, subnet_id=SUB2)))
    reading = read_flow_logs(world(), [log], clients, at=AT)
    assert reading.proofs == (igw_proof(1, SUB2),)
    assert clients.ec2.calls == []


def test_vpc_log_skips_lines_of_another_vpc() -> None:
    """VPC 로그 — 줄의 `vpc-id` 가 대상과 다르면 같은 그룹을 쓰는 다른 VPC 로그의 줄이다."""
    lines = [*trip(), *trip(vpc_id=PEER_VPC, srcport=40001)]
    reading = read([fl()], Clients(cwl(lines)))
    assert reading.proofs == (igw_proof(1),)
    assert result(reading, "fl-0000000000000001").detail == "다른 로그 줄 2"


def test_eni_log_skips_lines_of_another_eni() -> None:
    log = fl(level="network_interface", target=ENI1, vpc_id=None)
    lines = [*trip(), *trip(interface_id=ENI2, srcport=40001)]
    reading = read([log], Clients(cwl(lines)))
    assert reading.proofs == (igw_proof(1),)
    assert result(reading, log.id).detail == "다른 로그 줄 2"


def test_eni_listing_follows_next_token() -> None:
    reading, clients = silent(
        *quiet(),
        *quiet(ENI2),
        enis={SUB1: [ENI1, ENI2]},
        ec2=FakeEc2({SUB1: [ENI1, ENI2]}, page=1),
    )
    assert len(reading.silences) == 2
    assert [
        c.get("NextToken")
        for c in clients.ec2.calls
        if c["Filters"][0]["Values"] == [SUB1]
    ] == [None, "1"]
    # 두 번째 쪽에만 있는 ENI 가 기록을 가지면 침묵이 아니다 — 첫 쪽만 보면 틀린다.
    reading, _ = silent(
        *quiet(),
        *reply_trip(WS - LAG + 30),
        enis={SUB1: [ENI2, ENI1]},
        ec2=FakeEc2({SUB1: [ENI2, ENI1]}, page=1),
    )
    assert reading.silences == ()


def test_proofs_still_use_only_the_metric_window() -> None:
    """읽는 범위를 앞으로 넓혔어도 왕복 증명은 `[ws, we)` 안 기록만이다."""
    reading, _ = silent(*reply_trip(WS - 30))
    assert reading.proofs == ()
    reading, _ = silent(*reply_trip(WS))
    assert reading.proofs == (igw_proof(),)


# ---- 관측 문서 ---------------------------------------------------------------------------------


def _observe_with(
    reading: FlowLogReading, series: dict | None = None
) -> tuple[dict, dict]:
    topology = tof._snapshot(
        tof.SUPPORTED_RESOURCE_TYPES, tof.STRUCTURE_AT
    ).to_document()
    state = tof._snapshot(tof.OBSERVED_RESOURCE_TYPES, tof.AT)
    layer = tof.read_metrics(
        state,
        tof.factory_by_service({"cloudwatch": tm.FakeCloudWatch(series or {})}),
        at=tof.AT,
    )
    document, dropped = build_observation(
        state,
        topology=topology,
        observed_at=tof.AT,
        tool_version="test",
        metrics=layer,
        sources=tof._scan(tgw="on"),
        flow_logs=reading,
    )
    assert dropped == []
    return document, topology


def _rd(**kw: Any) -> FlowLogReading:
    return FlowLogReading(resources=(tof._log(),), calls=1, **kw)


def test_a_quiet_subnet_becomes_an_up_item_with_zero_datapoints() -> None:
    doc, topology = _observe_with(
        _rd(silences=(Proof(tof.IGW, tof.PRIV, tof.IGW_ROUTE, 0, API_CWL),))
    )
    row = tof._row(doc, tof.IGW_ROUTE)
    assert row is not None
    assert (
        row["status"] == "up"
    )  # 행 상태는 그대로 — 한 서브넷의 침묵은 IGW 의 침묵이 아니다
    assert row["by_subnet"] == [
        {
            "subnet_id": tof.PRIV,
            "status": "up",
            "evidence": {
                "api": API_CWL,
                "metric": "flow_log",
                "datapoints": 0,
                "window_seconds": 900,
            },
        }
    ]
    assert tof._problems(doc, topology) == []


def test_live_beats_quiet_on_the_same_subnet() -> None:
    doc, topology = _observe_with(
        _rd(
            proofs=(tof._igw(tof.PRIV),),
            silences=(Proof(tof.IGW, tof.PRIV, tof.IGW_ROUTE, 0, API_CWL),),
        )
    )
    row = tof._row(doc, tof.IGW_ROUTE)
    assert [(i["subnet_id"], i["status"]) for i in row["by_subnet"]] == [
        (tof.PRIV, "live")
    ]
    assert tof._problems(doc, topology) == []


def test_live_and_quiet_items_sit_side_by_side_sorted() -> None:
    doc, topology = _observe_with(
        _rd(
            proofs=(tof._igw(tof.PUB),),
            silences=(Proof(tof.IGW, tof.PRIV, tof.IGW_ROUTE, 0, API_CWL),),
        )
    )
    row = tof._row(doc, tof.IGW_ROUTE)
    got = [(i["subnet_id"], i["status"]) for i in row["by_subnet"]]
    assert sorted(got) == got
    assert set(got) == {(tof.PUB, "live"), (tof.PRIV, "up")}
    assert tof._problems(doc, topology) == []


def test_a_quiet_subnet_on_another_table_is_ignored() -> None:
    """서브넷의 테이블이 그 경로의 테이블이 아니면 올리지 않는다(구조 문서로 다시 거른다)."""
    doc, _ = _observe_with(
        _rd(silences=(Proof(tof.IGW, tof.EGR, tof.IGW_ROUTE, 0, API_CWL),))
    )
    row = tof._row(doc, tof.IGW_ROUTE)
    assert "by_subnet" not in row


def _attachment(source: str, target: str) -> str:
    return f"attachment|{source}|{target}"


ATT8888 = tof.nid("transit_gateway_attachment", tm.ATTACH)
ATT9999 = tof.nid("transit_gateway_attachment", tm.ATTACH2)
CORE = tof.nid("transit_gateway", tm.CORE_TGW)
A_TGW = _attachment(ATT8888, CORE)
A_VPC = _attachment(ATT8888, tof.APP)
B_TGW = _attachment(ATT9999, CORE)


def _zero_series() -> dict:
    return tm._series(
        *((edge, tm._flow_metric(edge), [0.0] * 3) for edge in (A_TGW, A_VPC, B_TGW)),
        flowing=(),
    )


def test_a_crossing_lifts_both_attachment_lines_with_their_grades() -> None:
    """→ TGW 선은 A(어태치먼트 자기 기록) · → VPC 선은 B(본 곳 = 어태치먼트)."""
    before, _ = _observe_with(_rd(), _zero_series())
    for edge in (A_TGW, A_VPC, B_TGW):
        assert tof._row(before, edge)["status"] == "up", edge  # 전제: 지표가 쟀고 0
    doc, topology = _observe_with(
        _rd(crossings=(Crossing((ATT8888, ATT9999), 3, API_CWL),)), _zero_series()
    )
    a_tgw, a_vpc, b_tgw = (tof._row(doc, e) for e in (A_TGW, A_VPC, B_TGW))
    assert (a_tgw["status"], a_tgw["why"]) == ("live", None)
    assert a_tgw["evidence"] == {
        "api": API_CWL,
        "metric": "flow_log",
        "datapoints": 3,
        "window_seconds": 900,
    }
    assert a_vpc["status"] == "live"
    assert a_vpc["evidence"]["observed_on"] == ATT8888
    assert b_tgw["status"] == "live" and "observed_on" not in b_tgw["evidence"]
    # 2층 원장은 올리기 전과 같다 — 올린 행도 2층이 답한 행이다(오라클 `_tgw_flow_lifted`).
    # (이 가짜 지표 세트의 원장은 올리기 전부터 맞지 않는다 — 비교 대상은 「같다」이다.)
    assert tof.ORACLE.metric_item_count_ledger(
        doc
    ) == tof.ORACLE.metric_item_count_ledger(before)
    assert tof.ORACLE.metric_only_without_a_metric(doc) == []
    assert [
        p for p in tof._problems(doc, topology) if not p.startswith("2층 원장")
    ] == []


def test_a_crossing_never_greens_a_blocked_attachment_line() -> None:
    """🔴 지표가 막힘(블랙홀 드롭)을 말한 선은 왕복 기록이 있어도 `down` 그대로다 — 막힘이 이긴다(D4c ③ 「`up` 행만」).
    대조군: 같은 건넘이 막히지 않은 옆 선(B→TGW)은 `live` 가 된다."""
    series = tm._series(
        *((edge, tm._flow_metric(edge), [0.0] * 3) for edge in (A_TGW, A_VPC, B_TGW)),
        (A_TGW, tm._drop_metric(A_TGW), [0.0, 5.0, 0.0]),
        flowing=(),
    )
    before, _ = _observe_with(_rd(), series)
    assert tof._row(before, A_TGW)["status"] == "down"  # 전제: 지표가 막힘을 말했다
    doc, _ = _observe_with(
        _rd(crossings=(Crossing((ATT8888, ATT9999), 3, API_CWL),)), series
    )
    assert tof._row(doc, A_TGW) == tof._row(before, A_TGW)
    assert tof._row(doc, B_TGW)["status"] == "live"


def test_a_crossing_never_creates_an_attachment_row() -> None:
    """2층이 행을 안 만들었으면(지표 없음 = 확인 불가) 흐름 로그도 만들지 않는다 — `live` 만 얹는다."""
    doc, _ = _observe_with(_rd(crossings=(Crossing((ATT8888, ATT9999), 3, API_CWL),)))
    assert tof._row(doc, A_TGW) is None


def test_crossing_pairs_add_up_per_attachment() -> None:
    doc, _ = _observe_with(
        _rd(
            crossings=(
                Crossing((ATT8888, ATT9999), 2, API_S3),
                Crossing((ATT8888,), 5, API_CWL),
            )
        ),
        _zero_series(),
    )
    a = tof._row(doc, A_TGW)["evidence"]
    assert (a["datapoints"], a["api"]) == (7, min(API_CWL, API_S3))
    assert tof._row(doc, B_TGW)["evidence"]["datapoints"] == 2


# ---- 스키마 1.3.0 ------------------------------------------------------------------------------


def _errors(doc: dict[str, Any]) -> list[str]:
    return [e.message for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(doc)]


def test_schema_rejects_live_with_zero_and_up_with_pairs() -> None:
    doc, _ = _observe_with(
        _rd(silences=(Proof(tof.IGW, tof.PRIV, tof.IGW_ROUTE, 0, API_CWL),))
    )
    assert _errors(doc) == []
    item = tof._row(doc, tof.IGW_ROUTE)["by_subnet"][0]
    item["status"] = "live"
    assert _errors(doc)
    item["status"] = "up"
    item["evidence"]["datapoints"] = 1
    assert _errors(doc)


def test_schema_attachment_observed_on_must_be_an_attachment() -> None:
    doc, _ = _observe_with(
        _rd(crossings=(Crossing((ATT8888, ATT9999), 3, API_CWL),)), _zero_series()
    )
    assert _errors(doc) == []
    row = tof._row(doc, A_VPC)
    row["evidence"]["observed_on"] = tof.PRIV
    assert _errors(doc)
    row["evidence"]["observed_on"] = ATT8888
    row["evidence"]["datapoints"] = 0
    assert _errors(doc)  # 행 수준 흐름 로그 근거는 1 이상(행은 `live` 만)


def test_schema_version_is_current() -> None:
    # 1.3.0 → 1.4.0(#27 사용률) → 1.5.0(사용률을 2층에서 떼어 냄) → 1.6.0(DX VIF BGP 수) → 1.7.0(접힌 반복의 시작 · ADR 0006 결정 12) → 1.8.0(LB 대상 선 행의 `targets` · ADR 0006 §O7) → 1.9.0(resource_type 에 `lambda_function` · P2-6) → 1.10.0(#114 수집 종류 4종을 `resource_type` 에 · 배치 B) → 1.11.0(배치 D 수집 종류 5개) → 1.12.0(지우는 중 `deleting` · QA C) → 1.13.0(사용률 창의 `no_points` · #128) → 1.14.0(#129 수집 종류 3개) → 1.15.0(LB 대상 다섯 갈래 · #137) → 1.16.0(WAF 수집 종류 2개 · ADR 0014) → 1.17.0(VPN 터널) → 1.18.0(사용률 → 사용량)은
    # minor — 이 파일의 1.3.0 키는 그대로 유효하다.
    assert SCHEMA["properties"]["schema_version"]["const"] == "1.18.0"
    doc, _ = _observe_with(_rd())
    assert doc["schema_version"] == "1.18.0"
    json.dumps(doc)
