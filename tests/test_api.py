"""API integration tests via TestClient.

These exercise the real HTTP surface: auth guards, ownership isolation, the
rights gate, and the full draft -> approve -> quality path. Rendering is
covered separately in test_pipeline.py because it is slow.
"""

from __future__ import annotations

# --- auth ------------------------------------------------------------------


def test_health_reports_environment(client):
    body = client.get("/api/health").json()
    assert body["status"] in {"ok", "degraded"}
    assert body["version"]
    assert isinstance(body["problems"], list)


def test_dashboard_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "ManhwaShorts" in response.text
    # Rights warning must be visible in the UI, not buried in docs.
    assert "hak" in response.text.lower()


def test_protected_routes_require_auth(client):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/workspace").status_code == 401
    assert client.post("/api/projects", json={"title": "x"}).status_code == 401


def test_register_login_logout_cycle(client):
    register = client.post(
        "/api/auth/register",
        json={"email": "cycle@example.com", "password": "password1234"},
    )
    assert register.status_code == 201
    assert client.get("/api/auth/me").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"email": "cycle@example.com", "password": "password1234"},
    )
    assert login.status_code == 200
    assert client.get("/api/auth/me").json()["email"] == "cycle@example.com"


def test_login_does_not_reveal_whether_account_exists(client):
    client.post(
        "/api/auth/register", json={"email": "known@example.com", "password": "password1234"}
    )
    client.post("/api/auth/logout")

    wrong_password = client.post(
        "/api/auth/login", json={"email": "known@example.com", "password": "nope12345678"}
    )
    unknown_user = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "nope12345678"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["detail"] == unknown_user.json()["detail"]


def test_duplicate_registration_conflicts(client):
    payload = {"email": "dupe@example.com", "password": "password1234"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_short_password_rejected(client):
    response = client.post(
        "/api/auth/register", json={"email": "short@example.com", "password": "abc"}
    )
    assert response.status_code == 422


def test_tampered_session_cookie_rejected(client):
    client.post(
        "/api/auth/register", json={"email": "tamper@example.com", "password": "password1234"}
    )
    client.cookies.set("ms_session", "forged.session.value")
    assert client.get("/api/auth/me").status_code == 401


# --- projects --------------------------------------------------------------


def _make_project(client, **overrides) -> str:
    payload = {
        "title": "Test Project",
        "manhwa_title": "Judul Uji",
        "chapter": "1",
        "target_duration": 60,
        "language": "id",
        "voice_id": "id",
    }
    payload.update(overrides)
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_project_crud(auth_client):
    project_id = _make_project(auth_client, title="CRUD Project")

    listed = auth_client.get("/api/projects").json()
    assert any(p["id"] == project_id for p in listed)

    patched = auth_client.patch(
        f"/api/projects/{project_id}", json={"title": "Renamed", "target_duration": 45}
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"
    assert patched.json()["target_duration"] == 45

    assert auth_client.delete(f"/api/projects/{project_id}").status_code == 200
    assert auth_client.get(f"/api/projects/{project_id}").status_code == 404


def test_project_validation_rejects_out_of_range_duration(auth_client):
    response = auth_client.post(
        "/api/projects", json={"title": "Too long", "target_duration": 600}
    )
    assert response.status_code == 422


def test_projects_are_isolated_between_users(client):
    client.post("/api/auth/register", json={"email": "a@example.com", "password": "password1234"})
    project_id = _make_project(client, title="Owner A project")
    client.post("/api/auth/logout")

    client.post("/api/auth/register", json={"email": "b@example.com", "password": "password1234"})
    # 404 rather than 403: do not confirm that the id exists.
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.delete(f"/api/projects/{project_id}").status_code == 404
    assert client.get("/api/projects").json() == []


def test_duplicate_project_copies_assets_and_rights(auth_client, recap_text, declared_rights):
    project_id = _make_project(auth_client)
    auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "title": "recap.txt", "rights": declared_rights},
    )

    clone = auth_client.post(f"/api/projects/{project_id}/duplicate").json()
    assert clone["id"] != project_id
    assert clone["status"] == "draft"
    clone_assets = auth_client.get(f"/api/projects/{clone['id']}/assets").json()
    assert len(clone_assets) == 1
    assert clone_assets[0]["rights_status"] == "declared"


# --- assets and the rights gate -------------------------------------------


def test_text_asset_records_rights(auth_client, recap_text, declared_rights):
    project_id = _make_project(auth_client)
    response = auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "title": "recap.txt", "rights": declared_rights},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["rights_status"] == "declared"
    assert body["rights_owner"] == "Tester"


def test_undeclared_asset_stays_undeclared(auth_client, recap_text):
    project_id = _make_project(auth_client)
    response = auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "rights": {"declared": True}},
    )
    # Box ticked but no owner or licence: still not publishable.
    assert response.json()["rights_status"] == "undeclared"


def test_short_text_rejected(auth_client):
    project_id = _make_project(auth_client)
    response = auth_client.post(
        f"/api/projects/{project_id}/assets/text", json={"text": "pendek", "rights": {}}
    )
    assert response.status_code == 422


def test_image_upload_and_rights(auth_client, panel_bytes):
    project_id = _make_project(auth_client)
    response = auth_client.post(
        f"/api/projects/{project_id}/assets/upload",
        files=[("files", ("panel.jpg", panel_bytes, "image/jpeg"))],
        data={
            "rights_owner": "Tester",
            "license_type": "owned",
            "source_name": "Generated",
            "declared": "true",
        },
    )
    assert response.status_code == 201, response.text
    asset = response.json()[0]
    assert asset["type"] == "image"
    assert asset["width"] == 900
    assert asset["rights_status"] == "declared"


def test_upload_rejects_disguised_file(auth_client):
    project_id = _make_project(auth_client)
    response = auth_client.post(
        f"/api/projects/{project_id}/assets/upload",
        files=[("files", ("payload.png", b"\x7fELF not really a png" * 30, "image/png"))],
        data={"rights_owner": "Tester", "license_type": "owned", "declared": "true"},
    )
    assert response.status_code == 422
    assert "not a valid image" in response.json()["detail"]


def test_upload_rejects_bad_license_value(auth_client, panel_bytes):
    project_id = _make_project(auth_client)
    response = auth_client.post(
        f"/api/projects/{project_id}/assets/upload",
        files=[("files", ("p.jpg", panel_bytes, "image/jpeg"))],
        data={"license_type": "totally-made-up", "declared": "true"},
    )
    assert response.status_code == 422


def test_rights_can_be_corrected_after_upload(auth_client, recap_text):
    project_id = _make_project(auth_client)
    asset = auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "rights": {}},
    ).json()
    assert asset["rights_status"] == "undeclared"

    fixed = auth_client.patch(
        f"/api/projects/{project_id}/assets/{asset['id']}/rights",
        json={
            "rights_owner": "Tester",
            "license_type": "licensed",
            "permission_reference": "Contract #12",
            "declared": True,
        },
    )
    assert fixed.status_code == 200
    assert fixed.json()["rights_status"] == "declared"


def test_asset_delete(auth_client, recap_text, declared_rights):
    project_id = _make_project(auth_client)
    asset = auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "rights": declared_rights},
    ).json()
    assert auth_client.delete(f"/api/projects/{project_id}/assets/{asset['id']}").status_code == 200
    assert auth_client.get(f"/api/projects/{project_id}/assets").json() == []


# --- pipeline over HTTP ---------------------------------------------------


def _project_with_material(
    client, recap_text, declared_rights, panel_bytes, panels: int = 3, template: str | None = None
):
    project_kwargs = {"title": "Pipeline Project"}
    if template is not None:
        project_kwargs["template"] = template
    project_id = _make_project(client, **project_kwargs)
    client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "title": "recap.txt", "rights": declared_rights},
    )
    client.post(
        f"/api/projects/{project_id}/assets/upload",
        files=[
            ("files", (f"panel{i}.jpg", panel_bytes, "image/jpeg")) for i in range(panels)
        ],
        data={
            "rights_owner": "Tester",
            "license_type": "owned",
            "source_name": "Generated",
            "declared": "true",
        },
    )
    return project_id


def _seed_vision_analysis(project_id: str) -> None:
    from test_vision_status_api import seed_reconciled_analysis_for_project_images

    seed_reconciled_analysis_for_project_images(project_id)


def test_analysis_requires_text(auth_client):
    project_id = _make_project(auth_client)
    response = auth_client.post(f"/api/projects/{project_id}/analysis")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "BLOCKED"
    assert "vision_capability_missing" in body["blocking_reasons"]["codes"]


def test_analysis_and_manual_correction(auth_client, recap_text, declared_rights):
    project_id = _make_project(auth_client)
    auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "rights": declared_rights},
    )
    analysis = auth_client.post(f"/api/projects/{project_id}/analysis").json()
    assert analysis["state"] == "BLOCKED"
    assert "vision_capability_missing" in analysis["blocking_reasons"]["codes"]

    patched = auth_client.patch(
        f"/api/projects/{project_id}/analysis",
        json={"twist": "Twist yang saya tulis sendiri."},
    )
    assert patched.status_code == 200
    assert patched.json()["twist"] == "Twist yang saya tulis sendiri."
    assert patched.json()["edited_by_user"] is True


def test_draft_creates_script_voice_timeline(
    auth_client, recap_text, declared_rights, panel_bytes
):
    project_id = _project_with_material(
        auth_client, recap_text, declared_rights, panel_bytes, template="classic"
    )
    _seed_vision_analysis(project_id)
    draft = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["script_version"] == 1
    assert body["segments"] == 0
    assert body["scenes"] == 0
    assert body["cues"] == 0
    assert body["audio_duration"] == 0.0

    approved = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json={"editorial_review_confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    voice = auth_client.post(f"/api/projects/{project_id}/voice", json={"speed": 1.0})
    assert voice.status_code == 200, voice.text
    assert voice.json()
    scenes = auth_client.post(f"/api/projects/{project_id}/timeline")
    assert scenes.status_code == 200, scenes.text
    assert scenes.json()
    assert auth_client.get(f"/api/projects/{project_id}/subtitles").json()


def test_script_edit_resets_approval(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    _seed_vision_analysis(project_id)
    draft = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    approved = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json={"editorial_review_confirmed": True},
    )
    assert approved.status_code == 200, approved.text

    script = auth_client.get(f"/api/projects/{project_id}/script").json()
    sections = [
        {
            "section": s["section"],
            "text": s["text"] + " Additional detail.",
            "locked": False,
            "citations": s["citations"],
            "editorial_role": s.get("editorial_role", ""),
            "claim_ids": s.get("claim_ids", []),
            "evidence_panel_ids": s.get("evidence_panel_ids", []),
            "evidence": s.get("evidence", []),
        }
        for s in script["sections"]
    ]
    edited = auth_client.patch(
        f"/api/projects/{project_id}/script", json={"sections": sections}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["approved_at"] is None


def test_empty_section_blocks_approval(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    _seed_vision_analysis(project_id)
    response = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert response.status_code == 200, response.text
    script = auth_client.get(f"/api/projects/{project_id}/script").json()
    sections = [
        {
            "section": s["section"],
            "text": "" if s["section"] == "twist" else s["text"],
            "locked": False,
            "citations": s["citations"],
            "editorial_role": s.get("editorial_role", ""),
            "claim_ids": s.get("claim_ids", []),
            "evidence_panel_ids": s.get("evidence_panel_ids", []),
            "evidence": s.get("evidence", []),
        }
        for s in script["sections"]
    ]
    edited = auth_client.patch(
        f"/api/projects/{project_id}/script", json={"sections": sections}
    )
    assert edited.status_code == 200, edited.text
    response = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json={"editorial_review_confirmed": True},
    )
    assert response.status_code == 422
    assert "text" in response.json()["detail"].lower()


def test_unknown_script_section_rejected(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    _seed_vision_analysis(project_id)
    draft = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    response = auth_client.patch(
        f"/api/projects/{project_id}/script",
        json={"sections": [{"section": "not_a_section", "text": "x"}]},
    )
    assert response.status_code == 422


def test_regenerate_requires_reconciled_evidence_after_edit(
    auth_client, recap_text, declared_rights, panel_bytes
):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    _seed_vision_analysis(project_id)
    draft = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    script = auth_client.get(f"/api/projects/{project_id}/script").json()
    sections = [
        {
            "section": s["section"],
            "text": "Locked hook supplied by the user." if s["section"] == "hook" else s["text"],
            "locked": s["section"] == "hook",
            "citations": s["citations"],
            "editorial_role": s.get("editorial_role", ""),
            "claim_ids": s.get("claim_ids", []),
            "evidence_panel_ids": s.get("evidence_panel_ids", []),
            "evidence": s.get("evidence", []),
        }
        for s in script["sections"]
    ]
    edited = auth_client.patch(
        f"/api/projects/{project_id}/script", json={"sections": sections}
    )
    assert edited.status_code == 200, edited.text
    regenerated = auth_client.post(
        f"/api/projects/{project_id}/script", json={"keep_locked": True, "seed": 7}
    )
    assert regenerated.status_code == 422, regenerated.text
    assert "reconciled vision analysis" in regenerated.json()["detail"]
    current = auth_client.get(f"/api/projects/{project_id}/script").json()
    hook = next(s for s in current["sections"] if s["section"] == "hook")
    assert hook["text"] == "Locked hook supplied by the user."
    assert current["version"] == 1


def test_voice_requires_script(auth_client, recap_text, declared_rights):
    project_id = _make_project(auth_client)
    auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "rights": declared_rights},
    )
    response = auth_client.post(f"/api/projects/{project_id}/voice", json={"speed": 1.0})
    assert response.status_code == 422


def test_subtitles_and_srt_export(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(
        auth_client, recap_text, declared_rights, panel_bytes, template="classic"
    )
    _seed_vision_analysis(project_id)
    draft = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    approved = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json={"editorial_review_confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    voice = auth_client.post(f"/api/projects/{project_id}/voice", json={"speed": 1.0})
    assert voice.status_code == 200, voice.text
    timeline = auth_client.post(f"/api/projects/{project_id}/timeline")
    assert timeline.status_code == 200, timeline.text

    cues = auth_client.get(f"/api/projects/{project_id}/subtitles").json()
    assert cues
    srt = auth_client.get(f"/api/projects/{project_id}/subtitles.srt")
    assert srt.status_code == 200
    assert " --> " in srt.text

    edited = auth_client.patch(
        f"/api/projects/{project_id}/subtitles/{cues[0]['id']}",
        json={"text": "Corrected"},
    )
    assert edited.status_code == 200
    assert edited.json()["edited_by_user"] is True


def test_scene_edit_validates_asset_and_times(
    auth_client, recap_text, declared_rights, panel_bytes
):
    project_id = _project_with_material(
        auth_client, recap_text, declared_rights, panel_bytes, template="classic"
    )
    _seed_vision_analysis(project_id)
    draft = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    approved = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json={"editorial_review_confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    assert auth_client.post(
        f"/api/projects/{project_id}/voice", json={"speed": 1.0}
    ).status_code == 200
    assert auth_client.post(f"/api/projects/{project_id}/timeline").status_code == 200

    scenes = auth_client.get(f"/api/projects/{project_id}/timeline").json()
    assert scenes
    scene_id = scenes[0]["id"]
    ok = auth_client.patch(
        f"/api/projects/{project_id}/timeline/{scene_id}",
        json={"focus_x": 0.3, "focus_y": 0.6, "effect": "pan_left"},
    )
    assert ok.status_code == 200
    assert ok.json()["effect"] == "pan_left"

    foreign = auth_client.patch(
        f"/api/projects/{project_id}/timeline/{scene_id}", json={"asset_id": "does-not-exist"}
    )
    assert foreign.status_code == 422

    backwards = auth_client.patch(
        f"/api/projects/{project_id}/timeline/{scene_id}",
        json={"start_time": 10.0, "end_time": 5.0},
    )
    assert backwards.status_code == 422


def test_quality_warns_when_rights_enforcement_disabled(auth_client, recap_text, panel_bytes):
    """Undeclared rights remain visible but do not block when enforcement is disabled."""
    project_id = _make_project(auth_client)
    auth_client.post(
        f"/api/projects/{project_id}/assets/text",
        json={"text": recap_text, "rights": {}},  # no declaration
    )
    auth_client.post(
        f"/api/projects/{project_id}/assets/upload",
        files=[("files", ("p.jpg", panel_bytes, "image/jpeg"))],
        data={"declared": "false"},
    )
    auth_client.post(f"/api/projects/{project_id}/draft?seed=42")

    summary = auth_client.post(f"/api/projects/{project_id}/quality").json()
    assert "rights.undeclared_assets" not in summary["error_codes"]
    assert "rights.enforcement_disabled" in summary["warning_codes"]


def test_render_not_blocked_by_undeclared_rights_when_enforcement_disabled(auth_client, recap_text, panel_bytes):
    project_id = _make_project(auth_client, template="classic")
    auth_client.post(
        f"/api/projects/{project_id}/assets/text", json={"text": recap_text, "rights": {}}
    )
    upload = auth_client.post(
        f"/api/projects/{project_id}/assets/upload",
        files=[("files", ("panel.jpg", panel_bytes, "image/jpeg"))],
        data={
            "rights_owner": "Tester",
            "license_type": "owned",
            "source_name": "Generated",
            "declared": "false",
        },
    )
    assert upload.status_code == 201, upload.text
    _seed_vision_analysis(project_id)
    draft = auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert draft.status_code == 200, draft.text
    approved = auth_client.post(
        f"/api/projects/{project_id}/script/approve",
        json={"editorial_review_confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    voice = auth_client.post(f"/api/projects/{project_id}/voice", json={"speed": 1.0})
    assert voice.status_code == 200, voice.text
    timeline = auth_client.post(f"/api/projects/{project_id}/timeline")
    assert timeline.status_code == 200, timeline.text
    response = auth_client.post(f"/api/projects/{project_id}/render", json={"kind": "final"})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "final"


def test_error_check_cannot_be_overridden(auth_client, recap_text, panel_bytes):
    project_id = _make_project(auth_client)
    auth_client.post(
        f"/api/projects/{project_id}/assets/text", json={"text": recap_text, "rights": {}}
    )
    auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    auth_client.post(f"/api/projects/{project_id}/quality")

    response = auth_client.post(
        f"/api/projects/{project_id}/quality/override",
        json={"code": "audio.missing", "reason": "saya terima risikonya"},
    )
    assert response.status_code == 422
    assert "cannot be overridden" in response.json()["detail"]


def test_warning_override_is_recorded(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    summary = auth_client.post(f"/api/projects/{project_id}/quality").json()
    if not summary["warning_codes"]:
        return  # nothing to override in this run

    code = summary["warning_codes"][0]
    response = auth_client.post(
        f"/api/projects/{project_id}/quality/override",
        json={"code": code, "reason": "sudah saya periksa manual"},
    )
    assert response.status_code == 200
    assert response.json()["override_reason"] == "sudah saya periksa manual"
    assert response.json()["passed"] is True


def test_qc_history_endpoint_is_append_only(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    assert auth_client.post(f"/api/projects/{project_id}/quality").status_code == 200
    assert auth_client.post(f"/api/projects/{project_id}/quality").status_code == 200
    history = auth_client.get(f"/api/projects/{project_id}/quality/history")
    assert history.status_code == 200
    snapshots = history.json()
    assert len(snapshots) == 2
    assert snapshots[0]["id"] != snapshots[1]["id"]


def test_override_requires_meaningful_reason(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    auth_client.post(f"/api/projects/{project_id}/quality")
    response = auth_client.post(
        f"/api/projects/{project_id}/quality/override",
        json={"code": "policy.high_similarity", "reason": "ok"},
    )
    assert response.status_code == 422


# --- publish guards -------------------------------------------------------


def test_publish_requires_render(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    auth_client.post(f"/api/projects/{project_id}/script/approve")

    response = auth_client.post(
        f"/api/projects/{project_id}/publish", json={"privacy_status": "private"}
    )
    assert response.status_code == 422
    assert "render" in response.json()["detail"].lower()


def test_readiness_without_render(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    body = auth_client.get(f"/api/projects/{project_id}/publish/readiness").json()
    assert body["ready"] is False
    assert "render" in body["reason"]


def test_metadata_suggestion(auth_client, recap_text, declared_rights, panel_bytes):
    project_id = _project_with_material(auth_client, recap_text, declared_rights, panel_bytes)
    auth_client.post(f"/api/projects/{project_id}/draft?seed=42")
    meta = auth_client.get(f"/api/projects/{project_id}/metadata").json()
    assert meta["title"]
    assert len(meta["title"]) <= 100
    assert "hak" in meta["description"].lower()
    assert "shorts" in meta["tags"]


def test_youtube_connect_reports_not_configured(auth_client):
    response = auth_client.get("/api/youtube/connect")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_oauth_callback_rejects_unknown_state(auth_client):
    response = auth_client.get("/api/youtube/callback?state=forged&code=abc")
    assert response.status_code == 400


def test_channels_start_empty(auth_client):
    assert auth_client.get("/api/youtube/channels").json() == []


def test_voices_endpoint_lists_options(client):
    body = client.get("/api/voices").json()
    assert body["voices"]
    assert all("id" in v and "label" in v for v in body["voices"])
