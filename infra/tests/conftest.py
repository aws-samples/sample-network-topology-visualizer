"""`infra/state-lambda/` 는 배포 zip 의 루트다 — 설치되는 패키지가 아니므로 경로로 임포트한다.

🔴 `sys.path` 에 붙이는 것이 Lambda 의 실제 임포트 모양이다(핸들러는 zip 루트에 평평하게 있고
`handler.handler` 로 불린다). 여기서 패키지처럼 만들면 검사가 배포와 다른 구조를 재게 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

STATE_LAMBDA = Path(__file__).resolve().parent.parent / "state-lambda"
if str(STATE_LAMBDA) not in sys.path:
    sys.path.insert(0, str(STATE_LAMBDA))

# 내부 문서 목록(`internal_docs` — 공개 트리에 없는 문서는 그 대조만 건너뛴다)이 schema/tests 에 있다
SCHEMA_TESTS = Path(__file__).resolve().parents[2] / "schema" / "tests"
if str(SCHEMA_TESTS) not in sys.path:
    sys.path.insert(0, str(SCHEMA_TESTS))
