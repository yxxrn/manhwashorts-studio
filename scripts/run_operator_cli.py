"""Start the local interactive operator console."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.operator_cli import main  # noqa: E402,I001


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
