"""msk_cluster(Amazon MSK — Kafka 클러스터) 수집 — 「이음매 2」 · #114 · 스키마 2.9.0.

**카드 하나 = 클러스터 하나**(`ListClustersV2` 의 항목 · 프로비저닝·서버리스 둘 다). 브로커는 카드가 아니다 — 브로커 ENI 는
클러스터의 속성(`broker_eni_ids`)이고, LB 의 IP 대상이 그 ENI 면 3패스가 이 카드로 잇는다(`lb_targets.py` — **ENI id** 로 잇는다:
설명 글자 형식에 기대지 않는다).

🔴 **응답에 VPC 가 없다**(ASG·ECS 와 같다). 클라이언트 서브넷(`BrokerNodeGroupInfo.ClientSubnets` · 서버리스는 `VpcConfigs[].SubnetIds`)만
준다 → 부모 = 리전 + `subnet_ids`(걸친 서브넷). 렌더러가 서브넷으로 VPC 를 찾는다 — 서브넷이 VPC 를 말하므로 날조가 아니다.

후속 호출: 프로비저닝 클러스터마다 `ListNodes`(브로커 ENI id · 클라이언트 서브넷). 거부되면 그 클러스터만 「일부 못 읽음」 —
구성의 서브넷은 그대로 있고 브로커 ENI 키가 **없다**(LB 선은 못 잇는다 · 「브로커 0개」로 적지 않는다). 서버리스는 브로커를
고객에게 보이지 않는다 — 묻지 않는다.

🔴 **상태는 담지 않는다.** `State` 는 탐침(`state`)으로만 나간다.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, node_id
from .base import Ctx, Fetched, NodeDraft, Service, Spec, classify, draft, pages
from .base import sorted_strs as _sorted

SPEC = Spec("kafka", "list_clusters_v2", "ClusterInfoList", "kafka:ListClustersV2")
_NODES_API = "kafka:ListNodes"
_BROKERS = "_Brokers"


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """프로비저닝 클러스터마다 `ListNodes` 로 브로커 ENI 를 붙인다. 거부는 그 클러스터의 사유로만 남는다(항목은 그대로)."""
    out: list[Fetched] = []
    for f in items:
        arn = f.payload.get("ClusterArn")
        if f.payload.get("ClusterType") != "PROVISIONED" or not arn:
            out.append(f)
            continue
        try:
            nodes = pages(client, "list_nodes", "NodeInfoList", ClusterArn=arn)
        except (ClientError, BotoCoreError) as exc:
            out.append(Fetched(f.payload, f.reasons + (classify(exc, _NODES_API)[1],)))
            continue
        brokers = [
            n.get("BrokerNodeInfo") or {} for n in nodes if n.get("BrokerNodeInfo")
        ]
        out.append(Fetched({**f.payload, _BROKERS: brokers}, f.reasons))
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = str(item["ClusterName"])
    kind = item.get("ClusterType")
    attributes: dict[str, Any] = {"cluster_type": kind}
    if kind == "PROVISIONED":
        prov = item.get("Provisioned") or {}
        group = prov.get("BrokerNodeGroupInfo") or {}
        attributes.update(
            {
                "kafka_version": (prov.get("CurrentBrokerSoftwareInfo") or {}).get(
                    "KafkaVersion"
                ),
                "broker_instance_type": group.get("InstanceType"),
                "number_of_broker_nodes": prov.get("NumberOfBrokerNodes"),
                "subnet_ids": _sorted(group.get("ClientSubnets") or []),
                "security_group_ids": _sorted(group.get("SecurityGroups") or []),
                "client_broker_encryption": (
                    (prov.get("EncryptionInfo") or {}).get("EncryptionInTransit") or {}
                ).get("ClientBroker"),
            }
        )
        brokers = item.get(_BROKERS)
        if brokers is not None:
            # 읽었다 — 브로커 ENI id(정렬). 못 읽었으면 키가 없다(「브로커 0개」 ≠ 「못 읽음」)
            attributes["broker_eni_ids"] = _sorted(
                b.get("AttachedENIId") for b in brokers
            )
    elif kind == "SERVERLESS":
        vpcs = (item.get("Serverless") or {}).get("VpcConfigs") or []
        attributes["subnet_ids"] = _sorted(
            s for v in vpcs for s in v.get("SubnetIds") or []
        )
        attributes["security_group_ids"] = _sorted(
            g for v in vpcs for g in v.get("SecurityGroupIds") or []
        )

    node = Node(
        id=node_id("msk_cluster", owner, ctx.region, native),
        type="msk_cluster",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("ClusterArn"),
        name=native,
        attributes=attributes,
        tags=dict(item.get("Tags") or {}),
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


def state(item: dict[str, Any]) -> str | None:
    """상태 탐침 — 상태 단 전용(`Service.state`)."""
    value = item.get("State")
    return str(value) if value else None


SERVICE = Service(spec=SPEC, build=build, enrich=enrich, state=state)
