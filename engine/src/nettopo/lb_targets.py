"""LB → 대상 잇기 — `load_balancer_target` 엣지(스키마 2.7.0 · ADR 0002 §8 · ADR 0007 결정 6 개정).

사용자 결정 2026-10-20: **LB 의 대상은 대상 종류마다 전부 선으로 그린다 · 대상의 정체는 구조에 싣는다(등록·해제 =
구조 변경) · 헬스는 상태 단에만.** 이 파일은 수집기 본체(`collect.py`)의 두 자리에서 불린다.

1. `lookup()` — 1패스가 다 끝난 뒤 · 3패스 앞. 사설 IP 대상(`TargetType = ip`)의 주인 ENI 를 **그 IP 가 사는 VPC 의
   (계정, 리전)** 에서 `ec2:DescribeNetworkInterfaces` 로 묻는다(VPC 마다 한 번 · `vpc-id` + `addresses.private-ip-address`
   필터 · 100개씩). 🔴 한 쌍을 도는 동안 할 수 없다 — AZ `all` 대상(LB VPC 밖 IP)은 피어링·TGW 너머 **다른 계정의 VPC**
   에 있을 수 있고, 그 VPC 의 CIDR 은 모든 쌍을 다 돈 뒤에야 안다. 거부되면 그 IP 를 물은 LB 에 사유가 붙는다(불변식 ④).
2. `join()` — 3패스(`_resolve`) 안. 전체 노드 id 가 확정된 뒤 대상마다 노드를 찾고 **(LB, 대상 노드) 마다 엣지 하나**로
   묶는다(라우트를 대상별로 묶는 ADR 0002 §4 와 같은 규칙 — ECS 태스크 IP 30개 = `ecs_service` 선 하나 · 태스크 IP 는
   배포마다 바뀌므로 IP 단위 선은 매 배포가 「구조 바뀜」이 된다).

대상 종류별 해소(`docs/adr/0002-resource-and-edge-model.md` §8 표가 정본):

| TargetType | `Target.Id` | 잇는 노드 |
|---|---|---|
| `instance` | `i-…` | `ec2_instance`(인스턴스 id 는 계정 사이에 겹치지 않는다 — native 색인) |
| `alb` | ALB ARN | `load_balancer`(ARN 의 계정·리전·이름으로 id 를 짓는다) |
| `lambda` | 함수 ARN | `lambda_function` — **이 파일이 짓는 최소 노드**(이름·계정·리전 · 수집 범위 안일 때만) |
| `ip` | 사설 IP | ENI 주인(아래 순서) · 수집한 어느 VPC 에도 안 드는 AZ `all` IP = `external_ip` 노드(이 파일이 짓는다) |

🔵 **그룹을 든 자원이 먼저다**(2.8.0 · 사용자 결정 2026-10-20 P1-1 · `_Index.claimer`): 그 대상 그룹을 자기 `target_group_arns` 로
든 ECS 서비스가 하나면 그 그룹의 등록은 대상 종류와 무관하게(태스크 IP · 컨테이너 인스턴스 = ECS on EC2 bridge) 그 서비스 선 하나로
간다 — 그리고 **등록이 0 이어도** 선이 있다(LB 초안의 `lb_groups` · `target_count` 0).

IP 의 주인 순서(사용자 지시 2026-10-20 · 앞이 이긴다): (위 그룹 주인) → VPC 엔드포인트(ENI 종류·설명의 `vpce-`) → RDS(요청자 `amazon-rds` · 그 VPC·AZ 의 DB 가 하나 · 여럿이면 전부 한 클러스터일 때 그 클러스터 · 2.9.0) → ElastiCache(2.9.0 · 설명의 클러스터 id 낱말) → OpenSearch(2.9.0 · 설명의 도메인 이름 낱말) → MSK(2.9.0 · 브로커 ENI id · 없으면 설명의 클러스터 ARN 낱말) → EKS(VPC CNI 태그 ·
`Amazon EKS <이름>`) → EC2(ENI 부착 인스턴스) → 다른 LB(설명 `ELB app|net|gwy/<이름>/…`) → EIC·SageMaker(설명에 그 자원
id 가 있을 때). 🔴 못 찾으면 **노드를 지어내지 않는다** — LB 의 `unresolved_refs` 에 대상 id 를 남기고
`unresolved_targets` 에 「`<대상 id> <까닭>`」을 적는다(까닭 = `not_collected`·`not_in_scope`·`no_eni`·`eni_unread`·
`ambiguous` · 수집하지 않는 서비스면 그 서비스 코드 `elasticache`·`opensearch`… — 화면이 「대상: 10.x (ElastiCache —
수집하지 않는 서비스)」로 말한다).

🔴 **결정론(불변식 ②).** 입력 순서와 무관하다: 대상은 (그룹, ARN, id, 포트) 순으로 정렬돼 오고(`load_balancer._ref_key`),
엣지·노드는 id 순으로 내보내고, 목록 속성은 정렬한다. ENI 응답은 `NetworkInterfaceId` 순으로 본다.
🔴 **읽기 전용(불변식 ①).** 부르는 API 는 `DescribeNetworkInterfaces` 하나다 — 같은 클라이언트 팩토리(가드가 걸린)로 받는다.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .model import Edge, Node, Reason, Scope, node_id, split_node_id
from .services.base import (
    TARGET_HEALTH_DETAIL,
    ClientFactory,
    NodeDraft,
    TargetRef,
    alive,
    classify,
    health_detail,
    pages,
)

ENI_API = "ec2:DescribeNetworkInterfaces"
EDGE_TYPE = "load_balancer_target"
# 필터 값 한 번에 넣는 수. AWS 는 필터 값 개수를 문서로 못 박지 않는다 — 작게 잘라 「너무 많다」 거부를 피한다.
_IP_CHUNK = 100
# AZ `all` = 「LB VPC 밖의 IP」(ELB 문서 · `RegisterTargets`). 그 밖의 값은 LB VPC 안의 AZ 이름이다.
_OUTSIDE = "all"
# VPC CNI 가 만든 ENI 의 클러스터 태그 + EKS 관리형 노드 ENI 태그(앞이 이긴다).
_EKS_TAGS = (
    "cluster.k8s.amazonaws.com/name",
    "eks:cluster-name",
    "aws:eks:cluster-name",
)
_ELB_DESC = re.compile(r"^ELB (?:app|net|gwy)/([^/]+)/")
_TOKEN = re.compile(r"\b((?:vpce|eice)-[0-9a-f]{8,})\b")
# Lambda 함수 ENI 설명 — `AWS Lambda VPC ENI-<함수 이름>-<uuid>`(함수 이름에 `-` 가 들어갈 수 있어 끝의 uuid 를 떼어 낸다)
_LAMBDA_DESC = re.compile(
    r"^AWS Lambda VPC ENI-(.+)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass(frozen=True, slots=True)
class KnownLambda:
    """구조 문서가 이미 읽은 VPC Lambda 함수 하나(#152 · ADR 0006 결정 15) — 상태 단이 공유 ENI 의 주인을 구조와 같게 가리는 재료."""

    node_id: str
    account_id: str
    region: str
    name: str
    arn: str | None
    subnet_ids: frozenset[str]
    security_group_ids: frozenset[str]


def structure_lambdas(topology: dict[str, Any]) -> tuple[KnownLambda, ...]:
    """구조 문서의 VPC Lambda 함수(서브넷이 있는 `lambda_function` 노드) — **상태 단 전용**(#152 · ADR 0006 결정 15).

    상태 단은 `lambda:ListFunctions` 를 부르지 않는다(`observe.OBSERVED_RESOURCE_TYPES` · 함수에는 상태 단이 읽을 상태가 없다).
    그러면 LB 의 IP 대상이 VPC Lambda 의 공유 ENI 일 때 주인을 못 가려(`… lambda`) LB → 함수 선이 상태 단에 없고, 그 선은 모니터링을
    켜도 상태가 없었다. 구조 문서는 같은 함수의 서브넷·보안 그룹을 이미 들고 있다 — 그것으로 **구조와 같은 규칙**(`_eni_owner` ⑦')
    으로 가린다. AWS 호출 0(ENI 는 상태 단이 원래 묻는다). 🔴 서브넷 없는 함수는 넣지 않는다 — 구조 단 색인(`_Index.lambdas`)과 같은 집합.
    """
    out: list[KnownLambda] = []
    for n in topology.get("nodes") or ():
        attrs = n.get("attributes") or {}
        subnets = attrs.get("subnet_ids") or []
        if n.get("type") != "lambda_function" or not subnets or not n.get("region"):
            continue
        out.append(
            KnownLambda(
                node_id=str(n["id"]),
                account_id=str(n["account_id"]),
                region=str(n["region"]),
                name=str(n.get("native_id") or n.get("name") or ""),
                arn=n.get("arn"),
                subnet_ids=frozenset(str(x) for x in subnets),
                security_group_ids=frozenset(
                    str(x) for x in attrs.get("security_group_ids") or []
                ),
            )
        )
    return tuple(sorted(out, key=lambda k: k.node_id))


@dataclass(frozen=True, slots=True)
class Eni:
    """ENI 응답 한 줄에서 주인을 가를 사실만."""

    eni_id: str
    account_id: str  # 물은 계정(= 그 VPC 를 수집한 쌍)
    region: str
    vpc: str
    zone: str | None
    interface_type: str
    requester_id: str
    description: str
    instance_id: str | None
    tags: tuple[tuple[str, str], ...]
    # 2.8.0(P2-6) — Lambda 공유 ENI 의 주인을 서브넷·보안 그룹 조합으로 가린다
    subnet_id: str = ""
    groups: tuple[str, ...] = ()


@dataclass(slots=True)
class Lookup:
    """`lookup()` 의 결과 — (VPC 노드 id, IP) → ENI · 못 읽은 VPC → 사유."""

    found: dict[tuple[str, str], list[Eni]] = field(default_factory=dict)
    failed: dict[str, Reason] = field(default_factory=dict)
    # IP → 그 IP 를 담는 수집한 VPC 노드 id 들(AZ `all` 대상의 후보 · 정렬)
    containing: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _ipv4(value: str) -> ipaddress.IPv4Address | None:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return None
    return addr if isinstance(addr, ipaddress.IPv4Address) else None


def _vpc_networks(
    drafts: Iterable[NodeDraft],
) -> list[tuple[str, ipaddress.IPv4Network]]:
    out: list[tuple[str, ipaddress.IPv4Network]] = []
    for d in drafts:
        if d.node.type != "vpc":
            continue
        for cidr in d.node.attributes.get("cidr_blocks") or []:
            try:
                net = ipaddress.ip_network(str(cidr), strict=False)
            except ValueError:
                continue
            if isinstance(net, ipaddress.IPv4Network):
                out.append((d.node.id, net))
    return sorted(out, key=lambda x: (x[0], str(x[1])))


def _lb_vpc(d: NodeDraft) -> str | None:
    """LB 초안의 VPC 노드 id(첫 부모 후보 — `load_balancer.build` 가 언제나 VPC 를 먼저 둔다)."""
    first = d.parents[0] if d.parents else None
    return first if first and first.startswith("vpc/") else None


def _where(
    ref: TargetRef, d: NodeDraft, networks: list[tuple[str, ipaddress.IPv4Network]]
) -> tuple[str, ...]:
    """그 IP 의 ENI 를 물을 VPC 노드 id 들. AZ 가 VPC 안이면 LB VPC 하나 · `all` 이면 그 IP 를 담는 수집한 VPC 전부."""
    if ref.zone != _OUTSIDE:
        own = _lb_vpc(d)
        return (own,) if own else ()
    addr = _ipv4(ref.target_id)
    if addr is None:
        return ()
    return tuple(sorted({vid for vid, net in networks if addr in net}))


def _vpc_pair(
    vpc_node: str, drafts_by_id: dict[str, NodeDraft]
) -> tuple[str, str, str] | None:
    """VPC 노드 id → (물을 계정, 리전, VPC native). 계정 = 그 VPC 를 **수집한 쌍**(LB VPC 면 LB 를 수집한 쌍)."""
    d = drafts_by_id.get(vpc_node)
    _, owner, region, native = split_node_id(vpc_node)
    if region is None or native is None:
        return None
    if d is not None:
        return d.account_id, d.region, native
    return owner, region, native


def lookup(drafts: list[NodeDraft], client_factory: ClientFactory) -> Lookup:
    """사설 IP 대상의 ENI 를 그 VPC 의 쌍에서 묻는다(읽기 전용 · VPC 마다 한 번 · 100개씩).

    🔴 실패는 예외가 아니다 — 그 VPC 를 물은 LB 초안에 사유를 붙이고(`partial` · 불변식 ④) 그 IP 들은
    `join()` 이 `eni_unread` 로 남긴다. 다른 VPC·다른 LB 의 선은 그대로 생긴다.
    """
    result = Lookup()
    networks = _vpc_networks(drafts)
    drafts_by_id = {d.node.id: d for d in drafts if d.node.type == "vpc"}
    # 물을 것: VPC 노드 id → (쌍, IP 집합) · 그 VPC 를 물은 LB 초안들
    asks: dict[str, set[str]] = {}
    askers: dict[str, list[NodeDraft]] = {}
    lb_vpc_pair: dict[str, tuple[str, str, str]] = {}
    for d in drafts:
        if d.node.type != "load_balancer":
            continue
        for ref in d.lb_targets:
            if ref.target_type != "ip" or _ipv4(ref.target_id) is None:
                continue
            where = _where(ref, d, networks)
            if ref.zone == _OUTSIDE:
                result.containing[ref.target_id] = where
            for vid in where:
                asks.setdefault(vid, set()).add(ref.target_id)
                if d not in askers.setdefault(vid, []):
                    askers[vid].append(d)
                if vid == _lb_vpc(d) and vid not in drafts_by_id:
                    # LB VPC 를 수집하지 않았어도(종류를 뺐다) LB 를 수집한 쌍이 그 VPC 를 물을 수 있다
                    _, _, _, native = split_node_id(vid)
                    if native:
                        lb_vpc_pair.setdefault(vid, (d.account_id, d.region, native))
    for vid in sorted(asks):
        pair = lb_vpc_pair.get(vid) or _vpc_pair(vid, drafts_by_id)
        if pair is None:
            continue
        account, region, native = pair
        try:
            client = client_factory(account, region, "ec2")
        except (ClientError, BotoCoreError) as exc:
            operation = getattr(exc, "operation_name", None)
            result.failed[vid] = classify(
                exc, f"sts:{operation}" if operation else ENI_API
            )[1]
            continue
        ips = sorted(asks[vid])
        try:
            got: list[dict[str, Any]] = []
            for i in range(0, len(ips), _IP_CHUNK):
                got.extend(
                    pages(
                        client,
                        "describe_network_interfaces",
                        "NetworkInterfaces",
                        Filters=[
                            {"Name": "vpc-id", "Values": [native]},
                            {
                                "Name": "addresses.private-ip-address",
                                "Values": ips[i : i + _IP_CHUNK],
                            },
                        ],
                    )
                )
        except (ClientError, BotoCoreError) as exc:
            result.failed[vid] = classify(exc, ENI_API)[1]
            continue
        wanted = set(ips)
        seen: set[str] = set()
        for raw in sorted(got, key=lambda e: str(e.get("NetworkInterfaceId") or "")):
            eid = str(raw.get("NetworkInterfaceId") or "")
            if eid in seen:
                continue  # 청크 사이에 같은 ENI 가 두 번 올 수 있다(보조 IP 둘)
            seen.add(eid)
            eni = Eni(
                eni_id=eid,
                account_id=account,
                region=region,
                vpc=vid,
                zone=str(raw["AvailabilityZone"])
                if raw.get("AvailabilityZone")
                else None,
                interface_type=str(raw.get("InterfaceType") or ""),
                requester_id=str(raw.get("RequesterId") or ""),
                description=str(raw.get("Description") or ""),
                instance_id=str((raw.get("Attachment") or {}).get("InstanceId") or "")
                or None,
                tags=tuple(
                    sorted(
                        (str(t["Key"]), str(t["Value"]))
                        for t in raw.get("TagSet") or []
                        if "Key" in t and "Value" in t
                    )
                ),
                subnet_id=str(raw.get("SubnetId") or ""),
                groups=tuple(
                    sorted(
                        str(g.get("GroupId"))
                        for g in raw.get("Groups") or []
                        if g.get("GroupId")
                    )
                ),
            )
            addrs = {str(raw.get("PrivateIpAddress") or "")} | {
                str(p.get("PrivateIpAddress") or "")
                for p in raw.get("PrivateIpAddresses") or []
            }
            for ip in sorted(addrs & wanted):
                result.found.setdefault((vid, ip), []).append(eni)
    # 실패를 그 VPC 를 물은 LB 에 붙인다 — 같은 사유는 한 번만(불변식 ④ · 결정론)
    for vid, reason in sorted(result.failed.items()):
        for d in askers.get(vid, []):
            if reason not in d.node.partial_reasons:
                d.node = replace(
                    d.node,
                    partial=True,
                    partial_reasons=d.node.partial_reasons + (reason,),
                )
    return result


# ------------------------------------------------------------------ 3패스


@dataclass(slots=True)
class _Bucket:
    """(LB, 대상 노드) 하나로 묶인 등록들."""

    groups: set[str] = field(default_factory=set)
    ports: set[int] = field(default_factory=set)
    kinds: set[str] = field(default_factory=set)
    registered: set[str] = field(
        default_factory=set
    )  # 등록이 하나라도 든 대상 그룹 ARN(빈 그룹 잇기가 포트를 섞지 않게)
    count: int = 0
    healthy: int = 0
    unhealthy: int = 0
    # 정상도 비정상도 아닌 다섯 갈래(관측 1.15.0 · #137 · 키 = `TARGET_HEALTH_DETAIL` 의 속성 키)
    detail: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k, _ in TARGET_HEALTH_DETAIL}
    )
    exits: set[str] | None = (
        None  # 외부 IP 선만 — 그 LB VPC 라우트 테이블이 그 IP 를 보내는 곳
    )


@dataclass(slots=True)
class Joined:
    nodes: list[NodeDraft]
    edges: list[Edge]
    unresolved: dict[str, set[str]]
    # LB 노드 id → `unresolved_targets` 줄(「<대상 id> <까닭>」)
    reasons: dict[str, set[str]]


def _arn_parts(arn: str) -> tuple[str, str, str] | None:
    """`arn:aws:<svc>:<region>:<account>:<resource>` → (region, account, resource)."""
    parts = arn.split(":", 5)
    if len(parts) != 6 or parts[0] != "arn":
        return None
    return parts[3], parts[4], parts[5]


def _owner_code(eni: Eni) -> str:
    """주인을 못 찾은 ENI 의 까닭 코드 — 수집하지 않는 서비스면 그 서비스(화면이 사람 말로 옮긴다)."""
    desc = eni.description
    low = desc.lower()
    kind = eni.interface_type.lower()
    table: tuple[tuple[bool, str], ...] = (
        (low.startswith("elasticache"), "elasticache"),
        (
            low.startswith("rdsnetworkinterface") or eni.requester_id == "amazon-rds",
            "rds",
        ),
        (kind == "lambda" or low.startswith("aws lambda vpc eni"), "lambda"),
        (low.startswith("efs mount target"), "efs"),
        (
            low.startswith("es ") or "opensearch" in low or "elasticsearch" in low,
            "opensearch",
        ),
        ("msk" in low or "kafka" in low, "msk"),
        (
            kind == "nat_gateway" or low.startswith("interface for nat gateway"),
            "nat_gateway",
        ),
        (low.startswith("arn:aws:ecs:"), "ecs_task"),
        (kind in ("vpc_endpoint", "gateway_load_balancer_endpoint"), "vpc_endpoint"),
        (low.startswith("elb "), "load_balancer"),
        (low.startswith("amazon eks"), "eks"),
        ("redshift" in low, "redshift"),
        (low.startswith("dmsnetworkinterface"), "dms"),
        ("directory" in low, "directory_service"),
        ("sagemaker" in low or "sagemaker" in eni.requester_id.lower(), "sagemaker"),
    )
    for hit, code in table:
        if hit:
            return code
    if kind and kind != "interface":
        return re.sub(r"[^a-z0-9_]", "_", kind)
    return "eni"


class _Index:
    """3패스가 대상을 찾는 색인. 전부 확정된 초안(`kept`)에서 만든다."""

    def __init__(
        self,
        kept: list[NodeDraft],
        ids: set[str],
        by_native: dict[str, str],
        seeded: tuple[KnownLambda, ...] = (),
    ) -> None:
        # 상태 단이 구조 문서에서 받은 함수(#152) — 이번에 수집한 같은 id 가 있으면 그쪽이 이긴다(색인에 두 번 들면 `ambiguous`)
        self.seeded = {k.node_id: k for k in seeded if k.node_id not in ids}
        self.ids = ids | set(self.seeded) if self.seeded else ids
        self.by_native = by_native
        self.ecs_by_tg: dict[str, list[str]] = {}
        # 대상 그룹 ARN → 그 그룹을 든 EKS 클러스터 노드(AWS LB Controller 태그 `elbv2.k8s.aws/cluster` · 2.8.0 · P2-5)
        self.eks_by_tg: dict[str, set[str]] = {}
        self.rds_by_zone: dict[tuple[str, str], list[str]] = {}
        # RDS 인스턴스 노드 id → 그 인스턴스가 속한 DB 클러스터 노드 id(2.9.0 · 속성 `db_cluster_identifier` 가 있을 때만)
        self.rds_cluster: dict[str, str] = {}
        self.sm_natives: dict[str, list[str]] = {}
        # 수집한 VPC Lambda 함수(2.8.0 · P2-6) — (노드 id, 서브넷 집합, 보안 그룹 집합) · 공유 ENI 의 주인 가리기
        self.lambdas: list[tuple[str, frozenset[str], frozenset[str]]] = []
        # 브로커 ENI id → MSK 클러스터 노드 id(2.9.0 · `ListNodes` 를 읽은 클러스터만)
        self.msk_by_eni: dict[str, list[str]] = {}
        # 클러스터 ARN → MSK 클러스터 노드 id(ENI 설명의 ARN 낱말로 잇는 둘째 길)
        self.msk_by_arn: dict[str, str] = {}
        self.rt_by_subnet: dict[tuple[str, str], list[NodeDraft]] = {}
        self.main_rt: dict[str, list[NodeDraft]] = {}
        for d in kept:
            if d.node.type == "load_balancer":
                for g in d.lb_groups:
                    eks = node_id(
                        "eks_cluster",
                        d.node.account_id,
                        d.node.region or "",
                        g.cluster or "",
                    )
                    if g.cluster and eks in ids:
                        self.eks_by_tg.setdefault(g.arn, set()).add(eks)
        for d in kept:
            n = d.node
            if n.type == "ecs_service":
                for arn in n.attributes.get("target_group_arns") or []:
                    self.ecs_by_tg.setdefault(str(arn), []).append(n.id)
            elif n.type == "rds_db_instance":
                vpc = next((p for p in d.parents if p.startswith("vpc/")), None)
                zones = {
                    n.attributes.get("availability_zone"),
                    n.attributes.get("secondary_availability_zone"),
                }
                for z in sorted(str(x) for x in zones if x):
                    if vpc:
                        self.rds_by_zone.setdefault((vpc, z), []).append(n.id)
                cluster = n.attributes.get("db_cluster_identifier")
                if cluster and n.region:
                    self.rds_cluster[n.id] = node_id(
                        "rds_db_cluster", n.account_id, n.region, str(cluster)
                    )
            elif n.type == "lambda_function" and n.attributes.get("subnet_ids"):
                self.lambdas.append(
                    (
                        n.id,
                        frozenset(str(x) for x in n.attributes.get("subnet_ids") or []),
                        frozenset(
                            str(x) for x in n.attributes.get("security_group_ids") or []
                        ),
                    )
                )
            elif n.type == "msk_cluster":
                if n.arn:
                    self.msk_by_arn[str(n.arn)] = n.id
                for eid in n.attributes.get("broker_eni_ids") or []:
                    self.msk_by_eni.setdefault(str(eid), []).append(n.id)
            elif n.type.startswith("sagemaker_") and n.native_id:
                self.sm_natives.setdefault(n.native_id, []).append(n.id)
            elif n.type == "route_table":
                vpc = d.parents[0] if d.parents else ""
                if n.attributes.get("main"):
                    self.main_rt.setdefault(vpc, []).append(d)
                for e in d.edges:
                    if e.type == "association" and e.live:
                        self.rt_by_subnet.setdefault((vpc, e.target_native), []).append(
                            d
                        )
        for k in sorted(self.seeded.values(), key=lambda k: k.node_id):
            self.lambdas.append((k.node_id, k.subnet_ids, k.security_group_ids))

    def claimer(self, group_arn: str, target_type: str) -> str | None:
        """그 대상 그룹을 **자기 것으로 든** 자원 하나(2.8.0) — 있으면 그 그룹의 등록은 전부 그 자원의 선 하나로 간다.

        ECS 서비스(`target_group_arns`)가 **하나**면 그 서비스다 — 대상이 태스크 IP(awsvpc)든 컨테이너 인스턴스(EC2 ·
        bridge·host 네트워크)든 같다(사용자 결정 2026-10-20 P1-1). 인스턴스 대상을 EC2 로 그리면 「이 LB 뒤의 서비스」가
        안 보이고 호스트만 보인다. 둘 이상이 들면(AWS 가 막지 않는 모양) 고르지 않는다 — 대상마다 원래 해소로 간다.
        Lambda·ALB 대상 그룹은 ECS 가 들 수 없다(그 종류는 원래 해소 그대로).
        """
        if target_type not in ("instance", "ip"):
            return None
        ecs = sorted(set(self.ecs_by_tg.get(group_arn) or []))
        if len(ecs) == 1:
            return ecs[0]
        # EKS(2.8.0 · P2-5) — AWS LB Controller 가 그 그룹에 클러스터 태그를 달았다 = 「이 LB 는 이 클러스터의 Ingress/Service 다」.
        #   파드 IP(ip)든 노드(instance · NodePort)든 그 클러스터 선 하나다 — 노드 EC2 로 흩어지면 「어느 클러스터 뒤인가」가 안 보인다
        eks = sorted(self.eks_by_tg.get(group_arn) or [])
        return eks[0] if len(eks) == 1 else None

    def native(self, value: str | None, node_type: str) -> str | None:
        if not value:
            return None
        hit = self.by_native.get(value)
        return hit if hit and hit.startswith(f"{node_type}/") else None

    def known(self, nid: str) -> str | None:
        return nid if nid in self.ids else None

    def exits(self, lb: NodeDraft, ip: str) -> set[str] | None:
        """LB 서브넷들의 라우트 테이블(명시 연관 · 없으면 VPC 주 테이블)에서 그 IP 의 가장 긴 접두사 대상.

        LB VPC 의 라우트 테이블을 하나도 못 모았으면 `None` — 「출구 없음」과 「출구를 모른다」를 가른다.
        """
        vpc = _lb_vpc(lb)
        addr = _ipv4(ip)
        if vpc is None or addr is None:
            return None
        subnets = [str(s) for s in lb.node.attributes.get("subnet_ids") or []]
        tables: list[NodeDraft] = []
        for s in subnets:
            tables.extend(
                self.rt_by_subnet.get((vpc, s)) or self.main_rt.get(vpc) or []
            )
        if not subnets:
            tables.extend(self.main_rt.get(vpc) or [])
        if (
            not tables
            and not self.main_rt.get(vpc)
            and not any(k[0] == vpc for k in self.rt_by_subnet)
        ):
            return None
        out: set[str] = set()
        for t in {id(x): x for x in tables}.values():
            best: tuple[int, str] | None = None
            for e in t.edges:
                if e.type != "route":
                    continue
                for dest in e.attributes.get("destinations") or []:
                    try:
                        net = ipaddress.ip_network(str(dest), strict=False)
                    except ValueError:
                        continue  # 접두사 목록(`pl-`)은 범위를 모른다 — 출구로 짓지 않는다
                    if isinstance(net, ipaddress.IPv4Network) and addr in net:
                        cand = (net.prefixlen, e.target_native)
                        if (
                            best is None
                            or cand[0] > best[0]
                            or (cand[0] == best[0] and cand[1] < best[1])
                        ):
                            best = cand
            if best is not None:
                out.add(best[1])
        return out


def _ip_owner(
    ref: TargetRef,
    lb: NodeDraft,
    found: Lookup,
    ix: _Index,
) -> tuple[str | None, str | None]:
    """사설 IP 대상 → (대상 노드 id, None) 또는 (None, 까닭 코드) · 외부 IP 면 (None, "external").

    그 그룹을 든 ECS 서비스(①)는 `_target` 이 먼저 본다(`_Index.claimer` — IP·인스턴스 대상 공통)."""
    if ref.zone == _OUTSIDE:
        where = found.containing.get(ref.target_id, ())
        if not where:
            return None, "external"
    else:
        own = _lb_vpc(lb)
        where = (own,) if own else ()
    enis: list[Eni] = []
    for vid in where:
        enis.extend(found.found.get((vid, ref.target_id)) or [])
    if not enis:
        if any(vid in found.failed for vid in where):
            return None, "eni_unread"
        return None, "no_eni"
    if len({e.eni_id for e in enis}) > 1:
        return None, "ambiguous"
    return _eni_owner(enis[0], ix)


def _eni_owner(eni: Eni, ix: _Index) -> tuple[str | None, str | None]:
    """ENI 하나 → (주인 노드 id, None) 또는 (None, 까닭 코드). LB 의 IP 대상(위)과 라우트의 `eni-` 대상(2.8.0 · P2-2 · 아래
    `route_owners`)이 같이 쓴다 — 두 길이 서로 다른 순서로 주인을 정하면 같은 ENI 가 화면 두 곳에서 다른 자원이 된다."""
    desc = eni.description
    # ② VPC 엔드포인트(인터페이스 · GWLB 엔드포인트) — ENI 설명에 엔드포인트 id 가 있다
    for tok in _TOKEN.findall(desc):
        if tok.startswith("vpce-"):
            hit = ix.native(tok, "vpc_endpoint")
            if hit:
                return hit, None
    if eni.interface_type in ("vpc_endpoint", "gateway_load_balancer_endpoint"):
        return None, "vpc_endpoint"
    # ③ RDS — RDS ENI 에는 DB 이름이 없다(`rds_db_instance.py` join_enis 머리말) · 그 VPC·AZ 의 DB 가 하나일 때만
    if eni.requester_id == "amazon-rds" or desc.startswith("RDSNetworkInterface"):
        dbs = sorted(set(ix.rds_by_zone.get((eni.vpc, eni.zone or ""), [])))
        if len(dbs) == 1:
            return dbs[0], None
        # 2.9.0(#114) — 그 VPC·AZ 의 DB 가 여럿이어도 **전부 한 클러스터의 멤버**면 그 ENI 는 그 클러스터의 것이다(어느 멤버인지는 모른다 —
        #   그래서 인스턴스가 아니라 클러스터 카드로 잇는다). 클러스터가 섞이면 고르지 않는다.
        clusters = {ix.rds_cluster.get(d) for d in dbs}
        if len(dbs) > 1 and len(clusters) == 1 and None not in clusters:
            hit = ix.known(str(next(iter(clusters))))
            if hit:
                return hit, None
        return None, "rds"
    # ③‴ MSK(#114 · 2.9.0) — 브로커 ENI id 가 클러스터 속성(`broker_eni_ids` · `ListNodes`)에 있다. 설명 글자에 기대지 않는다.
    msk = sorted(set(ix.msk_by_eni.get(eni.eni_id) or []))
    if len(msk) == 1:
        return msk[0], None
    #    없으면(ListNodes 거부 · ZooKeeper/컨트롤러 ENI) 설명의 클러스터 ARN 낱말 — 실측(2026-10-20 · 시드니 QA 스택) 설명 =
    #    「DO NOT DELETE - Amazon MSK network interface for cluster <클러스터 ARN>.」 · 요청자는 계정이다(amazon-* 가 아니다).
    #    🔴 ARN 이 낱말로 **통째로** 같을 때만(끝 마침표만 뗀다) — 부분 문자열로 고르지 않는다
    if "amazon msk" in desc.lower():
        arns = sorted(
            {
                ix.msk_by_arn[t]
                for t in {w.rstrip(".,") for w in desc.split()}
                if t in ix.msk_by_arn
            }
        )
        if len(arns) == 1:
            return arns[0], None
    # ③' ElastiCache(#114 · 2.9.0) — 노드 ENI 설명에 캐시 클러스터 id 가 낱말로 있다(`ElastiCache <id>` · 🔶 실물 미실측 —
    #    ADR 0013 「ENI 설명」). 수집한 클러스터 id 와 **낱말이 정확히 같을 때만** 그 카드다(부분 문자열로 고르지 않는다).
    if eni.requester_id == "amazon-elasticache" or desc.lower().startswith(
        "elasticache"
    ):
        hit = _word_owner(desc, "elasticache_cluster", eni, ix)
        return (hit, None) if hit else (None, "elasticache")
    # ③'' OpenSearch(#114 · 2.9.0) — 데이터 노드 ENI 설명에 도메인 이름이 낱말로 있다(`ES <도메인>` · 🔶 형식 미실측 — 공식 문서는
    #    「설명 열이 어느 도메인인지 보여 준다」까지만 말한다 · ADR 0013). 같은 낱말 규칙.
    low = desc.lower()
    if (
        eni.requester_id == "amazon-elasticsearch"
        or desc.startswith("ES ")
        or "opensearch" in low
    ):
        hit = _word_owner(desc, "opensearch_domain", eni, ix)
        return (hit, None) if hit else (None, "opensearch")
    # ④ EKS — VPC CNI 태그 · 제어부 ENI 설명 `Amazon EKS <이름>`
    tags = dict(eni.tags)
    cluster = next((tags[k] for k in _EKS_TAGS if tags.get(k)), None)
    if cluster is None and desc.startswith("Amazon EKS "):
        cluster = (
            desc[len("Amazon EKS ") :].split()[0]
            if desc[len("Amazon EKS ") :].strip()
            else None
        )
    if cluster:
        hit = ix.known(node_id("eks_cluster", eni.account_id, eni.region, cluster))
        if hit:
            return hit, None
    # ⑤ EC2 — 부착 인스턴스
    if eni.instance_id:
        hit = ix.native(eni.instance_id, "ec2_instance")
        if hit:
            return hit, None
    # ⑥ 다른 LB 의 ENI(`ELB app/<이름>/…`)
    m = _ELB_DESC.match(desc)
    if m:
        hit = ix.known(node_id("load_balancer", eni.account_id, eni.region, m.group(1)))
        if hit:
            return hit, None
    # ⑦' VPC Lambda(2.8.0 · P2-6) — 함수 ENI 는 같은 서브넷·보안 그룹 조합의 함수끼리 **공유**된다(Hyperplane). 서브넷·SG 가 맞는
    #   수집한 함수가 하나일 때만 그 함수다 · 둘 이상 = 고르지 않는다 · 없으면 설명(`AWS Lambda VPC ENI-<이름>-<uuid>`)의 이름
    if eni.interface_type == "lambda" or desc.startswith("AWS Lambda VPC ENI"):
        fit = sorted(
            nid
            for nid, subs, sgs in ix.lambdas
            if eni.subnet_id in subs and sgs == frozenset(eni.groups)
        )
        if len(fit) == 1:
            return fit[0], None
        if len(fit) > 1:
            return None, "ambiguous"
        m = _LAMBDA_DESC.match(desc)
        if m:
            hit = ix.known(
                node_id("lambda_function", eni.account_id, eni.region, m.group(1))
            )
            if hit:
                return hit, None
        return None, "lambda"
    # ⑦ EIC · SageMaker — 설명에 그 자원 id 가 낱말로 있을 때만
    for tok in _TOKEN.findall(desc):
        if tok.startswith("eice-"):
            hit = ix.native(tok, "ec2_instance_connect_endpoint")
            if hit:
                return hit, None
    words = set(re.split(r"[\s\[\]():,/]+", desc))
    sm = sorted({nid for w in words for nid in ix.sm_natives.get(w, [])})
    if len(sm) == 1:
        return sm[0], None
    if cluster:
        return None, "eks"
    if eni.instance_id:
        return None, "not_collected"
    return None, _owner_code(eni)


def _word_owner(desc: str, node_type: str, eni: Eni, ix: _Index) -> str | None:
    """ENI 설명의 낱말 가운데 그 계정·리전에 **수집한** `node_type` 노드의 native id 와 같은 것이 정확히 하나면 그 노드.

    🔴 낱말 단위다 — `redis-1` 클러스터가 `redis-10` 의 ENI 를 가져가지 않는다. 둘 이상이면 고르지 않는다(지어내지 않는다).
    """
    words = {w for w in re.split(r"[\s\[\]():,/]+", desc) if w}
    hits = sorted(
        {
            nid
            for w in words
            if (nid := ix.known(node_id(node_type, eni.account_id, eni.region, w)))
        }
    )
    return hits[0] if len(hits) == 1 else None


def _target(
    ref: TargetRef,
    lb: NodeDraft,
    scope: Scope,
    found: Lookup,
    ix: _Index,
    lambdas: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """대상 하나 → (노드 id, None) 또는 (None, 까닭). Lambda 노드는 `lambdas` 에 모은다(3패스가 노드로 낸다)."""
    kind = ref.target_type
    owner = ix.claimer(ref.target_group_arn, kind)
    if owner:
        return (
            owner,
            None,
        )  # ① 그 그룹을 든 ECS 서비스 — 인스턴스·IP 대상 공통(`_Index.claimer`)
    if kind == "instance":
        hit = ix.native(ref.target_id, "ec2_instance")
        return (hit, None) if hit else (None, "not_collected")
    if kind == "alb":
        parts = _arn_parts(ref.target_id)
        res = parts[2] if parts else ""
        if not parts or not res.startswith("loadbalancer/"):
            return None, "not_collected"
        name = res.split("/")[2] if len(res.split("/")) > 2 else ""
        hit = (
            ix.known(node_id("load_balancer", parts[1], parts[0], name))
            if name
            else None
        )
        return (hit, None) if hit else (None, "not_collected")
    if kind == "lambda":
        parts = _arn_parts(ref.target_id)
        if not parts or not parts[2].startswith("function:"):
            return None, "not_collected"
        region, account, res = parts
        bits = res.split(":")
        fname = bits[1] if len(bits) > 1 else ""
        if not fname:
            return None, "not_collected"
        if account not in scope.account_ids or region not in scope.regions:
            return None, "not_in_scope"
        nid = node_id("lambda_function", account, region, fname)
        # 수집한 함수(2.8.0 · P2-6) — 파생 노드를 짓지 않는다(같은 id)
        if ix.known(nid):
            return nid, None
        slot = lambdas.setdefault(
            nid,
            {
                "account": account,
                "region": region,
                "name": fname,
                "qualifiers": set(),
                "arn": f"arn:{ref.target_id.split(':')[1]}:lambda:{region}:{account}:function:{fname}",
            },
        )
        if len(bits) > 2 and bits[2]:
            slot["qualifiers"].add(bits[2])
        return nid, None
    if kind == "ip":
        return _ip_owner(ref, lb, found, ix)
    return None, "not_collected"


def join(
    kept: list[NodeDraft],
    ids: set[str],
    by_native: dict[str, str],
    found: Lookup,
    scope: Scope,
    *,
    with_state: bool = False,
    seeded: tuple[KnownLambda, ...] = (),
) -> Joined:
    """LB 대상 → 엣지(대상 노드마다 하나) + 이 파일이 짓는 노드(Lambda · 외부 IP) + 못 찾은 대상.

    `seeded` = 구조 문서가 읽은 VPC Lambda 함수(`structure_lambdas` · 상태 단 전용 · #152).
    """
    ix = _Index(kept, ids, by_native, seeded)
    lambdas: dict[str, dict[str, Any]] = {}
    externals: dict[str, dict[str, Any]] = {}
    buckets: dict[tuple[str, str], _Bucket] = {}
    unresolved: dict[str, set[str]] = {}
    reasons: dict[str, set[str]] = {}
    health_read: set[str] = set()
    for lb in kept:
        if lb.node.type != "load_balancer" or not (lb.lb_targets or lb.lb_groups):
            continue
        if not alive(lb.node.attributes.get("state")):
            continue  # 죽은 LB 는 선을 만들지 않는다(ADR 0002 §3)
        # 상태 단의 선별 헬스 — 그룹 하나라도 못 읽었으면 싣지 않는다(LB 합과 같은 규칙 · 일부만 센 수는 틀린 수다)
        health_ok = with_state and not any(
            r.api == "elasticloadbalancing:DescribeTargetHealth"
            for r in lb.node.partial_reasons
        )
        if health_ok:
            health_read.add(lb.node.id)
        for ref in lb.lb_targets:
            tid, why = _target(ref, lb, scope, found, ix, lambdas)
            if why == "external":
                tid = node_id(
                    "external_ip",
                    lb.node.account_id,
                    lb.node.region or "",
                    ref.target_id,
                )
                slot = externals.setdefault(
                    tid,
                    {
                        "account": lb.node.account_id,
                        "region": lb.node.region,
                        "ip": ref.target_id,
                        "exits": None,
                    },
                )
                exits = ix.exits(lb, ref.target_id)
                if exits is not None:
                    slot["exits"] = (slot["exits"] or set()) | exits
                why = None
                bucket = buckets.setdefault((lb.node.id, tid), _Bucket())
                if exits is not None:
                    bucket.exits = (bucket.exits or set()) | exits
            if tid is None:
                unresolved.setdefault(lb.node.id, set()).add(ref.target_id)
                reasons.setdefault(lb.node.id, set()).add(
                    f"{ref.target_id} {why or 'not_collected'}"
                )
                continue
            if tid == lb.node.id:
                continue  # 자기 자신을 대상으로 둘 수는 없다 — 방어
            b = buckets.setdefault((lb.node.id, tid), _Bucket())
            b.groups.add(ref.target_group)
            b.registered.add(ref.target_group_arn)
            if ref.port is not None:
                b.ports.add(ref.port)
            b.kinds.add(ref.target_type)
            b.count += 1
            if health_ok:
                b.healthy += ref.health == "healthy"
                b.unhealthy += ref.health == "unhealthy"
                key = health_detail(ref.health, ref.health_reason)
                if key is not None:
                    b.detail[key] += 1
        # 그룹을 든 자원(ECS 서비스 · EKS 클러스터)은 **등록이 없어도** 잇는다(2.8.0 · 사용자 결정 2026-10-20 P1) — 서비스가 0 으로 줄었거나
        #   배포 사이라 등록이 비어도 「이 LB 뒤에 이 서비스가 있다」는 구성이다. 선의 등록 수 = 0(화면 칩 `×0`).
        #   🔴 등록이 있는 그룹의 포트는 대상 포트가 말한다 — 그룹 포트(bridge 모드의 기본값)를 섞지 않는다.
        for g in lb.lb_groups:
            owner = ix.claimer(g.arn, g.target_type)
            if not owner or owner == lb.node.id:
                continue
            b = buckets.setdefault((lb.node.id, owner), _Bucket())
            if g.arn in b.registered:
                continue
            b.groups.add(g.name)
            if g.port is not None:
                b.ports.add(g.port)
            b.kinds.add(g.target_type)
    # 구조 문서의 함수로 이은 선(#152) — 끝 노드를 상태 스냅샷에도 최소 노드로 둔다(선만 있고 끝이 없는 스냅샷을 만들지 않는다 ·
    #   `lambda_function` 은 상태 없음이라 관측 행은 생기지 않는다 · §O7)
    for dst in sorted({d for _, d in buckets if d in ix.seeded and d not in lambdas}):
        k = ix.seeded[dst]
        lambdas[dst] = {
            "account": k.account_id,
            "region": k.region,
            "name": k.name,
            "qualifiers": set(),
            "arn": k.arn,
        }
    nodes: list[NodeDraft] = []
    for nid in sorted(lambdas):
        s = lambdas[nid]
        attrs: dict[str, Any] = {
            "function_name": s["name"],
            "qualifiers": sorted(s["qualifiers"]),
        }
        nodes.append(
            _derived(
                nid,
                "lambda_function",
                s["account"],
                s["region"],
                s["name"],
                s["arn"],
                s["name"],
                attrs,
            )
        )
    for nid in sorted(externals):
        s = externals[nid]
        attrs = {"ip": s["ip"]}
        if s["exits"] is not None:
            attrs["route_exits"] = sorted(s["exits"])
        nodes.append(
            _derived(
                nid,
                "external_ip",
                s["account"],
                s["region"],
                s["ip"],
                None,
                s["ip"],
                attrs,
            )
        )
    edges: list[Edge] = []
    for src, dst in sorted(buckets):
        b = buckets[(src, dst)]
        attrs = {
            "target_groups": sorted(b.groups),
            "ports": sorted(b.ports),
            "target_types": sorted(b.kinds),
            "target_count": b.count,
        }
        if b.exits is not None:
            attrs["route_exits"] = sorted(b.exits)
        if src in health_read:
            # 상태 단 전용 — 구조 수집(`with_state=False`)에는 이 키들이 없다(불변식 ②)
            attrs["healthy_target_count"] = b.healthy
            attrs["unhealthy_target_count"] = b.unhealthy
            for key, _ in TARGET_HEALTH_DETAIL:
                attrs[key] = b.detail[key]
        edges.append(
            Edge(
                id="|".join((EDGE_TYPE, src, dst)),
                type=EDGE_TYPE,
                source=src,
                target=dst,
                directed=True,
                attributes=attrs,
            )
        )
    return Joined(nodes=nodes, edges=edges, unresolved=unresolved, reasons=reasons)


def memberships(kept: list[NodeDraft], ids: set[str]) -> dict[str, str]:
    """인스턴스 노드 id → 그 인스턴스를 지금 가진 Auto Scaling 그룹 노드 id(2.8.0 · 사용자 결정 2026-10-20 P1-2).

    화면이 LB → 인스턴스 선을 **LB → ASG 선 하나**로 접는 재료다(`docs/canvas-principles.md` §17.27 「ASG」 행). 엔진의 선은
    그대로 인스턴스로 간다 — 상태 단은 ASG 를 부르지 않으므로 엔진이 접으면 구조와 상태 단의 선 id 가 갈린다.
    인스턴스는 같은 계정·리전의 ASG 에만 속한다(ASG API 가 계정·리전 단위다) → 그 쌍의 native 로 찾는다. 두 ASG 가 같은 인스턴스를
    말하면(AWS 가 허용하지 않는다) 달지 않는다. 결정론 — ASG id 순으로 본다.
    """
    by_pair: dict[tuple[str, str, str], set[str]] = {}
    for d in sorted(
        (x for x in kept if x.node.type == "auto_scaling_group" and x.asg_members),
        key=lambda x: x.node.id,
    ):
        for iid in d.asg_members:
            by_pair.setdefault(
                (d.node.account_id, d.node.region or "", iid), set()
            ).add(d.node.id)
    out: dict[str, str] = {}
    for d in kept:
        n = d.node
        if n.type != "ec2_instance" or not n.native_id:
            continue
        owners = by_pair.get((n.account_id, n.region or "", n.native_id)) or set()
        if len(owners) == 1:
            (asg,) = owners
            if asg in ids:
                out[n.id] = asg
    return out


def _derived(
    nid: str,
    node_type: str,
    account: str,
    region: str,
    native: str,
    arn: str | None,
    name: str,
    attributes: dict[str, Any],
) -> NodeDraft:
    """이 파일이 짓는 최소 노드 — 부모 = 그 리전(수집 범위 안이라 리전 노드가 반드시 있다)."""
    parent = node_id("region", account, region)
    return NodeDraft(
        node=Node(
            id=nid,
            type=node_type,
            account_id=account,
            region=region,
            native_id=native,
            arn=arn,
            name=name,
            parent_id=parent,
            attributes=attributes,
        ),
        account_id=account,
        region=region,
        parents=(parent,),
        parent_types=("region",),
    )


# ------------------------------------------------------------------ 라우트 → ENI 주인(2.8.0 · P2-2)

ROUTE_ENI_ATTR = "unresolved_route_targets"


@dataclass(slots=True)
class RouteLookup:
    """라우트의 `eni-` 대상 → ENI(물은 쌍에서 찾은 것) · 못 읽은 라우트 테이블 노드 id → 사유."""

    found: dict[str, Eni] = field(default_factory=dict)
    failed: dict[str, Reason] = field(default_factory=dict)
    # 라우트 테이블 노드 id → 그 테이블이 `eni-` 로 보내는 (ENI id → 목적지들)
    asked: dict[str, dict[str, set[str]]] = field(default_factory=dict)


def _eni_of(raw: dict[str, Any], account: str, region: str, vid: str) -> Eni:
    return Eni(
        eni_id=str(raw.get("NetworkInterfaceId") or ""),
        account_id=account,
        region=region,
        vpc=vid,
        zone=str(raw["AvailabilityZone"]) if raw.get("AvailabilityZone") else None,
        interface_type=str(raw.get("InterfaceType") or ""),
        requester_id=str(raw.get("RequesterId") or ""),
        description=str(raw.get("Description") or ""),
        instance_id=str((raw.get("Attachment") or {}).get("InstanceId") or "") or None,
        tags=tuple(
            sorted(
                (str(t["Key"]), str(t["Value"]))
                for t in raw.get("TagSet") or []
                if "Key" in t and "Value" in t
            )
        ),
        subnet_id=str(raw.get("SubnetId") or ""),
        groups=tuple(
            sorted(
                str(g.get("GroupId"))
                for g in raw.get("Groups") or []
                if g.get("GroupId")
            )
        ),
    )


def route_lookup(drafts: list[NodeDraft], client_factory: ClientFactory) -> RouteLookup:
    """라우트 테이블이 `eni-…` 로 보내는 라우트(NAT 인스턴스 · 방화벽 어플라이언스 · NVA)의 ENI 를 그 테이블을 수집한 쌍에서 묻는다.

    읽기 전용(`DescribeNetworkInterfaces` · `network-interface-id` 필터 · 100개씩 · 쌍마다). 🔴 거부는 예외가 아니다 — 그 쌍의 라우트
    테이블에 사유를 붙이고(`partial` · 불변식 ④) 그 라우트는 `eni_unread` 로 남는다. AWS 는 인스턴스로 가는 라우트에도
    `NetworkInterfaceId` 를 같이 준다(`route_table.py` 의 대상 키 순서) — 그래서 인스턴스 라우트도 여기서 주인을 찾는다.
    """
    out = RouteLookup()
    by_pair: dict[tuple[str, str], list[NodeDraft]] = {}
    for d in drafts:
        if d.node.type != "route_table":
            continue
        for e in d.edges:
            # 블랙홀 라우트(그 ENI·인스턴스가 지워졌다)는 묻지 않는다 — 없는 ENI 를 물어 권한만 늘고 답은 「없다」다(그대로 `unresolved_refs`)
            if (
                e.type == "route"
                and e.live
                and str(e.target_native).startswith("eni-")
                and not e.attributes.get("blackhole")
            ):
                dests = out.asked.setdefault(d.node.id, {}).setdefault(
                    e.target_native, set()
                )
                dests.update(str(x) for x in e.attributes.get("destinations") or [])
        if d.node.id in out.asked:
            by_pair.setdefault((d.account_id, d.region), []).append(d)
    for (account, region), rts in sorted(by_pair.items()):
        enis = sorted({eni for d in rts for eni in out.asked[d.node.id]})
        try:
            client = client_factory(account, region, "ec2")
            got: list[dict[str, Any]] = []
            for i in range(0, len(enis), _IP_CHUNK):
                got.extend(
                    pages(
                        client,
                        "describe_network_interfaces",
                        "NetworkInterfaces",
                        Filters=[
                            {
                                "Name": "network-interface-id",
                                "Values": enis[i : i + _IP_CHUNK],
                            }
                        ],
                    )
                )
        except (ClientError, BotoCoreError) as exc:
            reason = classify(exc, ENI_API)[1]
            for d in sorted(rts, key=lambda x: x.node.id):
                out.failed[d.node.id] = reason
                if reason not in d.node.partial_reasons:
                    d.node = replace(
                        d.node,
                        partial=True,
                        partial_reasons=d.node.partial_reasons + (reason,),
                    )
            continue
        for raw in sorted(got, key=lambda e: str(e.get("NetworkInterfaceId") or "")):
            eid = str(raw.get("NetworkInterfaceId") or "")
            if eid and eid not in out.found:
                vid = node_id("vpc", account, region, str(raw.get("VpcId") or ""))
                out.found[eid] = _eni_of(raw, account, region, vid)
    return out


def route_owners(
    kept: list[NodeDraft], ids: set[str], by_native: dict[str, str], rl: RouteLookup
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """(ENI id → 주인 노드 id, 라우트 테이블 노드 id → 못 이은 줄 「<eni> <까닭> <목적지>…」).

    주인 순서는 LB IP 대상과 같다(`_eni_owner` — 엔드포인트 → RDS → EKS → EC2 → 다른 LB → VPC Lambda → EIC·SageMaker).
    🔴 못 찾으면 지어내지 않는다 — 라우트 테이블의 `unresolved_refs` 에 ENI 가 그대로 남고(ADR 0002 §5) 까닭을 이 줄로 단다.
    """
    ix = _Index(kept, ids, by_native)
    owners: dict[str, str] = {}
    why: dict[str, set[str]] = {}
    for rt in sorted(rl.asked):
        for eid, dests in sorted(rl.asked[rt].items()):
            eni = rl.found.get(eid)
            if eni is None:
                code = "eni_unread" if rt in rl.failed else "no_eni"
                owner = None
            else:
                owner, code = _eni_owner(eni, ix)
            if owner:
                owners[eid] = owner
                continue
            why.setdefault(rt, set()).add(
                " ".join([eid, code or "not_collected", *sorted(dests)])
            )
    return owners, why
