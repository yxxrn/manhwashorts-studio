"""BYOK tests: bring your own key for analysis and narration (v1.1).

Everything runs against ``tests/mock_provider.py``, a local stand-in that speaks
the OpenAI wire format. No network, no real key, no spend.

The security assertions matter as much as the functional ones: several tests
exist purely to prove a key never reaches a response body, a log line, an audit
record, or the database in plaintext.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.constants import CredentialKind, CredentialStatus
from app.models import AuditLog, SourceAsset, User, Workspace
from app.security import hash_password
from app.services import credentials as cred_svc
from app.services import providers as pv
from app.services import resolver as resolver_svc

pytestmark = pytest.mark.usefixtures("app_settings")


# --- provider adapters -----------------------------------------------------


def test_catalog_covers_expected_vendors():
    catalog = pv.catalog()
    llm_keys = {p["key"] for p in catalog["llm"]}
    tts_keys = {p["key"] for p in catalog["tts"]}

    # The point of BYOK is choice; assert the major shapes are all present.
    assert {"openai", "anthropic", "google", "openrouter", "custom_openai"} <= llm_keys
    assert {"openai", "elevenlabs", "custom_openai"} <= tts_keys


def test_unknown_provider_is_rejected():
    with pytest.raises(pv.ProviderError):
        pv.get_spec(CredentialKind.LLM, "definitely-not-a-vendor")


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com", "gopher://x", "not-a-url"],
)
def test_base_url_scheme_is_restricted(url):
    """The endpoint is user input, so only http(s) may ever be dialled."""
    with pytest.raises(pv.ProviderError):
        pv.validate_base_url(url)


def test_base_url_normalises_trailing_slash():
    assert pv.validate_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    assert pv.validate_base_url("  ") is None
    assert pv.validate_base_url(None) is None


def test_verify_rejects_bad_key(mock_provider_url):
    result = pv.verify_credential("llm", "custom_openai", "sk-wrong-0000", mock_provider_url)
    assert result.ok is False
    assert "401" in result.message


def test_verify_fetches_models_with_good_key(mock_provider_url, good_key):
    """The headline BYOK feature: the model list comes from the user's own key."""
    result = pv.verify_credential("llm", "custom_openai", good_key, mock_provider_url)
    assert result.ok is True
    assert [m.id for m in result.models] == ["mock-large", "mock-small", "tts-1"]


def test_verify_reports_unreachable_endpoint():
    result = pv.verify_credential(
        "llm", "custom_openai", "sk-any-key-here", "http://127.0.0.1:1/v1"
    )
    assert result.ok is False
    assert "connect" in result.message.lower()


def test_empty_key_is_rejected_without_a_network_call():
    result = pv.verify_credential("llm", "openai", "   ")
    assert result.ok is False
    assert "empty" in result.message.lower()


def test_custom_provider_requires_base_url():
    result = pv.verify_credential("llm", "custom_openai", "sk-key-value-here", None)
    assert result.ok is False
    assert "base URL" in result.message


def test_error_text_never_echoes_the_key(mock_provider_url):
    """A vendor that echoes the key back must not leak it into our message."""
    leaky = "sk-this-value-must-never-appear-12345"
    result = pv.verify_credential("llm", "custom_openai", leaky, mock_provider_url)
    assert result.ok is False
    assert leaky not in result.message
    assert "[redacted]" in result.message or "sk-this-value" not in result.message


# --- credential storage ----------------------------------------------------


@pytest.fixture()
def workspace(db):
    user = User(email="byok@example.com", password_hash=hash_password("testpass1234"))
    db.add(user)
    db.flush()
    ws = Workspace(owner_id=user.id, name="BYOK WS")
    db.add(ws)
    db.flush()
    return ws


def test_save_credential_stores_ciphertext_only(db, workspace, mock_provider_url, good_key):
    row, result = cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
    )
    assert result.ok
    assert row.status == CredentialStatus.VERIFIED
    assert row.model == "mock-large"
    # The stored blob must not contain the key, and must decrypt back to it.
    assert good_key not in row.encrypted_secret
    assert cred_svc.reveal_secret(row) == good_key
    # Only the tail is exposed for display.
    assert row.key_hint == f"...{good_key[-4:]}"


def test_broken_key_is_never_persisted(db, workspace, mock_provider_url):
    with pytest.raises(cred_svc.CredentialError):
        cred_svc.save_credential(
            db,
            workspace_id=workspace.id,
            actor_id="tester",
            kind="llm",
            provider="custom_openai",
            api_key="sk-invalid-key-999",
            base_url=mock_provider_url,
        )
    assert cred_svc.list_credentials(db, workspace.id) == []


def test_unavailable_model_is_refused(db, workspace, mock_provider_url, good_key):
    """Silently substituting a model would bill the user for something else."""
    with pytest.raises(cred_svc.CredentialError, match="not available"):
        cred_svc.save_credential(
            db,
            workspace_id=workspace.id,
            actor_id="tester",
            kind="llm",
            provider="custom_openai",
            api_key=good_key,
            base_url=mock_provider_url,
            model="gpt-5-imaginary",
        )


def test_resaving_replaces_rather_than_duplicates(db, workspace, mock_provider_url, good_key):
    for _ in range(3):
        cred_svc.save_credential(
            db,
            workspace_id=workspace.id,
            actor_id="tester",
            kind="llm",
            provider="custom_openai",
            api_key=good_key,
            base_url=mock_provider_url,
            model="mock-large",
        )
    rows = cred_svc.list_credentials(db, workspace.id, "llm")
    assert len(rows) == 1, "rotating a key must not accumulate stale ciphertext"


def test_refresh_clears_a_model_that_disappeared(db, workspace, mock_provider_url, good_key):
    row, _ = cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
    )
    # Simulate the vendor retiring the selected model.
    row.model = "model-that-was-removed"
    db.flush()

    refreshed, result = cred_svc.refresh_models(db, workspace.id, row.id, "tester")
    assert result.ok
    assert refreshed.model == "", "a retired model must not stay selected"
    assert "no longer offered" in refreshed.status_message


def test_delete_removes_the_row(db, workspace, mock_provider_url, good_key):
    row, _ = cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
    )
    cred_svc.delete_credential(db, workspace.id, row.id, "tester")
    assert cred_svc.list_credentials(db, workspace.id) == []


def test_cross_workspace_access_is_denied(db, workspace, mock_provider_url, good_key):
    row, _ = cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
    )
    # A real second user: workspaces.owner_id is a foreign key.
    intruder = User(email="intruder@example.com", password_hash=hash_password("testpass1234"))
    db.add(intruder)
    db.flush()
    other = Workspace(owner_id=intruder.id, name="Theirs")
    db.add(other)
    db.flush()

    with pytest.raises(cred_svc.CredentialError):
        cred_svc.get_credential(db, other.id, row.id)


def test_audit_log_records_provider_but_not_key(db, workspace, mock_provider_url, good_key):
    cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
    )
    entries = db.query(AuditLog).filter(AuditLog.action == "credential.saved").all()
    assert entries, "saving a credential must be audited"
    assert good_key not in str([e.detail for e in entries])
    assert entries[-1].detail["provider"] == "custom_openai"


# --- resolution order ------------------------------------------------------


def test_resolver_defaults_to_local_without_a_key(db, workspace):
    """v1.0 offline behaviour must not regress when no key is present."""
    decision = resolver_svc.describe_llm(db, workspace.id)
    assert decision.source == "local"
    assert decision.provider == "rules"

    analyzer, _ = resolver_svc.resolve_analyzer(db, workspace.id)
    assert type(analyzer).__name__ == "RulesAnalyzer"


def test_resolver_prefers_byok_once_verified(db, workspace, mock_provider_url, good_key):
    cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
    )
    decision = resolver_svc.describe_llm(db, workspace.id)
    assert decision.source == "byok"
    assert decision.model == "mock-large"

    analyzer, _ = resolver_svc.resolve_analyzer(db, workspace.id)
    assert type(analyzer).__name__ == "ByokAnalyzer"


def test_credential_without_a_model_is_not_used(db, workspace, mock_provider_url, good_key):
    """No model selected means we must not guess and spend the user's credits."""
    row, _ = cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
    )
    assert row.model == ""
    assert cred_svc.active_credential(db, workspace.id, "llm") is None
    assert resolver_svc.describe_llm(db, workspace.id).source == "local"


def test_tts_override_beats_byok(db, workspace, mock_provider_url, good_key):
    """Seed scripts and tests force a local provider; that must always win."""
    cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="tts",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="tts-1",
    )
    assert resolver_svc.describe_tts(db, workspace.id).source == "byok"

    _, decision = resolver_svc.resolve_tts(db, workspace.id, override="null")
    assert decision.source == "local"
    assert decision.provider == "null"


# --- generation through a user key -----------------------------------------


def test_byok_analyzer_uses_provider_output(mock_provider_url, good_key):
    from app.services.analysis import ByokAnalyzer

    analyzer = ByokAnalyzer(
        "custom_openai", good_key, "mock-large", mock_provider_url, label="Mock"
    )
    result = analyzer.analyze([(0, "Rian masuk menara. Penjaga menghadang.")])

    assert result.generator.startswith("byok")
    assert result.twist == "Penjaga menara itu ayahnya sendiri"
    assert [c.name for c in result.characters] == ["Rian", "Kaela"]
    assert len(result.events) == 4


def test_byok_analyzer_degrades_visibly_on_failure(mock_provider_url, recap_text):
    """A failed key costs a weaker analysis, never a dead pipeline - but it says so."""
    from app.services.analysis import ByokAnalyzer

    analyzer = ByokAnalyzer(
        "custom_openai", "sk-rejected-key-1", "mock-large", mock_provider_url, label="Mock"
    )
    result = analyzer.analyze([(0, recap_text)])

    assert result.generator == "rules", "must fall back to the offline analyser"
    assert any("failed" in note.lower() for note in result.low_confidence_notes)
    assert "sk-rejected-key-1" not in " ".join(result.low_confidence_notes)


def test_byok_analyzer_survives_non_json_reply(monkeypatch, mock_provider_url, good_key):
    from app.services.analysis import ByokAnalyzer

    analyzer = ByokAnalyzer(
        "custom_openai", good_key, "mock-large", mock_provider_url, label="Mock"
    )
    monkeypatch.setattr(
        analyzer._adapter, "chat_json", lambda **kwargs: "I'm afraid I can't do that"
    )
    result = analyzer.analyze([(0, "Rian masuk menara.")])

    assert result.generator == "rules"
    assert any("invalid JSON" in note for note in result.low_confidence_notes)


def test_code_fenced_json_is_accepted(monkeypatch, mock_provider_url, good_key):
    """Several models wrap JSON in ``` fences despite being told not to."""
    from app.services.analysis import ByokAnalyzer

    analyzer = ByokAnalyzer(
        "custom_openai", good_key, "mock-large", mock_provider_url, label="Mock"
    )
    monkeypatch.setattr(
        analyzer._adapter,
        "chat_json",
        lambda **kwargs: '```json\n{"twist": "terungkap", "events": []}\n```',
    )
    result = analyzer.analyze([(0, "Rian masuk menara.")])

    assert result.twist == "terungkap"
    assert result.generator.startswith("byok")


def test_byok_tts_produces_real_audio(tmp_path, mock_provider_url, good_key):
    from app.services.tts import ByokProvider

    provider = ByokProvider(
        "custom_openai", good_key, "tts-1", mock_provider_url, label="Mock TTS"
    )
    out = tmp_path / "speech.wav"
    clip = provider.synthesize("Ini narasi percobaan.", out)

    assert out.exists() and out.stat().st_size > 1000
    assert clip.duration > 1.0
    assert clip.provider == "byok:custom_openai"
    assert clip.word_timings, "subtitles need word timings"


def test_byok_tts_does_not_silently_downgrade(tmp_path, mock_provider_url):
    """A paid voice failing must raise, not quietly hand back robotic espeak."""
    from app.services.tts import ByokProvider, TTSError

    provider = ByokProvider(
        "custom_openai", "sk-rejected-key-2", "tts-1", mock_provider_url, label="Mock TTS"
    )
    with pytest.raises(TTSError):
        provider.synthesize("Ini narasi percobaan.", tmp_path / "x.wav")


# --- HTTP surface ----------------------------------------------------------


def test_credential_routes_require_auth(client):
    assert client.get("/api/credentials").status_code == 401
    assert client.get("/api/credentials/active").status_code == 401
    assert client.post("/api/credentials", json={}).status_code == 401


def test_provider_catalogue_is_public(client):
    """The form needs this before anything is saved; it holds no user data."""
    response = client.get("/api/credentials/providers")
    assert response.status_code == 200
    assert response.json()["llm"]


def test_http_save_then_use(auth_client, mock_provider_url, good_key):
    test = auth_client.post(
        "/api/credentials/test",
        json={
            "kind": "llm",
            "provider": "custom_openai",
            "api_key": good_key,
            "base_url": mock_provider_url,
        },
    )
    assert test.status_code == 200
    assert test.json()["ok"] is True
    models = [m["id"] for m in test.json()["models"]]
    assert "mock-large" in models

    saved = auth_client.post(
        "/api/credentials",
        json={
            "kind": "llm",
            "provider": "custom_openai",
            "api_key": good_key,
            "base_url": mock_provider_url,
            "model": "mock-large",
            "label": "Mock LLM",
        },
    )
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert good_key not in saved.text, "the key must never come back in a response"
    assert body["key_hint"] == f"...{good_key[-4:]}"

    active = auth_client.get("/api/credentials/active").json()
    assert active["llm"]["source"] == "byok"
    assert active["llm"]["model"] == "mock-large"


def test_http_rejects_bad_key_as_400_not_500(auth_client, mock_provider_url):
    response = auth_client.post(
        "/api/credentials",
        json={
            "kind": "llm",
            "provider": "custom_openai",
            "api_key": "sk-bad-key-here-1",
            "base_url": mock_provider_url,
        },
    )
    assert response.status_code == 400
    assert "401" in response.json()["detail"]


def test_http_test_route_returns_ok_false_for_bad_key(auth_client, mock_provider_url):
    """A rejected key is an expected outcome, not a server error."""
    response = auth_client.post(
        "/api/credentials/test",
        json={
            "kind": "llm",
            "provider": "custom_openai",
            "api_key": "sk-bad-key-here-2",
            "base_url": mock_provider_url,
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_http_delete_reverts_to_local(auth_client, mock_provider_url, good_key):
    saved = auth_client.post(
        "/api/credentials",
        json={
            "kind": "llm",
            "provider": "custom_openai",
            "api_key": good_key,
            "base_url": mock_provider_url,
            "model": "mock-large",
        },
    )
    cred_id = saved.json()["id"]
    assert auth_client.get("/api/credentials/active").json()["llm"]["source"] == "byok"

    deleted = auth_client.delete(f"/api/credentials/{cred_id}")
    assert deleted.status_code == 200
    assert auth_client.get("/api/credentials/active").json()["llm"]["source"] == "local"


def test_http_cannot_touch_another_users_credential(client, mock_provider_url, good_key):
    client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "testpass1234"},
    )
    saved = client.post(
        "/api/credentials",
        json={
            "kind": "llm",
            "provider": "custom_openai",
            "api_key": good_key,
            "base_url": mock_provider_url,
            "model": "mock-large",
        },
    )
    cred_id = saved.json()["id"]
    client.post("/api/auth/logout")

    client.post(
        "/api/auth/register",
        json={"email": "intruder@example.com", "password": "testpass1234"},
    )
    assert client.get("/api/credentials").json() == []
    assert client.delete(f"/api/credentials/{cred_id}").status_code == 400
    assert client.post(f"/api/credentials/{cred_id}/refresh").status_code == 400


def test_database_never_holds_the_plaintext_key(auth_client, mock_provider_url, good_key):
    """Read the raw DB file: the key must not be findable anywhere in it."""
    saved = auth_client.post(
        "/api/credentials",
        json={
            "kind": "llm",
            "provider": "custom_openai",
            "api_key": good_key,
            "base_url": mock_provider_url,
            "model": "mock-large",
        },
    )
    assert saved.status_code == 201, saved.text

    # Ask the engine where the file is rather than parsing the URL by hand.
    from app.db import engine

    db_path = engine.url.database
    assert db_path, "expected a file-backed SQLite database in tests"
    connection = sqlite3.connect(db_path)
    try:
        blob, hint = connection.execute(
            "SELECT encrypted_secret, key_hint FROM provider_credentials"
        ).fetchone()
    finally:
        connection.close()

    assert good_key not in blob
    assert blob.startswith("gAAAAA"), "expected a Fernet token"
    assert hint == f"...{good_key[-4:]}"


# --- pipeline integration --------------------------------------------------


def test_pipeline_analysis_uses_the_users_key(
    db, workspace, mock_provider_url, good_key, recap_text
):
    """The whole point: a saved key must actually change what the pipeline does."""
    from app.constants import AssetType, LicenseType, RightsStatus
    from app.models import Project
    from app.services import pipeline as pl
    from app.services import storage

    project = Project(workspace_id=workspace.id, title="BYOK", manhwa_title="M", chapter="1")
    db.add(project)
    db.flush()
    stored = storage.put_bytes(f"projects/{project.id}/text", "r.txt", recap_text.encode())
    db.add(
        SourceAsset(
            project_id=project.id,
            type=AssetType.TEXT,
            original_filename="r.txt",
            storage_key=stored.storage_key,
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            mime_type="text/plain",
            extracted_text=recap_text,
            rights_status=RightsStatus.DECLARED,
            license_type=LicenseType.OWNED,
            rights_owner="Tester",
        )
    )
    db.flush()

    # Without a key: rule-based, and it says so.
    offline = pl.run_analysis(db, project.id, "tester")
    assert any("rule-based" in n for n in offline.low_confidence_notes)

    cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="tester",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
        label="Mock LLM",
    )

    # With a key: the provider's output reaches the database.
    online = pl.run_analysis(db, project.id, "tester")
    assert online.twist == "Penjaga menara itu ayahnya sendiri"
    assert any("using your Mock LLM key" in n for n in online.low_confidence_notes)

    entry = (
        db.query(AuditLog)
        .filter(AuditLog.action == "analysis.run")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert entry.detail["provider_source"] == "byok"
    assert entry.detail["model"] == "mock-large"
    assert good_key not in str(entry.detail)
