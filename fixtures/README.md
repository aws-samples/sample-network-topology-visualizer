# fixtures — 스크럽된 스냅샷

계약이 둘이므로 디렉터리도 둘이다 — **여기(`fixtures/*.json`)는 구조**
(`schema/topology.schema.json`), **`fixtures/observation/` 는 관측**
(`schema/observation.schema.json`, ADR 0006).

`schema/tests/test_schema.py`가 이 디렉터리의 `*.json`을 **전부 topology 스키마로** 검증하므로
(비재귀 glob), 여기에 파일을 추가하면 자동으로 게이트에 걸린다. 🔴 **그래서 관측 픽스처를 여기
옆에 두면 안 된다** — topology 스키마로 검증돼 FAIL 한다. 하위 디렉터리인 것이 그 이유다
(`test_observation_fixtures_are_outside_the_topology_glob` 이 이 사실을 고정한다).

**스냅샷은 순수한 인스턴스다.** 설명을 `$comment` 같은 키로 파일 안에 넣지 마라 — 루트가
`additionalProperties: false`라서 검증에 실패하고, 무엇보다 계약에 없는 키를 fixture 가 먼저
쓰기 시작하면 계약이 무너진다. 설명은 이 파일에 쓴다.

| 파일 | 무엇의 대조군인가 |
|---|---|
| `minimal.json` | 정상 경로. 1계정·1리전·VPC/서브넷 2개/IGW/라우트테이블/**NACL**, coverage 전부 `ok`. 렌더러의 기본 회귀 입력 |
| `partial-failure.json` | **불변식 ④.** 두 번째 계정(`444455556666`)은 통째로 `denied`(대상 계정에 `NetworkReadOnlyRole` 미배포), TGW 첨부는 `pagination_truncated`. 렌더러가 "확인하지 못한 영역"을 눈에 보이게 그리는지 여기서 검증한다 — 조용히 빈 그래프를 그리면 실패해야 한다 |
| `peering.json` | **피어링 + 메인 테이블.** VPC 2개가 `pcx-` 하나로 이어졌다. 앱 VPC 의 서브넷 하나는 라우트테이블을 **명시 연관**으로, 다른 하나는 **메인 테이블**(연관 선 없음 — `route_table.py:226`)로 쓴다. 두 테이블 모두 그 `pcx-` 로 가는 라우트를 든다. 흐름 로그 서브넷 근거가 「그 테이블을 실제로 쓰는 서브넷」인지를 두 경로 모두로 재는 입력이다(ADR 0008 「D4b 개정」) |
| `elb-followup-denied.json` | **불변식 ④ 의 리소스 단위 — coverage 는 `ok` 인데 노드가 `partial` 인 유일한 픽스처.** LB 2개 중 `example-nlb` 만 후속 호출(`DescribeListeners`)이 거부돼 `listeners` **키가 없고**(≠ `[]`) `partial_reasons` 를 든다. `example-alb` 는 전부 읽은 **살아 있는 짝**이다. 목록 호출은 성공했으므로 coverage 는 `ok`/2 — 여기를 `partial` 로 떨어뜨리면 「LB 목록을 못 읽었다」는 더 큰 거짓이 된다(ADR 0007 결정 2) |
| `forwarding.json` | **넘김 선(스키마 2.8.0 · ADR 0002 §9 · P1-3).** 바퀴살 VPC(앱 서브넷 기본 라우트 → GWLB 엔드포인트) · 검사 VPC(GWLB → 어플라이언스 EC2) · 엔드포인트 서비스 구성(서비스 → GWLB) · 제공자를 수집하지 않은 GWLB 엔드포인트 1(`unresolved_refs`) · **P1-4** Network Firewall `spoke-fw`(웹 서브넷 기본 라우트 → 방화벽 엔드포인트 → 방화벽 · `resolved_by` = `firewall_endpoint`). 🔴 **손으로 고치지 않는다** — `engine/tests/fwd_world.py` 를 엔진에 돌린 바이트 그대로이고 `test_forwarding.py::test_the_fixture_is_this_world_collected` 가 대조한다(다시 쓰기 = `NETTOPO_REGEN_FIXTURE=1`). 렌더러 검사 149 의 입력 |
| `lb-targets.json` | **LB → 대상 선(스키마 2.7.0 · ADR 0002 §8).** 대상 모양 전부를 한 VPC 에: instance(같은 서브넷 EC2 6대 = 화면 묶음 `EC2 ×6` · 선 하나) · alb(NLB → ALB) · lambda(엔진이 짓는 `lambda_function`) · ip → ECS 서비스(태스크 IP 2 = 선 하나 · 등록 2) · ip → VPC 엔드포인트 · RDS · 내부 ALB 의 ENI · EC2 부착 ENI · AZ `all` 외부 IP 3(`external_ip` · 출구 = VGW 2 · TGW 1) · 못 이은 대상 3(수집 안 한 인스턴스 · 주인 없는 IP · EFS 마운트 대상 — `unresolved_targets`) · 🆕 2.9.0(#114) ip → **ElastiCache 캐시 클러스터 카드**(그 전엔 못 이은 대상이었다) · ip → **Aurora 클러스터 카드**(같은 AZ 의 멤버 둘 — 인스턴스는 못 고르지만 클러스터는 안다) · ip → **OpenSearch 도메인 카드**(`ES <도메인>` 설명) · ip → **MSK 클러스터 카드**(브로커 ENI 둘 = 선 하나 · `ListNodes` 의 ENI id) · **2.8.0** 그룹을 든 ECS 서비스 둘(bridge 컨테이너 인스턴스 대상 3 = `ecs_service` 선 하나 · 원하는 수 0 = 등록 0 선) · ASG 둘(`web-asg` 인스턴스 2 = 화면이 ASG 선 하나로 접는다 · 한 대는 다른 그룹에도 손으로 등록해 안 접힌다 · `standby-asg` 원하는 수 0 = 선 없음) · Route 53 영역 둘(P2-4 · 검사 150) · CloudFront 배포 둘(P2-3 · 검사 151 — S3 REST·웹사이트 버킷 = 파생 `s3_bucket` 둘 · 사용자 도메인 `www` → 별칭 → `web-alb` · VPC 원본 → `int-alb`·worker EC2). 🔴 **손으로 고치지 않는다** — `engine/tests/lbt_world.py` 를 엔진에 돌린 바이트 그대로이고 `test_lb_targets.py::test_the_fixture_is_this_world_collected` 가 대조한다(다시 쓰기 = `NETTOPO_REGEN_FIXTURE=1`). 렌더러 검사 148 의 입력 |

| `relations.json` | **배치 D 관계 넓히기(스키마 2.10.0 · ADR 0002 §12 · §17.33~37).** 접두사 목록(AWS 관리형 S3 · 고객 관리형 `corp-onprem` CIDR 둘 · 항목 읽기가 거부된 `partner-nets`) · 탄력적 IP 여섯(인스턴스 둘 · NAT · ALB 서비스 관리형 · 연결 없음 · 없는 ENI) · API Gateway REST·HTTP(VPC 링크 → NLB·ALB · 단계 변수 · Cloud Map) · RDS 프록시 둘(→ Aurora 클러스터 · → 인스턴스 + 없는 DB) · Global Accelerator(→ ALB · → EIP 의 주인 EC2 · 수집 범위 밖 리전 · 없는 NLB · 빈 그룹) · 🆕 #129(2.11.0 · ADR 0002 §13 · §17.38~40): TGW Connect 셋(VPC 위 · DX 위 · 없는 전송 위 · GRE 피어) · Cloud WAN 코어 네트워크(이 VPC · 범위 밖 eu-west-1 VPC · 없는 VPC · Connect 연결 · 지우는 중 연결 · 라우트 `CoreNetworkArn`) · VPC Lattice(서비스 네트워크 ↔ 이 VPC · 없는 VPC · 서비스 둘 → EC2 · ALB · Lambda · IP · 없는 EC2 · 빈 대상 그룹). 🔴 **손으로 고치지 않는다** — `engine/tests/rel_world.py` 를 엔진에 돌린 바이트 그대로이고 `test_relations_d.py::test_the_fixture_is_this_world_collected` 가 대조한다(다시 쓰기 = `NETTOPO_REGEN_FIXTURE=1`). 렌더러 `rel` 페이지(검사 152~156 · 158~160)의 입력 |

`partial-failure.json`에는 **`item_count: 0` 과 `status: "denied"`가 함께 들어 있다.** 이 둘은 전혀
다른 사실이다("없음"과 "확인 못함"). 렌더러가 이를 같게 표시하면 사람이 오판한다 —
`eu-west-1`/`ok`/`item_count: 0` 항목이 그 구분을 강제하는 대조군이다.

`elb-followup-denied.json` 은 **실계정 하네스에도 먹인다.** `renderer/verify-real.mjs` 의 오라클은
페이지가 아니라 스냅샷 JSON 이므로 입력만 바꾸면 되고, 권한이 온전한 계정에서는 만들 수 없는
R9(못 읽은 LB 의 `.pmark`)가 이 입력에서만 실측된다 — `prove-real-breaks.sh` 의 「못 읽음」 축이
그 경로다(브레이크 `R8-못읽음을0으로`·`R9-표식삼킴`). 🔴 그 축에서 R5 가 `⏭`(라우트 엣지 0개)인
것은 정상이다. 왜 이 픽스처를 `minimal.json`·`partial-failure.json` 에 합치지 않았는지:
전자에 넣으면 기본 회귀 픽스처에 `.pmark` 가 생기고, 후자에 넣으면 서브넷이 없어
`unresolved_refs` 가 섞여 「후속 호출만 못 읽음」이라는 단일 변수가 흐려진다.

## `observation/` — 관측 픽스처 10장 (ADR 0006)

**짝은 파일명의 첫 구획이 정한다.** `observation/minimal.metric-off.json` → `fixtures/minimal.json`.
두 구조 픽스처가 같은 `collected_at`(`2026-01-02T03:04:05Z`)을 쓰므로 시각으로는 짝을 고를 수
없다. `run.topology_collected_at` 은 짝을 **고르는** 값이 아니라 **맞는지 확인하는** 값이다.

| 파일 | 무엇의 대조군인가 |
|---|---|
| `minimal.json` | 정상 경로. 2층 on, `up`·`none`, `changes: []`(변경 없음도 유효한 상태다 — 「기준 스냅샷」 1줄) |
| ↑ 세 `minimal.*` 공통 | **§O7 의 `association` 두 행을 한 픽스처에서 가른다**(2026-09-23 추가). 같은 연관선 타입인데 라우트테이블 발은 `up`(AWS 가 `AssociationState.State` 를 준다), NACL 발은 `none`(수집기가 상수를 넣는다 — `services/network_acl.py:67`). 🔴 이 두 항목이 없으면 「라우트테이블 연관을 `?` 로 퇴화시키면 FAIL」·「NACL 연관에 `up` 을 적으면 FAIL」 두 대조군이 **항목이 없어서 통과**로 끝난다(그 전까지 NACL 은 어느 픽스처에도 0회였다) |
| `minimal.down.json` | **이력 전부.** `down`(`error_count: 1`) + `changes[]` 3종 전건 + `repeat_count: 6`(플래핑 접힘) + `truncated: true`. `resource_removed` 의 대상은 구조 스냅샷에 **없다** — 유일하게 허용되는 매달린 참조다(ADR 0006 결정 7) |
| `minimal.metric-off.json` | **2층 off.** `layers.metric: false` + metric coverage `skipped`/`item_count: null`. 끈 것과 못 읽은 것은 다르다 |
| `partial-failure.json` | **불변식 ④ + `live`.** TGW attachment 가 지표 근거(12 dp / 300 s)로 `live`, TGW 노드는 `up`+`partial`(throttled), `444455556666` 은 두 층 모두 `denied` → 그 계정의 observation 은 **0건**(못 읽은 것에 상태를 붙이지 않는다) |
| `partial-failure.metric-denied.json` | 바로 위와 **2층 한 칸만 다른 A/B 쌍.** metric coverage 가 `denied` 로 바뀌면 같은 attachment 가 `live` → `up` + `partial: true` + 사유가 된다. 「모름」 → 「고장」 순서의 대조군 |
| `minimal.history.json` | **없어진 것의 모습(`was`).** 카드 줄·선 줄에 `was` 가 있는 유일한 픽스처(이력 비교 화면 · 검사 114). 🆕 1.7.0: **반복의 시작이 있는 접힌 줄**(IGW 카드 `6회 반복` · `first_at`·`first_from` — 짝수 번이라 마지막 `from` ≠ 반복 전 상태 · ADR 0006 결정 12). 시작이 **없는** 옛 모양 접힌 줄은 `minimal.down.json` 에 그대로 있다 |
| `minimal.flow-log.json` | **흐름 로그 켬 · IGW 모양.** `minimal.json` 에 판독기 몫만 더했다(그 몫을 걷으면 바이트가 같다 — `test_flow_log_fixtures_are_the_off_documents_plus_the_reader`). 퍼블릭 서브넷 한 곳의 IGW 라우트 행에 `by_subnet`(A 등급 · `observed_on` 없음) + IGW 카드 `live`(B · `observed_on` = 그 서브넷). 같은 VPC 에 로그 둘 — 하나는 읽음, 하나는 `duplicate_covered`. 🔴 같은 테이블을 쓰는 **다른 서브넷은 항목이 없다**(옆 서브넷 기록으로 켜지 않는다) |
| `peering.flow-log.json` | **흐름 로그 켬 · 피어링 모양.** 짝 = `fixtures/peering.json`. 명시 연관 서브넷과 메인 테이블 서브넷이 각자 `pcx-` 라우트 행에 `by_subnet`(B · `observed_on` 필수), 피어링 선 두 줄이 `live`(흐름 로그 전용 — 짝이 있을 때만 행). 반대편 VPC 의 라우트 행은 기록이 없어 `up` 그대로. 로그 목록 = ENI 단위 `format_v2` · 읽음 · `fields_missing` 세 줄(1.3.0 전에는 `format_parquet` — Parquet 이 스펙 밖이 된 뒤 바꿨다) |
| `lb-targets.json` | **엔진이 낸 관측 바이트 · LB 대상 다섯 갈래(1.15.0 · #137).** 짝 = `fixtures/lb-targets.json`. `engine/tests/lbt_world.py` 를 상태 단까지 엔진에 돌린 그대로다(`test_the_observation_fixture_is_this_world_observed` 가 바이트를 대조 · `NETTOPO_REGEN_FIXTURE=1` 로 다시 쓴다). Lambda 대상(`fn-tg`)이 AWS 모양 그대로 `unavailable` + `Target.HealthCheckDisabled` → `web-alb` 행 `health_check_off: 1` · `up`. 렌더 검사 129 `lbt` 의 입력. 알려진 빈자리 1(LB → VPC Lambda 선 행 없음 — `schema/tests/test_observation_schema.py` `KNOWN_SILENT`) |

🔴 **층을 못 읽었다는 사실은 `status` 가 아니라 `coverage` 에 있다.** 다섯 번째 어휘를 만들지
않기 위한 설계다. `coverage` 의 세 번째 키가
`resource_type` 이 아니라 **`layer`** 인 이유도 같다 — 관측은 리소스 종류가 아니라 층에서 실패한다.

## 🔴 스키마가 거부하는 상태는 fixture 로 만들지 마라 — **생성기**로 만든다

「스키마에 타입이 먼저 늘고 렌더러가 아직 규칙을 모른다」는
**정상 경로**여서 하네스가 재야 하지만, 그 입력은 이 폴더에 올 수 없다. 미지 타입은 `node_type`
enum 에 **없으므로** `schema/tests/test_schema.py` 가 정당하게 FAIL 하고, 그 게이트에 예외 목록을
뚫으면 **스키마를 안 고치고 타입을 늘릴 수 있게 되어 이음매 1 자체가 무너진다.**

그래서 그 축의 입력은 `renderer/gen-unknown-input.mjs` 가 `minimal.json` 을 base 로 **합성**해
`out/`(gitignore) 에 쓴다. 같은 판단이 필요한 다음 상태도 같은 방식으로 한다 — 판별 기준은
「이 입력이 스키마 게이트를 통과해야 하는가」다. 통과해야 하면 fixture, 아니면 생성기.

## 실계정 산출물을 승격할 때

`out/`·`snapshots/`의 실제 결과물은 **커밋 금지**다(민감 자료). fixture 로 올리려면 먼저 스크럽한다:

- 계정ID → `ALLOWED` 계열(`111122223333`, `444455556666`, …). `scripts/check_no_account_ids.py`가 강제한다
- VPC/서브넷/TGW ID, CIDR, 리소스명, 태그, `reason.message` 원문 → 예시 값으로 치환
- 조직 구조(OU 이름·계정 별칭)도 식별 정보다
