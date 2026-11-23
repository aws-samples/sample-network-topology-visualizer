"""The observation axis (`nettopo observe`). This is the only place `down` can
be measured.

The reason this file has to exist is what a real account actually returns: it
never gives us `down`. Counting every state value across a structure snapshot
of two accounts and three Regions turned up only `available` (VPC, subnet, NAT
gateway, TGW, TGW attachment, endpoint), `attached` (IGW), `active` (load
balancer, peering), `associated` (association) and `blackhole=False`. Nothing
was broken. So a passing run against a real account is no evidence that the
verdict logic is alive: with the verdict gutted entirely, everything still
reports `up`. The fixtures in `fakes.py` do the opposite and carry dead states
on purpose, namely a failed NAT gateway, a TGW attachment being deleted, a
failed peering, a failed load balancer, a blackhole route and a detached IGW.
Whether those six really come out as `down` is what this file is about.

It also forces the table of node types to agree with the constants in the code.
Adding a row to the table without adding a rule leaves that type silently
without a state. This is the production half of that; the contract half is
`test_o7_covers_every_node_type` in `schema/tests/test_observation_schema.py`.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fakes import (
    ACCOUNT,
    EGRESS_NAT,
    REGION,
    ROUTE_TABLES,
    FakeAws,
    client_error,
    factory,
)
from internal_docs import read_or_skip
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import DERIVED_TYPES, Reason, Scope
from nettopo.observe import (
    ASSOCIATION_EVIDENCE,
    DEFAULT_FLAP_COLLAPSE_THRESHOLD,
    DEFAULT_MAX_CHANGES,
    DEFAULT_RESOLUTION_SECONDS,
    DEFAULT_RETENTION_SECONDS,
    EDGE_RULES,
    FLOW_LOG_ONLY_EDGES,
    METRIC_ONLY_EDGES,
    NO_MEANS_EDGES,
    NO_MEANS_NODES,
    NO_STATE_EDGES,
    NO_STATE_NODES,
    NODE_RULES,
    OBSERVED_RESOURCE_TYPES,
    build_observation,
)
from nettopo.serialize import to_json

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schema" / "observation.schema.json").read_text(encoding="utf-8")
)


def _adr6() -> str:
    """The observation design document, which is internal and absent from the
    public tree. Only the tests that use it record a reason and skip; it is not
    read at import time.
    """
    return read_or_skip("docs/adr/0006-observation-schema.md")


STRUCTURE_AT = "2026-09-23T00:00:00Z"
OBSERVED_AT = "2026-09-23T00:05:00Z"


def _snapshot(resource_types: tuple[str, ...], at: str, **overrides: Any) -> Any:
    scope = Scope(
        account_ids=(ACCOUNT,), regions=(REGION,), resource_types=resource_types
    )
    # The state tier, the observe CLI, runs with the state probes on
    # (`with_state=True` in `cli.py`), while the structure tier runs with them
    # off. Observing with them off leaves RDS, ECS and others reporting a value
    # of None that no rule covers.
    return collect(
        scope,
        factory(FakeAws(**overrides)),
        clock=lambda: at,
        with_state=at != STRUCTURE_AT,
    )


def _observe(**overrides: Any) -> tuple[dict[str, Any], list[str]]:
    """Structure covers all twelve resource types and observation covers ten,
    which is the combination production uses.

    Running the same `FakeAws` twice matches the real order: the structure tier
    runs in CodeBuild and the state tier in Lambda. Running it once and reusing
    the result leaves entries for the types the state tier never calls sitting
    in the snapshot, which hides bugs.
    """
    topology = _snapshot(
        SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, **overrides
    ).to_document()
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT, **overrides)
    return build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )


def _by_id(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["target_id"]: o for o in document["observations"]}


def _one(document: dict[str, Any], needle: str) -> dict[str, Any]:
    """Exactly one observation whose id contains `needle`. With none or two,
    the assertion would be false.
    """
    found = [o for o in document["observations"] if needle in o["target_id"]]
    assert len(found) == 1, (
        f"{needle!r} matched {len(found)} observations; one was expected"
    )
    return found[0]


# ------------------------------------------------------------------- contract


def test_observation_satisfies_the_schema():
    document, _ = _observe()
    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert document["run"]["topology_collected_at"] == STRUCTURE_AT
    assert document["run"]["observed_at"] == OBSERVED_AT
    assert document["layers"] == {"describe": True, "metric": False}


def test_scope_comes_from_the_structure_document():
    """If observation decided the scope for itself the two documents would
    diverge, which is why there is no `--region`.
    """
    document, _ = _observe()
    assert document["scope"] == {
        "account_ids": [ACCOUNT],
        "regions": [REGION],
        # The resource types are the ones observation actually called, ten of
        # them, not the twelve that structure covers. Writing all twelve would
        # leave the security groups and network ACLs it never calls looking
        # permanently like missing cells, and the empty-cell check would fail.
        "resource_types": sorted(OBSERVED_RESOURCE_TYPES),
    }


def test_document_is_byte_identical_across_runs():
    """The same input has to give the same bytes. Without the sorting, dict
    ordering leaks into the output.
    """
    first, _ = _observe()
    second, _ = _observe()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    ids = [o["target_id"] for o in first["observations"]]
    assert ids == sorted(ids)


# ------------------------------- `down`, which a real account never returns


@pytest.mark.parametrize(
    ("needle", "why_contains"),
    [
        ("nat-0fff6666aaaa7777", "failed"),  # a NAT gateway that failed to be created
        ("pcx-0bbb2222cccc2222", "failed"),  # a failed peering, from Status.Code
        ("old-nlb", "failed"),  # a failed load balancer, from State.Code
        (
            "igw-0bbb2222cccc5555",
            "detached",
        ),  # a detached IGW, whose Attachments[] is empty
    ],
)
def test_dead_resources_are_down(needle, why_contains):
    """A path a real account never takes, not once. If it is not measured here
    it is measured nowhere.
    """
    document, _ = _observe()
    observation = _one(document, needle)
    assert observation["status"] == "down", observation
    # With the verdict right but the evidence empty, the screen cannot say why
    # something is red.
    assert why_contains in (observation["why"] or "")
    assert observation["error_count"] == 1
    assert observation["partial"] is False


def test_a_deleting_attachment_is_not_down():
    """A TGW attachment being deleted is not a fault. A resource somebody
    deleted on purpose, left showing a red "broken", reads as an outage.

    The fake account's `deleting` attachment is carried all the way through
    observation, because a unit check on `_judge` alone cannot measure whether
    the key reaches the document through `to_dict`.
    """
    document, _ = _observe()
    observation = _one(document, "tgw-attach-0ccc3333dddd0000")
    assert observation["status"] == "none", observation
    assert observation["deleting"] is True
    assert "'deleting'" in observation["why"] and "삭제 중" in observation["why"]
    assert observation["error_count"] == 0 and observation["partial"] is False
    # The control case: a row that is not being deleted has no such key at all.
    # Writing `false` would give one fact two byte representations.
    # Only two things in the fake account are being deleted, this attachment
    # and the Direct Connect VIF (`fakes.DX_DELETING_VIF`).
    marked = sorted(
        o["target_id"].rsplit("/", 1)[1]
        for o in document["observations"]
        if "deleting" in o
    )
    assert marked == ["dxvif-fg0ddd4444", "tgw-attach-0ccc3333dddd0000"]


def test_blackhole_route_is_down():
    """The state the design document calls the most expensive one in the
    product: looking at the structure alone, it appears connected.

    The blackhole route in `fakes.py` cannot measure this. That route targets an
    ENI, so it has no node, lands in `unresolved_refs`, and no edge is built at
    all, which means there is no observation entry either. So a blackhole whose
    target is alive is built separately here. That a blackhole with no target
    stays invisible is a property of the structure collector and not something
    this file can fix.
    """
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[0]["Routes"].append(
        # The target node exists, a NAT gateway, and only the path is dead.
        # This is the one blackhole shape that still leaves a line on screen.
        {
            "DestinationCidrBlock": "10.99.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "blackhole",
        }
    )
    document, _ = _observe(route_tables=tables)

    routes = [
        o for o in document["observations"] if o["target_id"].startswith("route|")
    ]
    blackholed = [o for o in routes if o["status"] == "down"]
    assert len(routes) > len(blackholed) >= 1, (
        f"{len(routes) - len(blackholed)} live routes and "
        f"{len(blackholed)} blackholed; both are needed for the verdict to "
        f"actually split"
    )
    assert "blackhole" in (blackholed[0]["why"] or "")
    assert blackholed[0]["error_count"] == 1


def test_a_healthy_route_sharing_a_target_with_a_blackhole_is_reported_down():
    """A healthy path and a dead path to the same NAT gateway. There is one
    line, it is `down`, but it names only the destinations that are actually
    dead (2.13.0).

    `services/route_table.py` merges the paths with the same route table and
    target into one edge and ORs their `blackhole` flags, which decides the
    line. Since 2.13.0 that edge carries `blackhole_destinations`, holding only
    the destinations that really are dead, while the table's `routes` carry the
    state of each path.
    If `why` called the healthy `10.98.0.0/16` a blackhole too, somebody would
    delete a working route.
    """
    tables = copy.deepcopy(ROUTE_TABLES)
    tables[0]["Routes"] += [
        {
            "DestinationCidrBlock": "10.98.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "active",
        },
        {
            "DestinationCidrBlock": "10.99.0.0/16",
            "NatGatewayId": EGRESS_NAT,
            "State": "blackhole",
        },
    ]
    document, _ = _observe(route_tables=tables)

    merged = [
        o
        for o in document["observations"]
        if o["target_id"].startswith("route|")
        and EGRESS_NAT in o["target_id"]
        and o["target_id"].split("|")[1].endswith(ROUTE_TABLES[0]["RouteTableId"])
    ]
    assert len(merged) == 1, (
        f"two paths have to merge into one line for this limit to be "
        f"measured; {len(merged)} came out"
    )
    assert merged[0]["status"] == "down"
    why = merged[0]["why"] or ""
    assert "10.99.0.0/16" in why and "10.98.0.0/16" not in why, why


def test_live_resources_are_up_and_carry_no_why():
    document, _ = _observe()
    # Pick by node row. Since 2.7.0 `app-alb` also appears in the id of the
    # load balancer to target line (`load_balancer_target|…app-alb|…`).
    (healthy,) = [
        o
        for o in document["observations"]
        if o["target_id"] == "load_balancer/111122223333/us-east-1/app-alb"
    ]
    assert (healthy["status"], healthy["why"], healthy["error_count"]) == (
        "up",
        None,
        0,
    )
    assert healthy["evidence"]["api"] == "elasticloadbalancing:DescribeLoadBalancers"
    # The metric layer was not turned on, so the metric cells must be empty;
    # S3 fills them in.
    assert healthy["evidence"]["metric"] is None
    assert healthy["evidence"]["datapoints"] is None


# ------------------------------------------------------------- unknown values


def test_unknown_state_is_not_silently_up():
    """A value in neither the up set nor the down set. If it quietly became
    `up`, a fault would be drawn as healthy.
    """
    document, _ = _observe(
        vpcs=[
            {
                "VpcId": "vpc-0aaa1111bbbb2222",
                "CidrBlock": "10.10.0.0/16",
                "State": "quantum-superposition",
                "IsDefault": False,
                "Tags": [],
            }
        ]
    )
    observation = _one(document, "vpc-0aaa1111bbbb2222")
    assert observation["status"] == "none"
    assert observation["partial"] is True
    # Recording the actual value is what tells somebody which rule to add.
    assert "quantum-superposition" in (observation["why"] or "")
    assert observation["error_count"] is None
    # The read succeeded, so the evidence API stays. That is different from
    # having failed to read.
    assert observation["evidence"]["api"] == "ec2:DescribeVpcs"


def test_missing_state_field_is_not_silently_up():
    """A response with no `State` key at all. `_judge` must not read `None` as
    up.
    """
    document, _ = _observe(
        vpcs=[
            {"VpcId": "vpc-0aaa1111bbbb2222", "CidrBlock": "10.10.0.0/16", "Tags": []}
        ]
    )
    observation = _one(document, "vpc-0aaa1111bbbb2222")
    assert observation["status"] == "none"
    assert observation["partial"] is True


def test_state_comparison_is_case_insensitive():
    """The AWS documentation gives the endpoint state as `Available`, with a
    capital letter, but a real account returned it lowercase.

    If either form fell through as an unknown value the whole screen would turn
    into question marks. The service file does not normalize the value, so the
    verdict has to fold the two.
    """
    document, _ = _observe(
        vpc_endpoints=[
            {
                "VpcEndpointId": "vpce-0aaa1111bbbb2222",
                "VpcId": "vpc-0aaa1111bbbb2222",
                "State": "Available",
                "VpcEndpointType": "Interface",
                "ServiceName": "com.amazonaws.us-east-1.s3",
                "SubnetIds": [],
                "Tags": [],
            }
        ]
    )
    assert _one(document, "vpce-0aaa1111bbbb2222")["status"] == "up"


# --------------------------------------------- `none` versus having no state


def test_security_group_and_nacl_get_a_none_entry_without_evidence():
    """`none` means an entry is created and marked with a question mark. It has
    to say that there is no way to observe this.
    """
    document, _ = _observe()
    # Pick with the prefix. Searching for `sg-…` alone also catches the three
    # `security_group_reference` edges that point at that security group, and
    # then it is unclear what is being measured.
    picked = [
        o
        for o in document["observations"]
        if o["target_id"].startswith(("security_group/", "network_acl/"))
    ]
    assert len(picked) == 4, (
        f"two security groups and two network ACLs were expected; got {len(picked)}"
    )
    for observation in picked:
        assert observation["status"] == "none"
        # These can never be observed, which has to stay distinct from there
        # being no rule for them yet (partial).
        assert observation["partial"] is False
        assert observation["evidence"] is None
        assert observation["error_count"] is None
        assert "AWS" in (observation["why"] or "")


def test_no_state_types_have_no_entry_at_all():
    """Having no state means no entry is created at all. Giving `none` instead
    puts a question mark on the screen.
    """
    document, _ = _observe()
    # A node id is `type/…` and an edge id is `type|…`. Splitting on the slash
    # alone means an edge type can never match, which makes the assertion below
    # unable to fail: not a pass but an absence of measurement. It was first
    # written that way.
    kinds = {re.split(r"[/|]", o["target_id"])[0] for o in document["observations"]}
    assert "association" in kinds, (
        "the way edge types are split has to be able to tell them apart"
    )
    # The flow-log-only type, peering, belongs here too: in a run with the
    # reader off it has no row, just like a type with no state.
    # Leaving it out would drop peering from this check, and nobody would catch
    # a regression that builds a peering row without the reader.
    silent = NO_STATE_NODES | NO_STATE_EDGES | FLOW_LOG_ONLY_EDGES
    assert "peering" in silent
    for type_name in silent:
        assert type_name not in kinds, (
            f"{type_name} must not create an observation entry"
        )
    # The same type has to be present in the structure document. Otherwise this
    # says nothing was built when in fact nothing was there.
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    present = {n["type"] for n in topology["nodes"]} | {
        e["type"] for e in topology["edges"]
    }
    # The two derived node types (2.7.0) only appear when a load balancer has
    #    Lambda or external IP targets, which the default fake does not. That
    #    they have no rows is measured in
    #    `test_lb_targets.py::test_derived_nodes_have_no_observation_rows`,
    #    against a document where those nodes do exist.
    # `endpoint_provider` (2.8.0) likewise needs a GWLB endpoint service, and
    #    `test_forwarding.py::test_provider_edges_have_no_observation_rows`
    #    measures it against a document where that edge does exist.
    # The relation types (2.10.0) — APIs, proxies, accelerators and their edges
    #    — are absent from the default fake and live in `rel_world`, so
    #    `test_relations_d.py::test_batch_d_types_have_no_observation_rows`
    #    measures them against a document that has them. Elastic IPs and
    #    `eip_association` are in the default fake.
    batch_d = {
        "api_gateway",
        "rds_db_proxy",
        "global_accelerator",
        "vpc_link",
        "proxy_target",
        "accelerator_endpoint",
        # All three (2.11.0) live only in `rel_world`, and
        # `test_relations_129.py::test_129_types_have_no_observation_rows`
        # measures them.
        "core_network",
        "lattice_service_network",
        "lattice_service",
        "connect_transport",
        "core_network_attachment",
        "lattice_vpc_association",
        "lattice_service_association",
        "lattice_target",
        # These (2.12.0) live only in axis ⑨ of `rel_world`, and
        # `test_waf.py::test_waf_types_have_no_observation_rows` measures them.
        "waf_web_acl",
        "waf_protection",
    }
    assert silent - set(DERIVED_TYPES) - {"endpoint_provider"} - batch_d <= present


def test_nacl_association_is_none_not_up():
    """The network ACL collector writes the constant `"associated"`, which is
    not a value AWS returned.

    Judging on the type alone would leave the network ACL to subnet line
    permanently `up`. The control case is a line of the same `association` type
    that came from a route table, and that one has to be `up`.
    """
    document, _ = _observe()
    associations = [
        o for o in document["observations"] if o["target_id"].startswith("association|")
    ]
    from_nacl = [o for o in associations if "|network_acl/" in o["target_id"]]
    from_route_table = [o for o in associations if "|route_table/" in o["target_id"]]

    assert from_nacl, (
        "the fixture needs a network ACL association for this test to tell "
        "anything apart"
    )
    assert from_route_table, (
        "without the control case, an association from a route table, the "
        "assertion is only half there"
    )
    assert {o["status"] for o in from_nacl} == {"none"}
    # The reasoning, that the collector writes a constant, is carried in the
    # comments of `observe.py`. The reason shown on screen is written for the
    # customer.
    assert all("상태를 제공하지 않습니다" in (o["why"] or "") for o in from_nacl)
    assert {o["status"] for o in from_route_table} == {"up"}


def test_route_without_the_blackhole_key_is_none():
    """If the structure collector changes to stop carrying the key, this
    reports that it does not know rather than papering over it with `up`.
    """
    from nettopo.observe import _observe_route

    observation = _observe_route("route/x", {}).to_dict()
    assert observation["status"] == "none"
    assert observation["partial"] is True


# --------------------------------------- coverage and dangling references


def test_unreadable_region_produces_no_observations_and_a_null_count():
    """For a (account, Region) pair that could not be read, no entry is created
    for its targets. Do not use `none` here.

    Writing `item_count: 0` would give the same bytes as having read it and
    found no targets.
    """
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=OBSERVED_RESOURCE_TYPES,
    )
    denied = FakeAws(
        errors={
            method: client_error("AccessDenied", method, "not authorized")
            for method in (
                "describe_vpcs",
                "describe_subnets",
                "describe_route_tables",
                "describe_internet_gateways",
                "describe_nat_gateways",
                "describe_transit_gateways",
                "describe_transit_gateway_attachments",
                "describe_vpc_peering_connections",
                "describe_vpc_endpoints",
                "describe_load_balancers",
                # The list calls of the ten types that carry a verdict. ECS and
                # EKS share the name `list_clusters`, so one line blocks both.
                "describe_transit_gateway_route_tables",
                "describe_db_instances",
                "list_clusters",
                "list_notebook_instances",
                "list_domains",
                "list_endpoints",
                "describe_instance_connect_endpoints",
                "describe_instances",
                "list_firewalls",
                # The three VPN types (2.6.0).
                "describe_vpn_gateways",
                "describe_customer_gateways",
                "describe_vpn_connections",
                # The three Direct Connect types. The Direct Connect gateway
                # is global but is called per Region, so this Region's cell is
                # the one being denied.
                "describe_connections",
                "describe_virtual_interfaces",
                "describe_direct_connect_gateways",
                # Services that use an IP inside the VPC (2.9.0).
                "describe_cache_clusters",
                "describe_db_clusters",
                "list_domain_names",
                "list_clusters_v2",
            )
        }
    )
    state = collect(scope, factory(denied), clock=lambda: OBSERVED_AT, with_state=True)

    document, dropped = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )

    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert document["observations"] == []
    assert dropped == []
    describe = [c for c in document["coverage"] if c["layer"] == "describe"]
    # There is one cell per resource type. All twenty were denied, so all
    # twenty cells read `denied`, which is distinct from a single collapsed
    # `denied`. Collapsed, nobody could tell which type was blocked.
    assert len(describe) == len(OBSERVED_RESOURCE_TYPES)
    assert {c["status"] for c in describe} == {"denied"}
    assert all(c["item_count"] is None for c in describe)
    assert all(c["reason"]["code"] == "access_denied" for c in describe)


# The check above denies all ten types, so no targets are read at all and the
# branch that says nothing about what could not be read is never taken: an
# assertion that cannot fail is an absence of measurement. The check below
# measures that half, and the defect that only a real account revealed had
# exactly this shape.
PARTIALLY_DENIED = {
    method: client_error("AccessDenied", method, "not authorized")
    for method in (
        "describe_subnets",
        "describe_internet_gateways",
        "describe_nat_gateways",
        "describe_vpc_peering_connections",
        "describe_vpc_endpoints",
        "describe_load_balancers",
    )
}


def test_a_partially_readable_region_keeps_what_it_read_and_says_nothing_about_the_rest():
    """A defect only a real account revealed, where one role allowed four of
    fourteen actions.

    This check was reversed twice in one day. At first, with a gate per
    (account, Region) cell, it expected everything to be discarded including
    what had been read, because coverage cells were keyed only by account,
    Region and tier, leaving nowhere to write "the subnets could not be read
    but the VPC is up". The decision was to record partial completion instead,
    so that work already done need not be redone. The cells gained a
    resource-type axis, and now the types that were read survive while only the
    types that were not stay silent.

    Both directions are measured. Measuring only the suppressing direction
    would let "suppress everything" pass, which measures nothing:
    first, a denied type has no entries for its targets; second, an `ok` type
    does have entries for its targets.
    """
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT, errors=PARTIALLY_DENIED)
    # Measure the premise first: the types that were not denied really were
    # read. If that is false, the second direction below measures "there was
    # nothing to read" rather than "it survived", and so measures nothing.
    read = {n.type for n in state.nodes} - {"account", "region"}
    assert {"vpc", "transit_gateway_attachment"} <= read, read
    assert "subnet" not in read

    document, dropped = build_observation(
        state,
        topology=_snapshot(
            SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT, errors=PARTIALLY_DENIED
        ).to_document(),
        observed_at=OBSERVED_AT,
        tool_version="test",
    )

    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert dropped == []
    kinds = {o["target_id"].split("/")[0] for o in document["observations"]}
    # Second: the types that were read survive, so nobody has to re-check a
    # VPC or TGW that is already confirmed.
    assert {"vpc", "transit_gateway", "transit_gateway_attachment"} <= kinds, kinds
    # First: the denied types are absent. They are not written as `none`
    # either, which would show the same question mark as a type that can never
    # be observed.
    assert not kinds & {"subnet", "nat_gateway", "internet_gateway", "vpc_endpoint"}

    # A route edge has to survive even where the NAT gateway was denied. That
    # line comes from the route table response, so judging it by the type of
    # its endpoints gives the wrong answer.
    assert _one(document, "route|route_table/")["status"] == "up"

    describe = {
        c["resource_type"]: (c["status"], c["item_count"])
        for c in document["coverage"]
        if c["layer"] == "describe"
    }
    # Partially read means an `ok` row and a failed row sitting in the same
    # place. It is not a single status value.
    assert describe["vpc"] == ("ok", 2)
    assert describe["subnet"] == ("denied", None)
    assert {s for s, _ in describe.values()} == {"ok", "denied"}


def test_a_denied_type_claims_nothing_even_with_the_response_in_hand():
    """The control case for the suppressing direction. In the check above, a
    denied type is absent from the snapshot to begin with.

    When a type is denied it has no targets either, so "there are no entries"
    passes through missing material rather than through suppression, and
    measures nothing. So here the targets are left in place and only the
    coverage is turned to `denied`: while the contract says that cell could not
    be read, the code must not claim a state for that type. That
    self-contradiction is what `claims_without_reading` in
    `schema/tests/test_observation_schema.py` catches.
    """
    import dataclasses

    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    assert any(n.type == "vpc" for n in state.nodes), (
        "premise: the VPC response is in hand"
    )
    state = dataclasses.replace(
        state,
        coverage=tuple(
            dataclasses.replace(
                c, status="denied", item_count=None, reason=c.reason or None
            )
            if c.resource_type == "vpc" and c.region is not None
            else c
            for c in state.coverage
        ),
    )

    document, _ = build_observation(
        state,
        topology=_snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document(),
        observed_at=OBSERVED_AT,
        tool_version="test",
    )

    kinds = {o["target_id"].split("/")[0] for o in document["observations"]}
    assert "vpc" not in kinds
    # Control case: the other types survive, so the gate did not kill
    # everything.
    assert "transit_gateway" in kinds


def test_a_denied_route_table_silences_the_edges_it_would_have_made():
    """Until this check existed, the gate on the edge side was unmeasured; the
    break harness found that.

    Loosening `if not readable(edge.id)` in `observe.py` to `and False` made not
    a single engine check fail, so that gate was not passing but unmeasured.
    The two checks above miss it for different reasons. The partially-denied
    case has no targets for the denied type to begin with, and therefore no
    edges. The case that denies one type, `vpc`, never reaches it, because a
    route edge has the type `route_table` rather than `vpc`.

    So here only `route_table`, the response that built that line, is turned to
    denied. The endpoints, the NAT gateway, IGW and TGW, were all read and the
    route attributes are in hand, but whether the route still looks that way is
    unknown, so nothing is claimed. `association` is checked alongside it: an
    association built by a route table disappears, while one built by a network
    ACL stays as `none`. An edge takes the type of the response that built it.
    """
    import dataclasses

    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    # Premise: the routes are in hand. If that is false, what follows measures
    # missing material rather than suppression.
    assert any(e.type == "route" for e in state.edges), (
        "premise: the route response is in hand"
    )
    state = dataclasses.replace(
        state,
        coverage=tuple(
            dataclasses.replace(
                c,
                status="denied",
                item_count=None,
                reason=Reason("access_denied", "DescribeRouteTables", "test"),
            )
            if c.resource_type == "route_table" and c.region is not None
            else c
            for c in state.coverage
        ),
    )

    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    document, dropped = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )

    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert dropped == []
    node_type = {n["id"]: n["type"] for n in topology["nodes"]}
    from_route_table = {
        e["id"]
        for e in topology["edges"]
        if e["type"] in ("route", "association")
        and node_type.get(e["source"]) == "route_table"
    }
    assert {e.split("|")[0] for e in from_route_table} == {"route", "association"}
    assert not from_route_table & set(_by_id(document))
    # Control case: a `route` line built by a TGW route table came from that
    # response, so it survives. Keying the cell by the edge
    # While the cell was keyed by type, mapping `route` to `route_table`, this
    # died along with it, which discarded what had been read.
    tgw_routes = {
        e["id"]
        for e in topology["edges"]
        if e["type"] == "route"
        and node_type.get(e["source"]) == "transit_gateway_route_table"
    }
    assert tgw_routes and tgw_routes <= set(_by_id(document))
    assert {_by_id(document)[e]["status"] for e in tgw_routes} == {"up"}

    # First control case: the node states in the same place survive, so the
    # gate did not kill the whole place.
    kinds = {o["target_id"].split("/")[0] for o in document["observations"]}
    assert {"vpc", "subnet", "nat_gateway", "internet_gateway"} <= kinds, kinds
    # Second control case: an association built by a network ACL stays `none`.
    # That verdict has nothing to do with permissions, so a denial leaves it
    # alone.
    nacl_assoc = {
        e["id"]
        for e in topology["edges"]
        if e["type"] == "association" and node_type.get(e["source"]) == "network_acl"
    }
    by_id = _by_id(document)
    assert nacl_assoc and nacl_assoc <= set(by_id)
    assert {by_id[e]["status"] for e in nacl_assoc} == {"none"}


def test_metric_layer_is_skipped_not_denied():
    """The metric layer is off by default. That is not blocked, it is simply
    not turned on.
    """
    document, _ = _observe()
    metric = [c for c in document["coverage"] if c["layer"] == "metric"]
    assert metric and all(c["status"] == "skipped" for c in metric)
    assert all(c["item_count"] is None and c["reason"] is None for c in metric)


def test_describe_coverage_counts_only_what_it_emitted():
    """The sum does not equal the number of observations, and that difference
    is a fact of the contract.

    `item_count` is the number of targets judged in one (account, Region, type,
    tier) cell, but the `none` entries of the types that are never called, the
    security groups, network ACLs, security group references and network ACL
    associations, belong to no cell at all. So the sum of `item_count` plus the
    entries outside any cell equals the number of observations. The difference
    is counted from the structure document rather than hard-coded.
    """
    document, _ = _observe()
    describe = [c for c in document["coverage"] if c["layer"] == "describe"]
    assert len(describe) == len(OBSERVED_RESOURCE_TYPES)
    assert {c["status"] for c in describe} == {"ok"}

    unobserved = set(SUPPORTED_RESOURCE_TYPES) - set(OBSERVED_RESOURCE_TYPES)
    # The three Direct Connect types gained structure-tier rules and left this
    # list. The Direct Connect gateway follows the global-placement rule.
    assert unobserved == {
        "security_group",
        "network_acl",
        # An Auto Scaling group has nothing to judge, being a logical grouping,
        # so it is an entry outside any cell.
        "auto_scaling_group",
        # CloudFront is here for a different reason. It is not that there are
        # no metrics, but that `AWS/CloudFront` exists only in us-east-1. Adding
        # it before the Region axis is pinned down would leave every
        # distribution showing as unknown for a customer who does not look at
        # us-east-1.
        "cloudfront",
        # The three second-round collectors (EC2, EKS, Network Firewall) and
        # the seven others gained verdicts and left this list.
        # A Lambda function does not call a state API of its own
        # (`NO_STATE_NODES`). The health of the load balancer to function line
        # is carried by the line.
        "lambda_function",
        # A Route 53 hosted zone has no state either (`NO_STATE_NODES`).
        "route53_hosted_zone",
        # All five relation types have no state (`NO_STATE_NODES`). The node of
        # `api_gateway_v2` is an `api_gateway`.
        "elastic_ip",
        "api_gateway",
        "api_gateway_v2",
        "rds_db_proxy",
        "global_accelerator",
        # All three of these have no state (`NO_STATE_NODES`).
        "core_network",
        "lattice_service_network",
        "lattice_service",
        # No state (`NO_STATE_NODES`). The two collected types share one card.
        "waf_web_acl",
        "waf_web_acl_global",
    }, unobserved
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    node_type = {n["id"]: n["type"] for n in topology["nodes"]}
    offgrid = {n["id"] for n in topology["nodes"] if n["type"] in unobserved}
    offgrid |= {
        e["id"] for e in topology["edges"] if node_type.get(e["source"]) in unobserved
    }
    priced = [o for o in document["observations"] if o["target_id"] not in offgrid]

    assert sum(c["item_count"] for c in describe) == len(priced)
    # Control case: with no entries outside a cell, the equation above collapses
    # to sum equals total and this check measures nothing.
    assert 0 < len(priced) < len(document["observations"])


def test_a_target_missing_from_the_structure_document_is_dropped():
    """A dangling reference is a picture with a state attached to a resource
    that no longer exists. Letting one through quietly breaks the renderer.
    """
    topology = _snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT).to_document()
    victim = next(n["id"] for n in topology["nodes"] if n["type"] == "nat_gateway")
    topology["nodes"] = [n for n in topology["nodes"] if n["id"] != victim]
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)

    document, dropped = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )

    assert victim in dropped
    assert victim not in _by_id(document)


# ------------------------------------------- the table against the code


def _o7_rows(block_title: str) -> dict[str, str]:
    """Maps the type in the first column of the design table to its last
    column, the widest vocabulary it can produce.

    The design document declares that table to be the source of truth. What is
    read here is the production half of that declaration: add a row without
    adding a rule in the code and that type silently gets no state.
    """
    section = _adr6().split("### O7 — ")[1].split("\n### ")[0]
    _, _, after = section.partition(f"**{block_title}**")
    # The node block runs all the way to the edge heading, because further
    # tables sit after their own bold headings. While this stopped at the first
    # heading those twelve rows were left out of the comparison, and rows that
    # claimed `live`, `up` and `down` with no rule behind them, such as EC2,
    # passed quietly. The parser on the schema side read all of it from the
    # start.
    block = (
        after.split("**엣지**")[0] if block_title == "노드" else after.split("\n**")[0]
    )

    rows: dict[str, str] = {}
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or all(set(c) <= {"-", ":"} for c in cells):
            continue
        # A row name can carry a qualifier, as in `association←network_acl`.
        # Failing to read the qualifier drops that row entirely, and the
        # comparison below quietly shrinks instead of naming a type that exists
        # only in the code.
        # While `0-9` was missing from this pattern the `ec2_instance` and
        # `ec2_instance_connect_endpoint` rows went unread, just as quietly. The
        # parser on the schema side had the same defect.
        types = re.findall(r"`([a-z0-9_]+(?:←[a-z0-9_]+)?)`", cells[0])
        if not types or "node_type" in types or "edge_type" in types:
            continue  # the header row
        for type_name in types:
            rows[type_name] = cells[-1]
    assert rows, (
        f"no rows could be read from the **{block_title}** table, which leaves "
        f"the assertions powerless"
    )
    return rows


def _classify(vocabulary: str) -> str:
    # The metric-layer-only case comes first. The structure tier gives these
    # rows no evidence, but they are not stateless: when the metric layer
    # measures that span, a row appears. Reversing the order merges the two and
    # sends the comparison below back to the old rule that forbade attaching the
    # metric layer to an edge.
    if "2층 전용" in vocabulary:
        return "metric_only"
    # The flow-log-only case also comes before the stateless one, for the same
    # reason.
    if "흐름 로그 전용" in vocabulary:
        return "flow_log_only"
    if "상태 없음" in vocabulary:
        return "no_state"
    if "`none`" in vocabulary:
        return "no_means"
    return "rule"


def test_o7_parser_reads_the_last_column():
    """Does the parser tell them apart: do the classes really separate in the
    table? If they merged into one, everything below would be powerless.
    """
    nodes = _o7_rows("노드")
    assert _classify(nodes["vpc"]) == "rule"
    assert _classify(nodes["route_table"]) == "no_state"
    assert _classify(nodes["security_group"]) == "no_means"
    assert len({_classify(v) for v in nodes.values()}) == 3

    # The fourth and fifth classes appear only in the edge table, so they are
    # measured separately from the three that separate in the node table.
    # Peering moved to flow-log-only, and `origin` refills the stateless row,
    # which makes five classes.
    edges = _o7_rows("엣지")
    assert _classify(edges["attachment"]) == "metric_only"
    assert _classify(edges["peering"]) == "flow_log_only"
    assert _classify(edges["security_group_reference"]) == "no_means"
    assert _classify(edges["origin"]) == "no_state"
    assert {_classify(v) for v in edges.values()} == {
        "rule",
        "no_state",
        "no_means",
        "metric_only",
        "flow_log_only",
    }


def test_node_rules_match_the_o7_table():
    documented = _o7_rows("노드")
    by_class: dict[str, set[str]] = {
        "rule": set(),
        "no_state": set(),
        "no_means": set(),
    }
    for type_name, vocabulary in documented.items():
        by_class[_classify(vocabulary)].add(type_name)

    assert by_class["rule"] == set(NODE_RULES), (
        "the types the node table says can produce a vocabulary differ from "
        "`NODE_RULES`: "
        f"table only: {sorted(by_class['rule'] - set(NODE_RULES))}; "
        f"code only: {sorted(set(NODE_RULES) - by_class['rule'])}"
    )
    assert by_class["no_means"] == set(NO_MEANS_NODES)
    assert by_class["no_state"] == set(NO_STATE_NODES)


def test_edge_rules_match_the_o7_table():
    """Rows with a qualifier are left out of the classes here, and the paired
    test below measures them instead.

    `association` has two rows in the table: `←route_table` carries a verdict
    and `←network_acl` is `none`. One edge type belongs to two classes at once,
    so mixing it into a comparison that looks only at type names makes either
    answer false. What the code branches on is not the type but the kind of the
    source node.
    (`ASSOCIATION_EVIDENCE`, `observe.py:142`).
    """
    documented = _o7_rows("엣지")
    by_class: dict[str, set[str]] = {
        "rule": set(),
        "no_state": set(),
        "no_means": set(),
        "metric_only": set(),
        "flow_log_only": set(),
    }
    for type_name, vocabulary in documented.items():
        if "←" in type_name:
            continue
        by_class[_classify(vocabulary)].add(type_name)

    # `route` is carried by `_observe_route()` rather than a `_Rule`, because
    # its value is a boolean. That is the exception to the first rule.
    # `association` has nothing but qualifier rows, so it drops out here and the
    # paired test below carries it.
    # `load_balancer_target` is likewise carried by `_observe_lb_target()`
    # rather than a `_Rule`, because its value is a count of registered health.
    assert by_class["rule"] == (set(EDGE_RULES) - {"association"}) | {
        "route",
        "load_balancer_target",
    }
    assert by_class["no_means"] == set(NO_MEANS_EDGES)
    assert by_class["no_state"] == set(NO_STATE_EDGES)
    # For these three types the row is built by the metric layer, from span
    # metrics, rather than by the structure tier.
    # Turning one line in the table back to stateless would leave the code
    # still building a metric-layer row, and this assertion is the only thing
    # that catches the mismatch. What somebody would see: an attachment line
    # that was measured but does not appear on screen.
    assert by_class["metric_only"] == set(METRIC_ONLY_EDGES)
    # For peering the row is built from flow logs. Turning the table back to
    # stateless, or emptying the set in the code, changes only one of the two,
    # and this assertion is the only thing that catches the mismatch.
    assert by_class["flow_log_only"] == set(FLOW_LOG_ONLY_EDGES)


def test_association_rows_match_the_evidence_map():
    """The two `association` rows in the table against the two entries in
    `ASSOCIATION_EVIDENCE`.

    Since qualifier rows were introduced the comparison above skips
    `association`, so without this check you could delete one of the two rows,
    or drop one collector from `ASSOCIATION_EVIDENCE`, and nothing would turn
    red.

    What somebody would see: the network ACL to subnet line permanently green,
    or the route table to subnet line permanently a question mark. From the
    screen alone, neither can be told apart from a dead verdict.
    """
    documented = _o7_rows("엣지")
    rows = {
        name.split("←")[1]: _classify(vocabulary)
        for name, vocabulary in documented.items()
        if name.startswith("association←")
    }
    assert rows == {"route_table": "rule", "network_acl": "no_means"}, rows
    assert {source: kind == "rule" for source, kind in rows.items()} == (
        ASSOCIATION_EVIDENCE
    ), f"table {rows} != code {ASSOCIATION_EVIDENCE}"
    assert "association" in EDGE_RULES, (
        "the code has no rule for the side the table allows a verdict on, the "
        "one from a route table"
    )


def test_association_evidence_covers_every_collector_that_makes_one():
    """When another service file starts building `association` edges, whether
    it carries evidence has to be registered.

    Without that, `_association_evidence()` returns `None` and observation
    reports a defect. It does not quietly become `up`, but released in that
    state the whole screen is question marks.
    """
    sources = set()
    for path in (ROOT / "engine" / "src" / "nettopo" / "services").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"association"' in text:
            sources.add(path.stem)
    assert sources, (
        "no file that builds association edges could be found, which leaves the "
        "assertions powerless"
    )
    assert sources == set(ASSOCIATION_EVIDENCE), (
        f"collectors that build association edges {sorted(sources)} != "
        f"registered evidence {sorted(ASSOCIATION_EVIDENCE)}"
    )


def test_observed_types_exclude_the_ones_with_nothing_to_read():
    """Security groups and network ACLs can only ever produce `none`, so there
    is no reason to call AWS for them at all.
    """
    assert set(OBSERVED_RESOURCE_TYPES) <= set(SUPPORTED_RESOURCE_TYPES)
    assert set(NO_MEANS_NODES) & set(OBSERVED_RESOURCE_TYPES) == set()
    # The route table has to be called even though it has no state, because
    # the route and association edges come only from that response.
    assert "route_table" in OBSERVED_RESOURCE_TYPES


def test_history_defaults_match_the_adr():
    """The table of approved defaults is the source of truth. Changing only the
    code makes it diverge from the fixtures and the schema tests.
    """
    section = _adr6().split("## 승인된 기본값")[1].split("\n## ")[0]
    numbers = {
        int(n.replace(",", "")) for n in re.findall(r"\*\*([0-9][0-9,]*)\*\*", section)
    } | {int(n.replace(",", "")) for n in re.findall(r"`([0-9][0-9,]*)`", section)}
    assert numbers, (
        "no numbers could be read from the table of approved defaults, which "
        "leaves the assertions powerless"
    )
    for value in (
        DEFAULT_RESOLUTION_SECONDS,
        DEFAULT_RETENTION_SECONDS,
        DEFAULT_FLAP_COLLAPSE_THRESHOLD,
        DEFAULT_MAX_CHANGES,
    ):
        assert value in numbers, f"{value} is not in the table of approved defaults"


def test_history_block_uses_those_defaults():
    document, _ = _observe()
    assert document["history"] == {
        "baseline_at": OBSERVED_AT,
        "resolution_seconds": DEFAULT_RESOLUTION_SECONDS,
        "retention_seconds": DEFAULT_RETENTION_SECONDS,
        "truncated": False,
        "flap_collapse_threshold": DEFAULT_FLAP_COLLAPSE_THRESHOLD,
        "max_changes": DEFAULT_MAX_CHANGES,
    }
    # An empty array does not mean nothing changed. It means this observation
    # is the baseline, so there is nothing to compare against.
    assert document["changes"] == []


def test_the_pair_carries_the_structure_fingerprint_when_the_structure_has_one():
    """When the structure document carries a digest, the observation document
    copies it across unchanged. The screen pairs the two by that value.
    """
    topology = json.loads(to_json(_snapshot(SUPPORTED_RESOURCE_TYPES, STRUCTURE_AT)))
    state = _snapshot(OBSERVED_RESOURCE_TYPES, OBSERVED_AT)
    document, _ = build_observation(
        state, topology=topology, observed_at=OBSERVED_AT, tool_version="test"
    )
    jsonschema.Draft202012Validator(SCHEMA).validate(document)
    assert (
        document["run"]["topology_content_digest"] == topology["run"]["content_digest"]
    )


def test_a_structure_without_a_fingerprint_leaves_the_key_out():
    """An older structure document has no digest, so the key is absent
    entirely. Writing `null` would let the screen read both sides as null and
    conclude they match.
    """
    document, _ = _observe()
    assert "topology_content_digest" not in document["run"]


# ---- A transitional state is `down`, not unobservable -----------------------
# Being deleted is the exception, and `test_deleting_state.py` covers it.
# What a real account showed: a TGW peering awaiting acceptance
# (`pendingAcceptance`) appeared in the history as unobservable turning
# healthy, and an endpoint being deleted appeared as healthy turning
# unobservable. `_judge` drops any value not in its list to `none`, so the
# transitional states have to be put in the rules.
TRANSITIONAL_DOWN = [
    ("vpc", "pending"),
    ("subnet", "pending"),
    ("subnet", "unavailable"),
    ("internet_gateway", "attaching"),
    ("internet_gateway", "detaching"),
    ("nat_gateway", "pending"),
    ("transit_gateway", "pending"),
    ("transit_gateway_attachment", "pendingAcceptance"),
    ("transit_gateway_attachment", "initiatingRequest"),
    ("transit_gateway_attachment", "initiating"),
    ("transit_gateway_attachment", "pending"),
    ("transit_gateway_attachment", "rollingBack"),
    ("vpc_peering_connection", "pending-acceptance"),
    ("vpc_peering_connection", "initiating-request"),
    ("vpc_peering_connection", "provisioning"),
    ("vpc_endpoint", "pendingAcceptance"),
    ("vpc_endpoint", "pending"),
    ("vpc_endpoint", "partial"),
    ("load_balancer", "provisioning"),
]


@pytest.mark.parametrize("node_type,value", TRANSITIONAL_DOWN)
def test_a_transitional_state_is_down_not_unknown(node_type, value):
    from nettopo.observe import NODE_RULES, _judge

    got = _judge(
        NODE_RULES[node_type], value, f"{node_type}/111122223333/us-east-1/x", "node"
    )
    assert got.status == "down", (
        f"{node_type}={value} gave {got.status}; dropping to unobservable "
        f"shows up in the history as unobservable turning healthy"
    )
    assert value in (
        got.why or ""
    )  # name the AWS value verbatim, so it can be found in the console


@pytest.mark.parametrize(
    "node_type,value",
    [("transit_gateway", "modifying"), ("transit_gateway_attachment", "modifying")],
)
def test_modifying_still_carries_traffic(node_type, value):
    from nettopo.observe import NODE_RULES, _judge

    assert (
        _judge(
            NODE_RULES[node_type],
            value,
            f"{node_type}/111122223333/us-east-1/x",
            "node",
        ).status
        == "up"
    )


def test_an_impaired_load_balancer_is_up_and_says_so():
    from nettopo.observe import NODE_RULES, _judge

    got = _judge(
        NODE_RULES["load_balancer"],
        "active_impaired",
        "load_balancer/111122223333/us-east-1/x",
        "node",
    )
    assert got.status == "up" and "손상" in (got.why or "")


# ---- The register of the reason field on screen -----------------------------
# The `why` of an observation row appears verbatim in the reason field of the
# detail panel and on the virtual interface rows. The register on screen is the
# polite form the Korean AWS console uses, so a single clause ending in the
# plain form fails this check. The renderer enforces the same discipline in
# `renderer/verify-aws.mjs`. A clause is a piece split on an em dash, a period,
# a middle dot or a newline, and a trailing parenthesis is examined both inside
# and out.
_OLD_TONE_OK = ("캐나다",)


def _old_tone_clauses(text: str) -> list[str]:
    def polite(c: str) -> bool:
        if c.endswith("습니다"):
            return True
        # The polite endings have a final consonant before the last two
        # syllables, which the plain negative form does not.
        return (
            c.endswith("니다")
            and len(c) >= 3
            and "가" <= c[-3] <= "힣"
            and (ord(c[-3]) - 0xAC00) % 28 == 17
        )

    bad = []
    for clause in re.split(r" — |\. | · |\n", text):
        cands = [clause.strip()]
        while cands[-1].endswith(")") and "(" in cands[-1]:
            head, _, inner = cands[-1][:-1].rpartition("(")
            cands += [inner.strip(), head.strip()]
        for c in cands:
            c = c.rstrip(".!? ")
            if c.endswith("다") and not polite(c) and not c.endswith(_OLD_TONE_OK):
                bad.append(clause.strip())
                break
    return bad


def test_the_old_tone_detector_catches_what_it_should():
    """The control case for the checker itself: one thing it has to catch and
    one it has to let through.
    """
    assert _old_tone_clauses("대상이 없다")
    assert _old_tone_clauses("판정하지 않습니다(이력에 남는다)")
    assert _old_tone_clauses("관측 불가 — 이 칸은 「없음」이 아니다")
    assert _old_tone_clauses("세지 못했다(0 이 아닙니다)")
    assert not _old_tone_clauses(
        "삭제 중입니다(삭제가 끝나면 이력에 「없어짐」으로 남습니다)"
    )
    assert not _old_tone_clauses("대상 2개 중 정상 0 — AWS 가 비정상 2개로 보고")
    assert not _old_tone_clauses("태스크가 0개입니다(원하는 수 > 0)")
    assert not _old_tone_clauses("캐나다")


def _every_screen_why() -> dict[str, str]:
    """Every sentence the engine can produce as the `why` of an observation
    row. Sentences that interpolate a value are built with a representative one.
    """
    from nettopo import metrics as _metrics
    from nettopo import observe as _observe

    out: dict[str, str] = {}
    for key, why in _observe._FOLDED_WHY.items():
        out[f"접힌 값 {key}"] = why
    for key, why in {**_observe.NO_MEANS_NODES, **_observe.NO_MEANS_EDGES}.items():
        out[f"관측 수단 없음 {key}"] = why
    out["NACL 연결"] = _observe.NO_MEANS_NACL_ASSOCIATION
    for node_type, rule in sorted(_observe.NODE_RULES.items()):
        for value in sorted(rule.deleting):
            got = _observe._judge(rule, value, f"{node_type}/x", "node")
            out[f"삭제 중 {node_type}={value}"] = got.why or ""
        unknown = _observe._judge(rule, "__no_such_value__", f"{node_type}/x", "node")
        out[f"규칙 없음 {node_type}"] = unknown.why or ""
    blackhole = {"blackhole": True, "destinations": ["10.0.0.0/16"]}
    out["경로 blackhole"] = _observe._observe_route("e", blackhole).why or ""
    out["경로 blackhole 키 없음"] = _observe._observe_route("e", {}).why or ""
    out["타입 규칙 없음"] = _observe._no_rule("x", "node", "made_up").why or ""
    for reporting in (_metrics.REPORT_ALWAYS, _metrics.REPORT_NONZERO_ONLY):
        reading = _metrics.Reading("BytesOut", "flow", "Sum", 0, 0, None, reporting)
        out[f"조용한 지표 {reporting}"] = _observe._quiet_why(reading) or ""
    verdict = _metrics.Verdict(
        metric_names=("BytesIn",),
        flow_names=(),
        flow_metric=None,
        flow_value=0.0,
        flow_points=0,
        drop_metric=None,
        drop_value=0.0,
        block_drop_metric=None,
        block_drop_value=0.0,
        blocked_metric=None,
        measured=True,
        arrival_metric="BytesIn",
        arrival_value=5.0,
    )
    out["들어옴만"] = verdict.arrival_why or ""
    # Every `why` of an edge row. The reason field of the line's detail panel
    # shows the customer-facing wording, and this text stays in that field's
    # title attribute.
    for key, why in _edge_whys().items():
        out[f"선 {key}"] = why
    return out


def _edge_whys() -> dict[str, str]:
    """Every sentence the engine can produce as the `why` of an edge
    observation row, which is the text behind the reason field of a line's
    detail panel.
    """
    from nettopo import metrics as _metrics
    from nettopo import observe as _observe

    out: dict[str, str] = {}
    blackhole = {"blackhole": True, "destinations": ["10.0.0.0/16"]}
    out["경로 blackhole"] = _observe._observe_route("e", blackhole).why or ""
    out["경로 blackhole 키 없음"] = _observe._observe_route("e", {}).why or ""
    rule = _observe.EDGE_RULES["association"]
    for value in sorted(rule.down):
        out[f"연관 {value}"] = _observe._judge(rule, value, "e", "edge").why or ""
    out["연관 규칙 없음"] = _observe._judge(rule, "__no__", "e", "edge").why or ""
    out["엣지 타입 규칙 없음"] = _observe._no_rule("e", "edge", "made_up").why or ""
    for key, why in _observe.NO_MEANS_EDGES.items():
        out[f"관측 수단 없음 {key}"] = why
    out["NACL 연결"] = _observe.NO_MEANS_NACL_ASSOCIATION
    lbt = _observe._observe_lb_target(
        "e",
        {"target_count": 2, "healthy_target_count": 0, "unhealthy_target_count": 2},
        None,
    )
    out["LB 대상 전부 비정상"] = lbt.why or ""
    out["2층 응답 누락"] = _observe._MISSING_RESULT
    probe = _metrics.EDGE_PROBES["attachment"]
    out["2층 차원 못 만듦"] = _metrics._unbuildable_why(probe)
    base = dict(
        metric_names=("BytesIn",),
        flow_names=("BytesOut",),
        flow_metric="BytesOut",
        flow_value=0.0,
        flow_points=0,
        drop_metric="PacketDropCountBlackhole",
        drop_value=3.0,
        block_drop_metric="PacketDropCountBlackhole",
        block_drop_value=3.0,
        blocked_metric=None,
        measured=True,
        arrival_metric="BytesIn",
        arrival_value=5.0,
    )
    out["차단 드롭"] = _metrics.Verdict(**base).block_reason or ""
    state = {**base, "blocked_metric": "TunnelState", "block_drop_value": 0.0}
    out["차단 상태 지표"] = _metrics.Verdict(**state).block_reason or ""
    return out


def test_every_why_the_panel_shows_is_in_console_tone():
    """The reason field shows the engine's sentence verbatim, so if the engine
    writes in the plain form, one field on screen is left in the wrong
    register.
    """
    whys = _every_screen_why()
    assert any(k.startswith("삭제 중") for k in whys) and len(whys) > 20, sorted(whys)
    assert all(whys.values()), [k for k, v in whys.items() if not v]
    bad = {k: _old_tone_clauses(v) for k, v in whys.items() if _old_tone_clauses(v)}
    assert not bad, bad
    assert not [k for k, v in whys.items() if "**" in v], (
        "a sentence shown on screen carries markdown emphasis"
    )
