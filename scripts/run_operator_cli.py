"""Start the local interactive operator console."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == "__main__":  # pragma: no cover
    from app.services.operator_cli import main

    raise SystemExit(main(sys.argv[1:]))
