"""사용률(#27 · ADR 0011) — 가장 바빴던 단위 · 평균 · 칸 고르기 · 한도 출처 · 이월 · 못 읽음.

각 검사는 **틀린 계산과 값이 갈리는** 입력을 쓴다. 예: 「매일 1시간 100%」는 가장 바빴던 단위 = 100% · 기간 평균 ≈ 4% 라서
배지를 평균으로 바꾸면 FAIL 한다. 같아지는 입력(늘 같은 값)이면 둘 중 무엇을 써도 통과한다 — 미측정이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fakes import (
    ACCOUNT,
    REGION,
    FakeMinuteCloudWatch,
    FakeServiceQuotas,
    client_error,
    factory_by_service,
    series_key,
)
from nettopo.model import CoverageEntry, Node, Reason, Scope, Snapshot
from nettopo.utilization import compute_utilization

AT = "2026-10-04T12:00:30Z"
END = datetime(2026, 10, 4, 12, 0, 30, tzinfo=timezone.utc)
TGW = "tgw-0aaa1111bbbb2222"
ATT = "tgw-attach-0aaa1111bbbb2222"
VPC = "vpc-0aaa1111bbbb2222"
AZ1, AZ2 = "use1-az1", "use1-az2"
LIMIT_BYTES_PER_MINUTE = 100e9 / 8 * 60  # TGW VPC 연결 100 Gbps 를 1분 내내 채운 바이트
# 사용량(2026-10-26)부터 엔진은 한도를 싣지 않는다 — 비율 대신 이 속도(bit/s)와 견준다
FULL_BPS = 100e9


def nid(kind: str, native: str) -> str:
    return f"{kind}/{ACCOUNT}/{REGION}/{native}"


def node(
    kind: str, native: str, *, parent: str | None = None, **attributes: Any
) -> dict[str, Any]:
    return {
        "id": nid(kind, native),
        "type": kind,
        "account_id": ACCOUNT,
        "region": REGION,
        "native_id": native,
        "arn": attributes.pop("arn", None),
        "name": None,
        "parent_id": parent,
        "attributes": attributes,
    }


def edge(kind: str, source: str, target: str, **attributes: Any) -> dict[str, Any]:
    return {
        "id": f"{kind}:{source}->{target}",
        "type": kind,
        "source": source,
        "target": target,
        "attributes": attributes,
    }


def tgw_topology(azs: tuple[str, ...] = (AZ1,)) -> dict[str, Any]:
    vpc = node("vpc", VPC, cidr_blocks=["10.0.0.0/16"])
    subnets = [
        node(
            "subnet",
            f"subnet-0aaa1111bbbb{i:04d}",
            parent=vpc["id"],
            availability_zone_id=az,
            availability_zone=f"us-east-1{'ab'[i]}",
            cidr_block=f"10.0.{i}.0/24",
        )
        for i, az in enumerate(azs)
    ]
    att = node(
        "transit_gateway_attachment",
        ATT,
        state="available",
        resource_type="vpc",
        resource_id=VPC,
        transit_gateway_id=TGW,
    )
    return {
        "nodes": [vpc, *subnets, att],
        "edges": [edge("attachment", att["id"], vpc["id"], state="available")],
    }


def tgw_key(metric: str, az: str = AZ1) -> tuple:
    return series_key(
        "AWS/TransitGateway",
        metric,
        {
            "AvailabilityZone": az,
            "TransitGateway": TGW,
            "TransitGatewayAttachment": ATT,
        },
    )


def minutes(days: float, value: Any) -> dict[datetime, float]:
    """끝에서 `days` 일 전부터 끝까지 1분 점. `value(시각)` → 값."""
    floor = END.replace(second=0)
    count = int(days * 1440)
    return {
        floor - timedelta(minutes=i): float(value(floor - timedelta(minutes=i)))
        for i in range(1, count + 1)
    }


def run(
    topology: dict[str, Any],
    cw: Any,
    quotas: Any | None = None,
    *,
    at: str = AT,
    previous: dict[str, Any] | None = None,
    snapshot: Snapshot | None = None,
) -> list[dict[str, Any]]:
    clients = {"cloudwatch": cw, "service-quotas": quotas or FakeServiceQuotas()}
    make = factory_by_service(clients)
    return compute_utilization(
        topology,
        snapshot,
        metric_factory=make,
        quota_factory=make,
        at=at,
        previous=previous,
    ).items


def measure(items: list[dict[str, Any]], target: str, name: str) -> dict[str, Any]:
    found = [
        m
        for i in items
        if i["target_id"] == target
        for m in i["measures"]
        if m["measure"] == name
    ]
    assert len(found) == 1, f"전제: {target} 의 {name} 이 하나 있다 — {found}"
    return found[0]


def window(m: dict[str, Any], length: int) -> dict[str, Any]:
    found = [w for w in m.get("windows") or [] if w["minutes"] == length]
    assert len(found) == 1, f"전제: {length}분 창이 하나 있다 — {m}"
    return found[0]


# ─────────────────────────────────────────────── 가장 바빴던 단위 vs 평균(D2)


def busy_hour_daily(moment: datetime) -> float:
    return LIMIT_BYTES_PER_MINUTE if moment.hour == 3 else 0.0


def test_week_badge_is_the_busiest_minute_not_the_period_average() -> None:
    """매일 03시 한 시간만 100% → 1주 배지 = 100% · 평균 = 1/24(≈4%). 배지를 평균으로 바꾸면 4% 가 나온다."""
    cw = FakeMinuteCloudWatch(
        {
            tgw_key("BytesIn"): minutes(8, busy_hour_daily),
            tgw_key("BytesOut"): minutes(8, lambda _m: 0),
            tgw_key("PacketsIn"): minutes(8, lambda _m: 0),
            tgw_key("PacketsOut"): minutes(8, lambda _m: 0),
        }
    )
    bw = measure(
        run(tgw_topology(), cw), nid("transit_gateway_attachment", ATT), "bandwidth"
    )
    week = window(bw, 10080)
    # 사용량(사용자 결정 2026-10-26) — 한도 대비가 아니다: limit 은 null · 기간 합(total) = 바이트
    assert bw["limit"] is None and bw["limit_source"] is None
    assert bw["total_unit"] == "byte"
    assert week["peak"] / FULL_BPS == pytest.approx(1.0)
    assert week["average"] / FULL_BPS == pytest.approx(1 / 24, rel=1e-3)
    assert week["total"] == pytest.approx(7 * 60 * LIMIT_BYTES_PER_MINUTE, rel=1e-6)
    assert week["covered_seconds"] == 10080 * 60
    assert week["peak_direction"] == "in" and week["peak_az"] == AZ1
    assert week["peak_at"].endswith("T03:00:00Z"), (
        "가장 바빴던 단위가 여럿이면 가장 이른 것"
    )
    # 15분 창(11:45~12:00)에는 몰림이 없다 — 짧은 창이 24시간 읽기를 제대로 잘라 썼다.
    assert window(bw, 15)["peak"] == 0
    assert [w["step_seconds"] for w in bw["windows"]] == [60, 60, 60, 60, 300, 3600]


def test_month_folds_to_five_minute_units() -> None:
    """1달 = 5분 단위 — 1분짜리 100% 한 번은 5분 평균 20% 로 녹는다(CloudWatch 보관 해상도의 대가 · ADR 0011 결과)."""
    spike = END.replace(second=0) - timedelta(days=2, minutes=7)
    spike = spike - timedelta(minutes=spike.minute % 5)
    cw = FakeMinuteCloudWatch(
        {
            tgw_key("BytesIn"): minutes(
                3, lambda m: LIMIT_BYTES_PER_MINUTE if m == spike else 0
            )
        }
    )
    bw = measure(
        run(tgw_topology(), cw), nid("transit_gateway_attachment", ATT), "bandwidth"
    )
    assert window(bw, 10080)["peak"] / FULL_BPS == pytest.approx(1.0)
    assert window(bw, 43200)["peak"] / FULL_BPS == pytest.approx(0.2)


def test_young_always_reported_series_divides_average_by_covered_time() -> None:
    """「항상 보낸다」 지표가 2일치뿐이면 1주 평균은 2일로 나눈다 — 1주로 나누면 3.5배 낮게 말한다."""
    cw = FakeMinuteCloudWatch(
        {tgw_key("BytesIn"): minutes(2, lambda _m: LIMIT_BYTES_PER_MINUTE / 2)}
    )
    bw = measure(
        run(tgw_topology(), cw), nid("transit_gateway_attachment", ATT), "bandwidth"
    )
    week = window(bw, 10080)
    assert week["covered_seconds"] == pytest.approx(2 * 86400, abs=60)
    assert week["average"] / FULL_BPS == pytest.approx(0.5, rel=1e-3)


def test_always_reported_series_with_no_points_is_not_zero() -> None:
    """점이 0개인 「항상 보낸다」 지표 = 아직 없던 자원 — 0% 가 아니라 값 없음(못 읽음 ≠ 0 과 같은 뜻)."""
    bw = measure(
        run(tgw_topology(), FakeMinuteCloudWatch()),
        nid("transit_gateway_attachment", ATT),
        "bandwidth",
    )
    assert window(bw, 60)["peak"] is None and window(bw, 60)["covered_seconds"] == 0


# ─────────────────────────────────────────────── 칸 고르기(AZ · 방향 · 엔진)


def test_busiest_az_and_direction_win() -> None:
    """AZ2 의 나가는 쪽이 가장 바쁘면 그 칸 — 두 AZ 를 합하면(한도는 AZ 마다) 실제보다 높게 말한다."""
    cw = FakeMinuteCloudWatch(
        {
            tgw_key("BytesIn", AZ1): minutes(
                1, lambda _m: LIMIT_BYTES_PER_MINUTE * 0.3
            ),
            tgw_key("BytesOut", AZ1): minutes(
                1, lambda _m: LIMIT_BYTES_PER_MINUTE * 0.1
            ),
            tgw_key("BytesIn", AZ2): minutes(
                1, lambda _m: LIMIT_BYTES_PER_MINUTE * 0.2
            ),
            tgw_key("BytesOut", AZ2): minutes(
                1, lambda _m: LIMIT_BYTES_PER_MINUTE * 0.4
            ),
        }
    )
    bw = measure(
        run(tgw_topology((AZ1, AZ2)), cw),
        nid("transit_gateway_attachment", ATT),
        "bandwidth",
    )
    w = window(bw, 60)
    assert (w["peak_az"], w["peak_direction"]) == (AZ2, "out")
    assert w["peak"] / FULL_BPS == pytest.approx(0.4)
    # 사용량은 모든 칸(방향 × AZ)의 합 — 가장 바빴던 칸 하나가 아니다
    assert w["total"] == pytest.approx(
        (0.3 + 0.1 + 0.2 + 0.4) * 60 * LIMIT_BYTES_PER_MINUTE
    )


def test_firewall_takes_the_larger_engine_not_the_sum() -> None:
    """상태 비저장 100 · 상태 저장 60 이 같은 분에 오면 100 — 더하면 같은 바이트를 두 번 센다(160)."""
    fw = node("network_firewall", "app-fw", subnet_ids=["subnet-0aaa1111bbbb0000"])
    sub = node(
        "subnet",
        "subnet-0aaa1111bbbb0000",
        parent=nid("vpc", VPC),
        availability_zone="us-east-1a",
        availability_zone_id=AZ1,
        cidr_block="10.0.0.0/24",
    )
    floor = END.replace(second=0) - timedelta(minutes=3)

    def key(engine: str) -> tuple:
        return series_key(
            "AWS/NetworkFirewall",
            "ReceivedBytes",
            {
                "AvailabilityZone": "us-east-1a",
                "Engine": engine,
                "FirewallName": "app-fw",
            },
        )

    cw = FakeMinuteCloudWatch(
        {key("Stateless"): {floor: 100.0}, key("Stateful"): {floor: 60.0}}
    )
    bw = measure(run({"nodes": [fw, sub], "edges": []}, cw), fw["id"], "bandwidth")
    w = window(bw, 15)
    assert w["peak"] == pytest.approx(100 * 8 / 60, abs=1e-3)
    assert w["peak_az"] == "us-east-1a"
    # 트래픽이 있을 때만 보내는 지표 — 빈 분은 0 이라 창 전체로 나눈다.
    assert w["covered_seconds"] == 15 * 60
    assert w["average"] == pytest.approx(100 * 8 / 900, abs=1e-3)


def test_ec2_never_folds_finer_than_five_minutes() -> None:
    """EC2 기본 모니터링은 5분 합이다 — 60초로 나누면 5배로 말한다."""
    ec2 = node("ec2_instance", "i-0aaa1111bbbb2222", state="running")
    floor = END.replace(second=0)
    floor -= timedelta(minutes=floor.minute % 5 + 5)
    key = series_key("AWS/EC2", "NetworkIn", {"InstanceId": "i-0aaa1111bbbb2222"})
    bw = measure(
        run(
            {"nodes": [ec2], "edges": []}, FakeMinuteCloudWatch({key: {floor: 3000.0}})
        ),
        ec2["id"],
        "bandwidth",
    )
    w = window(bw, 60)
    assert w["step_seconds"] == 300
    assert w["peak"] == pytest.approx(3000 * 8 / 300)
    assert bw["limit"] is None, "EC2 는 값만(D1 · 「Up to」 한도)"


def test_nat_usage_is_bytes_received_plus_sent() -> None:
    """NAT 사용량 = 목적지에서 받은 바이트 + 목적지로 보낸 바이트(합 · 사용자 결정 2026-10-26).

    옛 `PeakBytesPerSecond`(10초 최대)는 받는 쪽을 따로 말하지 못했다 — 실측은 받는 쪽이 8배 컸다(238 MB 대 30 MB).
    """
    nat = node("nat_gateway", "nat-0aaa1111bbbb2222", state="available")
    dims = {"NatGatewayId": "nat-0aaa1111bbbb2222"}
    cw = FakeMinuteCloudWatch(
        {
            series_key("AWS/NATGateway", "BytesInFromDestination", dims): minutes(
                1, lambda _m: 10.0
            ),
            series_key("AWS/NATGateway", "BytesOutToDestination", dims): minutes(
                1, lambda _m: 5.0
            ),
        }
    )
    bw = measure(run({"nodes": [nat], "edges": []}, cw), nat["id"], "bandwidth")
    w = window(bw, 60)
    assert w["total"] == pytest.approx(60 * 15.0)
    assert (
        w["peak"] == pytest.approx(10 * 8 / 60, abs=1e-3)
        and w["peak_direction"] == "in"
    )
    assert bw["limit"] is None


def test_events_are_sums_not_rates() -> None:
    nat = node("nat_gateway", "nat-0aaa1111bbbb2222", state="available")
    key = series_key(
        "AWS/NATGateway",
        "ErrorPortAllocation",
        {"NatGatewayId": "nat-0aaa1111bbbb2222"},
    )
    floor = END.replace(second=0)
    cw = FakeMinuteCloudWatch(
        {key: {floor - timedelta(minutes=5): 3.0, floor - timedelta(hours=3): 4.0}}
    )
    errors = measure(
        run({"nodes": [nat], "edges": []}, cw), nat["id"], "port_allocation_errors"
    )
    assert errors["kind"] == "events" and errors["limit"] is None
    assert window(errors, 15)["sum"] == 3
    assert window(errors, 1440)["sum"] == 7


# ─────────────────────────────────────────────── 빈 응답 ≠ 0(#128 · 불변식 ④)

ALB_DIM = "app/example-alb/0aaa1111bbbb2222"


def alb_series(metric: str) -> tuple:
    return series_key("AWS/ApplicationELB", metric, {"LoadBalancer": ALB_DIM})


def alb_measures(cw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """ALB 사용량(`ProcessedBytes`)·거부 수 — 둘 다 「값이 있을 때만 보내는」(nonzero) 지표다."""
    alb = node(
        "load_balancer",
        "example-alb",
        arn=f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/{ALB_DIM}",
        load_balancer_type="application",
        state="active",
    )
    items = run({"nodes": [alb], "edges": []}, cw)
    return (
        measure(items, alb["id"], "bandwidth"),
        measure(items, alb["id"], "rejected_connections"),
    )


def test_nonzero_events_with_no_points_are_no_data_not_zero() -> None:
    """거부·드롭 수(events)가 점을 하나도 안 보냈다(성공한 빈 응답) — 0 이 아니라 값 없음(#128 그대로)."""
    _bw, errors = alb_measures(FakeMinuteCloudWatch())
    assert "unread" not in errors, "읽기는 성공했다 — 못 읽음이 아니다"
    for w in errors["windows"]:
        assert w.get("no_points") is True, w
        assert w["covered_seconds"] == 0
        assert "sum" not in w and "peak_at" not in w


def test_usage_metric_with_no_points_is_zero() -> None:
    """사용량 지표(ALB `ProcessedBytes`)는 트래픽이 없으면 AWS 가 점을 보내지 않는다 → **0**(사용자 결정 2026-10-26 ④).

    「값 없음」으로 두면 놀고 있는 ALB 가 「확인하지 못함」처럼 보인다. 덮은 시간 = 창 전체.
    """
    bw, _errors = alb_measures(FakeMinuteCloudWatch())
    assert "unread" not in bw
    for w in bw["windows"]:
        assert "no_points" not in w, w
        assert (w["peak"], w["average"], w["total"]) == (0.0, 0.0, 0.0)
        assert w["covered_seconds"] == w["minutes"] * 60


def test_nonzero_metric_with_real_zero_points_is_zero() -> None:
    """점이 왔는데 0 이면 정말 0 — 값 없음으로 바꾸면 「조용한 자원」이 「모르는 자원」이 된다."""
    bw, errors = alb_measures(
        FakeMinuteCloudWatch(
            {
                alb_series("ProcessedBytes"): minutes(1, lambda _m: 0),
                alb_series("RejectedConnectionCount"): minutes(1, lambda _m: 0),
            }
        )
    )
    w = window(bw, 15)
    assert "no_points" not in w and w["peak"] == 0 and w["covered_seconds"] == 900
    e = window(errors, 15)
    assert "no_points" not in e and e["sum"] == 0


def test_nonzero_metric_is_no_data_only_in_the_windows_without_points() -> None:
    """3시간 전에만 점 하나 — events 15분·1시간 창 = 값 없음 · 사용량 15분·1시간 = 0 · 24시간 창 = 그 값(빈 단위는 0)."""
    floor = END.replace(second=0)
    bw, errors = alb_measures(
        FakeMinuteCloudWatch(
            {
                alb_series("ProcessedBytes"): {floor - timedelta(hours=3): 10.0},
                alb_series("RejectedConnectionCount"): {
                    floor - timedelta(hours=3): 4.0
                },
            }
        )
    )
    assert window(errors, 15).get("no_points") is True
    assert window(errors, 60).get("no_points") is True
    assert "no_points" not in window(errors, 1440)
    assert window(bw, 15)["total"] == 0 and window(bw, 60)["total"] == 0
    assert all("no_points" not in w for w in bw["windows"])
    assert window(bw, 1440)["peak"] == pytest.approx(10.0 * 8 / 60, abs=1e-3)
    assert window(bw, 1440)["total"] == pytest.approx(10.0)
    assert window(bw, 1440)["covered_seconds"] == 1440 * 60
    assert window(errors, 1440)["sum"] == 4


def test_next_token_pages_are_joined() -> None:
    """한 응답이 다 못 담으면 `NextToken` — 첫 장만 쓰면 몰림이 뒷장에 있을 때 놓친다."""
    series = {tgw_key("BytesIn"): minutes(1, busy_hour_daily)}
    whole = run(tgw_topology(), FakeMinuteCloudWatch(series))
    paged_cw = FakeMinuteCloudWatch(series, page_size=97)
    assert run(tgw_topology(), paged_cw) == whole
    assert any("NextToken" in c for c in paged_cw.calls), "전제: 정말로 여러 장이었다"


def test_failed_read_is_unread_not_zero() -> None:
    denied = client_error("AccessDenied", "GetMetricData")
    bw = measure(
        run(tgw_topology(), FakeMinuteCloudWatch(error=denied)),
        nid("transit_gateway_attachment", ATT),
        "bandwidth",
    )
    assert bw["unread"]["code"] == "access_denied"
    assert "windows" not in bw


# ─────────────────────────────────────────────── 서비스 한도(D5)


def rt_topology() -> dict[str, Any]:
    vpc = node("vpc", VPC, cidr_blocks=["10.0.0.0/16"])
    rt = node(
        "route_table",
        "rtb-0aaa1111",
        parent=vpc["id"],
        ipv4_route_count=312,
        ipv6_route_count=0,
        propagated_route_count=3,
    )
    return {"nodes": [vpc, rt], "edges": []}


def routes(quotas: FakeServiceQuotas) -> dict[str, Any]:
    return measure(
        run(rt_topology(), FakeMinuteCloudWatch(), quotas),
        nid("route_table", "rtb-0aaa1111"),
        "routes_ipv4",
    )


def test_raised_account_quota_is_the_denominator() -> None:
    m = routes(
        FakeServiceQuotas(
            applied={"L-93826ACB": 1000.0}, defaults={"L-93826ACB": 500.0}
        )
    )
    assert (m["limit"], m["limit_source"], m["quota_code"]) == (
        1000,
        "account_quota",
        "L-93826ACB",
    )
    assert m["now"] == {"used": 312}
    assert "limit_reason" not in m


def test_no_applied_value_falls_back_to_aws_default_without_a_reason() -> None:
    """적용 값이 없는 것은 실패가 아니다(문서) — 이유를 달면 화면이 「못 읽음」을 말한다."""
    m = routes(FakeServiceQuotas(defaults={"L-93826ACB": 500.0}))
    assert (m["limit"], m["limit_source"]) == (500, "default_quota")
    assert "limit_reason" not in m


def test_denied_quota_read_falls_back_with_the_reason() -> None:
    denied = client_error("AccessDeniedException", "GetServiceQuota")
    m = routes(FakeServiceQuotas(defaults={"L-93826ACB": 500.0}, applied_error=denied))
    assert (m["limit"], m["limit_source"]) == (500, "default_quota")
    assert m["limit_reason"]["code"] == "access_denied"


def test_both_quota_reads_failing_use_the_documented_default() -> None:
    denied = client_error("AccessDeniedException", "GetServiceQuota")
    m = routes(FakeServiceQuotas(applied_error=denied, default_error=denied))
    assert (m["limit"], m["limit_source"]) == (500, "default_quota")
    assert m["limit_reason"]["code"] == "access_denied"


def test_propagated_routes_have_their_own_fixed_limit() -> None:
    m = measure(
        run(rt_topology(), FakeMinuteCloudWatch()),
        nid("route_table", "rtb-0aaa1111"),
        "routes_propagated",
    )
    assert (m["limit"], m["limit_source"], m["now"]) == (100, "fixed", {"used": 3})


# ─────────────────────────────────────────────── 개수 한도(D3)


def test_security_group_reports_the_fullest_cell() -> None:
    sg = node(
        "security_group",
        "sg-0aaa1111",
        parent=nid("vpc", VPC),
        ingress_ipv4_rule_count=7,
        ingress_ipv6_rule_count=2,
        egress_ipv4_rule_count=9,
        egress_ipv6_rule_count=0,
        ingress_prefix_list_rule_count=0,
        egress_prefix_list_rule_count=0,
    )
    m = measure(
        run({"nodes": [sg], "edges": []}, FakeMinuteCloudWatch()),
        sg["id"],
        "security_group_rules",
    )
    assert (m["now"], m["part"]) == ({"used": 9}, "egress_ipv4")


def test_security_group_with_prefix_list_rules_has_unknown_limit() -> None:
    """접두사 목록 규칙은 목록의 최대 항목 수만큼 센다 — 그 수를 모르면 60 으로 나눈 %는 거짓이다."""
    sg = node(
        "security_group",
        "sg-0aaa1111",
        parent=nid("vpc", VPC),
        ingress_ipv4_rule_count=7,
        ingress_ipv6_rule_count=0,
        egress_ipv4_rule_count=1,
        egress_ipv6_rule_count=0,
        ingress_prefix_list_rule_count=1,
        egress_prefix_list_rule_count=0,
    )
    m = measure(
        run({"nodes": [sg], "edges": []}, FakeMinuteCloudWatch()),
        sg["id"],
        "security_group_rules",
    )
    assert m["limit"] is None and m["limit_source"] is None


def test_vpc_counts_children_and_active_peerings() -> None:
    vpc = node("vpc", VPC, cidr_blocks=["10.0.0.0/16", "10.1.0.0/16"])
    kids = [
        node("subnet", f"subnet-0aaa{i}", parent=vpc["id"], cidr_block="10.0.0.0/24")
        for i in range(3)
    ]
    endpoints = [
        node(
            "vpc_endpoint",
            "vpce-0aaa1",
            parent=vpc["id"],
            endpoint_type="Interface",
            state="available",
        ),
        node(
            "vpc_endpoint",
            "vpce-0aaa2",
            parent=vpc["id"],
            endpoint_type="Gateway",
            state="available",
        ),
        node(
            "vpc_endpoint",
            "vpce-0aaa3",
            parent=vpc["id"],
            endpoint_type="GatewayLoadBalancer",
            state="available",
        ),
    ]
    edges = [
        edge(
            "peering", nid("vpc_peering_connection", "pcx-1"), vpc["id"], state="active"
        ),
        edge(
            "peering",
            nid("vpc_peering_connection", "pcx-2"),
            vpc["id"],
            state="deleted",
        ),
    ]
    items = run(
        {"nodes": [vpc, *kids, *endpoints], "edges": edges}, FakeMinuteCloudWatch()
    )
    used = {
        m["measure"]: m["now"]["used"]
        for i in items
        if i["target_id"] == vpc["id"]
        for m in i["measures"]
    }
    assert used == {
        "vpc_active_peerings": 1,
        "vpc_interface_endpoints": 2,
        "vpc_ipv4_cidrs": 2,
        "vpc_network_acls": 0,
        "vpc_route_tables": 0,
        "vpc_subnets": 3,
    }


def snapshot_with(free: int | None, *, failed: bool = False) -> Snapshot:
    attrs = {} if free is None else {"available_ip_address_count": free}
    nodes = (
        ()
        if failed
        else (
            Node(
                nid("subnet", "subnet-0aaa1"),
                "subnet",
                ACCOUNT,
                REGION,
                "subnet-0aaa1",
                attributes=attrs,
            ),
        )
    )
    cov = CoverageEntry(
        ACCOUNT,
        REGION,
        "subnet",
        "failed" if failed else "ok",
        Reason("access_denied", "ec2:DescribeSubnets", "거부") if failed else None,
    )
    return Snapshot(
        AT, "test", Scope((ACCOUNT,), (REGION,), ("subnet",)), nodes, (), (cov,)
    )


def subnet_topology() -> dict[str, Any]:
    return {
        "nodes": [
            node(
                "subnet",
                "subnet-0aaa1",
                parent=nid("vpc", VPC),
                cidr_block="10.0.0.0/24",
            )
        ],
        "edges": [],
    }


def test_tgw_routes_are_counted_from_route_edges_once_per_destination() -> None:
    """TGW 라우트는 테이블 노드가 아니라 `route` 엣지에 있다 · ECMP 로 두 엣지에 실린 목적지는 1개 · 블랙홀도 센다.

    라우트 검색이 실패한 테이블(`blackhole_destinations` 키 없음)이 하나라도 있으면 합을 내지 않는다 — 모자란 합은
    「여유 있다」로 읽힌다.
    """
    tgw = node("transit_gateway", TGW)
    table = node(
        "transit_gateway_route_table",
        "tgw-rtb-0aaa1",
        transit_gateway_id=TGW,
        blackhole_destinations=["10.9.0.0/16"],
    )
    a1, a2 = (
        nid("transit_gateway_attachment", "a1"),
        nid("transit_gateway_attachment", "a2"),
    )
    edges = [
        edge("route", table["id"], a1, destinations=["10.0.0.0/16", "10.1.0.0/16"]),
        edge("route", table["id"], a2, destinations=["10.1.0.0/16"]),  # ECMP
    ]
    items = run({"nodes": [tgw, table], "edges": edges}, FakeMinuteCloudWatch())
    assert measure(items, tgw["id"], "tgw_routes")["now"]["used"] == 3

    failed = node(
        "transit_gateway_route_table", "tgw-rtb-0aaa2", transit_gateway_id=TGW
    )
    items = run({"nodes": [tgw, table, failed], "edges": edges}, FakeMinuteCloudWatch())
    names = {
        m["measure"]
        for i in items
        if i["target_id"] == tgw["id"]
        for m in i["measures"]
    }
    assert "tgw_route_tables" in names, "전제: TGW 의 다른 개수는 그대로 나온다"
    assert "tgw_routes" not in names


def test_subnet_used_ips_are_size_minus_five_minus_free() -> None:
    m = measure(
        run(subnet_topology(), FakeMinuteCloudWatch(), snapshot=snapshot_with(200)),
        nid("subnet", "subnet-0aaa1"),
        "ip_addresses",
    )
    assert (m["limit"], m["limit_source"], m["now"]) == (
        251,
        "arithmetic",
        {"used": 51},
    )


def test_subnet_ips_unread_when_the_state_read_failed() -> None:
    m = measure(
        run(
            subnet_topology(),
            FakeMinuteCloudWatch(),
            snapshot=snapshot_with(None, failed=True),
        ),
        nid("subnet", "subnet-0aaa1"),
        "ip_addresses",
    )
    assert m["unread"]["code"] == "access_denied" and "now" not in m


# ─────────────────────────────────────────────── 이월(D5 · D6)


def as_previous(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"utilization": items}


def test_long_windows_and_quotas_ride_forward_within_the_hour() -> None:
    series = {tgw_key("BytesIn"): minutes(8, busy_hour_daily)}
    topo = {
        "nodes": tgw_topology()["nodes"] + rt_topology()["nodes"][1:],
        "edges": tgw_topology()["edges"],
    }
    first = run(
        topo,
        FakeMinuteCloudWatch(series),
        FakeServiceQuotas(applied={"L-93826ACB": 1000.0}),
    )

    later = "2026-10-04T12:10:30Z"
    cw = FakeMinuteCloudWatch(series)
    quotas = FakeServiceQuotas(applied={"L-93826ACB": 2000.0})
    second = run(topo, cw, quotas, at=later, previous=as_previous(first))
    att = nid("transit_gateway_attachment", ATT)
    assert window(measure(second, att, "bandwidth"), 10080) == window(
        measure(first, att, "bandwidth"), 10080
    )
    assert window(measure(second, att, "bandwidth"), 15)["computed_at"] == later
    spans = {
        round((c["EndTime"] - c["StartTime"]).total_seconds() / 3600) for c in cw.calls
    }
    assert spans == {24}, "1시간 안에는 긴 묶음을 읽지 않는다"
    assert quotas.calls == [], "1시간 안에는 한도를 읽지 않는다"
    assert (
        measure(second, nid("route_table", "rtb-0aaa1111"), "routes_ipv4")["limit"]
        == 1000
    )

    hour_later = "2026-10-04T13:00:30Z"
    third = run(
        topo,
        FakeMinuteCloudWatch(series),
        quotas,
        at=hour_later,
        previous=as_previous(second),
    )
    assert window(measure(third, att, "bandwidth"), 10080)["computed_at"] == hour_later
    assert (
        measure(third, nid("route_table", "rtb-0aaa1111"), "routes_ipv4")["limit"]
        == 2000
    )


def test_same_input_same_bytes() -> None:
    series = {tgw_key("BytesIn"): minutes(1, busy_hour_daily)}
    assert run(tgw_topology((AZ1, AZ2)), FakeMinuteCloudWatch(series)) == run(
        tgw_topology((AZ1, AZ2)), FakeMinuteCloudWatch(series)
    )


# ─────────────────────────────────────────────── 실제 수집기 출력으로(속성 키가 맞나 · 계약)


def test_plans_from_real_collector_output_and_satisfies_the_schema() -> None:
    """가짜 계정 전체를 수집기로 굽고 사용률을 붙인다 — 수집기의 속성 키가 바뀌면 그 종류의 measure 가 조용히 사라진다.

    그래서 「무엇이 나와야 하나」를 종류별로 센다(0 이면 FAIL). 스키마 검증은 그 위에.
    """
    import json
    from pathlib import Path

    import jsonschema
    from fakes import FakeAws, FakeCloudWatch, factory
    from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
    from nettopo.metrics import read_metrics
    from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation

    structure_at, observed_at = "2026-10-04T11:00:00Z", AT

    def snap(types: tuple[str, ...], at: str, state: bool) -> Snapshot:
        scope = Scope(account_ids=(ACCOUNT,), regions=(REGION,), resource_types=types)
        return collect(scope, factory(FakeAws()), clock=lambda: at, with_state=state)

    topology = snap(SUPPORTED_RESOURCE_TYPES, structure_at, False).to_document()
    state = snap(OBSERVED_RESOURCE_TYPES, observed_at, True)
    layer = read_metrics(state, factory(FakeCloudWatch()), at=observed_at)
    # 실제 AWS 처럼 코드마다 기본값이 있고 계정 값은 없다(늘린 적 없는 계정).
    from nettopo import utilization as module

    defaults = {
        v[1]: v[2]
        for k, v in vars(module).items()
        if k.startswith("QUOTA_") and isinstance(v, tuple)
    }
    items = run(
        topology,
        FakeMinuteCloudWatch(),
        FakeServiceQuotas(defaults=defaults),
        snapshot=state,
    )
    document, _ = build_observation(
        state,
        topology=topology,
        observed_at=observed_at,
        tool_version="test",
        metrics=layer,
        utilization=items,
    )
    root = Path(__file__).resolve().parents[2]
    schema = json.loads(
        (root / "schema" / "observation.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(document)
    assert document["layers"]["utilization"] is True

    types = {n["id"]: n["type"] for n in topology["nodes"]}
    seen = {(types[i["target_id"]], m["measure"]) for i in items for m in i["measures"]}
    expected = {
        ("transit_gateway_attachment", "bandwidth"),
        ("direct_connect_virtual_interface", "bandwidth"),
        ("network_firewall", "bandwidth"),
        ("load_balancer", "bandwidth"),
        ("load_balancer", "rejected_connections"),
        ("nat_gateway", "bandwidth"),
        ("nat_gateway", "port_allocation_errors"),
        ("ec2_instance", "bandwidth"),
        ("rds_db_instance", "bandwidth"),
        ("cloudfront", "bandwidth"),
        ("security_group", "security_group_rules"),
        ("network_acl", "network_acl_entries"),
        ("route_table", "routes_ipv4"),
        ("route_table", "routes_propagated"),
        ("subnet", "ip_addresses"),
        ("vpc", "vpc_subnets"),
        ("vpc", "vpc_ipv4_cidrs"),
        ("transit_gateway", "tgw_attachments"),
        ("transit_gateway", "tgw_routes"),
        ("direct_connect_gateway", "dxgw_virtual_interfaces"),
    }
    assert expected - seen == set(), (
        f"수집기 출력에서 계획되지 않았다: {sorted(expected - seen)}"
    )
    # 계정 값 없음(NoSuchResource) → AWS 기본값 · 이유 없음. 코드가 틀렸으면 기본값 호출도 NoSuchResource 라
    # 이유가 붙는다 → 이 어서션이 우리 코드표의 오타를 잡는다.
    quoted = [m for i in items for m in i["measures"] if m.get("quota_code")]
    assert quoted, "전제: 서비스 한도로 나눈 measure 가 있다"
    assert [
        m for m in quoted if "limit_reason" in m or m["limit_source"] != "default_quota"
    ] == []
    nacl = next(m for m in quoted if m["measure"] == "network_acl_entries")
    assert nacl["limit"] == 20


def test_utilization_stands_without_the_metric_layer() -> None:
    """「사용률만」(2층 끔) — 같은 사용률이 붙고 문서가 계약을 지킨다(스키마 1.5.0 · ADR 0011 D7 개정 2026-10-06).

    살아 있는 짝: 흐름 로그는 여전히 2층 없이는 거부된다 — 2층 검사 자체가 지워진 것이 아니다.
    """
    import json
    from pathlib import Path

    import jsonschema
    import pytest
    from fakes import FakeAws, factory
    from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
    from nettopo.flowlogs import FlowLogReading
    from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation

    def snap(types: tuple[str, ...], at: str, state: bool) -> Snapshot:
        scope = Scope(account_ids=(ACCOUNT,), regions=(REGION,), resource_types=types)
        return collect(scope, factory(FakeAws()), clock=lambda: at, with_state=state)

    topology = snap(
        SUPPORTED_RESOURCE_TYPES, "2026-10-04T11:00:00Z", False
    ).to_document()
    state = snap(OBSERVED_RESOURCE_TYPES, AT, True)
    items = run(topology, FakeMinuteCloudWatch(), FakeServiceQuotas(), snapshot=state)
    assert items, "전제: 사용률 행이 있다"
    document, _ = build_observation(
        state, topology=topology, observed_at=AT, tool_version="test", utilization=items
    )
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schema" / "observation.schema.json"
        ).read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(document)
    assert (document["layers"]["metric"], document["layers"]["utilization"]) == (
        False,
        True,
    )
    assert document["utilization"] == items
    with pytest.raises(ValueError, match="2층"):
        build_observation(
            state,
            topology=topology,
            observed_at=AT,
            tool_version="test",
            flow_logs=FlowLogReading.__new__(FlowLogReading),
        )


def test_cloudfront_metrics_are_read_in_us_east_1() -> None:
    """CloudFront 지표는 us-east-1 에만 있다 — 배포 리전 클라이언트로 물으면 「점 0개」가 와서 「조용함」과 같아진다."""
    cf = node("cloudfront", "E1AAAAAAAAAAAA")
    cf["region"] = "us-west-2"
    regions: list[str] = []
    cw = FakeMinuteCloudWatch()

    def make(account_id: str, region: str, service: str) -> Any:
        regions.append(region)
        return cw if service == "cloudwatch" else FakeServiceQuotas()

    compute_utilization(
        {"nodes": [cf], "edges": []},
        None,
        metric_factory=make,
        quota_factory=make,
        at=AT,
    )
    assert regions == ["us-east-1"]
    dims = {
        d["Name"]: d["Value"]
        for d in cw.calls[0]["MetricDataQueries"][0]["MetricStat"]["Metric"][
            "Dimensions"
        ]
    }
    assert dims == {"DistributionId": "E1AAAAAAAAAAAA", "Region": "Global"}


def test_dx_usage_has_no_line_bandwidth_denominator() -> None:
    """DX VIF = 평균 bit/s(이미 속도) · 회선 대역폭 분모는 뺐다(사용량 · 2026-10-26) · 기간 합 = 속도 × 초 ÷ 8."""
    conn = node(
        "direct_connect_connection",
        "dxcon-fg0aaa1111",
        bandwidth="50Mbps",
        state="available",
    )
    vif = node(
        "direct_connect_virtual_interface",
        "dxvif-fg0aaa1111",
        state="available",
        connection_id="dxcon-fg0aaa1111",
    )
    key = series_key(
        "AWS/DX",
        "VirtualInterfaceBpsEgress",
        {"ConnectionId": "dxcon-fg0aaa1111", "VirtualInterfaceId": "dxvif-fg0aaa1111"},
    )
    cw = FakeMinuteCloudWatch(
        {key: minutes(1, lambda m: 40e6 if m.minute == 30 else 10e6)}
    )
    bw = measure(run({"nodes": [conn, vif], "edges": []}, cw), vif["id"], "bandwidth")
    assert (bw["limit"], bw["limit_source"]) == (None, None)
    assert "limit_scope" not in bw
    w = window(bw, 60)
    assert w["total"] == pytest.approx((40e6 + 59 * 10e6) * 60 / 8)
    assert w["peak"] == pytest.approx(40e6) and w["peak_direction"] == "out"
    # 이미 속도인 값의 평균 = 점들의 평균(합 ÷ 덮은 초가 아니다 — 그러면 60배 작다).
    assert w["average"] == pytest.approx((40e6 + 59 * 10e6) / 60)


# ─────────────────────────────────────────────── 사용량(사용자 결정 2026-10-26 · 관측 1.18.0)


def test_alb_usage_is_processed_bytes_not_capacity() -> None:
    """ALB = `ProcessedBytes` — `CapacityUtilization` 은 요청 0건 ALB 와 671건 ALB 가 같은 20~40 이었다(실측)."""
    bw, _errors = alb_measures(
        FakeMinuteCloudWatch(
            {alb_series("ProcessedBytes"): minutes(1, lambda _m: 600.0)}
        )
    )
    assert (bw["unit"], bw["total_unit"], bw["limit"]) == (
        "bit_per_second",
        "byte",
        None,
    )
    assert window(bw, 15)["total"] == pytest.approx(15 * 600.0)
    alb = node(
        "load_balancer",
        "example-alb",
        arn=f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/{ALB_DIM}",
        load_balancer_type="application",
        state="active",
    )
    items = run({"nodes": [alb], "edges": []}, FakeMinuteCloudWatch())
    assert "capacity" not in {m["measure"] for i in items for m in i["measures"]}


def test_vpn_on_vgw_reads_tunnel_bytes_with_health_check_note() -> None:
    """VGW VPN = `TunnelDataIn/Out` + 「점검 바이트 포함」(결정 ⑤) · TGW VPN = 읽지 않는다(그 어태치먼트가 말한다)."""
    vgw_vpn = node(
        "vpn_connection",
        "vpn-0aaa1111bbbb0001",
        state="available",
        vpn_gateway_id="vgw-0aaa1111",
    )
    tgw_vpn = node(
        "vpn_connection",
        "vpn-0aaa1111bbbb0002",
        state="available",
        transit_gateway_id=TGW,
    )
    dims = {"VpnId": "vpn-0aaa1111bbbb0001"}
    cw = FakeMinuteCloudWatch(
        {
            series_key("AWS/VPN", "TunnelDataIn", dims): minutes(1, lambda _m: 2.0),
            series_key("AWS/VPN", "TunnelDataOut", dims): minutes(1, lambda _m: 3.0),
        }
    )
    items = run({"nodes": [vgw_vpn, tgw_vpn], "edges": []}, cw)
    bw = measure(items, vgw_vpn["id"], "bandwidth")
    assert bw["includes"] == "tunnel_health_checks"
    assert window(bw, 15)["total"] == pytest.approx(15 * 5.0)
    assert all(i["target_id"] != tgw_vpn["id"] for i in items)


def test_lambda_and_api_gateway_count_calls() -> None:
    """바이트가 없는 서비스 = 횟수 · 빈 창 = 0 회(결정 ④) · REST = ApiName · HTTP = ApiId(+ DataProcessed 바이트)."""
    fn = node("lambda_function", "orders-fn")
    rest = node("api_gateway", "a1b2c3d4e5", protocol="REST")
    rest["name"] = "orders-api"
    http = node("api_gateway", "q9w8e7r6t5", protocol="HTTP")
    floor = END.replace(second=0)
    cw = FakeMinuteCloudWatch(
        {
            series_key("AWS/Lambda", "Invocations", {"FunctionName": "orders-fn"}): {
                floor - timedelta(minutes=3): 4.0,
                floor - timedelta(minutes=2): 5.0,
            },
            series_key("AWS/ApiGateway", "Count", {"ApiName": "orders-api"}): {
                floor - timedelta(minutes=2): 7.0
            },
            series_key("AWS/ApiGateway", "DataProcessed", {"ApiId": "q9w8e7r6t5"}): {
                floor - timedelta(minutes=2): 300.0
            },
        }
    )
    items = run({"nodes": [fn, rest, http], "edges": []}, cw)
    inv = measure(items, fn["id"], "invocations")
    assert (inv["unit"], inv["total_unit"]) == ("count_per_second", "count")
    assert window(inv, 15)["total"] == 9
    assert window(inv, 15)["peak"] == pytest.approx(5 / 60, abs=1e-3)
    assert window(measure(items, rest["id"], "requests"), 15)["total"] == 7
    assert window(measure(items, http["id"], "bandwidth"), 15)["total"] == 300
    # 호출이 없던 HTTP API 의 요청 = 0(값 없음이 아니다)
    assert window(measure(items, http["id"], "requests"), 15)["total"] == 0


def test_global_accelerator_metrics_are_read_in_us_west_2() -> None:
    ga = node("global_accelerator", "8bef1d36-0000-0000-0000-000000000000")
    ga["region"] = None
    regions: list[str] = []
    cw = FakeMinuteCloudWatch()

    def make(account_id: str, region: str, service: str) -> Any:
        regions.append(region)
        return cw if service == "cloudwatch" else FakeServiceQuotas()

    compute_utilization(
        {"nodes": [ga], "edges": []},
        None,
        metric_factory=make,
        quota_factory=make,
        at=AT,
    )
    assert regions == ["us-west-2"]


def test_rds_connections_ride_with_throughput() -> None:
    """RDS 처리량은 연결 0 이어도 ≈ 100 kbps(실측) — 「아무도 안 쓴다」는 연결 수(보조 measure)가 말한다."""
    db = node("rds_db_instance", "orders-db")
    dims = {"DBInstanceIdentifier": "orders-db"}
    cw = FakeMinuteCloudWatch(
        {series_key("AWS/RDS", "DatabaseConnections", dims): minutes(1, lambda _m: 0.0)}
    )
    conns = measure(run({"nodes": [db], "edges": []}, cw), db["id"], "connections")
    assert (conns["kind"], conns["unit"]) == ("rate", "count")
    assert "total_unit" not in conns
    assert window(conns, 15)["peak"] == 0
