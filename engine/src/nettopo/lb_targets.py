"""Resolve load balancer targets and route ENI targets to resources.

Resolve private IP owners from ENIs in the target VPC and collected
resources. Omit unresolved relationships and return references and
reasons.
Use registrations for topology and target health for observations.
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
_IP_CHUNK = 100
_OUTSIDE = "all"
_EKS_TAGS = (
    "cluster.k8s.amazonaws.com/name",
    "eks:cluster-name",
    "aws:eks:cluster-name",
)
_ELB_DESC = re.compile(r"^ELB (?:app|net|gwy)/([^/]+)/")
_TOKEN = re.compile(r"\b((?:vpce|eice)-[0-9a-f]{8,})\b")
_LAMBDA_DESC = re.compile(
    r"^AWS Lambda VPC ENI-(.+)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass(frozen=True, slots=True)
class KnownLambda:

    """Store Lambda placements used to resolve ENI owners.

    node_id: Lambda resource's Node.id
    account_id: Lambda resource's account ID
    region: Lambda resource's region
    name: Function name
    arn: Function ARN
    subnet_ids: Subnet IDs configured on the function
    security_group_ids: Security group IDs configured on the function
    """
    node_id: str
    account_id: str
    region: str
    name: str
    arn: str | None
    subnet_ids: frozenset[str]
    security_group_ids: frozenset[str]


def structure_lambdas(topology: dict[str, Any]) -> tuple[KnownLambda, ...]:
    """Return Lambda placements from topology JSON for ENI owner
    resolution.
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

    """Store ENI details used to resolve registered and route target
    owners.

    eni_id: AWS ENI ID
    account_id: Account that queried the ENI
    region: Region where the ENI was queried
    vpc: Node.id of the ENI's VPC
    zone: Availability Zone
    interface_type: ENI type
    requester_id: ID of the principal that requested ENI creation
    description: ENI description from the API response
    instance_id: Attached EC2 instance ID
    tags: ENI tag keys and values
    subnet_id: ENI's subnet ID
    groups: Security group IDs configured on the ENI
    """
    eni_id: str
    account_id: str
    region: str
    vpc: str
    zone: str | None
    interface_type: str
    requester_id: str
    description: str
    instance_id: str | None
    tags: tuple[tuple[str, str], ...]
    subnet_id: str = ""
    groups: tuple[str, ...] = ()


@dataclass(slots=True)
class Lookup:

    """Store ENI lookup results for registered private IP targets.

    found: ENIs by (VPC Node.id, private IP)
    failed: Lookup failure Reason by VPC Node.id
    containing: In-scope VPC Node.id containing IPs registered outside
        the VPC
    """
    found: dict[tuple[str, str], list[Eni]] = field(default_factory=dict)
    failed: dict[str, Reason] = field(default_factory=dict)
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
    first = d.parents[0] if d.parents else None
    return first if first and first.startswith("vpc/") else None


def _where(
    ref: TargetRef, d: NodeDraft, networks: list[tuple[str, ipaddress.IPv4Network]]
) -> tuple[str, ...]:
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
    d = drafts_by_id.get(vpc_node)
    _, owner, region, native = split_node_id(vpc_node)
    if region is None or native is None:
        return None
    if d is not None:
        return d.account_id, d.region, native
    return owner, region, native


def lookup(drafts: list[NodeDraft], client_factory: ClientFactory) -> Lookup:
    """Query ENIs for registered private IP targets in their VPCs.

    drafts: Collected resources and load balancer target references
    client_factory: Function that creates EC2 clients by account and
        region

    Record failures in Lookup.failed and the requesting
    Node.partial_reasons.
    """
    result = Lookup()
    networks = _vpc_networks(drafts)
    drafts_by_id = {d.node.id: d for d in drafts if d.node.type == "vpc"}
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
                continue
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
    for vid, reason in sorted(result.failed.items()):
        for d in askers.get(vid, []):
            if reason not in d.node.partial_reasons:
                d.node = replace(
                    d.node,
                    partial=True,
                    partial_reasons=d.node.partial_reasons + (reason,),
                )
    return result




@dataclass(slots=True)
class _Bucket:

    groups: set[str] = field(default_factory=set)
    ports: set[int] = field(default_factory=set)
    kinds: set[str] = field(default_factory=set)
    registered: set[str] = field(
        default_factory=set
    )
    count: int = 0
    healthy: int = 0
    unhealthy: int = 0
    detail: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k, _ in TARGET_HEALTH_DETAIL}
    )
    exits: set[str] | None = (
        None
    )


@dataclass(slots=True)
class Joined:
    """Store resolved load balancer targets and unresolved references.

    nodes: Additional resource nodes derived from target references
    edges: Relationships between load balancers and target resources
    unresolved: Unresolved references by load balancer Node.id
    reasons: References and failure reasons by load balancer Node.id
    """
    nodes: list[NodeDraft]
    edges: list[Edge]
    unresolved: dict[str, set[str]]
    reasons: dict[str, set[str]]


def _arn_parts(arn: str) -> tuple[str, str, str] | None:
    parts = arn.split(":", 5)
    if len(parts) != 6 or parts[0] != "arn":
        return None
    return parts[3], parts[4], parts[5]


def _owner_code(eni: Eni) -> str:
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

    def __init__(
        self,
        kept: list[NodeDraft],
        ids: set[str],
        by_native: dict[str, str],
        seeded: tuple[KnownLambda, ...] = (),
    ) -> None:
        self.seeded = {k.node_id: k for k in seeded if k.node_id not in ids}
        self.ids = ids | set(self.seeded) if self.seeded else ids
        self.by_native = by_native
        self.ecs_by_tg: dict[str, list[str]] = {}
        self.eks_by_tg: dict[str, set[str]] = {}
        self.rds_by_zone: dict[tuple[str, str], list[str]] = {}
        self.rds_cluster: dict[str, str] = {}
        self.sm_natives: dict[str, list[str]] = {}
        self.lambdas: list[tuple[str, frozenset[str], frozenset[str]]] = []
        self.msk_by_eni: dict[str, list[str]] = {}
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
        if target_type not in ("instance", "ip"):
            return None
        ecs = sorted(set(self.ecs_by_tg.get(group_arn) or []))
        if len(ecs) == 1:
            return ecs[0]
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
                        continue
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
    """Return the ENI owner's Node.id or the reason it could not be
    resolved.

    Return (Node.id, reason code), with Node.id=None for an unresolved
    owner.
    Apply the same rules to load balancer and route targets.
    """
    desc = eni.description
    for tok in _TOKEN.findall(desc):
        if tok.startswith("vpce-"):
            hit = ix.native(tok, "vpc_endpoint")
            if hit:
                return hit, None
    if eni.interface_type in ("vpc_endpoint", "gateway_load_balancer_endpoint"):
        return None, "vpc_endpoint"
    if eni.requester_id == "amazon-rds" or desc.startswith("RDSNetworkInterface"):
        dbs = sorted(set(ix.rds_by_zone.get((eni.vpc, eni.zone or ""), [])))
        if len(dbs) == 1:
            return dbs[0], None
        clusters = {ix.rds_cluster.get(d) for d in dbs}
        if len(dbs) > 1 and len(clusters) == 1 and None not in clusters:
            hit = ix.known(str(next(iter(clusters))))
            if hit:
                return hit, None
        return None, "rds"
    msk = sorted(set(ix.msk_by_eni.get(eni.eni_id) or []))
    if len(msk) == 1:
        return msk[0], None
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
    if eni.requester_id == "amazon-elasticache" or desc.lower().startswith(
        "elasticache"
    ):
        hit = _word_owner(desc, "elasticache_cluster", eni, ix)
        return (hit, None) if hit else (None, "elasticache")
    low = desc.lower()
    if (
        eni.requester_id == "amazon-elasticsearch"
        or desc.startswith("ES ")
        or "opensearch" in low
    ):
        hit = _word_owner(desc, "opensearch_domain", eni, ix)
        return (hit, None) if hit else (None, "opensearch")
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
    if eni.instance_id:
        hit = ix.native(eni.instance_id, "ec2_instance")
        if hit:
            return hit, None
    m = _ELB_DESC.match(desc)
    if m:
        hit = ix.known(node_id("load_balancer", eni.account_id, eni.region, m.group(1)))
        if hit:
            return hit, None
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
    kind = ref.target_type
    owner = ix.claimer(ref.target_group_arn, kind)
    if owner:
        return (
            owner,
            None,
        )
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
    """Return target relationships, added nodes, and unresolved target
    details.

    kept: Resources and target references to include in the result
    ids: Node.id values included in the collection result
    by_native: Mapping from AWS resource IDs to Node.id
    found: ENI lookup results for registered private IPs
    scope: Accounts and regions to collect
    with_state: Whether to include target health in relationship
        attributes
    seeded: Lambda placements from the input topology
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
            continue
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
                continue
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
    """Map collected EC2 Node.id values to their Auto Scaling group
    Node.id.

    Include only instances whose group is uniquely identified.
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



ROUTE_ENI_ATTR = "unresolved_route_targets"


@dataclass(slots=True)
class RouteLookup:

    """Store lookup results for ENIs referenced by route tables.

    found: Lookup results by ENI ID
    failed: Lookup failure Reason by route table Node.id
    asked: ENI IDs and route destinations by route table Node.id
    """
    found: dict[str, Eni] = field(default_factory=dict)
    failed: dict[str, Reason] = field(default_factory=dict)
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
    """Query route target ENIs in each route table's account and region.

    drafts: Collected route tables and route target references
    client_factory: Function that creates EC2 clients by account and
        region

    Record failures in RouteLookup.failed and the Node.partial_reasons.
    """
    out = RouteLookup()
    by_pair: dict[tuple[str, str], list[NodeDraft]] = {}
    for d in drafts:
        if d.node.type != "route_table":
            continue
        for e in d.edges:
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
    """Return route ENI owners and reasons for unresolved targets.

    kept: Resources included in the collection result
    ids: Node.id values included in the collection result
    by_native: Mapping from AWS resource IDs to Node.id
    rl: Route target ENI lookup results

    Map ENI IDs to Node.id on success. On failure, store ENI IDs, reason
    codes,
    and destinations by route table Node.id.
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
