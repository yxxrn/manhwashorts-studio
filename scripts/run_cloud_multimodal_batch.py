"""Run reconciled cloud multimodal jobs through the existing BYOK boundary.

This command never prints credentials or provider payloads. It writes only
small ignored job-state JSON and leaves final voice/render work behind the
existing approval and authoritative word-timing gates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db import session_scope
from app.services import cloud_multimodal, pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id",
        action="append",
        required=True,
        help="Project ID to process; repeat for an isolated batch.",
    )
    parser.add_argument(
        "--state-dir",
        default="data/cloud-multimodal-jobs",
        help="Ignored JSON resume state directory.",
    )
    parser.add_argument("--model", default=None, help="Require the configured model ID.")
    parser.add_argument("--actor-id", default="", help="Optional non-secret audit actor ID.")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--min-request-interval-s", type=float, default=0.0)
    parser.add_argument("--estimated-cost-per-request", type=float, default=0.0)
    parser.add_argument("--max-concurrent", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    state_dir = Path(options.state_dir)
    with session_scope() as db:
        jobs: dict[str, dict[str, object]] = {}
        for project_id in sorted(set(options.project_id)):
            try:
                runner = cloud_multimodal.resolve_cloud_runner(
                    db,
                    pipeline.get_project(db, project_id).workspace_id,
                    model=options.model,
                    max_attempts=options.max_attempts,
                    max_requests=options.max_requests,
                    min_request_interval_s=options.min_request_interval_s,
                    estimated_cost_per_request=options.estimated_cost_per_request,
                )
                service = cloud_multimodal.CloudBatchService(
                    runner=runner,
                    store=cloud_multimodal.JsonJobStore(state_dir),
                    max_concurrent=options.max_concurrent,
                )
                record = service.run_project(db, project_id, actor_id=options.actor_id)
                jobs[project_id] = record.as_dict()
            except cloud_multimodal.CloudStageError as exc:
                jobs[project_id] = {
                    "job_id": project_id,
                    "state": cloud_multimodal.ChapterState.FAILED.value,
                    "error_code": exc.code,
                    "error_message": str(exc),
                }
        print(json.dumps(jobs, ensure_ascii=False, sort_keys=True))
    return 0 if all(item.get("state") != "FAILED" for item in jobs.values()) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
