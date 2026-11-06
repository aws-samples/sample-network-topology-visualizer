"""2층(지표) 축 — ADR 0008 의 검사(D2 A급 지표 · D3 차단 신호 · D6 창 전체 · D8 구간 지표).

🔴 **이 파일의 이유는 「2층은 실패하지 않고 조용히 빈다」는 실측이다**(ADR 0006 §O7-1 함정 1:
차원 이름을 틀려도 `dp=0` + `StatusCode: Complete` + `Messages` 없음). 그래서 「실계정에서 돌려
봤고 에러가 없었다」는 2층이 동작한다는 증거가 **되지 못한다** — 차원 상수가 통째로 틀려도 같은
화면이 나온다(전부 `up`, 전부 「흐름 없음」). 판별력은 여기서만 만들어진다: `fakes.FakeCloudWatch`
는 등록한 차원과 **정확히** 일치할 때만 값을 주고, 틀리면 실제 API 처럼 조용히 빈 배열을 준다.

잣대 4개가 서로를 잡는다:
1. **정본 ↔ 기대값 ↔ 코드** — 프로브 상수를 이 파일의 기대표와 비교하고, 그 기대표의 모든 이름이
   ADR 0008 D2·D3 표에 실제로 적혀 있는지 다시 확인한다. 한쪽만 고치면 FAIL 한다.
2. **쿼리 모양** — `period=60` · 창 **900초**(D6) · 창의 끝 = **스냅샷 시각** · 사다리 없음 ·
   **같은 계열은 한 번만 산다**(요금은 요청한 지표 수로 매겨진다).
3. **판정** — 흐른다(`live`) / 쟀고 0(`up`+`datapoints: 0`) / 빈 배열(`up`+`?`) / 통과 0 + 드롭
   (`down`) / 막혔다(`up`+`partial`) / 지표 없는 종류(조용함). 🔴 **드롭 > 0 은 흐름을 부정하지
   않는다**(D3)와 🔴 **빈 배열이 1층 `up` 을 덮지 않는다**가 이 층의 핵심 계약이다.
4. **구간(엣지) 지표** — ADR 0008 D8 의 본체다. 「2층 전용」 엣지 3종은 **2층이 답했을 때만 행이
   생기고**, 못 쟀으면 **행이 없다**(D1 의 E). 어태치먼트↔VPC 선은 잰 대상이 달라서
   `evidence.observed_on` 으로 「연역」이라고 적는다(D1 의 B). 이 잣대가 0 이던 동안 실계정
   445행 중 2층 근거를 가진 엣지 행이 **0** 이었고, 화면의 초록 선 14개는 전부 종단 추론이었다.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import (
    ACCOUNT,
    APP_VPC,
    CORE_TGW,
    DXGW_TGW,
    EGRESS_NAT,
    LOAD_BALANCERS,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    TRANSIT_GATEWAY_ATTACHMENTS,
    VPC_ENDPOINTS,
    FakeAws,
    FakeCloudWatch,
    client_error,
    factory,
    factory_by_service,
    series_key,
)
from internal_docs import read_or_skip
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.history import apply_history
from nettopo.metrics import (
    ACCEPTED_ENDPOINT_PROBES,
    AGG_MAX,
    AGG_MIN,
    AGG_SUM,
    EDGE_PROBES,
    METRIC_API,
    METRIC_PENDING_TYPES,
    NO_METRIC_TYPES,
    NODE_PROBES,
    PERIOD_SECONDS,
    REPORT_ALWAYS,
    REPORT_CONDITIONAL,
    REPORT_NONZERO_ONLY,
    REPORT_UNKNOWN,
    ROLE_DROP,
    ROLE_FLOW,
    ROLE_HEALTH,
    ROLE_STATE,
    WINDOW_SECONDS,
    MetricLayer,
    MetricRef,
    Reading,
    _fold,
    edge_probe_for,
    plan,
    probe_for,
    read_metrics,
    read_series,
)
from nettopo.model import Scope
from nettopo.observe import (
    METRIC_ONLY_EDGES,
    OBSERVED_RESOURCE_TYPES,
    build_observation,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "observation.schema.json").read_text(encoding="utf-8")
)


def _adr6() -> str:
    """ADR 0006 — 내부 설계 문서라 공개 트리에는 없다. 쓰는 테스트만 사유를 적고 건너뛴다(import 때 읽지 않는다)."""
    return read_or_skip("docs/adr/0006-observation-schema.md")


def _adr8() -> str:
    """ADR 0008 — 위와 같다."""
    return read_or_skip("docs/adr/0008-flow-verdict.md")


STRUCTURE_AT = "2026-09-23T00:00:00Z"
OBSERVED_AT = "2026-09-23T00:05:00Z"

NAT_ID = f"nat_gateway/{ACCOUNT}/{REGION}/{EGRESS_NAT}"
DEAD_NAT_ID = f"nat_gateway/{ACCOUNT}/{REGION}/nat-0fff6666aaaa7777"
TGW_ID = f"transit_gateway/{ACCOUNT}/{REGION}/{CORE_TGW}"
ATTACH = "tgw-attach-0aaa1111bbbb8888"
ATTACH2 = "tgw-attach-0bbb2222cccc9999"
ATTACH3 = "tgw-attach-0ccc3333dddd0000"
ATTACH_ID = f"transit_gateway_attachment/{ACCOUNT}/{REGION}/{ATTACH}"
ATTACH2_ID = f"transit_gateway_attachment/{ACCOUNT}/{REGION}/{ATTACH2}"
ATTACH3_ID = f"transit_gateway_attachment/{ACCOUNT}/{REGION}/{ATTACH3}"
# DXGW 어태치먼트(`fakes.py` — 2.1.0 부터 대상 노드가 있다 · ADR 0010). 노드·TGW 선이 계획된다.
ATTACH_DX = "tgw-attach-0ddd4444eeee1111"
ATTACH_DX_ID = f"transit_gateway_attachment/{ACCOUNT}/{REGION}/{ATTACH_DX}"
ENDPOINT = "vpce-0bbb2222cccc8888"
ENDPOINT_ID = f"vpc_endpoint/{ACCOUNT}/{REGION}/{ENDPOINT}"
GATEWAY_ENDPOINT_ID = f"vpc_endpoint/{ACCOUNT}/{REGION}/vpce-0aaa1111bbbb7777"
ALB_ID = f"load_balancer/{ACCOUNT}/{REGION}/app-alb"
NLB_ID = f"load_balancer/{ACCOUNT}/{REGION}/old-nlb"
VPC_ID = f"vpc/{ACCOUNT}/{REGION}/{APP_VPC}"
PUBLIC_SUBNET_ID = f"subnet/{ACCOUNT}/{REGION}/{PUBLIC_SUBNET}"
PRIVATE_SUBNET_ID = f"subnet/{ACCOUNT}/{REGION}/{PRIVATE_SUBNET}"

# 「2층 전용」 엣지(ADR 0008 D8-1). 🔴 이 선들은 1층 근거가 없다 — 지표가 답하지 않으면 **행이
# 없는 것**이 정답이고, `up` 을 적으면 근거 0 인 주장이 된다.
ATTACH_TGW_EDGE = f"attachment|{ATTACH_ID}|{TGW_ID}"
ATTACH_VPC_EDGE = f"attachment|{ATTACH_ID}|{VPC_ID}"
ATTACH2_TGW_EDGE = f"attachment|{ATTACH2_ID}|{TGW_ID}"
# 2.6.0 — ATTACH2 는 VPN 어태치먼트다(`fakes.py`). 대상 노드(`vpn_connection`)가 생겨 그 쪽 선도 계획된다 —
# 어태치먼트 차원 지표는 VPN 어태치먼트에도 똑같이 나온다(VPC 쪽 선과 같은 차원).
ATTACH2_VPN_EDGE = (
    f"attachment|{ATTACH2_ID}|vpn_connection/{ACCOUNT}/{REGION}/vpn-0aaa1111bbbb3333"
)
ATTACH_DX_TGW_EDGE = f"attachment|{ATTACH_DX_ID}|{TGW_ID}"
# 2026-10-13 — DXGW 가 관측 대상이 되어(1층 · ADR 0006 §O7 「DX 3종」) 상태 스냅샷에도 DXGW 노드가 있다 → DXGW 쪽 선도
# 계획된다. VPN 쪽 선(위 `ATTACH2_VPN_EDGE`)과 같은 차원 · 같은 취급이다.
ATTACH_DX_DXGW_EDGE = (
    f"attachment|{ATTACH_DX_ID}|direct_connect_gateway/{ACCOUNT}/global/{DXGW_TGW}"
)
ENDPOINT_PUBLIC_EDGE = f"endpoint_connection|{ENDPOINT_ID}|{PUBLIC_SUBNET_ID}"
ENDPOINT_PRIVATE_EDGE = f"endpoint_connection|{ENDPOINT_ID}|{PRIVATE_SUBNET_ID}"
ALB_PUBLIC_EDGE = f"load_balancer_placement|{ALB_ID}|{PUBLIC_SUBNET_ID}"
ALB_PRIVATE_EDGE = f"load_balancer_placement|{ALB_ID}|{PRIVATE_SUBNET_ID}"
NLB_EGRESS_EDGE = f"load_balancer_placement|{NLB_ID}|subnet/{ACCOUNT}/{REGION}/subnet-0ddd4444eeee5555"

# ─────────────────────────────────────────────────────────────────────────────
# 기대표 — 🔴 **이것이 정본의 사본이 아니라 «기대값»이다.** 코드와 비교하고(아래 첫 검사) 그
# 다음 ADR 0008 표에 실제로 적혀 있는지 또 비교한다(둘째 검사). 한 곳만 고치면 FAIL 한다.
# (프로브 키, namespace, 차원 이름 순서, 흐름 지표, 차단 지표)
# ─────────────────────────────────────────────────────────────────────────────
EXPECTED_PROBES: dict[
    str, tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]
] = {
    "nat_gateway": (
        "AWS/NATGateway",
        ("NatGatewayId",),
        ("BytesOutToDestination", "BytesInFromSource"),
        ("PacketsDropCount", "ErrorPortAllocation"),
    ),
    "transit_gateway": (
        "AWS/TransitGateway",
        ("TransitGateway",),
        ("BytesIn", "BytesOut"),
        ("PacketDropCountBlackhole", "PacketDropCountNoRoute"),
    ),
    "transit_gateway_attachment": (
        "AWS/TransitGateway",
        ("TransitGateway", "TransitGatewayAttachment"),
        ("BytesIn", "BytesOut"),
        ("PacketDropCountBlackhole", "PacketDropCountNoRoute"),
    ),
    "vpc_endpoint": (
        "AWS/PrivateLinkEndpoints",
        ("VPC Id", "VPC Endpoint Id", "Endpoint Type", "Service Name"),
        ("BytesProcessed", "NewConnections"),
        ("PacketsDropped",),
    ),
    "load_balancer:application": (
        "AWS/ApplicationELB",
        ("LoadBalancer",),
        ("ProcessedBytes", "RequestCount"),
        (),
    ),
    "load_balancer:network": (
        "AWS/NetworkELB",
        ("LoadBalancer",),
        ("ProcessedBytes", "NewFlowCount", "ActiveFlowCount"),
        ("RejectedFlowCount",),
    ),
    "attachment": (
        "AWS/TransitGateway",
        ("TransitGateway", "TransitGatewayAttachment"),
        ("BytesIn", "BytesOut"),
        ("PacketDropCountBlackhole", "PacketDropCountNoRoute"),
    ),
    "endpoint_connection": (
        "AWS/PrivateLinkEndpoints",
        ("VPC Id", "VPC Endpoint Id", "Endpoint Type", "Service Name", "Subnet Id"),
        ("BytesProcessed", "NewConnections"),
        ("PacketsDropped",),
    ),
    "load_balancer_placement:application": (
        "AWS/ApplicationELB",
        ("LoadBalancer", "AvailabilityZone"),
        ("ProcessedBytes", "RequestCount"),
        (),
    ),
    "load_balancer_placement:network": (
        "AWS/NetworkELB",
        ("LoadBalancer", "AvailabilityZone"),
        ("ProcessedBytes", "NewFlowCount"),
        ("RejectedFlowCount",),
    ),
}

# 🔴 **ALB 계열엔 차단 지표가 없다** → `down` 을 만들 수 없다. 표가 아니라 사실이고(D3 의 ALB 행은
# `HTTPCode_ELB_5XX_Count`·`HealthyHostCount` 인데 둘 다 타깃그룹 차원이 있어야 뜻이 있다 = D10),
# 아래 `test_an_alb_placement_can_never_say_down` 이 그 사실을 화면 언어로 고정한다.
NO_BLOCK_PROBES = {"load_balancer:application", "load_balancer_placement:application"}

# 🔴 **드롭을 읽는 것과 드롭으로 「막혔다」를 적는 것은 다르다.** 여기 적힌 것만 판정을 `down` 으로
# 만들 수 있고(ADR 0008 D3 차단 신호표), 나머지 드롭은 화면에 「드롭 N」 배지로만 쓰인다. NAT 이
# 유일한 차이다 — 갓 만든 NAT 이 생성 직후 3분에 49패킷을 드롭하므로(랩 A1 실측 · 정정 5)
# `PacketsDropCount` 로 막혔다고 적으면 **정상인 새 NAT 이 빨갛게 된다**.
EXPECTED_BLOCKING_DROPS: dict[str, tuple[str, ...]] = {
    "nat_gateway": ("ErrorPortAllocation",),
    "transit_gateway": ("PacketDropCountBlackhole", "PacketDropCountNoRoute"),
    "transit_gateway_attachment": (
        "PacketDropCountBlackhole",
        "PacketDropCountNoRoute",
    ),
    "attachment": ("PacketDropCountBlackhole", "PacketDropCountNoRoute"),
    "vpc_endpoint": ("PacketsDropped",),
    "endpoint_connection": ("PacketsDropped",),
    "load_balancer:application": (),
    "load_balancer:network": ("RejectedFlowCount",),
    "load_balancer_placement:application": (),
    "load_balancer_placement:network": ("RejectedFlowCount",),
}

# 🔴 **「들어왔다」만 세는 통과량**(ADR 0008 정정 1-2). 여기 적힌 지표는 혼자서 흐름을 증명하지
# 못한다 — TGW 의 `BytesIn` 은 블랙홀로 전량 버려져도 420 이었다(랩 B3 · 정정 1). 이 표에서 빠지면
# 블랙홀 구간이 초록이고, 표에 없는 것이 붙으면 잘 도는 구간이 「판정 못 함」으로 떨어진다.
EXPECTED_ARRIVAL_ONLY: dict[str, tuple[str, ...]] = {
    "transit_gateway": ("BytesIn",),
    "transit_gateway_attachment": ("BytesIn",),
    "attachment": ("BytesIn",),
}

ALB_DIMENSION = "app/app-alb/50dc6c495c0c9188"
NLB_DIMENSION = "net/old-nlb/60dc6c495c0c9199"
ENDPOINT_DIMENSIONS = {
    "VPC Id": APP_VPC,
    "VPC Endpoint Id": ENDPOINT,
    # 🔴 값은 리터럴 `Interface` 다(§O7-1) — 소문자로 바꿔 보내면 조합이 없다.
    "Endpoint Type": "Interface",
    "Service Name": f"com.amazonaws.{REGION}.ssm",
}


def _attachment_dimensions(attachment: str) -> dict[str, str]:
    return {"TransitGateway": CORE_TGW, "TransitGatewayAttachment": attachment}


# 🔴 대상 → (프로브 키, 차원 값). **차원 값은 1층 응답에서** 만든 것이어야 한다(§O7-1 마지막 열).
# 여기 적힌 것이 곧 `plan()` 의 기대값이고, `FakeCloudWatch` 의 계열 키도 여기서 만든다 — 그래서
# 코드의 차원 상수가 한 글자라도 드리프트하면 계열이 안 맞아 판정 검사가 **같이** 빨개진다.
TARGETS: dict[str, tuple[str, dict[str, str]]] = {
    ALB_ID: ("load_balancer:application", {"LoadBalancer": ALB_DIMENSION}),
    NLB_ID: ("load_balancer:network", {"LoadBalancer": NLB_DIMENSION}),
    NAT_ID: ("nat_gateway", {"NatGatewayId": EGRESS_NAT}),
    DEAD_NAT_ID: ("nat_gateway", {"NatGatewayId": "nat-0fff6666aaaa7777"}),
    TGW_ID: ("transit_gateway", {"TransitGateway": CORE_TGW}),
    ATTACH_ID: ("transit_gateway_attachment", _attachment_dimensions(ATTACH)),
    ATTACH2_ID: ("transit_gateway_attachment", _attachment_dimensions(ATTACH2)),
    ATTACH3_ID: ("transit_gateway_attachment", _attachment_dimensions(ATTACH3)),
    ATTACH_DX_ID: ("transit_gateway_attachment", _attachment_dimensions(ATTACH_DX)),
    ENDPOINT_ID: ("vpc_endpoint", dict(ENDPOINT_DIMENSIONS)),
    ATTACH_TGW_EDGE: ("attachment", _attachment_dimensions(ATTACH)),
    ATTACH_VPC_EDGE: ("attachment", _attachment_dimensions(ATTACH)),
    ATTACH2_TGW_EDGE: ("attachment", _attachment_dimensions(ATTACH2)),
    ATTACH2_VPN_EDGE: ("attachment", _attachment_dimensions(ATTACH2)),
    ATTACH_DX_TGW_EDGE: ("attachment", _attachment_dimensions(ATTACH_DX)),
    ATTACH_DX_DXGW_EDGE: ("attachment", _attachment_dimensions(ATTACH_DX)),
    ENDPOINT_PUBLIC_EDGE: (
        "endpoint_connection",
        {**ENDPOINT_DIMENSIONS, "Subnet Id": PUBLIC_SUBNET},
    ),
    ENDPOINT_PRIVATE_EDGE: (
        "endpoint_connection",
        {**ENDPOINT_DIMENSIONS, "Subnet Id": PRIVATE_SUBNET},
    ),
    ALB_PUBLIC_EDGE: (
        "load_balancer_placement:application",
        {"LoadBalancer": ALB_DIMENSION, "AvailabilityZone": "us-east-1a"},
    ),
    ALB_PRIVATE_EDGE: (
        "load_balancer_placement:application",
        {"LoadBalancer": ALB_DIMENSION, "AvailabilityZone": "us-east-1b"},
    ),
}

# `failed` NLB 은 배치 선을 만들지 않는다(ADR 0002 §3) → 그 선을 재려면 상태를 바꿔야 한다.
NLB_TARGETS: dict[str, tuple[str, dict[str, str]]] = {
    NLB_EGRESS_EDGE: (
        "load_balancer_placement:network",
        {"LoadBalancer": NLB_DIMENSION, "AvailabilityZone": "us-east-1a"},
    ),
}

FLOW_VALUES = [4096.0, 0.0, 2048.0]  # 창 합계 6144 · 0 이 아닌 지점 2개

# 값이 있는 대상(= 흐른다). 🔴 「전부 0 dp 는 결함」 규칙이 있으므로 기본 대조군은 비어 있지 않다.
FLOWING = (NAT_ID, TGW_ID, ATTACH_ID, ENDPOINT_ID, ALB_ID)


def _probe_of(target_id: str) -> str:
    table = {**TARGETS, **NLB_TARGETS}
    return table[target_id][0]


def _flow_metric(target_id: str) -> str:
    """그 대상에서 **혼자 흐름을 증명하는** 첫 통과량 — 「들어옴만 센다」 지표는 건너뛴다."""
    probe = _probe_of(target_id)
    arrival = EXPECTED_ARRIVAL_ONLY.get(probe, ())
    return next(m for m in EXPECTED_PROBES[probe][2] if m not in arrival)


def _drop_metric(target_id: str) -> str:
    return EXPECTED_PROBES[_probe_of(target_id)][3][0]


def _key(target_id: str, metric: str) -> tuple[Any, ...]:
    probe, dimensions = {**TARGETS, **NLB_TARGETS}[target_id]
    return series_key(EXPECTED_PROBES[probe][0], metric, dimensions)


def _series(
    *entries: tuple[str, str, list[float]], flowing: tuple[str, ...] = FLOWING
) -> dict:
    """대상 id → 지표 계열. 등록하지 않은 대상·지표는 **빈 배열**이 온다(= 확인 불가).

    `flowing` 은 「그 대상의 첫 흐름 지표에 `FLOW_VALUES`」의 줄임이고, `entries` 는
    `(대상, 지표, 값)` 을 직접 적는다(드롭·게이지·0 계열을 만들 때 쓴다).
    """
    out: dict[Any, list[float]] = {}
    for target_id in flowing:
        out[_key(target_id, _flow_metric(target_id))] = list(FLOW_VALUES)
    for target_id, metric, values in entries:
        out[_key(target_id, metric)] = list(values)
    return out


def _snapshot(resource_types: tuple[str, ...], at: str, **overrides: Any) -> Any:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=resource_types
    )
    # 🔴 상태 단(observe CLI)은 상태 탐침을 켜고 돈다(`cli.py` 의 `with_state=True`) — 구조 단은 끈다.
    # 끈 채로 관측하면 RDS·ECS 등이 「규칙에 없는 값 None」(개발)이 된다(W3 실측).
    return collect(
        scope,
        factory(FakeAws(**overrides)),
        clock=lambda: at,
        with_state=at != STRUCTURE_AT,
    )


def _layer(
    cw: FakeCloudWatch, state: Any, *, at: str = OBSERVED_AT, **kwargs: Any
) -> MetricLayer:
    return read_metrics(state, factory_by_service({"cloudwatch": cw}, **kwargs), at=at)


def _observe(
    cw: FakeCloudWatch | None = None,
    *,
    fail: dict[str, Exception] | None = None,
    at: str = OBSERVED_AT,
    **overrides: Any,
) -> tuple[dict[str, Any], MetricLayer]:
    """생산과 같은 순서: 구조 12종 → 상태 10종 → 2층 → 판정."""
    topology = _snapshot(
        SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, **overrides
    ).to_document()
    state = _snapshot(OBSERVED_RESOURCE_TYPES, at, **overrides)
    layer = (
        MetricLayer.off()
        if cw is None
        else _layer(cw, state, at=at, **({"fail_services": fail} if fail else {}))
    )
    document, _dropped = build_observation(
        state,
        topology=topology,
        observed_at=at,
        tool_version="test",
        metrics=layer,
    )
    return document, layer


def _active_nlb() -> list[dict[str, Any]]:
    """상태만 `active` 로 바꾼 LB 목록 — NLB 배치 선을 존재하게 만든다."""
    balancers = copy.deepcopy(LOAD_BALANCERS)
    balancers[1]["State"] = {"Code": "active"}
    return balancers


def _by_id(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["target_id"]: o for o in document["observations"]}


def _metric_rows(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        row["resource_type"]: row
        for row in document["coverage"]
        if row["layer"] == "metric"
    }


def _valid(document: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(document)


# ---------------------------------------------------------------- 정본 ↔ 기대값 ↔ 코드


def _adr8_table(heading: str) -> dict[str, list[list[str]]]:
    """ADR 0008 의 한 절에서 **첫 표**를 읽는다 → {첫 칸 라벨: [열별 백틱 이름 목록]}.

    🔴 파싱이 깨지면 아래 검사들이 **빈 집합끼리 비교해 통과한다**(통과가 아니라 미측정). 그래서
    `test_adr8_tables_are_parseable` 이 행 수와 열 내용을 따로 잰다.
    """
    section = _adr8().split(heading, 1)[-1].split("\n### ", 1)[0]
    rows: dict[str, list[list[str]]] = {}
    started = False
    for line in section.splitlines():
        if not line.startswith("|"):
            if started:
                break  # 절에 표가 둘 이상이면 첫 표에서 멈춘다
            continue
        started = True
        if line.startswith("|-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        label = re.sub(r"[*~]", "", cells[0]).strip()
        if label in ("자원", "등급", "무엇"):
            continue
        rows[label] = [re.findall(r"`([^`]+)`", cell) for cell in cells[1:]]
    return rows


# 프로브 → 그 프로브를 뒷받침하는 ADR 0008 표의 행 라벨. 🔴 어태치먼트 행은 지표를 「같은 지표」
# 라고만 적으므로 TGW 행과 **함께** 본다(그것이 그 칸의 뜻이다).
D2_BACKING: dict[str, tuple[str, ...]] = {
    "nat_gateway": ("NAT GW",),
    "transit_gateway": ("TGW",),
    "transit_gateway_attachment": ("TGW", "TGW 어태치먼트 = 구간"),
    "attachment": ("TGW", "TGW 어태치먼트 = 구간"),
    "vpc_endpoint": ("인터페이스 엔드포인트 = 구간",),
    "endpoint_connection": ("인터페이스 엔드포인트 = 구간",),
    "load_balancer:application": ("ALB",),
    "load_balancer_placement:application": ("ALB",),
    "load_balancer:network": ("NLB", "NLB(보조)"),
    "load_balancer_placement:network": ("NLB", "NLB(보조)"),
}

D3_BACKING: dict[str, tuple[str, ...]] = {
    "nat_gateway": ("NAT GW",),
    "transit_gateway": ("TGW",),
    "transit_gateway_attachment": ("TGW",),
    "attachment": ("TGW",),
    "vpc_endpoint": ("엔드포인트",),
    "endpoint_connection": ("엔드포인트",),
    "load_balancer:network": ("NLB",),
    "load_balancer_placement:network": ("NLB",),
}


def test_adr8_tables_are_parseable():
    d2, d3 = _adr8_table("### D2."), _adr8_table("### D3.")
    assert len(d2) == 18, f"D2 표에서 읽은 행 {len(d2)}개: {sorted(d2)}"
    assert len(d3) == 9, f"D3 표에서 읽은 행 {len(d3)}개: {sorted(d3)}"
    # 라벨을 못 읽으면 아래 「뒷받침」 검사가 KeyError 도 못 내고 조용해진다.
    assert set(D2_BACKING) == set(EXPECTED_PROBES)
    for labels in D2_BACKING.values():
        for label in labels:
            assert label in d2, f"D2 표에 「{label}」 행이 없다 — 라벨이 바뀌었다"
    for labels in D3_BACKING.values():
        for label in labels:
            assert label in d3, f"D3 표에 「{label}」 행이 없다"
    assert d2["TGW"][0] == ["AWS/TransitGateway", "BytesIn", "BytesOut"]
    assert "TransitGatewayAttachment" in d2["TGW 어태치먼트 = 구간"][1]


def test_probe_constants_are_exactly_the_expected_table():
    """🔴 코드 ↔ 기대표. 차원 이름·순서·지표 이름·역할이 하나라도 다르면 FAIL.

    사람이 보는 결과로 말하면: 차원 이름이 한 글자 틀린 구간은 **영원히 회색**이고 아무 에러도
    나지 않는다(실측 §O7-1 함정 1). 그래서 화면으로는 절대 발견되지 않는다.
    """
    probes = {**NODE_PROBES, **EDGE_PROBES}
    assert set(probes) == set(EXPECTED_PROBES)
    for key, probe in probes.items():
        namespace, dimensions, flows, drops = EXPECTED_PROBES[key]
        assert probe.key == key
        assert probe.namespace == namespace, key
        assert probe.dimensions == dimensions, key
        by_role = {role: [] for role in (ROLE_FLOW, ROLE_DROP, ROLE_STATE, ROLE_HEALTH)}
        for ref in probe.metrics:
            by_role[ref.role].append(ref.name)
        assert tuple(by_role[ROLE_FLOW]) == flows, key
        assert tuple(by_role[ROLE_DROP]) == drops, key
        blocking = tuple(
            ref.name for ref in probe.metrics if ref.role == ROLE_DROP and ref.blocking
        )
        assert blocking == EXPECTED_BLOCKING_DROPS[key], key
        arrival = tuple(
            ref.name
            for ref in probe.metrics
            if ref.role == ROLE_FLOW and ref.arrival_only
        )
        assert arrival == EXPECTED_ARRIVAL_ONLY.get(key, ()), key
        # 「들어옴만 센다」가 붙은 프로브는 **차단 드롭을 가져야 한다** — 없으면 「재서 0」을 증명할
        # 길이 없어 그 대상은 영원히 「판정 못 함」이다.
        if arrival:
            assert blocking, f"{key}: 들어옴만 세는 지표가 있는데 차단 드롭이 없다"
        # 🔴 `down` 을 만들 수 없는 프로브는 「드롭을 안 읽는 것」이 아니라 **「차단 신호로 쓰는
        # 드롭이 없는 것」**이다. `not drops` 로 재면 NAT 처럼 배지 전용 드롭만 읽는 프로브가
        # 생겼을 때 조용히 통과한다.
        assert (key in NO_BLOCK_PROBES) == (not blocking), key
        # 구간 프로브는 차원이 2개 이상이어야 한다 — 그것이 ADR 0008 D8-1 의 자격 조건이다.
        if key in EDGE_PROBES:
            assert len(probe.dimensions) >= 2, (
                f"{key}: 구간을 특정하지 못하는 차원 조합"
            )


def test_reporting_is_pinned_per_namespace_and_metric():
    """🔴 발행 규칙(`reporting`)을 눈으로만 관리하면 조용히 뒤집힌다.

    사람이 보는 결과로 말하면: 이 값 하나가 「이 구간은 이 15분 동안 트래픽이 없었습니다」와
    「이 구간은 읽지 못했습니다 — 지표 발행 설정을 확인하세요」를 가른다. 지표 이름이 같아도
    네임스페이스마다 다르다는 것이 실측이다(2026-09-25: NLB `ProcessedBytes` 는 0 을 발행하고
    ALB `ProcessedBytes` 는 발행하지 않는다) → 그래서 기대표의 키가 (네임스페이스, 지표)다.

    `always` 로 적을 자격은 **문서 인용 또는 실계정 실측**뿐이다(근거는 `metrics.py` 의
    「`reporting=` 의 근거」 주석). 근거 없이 올리면 조용한 창이 전부 「흐름 없음」으로 확정된다.
    """
    expected = {
        # 문서: "All metrics are always reported."
        ("AWS/TransitGateway", "BytesIn"): REPORT_ALWAYS,
        ("AWS/TransitGateway", "BytesOut"): REPORT_ALWAYS,
        ("AWS/TransitGateway", "PacketDropCountBlackhole"): REPORT_ALWAYS,
        ("AWS/TransitGateway", "PacketDropCountNoRoute"): REPORT_ALWAYS,
        # 문서: "The endpoint received traffic during the one-minute period."
        ("AWS/PrivateLinkEndpoints", "BytesProcessed"): REPORT_NONZERO_ONLY,
        ("AWS/PrivateLinkEndpoints", "NewConnections"): REPORT_NONZERO_ONLY,
        ("AWS/PrivateLinkEndpoints", "PacketsDropped"): REPORT_NONZERO_ONLY,
        # 문서 + 실측(180분 중 9개만 존재 · 0 값 0개)
        ("AWS/ApplicationELB", "ProcessedBytes"): REPORT_NONZERO_ONLY,
        # 문서: "Reported if there are registered targets." + 실측(0개)
        ("AWS/ApplicationELB", "RequestCount"): REPORT_CONDITIONAL,
        # 실측(180분 · 0 값 157/179·86/179·179/179 · AZ 차원도 전부 0) — 문서 머리말이 틀렸다
        ("AWS/NetworkELB", "ProcessedBytes"): REPORT_ALWAYS,
        ("AWS/NetworkELB", "NewFlowCount"): REPORT_ALWAYS,
        ("AWS/NetworkELB", "ActiveFlowCount"): REPORT_ALWAYS,
        ("AWS/NetworkELB", "RejectedFlowCount"): REPORT_ALWAYS,
        # 실측(24시간 1,440개 전부 0)
        ("AWS/NATGateway", "PacketsDropCount"): REPORT_ALWAYS,
        # 랩 A1 실측 승격 2026-09-26 — 트래픽 0 NAT 을 73분 재서 **72점 전부 0**
        # (`docs/verification-lab.md:141,148`). 이전 값 `unknown` 은 「24시간 내내 트래픽이 있어
        # 0 이 될 기회가 없었다」는 미측정이었고, 랩이 그 기회를 만들어 닫았다.
        ("AWS/NATGateway", "BytesInFromSource"): REPORT_ALWAYS,
        ("AWS/NATGateway", "BytesOutToDestination"): REPORT_ALWAYS,
        # 🔴 미측정 — 같은 73분에 36~37점만 왔고 간격이 불규칙했다(포트 고갈이 없으면 계열이
        # 띄엄띄엄이다) → 「없음」을 「0」으로 읽으면 안 된다
        ("AWS/NATGateway", "ErrorPortAllocation"): REPORT_UNKNOWN,
    }
    seen = set()
    # 🔴 dict 로 접지 않고 **프로브마다** 잰다 — 같은 (네임스페이스, 지표)를 노드 프로브와 배치
    # 프로브가 서로 다르게 적어도 dict 비교는 마지막 값만 보고 통과한다.
    for probe in (*NODE_PROBES.values(), *EDGE_PROBES.values()):
        for ref in probe.metrics:
            key = (probe.namespace, ref.name)
            assert key in expected, (
                f"{probe.key}: 발행 규칙 기대표에 「{ref.name}」이 없다"
            )
            assert ref.reporting == expected[key], f"{probe.key} · {ref.name}"
            seen.add(key)
    assert seen == set(expected), "기대표에만 있는 항목 — 지표가 사라졌는데 표가 남았다"


def test_expected_probes_are_backed_by_the_adr():
    """🔴 기대표 ↔ 정본. 기대표의 모든 지표·차원 이름이 ADR 0008 표에 적혀 있어야 한다.

    위 검사만 있으면 둘이 **같이** 틀릴 수 있다(기대표를 코드에 맞춰 고치면 통과한다). 정본은
    ADR 이고, 여기가 그 방향을 잡는다.
    """
    d2, d3 = _adr8_table("### D2."), _adr8_table("### D3.")
    for key, (namespace, dimensions, flows, drops) in EXPECTED_PROBES.items():
        d2_names = {n for label in D2_BACKING[key] for cell in d2[label] for n in cell}
        assert namespace in d2_names, f"{key}: D2 표에 namespace 가 없다"
        for name in (*dimensions, *flows):
            assert name in d2_names, f"{key}: D2 표에 「{name}」이 없다"
        if not drops:
            continue
        d3_names = {n for label in D3_BACKING[key] for cell in d3[label] for n in cell}
        for name in drops:
            assert name in d3_names, f"{key}: D3 차단 신호표에 「{name}」이 없다"


def test_every_observed_type_is_measured_or_declared_metricless():
    """🔴 「아직 안 만들었다」를 「지표가 없다」로 조용히 섞지 않는다 — 합집합이 10종을 덮는다.

    이 검사가 실측으로 잡은 것(2026-09-25): 프로브를 노드/엣지로 쪼개면서 **어태치먼트 노드의
    2층 프로브가 사라졌다**. 화면에서는 어태치먼트 박스만 영원히 회색이고 그 양쪽 선은 초록이
    되는데, 아무 에러도 나지 않아 눈으로는 「트래픽이 없구나」로 읽힌다.
    """
    measured = {key.split(":", 1)[0] for key in NODE_PROBES}
    assert measured | set(NO_METRIC_TYPES) | set(METRIC_PENDING_TYPES) == set(
        OBSERVED_RESOURCE_TYPES
    )
    assert not (measured & NO_METRIC_TYPES)
    assert not (measured & METRIC_PENDING_TYPES)
    assert not (NO_METRIC_TYPES & METRIC_PENDING_TYPES)


def test_o7_node_table_says_which_types_have_no_metric():
    """ADR 0006 §O7 노드 표의 2층 열이 「없음」인 종류 == `NO_METRIC_TYPES`.

    표에 지표를 적고 코드가 안 읽으면(또는 반대로) 화면에서 구별되지 않는다 — 둘 다 「흐름을
    모른다」로 보인다.
    """
    section = _adr6().split("**노드** (1차 12종", 1)[-1].split("**엣지**", 1)[0]
    silent: set[str] = set()
    measured: set[str] = set()
    pending: set[str] = set()
    seen = 0
    for line in section.splitlines():
        if not line.startswith("|") or line.startswith("|-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        names = re.findall(r"`([^`]+)`", cells[0])
        if len(names) != 1 or names[0] not in OBSERVED_RESOURCE_TYPES:
            continue
        seen += 1
        # 🔵 로 시작하는 2층 칸 = 지표는 적었지만 프로브가 아직 없다(`METRIC_PENDING_TYPES`).
        if cells[2].startswith("없음"):
            silent.add(names[0])
        elif cells[2].startswith("🔵"):
            pending.add(names[0])
        else:
            measured.add(names[0])
    assert seen == len(OBSERVED_RESOURCE_TYPES), f"표에서 읽은 관측 대상 행 {seen}개"
    assert silent == set(NO_METRIC_TYPES)
    assert pending == set(METRIC_PENDING_TYPES)
    assert measured == {key.split(":", 1)[0] for key in NODE_PROBES}


def test_metric_only_edge_types_match_the_edge_probes():
    """§O7 엣지 표가 「2층 전용」이라고 적은 종류 == 엣지 프로브를 가진 종류.

    어긋나면 두 가지로 조용히 틀린다: 프로브는 있는데 「2층 전용」이 아니면 1층 근거를 요구받아
    행이 사라지고, 반대면 **행 자체가 만들어지지 않는다**(화면에 그 선의 상태가 없다).
    """
    assert {key.split(":", 1)[0] for key in EDGE_PROBES} == set(METRIC_ONLY_EDGES)


def test_no_probe_uses_the_state_or_health_roles_yet():
    """🔴 래칫 — 상태·건강 역할은 지금 **어느 프로브도 쓰지 않는다**(VPN·DX 는 D10 의 몫).

    그래서 그 판정 경로는 `_fold` 단위 검사로만 재고 있다. 이 줄이 FAIL 하는 날이 **종단**
    검사를 추가할 날이다 — 역할만 붙이고 종단을 안 재면, 상태가 0 인데 화면이 초록인 경우를
    아무 검사도 잡지 못한다.
    """
    roles = {
        ref.role
        for probe in {**NODE_PROBES, **EDGE_PROBES}.values()
        for ref in probe.metrics
    }
    assert roles == {ROLE_FLOW, ROLE_DROP}


# ---------------------------------------------------------------- 쿼리 계획


def test_plan_builds_every_target_and_dimension_from_layer_one():
    """차원 값이 1층 응답에서 나오는지 — 그리고 **엣지도 계획된다**(ADR 0008 D8 의 본체).

    🔴 옛 판은 노드만 순회했고, 그래서 실계정 445행 중 2층 근거를 가진 엣지 행이 **0** 이었다.
    선이 2층을 받을 통로 자체가 없었던 것이고, 그 빈자리를 화면이 종단 추론으로 메웠다.
    """
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    queries, unbuildable = plan(state)
    assert unbuildable == []
    built = {
        q.target_id: (
            q.probe.key,
            dict(zip(q.probe.dimensions, q.values, strict=True)),
        )
        for q in queries
    }
    assert built == TARGETS
    assert "arn:" not in built[ALB_ID][1]["LoadBalancer"]
    # 정렬은 코드가 한다(불변식 ②): 노드 묶음 → 엣지 묶음, 각각 id 순.
    kinds = [q.target_kind for q in queries]
    assert kinds == ["node"] * 10 + ["edge"] * 10, kinds
    for kind in ("node", "edge"):
        ids = [q.target_id for q in queries if q.target_kind == kind]
        assert ids == sorted(ids), kind


def test_the_vpc_side_of_an_attachment_is_marked_as_deduced():
    """🔴 같은 값을 두 선에 쓰면서 **한쪽은 「연역」이라고 적는다**(ADR 0008 D1 의 B · D8-2).

    지표의 차원이 이름 대는 것은 「TGW ↔ 어태치먼트」다. 「어태치먼트 ↔ VPC」는 그 바이트가 VPC
    경계를 넘었다는 **연역**이므로, 화면이 배지로 그렇게 말할 수 있게 데이터에 적어야 한다
    (불변식 ③). 아무 표시 없이 복사하는 것이 폐기된 종단 추론이 한 일이다.
    """
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    queries, _ = plan(state)
    deduced = {q.target_id: q.observed_on for q in queries if q.observed_on is not None}
    assert deduced == {ATTACH_VPC_EDGE: ATTACH_ID}


def test_identical_series_are_bought_once():
    """🔴 요금은 「요청한 지표 수」로 매겨진다 — 같은 계열을 세 번 보내면 세 번 낸다.

    어태치먼트 하나의 계열은 **세 대상**의 답이다(어태치먼트 박스 · TGW 쪽 선 · VPC 쪽 선).
    접지 않으면 어태치먼트당 4개가 아니라 12개를 사고, 그래도 화면은 똑같아서 아무도 모른다.
    🔴 접는 것은 요청이고 판정은 대상별로 남는다 — 세 대상이 **전부** 답을 받아야 한다.
    """
    cw = FakeCloudWatch(_series())
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    layer = _layer(cw, state)
    assert len(cw.calls) == 1
    sent = [
        (
            q["MetricStat"]["Metric"]["Namespace"],
            q["MetricStat"]["Metric"]["MetricName"],
            tuple(
                sorted(
                    (d["Name"], d["Value"])
                    for d in q["MetricStat"]["Metric"]["Dimensions"]
                )
            ),
        )
        for q in cw.calls[0]["MetricDataQueries"]
    ]
    assert len(sent) == len(set(sent)), "같은 계열을 두 번 샀다"
    attachment = [s for s in sent if ("TransitGatewayAttachment", ATTACH) in s[2]]
    assert len(attachment) == 4, (
        attachment
    )  # BytesIn·BytesOut·드롭 2 — 3대상 × 4 가 아니다
    for target_id in (ATTACH_ID, ATTACH_TGW_EDGE, ATTACH_VPC_EDGE):
        # 같은 계열 하나를 세 대상이 **나눠 받는다** — 기본 대조군이 값을 준 통과량이 셋 모두에 있다.
        assert layer.readings_for(target_id)[_flow_metric(ATTACH_ID)].measured, (
            target_id
        )


def test_types_without_metrics_are_not_queried():
    """지표가 없는 종류와 **Gateway 형** 엔드포인트는 아예 묻지 않는다(§O7: Interface 만)."""
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    queries, _ = plan(state)
    asked = {q.resource_type for q in queries}
    assert not (asked & NO_METRIC_TYPES)
    assert GATEWAY_ENDPOINT_ID not in {q.target_id for q in queries}


def test_a_missing_dimension_value_is_reported_not_guessed():
    """🔴 차원을 만들 수 없으면 **빈 값으로 부르지 않는다** — 그러면 「흐름 없음」과 같아진다.

    1층이 `TransitGatewayId` 를 안 준 어태치먼트는 노드와 VPC 쪽 선이 **못 물어보는 대상**이 된다
    (TGW 쪽 선은 그 id 로 그리는 선이라 구조에 아예 없다 — `collect` 가 만들지 못한다). 조용히
    건너뛰면 그 둘이 「트래픽 없음」으로 보이고, 고칠 사람은 개발자인데 화면은 아무 말도 하지 않는다.
    """
    attachments = copy.deepcopy(TRANSIT_GATEWAY_ATTACHMENTS)
    del attachments[0]["TransitGatewayId"]
    state = _snapshot(
        OBSERVED_RESOURCE_TYPES, OBSERVED_AT, transit_gateway_attachments=attachments
    )
    queries, unbuildable = plan(state)
    assert {t for t, _why in unbuildable} == {ATTACH_ID, ATTACH_VPC_EDGE}
    assert ATTACH_TGW_EDGE not in {e.id for e in state.edges}
    assert all("TransitGateway" in why for _t, why in unbuildable)
    assert not ({t for t, _ in unbuildable} & {q.target_id for q in queries})

    document, layer = _observe(
        FakeCloudWatch(_series()), transit_gateway_attachments=attachments
    )
    assert layer.unbuildable == dict(unbuildable)
    stuck = _by_id(document)[ATTACH_ID]
    # 1층 근거는 온전하므로 `up` 이고, 2층이 없다는 사실은 사유로 남는다.
    assert stuck["status"] == "up"
    assert stuck["partial"] is True
    assert [r["code"] for r in stuck["partial_reasons"]] == ["api_error"]
    _valid(document)


def test_request_pins_the_period_and_the_window():
    """`period=60` 고정 · 창 **900초**(ADR 0008 D6) · **사다리 없음**.

    🔴 창의 끝은 벽시계가 아니라 **스냅샷 시각**이다(불변식 ②) — 벽시계를 읽으면 같은 입력이
    다른 문서를 만든다. 사다리가 생기면 호출이 2회가 되므로 호출 수도 같이 잰다.
    """
    cw = FakeCloudWatch(_series())
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    _layer(cw, state)
    assert len(cw.calls) == 1, "재시도·사다리가 생겼다(period 를 올려 다시 부르지 마라)"
    call = cw.calls[0]
    assert call["EndTime"].isoformat() == "2026-09-23T00:05:00+00:00"
    assert (call["EndTime"] - call["StartTime"]).total_seconds() == WINDOW_SECONDS
    assert WINDOW_SECONDS == 900, "D6 이 승인한 창은 15분이다"
    assert {q["MetricStat"]["Period"] for q in call["MetricDataQueries"]} == {
        PERIOD_SECONDS
    }
    assert PERIOD_SECONDS == 60
    # 🔴 NLB 의 `ActiveFlowCount` 는 게이지다 — `Sum` 이면 표본 수만큼 부풀어 오른다.
    stats = {
        q["MetricStat"]["Metric"]["MetricName"]: q["MetricStat"]["Stat"]
        for q in call["MetricDataQueries"]
    }
    assert stats["ActiveFlowCount"] == "Average"
    assert stats["RequestCount"] == "Sum"
    assert stats["PacketDropCountBlackhole"] == "Sum"


# ---------------------------------------------------------------- 판정


def test_flowing_targets_become_live():
    document, _ = _observe(FakeCloudWatch(_series()))
    observations = _by_id(document)
    # 🔴 대상이 문서에 없으면 아래 for 가 **한 번도 돌지 않고 통과한다**(통과가 아니라 미측정).
    assert set(FLOWING) <= set(observations), set(FLOWING) - set(observations)
    for target_id in FLOWING:
        observed = observations[target_id]
        assert observed["status"] == "live", observed
        # `live` 의 근거는 지표다 — 스키마가 `metric` + `datapoints ≥ 1` 을 요구한다.
        assert observed["evidence"]["api"] == METRIC_API
        assert observed["evidence"]["metric"] == _flow_metric(target_id)
        assert observed["evidence"]["datapoints"] == 2  # FLOW_VALUES 중 0 이 아닌 수
        assert observed["evidence"]["window_seconds"] == WINDOW_SECONDS
        assert observed["partial"] is False
    _valid(document)
    assert document["layers"] == {"describe": True, "metric": True}


def test_the_window_is_folded_not_read_from_one_end():
    """🔴 판정은 **창 전체**를 접은 값이다(ADR 0008 D6) — 어느 한 칸이 아니다.

    옛 판은 「값이 있는 마지막 지점」(최신 우선 배열의 `[0]`)으로 판정했고, 그래서 1분 단위
    깜빡임이 그대로 선의 색이 됐다(15분 중 14분을 흘렸는데 마지막 1분이 0 이면 회색). 반대로
    배열을 뒤에서 읽으면 15분 전 값으로 지금을 말한다. **접으면 정렬 방향과 무관해진다.**
    """
    both_ends_zero = [(NAT_ID, "BytesOutToDestination", [0.0, 4096.0, 0.0])]
    document, _ = _observe(FakeCloudWatch(_series(*both_ends_zero)))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "live", "창 안에 통과량이 있는데 회색이 됐다"
    assert observed["evidence"]["datapoints"] == 1
    # 같은 계열을 뒤집어도 같은 판정이다 — 정렬에 기대지 않는다는 뜻이다.
    reversed_series = [(NAT_ID, "BytesOutToDestination", [0.0, 4096.0, 0.0][::-1])]
    flipped, _ = _observe(FakeCloudWatch(_series(*reversed_series)))
    assert _by_id(flipped)[NAT_ID] == observed


def test_live_evidence_names_the_first_and_last_flowing_minute():
    """`live` 의 근거에 **0 이 아닌 첫·마지막 분**이 실린다(ADR 0008 D6 보강 — 이력의 재료).

    FLOW_VALUES 는 00:05 기준 최신 우선 [4096, 0, 2048] = 00:04 · 00:03 · 00:02 다. 첫 = 00:02,
    마지막 = 00:04. 🔴 가운데 0 을 건너뛰어야 한다 — 「배열 끝」을 읽으면 둘 중 하나가 틀린다.
    """
    document, _ = _observe(FakeCloudWatch(_series()))
    observations = _by_id(document)
    assert set(FLOWING) <= set(observations), set(FLOWING) - set(observations)
    for target_id in FLOWING:
        evidence = observations[target_id]["evidence"]
        assert evidence["first_flow_at"] == "2026-09-23T00:02:00Z", target_id
        assert evidence["last_flow_at"] == "2026-09-23T00:04:00Z", target_id
    _valid(document)


def test_rows_that_are_not_live_carry_no_flow_minutes():
    """흐름이 없는 행에는 두 키가 **없다** — 모름·없음의 바이트 표현을 부재 하나로 둔다."""
    zeros = [(NAT_ID, "BytesOutToDestination", [0.0, 0.0, 0.0])]
    document, _ = _observe(FakeCloudWatch(_series(*zeros)))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up" and observed["evidence"]["datapoints"] == 0
    assert "first_flow_at" not in observed["evidence"]
    assert "last_flow_at" not in observed["evidence"]
    # 전제의 판별력 — 같은 문서에 흐르는 행은 키를 가진다.
    assert any(
        "last_flow_at" in (o["evidence"] or {}) for o in document["observations"]
    )


def test_flow_minutes_do_not_depend_on_the_scan_order():
    """🔴 최소·최대를 잰다 — `ScanBy` 가 뒤집혀도 같은 분이다(판정과 같은 규율)."""
    ref = MetricRef("BytesOutToDestination", "Sum", AGG_SUM, ROLE_FLOW, REPORT_ALWAYS)
    stamps = [f"2026-09-23T00:0{m}:00Z" for m in (4, 3, 2, 1)]
    values = [0.0, 7.0, 0.0, 5.0]
    descending = read_series(values, ref, stamps)
    ascending = read_series(values[::-1], ref, stamps[::-1])
    assert descending == ascending
    assert (descending.first_nonzero_at, descending.last_nonzero_at) == (
        "2026-09-23T00:01:00Z",
        "2026-09-23T00:03:00Z",
    )


@pytest.mark.parametrize(
    "stamps",
    [
        None,  # 응답에 `Timestamps` 가 없다
        ["2026-09-23T00:04:00Z"],  # 짝이 안 맞는다 — 어느 값이 어느 분인지 모른다
        [
            "2026-09-23T00:04:00",
            "2026-09-23T00:03:00",
        ],  # 시간대 없음 — UTC 로 가정하지 않는다
        ["not-a-time", "2026-09-23T00:03:00Z"],  # 읽을 수 없는 원소
    ],
)
def test_unusable_timestamps_leave_the_minutes_out_but_keep_the_verdict(stamps):
    """🔴 시각을 못 읽어도 **판정은 그대로**다 — 이 시각은 판정 재료가 아니다(불변식 ④)."""
    ref = MetricRef("BytesOutToDestination", "Sum", AGG_SUM, ROLE_FLOW, REPORT_ALWAYS)
    reading = read_series([4096.0, 2048.0], ref, stamps)
    assert (reading.first_nonzero_at, reading.last_nonzero_at) == (None, None)
    assert (reading.points, reading.nonzero, reading.value) == (2, 2, 6144.0)


def test_a_response_without_timestamps_is_still_live_and_valid():
    """실제 응답에서 시각이 빠져도 행은 `live` 이고 스키마를 통과한다(키만 없다)."""
    document, _ = _observe(FakeCloudWatch(_series(), with_timestamps=False))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "live"
    assert "first_flow_at" not in observed["evidence"]
    _valid(document)


def test_a_series_of_zeros_stays_up_and_says_it_measured():
    """쟀고 흐름이 없었다 → `up` + `datapoints: 0`. 🔴 `null`(안 쟀음)과 다른 사실이다."""
    document, _ = _observe(
        FakeCloudWatch(_series((NAT_ID, "BytesOutToDestination", [0.0, 0.0, 0.0])))
    )
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["evidence"]["datapoints"] == 0
    assert observed["evidence"]["metric"] == "BytesOutToDestination"
    assert observed["partial"] is False


def test_an_empty_series_does_not_overwrite_layer_one():
    """🔴 승인된 계약: 빈 배열은 「확인 불가」이고 1층 `up` 을 **덮지 않는다**.

    §O7-1 함정 3 의 그 경우다(구조에 있는데 지표가 없는 대상) — 「`up` 이고 `datapoints` 가
    `null` 인 것」이 정답이다. `partial` 도 아니다: 근거가 불완전한 것이 아니라 그 대상에 지표가
    없는 것이고, `partial` 로 적으면 화면이 「권한을 고치면 보인다」고 거짓말한다.

    🔴 **피험자가 엔드포인트에서 NAT 으로 바뀌었다(2026-10-21)**: 수락된 엔드포인트의 `BytesProcessed` 빈 응답은
    이제 「쟀고 0」이다(ADR 0006 §O7-2a 개정 · `test_a_silent_accepted_endpoint_is_up_with_no_flow`). 값 0 도 발행하는
    NAT(`always`)의 빈 응답이 이 계약의 피험자로 남는다 — 차원이 틀렸을 때 오는 바로 그 모양이다.
    """
    document, _ = _observe(FakeCloudWatch(_series(flowing=(TGW_ID,))))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    # 하나도 못 쟀으면 이름은 물어본 통과량의 첫 이름이다(`Verdict.report_metric` 의 마지막 갈래)
    assert observed["evidence"]["metric"] == "BytesInFromSource"
    assert observed["evidence"]["datapoints"] is None, "빈 배열을 0 으로 적었다"
    assert "값 0 도 발행하는 지표" in (observed["why"] or ""), observed
    assert observed["partial"] is False
    assert "partial_reasons" not in observed
    _valid(document)


def test_drops_do_not_deny_a_flow():
    """🔴 **드롭 > 0 은 흐름을 부정하지 않는다**(ADR 0008 D3).

    사람이 보는 결과: 정상적으로 흐르면서 일부 패킷을 버리는 구간(MTU·TTL·정책)이 전부 빨간
    점선이 된다. 그러면 진짜 차단과 구별되지 않고, 화면의 빨강이 신뢰를 잃는다.
    """
    document, _ = _observe(
        FakeCloudWatch(_series((NAT_ID, "PacketsDropCount", [12.0, 3.0])))
    )
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "live"
    assert observed["evidence"]["metric"] == "BytesOutToDestination"
    assert observed["why"] is None


def test_zero_flow_with_drops_is_down_and_names_the_drop():
    """통과 0 + 드롭 > 0 → `down`. 🔴 이름과 숫자는 **드롭의** 것이어야 한다.

    드롭으로 차단을 말했는데 `evidence` 에 통과량 이름과 0 이 적히면, 사람이 콘솔에서 그 지표를
    열어 보고 「0 이네, 아무 일도 없었는데?」로 읽는다 — 재현할 수 없는 빨강이다.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                # 🔴 **나감(`BytesOut`) 0 을 잰 계열**이 있어야 이 검사가 무엇을 잰다 — 들어옴만 두면
                #    통과량 이름 자체가 없어서(정정 1-2) 「통과량 이름을 적는」 회귀가 통과한다
                #    (브레이크 `차단에통과량이름` 이 죽었다 · 2026-09-28 전량 스윕).
                (TGW_ID, "BytesOut", [0.0, 0.0]),
                (TGW_ID, "BytesIn", [0.0, 0.0]),
                (TGW_ID, "PacketDropCountBlackhole", [7.0, 5.0]),
                flowing=(NAT_ID,),
            )
        )
    )
    observed = _by_id(document)[TGW_ID]
    assert observed["status"] == "down"
    assert observed["evidence"]["metric"] == "PacketDropCountBlackhole"
    assert observed["evidence"]["datapoints"] == 2
    assert observed["error_count"] == 1
    assert "PacketDropCountBlackhole" in observed["why"]
    assert "900" in observed["why"], "창 길이를 사유에 적지 않으면 재현할 수 없다"
    _valid(document)


def test_a_new_nat_that_dropped_packets_is_not_called_blocked():
    """🔴 **NAT 의 버림 수만으로 「막혔다」를 적지 않는다**(ADR 0008 D3 정정 5 · 랩 A1 실측).

    사람이 보는 결과: NAT 게이트웨이를 방금 만들면 **생성 직후 3분에** `PacketsDropCount` 가
    49패킷을 낸다(`docs/verification-lab.md:143,153`) — 아직 아무도 그 NAT 을 쓰지 않아 통과량은 0 이다.
    옛 규칙(통과 0 + 드롭 > 0 → 막힘)은 이 정상 NAT 을 **빨간 「막혔다」**로 그리고, 고객은 방금
    만든 것이 고장 났다고 읽는다. NAT 에서 막힘을 말할 수 있는 신호는 **포트 고갈 하나**다.

    대조군이 이 검사의 판별력이다 — 같은 모양인데 `ErrorPortAllocation` 이 양수면 `down` 이고
    사유에 적히는 이름도 그것이다. 둘이 같은 결과가 되면 이 검사는 아무것도 재지 않는다.
    """
    badge = _observe(
        FakeCloudWatch(
            _series(
                (NAT_ID, "BytesInFromSource", [0.0, 0.0]),
                (NAT_ID, "BytesOutToDestination", [0.0, 0.0]),
                (NAT_ID, "PacketsDropCount", [49.0, 0.0]),
                flowing=(TGW_ID,),
            )
        )
    )[0]
    observed = _by_id(badge)[NAT_ID]
    assert observed["status"] == "up", "갓 만든 정상 NAT 이 막힌 것으로 그려진다"
    # 동률(둘 다 0)이면 이름이 앞선 것 — `_fold` 의 결정론 규칙이다(불변식 ②).
    assert observed["evidence"]["metric"] == "BytesInFromSource", observed
    # `datapoints` 는 **0 이 아닌 점의 수**다 → 「쟀고 전부 0」은 `0`, 「못 쟀다」는 `None`.
    assert observed["evidence"]["datapoints"] == 0, "못 잰 것으로 적혔다"
    assert observed["why"] is None, observed

    blocked = _observe(
        FakeCloudWatch(
            _series(
                (NAT_ID, "BytesInFromSource", [0.0, 0.0]),
                (NAT_ID, "BytesOutToDestination", [0.0, 0.0]),
                (NAT_ID, "PacketsDropCount", [49.0, 0.0]),
                (NAT_ID, "ErrorPortAllocation", [3.0, 1.0]),
                flowing=(TGW_ID,),
            )
        )
    )[0]
    exhausted = _by_id(blocked)[NAT_ID]
    assert exhausted["status"] == "down", exhausted
    assert exhausted["evidence"]["metric"] == "ErrorPortAllocation", (
        "차단을 말했는데 배지용 드롭 이름을 적으면 콘솔에서 재현할 수 없다"
    )
    assert "ErrorPortAllocation" in exhausted["why"]
    assert "PacketsDropCount" not in (exhausted["why"] or "")


def test_the_adr_says_nat_packet_drops_are_not_a_blocking_signal():
    """🔴 코드의 `blocking` ↔ ADR 0008 D3 표. **둘이 같이 틀릴 수 있는 구멍을 막는다.**

    위 검사만 있으면 코드를 고쳐서 통과시킬 수 있다. 정본은 ADR 이므로, D3 의 NAT 행이 「버림 수를
    차단 신호에서 뺀다」고 적고 있다는 사실과 코드의 선언을 **한 검사 안에서** 맞춘다 — 한쪽만
    뒤집으면 FAIL 한다.
    """
    rows = [line for line in _adr8().splitlines() if line.startswith("| NAT GW |")]
    assert len(rows) == 1, f"D3 표의 NAT 행을 특정할 수 없다: {rows}"
    assert "`ErrorPortAllocation` (Sum)만" in rows[0], rows[0]
    assert "`PacketsDropCount` 는 D 신호에서 뺀다" in rows[0], rows[0]
    by_name = {ref.name: ref for ref in NODE_PROBES["nat_gateway"].metrics}
    assert by_name["ErrorPortAllocation"].blocking is True
    assert by_name["PacketsDropCount"].blocking is False, (
        "ADR 은 배지 전용이라고 적었는데 코드는 차단 신호로 쓴다"
    )


def test_the_drop_count_lands_in_the_row_not_only_in_the_why_line():
    """S2-2 ⓑ(2026-09-26) — 「드롭 N」이 읽는 칸(`drops`)을 엔진이 채운다.

    사람이 보는 결과: 흐름이 관측된 선에 **초록 + 「드롭 15」**가 같이 적힌다. 이 칸이 없으면
    화면이 드롭을 말할 수단이 `why` 문장뿐이고, **문장에서 숫자를 뽑는 화면은 계약이 아니다**
    (`schema/observation.schema.json` `drops` 의 `$comment`). 렌더러는 `row.drops` 를 읽는다
    (`renderer/flow.js` `wireBadge`·`showWirePanel`).

    🔴 **등급 경로를 다 잰다** — `drops` 는 등급과 **독립적인 사실**이므로 한 경로만 실으면 나머지
    등급의 행에서 배지가 조용히 사라진다. 그리고 **세 값이 서로 다른 사실**이다(ADR 0007):
    `15`(쟀고 15) · `0`(쟀고 0) · **키 없음**(못 쟀거나 드롭 지표가 없다). 대조군 두 개가 이
    검사의 판별력이다 — 키 없음만 재면 「아무 행에도 안 싣는다」도 통과하고, 0 을 재지 않으면
    「0 이면 키를 지운다」도 통과한다.
    """
    # ① `live` + 드롭 > 0 (흐르면서 일부를 버린다) · ② `live` + 「쟀고 0」 · ③ 드롭 지표가 조용하다
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                (NAT_ID, "PacketsDropCount", [12.0, 3.0]),
                (TGW_ID, "PacketDropCountBlackhole", [0.0, 0.0]),
            )
        )
    )
    rows = _by_id(document)
    assert rows[NAT_ID]["status"] == "live"
    assert rows[NAT_ID]["drops"] == 15, "창 합계여야 한다(12 + 3)"
    assert rows[TGW_ID]["drops"] == 0, "「쟀고 0」이 키 없음으로 뭉개졌다"
    assert "drops" not in rows[ENDPOINT_ID], (
        "드롭 지표가 조용한데 0 을 적었다 — 「드롭 없음」과 「못 쟀음」이 같아진다"
    )
    _valid(document)

    # ④ `down` 경로 — 차단을 말한 행에도 그 숫자가 남아야 한다(사유 문장과 같은 수).
    blocked, _ = _observe(
        FakeCloudWatch(
            _series(
                (TGW_ID, "BytesIn", [0.0, 0.0]),
                (TGW_ID, "PacketDropCountBlackhole", [7.0, 5.0]),
                flowing=(NAT_ID,),
            )
        )
    )
    down = _by_id(blocked)[TGW_ID]
    assert down["status"] == "down"
    assert down["drops"] == 12
    _valid(blocked)

    # ⑤ 「흐름 없음」(`up` + 통과 0) 경로 — 갓 만든 NAT 의 배지 전용 드롭. 등급은 안 바뀌지만
    #    화면은 **왜 회색인데 버림이 있는지**를 말할 수 있어야 한다(랩 A1 정정 5).
    quiet, _ = _observe(
        FakeCloudWatch(
            _series(
                (NAT_ID, "BytesInFromSource", [0.0, 0.0]),
                (NAT_ID, "BytesOutToDestination", [0.0, 0.0]),
                (NAT_ID, "PacketsDropCount", [49.0, 0.0]),
                flowing=(TGW_ID,),
            )
        )
    )
    new_nat = _by_id(quiet)[NAT_ID]
    assert new_nat["status"] == "up", "전제가 바뀌었다 — 이 경로는 `up` 이어야 한다"
    assert new_nat["drops"] == 49
    _valid(quiet)


def test_two_drop_metrics_on_one_target_report_the_larger_not_the_sum():
    """🔴 `drops` 는 드롭 지표 **하나**의 창 합계다 — 지표끼리 더하지 않는다(#32 리뷰 5).

    TGW 는 블랙홀과 「경로 없음」을 따로 센다. 둘을 더하면 화면의 「드롭 100」이 콘솔의 어느 지표와도
    맞지 않아 사람이 따라갈 수 없다. 차단 사유 문장이 이름을 대는 지표와 숫자가 같아야 한다.
    스키마 `drops` 설명이 이 규칙을 적는다(`observation.schema.json` `drops`).
    """
    # ① 흐르면서 두 종류로 버린다 — 70 과 30 중 큰 쪽
    flowing, _ = _observe(
        FakeCloudWatch(
            _series(
                (TGW_ID, "PacketDropCountBlackhole", [40.0, 30.0]),
                (TGW_ID, "PacketDropCountNoRoute", [20.0, 10.0]),
            )
        )
    )
    live = _by_id(flowing)[TGW_ID]
    assert live["status"] == "live"
    assert live["drops"] == 70, (
        "두 드롭 지표를 더했다(70 + 30) — 콘솔의 어느 지표와도 안 맞는다"
    )
    _valid(flowing)

    # ② 막혔다 — 사유 문장의 지표와 숫자가 `drops` 와 같다
    blocked, _ = _observe(
        FakeCloudWatch(
            _series(
                (TGW_ID, "BytesOut", [0.0, 0.0]),
                (TGW_ID, "BytesIn", [0.0, 0.0]),
                (TGW_ID, "PacketDropCountBlackhole", [40.0, 30.0]),
                (TGW_ID, "PacketDropCountNoRoute", [20.0, 10.0]),
                flowing=(NAT_ID,),
            )
        )
    )
    down = _by_id(blocked)[TGW_ID]
    assert down["status"] == "down"
    assert down["drops"] == 70
    assert "PacketDropCountBlackhole = 70" in down["why"], down["why"]
    _valid(blocked)


def test_a_gauge_is_folded_by_max_not_by_sum():
    """🔴 게이지는 창 **최대**로 접는다 — 합계로 접으면 「1분에 흐름 3,600개」가 나온다.

    `_fold` 는 값이 가장 큰 흐름 지표를 고르므로, 게이지를 합계로 접으면 그 부풀린 수가 판정의
    근거로 올라가고 `evidence.metric` 도 그 이름이 된다(사람이 콘솔에서 확인하면 다른 수다).
    """
    nlb = _active_nlb()
    cw = FakeCloudWatch(
        _series((NLB_ID, "ActiveFlowCount", [3.0, 5.0, 4.0]), flowing=(NAT_ID,)),
    )
    document, layer = _observe(cw, load_balancers=nlb)
    reading = layer.readings_for(NLB_ID)["ActiveFlowCount"]
    assert reading.aggregate == AGG_MAX
    assert reading.value == 5.0, "합계(12)로 접혔다"
    assert _by_id(document)[NLB_ID]["status"] == "live"


def test_fold_folds_counters_by_sum_and_state_by_max():
    """`_fold` 단위 검사 — 역할별 접기 규칙(ADR 0008 D2 통계 규칙 · D6).

    🔴 상태·건강 역할은 아직 어느 프로브도 쓰지 않으므로(위 래칫) **여기가 유일한 측정**이다.
    상태는 창 최대 = 「창 내내 0 이어야 차단」이고, 건강은 창 최소 = 「한 칸이라도 0 이면 전멸
    구간이 있었다」다. 둘을 뒤집으면 유지보수·재수렴이 전부 빨간 선이 된다.
    """
    flowing = _fold(
        {"BytesIn": Reading("BytesIn", ROLE_FLOW, AGG_SUM, 3, 2, 6144.0, REPORT_ALWAYS)}
    )
    assert flowing.flowing and not flowing.blocked

    state_down = _fold(
        {
            "TunnelState": Reading(
                "TunnelState", ROLE_STATE, AGG_MAX, 15, 0, 0.0, REPORT_ALWAYS
            )
        }
    )
    assert state_down.blocked and state_down.report_metric == "TunnelState"

    state_up = _fold(
        {
            "TunnelState": Reading(
                "TunnelState", ROLE_STATE, AGG_MAX, 15, 3, 1.0, REPORT_ALWAYS
            )
        }
    )
    assert not state_up.blocked, "창 안에 한 번이라도 1 이면 차단이 아니다"

    health_gone = _fold(
        {
            "HealthyHostCount": Reading(
                "HealthyHostCount", ROLE_HEALTH, AGG_MIN, 15, 9, 0.0, REPORT_CONDITIONAL
            )
        }
    )
    assert health_gone.blocked

    # 🔴 못 쟀으면(빈 배열) 차단이 아니다 — 「모름」을 「고장」으로 올리지 않는다(이음매 3).
    unmeasured = _fold(
        {
            "TunnelState": Reading(
                "TunnelState", ROLE_STATE, AGG_MAX, 0, 0, None, REPORT_ALWAYS
            )
        }
    )
    assert not unmeasured.measured and not unmeasured.blocked


def test_report_metric_never_borrows_a_health_name_for_a_silent_flow():
    """🔴 통과량을 **하나도 못 쟀을 때**도 근거 이름은 통과량의 것이다(ADR 0009 D3).

    사람이 보는 결과: 화면의 「흐름」 칸이 「확인 불가」인데 근거로 `HealthyHostCount` 가 적혀
    있으면, 사람은 콘솔에서 그 지표를 열고 「타깃은 건강한데 왜 확인 불가인가」로 헤맨다. 이름은
    **우리가 무엇을 물었는지**를 말해야 한다 — 물은 것은 통과량이다.
    """
    verdict = _fold(
        {
            # 이름순으로는 이 건강 지표가 앞선다 → `metric_names[0]` 폴백이면 이것이 뽑힌다.
            "HealthyHostCount": Reading(
                "HealthyHostCount",
                ROLE_HEALTH,
                AGG_MIN,
                15,
                15,
                3.0,
                REPORT_CONDITIONAL,
            ),
            "ProcessedBytes": Reading(
                "ProcessedBytes", ROLE_FLOW, AGG_SUM, 0, 0, None, REPORT_NONZERO_ONLY
            ),
        }
    )
    assert verdict.measured, "건강 지표를 쟀으므로 대상 전체로는 «쟀다» 다"
    assert not verdict.flowing and not verdict.blocked
    assert verdict.report_metric == "ProcessedBytes", verdict


def test_a_silent_flow_metric_is_null_even_when_another_series_answered():
    """🔴 **같은 대상의 다른 계열이 답했다고 통과량을 쟀다고 적지 않는다**(ADR 0009 ④).

    사람이 보는 결과: 엔드포인트에 드롭 계열만 답이 오고 통과량은 한 점도 안 온 상황에서, 옛 판은
    그 칸에 「데이터포인트 0개」를 적었다 — 화면은 그것을 **「이 구간에 흐름이 없었다」는 확정**
    으로 읽는다. 실제로 일어난 일은 「그 지표를 못 봤다」이므로 숫자는 비어야 하고, 사유가
    다음에 무엇을 할지(= 기다릴 일인지 우리가 고칠 일인지)를 말해야 한다.

    대조군이 이 검사의 판별력이다 — **같은 `up`** 인데 통과량을 실제로 쟀으면 `0` 이 적히고
    사유가 없다. 둘이 같은 바이트가 되면 이 검사는 아무것도 재지 않는다.

    🔴 **피험자가 NAT 에서 엔드포인트로 바뀌었다(2026-09-26)**: 랩 A1 실측으로 NAT 통과량 2종이
    `always` 가 되어 「점이 안 왔다」의 뜻이 정반대가 됐다 — NAT 에서 점이 없으면 이제 **우리가
    고칠 결함**(차원 조합·발행 시작)이고, 여기서 재려는 「발행되지 않는 지표라 기다릴 일」은
    `nonzero_only` 자원에서만 성립한다. 엔드포인트 통과량 2종이 그 자리다.

    🔴 **2026-10-21 개정**(ADR 0006 §O7-2a): 수락된 엔드포인트의 `BytesProcessed` **성공한** 빈 응답은 이제 「쟀고 0」
    이다. 그래서 「통과량을 못 봤다」를 만드는 길은 그 계열의 쿼리가 `Complete` 가 아닌 경우(`Forbidden`)뿐이다 —
    드롭 계열이 답했다고 그 계열을 0 으로 채우면 거부가 「흐름 없음」으로 숨는다(불변식 ④).
    """
    drop = _drop_metric(ENDPOINT_ID)
    silent = _observe(
        FakeCloudWatch(
            _series((ENDPOINT_ID, drop, [0.0, 0.0, 0.0]), flowing=(TGW_ID,)),
            status_codes={_key(ENDPOINT_ID, "BytesProcessed"): "Forbidden"},
        )
    )[0]
    observed = _by_id(silent)[ENDPOINT_ID]
    assert observed["status"] == "up", observed
    assert observed["evidence"]["metric"] == "BytesProcessed"
    assert observed["evidence"]["datapoints"] is None, "못 잰 것을 «쟀고 0» 으로 적었다"
    # 사유 = `_quiet_why` 의 「값 0 이면 발행되지 않는 지표」 갈래(쿼리 `StatusCode` 는 따로 사유로 올리지 않는다 — 지금 그대로)
    assert "발행되지 않는 지표" in (observed["why"] or ""), observed

    measured = _observe(
        FakeCloudWatch(
            _series(
                (ENDPOINT_ID, "BytesProcessed", [0.0, 0.0, 0.0]),
                (ENDPOINT_ID, "NewConnections", [0.0, 0.0, 0.0]),
                flowing=(TGW_ID,),
            )
        )
    )[0]
    zeroed = _by_id(measured)[ENDPOINT_ID]
    assert zeroed["status"] == "up"
    assert zeroed["evidence"]["datapoints"] == 0, "쟀고 0 은 숫자가 있어야 한다"
    assert zeroed["why"] is None, "쟀는데 «못 쟀다» 사유가 붙었다"


def test_traffic_does_not_erase_a_down_verdict():
    """🔴 2층은 **`up` 에서만** 올라간다 — 실패한 NAT·`failed` LB 에 흐름이 보여도 `down` 이다.

    사람이 보는 결과: 고장 난 자원이 초록으로 바뀌어 화면에서 사라진다. 「흐르는데 고장」을
    그리려면 다섯 번째 어휘가 필요하고 ADR 0008 D1 이 그것을 금지했다.
    """
    cw = FakeCloudWatch(_series(flowing=FLOWING + (DEAD_NAT_ID, NLB_ID)))
    document, _ = _observe(cw)
    for target_id in (DEAD_NAT_ID, NLB_ID):
        observed = _by_id(document)[target_id]
        assert observed["status"] == "down", observed
        assert observed["evidence"]["metric"] is None
        assert observed["evidence"]["api"] != METRIC_API
        assert observed["partial"] is False


# ---------------------------------------------------------------- 2층 전용 엣지(D8)


def test_metric_only_edges_appear_only_when_the_metric_answered():
    """🔴 못 쟀으면 **행이 없다**(ADR 0008 D1 의 E · D8-1).

    사람이 보는 결과의 차이: 행이 없으면 그 선은 「경로만 확인」으로 남고, 여기서 `up` 을
    만들면 **근거 0 인 초록**이 된다 — 그것이 이 ADR 이 폐기한 종단 추론의 형태다.
    대조군을 둘 둔다: 지표를 하나도 등록하지 않은 실행과 등록한 실행.
    """
    quiet, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    # 🔴 예외 둘 — ALB 배치 선 · 수락된 엔드포인트의 서브넷 구간은 **성공한 빈 응답 = 쟀고 0**(`empty_is_zero` ·
    #    사용자 결정 2026-10-21 · ADR 0006 §O7-2a)이라 행이 있다. 그 행은 `up` + `datapoints: 0`(흐름 없음)이다 —
    #    근거 0 인 초록이 아니다.
    zeroed = [
        o
        for o in quiet["observations"]
        if o["target_id"].startswith(
            ("load_balancer_placement|" + ALB_ID, "endpoint_connection|" + ENDPOINT_ID)
        )
    ]
    assert {o["target_id"] for o in zeroed} == {
        ALB_PUBLIC_EDGE,
        ALB_PRIVATE_EDGE,
        ENDPOINT_PUBLIC_EDGE,
        ENDPOINT_PRIVATE_EDGE,
    }
    assert all(
        o["status"] == "up" and o["evidence"]["datapoints"] == 0 for o in zeroed
    ), zeroed
    assert not [
        o
        for o in quiet["observations"]
        if o["target_id"].split("|")[0] in METRIC_ONLY_EDGES and o not in zeroed
    ], "2층이 그 구간에 답하지 않았는데 행을 만들었다"

    document, _ = _observe(
        FakeCloudWatch(
            _series(flowing=(NAT_ID, ATTACH_ID, ALB_PUBLIC_EDGE, ENDPOINT_PUBLIC_EDGE))
        )
    )
    rows = _by_id(document)
    assert rows[ALB_PUBLIC_EDGE]["status"] == "live"
    assert rows[ALB_PUBLIC_EDGE]["evidence"]["api"] == METRIC_API
    assert rows[ALB_PUBLIC_EDGE]["evidence"]["metric"] == "ProcessedBytes"
    assert rows[ATTACH_TGW_EDGE]["status"] == "live"
    assert rows[ENDPOINT_PUBLIC_EDGE]["status"] == "live"
    # 🔴 대조군 — **같은 종류의 다른 구간**은 계열을 등록하지 않았으니 흐름이 없다. 엔드포인트 노드
    # 지표가 서브넷 구간으로 번지면 여기가 `live` 로 잡히고, 그것이 옛 종단 추론이다. (수락된 엔드포인트라
    # 빈 응답 = 쟀고 0 → 행은 있고 `up` · 0 · 2026-10-21)
    assert rows[ENDPOINT_PRIVATE_EDGE]["status"] == "up", (
        "등록하지 않은 계열에 흐름이 번졌다"
    )
    assert rows[ENDPOINT_PRIVATE_EDGE]["evidence"]["datapoints"] == 0
    # ALB 배치 선은 빈 응답 = 쟀고 0(`empty_is_zero`) → 행은 있지만 옆 구간의 흐름이 번지지 않는다(`up` · 0)
    assert rows[ALB_PRIVATE_EDGE]["status"] == "up"
    assert rows[ALB_PRIVATE_EDGE]["evidence"]["datapoints"] == 0
    _valid(document)


def test_a_measured_span_that_carried_nothing_is_up_with_zero():
    """쟀고 0 인 구간 → 행이 **있고** `up` + `datapoints: 0`.

    「행 없음」(못 쟀다)과 「`up` + 0」(쟀고 조용했다)은 화면에서 달라야 한다 — 전자는 「경로만
    확인」, 후자는 「지금 트래픽 없음」이다. 고객이 물을 질문이 다르다.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                (ALB_PUBLIC_EDGE, "ProcessedBytes", [0.0, 0.0, 0.0]),
                flowing=(NAT_ID,),
            )
        )
    )
    observed = _by_id(document)[ALB_PUBLIC_EDGE]
    assert observed["status"] == "up"
    assert observed["evidence"]["datapoints"] == 0
    assert observed["evidence"]["api"] == METRIC_API
    _valid(document)


def test_the_vpc_side_of_a_measured_attachment_says_deduced_in_the_document():
    """🔴 `evidence.observed_on` 이 **문서에** 적힌다(ADR 0008 D8-2 · 불변식 ③).

    같은 계열이 두 선을 초록으로 만드는데, 한쪽은 지표가 그 구간을 직접 이름 댄 것이고 다른
    한쪽은 연역이다. 그 차이를 데이터에 적지 않으면 화면이 배지로 말할 수 없고, 두 선은
    구별되지 않는다 — 그 구별이 사라진 상태가 폐기된 종단 추론이다.
    """
    document, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID, ATTACH_ID))))
    rows = _by_id(document)
    direct, deduced = rows[ATTACH_TGW_EDGE], rows[ATTACH_VPC_EDGE]
    assert direct["status"] == deduced["status"] == "live"
    assert direct["evidence"].get("observed_on") is None, (
        "직접 측정에 연역 표시가 붙었다"
    )
    assert deduced["evidence"]["observed_on"] == ATTACH_ID
    # 어태치먼트 노드 자신도 같은 계열로 초록이다 — 박스만 회색인 화면이 되지 않는다.
    assert rows[ATTACH_ID]["status"] == "live"
    _valid(document)


def test_a_metric_only_edge_can_be_down():
    """구간 지표가 차단을 말하면 그 **선**이 `down` 이다(ADR 0008 D3 + D8).

    사람이 보는 결과: blackhole 라우트가 생긴 어태치먼트 구간이 점선 + `!` 로 바뀐다. 이 경로가
    없으면 차단은 노드에만 보이고 **어느 구간이 막혔는지**는 화면에 없다.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                (ATTACH_ID, "BytesIn", [0.0, 0.0]),
                (ATTACH_ID, "PacketDropCountBlackhole", [9.0]),
                flowing=(NAT_ID,),
            )
        )
    )
    rows = _by_id(document)
    for edge_id in (ATTACH_TGW_EDGE, ATTACH_VPC_EDGE):
        assert rows[edge_id]["status"] == "down", edge_id
        assert rows[edge_id]["evidence"]["metric"] == "PacketDropCountBlackhole"
        assert rows[edge_id]["error_count"] == 1
    assert rows[ATTACH_ID]["status"] == "down"
    _valid(document)


# ─────────────────────────────────────────────────────────────────────────────
# ADR 0008 정정 1-2 — 「들어옴」과 「통과」를 가른다. 서부 랩 블랙홀 구간이 초록이던 결함(2026-09-28).
# ─────────────────────────────────────────────────────────────────────────────
ARRIVED = [210.0, 210.0]  # 창 합계 420 = 랩 B3 의 `BytesIn`(정정 1)
ZERO = [0.0, 0.0]


def _tgw_family(
    target_id: str,
    *,
    out: list[float],
    blackhole: list[float] | None,
    noroute: list[float] | None,
) -> tuple[tuple[str, str, list[float]], ...]:
    """TGW 계열 한 벌. 드롭을 `None` 으로 주면 **빈 배열**(= 못 쟀다)이 온다."""
    rows = [(target_id, "BytesIn", ARRIVED), (target_id, "BytesOut", out)]
    if blackhole is not None:
        rows.append((target_id, "PacketDropCountBlackhole", blackhole))
    if noroute is not None:
        rows.append((target_id, "PacketDropCountNoRoute", noroute))
    return tuple(rows)


def test_traffic_that_only_arrived_and_was_blackholed_is_down_on_card_and_line():
    """🔴 **전량 블랙홀 = 막힘**(ADR 0008 정정 1 · 1-2). 카드·어태치먼트 카드·어태치먼트 선 셋 다.

    사람이 보는 결과: 서부 랩처럼 TGW 로 들어온 트래픽이 1분마다 블랙홀에 전부 버려지는데, 옛 판은
    `BytesIn` 420 을 「흐른다」로 읽어 **선을 초록으로** 그렸다. 사유에 들어온 양을 같이 적어야 사람이
    「보낸 것은 있었는데 건너가지 못했다」를 읽는다.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                *_tgw_family(TGW_ID, out=ZERO, blackhole=[3.0, 2.0], noroute=ZERO),
                *_tgw_family(ATTACH_ID, out=ZERO, blackhole=[3.0, 2.0], noroute=ZERO),
                flowing=(NAT_ID,),
            )
        )
    )
    rows = _by_id(document)
    for target_id in (TGW_ID, ATTACH_ID, ATTACH_TGW_EDGE, ATTACH_VPC_EDGE):
        row = rows[target_id]
        assert row["status"] == "down", (target_id, row)
        assert row["evidence"]["metric"] == "PacketDropCountBlackhole", target_id
        assert "들어옴 BytesIn = 420" in row["why"], (target_id, row["why"])
    _valid(document)


def test_a_one_way_sender_with_measured_zero_drops_is_a_flow():
    """🔴 **한 방향 송신은 흐름이다** — 정정 1 의 「흐름 = `BytesOut` > 0」만으로는 틀린다(정정 1-2).

    사람이 보는 결과: VPC 에서 온프렘 syslog 로 보내기만 하는 어태치먼트는 `BytesIn` > 0 · `BytesOut`
    = 0 이다. 차단 드롭을 **재서 0** 이면 버려진 것이 없으므로 건너갔다. 이것을 「흐름 없음」으로
    적으면 잘 도는 로그 경로가 회색이 된다. 근거 이름은 실제로 값이 있는 `BytesIn` 이다.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                *_tgw_family(ATTACH_ID, out=ZERO, blackhole=ZERO, noroute=ZERO),
                flowing=(NAT_ID, TGW_ID),
            )
        )
    )
    rows = _by_id(document)
    for target_id in (ATTACH_ID, ATTACH_TGW_EDGE, ATTACH_VPC_EDGE):
        row = rows[target_id]
        assert row["status"] == "live", (target_id, row)
        assert row["evidence"]["metric"] == "BytesIn", target_id
        assert row["evidence"]["datapoints"] == 2, target_id
        assert row["drops"] == 0, target_id
    _valid(document)


def test_arrival_without_measured_drops_is_undecided_not_a_flow_or_quiet():
    """🔴 **들어왔는데 버림을 못 쟀다 = 판정 못 함**(정정 1-2). 흐름도 「흐름 없음」도 아니다.

    사람이 보는 결과: 흐름으로 적으면 블랙홀일 수 있는 선이 초록이고, 「흐름 없음」으로 적으면
    들어온 420 바이트를 없었다고 단정한다. 그래서 숫자를 적지 않고(`datapoints: null`) 이유를 말한다.

    대조군이 판별력이다 — 같은 모양에서 드롭을 재서 0 이면 `live`(위 검사), 한 계열만 빠져도
    판정 못 함이다(「둘 다 재서 0」이 조건이다).
    """
    for blackhole, noroute in ((None, None), (ZERO, None), (None, ZERO)):
        document, _ = _observe(
            FakeCloudWatch(
                _series(
                    *_tgw_family(
                        ATTACH_ID, out=ZERO, blackhole=blackhole, noroute=noroute
                    ),
                    flowing=(NAT_ID, TGW_ID),
                )
            )
        )
        row = _by_id(document)[ATTACH_ID]
        case = (blackhole, noroute)
        assert row["status"] == "up", (case, row)
        assert row["evidence"]["metric"] == "BytesIn", case
        assert row["evidence"]["datapoints"] is None, case
        assert "건너갔는지 버려졌는지 알 수 없습니다" in row["why"], (case, row["why"])
        _valid(document)


def test_a_tgw_card_that_drops_for_no_route_is_down():
    """🔴 TGW 카드도 「경로 없음」 버림을 잰다(정정 1-2). 옛 카드는 블랙홀만 읽었다.

    사람이 보는 결과: 라우트가 **없어서** 전량 버리는 TGW 가 카드에서 초록이었다(블랙홀 0 이므로).
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                *_tgw_family(TGW_ID, out=ZERO, blackhole=ZERO, noroute=[4.0, 1.0]),
                flowing=(NAT_ID, ATTACH_ID),
            )
        )
    )
    row = _by_id(document)[TGW_ID]
    assert row["status"] == "down", row
    assert row["evidence"]["metric"] == "PacketDropCountNoRoute"
    assert "들어옴 BytesIn = 420" in row["why"], row["why"]
    _valid(document)


def test_a_missing_drop_series_is_not_a_measured_zero():
    """`_fold` 단위 — 차단 드롭 계열이 **응답에서 빠졌으면**(`incomplete`) 「재서 0」이 아니다.

    빈 배열(`points == 0`)과 달리 응답에서 빠진 계열은 `readings` 에 아예 없다 → 남은 드롭이 전부
    0 이라는 사실만으로 흐름을 적으면, 빠진 그 계열이 전량 버림을 말하고 있었을 수 있다.
    """
    readings = {
        "BytesIn": Reading(
            "BytesIn", ROLE_FLOW, AGG_SUM, 2, 2, 420.0, REPORT_ALWAYS, arrival_only=True
        ),
        "BytesOut": Reading("BytesOut", ROLE_FLOW, AGG_SUM, 2, 0, 0.0, REPORT_ALWAYS),
        "PacketDropCountBlackhole": Reading(
            "PacketDropCountBlackhole",
            ROLE_DROP,
            AGG_SUM,
            2,
            0,
            0.0,
            REPORT_ALWAYS,
            True,
        ),
    }
    assert _fold(readings).flowing, "대조군: 전부 돌아왔으면 한 방향 송신 = 흐름"
    partial = _fold(readings, complete=False)
    assert not partial.flowing and not partial.blocked
    assert partial.arrival_unproven
    assert partial.report_metric == "BytesIn"


def test_the_adr_flow_rule_for_tgw_matches_the_declarations():
    """🔴 코드의 `arrival_only` ↔ ADR 0008 D2 의 TGW 두 행. **둘이 같이 틀릴 수 있는 구멍을 막는다.**

    정정 1 은 표만 고치고 코드가 따르지 않았다 — 그래서 블랙홀 구간이 2일 동안 초록이었다. 이
    검사는 규칙 문장이 표에 있다는 것과 코드가 그 문장대로 선언했다는 것을 한 곳에서 맞춘다.
    """
    rule = "**`BytesOut` > 0, 또는 `BytesIn` > 0 이면서 차단 드롭을 재서 0**"
    for label in ("| **TGW** |", "| **TGW 어태치먼트 = 구간** |"):
        rows = [line for line in _adr8().splitlines() if line.startswith(label)]
        assert len(rows) == 1, f"D2 표의 {label} 행을 특정할 수 없다: {rows}"
        condition = rows[0].split(" | ")[3]
        assert rule in condition, (label, condition)
        assert not condition.startswith("어느 한쪽"), (label, condition)
    for key in ("transit_gateway", "transit_gateway_attachment", "attachment"):
        probe = {**NODE_PROBES, **EDGE_PROBES}[key]
        by_name = {ref.name: ref for ref in probe.metrics}
        assert by_name["BytesIn"].arrival_only is True, key
        assert by_name["BytesOut"].arrival_only is False, key
        assert by_name["PacketDropCountBlackhole"].blocking is True, key
        assert by_name["PacketDropCountNoRoute"].blocking is True, key


def test_an_alb_placement_can_never_say_down():
    """🔴 판별력 — ALB 배치 구간은 **차단 지표가 없어서** `down` 을 만들 수 없다.

    통과 0 이 곧 차단이 아니라는 것(D1·D3)을 여기서 한 번 더 고정한다. 반대편 대조군은 NLB
    배치다 — 거기엔 `RejectedFlowCount` 가 있으므로 같은 조건에서 `down` 이 나와야 한다.
    같은 화면을 보는 사람 입장에서: 「ALB 는 트래픽이 없을 뿐이고, NLB 는 거절하고 있다」.
    """
    quiet_alb, _ = _observe(
        FakeCloudWatch(
            _series((ALB_PUBLIC_EDGE, "ProcessedBytes", [0.0]), flowing=(NAT_ID,))
        )
    )
    assert _by_id(quiet_alb)[ALB_PUBLIC_EDGE]["status"] == "up"

    document, _ = _observe(
        FakeCloudWatch(
            _series(
                (NLB_EGRESS_EDGE, "ProcessedBytes", [0.0]),
                (NLB_EGRESS_EDGE, "RejectedFlowCount", [4.0]),
                flowing=(NAT_ID,),
            )
        ),
        load_balancers=_active_nlb(),
    )
    rejected = _by_id(document)[NLB_EGRESS_EDGE]
    assert rejected["status"] == "down"
    assert rejected["evidence"]["metric"] == "RejectedFlowCount"
    _valid(document)


# ---- ALB 의 빈 응답 = 흐름 없음(사용자 결정 2026-10-21 · `MetricRef.empty_is_zero`) -------------------------
# AWS 는 ALB 의 `ProcessedBytes` 가 0 이면 점을 보내지 않는다(「There is a nonzero value」 · 실측). 고치기 전에는
# 그 빈 응답이 「확인 불가」였고, 배치 선은 행이 통째로 사라져 이력에 「흐름 멈춤」이 남지 않았다.
# 🔴 못 읽음(호출 거부 · 쿼리 Forbidden)은 그대로 확인 불가다 — 0 으로 적으면 거부가 「흐름 없음」으로 숨는다(불변식 ④).

ALB_FLOW_KEYS = tuple(
    _key(t, "ProcessedBytes") for t in (ALB_ID, ALB_PUBLIC_EDGE, ALB_PRIVATE_EDGE)
)


def test_a_silent_alb_is_up_with_no_flow():
    """성공한 빈 응답 → ALB 노드·배치 선 둘 다 `up` + `datapoints: 0`(흐름 없음) · 사유 없음."""
    document, layer = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    rows = _by_id(document)
    for target in (ALB_ID, ALB_PUBLIC_EDGE, ALB_PRIVATE_EDGE):
        row = rows[target]
        assert row["status"] == "up", row
        assert row["partial"] is False
        assert row["evidence"]["metric"] == "ProcessedBytes"
        assert row["evidence"]["datapoints"] == 0, "쟀고 0 — null(못 쟀다)이 아니다"
        assert row.get("why") is None, row
    # 점은 지어내지 않는다 — 받은 점은 0개 그대로다.
    reading = layer.readings[(ALB_ID, "ProcessedBytes")]
    assert reading.points == 0 and reading.zero_filled and reading.value == 0.0
    # 🔴 대조군 — 같은 문서의 `always`(NAT 2개 중 값을 안 등록한 것 · TGW)는 빈 응답이 여전히 확인 불가다.
    #    (엔드포인트는 2026-10-21 부터 수락됐으면 같은 규칙 — 아래 「수락된 엔드포인트」 절)
    assert rows[TGW_ID]["evidence"]["datapoints"] is None
    _valid(document)


def test_a_forbidden_alb_query_is_not_read_as_zero():
    """호출은 성공했는데 그 계열이 `Forbidden` — 빈 배열이지만 「쟀고 0」이 아니다(확인 불가 · 배치 선 행 없음)."""
    document, layer = _observe(
        FakeCloudWatch(
            _series(flowing=(NAT_ID,)),
            status_codes={k: "Forbidden" for k in ALB_FLOW_KEYS},
        )
    )
    rows = _by_id(document)
    assert rows[ALB_ID]["status"] == "up"
    assert rows[ALB_ID]["evidence"]["datapoints"] is None, rows[ALB_ID]
    assert not layer.readings[(ALB_ID, "ProcessedBytes")].zero_filled
    assert ALB_PUBLIC_EDGE not in rows and ALB_PRIVATE_EDGE not in rows
    _valid(document)


def test_a_denied_alb_is_partial_not_zero():
    """2층이 막혔다(AccessDenied) — ALB 도 `partial` + 사유 · 숫자 없음 · 배치 선 행 없음(coverage 가 막힘을 말한다)."""
    document, _ = _observe(
        FakeCloudWatch(_series(), error=client_error("AccessDenied", "GetMetricData"))
    )
    rows = _by_id(document)
    alb = rows[ALB_ID]
    assert alb["status"] == "up" and alb["partial"] is True, alb
    assert [r["code"] for r in alb["partial_reasons"]] == ["access_denied"]
    assert alb["evidence"]["datapoints"] is None
    assert ALB_PUBLIC_EDGE not in rows and ALB_PRIVATE_EDGE not in rows
    _valid(document)


def test_an_alb_that_went_silent_lists_one_flow_stop_each():
    """ALB `live` → 빈 응답 → 「흐름 멈춤」(`live → up`)이 노드·배치 선마다 **정확히 한 줄** · 다음 빈 실행은 0줄.

    고치기 전에는 배치 선의 행이 사라져(2층 전용 연결의 「없음」 = 미측정) 그 선의 멈춤이 이력에 남지 않았다.
    """
    lit = FakeCloudWatch(_series(flowing=(NAT_ID, ALB_ID, ALB_PUBLIC_EDGE)))
    before, _ = _observe(lit)
    assert _by_id(before)[ALB_ID]["status"] == "live"
    assert _by_id(before)[ALB_PUBLIC_EDGE]["status"] == "live"
    after, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))), at=LATER_AT)
    report = apply_history(after, before)
    stops = [
        c["target_id"]
        for c in after["changes"]
        if (c["kind"], c.get("from"), c.get("to")) == ("status_changed", "live", "up")
    ]
    assert sorted(stops) == sorted([ALB_ID, ALB_PUBLIC_EDGE]), after["changes"]
    assert report.unmeasured == 0, report
    _valid(after)
    # 계속 조용하면 새 줄이 없다(같은 `up` → `up`).
    third, _ = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID,))), at="2026-09-23T00:20:00Z"
    )
    again = apply_history(third, after)
    assert again.new_rows == 0, third["changes"]


# ---- 수락된 PrivateLink 엔드포인트의 빈 응답 = 흐름 없음(사용자 결정 2026-10-21 · ADR 0006 §O7-2a 개정) ------------------
# AWS 는 엔드포인트가 그 1분에 트래픽을 받았을 때만 `AWS/PrivateLinkEndpoints` 를 발행한다(ALB 와 같은 모양). 고치기 전에는
# 그 빈 응답이 「확인 불가」였고 서브넷 구간은 행이 통째로 사라져 이력에 「흐름 멈춤」이 남지 않았다.
# 🔴 수락 전(`pendingAcceptance` 등)인 엔드포인트도 똑같이 0점이다(ADR 0008 정정 4) — 그것까지 「흐름 없음」이라 하면
# 트래픽을 받을 수 없는 엔드포인트가 조용히 정상으로 보인다. 그래서 고르는 기준은 지표가 아니라 1층 상태다.

ENDPOINT_FLOW_KEYS = tuple(
    _key(t, "BytesProcessed")
    for t in (ENDPOINT_ID, ENDPOINT_PUBLIC_EDGE, ENDPOINT_PRIVATE_EDGE)
)
ENDPOINT_TARGETS = (ENDPOINT_ID, ENDPOINT_PUBLIC_EDGE, ENDPOINT_PRIVATE_EDGE)


def _endpoints_in(state: str) -> list[dict[str, Any]]:
    """인터페이스 엔드포인트 하나의 `State` 만 바꾼 응답(게이트웨이 엔드포인트는 그대로)."""
    endpoints = copy.deepcopy(VPC_ENDPOINTS)
    for endpoint in endpoints:
        if endpoint["VpcEndpointType"] == "Interface":
            endpoint["State"] = state
    return endpoints


def test_accepted_endpoint_probes_differ_only_in_reading_the_empty_answer():
    """🔴 수락된 판은 **해석 하나만** 다르다 — 차원·지표·발행 규칙이 두 벌이 되면 한쪽만 고쳐진다(§O7-1 함정 1)."""
    for key, accepted in ACCEPTED_ENDPOINT_PROBES.items():
        base = {**NODE_PROBES, **EDGE_PROBES}[key]
        assert (accepted.key, accepted.namespace, accepted.dimensions) == (
            base.key,
            base.namespace,
            base.dimensions,
        )
        assert [r.name for r in accepted.metrics] == [r.name for r in base.metrics]
        zeroed = [r.name for r in accepted.metrics if r.empty_is_zero]
        assert zeroed == ["BytesProcessed"], (key, zeroed)
        # 드롭은 0 으로 채우지 않는다 — 채우면 「드롭을 재서 0」이 근거 없는 주장이 된다.
        assert not any(r.empty_is_zero for r in base.metrics), key
        assert all(
            a.reporting == b.reporting and a.role == b.role and a.blocking == b.blocking
            for a, b in zip(accepted.metrics, base.metrics, strict=True)
        ), key


def test_a_silent_accepted_endpoint_is_up_with_no_flow():
    """수락됨 + 성공한 빈 응답 → 엔드포인트 노드·서브넷 구간 둘 다 `up` + `datapoints: 0`(흐름 없음) · 사유 없음."""
    document, layer = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    rows = _by_id(document)
    for target in ENDPOINT_TARGETS:
        row = rows[target]
        assert row["status"] == "up", row
        assert row["partial"] is False
        assert row["evidence"]["metric"] == "BytesProcessed"
        assert row["evidence"]["datapoints"] == 0, "쟀고 0 — null(못 쟀다)이 아니다"
        assert row.get("why") is None, row
    reading = layer.readings[(ENDPOINT_ID, "BytesProcessed")]
    assert reading.points == 0 and reading.zero_filled and reading.value == 0.0
    # 판정은 `BytesProcessed` 가 선다 — 다른 계열을 0 으로 지어내지 않는다.
    assert not layer.readings[(ENDPOINT_ID, "NewConnections")].zero_filled
    assert not layer.readings[(ENDPOINT_ID, "PacketsDropped")].zero_filled
    _valid(document)


def test_an_accepted_endpoint_that_went_silent_lists_one_flow_stop_each():
    """수락된 엔드포인트 `live` → 빈 응답 → 「흐름 멈춤」(`live → up`)이 노드·구간마다 **정확히 한 줄** · 다음 빈 실행은 0줄.

    고치기 전에는 구간의 행이 사라져(2층 전용 연결의 「없음」 = 미측정) 그 선의 멈춤이 이력에 남지 않았다.
    """
    before, _ = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID, ENDPOINT_ID, ENDPOINT_PUBLIC_EDGE)))
    )
    assert _by_id(before)[ENDPOINT_ID]["status"] == "live"
    assert _by_id(before)[ENDPOINT_PUBLIC_EDGE]["status"] == "live"
    after, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))), at=LATER_AT)
    report = apply_history(after, before)
    rows = [c for c in after["changes"] if c["target_id"] in ENDPOINT_TARGETS]
    stops = [
        c["target_id"]
        for c in rows
        if (c["kind"], c.get("from"), c.get("to")) == ("status_changed", "live", "up")
    ]
    assert sorted(stops) == sorted([ENDPOINT_ID, ENDPOINT_PUBLIC_EDGE]), rows
    assert len(rows) == len(stops), rows  # 그 밖의 줄(생김·없어짐)이 없다
    assert report.unmeasured == 0, report
    _valid(after)
    third, _ = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID,))), at="2026-09-23T00:20:00Z"
    )
    again = apply_history(third, after)
    assert again.new_rows == 0, third["changes"]


# `rejected`·`expired`·`deleted` 는 구조가 노드째 뺀다(`services/base.py` `GONE_STATES`) — 판정할 행 자체가 없다.
@pytest.mark.parametrize("state", ["pendingAcceptance", "pending", "failed", "partial"])
def test_a_pending_endpoint_with_no_points_is_unknown(state):
    """수락 전·실패 엔드포인트 + 빈 응답 → 「흐름 없음」이 **아니다**: 구간 행 없음(경로만 확인) · 노드는 1층 상태가 말한다.

    사람이 보는 결과: 수락을 기다리는 엔드포인트가 「지금 트래픽 없음」으로 보이면 고객은 「연결은 됐고 조용할 뿐」으로 읽는다.
    실제로는 그 엔드포인트가 아직 트래픽을 받을 수 없다(ADR 0008 정정 4 — 수락 대기도 0점이다).
    """
    endpoints = _endpoints_in(state)
    document, layer = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID,))), vpc_endpoints=endpoints
    )
    rows = _by_id(document)
    for edge in (ENDPOINT_PUBLIC_EDGE, ENDPOINT_PRIVATE_EDGE):
        assert edge not in rows, (
            "수락 전 엔드포인트 구간에 「흐름 없음」 행을 만들었다",
            rows[edge],
        )
    node = rows[ENDPOINT_ID]
    assert node["status"] != "up", (
        node
    )  # 1층(`NODE_RULES`)이 판정한다 — 「흐름 없음」(up · 0)이 아니다
    assert node["evidence"].get("datapoints") is None, node
    assert state in (node.get("why") or ""), node
    assert not layer.readings[(ENDPOINT_ID, "BytesProcessed")].zero_filled
    # 다음 실행도 같은 상태 · 같은 빈 응답 → 이력에 그 엔드포인트의 줄이 하나도 없다
    later, _ = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID,))),
        at=LATER_AT,
        vpc_endpoints=endpoints,
    )
    apply_history(later, document)
    assert not [c for c in later["changes"] if c["target_id"] in ENDPOINT_TARGETS], (
        later["changes"]
    )
    _valid(document)
    _valid(later)


def test_the_endpoint_probe_is_chosen_by_the_layer_one_state():
    """고르는 곳 자체 — `Available`(문서 대소문자) · `available`(실측) 둘 다 수락 판 · 상태 없음은 기본 판."""
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    nodes = {n.id: n for n in state.nodes}
    endpoint = nodes[ENDPOINT_ID]
    edge = next(e for e in state.edges if e.id == ENDPOINT_PUBLIC_EDGE)
    assert probe_for(endpoint) is ACCEPTED_ENDPOINT_PROBES["vpc_endpoint"]
    assert (
        edge_probe_for(edge, nodes) is ACCEPTED_ENDPOINT_PROBES["endpoint_connection"]
    )
    for value, accepted in (
        ("Available", True),
        ("pendingAcceptance", False),
        (None, False),
    ):
        endpoint.attributes["state"] = value
        assert (
            probe_for(endpoint) is ACCEPTED_ENDPOINT_PROBES["vpc_endpoint"]
        ) is accepted
        assert (
            edge_probe_for(edge, nodes)
            is ACCEPTED_ENDPOINT_PROBES["endpoint_connection"]
        ) is accepted
        if not accepted:
            assert probe_for(endpoint) is NODE_PROBES["vpc_endpoint"]
            assert edge_probe_for(edge, nodes) is EDGE_PROBES["endpoint_connection"]


def test_a_forbidden_endpoint_query_is_not_read_as_zero():
    """수락됐어도 그 계열이 `Forbidden` 이면 빈 배열은 「쟀고 0」이 아니다 — 숫자 없음 · 구간 행 없음."""
    document, layer = _observe(
        FakeCloudWatch(
            _series(flowing=(NAT_ID,)),
            status_codes={k: "Forbidden" for k in ENDPOINT_FLOW_KEYS},
        )
    )
    rows = _by_id(document)
    assert rows[ENDPOINT_ID]["status"] == "up"
    assert rows[ENDPOINT_ID]["evidence"]["datapoints"] is None, rows[ENDPOINT_ID]
    assert not layer.readings[(ENDPOINT_ID, "BytesProcessed")].zero_filled
    assert ENDPOINT_PUBLIC_EDGE not in rows and ENDPOINT_PRIVATE_EDGE not in rows
    _valid(document)


def test_a_denied_endpoint_is_partial_not_zero():
    """2층이 막혔다(AccessDenied) — 수락된 엔드포인트도 `partial` + 사유 · 숫자 없음 · 구간 행 없음(불변식 ④)."""
    document, _ = _observe(
        FakeCloudWatch(_series(), error=client_error("AccessDenied", "GetMetricData"))
    )
    rows = _by_id(document)
    endpoint = rows[ENDPOINT_ID]
    assert endpoint["status"] == "up" and endpoint["partial"] is True, endpoint
    assert [r["code"] for r in endpoint["partial_reasons"]] == ["access_denied"]
    assert endpoint["evidence"]["datapoints"] is None
    assert ENDPOINT_PUBLIC_EDGE not in rows and ENDPOINT_PRIVATE_EDGE not in rows
    _valid(document)


def test_a_metric_only_edge_with_an_unbuildable_dimension_says_none():
    """🔴 개발 결함은 **조용할 수 없다** — 행이 없으면 아무 데도 드러나지 않는다.

    2층 전용 엣지의 「행 없음」은 정상(못 쟀다)이므로, 차원을 못 만든 경우까지 행을 안 만들면
    우리 쪽 결함이 「경로만 확인」으로 위장된다. 그래서 그때는 `none` + 사유로 행을 만든다.
    """
    attachments = copy.deepcopy(TRANSIT_GATEWAY_ATTACHMENTS)
    del attachments[0]["TransitGatewayId"]
    document, _ = _observe(
        FakeCloudWatch(_series(flowing=FLOWING + (ENDPOINT_PUBLIC_EDGE,))),
        transit_gateway_attachments=attachments,
    )
    rows = _by_id(document)
    observed = rows[ATTACH_VPC_EDGE]
    assert observed["status"] == "none"
    assert "구조 수집기" in observed["why"]
    assert [r["code"] for r in observed["partial_reasons"]] == ["api_error"]
    # 대조군 — 같은 문서의 다른 구간은 정상이다(전부 `none` 이면 판별력이 없다).
    assert rows[ENDPOINT_PUBLIC_EDGE]["status"] == "live"
    _valid(document)


def test_other_edges_never_get_a_metric():
    """2층 전용 3종 **밖의** 선은 지표를 받지 않는다(§O7 규칙 1).

    라우트·연관 선에 `live` 를 붙이면 그 값은 어디서 온 것도 아니다 — 노드의 값을 선에 복사한
    것이고, 그것이 종단 추론이다.
    """
    document, _ = _observe(FakeCloudWatch(_series()))
    others = [
        o
        for o in document["observations"]
        if o["target_kind"] == "edge"
        and o["target_id"].split("|")[0] not in METRIC_ONLY_EDGES
    ]
    assert others, "엣지 관측이 0건이면 아래 검사는 아무것도 재지 않는다"
    for observed in others:
        assert observed["status"] != "live", observed["target_id"]
        assert (observed["evidence"] or {}).get("metric") is None


# 🔴 이력에서 2층 전용 연결은 「생겼다/없어졌다」가 아니다(배포본 실측 2026-09-27). 그 행은
# 쟀을 때만 있으므로, 행의 유무를 구성 변경으로 읽으면 실시간을 켜고 끌 때마다 연결 17개가
# 목록에 「생겼다」·「없어졌다」로 쏟아지고 흐름이 멈출 때마다 1~2줄이 더 붙는다.
LATER_AT = "2026-09-23T00:10:00Z"
EDGES_TOO = FLOWING + (ALB_PUBLIC_EDGE, ENDPOINT_PUBLIC_EDGE)


def _metric_only(document: dict[str, Any]) -> list[str]:
    return sorted(
        o["target_id"]
        for o in document["observations"]
        if o["target_id"].split("|")[0] in METRIC_ONLY_EDGES
    )


@pytest.mark.parametrize("turned", ["on", "off"])
def test_turning_live_on_or_off_lists_no_connection_as_created_or_removed(turned):
    lit = FakeCloudWatch(_series(flowing=EDGES_TOO))
    first, second = (None, lit) if turned == "on" else (lit, None)
    before, _ = _observe(first)
    after, _ = _observe(second, at=LATER_AT)
    edges = _metric_only(after if turned == "on" else before)
    assert len(edges) >= 3, "전제: 한쪽 실행에만 2층 전용 연결 행이 있어야 한다"

    report = apply_history(after, before)

    assert after["changes"] == [], [
        (c["kind"], c["target_id"]) for c in after["changes"]
    ]
    # 흐름 노드의 `live ↔ up` 도 같은 칸 규칙으로 빠진다 → 연결 행 전부 + 그 노드들.
    assert report.layer_changed >= len(edges), report
    assert report.unmeasured == 0, report
    _valid(after)


def test_a_connection_that_went_quiet_is_not_listed_as_removed():
    before, _ = _observe(FakeCloudWatch(_series(flowing=EDGES_TOO)))
    after, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))), at=LATER_AT)
    quiet = sorted(set(_metric_only(before)) - set(_metric_only(after)))
    # 어태치먼트 선 2개(`always` 의 빈 응답 = 확인 불가 → 행 없음). ALB 배치 선 · 수락된 엔드포인트 구간은 빈 응답 =
    # 쟀고 0 이라 행이 남는다(ADR 0006 §O7-2a) — 그 둘은 아래 대조군의 「흐름 멈춤」 쪽이다.
    assert len(quiet) >= 2, "전제: 이번 실행에서 값이 없어 사라진 연결 행이 있어야 한다"

    report = apply_history(after, before)

    kinds = {(c["kind"], c["target_id"].split("|")[0]) for c in after["changes"]}
    assert not {k for k in kinds if k[0] != "status_changed"}, kinds
    assert report.unmeasured == len(quiet), report
    assert report.unknown_cell == 0, report
    # 🔴 대조군 — 멈춤은 **소유 자원 행**이 여전히 말한다. 이것이 없으면 위 가드가 흐름 멈춤
    # 기록 자체를 지워도 통과한다.
    stopped = {
        c["target_id"]
        for c in after["changes"]
        if (c["kind"], c["from"], c["to"]) == ("status_changed", "live", "up")
    }
    assert ATTACH_ID in stopped, stopped
    assert {ALB_PUBLIC_EDGE, ENDPOINT_PUBLIC_EDGE} <= stopped, stopped
    _valid(after)


def test_a_transit_gateway_link_that_stopped_is_recorded():
    """🔴 TGW 어태치먼트 선도 이력의 칸을 찾아야 한다(`history.target_cells` ↔ `observe` 같은 규칙).

    사람이 보는 결과: 칸을 못 찾으면 그 선의 멈춤/시작은 「칸 미상」으로 세어질 뿐 목록에 한 줄도
    남지 않는다 — 어태치먼트 노드 줄만 보고 「어느 쪽 선이 멈췄나」를 알 수 없다.
    """
    before, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID, ATTACH_ID))))
    after, _ = _observe(
        FakeCloudWatch(
            _series(
                (ATTACH_ID, _flow_metric(ATTACH_ID), [0.0, 0.0, 0.0]), flowing=(NAT_ID,)
            )
        ),
        at=LATER_AT,
    )
    assert _by_id(before)[ATTACH_TGW_EDGE]["status"] == "live"
    assert _by_id(after)[ATTACH_TGW_EDGE]["status"] == "up", "전제: 쟀고 0 이어야 한다"

    report = apply_history(after, before)

    moved = {
        (c["target_id"], c["from"], c["to"])
        for c in after["changes"]
        if c["kind"] == "status_changed"
    }
    assert (ATTACH_TGW_EDGE, "live", "up") in moved, moved
    assert report.unknown_cell == 0, report
    _valid(after)


# ---------------------------------------------------------------- 실패 경로


def test_a_denied_metric_layer_keeps_up_and_records_why():
    """2층만 막힌 경우 — 스키마 `$comment` 가 정한 모양: `up` + `partial` + 사유.

    🔴 `none` 으로 떨어뜨리면 「구조적으로 관측 불가」와 섞이고, `up` 만 적으면 2층 실패가
    사라진다. 1층 판정은 그대로 살아 있어야 한다(대상이 사라진 것이 아니다).
    """
    cw = FakeCloudWatch(_series(), error=client_error("AccessDenied", "GetMetricData"))
    document, layer = _observe(cw)
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["partial"] is True
    assert [r["code"] for r in observed["partial_reasons"]] == ["access_denied"]
    assert observed["partial_reasons"][0]["api"] == METRIC_API
    assert observed["evidence"]["metric"] is None
    # coverage 는 종류마다 같은 실패를 적는다(2층은 층 단위로 죽는다).
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["status"] == "denied"
    assert rows["nat_gateway"]["reason"]["code"] == "access_denied"
    assert rows["nat_gateway"]["item_count"] is None
    # 지표가 없는 종류는 부르지 않았으므로 **막히지 않았다** — 같이 빨갛게 만들지 마라.
    assert rows["vpc"]["status"] == "ok"
    assert layer.enabled is True
    # 🔴 2층 전용 엣지는 **행이 없다** — 막힌 것은 coverage 가 말한다(위 `denied`).
    assert not [
        o
        for o in document["observations"]
        if o["target_id"].split("|")[0] in METRIC_ONLY_EDGES
    ]
    _valid(document)


def test_credentials_failure_is_a_layer_failure_not_a_crash():
    """자격증명이 없어 클라이언트를 못 만든 경우도 「2층이 죽었다」다(전체를 죽이지 않는다)."""
    document, _ = _observe(
        FakeCloudWatch(_series()),
        fail={"cloudwatch": client_error("ExpiredToken", "AssumeRole")},
    )
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["partial"] is True
    assert _metric_rows(document)["nat_gateway"]["status"] != "ok"


def test_metric_client_factory_failure_names_the_api_that_actually_failed():
    """🔴 2층 진입 실패는 `GetMetricData` 가 **아니다** — 실계정이 잡은 결함(2026-09-24).

    화면에서 일어난 일: 멤버 계정 역할에 `cloudwatch:GetMetricData` 를 넣어 배포한 **뒤에도**
    그 계정 칸이 「확인 불가 · cloudwatch:GetMetricData」라고 말했다. 진짜 원인은 허브 쪽 2층
    자격증명에 `sts:AssumeRole` 이 없는 것이라서, 대상 계정 정책을 몇 번 고쳐도 화면이 바뀌지
    않는다. 위 `test_a_denied_metric_layer_keeps_up_and_records_why` 가 **대조군**이다 —
    거기는 `get_metric_data` **호출**이 막힌 경우이므로 `METRIC_API` 를 계속 말해야 한다.
    """
    document, _ = _observe(
        FakeCloudWatch(_series()),
        fail={"cloudwatch": client_error("AccessDenied", "AssumeRole")},
    )
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["partial"] is True
    assert [r["api"] for r in observed["partial_reasons"]] == ["sts:AssumeRole"]
    row = _metric_rows(document)["nat_gateway"]
    assert row["status"] == "denied"
    assert row["reason"]["api"] == "sts:AssumeRole"
    assert row["reason"]["api"] != METRIC_API


def test_metric_factory_failure_without_an_operation_name_says_so():
    """API 이름을 알 수 없는 실패(`ClientError` 가 아닌 것)는 **추측하지 않는다**.

    `sts:None` 이나 `cloudwatch:GetMetricData` 로 적으면 둘 다 거짓이다 — 사람을 있지도 않은
    액션·정책으로 보낸다. 이 자리의 정직한 이름은 「우리 팩토리가 죽었다」다.
    """
    document, _ = _observe(
        FakeCloudWatch(_series()),
        fail={"cloudwatch": RuntimeError("프로필을 찾을 수 없다")},
    )
    row = _metric_rows(document)["nat_gateway"]
    assert row["status"] != "ok"
    assert row["reason"]["api"] == "nettopo:client_factory"


def test_all_targets_empty_is_a_defect_not_a_quiet_network():
    """🔴 §O7-1 함정 1+3 — 「대상이 1개 이상인데 dp>0 이 하나도 없으면 결함」.

    차원 이름을 틀렸을 때와 「정말 아무 흐름도 없다」가 **바이트 단위로 같다**(둘 다 `Complete`,
    `Messages` 없음). 전자로 읽어 통과시키면 이 층은 실패할 수 없는 검사가 된다.
    """
    document, layer = _observe(FakeCloudWatch({}))  # 아무 계열도 등록하지 않았다
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["status"] == "error"
    assert rows["nat_gateway"]["reason"]["code"] == "api_error"
    assert any("전부 지점 0개" in w for w in layer.warnings)
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["partial"] is True
    _valid(document)


def test_one_answered_target_is_not_a_defect():
    """대조군 — 하나라도 답이 오면 「전부 0」 규칙이 발동하지 않는다(판별력 확인)."""
    _document, layer = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    assert layer.failures == {}
    assert layer.warnings == ()


def test_a_missing_result_id_is_reported():
    """쿼리는 보냈는데 응답에 그 계열이 없는 경우 — 조용히 「지표 없음」이 되면 안 된다.

    🔴 **이 검사가 실측으로 잡은 구멍(2026-09-25)**: 대상 하나가 지표 4개를 갖게 된 뒤로는 계열
    하나가 빠져도 나머지로 판정이 서기 때문에 「응답에 없다」 경로가 **한 번도 발동하지 않았다**.
    사람이 겪는 일: 선은 초록으로 그려지는데 그 초록의 근거가 우리가 물어본 것의 일부뿐이고,
    화면에는 아무 표시도 없다. 최악의 형태는 드롭 계열만 빠지는 것이다 — 차단을 못 보고 초록으로
    남는다. 그래서 판정은 그대로 쓰고(불변식 ④) **사유를 함께** 남긴다.
    """
    cw = FakeCloudWatch(_series(), missing_ids=("m0",))
    document, layer = _observe(cw)
    missing = [
        o
        for o in document["observations"]
        if o["partial"]
        and "대응이 깨졌습니다" in json.dumps(o["partial_reasons"], ensure_ascii=False)
    ]
    assert missing, "응답에서 빠진 쿼리가 아무 행에도 드러나지 않았다"
    assert layer.incomplete, "층이 빠진 계열을 기억하지 않는다"
    assert any("응답에 없다" in w for w in layer.warnings)
    # 🔴 판정은 살아 있다 — 남은 계열로 선 판정을 지우면 「결함을 신고하려고 화면을 비우는」 셈이다.
    assert {o["status"] for o in missing} <= {"live", "up", "down"}
    assert layer.readings, "한 id 가 빠졌다고 층 전체가 비었다"
    # 대조군 — 빠진 계열을 쓰지 않는 대상은 `partial` 이 아니다(전부 빨개지면 판별력이 없다).
    assert any(o["partial"] is False for o in document["observations"])
    _valid(document)


# ---------------------------------------------------------------- 층 off · coverage


def test_layer_off_is_not_a_failure():
    """기본값은 2층 off(ADR 0006 D4) — 「안 켰다」는 `skipped` 이고 `?` 의 첫 번째 이유다."""
    document, _ = _observe()
    assert document["layers"] == {"describe": True, "metric": False}
    for row in _metric_rows(document).values():
        assert row["status"] == "skipped"
        assert row["reason"] is None
        assert row["item_count"] is None
    assert all(o["partial"] is False for o in document["observations"])
    # 🔴 2층이 꺼지면 2층 전용 엣지는 통째로 조용하다 — 그것이 정상이다(ADR 0008 D8-1).
    assert not [
        o
        for o in document["observations"]
        if o["target_id"].split("|")[0] in METRIC_ONLY_EDGES
    ]


def test_metric_coverage_counts_only_answered_targets():
    """2층 `item_count` = 「지표가 답을 준 대상 수」. 빈 배열은 세지 않는다.

    🔴 2층 전용 엣지는 **2층 칸에서만** 세어진다(1층 칸은 describe 가 읽은 것만 센다 —
    `schema/tests` 의 두 원장). 그래서 모니터링을 켰다 끌 때 1층의 숫자가 움직이지 않는다.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(flowing=(NAT_ID, ATTACH_ID, ENDPOINT_ID, ALB_ID, ALB_PUBLIC_EDGE))
        )
    )
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["item_count"] == 1  # NAT 2개 중 답이 온 것 1개
    # 어태치먼트 1개의 계열이 노드 1 + 선 2 를 답한다(같은 계열 · 세 대상).
    assert rows["transit_gateway_attachment"]["item_count"] == 3
    # ALB 노드 + 배치 선 2개 — 값이 온 선 1개 + 빈 응답 = 쟀고 0 인 선 1개(`empty_is_zero` · 사용자 결정 2026-10-21)
    assert rows["load_balancer"]["item_count"] == 3
    # Interface 엔드포인트 노드(값이 왔다) + 서브넷 구간 2개(수락됨 · 빈 응답 = 쟀고 0 · ADR 0006 §O7-2a 2026-10-21)
    assert rows["vpc_endpoint"]["item_count"] == 3
    assert rows["route_table"]["item_count"] == 0  # 지표가 없는 종류
    # 🔴 원장: 2층 `item_count` 의 합 == **지표가 답을 준 행 수**(`datapoints` 가 숫자인 행).
    # 이름만 적히고 `datapoints` 가 `null` 인 행(= 물었는데 조용했다)은 세지 않는다 — 그 둘을
    # 합치면 「읽었다」와 「빈 배열이 왔다」가 같은 숫자가 되고, 화면의 「확인 불가 N」이 거짓이 된다.
    answered = [
        o
        for o in document["observations"]
        if (o.get("evidence") or {}).get("datapoints") is not None
    ]
    written = sum(r["item_count"] or 0 for r in rows.values() if r["status"] == "ok")
    assert written == len(answered), (
        "2층 원장이 어긋났다(요약의 숫자와 화면의 근거가 다르다)"
    )
    # 판별력 — 「이름은 적혔지만 못 쟀다」 행이 실제로 있어야 위 등식이 무언가를 가른다.
    named_but_quiet = [
        o
        for o in document["observations"]
        if (o.get("evidence") or {}).get("metric")
        and (o.get("evidence") or {}).get("datapoints") is None
    ]
    assert named_but_quiet, "빈 배열로 온 대상이 0개면 이 원장은 아무것도 가르지 않는다"


def test_a_denied_describe_does_not_claim_the_metric_layer_was_read():
    """🔴 1층이 막히면 대상 id 를 모르므로 2층을 **부르지 않았다** — `ok` 를 적으면 거짓이다.

    describe 와 같은 사유를 복사해도 거짓이다(부르지도 않은 API 의 거부를 주장한다).
    """
    denied = client_error("UnauthorizedOperation", "DescribeNatGateways")
    document, _ = _observe(
        FakeCloudWatch(_series()), errors={"describe_nat_gateways": denied}
    )
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["status"] == "skipped"
    assert rows["nat_gateway"]["reason"] is None
    assert rows["vpc"]["status"] == "ok"
    # 1층이 막힌 종류에는 관측 항목 자체가 없다(스키마 규칙 2).
    assert NAT_ID not in _by_id(document)
    _valid(document)


def test_metric_layer_is_byte_identical_across_runs():
    """불변식 ② — 2층을 붙여도 같은 입력이면 같은 바이트."""
    first, _ = _observe(FakeCloudWatch(_series()))
    second, _ = _observe(FakeCloudWatch(_series()))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
