"""lattice_service(Amazon VPC Lattice 서비스) 수집 — 「이음매 2」 · #129(스키마 2.11.0 · ADR 0002 §13-3 · target-gap §C 16).

서비스는 **리전 자원**이다(부모 = 리전). 요청을 넘기는 곳은 대상 그룹이다 — 그 쌍에서 한 번 `ListTargetGroups` 로 대상 그룹 전부를 읽고
(요약의 `serviceArns` = 그 그룹을 쓰는 서비스 · 리스너·규칙을 따로 묻지 않는다), 이 쌍의 서비스가 쓰는 그룹마다 `ListTargets` 로 등록 대상을 읽는다.

엣지 `lattice_target`(서비스 → 대상 · 방향 있음 · (서비스, 대상 노드)마다 하나 — LB 대상 선과 같은 묶음 규칙):
- `INSTANCE` → EC2(인스턴스 id 색인) · `ALB` → 그 ALB(ARN 의 계정·리전·이름 · `EdgeDraft.by_id`) · `LAMBDA` → 그 함수(ARN 의 계정·리전·이름)
- `IP` → 선을 짓지 않는다 — `unresolved_lattice_targets` 「<ip> ip_target」(사설 IP 의 주인을 찾는 ENI 조회는 아직 하지 않는다 · 정보창이 IP 를 말한다)
- 모르는 꼴 → 「<id> unknown_type」 · 이 스냅샷에 없는 대상 → 「<ref> not_collected」(3패스)
속성 `target_groups` = 「<이름> <종류> <프로토콜> <포트>」(그 서비스가 쓰는 그룹 · 이름 순) · 등록이 0 인 그룹 = `empty_target_groups`.

🔴 대상 헬스(`ListTargets` 의 `status`·`reasonCode`)는 싣지 않는다(시시각각 · 상태 단의 몫 · 불변식 ②). 거부: `ListTargetGroups` = 그 쌍의 서비스 전부의 사유 ·
`ListTargets` = 그 그룹을 쓰는 서비스의 사유 — 둘 다 키를 만들지 않는다(선 없이 사유만 · 불변식 ④). 188 에는 Lattice 가 없다 — 가짜 세계로만 쟀다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .api_gateway import lb_ref
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
)
from .lattice_service_network import arn_account

SPEC = Spec("vpc-lattice", "list_services", "items", "vpc-lattice:ListServices")
TG_API = "vpc-lattice:ListTargetGroups"
TARGETS_API = "vpc-lattice:ListTargets"
_TGS = "_TargetGroups"
WHY_ATTR = "unresolved_lattice_targets"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    if not items:
        return items  # 서비스가 없는 쌍은 대상 그룹을 묻지 않는다(최소 호출)
    try:
        groups = pages(client, "list_target_groups", "items")
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, TG_API)[1]
        return [Fetched(it.payload, it.reasons + (reason,)) for it in items]
    arns = {str(it.payload.get("arn") or "") for it in items}
    targets: dict[str, list[dict[str, Any]] | Reason] = {}
    for g in sorted(groups, key=lambda x: str(x.get("id"))):
        if not arns & {str(a) for a in g.get("serviceArns") or []}:
            continue  # 이 쌍의 서비스가 쓰지 않는 그룹은 대상을 묻지 않는다
        try:
            targets[str(g["id"])] = pages(
                client, "list_targets", "items", targetGroupIdentifier=str(g["id"])
            )
        except (ClientError, BotoCoreError) as exc:
            targets[str(g["id"])] = classify(exc, TARGETS_API)[1]
    out: list[Fetched] = []
    for it in items:
        arn = str(it.payload.get("arn") or "")
        mine = [
            g for g in groups if arn in {str(a) for a in g.get("serviceArns") or []}
        ]
        reasons = list(it.reasons)
        rows: list[dict[str, Any]] = []
        for g in sorted(mine, key=lambda x: (str(x.get("name")), str(x.get("id")))):
            got = targets.get(str(g["id"]))
            if isinstance(got, Reason):
                if got not in reasons:
                    reasons.append(got)
                rows.append({**g, "_Targets": None})
            else:
                rows.append({**g, "_Targets": got or []})
        out.append(Fetched({**it.payload, _TGS: rows}, tuple(reasons)))
    return out


def _target(kind: str, tid: str, ctx: Ctx) -> tuple[str, str | None, bool, str] | str:
    """(대상 키, 대상 노드 타입, by_id, ref) — 못 잇는 꼴이면 까닭 코드 글자."""
    if kind == "instance" and tid.startswith("i-"):
        return tid, "ec2_instance", False, tid
    if kind == "alb":
        ref = lb_ref(tid)
        if ref is None:
            return "unknown_type"
        region, account, name = ref
        return node_id("load_balancer", account, region, name), None, True, name
    if kind == "lambda":
        parts = tid.split(":")
        # arn:aws:lambda:<리전>:<계정>:function:<이름>[:<별칭·버전>]
        if len(parts) >= 7 and parts[2] == "lambda" and parts[5] == "function":
            return (
                node_id("lambda_function", parts[4], parts[3], parts[6]),
                None,
                True,
                parts[6],
            )
        return "unknown_type"
    if kind == "ip":
        return "ip_target"
    return "unknown_type"


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    raw_owner = arn_account(str(item.get("arn") or ""))
    if raw_owner and raw_owner != ctx.account_id and raw_owner in ctx.scope_accounts:
        # 공유받은 계정의 뷰 — 대상 그룹은 소유 계정에 있다(그 계정의 `ListTargetGroups` 만 그 그룹을 본다) → 소유 계정의 뷰만 노드로 만든다
        return None
    owner, extra = ctx.attribute({"OwnerId": raw_owner})
    native = str(item["id"])
    nid = node_id("lattice_service", owner, ctx.region, native)
    dns = (item.get("dnsEntry") or {}).get("domainName")
    attributes: dict[str, Any] = {
        **({"dns_name": str(dns)} if dns else {}),
        **(
            {"custom_domain_name": str(item["customDomainName"])}
            if item.get("customDomainName")
            else {}
        ),
        **extra,
    }
    edges: list[EdgeDraft] = []
    rows = item.get(_TGS)
    if rows is not None:
        attributes["target_groups"] = sorted(
            f"{g.get('name') or g.get('id')} {str(g.get('type') or '?').lower()} "
            f"{g.get('protocol') or '-'} {g.get('port') if g.get('port') is not None else '-'}"
            for g in rows
        )
        empty = sorted(
            str(g.get("name") or g.get("id")) for g in rows if g["_Targets"] == []
        )
        if empty:
            attributes["empty_target_groups"] = empty
        per: dict[tuple[str, str | None, bool, str], dict[str, Any]] = {}
        why: set[str] = set()
        for g in rows:
            if g["_Targets"] is None:
                continue  # 이 그룹의 대상을 못 읽었다 — 사유는 노드에 있다
            kind = str(g.get("type") or "").lower()
            for t in g["_Targets"]:
                tid = str(t.get("id") or "")
                got = _target(kind, tid, ctx)
                if isinstance(got, str):
                    why.add(f"{tid} {got}")
                    continue
                slot = per.setdefault(got, {"tgs": set(), "ports": set(), "kind": kind})
                slot["tgs"].add(str(g.get("name") or g.get("id")))
                port = t.get("port") if t.get("port") is not None else g.get("port")
                if port is not None:
                    slot["ports"].add(int(port))
        for (target, ttype, by_id, ref), v in sorted(
            per.items(), key=lambda x: (x[0][0], x[0][3])
        ):
            edges.append(
                EdgeDraft(
                    "lattice_target",
                    nid,
                    target,
                    ttype,
                    True,  # 서비스 → 요청을 받는 대상
                    {
                        "target_groups": sorted(v["tgs"]),
                        "ports": sorted(v["ports"]),
                        "target_type": v["kind"],
                    },
                    by_id=by_id,
                    ref=ref,
                    why_attr=WHY_ATTR,
                )
            )
        if why:
            attributes[WHY_ATTR] = sorted(why)
    node = Node(
        id=nid,
        type="lattice_service",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("arn"),
        name=item.get("name") or native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
