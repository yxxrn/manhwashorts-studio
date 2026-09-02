from __future__ import annotations

from types import SimpleNamespace


def _ready_browser():
    return {
        "publisher": "youtube_studio_browser",
        "available": True,
        "authenticated": True,
        "action_required": None,
    }


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
    monkeypatch.setattr(publish, "browser_status", _ready_browser)

    result = publish.can_publish(object(), "project1")
    assert result["ready"] is True
    assert seen == {"project_id": "project1", "job": job}


def test_can_publish_blocks_when_browser_needs_login(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(publish, "successful_render", lambda db, project_id: SimpleNamespace(id="r1"))
    monkeypatch.setattr(publish, "evaluate_quality_checks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        publish.quality_svc,
        "summarise",
        lambda results: {"can_publish": True, "error_codes": []},
    )
    monkeypatch.setattr(
        publish,
        "browser_status",
        lambda: {
            "publisher": "youtube_studio_browser",
            "available": True,
            "authenticated": False,
            "action_required": "youtube_reauthentication",
        },
    )

    result = publish.can_publish(object(), "project1")
    assert result["ready"] is False
    assert "reauthentication" in result["reason"]
