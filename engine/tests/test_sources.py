"""옵션형 근거 출처 탐지 축 — ADR 0009 1차(`nettopo/sources.py`).

🔴 **이 파일의 이유는 「탐지는 실패하지 않고 조용히 «꺼져 있다»고 답한다」는 것이다.** 흐름 로그를
못 읽어도, EKS 를 Helm 으로 깔았어도, 클러스터가 아예 없어도 코드가 손쉽게 낼 수 있는 답은 전부
`off` 다. 그리고 `off` 는 화면에서 **「고객이 안 켰다」**로 읽히고 「켜세요」라는 권고가 된다. 즉
틀린 답이 예외도 경고도 없이 제품 문구가 된다(ADR 0009 D2).

그래서 잣대를 네 개 둔다:
1. **어휘 일치** — 여기서 내는 `kind`·`scope.unit` 이 `schema/observation.schema.json` 의 어휘와
   같은지. 엔진만 늘리면 문서가 스키마를 어기고, 스키마만 늘리면 영원히 안 나오는 줄이 생긴다.
2. **「모름」과 「꺼짐」의 분리** — 거부·판정불가는 `unknown` + 사유이고 **범위를 적지 않는다**.
   반대로 자원이 0 개인 것은 `off` + 분모 0 이다(「해당 없음」이지 「경고」가 아니다).
3. **켜짐이 구간의 증거가 아니다** — VPC 기본 포맷(v2)·전달 실패·BGP 로그 미설정은 켜짐이어도
   양끝을 이름 댈 수 없다. 그 사실이 `detail` 에 남는지 잰다(D1-1 조건 ①).
4. **분모를 발명하지 않는다** — 분자는 API, 분모는 구조 스냅샷이라 「3/0」·「9/7」이 가능하다.
   못 믿을 때 범위를 **생략**하는지 잰다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
from fakes import (
    ACCOUNT,
    APP_VPC,
    CORE_TGW,
    EGRESS_SUBNET,
    EGRESS_VPC,
    PARTNER,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    FakeAws,
    client_error,
    factory,
    factory_by_service,
)
from internal_docs import read_or_skip
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation
from nettopo.sources import (
    CONTAINER_INSIGHTS,
    EC2_DETAILED_MONITORING,
    EKS_NETWORK_POLICY_LOG,
    KINDS,
    LOG_DELIVERY,
    PROBES,
    REQUIRED_FIELDS,
    RESOLVER_TARGET_NS_METRICS,
    TGW_FLOW_LOG,
    UNIT,
    V2_FIELDS,
    VPC_FLOW_LOG,
    VPN_TUNNEL_BGP_LOG,
    SourceScan,
    detect_sources,
    flow_log_fields,
    is_default_format,
    missing_fields,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "observation.schema.json").read_text(encoding="utf-8")
)
SOURCE_DEF = SCHEMA["$defs"]["source"]

STRUCTURE_AT = "2026-09-25T00:00:00Z"
OBSERVED_AT = "2026-09-25T00:05:00Z"

# 판독기가 읽을 수 있는 사용자 형식 — 필수 필드 전부 + 서브넷 귀속. 순서를 일부러 섞었다(조합표 4.2).
PAIR_FORMAT = (
    "${traffic-path} ${version} ${interface-id} ${subnet-id} ${srcaddr} ${dstaddr}"
    " ${srcport} ${dstport} ${protocol} ${start} ${end} ${action} ${log-status}"
    " ${pkt-srcaddr} ${pkt-dstaddr}"
)
V2_FORMAT = "${version} ${account-id} ${srcaddr} ${dstaddr} ${action} ${log-status}"
# 🔴 `DescribeFlowLogs` 가 기본 형식 로그에 **실제로 돌려주는** 문자열 — 비어 있지 않다.
V2_EXPANDED = " ".join(f"${{{f}}}" for f in V2_FIELDS)
# 필수 필드 중 `traffic-path` 만 빠진 사용자 형식(조합표 4.3).
SHORT_FORMAT = PAIR_FORMAT.replace("${traffic-path} ", "")

ECS_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/"


# ---- 가짜 클라이언트 ----------------------------------------------------------
#
# 🔴 `fakes.FakeAws` 를 늘리지 않는다 — 그쪽은 1차 리소스 12종의 개수 어서션
# (`RESOURCE_NODE_COUNT`)이 걸려 있어서 데이터셋을 추가하면 그 수가 조용히 움직인다.


class _Pages:
    def __init__(self, key: str, items: list[Any]) -> None:
        self._key, self._items = key, items

    def paginate(self, **_: Any) -> list[dict[str, list[Any]]]:
        # 2장으로 쪼갠다 — 병합을 빼먹으면 개수가 절반이 된다.
        middle = len(self._items) // 2
        return [{self._key: self._items[:middle]}, {self._key: self._items[middle:]}]


class _Raises:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        raise self._error


class FakeProbe:
    """탐지 API 만 아는 가짜. 등록하지 않은 메서드는 **큰 소리로** 죽는다.

    조용히 빈 답을 주면 API 이름을 틀려도 `off` 가 나오고 검사가 통과한다.
    """

    def __init__(
        self,
        paginated: dict[str, tuple[str, list[Any]]],
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._paginated = paginated
        self._errors = errors or {}
        self.calls: list[str] = []

    def can_paginate(self, method: str) -> bool:
        if method not in self._paginated:
            raise AssertionError(f"등록하지 않은 탐지 API: {method}")
        return True

    def get_paginator(self, method: str) -> Any:
        self.calls.append(method)
        if method in self._errors:
            return _Raises(self._errors[method])
        key, items = self._paginated[method]
        return _Pages(key, items)


class FakeEcs(FakeProbe):
    """`ListClusters` + `DescribeClusters`(100개 배치). 배치 호출을 세서 경계를 잰다."""

    def __init__(
        self,
        arns: tuple[str, ...] = (),
        settings: dict[str, str] | None = None,
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        # 🔴 `clusterArns` 는 **문자열** 목록이다(dict 가 아니다) — `_Pages` 는 그대로 쪼갠다.
        super().__init__({"list_clusters": ("clusterArns", list(arns))}, errors=errors)
        self._settings = settings or {}
        self.batches: list[int] = []

    def describe_clusters(self, *, clusters: list[str]) -> dict[str, Any]:
        self.batches.append(len(clusters))
        assert len(clusters) <= 100, "DescribeClusters 는 한 번에 100개까지다"
        return {
            "clusters": [
                {
                    "clusterArn": arn,
                    "settings": (
                        [{"name": "containerInsights", "value": self._settings[arn]}]
                        if arn in self._settings
                        else []
                    ),
                }
                for arn in clusters
            ]
        }


class FakeEks(FakeProbe):
    """`ListClusters` + `DescribeAddon`. add-on 이 없는 클러스터는 예외를 던진다(Helm 설치)."""

    def __init__(
        self,
        names: tuple[str, ...] = (),
        addons: dict[str, Any] | None = None,
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        super().__init__({"list_clusters": ("clusters", list(names))}, errors=errors)
        self._addons = addons or {}
        self.addon_calls: list[str] = []

    def describe_addon(self, *, clusterName: str, addonName: str) -> dict[str, Any]:  # noqa: N803 — boto3 kwarg 이름
        self.addon_calls.append(clusterName)
        assert addonName == "vpc-cni", (
            "네트워크 정책 로그는 vpc-cni add-on 의 설정값이다 — 다른 add-on 을 보면 안 된다"
        )
        value = self._addons.get(clusterName)
        if value is None:
            raise client_error("ResourceNotFoundException", "DescribeAddon")
        return {"addon": {"configurationValues": value}}


def _ec2(
    *,
    flow_logs: tuple[dict[str, Any], ...] = (),
    instances: tuple[dict[str, Any], ...] = (),
    vpns: tuple[dict[str, Any], ...] = (),
    errors: dict[str, Exception] | None = None,
) -> FakeProbe:
    return FakeProbe(
        {
            "describe_flow_logs": ("FlowLogs", list(flow_logs)),
            "describe_instances": ("Reservations", list(instances)),
            "describe_vpn_connections": ("VpnConnections", list(vpns)),
        },
        errors=errors,
    )


def _logs(*log_types: str, errors: dict[str, Exception] | None = None) -> FakeProbe:
    return FakeProbe(
        {
            "describe_delivery_sources": (
                "deliverySources",
                [{"name": f"src-{i}", "logType": t} for i, t in enumerate(log_types)],
            )
        },
        errors=errors,
    )


def _resolver(*endpoints: dict[str, Any]) -> FakeProbe:
    return FakeProbe(
        {"list_resolver_endpoints": ("ResolverEndpoints", list(endpoints))}
    )


def _flow_log(
    resource_id: str,
    *,
    status: str = "ACTIVE",
    delivery: str = "SUCCESS",
    log_format: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    log: dict[str, Any] = {
        "FlowLogId": f"fl-{resource_id}",
        "ResourceId": resource_id,
        "FlowLogStatus": status,
        "DeliverLogsStatus": delivery,
        **extra,
    }
    if log_format is not None:
        log["LogFormat"] = log_format
    return log


def _scan(*flow_logs: dict[str, Any], snapshot: Any = None) -> SourceScan:
    return detect_sources(
        Scope(account_ids=(ACCOUNT,), regions=(REGION,), resource_types=()),
        factory_by_service(_clients(ec2=_ec2(flow_logs=flow_logs))),
        snapshot=snapshot if snapshot is not None else _snapshot(),
    )


def _instance(state: str = "running", *, detailed: bool = False) -> dict[str, Any]:
    return {
        "Instances": [
            {
                "InstanceId": f"i-0{state}{'1' if detailed else '0'}",
                "State": {"Name": state},
                "Monitoring": {"State": "enabled" if detailed else "disabled"},
            }
        ]
    }


def _vpn(
    *, tunnel_log: bool = False, bgp_log: bool = False, state: str = "available"
) -> dict[str, Any]:
    return {
        "VpnConnectionId": f"vpn-0{int(tunnel_log)}{int(bgp_log)}{state}",
        "State": state,
        "Options": {
            "TunnelOptions": [
                {
                    "LogOptions": {
                        "CloudWatchLogOptions": {
                            "LogEnabled": tunnel_log,
                            "BgpLogEnabled": bgp_log,
                        }
                    }
                }
            ]
        },
    }


# ---- 스캔 헬퍼 ----------------------------------------------------------------


def _snapshot(resource_types: tuple[str, ...] = SUPPORTED_RESOURCE_TYPES) -> Any:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=resource_types
    )
    return collect(scope, factory(FakeAws()), clock=lambda: STRUCTURE_AT)


def _clients(**overrides: Any) -> dict[str, Any]:
    clients: dict[str, Any] = {
        "ec2": _ec2(),
        "ecs": FakeEcs(),
        "eks": FakeEks(),
        "logs": _logs(),
        "route53resolver": _resolver(),
    }
    unknown = set(overrides) - set(clients)
    if unknown:  # 🔴 오타 난 서비스 이름이 조용히 무시되면 「빈 응답으로 통과」가 된다
        raise TypeError(
            f"모르는 서비스: {sorted(unknown)} (아는 것: {sorted(clients)})"
        )
    clients.update(overrides)
    return clients


def _rows(
    *,
    accounts: tuple[str, ...] = (ACCOUNT,),
    snapshot: Any = None,
    fail: dict[str, Exception] | None = None,
    **overrides: Any,
) -> dict[str, dict[str, Any]]:
    """kind → 그 줄. 빠진 서비스는 **빈 응답**으로 채운다(등록 누락은 큰 소리로 죽는다)."""
    scan = detect_sources(
        Scope(account_ids=accounts, regions=(REGION,), resource_types=()),
        factory_by_service(_clients(**overrides), fail_services=fail),
        snapshot=snapshot if snapshot is not None else _snapshot(),
    )
    rows = scan.to_list()
    assert [r["kind"] for r in rows] == sorted(r["kind"] for r in rows), (
        "정렬이 코드에 없으면 같은 입력이 다른 바이트를 낸다(불변식 ②)"
    )
    return {row["kind"]: row for row in rows}


# ---- ① 어휘 일치 ---------------------------------------------------------------


def test_every_kind_gets_a_row() -> None:
    """🔴 8종 **전부**가 나와야 한다 — 안 나온 kind 는 화면에서 「모름」도 아니고 **없는 것**이
    되고, 고객은 그 근거를 우리가 볼 생각조차 안 했다는 사실을 알 수 없다.
    """
    rows = _rows()
    assert tuple(sorted(rows)) == tuple(sorted(KINDS))
    assert len(rows) == len(KINDS) == 8


def test_kind_vocabulary_matches_the_schema() -> None:
    assert list(KINDS) == SOURCE_DEF["properties"]["kind"]["enum"]


def test_scope_unit_vocabulary_matches_the_schema() -> None:
    """`scope.unit` 은 `resource_type` 과 **다른 축**이다(ADR 0009 D2-3)."""
    allowed = set(SCHEMA["$defs"]["source_scope_unit"]["enum"])
    assert set(UNIT.values()) <= allowed
    # 계정 단위 배선은 분모가 없다 — 단위를 주면 「배선 1/0개」라는 읽을 수 없는 수치가 나온다.
    assert LOG_DELIVERY not in UNIT


def test_every_probe_is_a_read_only_api() -> None:
    """불변식 ① — 탐지 단계에 write 가 섞이면 이 툴의 전제가 무너진다."""
    for _service, api, _scan in PROBES:
        verb = api.split(":", 1)[1]
        assert verb.startswith(("Describe", "Get", "List", "Search")), api


def test_the_document_validates_against_the_schema() -> None:
    """탐지 결과가 그대로 관측 문서에 들어가고 스키마를 통과한다(궂은 경우를 섞어서)."""
    scan = detect_sources(
        Scope(account_ids=(ACCOUNT,), regions=(REGION,), resource_types=()),
        factory_by_service(
            _clients(
                ec2=_ec2(flow_logs=(_flow_log(APP_VPC),)),
                eks=FakeEks(("helm-only",)),
                logs=_logs("FLOW_LOGS"),
            )
        ),
        snapshot=_snapshot(),
    )
    document, _dropped = build_observation(
        collect(
            Scope(
                account_ids=(ACCOUNT,),
                regions=(REGION,),
                resource_types=OBSERVED_RESOURCE_TYPES,
            ),
            factory(FakeAws()),
            clock=lambda: OBSERVED_AT,
        ),
        topology=_snapshot().to_document(),
        observed_at=OBSERVED_AT,
        tool_version="test",
        sources=scan,
    )
    jsonschema.validate(document, SCHEMA)
    kinds = {s["kind"]: s for s in document["sources"]}
    assert kinds[VPC_FLOW_LOG]["state"] == "on"
    assert kinds[EKS_NETWORK_POLICY_LOG]["state"] == "unknown"


def test_nothing_is_read_in_this_version() -> None:
    """1차는 탐지뿐이다 — `used` 가 하나라도 true 면 읽지 않은 것을 읽었다고 적은 것이다."""
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log(APP_VPC),)), logs=_logs("FLOW_LOGS"))
    assert all(row["used"] is False for row in rows.values())


def test_a_vpc_whose_only_flow_log_is_parquet_reads_off() -> None:
    """Parquet 은 스펙 밖이다(ADR 0008 「D4c」 ①) — 그 로그 하나뿐인 VPC 는 「꺼짐」이고 못 읽는다는 안내도 없다.
    대조군: 같은 로그를 텍스트로 바꾸면 「켜짐」이다(늘 꺼짐이면 위는 아무것도 안 잰다)."""

    def s3(file_format: str) -> dict[str, Any]:
        return _flow_log(
            APP_VPC,
            LogDestinationType="s3",
            LogDestination="arn:aws:s3:::example-flow-bucket/p",
            DestinationOptions={"FileFormat": file_format},
        )

    off = _rows(ec2=_ec2(flow_logs=(s3("parquet"),)))[VPC_FLOW_LOG]
    assert off["state"] == "off"
    assert "why" not in off
    assert "arquet" not in json.dumps(off, ensure_ascii=False)
    assert _rows(ec2=_ec2(flow_logs=(s3("plain-text"),)))[VPC_FLOW_LOG]["state"] == "on"


def test_enabled_but_unread_explains_itself() -> None:
    """`state=on ∧ used=false ⇒ why` (스키마 allOf). 사유가 없으면 화면이 「⚠ 근거 못 읽음」을
    띄우면서 고객이 무엇을 고쳐야 하는지 말하지 못한다.
    """
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log(APP_VPC),)))
    assert rows[VPC_FLOW_LOG]["state"] == "on"
    # 판독기를 거치지 않은 VPC 줄 = 판독을 끈 것이다 → 켜는 스위치를 말한다(U9 · 「2차」라고 적으면 거짓).
    assert "FlowLogReading" in rows[VPC_FLOW_LOG]["why"]
    # 꺼진 줄에는 사유를 붙이지 않는다 — 「꺼져 있다」는 그 자체로 완결된 사실이다.
    assert "why" not in rows[CONTAINER_INSIGHTS]


# ---- ② 「모름」과 「꺼짐」의 분리 -----------------------------------------------


def test_denied_detection_is_unknown_not_off() -> None:
    """🔴 거부를 `off` 로 적으면 이미 켠 고객에게 「켜세요」라고 말한다(ADR 0009 D2)."""
    rows = _rows(
        ec2=_ec2(
            errors={
                "describe_flow_logs": client_error("AccessDenied", "DescribeFlowLogs")
            }
        )
    )
    for kind in (VPC_FLOW_LOG, TGW_FLOW_LOG):
        assert rows[kind]["state"] == "unknown", kind
        assert "ec2:DescribeFlowLogs" in rows[kind]["why"]
        assert "AccessDenied" in rows[kind]["why"]
        # 🔴 「0/2 VPC」는 「2개 다 꺼져 있다」로 읽힌다 — 못 읽었을 때 범위를 적지 않는다.
        assert "scope" not in rows[kind], kind
    # 같은 호출에서 온 다른 kind 는 멀쩡해야 한다(불변식 ④: 부분 실패는 정상 경로).
    assert rows[EC2_DETAILED_MONITORING]["state"] == "off"


def test_a_denied_service_marks_only_its_own_kinds() -> None:
    rows = _rows(fail={"ecs": client_error("AccessDenied", "ListClusters")})
    assert rows[CONTAINER_INSIGHTS]["state"] == "unknown"
    assert rows[VPC_FLOW_LOG]["state"] == "off"


def test_failures_in_several_places_are_counted() -> None:
    """자리 수를 적지 않으면 「한 리전만 막혔다」와 「전부 막혔다」가 같은 문장이 된다."""
    rows = _rows(
        accounts=(PARTNER, ACCOUNT),
        fail={"route53resolver": client_error("AccessDenied", "ListResolverEndpoints")},
    )
    assert rows[RESOLVER_TARGET_NS_METRICS]["state"] == "unknown"
    assert "2개 자리에서 탐지 실패" in rows[RESOLVER_TARGET_NS_METRICS]["detail"]


def test_no_clusters_is_off_with_a_zero_denominator() -> None:
    """ECS·EKS 를 안 쓰는 고객에게 「근거 확인 못함」 경고를 띄우면 안 된다 — 분모 0 이 정답이다."""
    rows = _rows()
    for kind, unit in (
        (CONTAINER_INSIGHTS, "ecs_cluster"),
        (EKS_NETWORK_POLICY_LOG, "eks_cluster"),
    ):
        assert rows[kind]["state"] == "off", kind
        assert rows[kind]["scope"] == {"unit": unit, "enabled_ids": 0, "total_ids": 0}


def test_helm_installed_eks_is_unknown_not_off() -> None:
    """🔴 add-on API 밖의 클러스터는 **꺼짐이 아니라 모름**이다(ADR 0009 D4).

    `eks:DescribeCluster` 의 `logging.clusterLogging` 에는 네트워크 정책 로그가 아예 없고,
    Helm 으로 vpc-cni 를 깐 클러스터는 `DescribeAddon` 에 잡히지 않는다. 여기서 `off` 를 적으면
    이미 켠 고객에게 「켜세요」라고 말한다.
    """
    rows = _rows(eks=FakeEks(("helm-cluster",)))
    assert rows[EKS_NETWORK_POLICY_LOG]["state"] == "unknown"
    assert "Helm" in rows[EKS_NETWORK_POLICY_LOG]["why"]
    assert "scope" not in rows[EKS_NETWORK_POLICY_LOG]


def test_an_enabled_cluster_beside_a_helm_one_stays_on() -> None:
    """판별력: 「모름」이 켜짐을 덮어써선 안 된다. 켜진 게 하나라도 있으면 `on` 이고 나머지는
    `detail` 로 말한다 — `unknown` 으로 올리면 실제로 있는 근거를 없다고 적는 셈이다.
    """
    rows = _rows(
        eks=FakeEks(
            ("helm-cluster", "managed-cluster"),
            {"managed-cluster": json.dumps({"enableNetworkPolicy": True})},
        )
    )
    row = rows[EKS_NETWORK_POLICY_LOG]
    assert row["state"] == "on"
    assert row["scope"] == {"unit": "eks_cluster", "enabled_ids": 1, "total_ids": 2}
    assert "꺼짐이 아니라 모름" in row["detail"]


def test_unparseable_addon_config_is_not_off() -> None:
    rows = _rows(eks=FakeEks(("odd",), {"odd": "not json"}))
    assert rows[EKS_NETWORK_POLICY_LOG]["state"] == "unknown"


def test_addon_present_but_policy_disabled_is_off() -> None:
    """판별력: 「add-on 을 읽었다」가 「켜져 있다」가 아니다."""
    rows = _rows(
        eks=FakeEks(
            ("managed",), {"managed": json.dumps({"enableNetworkPolicy": False})}
        )
    )
    row = rows[EKS_NETWORK_POLICY_LOG]
    assert row["state"] == "off"
    assert row["scope"] == {"unit": "eks_cluster", "enabled_ids": 0, "total_ids": 1}
    assert "detail" not in row


def test_not_scanned_is_not_everything_off() -> None:
    """`--no-sources` 의 결과. 🔴 빈 배열은 「전부 꺼짐」이 아니라 「탐지하지 않았다」다 —
    화면은 이때 알약을 띄울 자격이 없다.
    """
    scan = SourceScan.not_scanned()
    assert scan.scanned is False
    assert scan.to_list() == []


# ---- ③ 켜짐이 구간의 증거가 아니다 ---------------------------------------------


def test_default_flow_log_format_cannot_name_a_segment() -> None:
    """🔴 VPC 기본 포맷(v2)에는 `pkt-srcaddr`·`traffic-path` 가 없다 — 「흐름 로그가 켜져 있다」
    만으로 구간을 증명한다고 적으면 거짓이다(ADR 0009 D4).
    """
    assert flow_log_fields(None) == ()
    assert flow_log_fields("") == ()
    assert "pkt-srcaddr" in flow_log_fields(PAIR_FORMAT)
    assert "pkt-srcaddr" not in flow_log_fields(V2_FORMAT)

    # 🔴 실계정의 모양 = 펼친 기본 형식 문자열. 「빈 문자열이면 기본」으로 가르면 여기서 틀린다.
    for fmt in (V2_EXPANDED, V2_FORMAT):
        rows = _rows(ec2=_ec2(flow_logs=(_flow_log(APP_VPC, log_format=fmt),)))
        assert rows[VPC_FLOW_LOG]["state"] == "on"
        assert "logFormat v2 기본 1건" in rows[VPC_FLOW_LOG]["detail"], fmt
        assert "필수 필드가 빠진" not in rows[VPC_FLOW_LOG]["detail"], fmt


def test_a_custom_format_carries_no_v2_warning() -> None:
    """판별력 — 위 경고가 **항상** 붙는다면 그 어서션은 아무것도 재지 않는다."""
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log(APP_VPC, log_format=PAIR_FORMAT),)))
    assert rows[VPC_FLOW_LOG]["state"] == "on"
    assert "구간을 특정하는 필드가 없다" not in (rows[VPC_FLOW_LOG].get("detail") or "")
    assert "필수 필드가 빠진" not in (rows[VPC_FLOW_LOG].get("detail") or "")


def test_format_is_judged_by_the_field_set() -> None:
    """ADR 0009 D2-4 — 기본 형식 = 선언이 전부 v2 14필드 안. 사용자 형식은 필수 필드로 가른다."""
    assert is_default_format(flow_log_fields(V2_EXPANDED))
    assert is_default_format(())  # 형식을 모르면 AWS 기본값(v2)
    assert not is_default_format(flow_log_fields(PAIR_FORMAT))
    assert missing_fields(flow_log_fields(PAIR_FORMAT)) == ()
    assert missing_fields(flow_log_fields(SHORT_FORMAT)) == ("traffic-path",)
    # 선언 순서를 그대로 돌려준다 — 판독기가 레코드를 이 순서로 자른다(조합표 4.2).
    assert flow_log_fields(PAIR_FORMAT)[:3] == (
        "traffic-path",
        "version",
        "interface-id",
    )


def test_a_custom_format_missing_a_required_field_says_so() -> None:
    """조합표 4.3 — 사용자 형식인데 `traffic-path` 가 없으면 기본 형식이 아니라 「필수 필드가 빠진」
    것이다. 둘을 섞으면 고객은 켜지도 않은 기본 형식을 찾으러 간다.
    """
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log(APP_VPC, log_format=SHORT_FORMAT),)))
    detail = rows[VPC_FLOW_LOG]["detail"]
    assert "필수 필드가 빠진 사용자 형식 1건" in detail
    assert "logFormat v2 기본" not in detail


def test_required_fields_match_the_adr() -> None:
    """정본 = ADR 0008 「D4b 개정」 조합표 아래 「필수 필드(4.3)」 줄. 코드만 늘리거나 줄이면 고객
    설정 조합표의 4.3 칸이 다른 로그를 가리킨다.
    """
    adr = read_or_skip("docs/adr/0008-flow-verdict.md")
    lines = [ln for ln in adr.splitlines() if ln.startswith("필수 필드(4.3):")]
    assert len(lines) == 1, "ADR 0008 의 필수 필드 줄을 못 찾았다(못 읽음 → FAIL)"
    listed = tuple(
        tok.strip("`") for tok in lines[0].split(":", 1)[1].strip(" .").split("·")
    )
    assert listed == REQUIRED_FIELDS


def test_failed_delivery_is_recorded() -> None:
    """D1-1 조건 ① — 전달이 죽어 있으면 **레코드 부재가 트래픽 부재가 아니다**. 이 사실을
    적지 않으면 2차 리더가 빈 버킷을 보고 「흐름 없음」이라고 단정한다.
    """
    rows = _rows(
        ec2=_ec2(
            flow_logs=(_flow_log(APP_VPC, log_format=PAIR_FORMAT, delivery="FAILED"),)
        )
    )
    assert "전달 실패 1건" in rows[VPC_FLOW_LOG]["detail"]


def test_a_subnet_level_log_turns_its_vpc_on() -> None:
    """🔴 옛 결함(ADR 0009 D2-4) — `vpc-` 접두만 세서 서브넷 단위로 켠 고객에게 「꺼짐」을 말했다.
    같은 VPC 의 서브넷 둘은 VPC 하나다.
    """
    rows = _rows(
        ec2=_ec2(
            flow_logs=(
                _flow_log(PUBLIC_SUBNET, log_format=PAIR_FORMAT),
                _flow_log(PRIVATE_SUBNET, log_format=PAIR_FORMAT),
            )
        )
    )
    row = rows[VPC_FLOW_LOG]
    assert row["state"] == "on"
    assert row["scope"] == {"unit": "vpc", "enabled_ids": 1, "total_ids": 2}
    assert "서브넷 단위 2건" in row["detail"]

    rows = _rows(
        ec2=_ec2(
            flow_logs=(
                _flow_log(PUBLIC_SUBNET, log_format=PAIR_FORMAT),
                _flow_log(EGRESS_SUBNET, log_format=PAIR_FORMAT),
            )
        )
    )
    assert rows[VPC_FLOW_LOG]["scope"]["enabled_ids"] == 2


def test_an_eni_only_flow_log_is_on_without_a_scope() -> None:
    """ENI 하나는 VPC 전체를 덮지 않는다 — 그래도 **꺼짐이 아니다**(켠 고객에게 「켜세요」가 된다).
    「켜짐 0/2 VPC」는 스키마가 막고 화면에서 꺼짐으로 읽히므로 범위를 적지 않는다.
    """
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log("eni-0aaa1111bbbb9999"),)))
    row = rows[VPC_FLOW_LOG]
    assert row["state"] == "on"
    assert "scope" not in row
    assert "ENI 단위 1건" in row["detail"]
    assert "켜진 자원 0개" not in row["detail"]


def test_an_eni_log_beside_a_vpc_log_adds_no_vpc() -> None:
    """판별력 — ENI 로그가 VPC 수를 늘리면 「2/2 VPC」가 되고 덮지 않은 VPC 가 켜짐으로 보인다."""
    rows = _rows(
        ec2=_ec2(flow_logs=(_flow_log(APP_VPC), _flow_log("eni-0aaa1111bbbb9999")))
    )
    assert rows[VPC_FLOW_LOG]["scope"] == {
        "unit": "vpc",
        "enabled_ids": 1,
        "total_ids": 2,
    }


def test_a_subnet_the_structure_lacks_is_on_without_a_scope() -> None:
    """1층이 못 읽은 서브넷(또는 남의 공유 서브넷)의 로그 — 어느 VPC 인지 모른다. 켜짐으로 세되
    범위는 적지 않는다(모자라게 센 「1/2」도 켠 VPC 를 꺼졌다고 말한다).
    """
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log("subnet-0fff9999eeee8888"),)))
    row = rows[VPC_FLOW_LOG]
    assert row["state"] == "on"
    assert "scope" not in row
    assert "구조에 없는 서브넷의 로그 1건" in row["detail"]

    rows = _rows(
        ec2=_ec2(flow_logs=(_flow_log(APP_VPC), _flow_log("subnet-0fff9999eeee8888")))
    )
    assert "scope" not in rows[VPC_FLOW_LOG]


def test_every_active_flow_log_comes_back_with_its_configuration() -> None:
    """판독기의 유일한 입력 — 로그마다 설정 전부(ADR 0009 D2-4). ACTIVE 만 · (계정, 리전, id) 순."""
    scan = _scan(
        _flow_log(
            APP_VPC,
            log_format=PAIR_FORMAT,
            TrafficType="ALL",
            LogDestinationType="cloud-watch-logs",
            LogGroupName="example-vpc-flow",
            LogDestination="arn:aws:logs:us-east-1:111122223333:log-group:example-vpc-flow",
            MaxAggregationInterval=60,
        ),
        _flow_log(
            PUBLIC_SUBNET,
            log_format=PAIR_FORMAT,
            TrafficType="ACCEPT",
            LogDestinationType="s3",
            LogDestination="arn:aws:s3:::example-flow-bucket/prefix",
            DestinationOptions={
                "FileFormat": "plain-text",
                "HiveCompatiblePartitions": True,
                "PerHourPartition": True,
            },
            MaxAggregationInterval=600,
            delivery="FAILED",
        ),
        # Parquet 은 이 도구가 다루지 않는 형식이다(ADR 0008 「D4c」 ①) — 켜짐으로도 세지 않고 목록에서 뺀다.
        _flow_log(
            "subnet-0aaa1111bbbb8888",
            log_format=PAIR_FORMAT,
            LogDestinationType="s3",
            LogDestination="arn:aws:s3:::example-flow-bucket/parquet",
            DestinationOptions={"FileFormat": "parquet"},
        ),
        _flow_log(CORE_TGW, LogDestinationType="s3", LogDestination="arn:aws:s3:::b"),
        _flow_log("eni-0aaa1111bbbb9999", status="FAILED"),
    )
    by_id = {log.id: log for log in scan.flow_logs}
    assert [log.id for log in scan.flow_logs] == sorted(by_id)
    assert set(by_id) == {f"fl-{APP_VPC}", f"fl-{PUBLIC_SUBNET}", f"fl-{CORE_TGW}"}

    vpc = by_id[f"fl-{APP_VPC}"]
    assert (vpc.level, vpc.target, vpc.vpc_id) == ("vpc", APP_VPC, APP_VPC)
    assert (vpc.traffic_type, vpc.destination_type) == ("ALL", "cloud-watch-logs")
    assert (
        vpc.destination == "example-vpc-flow"
    )  # 로그 그룹 이름 — 판독기가 이것으로 찾는다
    assert (vpc.interval, vpc.delivered, vpc.file_format) == (60, True, None)
    assert vpc.fields == flow_log_fields(PAIR_FORMAT)
    assert (vpc.account_id, vpc.region) == (ACCOUNT, REGION)

    sub = by_id[f"fl-{PUBLIC_SUBNET}"]
    assert (sub.level, sub.vpc_id) == ("subnet", APP_VPC)  # 서브넷 → VPC 는 구조에서
    assert (sub.file_format, sub.hive, sub.per_hour) == ("plain-text", True, True)
    assert (sub.interval, sub.delivered) == (600, False)

    tgw = by_id[f"fl-{CORE_TGW}"]
    assert (tgw.level, tgw.vpc_id, tgw.traffic_type) == ("transit_gateway", None, None)

    # 🔴 목적지 이름은 식별자다 — repr 에도, 문서에도 나가지 않는다.
    assert "example" not in repr(scan.flow_logs)
    dumped = json.dumps(scan.to_list(), ensure_ascii=False)
    assert "example" not in dumped and "fl-" not in dumped


def test_a_tgw_delivery_failure_is_not_blamed_on_the_vpc_row() -> None:
    """전달 실패는 그 로그의 출처 줄에만 — TGW 로그가 죽었는데 VPC 줄이 「레코드 부재가 트래픽 부재가
    아니다」를 말하면 VPC 로그를 멀쩡히 읽는 고객이 원인을 엉뚱한 곳에서 찾는다.
    """
    rows = _rows(
        ec2=_ec2(
            flow_logs=(
                _flow_log(APP_VPC, log_format=PAIR_FORMAT),
                _flow_log(CORE_TGW, delivery="FAILED"),
            )
        )
    )
    assert "전달 실패" not in (rows[VPC_FLOW_LOG].get("detail") or "")
    assert "전달 실패 1건" in rows[TGW_FLOW_LOG]["detail"]


def test_only_active_flow_logs_count() -> None:
    """만들다 실패한 흐름 로그를 세면 없는 근거를 있다고 적는다."""
    rows = _rows(
        ec2=_ec2(
            flow_logs=(
                _flow_log(APP_VPC, status="FAILED"),
                _flow_log(EGRESS_VPC, status="ACTIVE"),
            )
        )
    )
    assert rows[VPC_FLOW_LOG]["scope"] == {
        "unit": "vpc",
        "enabled_ids": 1,
        "total_ids": 2,
    }


def test_vpn_bgp_log_is_a_separate_switch() -> None:
    """🔴 스위치가 2개고 둘 다 기본 꺼짐이다. 터널 로그만 켜져 있으면 구간의 양끝은 모른다 —
    `DENIED due to`·prefix·피어는 BGP 쪽에만 있다.
    """
    rows = _rows(
        ec2=_ec2(vpns=(_vpn(tunnel_log=True), _vpn(tunnel_log=True, bgp_log=True)))
    )
    row = rows[VPN_TUNNEL_BGP_LOG]
    assert row["state"] == "on"
    assert row["scope"] == {"unit": "vpn_connection", "enabled_ids": 2, "total_ids": 2}
    assert "BGP 로그는 1/2개" in row["detail"]


def test_deleted_vpn_connections_are_not_counted() -> None:
    rows = _rows(ec2=_ec2(vpns=(_vpn(state="deleted"), _vpn(tunnel_log=True))))
    assert rows[VPN_TUNNEL_BGP_LOG]["scope"]["total_ids"] == 1


def test_container_insights_counts_enhanced() -> None:
    """`enhanced`(2024년 추가)를 빼면 최신 설정을 켠 고객이 「안 켰다」로 나온다."""
    arns = tuple(f"{ECS_ARN}c{i}" for i in range(3))
    rows = _rows(
        ecs=FakeEcs(
            arns,
            {arns[0]: "enabled", arns[1]: "enhanced", arns[2]: "disabled"},
        )
    )
    assert rows[CONTAINER_INSIGHTS]["scope"] == {
        "unit": "ecs_cluster",
        "enabled_ids": 2,
        "total_ids": 3,
    }


def test_container_insights_batches_at_a_hundred() -> None:
    """🔴 `DescribeClusters` 는 한 번에 100개다 — 배치를 안 쪼개면 101번째부터 조용히 빠진다."""
    arns = tuple(f"{ECS_ARN}c{i:03d}" for i in range(101))
    ecs = FakeEcs(arns, dict.fromkeys(arns, "enabled"))
    rows = _rows(ecs=ecs)
    assert ecs.batches == [100, 1]
    assert rows[CONTAINER_INSIGHTS]["scope"]["enabled_ids"] == 101


def test_detailed_monitoring_ignores_terminated_instances() -> None:
    """종료된 인스턴스를 분모에 넣으면 「20대 중 3대만 켰다」가 영원히 개선되지 않는다."""
    rows = _rows(
        ec2=_ec2(
            instances=(
                _instance("terminated"),
                _instance("running", detailed=True),
                _instance("stopped"),
            )
        )
    )
    assert rows[EC2_DETAILED_MONITORING]["scope"] == {
        "unit": "ec2_instance",
        "enabled_ids": 1,
        "total_ids": 2,
    }


def test_resolver_metrics_are_off_by_default() -> None:
    rows = _rows(
        route53resolver=_resolver(
            {"Id": "rslvr-out-1", "Status": "OPERATIONAL"},
            {
                "Id": "rslvr-out-2",
                "Status": "OPERATIONAL",
                "TargetNameServerMetricsEnabled": True,
            },
            {
                "Id": "rslvr-out-3",
                "Status": "DELETING",
                "TargetNameServerMetricsEnabled": True,
            },
        )
    )
    assert rows[RESOLVER_TARGET_NS_METRICS]["scope"] == {
        "unit": "resolver_endpoint",
        "enabled_ids": 1,
        "total_ids": 2,
    }


def test_log_delivery_ignores_non_network_log_types() -> None:
    """🔴 전체 배선을 세면 Lambda 로그 하나로 「근거가 있다」고 말하게 된다."""
    rows = _rows(logs=_logs("APPLICATION_LOGS", "TRACES"))
    assert rows[LOG_DELIVERY]["state"] == "off"
    assert "scope" not in rows[LOG_DELIVERY]

    rows = _rows(logs=_logs("WAF_LOGS", "APPLICATION_LOGS", "FLOW_LOGS"))
    assert rows[LOG_DELIVERY]["state"] == "on"
    assert rows[LOG_DELIVERY]["detail"] == "FLOW_LOGS, WAF_LOGS"


# ---- ④ 분모를 발명하지 않는다 --------------------------------------------------


def test_tgw_attachment_flow_log_does_not_invent_a_denominator() -> None:
    """어태치먼트에 걸린 흐름 로그는 「TGW 흐름 로그가 켜져 있다」는 사실이지만 **어느 TGW 의
    것인지는 응답에 없다**. 분모를 믿을 수 없으므로 범위를 적지 않고 이유를 남긴다.
    """
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log("tgw-attach-0aaa1111bbbb8888"),)))
    row = rows[TGW_FLOW_LOG]
    assert row["state"] == "on"
    assert "scope" not in row
    assert "어태치먼트 단위 1건" in row["detail"]
    assert "믿을 수 없어 범위를 적지 않는다" in row["detail"]


def test_a_tgw_level_flow_log_does_report_a_scope() -> None:
    """판별력 — 위 생략이 **항상** 일어난다면 아무것도 재지 않는다."""
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log(CORE_TGW),)))
    assert rows[TGW_FLOW_LOG]["scope"] == {
        "unit": "transit_gateway",
        "enabled_ids": 1,
        "total_ids": 1,
    }


def test_more_enabled_than_the_structure_saw_hides_the_scope() -> None:
    """🔴 분자는 API, 분모는 구조 스냅샷이다 — 1층이 못 읽은 VPC 에 흐름 로그가 켜져 있으면
    「3/2 VPC」가 나온다. 읽을 수 없는 수치를 화면에 내지 않는다.
    """
    rows = _rows(
        ec2=_ec2(
            flow_logs=tuple(
                _flow_log(f"vpc-0{i}{i}{i}", log_format=PAIR_FORMAT) for i in range(3)
            )
        )
    )
    row = rows[VPC_FLOW_LOG]
    assert row["state"] == "on"
    assert "scope" not in row
    assert "켜진 자원 3개" in row["detail"]


def test_the_denominator_comes_from_the_structure_layer() -> None:
    """분모를 여기서 또 세면 화면의 VPC 개수와 갈라지고 사람은 어느 쪽이 맞는지 알 수 없다."""
    rows = _rows(ec2=_ec2(flow_logs=(_flow_log(APP_VPC),)))
    assert rows[VPC_FLOW_LOG]["scope"]["total_ids"] == 2  # 픽스처의 VPC 2개
    empty = collect(
        Scope(
            account_ids=(ACCOUNT,),
            regions=(REGION,),
            resource_types=SUPPORTED_RESOURCE_TYPES,
        ),
        factory(FakeAws.empty()),
        clock=lambda: STRUCTURE_AT,
    )
    rows = _rows(snapshot=empty, ec2=_ec2(flow_logs=(_flow_log(APP_VPC),)))
    assert "scope" not in rows[VPC_FLOW_LOG]


# ---- 결정론 -------------------------------------------------------------------


def test_two_runs_are_byte_identical() -> None:
    """불변식 ② — 같은 입력이면 바이트가 같다. 자리 순회가 `set` 이면 여기서 깨진다."""

    def once() -> str:
        return json.dumps(
            detect_sources(
                Scope(
                    account_ids=(PARTNER, ACCOUNT), regions=(REGION,), resource_types=()
                ),
                factory_by_service(
                    _clients(
                        ec2=_ec2(flow_logs=(_flow_log(APP_VPC), _flow_log(CORE_TGW))),
                        logs=_logs("FLOW_LOGS", "WAF_LOGS"),
                    )
                ),
                snapshot=_snapshot(),
            ).to_list(),
            ensure_ascii=False,
            sort_keys=True,
        )

    assert once() == once()


def test_places_are_visited_in_sorted_order() -> None:
    """정렬된 순회가 위 바이트 동일성의 근거다 — 팩토리가 받은 순서를 직접 잰다."""
    seen: list[tuple[str, str, str]] = []
    clients = _clients()

    def make(account_id: str, region: str, service: str) -> Any:
        seen.append((account_id, region, service))
        return clients[service]

    detect_sources(
        Scope(
            account_ids=(PARTNER, ACCOUNT),
            regions=("us-west-2", REGION),
            resource_types=(),
        ),
        make,
        snapshot=_snapshot(),
    )
    places = list(dict.fromkeys((a, r) for a, r, _ in seen))
    assert places == [
        (ACCOUNT, REGION),
        (ACCOUNT, "us-west-2"),
        (PARTNER, REGION),
        (PARTNER, "us-west-2"),
    ]
    assert [s for _a, _r, s in seen[: len(PROBES)]] == [p[0] for p in PROBES]
