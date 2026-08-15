"""Apply review-only agent-vision panel observations.

Reads one JSON file: a list of observations, one per panel:

    [
      {
        "panel_id": "region-...",
        "balloon_mask_status": "known_empty" | "known_nonempty",
        "mask_confidence": 0.9,
        "mask_reason": "...",
        "balloon_regions": [...],
        "protected_regions": [...]
      }
    ]

Review-only: requires --silent-reference-review and rejects publish. No
provider call, no credentials, no media output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import session_scope  # noqa: E402
from app.services import agent_visual_observation as avo  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--agent-label", required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--silent-reference-review", action="store_true")
    args = parser.parse_args(argv)

    observations = json.loads(args.observations.read_text(encoding="utf-8"))
    if not isinstance(observations, list):
        print("agent_observation.geometry_invalid: observations file must be a JSON list")
        return 1
    try:
        with session_scope() as db:
            report = avo.apply_agent_panel_observations(
                db,
                args.project_id,
                observations,
                agent_label=args.agent_label,
                silent_reference_review=args.silent_reference_review,
                publish_allowed=False,
                output_dir=args.output_dir,
            )
    except avo.AgentObservationError as exc:
        print(f"{exc.code}: {exc}")
        return 1
    print(json.dumps({"applied": report["applied"], "ledger": str(report["ledger_path"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
