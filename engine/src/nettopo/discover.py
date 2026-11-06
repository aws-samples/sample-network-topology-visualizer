"""계정 자동 발견 — ADR 0003 D1 「스택 A 는 계정 목록을 요구하지 않는다」를 코드로 옮긴 것.

허브(= 호출자) 계정에서 **TGW 어태치먼트**를 읽고 그 `ResourceOwnerId` 로 멤버 계정을 찾는다.
새 API·새 권한이 필요 없다 — `ec2:DescribeTransitGatewayAttachments` 는 이미
`docs/required-permissions.md` §1 표에 있고 수집이 쓰는 것과 같은 호출이다.

🔴 **「못 봤다」와 「없다」를 구별한다**(불변식 ④). 어태치먼트 조회가 실패한 리전은 사유를
`failures` 에 남기고, **모든 리전에서 실패하면 `blind` 가 참**이다. 그 상태로 「허브 하나뿐」이라고
수집하면 스냅샷이 「이게 전부다」라고 거짓말을 한다 — 그래서 호출자(CLI)는 `blind` 에서 멈추고
사람에게 `--accounts` 를 요구한다(목록이 있으면 목록 ∪ 허브로 진행 + 경고 · 사용자 결정 2026-10-20).

🔵 `TransitGatewayOwnerId`(공유 TGW 의 소유 계정)는 **일부러 쓰지 않는다.** ADR 0003 D1 이 적은
발견 근거는 `resourceOwnerId` 하나이고, 근거를 늘리면 「조사하지도 않은 계정을 그림에 등장시키지
않는다」(`services/base.py` `Ctx.attribute`)는 규칙과 맞물려 판단이 필요하다 — 늘리려면 ADR 에
한 줄을 먼저 더한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .model import Reason
from .services.base import ClientFactory, alive, classify, pages

DISCOVERY_API = "ec2:DescribeTransitGatewayAttachments"
_METHOD = "describe_transit_gateway_attachments"
_RESULT_KEY = "TransitGatewayAttachments"
_OWNER_KEY = "ResourceOwnerId"
_ACCOUNT_ID = re.compile(r"^[0-9]{12}$")

REGION_DISCOVERY_API = "ec2:DescribeRegions"
_REGION_METHOD = "describe_regions"
_REGION_RESULT_KEY = "Regions"
_REGION_NAME = re.compile(r"^[a-z]{2}-[a-z-]+-[0-9]+$")

# `DescribeRegions` 자체를 부를 엔드포인트가 **하나는** 필요하다(EC2 에는 전역 엔드포인트가 없다).
# 🔴 셸 기본 리전(`AWS_REGION`)을 쓰지 않는다 — 그러면 같은 명령이 사람마다 다른 범위를 수집한다
# (CLI 머리주석). 대신 **파티션**에서 정한다: 파티션은 호출자 ARN 에 들어 있어 환경과 무관하다.
BOOTSTRAP_REGIONS = {
    "aws": "us-east-1",
    "aws-cn": "cn-north-1",
    "aws-us-gov": "us-gov-west-1",
}


def bootstrap_region(caller_arn: str) -> str | None:
    """호출자 ARN 의 파티션으로 발견 부트스트랩 리전을 정한다. 모르는 파티션이면 `None`.

    모르는 파티션에서 `us-east-1` 로 추측하면 도달하지 않는 엔드포인트를 때리고 사람은 그 실패를
    권한 문제로 읽는다 — 그래서 추측하지 않고 호출자에게 `--region` 을 요구한다.
    """
    parts = caller_arn.split(":")
    return BOOTSTRAP_REGIONS.get(parts[1]) if len(parts) > 3 else None


@dataclass(frozen=True, slots=True)
class Failure:
    """한 리전의 발견 실패 — `collect()` 의 coverage 행과 같은 어휘(status·Reason)를 쓴다."""

    region: str
    status: str
    reason: Reason


@dataclass(frozen=True, slots=True)
class Discovery:
    accounts: tuple[str, ...]
    """수집 대상 = 허브 + 발견된 멤버. **정렬**돼 있다(불변식 ②)."""

    discovered: tuple[str, ...]
    """허브를 뺀 새로 찾은 계정."""

    looked: tuple[str, ...]
    """실제로 응답을 받은 리전. 비어 있으면 아무것도 보지 못했다."""

    failures: tuple[Failure, ...] = ()

    @property
    def blind(self) -> bool:
        """한 리전도 못 봤다 → 「멤버가 없다」와 구별할 수 없다."""
        return not self.looked


@dataclass(frozen=True, slots=True)
class RegionDiscovery:
    regions: tuple[str, ...]
    """계정이 **켜 둔** 리전. 정렬돼 있다(불변식 ②)."""

    asked: str
    """`DescribeRegions` 를 부른 부트스트랩 리전."""

    failure: Failure | None = None

    @property
    def blind(self) -> bool:
        return not self.regions


def discover_regions(
    *, hub_account: str, bootstrap: str, client: ClientFactory
) -> RegionDiscovery:
    """활성 리전 목록을 얻는다 — ADR 0003 D1 의 「리전은 `ec2:DescribeRegions` 로 얻는다」.

    🔴 **켜지 않은 리전(`not-opted-in`)은 넣지 않는다.** 그 리전에서는 STS·assume 이 거부되고
    (실측 2026-09-14: `ap-east-1` → `InvalidClientTokenId`) coverage 만 붉어진다 — 「못 봤다」가
    아니라 「거기에 자원을 둘 수 없다」이므로 조사 범위 밖이다. 기본 `DescribeRegions` 응답도
    활성 리전만 주지만(`AllRegions` 를 주지 않는다) 응답을 믿지 않고 한 번 더 건다.
    """
    try:
        items: list[dict[str, Any]] = pages(
            client(hub_account, bootstrap, "ec2"), _REGION_METHOD, _REGION_RESULT_KEY
        )
    except (ClientError, BotoCoreError) as exc:
        status, reason = classify(exc, REGION_DISCOVERY_API)
        return RegionDiscovery(
            regions=(),
            asked=bootstrap,
            failure=Failure(region=bootstrap, status=status, reason=reason),
        )
    names = {
        str(item.get("RegionName") or "")
        for item in items
        if item.get("OptInStatus") != "not-opted-in"
    }
    return RegionDiscovery(
        regions=tuple(sorted(n for n in names if _REGION_NAME.match(n))),
        asked=bootstrap,
    )


def discover_accounts(
    *,
    hub_account: str,
    regions: Sequence[str],
    client: ClientFactory,
) -> Discovery:
    """허브의 TGW 어태치먼트 소유 계정으로 수집 대상 계정을 만든다.

    `client` 는 수집과 **같은** 팩토리다(`(account_id, region, service) -> client`) — 읽기 전용
    가드가 그대로 걸리고, 테스트는 가짜 클라이언트를 끼운다.
    """
    owners: set[str] = set()
    looked: list[str] = []
    failures: list[Failure] = []

    for region in dict.fromkeys(regions):
        try:
            items: list[dict[str, Any]] = pages(
                client(hub_account, region, "ec2"), _METHOD, _RESULT_KEY
            )
        except (ClientError, BotoCoreError) as exc:
            status, reason = classify(exc, DISCOVERY_API)
            failures.append(Failure(region=region, status=status, reason=reason))
            continue
        looked.append(region)
        for item in items:
            # 죽은 어태치먼트(deleting·rejected…)는 계정을 만들지 않는다. 지워진 경로를 근거로
            # 계정을 더하면 그 계정은 assume 도 안 되고 coverage 만 붉어진다.
            if not alive(item.get("State")):
                continue
            raw = item.get(_OWNER_KEY)
            owner = str(raw) if raw else ""
            if _ACCOUNT_ID.match(owner):
                owners.add(owner)

    discovered = tuple(sorted(owners - {hub_account}))
    return Discovery(
        accounts=tuple(sorted(owners | {hub_account})),
        discovered=discovered,
        looked=tuple(looked),
        failures=tuple(failures),
    )
