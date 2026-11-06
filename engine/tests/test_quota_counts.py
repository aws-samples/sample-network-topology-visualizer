"""한도 단위의 개수(#27 사용률 · ADR 0011 D3) — SG 규칙 · NACL 항목 · 라우트 · 서브넷 남은 IP.

기존 `*_rule_count` · `entry_count` · `route_count` 는 **한도와 단위가 다르다**(묶음 수 · 기본 규칙 포함 · 계열 섞음).
여기서 재는 것은 「AWS 가 한도에 대어 세는 방식」으로 다시 센 수다. 각 검사는 기존 수와 **값이 달라지는** 입력을 쓴다 —
같아지는 입력이면 새 수를 기존 수로 되돌려도 통과한다(미측정).
"""

from __future__ import annotations

from typing import Any

from fakes import ACCOUNT, PUBLIC_SUBNET, REGION, FakeAws, factory
from nettopo.collect import SUPPORTED_RESOURCE_TYPES, collect
from nettopo.model import Scope
from nettopo.services import network_acl as nacl_module
from nettopo.services import route_table as rt_module
from nettopo.services import security_group as sg_module
from nettopo.services.base import Ctx

CTX = Ctx(
    account_id=ACCOUNT,
    region=REGION,
    scope_accounts=frozenset({ACCOUNT}),
    scope_regions=frozenset({REGION}),
)
AT = "2026-01-02T03:04:05Z"


def _attrs(module: Any, item: dict[str, Any]) -> dict[str, Any]:
    return module.build(item, CTX).node.attributes


# ------------------------------------------------------------------ 보안 그룹


def _sg(
    ingress: list[dict[str, Any]], egress: list[dict[str, Any]] | None = None
) -> dict:
    return {
        "GroupId": "sg-0aaa1111bbbb2222c",
        "GroupName": "app",
        "VpcId": "vpc-0aaa1111",
        "OwnerId": ACCOUNT,
        "IpPermissions": ingress,
        "IpPermissionsEgress": egress or [],
    }


def test_sg_counts_each_cidr_not_each_permission() -> None:
    """묶음 1개에 CIDR 3개 = 규칙 3(문서) — 묶음 수(`ingress_rule_count`)는 1이다."""
    a = _attrs(
        sg_module,
        _sg(
            [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 443,
                    "ToPort": 443,
                    "IpRanges": [
                        {"CidrIp": "10.0.0.0/8"},
                        {"CidrIp": "172.16.0.0/12"},
                        {"CidrIp": "192.168.0.0/16"},
                    ],
                }
            ]
        ),
    )
    assert a["ingress_rule_count"] == 1
    assert a["ingress_ipv4_rule_count"] == 3
    assert a["ingress_ipv6_rule_count"] == 0
    assert a["egress_ipv4_rule_count"] == 0


def test_sg_splits_families_and_counts_references_in_both() -> None:
    """IPv4 · IPv6 는 따로 센다 · 참조 SG 는 계열을 말하지 않으므로 둘 다에 1(적게 말하지 않는다)."""
    a = _attrs(
        sg_module,
        _sg(
            [
                {
                    "IpProtocol": "-1",
                    "IpRanges": [{"CidrIp": "10.0.0.0/8"}],
                    "Ipv6Ranges": [
                        {"CidrIpv6": "2001:db8::/32"},
                        {"CidrIpv6": "2001:db8:1::/48"},
                    ],
                    "UserIdGroupPairs": [
                        {"GroupId": "sg-0ccc3333dddd4444e", "UserId": ACCOUNT}
                    ],
                }
            ],
            [
                {
                    "IpProtocol": "-1",
                    "PrefixListIds": [
                        {"PrefixListId": "pl-0aaa1111"},
                        {"PrefixListId": "pl-0bbb2222"},
                    ],
                }
            ],
        ),
    )
    assert (a["ingress_ipv4_rule_count"], a["ingress_ipv6_rule_count"]) == (2, 3)
    assert a["ingress_prefix_list_rule_count"] == 0
    # 접두사 목록은 계열 칸에 넣지 않는다 — 목록의 최대 항목 수만큼 세야 하는데 그 수를 모른다(ADR 0011 D3).
    assert (a["egress_ipv4_rule_count"], a["egress_ipv6_rule_count"]) == (0, 0)
    assert a["egress_prefix_list_rule_count"] == 2


# ------------------------------------------------------------------ NACL


def _entry(number: int, *, egress: bool, ipv6: bool = False) -> dict[str, Any]:
    key = "Ipv6CidrBlock" if ipv6 else "CidrBlock"
    return {
        "RuleNumber": number,
        "Egress": egress,
        "RuleAction": "allow",
        "Protocol": "-1",
        key: "::/0" if ipv6 else "0.0.0.0/0",
    }


def test_nacl_counts_per_direction_without_the_default_rules() -> None:
    """기본 규칙(32767 · IPv4 · IPv6 각각)은 빼고 방향마다 센다 — `entry_count` 는 전부의 합이다."""
    a = _attrs(
        nacl_module,
        {
            "NetworkAclId": "acl-0aaa1111",
            "VpcId": "vpc-0aaa1111",
            "OwnerId": ACCOUNT,
            "IsDefault": False,
            "Associations": [],
            "Entries": [
                _entry(100, egress=False),
                _entry(110, egress=False),
                _entry(120, egress=False, ipv6=True),
                _entry(32767, egress=False),
                _entry(32767, egress=False, ipv6=True),
                _entry(100, egress=True),
                _entry(32767, egress=True),
                _entry(32767, egress=True, ipv6=True),
            ],
        },
    )
    assert a["entry_count"] == 8
    assert (a["ingress_entry_count"], a["egress_entry_count"]) == (3, 1)


# ------------------------------------------------------------------ 라우트


def test_route_table_counts_families_and_propagated_routes_apart() -> None:
    """IPv4(+접두사 목록 1개 = 1) · IPv6 · 전파는 한도가 따로다 — `route_count` 는 셋을 섞는다."""
    routes = [
        {
            "DestinationCidrBlock": "10.0.0.0/16",
            "GatewayId": "local",
            "Origin": "CreateRouteTable",
            "State": "active",
        },
        {
            "DestinationCidrBlock": "0.0.0.0/0",
            "GatewayId": "igw-0aaa1111",
            "Origin": "CreateRoute",
            "State": "active",
        },
        {
            "DestinationPrefixListId": "pl-0aaa1111",
            "GatewayId": "vpce-0aaa1111",
            "Origin": "CreateRoute",
            "State": "active",
        },
        {
            "DestinationIpv6CidrBlock": "::/0",
            "GatewayId": "igw-0aaa1111",
            "Origin": "CreateRoute",
            "State": "active",
        },
        {
            "DestinationCidrBlock": "192.168.0.0/16",
            "GatewayId": "vgw-0aaa1111",
            "Origin": "EnableVgwRoutePropagation",
            "State": "active",
        },
    ]
    a = _attrs(
        rt_module,
        {
            "RouteTableId": "rtb-0aaa1111",
            "VpcId": "vpc-0aaa1111",
            "OwnerId": ACCOUNT,
            "Associations": [],
            "Routes": routes,
        },
    )
    assert a["route_count"] == 5
    assert (
        a["ipv4_route_count"],
        a["ipv6_route_count"],
        a["propagated_route_count"],
    ) == (3, 1, 1)


# ------------------------------------------------------------------ 서브넷 남은 IP


def test_subnet_free_ips_ride_only_the_state_path() -> None:
    """🔴 남은 IP 는 시각마다 바뀐다 → 구조 스냅샷에 넣으면 구조 해시가 흔들린다(불변식 ②). 상태 단에만."""
    scope = Scope(
        account_ids=(ACCOUNT,),
        regions=(REGION,),
        resource_types=SUPPORTED_RESOURCE_TYPES,
    )
    sid = f"subnet/{ACCOUNT}/{REGION}/{PUBLIC_SUBNET}"

    def subnet(with_state: bool) -> dict[str, Any]:
        snap = collect(
            scope, factory(FakeAws()), clock=lambda: AT, with_state=with_state
        )
        found = [n for n in snap.nodes if n.id == sid]
        assert len(found) == 1, "전제: 서브넷이 수집됐다"
        return found[0].attributes

    assert "available_ip_address_count" not in subnet(False)
    assert (
        subnet(True)["available_ip_address_count"] == 251
    )  # fakes.py 의 PUBLIC_SUBNET
