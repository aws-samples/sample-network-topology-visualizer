"""LB 대상 선(스키마 2.7.0 · ADR 0002 §8)의 가짜 세계 — 대상 모양 전부를 한 VPC 에 둔다.

`test_lb_targets.py` 가 이 세계로 엔진을 재고, 같은 세계를 엔진에 돌린 산출물이 **그대로**
`fixtures/lb-targets.json`(렌더러 하네스 입력 · 검사 148)이다 — 손으로 쓴 fixture 가 엔진 모양에서
조용히 멀어지지 않게 `test_the_fixture_is_this_world_collected` 가 바이트를 대조한다
(`NETTOPO_REGEN_FIXTURE=1` 이면 다시 쓴다).

계정 = 마스킹된 예시 계열(`111122223333` · 불변식 ⑤). 모양:

| LB | 대상 그룹(TargetType) | 대상 | 잇는 노드 |
|---|---|---|---|
| `web-alb`(ALB · 인터넷용 · 퍼블릭 a·b) | `web-tg`(instance :80) | EC2 6대(같은 서브넷 → 화면 묶음 `EC2 ×6`) + 수집하지 않은 인스턴스 1 | `ec2_instance` ×6 · 1 = `not_collected` |
| | `api-tg`(ip :8080) | ECS 태스크 IP 2(그 그룹을 든 ECS 서비스 하나) | `ecs_service`(선 하나 · 등록 2) |
| | `fn-tg`(lambda) | 함수 ARN(별칭 하나) | `lambda_function`(엔진이 짓는다) |
| `edge-nlb`(NLB · 내부 · 앱 a·b) | `web-alb-tg`(alb :80) | `web-alb` ARN | `load_balancer`(NLB → ALB) |
| | `onprem-tg`(ip :443 · AZ `all`) | 192.168.10.5 · .6(→ VGW) · 172.16.5.9(→ TGW) | `external_ip` ×3(출구 = 주 라우트 테이블) |
| | `svc-tg`(ip) | 인터페이스 엔드포인트 ENI · RDS ENI · 내부 ALB ENI · EC2 부착 ENI · ElastiCache ENI · EFS ENI · 주인 없는 IP | `vpc_endpoint` · `rds_db_instance` · `load_balancer` · `ec2_instance` · `elasticache_cluster`(2.9.0 · #114) · `efs`(수집 안 함) · `no_eni` |
| | `svc-tg`(ip · 2.9.0) | Aurora 멤버 둘이 같은 AZ(데이터 b) — 그 VPC·AZ 의 RDS ENI | `rds_db_cluster`(멤버가 둘이라 인스턴스는 못 고르지만 둘 다 한 클러스터) |
| | `svc-tg`(ip · 2.9.0) | OpenSearch 데이터 노드 ENI(`ES <도메인>`) | `opensearch_domain` |
| | `kafka-tg`(ip :9094 · 2.9.0) | MSK 브로커 ENI 둘(데이터 a·b — `ListNodes` 의 ENI id) | `msk_cluster`(선 하나 · 등록 2) |
| `int-alb`(ALB · 내부 · 앱 a·b) | `int-tg`(instance :8080) | worker EC2 | `ec2_instance` |
| | `bridge-tg`(instance :80 · 2.8.0) | ECS 컨테이너 인스턴스(EC2 2대 · bridge 동적 포트 3) — 그 그룹을 든 ECS 서비스 `bridge-svc`(EC2 시작 유형 · 서브넷 없음 = 리전 줄) | `ecs_service`(선 하나 · 등록 3 · EC2 로 가지 않는다) |
| | `idle-tg`(ip :8080 · 2.8.0) | 등록 0 — 그 그룹을 든 ECS 서비스 `idle-svc`(원하는 수 0) | `ecs_service`(선 하나 · 등록 0 · 칩 `×0`) |
| | `standby-tg`(instance :8080 · 2.8.0) | 등록 0 — 그 그룹을 든 ASG `standby-asg`(원하는 수 0 · 앱 a·b) | `auto_scaling_group`(선 하나 · 등록 0) |
| `edge-nlb` | `svc-tg` 에 IP 하나 더(2.8.0 · P2-6) | VPC Lambda `orders-fn`(데이터 b 서브넷)의 공유 ENI(`InterfaceType` = `lambda`) | `lambda_function`(수집한 노드 · 서브넷 칸에 선다) |
| Route 53(2.8.0 · P2-4) | 공개 영역 `example.com`(별칭 `www` → `web-alb` dualstack · `old` → 수집하지 않은 LB) · 사설 영역 `corp.internal`(lbt-vpc · 별칭 `api` → `int-alb`) | `dns_alias` 둘 · `zone_association` 하나 · 못 이은 별칭 1(`unresolved_refs`) |
| `web-alb` | `k8s-web-tg`(ip :8080 · 2.8.0 · P2-5) | 파드 IP 2(ENI 없음) — 그룹 태그 `elbv2.k8s.aws/cluster` = `lbt-eks` | `eks_cluster`(선 하나 · 등록 2 · ENI 를 묻지 않는다) |
| CloudFront(2.8.0 · P2-3) | 배포 `E0LBTPUB1111`: S3 REST `lbt-static`(리전 꼴) · S3 웹사이트 `lbt-site` · 사용자 도메인 `www.example.com`(→ 별칭 → `web-alb`) · 배포 `E0LBTINT2222`: VPC 원본 → `int-alb`(도메인 = 그 DNS) · → worker EC2 · 같은 S3 `lbt-static` | `s3_bucket` 둘(같은 버킷 = 하나) · `origin` 여섯(`via` = `s3` 셋 · `dns_alias` · `vpc_origin` 둘) |
| `web-alb` | `asg-tg`(instance :8081 · 2.8.0) | ASG `web-asg` 의 인스턴스 2(퍼블릭 a·b) + 손으로 등록한 worker · `web-asg-1` 은 `web-tg` 에도 손으로 | 엔진 선 = `ec2_instance`(그대로) · 화면이 `web-asg-2` 선을 ASG 로 접는다(§17.27) · `web-asg-1` 은 그룹이 섞여 안 접힌다 |
"""

from __future__ import annotations

from typing import Any

from fakes import FakeAws

ACCOUNT = "111122223333"
REGION = "us-east-1"
VPC = "vpc-0abc1234def56001"
SN = {
    "pub-a": ("subnet-0abc1234def56011", "10.20.1.0/24", "us-east-1a"),
    "pub-b": ("subnet-0abc1234def56012", "10.20.2.0/24", "us-east-1b"),
    "app-a": ("subnet-0abc1234def56021", "10.20.11.0/24", "us-east-1a"),
    "app-b": ("subnet-0abc1234def56022", "10.20.12.0/24", "us-east-1b"),
    "data-a": ("subnet-0abc1234def56031", "10.20.21.0/24", "us-east-1a"),
    "data-b": ("subnet-0abc1234def56032", "10.20.22.0/24", "us-east-1b"),
}
IGW = "igw-0abc1234def56041"
TGW = "tgw-0abc1234def56051"
TGW_ATT = "tgw-attach-0abc1234def56052"
VGW = "vgw-0abc1234def56061"
RT_PUB = "rtb-0abc1234def56071"
RT_MAIN = "rtb-0abc1234def56072"
VPCE = "vpce-0abc1234def56301"
WEB = [f"i-0abc1234def5610{i}" for i in range(1, 7)]
WORKER = "i-0abc1234def56201"
# ECS 컨테이너 인스턴스(bridge 네트워크 · 2.8.0) — 대상 그룹을 든 서비스가 이긴다(EC2 로 가지 않는다)
ECS_HOSTS = ("i-0abc1234def56301", "i-0abc1234def56302")
# Auto Scaling 그룹의 인스턴스(2.8.0 · P1-2) — 그룹이 든 대상 그룹의 등록이 ASG 선 하나로 접힌다
ASG_INSTANCES = ("i-0abc1234def56401", "i-0abc1234def56402")
ASG = "web-asg"
EKS = "lbt-eks"
PARTNER = "444455556666"
LAMBDA_VPC_FN = "orders-fn"
ZONE_PUB = "Z0LBT1111AAAA1111"
CF_PUB = "E0LBTPUB1111"
CF_INT = "E0LBTINT2222"
ZONE_PRIV = "Z0LBT2222BBBB2222"
LAMBDA_ENI_IP = "10.20.22.40"  # 수집 범위 밖 파트너 계정(마스킹된 예시 계열)
POD_IPS = ("10.20.11.90", "10.20.12.90")
ASG_IDLE = "standby-asg"
GONE_INSTANCE = "i-0abc1234def56999"
RDS = "orders-db"
ECS_CLUSTER_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/lbt-ecs"
ECS_SERVICE_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/lbt-ecs/api-svc"
ECS_BRIDGE_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/lbt-ecs/bridge-svc"
ECS_IDLE_ARN = f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/lbt-ecs/idle-svc"
FN_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:thumbnailer"

_ELB = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}"
WEB_ALB_ARN = f"{_ELB}:loadbalancer/app/web-alb/1a2b3c4d5e6f7081"
NLB_ARN = f"{_ELB}:loadbalancer/net/edge-nlb/1a2b3c4d5e6f7082"
INT_ALB_ARN = f"{_ELB}:loadbalancer/app/int-alb/1a2b3c4d5e6f7083"
TG = {
    "web": f"{_ELB}:targetgroup/web-tg/0f1e2d3c4b5a6901",
    "api": f"{_ELB}:targetgroup/api-tg/0f1e2d3c4b5a6902",
    "fn": f"{_ELB}:targetgroup/fn-tg/0f1e2d3c4b5a6903",
    "alb": f"{_ELB}:targetgroup/web-alb-tg/0f1e2d3c4b5a6904",
    "onprem": f"{_ELB}:targetgroup/onprem-tg/0f1e2d3c4b5a6905",
    "svc": f"{_ELB}:targetgroup/svc-tg/0f1e2d3c4b5a6906",
    "int": f"{_ELB}:targetgroup/int-tg/0f1e2d3c4b5a6907",
    "bridge": f"{_ELB}:targetgroup/bridge-tg/0f1e2d3c4b5a6908",
    "idle": f"{_ELB}:targetgroup/idle-tg/0f1e2d3c4b5a6909",
    "asg": f"{_ELB}:targetgroup/asg-tg/0f1e2d3c4b5a6910",
    "standby": f"{_ELB}:targetgroup/standby-tg/0f1e2d3c4b5a6911",
    "k8s": f"{_ELB}:targetgroup/k8s-web-tg/0f1e2d3c4b5a6912",
    "kafka": f"{_ELB}:targetgroup/kafka-tg/0f1e2d3c4b5a6908",
}
EXTERNAL = ("172.16.5.9", "192.168.10.5", "192.168.10.6")
UNRESOLVED_IP = "10.20.12.99"
# #114(2.9.0) — ElastiCache 캐시 클러스터(복제 그룹의 노드 하나 · 데이터 a 서브넷). ENI 설명이 그 id 를 낱말로 든다.
CACHE = "lbt-cache-0001-001"
# 그 캐시 노드의 ENI IP — 데이터 b(2.9.0 · 배치 A 의 CloudFront VPC 원본 선이 데이터 a 줄을 지나 그 자리를 비운다 )
CACHE_NODE_IP = "10.20.22.30"
# 수집하지 않는 서비스(EFS 마운트 대상) — 「<서비스> — 수집하지 않는 서비스」 글자를 재는 짝(그 전엔 ElastiCache 가 이 자리였다)
EFS_IP = "10.20.22.45"
# #114(2.9.0) — Aurora 클러스터(멤버 둘이 같은 AZ = 데이터 b). 그 AZ 의 RDS ENI 는 어느 멤버 것인지 모르지만 클러스터는 안다
AURORA = "lbt-aurora"
AURORA_MEMBERS = ("lbt-aurora-1", "lbt-aurora-2")
AURORA_IP = "10.20.22.50"
# #114(2.9.0) — OpenSearch VPC 도메인(데이터 b 한 서브넷 · 노드 하나)
SEARCH = "lbt-search"
SEARCH_IP = (
    "10.20.22.41"  # 데이터 b(배치 A 의 CloudFront VPC 원본 선이 데이터 a 줄을 지난다)
)
# #114(2.9.0) — MSK 프로비저닝 클러스터(브로커 둘 · 데이터 a·b). LB 는 브로커 IP 둘을 한 대상 그룹으로 든다
KAFKA = "lbt-kafka"
KAFKA_ARN = f"arn:aws:kafka:{REGION}:{ACCOUNT}:cluster/{KAFKA}/0f1e2d3c-4b5a-6901-2345-6789abcdef01-1"
KAFKA_BROKERS = (
    ("eni-0abc1234def57011", "10.20.21.60", "data-a"),
    ("eni-0abc1234def57012", "10.20.22.60", "data-b"),
)


def _tags(name: str) -> list[dict[str, str]]:
    return [{"Key": "Name", "Value": name}]


def _lb(
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
        "SecurityGroups": [] if kind == "network" else ["sg-0abc1234def56501"],
    }


def _cf(dist: str, tag: str, origins: list[dict[str, Any]]) -> dict[str, Any]:
    """CloudFront 배포(2.8.0 · P2-3) — 원본 꼴만 다르다."""
    return {
        "Id": dist,
        "ARN": f"arn:aws:cloudfront::{ACCOUNT}:distribution/{dist}",
        "DomainName": f"d{tag}lbt0000000.cloudfront.net",
        "Status": "Deployed",
        "Enabled": True,
        "IsIPV6Enabled": True,
        "HttpVersion": "http2",
        "PriceClass": "PriceClass_All",
        "WebACLId": "",
        "Aliases": {"Quantity": 0},
        "Origins": {"Quantity": len(origins), "Items": origins},
    }


def _tg(key: str, name: str, kind: str, port: int | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "TargetGroupName": name,
        "TargetGroupArn": TG[key],
        "TargetType": kind,
    }
    if port is not None:
        out["Port"] = port
    return out


# 헬스 체크를 끈 대상 그룹(Lambda 의 기본값)에 AWS 가 주는 대상 헬스 — State `unavailable` + 이 Reason(#137 · 관측 1.15.0)
HEALTH_CHECK_OFF = {
    "State": "unavailable",
    "Reason": "Target.HealthCheckDisabled",
    "Description": "Health checks disabled",
}


def _target(
    tid: str, port: int | None = None, zone: str | None = None, state: str = "healthy"
) -> dict[str, Any]:
    target: dict[str, Any] = {"Id": tid}
    if port is not None:
        target["Port"] = port
    if zone is not None:
        target["AvailabilityZone"] = zone
    health = dict(HEALTH_CHECK_OFF) if state == "health_check_off" else {"State": state}
    return {"Target": target, "TargetHealth": health}


def _eni(eid: str, ip: str, subnet: str, **extra: Any) -> dict[str, Any]:
    sid, _, zone = SN[subnet]
    out: dict[str, Any] = {
        "NetworkInterfaceId": eid,
        "VpcId": VPC,
        "SubnetId": sid,
        "AvailabilityZone": zone,
        "PrivateIpAddress": ip,
        "PrivateIpAddresses": [{"PrivateIpAddress": ip, "Primary": True}],
        "InterfaceType": extra.pop("InterfaceType", "interface"),
        "RequesterId": extra.pop("RequesterId", ""),
        "Description": extra.pop("Description", ""),
        "TagSet": extra.pop("TagSet", []),
    }
    out.update(extra)
    return out


def world(
    *,
    health: dict[str, str] | None = None,
    followup_errors: dict[tuple[str, str], Exception] | None = None,
    errors: dict[str, Exception] | None = None,
) -> FakeAws:
    """`health` = {대상 그룹 키: 헬스} 로 그 그룹의 모든 대상 헬스를 바꾼다(기본 healthy · `fn` 만 기본 헬스 체크 꺼짐).

    `health_check_off` = State `unavailable` + Reason `Target.HealthCheckDisabled`(`HEALTH_CHECK_OFF`). Lambda 대상 그룹은
    헬스 체크가 기본으로 꺼져 있고, 그 대상은 AWS 문서상 이 모양으로 답한다(ELB `TargetHealth` · #137).
    """
    hs = {"fn": "health_check_off", **(health or {})}

    def h(key: str) -> str:
        return hs.get(key, "healthy")

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
            "Tags": _tags(f"lbt-{name}"),
        }
        for name, (sid, cidr, zone) in SN.items()
    ]
    route_tables = [
        {
            "RouteTableId": RT_PUB,
            "VpcId": VPC,
            "Associations": [
                {
                    "RouteTableAssociationId": f"rtbassoc-0pub{i}",
                    "SubnetId": SN[z][0],
                    "AssociationState": {"State": "associated"},
                }
                for i, z in enumerate(("pub-a", "pub-b"))
            ],
            "Routes": [
                {
                    "DestinationCidrBlock": "10.20.0.0/16",
                    "GatewayId": "local",
                    "State": "active",
                },
                {
                    "DestinationCidrBlock": "0.0.0.0/0",
                    "GatewayId": IGW,
                    "State": "active",
                },
            ],
            "Tags": _tags("lbt-public-rt"),
        },
        {
            # 주 라우트 테이블 — 앱·데이터 서브넷이 연관 선 없이 쓴다(→ NLB 의 외부 IP 출구)
            "RouteTableId": RT_MAIN,
            "VpcId": VPC,
            "Associations": [
                {"RouteTableAssociationId": "rtbassoc-0main", "Main": True}
            ],
            "Routes": [
                {
                    "DestinationCidrBlock": "10.20.0.0/16",
                    "GatewayId": "local",
                    "State": "active",
                },
                {
                    "DestinationCidrBlock": "172.16.0.0/12",
                    "TransitGatewayId": TGW,
                    "State": "active",
                },
                {
                    "DestinationCidrBlock": "192.168.0.0/16",
                    "GatewayId": VGW,
                    "State": "active",
                    "Origin": "EnableVgwRoutePropagation",
                },
            ],
            "Tags": _tags("lbt-private-rt"),
        },
    ]
    reservations = [
        {
            "OwnerId": ACCOUNT,
            "Instances": [
                {
                    "InstanceId": iid,
                    "InstanceType": "t3.small" if iid != WORKER else "c6i.large",
                    "State": {"Name": "running"},
                    "Placement": {"AvailabilityZone": SN[subnet][2]},
                    "SubnetId": SN[subnet][0],
                    "VpcId": VPC,
                    "PrivateIpAddress": ip,
                    "SecurityGroups": [{"GroupId": "sg-0abc1234def56501"}],
                    "Tags": _tags(name),
                }
            ],
        }
        for iid, subnet, ip, name in [
            *[
                (w, "app-a", f"10.20.11.{11 + i}", f"web-{i + 1}")
                for i, w in enumerate(WEB)
            ],
            (WORKER, "app-b", "10.20.12.60", "worker"),
            *[
                (hst, "app-b", f"10.20.12.{70 + i}", f"ecs-host-{i + 1}")
                for i, hst in enumerate(ECS_HOSTS)
            ],
            (ASG_INSTANCES[0], "pub-a", "10.20.1.21", "web-asg-1"),
            (ASG_INSTANCES[1], "pub-b", "10.20.2.21", "web-asg-2"),
        ]
    ]
    target_groups = {
        WEB_ALB_ARN: [
            _tg("web", "web-tg", "instance", 80),
            _tg("api", "api-tg", "ip", 8080),
            _tg("fn", "fn-tg", "lambda", None),
            _tg("asg", "asg-tg", "instance", 8081),
            _tg("k8s", "k8s-web-tg", "ip", 8080),
        ],
        NLB_ARN: [
            _tg("alb", "web-alb-tg", "alb", 80),
            _tg("onprem", "onprem-tg", "ip", 443),
            _tg("svc", "svc-tg", "ip", 443),
            _tg("kafka", "kafka-tg", "ip", 9094),
        ],
        INT_ALB_ARN: [
            _tg("int", "int-tg", "instance", 8080),
            _tg("bridge", "bridge-tg", "instance", 80),
            _tg("idle", "idle-tg", "ip", 8080),
            _tg("standby", "standby-tg", "instance", 8080),
        ],
    }
    target_health = {
        TG["web"]: [_target(w, 80, state=h("web")) for w in reversed(WEB)]
        + [_target(GONE_INSTANCE, 80, state=h("web"))]
        # ASG 인스턴스 하나를 ASG 가 안 든 그룹에도 손으로 등록(2.8.0) — 그 선은 ASG 로 접히지 않는다(그룹이 전부 ASG 것이 아니다)
        + [_target(ASG_INSTANCES[0], 80, state=h("web"))],
        TG["api"]: [
            _target("10.20.12.51", 8080, "us-east-1b", h("api")),
            _target("10.20.11.50", 8080, "us-east-1a", h("api")),
        ],
        TG["fn"]: [_target(f"{FN_ARN}:live", state=h("fn"))],
        TG["alb"]: [_target(WEB_ALB_ARN, 80, state=h("alb"))],
        TG["onprem"]: [_target(ip, 443, "all", h("onprem")) for ip in EXTERNAL],
        TG["svc"]: [
            _target("10.20.11.70", 443, "us-east-1a", h("svc")),
            _target("10.20.21.15", 5432, "us-east-1a", h("svc")),
            _target(CACHE_NODE_IP, 6379, "us-east-1b", h("svc")),
            _target(EFS_IP, 2049, "us-east-1b", h("svc")),
            _target(AURORA_IP, 5432, "us-east-1b", h("svc")),
            _target(SEARCH_IP, 443, "us-east-1b", h("svc")),
            _target("10.20.11.80", 80, "us-east-1a", h("svc")),
            _target("10.20.12.60", 9000, "us-east-1b", h("svc")),
            _target(UNRESOLVED_IP, 443, "us-east-1b", h("svc")),
            _target(LAMBDA_ENI_IP, 8443, "us-east-1b", h("svc")),
        ],
        TG["int"]: [_target(WORKER, 8080, state=h("int"))],
        # bridge 동적 포트 — 같은 호스트에 태스크 둘(포트가 다르다) · 다른 호스트에 하나
        TG["bridge"]: [
            _target(ECS_HOSTS[0], 32768, state=h("bridge")),
            _target(ECS_HOSTS[0], 32769, state=h("bridge")),
            _target(ECS_HOSTS[1], 32768, state=h("bridge")),
        ],
        TG["idle"]: [],
        # ASG 가 등록한 인스턴스 둘 + 손으로 등록한 worker(그 ASG 의 인스턴스가 아니다 → 제 EC2 선)
        TG["asg"]: [
            _target(ASG_INSTANCES[1], 8081, state=h("asg")),
            _target(WORKER, 8081, state=h("asg")),
            _target(ASG_INSTANCES[0], 8081, state=h("asg")),
        ],
        TG["standby"]: [],
        # AWS LB Controller 가 등록한 파드 IP — ENI 가 이 세계에 없다(그룹 태그가 클러스터를 말하므로 ENI 를 묻지 않아도 된다)
        TG["k8s"]: [
            _target(
                ip,
                8080,
                SN["app-a" if ip.startswith("10.20.11") else "app-b"][2],
                h("k8s"),
            )
            for ip in POD_IPS
        ],
        TG["kafka"]: [
            _target(ip, 9094, SN[sn][2], h("kafka")) for _, ip, sn in KAFKA_BROKERS
        ],
    }
    enis = [
        # ECS 태스크 ENI — 대상 그룹을 든 서비스가 먼저 이긴다(ENI 는 보지 않는다)
        _eni(
            "eni-0abc1234def57001",
            "10.20.11.50",
            "app-a",
            Description="arn:aws:ecs:us-east-1:111122223333:attachment/0f0e",
            InterfaceType="interface",
        ),
        _eni(
            "eni-0abc1234def57002",
            "10.20.12.51",
            "app-b",
            Description="arn:aws:ecs:us-east-1:111122223333:attachment/0f0f",
            InterfaceType="interface",
        ),
        _eni(
            "eni-0abc1234def57003",
            "10.20.11.70",
            "app-a",
            Description=f"VPC Endpoint Interface {VPCE}",
            InterfaceType="vpc_endpoint",
            RequesterId="amazon-vpce",
        ),
        _eni(
            "eni-0abc1234def57004",
            "10.20.21.15",
            "data-a",
            Description="RDSNetworkInterface",
            RequesterId="amazon-rds",
        ),
        _eni(
            "eni-0abc1234def57005",
            CACHE_NODE_IP,
            "data-b",
            Description=f"ElastiCache {CACHE}",
            RequesterId="amazon-elasticache",
        ),
        _eni(
            "eni-0abc1234def57208",
            EFS_IP,
            "data-b",
            Description="EFS mount target for fs-0abc1234def56701 (fsmt-0abc1234def56702)",
            RequesterId="",
        ),
        # MSK 브로커 ENI — 설명 글자에 클러스터 이름이 없어도 ENI id 로 잇는다(`ListNodes`)
        *[
            _eni(
                eid,
                ip,
                sn,
                Description="[DO NOT DELETE] ENI for Kafka broker",
                RequesterId="",
            )
            for eid, ip, sn in KAFKA_BROKERS
        ],
        _eni(
            "eni-0abc1234def57010",
            SEARCH_IP,
            "data-b",
            Description=f"ES {SEARCH}",
            RequesterId="amazon-elasticsearch",
        ),
        _eni(
            "eni-0abc1234def57009",
            AURORA_IP,
            "data-b",
            Description="RDSNetworkInterface",
            RequesterId="amazon-rds",
        ),
        _eni(
            "eni-0abc1234def57006",
            "10.20.11.80",
            "app-a",
            Description="ELB app/int-alb/1a2b3c4d5e6f7083",
            RequesterId="amazon-elb",
        ),
        _eni(
            "eni-0abc1234def57007",
            "10.20.12.60",
            "app-b",
            Attachment={"InstanceId": WORKER, "InstanceOwnerId": ACCOUNT},
        ),
        # VPC Lambda 의 공유 ENI(2.8.0 · P2-6) — 서브넷·보안 그룹이 맞는 수집한 함수가 하나라 그 함수다
        _eni(
            "eni-0abc1234def57008",
            LAMBDA_ENI_IP,
            "data-b",
            Description=f"AWS Lambda VPC ENI-{LAMBDA_VPC_FN}-0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b",
            InterfaceType="lambda",
            RequesterId="AROAEXAMPLE:orders-fn",
            Groups=[{"GroupId": "sg-0abc1234def56501"}],
        ),
    ]
    ecs = {
        ECS_SERVICE_ARN: {
            "serviceArn": ECS_SERVICE_ARN,
            "serviceName": "api-svc",
            "clusterArn": ECS_CLUSTER_ARN,
            "launchType": "FARGATE",
            "status": "ACTIVE",
            "runningCount": 2,
            "desiredCount": 2,
            "loadBalancers": [
                {
                    "targetGroupArn": TG["api"],
                    "containerName": "api",
                    "containerPort": 8080,
                }
            ],
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": [SN["app-b"][0], SN["app-a"][0]],
                    "securityGroups": ["sg-0abc1234def56501"],
                    "assignPublicIp": "DISABLED",
                }
            },
        },
        # EC2 시작 유형 · bridge 네트워크(awsvpc 설정 없음) — 대상 = 컨테이너 인스턴스(`instance` 대상 그룹)
        ECS_BRIDGE_ARN: {
            "serviceArn": ECS_BRIDGE_ARN,
            "serviceName": "bridge-svc",
            "clusterArn": ECS_CLUSTER_ARN,
            "launchType": "EC2",
            "status": "ACTIVE",
            "runningCount": 3,
            "desiredCount": 3,
            "loadBalancers": [
                {
                    "targetGroupArn": TG["bridge"],
                    "containerName": "web",
                    "containerPort": 80,
                }
            ],
        },
        # 원하는 수 0 — 등록이 없어도 「이 LB 뒤의 서비스」는 구성이다(선 · 등록 0)
        ECS_IDLE_ARN: {
            "serviceArn": ECS_IDLE_ARN,
            "serviceName": "idle-svc",
            "clusterArn": ECS_CLUSTER_ARN,
            "launchType": "FARGATE",
            "status": "ACTIVE",
            "runningCount": 0,
            "desiredCount": 0,
            "loadBalancers": [
                {
                    "targetGroupArn": TG["idle"],
                    "containerName": "api",
                    "containerPort": 8080,
                }
            ],
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": [SN["app-a"][0], SN["app-b"][0]],
                    "securityGroups": ["sg-0abc1234def56501"],
                    "assignPublicIp": "DISABLED",
                }
            },
        },
    }
    return FakeAws.empty(
        vpcs=[
            {
                "VpcId": VPC,
                "CidrBlock": "10.20.0.0/16",
                "State": "available",
                "IsDefault": False,
                "Tags": _tags("lbt-vpc"),
            }
        ],
        subnets=subnets,
        route_tables=route_tables,
        internet_gateways=[
            {
                "InternetGatewayId": IGW,
                "Attachments": [{"VpcId": VPC, "State": "available"}],
                "Tags": _tags("lbt-igw"),
            }
        ],
        transit_gateways=[
            {
                "TransitGatewayId": TGW,
                "TransitGatewayArn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:transit-gateway/{TGW}",
                "OwnerId": ACCOUNT,
                "State": "available",
                "Options": {"AmazonSideAsn": 64512},
                "Tags": _tags("lbt-tgw"),
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
            }
        ],
        vpn_gateways=[
            {
                "VpnGatewayId": VGW,
                "State": "available",
                "Type": "ipsec.1",
                "AmazonSideAsn": 64513,
                "VpcAttachments": [{"VpcId": VPC, "State": "attached"}],
                "Tags": _tags("lbt-vgw"),
            }
        ],
        vpc_endpoints=[
            {
                "VpcEndpointId": VPCE,
                "VpcEndpointType": "Interface",
                "VpcId": VPC,
                "ServiceName": "com.amazonaws.vpce.us-east-1.vpce-svc-0abc1234def56aaa",
                "State": "available",
                "RouteTableIds": [],
                "SubnetIds": [SN["app-a"][0]],
                "PrivateDnsEnabled": False,
                "Tags": _tags("partner-api"),
            }
        ],
        load_balancers=[
            _lb(
                "web-alb",
                WEB_ALB_ARN,
                "application",
                "internet-facing",
                ("pub-a", "pub-b"),
            ),
            _lb("edge-nlb", NLB_ARN, "network", "internal", ("app-a", "app-b")),
            _lb("int-alb", INT_ALB_ARN, "application", "internal", ("app-a", "app-b")),
        ],
        listeners={
            WEB_ALB_ARN: [
                {"ListenerArn": f"{WEB_ALB_ARN}/l/1", "Protocol": "HTTPS", "Port": 443}
            ],
            NLB_ARN: [
                {"ListenerArn": f"{NLB_ARN}/l/1", "Protocol": "TCP", "Port": 443}
            ],
            INT_ALB_ARN: [
                {"ListenerArn": f"{INT_ALB_ARN}/l/1", "Protocol": "HTTP", "Port": 80}
            ],
        },
        target_groups=target_groups,
        target_health=target_health,
        network_interfaces=enis,
        instances=reservations,
        rds_instances=[
            {
                "DBInstanceIdentifier": RDS,
                "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:{RDS}",
                "DBInstanceClass": "db.t3.medium",
                "Engine": "postgres",
                "DBInstanceStatus": "available",
                "MultiAZ": False,
                "AvailabilityZone": "us-east-1a",
                "PubliclyAccessible": False,
                "VpcSecurityGroups": [
                    {"VpcSecurityGroupId": "sg-0abc1234def56501", "Status": "active"}
                ],
                "DBSubnetGroup": {
                    "DBSubnetGroupName": "lbt-db",
                    "VpcId": VPC,
                    "Subnets": [
                        {
                            "SubnetIdentifier": SN["data-a"][0],
                            "SubnetAvailabilityZone": {"Name": "us-east-1a"},
                        },
                        {
                            "SubnetIdentifier": SN["data-b"][0],
                            "SubnetAvailabilityZone": {"Name": "us-east-1b"},
                        },
                    ],
                },
                "TagList": [],
            },
            *[
                {
                    "DBInstanceIdentifier": m,
                    "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:{m}",
                    "DBInstanceClass": "db.t4g.medium",
                    "Engine": "aurora-postgresql",
                    "DBInstanceStatus": "available",
                    "DBClusterIdentifier": AURORA,
                    "MultiAZ": False,
                    "AvailabilityZone": "us-east-1b",
                    "PubliclyAccessible": False,
                    "VpcSecurityGroups": [
                        {
                            "VpcSecurityGroupId": "sg-0abc1234def56501",
                            "Status": "active",
                        }
                    ],
                    "DBSubnetGroup": {
                        "DBSubnetGroupName": "lbt-db",
                        "VpcId": VPC,
                        "Subnets": [
                            {
                                "SubnetIdentifier": SN["data-a"][0],
                                "SubnetAvailabilityZone": {"Name": "us-east-1a"},
                            },
                            {
                                "SubnetIdentifier": SN["data-b"][0],
                                "SubnetAvailabilityZone": {"Name": "us-east-1b"},
                            },
                        ],
                    },
                    "TagList": [],
                }
                for m in AURORA_MEMBERS
            ],
        ],
        rds_clusters=[
            {
                "DBClusterIdentifier": AURORA,
                "DBClusterArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:{AURORA}",
                "Engine": "aurora-postgresql",
                "EngineVersion": "16.4",
                "EngineMode": "provisioned",
                "Status": "available",
                "Endpoint": f"{AURORA}.cluster-x.{REGION}.rds.amazonaws.com",
                "ReaderEndpoint": f"{AURORA}.cluster-ro-x.{REGION}.rds.amazonaws.com",
                "Port": 5432,
                "MultiAZ": False,
                "AvailabilityZones": ["us-east-1a", "us-east-1b"],
                "DBClusterMembers": [
                    {"DBInstanceIdentifier": m, "IsClusterWriter": i == 0}
                    for i, m in enumerate(AURORA_MEMBERS)
                ],
                "DBSubnetGroup": "lbt-db",
                "VpcSecurityGroups": [
                    {"VpcSecurityGroupId": "sg-0abc1234def56501", "Status": "active"}
                ],
                "StorageEncrypted": True,
            }
        ],
        db_subnet_groups=[
            {
                "DBSubnetGroupName": "lbt-db",
                "VpcId": VPC,
                "Subnets": [
                    {
                        "SubnetIdentifier": SN["data-a"][0],
                        "SubnetAvailabilityZone": {"Name": "us-east-1a"},
                    },
                    {
                        "SubnetIdentifier": SN["data-b"][0],
                        "SubnetAvailabilityZone": {"Name": "us-east-1b"},
                    },
                ],
            }
        ],
        auto_scaling_groups=[
            {
                "AutoScalingGroupName": ASG,
                "AutoScalingGroupARN": f"arn:aws:autoscaling:{REGION}:{ACCOUNT}:autoScalingGroup:0f0e:autoScalingGroupName/{ASG}",
                "MinSize": 2,
                "MaxSize": 4,
                "DesiredCapacity": 2,
                "AvailabilityZones": ["us-east-1b", "us-east-1a"],
                "VPCZoneIdentifier": f"{SN['pub-b'][0]},{SN['pub-a'][0]}",
                "TargetGroupARNs": [TG["asg"]],
                "Instances": [
                    {
                        "InstanceId": i,
                        "LifecycleState": "InService",
                        "HealthStatus": "Healthy",
                    }
                    for i in reversed(ASG_INSTANCES)
                ],
            },
            {
                "AutoScalingGroupName": ASG_IDLE,
                "AutoScalingGroupARN": f"arn:aws:autoscaling:{REGION}:{ACCOUNT}:autoScalingGroup:0f0f:autoScalingGroupName/{ASG_IDLE}",
                "MinSize": 0,
                "MaxSize": 2,
                "DesiredCapacity": 0,
                "AvailabilityZones": ["us-east-1a", "us-east-1b"],
                "VPCZoneIdentifier": f"{SN['app-a'][0]},{SN['app-b'][0]}",
                "TargetGroupARNs": [TG["standby"]],
                "Instances": [],
            },
        ],
        eks_clusters=[EKS],
        eks_details={
            EKS: {
                "name": EKS,
                "arn": f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{EKS}",
                "version": "1.30",
                "resourcesVpcConfig": {
                    "vpcId": VPC,
                    "subnetIds": [SN["app-a"][0], SN["app-b"][0]],
                    "securityGroupIds": [],
                    "clusterSecurityGroupId": "sg-0abc1234def56502",
                    "endpointPublicAccess": False,
                    "endpointPrivateAccess": True,
                    "publicAccessCidrs": [],
                },
                "status": "ACTIVE",
            }
        },
        # 파트너 서비스의 주인 계정(2.8.0 · P2-1) — 수집 범위 밖 계정이라 제공자 NLB 까지는 못 잇고 「어느 계정의 서비스인가」만 안다
        consumer_services={
            "com.amazonaws.vpce.us-east-1.vpce-svc-0abc1234def56aaa": PARTNER
        },
        tg_tags={
            TG["k8s"]: {
                "elbv2.k8s.aws/cluster": EKS,
                "elbv2.k8s.aws/resource": "default/web",
            }
        },
        hosted_zones=[
            {
                "Id": f"/hostedzone/{ZONE_PUB}",
                "Name": "example.com.",
                "Config": {"PrivateZone": False},
                "ResourceRecordSetCount": 4,
            },
            {
                "Id": f"/hostedzone/{ZONE_PRIV}",
                "Name": "corp.internal.",
                "Config": {"PrivateZone": True},
                "ResourceRecordSetCount": 3,
            },
        ],
        record_sets={
            f"/hostedzone/{ZONE_PUB}": [
                {
                    "Name": "www.example.com.",
                    "Type": "A",
                    "AliasTarget": {
                        "DNSName": f"dualstack.web-alb-1234567890.{REGION}.elb.amazonaws.com.",
                        "EvaluateTargetHealth": True,
                    },
                },
                {
                    "Name": "www.example.com.",
                    "Type": "AAAA",
                    "AliasTarget": {
                        "DNSName": f"dualstack.web-alb-1234567890.{REGION}.elb.amazonaws.com.",
                        "EvaluateTargetHealth": True,
                    },
                },
                {
                    "Name": "old.example.com.",
                    "Type": "A",
                    "AliasTarget": {
                        "DNSName": f"retired-alb-555.{REGION}.elb.amazonaws.com.",
                        "EvaluateTargetHealth": False,
                    },
                },
            ],
            f"/hostedzone/{ZONE_PRIV}": [
                {
                    "Name": "api.corp.internal.",
                    "Type": "A",
                    "AliasTarget": {
                        "DNSName": f"int-alb-1234567890.{REGION}.elb.amazonaws.com.",
                        "EvaluateTargetHealth": False,
                    },
                },
            ],
        },
        zone_details={
            f"/hostedzone/{ZONE_PRIV}": {"VPCs": [{"VPCRegion": REGION, "VPCId": VPC}]}
        },
        cloudfront_distributions=[
            _cf(
                CF_PUB,
                "pub",
                [
                    {
                        "Id": "static",
                        "DomainName": f"lbt-static.s3.{REGION}.amazonaws.com",
                    },
                    {
                        "Id": "site",
                        "DomainName": f"lbt-site.s3-website-{REGION}.amazonaws.com",
                    },
                    {"Id": "web", "DomainName": "www.example.com"},
                ],
            ),
            _cf(
                CF_INT,
                "int",
                [
                    {
                        "Id": "int",
                        "DomainName": f"int-alb-1234567890.{REGION}.elb.amazonaws.com",
                        "VpcOriginConfig": {"VpcOriginId": "vo-int"},
                    },
                    {
                        "Id": "worker",
                        "DomainName": "ip-10-20-12-10.ec2.internal",
                        "VpcOriginConfig": {"VpcOriginId": "vo-worker"},
                    },
                    {
                        "Id": "static",
                        "DomainName": f"lbt-static.s3.{REGION}.amazonaws.com",
                    },
                ],
            ),
        ],
        vpc_origins=[
            {"Id": "vo-int", "OriginEndpointArn": INT_ALB_ARN},
            {
                "Id": "vo-worker",
                "OriginEndpointArn": f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/{WORKER}",
            },
        ],
        lambda_functions=[
            {
                "FunctionName": LAMBDA_VPC_FN,
                "FunctionArn": f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{LAMBDA_VPC_FN}",
                "Runtime": "python3.12",
                "VpcConfig": {
                    "VpcId": VPC,
                    "SubnetIds": [SN["data-b"][0]],
                    "SecurityGroupIds": ["sg-0abc1234def56501"],
                },
            }
        ],
        msk_clusters=[
            {
                "ClusterName": KAFKA,
                "ClusterArn": KAFKA_ARN,
                "ClusterType": "PROVISIONED",
                "State": "ACTIVE",
                "Provisioned": {
                    "BrokerNodeGroupInfo": {
                        "InstanceType": "kafka.t3.small",
                        "ClientSubnets": [SN["data-a"][0], SN["data-b"][0]],
                        "SecurityGroups": ["sg-0abc1234def56501"],
                    },
                    "CurrentBrokerSoftwareInfo": {"KafkaVersion": "3.6.0"},
                    "NumberOfBrokerNodes": 2,
                },
                "Tags": {},
            }
        ],
        msk_nodes={
            KAFKA_ARN: [
                {
                    "NodeType": "BROKER",
                    "BrokerNodeInfo": {
                        "BrokerId": float(i + 1),
                        "AttachedENIId": eid,
                        "ClientSubnet": SN[sn][0],
                        "ClientVpcIpAddress": ip,
                    },
                }
                for i, (eid, ip, sn) in enumerate(KAFKA_BROKERS)
            ]
        },
        opensearch_domains=[{"DomainName": SEARCH, "EngineType": "OpenSearch"}],
        opensearch_details={
            SEARCH: {
                "DomainName": SEARCH,
                "ARN": f"arn:aws:es:{REGION}:{ACCOUNT}:domain/{SEARCH}",
                "Created": True,
                "DomainProcessingStatus": "Active",
                "EngineVersion": "OpenSearch_2.13",
                "Endpoints": {"vpc": f"vpc-{SEARCH}-x.{REGION}.es.amazonaws.com"},
                "ClusterConfig": {
                    "InstanceType": "t3.small.search",
                    "InstanceCount": 1,
                },
                "VPCOptions": {
                    "VPCId": VPC,
                    "SubnetIds": [SN["data-b"][0]],
                    "AvailabilityZones": ["us-east-1b"],
                    "SecurityGroupIds": ["sg-0abc1234def56501"],
                },
            }
        },
        elasticache_clusters=[
            {
                "CacheClusterId": CACHE,
                "ARN": f"arn:aws:elasticache:{REGION}:{ACCOUNT}:cluster:{CACHE}",
                "Engine": "valkey",
                "EngineVersion": "7.2",
                "CacheNodeType": "cache.t4g.micro",
                "NumCacheNodes": 1,
                "CacheClusterStatus": "available",
                "PreferredAvailabilityZone": "us-east-1b",
                "CacheSubnetGroupName": "lbt-cache",
                "ReplicationGroupId": "lbt-cache",
                "SecurityGroups": [
                    {"SecurityGroupId": "sg-0abc1234def56501", "Status": "active"}
                ],
                "CacheNodes": [
                    {
                        "CacheNodeId": "0001",
                        "CustomerAvailabilityZone": "us-east-1b",
                        "Endpoint": {
                            "Address": f"{CACHE}.cache.amazonaws.com",
                            "Port": 6379,
                        },
                    }
                ],
            }
        ],
        cache_subnet_groups=[
            {
                "CacheSubnetGroupName": "lbt-cache",
                "VpcId": VPC,
                "Subnets": [
                    {
                        "SubnetIdentifier": SN["data-a"][0],
                        "SubnetAvailabilityZone": {"Name": "us-east-1a"},
                    },
                    {
                        "SubnetIdentifier": SN["data-b"][0],
                        "SubnetAvailabilityZone": {"Name": "us-east-1b"},
                    },
                ],
            }
        ],
        cache_replication_groups=[
            {
                "ReplicationGroupId": "lbt-cache",
                "MemberClusters": [CACHE],
                "ClusterEnabled": True,
                "MultiAZ": "disabled",
                "AutomaticFailover": "enabled",
            }
        ],
        ecs_clusters=[ECS_CLUSTER_ARN],
        ecs_services={ECS_CLUSTER_ARN: [ECS_SERVICE_ARN, ECS_BRIDGE_ARN, ECS_IDLE_ARN]},
        ecs_details=ecs,
        web_acls={},
        followup_errors=followup_errors,
        errors=errors,
    )
