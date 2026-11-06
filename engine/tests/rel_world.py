"""배치 D(스키마 2.10.0 · ADR 0002 §12)의 가짜 세계 — 「가리킨다/넘긴다」 관계 모양 전부를 한 VPC 에 둔다.

`test_relations_d.py` 가 이 세계로 엔진을 재고, 같은 세계를 엔진에 돌린 산출물이 **그대로** `fixtures/relations.json`
(렌더러 하네스 `rel` 페이지 입력 · 검사 152~156)이다 — `test_the_fixture_is_this_world_collected` 가 바이트를 대조한다
(`NETTOPO_REGEN_FIXTURE=1` 이면 다시 쓴다). 🔴 손으로 고치지 않는다.

계정 = 마스킹된 예시 계열(`111122223333` · 불변식 ⑤). 모양:

| 축 | 자원 | 엔진이 내는 것 |
|---|---|---|
| ① 접두사 목록 | 앱 테이블: `corp-onprem`(고객 관리형 · CIDR 둘 → VGW) · `partner-nets`(고객 관리형 · **항목 읽기 거부** → TGW) · S3(AWS 관리형 → 게이트웨이 엔드포인트) · 주 테이블(데이터 서브넷): S3 → 게이트웨이 엔드포인트 | 테이블 속성 `prefix_lists`·`prefix_list_entries`·`prefix_list_entries_unread` · 앱 테이블 `partial`(항목 거부) |
| ② 탄력적 IP | bastion EC2(인스턴스 연결) · NAT(ENI · 「Interface for NAT Gateway nat-…」) · `pub-alb` 서비스 관리형(ENI · 「ELB app/pub-alb/…」) · game EC2(GA 의 EIP 엔드포인트) · 연결 없는 EIP · 이 세계에 없는 ENI 에 붙은 EIP | `eip_association` 넷 · `associated` false 하나 · `association_unresolved` 「<eni> no_eni」 하나 |
| ③ API Gateway | REST `orders-api`: VPC 링크 `orders-link` → `edge-nlb` · 단계 변수 링크 · HTTP `web-http`: VPC 링크 `web-link` → `int-alb` 리스너 · Cloud Map 통합 | `vpc_link` 둘 · `unresolved_vpc_links` 「${stageVariables.vpcLinkId} stage_variable」·「srv-… cloud_map」 |
| ④ RDS 프록시 | `rel-proxy-aurora`(데이터 a·b · TRACKED_CLUSTER `rel-aurora` + 멤버 둘) · `rel-proxy-orders`(데이터 a · `orders-db` + 이 세계에 없는 `legacy-db`) | `proxy_target` 둘(클러스터 선 하나 · 인스턴스 선 하나) · `target_instance_ids` · `unresolved_proxy_targets` 「legacy-db not_collected」 |
| ⑤ Global Accelerator | `rel-ga`(TCP 443): us-east-1 = `pub-alb` · game EIP · 이 세계에 없는 `retired-nlb` · eu-west-1 = 수집 범위 밖 ALB · eu-west-2 = 빈 그룹 | `accelerator_endpoint` 둘(→ `pub-alb` · → game EC2 `via_eip`) · `unresolved_endpoints` 「retired-nlb not_collected」·「eu-alb not_in_scope eu-west-1」 · `empty_endpoint_regions` |
| ⑥ TGW Connect(#129 · 2.11.0) | `rel-sdwan` = VPC 어태치먼트 위 GRE(피어 둘 — 하나는 지워짐) · `rel-sdwan-dx` = DX 게이트웨이 어태치먼트 위 · `rel-sdwan-gone` = 이 세계에 없는 전송 어태치먼트 위 | `connect_transport` 둘 · `connect_peers` · `unresolved_refs` 「<전송 id>」 하나 |
| ⑦ Cloud WAN(#129) | 코어 네트워크 `rel-core`(us-west-2 API): 이 VPC(prod) · 수집 범위 밖 eu-west-1 VPC · 이 세계에 없는 VPC · Connect 연결 · 지우는 중 연결 · 앱 테이블 10.200.0.0/16 → `CoreNetworkArn` | `core_network_attachment` 하나 · `unresolved_attachments` 「… not_in_scope eu-west-1」·「… not_collected」 · `other_attachments` 하나 · 라우트 → 코어 네트워크 |
| ⑧ VPC Lattice(#129) | 서비스 네트워크 `rel-sn`: 이 VPC · 이 세계에 없는 VPC · 서비스 `orders-svc`(EC2 · ALB · Lambda · IP · 이 세계에 없는 EC2) · `status-svc`(빈 대상 그룹) · 이 세계에 없는 서비스 | `lattice_vpc_association` 하나 · `lattice_service_association` 둘 · `lattice_target` 셋 · 까닭 줄 |
| ⑨ WAF 웹 ACL(2.12.0 · ADR 0014) | 리전 범위 `rel-waf-edge`: `pub-alb`(ALB 쪽 `GetWebACLForResource`) · `orders-api` 스테이지 prod·beta · 이 세계에 없는 API 스테이지 · 리전 범위 `rel-waf-idle`(붙은 곳 없음) · CloudFront 범위 `rel-waf-cf`(us-east-1 API · 배포 없음) | `waf_protection` 둘(→ `pub-alb` · → `orders-api` `stages`) · `unresolved_protected` 「… not_collected」 · 글로벌 web ACL 카드 하나 |
"""

from __future__ import annotations

from typing import Any

from fakes import FakeAws, client_error

ACCOUNT = "111122223333"
REGION = "us-east-1"
VPC = "vpc-0rel1234abcd0001"
SN = {
    "pub-a": ("subnet-0rel1234abcd0011", "10.30.1.0/24", "us-east-1a"),
    "pub-b": ("subnet-0rel1234abcd0012", "10.30.2.0/24", "us-east-1b"),
    "app-a": ("subnet-0rel1234abcd0021", "10.30.11.0/24", "us-east-1a"),
    "app-b": ("subnet-0rel1234abcd0022", "10.30.12.0/24", "us-east-1b"),
    "data-a": ("subnet-0rel1234abcd0031", "10.30.21.0/24", "us-east-1a"),
    "data-b": ("subnet-0rel1234abcd0032", "10.30.22.0/24", "us-east-1b"),
}
IGW = "igw-0rel1234abcd0041"
TGW = "tgw-0rel1234abcd0051"
TGW_ATT = "tgw-attach-0rel1234abcd0052"
VGW = "vgw-0rel1234abcd0061"
RT_PUB = "rtb-0rel1234abcd0071"
RT_APP = "rtb-0rel1234abcd0072"
RT_MAIN = "rtb-0rel1234abcd0073"
VPCE_S3 = "vpce-0rel1234abcd0081"
# ① 접두사 목록 — AWS 관리형 S3 · 고객 관리형 둘(하나는 항목 읽기가 거부된다)
PL_S3 = "pl-0rel1234abcd0101"
PL_CORP = "pl-0rel1234abcd0102"
PL_PARTNER = "pl-0rel1234abcd0103"
CORP_CIDRS = ("192.168.10.0/24", "192.168.20.0/24")
_ELB = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}"
PUB_ALB_ARN = f"{_ELB}:loadbalancer/app/pub-alb/0a1b2c3d4e5f6001"
NLB_ARN = f"{_ELB}:loadbalancer/net/edge-nlb/0a1b2c3d4e5f6002"
INT_ALB_ARN = f"{_ELB}:loadbalancer/app/int-alb/0a1b2c3d4e5f6003"
INT_LISTENER_ARN = f"{_ELB}:listener/app/int-alb/0a1b2c3d4e5f6003/0f0e0d0c0b0a0901"
RETIRED_NLB_ARN = f"{_ELB}:loadbalancer/net/retired-nlb/0a1b2c3d4e5f6009"
EU_ALB_ARN = f"arn:aws:elasticloadbalancing:eu-west-1:{ACCOUNT}:loadbalancer/app/eu-alb/0a1b2c3d4e5f6010"
BASTION = "i-0abc1234def70001"
GAME = "i-0abc1234def70002"
APP1 = "i-0abc1234def70003"
NAT = "nat-0abc1234def70011"
NAT_ENI = "eni-0abc1234def70021"
ALB_ENI = "eni-0abc1234def70022"
GONE_ENI = "eni-0abc1234def70029"
# ② 탄력적 IP
EIP = {
    "bastion": "eipalloc-0abc1234def70101",
    "nat": "eipalloc-0abc1234def70102",
    "alb": "eipalloc-0abc1234def70103",
    "game": "eipalloc-0abc1234def70104",
    "idle": "eipalloc-0abc1234def70105",
    "orphan": "eipalloc-0abc1234def70106",
}
# ③ API Gateway
REST_API = "a1b2c3d4e5"  # pragma: allowlist secret — 가짜 API id(비밀 아님)
HTTP_API = "f6e5d4c3b2"  # pragma: allowlist secret — 가짜 API id(비밀 아님)
VPC_LINK = "vl0abc1"
VPC_LINK_V2 = "v2l0abc1"
CLOUD_MAP_ARN = (
    f"arn:aws:servicediscovery:{REGION}:{ACCOUNT}:service/srv-0abc1234def70201"
)
# ④ RDS 프록시
RDS = "orders-db"
AURORA = "rel-aurora"
AURORA_MEMBERS = ("rel-aurora-1", "rel-aurora-2")
PROXY_AURORA = "rel-proxy-aurora"
PROXY_ORDERS = "rel-proxy-orders"
# ⑤ Global Accelerator
GA_ARN = f"arn:aws:globalaccelerator::{ACCOUNT}:accelerator/0a1b2c3d-1111-2222-3333-0abc1234def7"
GA_LISTENER = f"{GA_ARN}/listener/0abc1234"


# ⑥ TGW Connect(#129) — SD-WAN 어플라이언스 주소(이 세계에 그 EC2 는 없다 — 피어 주소는 글자일 뿐이다 · 데이터 서브넷 → NAT 라우트 선 자리를 비운다) ·
#   DX 게이트웨이(전송 어태치먼트 둘째 꼴)
SDWAN_IP = "10.30.12.20"
DXGW = "0d0e0f00-1111-2222-3333-0abc1234def7"
DX_ATT = "tgw-attach-0rel1234abcd0054"
CONNECT_VPC = "tgw-attach-0rel1234abcd0053"
CONNECT_DX = "tgw-attach-0rel1234abcd0055"
CONNECT_GONE = "tgw-attach-0rel1234abcd0056"
GONE_TRANSPORT = "tgw-attach-0rel1234abcd0059"
PEER_A = "tgw-connect-peer-0rel1234abcd0061"
PEER_B = "tgw-connect-peer-0rel1234abcd0062"
PEER_DELETED = "tgw-connect-peer-0rel1234abcd0063"
# ⑦ Cloud WAN(#129)
CORE = "core-network-0rel1234abcd0201"
CORE_ARN = f"arn:aws:networkmanager::{ACCOUNT}:core-network/{CORE}"
GLOBAL_NET = "global-network-0rel1234abcd0200"
EU_VPC = "vpc-0rel1234abcd0091"
GONE_VPC = "vpc-0rel1234abcd0092"
CORE_CIDR = "10.200.0.0/16"
# ⑧ VPC Lattice(#129)
SN_ID = "sn-0rel1234abcd0301"
SVC_ORDERS = "svc-0rel1234abcd0311"
SVC_STATUS = "svc-0rel1234abcd0312"
SVC_GONE = "svc-0rel1234abcd0319"
LATTICE_GONE_VPC = "vpc-0rel1234abcd0093"
FN = "rel-fn"
FN_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FN}"
GONE_INSTANCE = "i-0abc1234def70099"
LATTICE_IP = "10.30.11.99"
_LAT = f"arn:aws:vpc-lattice:{REGION}:{ACCOUNT}"
# ⑨ WAF 웹 ACL(2.12.0 · ADR 0014)
_WAF = f"arn:aws:wafv2:{REGION}:{ACCOUNT}"
WAF_EDGE = "0rel1234-aaaa-bbbb-cccc-0rel0waf0401"
WAF_IDLE = "0rel1234-aaaa-bbbb-cccc-0rel0waf0402"
WAF_CF = "0rel1234-aaaa-bbbb-cccc-0rel0waf0403"
WAF_EDGE_ARN = f"{_WAF}:regional/webacl/rel-waf-edge/{WAF_EDGE}"
WAF_IDLE_ARN = f"{_WAF}:regional/webacl/rel-waf-idle/{WAF_IDLE}"
WAF_CF_ARN = f"arn:aws:wafv2:us-east-1:{ACCOUNT}:global/webacl/rel-waf-cf/{WAF_CF}"
WAF_GONE_API = (
    "q9w8e7r6t5"  # pragma: allowlist secret — 이 세계에 없는 가짜 API id(비밀 아님)
)


def _tags(name: str) -> list[dict[str, str]]:
    return [{"Key": "Name", "Value": name}]


def _local() -> dict[str, Any]:
    return {
        "DestinationCidrBlock": "10.30.0.0/16",
        "GatewayId": "local",
        "State": "active",
    }


def _assoc(rt: str, *zones: str) -> list[dict[str, Any]]:
    return [
        {
            "RouteTableAssociationId": f"rtbassoc-0{rt[-4:]}{i}",
            "SubnetId": SN[z][0],
            "AssociationState": {"State": "associated"},
        }
        for i, z in enumerate(zones)
    ]


def world() -> FakeAws:
    subnets = [
        {
            "SubnetId": sid,
            "VpcId": VPC,
            "CidrBlock": cidr,
            "AvailabilityZone": zone,
            "AvailabilityZoneId": "use1-az1" if zone.endswith("a") else "use1-az2",
            "State": "available",
            "MapPublicIpOnLaunch": name.startswith("pub"),
            "AvailableIpAddressCount": 240,
            "Tags": _tags(f"rel-{name}"),
        }
        for name, (sid, cidr, zone) in SN.items()
    ]
    route_tables = [
        {
            "RouteTableId": RT_PUB,
            "VpcId": VPC,
            "Associations": _assoc(RT_PUB, "pub-a", "pub-b"),
            "Routes": [
                _local(),
                {
                    "DestinationCidrBlock": "0.0.0.0/0",
                    "GatewayId": IGW,
                    "State": "active",
                },
            ],
            "Tags": _tags("rel-public-rt"),
        },
        {
            "RouteTableId": RT_APP,
            "VpcId": VPC,
            "Associations": _assoc(RT_APP, "app-a", "app-b"),
            "Routes": [
                _local(),
                {
                    "DestinationPrefixListId": PL_CORP,
                    "GatewayId": VGW,
                    "State": "active",
                },
                {
                    "DestinationPrefixListId": PL_PARTNER,
                    "TransitGatewayId": TGW,
                    "State": "active",
                },
                {
                    "DestinationPrefixListId": PL_S3,
                    "GatewayId": VPCE_S3,
                    "State": "active",
                },
                # ⑦ Cloud WAN — 라우트 대상이 코어 네트워크 ARN 으로만 온다(#129)
                {
                    "DestinationCidrBlock": CORE_CIDR,
                    "CoreNetworkArn": CORE_ARN,
                    "State": "active",
                },
            ],
            "Tags": _tags("rel-app-rt"),
        },
        {
            # 주 라우트 테이블 — 데이터 서브넷이 연관 선 없이 쓴다
            "RouteTableId": RT_MAIN,
            "VpcId": VPC,
            "Associations": [
                {"RouteTableAssociationId": "rtbassoc-0relmain", "Main": True}
            ],
            "Routes": [
                _local(),
                {
                    "DestinationPrefixListId": PL_S3,
                    "GatewayId": VPCE_S3,
                    "State": "active",
                },
            ],
            "Tags": _tags("rel-data-rt"),
        },
    ]
    prefix_lists = [
        {
            "PrefixListId": PL_S3,
            "AddressFamily": "IPv4",
            "State": "create-complete",
            "PrefixListName": f"com.amazonaws.{REGION}.s3",
            "OwnerId": "AWS",
        },
        {
            "PrefixListId": PL_CORP,
            "AddressFamily": "IPv4",
            "State": "create-complete",
            "PrefixListName": "corp-onprem",
            "MaxEntries": 10,
            "OwnerId": ACCOUNT,
        },
        {
            "PrefixListId": PL_PARTNER,
            "AddressFamily": "IPv4",
            "State": "create-complete",
            "PrefixListName": "partner-nets",
            "MaxEntries": 5,
            "OwnerId": ACCOUNT,
        },
        # 이 VPC 가 안 쓰는 AWS 관리형 목록 — 고르지 않는다(이름 표에 섞이지 않는다)
        {
            "PrefixListId": "pl-0rel1234abcd0199",
            "AddressFamily": "IPv4",
            "State": "create-complete",
            "PrefixListName": f"com.amazonaws.{REGION}.dynamodb",
            "OwnerId": "AWS",
        },
    ]
    route_tables[2]["Routes"].append(
        {"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": NAT, "State": "active"}
    )

    def lb(
        name: str, arn: str, kind: str, scheme: str, zones: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            "LoadBalancerName": name,
            "LoadBalancerArn": arn,
            "DNSName": f"{name}-1234567890.{REGION}.elb.amazonaws.com",
            "Scheme": scheme,
            "Type": kind,
            "VpcId": VPC,
            "State": {"Code": "active"},
            "AvailabilityZones": [
                {"ZoneName": SN[z][2], "SubnetId": SN[z][0]} for z in zones
            ],
            "SecurityGroups": [] if kind == "network" else ["sg-0abc1234def70301"],
        }

    def inst(iid: str, subnet: str, ip: str, name: str) -> dict[str, Any]:
        return {
            "OwnerId": ACCOUNT,
            "Instances": [
                {
                    "InstanceId": iid,
                    "InstanceType": "t4g.nano",
                    "State": {"Name": "running"},
                    "Placement": {"AvailabilityZone": SN[subnet][2]},
                    "SubnetId": SN[subnet][0],
                    "VpcId": VPC,
                    "PrivateIpAddress": ip,
                    "SecurityGroups": [{"GroupId": "sg-0abc1234def70301"}],
                    "Tags": _tags(name),
                }
            ],
        }

    def eni(
        eid: str, subnet: str, ip: str, kind: str, desc: str, requester: str = ""
    ) -> dict[str, Any]:
        sid, _, zone = SN[subnet]
        return {
            "NetworkInterfaceId": eid,
            "VpcId": VPC,
            "SubnetId": sid,
            "AvailabilityZone": zone,
            "PrivateIpAddress": ip,
            "PrivateIpAddresses": [{"PrivateIpAddress": ip, "Primary": True}],
            "InterfaceType": kind,
            "RequesterId": requester,
            "Description": desc,
            "TagSet": [],
        }

    def addr(key: str, ip: str, **extra: Any) -> dict[str, Any]:
        out = {
            "AllocationId": EIP[key],
            "Domain": "vpc",
            "PublicIp": ip,
            "PublicIpv4Pool": "amazon",
            "NetworkBorderGroup": REGION,
        }
        if "InstanceId" in extra or "NetworkInterfaceId" in extra:
            out["AssociationId"] = "eipassoc-" + EIP[key].split("-")[1]
        out.update(extra)
        return out

    def rds(iid: str, subnet: str, cluster: str | None = None) -> dict[str, Any]:
        out = {
            "DBInstanceIdentifier": iid,
            "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:{iid}",
            "DBInstanceClass": "db.t4g.micro",
            "Engine": "aurora-postgresql" if cluster else "postgres",
            "DBInstanceStatus": "available",
            "MultiAZ": False,
            "AvailabilityZone": SN[subnet][2],
            "PubliclyAccessible": False,
            "VpcSecurityGroups": [
                {"VpcSecurityGroupId": "sg-0abc1234def70302", "Status": "active"}
            ],
            "DBSubnetGroup": {
                "DBSubnetGroupName": "rel-db",
                "VpcId": VPC,
                "Subnets": [
                    {
                        "SubnetIdentifier": SN[z][0],
                        "SubnetAvailabilityZone": {"Name": SN[z][2]},
                    }
                    for z in ("data-a", "data-b")
                ],
            },
            "TagList": [],
        }
        if cluster:
            out["DBClusterIdentifier"] = cluster
        return out

    def proxy(name: str, zones: tuple[str, ...]) -> dict[str, Any]:
        return {
            "DBProxyName": name,
            "DBProxyArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db-proxy:prx-0abc1234def7{len(zones)}",
            "Status": "available",
            "EngineFamily": "POSTGRESQL",
            "VpcId": VPC,
            "VpcSecurityGroupIds": ["sg-0abc1234def70302"],
            "VpcSubnetIds": [SN[z][0] for z in zones],
            "Auth": [
                {
                    "AuthScheme": "SECRETS",
                    "SecretArn": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:rel-db-NTSECRET",
                }
            ],
            "RoleArn": f"arn:aws:iam::{ACCOUNT}:role/rel-proxy-role",
            "Endpoint": f"{name}.proxy-x.{REGION}.rds.amazonaws.com",
            "RequireTLS": True,
            "IdleClientTimeout": 1800,
        }

    datasets: dict[str, Any] = {
        "load_balancers": [
            lb(
                "pub-alb",
                PUB_ALB_ARN,
                "application",
                "internet-facing",
                ("pub-a", "pub-b"),
            ),
            lb("edge-nlb", NLB_ARN, "network", "internal", ("app-a", "app-b")),
            lb("int-alb", INT_ALB_ARN, "application", "internal", ("app-a", "app-b")),
        ],
        "listeners": {
            PUB_ALB_ARN: [
                {"ListenerArn": f"{PUB_ALB_ARN}/l/1", "Protocol": "HTTPS", "Port": 443}
            ],
            NLB_ARN: [
                {"ListenerArn": f"{NLB_ARN}/l/1", "Protocol": "TCP", "Port": 443}
            ],
            INT_ALB_ARN: [
                {"ListenerArn": INT_LISTENER_ARN, "Protocol": "HTTP", "Port": 80}
            ],
        },
        "instances": [
            inst(BASTION, "pub-a", "10.30.1.10", "bastion"),
            inst(GAME, "pub-a", "10.30.1.11", "game-1"),
            inst(APP1, "app-a", "10.30.11.10", "app-1"),
        ],
        "nat_gateways": [
            {
                "NatGatewayId": NAT,
                "SubnetId": SN["pub-b"][0],
                "VpcId": VPC,
                "State": "available",
                "ConnectivityType": "public",
                "NatGatewayAddresses": [
                    {
                        "AllocationId": EIP["nat"],
                        "NetworkInterfaceId": NAT_ENI,
                        "PrivateIp": "10.30.2.50",
                        "PublicIp": "198.51.100.32",
                    }
                ],
                "Tags": _tags("rel-nat"),
            }
        ],
        "network_interfaces": [
            eni(
                NAT_ENI,
                "pub-b",
                "10.30.2.50",
                "nat_gateway",
                f"Interface for NAT Gateway {NAT}",
                "amazon-nat",
            ),
            eni(
                ALB_ENI,
                "pub-a",
                "10.30.1.60",
                "interface",
                "ELB app/pub-alb/0a1b2c3d4e5f6001",
                "amazon-elb",
            ),
        ],
        "elastic_ips": [
            addr(
                "bastion",
                "198.51.100.31",
                InstanceId=BASTION,
                NetworkInterfaceId="eni-0abc1234def70031",
                PrivateIpAddress="10.30.1.10",
                Tags=_tags("bastion-eip"),
            ),
            addr(
                "nat",
                "198.51.100.32",
                NetworkInterfaceId=NAT_ENI,
                PrivateIpAddress="10.30.2.50",
            ),
            addr(
                "alb",
                "198.51.100.33",
                NetworkInterfaceId=ALB_ENI,
                PrivateIpAddress="10.30.1.60",
                ServiceManaged="alb",
            ),
            addr(
                "game",
                "198.51.100.34",
                InstanceId=GAME,
                NetworkInterfaceId="eni-0abc1234def70032",
                PrivateIpAddress="10.30.1.11",
            ),
            addr("idle", "198.51.100.35", Tags=_tags("spare-eip")),
            addr(
                "orphan",
                "198.51.100.36",
                NetworkInterfaceId=GONE_ENI,
                PrivateIpAddress="10.30.12.99",
            ),
        ],
        "rest_apis": [
            {
                "id": REST_API,
                "name": "orders-api",
                "endpointConfiguration": {"types": ["REGIONAL"]},
                "tags": {"team": "orders"},
            }
        ],
        "rest_resources": {
            REST_API: [
                {"id": "r0001", "path": "/"},
                {
                    "id": "r0002",
                    "path": "/orders",
                    "resourceMethods": {
                        "GET": {
                            "httpMethod": "GET",
                            "methodIntegration": {
                                "type": "HTTP_PROXY",
                                "connectionType": "VPC_LINK",
                                "connectionId": VPC_LINK,
                                "uri": "http://edge-nlb/orders",
                            },
                        },
                        "POST": {
                            "httpMethod": "POST",
                            "methodIntegration": {
                                "type": "HTTP_PROXY",
                                "connectionType": "VPC_LINK",
                                "connectionId": "${stageVariables.vpcLinkId}",
                            },
                        },
                        "OPTIONS": {
                            "httpMethod": "OPTIONS",
                            "methodIntegration": {"type": "MOCK"},
                        },
                    },
                },
            ]
        },
        "vpc_links": [
            {
                "id": VPC_LINK,
                "name": "orders-link",
                "targetArns": [NLB_ARN],
                "status": "AVAILABLE",
            }
        ],
        "http_apis": [
            {
                "ApiId": HTTP_API,
                "Name": "web-http",
                "ProtocolType": "HTTP",
                "ApiEndpoint": f"https://{HTTP_API}.execute-api.{REGION}.amazonaws.com",
                "Tags": {},
            }
        ],
        "v2_integrations": {
            HTTP_API: [
                {
                    "IntegrationId": "i0001",
                    "ConnectionType": "VPC_LINK",
                    "ConnectionId": VPC_LINK_V2,
                    "IntegrationType": "HTTP_PROXY",
                    "IntegrationUri": INT_LISTENER_ARN,
                },
                {
                    "IntegrationId": "i0002",
                    "ConnectionType": "VPC_LINK",
                    "ConnectionId": VPC_LINK_V2,
                    "IntegrationType": "HTTP_PROXY",
                    "IntegrationUri": CLOUD_MAP_ARN,
                },
                {
                    "IntegrationId": "i0003",
                    "ConnectionType": "INTERNET",
                    "IntegrationType": "AWS_PROXY",
                    "IntegrationUri": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:web-fn",
                },
            ]
        },
        "vpc_links_v2": [
            {
                "VpcLinkId": VPC_LINK_V2,
                "Name": "web-link",
                "SubnetIds": [SN["app-b"][0], SN["app-a"][0]],
                "SecurityGroupIds": ["sg-0abc1234def70301"],
                "VpcLinkStatus": "AVAILABLE",
            }
        ],
        "rds_instances": [
            rds(RDS, "data-a"),
            rds(AURORA_MEMBERS[0], "data-a", AURORA),
            rds(AURORA_MEMBERS[1], "data-b", AURORA),
        ],
        "rds_clusters": [
            {
                "DBClusterIdentifier": AURORA,
                "DBClusterArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:{AURORA}",
                "Engine": "aurora-postgresql",
                "EngineVersion": "16.4",
                "EngineMode": "provisioned",
                "Status": "available",
                "Endpoint": f"{AURORA}.cluster-x.{REGION}.rds.amazonaws.com",
                "Port": 5432,
                "MultiAZ": False,
                "AvailabilityZones": ["us-east-1a", "us-east-1b"],
                "DBClusterMembers": [
                    {"DBInstanceIdentifier": m, "IsClusterWriter": i == 0}
                    for i, m in enumerate(AURORA_MEMBERS)
                ],
                "DBSubnetGroup": "rel-db",
                "VpcSecurityGroups": [
                    {"VpcSecurityGroupId": "sg-0abc1234def70302", "Status": "active"}
                ],
                "StorageEncrypted": True,
            }
        ],
        "db_subnet_groups": [
            {
                "DBSubnetGroupName": "rel-db",
                "VpcId": VPC,
                "Subnets": [
                    {
                        "SubnetIdentifier": SN[z][0],
                        "SubnetAvailabilityZone": {"Name": SN[z][2]},
                    }
                    for z in ("data-a", "data-b")
                ],
            }
        ],
        "db_proxies": [
            proxy(PROXY_AURORA, ("data-b", "data-a")),
            proxy(PROXY_ORDERS, ("data-a",)),
        ],
        "proxy_targets": {
            PROXY_AURORA: [
                {"RdsResourceId": AURORA, "Port": 5432, "Type": "TRACKED_CLUSTER"},
                *[
                    {
                        "RdsResourceId": m,
                        "Port": 5432,
                        "Type": "RDS_INSTANCE",
                        "TargetHealth": {"State": "AVAILABLE"},
                    }
                    for m in AURORA_MEMBERS
                ],
            ],
            PROXY_ORDERS: [
                {
                    "RdsResourceId": RDS,
                    "Port": 5432,
                    "Type": "RDS_INSTANCE",
                    "TargetHealth": {"State": "AVAILABLE"},
                },
                {
                    "RdsResourceId": "legacy-db",
                    "Port": 5432,
                    "Type": "RDS_INSTANCE",
                    "TargetHealth": {"State": "UNAVAILABLE"},
                },
            ],
        },
        "accelerators": [
            {
                "AcceleratorArn": GA_ARN,
                "Name": "rel-ga",
                "IpAddressType": "IPV4",
                "Enabled": True,
                "IpSets": [
                    {"IpFamily": "IPv4", "IpAddresses": ["192.0.2.10", "192.0.2.11"]}
                ],
                "DnsName": "a0abc1234def7.awsglobalaccelerator.com",
                "Status": "DEPLOYED",
            }
        ],
        "ga_listeners": {
            GA_ARN: [
                {
                    "ListenerArn": GA_LISTENER,
                    "Protocol": "TCP",
                    "PortRanges": [{"FromPort": 443, "ToPort": 443}],
                }
            ]
        },
        "ga_endpoint_groups": {
            GA_LISTENER: [
                {
                    "EndpointGroupArn": f"{GA_LISTENER}/endpoint-group/0001",
                    "EndpointGroupRegion": REGION,
                    "EndpointDescriptions": [
                        {
                            "EndpointId": PUB_ALB_ARN,
                            "Weight": 128,
                            "HealthState": "HEALTHY",
                        },
                        {
                            "EndpointId": EIP["game"],
                            "Weight": 64,
                            "HealthState": "HEALTHY",
                        },
                        {
                            "EndpointId": RETIRED_NLB_ARN,
                            "Weight": 64,
                            "HealthState": "UNHEALTHY",
                        },
                    ],
                },
                {
                    "EndpointGroupArn": f"{GA_LISTENER}/endpoint-group/0002",
                    "EndpointGroupRegion": "eu-west-1",
                    "EndpointDescriptions": [{"EndpointId": EU_ALB_ARN, "Weight": 128}],
                },
                {
                    "EndpointGroupArn": f"{GA_LISTENER}/endpoint-group/0003",
                    "EndpointGroupRegion": "eu-west-2",
                    "EndpointDescriptions": [],
                },
            ]
        },
    }
    datasets.update(_overlay())
    return FakeAws.empty(
        **datasets,
        **_waf(),
        vpcs=[
            {
                "VpcId": VPC,
                "CidrBlock": "10.30.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": _tags("rel-vpc"),
            }
        ],
        subnets=subnets,
        route_tables=route_tables,
        internet_gateways=[
            {
                "InternetGatewayId": IGW,
                "Attachments": [{"VpcId": VPC, "State": "available"}],
                "Tags": _tags("rel-igw"),
            }
        ],
        transit_gateways=[
            {
                "TransitGatewayId": TGW,
                "TransitGatewayArn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:transit-gateway/{TGW}",
                "OwnerId": ACCOUNT,
                "State": "available",
                "Options": {"AmazonSideAsn": 64512},
                "Tags": _tags("rel-tgw"),
            }
        ],
        transit_gateway_attachments=[
            {
                "TransitGatewayAttachmentId": TGW_ATT,
                "TransitGatewayId": TGW,
                "TransitGatewayOwnerId": ACCOUNT,
                "ResourceOwnerId": ACCOUNT,
                "ResourceType": "vpc",
                "ResourceId": VPC,
                "State": "available",
                "Tags": [],
            },
            # ⑥ DX 게이트웨이 어태치먼트(Connect 의 둘째 전송) · Connect 셋(VPC 위 · DX 위 · 이 세계에 없는 전송 위)
            {
                "TransitGatewayAttachmentId": DX_ATT,
                "TransitGatewayId": TGW,
                "TransitGatewayOwnerId": ACCOUNT,
                "ResourceOwnerId": ACCOUNT,
                "ResourceType": "direct-connect-gateway",
                "ResourceId": DXGW,
                "State": "available",
                "Tags": [],
            },
            *(
                {
                    "TransitGatewayAttachmentId": aid,
                    "TransitGatewayId": TGW,
                    "TransitGatewayOwnerId": ACCOUNT,
                    "ResourceOwnerId": ACCOUNT,
                    "ResourceType": "connect",
                    "ResourceId": transport,
                    "State": "available",
                    "Tags": _tags(name),
                }
                for aid, transport, name in (
                    (CONNECT_VPC, TGW_ATT, "rel-sdwan"),
                    (CONNECT_DX, DX_ATT, "rel-sdwan-dx"),
                    (CONNECT_GONE, GONE_TRANSPORT, "rel-sdwan-gone"),
                )
            ),
        ],
        dx_gateways=[
            {
                "directConnectGatewayId": DXGW,
                "directConnectGatewayName": "rel-dxgw",
                "amazonSideAsn": 64520,
                "ownerAccount": ACCOUNT,
                "directConnectGatewayState": "available",
            }
        ],
        tgw_connects=[
            {
                "TransitGatewayAttachmentId": aid,
                "TransportTransitGatewayAttachmentId": transport,
                "TransitGatewayId": TGW,
                "State": "available",
                "Options": {"Protocol": "gre"},
            }
            for aid, transport in (
                (CONNECT_VPC, TGW_ATT),
                (CONNECT_DX, DX_ATT),
                (CONNECT_GONE, GONE_TRANSPORT),
            )
        ],
        tgw_connect_peers=[
            _peer(
                PEER_A,
                CONNECT_VPC,
                "10.99.0.10",
                SDWAN_IP,
                "169.254.100.0/29",
                65010,
                "sdwan-a",
            ),
            _peer(
                PEER_B,
                CONNECT_DX,
                "10.99.0.11",
                "172.16.5.10",
                "169.254.101.0/29",
                65020,
                "branch-dx",
            ),
            # 지워진 피어 — 목록에 남아도 싣지 않는다
            {
                **_peer(
                    PEER_DELETED,
                    CONNECT_VPC,
                    "10.99.0.12",
                    SDWAN_IP,
                    "169.254.102.0/29",
                    65010,
                    "old",
                ),
                "State": "deleted",
            },
        ],
        vpn_gateways=[
            {
                "VpnGatewayId": VGW,
                "State": "available",
                "Type": "ipsec.1",
                "AmazonSideAsn": 64513,
                "VpcAttachments": [{"VpcId": VPC, "State": "attached"}],
                "Tags": _tags("rel-vgw"),
            }
        ],
        vpc_endpoints=[
            {
                "VpcEndpointId": VPCE_S3,
                "VpcEndpointType": "Gateway",
                "VpcId": VPC,
                "ServiceName": f"com.amazonaws.{REGION}.s3",
                "State": "available",
                "RouteTableIds": [RT_APP, RT_MAIN],
                "SubnetIds": [],
                "PrivateDnsEnabled": False,
                "Tags": _tags("rel-s3"),
            }
        ],
        managed_prefix_lists=prefix_lists,
        prefix_list_entries={PL_CORP: [{"Cidr": c} for c in reversed(CORP_CIDRS)]},
        followup_errors={
            ("get_managed_prefix_list_entries", PL_PARTNER): client_error(
                "UnauthorizedOperation", "GetManagedPrefixListEntries"
            )
        },
    )


def _peer(
    pid: str, att: str, tgw_ip: str, peer_ip: str, cidr: str, asn: int, name: str
) -> dict[str, Any]:
    """TGW Connect 피어(GRE 터널 하나 + BGP 세션 둘) — BGP 상태 값은 구조에 새면 안 된다(`BgpStatus`)."""
    base = cidr.rsplit(".", 1)[0]
    return {
        "TransitGatewayAttachmentId": att,
        "TransitGatewayConnectPeerId": pid,
        "State": "available",
        "ConnectPeerConfiguration": {
            "TransitGatewayAddress": tgw_ip,
            "PeerAddress": peer_ip,
            "InsideCidrBlocks": [cidr],
            "Protocol": "gre",
            "BgpConfigurations": [
                {
                    "TransitGatewayAsn": 64512,
                    "PeerAsn": asn,
                    "TransitGatewayAddress": f"{base}.2",
                    "PeerAddress": f"{base}.1",
                    "BgpStatus": "up",
                },
                {
                    "TransitGatewayAsn": 64512,
                    "PeerAsn": asn,
                    "TransitGatewayAddress": f"{base}.3",
                    "PeerAddress": f"{base}.1",
                    "BgpStatus": "down",
                },
            ],
        },
        "Tags": _tags(name),
    }


def _overlay() -> dict[str, Any]:
    """⑦ Cloud WAN · ⑧ VPC Lattice 의 응답(#129)."""

    def att(
        aid: str, kind: str, arn: str, seg: str, loc: str, state: str = "AVAILABLE"
    ) -> dict[str, Any]:
        return {
            "CoreNetworkId": CORE,
            "CoreNetworkArn": CORE_ARN,
            "AttachmentId": aid,
            "OwnerAccountId": ACCOUNT,
            "AttachmentType": kind,
            "State": state,
            "EdgeLocation": loc,
            "ResourceArn": arn,
            "SegmentName": seg,
        }

    def tg(
        tid: str, name: str, kind: str, port: int | None, svcs: list[str]
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": tid,
            "arn": f"{_LAT}:targetgroup/{tid}",
            "name": name,
            "type": kind,
            "status": "ACTIVE",
            "serviceArns": svcs,
        }
        if kind != "LAMBDA":
            out.update(protocol="HTTP", vpcIdentifier=VPC)
        if port is not None:
            out["port"] = port
        return out

    orders = f"{_LAT}:service/{SVC_ORDERS}"
    status = f"{_LAT}:service/{SVC_STATUS}"
    ec2 = f"arn:aws:ec2:{REGION}:{ACCOUNT}:vpc"
    return {
        "core_networks": [
            {
                "CoreNetworkId": CORE,
                "CoreNetworkArn": CORE_ARN,
                "GlobalNetworkId": GLOBAL_NET,
                "OwnerAccountId": ACCOUNT,
                "State": "UPDATING",
                "Description": "rel core network",
                "Tags": _tags("rel-core"),
            }
        ],
        "cwan_attachments": {
            CORE: [
                att(
                    "attachment-0rel1234abcd0211", "VPC", f"{ec2}/{VPC}", "prod", REGION
                ),
                att(
                    "attachment-0rel1234abcd0212",
                    "VPC",
                    f"arn:aws:ec2:eu-west-1:{ACCOUNT}:vpc/{EU_VPC}",
                    "prod",
                    "eu-west-1",
                ),
                att(
                    "attachment-0rel1234abcd0213",
                    "VPC",
                    f"{ec2}/{GONE_VPC}",
                    "dev",
                    REGION,
                ),
                att(
                    "attachment-0rel1234abcd0214",
                    "CONNECT",
                    f"arn:aws:networkmanager::{ACCOUNT}:attachment/attachment-0rel1234abcd0211",
                    "prod",
                    REGION,
                ),
                att(
                    "attachment-0rel1234abcd0215",
                    "VPC",
                    f"{ec2}/{VPC}",
                    "dev",
                    REGION,
                    "DELETING",
                ),
            ]
        },
        "service_networks": [
            {
                "id": SN_ID,
                "arn": f"{_LAT}:servicenetwork/{SN_ID}",
                "name": "rel-sn",
                "numberOfAssociatedVPCs": 2,
                "numberOfAssociatedServices": 3,
            }
        ],
        "sn_vpc_associations": {
            SN_ID: [
                {"id": "snva-0rel1234abcd0321", "status": "ACTIVE", "vpcId": VPC},
                {
                    "id": "snva-0rel1234abcd0322",
                    "status": "ACTIVE",
                    "vpcId": LATTICE_GONE_VPC,
                },
                {
                    "id": "snva-0rel1234abcd0323",
                    "status": "DELETE_IN_PROGRESS",
                    "vpcId": "vpc-0rel1234abcd0094",
                },
            ]
        },
        "sn_service_associations": {
            SN_ID: [
                {
                    "id": "snsa-0rel1234abcd0331",
                    "status": "ACTIVE",
                    "serviceId": SVC_ORDERS,
                    "serviceName": "orders-svc",
                    "serviceArn": orders,
                },
                {
                    "id": "snsa-0rel1234abcd0332",
                    "status": "ACTIVE",
                    "serviceId": SVC_STATUS,
                    "serviceName": "status-svc",
                    "serviceArn": status,
                },
                {
                    "id": "snsa-0rel1234abcd0333",
                    "status": "ACTIVE",
                    "serviceId": SVC_GONE,
                    "serviceName": "partner-svc",
                    "serviceArn": f"arn:aws:vpc-lattice:{REGION}:222233334444:service/{SVC_GONE}",
                },
            ]
        },
        "lattice_services": [
            {
                "id": SVC_ORDERS,
                "arn": orders,
                "name": "orders-svc",
                "dnsEntry": {
                    "domainName": f"orders-svc-0rel1234abcd0311.7d67968.vpc-lattice-svcs.{REGION}.on.aws"
                },
                "status": "ACTIVE",
            },
            {
                "id": SVC_STATUS,
                "arn": status,
                "name": "status-svc",
                "dnsEntry": {
                    "domainName": f"status-svc-0rel1234abcd0312.7d67968.vpc-lattice-svcs.{REGION}.on.aws"
                },
                "status": "ACTIVE",
            },
        ],
        "lattice_target_groups": [
            tg("tg-0rel1234abcd0341", "orders-ec2", "INSTANCE", 8080, [orders]),
            tg("tg-0rel1234abcd0342", "orders-alb", "ALB", 80, [orders]),
            tg("tg-0rel1234abcd0343", "orders-fn", "LAMBDA", None, [orders]),
            tg("tg-0rel1234abcd0344", "orders-ip", "IP", 8080, [orders]),
            tg("tg-0rel1234abcd0345", "status-empty", "INSTANCE", 80, [status]),
            # 이 쌍의 서비스가 쓰지 않는 그룹 — 대상을 묻지 않는다
            tg("tg-0rel1234abcd0349", "unused", "INSTANCE", 80, []),
        ],
        "lattice_targets": {
            "tg-0rel1234abcd0341": [
                {"id": APP1, "port": 8080, "status": "HEALTHY"},
                {"id": GONE_INSTANCE, "port": 8080, "status": "UNAVAILABLE"},
            ],
            "tg-0rel1234abcd0342": [
                {"id": INT_ALB_ARN, "port": 80, "status": "HEALTHY"}
            ],
            "tg-0rel1234abcd0343": [{"id": FN_ARN, "status": "HEALTHY"}],
            "tg-0rel1234abcd0344": [
                {"id": LATTICE_IP, "port": 8080, "status": "HEALTHY"}
            ],
            "tg-0rel1234abcd0345": [],
        },
        "lambda_functions": [
            {
                "FunctionName": FN,
                "FunctionArn": FN_ARN,
                "Runtime": "python3.12",
                "VpcConfig": {"VpcId": "", "SubnetIds": [], "SecurityGroupIds": []},
            }
        ],
    }


def _waf() -> dict[str, Any]:
    """⑨ WAF 웹 ACL(2.12.0 · ADR 0014) — 리전 범위 둘 · CloudFront 범위 하나 · `pub-alb` 의 web ACL(ALB 쪽 사실)."""

    def summary(name: str, acl_id: str, arn: str) -> dict[str, Any]:
        return {"Name": name, "Id": acl_id, "ARN": arn, "LockToken": f"lock-{acl_id}"}

    stage = f"arn:aws:apigateway:{REGION}::/restapis"
    return {
        "waf_regional": [
            summary("rel-waf-edge", WAF_EDGE, WAF_EDGE_ARN),
            summary("rel-waf-idle", WAF_IDLE, WAF_IDLE_ARN),
        ],
        "waf_cloudfront": [summary("rel-waf-cf", WAF_CF, WAF_CF_ARN)],
        "waf_details": {
            WAF_EDGE: {
                "Name": "rel-waf-edge",
                "Id": WAF_EDGE,
                "ARN": WAF_EDGE_ARN,
                "DefaultAction": {"Allow": {}},
                "Capacity": 1400,
                "Rules": [
                    {
                        "Name": "common",
                        "Priority": 0,
                        "OverrideAction": {"None": {}},
                        "Statement": {
                            "ManagedRuleGroupStatement": {
                                "VendorName": "AWS",
                                "Name": "AWSManagedRulesCommonRuleSet",
                            }
                        },
                    },
                    {
                        "Name": "rate-limit",
                        "Priority": 1,
                        "Action": {"Block": {}},
                        "Statement": {"RateBasedStatement": {"Limit": 2000}},
                    },
                ],
            },
            WAF_IDLE: {
                "Name": "rel-waf-idle",
                "Id": WAF_IDLE,
                "ARN": WAF_IDLE_ARN,
                "DefaultAction": {"Block": {}},
                "Rules": [],
            },
            WAF_CF: {
                "Name": "rel-waf-cf",
                "Id": WAF_CF,
                "ARN": WAF_CF_ARN,
                "DefaultAction": {"Allow": {}},
                "ManagedByFirewallManager": True,
                "Rules": [
                    {
                        "Name": "geo-block",
                        "Priority": 0,
                        "Action": {"Block": {}},
                        "Statement": {"GeoMatchStatement": {}},
                    }
                ],
            },
        },
        "waf_resources": {
            WAF_EDGE_ARN: [
                f"{stage}/{REST_API}/stages/prod",
                f"{stage}/{REST_API}/stages/beta",
                f"{stage}/{WAF_GONE_API}/stages/prod",
            ]
        },
        "web_acls": {
            PUB_ALB_ARN: {"Name": "rel-waf-edge", "ARN": WAF_EDGE_ARN, "Id": WAF_EDGE}
        },
    }
