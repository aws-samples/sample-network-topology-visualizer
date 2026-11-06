"""lattice_service_network(Amazon VPC Lattice 서비스 네트워크) 수집 — 「이음매 2」 · #129(스키마 2.11.0 · ADR 0002 §13-3 · target-gap §C 16).

서비스 네트워크는 **리전 자원**이다(VPC 에 살지 않는다 · 부모 = 리전). 그 네트워크에 붙은 VPC 와 서비스를 후속 호출 둘로 읽는다:

- `ListServiceNetworkVpcAssociations(serviceNetworkIdentifier=…)` → 엣지 `lattice_vpc_association`(서비스 네트워크 ↔ VPC · 방향 없음 · vpc id 로 찾는다 —
  VPC id 는 계정 사이에 겹치지 않는다). 그 VPC 안의 클라이언트가 이 네트워크의 서비스를 이름으로 부른다.
- `ListServiceNetworkServiceAssociations(serviceNetworkIdentifier=…)` → 엣지 `lattice_service_association`(서비스 네트워크 ↔ 서비스 · 화면은 선이 아니라
  두 정보창 · `docs/canvas-principles.md` §17.40).

🔴 못 이음은 지어내지 않는다 — `unresolved_vpc_associations` = 「<vpc id> not_collected」(3패스 · 이 스냅샷에 없는 VPC) · 서비스도 같다
(`unresolved_service_associations`). 죽은 연관(`DELETE_IN_PROGRESS`·`CREATE_FAILED`)은 선을 만들지 않는다. 후속 호출 거부 = 그 네트워크의 사유
(키를 만들지 않는다 · 선 없음 · 불변식 ④) — 「붙은 VPC 없음」(속성 `vpc_association_count` = 0)과 「못 읽음」(키 없음 + `partial`)이 같은 모양이면 안 된다.

🔵 소유 계정(ARN 의 계정)이 범위 안이면 그 계정에 귀속시킨다(RAM 공유 · ADR 0002 §2 `Ctx.attribute`) — 공유받은 계정의 뷰는 자기 연관만 본다(코어 네트워크와
같은 이유 · `core_network.py` 머리말 🔵) → 소유 계정의 뷰만 노드로 만든다.

🔴 연관·네트워크의 `status` 는 연관 선의 `state` 로만 싣는다(TGW 어태치먼트 선과 같다). 188 에는 Lattice 가 없다 — 가짜 세계로만 쟀다.
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
)

SPEC = Spec(
    "vpc-lattice",
    "list_service_networks",
    "items",
    "vpc-lattice:ListServiceNetworks",
)
VPC_ASSOC_API = "vpc-lattice:ListServiceNetworkVpcAssociations"
SVC_ASSOC_API = "vpc-lattice:ListServiceNetworkServiceAssociations"
_VPCS = "_VpcAssociations"
_SVCS = "_ServiceAssociations"
VPC_WHY = "unresolved_vpc_associations"
SVC_WHY = "unresolved_service_associations"
# 연관이 아직(또는 더는) 없다 — 선을 만들지 않는다(ADR 0002 §3 · Lattice 상태는 대문자 밑줄)
DEAD = frozenset({"DELETE_IN_PROGRESS", "CREATE_FAILED"})


def arn_account(arn: str) -> str | None:
    parts = str(arn).split(":", 5)
    return parts[4] if len(parts) == 6 and parts[4] else None


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for it in items:
        sid = str(it.payload["id"])
        payload = dict(it.payload)
        reasons = list(it.reasons)
        for method, api, key in (
            ("list_service_network_vpc_associations", VPC_ASSOC_API, _VPCS),
            ("list_service_network_service_associations", SVC_ASSOC_API, _SVCS),
        ):
            try:
                payload[key] = pages(
                    client, method, "items", serviceNetworkIdentifier=sid
                )
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, api)[1])
        out.append(Fetched(payload, tuple(reasons)))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    raw_owner = arn_account(str(item.get("arn") or ""))
    if raw_owner and raw_owner != ctx.account_id and raw_owner in ctx.scope_accounts:
        return None  # 소유 계정의 뷰가 연관 전부를 본다(머리말 🔵)
    owner, extra = ctx.attribute({"OwnerId": raw_owner})
    native = str(item["id"])
    nid = node_id("lattice_service_network", owner, ctx.region, native)
    attributes: dict[str, Any] = {**extra}
    edges: list[EdgeDraft] = []
    vpcs = item.get(_VPCS)
    if vpcs is not None:
        live = [
            a for a in vpcs if str(a.get("status") or "") not in DEAD and a.get("vpcId")
        ]
        attributes["vpc_association_count"] = len({str(a["vpcId"]) for a in live})
        for a in sorted(live, key=lambda x: str(x["vpcId"])):
            edges.append(
                EdgeDraft(
                    "lattice_vpc_association",
                    nid,
                    str(a["vpcId"]),
                    "vpc",
                    False,
                    {"state": str(a.get("status") or "").lower()},
                    disc=None,
                    why_attr=VPC_WHY,
                )
            )
    svcs = item.get(_SVCS)
    if svcs is not None:
        live = [
            s
            for s in svcs
            if str(s.get("status") or "") not in DEAD and s.get("serviceId")
        ]
        attributes["service_association_count"] = len(
            {str(s["serviceId"]) for s in live}
        )
        for s in sorted(live, key=lambda x: str(x["serviceId"])):
            edges.append(
                EdgeDraft(
                    "lattice_service_association",
                    nid,
                    str(s["serviceId"]),
                    "lattice_service",
                    False,
                    {"state": str(s.get("status") or "").lower()},
                    ref=str(s.get("serviceName") or s["serviceId"]),
                    why_attr=SVC_WHY,
                )
            )
    node = Node(
        id=nid,
        type="lattice_service_network",
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
