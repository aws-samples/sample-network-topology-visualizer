"""core_network(AWS Cloud WAN 코어 네트워크) 수집 — 「이음매 2」 · #129(스키마 2.11.0 · ADR 0002 §13-2 · target-gap §C 15) · **다섯 번째 글로벌 리소스**.

Global Accelerator 가 세운 규약을 그대로 쓴다(`global_accelerator.py` 머리말): id 의 리전 자리 = `GLOBAL` · `region` = null · 부모 = 계정 노드.

🔴 **API 는 us-west-2 에만 있다** — Network Manager(Cloud WAN 의 API)는 글로벌 서비스이고 홈 리전이 미국 서부(오리건)다(AWS 문서
「Network Manager 는 us-west-2 에서 호스팅된다」 · botocore 도 어느 리전으로 만들든 `networkmanager.us-west-2.amazonaws.com` 으로 보낸다 —
2026-10-21 이 리포의 botocore 1.43 으로 확인). 그래서 `Spec.region = "us-west-2"` 로 클라이언트를 그 리전에 고정하고(Global Accelerator 와 같은 문 ·
`collect._fetch`), IAM 도 스택 B 의 **별 문장**(`ReadCloudWan` · `aws:RequestedRegion` = us-west-2)이다.

엣지 `core_network_attachment`(코어 네트워크 ↔ VPC · 방향 없음): `ListAttachments(CoreNetworkId=…)` 의 VPC 연결(`AttachmentType` = VPC ·
`ResourceArn` = `arn:aws:ec2:<리전>:<계정>:vpc/<vpc-id>`) — ARN 이 계정·리전을 말하므로 VPC 노드 id 를 그것으로 짓는다(`EdgeDraft.by_id`).
같은 VPC 의 연결이 여럿이면 선 하나(`attachment_ids`·`segments`·`edge_locations`). 🔴 못 이음은 지어내지 않는다 — `unresolved_attachments` =
「<ref> <까닭> [리전]」: 수집 범위 밖 계정·리전의 VPC(`not_in_scope <리전>`) · 이 스냅샷에 없는 VPC(`not_collected` · 3패스) · 아직 선으로 그리지 않는
연결 종류(`<종류> <엣지 위치>` — Connect · Site-to-Site VPN · TGW 라우트 테이블 · DX 게이트웨이 · 까닭 = 그 종류 소문자). 죽은 연결(`DELETING`·
`REJECTED`·`FAILED`)은 선을 만들지 않는다(`base.DEAD_STATES` · ADR 0002 §3).

라우트의 `CoreNetworkArn` 대상은 `route_table.py` 가 이 노드의 native id(`core-network-…`)로 바꿔 3패스가 잇는다.

🔵 **소유 계정이 수집 범위 안이면 소유 계정의 응답만 노드로 만든다** — `ListCoreNetworks` 는 RAM 으로 공유받은 계정에서도 그 코어 네트워크를 돌려주는데,
그 계정의 `ListAttachments` 는 자기 연결만 본다. 먼저 만난 뷰가 남는 규칙(`collect._resolve`)이 공유받은 계정의 뷰를 남기면 소유 계정이 보는 다른 계정의
연결이 사라진다. 소유 계정이 범위 밖이면 조회한 계정이 본 것을 그 계정에 귀속시킨다(`owner_account_id` · ADR 0002 §2).

🔴 구성만 싣는다 — 코어 네트워크·연결의 `State`(정책 바꿀 때마다 `UPDATING` · 시시각각)는 노드에 싣지 않는다(연결 선의 `state` 만 — TGW 어태치먼트 선과 같다).
🔴 188 에는 Cloud WAN 이 없다 — 가짜 세계(`engine/tests/rel_world.py`)로만 쟀다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import GLOBAL, Node, node_id
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    Spec,
    alive,
    classify,
    draft,
    pages,
    tags_of,
)
from .base import sorted_strs as _sorted

API_REGION = "us-west-2"
SPEC = Spec(
    "networkmanager",
    "list_core_networks",
    "CoreNetworks",
    "networkmanager:ListCoreNetworks",
    region=API_REGION,
)
ATTACHMENTS_API = "networkmanager:ListAttachments"
_ATT = "_Attachments"
WHY_ATTR = "unresolved_attachments"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    out: list[Fetched] = []
    for it in items:
        try:
            got = pages(
                client,
                "list_attachments",
                "Attachments",
                CoreNetworkId=str(it.payload["CoreNetworkId"]),
            )
        except (ClientError, BotoCoreError) as exc:
            # 연결 목록을 못 읽었다 — 노드는 남기고 「일부 못 읽음」(키를 만들지 않는다 · 선 없음 · 불변식 ④)
            out.append(
                Fetched(it.payload, it.reasons + (classify(exc, ATTACHMENTS_API)[1],))
            )
            continue
        out.append(Fetched({**it.payload, _ATT: got}, it.reasons))
    return out


def _vpc_ref(arn: str) -> tuple[str, str, str] | None:
    """VPC ARN → (리전, 계정, vpc id). 꼴이 아니면 None."""
    parts = str(arn).split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[2] != "ec2":
        return None
    kind, _, vid = parts[5].partition("/")
    if kind != "vpc" or not vid:
        return None
    return parts[3], parts[4], vid


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    raw_owner = str(item.get("OwnerAccountId") or "")
    if raw_owner and raw_owner != ctx.account_id and raw_owner in ctx.scope_accounts:
        # 소유 계정이 범위 안 — 그 계정의 응답이 연결 전부를 본다(머리말 🔵). 이 뷰는 버린다(coverage 의 항목 수는 목록 그대로)
        return None
    owner, extra = ctx.attribute(item, "OwnerAccountId")
    native = str(item["CoreNetworkId"])
    nid = node_id("core_network", owner, GLOBAL, native)
    tags = tags_of(item)
    attributes: dict[str, Any] = {
        "global_network_id": item.get("GlobalNetworkId"),
        "description": item.get("Description") or None,
        **extra,
    }
    edges: list[EdgeDraft] = []
    atts = item.get(_ATT)
    if atts is not None:
        per: dict[str, dict[str, Any]] = {}
        why: set[str] = set()
        others: set[str] = set()
        locations: set[str] = set()
        live_n = 0
        for a in sorted(atts, key=lambda x: str(x.get("AttachmentId"))):
            state = str(a.get("State") or "").lower()
            if not alive(state):
                continue  # 죽은 연결은 관계가 아니다(ADR 0002 §3)
            live_n += 1
            kind = str(a.get("AttachmentType") or "")
            loc = str(a.get("EdgeLocation") or "")
            if loc:
                locations.add(loc)
            aid = str(a.get("AttachmentId") or "?")
            ref = _vpc_ref(str(a.get("ResourceArn") or "")) if kind == "VPC" else None
            if ref is None:
                # 아직 선으로 그리지 않는 연결 종류(Connect · VPN · TGW 라우트 테이블 · DX 게이트웨이) — 정보창이 말한다
                others.add(f"{aid} {kind.lower() or 'unknown'} {loc or '?'}".rstrip())
                continue
            region, account, vid = ref
            if account not in ctx.scope_accounts or region not in ctx.scope_regions:
                why.add(f"{vid} not_in_scope {region}")
                continue
            target = node_id("vpc", account, region, vid)
            slot = per.setdefault(
                target,
                {
                    "ids": set(),
                    "segments": set(),
                    "locations": set(),
                    "states": set(),
                    "ref": vid,
                },
            )
            slot["ids"].add(aid)
            if a.get("SegmentName"):
                slot["segments"].add(str(a["SegmentName"]))
            if loc:
                slot["locations"].add(loc)
            slot["states"].add(state)
        for target, v in sorted(per.items()):
            edges.append(
                EdgeDraft(
                    "core_network_attachment",
                    nid,
                    target,
                    None,
                    False,  # 코어 네트워크 ↔ VPC(TGW 어태치먼트와 같다 — 방향 없음)
                    {
                        "attachment_ids": sorted(v["ids"]),
                        "segments": sorted(v["segments"]),
                        "edge_locations": sorted(v["locations"]),
                        "state": ",".join(sorted(v["states"])),
                    },
                    by_id=True,
                    ref=v["ref"],
                    why_attr=WHY_ATTR,
                )
            )
        # 살아 있는 연결 수(종류 · 범위 무관) — 정보창 머리 숫자
        attributes["attachment_count"] = live_n
        attributes["edge_locations"] = _sorted(locations)
        attributes["other_attachments"] = sorted(others)
        if why:
            attributes[WHY_ATTR] = sorted(why)
    node = Node(
        id=nid,
        type="core_network",
        account_id=owner,
        region=None,
        native_id=native,
        arn=item.get("CoreNetworkArn"),
        name=tags.get("Name") or native,
        attributes={k: v for k, v in attributes.items() if v is not None},
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(node_id("account", owner),),
        parent_types=("account",),
        edges=edges,
    )


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
