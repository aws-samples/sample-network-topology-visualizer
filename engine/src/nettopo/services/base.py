"""서비스별 수집기가 공유하는 호출·초안 primitive — 「이음매 2」의 공개 면.

`docs/contract-seams.md` 「이음매 2」는 **서비스 하나 = 파일 하나**를 규약으로 정했다. 그런데
서비스 파일이 수집기 본체(`collect.py`)에서 초안 타입을 가져오면 순환 임포트가 된다
(`collect` → `services.elb` → `collect`). 그래서 서비스 파일이 필요한 것만 여기로 내렸다:
호출(`Spec`·`pages`·`classify`) · 좌표 규칙(`Ctx`) · 초안(`NodeDraft`·`EdgeDraft`·`draft`).

🔴 **여기 있는 이름은 서비스 파일이 쓰는 계약이다.** 바꾸면 세 사람이 각자 고쳐야 한다 —
`collect.py` 는 기존 사설 이름(`_Ctx` 등)으로 **별칭 임포트**해서 본체 diff 를 0 으로 둔다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from botocore.exceptions import ClientError, NoCredentialsError

from ..model import Node, Reason, node_id

# (account_id, region, service) → boto3 client. 클라이언트를 만드는 책임은 awsguard 에 있고
# 수집 코드는 팩토리를 통해서만 받는다 — 테스트가 가짜 클라이언트를 끼울 수 있는 이유이기도 하다.
ClientFactory = Callable[[str, str, str], Any]


@dataclass(frozen=True, slots=True)
class Spec:
    service: str
    method: str
    result_key: str
    api: str
    # 목록 호출에 늘 붙이는 인자(정렬된 (이름, 값) 쌍). ElastiCache 는 `ShowCacheNodeInfo=True` 를 줘야 노드(=AZ)가
    # 응답에 실린다 — 없으면 Memcached 다중 AZ 클러스터의 자리를 모른다(`elasticache_cluster.py`). 기본은 비었다.
    params: tuple[tuple[str, Any], ...] = ()
    # 🔴 **클라이언트를 이 리전으로 고정한다**(배치 D · 스키마 2.10.0) — 수집 리전과 무관하게 한 리전 엔드포인트에만 있는
    #    글로벌 서비스의 것이다. Global Accelerator 의 API 는 **us-west-2 에만** 있다(AWS 문서 「Global Accelerator API 는
    #    us-west-2 엔드포인트에서만 호출한다」) — 수집 리전(`ctx.region`)으로 클라이언트를 만들면 ap-northeast-2 만 고른 고객은
    #    가속기가 통째로 「연결 실패」다. `None` = 수집 리전 그대로(나머지 전부). `collect._fetch` 가 클라이언트를 만들 때 읽는다.
    region: str | None = None


@dataclass(frozen=True, slots=True)
class Fetched:
    """목록 호출 1건 + 후속 호출이 덧붙인 것 + 후속 호출이 **실패한 사실**.

    ELB 처럼 목록 하나로 끝나지 않는 서비스(리스너·타깃 그룹은 별 API)를 위한 그릇이다.
    후속 호출이 거부돼도 예외를 던지지 않는다 — 노드는 남기고 `reasons` 를 실어 보내
    `collect()` 가 그 노드에 `partial` 을 세운다(불변식 ④). 사유를 버리면 「리스너 없음」과
    「리스너를 못 읽음」이 같은 그림이 된다.
    """

    payload: dict[str, Any]
    reasons: tuple[Reason, ...] = ()


# 후속 호출. 클라이언트와 목록 결과를 받아 목록을 돌려준다(항목을 버리지 마라). 길이는 바뀔 수
# 있다 — EC2 는 예약을 인스턴스로 펴고, ECS 는 클러스터를 그 안의 서비스로 편다.
Enrich = Callable[[Any, list[Fetched]], list[Fetched]]


class ListingFailed(Exception):
    """후속 호출이 **목록의 일부**를 못 읽었다 — 그 종류 전체를 실패로 적게 한다.

    항목 하나의 상세가 거부되면 그 항목에 사유를 단다(`Fetched.reasons`). 그런데 ECS 처럼 첫 목록이
    부모(클러스터)이고 노드가 될 항목(서비스)을 **후속 호출로 처음 알게 되는** 종류는, 그 호출이
    거부되면 사유를 달 항목이 없다. 그 클러스터를 조용히 건너뛰면 「서비스가 없다」로 그려진다
    (불변식 ④ — 있는데 없다고 그리는 것이 최악이다). 그래서 enrich 가 이것을 던지고
    `collect.one()` 이 종류 전체를 목록 호출 실패와 똑같이 적는다.
    """

    def __init__(self, exc: Exception, api: str) -> None:
        super().__init__(str(exc))
        self.status, self.reason = classify(exc, api)


@dataclass(frozen=True, slots=True)
class Service:
    """서비스 파일 하나가 수집기에 등록하는 것 — 목록 호출 + 빌더 (+ 선택적 후속 호출)."""

    spec: Spec
    build: Callable[[dict[str, Any], Ctx], NodeDraft | None]
    enrich: Enrich | None = None
    # 🔴 **상태 탐침** — 구조 문서에 일부러 안 싣는 상태 값을 **상태 단에만** 꺼내 준다(ADR 0006 §O7
    # 「W2 → W3 판정 규칙」 규칙 1). `collect(with_state=True)` 일 때만 불리고 그 값이 노드 속성 `state`
    # 가 된다 — 구조 수집(`nettopo collect`)은 부르지 않으므로 구조 문서의 바이트가 변하지 않는다.
    # 값을 못 꺼내면 `None`(상세 응답이 거부된 부분 노드) — 지어내지 않는다.
    state: Callable[[dict[str, Any]], str | None] | None = None
    # 🔴 **상태 속성**(W3② · ADR 0006 「W3② LB 대상 상태」) — 구조 후속 호출(`enrich`)이 읽어 둔 응답에서
    # 상태 값(LB 대상 healthy/unhealthy 수)을 꺼내 노드 속성으로 싣는다. `state` 와 같은 문:
    # `collect(with_state=True)` 일 때만 → 구조 문서의 바이트는 안 바뀐다(못 꺼내면 빈 dict — 지어내지 않는다).
    # 🔵 상태 단 전용 «호출»(옛 `state_enrich`)은 2026-10-16 에 없앴다 — LB 가 유일한 사용자였고, 등록 대상 수를
    #    구조에 싣기로 하면서(사용자 결정) `DescribeTargetHealth` 가 구조 후속 호출이 됐다. 같은 응답을 두 번 묻지 않는다.
    state_attributes: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # **다른 서비스의 응답으로 좁히는 후속 호출**(W3② · RDS → EC2 ENI · `docs/canvas-principles.md` §17.12).
    # ((boto3 서비스 이름, 함수), …) — **적힌 순서대로** 돈다(2.8.0 · LB = wafv2 web ACL → ec2 엔드포인트 서비스).
    # 함수는 (그 서비스 클라이언트 | None, 항목, 진입 실패 사유 | None) 을 받는다 — 클라이언트를 못 만들었으면
    # None + 사유가 오고, 항목에 그 사유를 달아 돌려준다(항목을 버리지 마라).
    peer_enrich: tuple[
        tuple[str, Callable[[Any, list[Fetched], Reason | None], list[Fetched]]], ...
    ] = ()


STATUS_BY_ERROR_CODE: dict[str, tuple[str, str]] = {
    "AccessDenied": ("denied", "access_denied"),
    "AccessDeniedException": ("denied", "access_denied"),
    "UnauthorizedOperation": ("denied", "access_denied"),
    "Throttling": ("throttled", "throttled"),
    "ThrottlingException": ("throttled", "throttled"),
    "RequestLimitExceeded": ("throttled", "throttled"),
    "TooManyRequestsException": ("throttled", "throttled"),
    # EC2 는 옵트인하지 않은 리전에 AuthFailure 를 준다 — 권한 문제로 오진하지 마라.
    "AuthFailure": ("unsupported", "unsupported_region"),
    "OptInRequired": ("unsupported", "unsupported_region"),
    "UnsupportedOperation": ("unsupported", "unsupported_region"),
    "InvalidClientTokenId": ("error", "credentials_unavailable"),
}

MESSAGE_LIMIT = 500

# 죽은 상태 — **엣지**를 만들지 않는다(ADR 0002 §3). 없어진 경로를 선으로 그리면 사람이 「지금 붙어
# 있다」고 오판한다. 노드 쪽은 다르다: 자원이 아직 있는 전이·실패 상태는 노드를 남기고, 자원이 더는
# 없다는 상태는 아래 `GONE_STATES` 가 노드째 뺀다(§3 개정 2026-10-14). 상태 문자열은 서비스마다 대소문자가 다르므로
# (TGW 어태치먼트는 `pendingAcceptance` 처럼 camelCase) 소문자로 비교한다.
DEAD_STATES = frozenset(
    {
        "deleted",
        "deleting",
        "deleted-failed",
        "detached",
        "detaching",
        # DXGW association(ADR 0010) — 해제된 연결은 관계가 아니다
        "disassociated",
        "disassociating",
        "expired",
        "failed",
        "failing",
        "rejected",
        "rejecting",
        "rollingback",
    }
)


# 🔴 **없어진 상태 — 수집에서 노드로 만들지 않는다**(ADR 0002 §3 개정 2026-10-14 · QA B 실측).
# 스택을 지운 뒤에도 AWS 는 NAT `deleted` · EC2 `terminated` · TGW 계열 `deleted` 를 **1시간 넘게** 돌려준다.
# 노드로 남기면 화면이 지워진 자원을 한 시간 동안 그리고, 관측 규칙이 그것을 `down` 으로 읽어 이력이
# 「없어짐」 대신 「[끊김] 정상 → 이상」을 적는다(Auto Scaling 축소 · 스택 철거마다). 「방금까지 있었다」는
# 사실은 이력의 `resource_removed` + `was` 가 싣는다(ADR 0006 #11) — 구조 문서가 들 몫이 아니다.
#
# `DEAD_STATES`(엣지를 끊는 집합)를 재사용하지 않는다: 거기엔 `detached`·`failed`·`deleting` 처럼
# **자원은 아직 있는** 상태가 들어 있다. 그런 노드는 `state` 와 함께 남아야 사람이 「고장/철거 중」을 본다.
# 여기는 종류마다 「AWS 가 그 자원이 더는 없다고 말하는 값」만 적는다(소문자 비교 — `alive()` 와 같은 이유).
#   - 전이·실패(`deleting`·`shutting-down`·`stopped`·`failed`·`rejecting`·`delete-in-progress`…)는 남긴다.
#   - `rejected` 는 「수락되지 않아 생기지 않은」 자원이다 — 피어링·TGW 어태치먼트·엔드포인트·DX 연결·VIF
#     (`docs/resource-catalog.md` §5.4: hosted 연결·VIF 의 `rejected` = 수락 전 삭제).
#   - 표에 없는 종류(RDS·EKS·LB·서브넷 …)는 AWS 가 지운 항목을 목록에서 바로 뺀다 — 걸러낼 값이 없다.
# 노드를 안 만들어도 `coverage.item_count` 는 **목록 호출의 항목 수** 그대로다(`collect.py` 의 `len(got)`).
GONE_STATES: dict[str, frozenset[str]] = {
    "ec2_instance": frozenset({"terminated"}),
    "nat_gateway": frozenset({"deleted"}),
    "transit_gateway": frozenset({"deleted"}),
    "transit_gateway_route_table": frozenset({"deleted"}),
    "transit_gateway_attachment": frozenset({"deleted", "rejected"}),
    "vpc_peering_connection": frozenset({"deleted", "rejected", "expired"}),
    "vpc_endpoint": frozenset({"deleted", "rejected", "expired"}),
    "vpn_connection": frozenset({"deleted"}),
    "vpn_gateway": frozenset({"deleted"}),
    "customer_gateway": frozenset({"deleted"}),
    "direct_connect_connection": frozenset({"deleted", "rejected"}),
    "direct_connect_virtual_interface": frozenset({"deleted", "rejected"}),
    "direct_connect_gateway": frozenset({"deleted"}),
    "ec2_instance_connect_endpoint": frozenset({"delete-complete"}),
    # 지운 ECS 서비스는 `INACTIVE` 로 한동안 `DescribeServices` 에 남는다(`DRAINING` = 철거 중 → 남긴다).
    "ecs_service": frozenset({"inactive"}),
}


def gone(resource_type: str, state: Any) -> bool:
    """이 응답 항목은 AWS 가 「더는 없다」고 말하는 자원인가 — 빌더가 `None` 을 돌려줄 근거.

    상태 값이 없으면(부분 응답) 없어졌다고 단정하지 않는다 — 지어내지 않는다(불변식 ④).
    """
    return str(state or "").lower() in GONE_STATES.get(resource_type, frozenset())


def classify(exc: Exception, api: str) -> tuple[str, Reason]:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        status, reason_code = STATUS_BY_ERROR_CODE.get(code, ("error", "api_error"))
    elif isinstance(exc, NoCredentialsError):
        status, reason_code = "error", "credentials_unavailable"
    else:
        status, reason_code = "error", "api_error"
    message = _ENCODED.sub(r"\1(생략)", " ".join(str(exc).split()))[:MESSAGE_LIMIT]
    return status, Reason(code=reason_code, api=api, message=message)


# 🔴 EC2 일부 거부(`DescribeVpcAttribute` 등)는 사유 끝에 **호출마다 다른** 암호화 진단 문자열을 붙인다(`Encoded authorization failure
#    message: <매번 다른 토큰>` — 같은 거부를 두 번 수집하니 노드의 `partial_reasons` 만 달라 구조 지문이 바뀌었다). 그 토큰은 사람이
#    `sts:DecodeAuthorizationMessage` 로만 읽는 것이라 화면에 쓸모가 없고, 남기면 같은 입력이 다른 산출물이 된다(불변식 ②) → 자리만 남기고 뺀다.
_ENCODED = re.compile(r"(Encoded authorization failure message: )\S+")


# boto3 페이지네이터가 없는 API 가 응답에 싣는 토큰 키. 요청 인자 이름은 응답 키와 같다
# (Direct Connect 는 `nextToken`, 대부분의 다른 서비스는 `NextToken`, WAFv2 는 `NextMarker` — 2.12.0 · `ListWebACLs` 는 요청 인자도
# `NextMarker` 다(botocore 모델 실측 2026-10-23). 🔴 요청 인자가 `Marker` 인 API(CloudFront `ListVpcOrigins`)는 이 길을 쓰지 않는다 — 손으로 돈다.
_TOKEN_KEYS = ("NextToken", "nextToken", "NextMarker")


def _manual_pages(
    client: Any, method: str, kwargs: dict[str, Any]
) -> Iterable[dict[str, Any]]:
    """페이지네이터가 없는 API 의 페이지를 토큰이 끊길 때까지 돌린다.

    🔴 `describe_connections`·`describe_virtual_interfaces` 는 `nextToken`(최대 100건/장)을
    주는데 boto3 에 페이지네이터가 없다(`docs/resource-catalog.md` §5.5-11). 첫 장만 읽으면
    101번째 연결부터 **조용히** 사라진다 — 누락은 실패가 아니라 「없음」으로 그려지므로 최악이다.
    """
    call = getattr(client, method)
    params = dict(kwargs)
    seen: set[str] = set()
    while True:
        page = call(**params)
        yield page
        token_key = next((k for k in _TOKEN_KEYS if page.get(k)), None)
        # 같은 토큰이 되돌아오면 멈춘다 — 마지막 장에도 표지를 주는 API 가 있으면 무한히 돈다(2.12.0 · WAFv2 `NextMarker` 방어)
        if token_key is None or str(page[token_key]) in seen:
            return
        seen.add(str(page[token_key]))
        params = {**kwargs, token_key: page[token_key]}


def dig(page: dict[str, Any], result_key: str) -> list[Any]:
    """`result_key` 를 점으로 끊어 응답 속을 내려간다 — 중첩 목록을 위한 것이다.

    🔴 **평평한 키만 가정하면 조용히 0건이 된다.** CloudFront `ListDistributions` 의 항목은
    `DistributionList.Items` 에 있고(boto3 페이지네이터의 `result_key` 자신이 그 점 표기다),
    `page.get("DistributionList.Items")` 는 그런 키가 없으므로 **예외 없이 빈 목록**을 준다 →
    「배포가 하나도 없다」로 그려진다. 누락은 실패가 아니라 「없음」으로 보이므로 최악이다
    (불변식 ④). 평평한 키는 조각이 하나인 경로이므로 같은 코드가 처리한다.

    없는 조각·`None`·중간이 dict 가 아닌 경우는 **빈 목록**이다 — 여기서 「못 읽음」과 「없음」을
    가르지 않는다. 그 구분은 호출한 쪽의 예외 처리(`classify`)와 coverage 가 한다.
    """
    node: Any = page
    for part in result_key.split("."):
        if not isinstance(node, dict):
            return []
        node = node.get(part)
        if node is None:
            return []
    return list(node) if isinstance(node, list) else []


def pages(
    client: Any, method: str, result_key: str, **kwargs: Any
) -> list[dict[str, Any]]:
    """페이지네이션을 손으로 돌리지 않는다 — 누락은 조용한 결함이다.

    `result_key` 는 점을 포함할 수 있다(`dig()` 참조 — 중첩 목록).
    """
    if client.can_paginate(method):
        pages: Iterable[dict[str, Any]] = client.get_paginator(method).paginate(
            **kwargs
        )
    else:
        pages = _manual_pages(client, method, kwargs)
    items: list[dict[str, Any]] = []
    for page in pages:
        items.extend(dig(page, result_key))
    return items


@dataclass(frozen=True, slots=True)
class Ctx:
    """지금 어느 계정·리전을 조회 중인가 + 소유자 귀속 규칙."""

    account_id: str
    region: str
    scope_accounts: frozenset[str]
    scope_regions: frozenset[str]

    def attribute(
        self, item: dict[str, Any], key: str = "OwnerId"
    ) -> tuple[str, dict[str, Any]]:
        """(노드의 account_id, 추가 속성) — ADR 0002 §2.

        소유자가 scope 안이면 소유자로 귀속시켜 공유 자원이 노드 하나로 합쳐지게 한다.
        scope 밖이면 조회한 계정으로 귀속시키고 소유자를 속성으로만 남긴다 —
        조사하지도 않은 계정을 그림에 등장시키지 않기 위해서다.
        """
        raw = item.get(key)
        owner = str(raw) if raw else None
        if owner and owner in self.scope_accounts:
            return owner, {}
        if owner and owner != self.account_id:
            return self.account_id, {"owner_account_id": owner}
        return self.account_id, {}

    def region_node(self, account_id: str | None = None) -> str:
        return node_id("region", account_id or self.account_id, self.region)

    def canonical_region(self, info: dict[str, Any], owner: str) -> str:
        """정본 리전 — ADR 0002 §2-1.

        크로스리전 자원(피어링)을 양쪽 리전에서 봐도 노드가 하나로 합쳐지게 하려면 리전 축에도
        정본이 필요하다. 기본은 그 정보가 가리키는 리전이지만 **조회한 (계정, 리전) 조합 안에만**
        노드를 놓는다:

        - 그 리전이 scope 밖이면 → 조회하지 않은 리전 레인이 생긴다(coverage 가 비어 있다).
        - 그 정보의 계정이 정본 계정과 다르면 → 정본 계정은 그 리전에서 그 자원을 보지 못한다.
          예: 다른 계정·다른 리전의 요청자와 수락자 사이 피어링은 수락자 계정 세션의 **수락자 리전
          응답에만** 나온다. 요청자 리전을 그대로 쓰면 노드가 (수락자 계정, 요청자 리전)에
          놓이는데 그 좌표엔 끝점이 하나도 없다.
        """
        raw_region = info.get("Region")
        raw_owner = info.get("OwnerId")
        region = str(raw_region) if raw_region else None
        if region is None or region not in self.scope_regions:
            return self.region
        if raw_owner and str(raw_owner) != owner:
            return self.region
        return region


@dataclass(frozen=True, slots=True)
class EdgeDraft:
    """대상을 **네이티브 ID** 로 들고 있는 엣지. 노드 id 는 3패스에서 찾는다.

    `by_dns=True` 면 `target_native` 는 native id 가 아니라 **DNS 이름**(`dns_key()` 로 접은 것)이고
    3패스는 `dns_name` 속성 색인에서 찾는다(#43 — CloudFront 원본은 LB 이름이 아니라 DNS 이름만 준다).
    🔴 두 색인을 합치지 않는다 — LB 이름과 DNS 이름이 한 네임스페이스에 들어가면 서로를 가린다.
    """

    type: str
    source: str
    target_native: str
    target_type: str | None
    directed: bool
    attributes: dict[str, Any]
    disc: str | None = None
    live: bool = True
    by_dns: bool = False
    # 🔵 배치 D(스키마 2.10.0) — `by_id=True` 면 `target_native` 는 **노드 id 그 자체**다(빌더가 ARN·같은 쌍의 사실로 지었다:
    #    API Gateway VPC 링크의 NLB ARN · RDS 프록시 대상 · Global Accelerator 엔드포인트 ARN). 이름 색인(`by_native`)은 먼저 만난
    #    것을 남기므로 **계정·리전이 다른 같은 이름의 LB·DB** 를 가린다 — ARN 이 계정·리전을 말하면 그것으로 짓는다.
    by_id: bool = False
    # 못 찾았을 때 `unresolved_refs` 에 남길 글자(없으면 `target_native`). 노드 id 를 그대로 남기면 화면이 「load_balancer/…」를 읽는다
    ref: str | None = None
    # 못 찾았을 때 출발 노드의 이 속성에 「<ref> not_collected」 줄을 더한다(까닭 줄 · LB `unresolved_targets` 와 같은 꼴) — 없으면 줄 없음
    why_attr: str | None = None


def dns_key(name: str) -> str:
    """DNS 이름 → 색인 키. 대소문자·끝 점·`dualstack.` 접두를 접는다.

    셋 다 **같은 LB 를 가리키는 다른 글자**다: DNS 는 대소문자를 가리지 않고, 끝 점은 FQDN 표기이며,
    `dualstack.<LB DNS>` 는 같은 LB 의 IPv4+IPv6 이름이다(CloudFront 콘솔이 원본 목록에 그 꼴을 준다).
    접지 않으면 같은 LB 인데 「수집하지 않은 로드밸런서」로 그려진다.
    """
    key = name.strip().lower().rstrip(".")
    return key.removeprefix("dualstack.")


@dataclass(frozen=True, slots=True)
class TargetRef:
    """LB 대상 그룹에 **등록된 대상 하나**(`DescribeTargetHealth` 의 `Target` + 그 그룹의 `TargetType`).

    🔴 **구조에 그대로 싣지 않는다** — 노드 속성이 아니라 초안(`NodeDraft.lb_targets`)의 비공개 칸이다. 3패스의
    대상 잇기(`nettopo.lb_targets`)가 이것을 **대상 노드마다 엣지 하나**(`load_balancer_target`)로 묶는다
    (ADR 0002 §8 · ADR 0007 결정 6 개정). `health` 는 상태 단(`collect(with_state=True)`)만 쓴다 — 구조 문서에
    헬스를 넣으면 매 수집이 「구조 바뀜」이 된다(불변식 ②).
    """

    target_group: str  # 대상 그룹 이름
    target_group_arn: str
    target_type: str  # instance · ip · lambda · alb (AWS `TargetType` 그대로, 소문자)
    target_id: str  # `Target.Id` — 인스턴스 id · 사설 IP · Lambda ARN · ALB ARN
    port: int | None  # `Target.Port`(없으면 그룹 `Port` · Lambda 는 없다)
    zone: str | None  # `Target.AvailabilityZone` — IP 대상의 `all` = 「LB VPC 밖」
    health: str | None = None  # `TargetHealth.State` — 상태 단 전용
    # `TargetHealth.Reason` — 상태 단 전용(`unavailable` 이 헬스 체크 꺼짐인지 가른다 · #137)
    health_reason: str | None = None


# LB 대상 헬스의 「정상도 비정상도 아닌」 다섯 갈래(관측 1.15.0 `targets` · #137) — (상태 단 속성 키, 관측 키).
#   LB 노드(`load_balancer.state_attributes`)와 LB → 대상 선(`nettopo/lb_targets.py`)이 같은 표로 세고,
#   `observe.py` 가 같은 표로 관측 `targets` 에 옮긴다. 🔴 판정에는 쓰지 않는다 — `down` 은 여전히 healthy·unhealthy 만 본다.
TARGET_HEALTH_DETAIL: tuple[tuple[str, str], ...] = (
    ("health_check_off_target_count", "health_check_off"),
    ("initial_target_count", "initial"),
    ("draining_target_count", "draining"),
    ("unused_target_count", "unused"),
    ("other_unavailable_target_count", "other_unavailable"),
)
# `unavailable` + 이 Reason = 대상 그룹의 헬스 체크를 껐다(Lambda 대상 그룹의 기본값 · AWS ELB `TargetHealth.Reason`).
HEALTH_CHECK_DISABLED = "Target.HealthCheckDisabled"
_DETAIL_BY_STATE = {
    "initial": "initial_target_count",
    "draining": "draining_target_count",
    "unused": "unused_target_count",
}


def health_detail(state: str | None, reason: str | None) -> str | None:
    """대상 하나의 AWS `TargetHealth.State`(+`Reason`) → 다섯 갈래 중 하나의 속성 키.

    `healthy`·`unhealthy` 와 표에 없는 상태(`unhealthy.draining` 등)는 `None` — 앞의 둘은 따로 세고, 뒤는 어느 갈래에도
    넣지 않는다(화면이 `total` 과의 차를 「그 밖」으로 말한다 · 모르는 상태를 아는 갈래로 지어 넣지 않는다).
    """
    if state == "unavailable":
        return (
            "health_check_off_target_count"
            if reason == HEALTH_CHECK_DISABLED
            else "other_unavailable_target_count"
        )
    return _DETAIL_BY_STATE.get(state or "")


@dataclass(frozen=True, slots=True)
class TargetGroupRef:
    """LB 가 든 **대상 그룹 하나**(`DescribeTargetGroups` 응답 · 2.8.0) — 등록 대상이 0개여도 있다.

    🔴 구조에 그대로 싣지 않는다(`TargetRef` 와 같은 비공개 칸). 3패스가 「이 그룹을 자기 것으로 든 자원」
    (ECS 서비스 `target_group_arns` · Auto Scaling 그룹 `target_group_arns`)과 LB 를 **등록 수와 무관하게** 잇는 재료다 —
    서비스가 0 으로 줄어 등록이 없어도 「이 LB 뒤에 이 서비스가 있다」는 구성이다(`nettopo/lb_targets.py`).
    """

    name: str
    arn: str
    target_type: str  # instance · ip · lambda · alb (소문자)
    port: int | None  # 그룹 `Port`(Lambda 는 없다)
    # AWS Load Balancer Controller 가 단 태그 `elbv2.k8s.aws/cluster`(2.8.0 · P2-5) — 그 EKS 클러스터가 이 그룹을 든다.
    #   태그를 못 읽었거나 없으면 None(LB 초안에 사유가 붙는다 · 「태그 없음」과 「못 읽음」은 LB 의 `partial` 이 가른다)
    cluster: str | None = None


@dataclass(slots=True)
class NodeDraft:
    node: Node
    account_id: str  # 조회한 계정(부모 실패 사유를 찾는 키)
    region: str
    parents: tuple[str, ...]  # 후보 체인. 첫 후보가 node.parent_id
    parent_types: tuple[str, ...]  # 후보마다 「없으면 어느 수집이 실패했나」
    edges: list[EdgeDraft] = field(default_factory=list)
    # LB 의 등록 대상(위 `TargetRef`) — 3패스가 대상 노드를 찾아 엣지로 묶는다. LB 말고는 언제나 비어 있다.
    lb_targets: tuple[TargetRef, ...] = ()
    # LB 가 읽은 대상 그룹(위 `TargetGroupRef` · 2.8.0) — 등록 0 인 그룹도 든다. LB 말고는 언제나 비어 있다.
    lb_groups: tuple[TargetGroupRef, ...] = ()
    # Auto Scaling 그룹의 **지금** 인스턴스 id(2.8.0) — 🔴 ASG 노드에 싣지 않는다(시시각각 바뀐다 · `auto_scaling_group.py`
    #   머리말). 3패스가 그 인스턴스 노드에 소속(`auto_scaling_group_id`)을 다는 데만 쓴다. ASG 말고는 비어 있다.
    asg_members: frozenset[str] = frozenset()
    # 🔴 「이 응답은 한 자원을 **여러 (계정, 리전) 에서 본 것 중 하나**다」를 빌더가 선언한다
    #    (ADR 0002 §2-2). 켜면 `collect` 가 노드 id 가 아니라 native id 로 묶어 뷰를 병합한다.
    #    정본 규칙(§2·§2-1)으로 두 쪽의 id 를 같게 만들 수 있으면 이것을 켜지 말고 그쪽을 써라 —
    #    여기 오는 것은 「응답에 상대 쪽 좌표가 없어서 정본을 계산할 수 없는」 경우뿐이다.
    shared_native: bool = False
    # 🔴 **3패스 잇기의 재료 — 구조에 싣지 않는다**(배치 D · 스키마 2.10.0). 탄력적 IP 의 ENI 응답처럼 노드 속성으로 내보내면 안 되는
    #    (ENI 설명·요청자 = 매 수집 같은 값이지만 화면 몫이 아니다) 사실을 `nettopo/relations.py` 로 넘기는 칸이다. 키는 그 파일이 정한다.
    private: dict[str, Any] = field(default_factory=dict)


def alive(state: Any) -> bool:
    return str(state or "").lower() not in DEAD_STATES


def tags_of(
    item: dict[str, Any],
    field_name: str = "Tags",
    key_name: str = "Key",
    value_name: str = "Value",
) -> dict[str, str]:
    """응답의 태그 배열 → dict. 이름이 `tags` 가 아닌 이유는 빌더가 지역 변수로 `tags` 를 쓴다.

    EC2·ELB 는 `Tags[].Key/Value`, Direct Connect 는 `tags[].key/value` 다 — 표기만 다르고
    같은 사실이므로 함수는 하나다.
    """
    return {
        t[key_name]: t[value_name]
        for t in item.get(field_name) or []
        if key_name in t and value_name in t
    }


def sorted_strs(values: Iterable[Any]) -> list[str]:
    return sorted({str(v) for v in values if v})


def draft(
    *,
    node: Node,
    ctx: Ctx,
    parents: tuple[str, ...],
    parent_types: tuple[str, ...],
    edges: list[EdgeDraft] | None = None,
    shared_native: bool = False,
    lb_targets: tuple[TargetRef, ...] = (),
    lb_groups: tuple[TargetGroupRef, ...] = (),
    asg_members: frozenset[str] = frozenset(),
    private: dict[str, Any] | None = None,
) -> NodeDraft:
    return NodeDraft(
        node=replace(node, parent_id=parents[0]),
        account_id=ctx.account_id,
        region=ctx.region,
        parents=parents,
        parent_types=parent_types,
        edges=edges or [],
        shared_native=shared_native,
        lb_targets=lb_targets,
        lb_groups=lb_groups,
        asg_members=asg_members,
        private=dict(private or {}),
    )
