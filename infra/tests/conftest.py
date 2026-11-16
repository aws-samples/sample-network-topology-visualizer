"""`infra/state-lambda/` is the root of the deployment zip rather than an
installed package, so it is imported by path.

Appending to `sys.path` mirrors how Lambda actually imports these modules: the
handlers sit flat at the zip root and are invoked as `handler.handler`. Turning
the directory into a package here would test a layout that is not the one we
deploy.
"""

from __future__ import annotations

import sys
from pathlib import Path

STATE_LAMBDA = Path(__file__).resolve().parent.parent / "state-lambda"
if str(STATE_LAMBDA) not in sys.path:
    sys.path.insert(0, str(STATE_LAMBDA))

# `internal_docs`, the list of documents that are absent from the public tree so
# their cross-checks are skipped, lives in schema/tests.
SCHEMA_TESTS = Path(__file__).resolve().parents[2] / "schema" / "tests"
if str(SCHEMA_TESTS) not in sys.path:
    sys.path.insert(0, str(SCHEMA_TESTS))
