"""정보창 개편 ① 라우팅 데이터(스키마 2.13.0 · 사용자 결정 2026-10-24 「콘솔이 보여 주는 값은 우리도 보여 준다」).

콘솔 화면과 같은 칸을 구조에 싣는다 — VPC 라우팅 테이블 「라우팅」 탭(VPC 내부 경로 포함) · TGW 라우팅 테이블 네 탭 · TGW 어태치먼트 상세 ·
TGW 「세부 정보」 · 피어링 DNS · VPC DNS·DHCP. 각 종류마다 ① 값이 콘솔과 같은 모양인가 ② 새 후속 호출이 거부되면 「꺼짐/없음」이 아니라
**키 없음 + partial** 인가(불변식 ④)를 잰다.
"""

from __future__ import annotations

import copy

from fakes import (
    ACCOUNT,
    APP_VPC,
    CORE_TGW,
    DHCP_OPTIONS_ID,
    EGRESS_NAT,
    EGRESS_VPC,
    PL_CORP,
    PRIVATE_SUBNET,
    PUBLIC_SUBNET,
    REGION,
    ROUTE_TABLES,
    TGW_ROUTE_TABLE,
    TGW_RT_ROUTES,
    TRANSIT_GATEWAY_ATTACHMENTS,
    TRANSIT_GATEWAYS,
    VPCS,
    PEERINGS,
    FakeAws,
    client_error,
)
from test_services import node_of, run

APP_RT = ROUTE_TABLES[0]["RouteTableId"]
EGRESS_RT = ROUTE_TABLES[1]["RouteTableId"]
VPC_ATT = "tgw-attach-0aaa1111bbbb8888"
VPN_ATT = "tgw-attach-0bbb2222cccc9999"
DXGW_ATT = "tgw-attach-0ddd4444eeee1111"


def _id(kind: str, native: str) -> str:
    return f"{kind}/{ACCOUNT}/{REGION}/{native}"


def _reasons(node: dict) -> list[tuple[str, str]]:
    return [(r["code"], r["api"]) for r in node.get("partial_reasons") or []]


# ------------------------------------------------------------ VPC 라우팅 테이블


def test_the_route_table_lists_every_route_including_the_vpc_local_route() -> None:
    """🔴 콘솔 「라우팅 (3)」 = 우리 경로 3줄 — VPC 내부(local) 경로가 빠지면 「경로가 빠졌다」로 읽힌다(실측 2026-10-24: 콘솔 3 · 우리 2)."""
    attrs = node_of(run(FakeAws()), _id("route_table", APP_RT))["attributes"]
    assert "10.10.0.0/16 local active -" in attrs["routes"], attrs["routes"]
    assert (
        len(attrs["routes"]) == attrs["route_count"] == len(ROUTE_TABLES[0]["Routes"])
    )
    assert attrs["routes"] == sorted(attrs["routes"])


def test_each_route_keeps_its_own_state_and_origin() -> None:
    """같은 대상으로 가는 정상 경로와 죽은 경로 — 경로 표는 각자의 상태를, 선은 죽은 목적지만 든다."""
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[1]["Routes"] += [
        {
            "DestinationCidrBlock": "10.98.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "active",
            "Origin": "CreateRoute",
        },
        {
            "DestinationCidrBlock": "10.99.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "blackhole",
            "Origin": "CreateRoute",
        },
    ]
    doc = run(FakeAws(route_tables=tables))
    attrs = node_of(doc, _id("route_table", EGRESS_RT))["attributes"]
    assert f"10.98.0.0/16 {EGRESS_NAT} active CreateRoute" in attrs["routes"]
    assert f"10.99.0.0/16 {EGRESS_NAT} blackhole CreateRoute" in attrs["routes"]
    edge = next(
        e
        for e in doc["edges"]
        if e["type"] == "route"
        and e["source"] == _id("route_table", EGRESS_RT)
        and e["target"].endswith(EGRESS_NAT)
    )
    assert edge["attributes"]["blackhole"] is True  # 선의 판정(대상 묶음 OR)은 그대로
    assert edge["attributes"]["blackhole_destinations"] == ["10.99.0.0/16"]


def test_gateway_edge_associations_and_propagating_vgws_are_read() -> None:
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[1]["Associations"].append(
        {
            "RouteTableAssociationId": "rtbassoc-0gw",
            "GatewayId": "igw-0aaa1111bbbb4444",
            "AssociationState": {"State": "associated"},
        }
    )
    tables[1]["PropagatingVgws"] = [
        {"GatewayId": "vgw-0bbb"},
        {"GatewayId": "vgw-0aaa"},
    ]
    attrs = node_of(run(FakeAws(route_tables=tables)), _id("route_table", EGRESS_RT))[
        "attributes"
    ]
    assert attrs["edge_association_gateway_ids"] == ["igw-0aaa1111bbbb4444"]
    assert attrs["propagating_vgw_ids"] == ["vgw-0aaa", "vgw-0bbb"]


# ------------------------------------------------------------ TGW 라우팅 테이블


def test_the_tgw_route_table_carries_the_four_console_tabs() -> None:
    attrs = node_of(
        run(FakeAws()), _id("transit_gateway_route_table", TGW_ROUTE_TABLE)
    )["attributes"]
    # 「연결」 탭 — 끊긴 연결은 뺀다 · 콘솔 열 순서(어태치먼트 · 종류 · 리소스 · 상태)
    assert attrs["associations"] == [
        f"{VPC_ATT} vpc {APP_VPC} associated",
        f"{VPN_ATT} vpn vpn-0aaa1111bbbb3333 associated",
    ]
    # 「전파」 탭 — 꺼진 전파도 상태와 함께 보인다(콘솔처럼) · id 목록은 켜진 것만 그대로
    assert attrs["propagations"] == [
        f"{VPC_ATT} vpc {APP_VPC} enabled",
        f"{VPN_ATT} vpn vpn-0aaa1111bbbb3333 disabled",
    ]
    assert attrs["propagation_attachment_ids"] == [VPC_ATT]
    # 「경로」 탭 — 블랙홀은 어태치먼트 자리 `-` · 지워진 경로는 없다
    assert attrs["routes"] == [
        "0.0.0.0/0 - - - static blackhole",
        f"10.10.0.0/16 {VPC_ATT} vpc {APP_VPC} propagated active",
    ]
    assert (
        len(TGW_RT_ROUTES[TGW_ROUTE_TABLE]) == 3
    )  # 그중 하나(deleted)가 빠졌다 = 재료가 셋이다
    # 「접두사 목록 참조」 탭
    assert attrs["prefix_list_references"] == [
        f"{PL_CORP} {ACCOUNT} {VPN_ATT} vpn vpn-0aaa1111bbbb3333 available no"
    ]


def test_ecmp_routes_list_one_line_per_attachment() -> None:
    routes = {
        TGW_ROUTE_TABLE: [
            {
                "DestinationCidrBlock": "192.168.0.0/16",
                "Type": "propagated",
                "State": "active",
                "TransitGatewayAttachments": [
                    {
                        "TransitGatewayAttachmentId": VPN_ATT,
                        "ResourceType": "vpn",
                        "ResourceId": "vpn-b",
                    },
                    {
                        "TransitGatewayAttachmentId": "tgw-attach-0aaa",
                        "ResourceType": "vpn",
                        "ResourceId": "vpn-a",
                    },
                ],
            }
        ]
    }
    attrs = node_of(
        run(FakeAws(tgw_rt_routes=routes)),
        _id("transit_gateway_route_table", TGW_ROUTE_TABLE),
    )["attributes"]
    assert attrs["routes"] == [
        "192.168.0.0/16 tgw-attach-0aaa vpn vpn-a propagated active",
        f"192.168.0.0/16 {VPN_ATT} vpn vpn-b propagated active",
    ]


# ------------------------------------------------------------ TGW 어태치먼트 · TGW


def test_a_vpc_attachment_carries_its_subnets_and_options() -> None:
    """🔴 정보창이 「연결된 서브넷 — 수집 안 함」이라 말하던 자리 — 콘솔은 「서브넷 2개」(실측 2026-10-24)."""
    doc = run(FakeAws())
    attrs = node_of(doc, _id("transit_gateway_attachment", VPC_ATT))["attributes"]
    assert attrs["subnet_ids"] == sorted([PUBLIC_SUBNET, PRIVATE_SUBNET])
    assert attrs["dns_support"] == "enable"
    assert attrs["appliance_mode_support"] == "disable"
    assert attrs["ipv6_support"] == "disable"
    assert attrs["security_group_referencing_support"] == "disable"
    assert attrs["resource_owner_id"] == ACCOUNT
    vpn = node_of(doc, _id("transit_gateway_attachment", VPN_ATT))["attributes"]
    assert "subnet_ids" not in vpn  # VPC 가 아닌 어태치먼트는 묻지 않는다


def test_the_attachment_names_its_associated_route_table() -> None:
    atts = copy.deepcopy(TRANSIT_GATEWAY_ATTACHMENTS)
    atts[0]["Association"] = {
        "TransitGatewayRouteTableId": TGW_ROUTE_TABLE,
        "State": "associated",
    }
    attrs = node_of(
        run(FakeAws(transit_gateway_attachments=atts)),
        _id("transit_gateway_attachment", VPC_ATT),
    )["attributes"]
    assert attrs["association_route_table_id"] == TGW_ROUTE_TABLE
    assert attrs["association_state"] == "associated"


def test_a_denied_vpc_attachment_read_is_a_reason_not_no_subnets() -> None:
    """🔴 `DescribeTransitGatewayVpcAttachments` 거부 = VPC 어태치먼트만 partial + 키 없음 — 「서브넷 0개」로 적으면 거짓이다."""
    fake = FakeAws(
        errors={
            "describe_transit_gateway_vpc_attachments": client_error(
                "UnauthorizedOperation", "DescribeTransitGatewayVpcAttachments"
            )
        }
    )
    doc = run(fake)
    vpc_att = node_of(doc, _id("transit_gateway_attachment", VPC_ATT))
    assert vpc_att["partial"] is True
    assert ("access_denied", "ec2:DescribeTransitGatewayVpcAttachments") in _reasons(
        vpc_att
    )
    assert "subnet_ids" not in vpc_att["attributes"]
    assert "dns_support" not in vpc_att["attributes"]
    assert node_of(doc, _id("transit_gateway_attachment", VPN_ATT))["partial"] is False


def test_no_vpc_attachment_means_no_vpc_attachment_call() -> None:
    atts = [a for a in TRANSIT_GATEWAY_ATTACHMENTS if a["ResourceType"] != "vpc"]
    fake = FakeAws(transit_gateway_attachments=atts)
    run(fake)
    assert "describe_transit_gateway_vpc_attachments" not in fake.calls


def test_the_tgw_carries_the_console_details() -> None:
    tgws = copy.deepcopy(TRANSIT_GATEWAYS)
    tgws[0]["Options"].update(
        {
            "AssociationDefaultRouteTableId": TGW_ROUTE_TABLE,
            "DefaultRouteTablePropagation": "enable",
            "PropagationDefaultRouteTableId": TGW_ROUTE_TABLE,
            "AutoAcceptSharedAttachments": "disable",
            "VpnEcmpSupport": "enable",
            "DnsSupport": "enable",
            "MulticastSupport": "disable",
            "TransitGatewayCidrBlocks": ["10.255.1.0/24", "10.255.0.0/24"],
            "EncryptionSupport": {"EncryptionState": "disable"},
        }
    )
    attrs = node_of(
        run(FakeAws(transit_gateways=tgws)), _id("transit_gateway", CORE_TGW)
    )["attributes"]
    assert attrs["association_default_route_table_id"] == TGW_ROUTE_TABLE
    assert attrs["propagation_default_route_table_id"] == TGW_ROUTE_TABLE
    assert attrs["vpn_ecmp_support"] == "enable"
    assert attrs["auto_accept_shared_attachments"] == "disable"
    assert attrs["transit_gateway_cidr_blocks"] == ["10.255.0.0/24", "10.255.1.0/24"]
    assert attrs["encryption_support"] == "disable"
    assert attrs["description"] == "core network"


# ------------------------------------------------------------ 피어링


def test_the_peering_carries_dns_options_and_ipv6() -> None:
    peerings = copy.deepcopy(PEERINGS)
    peerings[0]["RequesterVpcInfo"]["PeeringOptions"] = {
        "AllowDnsResolutionFromRemoteVpc": True
    }
    peerings[0]["AccepterVpcInfo"]["PeeringOptions"] = {
        "AllowDnsResolutionFromRemoteVpc": False
    }
    peerings[0]["AccepterVpcInfo"]["Ipv6CidrBlockSet"] = [
        {"Ipv6CidrBlock": "2600:1f18::/56"}
    ]
    attrs = node_of(
        run(FakeAws(peerings=peerings)),
        _id("vpc_peering_connection", "pcx-0aaa1111bbbb1111"),
    )["attributes"]
    assert attrs["requester_allow_dns_resolution_from_remote_vpc"] is True
    assert attrs["accepter_allow_dns_resolution_from_remote_vpc"] is False
    assert attrs["accepter_ipv6_cidr_blocks"] == ["2600:1f18::/56"]
    assert "requester_ipv6_cidr_blocks" not in attrs


# ------------------------------------------------------------ VPC


def test_the_vpc_carries_dns_dhcp_and_tenancy() -> None:
    vpcs = copy.deepcopy(VPCS)
    for v in vpcs:
        v["DhcpOptionsId"] = DHCP_OPTIONS_ID
        v["InstanceTenancy"] = "default"
    doc = run(FakeAws(vpcs=vpcs))
    app = node_of(doc, _id("vpc", APP_VPC))["attributes"]
    egress = node_of(doc, _id("vpc", EGRESS_VPC))["attributes"]
    assert app["enable_dns_support"] is True and app["enable_dns_hostnames"] is True
    assert (
        egress["enable_dns_support"] is True and egress["enable_dns_hostnames"] is False
    )
    assert app["dhcp_options_id"] == DHCP_OPTIONS_ID
    assert app["dhcp_options"] == [
        "domain-name ec2.internal",
        "domain-name-servers AmazonProvidedDNS",
    ]
    assert app["instance_tenancy"] == "default"


def test_a_denied_vpc_attribute_is_a_reason_not_dns_off() -> None:
    """🔴 `DescribeVpcAttribute` 거부 = **그 VPC 만** partial + 키 없음 — `false` 로 적으면 DNS 를 끈 VPC 로 읽힌다."""
    fake = FakeAws(
        followup_errors={
            ("describe_vpc_attribute", APP_VPC): client_error(
                "UnauthorizedOperation", "DescribeVpcAttribute"
            )
        }
    )
    doc = run(fake)
    app = node_of(doc, _id("vpc", APP_VPC))
    assert app["partial"] is True
    assert _reasons(app) == [("access_denied", "ec2:DescribeVpcAttribute")]
    assert "enable_dns_support" not in app["attributes"]
    assert "enable_dns_hostnames" not in app["attributes"]
    egress = node_of(doc, _id("vpc", EGRESS_VPC))
    assert (
        egress["partial"] is False
        and egress["attributes"]["enable_dns_support"] is True
    )


def test_a_denied_dhcp_read_marks_every_vpc_and_drops_only_the_dhcp_contents() -> None:
    vpcs = copy.deepcopy(VPCS)
    for v in vpcs:
        v["DhcpOptionsId"] = DHCP_OPTIONS_ID
    fake = FakeAws(
        vpcs=vpcs,
        errors={
            "describe_dhcp_options": client_error(
                "UnauthorizedOperation", "DescribeDhcpOptions"
            )
        },
    )
    doc = run(fake)
    for vpc in (APP_VPC, EGRESS_VPC):
        node = node_of(doc, _id("vpc", vpc))
        assert ("access_denied", "ec2:DescribeDhcpOptions") in _reasons(node)
        assert "dhcp_options" not in node["attributes"]
        assert (
            node["attributes"]["dhcp_options_id"] == DHCP_OPTIONS_ID
        )  # 목록 응답의 id 는 남는다


def test_a_disassociated_secondary_cidr_is_not_drawn() -> None:
    vpcs = copy.deepcopy(VPCS)
    vpcs[0]["CidrBlockAssociationSet"] = [
        {"CidrBlock": "10.20.0.0/16", "CidrBlockState": {"State": "associated"}},
        {"CidrBlock": "10.21.0.0/16", "CidrBlockState": {"State": "disassociated"}},
    ]
    attrs = node_of(run(FakeAws(vpcs=vpcs)), _id("vpc", EGRESS_VPC))["attributes"]
    assert attrs["cidr_blocks"] == ["10.20.0.0/16"]


# ------------------------------------------------------------ 결정론(거부 사유)


def test_a_per_call_encoded_denial_token_does_not_change_the_snapshot() -> None:
    """🔴 EC2 거부 사유 끝의 「Encoded authorization failure message: <매번 다른 토큰>」 — 같은 거부를 두 번 수집하면 구조 지문이
    바뀌었다(실측 2026-10-24 · `DescribeVpcAttribute`). 토큰은 자리만 남기고 뺀다(불변식 ②)."""

    def denied(token: str) -> FakeAws:
        return FakeAws(
            followup_errors={
                ("describe_vpc_attribute", APP_VPC): client_error(
                    "UnauthorizedOperation",
                    "DescribeVpcAttribute",
                    f"You are not authorized. Encoded authorization failure message: {token}",
                )
            }
        )

    a = node_of(run(denied("fNac-Po_t0OPtTKO1")), _id("vpc", APP_VPC))
    b = node_of(run(denied("MIttkWYUfCJU4ek6W")), _id("vpc", APP_VPC))
    assert a == b
    assert (
        "Encoded authorization failure message: (생략)"
        in a["partial_reasons"][0]["message"]
    )
