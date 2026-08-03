"""Agent-control regression tests (v1.3.1).

The project is meant to be driven end to end by an AI agent over REST, with the
web UI only for occasional manual review. Two real bugs broke exactly that, and
both are covered here:

1. **Secure cookies locked out loopback clients.** Setting
   ``MS_ENVIRONMENT=production`` marked every session cookie ``Secure``. A client
   on ``http://127.0.0.1:8000`` then refuses to send it back, so login returned
   200 and the very next call returned 401. Secure must be decided per request
   from the actual scheme, not from a global setting.

2. **The health contract silently lost its schema.** Adding ``disk_usage``
   dropped ``response_model=HealthOut``, so the endpoint stopped being validated
   and an agent reading it had no guarantee of shape.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("app_settings")


# --- session auth over plain HTTP (the agent path) -------------------------


def test_registration_can_be_closed(client, monkeypatch):
    from app.routers import auth

    monkeypatch.setattr(auth.settings, "allow_registration", False)
    response = client.post(
        "/api/auth/register",
        json={"email": "closed@example.com", "password": "closedpass1234"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Registration is currently closed."


def _cookie_header(response) -> str:
    return response.headers.get("set-cookie", "")


def test_loopback_login_cookie_is_not_secure(client):
    """A cookie marked Secure would never come back over http://127.0.0.1."""
    response = client.post(
        "/api/auth/register",
        json={"email": "agent-local@example.com", "password": "agentpass1234"},
    )
    assert response.status_code == 201, response.text
    assert "secure" not in _cookie_header(response).lower()


def test_agent_can_keep_a_session_over_plain_http(client):
    """The regression: login 200 then 401 on the next call."""
    register = client.post(
        "/api/auth/register",
        json={"email": "agent-session@example.com", "password": "agentpass1234"},
    )
    assert register.status_code == 201

    # Same client, so the cookie jar carries the session forward.
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/projects").status_code == 200


def test_forwarded_proto_https_marks_the_cookie_secure(client):
    """Behind Cloudflare the app sees X-Forwarded-Proto, and must honour it."""
    response = client.post(
        "/api/auth/register",
        json={"email": "agent-proxied@example.com", "password": "agentpass1234"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 201, response.text
    assert "secure" in _cookie_header(response).lower()


def test_forwarded_proto_chain_uses_the_client_facing_hop(client):
    """Multi-proxy chains arrive comma-separated; the first hop is the client's.

    Registers its own account: the ``client`` fixture rebuilds the database per
    test, so a user created in an earlier test does not exist here.
    """
    assert client.post(
        "/api/auth/register",
        json={"email": "agent-chain@example.com", "password": "agentpass1234"},
    ).status_code == 201

    response = client.post(
        "/api/auth/login",
        json={"email": "agent-chain@example.com", "password": "agentpass1234"},
        headers={"X-Forwarded-Proto": "https, http"},
    )
    assert response.status_code == 200
    assert "secure" in _cookie_header(response).lower()


def test_forwarded_proto_http_leaves_cookie_insecure(client):
    assert client.post(
        "/api/auth/register",
        json={"email": "agent-plain@example.com", "password": "agentpass1234"},
    ).status_code == 201

    response = client.post(
        "/api/auth/login",
        json={"email": "agent-plain@example.com", "password": "agentpass1234"},
        headers={"X-Forwarded-Proto": "http"},
    )
    assert response.status_code == 200
    assert "secure" not in _cookie_header(response).lower()


# --- health contract -------------------------------------------------------


def test_health_still_declares_a_response_model():
    """Without response_model an agent has no guaranteed shape to parse."""
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/health")
    assert route.response_model is not None, "health lost its schema"


def test_health_reports_disk_usage(client):
    body = client.get("/api/health").json()
    for field in ["status", "version", "environment", "video_encoder", "gpu_encoding"]:
        assert field in body

    disk = body.get("disk_usage")
    assert disk is not None, "agents rely on disk_usage to spot growth"
    for field in ["tmp_bytes", "output_bytes", "storage_bytes",
                  "total_bytes", "total_human", "over_limit"]:
        assert field in disk


# --- the full agent-driven flow --------------------------------------------


def test_agent_can_drive_the_whole_pipeline_over_rest(
    client, recap_text, panel_bytes, declared_rights
):
    """One pass with no UI and no internal imports: create -> render-ready.

    Rendering itself is covered by the slow suite; this asserts every step an
    agent needs is reachable over HTTP and returns a usable payload.
    """
    assert client.post(
        "/api/auth/register",
        json={"email": "agent-flow@example.com", "password": "agentpass1234"},
    ).status_code == 201

    project = client.post(
        "/api/projects",
        json={
            "title": "Agent Flow",
            "manhwa_title": "Menara",
            "chapter": "9",
            "target_duration": 40,
            "language": "id",  # fixture text is Indonesian; explicit opt-in
        },
    ).json()
    pid = project["id"]

    # Source text plus a rights declaration.
    assert client.post(
        f"/api/projects/{pid}/assets/text",
        json={"text": recap_text, "title": "recap.txt", "rights": declared_rights},
    ).status_code == 201

    # Panels as multipart, the same way an agent would post images.
    upload = client.post(
        f"/api/projects/{pid}/assets/upload",
        files=[("files", ("panel01.jpg", panel_bytes, "image/jpeg"))],
        data={k: str(v) for k, v in declared_rights.items()},
    )
    assert upload.status_code == 201, upload.text

    # analyse -> script -> voice -> timeline in one call.
    draft = client.post(f"/api/projects/{pid}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["segments"] > 0
    assert body["scenes"] > 0
    assert body["cues"] > 0

    # The agent reads the script, then approves it.
    script = client.get(f"/api/projects/{pid}/script").json()
    assert [s["section"] for s in script["sections"]] == [
        "hook", "setup", "conflict", "twist", "cta"
    ]
    assert client.post(f"/api/projects/{pid}/script/approve").status_code == 200

    # Quality gate must be readable and must not block a clean project.
    quality = client.post(f"/api/projects/{pid}/quality").json()
    assert quality["errors"] == 0, quality["error_codes"]
    assert quality["can_publish"] is True

    # Everything an agent polls afterwards is reachable.
    for path in ["/analysis", "/timeline", "/subtitles", "/voice", "/render",
                 "/publish/readiness", "/metadata"]:
        assert client.get(f"/api/projects/{pid}{path}").status_code == 200, path


def test_openapi_is_available_for_agent_introspection(client):
    """An agent should be able to discover the surface without docs."""
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    for required in [
        "/api/projects",
        "/api/projects/{project_id}/draft",
        "/api/projects/{project_id}/render",
        "/api/projects/{project_id}/assets/upload",
    ]:
        assert required in paths, f"{required} missing from OpenAPI"
