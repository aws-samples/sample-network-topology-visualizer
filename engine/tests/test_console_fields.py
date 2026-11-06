"""콘솔식 정보창의 재료(사용자 결정 2026-10-24 「AWS 콘솔의 정보 표현을 따르자 · 콘솔이 보여 주는 값이면 우리도 보여 준다」).

각 검사는 **콘솔 화면과 같은 수·같은 칸**을 잰다(실측 2026-10-24 · 콘솔 비교).
기존 수(묶음 수 · 한도 단위)와 값이 달라지는 입력을 쓴다 — 같아지는 입력이면 새 칸을 옛 수로 되돌려도 통과한다(미측정).
거부 경로는 「없음」과 「못 읽음」을 가른다(불변식 ④).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fakes import ACCOUNT, REGION, FakeAws, client_error
from nettopo.services import cloudfront as cf_module
from nettopo.services import direct_connect_gateway as dxgw_module
from nettopo.services import load_balancer as lb_module
from nettopo.services import nat_gateway as nat_module
from nettopo.services import network_acl as nacl_module
from nettopo.services import security_group as sg_module
from nettopo.services import vpc_endpoint as vpce_module
from nettopo.services import vpn_connection as vpn_module
from nettopo.services import waf_web_acl as waf_module
from nettopo.services.base import Ctx, Fetched

CTX = Ctx(
    account_id=ACCOUNT,
    region=REGION,
    scope_accounts=frozenset({ACCOUNT}),
    scope_regions=frozenset({REGION}),
)


def _attrs(module: Any, item: dict[str, Any]) -> dict[str, Any]:
    return module.build(item, CTX).node.attributes


# ------------------------------------------------------------------ 보안 그룹 · NACL


def test_sg_rule_rows_count_like_the_console() -> None:
    """콘솔 = 소스 하나가 한 줄(인바운드 4 · 아웃바운드 2) — 묶음(IpPermission) 수는 3 · 1 이다(실측 모양)."""
    a = _attrs(
        sg_module,
        {
            "GroupId": "sg-0aaa1111bbbb2222c",
            "GroupName": "alb",
            "VpcId": "vpc-0aaa1111",
            "OwnerId": ACCOUNT,
            "IpPermissions": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                },
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "web\tfront"}],
                },
                {
                    "IpProtocol": "-1",
                    "IpRanges": [{"CidrIp": "10.0.0.0/16"}],
                    "PrefixListIds": [{"PrefixListId": "pl-0aaa1111"}],
                },
            ],
            "IpPermissionsEgress": [
                {
                    "IpProtocol": "-1",
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                    "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
                },
            ],
        },
    )
    assert (a["ingress_rule_count"], a["egress_rule_count"]) == (3, 1)
    assert (a["ingress_rule_entry_count"], a["egress_rule_entry_count"]) == (4, 2)
    assert len(a["ingress_rules"]) == 4
    # 한 줄 = IP 버전 · 프로토콜 · 포트 · 소스 · 설명(설명의 탭은 공백으로 — 칸이 밀리지 않는다)
    assert "IPv4\ttcp\t80-80\t0.0.0.0/0\tweb front" in a["ingress_rules"]
    assert "\t-1\t\tpl-0aaa1111\t" in a["ingress_rules"]
    assert all(len(r.split("\t")) == 5 for r in a["ingress_rules"] + a["egress_rules"])
    assert "IPv6\t-1\t\t::/0\t" in a["egress_rules"]


def test_sg_reference_to_another_account_names_that_account() -> None:
    a = _attrs(
        sg_module,
        {
            "GroupId": "sg-0aaa1111bbbb2222c",
            "VpcId": "vpc-0aaa1111",
            "OwnerId": ACCOUNT,
            "IpPermissions": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5432,
                    "ToPort": 5432,
                    "UserIdGroupPairs": [
                        {"GroupId": "sg-0ccc3333", "UserId": "444455556666"}
                    ],
                },
            ],
            "IpPermissionsEgress": [],
        },
    )
    assert a["ingress_rules"] == ["\ttcp\t5432-5432\t444455556666/sg-0ccc3333\t"]


def test_nacl_rows_keep_the_default_deny_like_the_console() -> None:
    """콘솔 인바운드 = 기본 `*` 거부(32767)까지 6 · 한도 단위 수(`ingress_entry_count`)는 5 다."""
    entries = [
        {
            "RuleNumber": n,
            "Egress": False,
            "RuleAction": "allow",
            "Protocol": "6",
            "CidrBlock": "0.0.0.0/0",
            "PortRange": {"From": n, "To": n},
        }
        for n in (443, 80, 200, 300)
    ] + [
        {
            "RuleNumber": 100,
            "Egress": False,
            "RuleAction": "allow",
            "Protocol": "1",
            "CidrBlock": "0.0.0.0/0",
            "IcmpTypeCode": {"Type": -1, "Code": -1},
        },
        {
            "RuleNumber": 32767,
            "Egress": False,
            "RuleAction": "deny",
            "Protocol": "-1",
            "CidrBlock": "0.0.0.0/0",
        },
        {
            "RuleNumber": 32767,
            "Egress": True,
            "RuleAction": "deny",
            "Protocol": "-1",
            "CidrBlock": "0.0.0.0/0",
        },
    ]
    a = _attrs(
        nacl_module,
        {
            "NetworkAclId": "acl-0aaa1111",
            "VpcId": "vpc-0aaa1111",
            "OwnerId": ACCOUNT,
            "IsDefault": False,
            "Associations": [],
            "Entries": entries,
        },
    )
    assert a["ingress_entry_count"] == 5
    assert len(a["ingress_entries"]) == 6
    # 순서 = 규칙 번호(AWS 가 평가하는 순서) · 마지막이 기본 거부
    assert [r.split("\t")[0] for r in a["ingress_entries"]] == [
        "80",
        "100",
        "200",
        "300",
        "443",
        "32767",
    ]
    assert a["ingress_entries"][-1] == "32767\t-1\t\t0.0.0.0/0\tdeny"
    assert a["ingress_entries"][1] == "100\t1\ticmp -1/-1\t0.0.0.0/0\tallow"
    assert a["egress_entries"] == ["32767\t-1\t\t0.0.0.0/0\tdeny"]


# ------------------------------------------------------------------ VPN 터널


def _vpn(**telemetry_status: str) -> dict[str, Any]:
    return {
        "VpnConnectionId": "vpn-0aaa1111",
        "State": "available",
        "Type": "ipsec.1",
        "CustomerGatewayId": "cgw-0aaa1111",
        "TransitGatewayId": "tgw-0aaa1111",
        "Options": {
            "TunnelOptions": [
                {"OutsideIpAddress": "3.0.0.2", "TunnelInsideCidr": "169.254.10.4/30"},
                {
                    "OutsideIpAddress": "3.0.0.1",
                    "TunnelInsideCidr": "169.254.10.0/30",
                    "PreSharedKey": "s3cret-psk",
                },
            ]
        },
        "VgwTelemetry": [
            {
                "OutsideIpAddress": ip,
                "Status": status,
                "LastStatusChange": dt.datetime(
                    2026, 10, 24, 1, 2, 3, tzinfo=dt.timezone.utc
                ),
                "StatusMessage": "IPSEC IS DOWN",
                "AcceptedRouteCount": 0,
            }
            for ip, status in (
                ("3.0.0.1", telemetry_status.get("a", "DOWN")),
                ("3.0.0.2", telemetry_status.get("b", "DOWN")),
            )
        ],
    }


def test_vpn_tunnel_inside_cidrs_are_structure_and_status_is_not() -> None:
    """내부 CIDR 은 설정(구조) · UP/DOWN 은 상태 단 전용 — 구조 바이트에 터널 상태 글자가 없다(불변식 ②)."""
    a = _attrs(vpn_module, _vpn())
    assert a["tunnel_inside_cidrs"] == [
        "3.0.0.1\t169.254.10.0/30\t",
        "3.0.0.2\t169.254.10.4/30\t",
    ]
    text = repr(a)
    assert (
        "DOWN" not in text and "IPSEC IS DOWN" not in text and "s3cret-psk" not in text
    )


def test_vpn_state_attributes_name_each_tunnel() -> None:
    rows = vpn_module.state_attributes(_vpn(a="UP", b="DOWN"))["tunnel_status"]
    assert rows == [
        "3.0.0.1\tUP\t2026-10-24T01:02:03+00:00\tIPSEC IS DOWN\t0",
        "3.0.0.2\tDOWN\t2026-10-24T01:02:03+00:00\tIPSEC IS DOWN\t0",
    ]
    # 원격 측정이 없으면 「모른다」 — 빈 목록(「터널 없음」)을 짓지 않는다
    assert vpn_module.state_attributes({**_vpn(), "VgwTelemetry": []}) == {}


def test_observation_carries_tunnels_and_keeps_the_folded_verdict() -> None:
    from types import SimpleNamespace

    from nettopo.observe import Observation, _with_tunnels

    judged = Observation(
        "vpn_connection/x/y/vpn-0aaa1111", "node", "down", "터널이 전부 DOWN", None, 0
    )
    node = SimpleNamespace(attributes=vpn_module.state_attributes(_vpn()))
    out = _with_tunnels(judged, node).to_dict()
    assert out["status"] == "down"
    assert [t["status"] for t in out["tunnels"]] == ["DOWN", "DOWN"]
    assert out["tunnels"][0] == {
        "outside_ip": "3.0.0.1",
        "status": "DOWN",
        "last_status_change": "2026-10-24T01:02:03+00:00",
        "status_message": "IPSEC IS DOWN",
        "accepted_route_count": 0,
    }
    # 줄이 없으면 키가 없다
    assert (
        "tunnels" not in _with_tunnels(judged, SimpleNamespace(attributes={})).to_dict()
    )


# ------------------------------------------------------------------ DX 게이트웨이 연결


def test_dxgw_association_rows_carry_allowed_prefixes() -> None:
    a = _attrs(
        dxgw_module,
        {
            "directConnectGatewayId": "0a0b0c0d-0aaa-1111-bbbb-0000aaaa1111",
            "directConnectGatewayState": "available",
            "ownerAccount": ACCOUNT,
            "directConnectGatewayAssociations": [
                {
                    "associationId": "a-2",
                    "associationState": "associated",
                    "associatedGateway": {
                        "id": "tgw-0aaa",
                        "type": "transitGateway",
                        "region": "us-east-1",
                        "ownerAccount": ACCOUNT,
                    },
                    "allowedPrefixesToDirectConnectGateway": [
                        {"cidr": "10.2.0.0/16"},
                        {"cidr": "10.1.0.0/16"},
                    ],
                },
                {
                    "associationId": "a-1",
                    "associationState": "disassociated",
                    "associatedGateway": {
                        "id": "vgw-0bbb",
                        "type": "virtualPrivateGateway",
                        "region": "us-west-2",
                        "ownerAccount": ACCOUNT,
                    },
                },
            ],
        },
    )
    assert a["gateway_associations"] == [
        f"a-1\tvgw-0bbb\tvirtualPrivateGateway\tus-west-2\t{ACCOUNT}\tdisassociated\t",
        f"a-2\ttgw-0aaa\ttransitGateway\tus-east-1\t{ACCOUNT}\tassociated\t10.1.0.0/16,10.2.0.0/16",
    ]
    # 살아 있는 연결만의 id 목록은 그대로(지워진 VGW 는 관계가 아니다)
    assert a["associated_virtual_private_gateway_ids"] == []


# ------------------------------------------------------------------ CloudFront


def _dist(**extra: Any) -> dict[str, Any]:
    return {
        "Id": "E0AAA1111",
        "DomainName": "d1aaaa.cloudfront.net",
        "Comment": "NTA lab #43",
        "Origins": {
            "Items": [
                {
                    "Id": "web",
                    "DomainName": "app-1.us-east-1.elb.amazonaws.com",
                    "CustomOriginConfig": {},
                },
                {
                    "Id": "assets",
                    "DomainName": "assets.s3.us-east-1.amazonaws.com",
                    "S3OriginConfig": {},
                    "OriginAccessControlId": "OAC1",
                    "OriginPath": "/static",
                },
            ]
        },
        "DefaultCacheBehavior": {
            "TargetOriginId": "web",
            "ViewerProtocolPolicy": "redirect-to-https",
            "CachePolicyId": "pol-default",
        },
        "CacheBehaviors": {
            "Items": [
                {
                    "PathPattern": "/assets/*",
                    "TargetOriginId": "assets",
                    "ViewerProtocolPolicy": "https-only",
                    "CachePolicyId": "pol-opt",
                },
            ]
        },
        **extra,
    }


def _cf_enriched(fake: FakeAws) -> dict[str, Any]:
    (got,) = cf_module.enrich(fake, [Fetched(_dist(), ())])
    return {"payload": got.payload, "reasons": got.reasons}


def test_cloudfront_carries_comment_origins_and_behaviors_with_names() -> None:
    fake = FakeAws(
        cache_policies=[
            {
                "CachePolicy": {
                    "Id": "pol-default",
                    "CachePolicyConfig": {"Name": "Managed-CachingDisabled"},
                }
            },
            {
                "CachePolicy": {
                    "Id": "pol-opt",
                    "CachePolicyConfig": {"Name": "Managed-CachingOptimized"},
                }
            },
            {"CachePolicy": {"Id": "pol-unused", "CachePolicyConfig": {"Name": "x"}}},
        ],
        oacs=[{"Id": "OAC1", "Name": "assets-oac"}],
    )
    got = _cf_enriched(fake)
    assert got["reasons"] == ()
    a = _attrs(cf_module, got["payload"])
    assert a["comment"] == "NTA lab #43"
    # 원본 = 설정 순서(콘솔 표 순서) · 유형 · 원본 경로 · OAC id · 이름
    assert a["origins"] == [
        "web\tapp-1.us-east-1.elb.amazonaws.com\tcustom\t\t\t",
        "assets\tassets.s3.us-east-1.amazonaws.com\ts3\t/static\tOAC1\tassets-oac",
    ]
    # 동작 = 우선순위 순 · 기본 동작은 맨 끝(경로 칸 비움) · 캐시 정책 이름
    assert a["behaviors"] == [
        "0\t/assets/*\tassets\thttps-only\tpol-opt\tManaged-CachingOptimized",
        "1\t\tweb\tredirect-to-https\tpol-default\tManaged-CachingDisabled",
    ]
    # 두 장(Marker)을 끝까지 돌았다 — 첫 장만 읽으면 둘째 장의 정책 이름이 빈다
    assert ("list_cache_policies", {"Marker": "page-2"}) in fake.direct_calls


def test_cloudfront_denied_policy_names_mark_the_distribution_partial() -> None:
    fake = FakeAws(
        errors={
            "list_cache_policies": client_error("AccessDenied", "ListCachePolicies")
        },
        oacs=[{"Id": "OAC1", "Name": "assets-oac"}],
    )
    got = _cf_enriched(fake)
    assert [r.api for r in got["reasons"]] == ["cloudfront:ListCachePolicies"]
    a = _attrs(cf_module, got["payload"])
    # 이름 칸만 비고 id 는 남는다 — 사유는 노드가 든다(「정책 없음」이 아니다)
    assert a["behaviors"][1] == "1\t\tweb\tredirect-to-https\tpol-default\t"
    assert a["origins"][1].endswith("\tOAC1\tassets-oac")


def test_cloudfront_without_policy_ids_does_not_ask() -> None:
    fake = FakeAws()
    item = _dist(
        DefaultCacheBehavior={
            "TargetOriginId": "web",
            "ViewerProtocolPolicy": "allow-all",
        },
        CacheBehaviors={"Items": []},
    )
    item["Origins"]["Items"][1].pop("OriginAccessControlId")
    cf_module.enrich(fake, [Fetched(item, ())])
    assert (
        "list_cache_policies" not in fake.calls
        and "list_origin_access_controls" not in fake.calls
    )


# ------------------------------------------------------------------ WAF


def test_waf_rules_carry_priority_and_action() -> None:
    a = waf_module._detail_attrs(
        {
            "Rules": [
                {"Name": "rate", "Priority": 2, "Action": {"Block": {}}},
                {
                    "Name": "common",
                    "Priority": 1,
                    "OverrideAction": {"None": {}},
                    "Statement": {
                        "ManagedRuleGroupStatement": {
                            "VendorName": "AWS",
                            "Name": "AWSManagedRulesCommonRuleSet",
                        }
                    },
                },
                {"Name": "probe", "Priority": 3, "OverrideAction": {"Count": {}}},
                {"Name": "allow-office", "Priority": 0, "Action": {"Allow": {}}},
            ],
            "DefaultAction": {"Allow": {}},
        }
    )
    assert a["rules"] == [
        "0\tallow-office\tallow",
        "1\tcommon\tnone",
        "2\trate\tblock",
        "3\tprobe\toverride:count",
    ]
    assert a["rule_names"] == ["allow-office", "common", "rate", "probe"]


# ------------------------------------------------------------------ VPC 엔드포인트 · NAT


def _vpce() -> dict[str, Any]:
    return {
        "VpcEndpointId": "vpce-0aaa1111",
        "VpcEndpointType": "Interface",
        "ServiceName": "com.amazonaws.us-east-1.ssm",
        "VpcId": "vpc-0aaa1111",
        "OwnerId": ACCOUNT,
        "State": "available",
        "SubnetIds": ["subnet-0bbb", "subnet-0aaa"],
        "NetworkInterfaceIds": ["eni-0bbb", "eni-0aaa"],
        "Groups": [
            {"GroupId": "sg-0bbb", "GroupName": "vpce"},
            {"GroupId": "sg-0aaa", "GroupName": "base"},
        ],
    }


def test_vpce_subnet_rows_come_from_its_enis() -> None:
    fake = FakeAws(
        network_interfaces=[
            {
                "NetworkInterfaceId": "eni-0aaa",
                "SubnetId": "subnet-0aaa",
                "AvailabilityZone": "us-east-1a",
                "PrivateIpAddress": "10.0.1.10",
            },
            {
                "NetworkInterfaceId": "eni-0bbb",
                "SubnetId": "subnet-0bbb",
                "AvailabilityZone": "us-east-1b",
                "PrivateIpAddress": "10.0.2.10",
            },
        ]
    )
    (got,) = vpce_module._enrich_enis(fake, [Fetched(_vpce(), ())])
    assert got.reasons == ()
    a = _attrs(vpce_module, got.payload)
    assert a["subnet_addresses"] == [
        "subnet-0aaa\tus-east-1a\t10.0.1.10\teni-0aaa",
        "subnet-0bbb\tus-east-1b\t10.0.2.10\teni-0bbb",
    ]
    assert a["security_groups"] == ["sg-0aaa\tbase", "sg-0bbb\tvpce"]
    assert a["service_name"] == "com.amazonaws.us-east-1.ssm"


def test_vpce_denied_eni_read_is_a_reason_not_no_addresses() -> None:
    fake = FakeAws(
        errors={
            "describe_network_interfaces": client_error(
                "UnauthorizedOperation", "DescribeNetworkInterfaces"
            )
        }
    )
    (got,) = vpce_module._enrich_enis(fake, [Fetched(_vpce(), ())])
    assert [r.api for r in got.reasons] == ["ec2:DescribeNetworkInterfaces"]
    assert "subnet_addresses" not in _attrs(vpce_module, got.payload)


def test_nat_address_rows_put_the_primary_first() -> None:
    a = _attrs(
        nat_module,
        {
            "NatGatewayId": "nat-0aaa1111",
            "State": "available",
            "SubnetId": "subnet-0aaa",
            "VpcId": "vpc-0aaa1111",
            "ConnectivityType": "public",
            "NatGatewayAddresses": [
                {
                    "PublicIp": "3.0.0.9",
                    "PrivateIp": "10.0.0.9",
                    "NetworkInterfaceId": "eni-0b",
                    "AllocationId": "eipalloc-0b",
                    "IsPrimary": False,
                },
                {
                    "PublicIp": "3.0.0.5",
                    "PrivateIp": "10.0.0.5",
                    "NetworkInterfaceId": "eni-0a",
                    "AllocationId": "eipalloc-0a",
                    "IsPrimary": True,
                },
            ],
        },
    )
    assert a["addresses"] == [
        "3.0.0.5\t10.0.0.5\teni-0a\teipalloc-0a\ttrue",
        "3.0.0.9\t10.0.0.9\teni-0b\teipalloc-0b\tfalse",
    ]


# ------------------------------------------------------------------ 로드밸런서


_ALB = "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/web/abc"
_HTTPS = f"{_ALB}/listener/https"
_HTTP = f"{_ALB}/listener/http"
_TG = "arn:aws:elasticloadbalancing:us-east-1:111122223333:targetgroup/web-tg/def"


def _lb_fake(**kw: Any) -> FakeAws:
    return FakeAws(
        listeners={
            _ALB: [
                {
                    "ListenerArn": _HTTPS,
                    "Protocol": "HTTPS",
                    "Port": 443,
                    "SslPolicy": "ELBSecurityPolicy-TLS13-1-2-2021-06",
                    "Certificates": [{"CertificateArn": "cert-c-default"}],
                    "DefaultActions": [
                        {"Type": "forward", "TargetGroupArn": _TG, "Order": 1}
                    ],
                },
                {
                    "ListenerArn": _HTTP,
                    "Protocol": "HTTP",
                    "Port": 80,
                    "DefaultActions": [
                        {
                            "Type": "redirect",
                            "Order": 1,
                            "RedirectConfig": {
                                "Protocol": "HTTPS",
                                "Host": "#{host}",
                                "Port": "443",
                                "Path": "/#{path}",
                                "StatusCode": "HTTP_301",
                            },
                        }
                    ],
                },
            ]
        },
        target_groups={
            _ALB: [
                {
                    "TargetGroupName": "web-tg",
                    "TargetGroupArn": _TG,
                    "Protocol": "HTTP",
                    "Port": 80,
                    "TargetType": "instance",
                    "HealthCheckProtocol": "HTTP",
                    "HealthCheckPort": "traffic-port",
                    "HealthCheckPath": "/health",
                    "HealthyThresholdCount": 5,
                    "UnhealthyThresholdCount": 2,
                    "HealthCheckIntervalSeconds": 30,
                    "HealthCheckTimeoutSeconds": 5,
                    "Matcher": {"HttpCode": "200"},
                    "HealthCheckEnabled": True,
                }
            ]
        },
        lb_rules={
            _HTTPS: [
                {"RuleArn": "r1", "IsDefault": False},
                {"RuleArn": "r2", "IsDefault": False},
                {"RuleArn": "r0", "IsDefault": True},
            ],
            _HTTP: [{"RuleArn": "r9", "IsDefault": True}],
        },
        listener_certificates={
            _HTTPS: [
                {
                    "CertificateArn": "cert-c-sni",
                    "IsDefault": False,
                },
                {
                    "CertificateArn": "cert-c-default",
                    "IsDefault": True,
                },
            ]
        },
        **kw,
    )


def _lb_item() -> dict[str, Any]:
    return {
        "LoadBalancerArn": _ALB,
        "LoadBalancerName": "web",
        "Type": "application",
        "Scheme": "internet-facing",
        "DNSName": "web-1.us-east-1.elb.amazonaws.com",
        "State": {"Code": "active"},
        "AvailabilityZones": [],
    }


def test_lb_listener_rows_read_like_the_console() -> None:
    (got,) = lb_module.enrich(_lb_fake(), [Fetched(_lb_item(), ())])
    assert got.reasons == ()
    a = _attrs(lb_module, got.payload)
    assert a["listener_details"] == [
        "HTTP\t80\tredirect:HTTPS://#{host}:443/#{path} HTTP_301\t1\t\t",
        "HTTPS\t443\tforward:web-tg\t3\tELBSecurityPolicy-TLS13-1-2-2021-06\t"
        "cert-c-default,cert-c-sni",
    ]
    assert a["target_group_details"] == [
        "web-tg\tHTTP\t80\tinstance\tHTTP\ttraffic-port\t/health\t5\t2\t30\t5\t200\ttrue"
    ]


def test_lb_denied_rules_leave_the_count_blank_and_say_why() -> None:
    fake = _lb_fake(
        followup_errors={
            ("describe_rules", _HTTPS): client_error("AccessDenied", "DescribeRules")
        }
    )
    (got,) = lb_module.enrich(fake, [Fetched(_lb_item(), ())])
    assert [r.api for r in got.reasons] == ["elasticloadbalancing:DescribeRules"]
    rows = _attrs(lb_module, got.payload)["listener_details"]
    # 거부된 리스너의 규칙 수는 빈 칸 — 「0」이 아니다 · 다른 리스너는 그대로 센다
    assert rows[1].split("\t")[3] == "" and rows[0].split("\t")[3] == "1"
