"""2층(지표) 어댑터 — **ADR 0008 「흐름 판정」의 구현**(D2 지표 고정표 · D3 차단 신호 · D6 판정 창).

ADR 0006 §O7 의 2층 열을 ADR 0008 이 개정했다. 바뀐 것 4개만 먼저 적는다:

1. **창이 900초**(D6, 사용자 결정 2026-09-24)이고 **판정은 창 전체**로 한다 — 카운터는 창 합계,
   레이트·게이지는 창 최대, 상태·건강은 창 최소·최대. 옛 판정은 「값이 있는 마지막 지점」이었고
   실계정에서 `live`↔`up` 왕복 8건을 만들었다(04:18:02 에 떨어졌다가 **8초 뒤** 복귀 — 트래픽이
   사라진 것이 아니라 판정이 마지막 한 칸만 보고 있었다).
2. **한 대상에 지표가 여러 개**다. 역할이 넷이다 — `flow`(통과량) · `drop`(버려짐) ·
   `health`(대상 건강) · `state`(구간 상태). 역할이 판정을 만들고, 이름이 판정을 만들지 않는다.
3. **엣지도 2층을 받는다**(D8 = ADR 0006 O7 규칙 1 의 개정). 조건은 **구간을 특정하는 차원 조합**을
   가진 지표뿐이다 — 노드 지표를 엣지에 복사하는 것은 여전히 금지다(그것이 폐기된 종단 추론이다).
4. **`down` 을 2층이 낼 수 있다**(D3). 옛 머리말은 「2층은 `live` 만 만든다」였고 근거는 「ALB 5XX 는
   앱 오류일 수 있다」였다 — 그 근거는 여전히 참이므로 **통과량이 0 일 때만** 차단을 말한다.
   🔴 통과 > 0 이면 드롭이 있어도 `live` 다(D3 의 🔴: 「드롭 > 0 은 흐름을 부정하지 않는다」).

🔴 **차원 조합은 실측이다.** 「지표 이름이 있다」와 「그 차원 조합으로 발행된다」는 다른 사실이고,
틀리면 **에러가 아니라 빈 배열**이 와서 「흐름 없음」과 바이트가 같아진다(ADR 0006 §O7-1 함정 1).
아래 표의 조합은 전부 2026-09-24 `cloudwatch:list-metrics`(무료) 로 **이 계정에서 발행 중임을
확인**했다 — 조합을 고칠 때는 같은 방법으로 다시 재라.

🔴 **창의 끝은 벽시계가 아니라 스냅샷의 시각이다**(불변식 ② — `collect.utc_now()` 가 유일한
wall-clock 예외이고 여기서 시각을 또 읽으면 예외가 둘이 된다).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from .model import Reason, Snapshot
from .services.base import classify

METRIC_API = "cloudwatch:GetMetricData"

# 🔴 `period` 는 60 고정(승인 2026-09-23). 창은 **900**(ADR 0008 D6) — 창 15분 · 해상도 1분이면
# 지표 하나가 15 dp 다. 창을 더 줄이면 게시 지연(PrivateLink 246s, §O7-1) 때문에 최근 구간이
# 통째로 비고, 늘리면 「지금 흐르나」가 아니라 「최근에 흘렀나」를 말하게 된다.
PERIOD_SECONDS = 60
WINDOW_SECONDS = 900

# 한 번의 `GetMetricData` 에 넣는 쿼리 수(= **지표 1개 × 대상 1개**가 쿼리 1개다). AWS 상한은
# 쿼리 500 · `MaxDatapoints` 100,800 이고 쿼리당 15 dp 이므로 100개면 1,500 으로 넉넉하다.
QUERIES_PER_CALL = 100

# 역할 4개. 🔴 **판정은 역할로 한다** — 지표 이름으로 분기하면 서비스를 더할 때마다 판정 코드가
# 갈라지고, 「이 이름은 통과량인가 드롭인가」가 호출부마다 다르게 해석된다.
ROLE_FLOW = "flow"
ROLE_DROP = "drop"
ROLE_HEALTH = "health"
ROLE_STATE = "state"

# 창을 접는 방법 3개. 🔴 통계(`Stat`)와 접는 방법은 **다른 층의 선택**이다 — `Stat` 은 1분 칸 안을
# 접고, `aggregate` 는 15칸을 접는다. 카운터에 `max` 를 쓰면 「가장 바쁜 1분」만 보게 되고,
# 레이트에 `sum` 을 쓰면 「분당 비트/초의 합」이라는 뜻 없는 수가 된다(ADR 0008 D2 통계 규칙).
AGG_SUM = "sum"
AGG_MAX = "max"
AGG_MIN = "min"

# 발행 규칙 4개(ADR 0009 D3). 🔴 **「이 창에 흐름이 없었다」를 적을 자격은 `REPORT_ALWAYS` 에만
# 있다.** 나머지 셋은 값이 0 일 때 데이터포인트가 **애초에 오지 않으므로**, 0 을 봤다는 사실이
# 「흐름 없음」이 아니라 「그 지표로는 관측 불가」다. 둘을 같은 색으로 말하면 화면이 「트래픽이
# 없습니다」라고 단정하는데 실제로는 지표가 조용했을 뿐인 경우가 섞인다(ADR 0009 ④).
REPORT_ALWAYS = "always"
REPORT_NONZERO_ONLY = "nonzero_only"
# 전제(등록된 타깃·기능 활성화)가 있어야 발행된다 → 부재는 「차단」이 아니라 「전제 불충족」이다.
REPORT_CONDITIONAL = "conditional"
# 🔴 **AWS 문서가 그 지표의 발행 조건을 적지 않았다.** `nonzero_only` 로 적으면 우리가 확인하지
# 않은 사실을 주장하는 것이고(ADR 0009 D2 의 「모름을 꺼짐으로 적지 마라」와 같은 규율), 판정에서는
# `nonzero_only` 와 똑같이 **자격 없음**으로 다룬다. 문서를 확인하면 이 값을 지운다.
REPORT_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MetricRef:
    """한 지표를 읽는 방법. `role` 이 판정에서의 뜻이고 `aggregate` 가 창을 접는 방법이다.

    `reporting` 은 **AWS 가 그 지표를 언제 발행하는가**이고, 접는 방법과는 다른 층의 사실이다 —
    창을 어떻게 접어도 오지 않은 데이터포인트는 만들어지지 않는다(ADR 0009 D3).
    """

    name: str
    statistic: str
    aggregate: str
    role: str
    reporting: str
    # 🔴 **드롭 전용 — 이 지표가 ADR 0008 D3 의 「차단 신호」인가.** 드롭이라는 역할과 「차단이라고
    # 말할 자격」은 다른 사실이다: NAT 의 `PacketsDropCount` 는 **갓 만든 정상 NAT 이 생성 직후
    # 3분에 49패킷을 버린다**(랩 A1 실측) → 그것으로 차단을 적으면 만든 지 15분 안 된 NAT 이 전부
    # 빨갛게 찍힌다. 그래서 그 지표는 **배지 전용**으로 남기고 차단 근거에서 뺀다. 이름으로 분기하지
    # 않는 이유는 이 파일 머리(역할 4개 주석)와 같다 — 선언에 적고 판정은 선언만 본다.
    blocking: bool = False
    # 🔴 **통과량 전용 — 이 지표가 「들어왔다」만 세는가**(ADR 0008 정정 1-2). TGW 의 `BytesIn` 은
    # 「TGW 로 들어왔다」를 세고 「건너갔다」를 세지 않는다 — 랩에서 `BytesIn` 420 인데 **전량이
    # 블랙홀에 버려졌다**(정정 1). 이 표시가 붙은 지표는 혼자서 흐름을 증명하지 못하고, 차단
    # 드롭을 **재서** 0 일 때만 흐름이 된다(`_fold`). 빠뜨리면 블랙홀 구간이 초록으로 그려진다.
    arrival_only: bool = False
    # 🔴 **통과량 전용 — 빈 응답을 「쟀고 0」으로 읽는가**(사용자 결정 2026-10-21 · #128 과 함께). `reporting` 은 AWS 가
    # 언제 발행하나(사실)이고 이 표시는 **우리가 빈 응답을 어떻게 읽나**(해석)다 — 두 층을 한 값에 섞지 않는다.
    # 참이면: 호출이 성공했고(그 쿼리의 `StatusCode` = `Complete`) 창 안에 점이 0개면 그 지표를 0 으로 잰 것으로
    # 본다(`Reading.zero_filled`) → 판정 = `up`(흐름 없음 · `datapoints: 0`). 못 읽음(호출 거부·쿼리 Forbidden·
    # 응답 누락)은 그대로 `partial`/확인 불가다(불변식 ④). 지금 참인 것 = **ALB `ProcessedBytes`**(노드·배치 선) +
    # **수락된 엔드포인트의 `BytesProcessed`**(노드·서브넷 구간 · `ACCEPTED_ENDPOINT_PROBES` — 상태로 고른다) —
    # 아래 「발행 규칙별 빈 응답 해석」 표. 🔴 사용률(`utilization.py`)은 같은 빈 응답을 **「값 없음」** 으로 둔다
    # (#128) — 「지금 흐르나」는 ALB 의 침묵을 「안 흐른다」로 말해도 되지만 「얼마나 찼나」는 0% 를 지어낼 수 없다.
    empty_is_zero: bool = False


def _flow(
    name: str,
    *,
    reporting: str,
    statistic: str = "Sum",
    aggregate: str = AGG_SUM,
    arrival_only: bool = False,
    empty_is_zero: bool = False,
) -> MetricRef:
    """통과량. 기본은 카운터(Sum + 창 합계)이고 게이지는 호출부가 `Average`+`max` 로 준다.

    🔴 `reporting` 은 **기본값이 없다.** 통과량 지표의 발행 조건이 곧 「0 을 흐름 없음으로 읽어도
    되는가」이므로, 여기에 기본값을 두면 지표를 더하는 사람이 조사하지 않고 지나갈 수 있고 그
    누락은 화면에서 **거짓 단정**으로 나타난다(ADR 0009 ④).
    """
    return MetricRef(
        name,
        statistic,
        aggregate,
        ROLE_FLOW,
        reporting,
        arrival_only=arrival_only,
        empty_is_zero=empty_is_zero,
    )


def _drop(name: str, *, reporting: str, blocking: bool) -> MetricRef:
    """버려진 양. 카운터다 — 창 합계가 0 보다 크면 「이 창에 드롭이 있었다」.

    🔴 `blocking` 도 **기본값이 없다**(`reporting` 과 같은 이유). 「드롭이 있었다」와 「막혔다고
    말해도 된다」는 다른 주장이고, 기본값을 두면 지표를 더하는 사람이 ADR 0008 D3 표를 보지 않고
    지나갈 수 있다. 빠뜨리면 화면이 **잘 도는 구간을 빨갛게** 칠하거나(True 기본) 반대로 **진짜
    차단을 조용히 삼킨다**(False 기본). `test_metrics.py` 가 이 값을 D3 표와 대조한다.
    """
    return MetricRef(name, "Sum", AGG_SUM, ROLE_DROP, reporting, blocking)


def _health(name: str, *, reporting: str = REPORT_CONDITIONAL) -> MetricRef:
    """대상 건강 수. 🔴 `Minimum` + 창 **최소** — 한 칸이라도 0 이면 그 창에 전멸 구간이 있었다.

    기본 `conditional` 의 근거: 이 계열은 **등록된 타깃이 있어야** 발행된다(ADR 0009 D3 부수
    결론). 그래서 **부재를 「불건강」으로 읽으면 타깃이 빈 로드밸런서가 빨갛게 그려진다** — 판정
    코드가 `measured` 를 함께 요구하는 이유다(`_fold`).
    """
    return MetricRef(name, "Minimum", AGG_MIN, ROLE_HEALTH, reporting)


def _state(name: str, *, reporting: str = REPORT_ALWAYS) -> MetricRef:
    """구간 상태(1=up). 🔴 `Maximum` + 창 **최대** — 창 내내 0 이어야 차단으로 본다.

    한 칸만 0 이어도 `down` 으로 적으면 유지보수·재수렴이 전부 빨간 선이 된다(D6 의 깜빡임과 같은
    계열). 「한 번 끊겼다」는 이력의 몫이고, 여기는 「지금 차단인가」를 말한다.

    기본 `always` 의 근거: 상태 지표는 **값이 0 인 것 자체가 사실**이므로 발행돼야 의미가 있다
    (`TunnelState` 가 0 일 때 안 오면 터널이 내려간 것을 아무도 못 본다). 지표를 실제로 붙이는
    PR 에서 문서로 확인하고 다르면 여기서 바꾼다.
    """
    return MetricRef(name, "Maximum", AGG_MAX, ROLE_STATE, reporting)


@dataclass(frozen=True, slots=True)
class Probe:
    """한 대상 종류를 2층에서 읽는 방법 전부. 🔴 **차원 이름은 여기가 코드 상수다**(§O7-1 결정).

    `ListMetrics` 로 찾지 않는다 — 차원 값이 전부 1층 `Describe*` 응답에서 오므로 발견이 필요
    없고, 「`ListMetrics` 는 제품 경로가 아니다」(`docs/required-permissions.md` §1-2)가 유지된다.
    ADR 0008 D2 표와의 일치는 `engine/tests/test_metrics.py` 가 ADR 을 파싱해 강제한다.
    """

    key: str
    namespace: str
    dimensions: tuple[str, ...]
    metrics: tuple[MetricRef, ...]


# ─────────────────────────────────────────────────────────────────────────────
# `reporting=` 의 근거 (AWS 공식 문서 직접 확인 2026-09-25 · ADR 0009 D3)
#
# 🔴 **이 값은 추측하면 안 된다.** 「조용하다」를 「흐름이 없었다」로 읽어도 되는지가 여기서
# 갈리고, 틀리면 화면이 **거짓 단정**을 한다(ADR 0009 ④). 지표를 더할 때는 그 지표의
# "Reporting criteria" 문장을 찾아 아래처럼 근거를 남기고, 못 찾으면 `unknown` 이 정답이다.
#
# | 네임스페이스 | 문서가 적은 발행 조건(원문) | 값 |
# |---|---|---|
# | `AWS/TransitGateway` | "All metrics are always reported. Their values are dependent on the traffic through the transit gateway." (지표 표 바로 위 · 어태치먼트 절에도 같은 문장) | `always` |
# | `AWS/PrivateLinkEndpoints` | "Reporting criteria: The endpoint received traffic during the one-minute period." | `nonzero_only` |
# | `AWS/ApplicationELB` `ProcessedBytes` | "Reporting criteria: There is a nonzero value" | `nonzero_only` |
# | `AWS/ApplicationELB` `RequestCount` | "Reporting criteria: Reported if there are registered targets." | `conditional` |
# | `AWS/NetworkELB` 통과량·거절 4종 | **문서가 자기모순이다**(아래) → 실계정 실측으로 **표가 이겼다** | `always` |
# | `AWS/NATGateway` `PacketsDropCount` | 발행 조건 문장이 **한 건도 없다** (`metrics-dimensions-nat-gateway.html` 에 "Reporting criteria" 0건) → 실측으로 0 발행 확인 | `always` |
# | `AWS/NATGateway` 바이트 2종 | 문서 0건 · **랩 A1 실측으로 확정**(트래픽 0 인 NAT 이 73분 중 72분에 0 을 발행) | `always` |
# | `AWS/NATGateway` `ErrorPortAllocation` | 문서 0건 · 실측은 73분에 **36~37점**(간격 불규칙) | `unknown` |
#
# 🔴 **NLB 의 모순**: 지표 표는 `ActiveFlowCount`·`NewFlowCount`·`ProcessedBytes`·
# `RejectedFlowCount` 에 "Reporting criteria: Always reported." 라고 적는데, 같은 페이지 머리말은
# "Elastic Load Balancing reports metrics to CloudWatch **only when requests are flowing** through
# the load balancer. … If there are no requests flowing through the load balancer or no data for a
# metric, **the metric is not reported**." 라고 적는다. ALB 페이지에도 **같은 머리말**이 있고 거기서는
# 표가 "There is a nonzero value" 로 머리말과 일치한다 → 머리말은 ELB 공통이고 NLB 표만 어긋난다.
# 어느 쪽이 맞는지는 **문서로 결정할 수 없다** → 실계정으로 갈랐다(아래 표): NLB 는 0 값을 발행하고
# ALB 는 발행하지 않는다. 즉 머리말은 NLB 에 대해 틀렸고 표가 맞다.
#
# 🔵 **실계정 실측 2026-09-25**(`get_metric_data` · 개발 계정 · 창 3시간 + 24시간 · 주기 60초 ·
# 자격 = `points > 0 ∧ 0 값 데이터포인트 ≥ 1` 이면 표가 이긴 것이므로 `always`, `points == 0` 이면
# 머리말이 맞고 `nonzero_only`). 🔴 값을 올린 근거는 **이 표뿐이다** — 문서만으로 올리지 마라.
#
# | 대상 | 지표 | 180분 창 결과 | 판정 |
# |---|---|---|---|
# | NLB 1대 | `NewFlowCount` | 179개 · **전부 0** | `always` |
# | NLB 1대 | `RejectedFlowCount` | 179개 · **전부 0** | `always` |
# | NLB 1대 | `ProcessedBytes` | 179개 · **0 값 157개** | `always` |
# | NLB 1대 | `ActiveFlowCount` | 179개 · **0 값 86개** | `always` |
# | NLB 1대 | `HealthyHostCount` | **0개** | `conditional` 그대로(타깃 없음) |
# | 같은 NLB · **AZ 차원까지** | 통과량·거절 3종 | 180개 · **전부 0** | `always`(배치 프로브도 같다) |
# | ALB 1대 | `ProcessedBytes` | **9개** · 0 값 0개 | `nonzero_only` 확인 |
# | ALB 1대 | `RequestCount`·`HealthyHostCount` | **0개**(통과량은 9개였다) | `conditional` 확인 |
# | NAT 1대 | `PacketsDropCount` | 180개 → 24시간 **1,440개 전부 0** | `always` |
# | NAT 1대 | `BytesInFromSource`·`BytesOutToDestination` | 1,440개 · 0 값 0개 · **최소 5,669** | **미측정** |
# | **랩 NAT 1대(트래픽 0)** | `BytesInFromSource`·`BytesOutToDestination` | **각 72**(73분 창) · **전부 0** | **`always`** |
# | 같은 랩 NAT · **대조군** | `PacketsDropCount` | 72 · 합계 **49**(생성 직후 3분에 전부) | `always` 재확인 |
# | 같은 랩 NAT · **음성 대조군** | `IdleTimeoutCount` | **0개**(`list-metrics` 에도 없다) | 판별력 증거 |
#
# 🔵 **승격 완료 2026-09-26**(랩 A1 · `docs/verification-lab.md:141,148`): 24시간 실측이 `unknown`
# 이었던 이유는 「0 을 발행하지 않는다」가 아니라 그 NAT 에 **조용한 분이 한 번도 없었다**는 것이었다
# (0 이 될 기회가 없었다). 라우트를 하나도 붙이지 않은 NAT 을 만들어 73분을 재니 **72분에 점이 오고
# 값이 전부 0** 이다 → 조용한 NAT 은 「흐름 없음」으로 확정해도 된다. 같은 창의 대조군이 점 개수뿐
# 아니라 **0 아닌 값(49)** 까지 냈으므로 「우리 호출·차원이 틀렸다」는 배제됐고, 0개가 나올 수 있는
# 호출(음성 대조군 `IdleTimeoutCount` = 0개)로 72개를 받았으므로 이 측정에는 판별력이 있다.
# 🔴 **같은 측정이 결함 하나를 같이 찾았다** — 드롭 49개가 **NAT 생성 직후 3분에만** 몰렸고 그 창의
# 통과량은 0 이다. 그래서 「통과 0 + 드롭 > 0 = 차단」을 그대로 적용하면 **갓 만든 정상 NAT 이 만든 지
# 15분 동안 차단으로 찍힌다.** NAT 의 드롭 수는 **배지 전용**이고 차단 판정에 쓰지 않는다
# (ADR 0008 「실측 정정 5」 · `docs/verification-lab.md:153-159`).
#
# ── 발행 규칙별 빈 응답 해석(흐름 판정 · 사용자 결정 2026-10-21 · ADR 0006 §O7 「빈 응답 해석」) ──
#
# 「호출은 성공했는데 15분 창에 점이 0개」를 지표 계열마다 어떻게 읽나. 못 읽음(권한·throttle·쿼리 Forbidden·
# 응답 누락)은 **어느 계열이든** 확인 불가 + 사유다(불변식 ④) — 아래 표는 성공한 빈 응답만 다룬다.
#
# | 계열 | 빈 응답 = | 근거 |
# |---|---|---|
# | `always`(TGW · NLB · NAT 바이트·드롭) | 우리 쪽 결함 의심 → 확인 불가 + 「차원 조합 확인」 사유 | 값 0 도 발행한다(위 실측) |
# | ALB `ProcessedBytes`(`empty_is_zero`) | **쟀고 0 → `up`(흐름 없음)** · 배치 선 행도 남는다 | 「There is a nonzero value」 + 실측(9점 · 0 값 0개) · 사용자 결정 2026-10-21: 행이 사라지면 이력에 「흐름 멈춤」이 남지 않았다 |
# | ALB `RequestCount`(`conditional`) | 확인 불가(그대로) — 판정은 `ProcessedBytes` 가 선다 | 등록된 대상이 없으면 오지 않는다(부재 = 전제 불충족) |
# | `AWS/PrivateLinkEndpoints` `BytesProcessed` · **수락된**(`State` = `available`) 인터페이스 엔드포인트(노드 · 서브넷 구간) | **쟀고 0 → `up`(흐름 없음)** · 구간 행도 남는다(`ACCEPTED_ENDPOINT_PROBES`) | 「The endpoint received traffic during the one-minute period」 = ALB 와 같은 모양 · 사용자 결정 2026-10-21(ADR 0006 §O7-2a 개정) |
# | 같은 지표 · **수락 전·실패**(`pendingAcceptance` · `pending` · `failed` · `partial` · 값 없음 … — `rejected`·`expired` 는 구조가 노드째 뺀다) | 확인 불가(그대로 · 구간 행 없음) — 트래픽을 받을 수 없는 엔드포인트를 「흐름 없음」이라 하지 않는다 | 수락 대기 엔드포인트도 0점이다(ADR 0008 정정 4) · 노드는 1층이 이미 `down`(`observe.NODE_RULES`) |
# | `AWS/PrivateLinkEndpoints` `NewConnections` · `PacketsDropped` | 확인 불가(그대로 · ADR 0009 D3) — 판정은 `BytesProcessed` 가 선다 · 차단은 드롭을 **받았을 때만** | 0 을 지어내면 「드롭을 재서 0」이 거짓 근거가 된다 |
# | `unknown`(NAT `ErrorPortAllocation`) | 확인 불가(그대로) | 발행 조건을 모른다 |
#
# 🔴 사용률(`utilization.py` · #128)의 해석은 **따로다**: `nonzero` 계열의 빈 창 = 「값 없음」(`no_points`) — 0% 가 아니다.
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 노드 프로브 — 「이 자원 자신이 무언가를 흘렸나」
# ─────────────────────────────────────────────────────────────────────────────
NODE_PROBES: dict[str, Probe] = {
    "nat_gateway": Probe(
        "nat_gateway",
        "AWS/NATGateway",
        ("NatGatewayId",),
        (
            # 🔵 랩 A1 실측으로 `unknown` → `always` 승격(2026-09-26 · `verification-lab.md:141,148`).
            _flow("BytesOutToDestination", reporting=REPORT_ALWAYS),
            _flow("BytesInFromSource", reporting=REPORT_ALWAYS),
            # 🔴 **배지 전용**(blocking=False · ADR 0008 D3 + 실측 정정 5). 이 수로 차단을 적으면
            # 갓 만든 정상 NAT 이 15분 동안 빨갛다 — 랩 A1 에서 드롭 49개가 **생성 직후 3분에만**
            # 몰렸고 같은 창의 통과량은 0 이었다(`docs/verification-lab.md:143,153`).
            _drop("PacketsDropCount", reporting=REPORT_ALWAYS, blocking=False),
            # NAT 의 차단 신호는 **포트 고갈**뿐이다 = 실제로 새 연결을 못 맺는다(D3 표).
            # 🔴 `unknown`: 랩 A1 에서 73분 중 **36~37점**만 왔고 간격이 불규칙했다 → 「매 분 발행」이
            # 아니므로 `always` 로 적으면 거짓이고, 0 을 발행한 것은 봤으므로 `nonzero_only` 도
            # 아니다. 값 > 0 일 때만 쓰는 신호라 자격 없음으로 다뤄도 판정이 약해지지 않는다.
            _drop("ErrorPortAllocation", reporting=REPORT_UNKNOWN, blocking=True),
        ),
    ),
    "transit_gateway": Probe(
        "transit_gateway",
        "AWS/TransitGateway",
        ("TransitGateway",),
        (
            _flow("BytesIn", reporting=REPORT_ALWAYS, arrival_only=True),
            _flow("BytesOut", reporting=REPORT_ALWAYS),
            _drop("PacketDropCountBlackhole", reporting=REPORT_ALWAYS, blocking=True),
            # 🔴 「경로 없음」 드롭도 카드에서 재야 한다(정정 1-2). 블랙홀만 재면 라우트가 **없어서**
            # 전량 버리는 TGW 가 「들어옴 > 0 · 차단 드롭 0」 = 흐름으로 그려진다.
            _drop("PacketDropCountNoRoute", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
    # 🔴 어태치먼트는 **노드이면서 구간**이다 — 아래 엣지 프로브와 같은 계열을 쓴다. 둘 다
    # 필요하다: ADR 0006 §O7 노드 표가 이 종류에 `live`·`up`·`down` 을 약속했고, D8-1 은 같은
    # 차원이 **구간**도 특정한다고 적었다. 노드 프로브를 빼면 화면에서 **어태치먼트 박스만 영원히
    # 회색이고 그 양쪽 선은 초록**이 된다(같은 사실을 두 모양으로 그린 것이다).
    # 같은 계열을 세 번 사지는 않는다 — `_expand()` 가 중복 계열을 하나로 접는다.
    "transit_gateway_attachment": Probe(
        "transit_gateway_attachment",
        "AWS/TransitGateway",
        ("TransitGateway", "TransitGatewayAttachment"),
        (
            _flow("BytesIn", reporting=REPORT_ALWAYS, arrival_only=True),
            _flow("BytesOut", reporting=REPORT_ALWAYS),
            _drop("PacketDropCountBlackhole", reporting=REPORT_ALWAYS, blocking=True),
            _drop("PacketDropCountNoRoute", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
    # 🔴 차원 이름에 **공백이 있고**, 넷을 전부 채워야 조합이 존재한다(§O7-1 함정 2).
    # 실측 2026-09-24: 이 4개 조합과 「+ `Subnet Id`」 5개 조합이 **둘 다** 발행된다 —
    # 노드(엔드포인트 전체)는 4개, 구간(엔드포인트↔서브넷)은 5개를 쓴다(아래 엣지 프로브).
    "vpc_endpoint": Probe(
        "vpc_endpoint",
        "AWS/PrivateLinkEndpoints",
        ("VPC Id", "VPC Endpoint Id", "Endpoint Type", "Service Name"),
        (
            _flow("BytesProcessed", reporting=REPORT_NONZERO_ONLY),
            _flow("NewConnections", reporting=REPORT_NONZERO_ONLY),
            _drop("PacketsDropped", reporting=REPORT_NONZERO_ONLY, blocking=True),
        ),
    ),
    "load_balancer:application": Probe(
        "load_balancer:application",
        "AWS/ApplicationELB",
        ("LoadBalancer",),
        (
            # 🔴 `empty_is_zero` — 성공한 빈 응답 = 흐름 없음(위 「빈 응답 해석」 표 · 사용자 결정 2026-10-21)
            _flow("ProcessedBytes", reporting=REPORT_NONZERO_ONLY, empty_is_zero=True),
            _flow("RequestCount", reporting=REPORT_CONDITIONAL),
        ),
    ),
    "load_balancer:network": Probe(
        "load_balancer:network",
        "AWS/NetworkELB",
        ("LoadBalancer",),
        (
            _flow("ProcessedBytes", reporting=REPORT_ALWAYS),
            _flow("NewFlowCount", reporting=REPORT_ALWAYS),
            # 🔴 게이지다 — `Sum` 으로 부르면 같은 흐름을 표본 수만큼 더해 「1분에 흐름 3,600개」가
            # 나온다. 흐름의 유무만 보므로 `Average` + 창 최대가 맞다.
            _flow(
                "ActiveFlowCount",
                reporting=REPORT_ALWAYS,
                statistic="Average",
                aggregate=AGG_MAX,
            ),
            _drop("RejectedFlowCount", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# 엣지 프로브 — 「이 **구간**을 무언가가 지났나」. ADR 0008 D8 의 자격: 차원 조합이 구간을
# 특정해야 한다. 그래서 여기 있는 셋은 전부 **차원이 2개 이상**이고, 그 중 하나가 상대편이다.
# ─────────────────────────────────────────────────────────────────────────────
EDGE_PROBES: dict[str, Probe] = {
    # 어태치먼트 = TGW 와 VPC 사이의 구간. 실측: `TransitGateway + TransitGatewayAttachment`
    # 조합으로 BytesIn/Out 과 드롭 5종이 발행된다.
    "attachment": Probe(
        "attachment",
        "AWS/TransitGateway",
        ("TransitGateway", "TransitGatewayAttachment"),
        (
            _flow("BytesIn", reporting=REPORT_ALWAYS, arrival_only=True),
            _flow("BytesOut", reporting=REPORT_ALWAYS),
            _drop("PacketDropCountBlackhole", reporting=REPORT_ALWAYS, blocking=True),
            _drop("PacketDropCountNoRoute", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
    # 엔드포인트 ↔ 서브넷. 실측 정정 2026-09-24: `Subnet Id` 차원이 **발행된다** → 이 구간은
    # 연역이 아니라 직접 측정이다(옛 「서브넷 묶음 연역」은 D4 에서 폐기했다).
    "endpoint_connection": Probe(
        "endpoint_connection",
        "AWS/PrivateLinkEndpoints",
        ("VPC Id", "VPC Endpoint Id", "Endpoint Type", "Service Name", "Subnet Id"),
        (
            _flow("BytesProcessed", reporting=REPORT_NONZERO_ONLY),
            _flow("NewConnections", reporting=REPORT_NONZERO_ONLY),
            _drop("PacketsDropped", reporting=REPORT_NONZERO_ONLY, blocking=True),
        ),
    ),
    # LB ↔ 서브넷 배치. 🔴 AWS 는 서브넷 차원을 발행하지 않고 **AZ 차원**을 발행한다(실측:
    # `AvailabilityZone + LoadBalancer`). 서브넷은 AZ 하나에 속하므로 서브넷의 AZ 로 묻는다.
    # 같은 AZ 에 같은 LB 의 서브넷이 둘이면 두 선이 같은 값을 받는다 — 그것이 AWS 가 아는
    # 최대 해상도이고, 우리가 더 잘게 나누면 발명이 된다(불변식 ③).
    "load_balancer_placement:application": Probe(
        "load_balancer_placement:application",
        "AWS/ApplicationELB",
        ("LoadBalancer", "AvailabilityZone"),
        (
            # 🔴 `empty_is_zero` — 고치기 전에는 대상이 없는 AZ(`RequestCount` 도 안 온다)에서 이 선의 행이 통째로
            # 사라져(`observe._metric_only_edge` 의 「못 쟀으면 행이 없다」) 이력에 「흐름 멈춤」이 남지 않았다.
            # 「배치 흐름 멈춤」이 남은 때는 그 AZ 의 `RequestCount` 가 0 을 낸 창뿐이었다(QA B 2차 F5 · 2026-10-16).
            _flow("ProcessedBytes", reporting=REPORT_NONZERO_ONLY, empty_is_zero=True),
            _flow("RequestCount", reporting=REPORT_CONDITIONAL),
        ),
    ),
    "load_balancer_placement:network": Probe(
        "load_balancer_placement:network",
        "AWS/NetworkELB",
        ("LoadBalancer", "AvailabilityZone"),
        (
            _flow("ProcessedBytes", reporting=REPORT_ALWAYS),
            _flow("NewFlowCount", reporting=REPORT_ALWAYS),
            _drop("RejectedFlowCount", reporting=REPORT_ALWAYS, blocking=True),
        ),
    ),
}

# 🔴 **수락된 엔드포인트의 판정용 프로브**(사용자 결정 2026-10-21 · ADR 0006 §O7-2a 개정 · 위 「빈 응답 해석」 표).
# 차원·지표·발행 규칙은 위 상수 **그대로**(복사하지 않고 거기서 만든다 — 차원 상수가 두 벌이 되면 한쪽만 고쳐진다)이고,
# 달라지는 것은 `BytesProcessed` 의 **해석** 하나다 = 성공한 빈 응답을 「쟀고 0」(`empty_is_zero`)으로 읽는다.
# 🔴 수락 전(`pendingAcceptance` 등)에는 쓰지 않는다 — 그 엔드포인트도 똑같이 0점이라(ADR 0008 정정 4) 같은 빈 응답을
# 「흐름 없음」으로 읽으면 트래픽을 받을 수 없는 엔드포인트가 「조용히 정상」으로 보인다. 고르는 곳 = `_endpoint_probe`.
_ENDPOINT_ACCEPTED = "available"
_ENDPOINT_ZERO_FILLED = "BytesProcessed"


def _accepted_variant(probe: Probe) -> Probe:
    return replace(
        probe,
        metrics=tuple(
            replace(ref, empty_is_zero=True)
            if ref.name == _ENDPOINT_ZERO_FILLED
            else ref
            for ref in probe.metrics
        ),
    )


ACCEPTED_ENDPOINT_PROBES: dict[str, Probe] = {
    "vpc_endpoint": _accepted_variant(NODE_PROBES["vpc_endpoint"]),
    "endpoint_connection": _accepted_variant(EDGE_PROBES["endpoint_connection"]),
}

# 2층 지표가 **없는** 종류(ADR 0008 D2 마지막 두 줄 = §O7 의 「없음」). 「아직 안 만들었다」와
# 구별하기 위해 적어 둔다 — 이 집합과 `NODE_PROBES` 의 합집합이 관측하는 종류를 덮는지
# 테스트가 검사한다. 🔴 라우팅 테이블은 **판정의 입력**이고(D5) 자신은 흐름 어휘를 갖지 않는다.
NO_METRIC_TYPES = frozenset(
    {
        "vpc",
        "subnet",
        "route_table",
        "internet_gateway",
        "vpc_peering_connection",
        # W3 — §O7 의 2층 칸이 「없음」인 W2 종류.
        "transit_gateway_route_table",
        "sagemaker_notebook_instance",
        "sagemaker_domain",
        "ec2_instance_connect_endpoint",
        # 2.6.0 — VPN 게이트웨이 둘. 지표는 VPN 연결 차원(`VpnId`)에만 있다(§O7 「Site-to-Site VPN 3종」).
        "vpn_gateway",
        "customer_gateway",
        # 2026-10-13 — DXGW 는 글로벌이고 CloudWatch 지표가 없다(§O7 DX 행 · 지표는 연결·VIF 차원뿐).
        "direct_connect_gateway",
    }
)

# 🔴 세 번째 갈래 — §O7 이 지표를 **적었지만**(🔵 칸) 프로브를 **아직 안 만든** 종류(W3 에 1층만 붙었다).
# `NO_METRIC_TYPES` 에 넣으면 「지표가 없다」는 거짓이 되고, 2층 coverage 가 `ok`+0 이 되어 「읽었는데 조용하다」로
# 읽힌다. 그래서 2층을 켜도 이 칸은 `skipped`(안 했다)다(`observe.build_observation`). 프로브를 만드는 PR 이 줄을 뺀다.
METRIC_PENDING_TYPES = frozenset(
    {
        "rds_db_instance",
        "ecs_service",
        "sagemaker_endpoint",
        "ec2_instance",
        "eks_cluster",
        "network_firewall",
        # 2.6.0 — `AWS/VPN` `TunnelState`·`TunnelDataIn/Out`. 실물 0건이라 좌표를 랩 없이 확정 못 했다.
        "vpn_connection",
        # #114(2.9.0) — `AWS/ElastiCache` `CurrConnections`·`NetworkBytesIn/Out`(`CacheClusterId` 차원) · 1층만 붙었다.
        "elasticache_cluster",
        # #114 — `AWS/RDS` `DatabaseConnections`(`DBClusterIdentifier` 차원).
        "rds_db_cluster",
        # #114 — `AWS/ES` `SearchRate`·`IndexingRate`(`DomainName`·`ClientId` 차원).
        "opensearch_domain",
        # #114 — `AWS/Kafka` `BytesInPerSec`·`BytesOutPerSec`(`Cluster Name`·`Broker ID` 차원).
        "msk_cluster",
        # 2026-10-13 — `AWS/DX` `ConnectionState`·`VirtualInterfaceBpsEgress/Ingress`(1층만 붙었다 · 2층은 후속).
        "direct_connect_connection",
        "direct_connect_virtual_interface",
    }
)

# 게이트웨이 엔드포인트·게이트웨이 LB 는 지표가 없다(§O7: 「Interface 만」).
_INTERFACE = "interface"


@dataclass(frozen=True, slots=True)
class MetricQuery:
    """한 대상 × 한 프로브. `values` 는 `probe.dimensions` 와 **같은 순서**다.

    `observed_on` = **지표를 실제로 잰 대상**(ADR 0008 D8-2). `target_id` 와 다르면 그 행은
    B(연역)이고 렌더러가 배지로 그렇게 말한다. 같으면(기본) A(직접 측정)다.
    """

    target_id: str
    target_kind: str
    account_id: str
    region: str
    resource_type: str
    probe: Probe
    values: tuple[str, ...]
    observed_on: str | None = None

    def dimensions(self) -> list[dict[str, str]]:
        return [
            {"Name": name, "Value": value}
            for name, value in zip(self.probe.dimensions, self.values, strict=True)
        ]


@dataclass(frozen=True, slots=True)
class Reading:
    """한 대상 × 한 지표의 결과.

    `points` = 돌아온 지점 수(**0 = 빈 배열 = 확인 불가**) / `nonzero` = 0 이 아닌 지점 수 /
    `value` = **창 전체를 `aggregate` 로 접은 값**.

    🔴 `points == 0` 과 `value == 0` 은 다른 사실이다 — 전자는 「못 쟀다」, 후자는 「쟀고 0
    이었다」다. 스키마의 `evidence.datapoints` 도 같은 구분을 요구한다(`null` vs `0`).
    """

    metric: str
    role: str
    aggregate: str
    points: int
    nonzero: int
    value: float | None
    reporting: str
    # 선언(`MetricRef.blocking`)을 그대로 들고 온다 — 판정은 이 값만 보고 「차단이라고 말할 자격」을
    # 정한다. 기본 False 는 **드롭이 아닌 역할**(통과량·상태·건강)에 해당한다.
    blocking: bool = False
    # 🔴 **0 이 아닌 첫·마지막 지점의 시각**(RFC3339 · 그 지점 구간의 **시작** = 1분, EC2 기본
    # 모니터링은 5분). 이력이 「확인한 시각」과 따로 **실제로 멈춘/시작한 분**을 적는 재료다(ADR 0008
    # D6 보강 · 사용자 결정 2026-09-27). 응답에 시각이 없거나 값과 짝이 안 맞으면 `None` — 시각을
    # 추정해 채우지 않는다(추정한 분은 사람이 콘솔에서 재현할 수 없다).
    first_nonzero_at: str | None = None
    last_nonzero_at: str | None = None
    # 선언(`MetricRef.arrival_only`)을 그대로 들고 온다 — `_fold` 가 「들어옴」과 「통과」를 가른다.
    arrival_only: bool = False
    # 🔴 **점은 0개인데 「쟀고 0」으로 본다**(`MetricRef.empty_is_zero` · 호출 성공 + `StatusCode: Complete` 일 때만 ·
    # `read_metrics`). `points` 는 그대로 0 이다 — 「AWS 가 보낸 점」은 지어내지 않는다.
    zero_filled: bool = False

    @property
    def measured(self) -> bool:
        return self.points > 0 or self.zero_filled

    @property
    def silence_is_expected(self) -> bool:
        """**빈 응답이 정상인** 지표인가 = 값이 0 이면 AWS 가 발행하지 않는다(ADR 0009 D3).

        🔴 이것이 「데이터포인트 0개」를 두 가지로 가른다. `always` 가 아니면 조용한 것이
        **트래픽이 없었다는 뜻일 수도 있고 우리가 못 봤다는 뜻일 수도 있어** 구별이 불가능하다 —
        그래서 화면은 회색 확정(흐름 없음) 대신 「확인 불가」로 말하고 사유를 적는다. `always`
        인데 조용하면 반대로 **우리 쪽을 의심해야 한다**(차원 조합이 틀리면 에러가 아니라 빈
        배열이 온다 · ADR 0006 §O7-1 함정 1).
        """
        return self.reporting != REPORT_ALWAYS


@dataclass(frozen=True, slots=True)
class Verdict:
    """한 대상의 2층 판정 재료. **등급 판정은 `observe` 가 하고, 여기서는 수를 접는다.**"""

    metric_names: tuple[str, ...]
    # 🔴 **물어본 통과량 지표 이름 전부**(못 쟀어도 남는다 · ADR 0009 D3). `flow_metric` 은 「쟀다」를
    # 뜻하므로 하나도 못 쟀을 때 `None` 이 되고, 그때 근거 이름을 `metric_names[0]` 으로 고르면
    # 이름순 첫 지표가 뽑혀 **건강·상태 계열의 지점 수가 「흐름 dp」로 적힐 수 있다.**
    flow_names: tuple[str, ...]
    flow_metric: str | None
    flow_value: float
    flow_points: int
    drop_metric: str | None
    drop_value: float
    # 🔴 **배지용 드롭과 차단용 드롭은 다른 칸이다**(ADR 0008 D3 · 실측 정정 5). 위 둘은 「드롭 N」
    # 배지에 쓰는 수 전부이고, 아래 둘은 **D3 표에 차단 신호로 적힌 지표만** 골라 담는다. 한 칸으로
    # 합치면 NAT 의 `PacketsDropCount` 가 다시 차단을 만들고, 갓 만든 NAT 이 15분 동안 빨개진다.
    block_drop_metric: str | None
    block_drop_value: float
    blocked_metric: str | None
    measured: bool
    # 🔴 **「들어옴만 센다」 지표의 최대값**(ADR 0008 정정 1-2). 통과를 증명하지 못했을 때만 뜻이
    # 있다 — 차단 사유에 「들어옴 N」을 적고, 차단도 흐름도 증명 못 하면 「판정 못 함」을 만든다.
    arrival_metric: str | None = None
    arrival_value: float = 0.0

    @property
    def flowing(self) -> bool:
        """창 안에 통과량이 있었다 = A 등급(또는 연역이면 B)."""
        return self.flow_metric is not None and self.flow_value > 0

    @property
    def blocked(self) -> bool:
        """🔴 **통과가 0 일 때만** 차단을 말한다(D3). 통과 > 0 + 드롭 > 0 은 `live` + 배지다.

        🔴 드롭은 **D3 표에 차단 신호로 적힌 것만** 센다(`block_drop_value`). 「드롭이 있었다」를
        그대로 차단으로 읽으면 NAT 은 **만든 지 15분 안에 전부 빨강**이다(랩 A1 정정 5).
        """
        if self.flowing:
            return False
        return self.blocked_metric is not None or self.block_drop_value > 0

    @property
    def arrival_unproven(self) -> bool:
        """🔴 **들어왔는데 건너갔는지 모른다**(ADR 0008 정정 1-2) — 흐름도 차단도 말할 수 없다.

        `BytesIn` > 0 · 나간 쪽 0 · 차단 드롭을 **못 쟀다**. 흐름으로 적으면 블랙홀이 초록이고,
        「흐름 없음」으로 적으면 들어온 트래픽을 없었다고 단정한다. 그래서 수치를 적지 않는다.
        """
        return not self.flowing and not self.blocked and self.arrival_value > 0

    @property
    def arrival_why(self) -> str | None:
        if not self.arrival_unproven:
            return None
        return (
            f"{self.arrival_metric} = {self.arrival_value:g} 로 들어왔지만 나간 통과량이 없고 "
            f"차단 드롭을 측정하지 못했습니다 — 건너갔는지 버려졌는지 알 수 없습니다 (창 {WINDOW_SECONDS}초)"
        )

    @property
    def drops(self) -> int | None:
        """`Observation.drops` 에 적을 「드롭 N」 — 🔴 **배지용 최대값**이고 차단용이 아니다.

        `drop_value`(모든 드롭 중 최대)를 쓴다. `block_drop_value` 를 쓰면 정상 NAT 의
        `PacketsDropCount` 49 가 화면에서 사라진다(배지 전용 지표는 `blocking=False` 다).

        🔴 `None` = 「못 쟀거나 이 자원에 드롭 지표가 없다」 → 행에 **키를 만들지 않는다**.
        「쟀고 0 이었다」는 `0` 으로 남는다(ADR 0007 「없음 vs 못 읽음」 · 스키마
        `observation.schema.json` `drops`). 드롭이 0 이 아니면 발행되지 않는 지표
        (`REPORT_NONZERO_ONLY`)는 조용할 때 `points == 0` 이므로 여기서도 `None` 이다 —
        그 창에 0 이었는지 우리가 못 봤는지 구별할 수 없다(ADR 0009 D3).

        카운터 합계라 정수지만 CloudWatch 는 float 로 주므로 `round` 한다(스키마 `integer`).
        """
        if self.drop_metric is None:
            return None
        return round(self.drop_value)

    @property
    def block_reason(self) -> str | None:
        if not self.blocked:
            return None
        if self.blocked_metric is not None:
            return f"{self.blocked_metric} = 0 (창 {WINDOW_SECONDS}초)"
        # 🔴 들어온 양을 같이 적는다 — 「통과 0 · 드롭 5」만으로는 한 방향 송신과 부분 블랙홀이
        # 겹친 경우(정정 1-2 의 남는 경우)를 사람이 가려낼 수 없다.
        arrived = (
            f"들어옴 {self.arrival_metric} = {self.arrival_value:g} · "
            if self.arrival_metric is not None and self.arrival_value > 0
            else ""
        )
        return (
            f"통과량 0 · {arrived}{self.block_drop_metric} = "
            f"{self.block_drop_value:g} (창 {WINDOW_SECONDS}초)"
        )

    @property
    def report_metric(self) -> str | None:
        """`evidence.metric` 에 적을 이름 — **판정을 만든** 지표. 없으면 물어본 첫 지표.

        🔴 순서가 `blocked` 를 먼저 본다. 옛 판은 `flow_metric is not None` 을 맨 앞에 뒀는데
        그 값은 「쟀다」만 뜻하므로(0 이어도 채워진다) **차단 판정에도 통과량 이름이 적혔다**.
        그러면 화면이 점선 + `!` 로 「막혔다」고 말하면서 근거로는 `BytesIn = 0 지점` 을 대고,
        사람이 콘솔에서 그 지표를 열면 「0 이네, 아무 일도 없었는데?」로 읽는다 — 재현할 수 없는
        빨강이다. `_report_points()` 의 주석이 이미 「드롭으로 말했으면 숫자도 드롭의 것」이라고
        적어 뒀지만, 이름을 고르는 여기서 어긋나 있었다(2026-09-25 `test_metrics` 가 실측).
        """
        if self.flowing:
            return self.flow_metric
        if self.blocked_metric is not None:
            return self.blocked_metric
        # 🔴 **배지용 드롭은 근거 이름이 될 수 없다** — 판정을 만든 것만 여기 적는다. NAT 은 통과량
        # 0 + `PacketsDropCount` 49 여도 차단이 아니므로, 그 이름을 적으면 화면이 「흐름 없음」 칸에
        # 드롭 지표를 근거로 대고 사람이 콘솔에서 재현할 수 없는 설명을 읽는다.
        if self.block_drop_metric is not None and self.block_drop_value > 0:
            return self.block_drop_metric
        # 「판정 못 함」의 근거는 **들어온 쪽**이다 — 나간 쪽 0 을 적으면 「아무 일도 없었다」로 읽힌다.
        if self.arrival_unproven:
            return self.arrival_metric
        if self.flow_metric is not None:
            return self.flow_metric
        # 🔴 통과량을 **하나도 못 쟀을 때도 이름은 통과량의 것**이어야 한다. 그러지 않으면 사람이
        # 「흐름 없음」이라는 칸에서 `HealthyHostCount` 같은 이름을 보고 콘솔을 열게 되고, 우리가
        # 무엇을 물었는지도 드러나지 않는다(ADR 0009 D3).
        if self.flow_names:
            return self.flow_names[0]
        return self.metric_names[0] if self.metric_names else None


def _fold(readings: dict[str, Reading], *, complete: bool = True) -> Verdict:
    """지표별 결과 → 한 대상의 판정 재료. 🔴 정렬로 결정론을 만든다(불변식 ②).

    고르는 규칙: 통과량은 **값이 가장 큰 지표**, 동률이면 이름이 앞선 지표. 드롭도 같다.
    상태·건강은 **0 을 말한 지표 중 이름이 앞선 것**(여럿이면 어느 것이든 차단이므로).
    드롭은 **두 번 고른다** — 화면에 「드롭 N」으로 적을 최대값(`drop_*`)과 차단 판정에 쓸
    `blocking=True` 중 최대값(`block_drop_*`). 둘을 합치면 정상 NAT 이 「막혔다」가 된다.

    🔴 **「들어옴만 센다」 지표(`arrival_only`)는 통과량 후보가 아니다**(ADR 0008 정정 1-2). 그
    값은 나간 쪽이 0 이고 차단 드롭을 **전부 재서** 0 일 때만 흐름의 근거가 된다(한 방향 송신).
    `complete` = 보낸 계열이 전부 돌아왔다. 차단 드롭 계열이 응답에서 빠졌으면 「재서 0」이 아니다.
    """
    names = tuple(sorted(readings))
    measured_flow = [r for r in readings.values() if r.role == ROLE_FLOW and r.measured]
    flow = [r for r in measured_flow if not r.arrival_only]
    arrivals = [r for r in measured_flow if r.arrival_only]
    drops = [r for r in readings.values() if r.role == ROLE_DROP and r.measured]
    blocked = [
        r
        for r in readings.values()
        if r.role in (ROLE_STATE, ROLE_HEALTH) and r.measured and (r.value or 0) <= 0
    ]
    best_flow = max(
        flow, key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]), default=None
    )
    best_drop = max(
        drops, key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]), default=None
    )
    # 🔴 **차단 신호로 쓸 드롭은 따로 고른다** — `blocking` 선언이 참인 것만(ADR 0008 D3 표).
    # 같은 자원에서 가장 큰 드롭이 배지 전용일 수 있다(NAT: `PacketsDropCount` 49 vs
    # `ErrorPortAllocation` 0) → 전체 최대값으로 차단을 판단하면 정상 NAT 이 「막혔다」가 된다.
    best_block_drop = max(
        (r for r in drops if r.blocking),
        key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]),
        default=None,
    )
    first_block = min(blocked, key=lambda r: r.metric, default=None)
    best_arrival = max(
        arrivals,
        key=lambda r: (r.value or 0.0, [-ord(c) for c in r.metric]),
        default=None,
    )
    arrival_value = float(best_arrival.value or 0.0) if best_arrival else 0.0
    blocking = [r for r in readings.values() if r.role == ROLE_DROP and r.blocking]
    # 🔴 「차단 드롭이 0」은 **선언된 차단 드롭을 전부 재서** 0 일 때만이다. 하나라도 빈 배열이면
    # 그 이유로 버려졌는지 모른다 — 그때 들어온 양을 흐름으로 적으면 블랙홀이 초록이 된다.
    block_drops_zero = (
        complete
        and bool(blocking)
        and all(r.measured and (r.value or 0.0) <= 0 for r in blocking)
    )
    crossed = best_flow is not None and (best_flow.value or 0.0) > 0
    if not crossed and arrival_value > 0 and block_drops_zero:
        best_flow = best_arrival
    return Verdict(
        metric_names=names,
        flow_names=tuple(
            sorted(r.metric for r in readings.values() if r.role == ROLE_FLOW)
        ),
        flow_metric=best_flow.metric if best_flow else None,
        flow_value=float(best_flow.value or 0.0) if best_flow else 0.0,
        flow_points=best_flow.nonzero if best_flow else 0,
        drop_metric=best_drop.metric if best_drop else None,
        drop_value=float(best_drop.value or 0.0) if best_drop else 0.0,
        block_drop_metric=best_block_drop.metric if best_block_drop else None,
        block_drop_value=(
            float(best_block_drop.value or 0.0) if best_block_drop else 0.0
        ),
        blocked_metric=first_block.metric if first_block else None,
        measured=any(r.measured for r in readings.values()),
        arrival_metric=best_arrival.metric if best_arrival else None,
        arrival_value=arrival_value,
    )


@dataclass(frozen=True, slots=True)
class MetricLayer:
    """2층의 결과 전부. `observe.build_observation` 이 이것만 보고 판정을 올린다."""

    enabled: bool
    window_seconds: int | None
    readings: dict[tuple[str, str], Reading]
    # 🔴 **물어본 대상 id 전부.** 「지표가 없는 종류」와 「물었는데 응답에 없다(개발 결함)」를
    # 가르는 유일한 수단이다 — `readings` 만 보면 둘이 같은 「빈 dict」다.
    planned: frozenset[str]
    # (계정, 리전, 종류) → (coverage status, 사유). 성공한 칸은 여기 없다(기본값 `ok`).
    failures: dict[tuple[str, str, str], tuple[str, Reason]]
    # 쿼리를 **보낸** 칸. 지표가 없는 종류와 구별해야 「0 dp 결함」을 셀 수 있다.
    queried: frozenset[tuple[str, str, str]]
    # 대상 id → 차원을 못 만든 사유. 🔴 「지표가 없다」가 아니라 **개발 결함**이므로 관측에
    # `partial` + 사유로 남는다(조용히 건너뛰면 「흐름 없음」과 구별되지 않는다).
    unbuildable: dict[str, str]
    # 대상 id → 그 지표를 실제로 잰 대상(≠ 대상이면 B 등급). 없으면 직접 측정이다.
    observed_on: dict[str, str]
    # 🔴 **보낸 쿼리 중 응답에 안 돌아온 것이 있던 대상.** 대상의 지표가 여러 개가 된 뒤(ADR 0008
    # D2) 한 계열만 빠지면 나머지로 판정이 서기 때문에 `verdict()` 가 `None` 이 되지 않는다 —
    # 즉 **판정은 나오는데 근거가 덜 온 상태**가 조용히 지나간다. `_MISSING_RESULT` 주석이 약속한
    # 「조용히 「지표 없음」으로 두면 사라진다」를 지키려면 이 집합이 필요하다.
    incomplete: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()

    @classmethod
    def off(cls) -> MetricLayer:
        """2층을 안 켠 상태. 🔴 **「막혔다」가 아니라 「안 켰다」다**(coverage `skipped`)."""
        return cls(
            enabled=False,
            window_seconds=None,
            readings={},
            planned=frozenset(),
            failures={},
            queried=frozenset(),
            unbuildable={},
            observed_on={},
        )

    def cell(self, account_id: str, region: str, resource_type: str) -> tuple[str, Any]:
        """그 칸의 2층 coverage (status, reason). 세 이유가 여기서 갈린다."""
        if not self.enabled:
            return "skipped", None
        failed = self.failures.get((account_id, region, resource_type))
        if failed is not None:
            return failed[0], failed[1].to_dict()
        return "ok", None

    def readings_for(self, target_id: str) -> dict[str, Reading]:
        return {
            metric: reading
            for (tid, metric), reading in self.readings.items()
            if tid == target_id
        }

    def verdict(self, target_id: str) -> Verdict | None:
        """그 대상의 판정 재료. 쿼리를 보내지 않았으면 `None`(= 지표가 없는 종류)."""
        readings = self.readings_for(target_id)
        if not readings:
            return None
        return _fold(readings, complete=target_id not in self.incomplete)

    def answered(self) -> frozenset[str]:
        """지표가 **답을 준** 대상 id. coverage 의 2층 `item_count` 가 이것을 센다."""
        return frozenset(
            tid for (tid, _metric), r in self.readings.items() if r.measured
        )


def probe_for(node: Any) -> Probe | None:
    """그 노드를 2층에서 읽을 방법. 없으면 `None`(= 이 종류·이 모양엔 지표가 없다)."""
    attributes = node.attributes or {}
    if node.type == "load_balancer":
        return NODE_PROBES.get(f"load_balancer:{_lb_kind(node)}")
    if node.type == "vpc_endpoint":
        if str(attributes.get("endpoint_type") or "").casefold() != _INTERFACE:
            return None  # Gateway 형은 지표가 없다(§O7)
        return _endpoint_probe(node, "vpc_endpoint")
    return NODE_PROBES.get(node.type)


def _endpoint_probe(endpoint: Any, key: str) -> Probe:
    """엔드포인트(노드)·그 서브넷 구간의 프로브 — **수락됐을 때만** 빈 응답을 「쟀고 0」으로 읽는 판.

    🔴 상태가 `available` 이 아니면(수락 대기 · 만드는 중 · 거절 · 실패 · 값 없음) 기본 판이다 → 빈 응답 = 확인 불가 +
    「구별할 수 없다」 사유 · 구간 행 없음. 비교는 casefold — 서비스 파일은 AWS 값을 그대로 담는다(`observe._rule` 과 같다).
    """
    state = str((endpoint.attributes or {}).get("state") or "").casefold()
    if state == _ENDPOINT_ACCEPTED:
        return ACCEPTED_ENDPOINT_PROBES[key]
    return NODE_PROBES[key] if key in NODE_PROBES else EDGE_PROBES[key]


def _lb_kind(node: Any) -> str:
    return str((node.attributes or {}).get("load_balancer_type") or "").casefold()


def _lb_dimension(node: Any) -> str | None:
    """🔴 ARN 이 아니라 `app/<name>/<hash>`·`net/<name>/<hash>` 다(§O7-1)."""
    arn = node.arn or ""
    marker = ":loadbalancer/"
    index = arn.find(marker)
    if index < 0:
        return None
    return arn[index + len(marker) :] or None


def _vpc_of(node: Any) -> str | None:
    """엔드포인트의 `VPC Id`. 구조 수집기는 부모 노드 id 로만 들고 있다."""
    parent = node.parent_id or ""
    return parent.rsplit("/", 1)[-1] if parent.startswith("vpc/") else None


def _endpoint_values(node: Any) -> tuple[str | None, ...]:
    attributes = node.attributes or {}
    return (
        _vpc_of(node),
        node.native_id,
        attributes.get("endpoint_type"),
        attributes.get("service_name"),
    )


def dimension_values(node: Any, probe: Probe) -> tuple[str, ...] | None:
    """노드의 차원 값을 1층 응답에서 만든다. 하나라도 비면 `None`.

    🔴 **조용히 빈 값으로 부르지 않는다.** 빈 값으로 부르면 §O7-1 함정 1 그대로 `dp=0` +
    `Complete` 가 와서 「흐름 없음」과 바이트가 같아진다. 만들 수 없으면 만들 수 없다고 말해야
    사람이 개발을 부른다.
    """
    attributes = node.attributes or {}
    if node.type == "nat_gateway" or node.type == "transit_gateway":
        values: tuple[str | None, ...] = (node.native_id,)
    elif node.type == "transit_gateway_attachment":
        values = (attributes.get("transit_gateway_id"), node.native_id)
    elif node.type == "vpc_endpoint":
        values = _endpoint_values(node)
    elif node.type == "load_balancer":
        values = (_lb_dimension(node),)
    else:
        return None
    return _checked(values, probe)


def _checked(values: tuple[Any, ...], probe: Probe) -> tuple[str, ...] | None:
    if len(values) != len(probe.dimensions) or any(not v for v in values):
        return None
    return tuple(str(v) for v in values)


def edge_probe_for(edge: Any, nodes: dict[str, Any]) -> Probe | None:
    """그 **구간**을 2층에서 읽을 방법. 🔴 자격은 ADR 0008 D8-1 = 구간을 특정하는 차원 조합."""
    if edge.type == "attachment":
        source = nodes.get(edge.source)
        if source is None or source.type != "transit_gateway_attachment":
            return None
        return EDGE_PROBES["attachment"]
    if edge.type == "endpoint_connection":
        source = nodes.get(edge.source)
        if source is None or probe_for(source) is None:
            return None  # Gateway 엔드포인트 = 지표 없음
        return _endpoint_probe(source, "endpoint_connection")
    if edge.type == "load_balancer_placement":
        source = nodes.get(edge.source)
        if source is None or source.type != "load_balancer":
            return None
        return EDGE_PROBES.get(f"load_balancer_placement:{_lb_kind(source)}")
    return None


def edge_dimension_values(
    edge: Any, nodes: dict[str, Any], probe: Probe
) -> tuple[str, ...] | None:
    """구간의 차원 값 — **양 끝 노드에서** 만든다. 하나라도 비면 `None`(위 🔴 와 같은 이유)."""
    source = nodes.get(edge.source)
    target = nodes.get(edge.target)
    if source is None:
        return None
    if edge.type == "attachment":
        own = (source.attributes or {}).get("transit_gateway_id")
        values: tuple[Any, ...] = (
            target.native_id if _far_side(edge, nodes) else own,
            source.native_id,
        )
    elif edge.type == "endpoint_connection":
        values = (*_endpoint_values(source), target.native_id if target else None)
    elif edge.type == "load_balancer_placement":
        zone = (target.attributes or {}).get("availability_zone") if target else None
        values = (_lb_dimension(source), zone)
    else:
        return None
    return _checked(values, probe)


def lands_on_another_tgw(
    edge_type: str, own_tgw: Any, target_type: Any, target_native: Any
) -> bool:
    """어태치먼트 선이 **어태치먼트 자기 TGW 가 아닌** TGW 에 닿는가 = 병합된 피어링의 상대편 선.

    🔴 그 선은 **상대편 뷰**(상대 계정·리전의 응답)에서 왔고, 병합이 출발만 승자 카드로 옮겼다
    (`collect._merge_views`). 그 구간의 지표는 **상대편 TGW** 에 실린다 — 출발 카드의 자리·TGW 로
    물으면 우리 쪽 구간의 값을 받아 「직접 측정」으로 칠하고, 상대편이 버린 패킷은 영영 안 보인다
    (#31 리뷰 ①, 2026-09-28). 그래서 이 선의 자리(계정·리전)와 TGW 차원은 **도착 TGW** 에서 뽑는다.
    `observe` 의 자리 표도 이 함수를 쓴다 — 갈라지면 상대편 실패를 엉뚱한 칸에서 찾아 「막혔는데
    조용하다」가 된다.
    """
    return (
        edge_type == "attachment"
        and target_type == "transit_gateway"
        and bool(own_tgw)
        and target_native != own_tgw
    )


def _far_side(edge: Any, nodes: dict[str, Any]) -> bool:
    source = nodes.get(edge.source)
    target = nodes.get(edge.target)
    if source is None or target is None:
        return False
    own = (source.attributes or {}).get("transit_gateway_id")
    return lands_on_another_tgw(edge.type, own, target.type, target.native_id)


def _resource_of_edge(edge: Any, nodes: dict[str, Any]) -> str | None:
    """엣지의 coverage 칸 = **그 엣지를 만든 응답의 종류**(ADR 0006 O10) = source 노드의 종류.

    여기 있는 세 엣지는 전부 source 가 자기를 만든 자원이다(어태치먼트·엔드포인트·LB).
    """
    source = nodes.get(edge.source)
    return source.type if source is not None else None


def plan(snapshot: Snapshot) -> tuple[list[MetricQuery], list[tuple[str, str]]]:
    """스냅샷 → (보낼 쿼리, 차원을 못 만든 대상 [(id, 사유)]).

    🔴 **노드와 엣지를 둘 다 순회한다.** 옛 판은 노드만 돌았고(`plan` 의 for 문 하나), 그래서
    실계정 445행 중 **2층 근거를 가진 엣지 행이 0** 이었다(ADR 0008 맥락 표). 선이 2층을 받을
    통로 자체가 없었던 것이고, 렌더러가 그 빈자리를 종단 추론으로 메웠다.

    정렬은 **노드 묶음 → 엣지 묶음, 각각 `target_id` 순**으로 고정한다(불변식 ②). 응답 id 대응은
    이 순서가 아니라 `_expand()` 가 계열을 접고 다시 정렬한 결과에 달려 있다.
    """
    queries: list[MetricQuery] = []
    unbuildable: list[tuple[str, str]] = []
    nodes = {n.id: n for n in snapshot.nodes}

    for node in sorted(snapshot.nodes, key=lambda n: n.id):
        probe = probe_for(node)
        if probe is None or node.region is None:
            continue
        values = dimension_values(node, probe)
        if values is None:
            unbuildable.append((node.id, _unbuildable_why(probe)))
            continue
        queries.append(
            MetricQuery(
                target_id=node.id,
                target_kind="node",
                account_id=node.account_id,
                region=node.region,
                resource_type=node.type,
                probe=probe,
                values=values,
            )
        )

    for edge in sorted(snapshot.edges, key=lambda e: e.id):
        probe = edge_probe_for(edge, nodes)
        if probe is None:
            continue
        # 🔴 자리 = 그 선의 지표가 실린 곳. 보통 출발 카드, 병합된 피어링의 상대편 선만 도착 TGW.
        at = nodes.get(edge.target if _far_side(edge, nodes) else edge.source)
        resource_type = _resource_of_edge(edge, nodes)
        if at is None or at.region is None or resource_type is None:
            continue
        values = edge_dimension_values(edge, nodes, probe)
        if values is None:
            unbuildable.append((edge.id, _unbuildable_why(probe)))
            continue
        queries.append(
            MetricQuery(
                target_id=edge.id,
                target_kind="edge",
                account_id=at.account_id,
                region=at.region,
                resource_type=resource_type,
                probe=probe,
                values=values,
                observed_on=_deduced_from(edge, nodes),
            )
        )
    return queries, unbuildable


def _deduced_from(edge: Any, nodes: dict[str, Any]) -> str | None:
    """이 구간이 **B(연역)** 이면 「실제로 잰 대상」의 id, A(직접 측정)면 `None`.

    🔴 어태치먼트는 선이 **둘**이다 — 어태치먼트↔TGW 와 어태치먼트↔VPC. 지표의 차원
    (`TransitGateway` + `TransitGatewayAttachment`)이 이름 대는 것은 **앞의 선**이므로 그쪽이
    A 다. 뒤의 선은 「그 어태치먼트를 지난 바이트는 그 VPC 경계를 넘었다」는 연역이고(ADR 0008
    D4 계열), 그 사실을 **데이터에 적어야** 렌더러가 배지로 말할 수 있다(불변식 ③).

    같은 값을 두 선에 복사하면서 아무 표시도 남기지 않는 것이 옛 종단 추론이 한 일이다.
    """
    target = nodes.get(edge.target)
    if edge.type == "attachment" and target is not None and target.type == "vpc":
        return edge.source
    return None


def _unbuildable_why(probe: Probe) -> str:
    return (
        f"2층 차원({' · '.join(probe.dimensions)})을 1층 응답에서 만들 수 없습니다 "
        "— 구조 수집기가 그 값을 담지 않았습니다(개발)"
    )


def _stamp(value: Any) -> datetime | None:
    """`Timestamps` 원소 → UTC. boto3 는 `datetime` 을 주고, 기록된 응답은 문자열일 수 있다.

    🔴 읽을 수 없으면 `None` 이다 — 예외를 올리면 **판정까지** 죽는다(이 시각은 판정 재료가 아니다 ·
    불변식 ④). 시간대 없는 시각도 `None` 이다: UTC 로 가정하면 로컬 시각일 때 9시간이 밀린다.
    """
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def _nonzero_span(
    numbers: list[float], timestamps: list[Any] | None
) -> tuple[str | None, str | None]:
    """0 이 아닌 지점의 (가장 이른, 가장 늦은) 시각. 🔴 **정렬 방향에 기대지 않는다** — 최소·최대를 잰다."""
    if timestamps is None or len(timestamps) != len(numbers):
        # 짝이 안 맞으면 어느 값이 어느 분인지 모른다 — 고르지 않고 비운다.
        return None, None
    stamped = [_stamp(t) for t, v in zip(timestamps, numbers, strict=True) if v != 0]
    moments = [m for m in stamped if m is not None]
    if not moments or len(moments) != len(stamped):
        return None, None
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return min(moments).strftime(fmt), max(moments).strftime(fmt)


def read_series(
    values: list[Any], ref: MetricRef, timestamps: list[Any] | None = None
) -> Reading:
    """CloudWatch 의 값 배열 → 판정 재료. **창 전체를 `ref.aggregate` 로 접는다**(ADR 0008 D6).

    🔴 CloudWatch 는 **표본이 없는 구간을 아예 돌려주지 않는다** — 그래서 배열 길이가 곧
    「몇 칸을 쟀나」이고, 빈 배열은 「못 쟀다」다. 창을 접으므로 정렬 방향에 기대지 않는다
    (옛 판은 `Values[0]` 을 읽어 `ScanBy` 에 기댔다).

    `timestamps` 는 `Values` 와 **같은 길이·같은 순서**의 짝(`GetMetricData` 의 `Timestamps`)이다.
    판정에는 쓰지 않고 **0 이 아닌 첫·마지막 분**만 뽑는다(이력의 「실제로 멈춘/시작한 분」).
    """
    numbers = [float(v) for v in values]
    first_at, last_at = _nonzero_span(numbers, timestamps)
    if not numbers:
        folded: float | None = None
    elif ref.aggregate == AGG_SUM:
        folded = sum(numbers)
    elif ref.aggregate == AGG_MIN:
        folded = min(numbers)
    else:
        folded = max(numbers)
    return Reading(
        metric=ref.name,
        role=ref.role,
        aggregate=ref.aggregate,
        points=len(numbers),
        nonzero=sum(1 for v in numbers if v != 0),
        value=folded,
        reporting=ref.reporting,
        blocking=ref.blocking,
        first_nonzero_at=first_at,
        last_nonzero_at=last_at,
        arrival_only=ref.arrival_only,
    )


def _window(at: str) -> tuple[datetime, datetime]:
    end = datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc)
    return end - timedelta(seconds=WINDOW_SECONDS), end


@dataclass(frozen=True, slots=True)
class _Slot:
    """`GetMetricData` 쿼리 1개 = **계열 1개**. `targets` 는 그 계열이 답을 주는 대상 전부다."""

    namespace: str
    ref: MetricRef
    dimensions: tuple[tuple[str, str], ...]
    targets: tuple[MetricQuery, ...]

    def as_dimensions(self) -> list[dict[str, str]]:
        return [{"Name": name, "Value": value} for name, value in self.dimensions]


def _expand(queries: list[MetricQuery]) -> list[_Slot]:
    """대상 × 지표로 펼치되 **같은 계열은 한 번만 산다**.

    🔴 요금은 「요청한 지표 수」로 매겨진다(1,000개당 $0.01) — 같은
    (namespace · 지표 · 통계 · 차원)을 두 번 보내면 두 번 낸다. 어태치먼트는 한 계열이 **세
    대상**의 답이다(어태치먼트 노드 · TGW↔어태치먼트 선 · 어태치먼트↔VPC 선) — 접지 않으면
    어태치먼트 하나당 4개가 아니라 12개를 산다.

    🔴 접는 것은 **요청**이고 판정은 대상별로 그대로 남는다 — 같은 계열을 가리키는 대상들은
    같은 `Reading` 을 받고, 그 중 하나(어태치먼트↔VPC)는 `observed_on` 으로 「연역」이라고
    적힌다. 여기서 대상을 합쳐 버리면 그 구분이 사라진다.
    """
    grouped: dict[tuple[Any, ...], list[MetricQuery]] = {}
    for query in queries:
        for ref in query.probe.metrics:
            key = (
                query.probe.namespace,
                ref,
                tuple(zip(query.probe.dimensions, query.values, strict=True)),
            )
            grouped.setdefault(key, []).append(query)
    # 정렬로 결정론을 만든다(불변식 ②) — 쿼리 순서가 응답 id 에 들어간다.
    return [
        _Slot(namespace, ref, dimensions, tuple(targets))
        for (namespace, ref, dimensions), targets in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1].name, item[0][2])
        )
    ]


def _batches(slots: list[_Slot]) -> list[list[_Slot]]:
    return [
        slots[i : i + QUERIES_PER_CALL] for i in range(0, len(slots), QUERIES_PER_CALL)
    ]


def read_metrics(
    snapshot: Snapshot,
    client_factory: Any,
    *,
    at: str,
) -> MetricLayer:
    """2층을 읽는다. `client_factory(account_id, region, "cloudwatch")` 만 쓴다.

    실패는 **(계정,리전) 단위**로 온다 — `GetMetricData` 는 한 번에 여러 종류를 물으므로 권한이
    없으면 그 호출에 들어간 종류가 **전부** 같이 죽는다. 그것이 「2층은 층 단위로 죽는다」의
    실제 모양이다(`observe._describe_by_type` 의 주석과 짝).
    """
    queries, unbuildable = plan(snapshot)
    start, end = _window(at)
    readings: dict[tuple[str, str], Reading] = {}
    failures: dict[tuple[str, str, str], tuple[str, Reason]] = {}
    queried = {(q.account_id, q.region, q.resource_type) for q in queries}
    observed_on = {
        q.target_id: q.observed_on for q in queries if q.observed_on is not None
    }
    warnings = [f"{target}: {why}" for target, why in unbuildable]
    incomplete: set[str] = set()

    by_place: dict[tuple[str, str], list[MetricQuery]] = {}
    for query in queries:
        by_place.setdefault((query.account_id, query.region), []).append(query)

    for (account_id, region), group in sorted(by_place.items()):
        try:
            client = client_factory(account_id, region, "cloudwatch")
        except Exception as exc:  # noqa: BLE001 — 자격증명 실패도 「층이 죽었다」다
            # 🔴 **여기서 실패한 API 는 `GetMetricData` 가 아니다.** 이 자리가 부르는 것은
            # 크로스계정 진입(`sts:AssumeRole`)이거나 자격증명 해석이고, 그 실패를
            # `cloudwatch:GetMetricData` 로 적으면 사람을 **다른 계정의 다른 정책**으로 보낸다.
            # 실측 2026-09-24(허브→멤버 실계정): 멤버 역할에 `GetMetricData` 를 넣어 배포한 **뒤에도**
            # 화면은 「확인 불가 · cloudwatch:GetMetricData」라고 말했다. 진짜 원인은 허브 쪽 2층
            # 자격증명에 `sts:AssumeRole` 이 없는 것이었다 — 대상 계정 정책을 아무리 고쳐도 화면이
            # 바뀌지 않는다. 1층은 이 함정을 이미 닫아 뒀다(`collect.py` 의 같은 자리, 실측
            # 2026-09-14 · 브레이크 `팩토리라벨고정`) — **2층만 열려 있었다.**
            operation = getattr(exc, "operation_name", None)
            api = f"sts:{operation}" if operation else "nettopo:client_factory"
            status, reason = classify(exc, api)
            for cell in {(account_id, region, q.resource_type) for q in group}:
                failures[cell] = (status, reason)
            continue
        for batch in _batches(_expand(group)):
            ids = {f"m{index}": slot for index, slot in enumerate(batch)}
            try:
                response = client.get_metric_data(
                    MetricDataQueries=[
                        {
                            "Id": key,
                            "MetricStat": {
                                "Metric": {
                                    "Namespace": slot.namespace,
                                    "MetricName": slot.ref.name,
                                    "Dimensions": slot.as_dimensions(),
                                },
                                "Period": PERIOD_SECONDS,
                                "Stat": slot.ref.statistic,
                            },
                            "ReturnData": True,
                        }
                        for key, slot in ids.items()
                    ],
                    StartTime=start,
                    EndTime=end,
                    # 판정은 창 전체를 접으므로 정렬에 기대지 않는다. 그래도 고정해 둔다 —
                    # 같은 응답을 같은 순서로 받는 것이 결정론 디버깅을 쉽게 한다(불변식 ②).
                    ScanBy="TimestampDescending",
                )
            except Exception as exc:  # noqa: BLE001 — ClientError·BotoCoreError 둘 다
                status, reason = classify(exc, METRIC_API)
                for cell in {
                    (account_id, region, q.resource_type)
                    for slot in batch
                    for q in slot.targets
                }:
                    failures[cell] = (status, reason)
                continue
            returned: set[str] = set()
            for result in response.get("MetricDataResults") or []:
                key = str(result.get("Id"))
                slot = ids.get(key)
                if slot is None:
                    continue
                returned.add(key)
                reading = read_series(
                    list(result.get("Values") or []),
                    slot.ref,
                    list(result.get("Timestamps") or []),
                )
                if (
                    slot.ref.empty_is_zero
                    and reading.points == 0
                    and result.get("StatusCode") == "Complete"
                ):
                    # 성공한 빈 응답 = 쟀고 0(「빈 응답 해석」 표). 🔴 `Complete` 일 때만 — `Forbidden`·
                    # `InternalError` 의 빈 배열은 못 읽은 것이다(0 으로 적으면 거부가 「흐름 없음」이 된다).
                    reading = replace(reading, value=0.0, zero_filled=True)
                # 🔴 한 계열이 여러 대상의 답이다(`_expand` 참고) — 같은 `Reading` 을 대상마다
                # 적는다. 대상 하나만 적으면 나머지는 「물었는데 응답에 없다」(개발 결함)가 된다.
                for query in slot.targets:
                    readings[(query.target_id, slot.ref.name)] = reading
            # 🔴 보냈는데 안 돌아온 쿼리. 대상이 지표를 여러 개 갖게 된 뒤로는 **한 계열만 빠지면
            # 판정이 남은 계열로 서므로** `verdict()` 가 `None` 이 되지 않는다 — 조용히 근거가
            # 얇아지고 화면은 똑같다. 그래서 대상별로 표시해 행의 사유로 올린다.
            for key, slot in ids.items():
                if key in returned:
                    continue
                for query in slot.targets:
                    incomplete.add(query.target_id)
                    warnings.append(
                        f"{query.target_id}: {slot.ref.name} 쿼리를 보냈지만 응답에 없다(개발)"
                    )

    # 🔴 §O7-1 함정 1+3 — **차원을 틀리면 에러가 아니라 「빈 결과」가 온다.** 대상이 있는데 어느
    # 쿼리도 지점을 하나도 못 받으면 「전부 조용하다」와 「차원이 틀렸다」가 바이트 단위로 같다.
    # 전자로 읽어 통과시키면 이 층은 **실패할 수 없는 검사**가 된다(검증 규칙) → 결함으로 본다.
    targets = {tid for tid, _metric in readings}
    # 🔴 **실제로 받은 점**만 센다 — 「빈 응답 = 0」으로 본 계열(`zero_filled`)을 답으로 세면 차원이 전부 틀린
    # 날에도 ALB 하나가 이 결함 신고를 끈다.
    answered = {tid for (tid, _metric), r in readings.items() if r.points > 0}
    if targets and not answered:
        reason = Reason(
            code="api_error",
            api=METRIC_API,
            message=(
                f"2층 대상 {len(targets)}개 전부 지점 0개다 — 차원 이름·값이 틀렸을 때와 "
                "「정말 아무 흐름도 없다」가 같은 응답으로 온다(ADR 0006 §O7-1 함정 1+3). "
                "결함으로 본다"
            ),
        )
        for cell in queried:
            failures.setdefault(cell, ("error", reason))
        warnings.append(
            f"2층 대상 {len(targets)}개 전부 지점 0개 — 차원 상수를 확인하라(§O7-1)"
        )

    return MetricLayer(
        enabled=True,
        window_seconds=WINDOW_SECONDS,
        readings=readings,
        planned=frozenset(q.target_id for q in queries),
        failures=failures,
        queried=frozenset(queried),
        unbuildable=dict(unbuildable),
        observed_on=observed_on,
        incomplete=frozenset(incomplete),
        warnings=tuple(warnings),
    )
