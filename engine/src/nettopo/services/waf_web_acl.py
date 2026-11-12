"""Collect regional WAF web ACLs and API Gateway protection links.

Resolve ALB and CloudFront associations from collected attributes. Keep
rules in evaluation priority order. Leave associations unknown if
queries
fail.
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
    classify,
    draft,
)

LIST_API = "wafv2:ListWebACLs"
GET_API = "wafv2:GetWebACL"
RESOURCES_API = "wafv2:ListResourcesForWebACL"
EDGE = "waf_protection"
WHY_ATTR = "unresolved_protected"
SPEC = Spec(
    "wafv2", "list_web_acls", "WebACLs", LIST_API, params=(("Scope", "REGIONAL"),)
)
_DETAIL = "_WebAcl"
_STAGES = "_ApiStages"
_SCOPE_ATTR = {"REGIONAL": "regional", "CLOUDFRONT": "cloudfront"}


def acl_node_id(arn: str) -> str | None:
    """Convert a web ACL ARN to Node.id.

    Return None if the ARN is not a web ACL. For global scope, use
    global in
    Node.id's region component.
    """
    parts = str(arn or "").split(":", 5)
    if len(parts) != 6 or parts[0] != "arn" or parts[2] != "wafv2":
        return None
    res = parts[5].split("/")
    if len(res) != 4 or res[1] != "webacl" or res[0] not in ("regional", "global"):
        return None
    region = GLOBAL if res[0] == "global" else parts[3]
    return node_id("waf_web_acl", parts[4], region, res[3])


def _api_of(stage_arn: str) -> tuple[str, str] | None:
    parts = str(stage_arn).split(":", 5)
    if len(parts) != 6 or parts[2] != "apigateway":
        return None
    res = parts[5].split("/")
    if len(res) != 5 or res[1] != "restapis" or res[3] != "stages":
        return None
    return res[2], res[4]


def enrich_for(scope: str, *, resources: bool) -> Any:

    """Return a detail-query function for the web ACL scope.

    scope: WAF query scope (REGIONAL or CLOUDFRONT)
    resources: Whether to query API Gateway protected resources too
    """
    def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
        out: list[Fetched] = []
        for it in items:
            p = it.payload
            reasons = list(it.reasons)
            payload = dict(p)
            try:
                got = client.get_web_acl(
                    Name=str(p.get("Name")), Scope=scope, Id=str(p.get("Id"))
                )
                payload[_DETAIL] = got.get("WebACL") or {}
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, GET_API)[1])
            if resources:
                try:
                    arns = client.list_resources_for_web_acl(
                        WebACLArn=str(p.get("ARN")), ResourceType="API_GATEWAY"
                    ).get("ResourceArns")
                    payload[_STAGES] = sorted(str(a) for a in arns or [])
                except (ClientError, BotoCoreError) as exc:
                    reasons.append(classify(exc, RESOURCES_API)[1])
            out.append(Fetched(payload, tuple(reasons)))
        return out

    return enrich


def _action(a: dict[str, Any] | None) -> str | None:
    keys = sorted(k for k in (a or {}) if k in ("Allow", "Block"))
    return keys[0].lower() if len(keys) == 1 else None


_RULE_ACTIONS = ("Allow", "Block", "Count", "Captcha", "Challenge")


def _rule_action(r: dict[str, Any]) -> str:
    action = r.get("Action") or {}
    keys = [k for k in _RULE_ACTIONS if k in action]
    if keys:
        return keys[0].lower()
    override = r.get("OverrideAction") or {}
    if "Count" in override:
        return "override:count"
    if "None" in override:
        return "none"
    return ""


def _detail_attrs(detail: dict[str, Any]) -> dict[str, Any]:
    rules = sorted(
        detail.get("Rules") or [],
        key=lambda r: (int(r.get("Priority") or 0), str(r.get("Name") or "")),
    )
    managed = {
        f"{m.get('VendorName')}/{m.get('Name')}"
        for r in rules
        for m in [(r.get("Statement") or {}).get("ManagedRuleGroupStatement")]
        if m and m.get("VendorName") and m.get("Name")
    }
    attrs: dict[str, Any] = {
        "rule_count": len(rules),
        "rule_names": [str(r.get("Name")) for r in rules if r.get("Name")],
        "rules": [
            "\t".join(
                (
                    str(int(r.get("Priority") or 0)),
                    str(r.get("Name") or ""),
                    _rule_action(r),
                )
            )
            for r in rules
        ],
        "managed_rule_groups": sorted(managed),
        "firewall_manager": bool(detail.get("ManagedByFirewallManager")),
    }
    action = _action(detail.get("DefaultAction"))
    if action is not None:
        attrs["default_action"] = action
    if isinstance(detail.get("Capacity"), int):
        attrs["capacity"] = detail["Capacity"]
    return attrs


def build_for(scope: str) -> Any:
    """Return a node-building function for the web ACL scope.

    scope: WAF query scope (REGIONAL or CLOUDFRONT)
    """
    def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
        owner = ctx.account_id
        native = str(item["Id"])
        glob = scope == "CLOUDFRONT"
        nid = node_id("waf_web_acl", owner, GLOBAL if glob else ctx.region, native)
        attributes: dict[str, Any] = {"scope": _SCOPE_ATTR[scope]}
        if _DETAIL in item:
            attributes.update(_detail_attrs(item[_DETAIL]))
        edges: list[EdgeDraft] = []
        stages = item.get(_STAGES)
        if stages is not None:
            per: dict[str, set[str]] = {}
            for arn in stages:
                got = _api_of(arn)
                if got is not None:
                    per.setdefault(got[0], set()).add(got[1])
            for api in sorted(per):
                edges.append(
                    EdgeDraft(
                        EDGE,
                        nid,
                        node_id("api_gateway", owner, ctx.region, api),
                        None,
                        True,
                        {"stages": sorted(per[api])},
                        by_id=True,
                        ref=api,
                        why_attr=WHY_ATTR,
                    )
                )
        node = Node(
            id=nid,
            type="waf_web_acl",
            account_id=owner,
            region=None if glob else ctx.region,
            native_id=native,
            arn=item.get("ARN"),
            name=item.get("Name") or native,
            attributes=attributes,
        )
        return draft(
            node=node,
            ctx=ctx,
            parents=(node_id("account", owner),) if glob else (ctx.region_node(owner),),
            parent_types=("account",) if glob else ("region",),
            edges=edges,
        )

    return build


SERVICE = Service(
    spec=SPEC,
    build=build_for("REGIONAL"),
    enrich=enrich_for("REGIONAL", resources=True),
)
