"""auto_scaling_group(EC2 Auto Scaling 그룹) 수집 — 「이음매 2」.

파일명이 `auto_scaling.py` 가 아니라 **수집 종류**(`auto_scaling_group`)인 이유는
`services/__init__.py` 머리말 규약(종류=파일명=레지스트리 키)이다. boto3 서비스 이름과 IAM
접두는 둘 다 `autoscaling` 이고 아래 `SPEC` 이 들고 있다.

🔴 **ASG 는 서브넷 하나에 살지 않는다 — VPCZoneIdentifier 로 여러 서브넷·AZ 에 걸친다.** 그리고
`DescribeAutoScalingGroups` 응답에는 VpcId 가 없다(서브넷 ID 만 있다). 그래서 **부모를 리전으로**
두고 걸친 서브넷은 `subnet_ids` 속성으로 남긴다 — 여러 서브넷 중 하나를 골라 부모로 삼으면
없는 위치를 지어내는 것이다(어댑터의 「위치를 날조하지 않는다」 원칙). 렌더러/어댑터가 그
`subnet_ids` 로 VPC·그룹 배치를 정한다(스냅샷에 서브넷 노드가 이미 있으므로 VPC 는 거기서 해소).

🔴 **헬스·현재 인스턴스 목록은 구성이 아니다.** `Instances[].HealthStatus`·`LifecycleState` 와
현재 인스턴스 수는 시시각각 변한다 → 담지 않는다(불변식 ②, 관측 계약 소관). 스케일 **구성**
(min/max/desired)·걸친 서브넷·AZ 만 싣는다. desired 는 서서히 바뀌는 값이라 다른 수집기의
`state` 와 같은 취급(설정된 목표값)으로 남긴다.

🔴 **엣지는 만들지 않는다.** ASG→EC2 엣지는 실제 배치(부모 체인)와 중복이다.
🔵 **LB → ASG 는 화면이 접는 선이다**(스키마 2.8.0 · 사용자 결정 2026-10-20 P1-2 · `docs/canvas-principles.md` §17.27
「ASG」 행). 엔진의 선은 #116 그대로 **LB → 인스턴스**(`load_balancer_target`)다 — 구조와 상태 단이 같은 선 id 를 내야 선
헬스가 붙는데, 상태 단은 ASG 를 부르지 않는다(`observe.OBSERVED_RESOURCE_TYPES` · 논리 묶음 `none`). 이 파일은 접을 재료 둘만 낸다:
① 같은 응답의 `TargetGroupARNs` = **구성 속성** `target_group_arns`(정렬) ② 지금 인스턴스 id = **초안의 비공개 칸**
(`NodeDraft.asg_members`) — 3패스가 그 인스턴스 노드에 `auto_scaling_group_id` 를 단다(`nettopo/lb_targets.py` `memberships`).
🔴 ASG 노드에는 인스턴스 목록을 싣지 않는다(위 「현재 인스턴스 목록은 구성이 아니다」 — 스케일마다 ASG 노드가 「바뀜」이 된다).
인스턴스 쪽 속성은 그 인스턴스와 같이 나고 같이 사라지므로 새 「바뀜」을 만들지 않는다. 옛 판의 「`TargetGroupARNs` 엣지는
"ASG 엔 붙었는데 타깃엔 없는" 상태를 가린다」(Topology_2.md §4)는 엣지를 만들지 않으므로 그대로 피한다 — 등록이 없으면 선도 없고,
정보창 「LB 대상 그룹」 칸이 「등록 0」을 글로 말한다.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..model import Node, node_id
from .base import Ctx, NodeDraft, Service, Spec, draft
from .base import sorted_strs as _sorted

SPEC = Spec(
    "autoscaling",
    "describe_auto_scaling_groups",
    "AutoScalingGroups",
    "autoscaling:DescribeAutoScalingGroups",
)


def _id_segment(native: str) -> str:
    """ASG 이름을 `node_id` 세그먼트로 쓸 수 있는 형태로 만든다 — **`native_id` 는 손대지 않는다.**

    🔴 **AWS 가 이 이름을 제약하지 않는다.** botocore 모델상 ASG 이름은 공백·`|`·`/` 를 허용하고
    `/` 는 **어느 자리에든** 올 수 있다(`/app`·`app/`·`a//b`)
    (`network-firewall` 쪽은 `^[a-zA-Z0-9-]+$` 라 이 문제가 없다). 그런데 `node_id` 패턴은
    `^[a-z0-9_]+/[^/|\\s]+(/[^/|\\s]+)*$`(`schema/topology.schema.json`)이고 **런타임 스키마 검증이
    없어서**(리포에 `jsonschema` 호출 0건) 계약 위반 산출물이 경고 없이 써진다. 실측 2026-09-28:
    `prod app asg`(공백) → 스키마 오류 1건 · `a|b` → 스키마 오류 1건.

    - 공백류·`|` 는 **퍼센트 인코딩**한다. `|` 를 남기면 `edge_id` 문법(`<type>|<node_id>|<node_id>`)의
      유일 파싱 보장까지 깨진다(`collect.py` `_edge_id()`).
    - 🔴 **`/` 도 인코딩한다**(`safe=""`, 리뷰 결정 2026-09-28). 문제는 세그먼트가 늘어나는 것이
      아니라 **빈 세그먼트**다. AWS 는 이름의 **어느 자리에든** `/` 를 허용하므로 `/app`·`app/`·
      `a//b` 가 가능하고, `/` 를 남기면 그것이 **빈 칸이 낀 node_id** 가 된다. 그러면
      ⓐ 스키마 `node_id` 패턴(`(/[^/|\\s]+)*`)이 금지하므로 산출물이 계약 위반이고,
      ⓑ 이력에서 **말없이 사라진다**: `split_node_id()` 가 `ValueError` → `history.target_cells()`
      가 `None` → 그 ASG 의 변경이 「칸 미상」 개수로만 세어지고 변경 이력 목록에서 빠진다.
      `team/app-asg` 처럼 흔한 이름은 인코딩 전에도 통과했지만, 경계 이름까지 **id 가 늘 한
      모양으로** 닫히는 쪽을 택했다.
      🔵 `split_node_id()` 의 `parts[3:]` 재결합(여러 세그먼트 지원)은 **그대로 살아 있다** — 그것은
      ASG 때문이 아니라 LB ARN 꼴(`app/<name>/<id>`)을 `native_id` 로 쓰는 날을 위한 앞선 설계다
      (`test_model.py::test_a_native_id_containing_a_slash_survives_the_round_trip` 이 그렇게 적었다).
    - 인코딩은 **단사(injective)** 여야 한다 — `_` 로 치환하면 `a b` 와 `a_b` 가 같은 id 가 되어
      서로 다른 ASG 두 개가 한 노드로 합쳐진다. `quote` 는 `%` 자신도 인코딩하므로 충돌이 없다.
      되돌리려면 `urllib.parse.unquote` 다(`native_id`·`name` 에 원문이 그대로 있으므로 평소에는
      되돌릴 필요가 없다).

    같은 규칙이 `services/load_balancer.py` 에 주석으로 있다 — 「`native_id` 에 ARN 을 쓸 수 없다:
    `node_id` 는 `/` 를 구분자로 쓴다」.
    """
    return quote(native, safe="")


def build(item: dict[str, Any], ctx: Ctx) -> NodeDraft:
    # ASG API 는 계정-로컬이라 OwnerId 를 주지 않는다 → 조회 계정이 소유자다.
    owner = ctx.account_id
    native = item["AutoScalingGroupName"]
    # VPCZoneIdentifier 는 쉼표로 이어붙인 서브넷 ID 문자열이다. `_sorted` 가 falsy 를 걸러
    # 빈 문자열(서브넷 미지정 ASG)을 자동으로 떨군다.
    subnet_ids = _sorted((item.get("VPCZoneIdentifier") or "").split(","))
    node = Node(
        id=node_id("auto_scaling_group", owner, ctx.region, _id_segment(native)),
        type="auto_scaling_group",
        account_id=owner,
        region=ctx.region,
        native_id=native,
        arn=item.get("AutoScalingGroupARN"),
        name=native,
        attributes={
            "min_size": item.get("MinSize"),
            "max_size": item.get("MaxSize"),
            "desired_capacity": item.get("DesiredCapacity"),
            "availability_zones": _sorted(item.get("AvailabilityZones") or []),
            "subnet_ids": subnet_ids,
            # 2.8.0 — 이 그룹이 인스턴스를 등록하는 LB 대상 그룹(구성 · 정렬). 화면이 LB → 인스턴스 선을 ASG 선으로 접는 재료
            "target_group_arns": _sorted(item.get("TargetGroupARNs") or []),
        },
    )
    return draft(
        node=node,
        ctx=ctx,
        parents=(ctx.region_node(owner),),
        parent_types=("region",),
        # 🔴 지금 인스턴스 — ASG 노드에 싣지 않는다(시시각각 바뀐다). 3패스가 인스턴스 노드에 소속(`auto_scaling_group_id`)을 단다.
        asg_members=frozenset(
            str(i["InstanceId"])
            for i in item.get("Instances") or []
            if i.get("InstanceId")
        ),
    )


SERVICE = Service(spec=SPEC, build=build)
