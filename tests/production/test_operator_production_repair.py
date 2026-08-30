from __future__ import annotations

import pytest

from app.services import operator_cli


def test_run_projects_forwards_production_repair_without_preview(tmp_path):
    captured = []

    class Record:
        def as_dict(self):
            return {"job_id": "chapter-1", "state": "READY_TO_RENDER", "error_code": ""}

    class Service:
        def run_project(self, _db, project_id, actor_id="", **kwargs):
            captured.append((project_id, actor_id, kwargs))
            return Record()

    source_root = tmp_path / "source"
    rows = operator_cli.run_projects(
        object(), ["chapter-1"],
        service_factory=lambda **_kwargs: Service(),
        actor_id="operator-1",
        repair_for_production=True,
        review_source_upscale_policy="review_silent_source_upscale_v1",
        review_source_root=source_root,
    )
    assert rows[0]["state"] == "READY_TO_RENDER"
    assert captured == [("chapter-1", "operator-1", {
        "repair_for_production": True,
        "review_source_upscale_policy": "review_silent_source_upscale_v1",
        "review_source_root": source_root,
    })]


def test_run_projects_rejects_preview_and_production_repair_together():
    with pytest.raises(operator_cli.OperatorCliError, match="operator.mode_invalid"):
        operator_cli.run_projects(
            object(), ["chapter-1"],
            service_factory=lambda **_kwargs: object(),
            review_only_preview=True,
            repair_for_production=True,
        )
