"""A fake world for forwarding edges: one inspection VPC and one spoke VPC.

`test_forwarding.py` measures the engine against this world, and running the
engine over the same world produces `fixtures/forwarding.json` byte for byte;
`test_the_fixture_is_this_world_collected` compares those bytes, and rewrites
the fixture when `NETTOPO_REGEN_FIXTURE=1` is set. The account numbers come
from the reserved example range (`111122223333`).

| What | Shape | What it links |
|---|---|---|
| Spoke VPC `fwd-spoke` | Subnets `gwlbe-a` (GWLB endpoint), `app-a` (application EC2, default route to the GWLB endpoint) and `edge-a` (to the IGW) | Route `app-a` to `vpce-…401`, an edge of the existing `route` kind |
| GWLB endpoint `vpce-…401` | `GatewayLoadBalancer`, service `vpce-svc-…aaa` | `endpoint_provider` to `inspect-gwlb`, matched by service name, so the provider is the GWLB in the inspection VPC |
| GWLB endpoint `vpce-…403` | Service `vpce-svc-…ccc`, whose provider was not collected | No edge; `unresolved_refs` holds that service name |
| Inspection VPC `fwd-inspection` | GWLB `inspect-gwlb` (subnet `appl-a`) and target group `appliance-tg` (instance targets, GENEVE 6081) | `load_balancer_target` to the appliance EC2 |
| Endpoint service configuration | `vpce-svc-…aaa` to the `inspect-gwlb` ARN, provider account the same account | The load balancer attribute `endpoint_service_names` |
| Interface endpoint `vpce-…404` | Service `vpce-svc-…bbb`, provided by `partner-nlb`, the NLB in the inspection VPC, through an endpoint service configuration | `endpoint_provider` to `partner-nlb`, owner account this account |
| Interface endpoint `vpce-…405` | Service `vpce-svc-…ddd` in partner account `444455556666`, which is out of scope | No edge; `unresolved_refs`, and `service_owner` is the partner account |
| NVA `nva-1`, an EC2 instance in `edge-a` | The application route table sends `10.99.0.0/16` to `eni-…7601`, that instance's ENI, because AWS reports an ENI for instance routes as well, and `10.98.0.0/16` to an ENI that does not exist | `route` to `ec2_instance` (`via_eni`); the missing ENI lands in `unresolved_route_targets` |
| Network Firewall `spoke-fw` | Firewall subnet `fw-a` and endpoint `vpce-…402`, an AWS-managed GWLB endpoint taken from `EndpointId` in `SyncStates` | Route `web-a` to `vpce-…402`, and `endpoint_provider` to the firewall (`firewall_endpoint`) |
"""

from __future__ import annotations

from typing import Any

from fakes import FakeAws

ACCOUNT = "111122223333"
REGION = "us-east-1"
SPOKE = "vpc-0abc1234def57001"
INSPECT = "vpc-0abc1234def57002"
SN = {
    # name: (id, CIDR, AZ, VPC)
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
NFW_SVC = f"com.amazonaws.vpce.{REGION}.vpce-svc-0abc1234def57fff"  # provided by an AWS account, so it has no endpoint service configuration in this world
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
                # The spoke application subnet's default route goes to the GWLB
                #   endpoint, so traffic leaves through inspection. AWS reports
                #   an endpoint target in `GatewayId`, not in `VpcEndpointId`.
                {
                    "DestinationCidrBlock": "0.0.0.0/0",
                    "GatewayId": GWLBE,
                    "State": "active",
                },
                # On-premises range to the NVA. AWS reports a
                #   `NetworkInterfaceId` for instance routes as well.
                {
                    "DestinationCidrBlock": "10.99.0.0/16",
                    "InstanceId": NVA,
                    "NetworkInterfaceId": NVA_ENI,
                    "State": "active",
                },
                # The ENI being asked about is absent, either because it belongs
                #   to another account or because a just-deleted ENI has not
                #   propagated yet. We do not invent an owner for it.
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
                # The web subnet's default route goes to the Network Firewall
                #   endpoint, which sits in the same `GatewayId` field.
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
        # Interface endpoints: one service provided by an NLB in the same
        # account, one partner service that is out of scope.
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
        # The firewall endpoint is an AWS-managed GWLB endpoint, which
        # DescribeVpcEndpoints reports as `RequesterManaged`.
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
        # The NVA's ENI, owned by the instance it is attached to.
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
