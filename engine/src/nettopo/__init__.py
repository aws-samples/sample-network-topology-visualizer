"""nettopo — AWS 네트워크 토폴로지 수집 엔진.

`engine/` 은 `renderer/` 를 모른다(불변식 ③). 유일한 접점은
`schema/topology.schema.json` 이다.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
