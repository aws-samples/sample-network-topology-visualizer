"""VPC 엔드포인트 수집 — 「이음매 2」.

두 종류가 한 API 로 온다: Interface(ENI 가 서브넷에 있다)와 Gateway(라우트 테이블에 붙는다).
`endpoint_connection` 엣지는 **서브넷이 있는 것만** 만든다 — 게이트웨이 엔드포인트의 선은
라우트 테이블이 만드는 `route` 엣지다. 두 종류를 같은 선으로 그리면 Gateway 엔드포인트가
서브넷 안에 있는 것처럼 보인다.

🔵 **엔드포인트 서비스의 주인 계정**(2.8.0 · 사용자 결정 2026-10-20 P2-1) — 고객·파트너가 만든 서비스(`…vpce-svc-…`)를 쓰는
엔드포인트는 「이 엔드포인트가 결국 어느 계정의 NLB·GWLB 로 가나」가 질문이다. 소비자 쪽 `DescribeVpcEndpointServices` 는 LB 를
말하지 않지만 **주인 계정**(`Owner`)은 말한다 → `service_owner` 로 싣는다(후속 호출 `enrich` · 서비스 이름마다 한 번).
제공자 계정을 수집했으면 3패스가 그 LB 까지 잇는다(`nettopo/endpoint_providers.py` · 제공자 쪽 구성). AWS 서비스
(`com.amazonaws.<리전>.<서비스>`)는 묻지 않는다 — 주인이 `amazon` 이라는 것은 이름이 이미 말한다.
🔴 세 갈래(불변식 ④): 읽었다 = `service_owner` · 서비스가 더는 없다(`InvalidServiceName`) = 키 없음(사실 — 사유 없음) · 거부 = 키 없음 + 그
엔드포인트 `partial`.

🆕 **Bedrock 엔드포인트**(#114 · 2.9.0 · ADR 0013 「Bedrock」): 인터페이스 엔드포인트의 서비스 이름이 **확정 목록과 정확히 같고**
(`com.amazonaws.<그 리전>.<접미>` · 아래 `BEDROCK_SUFFIXES`) `DescribeVpcEndpointServices` 의 `Owner` 가 `amazon` 일 때만 속성
`aws_service: "bedrock"` 를 싣는다. 🔴 부분 문자열(`bedrock` 포함)로 가르지 않는다 — 고객·파트너 PrivateLink 이름
(`com.amazonaws.vpce.<리전>.vpce-svc-…`)도, 목록에 없는 새 이름도 일반 엔드포인트로 남는다(모르는 것을 Bedrock 이라 하지 않는다).
소유자는 위 P2-1 의 같은 후속 호출이 묻는다(Bedrock 이름도 그 호출의 대상에 더했다 · `service_owner`). 물음이 거부되면 그 엔드포인트만 「일부 못 읽음」 +
`aws_service` 없음 — 「Bedrock 이 아니다」가 아니라 「확인 못 했다」다.
"""

from __future__ import annotations

import re
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
    alive,
    classify,
    draft,
    gone,
    pages,
    tags_of,
)
from .base import sorted_strs as _sorted

SPEC = Spec("ec2", "describe_vpc_endpoints", "VpcEndpoints", "ec2:DescribeVpcEndpoints")
# 🔴 확정 목록(ADR 0013 「Bedrock 엔드포인트 서비스 이름」 — 출처 두 갈래가 같은 집합을 말할 때만 넣었다):
#   ① AWS 문서(Bedrock 사용 설명서 「인터페이스 엔드포인트」 · PrivateLink 지원 서비스 목록 · AgentCore 개발 안내서)
#   ② 실계정 `DescribeVpcEndpointServices`(2026-10-20 · 읽기 전용 프로필 · us-east-1 14개 · 전부 Owner = amazon)
# 새 이름이 생기면 **이 표와 ADR 을 같이** 고친다 — 그 전까지는 일반 엔드포인트로 보인다(지어내지 않는다).
BEDROCK_SUFFIXES = frozenset(
    {
        "bedrock",
        "bedrock-fips",
        "bedrock-runtime",
        "bedrock-runtime-fips",
        "bedrock-agent",
        "bedrock-agent-runtime",
        "bedrock-agentcore",
        "bedrock-agentcore-control",
        "bedrock-agentcore.gateway",
        "bedrock-data-automation",
        "bedrock-data-automation-fips",
        "bedrock-data-automation-runtime",
        "bedrock-data-automation-runtime-fips",
        "bedrock-mantle",
    }
)
_AWS_NAME = re.compile(r"^com\.amazonaws\.([a-z]{2}(?:-[a-z]+)+-\d+)\.(.+)$")
_OWNER = "_ServiceOwner"


def bedrock_region(name: Any) -> str | None:
    """서비스 이름이 확정 목록의 Bedrock 이름이면 그 리전, 아니면 `None` — **정확히 같을 때만**(부분 문자열 금지)."""
    m = _AWS_NAME.match(str(name or ""))
    return m.group(1) if m and m.group(2) in BEDROCK_SUFFIXES else None


SERVICE_API = "ec2:DescribeVpcEndpointServices"
# 고객·파트너가 만든 엔드포인트 서비스의 이름 꼴(`com.amazonaws.vpce.<리전>.vpce-svc-…`) — 이것만 주인을 묻는다
_CUSTOMER_SERVICE = ".vpce-svc-"


ENI_API = "ec2:DescribeNetworkInterfaces"
_ENIS = "_Enis"
_CHUNK = 100


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """서비스 주인(아래 `_enrich_owners`) + 인터페이스 엔드포인트의 서브넷별 IP·ENI(`_enrich_enis`)."""
    return _enrich_enis(client, _enrich_owners(client, items))


def _eni_ids(item: dict[str, Any]) -> list[str]:
    return sorted({str(e) for e in item.get("NetworkInterfaceIds") or [] if e})


def _enrich_enis(client: Any, items: list[Fetched]) -> list[Fetched]:
    """콘솔 「서브넷」 탭의 IPv4 주소 · 네트워크 인터페이스 id(사용자 결정 2026-10-24) — `DescribeVpcEndpoints` 는 ENI id 만 준다 →
    이미 쓰는 `ec2:DescribeNetworkInterfaces`(새 권한 아님)로 그 ENI 의 서브넷·AZ·사설 IP 를 읽는다. 🔴 거부 = 그 엔드포인트에 사유
    (불변식 ④ — 「IP 없음」이 아니라 「못 읽음」)."""
    want = sorted({e for it in items for e in _eni_ids(it.payload)})
    if not want:
        return items
    got: list[dict[str, Any]] = []
    try:
        for i in range(0, len(want), _CHUNK):
            got.extend(
                pages(
                    client,
                    "describe_network_interfaces",
                    "NetworkInterfaces",
                    Filters=[
                        {"Name": "network-interface-id", "Values": want[i : i + _CHUNK]}
                    ],
                )
            )
    except (ClientError, BotoCoreError) as exc:
        reason = classify(exc, ENI_API)[1]
        return [
            Fetched(it.payload, it.reasons + (reason,)) if _eni_ids(it.payload) else it
            for it in items
        ]
    by = {
        str(e.get("NetworkInterfaceId")): e for e in got if e.get("NetworkInterfaceId")
    }
    out: list[Fetched] = []
    for it in items:
        ids = _eni_ids(it.payload)
        if not ids:
            out.append(it)
            continue
        out.append(
            Fetched({**it.payload, _ENIS: [by[e] for e in ids if e in by]}, it.reasons)
        )
    return out


def _subnet_rows(item: dict[str, Any]) -> list[str]:
    """콘솔 「서브넷」 표 한 줄 = 「서브넷 id \t 가용 영역 \t IPv4 주소 \t 네트워크 인터페이스 id」 — 서브넷 순.

    ENI 를 못 읽었으면(키 없음 · 노드 partial) 빈 목록이 아니라 **키 자체를 만들지 않는다**(부른 쪽 `build`).
    """
    rows = []
    for e in item.get(_ENIS) or []:
        rows.append(
            "\t".join(
                (
                    str(e.get("SubnetId") or ""),
                    str(e.get("AvailabilityZone") or ""),
                    str(e.get("PrivateIpAddress") or ""),
                    str(e.get("NetworkInterfaceId") or ""),
                )
            )
        )
    return sorted(rows)


def _enrich_owners(client: Any, items: list[Fetched]) -> list[Fetched]:
    """고객·파트너 엔드포인트 서비스마다 주인 계정(`Owner`)을 한 번씩 묻는다 — 소비자 쪽 · 읽기 전용(`Describe*`).

    🔴 이름마다 따로 부른다 — 이름 하나가 없어진 서비스면 AWS 가 **호출 전체**를 `InvalidServiceName` 으로 거절한다(다른 이름까지
    「모름」이 된다). 없어진 서비스는 사실이므로 사유를 달지 않는다 · 그 밖의 거부는 그 서비스를 쓰는 엔드포인트에 사유.
    """
    # #114(2.9.0) — Bedrock 확정 이름(인터페이스 · 이 클라이언트의 리전)도 묻는다: 「주인 = amazon」 이중 확인(ADR 0013)
    here = getattr(getattr(client, "meta", None), "region_name", None)
    names = sorted(
        {
            str(it.payload.get("ServiceName"))
            for it in items
            if _CUSTOMER_SERVICE in str(it.payload.get("ServiceName") or "")
            or (
                it.payload.get("VpcEndpointType") == "Interface"
                and (r := bedrock_region(it.payload.get("ServiceName")))
                and (here is None or r == here)
            )
        }
    )
    owners: dict[str, str] = {}
    failed: dict[str, Any] = {}
    for name in names:
        try:
            got = client.describe_vpc_endpoint_services(ServiceNames=[name])
        except ClientError as exc:
            if (exc.response.get("Error") or {}).get("Code") == "InvalidServiceName":
                continue
            failed[name] = classify(exc, SERVICE_API)[1]
            continue
        except BotoCoreError as exc:
            failed[name] = classify(exc, SERVICE_API)[1]
            continue
        for detail in got.get("ServiceDetails") or []:
            if detail.get("ServiceName") == name and detail.get("Owner"):
                owners[name] = str(detail["Owner"])
    out: list[Fetched] = []
    for it in items:
        name = str(it.payload.get("ServiceName") or "")
        if name in owners:
            out.append(Fetched({**it.payload, _OWNER: owners[name]}, it.reasons))
        elif name in failed:
            out.append(Fetched(it.payload, it.reasons + (failed[name],)))
        else:
            out.append(it)
    return out


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft | None:
    if gone("vpc_endpoint", item.get("State")):
        # AWS 가 「더는 없다」고 말하는 자원 — 노드로 만들지 않는다(`base.GONE_STATES`).
        return None
    tags = tags_of(item)
    owner, extra = ctx.attribute(item)
    native = item["VpcEndpointId"]
    nid = node_id("vpc_endpoint", owner, ctx.region, native)
    state = str(item.get("State") or "")
    subnets = _sorted(item.get("SubnetIds") or [])
    edges = [
        EdgeDraft(
            "endpoint_connection",
            nid,
            subnet,
            "subnet",
            False,
            {"state": state},
            live=alive(state),
        )
        for subnet in subnets
    ]
    node = Node(
        id=nid,
        type="vpc_endpoint",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        name=tags.get("Name"),
        attributes={
            "state": state,
            "endpoint_type": item.get("VpcEndpointType"),
            "service_name": item.get("ServiceName"),
            "private_dns_enabled": bool(item.get("PrivateDnsEnabled", False)),
            "subnet_ids": subnets,
            # 게이트웨이 엔드포인트는 서브넷이 아니라 라우트 테이블에 붙는다 — 선은 route 엣지가 만든다.
            "route_table_ids": _sorted(item.get("RouteTableIds") or []),
            # 콘솔 「보안 그룹」 탭(인터페이스 엔드포인트) — 「id \t 이름」 · id 순
            "security_groups": sorted(
                f"{g.get('GroupId')}\t{g.get('GroupName') or ''}"
                for g in item.get("Groups") or []
                if g.get("GroupId")
            ),
            "network_interface_ids": _eni_ids(item),
            "ip_address_type": item.get("IpAddressType"),
            **({"subnet_addresses": _subnet_rows(item)} if _ENIS in item else {}),
            # 2.8.0(P2-1) — 그 엔드포인트 서비스의 주인 계정(고객·파트너 서비스만 · 못 읽었거나 없어진 서비스면 키 없음)
            **({"service_owner": item[_OWNER]} if _OWNER in item else {}),
            **_bedrock(item, ctx),
            **extra,
        },
        tags=tags,
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(
            node_id("vpc", owner, ctx.region, str(item.get("VpcId"))),
            ctx.region_node(owner),
        ),
        parent_types=("vpc", "region"),
        edges=edges,
    )


def _bedrock(item: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    """정확한 Bedrock 이름 + 이 리전 + 읽은 소유자가 `amazon` 이면 `aws_service` — 소유자를 못 읽었으면(키 없음) 아무것도 싣지 않는다."""
    if (
        item.get(_OWNER) == "amazon"
        and bedrock_region(item.get("ServiceName")) == ctx.region
    ):
        return {"aws_service": "bedrock"}
    return {}


SERVICE = Service(spec=SPEC, build=build, enrich=enrich)
