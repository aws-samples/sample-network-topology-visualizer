"""Direct Connect 연결(dedicated · hosted) 수집 — 「이음매 2」, 2차 리소스 첫 종류(ADR 0010).

리전 리소스다. `DescribeConnections` 는 **그 리전 엔드포인트의 연결만** 돌려준다
(`docs/resource-catalog.md` §5.3). 부모는 리전 — 연결은 VPC 밖(온프렘 ↔ AWS 경계)에 있고
VPC 를 지워도 남는다(ADR 0002 §1 의 IGW·TGW 와 같은 규칙).

hosted 연결도 같은 API 에 같은 모양으로 나온다(`DescribeHostedConnections` 는 파트너 전용).
hosted 임을 알리는 명시 필드는 없어 `partner_name` 을 그대로 실을 뿐 판정하지 않는다.

🔴 **비밀·용량 카운터·상태는 싣지 않는다** — `macSecKeys[]`(CKN·secretARN) · `loaIssueTime` ·
`rateLimiterStatus` · `prefixPool*`(§5.4 「뺄 것」). MACsec 은 **구성**인 `encryption_mode` 로만
말한다. `portEncryptionStatus`(Encryption Up/Down)는 시시각각 바뀌는 상태라 관측 계약(ADR 0006)
의 몫이다(`load_balancer.py` 의 타깃 헬스와 같은 이유). `connectionState` 는 다른 종류의 `state`
와 같은 수명주기 필드라 싣는다.
🔴 **VIF 는 이 파일이 아니다.** 연결 → VIF 는 부모 관계이고 VIF 응답이 `connectionId` 를 들고
있으므로 `direct_connect_virtual_interface.py` 가 부모 후보로 이 노드를 가리킨다.
"""

from __future__ import annotations

from typing import Any

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft, gone, tags_of

# IAM 접두는 `directconnect:`. 응답 키는 소문자 camelCase 다(`connections`, `ownerAccount`).
# 🔴 boto3 페이지네이터가 없다 — `base.pages` 가 `nextToken` 을 손으로 돌린다.
SPEC = Spec(
    "directconnect",
    "describe_connections",
    "connections",
    "directconnect:DescribeConnections",
)


def dx_tags(item: dict[str, Any]) -> dict[str, str]:
    """Direct Connect 응답의 `tags[].key/value`(EC2 와 표기가 다르다)."""
    return tags_of(item, "tags", "key", "value")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("direct_connect_connection", item.get("connectionState")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = dx_tags(item)
    owner, extra = ctx.attribute(item, "ownerAccount")
    native = item["connectionId"]
    node = Node(
        id=node_id("direct_connect_connection", owner, ctx.region, native),
        type="direct_connect_connection",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name") or (item.get("connectionName") or None),
        attributes={
            "state": item.get("connectionState"),
            "location": item.get("location"),
            "bandwidth": item.get("bandwidth"),
            "vlan": item.get("vlan"),
            "lag_id": item.get("lagId"),
            "partner_name": item.get("partnerName"),
            "provider_name": item.get("providerName"),
            # 같은 `aws_device` 의 연결 둘은 단일 장비다 → 복원력 판정의 근거(§5.4-3).
            "aws_device": item.get("awsDeviceV2"),
            "aws_logical_device_id": item.get("awsLogicalDeviceId"),
            "has_logical_redundancy": item.get("hasLogicalRedundancy"),
            "jumbo_frame_capable": item.get("jumboFrameCapable"),
            "macsec_capable": item.get("macSecCapable"),
            "encryption_mode": item.get("encryptionMode"),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
    )


SERVICE = Service(spec=SPEC, build=build)
