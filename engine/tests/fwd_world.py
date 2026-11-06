"""넘김 선(스키마 2.8.0 · 사용자 결정 2026-10-20 P1-3·P1-4)의 가짜 세계 — 검사 VPC 와 바퀴살 VPC 하나씩.

`test_forwarding.py` 가 이 세계로 엔진을 재고, 같은 세계를 엔진에 돌린 산출물이 **그대로** `fixtures/forwarding.json`
(렌더러 하네스 입력 · 검사 149)이다 — `test_the_fixture_is_this_world_collected` 가 바이트를 대조한다
(`NETTOPO_REGEN_FIXTURE=1` 이면 다시 쓴다). 계정 = 마스킹된 예시 계열(`111122223333` · 불변식 ⑤).

| 무엇 | 모양 | 잇는 것 |
|---|---|---|
| 바퀴살 VPC `fwd-spoke` | 서브넷 `gwlbe-a`(GWLB 엔드포인트) · `app-a`(앱 EC2 · 기본 라우트 → GWLB 엔드포인트) · `edge-a`(→ IGW) | 라우트 `app-a` → `vpce-…401`(이미 있던 `route` 엣지) |
| GWLB 엔드포인트 `vpce-…401` | `GatewayLoadBalancer` · 서비스 `vpce-svc-…aaa` | `endpoint_provider` → `inspect-gwlb`(제공자 = 검사 VPC 의 GWLB · 서비스 이름으로) |
| GWLB 엔드포인트 `vpce-…403` | 서비스 `vpce-svc-…ccc`(제공자를 수집하지 않았다) | 선 없음 · `unresolved_refs` = 그 서비스 이름 |
| 검사 VPC `fwd-inspection` | GWLB `inspect-gwlb`(서브넷 `appl-a`) · 대상 그룹 `appliance-tg`(instance · GENEVE 6081) | `load_balancer_target` → 어플라이언스 EC2(#116) |
| 엔드포인트 서비스 구성 | `vpce-svc-…aaa` → `inspect-gwlb` ARN(제공자 계정 = 같은 계정) | LB 속성 `endpoint_service_names` |
| 인터페이스 엔드포인트 `vpce-…404`(P2-1) | 서비스 `vpce-svc-…bbb` — 제공자 = 검사 VPC 의 NLB `partner-nlb`(엔드포인트 서비스 구성) | `endpoint_provider` → `partner-nlb` · 주인 계정 = 이 계정 |
| 인터페이스 엔드포인트 `vpce-…405`(P2-1) | 서비스 `vpce-svc-…ddd` — 범위 밖 파트너 계정 `444455556666` | 선 없음 · `unresolved_refs` · `service_owner` = 파트너 계정 |
| NVA `nva-1`(P2-2 · `edge-a` 의 EC2) | 앱 라우트 테이블 `10.99.0.0/16` → `eni-…7601`(그 인스턴스의 ENI — AWS 는 인스턴스 라우트에도 ENI 를 준다) · `10.98.0.0/16` → 없는 ENI | `route` → `ec2_instance`(`via_eni`) · 없는 ENI = `unresolved_route_targets` |
| Network Firewall `spoke-fw`(P1-4) | 방화벽 서브넷 `fw-a` · 엔드포인트 `vpce-…402`(AWS 관리 GWLB 엔드포인트 · `SyncStates` 의 `EndpointId`) | 라우트 `web-a` → `vpce-…402` · `endpoint_provider` → 방화벽(`firewall_endpoint`) |
"""

from __future__ import annotations

from typing import Any

from fakes import FakeAws

ACCOUNT = "111122223333"
REGION = "us-east-1"
SPOKE = "vpc-0abc1234def57001"
INSPECT = "vpc-0abc1234def57002"
SN = {
    # 이름: (id, CIDR, AZ, VPC)
    "gwlbe-a": ("subnet-0abc1234def57011", "10.30.0.0/24", "us-east-1a", SPOKE),
    "app-a": ("subnet-0abc1234def57012", "10.30.1.0/24", "us-east-1a", SPOKE),
    "edge-a": ("subnet-0abc1234def57013", "10.30.3.0/24", "us-east-1a", SPOKE),
    "fw-a": ("subnet-0abc1234def57014", "10.30.2.0/24", "us-east-1a", SPOKE),
    "web-a": ("subnet-0abc1234def57015", "10.30.4.0/24", "us-east-1a", SPOKE),
    "appl-a": ("subnet-0abc1234def57021", "10.40.1.0/24", "us-east-1a", INSPECT),
}
IGW = "igw-0abc1234def57041"
RT_APP = "rtb-0abc1234def57051"
RT_EDGE = "rtb-0abc1234def57052"
RT_INSPECT = "rtb-0abc1234def57053"
RT_WEB = "rtb-0abc1234def57054"
GWLBE = "vpce-0abc1234def57401"
GWLBE_ORPHAN = "vpce-0abc1234def57403"
NFW_EP = "vpce-0abc1234def57402"
IF_EP = "vpce-0abc1234def57404"
IF_EP_PARTNER = "vpce-0abc1234def57405"
SVC_NLB = f"com.amazonaws.vpce.{REGION}.vpce-svc-0abc1234def57bbb"
SVC_PARTNER = f"com.amazonaws.vpce.{REGION}.vpce-svc-0abc1234def57ddd"
PARTNER = "444455556666"
NFW_SVC = f"com.amazonaws.vpce.{REGION}.vpce-svc-0abc1234def57fff"  # AWS 계정이 제공한다 — 이 세계의 엔드포인트 서비스 구성에 없다
FW_NAME = "spoke-fw"
FW_ARN = f"arn:aws:network-firewall:{REGION}:{ACCOUNT}:firewall/{FW_NAME}"
SVC = f"com.amazonaws.vpce.{REGION}.vpce-svc-0abc1234def57aaa"
SVC_ORPHAN = f"com.amazonaws.vpce.{REGION}.vpce-svc-0abc1234def57ccc"
APP = "i-0abc1234def57101"
WEB = "i-0abc1234def57102"
NVA = "i-0abc1234def57103"
NVA_ENI = "eni-0abc1234def57601"
GONE_ENI = "eni-0abc1234def57699"
APPLIANCE = "i-0abc1234def57501"
_ELB = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}"
GWLB_ARN = f"{_ELB}:loadbalancer/gwy/inspect-gwlb/1a2b3c4d5e6f7091"
NLB_ARN = f"{_ELB}:loadbalancer/net/partner-nlb/1a2b3c4d5e6f7092"
TG_APPLIANCE = f"{_ELB}:targetgroup/appliance-tg/0f1e2d3c4b5a6991"


def _tags(name: str) -> list[dict[str, str]]:
    return [{"Key": "Name", "Value": name}]


def _rt(
    rid: str, vpc: str, name: str, subnets: list[str], routes: list[dict[str, Any]]
) -> dict[str, Any]:
    cidr = "10.30.0.0/16" if vpc == SPOKE else "10.40.0.0/16"
    return {
        "RouteTableId": rid,
        "VpcId": vpc,
        "Associations": [
            {
                "RouteTableAssociationId": f"rtbassoc-0{rid[-4:]}{i}",
                "SubnetId": SN[s][0],
                "AssociationState": {"State": "associated"},
            }
            for i, s in enumerate(subnets)
        ],
        "Routes": [
            {"DestinationCidrBlock": cidr, "GatewayId": "local", "State": "active"},
            *routes,
        ],
        "Tags": _tags(name),
    }


def world(
    *,
    errors: dict[str, Exception] | None = None,
    endpoint_services: list[dict[str, Any]] | None = None,
    consumer_services: dict[str, str] | None = None,
) -> FakeAws:
    subnets = [
        {
            "SubnetId": sid,
            "VpcId": vpc,
            "CidrBlock": cidr,
            "AvailabilityZone": zone,
            "AvailabilityZoneId": "use1-az1",
            "State": "available",
            "MapPublicIpOnLaunch": False,
            "AvailableIpAddressCount": 240,
            "Tags": _tags(f"fwd-{name}"),
        }
        for name, (sid, cidr, zone, vpc) in SN.items()
    ]
    route_tables = [
        _rt(
            RT_APP,
            SPOKE,
            "fwd-app-rt",
            ["app-a"],
            [
                # 바퀴살 앱 서브넷의 기본 라우트 → GWLB 엔드포인트(검사를 거쳐 나간다). AWS 는 엔드포인트 대상을 `GatewayId` 에
                #   싣는다(ap-southeast-2 qa-lb-targets 실측 2026-10-20 — `VpcEndpointId` 가 아니다)
                {
                    "DestinationCidrBlock": "0.0.0.0/0",
                    "GatewayId": GWLBE,
                    "State": "active",
                },
                # 사내망 → NVA(P2-2) — 인스턴스로 보낸 라우트에도 AWS 는 `NetworkInterfaceId` 를 같이 준다
                {
                    "DestinationCidrBlock": "10.99.0.0/16",
                    "InstanceId": NVA,
                    "NetworkInterfaceId": NVA_ENI,
                    "State": "active",
                },
                # 묻는 ENI 가 없다(다른 계정 ENI · 막 지운 ENI 의 전파 지연) — 주인을 지어내지 않는다
                {
                    "DestinationCidrBlock": "10.98.0.0/16",
                    "NetworkInterfaceId": GONE_ENI,
                    "State": "active",
                },
            ],
        ),
        _rt(
            RT_WEB,
            SPOKE,
            "fwd-web-rt",
            ["web-a"],
            [
                # 웹 서브넷의 기본 라우트 → Network Firewall 엔드포인트(P1-4 · 같은 `GatewayId` 자리)
                {
                    "DestinationCidrBlock": "0.0.0.0/0",
                    "GatewayId": NFW_EP,
                    "State": "active",
                },
            ],
        ),
        _rt(
            RT_EDGE,
            SPOKE,
            "fwd-edge-rt",
            ["gwlbe-a", "edge-a", "fw-a"],
            [
                {
                    "DestinationCidrBlock": "0.0.0.0/0",
                    "GatewayId": IGW,
                    "State": "active",
                },
            ],
        ),
        _rt(RT_INSPECT, INSPECT, "fwd-inspection-rt", ["appl-a"], []),
    ]
    reservations = [
        {
            "OwnerId": ACCOUNT,
            "Instances": [
                {
                    "InstanceId": iid,
                    "InstanceType": "t3.small" if iid == APP else "c6in.large",
                    "State": {"Name": "running"},
                    "Placement": {"AvailabilityZone": "us-east-1a"},
                    "SubnetId": SN[subnet][0],
                    "VpcId": SN[subnet][3],
                    "PrivateIpAddress": ip,
                    "SecurityGroups": [],
                    "Tags": _tags(name),
                }
            ],
        }
        for iid, subnet, ip, name in [
            (APP, "app-a", "10.30.1.10", "app-1"),
            (WEB, "web-a", "10.30.4.10", "web-1"),
            (NVA, "edge-a", "10.30.3.10", "nva-1"),
            (APPLIANCE, "appl-a", "10.40.1.10", "appliance-1"),
        ]
    ]
    services = [
        {
            "ServiceName": SVC,
            "ServiceId": "vpce-svc-0abc1234def57aaa",
            "ServiceType": [{"ServiceType": "GatewayLoadBalancer"}],
            "ServiceState": "Available",
            "GatewayLoadBalancerArns": [GWLB_ARN],
            "AcceptanceRequired": False,
        },
        {
            "ServiceName": SVC_NLB,
            "ServiceId": "vpce-svc-0abc1234def57bbb",
            "ServiceType": [{"ServiceType": "Interface"}],
            "ServiceState": "Available",
            "NetworkLoadBalancerArns": [NLB_ARN],
            "AcceptanceRequired": True,
        },
    ]
    return FakeAws.empty(
        vpcs=[
            {
                "VpcId": SPOKE,
                "CidrBlock": "10.30.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": _tags("fwd-spoke"),
            },
            {
                "VpcId": INSPECT,
                "CidrBlock": "10.40.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": _tags("fwd-inspection"),
            },
        ],
        subnets=subnets,
        route_tables=route_tables,
        internet_gateways=[
            {
                "InternetGatewayId": IGW,
                "Attachments": [{"VpcId": SPOKE, "State": "available"}],
                "Tags": _tags("fwd-igw"),
            }
        ],
        vpc_endpoints=[
            {
                "VpcEndpointId": eid,
                "VpcEndpointType": "GatewayLoadBalancer",
                "VpcId": SPOKE,
                "ServiceName": svc,
                "State": "available",
                "RouteTableIds": [],
                "SubnetIds": [SN["gwlbe-a"][0]],
                "PrivateDnsEnabled": False,
                "Tags": _tags(name),
            }
            for eid, svc, name in [
                (GWLBE, SVC, "inspect-gwlbe"),
                (GWLBE_ORPHAN, SVC_ORPHAN, "partner-gwlbe"),
            ]
        ]
        # 인터페이스 엔드포인트(P2-1) — 같은 계정 NLB 가 제공하는 서비스 · 범위 밖 파트너 서비스
        + [
            {
                "VpcEndpointId": eid,
                "VpcEndpointType": "Interface",
                "VpcId": SPOKE,
                "ServiceName": svc,
                "State": "available",
                "RouteTableIds": [],
                "SubnetIds": [SN["app-a"][0]],
                "PrivateDnsEnabled": False,
                "Tags": _tags(name),
            }
            for eid, svc, name in [
                (IF_EP, SVC_NLB, "orders-api"),
                (IF_EP_PARTNER, SVC_PARTNER, "saas-partner"),
            ]
        ]
        # 방화벽 엔드포인트 — AWS 가 관리하는 GWLB 엔드포인트(DescribeVpcEndpoints 에 `RequesterManaged` 로 나온다 · us-east-1 실측 2026-10-20)
        + [
            {
                "VpcEndpointId": NFW_EP,
                "VpcEndpointType": "GatewayLoadBalancer",
                "VpcId": SPOKE,
                "ServiceName": NFW_SVC,
                "State": "available",
                "RouteTableIds": [],
                "SubnetIds": [SN["fw-a"][0]],
                "PrivateDnsEnabled": False,
                "RequesterManaged": True,
                "Tags": [],
            }
        ],
        network_firewalls=[{"FirewallName": FW_NAME, "FirewallArn": FW_ARN}],
        firewall_details={
            FW_ARN: {
                "Firewall": {
                    "FirewallName": FW_NAME,
                    "FirewallArn": FW_ARN,
                    "FirewallPolicyArn": f"arn:aws:network-firewall:{REGION}:{ACCOUNT}:firewall-policy/spoke-fw-policy",
                    "VpcId": SPOKE,
                    "SubnetMappings": [{"SubnetId": SN["fw-a"][0]}],
                    "DeleteProtection": False,
                },
                "FirewallStatus": {
                    "Status": "READY",
                    "SyncStates": {
                        "us-east-1a": {
                            "Attachment": {
                                "SubnetId": SN["fw-a"][0],
                                "EndpointId": NFW_EP,
                                "Status": "READY",
                            }
                        }
                    },
                },
            }
        },
        load_balancers=[
            {
                "LoadBalancerName": "inspect-gwlb",
                "LoadBalancerArn": GWLB_ARN,
                "Scheme": "internal",
                "Type": "gateway",
                "VpcId": INSPECT,
                "State": {"Code": "active"},
                "AvailabilityZones": [
                    {"ZoneName": "us-east-1a", "SubnetId": SN["appl-a"][0]}
                ],
            },
            {
                "LoadBalancerName": "partner-nlb",
                "LoadBalancerArn": NLB_ARN,
                "Scheme": "internal",
                "Type": "network",
                "VpcId": INSPECT,
                "State": {"Code": "active"},
                "AvailabilityZones": [
                    {"ZoneName": "us-east-1a", "SubnetId": SN["appl-a"][0]}
                ],
            },
        ],
        listeners={
            GWLB_ARN: [{"ListenerArn": f"{GWLB_ARN}/l/1"}],
            NLB_ARN: [
                {"ListenerArn": f"{NLB_ARN}/l/1", "Protocol": "TCP", "Port": 443}
            ],
        },
        target_groups={
            GWLB_ARN: [
                {
                    "TargetGroupName": "appliance-tg",
                    "TargetGroupArn": TG_APPLIANCE,
                    "TargetType": "instance",
                    "Port": 6081,
                }
            ]
        },
        target_health={
            TG_APPLIANCE: [
                {
                    "Target": {"Id": APPLIANCE, "Port": 6081},
                    "TargetHealth": {"State": "healthy"},
                }
            ]
        },
        instances=reservations,
        web_acls={},
        endpoint_services=services if endpoint_services is None else endpoint_services,
        consumer_services={
            SVC: ACCOUNT,
            SVC_NLB: ACCOUNT,
            SVC_PARTNER: PARTNER,
            **(consumer_services or {}),
        },
        # NVA 의 ENI(P2-2) — 부착 인스턴스가 주인이다
        network_interfaces=[
            {
                "NetworkInterfaceId": NVA_ENI,
                "VpcId": SPOKE,
                "SubnetId": SN["edge-a"][0],
                "AvailabilityZone": "us-east-1a",
                "PrivateIpAddress": "10.30.3.10",
                "PrivateIpAddresses": [
                    {"PrivateIpAddress": "10.30.3.10", "Primary": True}
                ],
                "InterfaceType": "interface",
                "RequesterId": "",
                "Description": "nva-1 primary",
                "TagSet": [],
                "Attachment": {"InstanceId": NVA, "InstanceOwnerId": ACCOUNT},
            }
        ],
        errors=errors,
    )
