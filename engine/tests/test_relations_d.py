"""배치 D(스키마 2.10.0 · ADR 0002 §12 · `docs/canvas-principles.md` §17.33~37) — 「가리킨다/넘긴다」 관계 넓히기.

| 축 | 무엇 | 잇는 법 |
|---|---|---|
| ① 접두사 목록 | 라우트 목적지 `pl-…` → 이름(AWS 관리형) · 이름 + CIDR(고객 관리형) | 라우트 테이블 속성(엣지 아님) |
| ② 탄력적 IP | EIP → 주인(인스턴스 · ENI 주인 — NAT · LB · …) | `eip_association`(화면 = 주인 정보창 줄) |
| ③ API Gateway | REST·HTTP API → VPC 링크 → NLB·ALB | `vpc_link` |
| ④ RDS 프록시 | 프록시 → DB 클러스터·인스턴스 | `proxy_target` |
| ⑤ Global Accelerator | 가속기 → ALB·NLB·EC2·EIP 의 주인(API = us-west-2) | `accelerator_endpoint` |

각 축마다 세 가지를 잰다: 모양(속성·엣지) · 못 읽음(거부 = 그 노드의 사유 · 키 없음 · 불변식 ④) · 안 부를 때는 안 부른다(최소 호출).
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

import jsonschema
import rel_world as RW
from fakes import (
    ACCOUNT,
    CORE_TGW,
    EIP_BASTION,
    EIP_IDLE,
    PL_CORP,
    PL_S3,
    REGION,
    ROUTE_TABLES,
    FakeAws,
    client_error,
    factory,
)
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import GLOBAL, Scope
from nettopo.observe import OBSERVED_RESOURCE_TYPES, build_observation
from nettopo.serialize import to_json
from test_services import coverage_of, node_of, run

APP_RT = f"route_table/{ACCOUNT}/{REGION}/rtb-0111aaaa2222bbbb"


def _rt_with_corp_list() -> list[dict[str, Any]]:
    """앱 퍼블릭 테이블에 고객 관리형 목록(온프레미스 대역)으로 TGW 에 가는 라우트를 하나 더한다."""
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[0]["Routes"].append(
        {
            "DestinationPrefixListId": PL_CORP,
            "TransitGatewayId": CORE_TGW,
            "State": "active",
        }
    )
    return tables


# ------------------------------------------------------------------ ① 접두사 목록


def test_an_aws_managed_prefix_list_route_carries_its_service_name() -> None:
    client = FakeAws()
    doc = run(client)
    a = node_of(doc, APP_RT)["attributes"]
    assert a["prefix_lists"] == [f"{PL_S3} aws com.amazonaws.{REGION}.s3"]
    # AWS 관리형 목록은 항목을 묻지 않는다(S3 목록 = 수백 줄 · 고객 구성이 아니다) — 고객 관리형이 없으면 항목 키도 없다
    assert "prefix_list_entries" not in a and "prefix_list_entries_unread" not in a
    assert "get_managed_prefix_list_entries" not in client.calls
    assert node_of(doc, APP_RT)["partial"] is False


def test_a_customer_prefix_list_route_carries_its_name_and_entries() -> None:
    doc = run(FakeAws(route_tables=_rt_with_corp_list()))
    a = node_of(doc, APP_RT)["attributes"]
    assert a["prefix_lists"] == [
        f"{PL_S3} aws com.amazonaws.{REGION}.s3",
        f"{PL_CORP} customer corp-onprem",
    ]
    assert a["prefix_list_entries"] == [
        f"{PL_CORP} 192.168.10.0/24",
        f"{PL_CORP} 192.168.20.0/24",
    ]
    # 라우트 엣지는 그대로 목적지에 목록 id 를 든다(대상별로 하나 · ADR 0002 §4)
    tgw = next(
        e
        for e in doc["edges"]
        if e["type"] == "route"
        and e["source"] == APP_RT
        and e["target"].startswith("transit_gateway/")
    )
    assert PL_CORP in tgw["attributes"]["destinations"]


def test_a_denied_prefix_list_read_is_the_tables_reason_not_a_missing_name() -> None:
    """불변식 ④ — 이름을 못 읽은 목록과 이름 없는 목록이 같은 모양이면 안 된다(키가 없고 사유가 붙는다)."""
    client = FakeAws(
        errors={
            "describe_managed_prefix_lists": client_error(
                "UnauthorizedOperation", "DescribeManagedPrefixLists"
            )
        }
    )
    doc = run(client)
    rt = node_of(doc, APP_RT)
    assert rt["partial"] is True
    assert "ec2:DescribeManagedPrefixLists" in [r["api"] for r in rt["partial_reasons"]]
    assert "prefix_lists" not in rt["attributes"]
    # 목록을 안 쓰는 테이블은 그 거부와 무관하다
    other = node_of(doc, f"route_table/{ACCOUNT}/{REGION}/rtb-0222bbbb3333cccc")
    assert other["partial"] is False


def test_a_denied_entry_read_keeps_the_name_and_says_the_entries_are_unread() -> None:
    client = FakeAws(
        route_tables=_rt_with_corp_list(),
        followup_errors={
            ("get_managed_prefix_list_entries", PL_CORP): client_error(
                "UnauthorizedOperation", "GetManagedPrefixListEntries"
            )
        },
    )
    rt = node_of(run(client), APP_RT)
    a = rt["attributes"]
    assert f"{PL_CORP} customer corp-onprem" in a["prefix_lists"]
    assert a["prefix_list_entries_unread"] == [PL_CORP]
    assert a["prefix_list_entries"] == []
    assert rt["partial"] is True
    assert "ec2:GetManagedPrefixListEntries" in [
        r["api"] for r in rt["partial_reasons"]
    ]


def test_no_prefix_list_route_means_no_prefix_list_call() -> None:
    tables = copy.deepcopy(ROUTE_TABLES)
    for t in tables:
        t["Routes"] = [r for r in t["Routes"] if not r.get("DestinationPrefixListId")]
    client = FakeAws(route_tables=tables)
    doc = run(client)
    assert "describe_managed_prefix_lists" not in client.calls
    assert "prefix_lists" not in node_of(doc, APP_RT)["attributes"]


def test_a_list_this_account_cannot_see_is_not_named() -> None:
    """공유가 끊긴 목록(응답에 없다) — 이름을 지어내지 않는다. 화면은 id 그대로 말한다."""
    client = FakeAws(managed_prefix_lists=[])
    a = node_of(run(client), APP_RT)["attributes"]
    assert a["prefix_lists"] == []


# ------------------------------------------------------------------ 세계 · 픽스처(렌더러 `rel` 페이지)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "relations.json"
SCHEMA = json.loads(
    (ROOT / "schema" / "topology.schema.json").read_text(encoding="utf-8")
)
CLOCK = "2026-01-02T03:04:05Z"
SCOPE = Scope(
    account_ids=(RW.ACCOUNT,),
    regions=(RW.REGION,),
    resource_types=SUPPORTED_RESOURCE_TYPES,
)


def _world_snap(
    client: FakeAws | None = None, scope: Scope = SCOPE, fac: Any = None
) -> Any:
    return collect(scope, fac or factory(client or RW.world()), clock=lambda: CLOCK)


def _wid(kind: str, native: str) -> str:
    return f"{kind}/{RW.ACCOUNT}/{RW.REGION}/{native}"


def test_the_fixture_is_this_world_collected() -> None:
    """`fixtures/relations.json` = 이 세계를 엔진에 돌린 바이트(렌더러 하네스 `rel` 페이지 입력 · 검사 152~156).

    🔴 손으로 고친 fixture 는 엔진 모양에서 조용히 멀어진다 — 다시 쓰려면 `NETTOPO_REGEN_FIXTURE=1`.
    """
    text = to_json(_world_snap())
    if os.environ.get("NETTOPO_REGEN_FIXTURE") == "1":
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text
    assert not list(
        jsonschema.Draft202012Validator(SCHEMA).iter_errors(json.loads(text))
    )


def test_the_world_names_every_prefix_list_shape() -> None:
    doc = _world_snap().to_document()
    app = node_of(doc, _wid("route_table", RW.RT_APP))
    a = app["attributes"]
    assert a["prefix_lists"] == sorted(
        [
            f"{RW.PL_S3} aws com.amazonaws.{RW.REGION}.s3",
            f"{RW.PL_CORP} customer corp-onprem",
            f"{RW.PL_PARTNER} customer partner-nets",
        ]
    )
    assert a["prefix_list_entries"] == [f"{RW.PL_CORP} {c}" for c in RW.CORP_CIDRS]
    assert a["prefix_list_entries_unread"] == [RW.PL_PARTNER]
    assert app["partial"] is True
    main = node_of(doc, _wid("route_table", RW.RT_MAIN))["attributes"]
    # 안 쓰는 목록(DynamoDB)은 이름 표에 섞이지 않는다
    assert main["prefix_lists"] == [f"{RW.PL_S3} aws com.amazonaws.{RW.REGION}.s3"]


# ------------------------------------------------------------------ ② 탄력적 IP


def _out(doc: dict[str, Any], kind: str, source: str) -> dict[str, dict[str, Any]]:
    return {
        e["target"]: e
        for e in doc["edges"]
        if e["type"] == kind and e["source"] == source
    }


def test_an_instance_eip_belongs_to_its_instance_and_an_idle_one_to_nobody() -> None:
    client = FakeAws()
    doc = run(client)
    bastion = node_of(doc, f"elastic_ip/{ACCOUNT}/{REGION}/{EIP_BASTION}")
    assert bastion["parent_id"] == f"region/{ACCOUNT}/{REGION}"
    assert bastion["attributes"]["public_ip"] == "198.51.100.10"
    assert list(_out(doc, "eip_association", bastion["id"])) == [
        f"ec2_instance/{ACCOUNT}/{REGION}/i-0aaa1111bbbb2222"
    ]
    idle = node_of(doc, f"elastic_ip/{ACCOUNT}/{REGION}/{EIP_IDLE}")
    assert idle["attributes"]["associated"] is False
    assert not _out(doc, "eip_association", idle["id"])
    assert "association_unresolved" not in idle["attributes"]
    # 인스턴스에 붙은 EIP 만 있으면 ENI 를 묻지 않는다(최소 호출)
    assert "describe_network_interfaces" not in client.calls
    # 연결 토큰(연결할 때마다 바뀐다)은 구조에 없다(불변식 ②)
    assert "eipassoc-0aaa1111bbbb2222" not in json.dumps(doc)


def test_world_eips_reach_nat_lb_and_instances_through_their_enis() -> None:
    doc = _world_snap().to_document()
    owner = {
        k: list(_out(doc, "eip_association", _wid("elastic_ip", v)))
        for k, v in RW.EIP.items()
    }
    assert owner["bastion"] == [_wid("ec2_instance", RW.BASTION)]
    assert owner["nat"] == [_wid("nat_gateway", RW.NAT)]
    assert owner["alb"] == [_wid("load_balancer", "pub-alb")]
    assert owner["game"] == [_wid("ec2_instance", RW.GAME)]
    assert owner["idle"] == [] and owner["orphan"] == []
    assert (
        node_of(doc, _wid("elastic_ip", RW.EIP["idle"]))["attributes"]["associated"]
        is False
    )
    orphan = node_of(doc, _wid("elastic_ip", RW.EIP["orphan"]))
    assert orphan["attributes"]["association_unresolved"] == f"{RW.GONE_ENI} no_eni"
    assert (
        node_of(doc, _wid("elastic_ip", RW.EIP["alb"]))["attributes"]["service_managed"]
        == "alb"
    )
    # ENI 응답(설명·요청자)은 구조에 싣지 않는다 — 주인을 가르는 재료일 뿐이다
    assert "Interface for NAT Gateway" not in json.dumps(doc)


def test_a_denied_eni_read_makes_the_eip_owner_unread_not_missing() -> None:
    client = RW.world()
    client._errors["describe_network_interfaces"] = client_error(
        "UnauthorizedOperation", "DescribeNetworkInterfaces"
    )
    doc = _world_snap(client).to_document()
    nat = node_of(doc, _wid("elastic_ip", RW.EIP["nat"]))
    assert nat["partial"] is True
    assert "ec2:DescribeNetworkInterfaces" in [r["api"] for r in nat["partial_reasons"]]
    assert nat["attributes"]["association_unresolved"] == f"{RW.NAT_ENI} eni_unread"
    assert not _out(doc, "eip_association", nat["id"])
    # 인스턴스 EIP 는 ENI 를 묻지 않았다 — 그 거부와 무관하다
    bastion = node_of(doc, _wid("elastic_ip", RW.EIP["bastion"]))
    assert bastion["partial"] is False and _out(doc, "eip_association", bastion["id"])


def test_a_denied_address_list_is_the_regions_coverage() -> None:
    client = FakeAws(
        errors={
            "describe_addresses": client_error(
                "UnauthorizedOperation", "DescribeAddresses"
            )
        }
    )
    doc = run(client)
    cov = coverage_of(doc, "elastic_ip")
    assert cov["status"] == "denied" and cov["item_count"] is None
    assert not [n for n in doc["nodes"] if n["type"] == "elastic_ip"]


# ------------------------------------------------------------------ ③ API Gateway VPC 링크


def test_world_apis_reach_their_lbs_through_vpc_links() -> None:
    doc = _world_snap().to_document()
    rest = node_of(doc, _wid("api_gateway", RW.REST_API))
    http = node_of(doc, _wid("api_gateway", RW.HTTP_API))
    assert (
        rest["parent_id"] == f"region/{RW.ACCOUNT}/{RW.REGION}"
        and rest["attributes"]["protocol"] == "REST"
    )
    assert http["attributes"]["protocol"] == "HTTP"
    r = _out(doc, "vpc_link", rest["id"])
    assert list(r) == [_wid("load_balancer", "edge-nlb")]
    assert r[_wid("load_balancer", "edge-nlb")]["attributes"] == {
        "vpc_link_ids": [RW.VPC_LINK],
        "vpc_link_names": ["orders-link"],
    }
    h = _out(doc, "vpc_link", http["id"])
    assert list(h) == [_wid("load_balancer", "int-alb")]
    assert h[_wid("load_balancer", "int-alb")]["attributes"]["vpc_link_names"] == [
        "web-link"
    ]
    # 지어내지 않는다 — 단계 변수 링크 · Cloud Map 통합은 까닭 줄
    assert rest["attributes"]["unresolved_vpc_links"] == [
        "${stageVariables.vpcLinkId} stage_variable"
    ]
    assert http["attributes"]["unresolved_vpc_links"] == [
        "srv-0abc1234def70201 cloud_map"
    ]
    # 통합 URI(내부 주소)는 싣지 않는다
    assert "http://edge-nlb/orders" not in json.dumps(doc)


def test_api_gateway_reads_are_partial_failures_per_api() -> None:
    client = RW.world()
    client._followup_errors[("get_resources", RW.REST_API)] = client_error(
        "AccessDeniedException", "GetResources"
    )
    client._followup_errors[("get_integrations", RW.HTTP_API)] = client_error(
        "AccessDeniedException", "GetIntegrations"
    )
    doc = _world_snap(client).to_document()
    for api in (RW.REST_API, RW.HTTP_API):
        n = node_of(doc, _wid("api_gateway", api))
        assert n["partial"] is True and "apigateway:GET" in [
            x["api"] for x in n["partial_reasons"]
        ]
        assert "vpc_link_ids" not in n["attributes"]
        assert not _out(doc, "vpc_link", n["id"])


def test_a_denied_vpc_link_list_keeps_the_api_and_says_so() -> None:
    client = RW.world()
    client._errors["get_vpc_links"] = client_error(
        "AccessDeniedException", "GetVpcLinks"
    )
    doc = _world_snap(client).to_document()
    rest = node_of(doc, _wid("api_gateway", RW.REST_API))
    assert rest["partial"] is True
    assert rest["attributes"]["vpc_link_ids"] == sorted(
        [RW.VPC_LINK, "${stageVariables.vpcLinkId}"]
    )
    # REST 는 링크의 대상을 링크 목록에서만 안다 — 못 읽었으면 선이 없다(「링크 없음」으로 적지 않는다 · 사유가 있다)
    assert not _out(doc, "vpc_link", rest["id"])
    # HTTP 는 대상이 통합 URI 다 — 링크 이름은 없지만 선은 있다
    http = node_of(doc, _wid("api_gateway", RW.HTTP_API))
    assert http["partial"] is True
    assert (
        _out(doc, "vpc_link", http["id"])[_wid("load_balancer", "int-alb")][
            "attributes"
        ]["vpc_link_names"]
        == []
    )


def test_a_rest_and_an_http_list_denial_are_separate_coverage_rows() -> None:
    client = RW.world()
    client._errors["get_apis"] = client_error("AccessDeniedException", "GetApis")
    doc = _world_snap(client).to_document()
    assert coverage_of(doc, "api_gateway_v2")["status"] == "denied"
    assert coverage_of(doc, "api_gateway")["status"] == "ok"
    assert node_of(doc, _wid("api_gateway", RW.REST_API))


# ------------------------------------------------------------------ ④ RDS 프록시


def test_world_proxies_reach_the_cluster_or_the_instance() -> None:
    doc = _world_snap().to_document()
    aur = node_of(doc, _wid("rds_db_proxy", RW.PROXY_AURORA))
    assert aur["parent_id"] == f"vpc/{RW.ACCOUNT}/{RW.REGION}/{RW.VPC}"
    assert aur["attributes"]["subnet_ids"] == sorted(
        [RW.SN["data-a"][0], RW.SN["data-b"][0]]
    )
    # 클러스터 대상 = 클러스터 선 하나(멤버마다 선을 긋지 않는다)
    assert list(_out(doc, "proxy_target", aur["id"])) == [
        _wid("rds_db_cluster", RW.AURORA)
    ]
    assert aur["attributes"]["target_instance_ids"] == list(RW.AURORA_MEMBERS)
    orders = node_of(doc, _wid("rds_db_proxy", RW.PROXY_ORDERS))
    assert list(_out(doc, "proxy_target", orders["id"])) == [
        _wid("rds_db_instance", RW.RDS)
    ]
    assert orders["attributes"]["unresolved_proxy_targets"] == [
        "legacy-db not_collected"
    ]
    assert orders["attributes"]["unresolved_refs"] == ["legacy-db"]
    # 비밀 위치·역할 ARN · 상태는 싣지 않는다
    text = json.dumps(doc)
    assert "NTSECRET" not in text and "rel-proxy-role" not in text


def test_a_denied_proxy_target_read_is_the_proxys_reason() -> None:
    client = RW.world()
    client._followup_errors[("describe_db_proxy_targets", RW.PROXY_ORDERS)] = (
        client_error("AccessDenied", "DescribeDBProxyTargets")
    )
    doc = _world_snap(client).to_document()
    n = node_of(doc, _wid("rds_db_proxy", RW.PROXY_ORDERS))
    assert n["partial"] is True and "rds:DescribeDBProxyTargets" in [
        r["api"] for r in n["partial_reasons"]
    ]
    assert "target_count" not in n["attributes"] and not _out(
        doc, "proxy_target", n["id"]
    )
    # 다른 프록시는 그 거부와 무관하다
    assert _out(doc, "proxy_target", _wid("rds_db_proxy", RW.PROXY_AURORA))


# ------------------------------------------------------------------ ⑤ Global Accelerator


GA_ID = f"global_accelerator/{RW.ACCOUNT}/{GLOBAL}/0a1b2c3d-1111-2222-3333-0abc1234def7"


def test_the_accelerator_api_is_called_in_us_west_2_whatever_the_scan_regions() -> None:
    """🔴 수집 리전이 ap-northeast-2 하나여도 가속기 클라이언트는 us-west-2 다(`Spec.region`) — 다른 서비스는 수집 리전 그대로."""
    seen: list[tuple[str, str]] = []
    client = RW.world()

    def fac(account: str, region: str, service: str) -> Any:
        seen.append((service, region))
        return client

    collect(
        Scope((RW.ACCOUNT,), ("ap-northeast-2",), SUPPORTED_RESOURCE_TYPES),
        fac,
        clock=lambda: CLOCK,
    )
    assert {r for s, r in seen if s == "globalaccelerator"} == {"us-west-2"}
    # #129 — Cloud WAN(networkmanager)도 us-west-2 고정이다(`test_relations_129.py` 가 따로 잰다)
    # 2.12.0 — wafv2 는 두 리전이다(CloudFront 범위 = us-east-1 고정 · 리전 범위·ALB web ACL = 수집 리전 · `test_waf.py` 가 따로 잰다)
    pinned = {"globalaccelerator", "networkmanager", "wafv2"}
    assert {r for s, r in seen if s not in pinned} == {"ap-northeast-2"}
    assert {r for s, r in seen if s == "wafv2"} == {"ap-northeast-2", "us-east-1"}


def test_world_accelerator_reaches_lb_and_the_eips_owner_and_names_what_it_cannot() -> (
    None
):
    doc = _world_snap().to_document()
    ga = node_of(doc, GA_ID)
    assert ga["parent_id"] == f"account/{RW.ACCOUNT}" and ga["region"] is None
    out = _out(doc, "accelerator_endpoint", GA_ID)
    assert set(out) == {_wid("load_balancer", "pub-alb"), _wid("ec2_instance", RW.GAME)}
    # EIP 엔드포인트 = 그 EIP 의 주인 · 어떤 EIP 를 거쳤나는 속성
    assert out[_wid("ec2_instance", RW.GAME)]["attributes"]["via_eip"] == RW.EIP["game"]
    assert out[_wid("load_balancer", "pub-alb")]["attributes"] == {
        "endpoint_type": "lb",
        "endpoint_region": RW.REGION,
        "listener_ports": ["TCP 443"],
    }
    a = ga["attributes"]
    # 수집 범위 밖 리전의 엔드포인트 · 이 스냅샷에 없는 LB — 지어내지 않고 까닭과 리전을 남긴다
    assert a["unresolved_endpoints"] == [
        "eu-alb not_in_scope eu-west-1",
        "retired-nlb not_collected",
    ]
    assert a["empty_endpoint_regions"] == ["eu-west-2"]
    # 그룹마다 등록된 대상(리전 · 종류 · 이름) — 정보창이 리전마다 한 줄로 말하는 재료(범위 밖 리전의 대상도 종류를 안다)
    assert a["endpoint_targets"] == sorted(
        [
            f"{RW.REGION} alb pub-alb",
            f"{RW.REGION} eip {RW.EIP['game']}",
            f"{RW.REGION} nlb retired-nlb",
            "eu-west-1 alb eu-alb",
        ]
    )
    assert a["listener_ports"] == ["TCP 443"]
    # 헬스·배포 상태는 구조에 없다(불변식 ②)
    text = json.dumps(ga)
    assert "HEALTHY" not in text and "DEPLOYED" not in text


def test_an_unowned_eip_endpoint_is_named_not_drawn() -> None:
    client = RW.world()
    key, items = client._data["describe_addresses"]
    client._data["describe_addresses"] = (
        key,
        [a for a in items if a["AllocationId"] != RW.EIP["game"]]
        + [
            {
                "AllocationId": RW.EIP["game"],
                "Domain": "vpc",
                "PublicIp": "198.51.100.34",
            }
        ],
    )
    doc = _world_snap(client).to_document()
    a = node_of(doc, GA_ID)["attributes"]
    assert f"{RW.EIP['game']} eip_no_owner" in a["unresolved_endpoints"]
    assert _wid("ec2_instance", RW.GAME) not in _out(doc, "accelerator_endpoint", GA_ID)


def test_accelerator_reads_are_partial_failures_of_that_accelerator() -> None:
    client = RW.world()
    client._followup_errors[("list_endpoint_groups", RW.GA_LISTENER)] = client_error(
        "AccessDeniedException", "ListEndpointGroups"
    )
    doc = _world_snap(client).to_document()
    ga = node_of(doc, GA_ID)
    assert ga["partial"] is True and "globalaccelerator:ListEndpointGroups" in [
        r["api"] for r in ga["partial_reasons"]
    ]
    assert "endpoint_regions" not in ga["attributes"] and not _out(
        doc, "accelerator_endpoint", GA_ID
    )
    client = RW.world()
    client._errors["list_accelerators"] = client_error(
        "AccessDeniedException", "ListAccelerators"
    )
    doc = _world_snap(client).to_document()
    assert coverage_of(doc, "global_accelerator")["status"] == "denied"


def test_the_accelerator_is_one_node_across_scan_regions() -> None:
    scope = Scope((RW.ACCOUNT,), (RW.REGION, "us-west-2"), SUPPORTED_RESOURCE_TYPES)
    doc = _world_snap(scope=scope).to_document()
    assert [n["id"] for n in doc["nodes"]].count(GA_ID) == 1


# ------------------------------------------------------------------ 관측 — 배치 D 종류는 상태가 없다


def test_batch_d_types_have_no_observation_rows() -> None:
    topology = _world_snap().to_document()
    state = collect(
        Scope((RW.ACCOUNT,), (RW.REGION,), OBSERVED_RESOURCE_TYPES),
        factory(RW.world()),
        clock=lambda: "2026-01-02T04:00:00Z",
        with_state=True,
    )
    obs, _ = build_observation(
        state, topology=topology, observed_at="2026-01-02T04:00:00Z", tool_version="t"
    )
    kinds = {re.split(r"[/|]", o["target_id"])[0] for o in obs["observations"]}
    batch_d = {
        "elastic_ip",
        "api_gateway",
        "rds_db_proxy",
        "global_accelerator",
        "eip_association",
        "vpc_link",
        "proxy_target",
        "accelerator_endpoint",
    }
    present = {n["type"] for n in topology["nodes"]} | {
        e["type"] for e in topology["edges"]
    }
    assert batch_d <= present, batch_d - present
    assert not kinds & batch_d, kinds & batch_d
