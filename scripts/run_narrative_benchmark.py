#!/usr/bin/env python3
"""Run deterministic narration-quality benchmark cases from a JSON file."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.narrative_benchmark import BENCHMARK_VERSION, evaluate_narration


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        default="tests/fixtures/narrative_benchmark_v1.json",
    )
    args = parser.parse_args()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    if payload.get("version") != BENCHMARK_VERSION or not isinstance(payload.get("cases"), list):
        raise SystemExit("invalid narrative benchmark fixture")
    results = []
    expectation_mismatches = []
    for case in payload["cases"]:
        result = evaluate_narration(
            str(case.get("case_id", "")),
            case.get("passages", []),
            case.get("claims", {}),
        )
        row = result.as_dict()
        expected = case.get("expected_pass")
        row["expected_pass"] = expected
        if isinstance(expected, bool) and result.passed != expected:
            expectation_mismatches.append(result.case_id)
        results.append(row)
    summary = {
        "version": BENCHMARK_VERSION,
        "case_count": len(results),
        "passed_count": sum(bool(item["passed"]) for item in results),
        "expectation_mismatches": expectation_mismatches,
        "results": results,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if expectation_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
