"""network_firewall(AWS Network Firewall) 수집 — 목록→상세 2단 호출.

FW 는 EKS 와 같은 목록→상세 모양이다: `list_firewalls` 는 이름/ARN 만 주고, `describe_firewall`
로 구성을 채운다. `describe_firewall` 은 리스트가 아니라 단일 객체
(`{"Firewall": {...}, "FirewallStatus": {...}}`)라 `pages()`(응답 키 아래가 리스트라고 가정)로
못 읽는다 — enrich 에서 직접 부른다. (EKS 와 다른 점: 목록이 이름 문자열이 아니라 이름/ARN dict 다.)

🔴 **상태는 담지 않는다.** `FirewallStatus.Status` 와 `SyncStates[az].Attachment.Status`
(READY/PROVISIONING)는 시시각각 바뀌므로 `topology.json` 에 넣으면 불변식 ②가 깨진다
(EKS `status`·LB 타깃 헬스와 같은 이유, ADR 0006 관측 계약).

🔴 **`firewall_endpoint_ids` 는 `FirewallStatus.SyncStates` 안에 있지만 상태가 아니라 구조다.**
Network Firewall 은 RT 의 `vpce-*` 타겟으로 경로에 삽입되고(Topology_2.md §6 핵심), 그
`Attachment.EndpointId` 가 RT 조인의 열쇠다. per-AZ `Status` 만 상태이므로 그것은 버리고
EndpointId 만 담는다. 🔴 `SyncStates` 는 배열이 아니라 **AZ 키 map** 이다(§6, botocore 검증) →
`.values()` 로 순회한다.

🔴 **엣지는 만들지 않는다.** FW 의 위치는 부모(VPC→리전)와 `subnet_ids` 속성으로만 표현한다 —
여러 서브넷에 걸치므로 단일 서브넷 부모를 지어내지 않는다(EKS·ASG 와 같다). RT→FW endpoint
엣지는 `vpce-*` 공통 네임스페이스(§6 미팅 안건)가 서면 그때 얹는다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft
from .base import sorted_strs as _sorted

SPEC = Spec(
    "network-firewall",
    "list_firewalls",
    "Firewalls",
    "network-firewall:ListFirewalls",
)

_DESCRIBE_API = "network-firewall:DescribeFirewall"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """FW 마다 `describe_firewall` 로 구성을 채운다. 거부는 그 FW 의 사유로만 남는다.

    목록(`list_firewalls`)이 성공했으므로 「FW 가 있다」는 확정이다 — describe 가 거부돼도 항목을
    버리지 않는다(버리면 「FW 가 없다」가 되어 거짓이다, 불변식 ④). 이름/ARN 만 든 payload 를
    남기면 `build` 가 리전 밑에 부분 노드로 세우고 `collect` 가 사유를 붙인다.

    성공 시 `Firewall`(구성)과 `SyncStates`(EndpointId 의 출처)를 payload 에 실어 보낸다 —
    상세를 그대로 넘기고, 상태(Status)를 버리는 판단은 `build` 가 한 곳에서 한다.
    """
    out: list[Fetched] = []
    for item in items:
        # list_firewalls 는 이름과 ARN 을 항상 같이 준다. 없으면 계약 위반이니 그대로 터뜨린다.
        arn = item.payload["FirewallArn"]
        name = item.payload["FirewallName"]
        try:
            resp = client.describe_firewall(FirewallArn=arn)
        except (ClientError, BotoCoreError) as exc:
            out.append(
                Fetched(
                    payload={"FirewallName": name, "FirewallArn": arn},
                    reasons=item.reasons + (classify(exc, _DESCRIBE_API)[1],),
                )
            )
            continue
        status = resp.get("FirewallStatus") or {}
        out.append(
            Fetched(
                payload={
                    "FirewallName": name,
                    "FirewallArn": arn,
                    "Firewall": resp.get("Firewall") or {},
                    "SyncStates": status.get("SyncStates"),
                    # 상태 탐침(`state`)만 읽는다 — `build` 는 이 키를 보지 않는다(구조에 상태를 안 싣는다).
                    "_status": status.get("Status"),
                },
                reasons=item.reasons,
            )
        )
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["FirewallName"]
    nid = node_id("network_firewall", owner, ctx.region, native)

    # describe 가 성공해야 `Firewall` 이 있다. 실패했으면(이름/ARN 만 있는 payload) 구성 키를
    # 만들지 않는다 — `[]`/None 을 채우면 「없음」과 「못 읽음」이 같아진다(LB 리스너와 같은 규율).
    firewall = item.get("Firewall")
    attributes: dict[str, Any] = {}
    parents: list[str] = []
    parent_types: list[str] = []
    if firewall is not None:
        attributes = {
            "firewall_policy_arn": firewall.get("FirewallPolicyArn"),
            "subnet_ids": _sorted(
                m.get("SubnetId") for m in firewall.get("SubnetMappings") or []
            ),
            # 🔴 EndpointId 는 FirewallStatus.SyncStates(AZ 키 map) 안에 있지만 상태가 아니라
            #    구조다 — RT 의 vpce-* 타겟이 이 FW 를 가리키는 조인 키(§6). Status 는 담지 않는다.
            "firewall_endpoint_ids": _sorted(
                att.get("EndpointId")
                for sync in (item.get("SyncStates") or {}).values()
                if (att := sync.get("Attachment"))
            ),
            "delete_protection": firewall.get("DeleteProtection"),
        }
        vpc_id = firewall.get("VpcId")
        if vpc_id:
            parents.append(node_id("vpc", owner, ctx.region, str(vpc_id)))
            parent_types.append("vpc")
    parents.append(ctx.region_node(owner))
    parent_types.append("region")

    node = Node(
        id=nid,
        type="network_firewall",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("FirewallArn"),
        name=native,
        attributes=attributes,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=tuple(parents),
        parent_types=tuple(parent_types),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — 상태 단 전용(`Service.state`). `DescribeFirewall` 이 거부되면 `None`."""
    value = item.get("_status")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
