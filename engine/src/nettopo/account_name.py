"""Read account display names through each account's AWS client.

Omit AccountId from GetAccountInformation to read the calling account's
name. Record failures in the account node's name_unread and
name_unread_api attributes. Name lookups do not affect resource
collection
status in coverage.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from botocore.exceptions import BotoCoreError, ClientError

from .model import Node, Reason
from .services import base

API = "account:GetAccountInformation"
SERVICE = "account"
# Sign Account Management requests in us-east-1.
API_REGION = "us-east-1"


@dataclass(frozen=True, slots=True)
class Read:

    """Store an account name lookup result and its failure reason.

    name: Account name (None if absent or the lookup fails)
    reason: Lookup failure Reason (None on success)
    """
    name: str | None = None
    reason: Reason | None = None


def read(
    account_ids: Sequence[str], client_factory: base.ClientFactory
) -> dict[str, Read]:
    """Read account names with per-account sessions and return results
    by ID.

    account_ids: Account IDs whose names to read
    client_factory: Function that creates Account clients by account and
        region

    Call GetAccountInformation without AccountId to query the session's
    own
    account. Record lookup failures in Read.reason.
    """
    out: dict[str, Read] = {}
    for account_id in dict.fromkeys(account_ids):
        try:
            client = client_factory(account_id, API_REGION, SERVICE)
        except (ClientError, BotoCoreError) as exc:
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
    """Return nodes with account name lookup results applied.

    nodes: Resources, including account nodes
    names: Name lookup results by account ID

    Record failures in the account node's attributes.name_unread and
    name_unread_api.
    """
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
