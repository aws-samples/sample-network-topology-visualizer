"""AWS 없이 수집 경로를 재는 가짜 클라이언트.

실제 계정을 쓰면 결정론·부분실패를 **재현 가능하게** 잴 수 없다(권한·리소스가 사람마다
다르고, 실패를 마음대로 일으킬 수 없다). 그래서 불변식 ②④는 여기서 잰다. 실계정 1회
실행은 그것과 별개의 게이트다(완료 기준 = 실계정 실행).

계정ID 는 AWS 예시 계열(`111122223333`)만 쓴다 — 불변식 ⑤, pre-commit 훅이 강제한다.

픽스처는 **일부러 궂은 경우를 담는다**. 정상 경로만 담으면 어서션이 통과해도 미측정이다:
같은 대상으로 가는 라우트 2개(집계), blackhole + 노드 없는 대상(unresolved), 떼어낸 IGW,
실패·삭제 중인 NAT·어태치먼트·피어링·VIF(죽은 상태 — 노드는 남고 선은 없다), 자기참조 SG,
범위 밖 계정 소유 VPC, 게이트웨이 vs 인터페이스 엔드포인트, 실패한 LB.

🔴 `deleted`·`terminated` 같은 **없어진 상태**는 기본 픽스처에 두지 않는다 — 수집이 노드째
버리므로(ADR 0002 §3 개정 2026-10-14 · `services/base.py` `GONE_STATES`) 여기 두면 노드 수
(`RESOURCE_NODE_COUNT`)와 어긋난다. 그 경로는 `test_gone_states.py` 가 종류마다 따로 잰다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

ACCOUNT = "111122223333"
# 계정 이름(`account:GetAccountInformation` · 2026-10-28) — 지어낸 값이다. 실계정 이름(이메일 모양도 있다)을 fixture 에 옮기지 마라
ACCOUNT_NAME = "network-hub"
# scope 안의 다른 계정(공유 자원의 소유자) / scope 밖 계정(귀속되지 않아야 한다)
PARTNER = "222233334444"
OUTSIDER = "999900001111"
REGION = "us-east-1"

APP_VPC = "vpc-0aaa1111bbbb2222"
EGRESS_VPC = "vpc-0ccc3333dddd4444"
PUBLIC_SUBNET = "subnet-0aaa1111bbbb3333"
PRIVATE_SUBNET = "subnet-0bbb2222cccc3333"
EGRESS_SUBNET = "subnet-0ddd4444eeee5555"
CORE_TGW = "tgw-0ffff5555eeee6666"
APP_IGW = "igw-0aaa1111bbbb4444"
EGRESS_NAT = "nat-0eee5555ffff6666"
APP_SG = "sg-0aaa1111bbbb2222"
DB_SG = "sg-0bbb2222cccc3333"

# 일부러 id 오름차순이 아닌 순서로 둔다 — 정렬이 코드에 있는지 재기 위해서다.
VPCS: list[dict[str, Any]] = [
    {
        "VpcId": EGRESS_VPC,
        "CidrBlock": "10.20.0.0/16",
        "State": "available",
        "IsDefault": False,
        "CidrBlockAssociationSet": [
            {"CidrBlock": "10.20.0.0/16"},
            {"CidrBlock": "10.21.0.0/16"},
        ],
        "Tags": [
            {"Key": "Name", "Value": "egress-vpc"},
            {"Key": "env", "Value": "dev"},
        ],
    },
    {
        "VpcId": APP_VPC,
        "CidrBlock": "10.10.0.0/16",
        "State": "available",
        "IsDefault": True,
        "Tags": [{"Key": "env", "Value": "dev"}, {"Key": "Name", "Value": "app-vpc"}],
    },
]

SUBNETS: list[dict[str, Any]] = [
    {
        "SubnetId": PRIVATE_SUBNET,
        "VpcId": APP_VPC,
        "CidrBlock": "10.10.2.0/24",
        "AvailabilityZone": "us-east-1b",
        "AvailabilityZoneId": "use1-az2",
        "State": "available",
        "MapPublicIpOnLaunch": False,
        "AvailableIpAddressCount": 250,
        "Tags": [{"Key": "Name", "Value": "app-private-b"}],
    },
    {
        "SubnetId": PUBLIC_SUBNET,
        "VpcId": APP_VPC,
        "CidrBlock": "10.10.1.0/24",
        "AvailabilityZone": "us-east-1a",
        "AvailabilityZoneId": "use1-az1",
        "State": "available",
        "MapPublicIpOnLaunch": True,
        "AvailableIpAddressCount": 251,
        "Tags": [{"Key": "Name", "Value": "app-public-a"}],
    },
    {
        "SubnetId": EGRESS_SUBNET,
        "VpcId": EGRESS_VPC,
        "CidrBlock": "10.20.1.0/24",
        "AvailabilityZone": "us-east-1a",
        "AvailabilityZoneId": "use1-az1",
        "State": "available",
        "MapPublicIpOnLaunch": True,
        "AvailableIpAddressCount": 249,
        "Tags": [],
    },
]

ROUTE_TABLES: list[dict[str, Any]] = [
    {
        "RouteTableId": "rtb-0111aaaa2222bbbb",
        "VpcId": APP_VPC,
        "Associations": [
            {"RouteTableAssociationId": "rtbassoc-0main1111", "Main": True},
            {
                "RouteTableAssociationId": "rtbassoc-0aaa1111",
                "SubnetId": PUBLIC_SUBNET,
                "AssociationState": {"State": "associated"},
            },
        ],
        "Routes": [
            {
                "DestinationCidrBlock": "10.10.0.0/16",
                "GatewayId": "local",
                "State": "active",
            },
            {
                "DestinationCidrBlock": "0.0.0.0/0",
                "GatewayId": APP_IGW,
                "State": "active",
            },
            # 같은 TGW 로 가는 목적지 2개 → 엣지는 1개여야 한다(ADR 0002 §4)
            {
                "DestinationCidrBlock": "10.20.0.0/16",
                "TransitGatewayId": CORE_TGW,
                "State": "active",
            },
            {
                "DestinationCidrBlock": "10.21.0.0/16",
                "TransitGatewayId": CORE_TGW,
                "State": "active",
            },
            # 노드가 없는 대상(ENI) + blackhole → unresolved_refs 로 남아야 한다
            {
                "DestinationCidrBlock": "172.16.0.0/16",
                "NetworkInterfaceId": "eni-0dead0000beef1111",
                "State": "blackhole",
            },
            {
                "DestinationPrefixListId": "pl-0aaa1111bbbb2222",
                "GatewayId": "vpce-0aaa1111bbbb7777",
                "State": "active",
            },
            # 🔴 실데이터에서 온 모양(2026-09-15 실측): AWS 는 `xxx-` 형태가 아닌
            # **의사 게이트웨이 이름**을 GatewayId 로 준다. 링크로컬 목적지 2개가 같은
            # 이름으로 묶여 하나의 미해결 참조가 된다. ID 모양으로 걸러내면 안 된다.
            {
                "DestinationCidrBlock": "169.254.171.0/24",
                "GatewayId": "VpcLattice",
                "State": "active",
            },
            {
                "DestinationIpv6CidrBlock": "fd00:ec2:80::/64",
                "GatewayId": "VpcLattice",
                "State": "active",
            },
        ],
        "Tags": [{"Key": "Name", "Value": "app-public-rt"}],
    },
    {
        "RouteTableId": "rtb-0222bbbb3333cccc",
        "VpcId": EGRESS_VPC,
        "Associations": [
            {
                "RouteTableAssociationId": "rtbassoc-0ddd4444",
                "SubnetId": EGRESS_SUBNET,
                "AssociationState": {"State": "associated"},
            }
        ],
        "Routes": [
            {
                "DestinationCidrBlock": "0.0.0.0/0",
                "NatGatewayId": EGRESS_NAT,
                "State": "active",
            }
        ],
        "Tags": [],
    },
]

INTERNET_GATEWAYS: list[dict[str, Any]] = [
    {
        "InternetGatewayId": APP_IGW,
        "Attachments": [{"VpcId": APP_VPC, "State": "available"}],
        "Tags": [{"Key": "Name", "Value": "app-igw"}],
    },
    # 떼어낸 IGW — 노드는 남지만 선은 없다
    {"InternetGatewayId": "igw-0bbb2222cccc5555", "Attachments": [], "Tags": []},
]

NAT_GATEWAYS: list[dict[str, Any]] = [
    {
        "NatGatewayId": EGRESS_NAT,
        "SubnetId": EGRESS_SUBNET,
        "VpcId": EGRESS_VPC,
        "State": "available",
        "ConnectivityType": "public",
        "NatGatewayAddresses": [{"PublicIp": "203.0.113.10", "PrivateIp": "10.20.1.5"}],
        "Tags": [{"Key": "Name", "Value": "egress-nat"}],
    },
    # 실패한 NAT(서브넷을 못 찾아 생성 실패 — `InvalidSubnetID.NotFound`): 서브넷 정보가 없다 →
    # 부모 후보가 VPC 로 내려간다. `failed` 는 자원이 아직 응답에 **있는** 상태라 노드로 남는다
    # (`deleted` 는 수집이 버린다 — ADR 0002 §3).
    {
        "NatGatewayId": "nat-0fff6666aaaa7777",
        "VpcId": EGRESS_VPC,
        "State": "failed",
        "FailureCode": "InvalidSubnetID.NotFound",
        "ConnectivityType": "public",
        "NatGatewayAddresses": [],
        "Tags": [],
    },
]

# 🔴 소유자가 다른 계정이다 — scope 안이면 그 계정으로 귀속돼 노드가 하나로 합쳐진다(ADR 0002 §2).
TRANSIT_GATEWAYS: list[dict[str, Any]] = [
    {
        "TransitGatewayId": CORE_TGW,
        "TransitGatewayArn": f"arn:aws:ec2:{REGION}:{PARTNER}:transit-gateway/{CORE_TGW}",
        "OwnerId": PARTNER,
        "State": "available",
        "Description": "core network",
        "Options": {"AmazonSideAsn": 64512, "DefaultRouteTableAssociation": "enable"},
        "Tags": [{"Key": "Name", "Value": "core-tgw"}],
    }
]

TRANSIT_GATEWAY_ATTACHMENTS: list[dict[str, Any]] = [
    {
        "TransitGatewayAttachmentId": "tgw-attach-0aaa1111bbbb8888",
        "TransitGatewayId": CORE_TGW,
        "TransitGatewayOwnerId": PARTNER,
        "ResourceOwnerId": ACCOUNT,
        "ResourceType": "vpc",
        "ResourceId": APP_VPC,
        "State": "available",
        "Tags": [],
    },
    # VPN 어태치먼트 — 대상 = VPN 연결 노드(2.6.0 전에는 노드가 없어 엣지가 TGW 쪽만이었다)
    {
        "TransitGatewayAttachmentId": "tgw-attach-0bbb2222cccc9999",
        "TransitGatewayId": CORE_TGW,
        "TransitGatewayOwnerId": PARTNER,
        "ResourceOwnerId": ACCOUNT,
        "ResourceType": "vpn",
        "ResourceId": "vpn-0aaa1111bbbb3333",
        "State": "available",
        "Tags": [],
    },
    # 삭제 중 — 노드는 남고 선은 0
    {
        "TransitGatewayAttachmentId": "tgw-attach-0ccc3333dddd0000",
        "TransitGatewayId": CORE_TGW,
        "TransitGatewayOwnerId": PARTNER,
        "ResourceOwnerId": ACCOUNT,
        "ResourceType": "vpc",
        "ResourceId": EGRESS_VPC,
        "State": "deleting",
        "Tags": [],
    },
    # DXGW 어태치먼트 — 2.1.0 부터 대상 노드가 있다. DXGW ↔ TGW 는 **이 선 하나**로만 그린다(ADR 0010)
    {
        "TransitGatewayAttachmentId": "tgw-attach-0ddd4444eeee1111",
        "TransitGatewayId": CORE_TGW,
        "TransitGatewayOwnerId": PARTNER,
        "ResourceOwnerId": ACCOUNT,
        "ResourceType": "direct-connect-gateway",
        "ResourceId": "0f0e0d0c-0aaa-1111-bbbb-0000bbbb2222",
        "State": "available",
        "Tags": [],
    },
]

PEERINGS: list[dict[str, Any]] = [
    {
        "VpcPeeringConnectionId": "pcx-0aaa1111bbbb1111",
        "RequesterVpcInfo": {
            "VpcId": APP_VPC,
            "OwnerId": ACCOUNT,
            "Region": REGION,
            "CidrBlockSet": [{"CidrBlock": "10.10.0.0/16"}],
        },
        "AccepterVpcInfo": {
            "VpcId": EGRESS_VPC,
            "OwnerId": ACCOUNT,
            "Region": REGION,
            "CidrBlock": "10.20.0.0/16",
        },
        "Status": {"Code": "active", "Message": "Active"},
        "Tags": [{"Key": "Name", "Value": "app-egress"}],
    },
    # 실패한 피어링 — 선을 그리면 없는 경로를 있다고 그린다(`deleted` 는 노드째 버린다 — §3)
    {
        "VpcPeeringConnectionId": "pcx-0bbb2222cccc2222",
        "RequesterVpcInfo": {"VpcId": APP_VPC, "OwnerId": ACCOUNT, "Region": REGION},
        "AccepterVpcInfo": {
            "VpcId": "vpc-0eee5555ffff9999",
            "OwnerId": OUTSIDER,
            "Region": REGION,
        },
        "Status": {"Code": "failed", "Message": "Failed due to incorrect VPC-ID"},
        "Tags": [],
    },
    # 살아 있지만 상대 VPC 가 scope 밖 → 한쪽만 선이 생기고 상대는 unresolved_refs
    {
        "VpcPeeringConnectionId": "pcx-0ccc3333dddd3333",
        "RequesterVpcInfo": {"VpcId": APP_VPC, "OwnerId": ACCOUNT, "Region": REGION},
        "AccepterVpcInfo": {
            "VpcId": "vpc-0fff6666aaaa8888",
            "OwnerId": OUTSIDER,
            "Region": REGION,
        },
        "Status": {"Code": "active", "Message": "Active"},
        "Tags": [],
    },
]

VPC_ENDPOINTS: list[dict[str, Any]] = [
    {
        "VpcEndpointId": "vpce-0aaa1111bbbb7777",
        "VpcEndpointType": "Gateway",
        "VpcId": APP_VPC,
        "ServiceName": f"com.amazonaws.{REGION}.s3",
        "State": "available",
        "RouteTableIds": ["rtb-0111aaaa2222bbbb"],
        "SubnetIds": [],
        "PrivateDnsEnabled": False,
        "Tags": [],
    },
    {
        "VpcEndpointId": "vpce-0bbb2222cccc8888",
        "VpcEndpointType": "Interface",
        "VpcId": APP_VPC,
        "ServiceName": f"com.amazonaws.{REGION}.ssm",
        "State": "available",
        "RouteTableIds": [],
        "SubnetIds": [PRIVATE_SUBNET, PUBLIC_SUBNET],
        "PrivateDnsEnabled": True,
        "Tags": [{"Key": "Name", "Value": "ssm-endpoint"}],
    },
]

SECURITY_GROUPS: list[dict[str, Any]] = [
    {
        "GroupId": APP_SG,
        "GroupName": "app-sg",
        "Description": "app tier",
        "VpcId": APP_VPC,
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 443,
                "ToPort": 443,
                "UserIdGroupPairs": [{"GroupId": DB_SG, "UserId": ACCOUNT}],
            },
            # 자기참조 — 엣지가 아니라 속성이다(ADR 0002 §6)
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "UserIdGroupPairs": [{"GroupId": APP_SG, "UserId": ACCOUNT}],
            },
        ],
        "IpPermissionsEgress": [
            {
                "IpProtocol": "-1",
                "UserIdGroupPairs": [{"GroupId": DB_SG, "UserId": ACCOUNT}],
            }
        ],
        "Tags": [],
    },
    {
        "GroupId": DB_SG,
        "GroupName": "db-sg",
        "Description": "db tier",
        "VpcId": APP_VPC,
        "IpPermissions": [
            {
                "IpProtocol": "tcp",
                "FromPort": 5432,
                "ToPort": 5432,
                "UserIdGroupPairs": [
                    {"GroupId": APP_SG, "UserId": ACCOUNT},
                    # 노드가 없는 SG(다른 계정 소유) → unresolved_refs
                    {"GroupId": "sg-0ddd4444eeee4444", "UserId": OUTSIDER},
                ],
            }
        ],
        "IpPermissionsEgress": [],
        "Tags": [],
    },
]

NETWORK_ACLS: list[dict[str, Any]] = [
    {
        "NetworkAclId": "acl-0aaa1111bbbb2222",
        "VpcId": APP_VPC,
        "IsDefault": True,
        "Associations": [
            {"NetworkAclAssociationId": "aclassoc-0aaa1111", "SubnetId": PUBLIC_SUBNET},
            {
                "NetworkAclAssociationId": "aclassoc-0bbb2222",
                "SubnetId": PRIVATE_SUBNET,
            },
        ],
        "Entries": [{"RuleNumber": 100}, {"RuleNumber": 32767}],
        "Tags": [],
    },
    {
        "NetworkAclId": "acl-0bbb2222cccc3333",
        "VpcId": EGRESS_VPC,
        "IsDefault": False,
        "Associations": [],
        "Entries": [{"RuleNumber": 100}],
        "Tags": [],
    },
]

APP_ALB_ARN = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
    "loadbalancer/app/app-alb/50dc6c495c0c9188"
)
OLD_NLB_ARN = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"
    "loadbalancer/net/old-nlb/60dc6c495c0c9199"
)

# ALB 의 web ACL — `wafv2:GetWebACLForResource` 로 **LB 당 1회** 더 읽는다(교차 서비스 후속 호출).
# 🔴 세 경우를 다 만든다: ⓐ 붙은 ALB ⓑ **안 붙은** ALB(응답에 `WebACL` 키가 없다) ⓒ NLB(부르면 안 된다).
# 🔵 ARN 은 `wafv2` REGIONAL 스코프 꼴이다 — CloudFront 용은 `global/` 이고 us-east-1 고정이다.
APP_WEB_ACL_ARN = (
    f"arn:aws:wafv2:{REGION}:{ACCOUNT}:regional/webacl/prod-edge-acl/abc12345"
)

# ResourceArn → web ACL 객체. **없는 키 = 붙지 않았다**(AWS 가 `WebACL` 키를 아예 안 준다).
WEB_ACLS: dict[str, dict[str, Any]] = {
    APP_ALB_ARN: {"Name": "prod-edge-acl", "ARN": APP_WEB_ACL_ARN, "Id": "abc12345"},
}

LOAD_BALANCERS: list[dict[str, Any]] = [
    {
        "LoadBalancerName": "app-alb",
        "LoadBalancerArn": APP_ALB_ARN,
        "DNSName": f"app-alb-1234567890.{REGION}.elb.amazonaws.com",
        "Scheme": "internet-facing",
        "Type": "application",
        "VpcId": APP_VPC,
        "State": {"Code": "active"},
        "AvailabilityZones": [
            {"ZoneName": "us-east-1a", "SubnetId": PUBLIC_SUBNET},
            {"ZoneName": "us-east-1b", "SubnetId": PRIVATE_SUBNET},
        ],
        "SecurityGroups": [APP_SG],
    },
    # 실패한 LB — 노드는 남고 배치 선은 없다
    {
        "LoadBalancerName": "old-nlb",
        "LoadBalancerArn": OLD_NLB_ARN,
        "DNSName": f"old-nlb-9876543210.{REGION}.elb.amazonaws.com",
        "Scheme": "internal",
        "Type": "network",
        "VpcId": EGRESS_VPC,
        "State": {"Code": "failed"},
        "AvailabilityZones": [{"ZoneName": "us-east-1a", "SubnetId": EGRESS_SUBNET}],
        "SecurityGroups": [],
    },
]

# ---------------------------------------------------------------- CloudFront (글로벌 2번째)
# 🔴 응답 모양이 다른 종류와 다르다: 항목이 **`DistributionList.Items` 에 중첩**돼 있고,
#    `{Quantity, Items}` 래퍼가 Aliases·Origins 안에도 한 번 더 있다. 평평한 키를 가정하는
#    구현은 여기서 조용히 0건이 된다(= 「배포가 없다」로 그려진다).
# 🔴 `Status`·`LastModifiedTime`·`ETag` 를 **일부러 넣어** 수집기가 상태·wall-clock 을 안 담는지
#    잰다. 이 값들이 산출물에 나오면 FAIL 이다.
# 🔵 오리진 하나를 픽스처 ALB 의 실제 DNS 이름으로 둔다 — DNS 색인으로 `origin` 엣지가 맞물린다
#    (#43 · `services/cloudfront.py` 머리말). S3 오리진은 LB 모양이 아니라 선도 해소 실패도 없다.
CLOUDFRONT_DISTRIBUTION = "E1AAAAAAAAAAAA"
CLOUDFRONT_DISABLED = "E2BBBBBBBBBBBB"
CLOUDFRONT_STATE_LEAKS = ("Deployed", "InProgress", "2026-09-30T00:00:00Z", "ETAG12345")

CLOUDFRONT_DISTRIBUTIONS: list[dict[str, Any]] = [
    # 일부러 id 역순 — 정렬이 코드에 있는지 재기 위해서다.
    {
        "Id": CLOUDFRONT_DISABLED,
        "ARN": f"arn:aws:cloudfront::{ACCOUNT}:distribution/{CLOUDFRONT_DISABLED}",
        "DomainName": "d2bbbbbbbbbbbb.cloudfront.net",
        "Status": "InProgress",
        "LastModifiedTime": "2026-09-30T00:00:00Z",
        "ETag": "ETAG12345",
        "Enabled": False,
        "IsIPV6Enabled": False,
        "HttpVersion": "http2",
        "PriceClass": "PriceClass_100",
        # 붙지 않은 배포는 **빈 문자열**을 준다 — None 으로 접히는지 잰다.
        "WebACLId": "",
        "Aliases": {"Quantity": 0},
        "Origins": {
            "Quantity": 1,
            "Items": [{"Id": "s3-origin", "DomainName": "assets.s3.amazonaws.com"}],
        },
    },
    {
        "Id": CLOUDFRONT_DISTRIBUTION,
        "ARN": f"arn:aws:cloudfront::{ACCOUNT}:distribution/{CLOUDFRONT_DISTRIBUTION}",
        "DomainName": "d1aaaaaaaaaaaa.cloudfront.net",
        "Status": "Deployed",
        "LastModifiedTime": "2026-09-30T00:00:00Z",
        "ETag": "ETAG12345",
        "Enabled": True,
        "IsIPV6Enabled": True,
        "HttpVersion": "http2and3",
        "PriceClass": "PriceClass_All",
        "WebACLId": "arn:aws:wafv2:us-east-1:111122223333:global/webacl/prod-edge-acl/abc",
        # 대체 도메인·오리진을 오름차순이 아닌 순서로 줘 정렬을 잰다.
        "Aliases": {"Quantity": 2, "Items": ["www.example.com", "example.com"]},
        "Origins": {
            "Quantity": 2,
            "Items": [
                {"Id": "s3-origin", "DomainName": "assets.s3.amazonaws.com"},
                {
                    "Id": "alb-origin",
                    "DomainName": f"app-alb-1234567890.{REGION}.elb.amazonaws.com",
                },
            ],
        },
    },
]

# ---------------------------------------------------------------- Direct Connect (2차, ADR 0010)
# 응답 키는 소문자 camelCase 다. 🔴 비밀 필드(`authKey`·`macSecKeys`·피어 IP·`customerRouterConfig`)를
# **일부러 넣는다** — 수집기가 그것을 버리는지 재기 위해서다. 이 값들이 산출물에 나오면 FAIL 이다.
DX_CONNECTION = "dxcon-fg0aaa1111"
DX_CONNECTION_DOWN = "dxcon-fg0bbb2222"
# hosted VIF 의 부모 연결 — 다른 계정(파트너) 소유라 `DescribeConnections` 에 **없다**(§5.5-2)
DX_HOSTED_PARENT = "dxcon-fg0ccc3333"
DX_PRIVATE_VIF = "dxvif-fg0aaa1111"
DX_HOSTED_VIF = "dxvif-fg0bbb2222"
DX_PUBLIC_VIF = "dxvif-fg0ccc3333"
DX_DELETING_VIF = "dxvif-fg0ddd4444"
DXGW = "0a0b0c0d-0aaa-1111-bbbb-0000aaaa1111"  # VGW 용 (private VIF 가 붙는다)
DXGW_TGW = "0f0e0d0c-0aaa-1111-bbbb-0000bbbb2222"  # TGW 용 (TGW 어태치먼트가 가리킨다)
DX_SECRETS = (
    "s3cret-bgp-auth-key",
    "s3cret-macsec-ckn",
    "arn:aws:secretsmanager:secret",
    "router-config-xml",
)
# BGP 피어 주소(Amazon 쪽 · 고객 쪽) — 비밀이 아니다: 콘솔이 VIF 세부 정보에 보여 주는 값이라 우리도 싣는다
# (사용자 결정 2026-10-24 「콘솔이 보여 주는 값이면 우리도 보여 줘도 된다」). 인증 키·라우터 설정은 여전히 비밀이다.
DX_PEER_ADDRESSES = (
    "169.254.255.1/30",
    "169.254.255.2/30",
)

DIRECT_CONNECT_CONNECTIONS: list[dict[str, Any]] = [
    # 일부러 id 역순 — 정렬이 코드에 있는지 재기 위해서다.
    {
        "connectionId": DX_CONNECTION_DOWN,
        "connectionName": "seoul-backup",
        "connectionState": "down",
        "region": REGION,
        "location": "EqSe2",
        "bandwidth": "1Gbps",
        "vlan": 102,
        "lagId": "dxlag-fg0aaa1111",
        "partnerName": "Example Partner",
        "ownerAccount": ACCOUNT,
        "awsDeviceV2": "EqSe2-device-A",
        "jumboFrameCapable": True,
        "macSecCapable": False,
        "hasLogicalRedundancy": "unknown",
        "tags": [],
    },
    {
        "connectionId": DX_CONNECTION,
        "connectionName": "seoul-primary",
        "connectionState": "available",
        "region": REGION,
        "location": "EqSe2",
        "bandwidth": "10Gbps",
        "vlan": 101,
        "partnerName": "Example Partner",
        "providerName": "Example Provider",
        "ownerAccount": ACCOUNT,
        "awsDeviceV2": "EqSe2-device-A",
        "awsLogicalDeviceId": "device-1",
        "jumboFrameCapable": True,
        "macSecCapable": True,
        "encryptionMode": "should_encrypt",
        "portEncryptionStatus": "Encryption Down",
        "macSecKeys": [{"ckn": DX_SECRETS[1], "secretARN": DX_SECRETS[2]}],
        "loaIssueTime": "2026-01-01T00:00:00Z",
        "tags": [
            {"key": "Name", "value": "dx-seoul-primary"},
            {"key": "env", "value": "dev"},
        ],
    },
]

DIRECT_CONNECT_VIRTUAL_INTERFACES: list[dict[str, Any]] = [
    # private VIF → DXGW. 4바이트 ASN 이라 `asn` 은 0 이고 `asnLong` 이 진짜다(§5.5-10)
    {
        "virtualInterfaceId": DX_PRIVATE_VIF,
        "virtualInterfaceName": "to-dxgw",
        "virtualInterfaceType": "private",
        "virtualInterfaceState": "available",
        "connectionId": DX_CONNECTION,
        "ownerAccount": ACCOUNT,
        "vlan": 101,
        "asn": 0,
        "asnLong": 4200000001,
        "amazonSideAsn": 64512,
        "addressFamily": "ipv4",
        "mtu": 9001,
        "jumboFrameCapable": True,
        "directConnectGatewayId": DXGW,
        "virtualGatewayId": "",
        "authKey": DX_SECRETS[0],
        "amazonAddress": DX_PEER_ADDRESSES[0],
        "customerAddress": DX_PEER_ADDRESSES[1],
        "customerRouterConfig": DX_SECRETS[3],
        "routeFilterPrefixes": [],
        "siteLinkEnabled": False,
        "bgpPeers": [
            {
                "addressFamily": "ipv4",
                "asn": 0,
                "asnLong": 4200000001,
                "authKey": DX_SECRETS[0],
                "amazonAddress": DX_PEER_ADDRESSES[0],
                "customerAddress": DX_PEER_ADDRESSES[1],
                "bgpStatus": "up",
                "bgpPeerState": "available",
            }
        ],
        "region": REGION,
        "tags": [],
    },
    # hosted VIF — 부모 연결이 목록에 없다(파트너 소유). 미수락 상태. 부모는 리전으로 내려간다
    {
        "virtualInterfaceId": DX_HOSTED_VIF,
        "virtualInterfaceName": "hosted-pending",
        "virtualInterfaceType": "private",
        "virtualInterfaceState": "confirming",
        "connectionId": DX_HOSTED_PARENT,
        "ownerAccount": ACCOUNT,
        "vlan": 200,
        "asn": 65001,
        "amazonSideAsn": 64512,
        "addressFamily": "ipv4",
        "mtu": 1500,
        "directConnectGatewayId": DXGW,
        "bgpPeers": [{"addressFamily": "ipv4", "asn": 65001, "bgpStatus": "down"}],
        "region": REGION,
        "tags": [],
    },
    # public VIF — 게이트웨이 필드가 둘 다 비어 있는 것이 **정상**이다(§5.5-8). 광고 prefix 를 든다
    {
        "virtualInterfaceId": DX_PUBLIC_VIF,
        "virtualInterfaceName": "public-services",
        "virtualInterfaceType": "public",
        "virtualInterfaceState": "available",
        "connectionId": DX_CONNECTION,
        "ownerAccount": ACCOUNT,
        "vlan": 300,
        "asn": 65002,
        "amazonSideAsn": 7224,
        "addressFamily": "ipv4",
        "mtu": 1500,
        "directConnectGatewayId": "",
        "virtualGatewayId": "",
        "routeFilterPrefixes": [
            {"cidr": "203.0.113.0/24"},
            {"cidr": "198.51.100.0/24"},
        ],
        "bgpPeers": [
            {"addressFamily": "ipv6", "asn": 65002, "bgpStatus": "up"},
            {"addressFamily": "ipv4", "asn": 65002, "bgpStatus": "up"},
        ],
        "region": REGION,
        "tags": [],
    },
    # 삭제 중인 VIF — 노드는 남고 DXGW 로 가는 선은 없다(ADR 0002 §3). 삭제가 끝난 `deleted`
    # (15분 잔존)는 수집이 노드째 버린다.
    {
        "virtualInterfaceId": DX_DELETING_VIF,
        "virtualInterfaceName": "old-transit",
        "virtualInterfaceType": "transit",
        "virtualInterfaceState": "deleting",
        "connectionId": DX_CONNECTION,
        "ownerAccount": ACCOUNT,
        "vlan": 400,
        "asn": 65003,
        "directConnectGatewayId": DXGW_TGW,
        "bgpPeers": [],
        "region": REGION,
        "tags": [],
    },
]

DIRECT_CONNECT_GATEWAYS: list[dict[str, Any]] = [
    {
        "directConnectGatewayId": DXGW_TGW,
        "directConnectGatewayName": "corp-dxgw-tgw",
        "directConnectGatewayState": "available",
        "amazonSideAsn": 64513,
        "ownerAccount": ACCOUNT,
        "totalPrefixPoolAllocations": 3,
        "tags": [],
    },
    {
        "directConnectGatewayId": DXGW,
        "directConnectGatewayName": "corp-dxgw",
        "directConnectGatewayState": "available",
        "amazonSideAsn": 64512,
        "ownerAccount": ACCOUNT,
        "tags": [{"key": "Name", "value": "corp-dxgw"}],
    },
]

# DXGW 별 연결(association) — 후속 호출. VGW 연결 + 해제된 연결(관계가 아니다) / TGW 연결(다른 계정·리전).
DXGW_ASSOCIATIONS: dict[str, list[dict[str, Any]]] = {
    DXGW: [
        {
            "associationId": "assoc-0aaa1111",
            "associationState": "associated",
            "directConnectGatewayId": DXGW,
            "directConnectGatewayOwnerAccount": ACCOUNT,
            "associatedGateway": {
                "id": "vgw-0aaa1111bbbb2222",
                "type": "virtualPrivateGateway",
                "ownerAccount": ACCOUNT,
                "region": "us-west-2",
            },
            "allowedPrefixesToDirectConnectGateway": [{"cidr": "10.10.0.0/16"}],
        },
        {
            "associationId": "assoc-0bbb2222",
            "associationState": "disassociated",
            "directConnectGatewayId": DXGW,
            "associatedGateway": {
                "id": "vgw-0dead0000beef1111",
                "type": "virtualPrivateGateway",
                "ownerAccount": ACCOUNT,
                "region": "eu-west-1",
            },
        },
    ],
    DXGW_TGW: [
        {
            "associationId": "assoc-0ccc3333",
            "associationState": "associated",
            "directConnectGatewayId": DXGW_TGW,
            "associatedGateway": {
                "id": CORE_TGW,
                "type": "transitGateway",
                "ownerAccount": PARTNER,
                "region": REGION,
            },
            "allowedPrefixesToDirectConnectGateway": [{"cidr": "10.0.0.0/8"}],
        }
    ],
}

# 🔴 boto3 페이지네이터가 없는 메서드 — 가짜도 `can_paginate` 를 False 로 답하고 `nextToken` 을
#    두 장에 걸쳐 준다. 첫 장만 읽는 구현은 여기서 개수가 준다.
# 2.12.0 — WAFv2 `ListWebACLs` 도 페이지네이터가 없다(`NextMarker` · botocore 실측 2026-10-23)
UNPAGINATED = frozenset(
    {"describe_connections", "describe_virtual_interfaces", "list_web_acls"}
)
# `describe_instances` 는 인스턴스를 **예약(Reservation)으로 감싸** 돌려준다 → result_key 는
# `Reservations`. `ec2_instance.enrich` 가 예약을 인스턴스로 편다. 🔴 예약당 인스턴스 **1개**로
# 둔다: RESOURCE_NODE_COUNT 가 예약 수(= 목록 항목 수)를 세므로 예약당 여럿을 넣으면 노드 수
# 어서션(test_cli)이 조용히 어긋난다. 예약당 여럿을 펴는 경로는 test_services 가 enrich 를
# 직접 불러 잰다. id·SG 를 일부러 오름차순이 아닌 순서로 둬 정렬이 코드에 있는지 잰다.
INSTANCE_RESERVATIONS: list[dict[str, Any]] = [
    {
        "OwnerId": ACCOUNT,
        "Instances": [
            {
                "InstanceId": "i-0bbb2222cccc3333",
                "InstanceType": "t3.micro",
                "State": {"Name": "running"},
                "Placement": {"AvailabilityZone": "us-east-1a"},
                "SubnetId": PRIVATE_SUBNET,
                "VpcId": APP_VPC,
                "PrivateIpAddress": "10.10.2.10",
                "SecurityGroups": [{"GroupId": DB_SG}, {"GroupId": APP_SG}],
                "Tags": [{"Key": "Name", "Value": "app-1"}],
            }
        ],
    },
    {
        "OwnerId": ACCOUNT,
        "Instances": [
            {
                "InstanceId": "i-0aaa1111bbbb2222",
                "InstanceType": "m5.large",
                "State": {"Name": "running"},
                "Placement": {"AvailabilityZone": "us-east-1a"},
                "SubnetId": PUBLIC_SUBNET,
                "VpcId": APP_VPC,
                "PrivateIpAddress": "10.10.1.20",
                "SecurityGroups": [{"GroupId": APP_SG}],
                "Tags": [{"Key": "Name", "Value": "bastion"}],
            }
        ],
    },
]

# ASG 는 서브넷 하나에 안 산다: VPCZoneIdentifier 로 여러 서브넷에 걸치고 응답에 VpcId 가 없다.
# 🔴 `Instances`·`HealthStatus`·`LifecycleState` 를 일부러 넣어 **수집기가 헬스를 안 담는지** 잰다.
# 서브넷·AZ 를 오름차순이 아닌 순서로 줘 정렬이 코드에 있는지 잰다(PRIVATE=0bbb, PUBLIC=0aaa).
#
# 🔴 두 번째 그룹의 이름은 **인코딩이 필요한 이름**이다 — `_id_segment()` 의 `quote` 줄을 픽스처가
#    실제로 타게 하려고 둔다(`app-asg` 하나만 있던 동안 그 줄은 한 번도 실행되지 않았다).
ASG_NAME_NEEDING_ENCODING = "team//prod app|asg"

AUTO_SCALING_GROUPS: list[dict[str, Any]] = [
    {
        "AutoScalingGroupName": "app-asg",
        "AutoScalingGroupARN": (
            f"arn:aws:autoscaling:{REGION}:{ACCOUNT}:autoScalingGroup:"
            "abcd1234-5678-90ab-cdef-EXAMPLE11111:autoScalingGroupName/app-asg"
        ),
        "MinSize": 2,
        "MaxSize": 6,
        "DesiredCapacity": 3,
        "AvailabilityZones": ["us-east-1b", "us-east-1a"],
        "VPCZoneIdentifier": f"{PRIVATE_SUBNET},{PUBLIC_SUBNET}",
        "TargetGroupARNs": [
            f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/app-tg/abc123"
        ],
        "Instances": [
            {
                "InstanceId": "i-0aaa1111bbbb2222",
                "AvailabilityZone": "us-east-1a",
                "LifecycleState": "InService",
                "HealthStatus": "Unhealthy",
            }
        ],
    },
    # 🔴 **인코딩 경로를 타는 이름**(리뷰 요청 2026-09-28). `app-asg` 하나만 있던 동안은
    #    `_id_segment()` 가 `quote` 를 부르는 줄이 픽스처로 한 번도 실행되지 않았다 = 미측정.
    #    한 이름에 세 축을 다 넣는다: `//`(빈 세그먼트 — `safe="/"` 로 되돌리면 스키마 위반) ·
    #    공백 · `|`(남기면 `edge_id` 파싱 유일성이 깨진다). AWS 는 이 이름을 실제로 허용한다.
    {
        "AutoScalingGroupName": ASG_NAME_NEEDING_ENCODING,
        "AutoScalingGroupARN": (
            f"arn:aws:autoscaling:{REGION}:{ACCOUNT}:autoScalingGroup:"
            f"abcd1234-5678-90ab-cdef-EXAMPLE22222:autoScalingGroupName/{ASG_NAME_NEEDING_ENCODING}"
        ),
        "MinSize": 0,
        "MaxSize": 1,
        "DesiredCapacity": 0,
        "AvailabilityZones": ["us-east-1a"],
        "VPCZoneIdentifier": PRIVATE_SUBNET,
    },
]

# EKS 는 list_clusters 가 dict 가 아니라 **이름 문자열** 목록을 준다 → describe_cluster 로 상세를
# 채운다(단일 객체 `{"cluster": {...}}`, 리스트가 아니라 pages() 로 못 읽는다). 🔴 status·health 를
# 일부러 넣어 **수집기가 상태를 안 담는지** 잰다. subnet·SG 를 오름차순이 아닌 순서로 줘 정렬을 잰다.
EKS_CLUSTER_NAME = "app-eks"
EKS_CLUSTER_NAMES: list[str] = [EKS_CLUSTER_NAME]
EKS_CLUSTERS: dict[str, dict[str, Any]] = {
    EKS_CLUSTER_NAME: {
        "name": EKS_CLUSTER_NAME,
        "arn": f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{EKS_CLUSTER_NAME}",
        "version": "1.29",
        "endpoint": f"https://EXAMPLE0A1B2C3D.gr7.{REGION}.eks.amazonaws.com",
        "resourcesVpcConfig": {
            "vpcId": APP_VPC,
            "subnetIds": [PRIVATE_SUBNET, PUBLIC_SUBNET],
            "securityGroupIds": [DB_SG, APP_SG],
            "clusterSecurityGroupId": "sg-0eee5555ffff7777",
            "endpointPublicAccess": True,
            "endpointPrivateAccess": True,
            "publicAccessCidrs": ["0.0.0.0/0"],
        },
        # 🔴 수집기가 담으면 안 되는 상태값 — status·health.issues 는 시시각각 바뀐다(불변식 ②).
        "status": "ACTIVE",
        "health": {"issues": []},
    }
}

# Network Firewall 도 목록→상세다: list_firewalls 는 이름/ARN dict 만, describe_firewall 로 상세를
# 채운다(단일 객체 {"Firewall": ..., "FirewallStatus": ...} → pages() 로 못 읽는다). 🔴 FirewallStatus
# 안 SyncStates 는 배열이 아니라 **AZ 키 map**(§6)이고 그 안의 Status·상위 Status 는 시시각각 바뀌는
# 상태다 → 수집기는 구조인 EndpointId(RT 조인 키 vpce-*)만 담고 Status 는 안 담아야 한다(불변식 ②).
# SubnetMapping·SyncStates 를 오름차순이 아닌 순서로 줘 정렬이 코드에 있는지 잰다.
FW_NAME = "app-fw"
FW_ARN = f"arn:aws:network-firewall:{REGION}:{ACCOUNT}:firewall/{FW_NAME}"
FW_POLICY_ARN = (
    f"arn:aws:network-firewall:{REGION}:{ACCOUNT}:firewall-policy/app-fw-policy"
)
FW_ENDPOINT_A = "vpce-0fff6666aaaa1111"
FW_ENDPOINT_B = "vpce-0eee5555ffff2222"
NETWORK_FIREWALLS: list[dict[str, Any]] = [
    {"FirewallName": FW_NAME, "FirewallArn": FW_ARN},
]
NETWORK_FIREWALL_DETAILS: dict[str, dict[str, Any]] = {
    FW_ARN: {
        "Firewall": {
            "FirewallName": FW_NAME,
            "FirewallArn": FW_ARN,
            "FirewallPolicyArn": FW_POLICY_ARN,
            "VpcId": APP_VPC,
            "SubnetMappings": [
                {"SubnetId": PRIVATE_SUBNET},
                {"SubnetId": PUBLIC_SUBNET},
            ],
            "DeleteProtection": True,
        },
        "FirewallStatus": {
            # 🔴 수집기가 담으면 안 되는 상태값 — Status·per-AZ Status 는 시시각각 바뀐다(불변식 ②).
            "Status": "READY",
            "SyncStates": {
                "us-east-1b": {
                    "Attachment": {
                        "SubnetId": PRIVATE_SUBNET,
                        "EndpointId": FW_ENDPOINT_B,
                        "Status": "READY",
                    }
                },
                "us-east-1a": {
                    "Attachment": {
                        "SubnetId": PUBLIC_SUBNET,
                        "EndpointId": FW_ENDPOINT_A,
                        "Status": "READY",
                    }
                },
            },
        },
    }
}

# 후속 호출 — **부모 ARN 별**로 다른 응답을 준다(ELB 의 리스너·타깃 그룹, 「이음매 2」 참조 구현).
# 🔴 `_DATASETS` 에 섞지 마라: RESOURCE_NODE_COUNT 가 리스너까지 세서 노드 수 어서션이 조용히
# 거짓이 된다. 리스너는 노드가 아니라 LB 의 속성이다.
LISTENERS: dict[str, list[dict[str, Any]]] = {
    # 일부러 포트 역순 + 8080 을 섞는다 — 문자열 정렬이면 "HTTP:8080" 이 "HTTP:80" 앞에 온다.
    APP_ALB_ARN: [
        {
            "ListenerArn": f"{APP_ALB_ARN}/listener/https",
            "Protocol": "HTTPS",
            "Port": 443,
        },
        {
            "ListenerArn": f"{APP_ALB_ARN}/listener/alt",
            "Protocol": "HTTP",
            "Port": 8080,
        },
        {"ListenerArn": f"{APP_ALB_ARN}/listener/http", "Protocol": "HTTP", "Port": 80},
    ],
    # 리스너가 **없는** LB. 「없음(빈 배열)」과 「못 읽음(키 부재)」의 대조군이다.
    OLD_NLB_ARN: [],
}

TARGET_GROUPS: dict[str, list[dict[str, Any]]] = {
    APP_ALB_ARN: [
        {
            "TargetGroupName": "app-tg-green",
            "TargetGroupArn": f"{APP_ALB_ARN}/tg/green",
            "TargetType": "instance",
            "Port": 80,
        },
        {
            "TargetGroupName": "app-tg-blue",
            "TargetGroupArn": f"{APP_ALB_ARN}/tg/blue",
            "TargetType": "instance",
            "Port": 8080,
        },
    ],
    OLD_NLB_ARN: [],
}

# W3② 대상 상태 — 타깃 그룹 ARN 으로 갈라진다. 기본 = green 정상 2 · blue 정상 1 + 등록 중 1(`initial` 은
# 비정상이 아니다). 구조 수집이 **개수만**(등록 대상 수 · 사용자 결정 2026-10-16) · 상태 단이 같은 응답의 헬스를 센다.
# 2.7.0(LB 대상 선 · ADR 0002 §8): 대상 정체도 쓴다 — `app-1`(i-…3333)은 두 그룹에 걸쳐 등록됐다(→ 선 하나 · 등록 2 ·
# 포트 80·8080) · `bastion`(i-…2222) 하나 · i-…0004 는 수집하지 않은 인스턴스(→ 선 없음 · `unresolved_targets`).
TARGET_HEALTH: dict[str, list[dict[str, Any]]] = {
    f"{APP_ALB_ARN}/tg/green": [
        {
            "Target": {"Id": "i-0bbb2222cccc3333", "Port": 80},
            "TargetHealth": {"State": "healthy"},
        },
        {
            "Target": {"Id": "i-0aaa1111bbbb2222", "Port": 80},
            "TargetHealth": {"State": "healthy"},
        },
    ],
    f"{APP_ALB_ARN}/tg/blue": [
        {
            "Target": {"Id": "i-0bbb2222cccc3333", "Port": 8080},
            "TargetHealth": {"State": "healthy"},
        },
        {
            "Target": {"Id": "i-0aaa1111bbbb0004", "Port": 8080},
            "TargetHealth": {"State": "initial"},
        },
    ],
}

# ---- W2 7종 -------------------------------------------------------------------------------------
# 🔴 모든 항목에 **수집기가 담으면 안 되는 상태값**을 일부러 넣는다(불변식 ②) — 담으면 test_services 가
#    잡는다. id·서브넷·SG 를 오름차순이 아닌 순서로 줘 정렬이 코드에 있는지 잰다.

# RDS — 응답에 서브넷이 없다. 서브넷 그룹 후보 중 AZ 가 맞는 것이 **하나**면 그것이 부모다.
# PRIVATE_SUBNET 은 us-east-1b · PUBLIC_SUBNET 은 us-east-1a 다(SUBNETS).
RDS_INSTANCE = "app-db"
RDS_STATE_LEAKS = ("backing-up", "2026-10-01T00:00:00Z")
RDS_DB_INSTANCES: list[dict[str, Any]] = [
    {
        "DBInstanceIdentifier": RDS_INSTANCE,
        "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:{RDS_INSTANCE}",
        "DBInstanceClass": "db.t3.micro",
        "Engine": "mysql",
        "DBInstanceStatus": RDS_STATE_LEAKS[0],
        "LatestRestorableTime": RDS_STATE_LEAKS[1],
        "MultiAZ": False,
        "AvailabilityZone": "us-east-1b",
        "PubliclyAccessible": False,
        "VpcSecurityGroups": [
            {"VpcSecurityGroupId": DB_SG, "Status": "active"},
            {"VpcSecurityGroupId": APP_SG, "Status": "active"},
        ],
        "DBSubnetGroup": {
            "DBSubnetGroupName": "app-db-subnets",
            "VpcId": APP_VPC,
            "Subnets": [
                {
                    "SubnetIdentifier": PRIVATE_SUBNET,
                    "SubnetAvailabilityZone": {"Name": "us-east-1b"},
                },
                {
                    "SubnetIdentifier": PUBLIC_SUBNET,
                    "SubnetAvailabilityZone": {"Name": "us-east-1a"},
                },
            ],
        },
        "TagList": [{"Key": "team", "Value": "app"}],
    }
]

# #114(2.9.0) ElastiCache — 복제 그룹 하나(주·복제 클러스터 둘 · AZ 마다 서브넷 하나 → 각자 그 서브넷) + 다중 AZ Memcached
# 하나(노드 둘 · 두 서브넷에 걸친다 → VPC + `subnet_ids`). 🔴 상태 값(`CacheClusterStatus`·`CurrentRole`)을 일부러 넣는다 —
# 구조 문서에 새면 FAIL 해야 한다(`test_new_collectors.py`).
CACHE_GROUP = "app-cache-subnets"
CACHE_RG = "app-redis"
CACHE_PRIMARY = "app-redis-001"
CACHE_REPLICA = "app-redis-002"
CACHE_MEMCACHED = "app-memcached"
CACHE_STATE_LEAKS = ("snapshotting", "primary")


def _cache(cid: str, engine: str, zones: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "CacheClusterId": cid,
        "ARN": f"arn:aws:elasticache:{REGION}:{ACCOUNT}:cluster:{cid}",
        "Engine": engine,
        "EngineVersion": "7.2" if engine != "memcached" else "1.6.22",
        "CacheNodeType": "cache.t4g.micro",
        "NumCacheNodes": len(zones),
        "CacheClusterStatus": CACHE_STATE_LEAKS[0],
        "PreferredAvailabilityZone": zones[0] if len(zones) == 1 else "Multiple",
        "CacheSubnetGroupName": CACHE_GROUP,
        "SecurityGroups": [{"SecurityGroupId": DB_SG, "Status": "active"}],
        "TransitEncryptionEnabled": True,
        "AtRestEncryptionEnabled": True,
        "CacheNodes": [
            {
                "CacheNodeId": f"{i + 1:04d}",
                "CacheNodeStatus": "available",
                "CustomerAvailabilityZone": z,
                "Endpoint": {
                    "Address": f"{cid}.{i:04d}.cache.amazonaws.com",
                    "Port": 6379 if engine != "memcached" else 11211,
                },
            }
            for i, z in enumerate(zones)
        ],
        **extra,
    }


# 일부러 id 오름차순이 아니다 — 정렬이 코드에 있는지 잰다.
ELASTICACHE_CLUSTERS: list[dict[str, Any]] = [
    _cache(CACHE_REPLICA, "valkey", ["us-east-1b"], ReplicationGroupId=CACHE_RG),
    _cache(CACHE_MEMCACHED, "memcached", ["us-east-1a", "us-east-1b"]),
    _cache(CACHE_PRIMARY, "valkey", ["us-east-1a"], ReplicationGroupId=CACHE_RG),
]
CACHE_SUBNET_GROUPS: list[dict[str, Any]] = [
    {
        "CacheSubnetGroupName": CACHE_GROUP,
        "VpcId": APP_VPC,
        "Subnets": [
            {
                "SubnetIdentifier": PUBLIC_SUBNET,
                "SubnetAvailabilityZone": {"Name": "us-east-1a"},
            },
            {
                "SubnetIdentifier": PRIVATE_SUBNET,
                "SubnetAvailabilityZone": {"Name": "us-east-1b"},
            },
        ],
    }
]
CACHE_REPLICATION_GROUPS: list[dict[str, Any]] = [
    {
        "ReplicationGroupId": CACHE_RG,
        "Status": "available",
        "MemberClusters": [CACHE_REPLICA, CACHE_PRIMARY],
        "ClusterEnabled": False,
        "MultiAZ": "enabled",
        "AutomaticFailover": "enabled",
        "NodeGroups": [
            {
                "NodeGroupId": "0001",
                "NodeGroupMembers": [
                    {
                        "CacheClusterId": CACHE_PRIMARY,
                        "CurrentRole": CACHE_STATE_LEAKS[1],
                    },
                    {"CacheClusterId": CACHE_REPLICA, "CurrentRole": "replica"},
                ],
            }
        ],
    }
]

# #114(2.9.0) Aurora/RDS DB 클러스터 — 멤버 0개인 Aurora 클러스터(실측 2026-10-20 의 모양 · 인스턴스 없이 클러스터만 있다).
#   응답의 `DBSubnetGroup` 은 **이름 문자열**이다(인스턴스 응답과 다르다) → VPC 는 `DescribeDBSubnetGroups` 로 안다.
#   🔴 상태 값(`Status`)·멤버 역할(`IsClusterWriter`)을 일부러 넣는다 — 구조 문서에 새면 FAIL.
DB_CLUSTER = "app-aurora"
DB_CLUSTER_STATE_LEAKS = ("backing-up",)
RDS_DB_CLUSTERS: list[dict[str, Any]] = [
    {
        "DBClusterIdentifier": DB_CLUSTER,
        "DBClusterArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:{DB_CLUSTER}",
        "Engine": "aurora-postgresql",
        "EngineVersion": "16.4",
        "EngineMode": "provisioned",
        "Status": DB_CLUSTER_STATE_LEAKS[0],
        "Endpoint": f"{DB_CLUSTER}.cluster-abc.{REGION}.rds.amazonaws.com",
        "ReaderEndpoint": f"{DB_CLUSTER}.cluster-ro-abc.{REGION}.rds.amazonaws.com",
        "Port": 5432,
        "MultiAZ": False,
        "AvailabilityZones": ["us-east-1b", "us-east-1a"],
        "DBClusterMembers": [],
        "DBSubnetGroup": "app-db-subnets",
        "VpcSecurityGroups": [{"VpcSecurityGroupId": DB_SG, "Status": "active"}],
        "StorageEncrypted": True,
        "ServerlessV2ScalingConfiguration": {"MinCapacity": 0.5, "MaxCapacity": 4.0},
        "TagList": [{"Key": "team", "Value": "app"}],
    }
]
DB_SUBNET_GROUPS: list[dict[str, Any]] = [
    {
        "DBSubnetGroupName": "app-db-subnets",
        "VpcId": APP_VPC,
        "Subnets": [
            {
                "SubnetIdentifier": PRIVATE_SUBNET,
                "SubnetAvailabilityZone": {"Name": "us-east-1b"},
            },
            {
                "SubnetIdentifier": PUBLIC_SUBNET,
                "SubnetAvailabilityZone": {"Name": "us-east-1a"},
            },
        ],
    }
]

# #114(2.9.0) OpenSearch — VPC 도메인 하나(두 서브넷 · 영역 인식) + 공개 도메인 하나(VPCOptions 없음 = 고객 VPC 밖).
#   목록은 이름만 · 상세는 `describe_domains`(5개씩). 🔴 상태 불리언(`Processing`·`UpgradeProcessing`)을 일부러 넣는다.
OS_DOMAIN = "app-search"
OS_PUBLIC = "public-logs"
OS_STATE_LEAKS = ("DomainProcessingStatus",)
OPENSEARCH_DOMAIN_NAMES: list[dict[str, Any]] = [
    {"DomainName": OS_PUBLIC, "EngineType": "OpenSearch"},
    {"DomainName": OS_DOMAIN, "EngineType": "OpenSearch"},
]
OPENSEARCH_DETAILS: dict[str, dict[str, Any]] = {
    OS_DOMAIN: {
        "DomainName": OS_DOMAIN,
        "ARN": f"arn:aws:es:{REGION}:{ACCOUNT}:domain/{OS_DOMAIN}",
        "Created": True,
        "Deleted": False,
        "Processing": False,
        "UpgradeProcessing": False,
        "DomainProcessingStatus": "Active",
        "EngineVersion": "OpenSearch_2.13",
        "Endpoints": {"vpc": f"vpc-{OS_DOMAIN}-abc.{REGION}.es.amazonaws.com"},
        "ClusterConfig": {
            "InstanceType": "t3.small.search",
            "InstanceCount": 2,
            "DedicatedMasterEnabled": False,
            "ZoneAwarenessEnabled": True,
        },
        "VPCOptions": {
            "VPCId": APP_VPC,
            "SubnetIds": [PUBLIC_SUBNET, PRIVATE_SUBNET],
            "AvailabilityZones": ["us-east-1b", "us-east-1a"],
            "SecurityGroupIds": [DB_SG],
        },
        "EncryptionAtRestOptions": {"Enabled": True},
        "NodeToNodeEncryptionOptions": {"Enabled": True},
        "DomainEndpointOptions": {"EnforceHTTPS": True},
    },
    OS_PUBLIC: {
        "DomainName": OS_PUBLIC,
        "ARN": f"arn:aws:es:{REGION}:{ACCOUNT}:domain/{OS_PUBLIC}",
        "Created": True,
        "Deleted": False,
        "Processing": True,
        "EngineVersion": "OpenSearch_2.11",
        "Endpoint": f"search-{OS_PUBLIC}-xyz.{REGION}.es.amazonaws.com",
        "ClusterConfig": {"InstanceType": "t3.small.search", "InstanceCount": 1},
        "EncryptionAtRestOptions": {"Enabled": False},
        "NodeToNodeEncryptionOptions": {"Enabled": False},
        "DomainEndpointOptions": {"EnforceHTTPS": True},
    },
}

# #114(2.9.0) MSK — 프로비저닝(브로커 둘 · 클라이언트 서브넷 둘) + 서버리스(VPC 설정 하나). 응답에 VPC 가 없다(부모 = 리전).
#   🔴 상태(`State`)를 일부러 넣는다. 브로커 ENI 는 `ListNodes`(프로비저닝만) — 서버리스는 묻지 않는다.
MSK_CLUSTER = "app-kafka"
MSK_SERVERLESS = "app-kafka-sls"
MSK_ARN = f"arn:aws:kafka:{REGION}:{ACCOUNT}:cluster/{MSK_CLUSTER}/0a1b2c3d-1111-2222-3333-4444aaaa5555-1"
MSK_SLS_ARN = f"arn:aws:kafka:{REGION}:{ACCOUNT}:cluster/{MSK_SERVERLESS}/0a1b2c3d-1111-2222-3333-4444aaaa5556-s1"
MSK_ENIS = ("eni-0aaa1111bbbb9001", "eni-0aaa1111bbbb9002")
MSK_CLUSTERS: list[dict[str, Any]] = [
    {
        "ClusterName": MSK_SERVERLESS,
        "ClusterArn": MSK_SLS_ARN,
        "ClusterType": "SERVERLESS",
        "State": "ACTIVE",
        "Serverless": {
            "VpcConfigs": [
                {
                    "SubnetIds": [PUBLIC_SUBNET, PRIVATE_SUBNET],
                    "SecurityGroupIds": [APP_SG],
                }
            ]
        },
        "Tags": {},
    },
    {
        "ClusterName": MSK_CLUSTER,
        "ClusterArn": MSK_ARN,
        "ClusterType": "PROVISIONED",
        "State": "UPDATING",
        "Provisioned": {
            "BrokerNodeGroupInfo": {
                "InstanceType": "kafka.t3.small",
                "ClientSubnets": [PRIVATE_SUBNET, PUBLIC_SUBNET],
                "SecurityGroups": [DB_SG],
            },
            "CurrentBrokerSoftwareInfo": {"KafkaVersion": "3.6.0"},
            "NumberOfBrokerNodes": 2,
            "EncryptionInfo": {
                "EncryptionInTransit": {"ClientBroker": "TLS", "InCluster": True}
            },
        },
        "Tags": {"team": "app"},
    },
]
MSK_NODES: dict[str, list[dict[str, Any]]] = {
    MSK_ARN: [
        {
            "NodeType": "BROKER",
            "BrokerNodeInfo": {
                "BrokerId": float(i + 1),
                "AttachedENIId": eni,
                "ClientSubnet": subnet,
                "ClientVpcIpAddress": ip,
            },
        }
        for i, (eni, subnet, ip) in enumerate(
            [
                (MSK_ENIS[1], PRIVATE_SUBNET, "10.10.2.90"),
                (MSK_ENIS[0], PUBLIC_SUBNET, "10.10.1.90"),
            ]
        )
    ]
}

# EIC — 서브넷 하나에 산다(EC2 인스턴스와 같은 부모 체인).
EIC_ENDPOINT = "eice-0aaa1111bbbb2222"
INSTANCE_CONNECT_ENDPOINTS: list[dict[str, Any]] = [
    {
        "InstanceConnectEndpointId": EIC_ENDPOINT,
        "InstanceConnectEndpointArn": (
            f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance-connect-endpoint/{EIC_ENDPOINT}"
        ),
        "OwnerId": ACCOUNT,
        "State": "create-complete",
        "StateMessage": "",
        "DnsName": f"{EIC_ENDPOINT}.example.ec2-instance-connect-endpoint.{REGION}",
        "NetworkInterfaceIds": ["eni-0aaa1111bbbb9999"],
        "VpcId": APP_VPC,
        "SubnetId": PRIVATE_SUBNET,
        "AvailabilityZone": "us-east-1b",
        "PreserveClientIp": False,
        "SecurityGroupIds": [DB_SG, APP_SG],
        "Tags": [{"Key": "Name", "Value": "app-eic"}],
    }
]

# 2차 Site-to-Site VPN(2.6.0) — VGW 하나(앱 VPC 에 붙음) · CGW 하나 · 연결 둘(TGW 종단 · VGW 종단).
# TGW 종단 연결의 id 는 위 TGW 어태치먼트(`ResourceType=vpn`)의 `ResourceId` 와 같다 — 그 엣지가 이 노드로 풀린다.
VGW = "vgw-0aaa1111bbbb4444"
CGW = "cgw-0aaa1111bbbb5555"
VPN_TGW = "vpn-0aaa1111bbbb3333"
VPN_VGW = "vpn-0bbb2222cccc4444"
# 🔴 비밀 표지 — 응답에 평문으로 오는 PSK·장비 설정 XML. 스냅샷 어디에도 나오면 안 된다(test_vpn).
VPN_SECRET = "PSK-must-not-leak-0x5ec7e7"  # pragma: allowlist secret
VPN_GATEWAYS: list[dict[str, Any]] = [
    {
        "VpnGatewayId": VGW,
        "State": "available",
        "Type": "ipsec.1",
        "AmazonSideAsn": 64512,
        "VpcAttachments": [{"VpcId": APP_VPC, "State": "attached"}],
        "Tags": [{"Key": "Name", "Value": "app-vgw"}],
    },
    # 떨어진 VGW — 노드는 남고(부모 = 리전) VPC 선은 0
    {
        "VpnGatewayId": "vgw-0dead0000beef2222",
        "State": "available",
        "Type": "ipsec.1",
        "AmazonSideAsn": 64513,
        "VpcAttachments": [{"VpcId": EGRESS_VPC, "State": "detached"}],
        "Tags": [],
    },
]
CUSTOMER_GATEWAYS: list[dict[str, Any]] = [
    {
        "CustomerGatewayId": CGW,
        "State": "available",
        "Type": "ipsec.1",
        "IpAddress": "198.51.100.10",
        "BgpAsn": "65000",
        "DeviceName": "onprem-router",
        "Tags": [{"Key": "Name", "Value": "onprem-cgw"}],
    }
]
# Lambda 함수(2.8.0 · P2-6) — VPC 에 붙은 함수 하나(서브넷 하나) + 붙지 않은 함수 하나. 🔴 `LastModified`·`CodeSha256` 은
#    배포마다 바뀌는 값이라 수집기가 담으면 안 된다(불변식 ②) — 일부러 넣는다.
LAMBDA_FN_VPC = "orders-worker"
LAMBDA_FN_PLAIN = "nightly-report"
LAMBDA_STATE_LEAKS = ("2026-10-01T00:00:00.000+0000", "c2hhMjU2LWV4YW1wbGU=")
LAMBDA_FUNCTIONS: list[dict[str, Any]] = [
    {
        "FunctionName": LAMBDA_FN_VPC,
        "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{LAMBDA_FN_VPC}",
        "Runtime": "python3.12",
        "LastModified": LAMBDA_STATE_LEAKS[0],
        "CodeSha256": LAMBDA_STATE_LEAKS[1],
        "VpcConfig": {
            "VpcId": APP_VPC,
            "SubnetIds": [PRIVATE_SUBNET],
            "SecurityGroupIds": [DB_SG, APP_SG],
        },
    },
    {
        "FunctionName": LAMBDA_FN_PLAIN,
        "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{LAMBDA_FN_PLAIN}",
        "Runtime": "nodejs20.x",
        "LastModified": LAMBDA_STATE_LEAKS[0],
        "CodeSha256": LAMBDA_STATE_LEAKS[1],
        "VpcConfig": {"VpcId": "", "SubnetIds": [], "SecurityGroupIds": []},
    },
]

VPN_CONNECTIONS: list[dict[str, Any]] = [
    {
        "VpnConnectionId": VPN_TGW,
        "State": "available",
        "Type": "ipsec.1",
        "Category": "VPN",
        "CustomerGatewayId": CGW,
        "TransitGatewayId": CORE_TGW,
        "GatewayAssociationState": "associated",
        "CustomerGatewayConfiguration": f"<vpn_connection><psk>{VPN_SECRET}</psk></vpn_connection>",
        "Options": {
            "StaticRoutesOnly": False,
            "EnableAcceleration": False,
            "OutsideIpAddressType": "PublicIpv4",
            "TunnelInsideIpVersion": "ipv4",
            "TunnelBandwidth": "standard",
            "LocalIpv4NetworkCidr": "0.0.0.0/0",
            "RemoteIpv4NetworkCidr": "0.0.0.0/0",
            "TunnelOptions": [
                {"OutsideIpAddress": "203.0.113.2", "PreSharedKey": VPN_SECRET},
                {"OutsideIpAddress": "203.0.113.1", "PreSharedKey": VPN_SECRET},
            ],
        },
        "Routes": [],
        # 상태 — 구조에는 싣지 않는다(상태 단 탐침만). 한 터널만 UP.
        "VgwTelemetry": [
            {
                "OutsideIpAddress": "203.0.113.1",
                "Status": "UP",
                "AcceptedRouteCount": 3,
            },
            {
                "OutsideIpAddress": "203.0.113.2",
                "Status": "DOWN",
                "AcceptedRouteCount": 0,
            },
        ],
        "Tags": [{"Key": "Name", "Value": "onprem-vpn-tgw"}],
    },
    {
        "VpnConnectionId": VPN_VGW,
        "State": "available",
        "Type": "ipsec.1",
        "Category": "VPN",
        "CustomerGatewayId": CGW,
        "VpnGatewayId": VGW,
        "Options": {
            "StaticRoutesOnly": True,
            "TunnelOptions": [
                {"OutsideIpAddress": "203.0.113.11", "PreSharedKey": VPN_SECRET},
                {"OutsideIpAddress": "203.0.113.12", "PreSharedKey": VPN_SECRET},
            ],
        },
        "Routes": [
            {
                "DestinationCidrBlock": "192.168.0.0/16",
                "Source": "Static",
                "State": "available",
            },
            {
                "DestinationCidrBlock": "172.31.0.0/16",
                "Source": "Static",
                "State": "deleted",
            },
        ],
        "VgwTelemetry": [
            {"OutsideIpAddress": "203.0.113.11", "Status": "UP"},
            {"OutsideIpAddress": "203.0.113.12", "Status": "UP"},
        ],
        "Tags": [],
    },
]

# TGW 라우트 테이블 — 연결·전파·경로는 테이블 id 로 갈라지는 후속 호출이다(`_FOLLOWUPS`).
TGW_ROUTE_TABLE = "tgw-rtb-0aaa1111bbbb2222"
TGW_ROUTE_TABLES: list[dict[str, Any]] = [
    {
        "TransitGatewayRouteTableId": TGW_ROUTE_TABLE,
        "TransitGatewayId": CORE_TGW,
        "State": "available",
        "DefaultAssociationRouteTable": True,
        "DefaultPropagationRouteTable": True,
        "CreationTime": "2026-10-01T00:00:00Z",
        "Tags": [{"Key": "Name", "Value": "core-rtb"}],
    }
]
TGW_RT_ASSOCIATIONS: dict[str, list[dict[str, Any]]] = {
    TGW_ROUTE_TABLE: [
        {
            "TransitGatewayAttachmentId": "tgw-attach-0bbb2222cccc9999",
            "ResourceId": "vpn-0aaa1111bbbb3333",
            "ResourceType": "vpn",
            "State": "associated",
        },
        {
            "TransitGatewayAttachmentId": "tgw-attach-0aaa1111bbbb8888",
            "ResourceId": APP_VPC,
            "ResourceType": "vpc",
            "State": "associated",
        },
        # 🔴 끊긴 연결 — 담으면 「이 VPC 가 이 표를 쓴다」가 거짓이 된다.
        {
            "TransitGatewayAttachmentId": "tgw-attach-0ccc3333dddd0000",
            "ResourceId": EGRESS_VPC,
            "ResourceType": "vpc",
            "State": "disassociated",
        },
    ]
}
TGW_RT_PROPAGATIONS: dict[str, list[dict[str, Any]]] = {
    TGW_ROUTE_TABLE: [
        {
            "TransitGatewayAttachmentId": "tgw-attach-0aaa1111bbbb8888",
            "ResourceId": APP_VPC,
            "ResourceType": "vpc",
            "State": "enabled",
        },
        # 🔴 꺼진 전파 — 경로를 알려 주지 않는다.
        {
            "TransitGatewayAttachmentId": "tgw-attach-0bbb2222cccc9999",
            "ResourceId": "vpn-0aaa1111bbbb3333",
            "ResourceType": "vpn",
            "State": "disabled",
        },
    ]
}
TGW_RT_ROUTES: dict[str, list[dict[str, Any]]] = {
    TGW_ROUTE_TABLE: [
        {
            "DestinationCidrBlock": "10.10.0.0/16",
            "Type": "propagated",
            "State": "active",
            "TransitGatewayAttachments": [
                {
                    "TransitGatewayAttachmentId": "tgw-attach-0aaa1111bbbb8888",
                    "ResourceId": APP_VPC,
                    "ResourceType": "vpc",
                }
            ],
        },
        {
            "DestinationCidrBlock": "0.0.0.0/0",
            "Type": "static",
            "State": "blackhole",
        },
        {
            "DestinationCidrBlock": "10.20.0.0/16",
            "Type": "static",
            "State": "deleted",
            "TransitGatewayAttachments": [
                {"TransitGatewayAttachmentId": "tgw-attach-0ccc3333dddd0000"}
            ],
        },
    ]
}

# ECS — 목록은 **클러스터 ARN 문자열**이고 노드는 그 안의 서비스다(`ecs_service.enrich` 가 편다).
# 🔴 클러스터당 서비스 **1개**로 둔다: RESOURCE_NODE_COUNT 가 목록 항목(클러스터) 수를 세므로 여럿을
# 넣으면 노드 수 어서션이 조용히 어긋난다(EC2 예약과 같은 규칙). 여럿·10개 배치는 test_services 가 잰다.
# 🔴 `list_clusters` 는 EKS 와 **메서드 이름이 같다** — 가짜는 한 객체로 두 서비스를 흉내 내므로
#    `get_paginator("list_clusters")` 가 두 키(`clusters`·`clusterArns`)를 한 페이지에 같이 싣는다.
#    각 수집기는 자기 `result_key` 만 읽는다(`base.dig`).
ECS_CLUSTER = "app-ecs"
ECS_CLUSTER_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/{ECS_CLUSTER}"
ECS_SERVICE = "web"
ECS_SERVICE_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/{ECS_CLUSTER}/{ECS_SERVICE}"
ECS_CLUSTER_ARNS: list[str] = [ECS_CLUSTER_ARN]
ECS_SERVICE_ARNS: dict[str, list[str]] = {ECS_CLUSTER_ARN: [ECS_SERVICE_ARN]}
ECS_STATE_LEAKS = ("PRIMARY", "task-definition/web:42")
ECS_SERVICES: dict[str, dict[str, Any]] = {
    ECS_SERVICE_ARN: {
        "serviceArn": ECS_SERVICE_ARN,
        "serviceName": ECS_SERVICE,
        "clusterArn": ECS_CLUSTER_ARN,
        "launchType": "FARGATE",
        "status": "ACTIVE",
        "runningCount": 2,
        "desiredCount": 2,
        "taskDefinition": f"arn:aws:ecs:{REGION}:{ACCOUNT}:{ECS_STATE_LEAKS[1]}",
        "deployments": [{"status": ECS_STATE_LEAKS[0], "runningCount": 2}],
        # 🔵 2.8.0 — 서비스가 든 대상 그룹은 이 세계의 LB 에 없다(다른 계정·수집 범위 밖 LB 의 그룹). 그룹을 든 ECS 서비스는
        #    그 그룹의 등록을 전부 가져간다(`lb_targets._Index.claimer`) — 예전처럼 `blue`(instance 대상)를 들면 Fargate 가
        #    가질 수 없는 인스턴스 등록이 이 서비스 선이 된다(AWS 에 없는 모양). LB → ECS 선은 `lbt_world.py` 가 잰다.
        "loadBalancers": [
            {
                "targetGroupArn": f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/web-ecs/0f1e2d3c4b5a6999",
                "containerName": "web",
                "containerPort": 8080,
            }
        ],
        "networkConfiguration": {
            "awsvpcConfiguration": {
                "subnets": [PRIVATE_SUBNET, PUBLIC_SUBNET],
                "securityGroups": [DB_SG, APP_SG],
                "assignPublicIp": "DISABLED",
            }
        },
    }
}

# SageMaker 3종 — 목록에는 네트워크가 없고 상세(직접 호출)에 있다.
SM_NOTEBOOK = "app-notebook"
SM_NOTEBOOKS: list[dict[str, Any]] = [
    {
        "NotebookInstanceName": SM_NOTEBOOK,
        "NotebookInstanceArn": (
            f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:notebook-instance/{SM_NOTEBOOK}"
        ),
        "NotebookInstanceStatus": "InService",
        "InstanceType": "ml.t3.medium",
    }
]
SM_NOTEBOOK_DETAILS: dict[str, dict[str, Any]] = {
    SM_NOTEBOOK: {
        "NotebookInstanceName": SM_NOTEBOOK,
        "NotebookInstanceStatus": "InService",
        "SubnetId": PRIVATE_SUBNET,
        "SecurityGroups": [DB_SG, APP_SG],
        "DirectInternetAccess": "Disabled",
        "NetworkInterfaceId": "eni-0bbb2222cccc9999",
    }
}
SM_DOMAIN = "d-aaaa1111bbbb"
SM_DOMAINS: list[dict[str, Any]] = [
    {
        "DomainId": SM_DOMAIN,
        "DomainName": "app-studio",
        "DomainArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:domain/{SM_DOMAIN}",
        "Status": "InService",
    }
]
SM_DOMAIN_DETAILS: dict[str, dict[str, Any]] = {
    SM_DOMAIN: {
        "DomainId": SM_DOMAIN,
        "Status": "InService",
        "VpcId": APP_VPC,
        "SubnetIds": [PRIVATE_SUBNET, PUBLIC_SUBNET],
        "AppNetworkAccessType": "VpcOnly",
        "DefaultUserSettings": {"SecurityGroups": [DB_SG, APP_SG]},
    }
}
SM_ENDPOINT = "app-infer"
SM_ENDPOINTS: list[dict[str, Any]] = [
    {
        "EndpointName": SM_ENDPOINT,
        "EndpointArn": f"arn:aws:sagemaker:{REGION}:{ACCOUNT}:endpoint/{SM_ENDPOINT}",
        "EndpointStatus": "InService",
    }
]
SM_ENDPOINT_DETAILS: dict[str, dict[str, Any]] = {
    SM_ENDPOINT: {
        "EndpointName": SM_ENDPOINT,
        "EndpointConfigName": "app-infer-cfg",
        "EndpointStatus": "InService",
    }
}
SM_ENDPOINT_CONFIGS: dict[str, dict[str, Any]] = {
    "app-infer-cfg": {
        "EndpointConfigName": "app-infer-cfg",
        # 모델 둘 — 서브넷·SG 를 **합친다**. 역순으로 둬 정렬을 잰다.
        "ProductionVariants": [
            {"VariantName": "b", "ModelName": "app-model-b"},
            {"VariantName": "a", "ModelName": "app-model-a"},
        ],
    }
}
SM_MODELS: dict[str, dict[str, Any]] = {
    "app-model-a": {
        "ModelName": "app-model-a",
        "VpcConfig": {"Subnets": [PRIVATE_SUBNET], "SecurityGroupIds": [APP_SG]},
    },
    "app-model-b": {
        "ModelName": "app-model-b",
        "VpcConfig": {"Subnets": [PUBLIC_SUBNET], "SecurityGroupIds": [DB_SG]},
    },
}

# (kwarg 이름, describe 메서드, 응답 키, 부모 kwarg, 기본 픽스처)
# Route 53(2.8.0 · P2-4) — 공개 영역 하나(별칭: www → app-alb · cdn → CloudFront · 꼴이 아닌 별칭 하나) + 사설 영역 하나(app VPC).
#   🔴 레코드 값(IP·CNAME)은 수집기가 담으면 안 된다(민감) — 일부러 평범한 A 레코드 값을 넣는다.
ZONE_PUBLIC = "Z0AAA1111BBBB2222"
ZONE_PRIVATE = "Z0BBB2222CCCC3333"
ZONE_LEAK = "203.0.113.77"
HOSTED_ZONES: list[dict[str, Any]] = [
    {
        "Id": f"/hostedzone/{ZONE_PUBLIC}",
        "Name": "example.com.",
        "CallerReference": "ref-1",
        "Config": {"PrivateZone": False},
        "ResourceRecordSetCount": 5,
    },
    {
        "Id": f"/hostedzone/{ZONE_PRIVATE}",
        "Name": "corp.internal.",
        "CallerReference": "ref-2",
        "Config": {"PrivateZone": True},
        "ResourceRecordSetCount": 3,
    },
]
RECORD_SETS: dict[str, list[dict[str, Any]]] = {
    f"/hostedzone/{ZONE_PUBLIC}": [
        {
            "Name": "example.com.",
            "Type": "NS",
            "ResourceRecords": [{"Value": "ns-1.awsdns-00.com."}],
        },
        {
            "Name": "www.example.com.",
            "Type": "A",
            "AliasTarget": {
                "HostedZoneId": "Z35SXDOTRQ7X7K",
                "DNSName": f"dualstack.app-alb-1234567890.{REGION}.elb.amazonaws.com.",
                "EvaluateTargetHealth": True,
            },
        },
        {
            "Name": "cdn.example.com.",
            "Type": "A",
            "AliasTarget": {
                "HostedZoneId": "Z2FDTNDATAQYW2",
                "DNSName": "d1aaaaaaaaaaaa.cloudfront.net.",
                "EvaluateTargetHealth": False,
            },
        },
        {
            "Name": "static.example.com.",
            "Type": "A",
            "AliasTarget": {
                "HostedZoneId": "Z3AQBSTGFYJSTF",
                "DNSName": "s3-website-us-east-1.amazonaws.com.",
                "EvaluateTargetHealth": False,
            },
        },
        {
            "Name": "mail.example.com.",
            "Type": "A",
            "TTL": 300,
            "ResourceRecords": [{"Value": ZONE_LEAK}],
        },
    ],
    f"/hostedzone/{ZONE_PRIVATE}": [
        {
            "Name": "api.corp.internal.",
            "Type": "A",
            "AliasTarget": {
                "HostedZoneId": "Z35SXDOTRQ7X7K",
                "DNSName": f"internal-app-alb-1234567890.{REGION}.elb.amazonaws.com.",
                "EvaluateTargetHealth": False,
            },
        },
    ],
}
ZONE_DETAILS: dict[str, dict[str, Any]] = {
    f"/hostedzone/{ZONE_PRIVATE}": {
        "HostedZone": {"Id": f"/hostedzone/{ZONE_PRIVATE}"},
        "VPCs": [{"VPCRegion": REGION, "VPCId": APP_VPC}],
    },
}

# 배치 D(스키마 2.10.0) — 관리형 접두사 목록(`DescribeManagedPrefixLists` · 노드가 아니다 = `_REGIONAL`) + 고객 관리형 목록의 항목
#   (`GetManagedPrefixListEntries` · `PrefixListId` 로 갈린다 = `_FOLLOWUPS`). 기본 세계의 앱 퍼블릭 테이블이 `pl-0aaa1111bbbb2222`(S3) 로
#   게이트웨이 엔드포인트에 간다 — AWS 관리형이라 항목을 묻지 않는다. 고객 관리형 `corp-onprem` 은 쓰는 테이블이 없다(쓰지 않는 목록의 항목을 묻지 않는지를 잰다).
PL_S3 = "pl-0aaa1111bbbb2222"
PL_CORP = "pl-0ccc3333dddd4444"
MANAGED_PREFIX_LISTS: list[dict[str, Any]] = [
    {
        "PrefixListId": PL_S3,
        "AddressFamily": "IPv4",
        "State": "create-complete",
        "PrefixListName": f"com.amazonaws.{REGION}.s3",
        "OwnerId": "AWS",
        "Tags": [],
    },
    {
        "PrefixListId": PL_CORP,
        "AddressFamily": "IPv4",
        "State": "create-complete",
        "PrefixListName": "corp-onprem",
        "MaxEntries": 10,
        "Version": 3,
        "OwnerId": ACCOUNT,
        "Tags": [{"Key": "Name", "Value": "corp-onprem"}],
    },
]
PREFIX_LIST_ENTRIES: dict[str, list[dict[str, Any]]] = {
    PL_CORP: [
        {"Cidr": "192.168.10.0/24", "Description": "HQ"},
        {"Cidr": "192.168.20.0/24", "Description": "branch"},
    ]
}

# 배치 D(2.10.0) — 탄력적 IP 둘: bastion 인스턴스에 붙은 것 · 어디에도 안 붙은 것(왼쪽 아래 「!」 목록)
EIP_BASTION = "eipalloc-0aaa1111bbbb2222"
EIP_IDLE = "eipalloc-0bbb2222cccc3333"
ELASTIC_IPS: list[dict[str, Any]] = [
    {
        "AllocationId": EIP_BASTION,
        "AssociationId": "eipassoc-0aaa1111bbbb2222",
        "Domain": "vpc",
        "InstanceId": "i-0aaa1111bbbb2222",
        "NetworkInterfaceId": "eni-0aaa1111bbbb9999",
        "NetworkInterfaceOwnerId": ACCOUNT,
        "PrivateIpAddress": "10.10.1.20",
        "PublicIp": "198.51.100.10",
        "PublicIpv4Pool": "amazon",
        "NetworkBorderGroup": REGION,
        "Tags": [{"Key": "Name", "Value": "bastion-eip"}],
    },
    {
        "AllocationId": EIP_IDLE,
        "Domain": "vpc",
        "PublicIp": "198.51.100.11",
        "PublicIpv4Pool": "amazon",
        "NetworkBorderGroup": REGION,
    },
]

# 2.13.0(정보창 개편 · 사용자 결정 2026-10-24) — 콘솔이 보여 주는 라우팅 쪽 값.
#   DHCP 옵션 세트(쌍 하나에 한 번 · `DescribeDhcpOptions`) — 두 VPC 가 같은 세트를 쓴다(기본 세트 모양)
DHCP_OPTIONS_ID = "dopt-0aaa1111bbbb2222"
DHCP_OPTIONS: list[dict[str, Any]] = [
    {
        "DhcpOptionsId": DHCP_OPTIONS_ID,
        "OwnerId": ACCOUNT,
        # 일부러 키 순서를 뒤집었다 — 정렬이 코드에 있는지
        "DhcpConfigurations": [
            {"Key": "domain-name-servers", "Values": [{"Value": "AmazonProvidedDNS"}]},
            {"Key": "domain-name", "Values": [{"Value": "ec2.internal"}]},
        ],
        "Tags": [],
    }
]
# VPC id → `DescribeVpcAttribute` 의 두 값(목록 응답에 없다 · VPC 마다 2번). egress 는 DNS 호스트 이름을 껐다(콘솔 기본값 = 꺼짐)
VPC_DNS: dict[str, dict[str, bool]] = {
    APP_VPC: {"enableDnsSupport": True, "enableDnsHostnames": True},
    EGRESS_VPC: {"enableDnsSupport": True, "enableDnsHostnames": False},
}
# VPC 어태치먼트 상세(쌍 하나에 한 번 · `DescribeTransitGatewayVpcAttachments`) — 서브넷을 일부러 역순으로
TGW_VPC_ATTACHMENTS: list[dict[str, Any]] = [
    {
        "TransitGatewayAttachmentId": "tgw-attach-0aaa1111bbbb8888",
        "TransitGatewayId": CORE_TGW,
        "VpcId": APP_VPC,
        "VpcOwnerId": ACCOUNT,
        "State": "available",
        "SubnetIds": [PUBLIC_SUBNET, PRIVATE_SUBNET],
        "Options": {
            "DnsSupport": "enable",
            "SecurityGroupReferencingSupport": "disable",
            "Ipv6Support": "disable",
            "ApplianceModeSupport": "disable",
        },
    },
    {
        "TransitGatewayAttachmentId": "tgw-attach-0ccc3333dddd0000",
        "TransitGatewayId": CORE_TGW,
        "VpcId": EGRESS_VPC,
        "VpcOwnerId": ACCOUNT,
        "State": "deleting",
        "SubnetIds": [EGRESS_SUBNET],
        "Options": {"DnsSupport": "enable", "ApplianceModeSupport": "enable"},
    },
]
# TGW 라우팅 테이블 → 접두사 목록 참조(`GetTransitGatewayPrefixListReferences` · 표 id 로 갈린다)
TGW_RT_PREFIX_REFERENCES: dict[str, list[dict[str, Any]]] = {
    TGW_ROUTE_TABLE: [
        {
            "TransitGatewayRouteTableId": TGW_ROUTE_TABLE,
            "PrefixListId": PL_CORP,
            "PrefixListOwnerId": ACCOUNT,
            "State": "available",
            "Blackhole": False,
            "TransitGatewayAttachment": {
                "TransitGatewayAttachmentId": "tgw-attach-0bbb2222cccc9999",
                "ResourceType": "vpn",
                "ResourceId": "vpn-0aaa1111bbbb3333",
            },
        }
    ]
}

_FOLLOWUPS: tuple[tuple[str, str, str, str, dict[str, list[dict[str, Any]]]], ...] = (
    (
        "record_sets",
        "list_resource_record_sets",
        "ResourceRecordSets",
        "HostedZoneId",
        RECORD_SETS,
    ),
    ("listeners", "describe_listeners", "Listeners", "LoadBalancerArn", LISTENERS),
    (
        "target_groups",
        "describe_target_groups",
        "TargetGroups",
        "LoadBalancerArn",
        TARGET_GROUPS,
    ),
    (
        "dxgw_associations",
        "describe_direct_connect_gateway_associations",
        "directConnectGatewayAssociations",
        "directConnectGatewayId",
        DXGW_ASSOCIATIONS,
    ),
    ("ecs_services", "list_services", "serviceArns", "cluster", ECS_SERVICE_ARNS),
    (
        "tgw_rt_associations",
        "get_transit_gateway_route_table_associations",
        "Associations",
        "TransitGatewayRouteTableId",
        TGW_RT_ASSOCIATIONS,
    ),
    (
        "tgw_rt_propagations",
        "get_transit_gateway_route_table_propagations",
        "TransitGatewayRouteTablePropagations",
        "TransitGatewayRouteTableId",
        TGW_RT_PROPAGATIONS,
    ),
    (
        "tgw_rt_routes",
        "search_transit_gateway_routes",
        "Routes",
        "TransitGatewayRouteTableId",
        TGW_RT_ROUTES,
    ),
    # 2.13.0 — 콘솔 「접두사 목록 참조」 탭
    (
        "tgw_rt_prefix_references",
        "get_transit_gateway_prefix_list_references",
        "TransitGatewayPrefixListReferences",
        "TransitGatewayRouteTableId",
        TGW_RT_PREFIX_REFERENCES,
    ),
    # #114(2.9.0) — MSK 프로비저닝 클러스터의 브로커(ENI id)
    ("msk_nodes", "list_nodes", "NodeInfoList", "ClusterArn", MSK_NODES),
    # 배치 D(2.10.0) — 고객 관리형 접두사 목록의 항목
    (
        "prefix_list_entries",
        "get_managed_prefix_list_entries",
        "Entries",
        "PrefixListId",
        PREFIX_LIST_ENTRIES,
    ),
    # 배치 D — API Gateway REST 의 리소스(embed=methods) · HTTP API 의 통합 · RDS 프록시 대상 · GA 리스너·엔드포인트 그룹(기본 세계 = 없음)
    ("rest_resources", "get_resources", "items", "restApiId", {}),
    ("v2_integrations", "get_integrations", "Items", "ApiId", {}),
    ("proxy_targets", "describe_db_proxy_targets", "Targets", "DBProxyName", {}),
    ("ga_listeners", "list_listeners", "Listeners", "AcceleratorArn", {}),
    ("ga_endpoint_groups", "list_endpoint_groups", "EndpointGroups", "ListenerArn", {}),
    # #129 — Cloud WAN 연결 · Lattice 서비스 네트워크의 VPC·서비스 연관 · 대상 그룹의 등록 대상(기본 세계 = 없음)
    ("cwan_attachments", "list_attachments", "Attachments", "CoreNetworkId", {}),
    (
        "sn_vpc_associations",
        "list_service_network_vpc_associations",
        "items",
        "serviceNetworkIdentifier",
        {},
    ),
    (
        "sn_service_associations",
        "list_service_network_service_associations",
        "items",
        "serviceNetworkIdentifier",
        {},
    ),
    ("lattice_targets", "list_targets", "items", "targetGroupIdentifier", {}),
)

# 후속 호출이 **반드시** 받아야 하는 인자 — 실제 API 가 그것 없이 부르면 거부한다.
# 🔴 `search_transit_gateway_routes` 는 `Filters` 가 필수다(없으면 InvalidParameterValue). 가짜가
#    친절하게 받아 주면 실계정에서만 터진다.
_REQUIRED_KWARGS: dict[str, str] = {"search_transit_gateway_routes": "Filters"}

# (kwarg 이름, describe 메서드, 응답 키, 기본 픽스처)
# 🔴 EKS 는 목록이 **이름 문자열**이라 마지막 자리가 list[str] 다(다른 종류는 list[dict]).
#    그래서 항목 타입을 넓게 잡는다 — RESOURCE_NODE_COUNT 는 len() 만 세므로 무관하다.
_DATASETS: tuple[tuple[str, str, str, list[Any]], ...] = (
    ("vpcs", "describe_vpcs", "Vpcs", VPCS),
    ("subnets", "describe_subnets", "Subnets", SUBNETS),
    ("route_tables", "describe_route_tables", "RouteTables", ROUTE_TABLES),
    (
        "internet_gateways",
        "describe_internet_gateways",
        "InternetGateways",
        INTERNET_GATEWAYS,
    ),
    ("nat_gateways", "describe_nat_gateways", "NatGateways", NAT_GATEWAYS),
    (
        "transit_gateways",
        "describe_transit_gateways",
        "TransitGateways",
        TRANSIT_GATEWAYS,
    ),
    (
        "transit_gateway_attachments",
        "describe_transit_gateway_attachments",
        "TransitGatewayAttachments",
        TRANSIT_GATEWAY_ATTACHMENTS,
    ),
    (
        "peerings",
        "describe_vpc_peering_connections",
        "VpcPeeringConnections",
        PEERINGS,
    ),
    ("vpc_endpoints", "describe_vpc_endpoints", "VpcEndpoints", VPC_ENDPOINTS),
    ("security_groups", "describe_security_groups", "SecurityGroups", SECURITY_GROUPS),
    ("network_acls", "describe_network_acls", "NetworkAcls", NETWORK_ACLS),
    ("load_balancers", "describe_load_balancers", "LoadBalancers", LOAD_BALANCERS),
    (
        "dx_connections",
        "describe_connections",
        "connections",
        DIRECT_CONNECT_CONNECTIONS,
    ),
    (
        "dx_virtual_interfaces",
        "describe_virtual_interfaces",
        "virtualInterfaces",
        DIRECT_CONNECT_VIRTUAL_INTERFACES,
    ),
    (
        "dx_gateways",
        "describe_direct_connect_gateways",
        "directConnectGateways",
        DIRECT_CONNECT_GATEWAYS,
    ),
    ("instances", "describe_instances", "Reservations", INSTANCE_RESERVATIONS),
    (
        "auto_scaling_groups",
        "describe_auto_scaling_groups",
        "AutoScalingGroups",
        AUTO_SCALING_GROUPS,
    ),
    ("eks_clusters", "list_clusters", "clusters", EKS_CLUSTER_NAMES),
    ("network_firewalls", "list_firewalls", "Firewalls", NETWORK_FIREWALLS),
    (
        "cloudfront_distributions",
        "list_distributions",
        # 🔴 점이 있는 키 — `_Pages` 가 이 표기를 보고 **실제로 중첩된** 페이지를 만든다.
        "DistributionList.Items",
        CLOUDFRONT_DISTRIBUTIONS,
    ),
    (
        "tgw_route_tables",
        "describe_transit_gateway_route_tables",
        "TransitGatewayRouteTables",
        TGW_ROUTE_TABLES,
    ),
    ("rds_instances", "describe_db_instances", "DBInstances", RDS_DB_INSTANCES),
    # 🔴 메서드 자리에 `#ecs` 가 붙은 이유 = EKS 와 이름이 같다(`get_paginator` 가 두 키를 합친다).
    ("ecs_clusters", "list_clusters#ecs", "clusterArns", ECS_CLUSTER_ARNS),
    ("sm_notebooks", "list_notebook_instances", "NotebookInstances", SM_NOTEBOOKS),
    ("sm_domains", "list_domains", "Domains", SM_DOMAINS),
    ("sm_endpoints", "list_endpoints", "Endpoints", SM_ENDPOINTS),
    (
        "instance_connect_endpoints",
        "describe_instance_connect_endpoints",
        "InstanceConnectEndpoints",
        INSTANCE_CONNECT_ENDPOINTS,
    ),
    ("vpn_gateways", "describe_vpn_gateways", "VpnGateways", VPN_GATEWAYS),
    (
        "customer_gateways",
        "describe_customer_gateways",
        "CustomerGateways",
        CUSTOMER_GATEWAYS,
    ),
    ("vpn_connections", "describe_vpn_connections", "VpnConnections", VPN_CONNECTIONS),
    ("lambda_functions", "list_functions", "Functions", LAMBDA_FUNCTIONS),
    ("hosted_zones", "list_hosted_zones", "HostedZones", HOSTED_ZONES),
    # #114(2.9.0) — VPC 안 IP 를 쓰는 서비스.
    (
        "elasticache_clusters",
        "describe_cache_clusters",
        "CacheClusters",
        ELASTICACHE_CLUSTERS,
    ),
    ("rds_clusters", "describe_db_clusters", "DBClusters", RDS_DB_CLUSTERS),
    ("opensearch_domains", "list_domain_names", "DomainNames", OPENSEARCH_DOMAIN_NAMES),
    ("msk_clusters", "list_clusters_v2", "ClusterInfoList", MSK_CLUSTERS),
    # 배치 D(2.10.0) — 탄력적 IP(기본 둘) · API Gateway REST·HTTP · RDS 프록시 · Global Accelerator(기본 세계 = 없음 · `rel_world.py` 가 채운다)
    ("elastic_ips", "describe_addresses", "Addresses", ELASTIC_IPS),
    ("rest_apis", "get_rest_apis", "items", []),
    ("http_apis", "get_apis", "Items", []),
    ("db_proxies", "describe_db_proxies", "DBProxies", []),
    ("accelerators", "list_accelerators", "Accelerators", []),
    # #129(2.11.0) — Cloud WAN 코어 네트워크 · VPC Lattice 서비스 네트워크 · 서비스(기본 세계 = 없음 · `rel_world.py` 가 채운다)
    # 🔴 `#lattice` = ECS 후속 호출 `list_services`(cluster=…)와 메서드 이름이 같다 — `cluster` 없이 부르면 이 목록이다(`_ByCluster`)
    ("core_networks", "list_core_networks", "CoreNetworks", []),
    ("service_networks", "list_service_networks", "items", []),
    ("lattice_services", "list_services#lattice", "items", []),
)

# 노드가 되지 않는 **리전 단위** 후속 목록(서브넷 그룹·복제 그룹 …) — 페이지네이터로 한 번에 읽는다. `_DATASETS` 밖이라
# `RESOURCE_NODE_COUNT` 가 세지 않는다. 거부는 `errors={메서드: 예외}` 로 넣는다(목록 호출과 같은 문).
_REGIONAL: tuple[tuple[str, str, str, list[Any]], ...] = (
    (
        "cache_subnet_groups",
        "describe_cache_subnet_groups",
        "CacheSubnetGroups",
        CACHE_SUBNET_GROUPS,
    ),
    (
        "cache_replication_groups",
        "describe_replication_groups",
        "ReplicationGroups",
        CACHE_REPLICATION_GROUPS,
    ),
    (
        "db_subnet_groups",
        "describe_db_subnet_groups",
        "DBSubnetGroups",
        DB_SUBNET_GROUPS,
    ),
    # 배치 D(2.10.0) — 이 계정이 보는 관리형 접두사 목록 전부(AWS 관리형 + 고객 관리형)
    (
        "managed_prefix_lists",
        "describe_managed_prefix_lists",
        "PrefixLists",
        MANAGED_PREFIX_LISTS,
    ),
    # #129(2.11.0) — TGW Connect · 그 GRE 피어(쌍 하나에 한 번 · 기본 세계 = 없음) · Lattice 대상 그룹(쌍 하나에 한 번)
    (
        "tgw_connects",
        "describe_transit_gateway_connects",
        "TransitGatewayConnects",
        [],
    ),
    (
        "tgw_connect_peers",
        "describe_transit_gateway_connect_peers",
        "TransitGatewayConnectPeers",
        [],
    ),
    ("lattice_target_groups", "list_target_groups", "items", []),
    # 2.13.0(정보창 개편) — DHCP 옵션 세트 · VPC 어태치먼트 상세(쌍 하나에 한 번)
    ("dhcp_options", "describe_dhcp_options", "DhcpOptions", DHCP_OPTIONS),
    (
        "tgw_vpc_attachments",
        "describe_transit_gateway_vpc_attachments",
        "TransitGatewayVpcAttachments",
        TGW_VPC_ATTACHMENTS,
    ),
)

# 컨테이너 2개(account·region)를 뺀 「1계정 1리전에서 나와야 하는 리소스 노드 수」.
RESOURCE_NODE_COUNT = sum(len(items) for _, _, _, items in _DATASETS)


def client_error(
    code: str, operation: str, message: str = "재현용 오류"
) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def _nest(key: str, items: list[Any]) -> dict[str, Any]:
    """점으로 끊은 경로대로 **실제로 중첩된** 페이지를 만든다.

    🔴 여기서 평평하게 만들면 `base.dig()` 의 중첩 지원이 **미측정**이 된다 — 가짜가 진짜와
    다른 모양을 주면 검사는 가짜의 문법을 재는 것이다(2026-09-23 fixtures 사고와 같은 종류).
    CloudFront `list_distributions` 는 `{"DistributionList": {"Items": [...]}}` 를 준다.
    """
    page: Any = items
    for part in reversed(key.split(".")):
        page = {part: page}
    return page


class _Pages:
    def __init__(self, key: str, items: list[dict[str, Any]]) -> None:
        self._key = key
        self._items = items

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        # 페이지를 2장으로 쪼갠다 — 병합을 빼먹으면 개수 어서션이 깨진다.
        middle = len(self._items) // 2
        return [
            _nest(self._key, self._items[:middle]),
            _nest(self._key, self._items[middle:]),
        ]


class _MergedPages:
    """메서드 이름이 같은 두 서비스(EKS·ECS 의 `list_clusters`)를 한 가짜가 흉내 낼 때 쓴다.

    각 장에 두 키를 **같이** 싣는다 — 수집기는 자기 `result_key` 만 읽으므로 서로의 항목을 못 본다.
    🔴 실제 응답은 한 키만 준다. 그래서 이 합침은 「상대 키를 읽어 버리는」 결함은 못 잡는다 —
       그것은 `test_services` 가 서비스별 결과 수로 잰다.
    """

    def __init__(self, parts: list[tuple[str, list[Any]]]) -> None:
        self._parts = parts

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        first: dict[str, Any] = {}
        second: dict[str, Any] = {}
        for key, items in self._parts:
            middle = len(items) // 2
            first.update(_nest(key, items[:middle]))
            second.update(_nest(key, items[middle:]))
        return [first, second]


class _Raises:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        raise self._error


class _Keyed:
    """부모 ARN 으로 갈라지는 응답 — 후속 호출용. LB 별로 다른 결과·다른 실패를 줄 수 있다."""

    def __init__(
        self,
        method: str,
        key: str,
        kwarg: str,
        data: dict[str, list[dict[str, Any]]],
        errors: dict[str, Exception],
    ) -> None:
        self._method, self._key, self._kwarg = method, key, kwarg
        self._data, self._errors = data, errors

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        parent = kwargs.get(self._kwarg)
        if parent is None:
            # 전체를 훑어 오면 LB 별 귀속이 거짓이 된다 — 조용히 답하지 않는다.
            raise AssertionError(f"{self._method} 를 {self._kwarg} 없이 불렀다")
        required = _REQUIRED_KWARGS.get(self._method)
        if required is not None and not kwargs.get(required):
            raise client_error("InvalidParameterValue", self._method)
        if parent in self._errors:
            raise self._errors[parent]
        # 등록하지 않은 부모는 0개 — 실제 AWS 도 리스너 없는 LB 에 빈 목록을 준다.
        return [{self._key: self._data.get(parent, [])}]


class _FilteredPages:
    """`Filters` 를 실제처럼 적용하는 페이지 — `describe_network_interfaces` 용(W3②).

    🔴 가짜가 필터를 무시하면 「다른 VPC 의 ENI 로 좁혔다」가 테스트에서 안 보인다.
    """

    _FIELDS = {
        "requester-id": "RequesterId",
        "vpc-id": "VpcId",
        "availability-zone": "AvailabilityZone",
        # 2.8.0(P2-2) — 라우트의 `eni-` 대상을 id 로 묻는다
        "network-interface-id": "NetworkInterfaceId",
    }

    def __init__(self, key: str, items: list[dict[str, Any]]) -> None:
        self._key, self._items = key, items

    @staticmethod
    def _addresses(eni: dict[str, Any]) -> set[str]:
        # `addresses.private-ip-address` = 주 IP + 보조 IP 전부(AWS 필터 정의 그대로 — EKS 파드 IP 는 보조 IP 다)
        return {str(eni.get("PrivateIpAddress") or "")} | {
            str(p.get("PrivateIpAddress") or "")
            for p in eni.get("PrivateIpAddresses") or []
        }

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        filters = kwargs.get("Filters")
        if not filters:  # 계정 전체 ENI 를 훑으면 대형 계정에서 느리고 귀속도 못 한다
            raise AssertionError("describe_network_interfaces 를 Filters 없이 불렀다")

        def hit(e: dict[str, Any], f: dict[str, Any]) -> bool:
            if f["Name"] == "addresses.private-ip-address":
                return bool(self._addresses(e) & set(f["Values"]))
            return e.get(self._FIELDS[f["Name"]]) in f["Values"]

        found = [e for e in self._items if all(hit(e, f) for f in filters)]
        return _Pages(self._key, found).paginate()


class _ByCluster:
    """`list_services` — ECS 는 `cluster=…` 로, VPC Lattice 는 인자 없이 부른다(#129). 인자로 두 가짜를 가른다."""

    def __init__(self, ecs: Any, lattice: Any) -> None:
        self._ecs, self._lattice = ecs, lattice

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        return (self._ecs if "cluster" in kwargs else self._lattice).paginate(**kwargs)


class FakeAws:
    """1차 12종 + Direct Connect 3종의 `describe_*` 를 아는 가짜 클라이언트(ec2 · elbv2 · directconnect 겸용)."""

    def __init__(
        self,
        *,
        errors: dict[str, Exception] | None = None,
        followup_errors: dict[tuple[str, str], Exception] | None = None,
        network_interfaces: list[dict[str, Any]] | None = None,
        target_health: dict[str, list[dict[str, Any]]] | None = None,
        web_acls: dict[str, dict[str, Any]] | None = None,
        ecs_details: dict[str, dict[str, Any]] | None = None,
        endpoint_services: list[dict[str, Any]] | None = None,
        firewall_details: dict[str, dict[str, Any]] | None = None,
        tg_tags: dict[str, dict[str, str]] | None = None,
        eks_details: dict[str, dict[str, Any]] | None = None,
        consumer_services: dict[str, str] | None = None,
        zone_details: dict[str, dict[str, Any]] | None = None,
        vpc_origins: list[dict[str, Any]] | None = None,
        opensearch_details: dict[str, dict[str, Any]] | None = None,
        vpc_links: list[dict[str, Any]] | None = None,
        vpc_links_v2: list[dict[str, Any]] | None = None,
        waf_regional: list[dict[str, Any]] | None = None,
        waf_cloudfront: list[dict[str, Any]] | None = None,
        waf_details: dict[str, dict[str, Any]] | None = None,
        waf_resources: dict[str, list[str]] | None = None,
        vpc_dns: dict[str, dict[str, bool]] | None = None,
        cache_policies: list[dict[str, Any]] | None = None,
        oacs: list[dict[str, Any]] | None = None,
        lb_rules: dict[str, list[dict[str, Any]]] | None = None,
        listener_certificates: dict[str, list[dict[str, Any]]] | None = None,
        account_name: str | None = ACCOUNT_NAME,
        **overrides: Any,
    ) -> None:
        known = (
            {name for name, _, _, _ in _DATASETS}
            | {name for name, _, _, _, _ in _FOLLOWUPS}
            | {name for name, _, _, _ in _REGIONAL}
        )
        unknown = set(overrides) - known
        if unknown:  # 오타 난 kwarg 가 조용히 무시되면 「기본 픽스처로 통과」가 된다
            raise TypeError(f"모르는 데이터셋: {sorted(unknown)}")
        self._data = {
            method: (key, default if overrides.get(name) is None else overrides[name])
            for name, method, key, default in _DATASETS
        }
        self._keyed = {
            method: (
                key,
                kwarg,
                default if overrides.get(name) is None else overrides[name],
            )
            for name, method, key, kwarg, default in _FOLLOWUPS
        }
        self._regional = {
            method: (key, default if overrides.get(name) is None else overrides[name])
            for name, method, key, default in _REGIONAL
        }
        self._errors = errors or {}
        # ResourceArn → web ACL. `{}` 를 주면 「어느 ALB 에도 안 붙었다」를 잰다(기본은 ALB 하나에 붙음).
        self._web_acls = WEB_ACLS if web_acls is None else web_acls
        # (메서드, 부모 ARN) → 예외. **한 LB 의 후속 호출만** 거부되는 경우를 만드는 데 쓴다.
        self._followup_errors = followup_errors or {}
        # W3② — 노드가 되지 않는 응답이라 `_DATASETS` 밖이다(노드 수 어서션이 안 센다).
        self._enis = list(network_interfaces or [])
        self._target_health = (
            TARGET_HEALTH if target_health is None else dict(target_health)
        )
        # 서비스 ARN → `DescribeServices` 항목(LB 대상 선 2.7.0 — 대상 그룹을 든 ECS 서비스를 바꿔 끼운다)
        self._ecs = ECS_SERVICES if ecs_details is None else dict(ecs_details)
        # 2.8.0 — 이 계정이 **제공하는** 엔드포인트 서비스 구성(GWLB·NLB 뒤 · `DescribeVpcEndpointServiceConfigurations`).
        #   노드가 되지 않는 응답이라 `_DATASETS` 밖이다. 기본 = 없음(기본 세계의 LB 는 엔드포인트 서비스 뒤에 있지 않다).
        self._endpoint_services = list(endpoint_services or [])
        # Network Firewall 상세(`describe_firewall`) — 기본 = 모듈의 `NETWORK_FIREWALL_DETAILS`(다른 세계가 바꿔 끼운다)
        self._firewall_details = (
            NETWORK_FIREWALL_DETAILS
            if firewall_details is None
            else dict(firewall_details)
        )
        # 2.8.0(P2-1) — 소비자 쪽 엔드포인트 서비스 이름 → 주인 계정(`DescribeVpcEndpointServices`). 기본 = 없음(이름을 물으면
        #   AWS 처럼 `InvalidServiceName`)
        self._consumer_services = dict(consumer_services or {})
        # Route 53 사설 영역 상세(`get_hosted_zone`) — 기본 = 모듈의 `ZONE_DETAILS`
        self._zones = ZONE_DETAILS if zone_details is None else dict(zone_details)
        # CloudFront VPC 원본(2.8.0 · P2-3 · `list_vpc_origins` — 페이지네이터 없음 · Marker) — 기본 = 없음
        self._vpc_origins = list(vpc_origins or [])
        # 2.8.0(P2-5) — 대상 그룹 ARN → 태그(`elbv2:DescribeTags`). 기본 = 태그 없음
        self._tg_tags = dict(tg_tags or {})
        # EKS 상세(`describe_cluster`) — 기본 = 모듈의 `EKS_CLUSTERS`
        self._eks = EKS_CLUSTERS if eks_details is None else dict(eks_details)
        # 도메인 이름 → `DescribeDomains` 항목(#114 · OpenSearch)
        self._os = (
            OPENSEARCH_DETAILS
            if opensearch_details is None
            else dict(opensearch_details)
        )
        # 배치 D — API Gateway VPC 링크(v1 = `items` · v2 = `Items` · 두 서비스가 같은 메서드 이름 `get_vpc_links` — EKS·ECS 의 `list_clusters` 처럼 한 장에 두 키)
        self._vpc_links = list(vpc_links or [])
        self._vpc_links_v2 = list(vpc_links_v2 or [])
        # 2.12.0(ADR 0014) — WAF 웹 ACL. 범위(`Scope`)마다 목록 · Id → `GetWebACL` 의 `WebACL` · web ACL ARN → API Gateway 스테이지 ARN.
        #   기본 = 없음(기본 세계에는 web ACL 노드가 없다 — `rel_world.py` 가 채운다). 거부: 목록 = `errors["list_web_acls#<Scope>"]` ·
        #   상세 = `followup_errors[("get_web_acl", Id)]` · 보호 대상 = `followup_errors[("list_resources_for_web_acl", ARN)]`
        self._waf = {
            "REGIONAL": list(waf_regional or []),
            "CLOUDFRONT": list(waf_cloudfront or []),
        }
        self._waf_details = dict(waf_details or {})
        self._waf_resources = dict(waf_resources or {})
        # 2.13.0 — VPC id → `DescribeVpcAttribute` 값(기본 = `VPC_DNS` · 없는 VPC 는 AWS 기본값 support=켬·hostnames=끔).
        #   거부 = `followup_errors[("describe_vpc_attribute", VPC id)]`
        self._vpc_dns = VPC_DNS if vpc_dns is None else dict(vpc_dns)
        # 콘솔식 정보창(사용자 결정 2026-10-24) — CloudFront 캐시 정책 · 원본 접근 제어 목록(Marker · 두 장) ·
        #   ELBv2 리스너 규칙(ListenerArn → Rules) · 리스너 인증서(ListenerArn → Certificates). 기본 = 없음.
        self._cache_policies = list(cache_policies or [])
        self._oacs = list(oacs or [])
        self._lb_rules = dict(lb_rules or {})
        self._listener_certs = dict(listener_certificates or {})
        # 두 ELBv2 API 는 페이지네이터가 있다(boto3 `can_paginate` True) → 리스너 ARN 으로 갈라지는 `_Keyed` 로 흉내 낸다.
        #   거부는 `followup_errors[("describe_rules", 리스너 ARN)]` · `followup_errors[("describe_listener_certificates", …)]`.
        self._keyed["describe_rules"] = ("Rules", "ListenerArn", self._lb_rules)
        self._keyed["describe_listener_certificates"] = (
            "Certificates",
            "ListenerArn",
            self._listener_certs,
        )
        # 계정 이름(`get_account_information` · 구조 수집만 부른다) — `None` = 응답에 `AccountName` 키가 없다.
        #   거부 = `errors["get_account_information"]`. `calls` 에 넣지 않는다(종류별 호출 목록을 재는 검사가 많다) — 따로 센다.
        self._account_name = account_name
        self.account_calls = 0
        self.calls: list[str] = []
        # 페이지네이터 없는 메서드의 직접 호출 인자 — 토큰이 되돌아왔는지 재는 데 쓴다.
        self.direct_calls: list[tuple[str, dict[str, Any]]] = []

    @classmethod
    def empty(cls, **overrides: Any) -> FakeAws:
        base: dict[str, Any] = {name: [] for name, _, _, _ in _DATASETS}
        base.update({name: {} for name, _, _, _, _ in _FOLLOWUPS})
        base.update({name: [] for name, _, _, _ in _REGIONAL})
        base.update(overrides)
        return cls(**base)

    def can_paginate(self, method: str) -> bool:
        return method not in UNPAGINATED

    def _unpaginated(self, method: str, **kwargs: Any) -> dict[str, Any]:
        """`nextToken` 을 손으로 돌려야 하는 API 흉내 — 2장으로 쪼개고 첫 장에만 토큰을 싣는다."""
        self.calls.append(method)
        self.direct_calls.append((method, dict(kwargs)))
        if method in self._errors:
            raise self._errors[method]
        key, items = self._data[method]
        middle = len(items) // 2
        if kwargs.get("nextToken") == "page-2":
            return {key: items[middle:]}
        if kwargs.get("nextToken"):
            raise AssertionError(f"{method} 에 모르는 토큰: {kwargs['nextToken']}")
        page: dict[str, Any] = {key: items[:middle]}
        if items[middle:]:
            page["nextToken"] = "page-2"
        return page

    def describe_vpc_attribute(self, VpcId: str, Attribute: str) -> dict[str, Any]:
        """VPC 속성 하나(2.13.0) — 페이지네이터 없음 · 요청 하나에 속성 하나(AWS 와 같다: 두 속성을 한 번에 못 묻는다)."""
        self.calls.append("describe_vpc_attribute")
        self.direct_calls.append(
            ("describe_vpc_attribute", {"VpcId": VpcId, "Attribute": Attribute})
        )
        err = self._followup_errors.get(("describe_vpc_attribute", VpcId))
        if err is not None:
            raise err
        known = {
            "enableDnsSupport": "EnableDnsSupport",
            "enableDnsHostnames": "EnableDnsHostnames",
        }
        if Attribute not in known:
            raise client_error("InvalidParameterValue", "DescribeVpcAttribute")
        defaults = {"enableDnsSupport": True, "enableDnsHostnames": False}
        value = self._vpc_dns.get(VpcId, defaults).get(Attribute, defaults[Attribute])
        return {"VpcId": VpcId, known[Attribute]: {"Value": value}}

    def get_account_information(self, **kwargs: Any) -> dict[str, Any]:
        """🔴 `AccountId` 를 넘기면 AssertionError — 계정마다 **그 계정의 세션**으로 자기 자신만 묻는 것이 계약이다(`account_name.py`)."""
        assert not kwargs, f"AccountId 없이 불러야 한다(자기 계정만): {kwargs}"
        self.account_calls += 1
        if "get_account_information" in self._errors:
            raise self._errors["get_account_information"]
        got: dict[str, Any] = {"AccountId": ACCOUNT, "AccountState": "ACTIVE"}
        if self._account_name is not None:
            got["AccountName"] = self._account_name
        return got

    def describe_connections(self, **kwargs: Any) -> dict[str, Any]:
        return self._unpaginated("describe_connections", **kwargs)

    def describe_virtual_interfaces(self, **kwargs: Any) -> dict[str, Any]:
        return self._unpaginated("describe_virtual_interfaces", **kwargs)

    def get_paginator(self, method: str) -> Any:
        if method in UNPAGINATED:
            raise AssertionError(
                f"{method} 에는 페이지네이터가 없다 — can_paginate 를 봐라"
            )
        self.calls.append(method)
        if method in self._errors:
            return _Raises(self._errors[method])
        if method == "list_services" and method in self._keyed:
            # #129 — ECS(`cluster=…` 후속 호출)와 Lattice(인자 없는 목록)가 같은 이름이다. 인자로 가른다
            key, kwarg, data = self._keyed[method]
            errors = {
                parent: exc
                for (m, parent), exc in self._followup_errors.items()
                if m == method
            }
            lattice = self._data["list_services#lattice"]
            lattice_err = self._errors.get("list_services#lattice")
            return _ByCluster(
                _Keyed(method, key, kwarg, data, errors),
                _Raises(lattice_err) if lattice_err else _Pages(*lattice),
            )
        if method in self._keyed:
            key, kwarg, data = self._keyed[method]
            errors = {
                parent: exc
                for (m, parent), exc in self._followup_errors.items()
                if m == method
            }
            return _Keyed(method, key, kwarg, data, errors)
        if method == "describe_network_interfaces":
            return _FilteredPages("NetworkInterfaces", self._enis)
        if method == "describe_vpc_endpoint_service_configurations":
            return _Pages("ServiceConfigurations", self._endpoint_services)
        if method in self._regional:
            return _Pages(*self._regional[method])
        if method == "get_vpc_links":
            return _MergedPages(
                [("items", self._vpc_links), ("Items", self._vpc_links_v2)]
            )
        if method == "list_clusters":
            # EKS·ECS 가 같은 이름을 쓴다 — 두 키를 한 장에 싣는다(`_MergedPages`).
            return _MergedPages(
                [self._data["list_clusters"], self._data["list_clusters#ecs"]]
            )
        key, items = self._data[method]
        return _Pages(key, items)

    def describe_cluster(self, name: str) -> dict[str, Any]:
        # EKS 상세는 단일 객체다(리스트가 아님) → 페이지네이터가 아니라 직접 호출한다.
        # 후속 호출 거부는 followup_errors[("describe_cluster", 이름)] 로 넣는다(LB 리스너와 같은 방식).
        self.calls.append("describe_cluster")
        err = self._followup_errors.get(("describe_cluster", name))
        if err is not None:
            raise err
        detail = self._eks.get(name)
        if detail is None:
            raise client_error("ResourceNotFoundException", "DescribeCluster")
        return {"cluster": detail}

    def _two_pages(
        self, method: str, key: str, items: list[dict[str, Any]], kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(method)
        self.direct_calls.append((method, dict(kwargs)))
        if method in self._errors:
            raise self._errors[method]
        half = (len(items) + 1) // 2
        if kwargs.get("Marker") == "page-2":
            return {key: {"Items": items[half:]}}
        return {
            key: {
                "Items": items[:half],
                **({"NextMarker": "page-2"} if items[half:] else {}),
            }
        }

    def list_cache_policies(self, **kwargs: Any) -> dict[str, Any]:
        return self._two_pages(
            "list_cache_policies", "CachePolicyList", self._cache_policies, kwargs
        )

    def list_origin_access_controls(self, **kwargs: Any) -> dict[str, Any]:
        return self._two_pages(
            "list_origin_access_controls", "OriginAccessControlList", self._oacs, kwargs
        )

    def list_vpc_origins(self, **kwargs: Any) -> dict[str, Any]:
        """두 장으로 쪼개 첫 장에 `NextMarker` 를 싣는다(수집기가 끝까지 도는지 잰다)."""
        self.calls.append("list_vpc_origins")
        self.direct_calls.append(("list_vpc_origins", dict(kwargs)))
        if "list_vpc_origins" in self._errors:
            raise self._errors["list_vpc_origins"]
        half = (len(self._vpc_origins) + 1) // 2
        if kwargs.get("Marker") == "page-2":
            return {
                "VpcOriginList": {
                    "Items": self._vpc_origins[half:],
                    "IsTruncated": False,
                }
            }
        page: dict[str, Any] = {
            "Items": self._vpc_origins[:half],
            "IsTruncated": half < len(self._vpc_origins),
        }
        if half < len(self._vpc_origins):
            page["NextMarker"] = "page-2"
        return {"VpcOriginList": page}

    def get_hosted_zone(self, Id: str) -> dict[str, Any]:
        """Route 53 영역 상세(단일 객체) — 거부는 `followup_errors[("get_hosted_zone", Id)]`."""
        self.calls.append("get_hosted_zone")
        err = self._followup_errors.get(("get_hosted_zone", Id))
        if err is not None:
            raise err
        detail = self._zones.get(Id)
        if detail is None:
            raise client_error("NoSuchHostedZone", "GetHostedZone")
        return detail

    def describe_vpc_endpoint_services(self, ServiceNames: list[str]) -> dict[str, Any]:
        """소비자 쪽 서비스 정보 — 이름 하나라도 모르면 AWS 처럼 **호출 전체**를 `InvalidServiceName` 으로 거절한다."""
        self.calls.append("describe_vpc_endpoint_services")
        self.direct_calls.append(
            ("describe_vpc_endpoint_services", {"ServiceNames": list(ServiceNames)})
        )
        if "describe_vpc_endpoint_services" in self._errors:
            raise self._errors["describe_vpc_endpoint_services"]
        missing = [n for n in ServiceNames if n not in self._consumer_services]
        if missing:
            raise client_error(
                "InvalidServiceName",
                "DescribeVpcEndpointServices",
                f"없는 서비스 {missing}",
            )
        return {
            "ServiceDetails": [
                {
                    "ServiceName": n,
                    "Owner": self._consumer_services[n],
                    "ServiceType": [{"ServiceType": "Interface"}],
                }
                for n in ServiceNames
            ],
            "ServiceNames": list(ServiceNames),
        }

    def describe_tags(self, ResourceArns: list[str]) -> dict[str, Any]:
        """`elbv2:DescribeTags` — 페이지네이터가 없고 ARN 20개까지 받는다(넘으면 AWS 가 거절한다 — 그 한도도 잰다)."""
        self.calls.append("describe_tags")
        self.direct_calls.append(
            ("describe_tags", {"ResourceArns": list(ResourceArns)})
        )
        if "describe_tags" in self._errors:
            raise self._errors["describe_tags"]
        if len(ResourceArns) > 20:
            raise client_error("ValidationError", "DescribeTags", "ARN 20개 넘음")
        return {
            "TagDescriptions": [
                {
                    "ResourceArn": a,
                    "Tags": [
                        {"Key": k, "Value": v}
                        for k, v in sorted(self._tg_tags.get(a, {}).items())
                    ],
                }
                for a in ResourceArns
            ]
        }

    def list_web_acls(self, **kwargs: Any) -> dict[str, Any]:
        """WAFv2 `ListWebACLs` — 페이지네이터가 없다 · 두 장으로 쪼개 첫 장에만 `NextMarker` 를 싣는다(수집기가 끝까지 도는지 잰다).

        🔴 `Scope` 없이 부르면 AWS 처럼 거절한다 — 범위를 빼먹은 호출이 「없음」으로 통과하지 않게.
        """
        self.calls.append("list_web_acls")
        self.direct_calls.append(("list_web_acls", dict(kwargs)))
        scope = kwargs.get("Scope")
        if scope not in self._waf:
            raise client_error(
                "WAFInvalidParameterException", "ListWebACLs", f"Scope={scope}"
            )
        err = self._errors.get(f"list_web_acls#{scope}")
        if err is not None:
            raise err
        items = self._waf[scope]
        middle = len(items) // 2
        if kwargs.get("NextMarker") == "page-2":
            return {"WebACLs": items[middle:]}
        if kwargs.get("NextMarker"):
            raise AssertionError(
                f"list_web_acls 에 모르는 표지: {kwargs['NextMarker']}"
            )
        page: dict[str, Any] = {"WebACLs": items[:middle]}
        if items[middle:]:
            page["NextMarker"] = "page-2"
        return page

    def get_web_acl(self, Name: str, Scope: str, Id: str) -> dict[str, Any]:
        self.calls.append("get_web_acl")
        self.direct_calls.append(
            ("get_web_acl", {"Name": Name, "Scope": Scope, "Id": Id})
        )
        err = self._followup_errors.get(("get_web_acl", Id))
        if err is not None:
            raise err
        detail = self._waf_details.get(Id)
        if detail is None:
            raise client_error("WAFNonexistentItemException", "GetWebACL")
        return {"WebACL": detail, "LockToken": f"lock-{Id}"}

    def list_resources_for_web_acl(
        self, WebACLArn: str, ResourceType: str
    ) -> dict[str, Any]:
        """보호 대상 ARN — `ResourceType` 이 API_GATEWAY 일 때만 답을 든다(그 밖을 물으면 AssertionError · 묻지 않기로 한 종류)."""
        self.calls.append("list_resources_for_web_acl")
        self.direct_calls.append(
            (
                "list_resources_for_web_acl",
                {"WebACLArn": WebACLArn, "ResourceType": ResourceType},
            )
        )
        if ResourceType != "API_GATEWAY":
            raise AssertionError(
                f"list_resources_for_web_acl 를 {ResourceType} 로 불렀다"
            )
        if ":global/webacl/" in WebACLArn:
            raise client_error(
                "WAFInvalidParameterException",
                "ListResourcesForWebACL",
                "CloudFront 범위",
            )
        err = self._followup_errors.get(("list_resources_for_web_acl", WebACLArn))
        if err is not None:
            raise err
        return {"ResourceArns": list(self._waf_resources.get(WebACLArn, []))}

    def get_web_acl_for_resource(self, ResourceArn: str) -> dict[str, Any]:
        """ALB 에 붙은 web ACL — 단일 객체이고 페이지네이터가 없다(실측: `can_paginate` False).

        🔴 **안 붙은 자원에는 `WebACL` 키를 주지 않는다** — AWS 가 그렇게 답한다. 가짜가 `{}` 대신
        `{"WebACL": {}}` 를 주면 「붙지 않았다」 경로가 실제와 다른 모양으로 측정된다.
        거부는 `followup_errors[("get_web_acl_for_resource", ARN)]` 로 넣는다(다른 후속 호출과 같다).
        """
        self.calls.append("get_web_acl_for_resource")
        err = self._followup_errors.get(("get_web_acl_for_resource", ResourceArn))
        if err is not None:
            raise err
        acl = self._web_acls.get(ResourceArn)
        return {"WebACL": acl} if acl is not None else {}

    def describe_firewall(self, FirewallArn: str) -> dict[str, Any]:
        # Network Firewall 상세도 단일 객체다(리스트가 아님) → 페이지네이터가 아니라 직접 호출한다.
        # 거부는 followup_errors[("describe_firewall", ARN)] 로 넣는다(EKS·LB 와 같은 방식).
        self.calls.append("describe_firewall")
        err = self._followup_errors.get(("describe_firewall", FirewallArn))
        if err is not None:
            raise err
        detail = self._firewall_details.get(FirewallArn)
        if detail is None:
            raise client_error("ResourceNotFoundException", "DescribeFirewall")
        return detail

    def _detail(
        self, method: str, key: str, table: dict[str, dict[str, Any]], code: str
    ) -> dict[str, Any]:
        # 단일 객체 상세(W2) — 거부는 followup_errors[(메서드, 키)] 로 넣는다(EKS 와 같은 방식).
        self.calls.append(method)
        err = self._followup_errors.get((method, key))
        if err is not None:
            raise err
        detail = table.get(key)
        if detail is None:
            raise client_error(code, method)
        return dict(detail)

    def describe_services(self, cluster: str, services: list[str]) -> dict[str, Any]:
        # 🔴 실제 API 는 한 번에 10개까지만 받는다 — 가짜도 넘으면 거부한다(배치를 안 나누면 터진다).
        self.calls.append("describe_services")
        self.direct_calls.append(
            ("describe_services", {"cluster": cluster, "services": list(services)})
        )
        if len(services) > 10:
            raise client_error("InvalidParameterException", "DescribeServices")
        err = self._followup_errors.get(("describe_services", cluster))
        if err is not None:
            raise err
        found = [self._ecs[a] for a in services if a in self._ecs]
        missing = [
            {"arn": a, "reason": "MISSING"} for a in services if a not in self._ecs
        ]
        return {"services": found, "failures": missing}

    def describe_domains(self, DomainNames: list[str]) -> dict[str, Any]:
        # 🔴 실제 API 는 한 번에 5개까지만 받는다 — 넘으면 거부한다(배치를 안 나누면 터진다).
        # 거부는 `errors={"describe_domains": 예외}`(전부) 또는 `followup_errors[("describe_domains", 첫 이름)]`(그 묶음만).
        self.calls.append("describe_domains")
        self.direct_calls.append(
            ("describe_domains", {"DomainNames": list(DomainNames)})
        )
        if len(DomainNames) > 5:
            raise client_error("ValidationException", "DescribeDomains")
        if "describe_domains" in self._errors:
            raise self._errors["describe_domains"]
        err = self._followup_errors.get(("describe_domains", DomainNames[0]))
        if err is not None:
            raise err
        return {
            "DomainStatusList": [
                dict(self._os[n]) for n in DomainNames if n in self._os
            ]
        }

    def describe_target_health(self, TargetGroupArn: str) -> dict[str, Any]:
        # 페이지네이터·토큰이 없는 API(boto3 elbv2). 거부는 followup_errors[(메서드, TG ARN)].
        self.calls.append("describe_target_health")
        err = self._followup_errors.get(("describe_target_health", TargetGroupArn))
        if err is not None:
            raise err
        return {"TargetHealthDescriptions": self._target_health.get(TargetGroupArn, [])}

    def describe_notebook_instance(self, NotebookInstanceName: str) -> dict[str, Any]:
        return self._detail(
            "describe_notebook_instance",
            NotebookInstanceName,
            SM_NOTEBOOK_DETAILS,
            "ValidationException",
        )

    def describe_domain(self, DomainId: str) -> dict[str, Any]:
        return self._detail(
            "describe_domain", DomainId, SM_DOMAIN_DETAILS, "ResourceNotFound"
        )

    def describe_endpoint(self, EndpointName: str) -> dict[str, Any]:
        return self._detail(
            "describe_endpoint",
            EndpointName,
            SM_ENDPOINT_DETAILS,
            "ValidationException",
        )

    def describe_endpoint_config(self, EndpointConfigName: str) -> dict[str, Any]:
        return self._detail(
            "describe_endpoint_config",
            EndpointConfigName,
            SM_ENDPOINT_CONFIGS,
            "ValidationException",
        )

    def describe_model(self, ModelName: str) -> dict[str, Any]:
        return self._detail(
            "describe_model", ModelName, SM_MODELS, "ValidationException"
        )


# 이름을 바꾸면 기존 테스트가 전부 깨진다 — 별칭으로 남긴다(내용은 15종을 안다).
FakeEc2 = FakeAws


def series_key(
    namespace: str, metric: str, dimensions: dict[str, str]
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    """`FakeCloudWatch` 의 계열 키. 차원은 **이름·값이 정확히** 맞아야 한다."""
    return (namespace, metric, tuple(sorted(dimensions.items())))


class FakeCloudWatch:
    """`GetMetricData` 만 아는 가짜 CloudWatch.

    🔴 **이 가짜의 값은 등록한 차원과 정확히 일치할 때만 나온다.** 이름이 한 글자 달라도 빈
    배열 + `StatusCode: Complete` 가 돌아간다 — ADR 0006 §O7-1 함정 1 의 실측 그대로다(오타 난
    차원 이름·존재하지 않는 값 둘 다 `dp=0`·`Complete`·`Messages` 없음). 가짜가 여기서
    친절하게 에러를 내면 차원 상수를 재는 검사가 **실패할 수 없는 어서션**이 된다(통과가 아니라 미측정).

    `values` 는 **최신 우선**으로 적는다(제품이 `ScanBy=TimestampDescending` 으로 부른다).
    `ScanBy` 를 `TimestampAscending` 으로 부르면 뒤집어 준다 — 「값이 있는 마지막 지점」을
    `Values[0]` 으로 읽는 규칙이 정렬에 기대고 있음을 검사가 잴 수 있어야 한다.

    `Timestamps` 는 실제 응답처럼 **값과 같은 순서의 짝**이다. i 번째 값(최신 우선)은
    `EndTime` 을 `Period` 로 내린 시각에서 (i+1)칸 앞 구간의 **시작**이다(CloudWatch 의 지점 시각 =
    구간 시작 — 실측 2026-09-27 · 멈춤 02:24:23Z 의 마지막 지점이 02:24:00Z). 빈 칸이 없는 연속
    계열만 만든다. `with_timestamps=False` 면 키를 빼서 「시각을 못 받았다」를 만든다.
    """

    def __init__(
        self,
        series: dict[tuple[str, str, tuple[tuple[str, str], ...]], list[float]]
        | None = None,
        *,
        error: Exception | None = None,
        missing_ids: tuple[str, ...] = (),
        with_timestamps: bool = True,
        status_codes: dict[tuple[str, str, tuple[tuple[str, str], ...]], str]
        | None = None,
    ) -> None:
        self._series = dict(series or {})
        self._error = error
        self._with_timestamps = with_timestamps
        # 계열 → 그 쿼리의 `StatusCode`(기본 `Complete`). `Forbidden` 은 호출은 성공했는데 그 계열만 거부된 응답이다
        # (교차 계정 관측 등) — 빈 배열이 오지만 「쟀고 0」이 아니다.
        self._status = dict(status_codes or {})
        # 응답에서 통째로 빠지는 쿼리 id — 쿼리↔결과 대응이 깨진 경우를 만든다.
        self._missing = set(missing_ids)
        self.calls: list[dict[str, Any]] = []

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        descending = kwargs.get("ScanBy", "TimestampDescending") != "TimestampAscending"
        results: list[dict[str, Any]] = []
        for query in kwargs["MetricDataQueries"]:
            if query["Id"] in self._missing:
                continue
            metric = query["MetricStat"]["Metric"]
            key = series_key(
                metric["Namespace"],
                metric["MetricName"],
                {d["Name"]: d["Value"] for d in metric["Dimensions"]},
            )
            values = list(self._series.get(key, []))
            period = int(query["MetricStat"]["Period"])
            end = kwargs["EndTime"]
            floor = end - timedelta(
                seconds=int(end.timestamp()) % period, microseconds=end.microsecond
            )
            stamps = [
                floor - timedelta(seconds=period * (i + 1)) for i in range(len(values))
            ]
            if not descending:
                values.reverse()
                stamps.reverse()
            result: dict[str, Any] = {
                "Id": query["Id"],
                "Label": metric["MetricName"],
                # 🔴 값이 없어도 `Complete` 다(실측). 「모름」과 「흐름 없음」이 여기서 섞인다.
                "StatusCode": self._status.get(key, "Complete"),
                "Values": values,
            }
            if self._with_timestamps:
                result["Timestamps"] = stamps
            results.append(result)
        return {"MetricDataResults": results, "Messages": []}


class FakeMinuteCloudWatch:
    """시각이 있는 1분 점으로 `GetMetricData` 를 흉내 낸다(#27 사용률).

    `FakeCloudWatch` 는 「끝에서부터 빈칸 없는 계열」만 만든다 — 기간 · 단위 · 몰림을 잴 수 없다. 여기서는
    계열마다 `{1분 시작 시각: 값}` 을 받고 묻는 `Period` · `Stat` 으로 **CloudWatch 처럼 접는다**:
    단위 시작 = 시각을 `Period` 로 내린 값 · `Sum` = 합 · `Maximum` = 최대 · `Average` = 평균.
    단위 안에 점이 없으면 그 단위는 응답에 없다(실측: 빈 단위는 0 이 아니라 「없음」).
    `StartTime` 이상 · `EndTime` 미만인 단위만 준다. `page_size` 를 주면 그 수씩 끊고 `NextToken` 을 준다.
    """

    def __init__(
        self,
        series: dict[
            tuple[str, str, tuple[tuple[str, str], ...]], dict[datetime, float]
        ]
        | None = None,
        *,
        error: Exception | None = None,
        page_size: int | None = None,
    ) -> None:
        self._series = {k: dict(v) for k, v in (series or {}).items()}
        self._error = error
        self._page_size = page_size
        self.calls: list[dict[str, Any]] = []

    def _fold(
        self, query: dict[str, Any], start: datetime, end: datetime
    ) -> list[tuple[datetime, float]]:
        stat = query["MetricStat"]
        metric = stat["Metric"]
        key = series_key(
            metric["Namespace"],
            metric["MetricName"],
            {d["Name"]: d["Value"] for d in metric["Dimensions"]},
        )
        period = int(stat["Period"])
        buckets: dict[datetime, list[float]] = {}
        for moment, value in self._series.get(key, {}).items():
            unit = moment - timedelta(seconds=int(moment.timestamp()) % period)
            if start <= unit < end:
                buckets.setdefault(unit, []).append(value)
        fold = {"Sum": sum, "Maximum": max, "Average": lambda v: sum(v) / len(v)}[
            stat["Stat"]
        ]
        return sorted((unit, float(fold(values))) for unit, values in buckets.items())

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        flat: list[tuple[str, datetime, float]] = []
        for query in kwargs["MetricDataQueries"]:
            for unit, value in self._fold(
                query, kwargs["StartTime"], kwargs["EndTime"]
            ):
                flat.append((query["Id"], unit, value))
        offset = int(kwargs.get("NextToken") or 0)
        size = self._page_size or len(flat) or 1
        page = flat[offset : offset + size]
        results: dict[str, dict[str, Any]] = {
            q["Id"]: {
                "Id": q["Id"],
                "StatusCode": "Complete",
                "Values": [],
                "Timestamps": [],
            }
            for q in kwargs["MetricDataQueries"]
        }
        for qid, unit, value in page:
            results[qid]["Values"].append(value)
            results[qid]["Timestamps"].append(unit)
        response: dict[str, Any] = {
            "MetricDataResults": list(results.values()),
            "Messages": [],
        }
        if offset + size < len(flat):
            response["NextToken"] = str(offset + size)
        return response


class FakeServiceQuotas:
    """`GetServiceQuota` · `GetAWSDefaultServiceQuota` 만 아는 가짜.

    `applied` = 계정에 적용된 값(코드 → 값) · `defaults` = AWS 기본값. 없는 코드는 실제처럼
    `NoSuchResourceException` 이다. `applied_error` · `default_error` 를 주면 그 호출이 그 예외를 낸다.
    """

    def __init__(
        self,
        applied: dict[str, float] | None = None,
        defaults: dict[str, float] | None = None,
        *,
        applied_error: Exception | None = None,
        default_error: Exception | None = None,
    ) -> None:
        self._applied = dict(applied or {})
        self._defaults = dict(defaults or {})
        self._applied_error = applied_error
        self._default_error = default_error
        self.calls: list[tuple[str, str, str]] = []

    def _answer(
        self,
        table: dict[str, float],
        error: Exception | None,
        op: str,
        service: str,
        code: str,
    ) -> dict:
        self.calls.append((op, service, code))
        if error is not None:
            raise error
        if code not in table:
            raise client_error("NoSuchResourceException", op)
        return {
            "Quota": {"ServiceCode": service, "QuotaCode": code, "Value": table[code]}
        }

    def get_service_quota(self, ServiceCode: str, QuotaCode: str) -> dict[str, Any]:
        return self._answer(
            self._applied,
            self._applied_error,
            "GetServiceQuota",
            ServiceCode,
            QuotaCode,
        )

    def get_aws_default_service_quota(
        self, ServiceCode: str, QuotaCode: str
    ) -> dict[str, Any]:
        return self._answer(
            self._defaults,
            self._default_error,
            "GetAWSDefaultServiceQuota",
            ServiceCode,
            QuotaCode,
        )


def factory_by_service(
    clients: dict[str, Any], *, fail_services: dict[str, Exception] | None = None
) -> Any:
    """서비스 이름으로 갈라지는 팩토리 — 1층(ec2·elbv2)과 2층(cloudwatch)이 다른 가짜다."""

    def make(account_id: str, region: str, service: str) -> Any:
        if fail_services and service in fail_services:
            raise fail_services[service]
        if service not in clients:
            # 등록하지 않은 서비스를 조용히 받아 주면 「2층을 안 불렀다」가 통과한다.
            raise AssertionError(f"등록하지 않은 서비스를 불렀다: {service}")
        return clients[service]

    return make


def factory(client: Any, *, fail_services: dict[str, Exception] | None = None) -> Any:
    """`collect()` 가 받는 ClientFactory 형태로 감싼다."""

    def make(account_id: str, region: str, service: str) -> Any:
        if fail_services and service in fail_services:
            raise fail_services[service]
        return client

    return make


def factory_for(clients: dict[str, Any]) -> Any:
    """계정마다 다른 응답을 주는 팩토리 — 공유 자원 중복 제거를 재는 데 쓴다."""

    def make(account_id: str, region: str, service: str) -> Any:
        return clients[account_id]

    return make


# 배치 D — Global Accelerator 클라이언트는 수집 리전과 무관하게 us-west-2 로 만든다(`Spec.region`). 리전·쌍으로 갈라 주는 팩토리는 그 리전을
#   모를 수 있다 → 빈 가짜(가속기 없음)를 준다. 🔴 다른 서비스는 그대로 KeyError — 모르는 쌍을 조용히 받아 주면 「안 불렀다」가 통과한다
GA_SERVICE = "globalaccelerator"
# #129(2.11.0) — Cloud WAN(networkmanager)도 us-west-2 고정이다(`Spec.region`) — 같은 처분
PINNED_SERVICES = frozenset({GA_SERVICE, "networkmanager"})


def factory_by_pair(clients: dict[tuple[str, str], Any]) -> Any:
    """(계정, 리전) 쌍마다 다른 응답을 주는 팩토리 — 「한 자원을 두 쌍에서 본다」를 재는 데 쓴다.

    계정 축·리전 축 팩토리로는 TGW 피어링 어태치먼트를 만들 수 없다: 그 어태치먼트는 «계정 A 의
    리전 R1» 과 «계정 B 의 리전 R2» 두 쌍에만 나오고 나머지 쌍(A/R2·B/R1)에는 나오지 않는다.
    한 축만 갈라주면 나오지 않아야 할 쌍에도 나와서, 병합이 실제로 잰 것보다 쉬워진다.
    """

    def make(account_id: str, region: str, service: str) -> Any:
        if service in PINNED_SERVICES and (account_id, region) not in clients:
            return (
                FakeAws.empty()
            )  # 가속기 API 리전(us-west-2)은 이 세계의 쌍이 아니다 — 가속기 없음
        return clients[(account_id, region)]

    return make


def factory_by_region(clients: dict[str, Any]) -> Any:
    """리전마다 다른 응답을 주는 팩토리 — 크로스리전 자원의 정본 리전을 재는 데 쓴다.

    한 클라이언트를 모든 리전에 주면 리전 간 자원(피어링)이 **양쪽에서 보이는** 상황만
    만들어진다. 「한쪽 리전 응답에만 나온다」는 실제 케이스는 이 팩토리로만 잴 수 있다.
    """

    def make(account_id: str, region: str, service: str) -> Any:
        if service in PINNED_SERVICES and region not in clients:
            return (
                FakeAws.empty()
            )  # 가속기 API 리전(us-west-2)은 이 세계의 리전이 아니다 — 가속기 없음
        return clients[region]

    return make


# ---- 흐름 로그 판독기(S5 U6)의 가짜 ---------------------------------------------------------------


class _Forbidden:
    """판독기가 부르면 안 되는 호출 — 읽기 가드(`awsguard.py`)가 실계정에서 막는 것과 같은 자리다.

    `HeadBucket`·`HeadObject` 는 allowlist verb 가 아니다. botocore 가 버킷 리전을 찾으러 조용히 부르는
    경로(틀린 리전 클라이언트 · `download_file`)를 판독기가 밟으면 실계정에서 `ReadOnlyViolation` 으로 죽는다.
    가짜가 그 호출을 받아 주면 그 경로가 fixture 에서 통과한다.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *_: Any, **__: Any) -> Any:
        raise AssertionError(f"판독기가 부르면 안 되는 호출: {self._name}")


class FakeLogs:
    """`DescribeLogGroups`·`DescribeLogStreams`·`GetLogEvents` 만 아는 가짜 CloudWatch Logs.

    `groups[이름] = {"class": "STANDARD", "streams": {스트림: {"last": ms|None, "created": ms,
    "events": [(ms, 메시지), …]}}}`. 이벤트는 `startTime ≤ ts < endTime` 만 준다(CWL 규약). 끝에 닿으면
    받은 `nextToken` 을 그대로 돌려준다 — 판독기가 이 규약으로 멈추는지를 잰다. `errors[메서드]` 는
    그 메서드의 n 번째 호출부터 던질 예외 `(n, 예외)`.
    """

    def __init__(
        self,
        groups: dict[str, dict[str, Any]] | None = None,
        *,
        group_page: int = 50,
        stream_page: int = 50,
        event_page: int = 10_000,
        errors: dict[str, tuple[int, Exception]] | None = None,
    ) -> None:
        self.groups = dict(groups or {})
        self._group_page = group_page
        self._stream_page = stream_page
        self._event_page = event_page
        self._errors = dict(errors or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.filter_log_events = _Forbidden("logs:FilterLogEvents")
        self.start_query = _Forbidden("logs:StartQuery")

    def _enter(self, method: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((method, dict(kwargs)))
        if method in self._errors:
            after, error = self._errors[method]
            if sum(1 for name, _ in self.calls if name == method) >= after:
                raise error

    def describe_log_groups(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("describe_log_groups", kwargs)
        names = sorted(
            n for n in self.groups if n.startswith(kwargs["logGroupNamePrefix"])
        )
        at = int(kwargs.get("nextToken") or 0)
        page = names[at : at + self._group_page]
        response: dict[str, Any] = {
            "logGroups": [
                {
                    "logGroupName": n,
                    "logGroupClass": self.groups[n].get("class", "STANDARD"),
                }
                for n in page
            ]
        }
        if at + self._group_page < len(names):
            response["nextToken"] = str(at + self._group_page)
        return response

    def describe_log_streams(self, **kwargs: Any) -> dict[str, Any]:
        self._enter("describe_log_streams", kwargs)
        assert (
            kwargs.get("orderBy") == "LastEventTime"
            and kwargs.get("descending") is True
        )
        streams = self.groups[kwargs["logGroupName"]].get("streams", {})
        # 최근 이벤트 순. 아직 갱신되지 않은 스트림(last=None)은 맨 뒤(실측 근거 없음 — 판독기는 순서에 기대지 않는다).
        order = sorted(
            streams,
            key=lambda s: (
                streams[s].get("last") is None,
                -(streams[s].get("last") or 0),
                s,
            ),
        )
        at = int(kwargs.get("nextToken") or 0)
        page = order[at : at + self._stream_page]
        items = []
        for name in page:
            item: dict[str, Any] = {
                "logStreamName": name,
                "creationTime": streams[name].get("created", 0),
            }
            if streams[name].get("last") is not None:
                item["lastEventTimestamp"] = streams[name]["last"]
            items.append(item)
        response: dict[str, Any] = {"logStreams": items}
        if at + self._stream_page < len(order):
            response["nextToken"] = str(at + self._stream_page)
        return response

    def get_log_events(self, **kwargs: Any) -> dict[str, Any]:
        """CWL 규약을 흉내 낸다 — 이벤트는 시각 오름차순 · `startFromHead=True` 는 앞에서부터 `nextForwardToken` ·
        `False`(기본) 는 **최근 쪽부터** `nextBackwardToken`. 한 쪽 안은 어느 방향이든 오름차순이다. 끝에 닿으면 받은 토큰을
        그대로 돌려준다(두 토큰 모두 · AWS 문서). 판독기가 어느 방향을 쓰든 같은 가짜가 답한다 — 방향을 되돌리는
        브레이크가 단언이 아니라 결과(버린 기록)로 FAIL 하게."""
        self._enter("get_log_events", kwargs)
        stream = self.groups[kwargs["logGroupName"]]["streams"][kwargs["logStreamName"]]
        events = sorted(
            (
                {"timestamp": ts, "message": message}
                for ts, message in stream.get("events", [])
                if kwargs["startTime"] <= ts < kwargs["endTime"]
            ),
            key=lambda e: e["timestamp"],
        )
        token = kwargs.get("nextToken")
        n = self._event_page
        if token:
            kind, at = token.split("/")[0], int(token.split("/")[1])
        else:
            kind = "f" if kwargs.get("startFromHead", False) else "b"
            at = 0 if kind == "f" else len(events)
        if kind == "f":
            page = events[at : at + n]
            return {
                "events": page,
                "nextForwardToken": f"f/{at + len(page)}",
                "nextBackwardToken": f"b/{at}",
            }
        low = max(0, at - n)
        return {
            "events": events[low:at],
            "nextForwardToken": f"f/{at}",
            "nextBackwardToken": f"b/{low}",
        }


class _Body:
    def __init__(self, data: bytes, chunk: int | None = None) -> None:
        self._data = data
        self._at = 0
        self._chunk = chunk
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self._chunk is not None:
            size = self._chunk if size < 0 else min(size, self._chunk)
        end = len(self._data) if size < 0 else self._at + size
        out = self._data[self._at : end]
        self._at += len(out)
        return out

    def close(self) -> None:
        self.closed = True


class FakeS3:
    """버킷별 리전을 아는 가짜 S3. `buckets[이름] = {"region": "…", "objects": {키: 바이트}}`.

    🔴 **버킷 리전이 아닌 리전의 클라이언트로 목록·객체를 부르면 터진다.** 실계정에서는 botocore 가 그 리전을
    `HeadBucket` 으로 찾으러 가고 읽기 가드가 그 호출을 막는다(가드 allowlist 밖). `GetBucketLocation` 은
    어느 리전에서 불러도 답한다(실계정 동작 — U6 실계정 확인 항목).
    """

    def __init__(
        self,
        buckets: dict[str, dict[str, Any]] | None = None,
        *,
        list_page: int = 1000,
        chunk: int | None = None,
        errors: dict[str, tuple[int, Exception]] | None = None,
    ) -> None:
        self.buckets = dict(buckets or {})
        self._list_page = list_page
        self._chunk = chunk
        self._errors = dict(errors or {})
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.bodies: list[_Body] = []

    def client(self, region: str) -> _S3View:
        return _S3View(self, region)

    def enter(self, method: str, region: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((method, region, dict(kwargs)))
        if method in self._errors:
            after, error = self._errors[method]
            if sum(1 for name, _, _ in self.calls if name == method) >= after:
                raise error


class _S3View:
    def __init__(self, s3: FakeS3, region: str) -> None:
        self._s3 = s3
        self.region = region
        self.head_bucket = _Forbidden("s3:HeadBucket")
        self.head_object = _Forbidden("s3:HeadObject")
        self.download_file = _Forbidden("s3:download_file")

    def _bucket(self, name: str, *, located: bool = True) -> dict[str, Any]:
        if name not in self._s3.buckets:
            raise client_error("NoSuchBucket", "GetObject")
        bucket = self._s3.buckets[name]
        if located and bucket["region"] != self.region:
            raise AssertionError(
                f"버킷 리전({bucket['region']})이 아닌 {self.region} 클라이언트 — 실계정이면 HeadBucket 으로 간다"
            )
        return bucket

    def get_bucket_location(self, **kwargs: Any) -> dict[str, Any]:
        self._s3.enter("get_bucket_location", self.region, kwargs)
        region = self._bucket(kwargs["Bucket"], located=False)["region"]
        constraint = {"us-east-1": None, "eu-west-1": "EU"}.get(region, region)
        return {"LocationConstraint": constraint}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self._s3.enter("list_objects_v2", self.region, kwargs)
        bucket = self._bucket(kwargs["Bucket"])
        keys = sorted(
            k for k in bucket["objects"] if k.startswith(kwargs.get("Prefix", ""))
        )
        token = kwargs.get("ContinuationToken")
        if token:
            keys = [k for k in keys if k > token]
        elif kwargs.get("StartAfter"):
            keys = [k for k in keys if k > kwargs["StartAfter"]]
        page = keys[: self._s3._list_page]
        response: dict[str, Any] = {
            "Contents": [{"Key": k, "Size": len(bucket["objects"][k])} for k in page],
            "IsTruncated": len(keys) > len(page),
        }
        if response["IsTruncated"]:
            response["NextContinuationToken"] = page[-1]
        return response

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._s3.enter("get_object", self.region, kwargs)
        bucket = self._bucket(kwargs["Bucket"])
        if kwargs["Key"] not in bucket["objects"]:
            raise client_error("NoSuchKey", "GetObject")
        body = _Body(bucket["objects"][kwargs["Key"]], self._s3._chunk)
        self._s3.bodies.append(body)
        return {"Body": body}


def gzip_log(lines: list[str], *, members: int = 1) -> bytes:
    """흐름 로그 텍스트 객체(머리줄 포함)를 gzip 으로 — `members>1` 이면 여러 멤버를 이어 붙인다."""
    import gzip

    text = "".join(f"{line}\n" for line in lines).encode("utf-8")
    if members == 1:
        return gzip.compress(text, mtime=0)
    size = max(1, len(text) // members)
    parts = [text[i : i + size] for i in range(0, len(text), size)]
    return b"".join(gzip.compress(part, mtime=0) for part in parts)
