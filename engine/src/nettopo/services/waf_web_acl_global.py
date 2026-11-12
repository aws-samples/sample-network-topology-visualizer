"""Collect CloudFront-scope WAF web ACLs in us-east-1.

Reuse the regional node builder with global IDs and account parents.
Build protection links from CloudFront distribution details.
"""

from __future__ import annotations

from typing import Any

from .base import Ctx, NodeDraft, Service, Spec
from .waf_web_acl import LIST_API, build_for, enrich_for

API_REGION = "us-east-1"
SPEC = Spec(
    "wafv2",
    "list_web_acls",
    "WebACLs",
    LIST_API,
    params=(("Scope", "CLOUDFRONT"),),
    region=API_REGION,
)

_BUILD = build_for("CLOUDFRONT")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    return _BUILD(item, ctx)


SERVICE = Service(
    spec=SPEC,
    build=build,
    enrich=enrich_for("CLOUDFRONT", resources=False),
)
