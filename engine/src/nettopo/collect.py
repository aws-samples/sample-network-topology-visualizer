"""수집 — scope 곱집합을 돌며 노드·엣지·coverage 를 만든다.

지원 범위는 **1차 지원 리소스 12종 + 2차 Direct Connect 3종**이다(`docs/product-architecture.md`
§11, ADR 0010). 부모(`parent_id`)를 무엇으로 두고
무엇을 엣지로 두는지, 공유 자원을 어느 계정에 귀속시키는지는 `docs/adr/0002-resource-and-edge-model.md`
가 정본이다 — 규칙을 바꾸려면 그 문서를 먼저 고친다.

불변식 ④가 이 파일의 주제다: 계정·리전 수십 개를 돌면 AccessDenied·throttle·미지원
리전이 **반드시** 나온다. 실패를 예외로 터뜨려 전체를 죽이지도, 조용히 빈 결과로 삼키지도
않고 `coverage[]` 에 사유와 함께 남긴다. `item_count` 는 실패 시 `None` 이다 — `0` 은
「없었다」는 다른 사실이다.

세 패스로 나뉜다. 엣지·부모는 **다른 계정·다른 리전의 노드를 가리킬 수 있어서**(RAM 공유 TGW,
크로스리전 피어링) 한 리전을 돌면서 확정할 수 없다:

1. `_fetch` — scope 곱집합을 돌며 API 응답 원본과 coverage 를 모은다.
2. 빌더 — 응답 하나당 `_NodeDraft`(노드 + 부모 후보 체인 + 엣지 후보)를 만든다.
3. 해소 — 전체 노드 id 집합이 확정된 뒤 부모를 고르고, 엣지의 대상을 네이티브 ID 로 찾는다.
   못 찾은 대상은 버리지 않고 소스 노드의 `attributes.unresolved_refs` 에 남긴다(ADR 0002 §5).

🔴 `ReadOnlyViolation` 은 여기서 잡지 않는다. 가드 위반은 부분 실패가 아니라 버그다.
🔴 응답에 필수 키가 없으면 그대로 터뜨린다(`item["VpcId"]`). API 계약이 깨진 것을 부분 실패로
   적으면 사람이 원인을 못 찾는다 — 불변식 ④는 **호출 실패**에 대한 규칙이다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from . import (
    __version__,
    account_name,
    endpoint_providers,
    lb_targets,
    origins,
    relations,
)
from .model import CoverageEntry, Edge, Node, Reason, Scope, Snapshot, node_id

# 「이음매 2」의 primitive 는 `services/base.py` 에 있다 — 서비스 파일이 수집기 본체를 임포트하면
# 순환이 되기 때문이다(`collect` → `services.elb` → `collect`).
from .services import REGISTRY, base

# 본체는 기존 사설 이름을 계속 쓴다 — 이름을 바꾸면 하네스 브레이크 앵커 10개가 같이 죽는다.
# (빌더가 `services/` 로 내려간 뒤 `alive`·`draft`·`sorted_strs` 는 본체에서 쓰지 않는다.
#  서비스 파일은 별칭 없이 `base` 의 공개 이름을 그대로 쓴다 — 그쪽이 계약이다.)
ClientFactory = base.ClientFactory
_Ctx = base.Ctx
_EdgeDraft = base.EdgeDraft
_Fetched = base.Fetched
_NodeDraft = base.NodeDraft
_Spec = base.Spec
_classify = base.classify
_pages = base.pages

__all__ = ["SUPPORTED_RESOURCE_TYPES", "ClientFactory", "collect", "utc_now"]

SUPPORTED_RESOURCE_TYPES = (
    "vpc",
    "subnet",
    "route_table",
    "internet_gateway",
    "nat_gateway",
    "transit_gateway",
    "transit_gateway_attachment",
    "vpc_peering_connection",
    "vpc_endpoint",
    "security_group",
    "network_acl",
    "load_balancer",
    "direct_connect_connection",
    "direct_connect_virtual_interface",
    "direct_connect_gateway",
    "ec2_instance",
    "auto_scaling_group",
    "eks_cluster",
    "network_firewall",
    "cloudfront",
    "transit_gateway_route_table",
    "rds_db_instance",
    "ecs_service",
    "sagemaker_notebook_instance",
    "sagemaker_domain",
    "sagemaker_endpoint",
    "ec2_instance_connect_endpoint",
    "vpn_gateway",
    "customer_gateway",
    "vpn_connection",
    "lambda_function",
    "route53_hosted_zone",
    "elasticache_cluster",
    "rds_db_cluster",
    "opensearch_domain",
    "msk_cluster",
    # 배치 D(2.10.0 · ADR 0002 §12) — 탄력적 IP · API Gateway(REST · HTTP/WebSocket) · RDS 프록시 · Global Accelerator(글로벌 · us-west-2)
    "elastic_ip",
    "api_gateway",
    "api_gateway_v2",
    "rds_db_proxy",
    "global_accelerator",
    # #129(2.11.0 · ADR 0002 §13) — Cloud WAN 코어 네트워크(글로벌 · networkmanager = us-west-2) · VPC Lattice 서비스 네트워크 · 서비스
    "core_network",
    "lattice_service_network",
    "lattice_service",
    # 2.12.0(ADR 0014) — WAF 웹 ACL 리전 범위 · CloudFront 범위(글로벌 · wafv2 = us-east-1 고정)
    "waf_web_acl",
    "waf_web_acl_global",
)


# 목록 호출과 빌더는 **전부** 서비스 파일이 소유한다(「이음매 2」). 본체에 남은 두 이름은
# 레지스트리의 투영일 뿐이다 — 여기에 종류를 직접 적지 마라. 적으면 「파일 하나 = 종류 하나」가
# 무너지고, `services/__init__.py` 3단계 절차가 거짓이 된다.
_SPECS: dict[str, _Spec] = {rt: service.spec for rt, service in REGISTRY.items()}

# 수집 순서는 요청 순서와 무관하게 고정한다: 부모가 될 수 있는 것이 먼저 온다
# (VPC → Subnet → 나머지). 부모 해소 자체는 3패스에서 하므로 순서에 의존하지 않지만,
# 순서를 고정해야 「먼저 만난 공유 자원을 남긴다」가 결정론이 된다(ADR 0002 §2).
_COLLECT_ORDER = SUPPORTED_RESOURCE_TYPES

# 한 (계정,리전) 안에서 **동시에** 부를 목록 호출 수.
#
# 🔴 왜 동시여야 하나: 모니터링 화면은 **여러 계정·여러 리전을 한 화면에** 그린다(사용자 확인
# 2026-09-24). 그러면 상태가 볼 자리도 그 화면의 칸 전부이고, 「그중 일부만 본 화면」은 제품이
# 아니다 — 범위를 줄이는 대신 **한 번에 묻는** 것이 요구사항이다. 실측(ADR 0004 **D2-4**): 쌍당
# 비용은 12번의 순차 describe 왕복 그 자체이고(하나당 ~0.48s → 쌍당 ~5.7s), 느린 종류는
# `vpc`·`load_balancer` 둘뿐이다.
#
# 🔴 **얼마나 내려가나 — 여기 「쌍당 ~1.1s」로 적었던 예측은 틀렸다**(ADR 0004 **D2-5** 실측):
# 실제는 1층 쌍당 **2.55s**(1층+2층 3.83s)다. 이유 둘 — ① 한 쌍의 describe 11개가 **같은 ec2
# 클라이언트 하나**를 쓰고 botocore 기본 연결 풀이 10 이라 동시성이 종류 수만큼 안 나온다,
# ② 아래 1단계(세션·클라이언트 생성, 크로스계정이면 `sts:AssumeRole`)가 쌍마다 직렬로 남는다.
# 그래서 30계정×1리전(30쌍)은 120s 로 들어가지만 **×3리전(90쌍)은 ~350s 로 240s 예산을 넘는다.**
# 다음 표적은 2층이 아니라 **쌍끼리 동시**다(2층이 0이어도 90쌍은 234s = 마진 2.5%).
#
# 🔴 **이 수가 throttle 과 맞바꾸는 유일한 손잡이다.** 올리면 대상 계정에서
# `RequestLimitExceeded` 가 늘고 그 자리는 화면에서 「확인 불가」가 된다(불변식 ④ — 죽지 않는다).
# 12 = 1차 지원 종류 전부라서 「한 화면의 자리를 한 번에 묻는다」가 성립하는 최소값이고, 실제
# 동시 호출은 종류 수로도 한 번 더 깎인다(`min`).
_FETCH_CONCURRENCY = 12

# **동시에 도는 (계정,리전) 쌍 수.** D2-5 가 지목한 다음 표적이고, 위 종류 동시성과 **곱해진다**
# (최대 in-flight 목록 호출 = 8 × 12 = 96).
#
# 🔴 왜 필요한가: 종류만 동시로 돌린 뒤 실측한 쌍당 비용이 **2.55s**(1층)라서 90쌍이면 234s 이고
# Lambda 타임아웃 240s(ADR 0004 D2-1 조건 3)에 마진이 2.5% 다. 2층 비용을 0으로 만들어도 예산이
# 생기지 않는다 — 남은 직렬 구간이 **쌍 그 자체**이기 때문이다(각 쌍의 세션·클라이언트 생성 +
# 같은 ec2 클라이언트 하나를 11개 describe 가 공유하는 연결 풀 10).
#
# 🔵 8 을 고른 근거: 쌍을 동시에 돌리면 쌍마다 **자기 클라이언트**를 쓰므로 연결 풀 10 의 천장이
# 쌍 수만큼 늘어난다(종류 동시성이 실제로 12 에 가까워진다). 8 × 12 = 96 은 스레드 수로도
# 감당되고, throttle 은 계정·리전별 버킷이라 서로 다른 쌍이 같은 버킷을 치는 경우가 적다.
# 올리기 전에 `RequestLimitExceeded` 를 실측해라 — 늘어난 거부는 화면에서 「확인 불가」다.
#
# 🔴 **실측 후(ADR 0004 D2-6): 이 수를 올리는 것이 다음 표적이 아니다.** 쌍당 비용이 자기 계정
# **0.70s** 대 다른 계정 **2.28s** 로 갈렸고, 차이는 `cli._SessionCache` 의 잠금 안에서 직렬로 서는
# `sts:AssumeRole` 이다(세션 키가 `(계정,리전)`이라 쌍마다 한 번). 여기를 16 으로 올려도 그 줄은
# 그대로다 — 먼저 **세션을 계정당 하나로** 줄여라.
_PAIR_CONCURRENCY = 8


def utc_now() -> str:
    """RFC3339(초 단위, UTC). 불변식 ②의 유일한 wall-clock 예외."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect(
    scope: Scope,
    client_factory: ClientFactory,
    *,
    tool_version: str = __version__,
    clock: Callable[[], str] = utc_now,
    with_state: bool = False,
    structure_lambdas: tuple[lb_targets.KnownLambda, ...] = (),
    account_names: bool = False,
) -> Snapshot:
    """`with_state` = 상태 탐침(`Service.state`)의 값을 노드 속성 `state` 로 싣는다 — **상태 단 전용**.

    🔴 구조 수집에서 켜지 마라. 탐침 값(RDS `DBInstanceStatus` 등)은 시시각각 바뀌어 구조 문서의
    바이트를 흔든다(불변식 ②) — 그래서 서비스 파일이 일부러 구조 속성에서 뺀 값이다.

    `structure_lambdas` = 구조 문서가 읽은 VPC Lambda 함수(`lb_targets.structure_lambdas` · #152 · ADR 0006 결정 15) — 상태 단이
    LB → VPC Lambda(공유 ENI) 선을 구조와 같은 id 로 다시 잇는 재료다. 🔴 상태 단 전용이다 — 구조 수집에 주면 구조 문서가 직전 구조
    문서에 기대게 된다(불변식 ②). 이번에 `lambda_function` 을 수집하면 쓰지 않는다(지금 읽은 것이 이긴다).

    `account_names` = 계정마다 `account:GetAccountInformation` 1회 → 계정 노드 `name`(사용자 결정 2026-10-28 · `account_name.py`).
    구조 수집(`cli collect`)만 켠다 — 5분 관측은 이름을 그리지 않는다. 거부는 `partial`·`coverage` 가 아니라 계정 노드 속성 `name_unread` 다.
    """
    if structure_lambdas and not with_state:
        raise ValueError("structure_lambdas 는 상태 단(with_state=True) 전용이다")
    unknown = [t for t in scope.resource_types if t not in _SPECS]
    if unknown:
        # 「아직 구현 안 했다」를 coverage 의 skipped 로 적으면 사용자가 제외한 것과 구분되지 않는다.
        raise ValueError(
            f"아직 수집할 수 없는 리소스 종류: {', '.join(sorted(unknown))}"
        )

    requested = tuple(t for t in _COLLECT_ORDER if t in scope.resource_types)
    scope_accounts = frozenset(scope.account_ids)
    scope_regions = frozenset(scope.regions)

    containers: list[Node] = []
    drafts: list[_NodeDraft] = []
    coverage: list[CoverageEntry] = []
    failures: dict[tuple[str, str, str], Reason] = {}

    # 컨테이너 노드(계정·리전)는 호출이 없으므로 **scope 순서대로 먼저** 만든다. 아래 조립이
    # 쌍을 어떤 순서로 완료하든 이 목록은 흔들리지 않는다(불변식 ②).
    pairs: list[_Ctx] = []
    for account_id in scope.account_ids:
        containers.append(
            Node(
                id=node_id("account", account_id),
                type="account",
                account_id=account_id,
                region=None,
            )
        )
        for region in scope.regions:
            containers.append(
                Node(
                    id=node_id("region", account_id, region),
                    type="region",
                    account_id=account_id,
                    region=region,
                    name=region,
                    parent_id=node_id("account", account_id),
                )
            )
            pairs.append(
                _Ctx(
                    account_id=account_id,
                    region=region,
                    scope_accounts=scope_accounts,
                    scope_regions=scope_regions,
                )
            )

    # 🔴 **청크로 나눠 도는 것은 메모리 때문이다.** 쌍 전부를 한 번에 띄우면 모든 쌍의 API 응답
    # 원본이 동시에 살아 있다 — 145 노드 쌍 하나가 ~0.5 MB 이고 510쌍이면 ~250 MB 다. 상태 단은
    # Lambda 에서 돌고(ADR 0004 D2-1) 메모리 상한이 예산이다. 청크 하나를 **조립까지 끝내고**
    # 다음 청크를 띄우면 살아 있는 원본이 `_PAIR_CONCURRENCY` 쌍으로 묶인다.
    # 🔵 청크 경계에서 겹침이 끊기는 손실은 「청크마다 가장 느린 쌍을 기다린다」뿐이다.
    for chunk in _chunks(pairs, _PAIR_CONCURRENCY):
        for ctx, (items, region_coverage, region_failures) in zip(
            chunk,
            _fetch_pairs(chunk, client_factory, requested),
            strict=True,
        ):
            # 🔴 조립은 **요청 순서(= scope 순서)로** 돈다. 완료 순서로 돌면 `coverage` 줄과
            # `nodes` 순서가 네트워크 지연에 따라 바뀌어 불변식 ②가 깨진다(`_fetch` 3단계와 같은
            # 이유이고, 여기는 그것의 쌍 단위 판이다).
            coverage.extend(region_coverage)
            failures.update(region_failures)
            for resource_type in requested:
                build = _BUILDERS[resource_type]
                for item in items.get(resource_type, ()):
                    draft = build(item.payload, ctx)
                    if draft is None:
                        continue
                    probe = _STATE_PROBES.get(resource_type) if with_state else None
                    if probe is not None:
                        value = probe(item.payload)
                        if value is not None:
                            draft.node = replace(
                                draft.node,
                                attributes={**draft.node.attributes, "state": value},
                            )
                    extra = _STATE_ATTRIBUTES.get(resource_type) if with_state else None
                    if extra is not None:
                        values = extra(item.payload)
                        if values:
                            draft.node = replace(
                                draft.node,
                                attributes={**draft.node.attributes, **values},
                            )
                    if item.reasons:
                        # 후속 호출이 실패한 리소스. 노드는 남기고 「일부만 읽었다」를 적는다 —
                        # 조용히 완전한 노드로 그리면 사람이 「리스너 없음」으로 오판한다(불변식 ④).
                        draft.node = replace(
                            draft.node,
                            partial=True,
                            partial_reasons=draft.node.partial_reasons + item.reasons,
                        )
                    drafts.append(draft)

    # LB 사설 IP 대상의 ENI — 모든 쌍을 다 돈 뒤에야 「그 IP 가 어느 수집한 VPC 에 사나」를 안다(`lb_targets.py` 머리말).
    found = lb_targets.lookup(drafts, client_factory)
    # 라우트의 `eni-` 대상(NAT 인스턴스 · 어플라이언스)의 주인(2.8.0 · P2-2) — 같은 이유로 모든 쌍을 돈 뒤에 묻는다
    routes = lb_targets.route_lookup(drafts, client_factory)
    nodes, edges = _resolve(
        containers,
        drafts,
        failures,
        found=found,
        scope=scope,
        with_state=with_state,
        routes=routes,
        seeded=() if "lambda_function" in requested else structure_lambdas,
    )
    if account_names:
        nodes = account_name.apply(
            nodes, account_name.read(scope.account_ids, client_factory)
        )
    return Snapshot(
        collected_at=clock(),
        tool_version=tool_version,
        scope=scope,
        nodes=tuple(nodes),
        edges=tuple(edges),
        coverage=tuple(coverage),
    )


# ---------------------------------------------------------------- 1패스: 호출

_Pair = tuple[
    dict[str, list[_Fetched]],
    list[CoverageEntry],
    dict[tuple[str, str, str], Reason],
]


def _chunks(pairs: list[_Ctx], size: int) -> list[list[_Ctx]]:
    return [pairs[i : i + size] for i in range(0, len(pairs), size)]


def _fetch_pairs(
    chunk: list[_Ctx],
    client_factory: ClientFactory,
    requested: Sequence[str],
) -> list[_Pair]:
    """쌍 여러 개를 **동시에** 부르고 결과를 **받은 순서가 아니라 준 순서로** 돌려준다.

    🔴 `pool.map` 인 것이 계약이다 — `as_completed` 로 바꾸면 조립이 완료 순서를 보게 되고
    불변식 ②가 깨진다. 결정론은 「누가 먼저 끝났나」와 무관해야 한다.

    🔴 예외는 삼키지 않는다. `ReadOnlyViolation`(불변식 ①)과 응답 계약 위반은 `_fetch` 안에서
    잡히지 않으므로 `list()` 가 그대로 다시 던진다 — 부분 실패가 아니라 버그다.
    """
    if len(chunk) == 1:
        # 쌍이 하나면 스레드를 만들지 않는다 — 로컬 CLI 의 가장 흔한 모양이고, 스택 트레이스가
        # 스레드 경계를 넘지 않는 편이 디버깅에 낫다.
        return [_fetch(chunk[0], client_factory, requested)]

    def one_pair(ctx: _Ctx) -> _Pair:
        return _fetch(ctx, client_factory, requested)

    with ThreadPoolExecutor(
        max_workers=len(chunk), thread_name_prefix="nettopo-pair"
    ) as pool:
        return list(pool.map(one_pair, chunk))


def _client_key(ctx: _Ctx, resource_type: str) -> tuple[str, str]:
    """그 종류의 목록 호출을 할 클라이언트의 열쇠 = (boto3 서비스, 리전) — `Spec.region` 이 있으면 그 리전, 없으면 수집 리전."""
    spec = _SPECS[resource_type]
    return spec.service, spec.region or ctx.region


def _fetch(
    ctx: _Ctx,
    client_factory: ClientFactory,
    requested: Sequence[str],
) -> tuple[
    dict[str, list[_Fetched]],
    list[CoverageEntry],
    dict[tuple[str, str, str], Reason],
]:
    items: dict[str, list[_Fetched]] = {}
    coverage: list[CoverageEntry] = []
    failures: dict[tuple[str, str, str], Reason] = {}
    clients: dict[str, Any] = {}
    # 서비스 단위로 기억한다 — elbv2 진입 실패가 ec2 리소스를 denied 로 만들면 오진이다.
    client_failures: dict[str, tuple[str, Reason]] = {}

    def fail(resource_type: str, status: str, reason: Reason) -> None:
        coverage.append(
            CoverageEntry(
                ctx.account_id, ctx.region, resource_type, status, reason, None
            )
        )
        failures[(ctx.account_id, ctx.region, resource_type)] = reason

    # 1단계 — 클라이언트는 **순차로** 만든다. botocore 는 클라이언트 **생성**이 스레드 안전하지
    # 않고, 크로스계정 경로에서는 이 자리가 `sts:AssumeRole` 을 부른다(`cli._SessionCache.client`).
    # 종류가 12개여도 서비스는 2개(ec2·elbv2)뿐이라 이 단계는 왕복 2회이고 병렬화 이득이 없다.
    # 다른 서비스로 좁히는 후속 호출(`Service.peer_enrich` — RDS → EC2 ENI)의 서비스도 여기서 만든다.
    # 🔴 리전을 고정한 서비스(`Spec.region` · 배치 D — Global Accelerator = us-west-2)는 수집 리전과 무관하게 그 리전으로 만든다.
    #    클라이언트 열쇠 = (서비스, 리전)(2.12.0 · WAF) — `wafv2` 하나를 **두 리전에서** 부른다: CloudFront 범위 web ACL = us-east-1 고정
    #    (`waf_web_acl_global`) · 리전 범위 web ACL·ALB 의 web ACL 후속 호출 = 수집 리전. 열쇠가 서비스 이름뿐이면 한쪽 리전이 다른 쪽을
    #    덮어 ap-northeast-2 의 ALB 를 us-east-1 에 묻는다(`test_waf.py` 가 잰다).
    wanted = [_client_key(ctx, rt) for rt in requested]
    for resource_type in requested:
        service = REGISTRY.get(resource_type)
        if service is not None:
            wanted.extend((peer, ctx.region) for peer, _ in service.peer_enrich)
    for key in wanted:
        if key in clients or key in client_failures:
            continue
        boto_service, client_region = key
        try:
            clients[key] = client_factory(ctx.account_id, client_region, boto_service)
        except (ClientError, BotoCoreError) as exc:
            # 자격증명·크로스계정 진입 문제로 클라이언트조차 못 만든 경우도 부분 실패다.
            # 실패한 API 이름은 예외에서 가져온다 — 팩토리가 부르는 것은 `sts:AssumeRole` 이고,
            # 고정 문자열로 적으면 사용자가 「EC2 권한이 없다」고 오진한다(실측 2026-09-14).
            # 🔴 서비스 단위로 기억한다 — elbv2 진입 실패가 ec2 리소스를 denied 로 만들면 오진이다.
            operation = getattr(exc, "operation_name", None)
            api = f"sts:{operation}" if operation else "nettopo:client_factory"
            client_failures[key] = _classify(exc, api)

    def one(
        resource_type: str,
    ) -> tuple[list[_Fetched], int, tuple[str, Reason] | None]:
        """종류 하나의 목록 호출(+ 후속 호출). **스레드에서 돈다** — 공유 상태를 만지지 않는다.

        🔴 `coverage`·`failures` 를 여기서 쓰지 않는 것이 핵심이다. 스레드가 리스트에 append 하면
        순서가 완료 순서가 되고 불변식 ②(같은 입력 → 바이트 동일)가 깨진다. 결과만 돌려주고
        3단계가 **요청 순서로** 조립한다.
        """
        spec = _SPECS[resource_type]
        client = clients[_client_key(ctx, resource_type)]
        try:
            got = _pages(client, spec.method, spec.result_key, **dict(spec.params))
        except (ClientError, BotoCoreError) as exc:
            return [], 0, _classify(exc, spec.api)
        fetched = [_Fetched(payload) for payload in got]
        service = REGISTRY.get(resource_type)
        if service is not None and service.enrich is not None:
            # 후속 호출(ELB 의 리스너·타깃 그룹). 실패는 예외가 아니라 항목별 사유로 돌아온다 —
            # coverage 는 **목록 호출**이 성공했음을 계속 말한다(항목 수도 목록 기준이다).
            try:
                fetched = service.enrich(client, fetched)
            except base.ListingFailed as exc:
                # 노드가 될 항목을 후속 호출로 처음 알게 되는 종류(ECS 서비스)의 목록 실패 —
                # 일부만 그리면 「없음」이 되므로 종류 전체를 실패로 적는다(`ListingFailed` 머리말).
                return [], 0, (exc.status, exc.reason)
        for peer, join in service.peer_enrich if service is not None else ():
            # 다른 서비스의 응답으로 좁힌다(RDS → EC2 ENI · LB → wafv2 · LB → ec2 엔드포인트 서비스). 그 서비스 진입이
            # 실패했으면 클라이언트 대신 사유가 간다 — 항목은 그대로 남고 사유만 붙는다(불변식 ④).
            failed = client_failures.get((peer, ctx.region))
            fetched = join(
                clients.get((peer, ctx.region)), fetched, failed[1] if failed else None
            )
        return fetched, len(got), None

    # 2단계 — 목록 호출은 **동시에** 한다. botocore 클라이언트는 만들어진 뒤의 호출은 스레드
    # 안전하고, 불변식 ① 의 가드 훅은 클라이언트에 붙어 있으므로 어느 스레드에서 불러도 그대로
    # 걸린다(`awsguard.py`). 진입 자체가 실패한 서비스는 부를 대상이 아니다.
    todo = [rt for rt in requested if _client_key(ctx, rt) not in client_failures]
    done: dict[str, tuple[list[_Fetched], int, tuple[str, Reason] | None]] = {}
    if todo:
        with ThreadPoolExecutor(
            max_workers=min(len(todo), _FETCH_CONCURRENCY),
            thread_name_prefix="nettopo-fetch",
        ) as pool:
            done = dict(zip(todo, pool.map(one, todo), strict=True))

    # 3단계 — 조립은 **요청 순서로** 다시 돈다. `coverage` 의 줄 순서가 여기서 정해지고, 그것이
    # 순차 구현과 바이트 동일해야 한다(`test_determinism`).
    for resource_type in requested:
        failure = client_failures.get(_client_key(ctx, resource_type))
        if failure is not None:
            fail(resource_type, failure[0], failure[1])
            continue
        fetched, count, failed = done[resource_type]
        if failed is not None:
            fail(resource_type, failed[0], failed[1])
            continue
        items[resource_type] = fetched
        coverage.append(
            CoverageEntry(ctx.account_id, ctx.region, resource_type, "ok", None, count)
        )
    return items, coverage, failures


# ---------------------------------------------------------------- 2패스: 빌더
#
# 빌더는 **전부** 서비스 파일에 있다(`services/<종류>.py`, 「이음매 2」). 본체는 레지스트리를
# 투영하기만 한다 — 이 dict 에 함수를 직접 적으면 그 종류의 규약(파일 하나 = 종류 하나)이 깨진다.
_BUILDERS: dict[str, Callable[[dict[str, Any], _Ctx], _NodeDraft | None]] = {
    rt: service.build for rt, service in REGISTRY.items()
}
_STATE_PROBES: dict[str, Callable[[dict[str, Any]], str | None]] = {
    rt: service.state for rt, service in REGISTRY.items() if service.state is not None
}
_STATE_ATTRIBUTES: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    rt: service.state_attributes
    for rt, service in REGISTRY.items()
    if service.state_attributes is not None
}


# ---------------------------------------------------------------- 3패스: 해소


def _edge_id(edge_type: str, source: str, target: str, disc: str | None) -> str:
    # 노드 id 는 `/` 를 쓰고 `|` 는 쓰지 않는다 → 구분자로 `|` 를 쓰면 파싱 없이도 유일하다.
    parts = [edge_type, source, target]
    if disc:
        parts.append(disc)
    return "|".join(parts)


def _resolve(
    containers: list[Node],
    drafts: list[_NodeDraft],
    failures: dict[tuple[str, str, str], Reason],
    *,
    found: lb_targets.Lookup | None = None,
    scope: Scope | None = None,
    with_state: bool = False,
    routes: lb_targets.RouteLookup | None = None,
    seeded: tuple[lb_targets.KnownLambda, ...] = (),
) -> tuple[list[Node], list[Edge]]:
    ids: set[str] = {n.id for n in containers}
    # 한 AWS 자원을 두 쪽에서 본 것은 **노드 하나**다. 보통은 정본 규칙(ADR 0002 §2·§2-1)이 두
    # 쪽의 노드 id 를 같게 만들어 주므로 **id 로 묶으면 끝난다**(먼저 만난 것을 남긴다 — scope
    # 순회 순서가 고정이라 결정론은 유지된다).
    # 🔴 예외 = 빌더가 `shared_native` 로 신고한 응답(§2-2). TGW 피어링 어태치먼트는 응답에 상대
    #    리전이 없어 정본 리전을 계산할 수 없다 → id 가 갈라진다. 그때만 native id 로 묶고 뷰를
    #    **병합**한다. 먼저 만난 것만 남기고 버리면 진 뷰의 **엣지까지 같이 버려서** 반대편 TGW 로
    #    가는 선이 사라진다(실측 2026-09-26: 실계정 R4 「사라진 선 1」· R2 「중복 2」의 유일한 원인).
    groups: dict[tuple[str, str], list[_NodeDraft]] = {}
    order: list[tuple[str, str]] = []
    for draft in drafts:
        if draft.node.id in ids:
            # 컨테이너(계정·리전) id 와 겹치는 리소스 노드는 계약 위반이므로 버린다.
            continue
        key = (
            (draft.node.type, draft.node.native_id)
            if draft.shared_native and draft.node.native_id
            else (draft.node.type, draft.node.id)
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(draft)

    kept: list[_NodeDraft] = [
        _merge_views(groups[key]) if groups[key][0].shared_native else groups[key][0]
        for key in order
    ]
    ids.update(d.node.id for d in kept)

    by_native: dict[str, str] = {}
    # 🔴 DNS 이름 색인은 **다른 사전**이다(`EdgeDraft.by_dns` · #43). 먼저 만난 것을 남기는 규칙은 같다.
    by_dns: dict[str, str] = {}
    for draft in kept:
        native = draft.node.native_id
        if native and native not in by_native:
            by_native[native] = draft.node.id
        dns = draft.node.attributes.get("dns_name")
        if isinstance(dns, str) and dns:
            by_dns.setdefault(base.dns_key(dns), draft.node.id)
        # CloudFront 배포 도메인(2.8.0 · P2-4) — Route 53 별칭 레코드가 배포를 가리킬 때 같은 색인에서 찾는다
        if draft.node.type == "cloudfront":
            cf = draft.node.attributes.get("domain_name")
            if isinstance(cf, str) and cf:
                by_dns.setdefault(base.dns_key(cf), draft.node.id)

    # LB → 대상(ADR 0002 §8). 대상 노드 id 가 다 확정된 뒤에만 잇는다 — Lambda·외부 IP 노드는 여기서 짓는다.
    joined = (
        lb_targets.join(
            kept, ids, by_native, found, scope, with_state=with_state, seeded=seeded
        )
        if found is not None and scope is not None
        else None
    )
    nodes: list[Node] = list(containers)
    edge_drafts: list[_EdgeDraft] = []
    for draft in kept:
        nodes.append(_with_parent(draft, ids, failures))
        edge_drafts.extend(draft.edges)
    if joined is not None:
        ids.update(d.node.id for d in joined.nodes)
        nodes.extend(_with_parent(d, ids, failures) for d in joined.nodes)

    # 라우트 → ENI 주인(2.8.0 · P2-2) — `eni-` 대상을 그 주인 노드로 잇는다. 엣지 id 끝에 ENI(`disc`)를 달아 같은 주인의 두 ENI 가
    #   한 엣지로 겹쳐 목적지를 잃지 않게 하고, 속성 `via_eni` 로 「어느 ENI 를 거쳐」를 남긴다. 못 이은 것은 테이블에 까닭을 단다.
    route_why: dict[str, set[str]] = {}
    if routes is not None and routes.asked:
        owners, route_why = lb_targets.route_owners(kept, ids, by_native, routes)
        if owners:
            edge_drafts = [
                replace(
                    d,
                    disc=d.target_native,
                    attributes={**d.attributes, "via_eni": d.target_native},
                )
                if d.type == "route" and d.target_native in owners
                else d
                for d in edge_drafts
            ]
            by_native = {**by_native, **owners}
    # 배치 D(2.10.0 · ADR 0002 §12-2) — 탄력적 IP 의 주인(인스턴스 · ENI 주인). Global Accelerator 의 EIP 엔드포인트(§12-5)는 그 주인으로 잇는다 —
    #   EIP 는 카드가 아니라 주인의 정보창 줄이라 선의 끝이 될 수 없다
    eips = relations.eip_owners(kept, ids, by_native)
    why: dict[tuple[str, str], set[str]] = {}
    unresolved_pre: dict[str, set[str]] = {}
    edge_drafts = _via_eip(edge_drafts, eips, ids, why, unresolved_pre)
    edges, unresolved = _resolve_edges(edge_drafts, by_native, by_dns, ids, why)
    for source, refs in unresolved_pre.items():
        unresolved.setdefault(source, set()).update(refs)
    edges.extend(eips.edges)
    # CloudFront 원본 — S3(파생 노드) · 사용자 도메인 → Route 53 별칭 → LB(2.8.0 · P2-3 · `origins.py`)
    cf = origins.join(kept, ids, by_dns, {e.id for e in edges})
    if cf.nodes:
        ids.update(d.node.id for d in cf.nodes)
        nodes.extend(_with_parent(d, ids, failures) for d in cf.nodes)
    edges.extend(cf.edges)
    if joined is not None:
        edges.extend(joined.edges)
        for source, natives in joined.unresolved.items():
            unresolved.setdefault(source, set()).update(natives)
        if joined.reasons:
            nodes = [_with_target_reasons(n, joined.reasons.get(n.id)) for n in nodes]
    # GWLB 엔드포인트 → 그 서비스를 제공하는 LB(2.8.0 · P1-3 · ADR 0002 §9) — 전체 노드 id 가 확정된 뒤 서비스 이름으로 잇는다
    provided = endpoint_providers.join(kept, ids)
    edges.extend(provided.edges)
    # WAF 웹 ACL → ALB·CloudFront(2.12.0 · ADR 0014) — 대상 쪽 속성으로 잇는다(API Gateway 선은 web ACL 빌더가 짓는다)
    edges.extend(relations.waf_protections(kept, ids))
    for source, names in provided.unresolved.items():
        unresolved.setdefault(source, set()).update(names)
    if route_why:
        nodes = [
            replace(
                n,
                attributes={
                    **n.attributes,
                    lb_targets.ROUTE_ENI_ATTR: sorted(route_why[n.id]),
                },
            )
            if n.id in route_why
            else n
            for n in nodes
        ]
    # 인스턴스의 Auto Scaling 그룹(2.8.0 · P1-2) — 화면이 LB → 인스턴스 선을 ASG 선으로 접는 재료(`lb_targets.memberships`)
    members = lb_targets.memberships(kept, ids)
    if members:
        nodes = [_with_asg(n, members.get(n.id)) for n in nodes]
    if unresolved:
        nodes = [_with_unresolved(n, unresolved.get(n.id)) for n in nodes]
    if eips.why or why:
        nodes = [_with_why(n, eips.why.get(n.id), why) for n in nodes]
    return nodes, edges


def _via_eip(
    drafts: list[_EdgeDraft],
    eips: relations.EipOwners,
    ids: set[str],
    why: dict[tuple[str, str], set[str]],
    unresolved: dict[str, set[str]],
) -> list[_EdgeDraft]:
    """엣지 초안 중 끝이 탄력적 IP 노드인 것(Global Accelerator EIP 엔드포인트 · 배치 D)을 그 EIP 의 주인으로 옮긴다.

    EIP 를 수집했는데 주인이 없으면(연결 안 됨 · 주인 못 찾음) 초안을 버리고 까닭 줄 「<ref> eip_no_owner」를 남긴다 — 「수집하지 않음」과 가른다.
    EIP 를 수집하지 않았으면 그대로 둔다(3패스가 `not_collected` 로 남긴다).
    """
    out: list[_EdgeDraft] = []
    for d in drafts:
        if not (d.by_id and d.target_native.startswith("elastic_ip/")):
            out.append(d)
            continue
        owner = eips.owner.get(d.target_native)
        alloc = d.target_native.rsplit("/", 1)[-1]
        if owner:
            out.append(
                replace(
                    d,
                    target_native=owner,
                    attributes={**d.attributes, "via_eip": alloc},
                )
            )
        elif d.target_native in ids:
            unresolved.setdefault(d.source, set()).add(d.ref or alloc)
            if d.why_attr:
                why.setdefault((d.source, d.why_attr), set()).add(
                    f"{d.ref or alloc} eip_no_owner"
                )
        else:
            out.append(d)
    return out


def _with_why(
    node: Node, eip_line: str | None, why: dict[tuple[str, str], set[str]]
) -> Node:
    """까닭 줄(배치 D) — 빌더가 단 줄(범위 밖 등)에 3패스가 찾은 줄(수집하지 않음)을 합친다(정렬 · 중복 없음)."""
    attrs = dict(node.attributes)
    changed = False
    if eip_line:
        attrs[relations.EIP_WHY_ATTR] = eip_line
        changed = True
    for (source, attr), lines in why.items():
        if source != node.id:
            continue
        attrs[attr] = sorted(set(attrs.get(attr) or []) | lines)
        changed = True
    return replace(node, attributes=attrs) if changed else node


def _with_asg(node: Node, asg: str | None) -> Node:
    """그 인스턴스를 지금 가진 ASG 노드 id(2.8.0) — ASG 노드에는 인스턴스 목록을 싣지 않는다(`auto_scaling_group.py` 머리말)."""
    if not asg:
        return node
    return replace(node, attributes={**node.attributes, "auto_scaling_group_id": asg})


def _with_target_reasons(node: Node, lines: set[str] | None) -> Node:
    """못 이은 LB 대상의 까닭(「<대상 id> <까닭>」 · 정렬) — 화면이 「대상: … (수집하지 않는 서비스)」로 말한다."""
    if not lines:
        return node
    return replace(
        node, attributes={**node.attributes, "unresolved_targets": sorted(lines)}
    )


def _merge_views(views: list[_NodeDraft]) -> _NodeDraft:
    """같은 자원의 여러 뷰 → 노드 하나(ADR 0002 §2-2). 뷰가 하나면 손대지 않는다.

    🔴 승자는 「먼저 만난 것」이 아니라 **(계정, 리전) 최소**다. 먼저 만난 것으로 고르면 scope 의
    계정·리전 순서만 바꿔도 카드가 다른 레인으로 옮겨간다 — AWS 상태가 같은데 산출물이 달라진다.
    🔴 진 뷰의 **엣지는 승자에게 옮겨 붙인다.** TGW 피어링은 뷰마다 «자기 쪽 TGW» 로 선을 내므로
    진 뷰를 버리면 반대편 TGW 로 가는 선이 사라진다(그게 원래 증상이었다). 잃지 않는 사실은
    `attributes.merged_from` = 「이 자원을 본 다른 (계정, 리전)」 으로 남긴다.
    """
    if len(views) == 1:
        return views[0]
    ordered = sorted(views, key=lambda d: (d.node.account_id, d.node.region or ""))
    win, *losers = ordered
    attributes: dict[str, Any] = dict(win.node.attributes)
    tags: dict[str, str] = dict(win.node.tags)
    reasons: list[Reason] = list(win.node.partial_reasons)
    for other in losers:
        for key, value in other.node.attributes.items():
            attributes.setdefault(key, value)
        for key, value in other.node.tags.items():
            if key == "Name":
                continue  # 이름표는 승자 계정 것만(아래 `name` · 사용자 결정 2026-10-25)
            tags.setdefault(key, value)
        for reason in other.node.partial_reasons:
            if reason not in reasons:
                reasons.append(reason)
    # 「**다른**」 좌표만 · 한 번씩 — 같은 (계정, 리전) 뷰가 둘이면 승자 자기 좌표가 끼었다(#31 리뷰).
    home = (win.node.account_id, win.node.region)
    attributes["merged_from"] = sorted(
        {
            f"{d.node.account_id}/{d.node.region}"
            for d in losers
            if (d.node.account_id, d.node.region) != home
        }
    )
    seen: set[tuple[str, str, str | None, str | None]] = set()
    edges: list[_EdgeDraft] = []
    for view in ordered:
        for edge in view.edges:
            key = (edge.type, edge.target_native, edge.target_type, edge.disc)
            if key in seen:
                continue
            seen.add(key)
            edges.append(replace(edge, source=win.node.id))
    return replace(
        win,
        node=replace(
            win.node,
            # 이름 = 승자 계정 쪽 Name 태그만(사용자 결정 2026-10-25 · 콘솔과 같게). 피어링 어태치먼트의
            #   Name 태그는 계정마다 따로 붙는다 — 승자 쪽이 비었는데 상대 계정 이름을 빌려 오면 승자 계정
            #   콘솔은 「–」인데 우리 화면만 상대 쪽 이름(실측: 188 「–」 · 화면 「NWRA-575-to-188-PEER」)을 쓴다.
            name=win.node.name,
            attributes=attributes,
            tags=tags,
            partial=any(d.node.partial for d in ordered),
            partial_reasons=tuple(reasons),
        ),
        edges=edges,
    )


def _with_parent(
    draft: _NodeDraft,
    ids: set[str],
    failures: dict[tuple[str, str, str], Reason],
) -> Node:
    node = draft.node
    for index, candidate in enumerate(draft.parents):
        if candidate not in ids:
            continue
        if index == 0:
            return node
        # 후보를 건너뛰었다 → 건너뛴 것 중 「수집이 실패해서 모르는 것」이 있으면 사유를 남긴다.
        # 렌더러가 「불완전」으로 표시할 근거다(불변식 ④).
        for skipped in draft.parent_types[:index]:
            reason = failures.get((draft.account_id, draft.region, skipped))
            if reason is not None:
                return replace(
                    node,
                    parent_id=candidate,
                    partial=True,
                    partial_reasons=node.partial_reasons + (reason,),
                )
        return replace(node, parent_id=candidate)
    # 후보 체인의 마지막은 항상 리전 노드이고 리전 노드는 반드시 존재한다.
    raise AssertionError(f"부모 후보가 모두 없다: {node.id} ← {draft.parents}")


def _with_unresolved(node: Node, natives: set[str] | None) -> Node:
    if not natives:
        return node
    return replace(
        node,
        attributes={**node.attributes, "unresolved_refs": sorted(natives)},
    )


def _resolve_edges(
    drafts: list[_EdgeDraft],
    by_native: dict[str, str],
    by_dns: dict[str, str],
    ids: set[str],
    why_out: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[list[Edge], dict[str, set[str]]]:
    edges: dict[str, Edge] = {}
    unresolved: dict[str, set[str]] = {}
    # (출발 노드 id, 속성 이름) → 까닭 줄(배치 D `EdgeDraft.why_attr`) — 받는 쪽이 없으면 버린다
    why: dict[tuple[str, str], set[str]] = {} if why_out is None else why_out
    for draft in drafts:
        if not draft.live:
            # 죽은 상태의 부착·피어링은 선을 만들지 않는다(ADR 0002 §3).
            continue
        if draft.by_id:
            # 배치 D — 빌더가 노드 id 를 지었다(ARN · 같은 쌍). 그 id 가 이 스냅샷에 있을 때만 잇는다
            target = draft.target_native if draft.target_native in ids else None
        else:
            target = (by_dns if draft.by_dns else by_native).get(draft.target_native)
        if target is None or (
            draft.target_type is not None
            and not target.startswith(f"{draft.target_type}/")
        ):
            # 참조가 끊긴 엣지를 만들지 않되, 조용히 버리지도 않는다(ADR 0002 §5).
            unresolved.setdefault(draft.source, set()).add(
                draft.ref or draft.target_native
            )
            if draft.why_attr:
                why.setdefault((draft.source, draft.why_attr), set()).add(
                    f"{draft.ref or draft.target_native} not_collected"
                )
            continue
        if draft.source not in ids:  # 방어 — 노드 없는 엣지는 계약 위반이다
            continue
        edge_id = _edge_id(draft.type, draft.source, target, draft.disc)
        if edge_id in edges:
            continue
        edges[edge_id] = Edge(
            id=edge_id,
            type=draft.type,
            source=draft.source,
            target=target,
            directed=draft.directed,
            attributes=dict(draft.attributes),
        )
    return list(edges.values()), unresolved
