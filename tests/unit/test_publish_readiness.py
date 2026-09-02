from __future__ import annotations

from types import SimpleNamespace


def test_can_publish_uses_shared_quality_evaluator(monkeypatch):
    from app.services import publish

    job = SimpleNamespace(id="render1")
    seen = {}
    monkeypatch.setattr(publish, "successful_render", lambda db, project_id: job)

    def evaluate(db, project_id, job=None):
        seen["project_id"] = project_id
        seen["job"] = job
        return ["shared-result"]

    monkeypatch.setattr(publish, "evaluate_quality_checks", evaluate)
    monkeypatch.setattr(
        publish.quality_svc,
        "summarise",
        lambda results: {"can_publish": True, "error_codes": []},
    )

    result = publish.can_publish(object(), "project1")
    assert result["ready"] is True
    assert seen == {"project_id": "project1", "job": job}


def test_thumbnail_403_note_is_actionable():
    from app.models import Publication

    publication = Publication(
        thumbnail_status="failed",
        thumbnail_error="thumbnail_http_403: forbidden",
    )
    note = publication.thumbnail_note.lower()
    assert "custom thumbnail" in note
    assert "enable" in note
    assert "retry" in note
