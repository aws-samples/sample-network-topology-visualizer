"""Fake clients that let the collection path be measured without AWS.

A real account cannot measure determinism or partial failure reproducibly,
because permissions and resources differ from person to person and failures
cannot be triggered at will. Those properties are measured here instead. A
single run against a real account is a separate gate.

Account ids come only from the AWS example range (`111122223333`), which a
pre-commit hook enforces.

The fixtures deliberately carry awkward cases. A fixture with nothing but the
happy path leaves assertions passing while measuring nothing, so this one holds
two routes to the same target (which must aggregate), a blackhole route and a
target with no node (which must stay unresolved), a detached IGW, a NAT
gateway, attachment, peering and virtual interface that are failed or being
deleted (dead states, where the node stays and the edge does not), a
self-referencing security group, a VPC owned by an account outside the scope,
a gateway endpoint next to an interface endpoint, and a failed load balancer.

Gone states such as `deleted` and `terminated` are kept out of the default
fixtures. Collection drops the whole node for those (see `GONE_STATES` in
`services/base.py`), so putting them here would contradict the node count in
`RESOURCE_NODE_COUNT`. `test_gone_states.py` measures that path separately for
each resource type.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

ACCOUNT = "111122223333"
# The account name from `account:GetAccountInformation`. This value is made up:
# never copy a real account name, which can look like an email address, into a
# fixture.
ACCOUNT_NAME = "network-hub"
# Another account inside the scope, the owner of a shared resource, and an
# account outside the scope, which must not be attributed.
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

# Deliberately not in ascending id order, so that the sorting has to come from
# the code.
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
            # Two destinations to the same TGW, which must produce one edge.
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
            # A target with no node (an ENI) and a blackhole route, both of
            # which must stay in unresolved_refs.
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
            # A shape taken from real data: AWS puts a pseudo gateway name,
            # which does not follow the `xxx-` form, in GatewayId. Two
            # link-local destinations group under that one name and become a
            # single unresolved reference. Do not filter these out by id shape.
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
    # A detached IGW: the node stays, the edge does not.
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
    # A failed NAT gateway, which could not be created because its subnet was
    # not found (`InvalidSubnetID.NotFound`). With no subnet information the
    # parent candidate falls back to the VPC. `failed` is a state in which the
    # resource is still present in the response, so it stays as a node, unlike
    # `deleted`, which collection drops.
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

# The owner is a different account. When that account is inside the scope, the
# resource is attributed to it and the nodes merge into one.
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
    # A VPN attachment, whose target is the VPN connection node. Before 2.6.0
    # there was no such node and the edge only had a TGW side.
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
    # Being deleted: the node stays and there are no edges.
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
    # A Direct Connect gateway attachment, which has had a target node since
    # 2.1.0. The DXGW and the TGW are drawn with this single edge and no other.
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
    # A failed peering. Drawing an edge for it would show a path that does not
    # exist, while `deleted` has its whole node dropped.
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
    # Alive, but the peer VPC is outside the scope, so only one side gets an
    # edge and the other lands in unresolved_refs.
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
            # A self reference, which is an attribute rather than an edge.
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
                    # A security group with no node, owned by another
                    # account, so it lands in unresolved_refs.
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

# The web ACL of an ALB, read with one extra `wafv2:GetWebACLForResource` call
# per load balancer, which is a cross-service follow-up call.
# All three cases are built here: an ALB that has one attached, an ALB that does
# not, where the response carries no `WebACL` key at all, and an NLB, which must
# never be asked.
# The ARNs follow the `wafv2` REGIONAL scope form. The CloudFront ones use
# `global/` and are fixed to us-east-1.
APP_WEB_ACL_ARN = (
    f"arn:aws:wafv2:{REGION}:{ACCOUNT}:regional/webacl/prod-edge-acl/abc12345"
)

# ResourceArn to web ACL object. A missing key means nothing is attached, which
# is how AWS answers: it omits the `WebACL` key entirely.
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
    # A failed load balancer: the node stays and it gets no placement edge.
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

# ------------------------------------------------- CloudFront (second global)
# The response shape differs from every other resource type: the items are
#    nested in `DistributionList.Items`, and the `{Quantity, Items}` wrapper
#    appears once more inside Aliases and Origins. An implementation that
#    assumes flat keys silently finds nothing here, and the screen then shows
#    that there are no distributions.
# `Status`, `LastModifiedTime` and `ETag` are included on purpose, to measure
#    that the collector carries neither state nor wall-clock time. If any of
#    these values reach the output, that is a failure.
# One origin uses the real DNS name of the fixture ALB, so that the DNS index
#    makes the `origin` edge meet it. The S3 origin has no load balancer shape,
#    so it produces neither an edge nor an unresolved reference.
CLOUDFRONT_DISTRIBUTION = "E1AAAAAAAAAAAA"
CLOUDFRONT_DISABLED = "E2BBBBBBBBBBBB"
CLOUDFRONT_STATE_LEAKS = ("Deployed", "InProgress", "2026-09-30T00:00:00Z", "ETAG12345")

CLOUDFRONT_DISTRIBUTIONS: list[dict[str, Any]] = [
    # Deliberately in reverse id order, so that the sorting has to come from
    # the code.
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
        # A distribution with nothing attached answers with an empty string,
        # so this measures whether that folds to None.
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
        # The alternate domain names and origins arrive out of ascending
        # order, so the sorting is measured.
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

# ------------------------------------------------------------- Direct Connect
# The response keys are lower camelCase. The secret fields (`authKey`,
# `macSecKeys`, the peer IPs and `customerRouterConfig`) are included on
# purpose, to measure that the collector discards them. If any of these values
# reach the output, that is a failure.
DX_CONNECTION = "dxcon-fg0aaa1111"
DX_CONNECTION_DOWN = "dxcon-fg0bbb2222"
# The parent connection of a hosted VIF. It is owned by another account, the
# partner, so it is absent from `DescribeConnections`.
DX_HOSTED_PARENT = "dxcon-fg0ccc3333"
DX_PRIVATE_VIF = "dxvif-fg0aaa1111"
DX_HOSTED_VIF = "dxvif-fg0bbb2222"
DX_PUBLIC_VIF = "dxvif-fg0ccc3333"
DX_DELETING_VIF = "dxvif-fg0ddd4444"
DXGW = "0a0b0c0d-0aaa-1111-bbbb-0000aaaa1111"  # for the VGW; the private VIF attaches to it
DXGW_TGW = "0f0e0d0c-0aaa-1111-bbbb-0000bbbb2222"  # for the TGW; the TGW attachment points at it
DX_SECRETS = (
    "s3cret-bgp-auth-key",
    "s3cret-macsec-ckn",
    "arn:aws:secretsmanager:secret",
    "router-config-xml",
)
# The BGP peer addresses, on the Amazon side and the customer side. These are
# not secret: the console shows them in the VIF details, so we carry them too.
# The authentication key and the router configuration remain secret.
DX_PEER_ADDRESSES = (
    "169.254.255.1/30",
    "169.254.255.2/30",
)

DIRECT_CONNECT_CONNECTIONS: list[dict[str, Any]] = [
    # Deliberately in reverse id order, so that the sorting has to come from
    # the code.
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
    # A private VIF to the DXGW. The ASN is four bytes, so `asn` is 0 and
    # `asnLong` holds the real value.
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
    # A hosted VIF whose parent connection is not in the list, because the
    # partner owns it. It is not yet confirmed, and its parent falls back to
    # the Region.
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
    # A public VIF. Both gateway fields being empty is the normal case here.
    # It carries the advertised prefixes.
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
    # A VIF being deleted: the node stays and it gets no edge to the DXGW.
    # Once deletion finishes, `deleted` lingers for about fifteen minutes, and
    # collection drops that whole node.
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

# The associations of each DXGW, read with a follow-up call: a VGW association
# and a disassociated one, which is not a relation, plus a TGW association in
# another account and Region.
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

# A method with no boto3 paginator. The fake answers `can_paginate` with False
#    as well, and hands back `nextToken` across two pages. An implementation
#    that reads only the first page comes up short here.
# WAFv2 `ListWebACLs` has no paginator either, and uses `NextMarker` (2.12.0).
UNPAGINATED = frozenset(
    {"describe_connections", "describe_virtual_interfaces", "list_web_acls"}
)
# `describe_instances` wraps the instances in Reservations, so the result_key is
# `Reservations` and `ec2_instance.enrich` flattens a reservation into
# instances. Each reservation holds exactly one instance here, because
# RESOURCE_NODE_COUNT counts reservations, that is list entries, and several
# instances per reservation would quietly break the node-count assertion in
# test_cli. The path that flattens several per reservation is measured in
# test_services, which calls enrich directly. The ids and security groups are
# deliberately out of ascending order, so the sorting has to come from the code.
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

# An ASG does not live in a single subnet: it spans several through
# VPCZoneIdentifier, and the response carries no VpcId.
# `Instances`, `HealthStatus` and `LifecycleState` are included on purpose, to
# measure that the collector does not carry health.
# The subnets and AZs arrive out of ascending order, so the sorting has to come
# from the code (PRIVATE=0bbb, PUBLIC=0aaa).
#
# The name of the second group needs encoding. It is here so that the fixture
#    actually reaches the `quote` line in `_id_segment()`; while `app-asg` was
#    the only group, that line never ran.
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
    # A name that reaches the encoding path. While `app-asg` was the only
    #    group, the line where `_id_segment()` calls `quote` never ran from a
    #    fixture, which left it unmeasured.
    #    One name covers all three cases: `//`, an empty segment, which
    #    violates the schema if `safe="/"` is restored; a space; and `|`,
    #    which breaks the uniqueness of `edge_id` parsing if it survives.
    #    AWS does allow this name.
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

# For EKS, list_clusters returns a list of name strings rather than dicts, so
# describe_cluster fills in the details. That returns a single object,
# `{"cluster": {...}}`, not a list, so pages() cannot read it. status and health
# are included on purpose, to measure that the collector does not carry state.
# The subnets and security groups arrive out of ascending order, so the sorting
# is measured.
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
        # State values the collector must not carry: status and health.issues
        # change from moment to moment.
        "status": "ACTIVE",
        "health": {"issues": []},
    }
}

# Network Firewall is list-then-describe as well: list_firewalls returns only
# name and ARN dicts, and describe_firewall fills in the details. That returns a
# single object, {"Firewall": ..., "FirewallStatus": ...}, so pages() cannot read
# it. Inside FirewallStatus, SyncStates is a map keyed by AZ rather than an
# array, and both the Status inside it and the Status above it change from
# moment to moment. The collector must carry only EndpointId, which is
# structure and the vpce-* join key for route tables, and not Status.
# SubnetMapping and SyncStates arrive out of ascending order, so the sorting has
# to come from the code.
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
            # State values the collector must not carry: Status and the
            # per-AZ Status change from moment to moment.
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

# Follow-up calls, which answer differently per parent ARN. The listeners and
# target groups of ELB are the reference case.
# Do not mix these into `_DATASETS`: RESOURCE_NODE_COUNT would count the
# listeners too and the node-count assertion would quietly become false.
# A listener is an attribute of a load balancer, not a node.
LISTENERS: dict[str, list[dict[str, Any]]] = {
    # The ports are deliberately in reverse order with 8080 mixed in: under a
    # string sort, "HTTP:8080" comes before "HTTP:80".
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
    # A load balancer with no listeners. This is the control case that
    # separates none, an empty array, from could not read, a missing key.
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

# Target health, split by target group ARN. By default green has two healthy
# targets and blue has one healthy plus one still registering, since `initial`
# is not unhealthy. Structure collection takes only the count, the number of
# registered targets, while the state tier reads the health from the same
# response.
# Since 2.7.0 the target identities are used as well: `app-1` (i-…3333) is
# registered in two groups, which produces one edge with two registrations on
# ports 80 and 8080; `bastion` (i-…2222) appears once; and i-…0004 is an
# instance that was not collected, which produces no edge and lands in
# `unresolved_targets`.
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

# ---- Seven more resource types ---------------------------------------------
# Every entry deliberately carries state values the collector must not pick up.
#    If it does, test_services catches it. The ids, subnets and security groups
#    arrive out of ascending order, so the sorting has to come from the code.

# RDS: the response carries no subnet. When exactly one subnet group candidate
# matches the AZ, that one is the parent.
# PRIVATE_SUBNET is in us-east-1b and PUBLIC_SUBNET in us-east-1a (see SUBNETS).
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

# ElastiCache (2.9.0): one replication group, with a primary and a replica
# cluster and one subnet per AZ, so each sits in its own subnet, plus one
# multi-AZ Memcached cluster, whose two nodes span two subnets, so it sits on
# the VPC with `subnet_ids`. The state values `CacheClusterStatus` and
# `CurrentRole` are included on purpose: if they leak into the structure
# document, `test_new_collectors.py` must fail.
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


# Deliberately not in ascending id order, so the sorting is measured.
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

# Aurora and RDS DB clusters (2.9.0): an Aurora cluster with no members, a
#   shape seen in a real account, where the cluster exists without instances.
#   In this response `DBSubnetGroup` is a name string, unlike the instance
#   response, so the VPC comes from `DescribeDBSubnetGroups`.
#   The state value `Status` and the member role `IsClusterWriter` are included
#   on purpose: if they leak into the structure document, the test must fail.
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

# OpenSearch (2.9.0): one VPC domain, zone-aware across two subnets, and one
#   public domain, which has no VPCOptions and therefore sits outside the
#   customer VPC. The list carries names only and `describe_domains` fills in
#   the details, five at a time. The state booleans `Processing` and
#   `UpgradeProcessing` are included on purpose.
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

# MSK (2.9.0): one provisioned cluster, with two brokers in two client subnets,
#   and one serverless cluster with a single VPC configuration. The response
#   carries no VPC, so the parent is the Region. The state value `State` is
#   included on purpose. Broker ENIs come from `ListNodes`, for provisioned
#   clusters only; the serverless one is never asked.
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

# EC2 Instance Connect endpoints live in a single subnet, on the same parent
# chain as an EC2 instance.
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

# Site-to-Site VPN (2.6.0): one VGW, attached to the app VPC, one CGW, and two
# connections, one terminating on the TGW and one on the VGW.
# The id of the TGW-terminated connection equals the `ResourceId` of the TGW
# attachment above, where `ResourceType=vpn`, so that edge resolves to this node.
VGW = "vgw-0aaa1111bbbb4444"
CGW = "cgw-0aaa1111bbbb5555"
VPN_TGW = "vpn-0aaa1111bbbb3333"
VPN_VGW = "vpn-0bbb2222cccc4444"
# Secret markers: the pre-shared key and the device configuration XML, which
# arrive in the response as plain text. Neither may appear anywhere in a
# snapshot (see test_vpn).
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
    # A detached VGW: the node stays, with the Region as its parent, and it
    # gets no VPC edge.
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
# Lambda functions (2.8.0): one attached to the VPC, in a single subnet, and
#    one not attached. `LastModified` and `CodeSha256` change with every
#    deployment, so the collector must not carry them; they are included on
#    purpose.
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
        # State, which the structure document does not carry; only the state
        # tier probes it. Just one tunnel is UP.
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

# TGW route tables. The associations, propagations and routes come from
# follow-up calls that split by table id (see `_FOLLOWUPS`).
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
        # A disassociated association. Carrying it would make "this VPC uses
        # this table" false.
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
        # A disabled propagation, which tells us nothing about the path.
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

# ECS: the list holds cluster ARN strings, and the nodes are the services
# inside them, which `ecs_service.enrich` flattens out.
# Each cluster holds exactly one service here, because RESOURCE_NODE_COUNT
# counts list entries, that is clusters, and several would quietly break the
# node-count assertion; this is the same rule as for EC2 reservations. The
# cases with several services and with batches of ten are measured in
# test_services.
# `list_clusters` has the same method name as the EKS one. The fake imitates
#    both services from a single object, so `get_paginator("list_clusters")`
#    carries both keys, `clusters` and `clusterArns`, on one page.
#    Each collector reads only its own `result_key` (see `base.dig`).
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
        # The target group this service holds belongs to no load balancer in
        #    this world; it is the group of a load balancer in another account
        #    or outside the collection scope. An ECS service that holds a group
        #    claims every registration in it (see `lb_targets._Index.claimer`).
        #    If it held `blue`, which has instance targets, instance
        #    registrations that Fargate cannot own would become edges of this
        #    service, a shape that does not exist in AWS. The load balancer to
        #    ECS edges are measured in `lbt_world.py`.
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

# Three SageMaker resource types. The list carries no network information; the
# details, read with a direct call, do.
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
        # Two models, whose subnets and security groups are merged. They are
        # in reverse order, so the sorting is measured.
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

# (kwarg name, describe method, response key, parent kwarg, default fixture)
# Route 53 (2.8.0): one public zone, whose aliases are www to app-alb, cdn to
#   CloudFront, and one alias in a form we do not recognize, plus one private
#   zone on the app VPC.
#   Record values such as IPs and CNAMEs are sensitive, so the collector must
#   not carry them; an ordinary A record value is included on purpose.
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

# Schema 2.10.0: managed prefix lists from `DescribeManagedPrefixLists`, which
#   are not nodes and so live in `_REGIONAL`, plus the entries of
#   customer-managed lists from `GetManagedPrefixListEntries`, which split by
#   `PrefixListId` and so live in `_FOLLOWUPS`. In the default world the app
#   public table reaches a gateway endpoint through `pl-0aaa1111bbbb2222`, the
#   S3 list; being AWS-managed, its entries are never asked for. No table uses
#   the customer-managed `corp-onprem`, which measures whether the entries of
#   an unused list are left alone.
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

# Two Elastic IPs (2.10.0): one associated with the bastion instance and one
# associated with nothing, which appears in the warnings list.
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

# The routing values the console shows (2.13.0).
#   The DHCP options set, read once per pair with `DescribeDhcpOptions`. Both
#   VPCs use the same set, which is the shape of the default set.
DHCP_OPTIONS_ID = "dopt-0aaa1111bbbb2222"
DHCP_OPTIONS: list[dict[str, Any]] = [
    {
        "DhcpOptionsId": DHCP_OPTIONS_ID,
        "OwnerId": ACCOUNT,
        # The keys are deliberately reversed, so the sorting has to come from
        # the code.
        "DhcpConfigurations": [
            {"Key": "domain-name-servers", "Values": [{"Value": "AmazonProvidedDNS"}]},
            {"Key": "domain-name", "Values": [{"Value": "ec2.internal"}]},
        ],
        "Tags": [],
    }
]
# VPC id to the two values from `DescribeVpcAttribute`, which the list response
# does not carry, read twice per VPC. The egress VPC has DNS hostnames turned
# off, which is the console default.
VPC_DNS: dict[str, dict[str, bool]] = {
    APP_VPC: {"enableDnsSupport": True, "enableDnsHostnames": True},
    EGRESS_VPC: {"enableDnsSupport": True, "enableDnsHostnames": False},
}
# The VPC attachment details, read once per pair with
# `DescribeTransitGatewayVpcAttachments`. The subnets are deliberately reversed.
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
# TGW route table to its prefix list references, from
# `GetTransitGatewayPrefixListReferences`, which splits by table id.
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
    # The console's prefix list references tab (2.13.0).
    (
        "tgw_rt_prefix_references",
        "get_transit_gateway_prefix_list_references",
        "TransitGatewayPrefixListReferences",
        "TransitGatewayRouteTableId",
        TGW_RT_PREFIX_REFERENCES,
    ),
    # The brokers of the provisioned MSK cluster, by ENI id (2.9.0).
    ("msk_nodes", "list_nodes", "NodeInfoList", "ClusterArn", MSK_NODES),
    # The entries of a customer-managed prefix list (2.10.0).
    (
        "prefix_list_entries",
        "get_managed_prefix_list_entries",
        "Entries",
        "PrefixListId",
        PREFIX_LIST_ENTRIES,
    ),
    # The resources of a REST API (embed=methods), the integrations of an HTTP
    # API, RDS proxy targets, and Global Accelerator listeners and endpoint
    # groups, none of which the default world holds.
    ("rest_resources", "get_resources", "items", "restApiId", {}),
    ("v2_integrations", "get_integrations", "Items", "ApiId", {}),
    ("proxy_targets", "describe_db_proxy_targets", "Targets", "DBProxyName", {}),
    ("ga_listeners", "list_listeners", "Listeners", "AcceleratorArn", {}),
    ("ga_endpoint_groups", "list_endpoint_groups", "EndpointGroups", "ListenerArn", {}),
    # Cloud WAN attachments, the VPC and service associations of a Lattice
    # service network, and the registered targets of a target group, none of
    # which the default world holds.
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

# Arguments a follow-up call must receive, because the real API rejects the
# call without them.
# `search_transit_gateway_routes` requires `Filters`; without it the API answers
#    InvalidParameterValue. If the fake accepted the call anyway, the failure
#    would only surface against a real account.
_REQUIRED_KWARGS: dict[str, str] = {"search_transit_gateway_routes": "Filters"}

# (kwarg name, describe method, response key, default fixture)
# For EKS the list holds name strings, so the last position is a list[str]
#    rather than the list[dict] every other type uses. The item type is
#    therefore kept wide. RESOURCE_NODE_COUNT only takes len(), so this makes
#    no difference to it.
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
        # A key containing a dot. `_Pages` reads that notation and builds a
        # genuinely nested page.
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
    # `#ecs` is appended in the method position because the name collides with
    # the EKS one, and `get_paginator` merges the two keys.
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
    # Services that use an IP inside the VPC (2.9.0).
    (
        "elasticache_clusters",
        "describe_cache_clusters",
        "CacheClusters",
        ELASTICACHE_CLUSTERS,
    ),
    ("rds_clusters", "describe_db_clusters", "DBClusters", RDS_DB_CLUSTERS),
    ("opensearch_domains", "list_domain_names", "DomainNames", OPENSEARCH_DOMAIN_NAMES),
    ("msk_clusters", "list_clusters_v2", "ClusterInfoList", MSK_CLUSTERS),
    # Elastic IPs, two by default, REST and HTTP API Gateway, RDS proxies, and
    # Global Accelerator (2.10.0). The default world holds no accelerator;
    # `rel_world.py` fills that in.
    ("elastic_ips", "describe_addresses", "Addresses", ELASTIC_IPS),
    ("rest_apis", "get_rest_apis", "items", []),
    ("http_apis", "get_apis", "Items", []),
    ("db_proxies", "describe_db_proxies", "DBProxies", []),
    ("accelerators", "list_accelerators", "Accelerators", []),
    # Cloud WAN core networks and VPC Lattice service networks and services
    # (2.11.0). The default world holds none; `rel_world.py` fills them in.
    # `#lattice` marks a name collision with the ECS follow-up call
    # `list_services(cluster=…)`. Called without `cluster`, it returns this
    # list instead (see `_ByCluster`).
    ("core_networks", "list_core_networks", "CoreNetworks", []),
    ("service_networks", "list_service_networks", "items", []),
    ("lattice_services", "list_services#lattice", "items", []),
)

# Region-wide follow-up lists that never become nodes, such as subnet groups
# and replication groups, read in one go through a paginator. They sit outside
# `_DATASETS`, so `RESOURCE_NODE_COUNT` does not count them. A denial is
# injected with `errors={method: exception}`, the same way as for a list call.
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
    # Every managed prefix list this account can see, both AWS-managed and
    # customer-managed (2.10.0).
    (
        "managed_prefix_lists",
        "describe_managed_prefix_lists",
        "PrefixLists",
        MANAGED_PREFIX_LISTS,
    ),
    # TGW Connect attachments, their GRE peers, read once per pair, and Lattice
    # target groups, also once per pair (2.11.0). The default world holds no
    # GRE peers.
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
    # The DHCP options set and the VPC attachment details, read once per pair
    # (2.13.0).
    ("dhcp_options", "describe_dhcp_options", "DhcpOptions", DHCP_OPTIONS),
    (
        "tgw_vpc_attachments",
        "describe_transit_gateway_vpc_attachments",
        "TransitGatewayVpcAttachments",
        TGW_VPC_ATTACHMENTS,
    ),
)

# The number of resource nodes one account in one Region must produce, with the
# two container nodes (account and region) left out.
RESOURCE_NODE_COUNT = sum(len(items) for _, _, _, items in _DATASETS)


def client_error(
    code: str, operation: str, message: str = "재현용 오류"
) -> ClientError:
    """Build a ClientError for the fakes to raise.

    The default message stays in Korean on purpose. It reaches the reason text
    of a denied read, and `fixtures/relations.json` carries that text; a test
    compares the fixture byte for byte, and the renderer harness reads it.
    Translating this string would mean regenerating that fixture.
    """
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def _nest(key: str, items: list[Any]) -> dict[str, Any]:
    """Build a genuinely nested page, following a path split on dots.

    Flattening it here would leave the nesting support in `base.dig()`
    unmeasured. When the fake hands back a shape the real API does not use, the
    checks end up measuring the fake's own grammar.
    CloudFront `list_distributions` answers with
    `{"DistributionList": {"Items": [...]}}`.
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
        # Split into two pages, so that a missing merge breaks the count
        # assertion.
        middle = len(self._items) // 2
        return [
            _nest(self._key, self._items[:middle]),
            _nest(self._key, self._items[middle:]),
        ]


class _MergedPages:
    """Used when one fake imitates two services that share a method name, as
    EKS and ECS do with `list_clusters`.

    Every page carries both keys at once. A collector reads only its own
    `result_key`, so it never sees the other one's items.
    A real response carries just one key, so this merge cannot catch a defect
       where a collector reads the other key. `test_services` measures that
       instead, through the result count per service.
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
    """A response that splits by parent ARN, for follow-up calls. It can hand
    back a different result, and a different failure, per load balancer.
    """

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
            # Sweeping everything would make the per-load-balancer attribution
            # false, so this does not answer quietly.
            raise AssertionError(f"{self._method} was called without {self._kwarg}")
        required = _REQUIRED_KWARGS.get(self._method)
        if required is not None and not kwargs.get(required):
            raise client_error("InvalidParameterValue", self._method)
        if parent in self._errors:
            raise self._errors[parent]
        # A parent that was never registered has none. Real AWS also answers
        # with an empty list for a load balancer with no listeners.
        return [{self._key: self._data.get(parent, [])}]


class _FilteredPages:
    """A page that applies `Filters` the way the real API does, for
    `describe_network_interfaces`.

    If the fake ignored the filters, a narrowing down to the ENIs of another
    VPC would be invisible to the tests.
    """

    _FIELDS = {
        "requester-id": "RequesterId",
        "vpc-id": "VpcId",
        "availability-zone": "AvailabilityZone",
        # Ask about the `eni-` target of a route by its id (2.8.0).
        "network-interface-id": "NetworkInterfaceId",
    }

    def __init__(self, key: str, items: list[dict[str, Any]]) -> None:
        self._key, self._items = key, items

    @staticmethod
    def _addresses(eni: dict[str, Any]) -> set[str]:
        # `addresses.private-ip-address` covers the primary IP and every
        # secondary one, exactly as the AWS filter is defined. An EKS pod IP is
        # a secondary IP.
        return {str(eni.get("PrivateIpAddress") or "")} | {
            str(p.get("PrivateIpAddress") or "")
            for p in eni.get("PrivateIpAddresses") or []
        }

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        filters = kwargs.get("Filters")
        if not filters:  # sweeping every ENI in the account is slow in a large
            # account and leaves nothing to attribute them to
            raise AssertionError(
                "describe_network_interfaces was called without Filters"
            )

        def hit(e: dict[str, Any], f: dict[str, Any]) -> bool:
            if f["Name"] == "addresses.private-ip-address":
                return bool(self._addresses(e) & set(f["Values"]))
            return e.get(self._FIELDS[f["Name"]]) in f["Values"]

        found = [e for e in self._items if all(hit(e, f) for f in filters)]
        return _Pages(self._key, found).paginate()


class _ByCluster:
    """`list_services`, which ECS calls with `cluster=…` and VPC Lattice calls
    with no arguments. The arguments tell the two fakes apart.
    """

    def __init__(self, ecs: Any, lattice: Any) -> None:
        self._ecs, self._lattice = ecs, lattice

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        return (self._ecs if "cluster" in kwargs else self._lattice).paginate(**kwargs)


class FakeAws:
    """A fake client that knows the `describe_*` calls of the first twelve
    resource types and the three Direct Connect ones. It stands in for ec2,
    elbv2 and directconnect alike.
    """

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
        if unknown:  # a misspelled kwarg that is ignored quietly turns into a
            # pass against the default fixture
            raise TypeError(f"unknown dataset: {sorted(unknown)}")
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
        # ResourceArn to web ACL. Passing `{}` measures the case where nothing
        # is attached to any ALB; by default one ALB has one attached.
        self._web_acls = WEB_ACLS if web_acls is None else web_acls
        # (method, parent ARN) to exception, used to build the case where only
        # one load balancer's follow-up call is denied.
        self._followup_errors = followup_errors or {}
        # This response never becomes a node, so it sits outside `_DATASETS`
        # and the node-count assertion does not count it.
        self._enis = list(network_interfaces or [])
        self._target_health = (
            TARGET_HEALTH if target_health is None else dict(target_health)
        )
        # Service ARN to a `DescribeServices` entry (2.7.0), used to swap in
        # the ECS service that holds a target group.
        self._ecs = ECS_SERVICES if ecs_details is None else dict(ecs_details)
        # The endpoint service configurations this account provides, sitting
        #   behind a GWLB or an NLB, from
        #   `DescribeVpcEndpointServiceConfigurations` (2.8.0). The response
        #   never becomes a node, so it sits outside `_DATASETS`. There are
        #   none by default, because no load balancer in the default world sits
        #   behind an endpoint service.
        self._endpoint_services = list(endpoint_services or [])
        # The Network Firewall details from `describe_firewall`, which default
        # to the module's `NETWORK_FIREWALL_DETAILS`; other worlds swap them.
        self._firewall_details = (
            NETWORK_FIREWALL_DETAILS
            if firewall_details is None
            else dict(firewall_details)
        )
        # The consumer-side endpoint service name to its owner account, from
        #   `DescribeVpcEndpointServices` (2.8.0). There are none by default,
        #   and asking for a name answers `InvalidServiceName`, as AWS does.
        self._consumer_services = dict(consumer_services or {})
        # The Route 53 private zone details from `get_hosted_zone`, which
        # default to the module's `ZONE_DETAILS`.
        self._zones = ZONE_DETAILS if zone_details is None else dict(zone_details)
        # CloudFront VPC origins from `list_vpc_origins` (2.8.0), which has no
        # paginator and uses Marker. There are none by default.
        self._vpc_origins = list(vpc_origins or [])
        # Target group ARN to its tags, from `elbv2:DescribeTags` (2.8.0).
        # There are no tags by default.
        self._tg_tags = dict(tg_tags or {})
        # The EKS details from `describe_cluster`, which default to the
        # module's `EKS_CLUSTERS`.
        self._eks = EKS_CLUSTERS if eks_details is None else dict(eks_details)
        # Domain name to a `DescribeDomains` entry, for OpenSearch.
        self._os = (
            OPENSEARCH_DETAILS
            if opensearch_details is None
            else dict(opensearch_details)
        )
        # API Gateway VPC links. v1 uses `items` and v2 uses `Items`, and both
        # services share the method name `get_vpc_links`, so one page carries
        # both keys, as it does for `list_clusters` on EKS and ECS.
        self._vpc_links = list(vpc_links or [])
        self._vpc_links_v2 = list(vpc_links_v2 or [])
        # WAF web ACLs (2.12.0): one list per `Scope`, an Id to the `WebACL` of
        #   `GetWebACL`, and a web ACL ARN to an API Gateway stage ARN.
        #   There are none by default, since the default world has no web ACL
        #   nodes; `rel_world.py` fills them in. To deny a call, use
        #   `errors["list_web_acls#<Scope>"]` for the list,
        #   `followup_errors[("get_web_acl", Id)]` for the details, and
        #   `followup_errors[("list_resources_for_web_acl", ARN)]` for the
        #   protected resources.
        self._waf = {
            "REGIONAL": list(waf_regional or []),
            "CLOUDFRONT": list(waf_cloudfront or []),
        }
        self._waf_details = dict(waf_details or {})
        self._waf_resources = dict(waf_resources or {})
        # VPC id to the `DescribeVpcAttribute` values (2.13.0), defaulting to
        #   `VPC_DNS`. A VPC that is absent gets the AWS defaults, support on
        #   and hostnames off. To deny the call, use
        #   `followup_errors[("describe_vpc_attribute", VPC id)]`.
        self._vpc_dns = VPC_DNS if vpc_dns is None else dict(vpc_dns)
        # The console-style detail panels: CloudFront cache policies and the
        #   origin access control list, which uses Marker across two pages,
        #   plus the ELBv2 listener rules (ListenerArn to Rules) and listener
        #   certificates (ListenerArn to Certificates). There are none by
        #   default.
        self._cache_policies = list(cache_policies or [])
        self._oacs = list(oacs or [])
        self._lb_rules = dict(lb_rules or {})
        self._listener_certs = dict(listener_certificates or {})
        # Both ELBv2 APIs do have paginators, since boto3 answers
        #   `can_paginate` with True, so they are imitated with `_Keyed`, which
        #   splits by listener ARN. To deny a call, use
        #   `followup_errors[("describe_rules", listener ARN)]` or
        #   `followup_errors[("describe_listener_certificates", …)]`.
        self._keyed["describe_rules"] = ("Rules", "ListenerArn", self._lb_rules)
        self._keyed["describe_listener_certificates"] = (
            "Certificates",
            "ListenerArn",
            self._listener_certs,
        )
        # The account name from `get_account_information`, which only structure
        #   collection calls. `None` means the response carries no
        #   `AccountName` key. To deny the call, use
        #   `errors["get_account_information"]`. It is kept out of `calls`,
        #   because many checks measure the per-type call list, and counted
        #   separately instead.
        self._account_name = account_name
        self.account_calls = 0
        self.calls: list[str] = []
        # The arguments of a direct call to a method with no paginator, used to
        # measure whether the token came back round.
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
        """Imitate an API whose `nextToken` has to be carried by hand: split
        into two pages, with the token on the first page only.
        """
        self.calls.append(method)
        self.direct_calls.append((method, dict(kwargs)))
        if method in self._errors:
            raise self._errors[method]
        key, items = self._data[method]
        middle = len(items) // 2
        if kwargs.get("nextToken") == "page-2":
            return {key: items[middle:]}
        if kwargs.get("nextToken"):
            raise AssertionError(f"unknown token for {method}: {kwargs['nextToken']}")
        page: dict[str, Any] = {key: items[:middle]}
        if items[middle:]:
            page["nextToken"] = "page-2"
        return page

    def describe_vpc_attribute(self, VpcId: str, Attribute: str) -> dict[str, Any]:
        """One VPC attribute (2.13.0). There is no paginator, and one request
        carries one attribute, as in AWS: two cannot be asked for at once.
        """
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
        """Passing `AccountId` raises AssertionError. The contract in
        `account_name.py` is that each account asks only about itself, through
        its own session.
        """
        assert not kwargs, (
            f"must be called without AccountId, for its own account only: {kwargs}"
        )
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
            raise AssertionError(f"{method} has no paginator; check can_paginate")
        self.calls.append(method)
        if method in self._errors:
            return _Raises(self._errors[method])
        if method == "list_services" and method in self._keyed:
            # ECS, as a `cluster=…` follow-up call, and Lattice, as a list with
            # no arguments, share this name. The arguments tell them apart.
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
            # EKS and ECS share this name, so one page carries both keys (see
            # `_MergedPages`).
            return _MergedPages(
                [self._data["list_clusters"], self._data["list_clusters#ecs"]]
            )
        key, items = self._data[method]
        return _Pages(key, items)

    def describe_cluster(self, name: str) -> dict[str, Any]:
        # The EKS details are a single object rather than a list, so they are
        # read with a direct call instead of a paginator.
        # To deny the follow-up call, use followup_errors[("describe_cluster",
        # name)], the same way as for load balancer listeners.
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
        """Split into two pages, with `NextMarker` on the first, to measure
        whether the collector runs to the end.
        """
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
        """The Route 53 zone details, a single object. To deny the call, use
        `followup_errors[("get_hosted_zone", Id)]`.
        """
        self.calls.append("get_hosted_zone")
        err = self._followup_errors.get(("get_hosted_zone", Id))
        if err is not None:
            raise err
        detail = self._zones.get(Id)
        if detail is None:
            raise client_error("NoSuchHostedZone", "GetHostedZone")
        return detail

    def describe_vpc_endpoint_services(self, ServiceNames: list[str]) -> dict[str, Any]:
        """The consumer-side service information. If even one name is unknown,
        the whole call is rejected with `InvalidServiceName`, as AWS does.
        """
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
                f"service not found: {missing}",
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
        """`elbv2:DescribeTags`, which has no paginator and takes up to twenty
        ARNs. AWS rejects a call above that, and the limit is measured here too.
        """
        self.calls.append("describe_tags")
        self.direct_calls.append(
            ("describe_tags", {"ResourceArns": list(ResourceArns)})
        )
        if "describe_tags" in self._errors:
            raise self._errors["describe_tags"]
        if len(ResourceArns) > 20:
            raise client_error("ValidationError", "DescribeTags", "more than 20 ARNs")
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
        """WAFv2 `ListWebACLs`, which has no paginator. It is split into two
        pages, with `NextMarker` on the first only, to measure whether the
        collector runs to the end.

        A call without `Scope` is rejected, as AWS does, so that a call missing
        the scope cannot pass as "none".
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
                f"unknown marker for list_web_acls: {kwargs['NextMarker']}"
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
        """The protected resource ARNs. Only `ResourceType` API_GATEWAY carries
        an answer; asking for any other type raises AssertionError, because
        those are the types we decided not to ask about.
        """
        self.calls.append("list_resources_for_web_acl")
        self.direct_calls.append(
            (
                "list_resources_for_web_acl",
                {"WebACLArn": WebACLArn, "ResourceType": ResourceType},
            )
        )
        if ResourceType != "API_GATEWAY":
            raise AssertionError(
                f"list_resources_for_web_acl was called with {ResourceType}"
            )
        if ":global/webacl/" in WebACLArn:
            raise client_error(
                "WAFInvalidParameterException",
                "ListResourcesForWebACL",
                "CloudFront scope",
            )
        err = self._followup_errors.get(("list_resources_for_web_acl", WebACLArn))
        if err is not None:
            raise err
        return {"ResourceArns": list(self._waf_resources.get(WebACLArn, []))}

    def get_web_acl_for_resource(self, ResourceArn: str) -> dict[str, Any]:
        """The web ACL attached to an ALB: a single object with no paginator,
        since `can_paginate` is False.

        A resource with nothing attached gets no `WebACL` key at all, which is
        how AWS answers. If the fake handed back `{"WebACL": {}}` instead of
        `{}`, the "nothing attached" path would be measured in a shape the real
        API never produces.
        To deny the call, use `followup_errors[("get_web_acl_for_resource",
        ARN)]`, as for any other follow-up call.
        """
        self.calls.append("get_web_acl_for_resource")
        err = self._followup_errors.get(("get_web_acl_for_resource", ResourceArn))
        if err is not None:
            raise err
        acl = self._web_acls.get(ResourceArn)
        return {"WebACL": acl} if acl is not None else {}

    def describe_firewall(self, FirewallArn: str) -> dict[str, Any]:
        # The Network Firewall details are a single object rather than a list
        # too, so they are read with a direct call instead of a paginator.
        # To deny the call, use followup_errors[("describe_firewall", ARN)], as
        # for EKS and the load balancers.
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
        # Single-object details. To deny the call, use followup_errors[(method,
        # key)], as for EKS.
        self.calls.append(method)
        err = self._followup_errors.get((method, key))
        if err is not None:
            raise err
        detail = table.get(key)
        if detail is None:
            raise client_error(code, method)
        return dict(detail)

    def describe_services(self, cluster: str, services: list[str]) -> dict[str, Any]:
        # The real API takes at most ten at a time, so the fake rejects more
        # as well: code that does not batch breaks here.
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
        # The real API takes at most five at a time and rejects more, so code
        # that does not batch breaks here.
        # To deny every call use `errors={"describe_domains": exception}`, and
        # to deny one batch use `followup_errors[("describe_domains", first
        # name)]`.
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
        # An API with neither a paginator nor a token (boto3 elbv2). To deny
        # the call, use followup_errors[(method, target group ARN)].
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


# Renaming this would break every existing test, so it stays as an alias. It
# knows all fifteen resource types.
FakeEc2 = FakeAws


def series_key(
    namespace: str, metric: str, dimensions: dict[str, str]
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    """The series key of `FakeCloudWatch`. A dimension has to match exactly, in
    both name and value.
    """
    return (namespace, metric, tuple(sorted(dimensions.items())))


class FakeCloudWatch:
    """A fake CloudWatch that knows `GetMetricData` and nothing else.

    A value comes back from this fake only when the dimensions match exactly.
    One character out of place in a name still returns an empty array with
    `StatusCode: Complete`, which is what a real account does: a misspelled
    dimension name and a value that does not exist both answer with no data
    points, `Complete`, and no `Messages`. If the fake raised a helpful error
    here, the checks that measure the dimension constants would become
    assertions that cannot fail, which is not a pass but an absence of
    measurement.

    Write `values` newest first, because the product calls with
    `ScanBy=TimestampDescending`. A call with `ScanBy=TimestampAscending` gets
    them reversed, so that the checks can measure how much the rule of reading
    the last data point that has a value from `Values[0]` leans on the ordering.

    As in a real response, `Timestamps` pairs with the values in the same order.
    The i-th value, counting newest first, is the start of the interval (i+1)
    slots before `EndTime` rounded down to `Period`, because a CloudWatch data
    point is stamped with the start of its interval: a stop at 02:24:23Z has its
    last data point at 02:24:00Z. Only continuous series with no gaps are built
    here. With `with_timestamps=False` the key is left out, which builds the
    case where no timestamps were received.
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
        # Series to the `StatusCode` of that query, `Complete` by default.
        # `Forbidden` is the response where the call succeeded but that one
        # series was denied, as happens with cross-account observation. An
        # empty array comes back, but it does not mean measured and zero.
        self._status = dict(status_codes or {})
        # Query ids that drop out of the response entirely, to build the case
        # where queries and results no longer line up.
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
                # It is `Complete` even with no values, which is what a real
                # account does. Unknown and no traffic meet here.
                "StatusCode": self._status.get(key, "Complete"),
                "Values": values,
            }
            if self._with_timestamps:
                result["Timestamps"] = stamps
            results.append(result)
        return {"MetricDataResults": results, "Messages": []}


class FakeMinuteCloudWatch:
    """Imitate `GetMetricData` with timestamped one-minute data points, for
    utilization.

    `FakeCloudWatch` only builds series that run back from the end with no
    gaps, which leaves the period, the bucket and any bunching unmeasurable.
    This fake takes a mapping of one-minute start time to value per series and
    folds it the way CloudWatch does, according to the `Period` and `Stat` being
    asked for: a bucket starts at the timestamp rounded down to `Period`, `Sum`
    adds, `Maximum` takes the largest and `Average` the mean.
    A bucket with no data points in it is absent from the response, because a
    real account reports an empty bucket as missing rather than as zero.
    Only buckets at or after `StartTime` and before `EndTime` are returned.
    Given `page_size`, the response is cut into pages of that size with a
    `NextToken`.
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
    """A fake that knows `GetServiceQuota` and `GetAWSDefaultServiceQuota` and
    nothing else.

    `applied` maps a quota code to the value applied to the account, and
    `defaults` holds the AWS defaults. A code that does not exist answers with
    `NoSuchResourceException`, as the real API does. Passing `applied_error` or
    `default_error` makes that call raise the given exception.
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
    """A factory that splits by service name, so that the structure tier (ec2
    and elbv2) and the state tier (cloudwatch) use different fakes.
    """

    def make(account_id: str, region: str, service: str) -> Any:
        if fail_services and service in fail_services:
            raise fail_services[service]
        if service not in clients:
            # Accepting an unregistered service quietly would let "the state
            # tier was never called" pass.
            raise AssertionError(f"called an unregistered service: {service}")
        return clients[service]

    return make


def factory(client: Any, *, fail_services: dict[str, Exception] | None = None) -> Any:
    """Wrap it in the ClientFactory shape that `collect()` expects."""

    def make(account_id: str, region: str, service: str) -> Any:
        if fail_services and service in fail_services:
            raise fail_services[service]
        return client

    return make


def factory_for(clients: dict[str, Any]) -> Any:
    """A factory that answers differently per account, used to measure the
    deduplication of shared resources.
    """

    def make(account_id: str, region: str, service: str) -> Any:
        return clients[account_id]

    return make


# The Global Accelerator client is built in us-west-2 regardless of the
#   collection Region, as `Spec.region` says. A factory that splits by Region or
#   by pair may not know that Region, so it hands back an empty fake with no
#   accelerators. Every other service still raises KeyError, because accepting
#   an unknown pair quietly would let "it was never called" pass.
GA_SERVICE = "globalaccelerator"
# Cloud WAN (networkmanager) is fixed to us-west-2 as well (2.11.0), and is
# treated the same way.
PINNED_SERVICES = frozenset({GA_SERVICE, "networkmanager"})


def factory_by_pair(clients: dict[tuple[str, str], Any]) -> Any:
    """A factory that answers differently per (account, Region) pair, used to
    measure seeing one resource from two pairs.

    A TGW peering attachment cannot be built with a factory that splits by
    account alone or by Region alone. Such an attachment appears in exactly two
    pairs, account A in Region R1 and account B in Region R2, and not in the
    remaining pairs, A/R2 and B/R1. Splitting on one axis makes it appear in
    pairs where it should not, which makes merging easier than what is actually
    being measured.
    """

    def make(account_id: str, region: str, service: str) -> Any:
        if service in PINNED_SERVICES and (account_id, region) not in clients:
            return (
                FakeAws.empty()
            )  # the accelerator API Region, us-west-2, is not a pair in this
            # world, so there are no accelerators
        return clients[(account_id, region)]

    return make


def factory_by_region(clients: dict[str, Any]) -> Any:
    """A factory that answers differently per Region, used to measure which
    Region a cross-Region resource belongs to.

    Handing one client to every Region only ever builds the situation where a
    cross-Region resource, such as a peering, is visible from both sides. The
    real case, where it appears in the response of one Region only, can be
    measured with this factory alone.
    """

    def make(account_id: str, region: str, service: str) -> Any:
        if service in PINNED_SERVICES and region not in clients:
            return (
                FakeAws.empty()
            )  # the accelerator API Region, us-west-2, is not a Region in this
            # world, so there are no accelerators
        return clients[region]

    return make


# ---- Fakes for the flow log reader -----------------------------------------


class _Forbidden:
    """A call the reader must never make, standing in the same place where the
    read-only guard in `awsguard.py` blocks it against a real account.

    `HeadBucket` and `HeadObject` are not allowlisted verbs. If the reader takes
    a path where botocore quietly calls them to find the bucket Region, as it
    does with a client in the wrong Region or with `download_file`, the run dies
    with `ReadOnlyViolation` against a real account. If the fake accepted the
    call, that path would pass against the fixtures.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *_: Any, **__: Any) -> Any:
        raise AssertionError(f"the reader must not call this: {self._name}")


class FakeLogs:
    """A fake CloudWatch Logs that knows `DescribeLogGroups`,
    `DescribeLogStreams` and `GetLogEvents` and nothing else.

    `groups[name] = {"class": "STANDARD", "streams": {stream: {"last": ms|None,
    "created": ms, "events": [(ms, message), …]}}}`. Only events where
    `startTime <= ts < endTime` are returned, which is the CWL convention. On
    reaching the end it hands back the `nextToken` it was given, unchanged, so
    that the checks measure whether the reader stops on that convention.
    `errors[method]` is `(n, exception)`: the exception to raise from the n-th
    call to that method onwards.
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
        # Most recent event first, with streams that have not been updated yet
        # (last=None) at the back. There is no measurement behind that choice;
        # the reader does not rely on the order.
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
        """Imitate the CWL convention. Events are in ascending time order.
        `startFromHead=True` reads from the front with `nextForwardToken`, and
        `False`, the default, reads from the most recent end with
        `nextBackwardToken`. Within one page the order is ascending either way.
        On reaching the end it hands back the token it was given, unchanged, for
        both tokens, as the AWS documentation describes. The same fake answers
        whichever direction the reader uses, so that reversing the direction
        fails on the result, the records it drops, rather than on an assertion.
        """
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
    """A fake S3 that knows the Region of each bucket.
    `buckets[name] = {"region": "…", "objects": {key: bytes}}`.

    Listing or reading an object through a client in any Region other than the
    bucket's breaks here. Against a real account botocore goes looking for that
    Region with `HeadBucket`, and the read-only guard blocks the call, since it
    is not an allowlisted verb. `GetBucketLocation` answers from any Region,
    which is what a real account does.
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
                f"client in {self.region}, not the bucket Region "
                f"({bucket['region']}); a real account would go to HeadBucket"
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
    """Gzip a flow log text object, header line included. With `members>1`,
    several gzip members are concatenated.
    """
    import gzip

    text = "".join(f"{line}\n" for line in lines).encode("utf-8")
    if members == 1:
        return gzip.compress(text, mtime=0)
    size = max(1, len(text) // members)
    parts = [text[i : i + size] for i in range(0, len(text), size)]
    return b"".join(gzip.compress(part, mtime=0) for part in parts)
