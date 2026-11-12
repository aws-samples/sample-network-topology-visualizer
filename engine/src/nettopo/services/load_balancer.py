"""Collect ALB, NLB, and GWLB configuration, listeners, and targets.

Use target registrations for topology and target health for
observations.
Keep load balancers when additional queries fail and record reasons in
partial_reasons.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..model import Node, Reason, node_id
from .base import (
    Ctx,
    EdgeDraft,
    Fetched,
    NodeDraft,
    Service,
    TARGET_HEALTH_DETAIL,
    Spec,
    TargetGroupRef,
    TargetRef,
    alive,
    classify,
    draft,
    health_detail,
    pages,
)
from .base import sorted_strs as _sorted

SPEC = Spec(
    "elbv2",
    "describe_load_balancers",
    "LoadBalancers",
    "elasticloadbalancing:DescribeLoadBalancers",
)

_FOLLOWUPS: tuple[tuple[str, str, str], ...] = (
    ("Listeners", "describe_listeners", "elasticloadbalancing:DescribeListeners"),
    (
        "TargetGroups",
        "describe_target_groups",
        "elasticloadbalancing:DescribeTargetGroups",
    ),
)


_WEB_ACL_API = "wafv2:GetWebACLForResource"
_WEB_ACL_DEPENDENT_API = "wafv2:GetWebACL"
_WEB_ACL_TYPES = frozenset({"application"})


def _web_acl(client: Any, arn: str) -> tuple[str | None, Reason | None]:
    try:
        got = client.get_web_acl_for_resource(ResourceArn=arn)
    except (ClientError, BotoCoreError) as exc:
        return None, classify(exc, _WEB_ACL_API)[1]
    acl = got.get("WebACL") or {}
    return (acl.get("ARN") or None), None


RULES_API = "elasticloadbalancing:DescribeRules"
LISTENER_CERTS_API = "elasticloadbalancing:DescribeListenerCertificates"
_RULE_COUNTS = "_RuleCounts"
_LISTENER_CERTS = "_ListenerCerts"
_RULE_TYPES = frozenset({"application"})
_CERT_PROTOCOLS = frozenset({"HTTPS", "TLS"})


def _listener_extras(
    client: Any, lb_type: str, listeners: list[dict[str, Any]]
) -> tuple[dict[str, int], dict[str, list[str]], list[Reason]]:
    counts: dict[str, int] = {}
    certs: dict[str, list[str]] = {}
    failed: list[Reason] = []
    for listener in listeners:
        arn = listener.get("ListenerArn")
        if not arn:
            continue
        if lb_type in _RULE_TYPES:
            try:
                counts[str(arn)] = len(
                    pages(client, "describe_rules", "Rules", ListenerArn=arn)
                )
            except (ClientError, BotoCoreError) as exc:
                failed.append(classify(exc, RULES_API)[1])
        if str(listener.get("Protocol") or "").upper() in _CERT_PROTOCOLS:
            try:
                got = pages(
                    client,
                    "describe_listener_certificates",
                    "Certificates",
                    ListenerArn=arn,
                )
                certs[str(arn)] = [
                    str(c["CertificateArn"])
                    for c in sorted(
                        got,
                        key=lambda c: (
                            not c.get("IsDefault"),
                            str(c.get("CertificateArn") or ""),
                        ),
                    )
                    if c.get("CertificateArn")
                ]
            except (ClientError, BotoCoreError) as exc:
                failed.append(classify(exc, LISTENER_CERTS_API)[1])
    return counts, certs, failed


def enrich(client: Any, items: list[Fetched]) -> list[Fetched]:
    """Query listeners, target groups, registrations, and additional
    settings.

    client: boto3 client for this service
    items: Fetched items from the listing API
    """
    out: list[Fetched] = []
    for item in items:
        arn = item.payload.get("LoadBalancerArn")
        payload = dict(item.payload)
        reasons: list[Reason] = list(item.reasons)
        for key, method, api in _FOLLOWUPS:
            if (
                not arn
            ):
                reasons.append(
                    Reason(code="api_error", api=api, message="LoadBalancerArn 없음")
                )
                continue
            try:
                payload[key] = pages(client, method, key, LoadBalancerArn=arn)
            except (ClientError, BotoCoreError) as exc:
                reasons.append(classify(exc, api)[1])
        listeners = payload.get("Listeners")
        if listeners:
            counts, certs, failed_extras = _listener_extras(
                client, str(payload.get("Type") or ""), listeners
            )
            payload[_RULE_COUNTS] = counts
            payload[_LISTENER_CERTS] = certs
            reasons.extend(failed_extras)
        groups = payload.get("TargetGroups")
        if groups is not None:
            refs: list[TargetRef] = []
            counted, found, failed = _registered(client, groups, refs)
            payload[_REGISTERED] = counted
            payload[_TARGETS] = tuple(sorted(refs, key=_ref_key))
            if found is not None:
                payload[_TARGET_HEALTH] = found
            reasons.extend(failed)
            clusters, tag_failed = _group_clusters(client, groups)
            if tag_failed is not None:
                reasons.append(tag_failed)
            else:
                payload[_TG_CLUSTERS] = clusters
        out.append(Fetched(payload=payload, reasons=tuple(reasons)))
    return out


TAGS_API = "elasticloadbalancing:DescribeTags"
_TAGS_CHUNK = 20
_EKS_TG_TAG = "elbv2.k8s.aws/cluster"
_TG_CLUSTERS = "_TgClusters"


def _group_clusters(
    client: Any, groups: list[dict[str, Any]]
) -> tuple[dict[str, str], Reason | None]:
    arns = sorted({str(g["TargetGroupArn"]) for g in groups if g.get("TargetGroupArn")})
    out: dict[str, str] = {}
    try:
        for i in range(0, len(arns), _TAGS_CHUNK):
            got = client.describe_tags(ResourceArns=arns[i : i + _TAGS_CHUNK])
            for desc in got.get("TagDescriptions") or []:
                for tag in desc.get("Tags") or []:
                    if tag.get("Key") == _EKS_TG_TAG and tag.get("Value"):
                        out[str(desc.get("ResourceArn"))] = str(tag["Value"])
    except (ClientError, BotoCoreError) as exc:
        return {}, classify(exc, TAGS_API)[1]
    return out, None


TARGET_HEALTH_API = "elasticloadbalancing:DescribeTargetHealth"
_REGISTERED = "_Registered"
_TARGET_HEALTH = "_TargetHealth"
_TARGETS = "_Targets"


def _ref_key(ref: TargetRef) -> tuple[str, str, str, int]:
    return (ref.target_group, ref.target_group_arn, ref.target_id, ref.port or -1)


def _group_refs(
    groups: list[dict[str, Any]], clusters: dict[str, str] | None = None
) -> tuple[TargetGroupRef, ...]:
    out = {
        str(g["TargetGroupArn"]): TargetGroupRef(
            name=str(g.get("TargetGroupName") or ""),
            arn=str(g["TargetGroupArn"]),
            target_type=str(g.get("TargetType") or "").lower(),
            port=int(g["Port"]) if isinstance(g.get("Port"), int) else None,
            cluster=(clusters or {}).get(str(g["TargetGroupArn"])),
        )
        for g in groups
        if g.get("TargetGroupArn")
    }
    return tuple(out[k] for k in sorted(out))


def _target_refs(group: dict[str, Any], got: list[dict[str, Any]]) -> list[TargetRef]:
    kind = str(group.get("TargetType") or "").lower()
    tg_port = group.get("Port")
    out: list[TargetRef] = []
    for desc in got:
        target = desc.get("Target") or {}
        tid = target.get("Id")
        if not tid:
            continue
        port = target.get("Port", tg_port if kind != "lambda" else None)
        out.append(
            TargetRef(
                target_group=str(group.get("TargetGroupName") or ""),
                target_group_arn=str(group.get("TargetGroupArn") or ""),
                target_type=kind,
                target_id=str(tid),
                port=int(port) if isinstance(port, int) else None,
                zone=str(target["AvailabilityZone"])
                if target.get("AvailabilityZone")
                else None,
                health=str((desc.get("TargetHealth") or {}).get("State") or "") or None,
                health_reason=str((desc.get("TargetHealth") or {}).get("Reason") or "")
                or None,
            )
        )
    return out


def _registered(
    client: Any, groups: list[dict[str, Any]], refs: list[TargetRef]
) -> tuple[list[tuple[str, int | None]], list[dict[str, Any]] | None, list[Reason]]:
    counted: list[tuple[str, int | None]] = []
    found: list[dict[str, Any]] = []
    failed: list[Reason] = []
    for group in groups:
        name = str(group.get("TargetGroupName") or "")
        arn = group.get("TargetGroupArn")
        if not arn:
            failed.append(Reason("api_error", TARGET_HEALTH_API, "TargetGroupArn 없음"))
            counted.append((name, None))
            continue
        try:
            got = (
                client.describe_target_health(TargetGroupArn=arn).get(
                    "TargetHealthDescriptions"
                )
                or []
            )
        except (ClientError, BotoCoreError) as exc:
            failed.append(classify(exc, TARGET_HEALTH_API)[1])
            counted.append((name, None))
            continue
        counted.append((name, len(got)))
        found.extend(got)
        refs.extend(_target_refs(group, got))
    return counted, (None if failed else found), failed


ENDPOINT_SERVICE_API = "ec2:DescribeVpcEndpointServiceConfigurations"
_ENDPOINT_SERVICES = "_EndpointServices"
_PROVIDER_TYPES = frozenset({"gateway", "network"})


def join_endpoint_services(
    client: Any, items: list[Fetched], failed: Reason | None
) -> list[Fetched]:
    want = [
        i
        for i, it in enumerate(items)
        if str(it.payload.get("Type") or "") in _PROVIDER_TYPES
    ]
    if not want:
        return items
    names: dict[str, set[str]] | None = None
    reason: Reason | None = None
    if client is None:
        reason = failed or Reason(
            "api_error", ENDPOINT_SERVICE_API, "ec2 클라이언트 없음"
        )
    else:
        try:
            got = pages(
                client,
                "describe_vpc_endpoint_service_configurations",
                "ServiceConfigurations",
            )
        except (ClientError, BotoCoreError) as exc:
            reason = classify(exc, ENDPOINT_SERVICE_API)[1]
        else:
            names = {}
            for cfg in got:
                name = cfg.get("ServiceName")
                if not name:
                    continue
                for arn in [
                    *(cfg.get("GatewayLoadBalancerArns") or []),
                    *(cfg.get("NetworkLoadBalancerArns") or []),
                ]:
                    names.setdefault(str(arn), set()).add(str(name))
    out = list(items)
    for i in want:
        it = out[i]
        if names is None:
            out[i] = Fetched(it.payload, it.reasons + (reason,)) if reason else it
            continue
        arn = str(it.payload.get("LoadBalancerArn") or "")
        out[i] = Fetched(
            {**it.payload, _ENDPOINT_SERVICES: sorted(names.get(arn, set()))},
            it.reasons,
        )
    return out


def join_web_acl(
    client: Any, items: list[Fetched], failed: Reason | None
) -> list[Fetched]:
    """Query ALB WAF web ACL associations to enrich load balancer items.

    client: WAFv2 boto3 client (None if creation failed)
    items: Collected load balancer items
    failed: Reason for WAFv2 client creation failure

    Keep existing items on query failure and add reasons to
    Fetched.reasons.
    """
    out: list[Fetched] = []
    for item in items:
        if str(item.payload.get("Type") or "") not in _WEB_ACL_TYPES:
            out.append(item)
            continue
        arn = item.payload.get("LoadBalancerArn")
        if client is None:
            reason = failed or Reason(
                "api_error", _WEB_ACL_API, "wafv2 클라이언트 없음"
            )
        elif not arn:
            reason = Reason("api_error", _WEB_ACL_API, "LoadBalancerArn 없음")
        else:
            acl, reason = _web_acl(client, str(arn))
            if reason is None:
                out.append(Fetched({**item.payload, "WebAclArn": acl}, item.reasons))
                continue
        out.append(Fetched(item.payload, item.reasons + (reason,)))
    return out


def state_attributes(item: dict[str, Any]) -> dict[str, Any]:
    found = item.get(_TARGET_HEALTH)
    if found is None:
        return {}
    pairs = [
        (
            str((t.get("TargetHealth") or {}).get("State") or ""),
            (t.get("TargetHealth") or {}).get("Reason"),
        )
        for t in found
    ]
    states = [s for s, _ in pairs]
    out: dict[str, Any] = {
        "target_count": len(states),
        "healthy_target_count": states.count("healthy"),
        "unhealthy_target_count": states.count("unhealthy"),
    }
    for key, _ in TARGET_HEALTH_DETAIL:
        out[key] = 0
    for state, reason in pairs:
        key = health_detail(state, reason)
        if key is not None:
            out[key] += 1
    return out


def _listener_labels(listeners: list[dict[str, Any]]) -> list[str]:
    labels: dict[tuple[str, int], str] = {}
    for listener in listeners:
        protocol = str(listener.get("Protocol") or "").upper()
        port = listener.get("Port")
        if not protocol or port is None:
            continue
        labels[(protocol, int(port))] = f"{protocol}:{int(port)}"
    return [labels[key] for key in sorted(labels)]


def _arn_tail(arn: Any) -> str:
    text = str(arn or "")
    if ":targetgroup/" in text:
        return text.split(":targetgroup/", 1)[1].split("/", 1)[0]
    return text.rsplit("/", 1)[-1] if "/" in text else text


def _action_text(actions: list[dict[str, Any]]) -> str:
    parts = []
    for a in sorted(actions, key=lambda a: int(a.get("Order") or 0)):
        kind = str(a.get("Type") or "")
        if kind == "forward":
            groups = ((a.get("ForwardConfig") or {}).get("TargetGroups")) or []
            if groups:
                body = ",".join(
                    f"{_arn_tail(g.get('TargetGroupArn'))}={g.get('Weight')}"
                    if len(groups) > 1
                    else _arn_tail(g.get("TargetGroupArn"))
                    for g in groups
                )
            else:
                body = _arn_tail(a.get("TargetGroupArn"))
        elif kind == "redirect":
            r = a.get("RedirectConfig") or {}
            body = f"{r.get('Protocol', '')}://{r.get('Host', '')}:{r.get('Port', '')}{r.get('Path', '')} {r.get('StatusCode', '')}".strip()
        elif kind == "fixed-response":
            body = str((a.get("FixedResponseConfig") or {}).get("StatusCode") or "")
        else:
            body = ""
        parts.append(f"{kind}:{body}" if body else kind)
    return ";".join(parts)


def _listener_rows(item: dict[str, Any]) -> list[str]:
    counts = item.get(_RULE_COUNTS) or {}
    certs = item.get(_LISTENER_CERTS) or {}
    rows = []
    for listener in item.get("Listeners") or []:
        arn = str(listener.get("ListenerArn") or "")
        port = listener.get("Port")
        cert_list = certs.get(arn)
        if cert_list is None:
            cert_list = [
                str(c["CertificateArn"])
                for c in listener.get("Certificates") or []
                if c.get("CertificateArn")
            ]
        rows.append(
            (
                int(port) if isinstance(port, int) else -1,
                "\t".join(
                    (
                        str(listener.get("Protocol") or ""),
                        "" if port is None else str(port),
                        _action_text(listener.get("DefaultActions") or []),
                        str(counts[arn]) if arn in counts else "",
                        str(listener.get("SslPolicy") or ""),
                        ",".join(cert_list),
                    )
                ),
            )
        )
    return [r[1] for r in sorted(rows)]


def _target_group_rows(groups: list[dict[str, Any]]) -> list[str]:
    rows = []
    for g in groups:
        matcher = g.get("Matcher") or {}
        rows.append(
            "\t".join(
                str(v) if v is not None else ""
                for v in (
                    g.get("TargetGroupName"),
                    g.get("Protocol"),
                    g.get("Port"),
                    g.get("TargetType"),
                    g.get("HealthCheckProtocol"),
                    g.get("HealthCheckPort"),
                    g.get("HealthCheckPath"),
                    g.get("HealthyThresholdCount"),
                    g.get("UnhealthyThresholdCount"),
                    g.get("HealthCheckIntervalSeconds"),
                    g.get("HealthCheckTimeoutSeconds"),
                    matcher.get("HttpCode") or matcher.get("GrpcCode"),
                    None
                    if g.get("HealthCheckEnabled") is None
                    else ("true" if g.get("HealthCheckEnabled") else "false"),
                )
            )
        )
    return sorted(rows)


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    owner = ctx.account_id
    native = item["LoadBalancerName"]
    nid = node_id("load_balancer", owner, ctx.region, native)
    zones = item.get("AvailabilityZones") or []
    state = str((item.get("State") or {}).get("Code") or "")
    subnets = _sorted(z.get("SubnetId") for z in zones)
    edges = [
        EdgeDraft(
            "load_balancer_placement",
            nid,
            subnet,
            "subnet",
            False,
            {"state": state},
            live=alive(state),
        )
        for subnet in subnets
    ]
    attributes: dict[str, Any] = {
        "state": state,
        "scheme": item.get("Scheme"),
        "load_balancer_type": item.get("Type"),
        "dns_name": item.get("DNSName"),
        "availability_zones": _sorted(z.get("ZoneName") for z in zones),
        "subnet_ids": subnets,
        "security_group_ids": _sorted(item.get("SecurityGroups") or []),
    }
    listeners = item.get("Listeners")
    if listeners is not None:
        attributes["listeners"] = _listener_labels(listeners)
        attributes["listener_details"] = _listener_rows(item)
    if "WebAclArn" in item:
        attributes["web_acl_arn"] = item["WebAclArn"]
    if _ENDPOINT_SERVICES in item:
        attributes["endpoint_service_names"] = list(item[_ENDPOINT_SERVICES])
    target_groups = item.get("TargetGroups")
    if target_groups is not None:
        attributes["target_groups"] = _sorted(
            tg.get("TargetGroupName") for tg in target_groups
        )
        attributes["target_group_details"] = _target_group_rows(target_groups)
    registered = item.get(_REGISTERED)
    if registered is not None:
        by_name = {name: n for name, n in registered if name}
        attributes["target_group_registered_target_counts"] = [
            by_name[name] for name in sorted(by_name)
        ]
        counts = [n for _, n in registered]
        if all(n is not None for n in counts):
            attributes["registered_target_count"] = sum(
                n for n in counts if n is not None
            )
    node = Node(
        id=nid,
        type="load_balancer",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("LoadBalancerArn"),
        name=native,
        attributes=attributes,
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
        lb_targets=tuple(item.get(_TARGETS) or ()),
        lb_groups=_group_refs(target_groups, item.get(_TG_CLUSTERS))
        if target_groups is not None
        else (),
    )


SERVICE = Service(
    spec=SPEC,
    build=build,
    enrich=enrich,
    state_attributes=state_attributes,
    peer_enrich=(("wafv2", join_web_acl), ("ec2", join_endpoint_services)),
)
