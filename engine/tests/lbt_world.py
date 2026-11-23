"""A fake world for load balancer target edges (schema 2.7.0), holding every
target shape in one VPC.

`test_lb_targets.py` measures the engine against this world, and running the
engine over the same world produces `fixtures/lb-targets.json` byte for byte.
`test_the_fixture_is_this_world_collected` compares those bytes so that a
hand-written fixture cannot drift away from the engine's shape unnoticed; it
rewrites the fixture when `NETTOPO_REGEN_FIXTURE=1` is set.

The account numbers come from the reserved example range (`111122223333`).
The shape is:

| Load balancer | Target group (TargetType) | Targets | Nodes it links |
|---|---|---|---|
| `web-alb` (ALB, internet-facing, public a and b) | `web-tg` (instance :80) | Six EC2 instances in the same subnet, which the screen groups as `EC2 ×6`, plus one instance that was not collected | `ec2_instance` ×6, and one `not_collected` |
| | `api-tg` (ip :8080) | Two ECS task IPs, from the one ECS service that holds the group | `ecs_service`, a single edge with two registrations |
| | `fn-tg` (lambda) | A function ARN with one alias | `lambda_function`, which the engine synthesizes |
| `edge-nlb` (NLB, internal, app a and b) | `web-alb-tg` (alb :80) | The `web-alb` ARN | `load_balancer`, NLB to ALB |
| | `onprem-tg` (ip :443, AZ `all`) | 192.168.10.5 and .6, which exit through the VGW, and 172.16.5.9, which exits through the TGW | `external_ip` ×3, with the exit taken from the main route table |
| | `svc-tg` (ip) | An interface endpoint ENI, an RDS ENI, an internal ALB ENI, an ENI attached to EC2, an ElastiCache ENI, an EFS ENI, and an IP with no owner | `vpc_endpoint`, `rds_db_instance`, `load_balancer`, `ec2_instance`, `elasticache_cluster` (2.9.0), `efs` (not collected), and `no_eni` |
| | `svc-tg` (ip, 2.9.0) | Two Aurora members in the same AZ (data b), reached through the RDS ENI in that VPC and AZ | `rds_db_cluster`: with two members the instance cannot be chosen, but both belong to one cluster |
| | `svc-tg` (ip, 2.9.0) | An OpenSearch data node ENI (`ES <domain>`) | `opensearch_domain` |
| | `kafka-tg` (ip :9094, 2.9.0) | Two MSK broker ENIs in data a and b, by the ENI ids from `ListNodes` | `msk_cluster`, a single edge with two registrations |
| `int-alb` (ALB, internal, app a and b) | `int-tg` (instance :8080) | The worker EC2 instance | `ec2_instance` |
| | `bridge-tg` (instance :80, 2.8.0) | Two ECS container instances with three dynamic bridge ports, held by the ECS service `bridge-svc`, which uses the EC2 launch type and has no subnet, so it sits on the Region row | `ecs_service`, a single edge with three registrations, and no edge to the EC2 instances |
| | `idle-tg` (ip :8080, 2.8.0) | No registrations, held by the ECS service `idle-svc` with a desired count of 0 | `ecs_service`, a single edge with zero registrations and the chip `×0` |
| | `standby-tg` (instance :8080, 2.8.0) | No registrations, held by the ASG `standby-asg` with a desired count of 0, in app a and b | `auto_scaling_group`, a single edge with zero registrations |
| `edge-nlb` | One more IP in `svc-tg` (2.8.0) | The shared ENI of the VPC Lambda `orders-fn` in a data b subnet, whose `InterfaceType` is `lambda` | `lambda_function`, a collected node that stands in the subnet cell |
| Route 53 (2.8.0) | The public zone `example.com`, where the alias `www` points at `web-alb` dualstack and `old` points at a load balancer that was not collected, and the private zone `corp.internal` on lbt-vpc, where the alias `api` points at `int-alb` | Two `dns_alias` edges, one `zone_association`, and one alias that could not be resolved (`unresolved_refs`) |
| `web-alb` | `k8s-web-tg` (ip :8080, 2.8.0) | Two pod IPs with no ENI, where the group tag `elbv2.k8s.aws/cluster` is `lbt-eks` | `eks_cluster`, a single edge with two registrations, without asking about the ENIs |
| CloudFront (2.8.0) | Distribution `E0LBTPUB1111` with the S3 REST origin `lbt-static` in regional form, the S3 website origin `lbt-site`, and the custom domain `www.example.com`, which resolves through the alias to `web-alb`; distribution `E0LBTINT2222` with a VPC origin to `int-alb` by its DNS name, another to the worker EC2 instance, and the same S3 bucket `lbt-static` | Two `s3_bucket` nodes, which collapse to one because it is the same bucket, and six `origin` edges: three with `via` set to `s3`, one `dns_alias`, and two `vpc_origin` |
| `web-alb` | `asg-tg` (instance :8081, 2.8.0) | The two instances of the ASG `web-asg` in public a and b, plus the worker registered by hand; `web-asg-1` is also registered by hand in `web-tg` | The engine keeps `ec2_instance` edges as they are. The screen folds the `web-asg-2` edge into the ASG, while `web-asg-1` is not folded because its group mixes registration sources |
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
# ECS container instances on the bridge network (2.8.0). The service that
# holds the target group wins, so no edge goes to the EC2 instances.
ECS_HOSTS = ("i-0abc1234def56301", "i-0abc1234def56302")
# Instances of an Auto Scaling group (2.8.0). Registrations in a target group
# the ASG holds fold into a single ASG edge.
ASG_INSTANCES = ("i-0abc1234def56401", "i-0abc1234def56402")
ASG = "web-asg"
EKS = "lbt-eks"
PARTNER = "444455556666"
LAMBDA_VPC_FN = "orders-fn"
ZONE_PUB = "Z0LBT1111AAAA1111"
CF_PUB = "E0LBTPUB1111"
CF_INT = "E0LBTINT2222"
ZONE_PRIV = "Z0LBT2222BBBB2222"
LAMBDA_ENI_IP = "10.20.22.40"  # a partner account outside the collection scope, from the example range
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
# An ElastiCache cache cluster (2.9.0): one node of a replication group, in a
# data a subnet. The ENI description names that id as a word.
CACHE = "lbt-cache-0001-001"
# The ENI IP of that cache node, in data b (2.9.0). A CloudFront VPC origin
# edge crosses the data a row and leaves that slot empty.
CACHE_NODE_IP = "10.20.22.30"
# A service we do not collect, here an EFS mount target. This is the case that
# measures the text for a service that is not collected.
EFS_IP = "10.20.22.45"
# An Aurora cluster (2.9.0) whose two members share an AZ, data b. The RDS ENI
# in that AZ does not say which member it belongs to, but it does say which
# cluster.
AURORA = "lbt-aurora"
AURORA_MEMBERS = ("lbt-aurora-1", "lbt-aurora-2")
AURORA_IP = "10.20.22.50"
# An OpenSearch VPC domain (2.9.0): one node in a single data b subnet.
SEARCH = "lbt-search"
SEARCH_IP = (
    "10.20.22.41"  # data b, because a CloudFront VPC origin edge crosses the data a row
)
# A provisioned MSK cluster (2.9.0) with two brokers, in data a and b. The load
# balancer holds both broker IPs in one target group.
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
    """A CloudFront distribution (2.8.0); only the origin shape differs."""
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


# The target health AWS reports for a target group with health checks turned
# off, which is the default for Lambda: State `unavailable` with this Reason.
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
    """Set the health of every target in a group through `health`, a mapping of
    target group key to health. The default is healthy, except for `fn`, where
    health checks are off by default.

    `health_check_off` means State `unavailable` with Reason
    `Target.HealthCheckDisabled` (`HEALTH_CHECK_OFF`). Health checks are off by
    default for Lambda target groups, and the AWS documentation says such a
    target answers in exactly this shape (ELB `TargetHealth`).
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
            # The main route table, used by the app and data subnets with no
            # association of their own. It is the exit for the NLB's
            # external IPs.
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
        # One ASG instance is also registered by hand in a group the ASG does
        # not hold (2.8.0). That edge is not folded into the ASG, because the
        # group does not belong to the ASG alone.
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
        # Dynamic bridge ports: two tasks on the same host, on different
        # ports, and one on another host.
        TG["bridge"]: [
            _target(ECS_HOSTS[0], 32768, state=h("bridge")),
            _target(ECS_HOSTS[0], 32769, state=h("bridge")),
            _target(ECS_HOSTS[1], 32768, state=h("bridge")),
        ],
        TG["idle"]: [],
        # The two instances the ASG registered, plus the worker registered by
        # hand. The worker is not an instance of that ASG, so it keeps its own
        # EC2 edge.
        TG["asg"]: [
            _target(ASG_INSTANCES[1], 8081, state=h("asg")),
            _target(WORKER, 8081, state=h("asg")),
            _target(ASG_INSTANCES[0], 8081, state=h("asg")),
        ],
        TG["standby"]: [],
        # Pod IPs registered by the AWS Load Balancer Controller. Their ENIs
        # are absent from this world, and we do not need to ask about them
        # because the group tag already names the cluster.
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
        # ECS task ENIs. The service that holds the target group wins first,
        # so the ENIs are never looked at.
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
        # MSK broker ENIs. Even when the description text does not carry the
        # cluster name, the ENI ids from `ListNodes` link them.
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
        # The shared ENI of a VPC Lambda (2.8.0). Exactly one collected
        # function matches the subnet and security groups, so it is that one.
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
        # EC2 launch type on the bridge network, with no awsvpc configuration,
        # so the targets are container instances in an `instance` target group.
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
        # A desired count of 0. Even with no registrations, which service sits
        # behind this load balancer is part of the configuration, so the edge
        # stays with zero registrations.
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
        # The owner account of a partner service (2.8.0). The account is
        # outside the collection scope, so we cannot reach the providing NLB
        # and only know which account the service belongs to.
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
