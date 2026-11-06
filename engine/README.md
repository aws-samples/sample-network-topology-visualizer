# `engine/` — 수집 엔진 (Python)

읽기 전용으로 AWS 네트워크 구성을 수집해 `schema/topology.schema.json` **v2.0.0** 을 만족하는
스냅샷 JSON 을 만든다. `renderer/` 를 모른다(불변식 ③).

v2.0.0 은 `partial`·`directed`·`item_count` 를 생략할 수 없게 했다 — **거짓이어도 항상 적는다**
(`model.py` 머리 주석). 생략을 「false」로 읽는 규칙은 계약 밖 지식이다.

## 설치·실행

```bash
python3.12 -m venv .venv                     # CI 는 3.12 로 잰다
.venv/bin/pip install -e './engine[dev]'     # 리포 루트에서
.venv/bin/nettopo collect --profile <읽기 전용 프로필> --region <리전> \
    --accounts <계정ID> --out out/snapshot.json
```

- `--region` 에 **기본값이 없다**(의도적). 셸 기본 리전에 의존한 산출물은 재현되지 않는다.
- 읽기 전용 권한의 프로필로 수집한다. **쓰기 권한이 붙은 프로필로 수집하지 마라** — 그러면
  런타임 가드가 유일한 방어선이 된다.
- `out/` 는 `.gitignore` 다. **스냅샷 자체가 민감 자료다**(계정 ID · CIDR · 공인 IP 가 들어 있다).

## 검증 게이트

```bash
.venv/bin/python -m pytest engine -q         # 불변식 ①②④ + 계약
./engine/prove-breaks.sh                     # 어서션 판별력(브레이크 전부 · 1개당 약 12초 · 개수는 check-break-anchors.py 가 센다)
pre-commit run --all-files                   # git add 뒤에 돌린다
```

- **"pytest 통과"는 완료가 아니다.** 수집 코드는 **실제 계정 1회 실행**까지가 완료다
  (검증 규칙). 2회 실행의 해시가 `collected_at` 을 뺀 채 같은지도 같이 본다 —
  CLI 가 그 해시를 찍는다.
- **실패할 수 없는 어서션은 통과가 아니라 미측정이다.** 새 검사에는
  `prove-breaks.sh` 에 브레이크를 하나 붙여 FAIL 을 한 번 눈으로 확인한다.
- 🔴 **제품 코드를 고쳤으면 `./engine/check-break-anchors.py` 를 돌려라**(1초, 두 하네스 다 받는다).
  앵커 문자열이 안 맞게 되면 하네스는 `SKIP 치환 실패` 로 넘기고 **아무도 그것을 실패로 못 본다** —
  전수 확인은 20분이라 사실상 안 돌린다. 실측(2026-09-26): S1 의 `_drop(..., blocking=...)` 인자 추가
  하나로 앵커 **2개**가 조용히 죽어 있었다. 출력의 「선언 수 = 측정 수」를 먼저 본다(양성 대조군).

## 지금 수집하는 것

**1차 지원 리소스 12종 전부**(전체 목록 = 루트 `README.md` 「Key Features」)와 **엣지 7종 전부**(`schema` 의
`edge_type` enum), 그리고 **2차 첫 종류 Direct Connect 3종**(연결·VIF·DXGW, ADR 0010 — 스키마 2.1.0) ·
Network Firewall · **Site-to-Site VPN 3종**(VPN 연결·가상 프라이빗 게이트웨이·고객 게이트웨이 — 스키마 2.6.0 · 2026-10-06 ·
PSK·장비 설정은 담지 않는다 · 터널 UP/DOWN 은 상태 탐침 몫 · 🔶 실계정 1회 수집은 QA 뒤) · **VPC 안 IP 를 쓰는 서비스**(#114 · 스키마 2.9.0 ·
ADR 0013 — ElastiCache 캐시 클러스터 · Aurora/RDS DB 클러스터 · OpenSearch 도메인 · MSK 클러스터 · LB IP 대상의 ENI 가 그 카드로 이어진다).
목록은 `services/__init__.py` 의 `REGISTRY`, 요구 IAM 액션은 `docs/required-permissions.md` 가 정본이다.
나머지 2차(RAM·Route 53 Resolver·Global Accelerator)는
`--resource-types` 에 넣으면 **조용히 건너뛰지 않고 `ValueError`** 로 죽는다 — `skipped` 는
사용자가 제외한 경우만이다.

DXGW 는 **첫 글로벌 리소스**다 — id 의 리전 자리가 `global`, `region` 은 null, 부모는 계정 노드.
리전마다 같은 목록이 나오므로 리전마다 coverage 가 남고 노드는 하나로 합쳐진다(ADR 0010 §2).
DX 목록 API 는 boto3 페이지네이터가 없어 `services/base.py` 가 `nextToken` 을 손으로 돌린다.

엣지·부모·공유자원 귀속 규칙(엣지 종류 = `schema/topology.schema.json` `edge_type`)이 산출물에서
실제로 성립하는지는 `engine/tests/test_edges.py` 가 잰다. 🔴 **`edges` 가 `[]` 이던 동안
「모든 엣지의 양 끝이 노드로 존재한다」류의 어서션은 통과했지만 미측정이었다** — 빈 배열은
어떤 조건도 위반하지 않는다. 그래서 그 파일의 검사들은 「없어야 하는 것」만 재지 않고 항상
**살아 있는 짝**을 같이 잰다(선이 0개여도 통과하는 검사와 구분되지 않으므로).

크로스계정 경로(`assume_role_session`)는 **실패 경로만 실증됐다**. 2026-09-14 실측: 존재하지
않는 대상 계정으로 돌려 `status=denied` + `reason.api=sts:AssumeRole` 이 스냅샷에 적히는 것을
확인했다(불변식 ④). **성공 경로는 아직 미실증** — 대상 계정에 `NetworkReadOnlyRole` 이
배포되지 않았다.

`prove-breaks.sh` 의 판정은 pytest 화면 출력이 아니라 **junit XML**(`break-report.py`)에서
읽는다. 요약줄을 grep 하던 판은 같은 브레이크에 실행마다 다른 판정을 냈다(2026-09-14 실측).
하네스가 스스로 흔들리면 스윕 결과는 증거가 아니다.

🔴 **같은 길이로 치환하는 브레이크는 바이트코드 캐시에 걸린다.** 파이썬의 pyc 무효화 기준은
`(mtime 초, 파일 크기)` 뿐이다. `"2.0.0"` → `"1.0.0"` 처럼 크기가 같고 cp·perl·mv 가 같은 초
안에 끝나면 **복원한 뒤에도 깨진 바이트코드가 임포트된다** — 실측(2026-09-14)에서 `구버전선언`
다음 대조군이 소스가 정상인데 `14 failed` 로 나왔다. 그래서 스윕은 `PYTHONDONTWRITEBYTECODE=1`
로 돌고 시작할 때 `__pycache__` 를 지운다. 이 두 줄을 지우면 그 거짓 실패가 재현된다(그게
이 방어의 대조군이다).
