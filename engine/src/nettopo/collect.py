"""Collect resources in scope and build Node and Edge objects.

Gather results across accounts and regions before resolving parents and
relationships. Record listing failures and item_count=None in
Snapshot.coverage. A successful empty listing has item_count=0.
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

from .services import REGISTRY, base

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
    "elastic_ip",
    "api_gateway",
    "api_gateway_v2",
    "rds_db_proxy",
    "global_accelerator",
    "core_network",
    "lattice_service_network",
    "lattice_service",
    "waf_web_acl",
    "waf_web_acl_global",
)


_SPECS: dict[str, _Spec] = {rt: service.spec for rt, service in REGISTRY.items()}

_COLLECT_ORDER = SUPPORTED_RESOURCE_TYPES

_FETCH_CONCURRENCY = 12

_PAIR_CONCURRENCY = 8


def utc_now() -> str:
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
    """Return resources, relationships, and collection status for a
    Scope.

    Return a Snapshot. With with_state=True, collect additional
    attributes
    needed for observations. structure_lambdas supplies function
    placements
    from the existing topology to resolve owners of shared ENIs during
    observation collection; it requires with_state=True.
    """
    if structure_lambdas and not with_state:
        raise ValueError("structure_lambdas 는 상태 단(with_state=True) 전용이다")
    unknown = [t for t in scope.resource_types if t not in _SPECS]
    if unknown:
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

    for chunk in _chunks(pairs, _PAIR_CONCURRENCY):
        for ctx, (items, region_coverage, region_failures) in zip(
            chunk,
            _fetch_pairs(chunk, client_factory, requested),
            strict=True,
        ):
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
                        draft.node = replace(
                            draft.node,
                            partial=True,
                            partial_reasons=draft.node.partial_reasons + item.reasons,
                        )
                    drafts.append(draft)

    found = lb_targets.lookup(drafts, client_factory)
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
    """Collect account-region pairs in parallel and return results in
    order.
    """
    if len(chunk) == 1:
        return [_fetch(chunk[0], client_factory, requested)]

    def one_pair(ctx: _Ctx) -> _Pair:
        return _fetch(ctx, client_factory, requested)

    with ThreadPoolExecutor(
        max_workers=len(chunk), thread_name_prefix="nettopo-pair"
    ) as pool:
        return list(pool.map(one_pair, chunk))


def _client_key(ctx: _Ctx, resource_type: str) -> tuple[str, str]:
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
    client_failures: dict[str, tuple[str, Reason]] = {}

    def fail(resource_type: str, status: str, reason: Reason) -> None:
        coverage.append(
            CoverageEntry(
                ctx.account_id, ctx.region, resource_type, status, reason, None
            )
        )
        failures[(ctx.account_id, ctx.region, resource_type)] = reason

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
            operation = getattr(exc, "operation_name", None)
            api = f"sts:{operation}" if operation else "nettopo:client_factory"
            client_failures[key] = _classify(exc, api)

    def one(
        resource_type: str,
    ) -> tuple[list[_Fetched], int, tuple[str, Reason] | None]:
        spec = _SPECS[resource_type]
        client = clients[_client_key(ctx, resource_type)]
        try:
            got = _pages(client, spec.method, spec.result_key, **dict(spec.params))
        except (ClientError, BotoCoreError) as exc:
            return [], 0, _classify(exc, spec.api)
        fetched = [_Fetched(payload) for payload in got]
        service = REGISTRY.get(resource_type)
        if service is not None and service.enrich is not None:
            try:
                fetched = service.enrich(client, fetched)
            except base.ListingFailed as exc:
                return [], 0, (exc.status, exc.reason)
        for peer, join in service.peer_enrich if service is not None else ():
            failed = client_failures.get((peer, ctx.region))
            fetched = join(
                clients.get((peer, ctx.region)), fetched, failed[1] if failed else None
            )
        return fetched, len(got), None

    todo = [rt for rt in requested if _client_key(ctx, rt) not in client_failures]
    done: dict[str, tuple[list[_Fetched], int, tuple[str, Reason] | None]] = {}
    if todo:
        with ThreadPoolExecutor(
            max_workers=min(len(todo), _FETCH_CONCURRENCY),
            thread_name_prefix="nettopo-fetch",
        ) as pool:
            done = dict(zip(todo, pool.map(one, todo), strict=True))

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




def _edge_id(edge_type: str, source: str, target: str, disc: str | None) -> str:
    """Build a string for Edge.id.

    Pattern: <type>|<source>|<target>[|<disc>]
    """
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
    groups: dict[tuple[str, str], list[_NodeDraft]] = {}
    order: list[tuple[str, str]] = []
    for draft in drafts:
        if draft.node.id in ids:
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
    by_dns: dict[str, str] = {}
    for draft in kept:
        native = draft.node.native_id
        if native and native not in by_native:
            by_native[native] = draft.node.id
        dns = draft.node.attributes.get("dns_name")
        if isinstance(dns, str) and dns:
            by_dns.setdefault(base.dns_key(dns), draft.node.id)
        if draft.node.type == "cloudfront":
            cf = draft.node.attributes.get("domain_name")
            if isinstance(cf, str) and cf:
                by_dns.setdefault(base.dns_key(cf), draft.node.id)

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
    eips = relations.eip_owners(kept, ids, by_native)
    why: dict[tuple[str, str], set[str]] = {}
    unresolved_pre: dict[str, set[str]] = {}
    edge_drafts = _via_eip(edge_drafts, eips, ids, why, unresolved_pre)
    edges, unresolved = _resolve_edges(edge_drafts, by_native, by_dns, ids, why)
    for source, refs in unresolved_pre.items():
        unresolved.setdefault(source, set()).update(refs)
    edges.extend(eips.edges)
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
    provided = endpoint_providers.join(kept, ids)
    edges.extend(provided.edges)
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
    if not asg:
        return node
    return replace(node, attributes={**node.attributes, "auto_scaling_group_id": asg})


def _with_target_reasons(node: Node, lines: set[str] | None) -> Node:
    if not lines:
        return node
    return replace(
        node, attributes={**node.attributes, "unresolved_targets": sorted(lines)}
    )


def _merge_views(views: list[_NodeDraft]) -> _NodeDraft:
    """Merge attributes and relationships for resources seen across
    scopes.
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
                continue
            tags.setdefault(key, value)
        for reason in other.node.partial_reasons:
            if reason not in reasons:
                reasons.append(reason)
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
    why: dict[tuple[str, str], set[str]] = {} if why_out is None else why_out
    for draft in drafts:
        if not draft.live:
            continue
        if draft.by_id:
            target = draft.target_native if draft.target_native in ids else None
        else:
            target = (by_dns if draft.by_dns else by_native).get(draft.target_native)
        if target is None or (
            draft.target_type is not None
            and not target.startswith(f"{draft.target_type}/")
        ):
            unresolved.setdefault(draft.source, set()).add(
                draft.ref or draft.target_native
            )
            if draft.why_attr:
                why.setdefault((draft.source, draft.why_attr), set()).add(
                    f"{draft.ref or draft.target_native} not_collected"
                )
            continue
        if draft.source not in ids:
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
