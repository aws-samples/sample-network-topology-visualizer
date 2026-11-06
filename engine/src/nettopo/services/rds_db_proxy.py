"""rds_db_proxy(RDS 프록시) 수집 — 「이음매 2」 · 배치 D(스키마 2.10.0 · ADR 0002 §12-4 · 사용자 결정 2026-10-20 · target-gap #14).

「앱은 프록시로 붙고, 프록시가 이 DB 로 넘긴다」. 노드 하나 = 프록시 하나 · 부모 = VPC(응답의 `VpcId`) · 걸친 서브넷 = `subnet_ids`
(`VpcSubnetIds` — 프록시 엔드포인트가 그 서브넷들에 ENI 를 둔다 · 하나면 그 칸 · 여럿이면 VPC 오른쪽 열 · §17.36).

엣지 `proxy_target`(프록시 → DB · 방향 있음): 기본 대상 그룹의 `DescribeDBProxyTargets` — `Type`:
- `TRACKED_CLUSTER` · `RDS_SERVERLESS_ENDPOINT` → `rds_db_cluster`(같은 계정·리전 · `RdsResourceId` = 클러스터 식별자)
- `RDS_INSTANCE` → `rds_db_instance` — 🔴 클러스터를 대상으로 둔 프록시는 클러스터 멤버도 `RDS_INSTANCE` 로 같이 준다. 그때는 **클러스터 선 하나**만
  긋고 멤버는 속성 `target_instance_ids` 로 남긴다(멤버마다 선을 그으면 같은 길이 N 개다 — 클러스터 카드의 「멤버 인스턴스」 칸이 말한다).
노드 id 는 이 쌍의 사실로 짓는다(`EdgeDraft.by_id` — 이름 색인은 다른 계정의 같은 이름 DB 를 가린다). 못 찾으면 `unresolved_refs` = 식별자 ·
`unresolved_proxy_targets` = 「<식별자> not_collected」.

🔴 대상 조회 거부 = 그 프록시의 사유(불변식 ④ · 키 없음 · 선 없음). 🔴 구성만 싣는다 — `Status`(시시각각) · `Auth`·`SecretArn`·`RoleArn`(비밀 위치 ·
계정ID) · 대상 헬스(`TargetHealth` — 상태 단의 몫)는 싣지 않는다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    classify,
    draft,
    pages,
    tags_of,
)
from .base import sorted_strs as _sorted

SPEC = Spec("rds", "describe_db_proxies", "DBProxies", "rds:DescribeDBProxies")
TARGETS_API = "rds:DescribeDBProxyTargets"
_TARGETS = "_Targets"
WHY_ATTR = "unresolved_proxy_targets"
_CLUSTER_TYPES = ("TRACKED_CLUSTER", "RDS_SERVERLESS_ENDPOINT")


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for it in items:
        name = str(it.payload.get("DBProxyName") or "")
        try:
            got = pages(
                client, "describe_db_proxy_targets", "Targets", DBProxyName=name
            )
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(it.payload, it.reasons + (classify(exc, TARGETS_API)[1],))
            )
            continue
        targets = sorted(
            {
                (str(t.get("Type") or ""), str(t["RdsResourceId"]), t.get("Port"))
                for t in got
                if t.get("RdsResourceId")
            },
            key=lambda x: (x[0], x[1], x[2] or 0),
        )
        out.append(Fetched({**it.payload, _TARGETS: targets}, it.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["DBProxyName"])
    nid = node_id("rds_db_proxy", owner, ctx.region, native)
    attributes: dict[str, Any] = {
        "engine_family": item.get("EngineFamily"),
        "endpoint": item.get("Endpoint"),
        "require_tls": item.get("RequireTLS"),
        "idle_client_timeout": item.get("IdleClientTimeout"),
        "subnet_ids": _sorted(item.get("VpcSubnetIds") or []),
        "security_group_ids": _sorted(item.get("VpcSecurityGroupIds") or []),
    }
    edges: list[EdgeDraft] = []
    targets = item.get(_TARGETS)
    if targets is not None:
        clusters = sorted({rid for kind, rid, _ in targets if kind in _CLUSTER_TYPES})
        instances = sorted({rid for kind, rid, _ in targets if kind == "RDS_INSTANCE"})
        ports = _sorted(str(p) for _, _, p in targets if p)
        attributes["target_count"] = len({rid for _, rid, _ in targets})
        if clusters and instances:
            attributes["target_instance_ids"] = instances
        picked = (
            [("rds_db_cluster", c) for c in clusters]
            if clusters
            else [("rds_db_instance", i) for i in instances]
        )
        for kind, rid in picked:
            edges.append(
                EdgeDraft(
                    "proxy_target",
                    nid,
                    node_id(kind, owner, ctx.region, rid),
                    None,
                    True,  # 프록시 → 그 DB 로 넘긴다
                    {
                        "target_type": "cluster"
                        if kind == "rds_db_cluster"
                        else "instance",
                        "ports": [int(p) for p in ports],
                    },
                    by_id=True,
                    ref=rid,
                    why_attr=WHY_ATTR,
                )
            )
    tags = {
        str(t["Key"]): str(t["Value"])
        for t in item.get("TagList") or []
        if "Key" in t and "Value" in t
    } or tags_of(item)
    node = Node(
        id=nid,
        type="rds_db_proxy",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("DBProxyArn"),
        name=native,
        attributes=attributes,
        tags=tags,
    )
    parents: list[str] = []
    types: list[str] = []
    if item.get("VpcId"):
        parents.append(node_id("vpc", owner, ctx.region, str(item["VpcId"])))
        types.append("vpc")
    parents.append(ctx.region_node(owner))
    types.append("region")
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(types),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
