"""The metric layer axis: A-grade metrics, blocking signals, the whole window
and span metrics.

The reason this file exists is what a real account showed: the metric layer
does not fail, it quietly comes back empty. Get a dimension name wrong and the
response is still no data points, `StatusCode: Complete` and no `Messages`. So
"we ran it against a real account and there were no errors" is no evidence that
the metric layer works. With the dimension constants wrong from end to end the
screen looks exactly the same: everything `up`, everything showing no traffic.
The power to tell those apart is created only here. `fakes.FakeCloudWatch`
returns a value only when the dimensions match exactly, and when they do not it
hands back an empty array quietly, just as the real API does.

Four measures catch each other:

1. The source of truth against the expectations against the code. The probe
   constants are compared with the expectation table in this file, and then
   every name in that table is checked to really appear in the design tables.
   Changing only one side fails.
2. The shape of the query: `period=60`, a 900-second window, the window ending
   at the snapshot moment, no ladder of retries, and each series bought exactly
   once, since the bill is by the number of metrics requested.
3. The verdict: flowing (`live`), measured and zero (`up` with
   `datapoints: 0`), an empty array (`up` with a question mark), no throughput
   with drops (`down`), blocked (`up` with `partial`), and a type with no
   metrics (quiet). The two central contracts of this layer are that drops
   above zero do not negate traffic, and that an empty array does not override
   an `up` from the structure tier.
4. Span metrics on edges, which is the body of the span design. The three
   metric-layer-only edge types get a row only when the metric layer answered,
   and no row at all when it could not measure. The attachment to VPC line
   measures a different target, so it records that it is a deduction through
   `evidence.observed_on`. While this measure did not exist, zero of 445 rows
   from a real account had metric-layer evidence, and all fourteen green lines
   on the screen were inferred from their endpoints.
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
    """The observation design document, which is internal and absent from the
    public tree. Only the tests that use it record a reason and skip; it is not
    read at import time.
    """
    return read_or_skip("docs/adr/0006-observation-schema.md")


def _adr8() -> str:
    """The span metric design document, handled the same way."""
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
# The Direct Connect gateway attachment, which has had a target node since
# 2.1.0. Both the node and the TGW line are planned.
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

# The metric-layer-only edges. These lines have no evidence from the structure
# tier, so when the metrics do not answer, having no row is the right answer.
# Writing `up` would be a claim with nothing behind it.
ATTACH_TGW_EDGE = f"attachment|{ATTACH_ID}|{TGW_ID}"
ATTACH_VPC_EDGE = f"attachment|{ATTACH_ID}|{VPC_ID}"
ATTACH2_TGW_EDGE = f"attachment|{ATTACH2_ID}|{TGW_ID}"
# ATTACH2 is a VPN attachment. It has a target node, `vpn_connection`, so the
# line on that side is planned as well. The attachment-dimension metrics appear
# for a VPN attachment just the same, on the same dimensions as the VPC line.
ATTACH2_VPN_EDGE = (
    f"attachment|{ATTACH2_ID}|vpn_connection/{ACCOUNT}/{REGION}/vpn-0aaa1111bbbb3333"
)
ATTACH_DX_TGW_EDGE = f"attachment|{ATTACH_DX_ID}|{TGW_ID}"
# The Direct Connect gateway became an observation target in the structure
# tier, so the state snapshot holds a node for it too and the line on its side
# is planned. Same dimensions and same treatment as the VPN line above.
ATTACH_DX_DXGW_EDGE = (
    f"attachment|{ATTACH_DX_ID}|direct_connect_gateway/{ACCOUNT}/global/{DXGW_TGW}"
)
ENDPOINT_PUBLIC_EDGE = f"endpoint_connection|{ENDPOINT_ID}|{PUBLIC_SUBNET_ID}"
ENDPOINT_PRIVATE_EDGE = f"endpoint_connection|{ENDPOINT_ID}|{PRIVATE_SUBNET_ID}"
ALB_PUBLIC_EDGE = f"load_balancer_placement|{ALB_ID}|{PUBLIC_SUBNET_ID}"
ALB_PRIVATE_EDGE = f"load_balancer_placement|{ALB_ID}|{PRIVATE_SUBNET_ID}"
NLB_EGRESS_EDGE = f"load_balancer_placement|{NLB_ID}|subnet/{ACCOUNT}/{REGION}/subnet-0ddd4444eeee5555"

# ─────────────────────────────────────────────────────────────────────────────
# The expectation table. This is not a copy of the source of truth but the
# expectation itself. It is compared with the code in the first check below, and
# then checked again against what the design tables actually say in the second.
# Changing only one side fails.
# (probe key, namespace, order of dimension names, flow metric, blocking metric)
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

# The ALB family has no blocking metric, so it cannot produce `down`. That is a
# fact rather than a table: the ALB row lists `HTTPCode_ELB_5XX_Count` and
# `HealthyHostCount`, and both are meaningless without a target group
# dimension. `test_an_alb_placement_can_never_say_down` below pins that fact
# down in the language of the screen.
NO_BLOCK_PROBES = {"load_balancer:application", "load_balancer_placement:application"}

# Reading a drop and recording a block because of a drop are different things.
# Only what is listed here can turn a verdict into `down`; every other drop is
# used on screen as a drop-count badge and nothing more. The NAT gateway is the
# one difference: a freshly created NAT gateway drops 49 packets in the three
# minutes after creation, so recording a block from `PacketsDropCount` would
# turn a healthy new NAT gateway red.
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

# Throughput metrics that count only what arrived. A metric listed here cannot
# prove traffic on its own: the `BytesIn` of a TGW read 420 while every byte was
# being discarded into a blackhole. Dropping a metric from this table turns a
# blackholed span green, and adding one that does not belong drops a perfectly
# healthy span to unjudgeable.
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
    # The value is the literal `Interface`. Sent in lowercase there is no such
    # combination.
    "Endpoint Type": "Interface",
    "Service Name": f"com.amazonaws.{REGION}.ssm",
}


def _attachment_dimensions(attachment: str) -> dict[str, str]:
    return {"TransitGateway": CORE_TGW, "TransitGatewayAttachment": attachment}


# Target to (probe key, dimension value). A dimension value has to be built from
# the structure-tier response.
# What is written here is both the expectation for `plan()` and the source of the
# series keys in `FakeCloudWatch`, so a single character of drift in the
# dimension constants leaves the series unmatched and turns the verdict checks
# red along with it.
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

# A `failed` NLB builds no placement line, so measuring that line means
# changing its state first.
NLB_TARGETS: dict[str, tuple[str, dict[str, str]]] = {
    NLB_EGRESS_EDGE: (
        "load_balancer_placement:network",
        {"LoadBalancer": NLB_DIMENSION, "AvailabilityZone": "us-east-1a"},
    ),
}

FLOW_VALUES = [4096.0, 0.0, 2048.0]  # window total 6144, two non-zero points

# The targets that have values, meaning they carry traffic. Because a document
# where every target has zero data points counts as a defect, the default
# control case is not empty.
FLOWING = (NAT_ID, TGW_ID, ATTACH_ID, ENDPOINT_ID, ALB_ID)


def _probe_of(target_id: str) -> str:
    table = {**TARGETS, **NLB_TARGETS}
    return table[target_id][0]


def _flow_metric(target_id: str) -> str:
    """The first throughput metric that proves traffic on its own for that
    target, skipping the ones that count only what arrived.
    """
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
    """Target id to metric series. A target or metric that was not registered
    comes back as an empty array, meaning it could not be confirmed.

    `flowing` is shorthand for putting `FLOW_VALUES` on that target's first flow
    metric, and `entries` writes `(target, metric, values)` directly, which is
    how the drop, gauge and all-zero series are built.
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
    # The state tier, the observe CLI, runs with the state probes on
    # (`with_state=True` in `cli.py`), while the structure tier runs with them
    # off. Observing with them off leaves RDS, ECS and others reporting a value
    # of None that no rule covers.
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
    """The same order production uses: twelve structure types, then ten state
    types, then the metric layer, then the verdict.
    """
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
    """The load balancer list with only the state changed to `active`, which is
    what makes the NLB placement line exist.
    """
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


# ------------- the source of truth, the expectations and the code


def _adr8_table(heading: str) -> dict[str, list[list[str]]]:
    """Read the first table of one section of the design document into a mapping
    of first-cell label to the backticked names in each column.

    If the parsing breaks, the checks below compare two empty sets and pass,
    which is not a pass but an absence of measurement. So
    `test_adr8_tables_are_parseable` measures the row count and the column
    contents separately.
    """
    section = _adr8().split(heading, 1)[-1].split("\n### ", 1)[0]
    rows: dict[str, list[list[str]]] = {}
    started = False
    for line in section.splitlines():
        if not line.startswith("|"):
            if started:
                break  # with more than one table in a section, stop at the first
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


# Probe to the row labels in the design table that back it. The attachment row
# describes its metrics only as the same metrics, so it is read together with
# the TGW row, which is what that cell means.
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
    assert len(d2) == 18, f"read {len(d2)} rows from the D2 table: {sorted(d2)}"
    assert len(d3) == 9, f"read {len(d3)} rows from the D3 table: {sorted(d3)}"
    # If the labels cannot be read, the backing check below goes quiet without
    # even raising KeyError.
    assert set(D2_BACKING) == set(EXPECTED_PROBES)
    for labels in D2_BACKING.values():
        for label in labels:
            assert label in d2, f"the D2 table has no {label!r} row; the label changed"
    for labels in D3_BACKING.values():
        for label in labels:
            assert label in d3, f"the D3 table has no {label!r} row"
    assert d2["TGW"][0] == ["AWS/TransitGateway", "BytesIn", "BytesOut"]
    assert "TransitGatewayAttachment" in d2["TGW 어태치먼트 = 구간"][1]


def test_probe_constants_are_exactly_the_expected_table():
    """The code against the expectation table. A single difference in dimension
    name, order, metric name or role fails.

    Put in terms of what somebody sees: a span with one character wrong in a
    dimension name stays grey for ever and raises no error at all. So it can
    never be found from the screen.
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
        # A probe whose metric counts only what arrived has to have a blocking
        # drop. Without one there is no way to prove measured and zero, and that
        # target stays unjudgeable for ever.
        if arrival:
            assert blocking, (
                f"{key}: has a metric that counts only arrivals but no blocking drop"
            )
        # A probe that cannot produce `down` is not one that reads no drops but
        # one that has no drop it uses as a blocking signal. Measuring with
        # `not drops` would pass quietly once a probe appears that, like the NAT
        # gateway, reads a drop for the badge only.
        assert (key in NO_BLOCK_PROBES) == (not blocking), key
        # A span probe needs two or more dimensions, which is what qualifies it
        # as one.
        if key in EDGE_PROBES:
            assert len(probe.dimensions) >= 2, (
                f"{key}: this dimension combination does not identify a span"
            )


def test_reporting_is_pinned_per_namespace_and_metric():
    """Managing the publishing rule (`reporting`) by eye alone lets it flip
    quietly.

    Put in terms of what somebody sees: this one value decides between "this
    span carried no traffic during these fifteen minutes" and "this span could
    not be read, check the metric publishing settings". A real account showed
    that the same metric name behaves differently per namespace: the NLB
    `ProcessedBytes` publishes zero while the ALB `ProcessedBytes` does not.
    That is why the expectation table is keyed on (namespace, metric).

    The only things that qualify a metric to be written `always` are a quotation
    from the documentation or a measurement against a real account; the
    reasoning lives in the comments of `metrics.py`. Raising one without
    evidence settles every quiet window as carrying no traffic.
    """
    expected = {
        # Documentation: "All metrics are always reported."
        ("AWS/TransitGateway", "BytesIn"): REPORT_ALWAYS,
        ("AWS/TransitGateway", "BytesOut"): REPORT_ALWAYS,
        ("AWS/TransitGateway", "PacketDropCountBlackhole"): REPORT_ALWAYS,
        ("AWS/TransitGateway", "PacketDropCountNoRoute"): REPORT_ALWAYS,
        # Documentation: "The endpoint received traffic during the one-minute period."
        ("AWS/PrivateLinkEndpoints", "BytesProcessed"): REPORT_NONZERO_ONLY,
        ("AWS/PrivateLinkEndpoints", "NewConnections"): REPORT_NONZERO_ONLY,
        ("AWS/PrivateLinkEndpoints", "PacketsDropped"): REPORT_NONZERO_ONLY,
        # Documentation, plus a measurement: only 9 points existed out of 180
        # minutes, and none of them were zero
        ("AWS/ApplicationELB", "ProcessedBytes"): REPORT_NONZERO_ONLY,
        # Documentation: "Reported if there are registered targets.", plus a
        # measurement that found none
        ("AWS/ApplicationELB", "RequestCount"): REPORT_CONDITIONAL,
        # Measured over 180 minutes: 157/179, 86/179 and 179/179 of the points
        # were zero, and every point on the AZ dimension was zero too. The
        # introduction to the documentation is wrong here.
        ("AWS/NetworkELB", "ProcessedBytes"): REPORT_ALWAYS,
        ("AWS/NetworkELB", "NewFlowCount"): REPORT_ALWAYS,
        ("AWS/NetworkELB", "ActiveFlowCount"): REPORT_ALWAYS,
        ("AWS/NetworkELB", "RejectedFlowCount"): REPORT_ALWAYS,
        # Measured over 24 hours: all 1,440 points were zero
        ("AWS/NATGateway", "PacketsDropCount"): REPORT_ALWAYS,
        # Promoted from a lab measurement: a NAT gateway with no traffic
        # measured over 73 minutes gave 72 points, all of them zero. The
        # previous value, `unknown`, reflected an absence of measurement, since
        # traffic ran for the whole 24 hours and there was never a chance for a
        # zero. The lab created that chance and closed it.
        ("AWS/NATGateway", "BytesInFromSource"): REPORT_ALWAYS,
        ("AWS/NATGateway", "BytesOutToDestination"): REPORT_ALWAYS,
        # Not measured: over the same 73 minutes only 36 to 37 points arrived,
        # at irregular intervals, because without port exhaustion the series is
        # sparse. Absence must not be read as zero here.
        ("AWS/NATGateway", "ErrorPortAllocation"): REPORT_UNKNOWN,
    }
    seen = set()
    # Measured per probe rather than folded into a dict. If a node probe and a
    # placement probe wrote the same (namespace, metric) differently, a dict
    # comparison would see only the last value and pass.
    for probe in (*NODE_PROBES.values(), *EDGE_PROBES.values()):
        for ref in probe.metrics:
            key = (probe.namespace, ref.name)
            assert key in expected, (
                f"{probe.key}: the publishing-rule table has no entry for {ref.name!r}"
            )
            assert ref.reporting == expected[key], f"{probe.key} · {ref.name}"
            seen.add(key)
    assert seen == set(expected), (
        "an entry exists only in the expectation table, so a metric disappeared "
        "while the table stayed"
    )


def test_expected_probes_are_backed_by_the_adr():
    """The expectation table against the source of truth: every metric and
    dimension name in the expectation table has to appear in the design tables.

    With only the check above, both could be wrong together, since editing the
    expectation table to match the code would make it pass. The design document
    is the source of truth, and this is what fixes that direction.
    """
    d2, d3 = _adr8_table("### D2."), _adr8_table("### D3.")
    for key, (namespace, dimensions, flows, drops) in EXPECTED_PROBES.items():
        d2_names = {n for label in D2_BACKING[key] for cell in d2[label] for n in cell}
        assert namespace in d2_names, f"{key}: the D2 table has no namespace"
        for name in (*dimensions, *flows):
            assert name in d2_names, f"{key}: the D2 table has no {name!r}"
        if not drops:
            continue
        d3_names = {n for label in D3_BACKING[key] for cell in d3[label] for n in cell}
        for name in drops:
            assert name in d3_names, (
                f"{key}: the D3 blocking-signal table has no {name!r}"
            )


def test_every_observed_type_is_measured_or_declared_metricless():
    """Do not quietly conflate "not built yet" with "has no metrics"; together
    the two sets have to cover all ten types.

    What this check caught: while splitting the probes into node and edge
    probes, the metric-layer probe of the attachment node disappeared. On screen
    the attachment box alone stays grey for ever while the lines on both sides
    go green, and since no error appears it reads to the eye as there being no
    traffic.
    """
    measured = {key.split(":", 1)[0] for key in NODE_PROBES}
    assert measured | set(NO_METRIC_TYPES) | set(METRIC_PENDING_TYPES) == set(
        OBSERVED_RESOURCE_TYPES
    )
    assert not (measured & NO_METRIC_TYPES)
    assert not (measured & METRIC_PENDING_TYPES)
    assert not (NO_METRIC_TYPES & METRIC_PENDING_TYPES)


def test_o7_node_table_says_which_types_have_no_metric():
    """The types whose metric-layer column in the node table reads none have to
    be exactly `NO_METRIC_TYPES`.

    Writing a metric in the table that the code does not read, or the other way
    round, is indistinguishable on screen: both look like not knowing whether
    there is traffic.
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
        # A metric-layer cell with a marker means a metric is written down but
        # there is no probe for it yet (`METRIC_PENDING_TYPES`).
        if cells[2].startswith("없음"):
            silent.add(names[0])
        elif cells[2].startswith("🔵"):
            pending.add(names[0])
        else:
            measured.add(names[0])
    assert seen == len(OBSERVED_RESOURCE_TYPES), (
        f"read {seen} observation-target rows from the table"
    )
    assert silent == set(NO_METRIC_TYPES)
    assert pending == set(METRIC_PENDING_TYPES)
    assert measured == {key.split(":", 1)[0] for key in NODE_PROBES}


def test_metric_only_edge_types_match_the_edge_probes():
    """The types the edge table marks as metric-layer-only have to be exactly
    the types that have an edge probe.

    A mismatch is wrong quietly in two ways. A type with a probe that is not
    marked metric-layer-only is asked for structure-tier evidence and its row
    disappears. The other way round, no row is built at all, and the screen has
    no state for that line.
    """
    assert {key.split(":", 1)[0] for key in EDGE_PROBES} == set(METRIC_ONLY_EDGES)


def test_no_probe_uses_the_state_or_health_roles_yet():
    """A ratchet: no probe currently uses the state or health roles, which
    belong to the VPN and Direct Connect work.

    So that verdict path is measured only by unit checks on `_fold`. The day
    this line fails is the day to add an end-to-end check. Attaching the role
    without measuring end to end would leave nothing to catch the case where
    the state is zero and the screen is green.
    """
    roles = {
        ref.role
        for probe in {**NODE_PROBES, **EDGE_PROBES}.values()
        for ref in probe.metrics
    }
    assert roles == {ROLE_FLOW, ROLE_DROP}


# ------------------------------------------------------------ query planning


def test_plan_builds_every_target_and_dimension_from_layer_one():
    """Whether the dimension values come from the structure-tier response, and
    whether edges are planned as well, which is the body of the span design.

    An older version walked only the nodes, which is why zero of 445 rows from a
    real account had metric-layer evidence on an edge. There was no path for a
    line to receive the metric layer at all, and the screen filled that gap by
    inferring from the endpoints.
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
    # The code does the sorting: the node group first, then the edge group,
    # each in id order.
    kinds = [q.target_kind for q in queries]
    assert kinds == ["node"] * 10 + ["edge"] * 10, kinds
    for kind in ("node", "edge"):
        ids = [q.target_id for q in queries if q.target_kind == kind]
        assert ids == sorted(ids), kind


def test_the_vpc_side_of_an_attachment_is_marked_as_deduced():
    """The same value is used on two lines, and on one of them it is recorded as
    a deduction.

    What the metric's dimensions name is the TGW and the attachment. That those
    bytes crossed the VPC boundary is a deduction about the attachment and the
    VPC, so it has to be written into the data for the screen to say so with a
    badge. Copying it across with no marking at all is what the discarded
    endpoint inference did.
    """
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    queries, _ = plan(state)
    deduced = {q.target_id: q.observed_on for q in queries if q.observed_on is not None}
    assert deduced == {ATTACH_VPC_EDGE: ATTACH_ID}


def test_identical_series_are_bought_once():
    """The bill is by the number of metrics requested, so sending the same
    series three times is paid for three times.

    The series of one attachment answers for three targets: the attachment box,
    the line on the TGW side and the line on the VPC side. Without folding, each
    attachment buys twelve metrics rather than four, and the screen looks
    identical, so nobody notices.
    What is folded is the request; the verdict stays per target, and all three
    targets have to receive an answer.
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
    assert len(sent) == len(set(sent)), "the same series was bought twice"
    attachment = [s for s in sent if ("TransitGatewayAttachment", ATTACH) in s[2]]
    assert len(attachment) == 4, (
        attachment
    )  # BytesIn, BytesOut and two drops; not three targets times four
    for target_id in (ATTACH_ID, ATTACH_TGW_EDGE, ATTACH_VPC_EDGE):
        # Three targets share one series, so the throughput the default control
        # case gave a value to appears on all three.
        assert layer.readings_for(target_id)[_flow_metric(ATTACH_ID)].measured, (
            target_id
        )


def test_types_without_metrics_are_not_queried():
    """A type with no metrics, and a Gateway-type endpoint, are never asked
    about at all; only Interface endpoints are.
    """
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    queries, _ = plan(state)
    asked = {q.resource_type for q in queries}
    assert not (asked & NO_METRIC_TYPES)
    assert GATEWAY_ENDPOINT_ID not in {q.target_id for q in queries}


def test_a_missing_dimension_value_is_reported_not_guessed():
    """When a dimension cannot be built, do not call with an empty value; that
    comes out the same as there being no traffic.

    For an attachment whose structure tier gave no `TransitGatewayId`, the node
    and the line on the VPC side become targets that cannot be asked about. The
    line on the TGW side is drawn from that id, so it is absent from the
    structure entirely and `collect` cannot build it. Skipping them quietly
    leaves both looking as though they carry no traffic, and the person who has
    to fix it is a developer while the screen says nothing.
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
    # The structure-tier evidence is intact, so it is `up`, and that the metric
    # layer is missing stays in the reason.
    assert stuck["status"] == "up"
    assert stuck["partial"] is True
    assert [r["code"] for r in stuck["partial_reasons"]] == ["api_error"]
    _valid(document)


def test_request_pins_the_period_and_the_window():
    """A fixed `period=60`, a 900-second window and no ladder of retries.

    The window ends at the snapshot moment rather than at the wall clock;
    reading the wall clock would make the same input produce different
    documents. A ladder would make two calls, so the call count is measured
    alongside it.
    """
    cw = FakeCloudWatch(_series())
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    _layer(cw, state)
    assert len(cw.calls) == 1, (
        "a retry or a ladder appeared; do not raise the period and call again"
    )
    call = cw.calls[0]
    assert call["EndTime"].isoformat() == "2026-09-23T00:05:00+00:00"
    assert (call["EndTime"] - call["StartTime"]).total_seconds() == WINDOW_SECONDS
    assert WINDOW_SECONDS == 900, "the approved window is fifteen minutes"
    assert {q["MetricStat"]["Period"] for q in call["MetricDataQueries"]} == {
        PERIOD_SECONDS
    }
    assert PERIOD_SECONDS == 60
    # The NLB `ActiveFlowCount` is a gauge, so `Sum` inflates it by the number
    # of samples.
    stats = {
        q["MetricStat"]["Metric"]["MetricName"]: q["MetricStat"]["Stat"]
        for q in call["MetricDataQueries"]
    }
    assert stats["ActiveFlowCount"] == "Average"
    assert stats["RequestCount"] == "Sum"
    assert stats["PacketDropCountBlackhole"] == "Sum"


# --------------------------------------------------------------- the verdict


def test_flowing_targets_become_live():
    document, _ = _observe(FakeCloudWatch(_series()))
    observations = _by_id(document)
    # If the target is absent from the document the loop below never runs once
    # and passes, which is not a pass but an absence of measurement.
    assert set(FLOWING) <= set(observations), set(FLOWING) - set(observations)
    for target_id in FLOWING:
        observed = observations[target_id]
        assert observed["status"] == "live", observed
        # The evidence behind `live` is a metric; the schema requires `metric`
        # with at least one data point.
        assert observed["evidence"]["api"] == METRIC_API
        assert observed["evidence"]["metric"] == _flow_metric(target_id)
        assert observed["evidence"]["datapoints"] == 2  # non-zero points in FLOW_VALUES
        assert observed["evidence"]["window_seconds"] == WINDOW_SECONDS
        assert observed["partial"] is False
    _valid(document)
    assert document["layers"] == {"describe": True, "metric": True}


def test_the_window_is_folded_not_read_from_one_end():
    """The verdict folds the whole window rather than reading any single slot.

    An older version judged on the last data point that had a value, `[0]` of
    the newest-first array, so a one-minute flicker became the colour of the
    line: traffic for fourteen of fifteen minutes with a zero in the last one
    came out grey. Reading the array from the other end instead describes now
    with a value from fifteen minutes ago. Folding makes the direction of the
    sort irrelevant.
    """
    both_ends_zero = [(NAT_ID, "BytesOutToDestination", [0.0, 4096.0, 0.0])]
    document, _ = _observe(FakeCloudWatch(_series(*both_ends_zero)))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "live", (
        "there is throughput inside the window and yet it came out grey"
    )
    assert observed["evidence"]["datapoints"] == 1
    # The same series reversed gives the same verdict, which is what it means
    # not to rely on the ordering.
    reversed_series = [(NAT_ID, "BytesOutToDestination", [0.0, 4096.0, 0.0][::-1])]
    flipped, _ = _observe(FakeCloudWatch(_series(*reversed_series)))
    assert _by_id(flipped)[NAT_ID] == observed


def test_live_evidence_names_the_first_and_last_flowing_minute():
    """The evidence behind `live` carries the first and last non-zero minute,
    which is the material the history is built from.

    FLOW_VALUES is [4096, 0, 2048] newest first from 00:05, so 00:04, 00:03 and
    00:02. The first is 00:02 and the last is 00:04. The zero in the middle has
    to be skipped; reading the ends of the array gets one of the two wrong.
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
    """A row with no traffic has neither key. Not knowing and not having are
    both represented by one absence.
    """
    zeros = [(NAT_ID, "BytesOutToDestination", [0.0, 0.0, 0.0])]
    document, _ = _observe(FakeCloudWatch(_series(*zeros)))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up" and observed["evidence"]["datapoints"] == 0
    assert "first_flow_at" not in observed["evidence"]
    assert "last_flow_at" not in observed["evidence"]
    # What makes the premise meaningful: a row with traffic in the same document
    # does have the keys.
    assert any(
        "last_flow_at" in (o["evidence"] or {}) for o in document["observations"]
    )


def test_flow_minutes_do_not_depend_on_the_scan_order():
    """Measure the minimum and the maximum, so a reversed `ScanBy` still gives
    the same minutes. Same discipline as the verdict.
    """
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
        None,  # the response carries no `Timestamps`
        ["2026-09-23T00:04:00Z"],  # they do not pair up, so which value belongs
        # to which minute is unknown
        [
            "2026-09-23T00:04:00",
            "2026-09-23T00:03:00",
        ],  # no time zone, and UTC is not assumed
        ["not-a-time", "2026-09-23T00:03:00Z"],  # an element that cannot be read
    ],
)
def test_unusable_timestamps_leave_the_minutes_out_but_keep_the_verdict(stamps):
    """The verdict stands even when the timestamps cannot be read, because they
    are not material the verdict is built from.
    """
    ref = MetricRef("BytesOutToDestination", "Sum", AGG_SUM, ROLE_FLOW, REPORT_ALWAYS)
    reading = read_series([4096.0, 2048.0], ref, stamps)
    assert (reading.first_nonzero_at, reading.last_nonzero_at) == (None, None)
    assert (reading.points, reading.nonzero, reading.value) == (2, 2, 6144.0)


def test_a_response_without_timestamps_is_still_live_and_valid():
    """With the timestamps missing from a real response the row is still `live`
    and still passes the schema; only the keys are absent.
    """
    document, _ = _observe(FakeCloudWatch(_series(), with_timestamps=False))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "live"
    assert "first_flow_at" not in observed["evidence"]
    _valid(document)


def test_a_series_of_zeros_stays_up_and_says_it_measured():
    """Measured, with no traffic: `up` with `datapoints: 0`. That states a
    different fact from `null`, which means it was not measured.
    """
    document, _ = _observe(
        FakeCloudWatch(_series((NAT_ID, "BytesOutToDestination", [0.0, 0.0, 0.0])))
    )
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["evidence"]["datapoints"] == 0
    assert observed["evidence"]["metric"] == "BytesOutToDestination"
    assert observed["partial"] is False


def test_an_empty_series_does_not_overwrite_layer_one():
    """The approved contract: an empty array means unconfirmed and does not
    override an `up` from the structure tier.

    This is the case of a target that exists in the structure but has no
    metrics, where the right answer is `up` with `datapoints` of `null`. It is
    not `partial` either: the evidence is not incomplete, that target simply has
    no metric, and writing `partial` would have the screen claim that fixing a
    permission would reveal it.

    The subject of this test moved from an endpoint to a NAT gateway. An empty
    `BytesProcessed` response on an accepted endpoint now means measured and
    zero. What remains the subject of this contract is an empty response from a
    NAT gateway, which publishes zero values too, and that is exactly the shape
    that arrives when a dimension is wrong.
    """
    document, _ = _observe(FakeCloudWatch(_series(flowing=(TGW_ID,))))
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    # With nothing measured at all the name is the first throughput metric that
    # was asked for, which is the last branch of `Verdict.report_metric`.
    assert observed["evidence"]["metric"] == "BytesInFromSource"
    assert observed["evidence"]["datapoints"] is None, (
        "an empty array was recorded as zero"
    )
    assert "값 0 도 발행하는 지표" in (observed["why"] or ""), observed
    assert observed["partial"] is False
    assert "partial_reasons" not in observed
    _valid(document)


def test_drops_do_not_deny_a_flow():
    """A drop above zero does not negate traffic.

    What somebody sees otherwise: every span that carries traffic normally while
    discarding some packets, through MTU, TTL or policy, turns into a red dashed
    line. Then it cannot be told apart from a real block, and the red on the
    screen loses its credibility.
    """
    document, _ = _observe(
        FakeCloudWatch(_series((NAT_ID, "PacketsDropCount", [12.0, 3.0])))
    )
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "live"
    assert observed["evidence"]["metric"] == "BytesOutToDestination"
    assert observed["why"] is None


def test_zero_flow_with_drops_is_down_and_names_the_drop():
    """No throughput with drops above zero gives `down`, and the name and the
    number have to be the drop's.

    If a block is stated from a drop while `evidence` records a throughput name
    and a zero, somebody opens that metric in the console, reads zero, and
    concludes that nothing happened. That is a red nobody can reproduce.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(
                # This check measures something only if there is a series that
                #    measured `BytesOut` as zero. With arrivals alone there is
                #    no throughput name at all, and a regression that records a
                #    throughput name would pass.
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
    assert "900" in observed["why"], (
        "without the window length in the reason it cannot be reproduced"
    )
    _valid(document)


def test_a_new_nat_that_dropped_packets_is_not_called_blocked():
    """Do not state a block from a NAT gateway's discard count alone.

    What somebody sees otherwise: a freshly created NAT gateway reports 49
    packets on `PacketsDropCount` in the three minutes after creation, while its
    throughput is zero because nobody is using it yet. The old rule, no
    throughput with drops above zero meaning blocked, draws that healthy NAT
    gateway as a red block, and the customer reads what they have just created
    as broken. The one signal that can state a block on a NAT gateway is port
    exhaustion.

    The control case is what gives this check its power: the same shape with a
    positive `ErrorPortAllocation` is `down`, and that is the name recorded in
    the reason. If the two gave the same result this check would measure
    nothing.
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
    assert observed["status"] == "up", (
        "a healthy, freshly created NAT gateway is drawn as blocked"
    )
    # On a tie, both zero, the earlier name wins. That is the determinism rule
    # of `_fold`.
    assert observed["evidence"]["metric"] == "BytesInFromSource", observed
    # `datapoints` counts the non-zero points, so measured and all zero gives
    # `0` while not measured gives `None`.
    assert observed["evidence"]["datapoints"] == 0, (
        "it was recorded as not having been measured"
    )
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
        "stating a block while recording a badge-only drop name cannot be "
        "reproduced in the console"
    )
    assert "ErrorPortAllocation" in exhausted["why"]
    assert "PacketsDropCount" not in (exhausted["why"] or "")


def test_the_adr_says_nat_packet_drops_are_not_a_blocking_signal():
    """The `blocking` declaration in the code against the D3 table, which closes
    the hole where both could be wrong together.

    With only the check above, the code could be changed to make it pass. The
    design document is the source of truth, so this matches the code's
    declaration against the fact that the NAT row of D3 says the discard count
    is excluded from the blocking signals, within one check. Flipping either
    side alone fails.
    """
    rows = [line for line in _adr8().splitlines() if line.startswith("| NAT GW |")]
    assert len(rows) == 1, f"the NAT row of the D3 table is not unique: {rows}"
    assert "`ErrorPortAllocation` (Sum)만" in rows[0], rows[0]
    assert "`PacketsDropCount` 는 D 신호에서 뺀다" in rows[0], rows[0]
    by_name = {ref.name: ref for ref in NODE_PROBES["nat_gateway"].metrics}
    assert by_name["ErrorPortAllocation"].blocking is True
    assert by_name["PacketsDropCount"].blocking is False, (
        "the design document says badge-only while the code uses it as a "
        "blocking signal"
    )


def test_the_drop_count_lands_in_the_row_not_only_in_the_why_line():
    """The engine fills the `drops` field that the drop badge reads.

    What somebody sees: a line with observed traffic carries both green and a
    drop count. Without this field the only way the screen could state a drop is
    the `why` sentence, and a screen that extracts a number out of a sentence is
    not a contract. The renderer reads `row.drops`.
    (`renderer/flow.js` `wireBadge`·`showWirePanel`).

    Every grade path is measured, because `drops` states a fact independent of
    the grade. Filling it on one path only makes the badge quietly disappear from
    the rows of every other grade. And the three values state three different
    facts: `15` means measured as 15, `0` means measured as zero, and a missing
    key means either not measured or no drop metric at all. The two control
    cases are what give this check its power: measuring only the missing key
    would let "never fill it on any row" pass, and not measuring zero would let
    "delete the key when it is zero" pass.
    """
    # First, `live` with drops above zero, carrying traffic while discarding
    # some. Second, `live` with measured and zero. Third, a drop metric that is
    # quiet.
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
    assert rows[NAT_ID]["drops"] == 15, "it has to be the window total, 12 + 3"
    assert rows[TGW_ID]["drops"] == 0, (
        "measured and zero was flattened into a missing key"
    )
    assert "drops" not in rows[ENDPOINT_ID], (
        "a zero was recorded while the drop metric was quiet, which makes no "
        "drops and not measured the same thing"
    )
    _valid(document)

    # Fourth, the `down` path: a row that states a block keeps that number too,
    # the same number as in the reason.
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

    # Fifth, the no-traffic path, `up` with zero throughput: the badge-only drop
    #    of a freshly created NAT gateway. The grade does not change, but the
    #    screen still has to be able to say why something is grey and yet
    #    discarding packets.
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
    assert new_nat["status"] == "up", "the premise changed; this path has to be `up`"
    assert new_nat["drops"] == 49
    _valid(quiet)


def test_two_drop_metrics_on_one_target_report_the_larger_not_the_sum():
    """`drops` is the window total of one drop metric; the metrics are not added
    together.

    A TGW counts blackholed packets and packets with no route separately. Adding
    the two gives a drop count on screen that matches no metric in the console,
    so nobody can follow it. The number has to match the metric the blocking
    reason names. The schema records this rule in the description of `drops`.
    """
    # First, carrying traffic while discarding in two ways: the larger of 70
    # and 30.
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
        "the two drop metrics were added, 70 + 30, which matches no metric in "
        "the console"
    )
    _valid(flowing)

    # Second, blocked: the metric and the number in the reason match `drops`.
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
    """A gauge folds to the maximum over the window. Folding it to a sum gives
    numbers like 3,600 flows in one minute.

    `_fold` picks the flow metric with the largest value, so folding a gauge to
    a sum promotes that inflated number into the evidence behind the verdict and
    puts its name in `evidence.metric`. Somebody checking it in the console sees
    a different number.
    """
    nlb = _active_nlb()
    cw = FakeCloudWatch(
        _series((NLB_ID, "ActiveFlowCount", [3.0, 5.0, 4.0]), flowing=(NAT_ID,)),
    )
    document, layer = _observe(cw, load_balancers=nlb)
    reading = layer.readings_for(NLB_ID)["ActiveFlowCount"]
    assert reading.aggregate == AGG_MAX
    assert reading.value == 5.0, "it folded to the sum, 12"
    assert _by_id(document)[NLB_ID]["status"] == "live"


def test_fold_folds_counters_by_sum_and_state_by_max():
    """A unit check on `_fold`: the folding rule for each role.

    No probe uses the state or health roles yet, as the ratchet above records,
    so this is the only place they are measured. State folds to the maximum over
    the window, so a block requires zero throughout; health folds to the
    minimum, so a single zero slot means there was a span with nothing healthy.
    Reversing the two turns every maintenance window and reconvergence into a
    red line.
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
    assert not state_up.blocked, (
        "a single 1 anywhere in the window means it is not blocked"
    )

    health_gone = _fold(
        {
            "HealthyHostCount": Reading(
                "HealthyHostCount", ROLE_HEALTH, AGG_MIN, 15, 9, 0.0, REPORT_CONDITIONAL
            )
        }
    )
    assert health_gone.blocked

    # An empty array means it was not measured, which is not a block. Do not
    # promote not knowing into broken.
    unmeasured = _fold(
        {
            "TunnelState": Reading(
                "TunnelState", ROLE_STATE, AGG_MAX, 0, 0, None, REPORT_ALWAYS
            )
        }
    )
    assert not unmeasured.measured and not unmeasured.blocked


def test_report_metric_never_borrows_a_health_name_for_a_silent_flow():
    """Even when no throughput was measured at all, the name in the evidence is
    a throughput metric.

    What somebody sees otherwise: the traffic field on screen reads unconfirmed
    while the evidence names `HealthyHostCount`, so they open that metric in the
    console and wonder why it is unconfirmed when the targets are healthy. The
    name has to say what we asked about, and what we asked about was throughput.
    """
    verdict = _fold(
        {
            # Alphabetically this health metric comes first, so a
            # `metric_names[0]` fallback would pick it.
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
    assert verdict.measured, (
        "a health metric was measured, so the target as a whole was measured"
    )
    assert not verdict.flowing and not verdict.blocked
    assert verdict.report_metric == "ProcessedBytes", verdict


def test_a_silent_flow_metric_is_null_even_when_another_series_answered():
    """Do not record throughput as measured just because another series on the
    same target answered.

    What somebody sees otherwise: with only the drop series answering for an
    endpoint and not one point of throughput, an older version wrote zero data
    points in that field, and the screen reads that as settled proof that there
    was no traffic in the span. What actually happened is that we never saw the
    metric, so the number has to be empty and the reason has to say what to do
    next: whether to wait or whether this is ours to fix.

    The control case is what gives this check its power. With the same `up` but
    the throughput really measured, a `0` is written and there is no reason. If
    the two produced the same bytes this check would measure nothing.

    The subject of this test moved from a NAT gateway to an endpoint. A lab
    measurement made two NAT throughput metrics `always`, which reversed the
    meaning of no points arriving: on a NAT gateway, no points is now a defect
    of ours to fix, in the dimension combination or in publishing. What this
    test wants to measure, a metric that is not published so there is nothing to
    do but wait, holds only for a `nonzero_only` resource, and the two endpoint
    throughput metrics are that case.

    A later revision made a successful empty `BytesProcessed` response on an
    accepted endpoint mean measured and zero. So the only way left to produce
    "the throughput was never seen" is for that series' query to come back as
    something other than `Complete`, such as `Forbidden`. Filling that series
    with zero because the drop series answered would hide a denial as an absence
    of traffic.
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
    assert observed["evidence"]["datapoints"] is None, (
        "something not measured was recorded as measured and zero"
    )
    # The reason comes from the branch of `_quiet_why` about a metric that is
    # not published when its value is zero. The query `StatusCode` is not raised
    # as a separate reason, which is how it stands today.
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
    assert zeroed["evidence"]["datapoints"] == 0, (
        "measured and zero has to carry a number"
    )
    assert zeroed["why"] is None, (
        "it was measured, yet a not-measured reason was attached"
    )


def test_traffic_does_not_erase_a_down_verdict():
    """The metric layer only ever lifts an `up`. A failed NAT gateway or a
    `failed` load balancer stays `down` even when traffic is visible.

    What somebody sees otherwise: a broken resource turns green and disappears
    from the screen. Drawing "carrying traffic but broken" would need a fifth
    word in the vocabulary, and the design forbids one.
    """
    cw = FakeCloudWatch(_series(flowing=FLOWING + (DEAD_NAT_ID, NLB_ID)))
    document, _ = _observe(cw)
    for target_id in (DEAD_NAT_ID, NLB_ID):
        observed = _by_id(document)[target_id]
        assert observed["status"] == "down", observed
        assert observed["evidence"]["metric"] is None
        assert observed["evidence"]["api"] != METRIC_API
        assert observed["partial"] is False


# ------------------------------------------- metric-layer-only edges


def test_metric_only_edges_appear_only_when_the_metric_answered():
    """When it could not be measured there is no row.

    The difference somebody sees: with no row, that line stays marked as having
    only its path confirmed. Producing an `up` here would make it green with
    nothing behind it, which is the shape of the endpoint inference this design
    discarded.
    There are two control cases: a run with no metrics registered at all, and a
    run with them registered.
    """
    quiet, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    # Two exceptions: for the ALB placement line and the subnet span of an
    #    accepted endpoint, a successful empty response means measured and zero
    #    (`empty_is_zero`), so they do have a row. That row is `up` with
    #    `datapoints: 0`, meaning no traffic, rather than a green with nothing
    #    behind it.
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
    ], "a row was built although the metric layer never answered for that span"

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
    # Control case: another span of the same type has no series registered, so
    # it has no traffic. If the metric of an endpoint node bled into the subnet
    # span this would come out `live`, and that is the old endpoint inference.
    # Being an accepted endpoint, an empty response means measured and zero, so
    # the row exists with `up` and 0.
    assert rows[ENDPOINT_PRIVATE_EDGE]["status"] == "up", (
        "traffic bled into a series that was never registered"
    )
    assert rows[ENDPOINT_PRIVATE_EDGE]["evidence"]["datapoints"] == 0
    # For the ALB placement line an empty response means measured and zero
    # (`empty_is_zero`), so the row exists without traffic bleeding in from the
    # neighbouring span: `up` with 0.
    assert rows[ALB_PRIVATE_EDGE]["status"] == "up"
    assert rows[ALB_PRIVATE_EDGE]["evidence"]["datapoints"] == 0
    _valid(document)


def test_a_measured_span_that_carried_nothing_is_up_with_zero():
    """A span that was measured and came out zero has a row, with `up` and
    `datapoints: 0`.

    Having no row, meaning it could not be measured, and `up` with 0, meaning it
    was measured and quiet, have to look different on screen. The first says
    only the path is confirmed and the second says there is no traffic right
    now. A customer would ask a different question about each.
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
    """`evidence.observed_on` is written into the document.

    One series turns two lines green: on one of them the metric named that span
    directly, and on the other it is a deduction. Without that difference in the
    data the screen cannot state it with a badge and the two lines are
    indistinguishable, and losing that distinction is the discarded endpoint
    inference.
    """
    document, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID, ATTACH_ID))))
    rows = _by_id(document)
    direct, deduced = rows[ATTACH_TGW_EDGE], rows[ATTACH_VPC_EDGE]
    assert direct["status"] == deduced["status"] == "live"
    assert direct["evidence"].get("observed_on") is None, (
        "a direct measurement was marked as a deduction"
    )
    assert deduced["evidence"]["observed_on"] == ATTACH_ID
    # The attachment node itself goes green from the same series, so the screen
    # does not end up with the box alone in grey.
    assert rows[ATTACH_ID]["status"] == "live"
    _valid(document)


def test_a_metric_only_edge_can_be_down():
    """When a span metric states a block, that line is `down`.

    What somebody sees: the attachment span that gained a blackhole route turns
    into a dashed line with a warning mark. Without this path a block is visible
    only on the node, and which span is blocked never reaches the screen.
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
# Separating what arrived from what got through. This is the defect where a
# blackholed span in the lab came out green.
# ─────────────────────────────────────────────────────────────────────────────
ARRIVED = [210.0, 210.0]  # window total 420, the `BytesIn` measured in the lab
ZERO = [0.0, 0.0]


def _tgw_family(
    target_id: str,
    *,
    out: list[float],
    blackhole: list[float] | None,
    noroute: list[float] | None,
) -> tuple[tuple[str, str, list[float]], ...]:
    """One set of TGW series. Passing `None` for the drops gives an empty array,
    meaning they could not be measured.
    """
    rows = [(target_id, "BytesIn", ARRIVED), (target_id, "BytesOut", out)]
    if blackhole is not None:
        rows.append((target_id, "PacketDropCountBlackhole", blackhole))
    if noroute is not None:
        rows.append((target_id, "PacketDropCountNoRoute", noroute))
    return tuple(rows)


def test_traffic_that_only_arrived_and_was_blackholed_is_down_on_card_and_line():
    """Everything blackholed means blocked, on the card, the attachment card and
    the attachment line alike.

    What somebody sees otherwise: as in the lab, traffic arriving at the TGW is
    discarded into a blackhole every minute, and an older version read the
    `BytesIn` of 420 as traffic and drew the line green. The reason has to carry
    the amount that arrived, so that somebody reads it as something having been
    sent but not having got through.
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
    """One-way sending is traffic. Defining traffic as `BytesOut` above zero
    alone gets this wrong.

    What somebody sees otherwise: an attachment that only sends from a VPC to an
    on-premises syslog has `BytesIn` above zero and `BytesOut` at zero. With the
    blocking drops measured at zero, nothing was discarded, so it got through.
    Recording that as no traffic turns a perfectly healthy logging path grey.
    The name in the evidence is `BytesIn`, the metric that actually has a value.
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
    """Something arrived but the discards could not be measured, which is
    unjudgeable: neither traffic nor an absence of traffic.

    What somebody sees otherwise: recorded as traffic, a line that might be
    blackholed comes out green; recorded as no traffic, the 420 bytes that did
    arrive are asserted never to have existed. So no number is written
    (`datapoints: null`) and the reason is stated instead.

    The control case gives this its power: in the same shape with the drops
    measured at zero it is `live`, as the check above shows, and a single
    missing series
    leaves it unjudgeable, because the condition is both measured at zero.
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
    """The TGW card measures packets discarded for having no route as well. The
    older card read only the blackholed ones.

    What somebody saw: a TGW discarding everything because it had no route came
    out green on its card, since the blackhole count was zero.
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
    """A unit check on `_fold`: a blocking drop series missing from the response
    (`incomplete`) is not the same as measured at zero.

    Unlike an empty array (`points == 0`), a series missing from the response is
    absent from `readings` entirely. Recording traffic on the strength of the
    remaining drops all being zero ignores that the missing series might have
    been reporting everything discarded.
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
    assert _fold(readings).flowing, (
        "control case: with every series returned, one-way sending is traffic"
    )
    partial = _fold(readings, complete=False)
    assert not partial.flowing and not partial.blocked
    assert partial.arrival_unproven
    assert partial.report_metric == "BytesIn"


def test_the_adr_flow_rule_for_tgw_matches_the_declarations():
    """The `arrival_only` declaration in the code against the two TGW rows of
    the D2 table, which closes the hole where both could be wrong together.

    One correction changed only the table and the code never followed, which is
    why a blackholed span stayed green for two days. This check matches, in one
    place, that the rule is written in the table and that the code declares it
    the same way.
    """
    rule = "**`BytesOut` > 0, 또는 `BytesIn` > 0 이면서 차단 드롭을 재서 0**"
    for label in ("| **TGW** |", "| **TGW 어태치먼트 = 구간** |"):
        rows = [line for line in _adr8().splitlines() if line.startswith(label)]
        assert len(rows) == 1, f"the {label} row of the D2 table is not unique: {rows}"
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
    """What tells them apart: an ALB placement span cannot produce `down`,
    because it has no blocking metric.

    This pins down once more that zero throughput is not itself a block. The
    control case on the other side is an NLB placement, which does have
    `RejectedFlowCount` and so has to come out `down` under the same conditions.
    To somebody looking at the screen: the ALB simply has no traffic, while the
    NLB is rejecting.
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


# ---- An empty response from an ALB means no traffic ------------------------
# AWS sends no point for the `ProcessedBytes` of an ALB when it is zero, which
# the documentation states and a real account confirmed. Before this was fixed
# such an empty response meant unconfirmed, and the placement line lost its row
# entirely, so no traffic stop was left in the history.
# A failure to read, a denied call or a query that comes back Forbidden, still
# means unconfirmed. Recording it as zero would hide a denial as an absence of
# traffic.

ALB_FLOW_KEYS = tuple(
    _key(t, "ProcessedBytes") for t in (ALB_ID, ALB_PUBLIC_EDGE, ALB_PRIVATE_EDGE)
)


def test_a_silent_alb_is_up_with_no_flow():
    """A successful empty response leaves both the ALB node and the placement
    line at `up` with `datapoints: 0`, meaning no traffic, and no reason.
    """
    document, layer = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    rows = _by_id(document)
    for target in (ALB_ID, ALB_PUBLIC_EDGE, ALB_PRIVATE_EDGE):
        row = rows[target]
        assert row["status"] == "up", row
        assert row["partial"] is False
        assert row["evidence"]["metric"] == "ProcessedBytes"
        assert row["evidence"]["datapoints"] == 0, (
            "measured and zero, not null, which would mean not measured"
        )
        assert row.get("why") is None, row
    # No points are invented; the number received stays zero.
    reading = layer.readings[(ALB_ID, "ProcessedBytes")]
    assert reading.points == 0 and reading.zero_filled and reading.value == 0.0
    # Control case: for the `always` metrics in the same document, the NAT
    #    gateway with no values registered and the TGW, an empty response still
    #    means unconfirmed. An accepted endpoint now follows the same rule as
    #    the ALB; see the accepted-endpoint section below.
    assert rows[TGW_ID]["evidence"]["datapoints"] is None
    _valid(document)


def test_a_forbidden_alb_query_is_not_read_as_zero():
    """The call succeeded but that series came back `Forbidden`. The array is
    empty, yet this is not measured and zero: it is unconfirmed, and the
    placement line gets no row.
    """
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
    """The metric layer is blocked with AccessDenied. The ALB too comes out
    `partial` with a reason and no number, and the placement line gets no row,
    because coverage is what states the block.
    """
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
    """An ALB going from `live` to an empty response records traffic stopping,
    `live → up`, as exactly one row for the node and one for the placement line,
    and the next empty run adds none.

    Before this was fixed the placement line lost its row, since an absence on a
    metric-layer-only connection meant no measurement, so that line's stop never
    reached the history.
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
    # While it stays quiet there are no new rows, since `up` follows `up`.
    third, _ = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID,))), at="2026-09-23T00:20:00Z"
    )
    again = apply_history(third, after)
    assert again.new_rows == 0, third["changes"]


# ---- An empty response from an accepted PrivateLink endpoint means no traffic
# AWS publishes `AWS/PrivateLinkEndpoints` only for a minute in which the
# endpoint received traffic, the same shape as the ALB. Before this was fixed
# such an empty response meant unconfirmed, and the subnet span lost its row
# entirely, so no traffic stop was left in the history.
# An endpoint that has not been accepted yet, in `pendingAcceptance` or
# similar, reports zero points just the same. Calling that no traffic too would
# leave an endpoint that cannot receive traffic quietly looking healthy. So what
# decides this is the structure-tier state rather than the metric.

ENDPOINT_FLOW_KEYS = tuple(
    _key(t, "BytesProcessed")
    for t in (ENDPOINT_ID, ENDPOINT_PUBLIC_EDGE, ENDPOINT_PRIVATE_EDGE)
)
ENDPOINT_TARGETS = (ENDPOINT_ID, ENDPOINT_PUBLIC_EDGE, ENDPOINT_PRIVATE_EDGE)


def _endpoints_in(state: str) -> list[dict[str, Any]]:
    """A response with only the `State` of one interface endpoint changed; the
    gateway endpoint is left alone.
    """
    endpoints = copy.deepcopy(VPC_ENDPOINTS)
    for endpoint in endpoints:
        if endpoint["VpcEndpointType"] == "Interface":
            endpoint["State"] = state
    return endpoints


def test_accepted_endpoint_probes_differ_only_in_reading_the_empty_answer():
    """The accepted case differs in its interpretation alone. Two sets of
    dimensions, metrics or publishing rules would leave one of them getting
    fixed on its own.
    """
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
        # The drops are not filled with zero. Filling them would make
        # "the drops were measured at zero" a claim with nothing behind it.
        assert not any(r.empty_is_zero for r in base.metrics), key
        assert all(
            a.reporting == b.reporting and a.role == b.role and a.blocking == b.blocking
            for a, b in zip(accepted.metrics, base.metrics, strict=True)
        ), key


def test_a_silent_accepted_endpoint_is_up_with_no_flow():
    """Accepted, with a successful empty response: both the endpoint node and
    the subnet span come out `up` with `datapoints: 0`, meaning no traffic, and
    no reason.
    """
    document, layer = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    rows = _by_id(document)
    for target in ENDPOINT_TARGETS:
        row = rows[target]
        assert row["status"] == "up", row
        assert row["partial"] is False
        assert row["evidence"]["metric"] == "BytesProcessed"
        assert row["evidence"]["datapoints"] == 0, (
            "measured and zero, not null, which would mean not measured"
        )
        assert row.get("why") is None, row
    reading = layer.readings[(ENDPOINT_ID, "BytesProcessed")]
    assert reading.points == 0 and reading.zero_filled and reading.value == 0.0
    # The verdict rests on `BytesProcessed`; the other series are not invented
    # as zeros.
    assert not layer.readings[(ENDPOINT_ID, "NewConnections")].zero_filled
    assert not layer.readings[(ENDPOINT_ID, "PacketsDropped")].zero_filled
    _valid(document)


def test_an_accepted_endpoint_that_went_silent_lists_one_flow_stop_each():
    """An accepted endpoint going from `live` to an empty response records
    traffic stopping, `live → up`, as exactly one row for the node and one for
    the span, and the next empty run adds none.

    Before this was fixed the span lost its row, since an absence on a
    metric-layer-only connection meant no measurement, so that line's stop never
    reached the history.
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
    assert len(rows) == len(stops), rows  # no other rows, no appearances or
    # disappearances
    assert report.unmeasured == 0, report
    _valid(after)
    third, _ = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID,))), at="2026-09-23T00:20:00Z"
    )
    again = apply_history(third, after)
    assert again.new_rows == 0, third["changes"]


# `rejected`, `expired` and `deleted` have their whole node dropped by the
# structure tier through `GONE_STATES` in `services/base.py`, so there is no row
# to judge in the first place.
@pytest.mark.parametrize("state", ["pendingAcceptance", "pending", "failed", "partial"])
def test_a_pending_endpoint_with_no_points_is_unknown(state):
    """An endpoint that is not yet accepted, or has failed, with an empty
    response does not mean no traffic: the span gets no row, leaving only its
    path confirmed, and the node is described by its structure-tier state.

    What somebody sees otherwise: an endpoint waiting for acceptance appears to
    have no traffic right now, and the customer reads that as being connected
    and merely quiet. In fact that endpoint cannot receive traffic yet, and an
    endpoint awaiting acceptance reports zero points just the same.
    """
    endpoints = _endpoints_in(state)
    document, layer = _observe(
        FakeCloudWatch(_series(flowing=(NAT_ID,))), vpc_endpoints=endpoints
    )
    rows = _by_id(document)
    for edge in (ENDPOINT_PUBLIC_EDGE, ENDPOINT_PRIVATE_EDGE):
        assert edge not in rows, (
            (
                "a no-traffic row was built for the span of an endpoint that "
                "has not been accepted"
            ),
            rows[edge],
        )
    node = rows[ENDPOINT_ID]
    assert node["status"] != "up", (
        node
    )  # the structure tier (`NODE_RULES`) decides this; it is not no traffic
    # (`up` with 0)
    assert node["evidence"].get("datapoints") is None, node
    assert state in (node.get("why") or ""), node
    assert not layer.readings[(ENDPOINT_ID, "BytesProcessed")].zero_filled
    # The next run has the same state and the same empty response, so the
    # history holds no row at all for that endpoint.
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
    """The selection itself: `Available`, as the documentation spells it, and
    `available`, as a real account returned it, both take the accepted branch,
    while a missing state takes the default one.
    """
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
    """Even when accepted, a series that comes back `Forbidden` makes an empty
    array something other than measured and zero: no number, and no row for the
    span.
    """
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
    """The metric layer is blocked with AccessDenied. An accepted endpoint too
    comes out `partial` with a reason, no number, and no row for the span.
    """
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
    """A defect of ours must not be able to stay quiet, and with no row it would
    surface nowhere.

    For a metric-layer-only edge, having no row is the normal case, meaning it
    could not be measured. So leaving out the row when a dimension could not be
    built would disguise a defect of ours as having only the path confirmed. In
    that case a row is built with `none` and a reason.
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
    # Control case: another span in the same document is fine. With everything
    # at `none` this check could tell nothing apart.
    assert rows[ENDPOINT_PUBLIC_EDGE]["status"] == "live"
    _valid(document)


def test_other_edges_never_get_a_metric():
    """A line outside the three metric-layer-only types receives no metrics.

    Putting `live` on a route or association line gives it a value from nowhere:
    it is a node's value copied onto a line, which is endpoint inference.
    """
    document, _ = _observe(FakeCloudWatch(_series()))
    others = [
        o
        for o in document["observations"]
        if o["target_kind"] == "edge"
        and o["target_id"].split("|")[0] not in METRIC_ONLY_EDGES
    ]
    assert others, "with no edge observations at all the check below measures nothing"
    for observed in others:
        assert observed["status"] != "live", observed["target_id"]
        assert (observed["evidence"] or {}).get("metric") is None


# In the history, a metric-layer-only connection appearing or disappearing is
# not a configuration change. Those rows exist only when something was
# measured, so reading their presence as a change floods the list with
# seventeen connections appearing and disappearing every time live monitoring is
# turned on and off, plus another row or two whenever traffic stops.
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
    assert len(edges) >= 3, (
        "premise: only one of the runs may have metric-layer-only connection rows"
    )

    report = apply_history(after, before)

    assert after["changes"] == [], [
        (c["kind"], c["target_id"]) for c in after["changes"]
    ]
    # The `live` and `up` of a node with traffic drops out under the same cell
    # rule, so that covers every connection row plus those nodes.
    assert report.layer_changed >= len(edges), report
    assert report.unmeasured == 0, report
    _valid(after)


def test_a_connection_that_went_quiet_is_not_listed_as_removed():
    before, _ = _observe(FakeCloudWatch(_series(flowing=EDGES_TOO)))
    after, _ = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))), at=LATER_AT)
    quiet = sorted(set(_metric_only(before)) - set(_metric_only(after)))
    # Two attachment lines, where an empty response on an `always` metric means
    # unconfirmed and so no row. For the ALB placement line and the accepted
    # endpoint span an empty response means measured and zero, so their rows
    # remain, and those two are the traffic-stop side of the control case below.
    assert len(quiet) >= 2, (
        "premise: this run needs connection rows that disappeared for want of a value"
    )

    report = apply_history(after, before)

    kinds = {(c["kind"], c["target_id"].split("|")[0]) for c in after["changes"]}
    assert not {k for k in kinds if k[0] != "status_changed"}, kinds
    assert report.unmeasured == len(quiet), report
    assert report.unknown_cell == 0, report
    # Control case: the row of the owning resource still states the stop.
    # Without this, the guard above would pass even if it deleted the record of
    # traffic stopping altogether.
    stopped = {
        c["target_id"]
        for c in after["changes"]
        if (c["kind"], c["from"], c["to"]) == ("status_changed", "live", "up")
    }
    assert ATTACH_ID in stopped, stopped
    assert {ALB_PUBLIC_EDGE, ENDPOINT_PUBLIC_EDGE} <= stopped, stopped
    _valid(after)


def test_a_transit_gateway_link_that_stopped_is_recorded():
    """A TGW attachment line has to find its cell in the history too, under the
    same rule that `history.target_cells` and `observe` share.

    What somebody sees otherwise: without a cell, that line's stop or start is
    only counted as having an unknown cell and never reaches the list. From the
    attachment node's row alone there is no telling which side stopped.
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
    assert _by_id(after)[ATTACH_TGW_EDGE]["status"] == "up", (
        "premise: it has to be measured and zero"
    )

    report = apply_history(after, before)

    moved = {
        (c["target_id"], c["from"], c["to"])
        for c in after["changes"]
        if c["kind"] == "status_changed"
    }
    assert (ATTACH_TGW_EDGE, "live", "up") in moved, moved
    assert report.unknown_cell == 0, report
    _valid(after)


# ------------------------------------------------------------- failure paths


def test_a_denied_metric_layer_keeps_up_and_records_why():
    """Only the metric layer is blocked, which the schema describes as `up` with
    `partial` and a reason.

    Dropping it to `none` would mix it up with being structurally unobservable,
    and writing `up` alone would make the metric-layer failure disappear. The
    structure-tier verdict has to stay alive, since the target did not go away.
    """
    cw = FakeCloudWatch(_series(), error=client_error("AccessDenied", "GetMetricData"))
    document, layer = _observe(cw)
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["partial"] is True
    assert [r["code"] for r in observed["partial_reasons"]] == ["access_denied"]
    assert observed["partial_reasons"][0]["api"] == METRIC_API
    assert observed["evidence"]["metric"] is None
    # Coverage records the same failure for every type, because the metric layer
    # dies as a whole layer.
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["status"] == "denied"
    assert rows["nat_gateway"]["reason"]["code"] == "access_denied"
    assert rows["nat_gateway"]["item_count"] is None
    # A type with no metrics was never called, so it was not blocked. Do not
    # turn it red along with the rest.
    assert rows["vpc"]["status"] == "ok"
    assert layer.enabled is True
    # A metric-layer-only edge has no row; coverage is what states the block,
    # as the `denied` above shows.
    assert not [
        o
        for o in document["observations"]
        if o["target_id"].split("|")[0] in METRIC_ONLY_EDGES
    ]
    _valid(document)


def test_credentials_failure_is_a_layer_failure_not_a_crash():
    """Failing to build a client for want of credentials is also the metric layer
    dying, and it does not kill the whole run.
    """
    document, _ = _observe(
        FakeCloudWatch(_series()),
        fail={"cloudwatch": client_error("ExpiredToken", "AssumeRole")},
    )
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["partial"] is True
    assert _metric_rows(document)["nat_gateway"]["status"] != "ok"


def test_metric_client_factory_failure_names_the_api_that_actually_failed():
    """Failing to enter the metric layer is not `GetMetricData`. A real account
    caught this one.

    What happened on the screen: even after deploying `cloudwatch:GetMetricData`
    into the member account role, that account's cell still said it was
    unconfirmed because of `cloudwatch:GetMetricData`. The real cause was that
    the hub-side metric-layer credentials lacked `sts:AssumeRole`, so no amount
    of editing the target account policy changed the screen.
    `test_a_denied_metric_layer_keeps_up_and_records_why` above is the control
    case: there the `get_metric_data` call itself is blocked, so it has to keep
    naming `METRIC_API`.
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
    """For a failure whose API name is unknown, anything that is not a
    `ClientError`, do not guess.

    Writing `sts:None` or `cloudwatch:GetMetricData` would both be false and
    would send somebody after an action or a policy that does not exist. The
    honest name here is that our own factory died.
    """
    document, _ = _observe(
        FakeCloudWatch(_series()),
        fail={"cloudwatch": RuntimeError("profile not found")},
    )
    row = _metric_rows(document)["nat_gateway"]
    assert row["status"] != "ok"
    assert row["reason"]["api"] == "nettopo:client_factory"


def test_all_targets_empty_is_a_defect_not_a_quiet_network():
    """With at least one target and not a single data point above zero,
    something is wrong.

    A wrong dimension name and genuinely having no traffic at all are identical
    byte for byte: both give `Complete` with no `Messages`. Reading the first as
    the second and passing turns this layer into a check that cannot fail.
    """
    document, layer = _observe(FakeCloudWatch({}))  # no series registered at all
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["status"] == "error"
    assert rows["nat_gateway"]["reason"]["code"] == "api_error"
    assert any("전부 지점 0개" in w for w in layer.warnings)
    observed = _by_id(document)[NAT_ID]
    assert observed["status"] == "up"
    assert observed["partial"] is True
    _valid(document)


def test_one_answered_target_is_not_a_defect():
    """Control case: with even one answer, the all-zero rule does not fire. This
    is what confirms the check can tell them apart.
    """
    _document, layer = _observe(FakeCloudWatch(_series(flowing=(NAT_ID,))))
    assert layer.failures == {}
    assert layer.warnings == ()


def test_a_missing_result_id_is_reported():
    """A query was sent but the response does not carry that series, which must
    not quietly become "no metric".

    The hole this check caught: once one target had four metrics, a single
    missing series still left the verdict standing on the rest, so the
    missing-from-the-response path never fired once.
    What somebody experiences: the line is drawn green while the evidence behind
    that green covers only part of what we asked about, and the screen shows
    nothing. The worst shape is the drop series alone going missing, which
    leaves a block unseen and the line green. So the verdict is used as it is
    and a reason is recorded alongside it.
    """
    cw = FakeCloudWatch(_series(), missing_ids=("m0",))
    document, layer = _observe(cw)
    missing = [
        o
        for o in document["observations"]
        if o["partial"]
        and "대응이 깨졌습니다" in json.dumps(o["partial_reasons"], ensure_ascii=False)
    ]
    assert missing, "a query missing from the response surfaced on no row at all"
    assert layer.incomplete, "the layer does not remember the missing series"
    assert any("응답에 없다" in w for w in layer.warnings)
    # The verdict stays alive. Erasing a line's verdict that the remaining
    # series supports would amount to blanking the screen in order to report a
    # defect.
    assert {o["status"] for o in missing} <= {"live", "up", "down"}
    assert layer.readings, "the whole layer came back empty because one id was missing"
    # Control case: a target that does not use the missing series is not
    # `partial`. With everything red this check could tell nothing apart.
    assert any(o["partial"] is False for o in document["observations"])
    _valid(document)


# ------------------------------------------- the layer off, and coverage


def test_layer_off_is_not_a_failure():
    """The metric layer is off by default. Not having turned it on is `skipped`,
    and it is the first of the reasons behind a question mark.
    """
    document, _ = _observe()
    assert document["layers"] == {"describe": True, "metric": False}
    for row in _metric_rows(document).values():
        assert row["status"] == "skipped"
        assert row["reason"] is None
        assert row["item_count"] is None
    assert all(o["partial"] is False for o in document["observations"])
    # With the metric layer off, the metric-layer-only edges are silent
    # altogether, and that is the normal case.
    assert not [
        o
        for o in document["observations"]
        if o["target_id"].split("|")[0] in METRIC_ONLY_EDGES
    ]


def test_metric_coverage_counts_only_answered_targets():
    """The `item_count` of the metric layer is the number of targets the metrics
    answered for. An empty array is not counted.

    A metric-layer-only edge is counted in the metric-layer cell alone; the
    structure-tier cell counts only what a describe call read. That is why
    turning monitoring on and off does not move the structure-tier numbers.
    """
    document, _ = _observe(
        FakeCloudWatch(
            _series(flowing=(NAT_ID, ATTACH_ID, ENDPOINT_ID, ALB_ID, ALB_PUBLIC_EDGE))
        )
    )
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["item_count"] == 1  # one of the two NAT gateways
    # answered
    # The series of one attachment answers for one node and two lines: one
    # series, three targets.
    assert rows["transit_gateway_attachment"]["item_count"] == 3
    # The ALB node plus two placement lines: one line that received a value and
    # one whose empty response means measured and zero (`empty_is_zero`).
    assert rows["load_balancer"]["item_count"] == 3
    # The interface endpoint node, which received a value, plus two subnet
    # spans that are accepted and whose empty response means measured and zero.
    assert rows["vpc_endpoint"]["item_count"] == 3
    assert rows["route_table"]["item_count"] == 0  # a type with no metrics
    # The ledger: the sum of the metric-layer `item_count` equals the number of
    # rows the metrics answered for, meaning rows whose `datapoints` is a
    # number. A row that carries only a name with `datapoints` of `null`, having
    # been asked and stayed quiet, is not counted. Merging the two would give
    # the same number for having read and for having received an empty array,
    # and the unconfirmed count on the screen would become false.
    answered = [
        o
        for o in document["observations"]
        if (o.get("evidence") or {}).get("datapoints") is not None
    ]
    written = sum(r["item_count"] or 0 for r in rows.values() if r["status"] == "ok")
    assert written == len(answered), (
        "the metric-layer ledger does not add up: the number in the summary "
        "differs from the evidence on screen"
    )
    # What tells them apart: the equation above distinguishes something only if
    # there really is a row that carries a name but was never measured.
    named_but_quiet = [
        o
        for o in document["observations"]
        if (o.get("evidence") or {}).get("metric")
        and (o.get("evidence") or {}).get("datapoints") is None
    ]
    assert named_but_quiet, (
        "with no target coming back as an empty array this ledger distinguishes nothing"
    )


def test_a_denied_describe_does_not_claim_the_metric_layer_was_read():
    """When the structure tier is blocked the target ids are unknown, so the
    metric layer was never called, and writing `ok` would be false.

    Copying the reason from the describe call would be false too, since it would
    claim a denial of an API that was never called.
    """
    denied = client_error("UnauthorizedOperation", "DescribeNatGateways")
    document, _ = _observe(
        FakeCloudWatch(_series()), errors={"describe_nat_gateways": denied}
    )
    rows = _metric_rows(document)
    assert rows["nat_gateway"]["status"] == "skipped"
    assert rows["nat_gateway"]["reason"] is None
    assert rows["vpc"]["status"] == "ok"
    # A type whose structure tier was blocked has no observation entry at all.
    assert NAT_ID not in _by_id(document)
    _valid(document)


def test_metric_layer_is_byte_identical_across_runs():
    """With the metric layer attached, the same input still gives the same
    bytes.
    """
    first, _ = _observe(FakeCloudWatch(_series()))
    second, _ = _observe(FakeCloudWatch(_series()))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
