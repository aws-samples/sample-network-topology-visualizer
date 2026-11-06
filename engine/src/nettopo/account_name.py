"""계정 이름 — 다이어그램의 계정 상자에 「이름 (가린 id)」를 쓰는 재료(사용자 결정 2026-10-28).

출처 = Account Management `account:GetAccountInformation` 을 **`AccountId` 없이** 부른다 → 「부른 계정 자신」의
`AccountName` 이 온다. 그래서 계정마다 **그 계정의 세션**으로 부른다: 허브 = 자기 읽기 세션 · 멤버 = assume 한
멤버 역할(`cli._SessionCache` 가 계정당 세션 하나라 assume 이 늘지 않는다). 조직 관리 계정의 권한도, `AccountId`
인자도 쓰지 않는다 — 멤버 역할이 남의 계정 이름을 물을 길을 처음부터 만들지 않는다.

🔴 **거부는 정상 경로다**(불변식 ④). 개발 읽기 권한 세트에는 이 액션이 없고(실측 2026-10-28 AccessDenied), 스택 B 를
갱신하기 전의 멤버 역할에도 없다. 그래서:

- 실행을 죽이지 않는다 — 이름만 `null` 이고 화면은 지금처럼 id 를 쓴다.
- **계정의 네트워크 데이터를 「일부만 읽음」으로 만들지 않는다** — 노드의 `partial` 도, `coverage` 행도 건드리지
  않는다. 둘 다 화면에서 그 계정·리전을 「확인하지 못했다」로 칠한다(`renderer/adapt-core.js` 의 부분 실패 절) —
  이름 하나 못 읽은 것으로 멀쩡히 읽은 VPC 를 의심하게 만들면 오판이다.
- 대신 계정 노드 속성에 **못 읽었다는 사실**을 남긴다: `name_unread` = 사유 코드(`Reason.code` 어휘 ·
  예 `access_denied`) · `name_unread_api` = 실패한 API(`account:GetAccountInformation`, 세션부터 못 만들었으면
  `sts:AssumeRole`). 원문 메시지는 싣지 않는다 — 거부 문장에는 호출자 ARN(계정ID)이 들어 있다.

🔴 `ReadOnlyViolation` 은 잡지 않는다(버그다 · `collect.py` 머리말과 같다). 응답에 이름이 없거나 빈 글자면 이름이
없는 것이지 실패가 아니다 — `null` 이고 `name_unread` 도 없다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from botocore.exceptions import BotoCoreError, ClientError

from .model import Node, Reason
from .services import base

# 「`svc:Action`」 글자 그대로가 계약이다 — 스택 A·B 의 권한과 `infra/tests/test_stack_template.py` 가 이 이름을 읽는다.
API = "account:GetAccountInformation"
SERVICE = "account"
# Account Management 는 글로벌 엔드포인트이고 us-east-1 로 서명한다 → 권한 문장도 `aws:RequestedRegion = us-east-1` 로 고정이다.
API_REGION = "us-east-1"


@dataclass(frozen=True, slots=True)
class Read:
    """계정 하나의 결과 — `name` 과 `reason` 은 동시에 서지 않는다(읽었거나 · 못 읽었거나 · 이름이 비었거나)."""

    name: str | None = None
    reason: Reason | None = None


def read(
    account_ids: Sequence[str], client_factory: base.ClientFactory
) -> dict[str, Read]:
    """계정마다 한 번 — **scope 순서로 순차** 부른다(호출 수 = 계정 수 · 결과는 계정 id 열쇠라 순서에 기대지 않는다)."""
    out: dict[str, Read] = {}
    for account_id in dict.fromkeys(account_ids):
        try:
            client = client_factory(account_id, API_REGION, SERVICE)
        except (ClientError, BotoCoreError) as exc:
            # 멤버 역할 진입부터 실패 — 실패한 API 는 `sts:AssumeRole` 이다(`collect._fetch` 1단계와 같은 규칙)
            operation = getattr(exc, "operation_name", None)
            out[account_id] = Read(
                reason=base.classify(
                    exc, f"sts:{operation}" if operation else "nettopo:client_factory"
                )[1]
            )
            continue
        try:
            got = client.get_account_information()
        except (ClientError, BotoCoreError) as exc:
            out[account_id] = Read(reason=base.classify(exc, API)[1])
            continue
        name = got.get("AccountName")
        out[account_id] = Read(
            name=(name.strip() or None) if isinstance(name, str) else None
        )
    return out


def apply(nodes: Iterable[Node], names: dict[str, Read]) -> list[Node]:
    """계정 노드에만 이름 · 못 읽음 표시를 얹는다. 다른 노드와 `partial` 은 그대로다(머리말)."""
    out: list[Node] = []
    for node in nodes:
        got = names.get(node.account_id) if node.type == "account" else None
        if got is None:
            out.append(node)
        elif got.reason is not None:
            out.append(
                replace(
                    node,
                    attributes={
                        **node.attributes,
                        "name_unread": got.reason.code,
                        "name_unread_api": got.reason.api,
                    },
                )
            )
        else:
            out.append(replace(node, name=got.name))
    return out
