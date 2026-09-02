from __future__ import annotations


def _draft(client, recap_text, panel_bytes, declared_rights):
    assert client.post(
        "/api/auth/register",
        json={"email": "publish-agent@example.com", "password": "agentpass1234"},
    ).status_code == 201
    project = client.post(
        "/api/projects",
        json={"title": "Agent Publish", "manhwa_title": "Menara", "chapter": "22-25", "template": "classic", "target_duration": 40, "language": "id"},
    ).json()
    pid = project["id"]
    assert client.post(
        f"/api/projects/{pid}/assets/text",
        json={"text": recap_text, "title": "recap.txt", "rights": declared_rights},
    ).status_code == 201
    assert client.post(
        f"/api/projects/{pid}/assets/upload",
        files=[("files", ("panel01.jpg", panel_bytes, "image/jpeg"))],
        data={k: str(v) for k, v in declared_rights.items()},
    ).status_code == 201
    from tests.factories.vision_api import seed_reconciled_analysis_for_project_images
    seed_reconciled_analysis_for_project_images(pid)
    response = client.post(f"/api/projects/{pid}/run", json={"until": "draft", "seed": 42})
    assert response.status_code == 200, response.text
    return pid


def test_trusted_agent_publish_still_requires_explicit_publish_intent(client, recap_text, panel_bytes, declared_rights):
    pid = _draft(client, recap_text, panel_bytes, declared_rights)
    response = client.post(
        f"/api/projects/{pid}/run",
        json={"until": "publish", "approval_mode": "trusted_agent", "confirm_publish_intent": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["action_required"] == "script_approval"
    assert response.json()["script_approved"] is False


def test_explicit_trusted_agent_publish_records_nonhuman_approval(client, recap_text, panel_bytes, declared_rights, monkeypatch):
    pid = _draft(client, recap_text, panel_bytes, declared_rights)
    from app.routers import pipeline as pipeline_router
    from app.services import pipeline as pipeline_service

    def stop_after_approval(*args, **kwargs):
        raise pipeline_service.PipelineError("test.stop_after_agent_approval")

    monkeypatch.setattr(pipeline_router.pl, "generate_voiceover", stop_after_approval)
    response = client.post(
        f"/api/projects/{pid}/run",
        json={"until": "publish", "approval_mode": "trusted_agent", "confirm_publish_intent": True},
    )
    assert response.status_code == 422
    script = client.get(f"/api/projects/{pid}/script").json()
    meta = script["editorial_metadata"]
    assert script["approved_at"] is not None
    assert meta["approval_actor_type"] == "trusted_agent"
    assert meta["approval_reason"] == "explicit_user_publish_request"
    assert meta["human_review_performed"] is False


def test_thumbnail_retry_route_is_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/publications/{publication_id}/thumbnail/retry" in paths
