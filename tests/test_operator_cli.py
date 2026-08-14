"""Contract tests for the Windows-first interactive operator CLI.

Imports are intentionally performed inside test bodies so a missing CLI module
produces ordinary body-level RED failures rather than a collection failure.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest


def _cli_module():
    return importlib.import_module("app.services.operator_cli")


@pytest.fixture(autouse=True)
def _allow_windows_test_db_path(monkeypatch):
    """Keep the shared destructive-test guard safe on Windows URL slashes."""

    if importlib.util.find_spec("sqlalchemy") is None:
        return
    from app import db as app_db

    def safe_drop_all(metadata, bind):
        normalized = str(bind.url).replace("\\", "/")
        if bind.url.get_backend_name() != "sqlite" or "/data/test_runs/" not in normalized:
            raise RuntimeError(f"refusing non-test database: {bind.url}")
        metadata.drop_all(bind=bind)

    monkeypatch.setattr(app_db, "safe_drop_all", safe_drop_all)


def test_endpoint_normalization_derives_models_url_without_query_or_credentials():
    cli = _cli_module()

    endpoint = cli.normalize_endpoint("https://api.example.test/v1/")

    assert endpoint.base_url == "https://api.example.test/v1"
    assert endpoint.models_url == "https://api.example.test/v1/models"
    assert "?" not in endpoint.base_url
    assert "@" not in endpoint.models_url


def test_explicit_models_url_is_normalized_and_base_is_derived():
    cli = _cli_module()

    endpoint = cli.normalize_endpoint(
        "https://gateway.example.test/v1",
        explicit_models_url="https://gateway.example.test/v1/models/",
    )

    assert endpoint.base_url == "https://gateway.example.test/v1"
    assert endpoint.models_url == "https://gateway.example.test/v1/models"


@pytest.mark.parametrize(
    "value",
    [
        "ftp://gateway.example.test/v1",
        "https://user:password@gateway.example.test/v1",
        "https://gateway.example.test/v1?api_key=secret",
        "not-a-url",
    ],
)
def test_endpoint_normalization_rejects_unsafe_url_forms(value):
    cli = _cli_module()

    with pytest.raises(cli.OperatorCliError, match="operator.endpoint_invalid"):
        cli.normalize_endpoint(value)


def test_model_payload_is_strict_deduplicated_and_sorted():
    cli = _cli_module()

    models = cli.parse_models_payload(
        {
            "data": [
                {"id": "vision-z"},
                {"id": "vision-a", "owned_by": "provider"},
                {"id": "vision-z"},
            ]
        }
    )

    assert [model.model_id for model in models] == ["vision-a", "vision-z"]


def test_malformed_model_payload_fails_without_echoing_provider_data():
    cli = _cli_module()

    with pytest.raises(cli.OperatorCliError, match="operator.models_invalid") as caught:
        cli.parse_models_payload({"data": [{"name": "sk-secret-value"}]})

    assert "sk-secret-value" not in str(caught.value)


def test_fetch_models_retries_and_sanitizes_auth_or_payload_errors():
    cli = _cli_module()
    secret = "sk-test-secret-123456"

    class Response:
        def raise_for_status(self):
            raise RuntimeError(f"Authorization Bearer {secret} rejected")

        def json(self):
            return {"data": []}

    calls = []

    def request_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    with pytest.raises(cli.OperatorCliError, match="operator.provider_unreachable") as caught:
        cli.fetch_models(
            cli.normalize_endpoint("https://gateway.example.test/v1"),
            secret,
            request_get=request_get,
            retries=2,
        )

    assert len(calls) == 2
    assert secret not in str(caught.value)
    assert "Authorization" not in str(caught.value)


def test_model_selector_supports_filter_and_manual_known_id_but_not_unknown_id():
    cli = _cli_module()
    models = cli.parse_models_payload({"data": [{"id": "vision-a"}, {"id": "vision-b"}]})

    assert cli.select_model(models, query="b", selection="1").model_id == "vision-b"
    assert cli.select_model(models, query="", selection="manual:vision-a").model_id == "vision-a"
    with pytest.raises(cli.OperatorCliError, match="operator.model_unavailable"):
        cli.select_model(models, query="", selection="manual:not-listed")


def test_capability_probe_requires_explicit_consent_before_runner_creation():
    cli = _cli_module()
    calls = []

    def runner_factory(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("provider runner must not be created without consent")

    result = cli.run_capability_probe(
        object(),
        "workspace-1",
        consent=False,
        runner_factory=runner_factory,
    )

    assert result.attempted is False
    assert result.code == "operator.capability_consent_required"
    assert calls == []


def test_capability_probe_uses_generated_local_image_and_accepts_only_structured_result():
    cli = _cli_module()
    calls = []

    class Runner:
        def run_visual_evidence(self, panels):
            calls.append(tuple(panels))
            return {"visual": "structured"}

    result = cli.run_capability_probe(
        object(),
        "workspace-1",
        consent=True,
        runner_factory=lambda *args, **kwargs: Runner(),
    )

    assert result.attempted is True
    assert result.ok is True
    assert len(calls) == 1
    assert calls[0][0].source_order == 0
    assert calls[0][0].mime_type.startswith("image/")
    assert calls[0][0].payload


def test_discover_chapter_folder_is_deterministic_and_rejects_non_images(tmp_path):
    cli = _cli_module()
    (tmp_path / "02.PNG").write_bytes(b"two")
    (tmp_path / "01.jpg").write_bytes(b"one")

    chapter = cli.discover_chapter_folder(tmp_path)

    assert [path.name for path in chapter.image_paths] == ["01.jpg", "02.PNG"]
    assert chapter.folder == tmp_path.resolve()

    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    with pytest.raises(cli.OperatorCliError, match="operator.unsupported_file"):
        cli.discover_chapter_folder(tmp_path)


def test_chapter_manifest_is_stable_and_changes_when_source_bytes_change(tmp_path):
    cli = _cli_module()
    image = tmp_path / "01.png"
    image.write_bytes(b"first")

    first = cli.chapter_manifest(cli.discover_chapter_folder(tmp_path))
    second = cli.chapter_manifest(cli.discover_chapter_folder(tmp_path))
    image.write_bytes(b"second")
    third = cli.chapter_manifest(cli.discover_chapter_folder(tmp_path))

    assert first == second
    assert first.manifest_sha256 != third.manifest_sha256


def test_batch_folder_discovery_is_casefold_sorted_and_rejects_invalid_child(tmp_path):
    cli = _cli_module()
    for name in ("B-chapter", "a-chapter"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "01.png").write_bytes(b"image")

    chapters = cli.discover_batch_folders(tmp_path)

    assert [chapter.folder.name for chapter in chapters] == ["a-chapter", "B-chapter"]

    (tmp_path / "bad-chapter").mkdir()
    (tmp_path / "bad-chapter" / "notes.txt").write_text("unsupported", encoding="utf-8")
    with pytest.raises(cli.OperatorCliError, match="operator.unsupported_file"):
        cli.discover_batch_folders(tmp_path)


def test_request_budget_parser_has_safe_defaults_and_rejects_negative_values():
    cli = _cli_module()

    assert cli.parse_run_options({}) == cli.RunOptions()
    assert cli.parse_run_options({"max_requests": "10", "estimated_cost_per_request": "0.02"}).max_requests == 10
    with pytest.raises(cli.OperatorCliError, match="operator.request_budget_invalid"):
        cli.parse_run_options({"max_attempts": "0"})


def test_job_state_listing_is_sorted_and_does_not_expose_provider_payload(tmp_path):
    cli = _cli_module()
    (tmp_path / "b.json").write_text(
        '{"job_id":"b","state":"FAILED","error_code":"cloud.provider_request_failed",'
        '"error_message":"safe"}',
        encoding="utf-8",
    )
    (tmp_path / "a.json").write_text(
        '{"job_id":"a","state":"READY_TO_RENDER","stage_results":{}}',
        encoding="utf-8",
    )

    rows = cli.list_job_states(tmp_path)

    assert [row["job_id"] for row in rows] == ["a", "b"]
    assert all("payload" not in row for row in rows)


def test_project_folder_path_must_be_a_directory(tmp_path):
    cli = _cli_module()
    file_path = tmp_path / "chapter.png"
    file_path.write_bytes(b"x")

    with pytest.raises(cli.OperatorCliError, match="operator.chapter_folder_invalid"):
        cli.discover_chapter_folder(file_path)


def test_operator_menu_can_exit_without_provider_or_database(monkeypatch):
    cli = _cli_module()
    output = []
    answers = iter(["0"])
    app = cli.OperatorCLI(
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
        secret_fn=lambda _prompt: "never-used",
        db_factory=lambda: (_ for _ in ()).throw(AssertionError("DB must not open on exit")),
    )

    assert app.run() == 0
    assert any("Exit" in line or "Keluar" in line for line in output)


def test_safe_error_text_never_contains_secret_or_raw_provider_body():
    cli = _cli_module()
    secret = "sk-test-secret-123456"

    message = cli.safe_error_text(
        RuntimeError(f"HTTP 401 Authorization: Bearer {secret}; body={{'key': '{secret}'}}"),
        secret=secret,
    )

    assert secret not in message
    assert "Bearer" not in message
    assert "body" not in message


def test_setup_provider_uses_hidden_key_and_existing_byok_boundary(monkeypatch):
    cli = _cli_module()
    from types import SimpleNamespace

    secret = "sk-operator-hidden-123456"
    captured = {}
    output = []

    class Providers:
        class ProviderError(Exception):
            pass

        @staticmethod
        def get_spec(_kind, _provider):
            return SimpleNamespace()

    class Credentials:
        @staticmethod
        def save_credential(db, **kwargs):
            captured.update(kwargs)
            assert db == "db"
            return SimpleNamespace(key_hint="sk-...3456"), SimpleNamespace()

    class Context:
        def __enter__(self):
            return "db"

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli, "_providers", lambda: Providers())
    monkeypatch.setattr(cli, "_credentials", lambda: Credentials())
    monkeypatch.setattr(
        cli,
        "fetch_models",
        lambda _endpoint, api_key: (
            captured.setdefault("fetched_key", api_key),
            cli.ModelChoice("vision-model"),
        )[1:],
    )
    monkeypatch.setattr(
        cli,
        "resolve_operator_context",
        lambda _db: (SimpleNamespace(id="user-1"), SimpleNamespace(id="workspace-1")),
    )
    answers = iter(["openai", "https://gateway.example.test/v1", "", "operator"])
    app = cli.OperatorCLI(
        input_fn=lambda _prompt: next(answers),
        output_fn=output.append,
        secret_fn=lambda _prompt: secret,
        db_factory=lambda: Context(),
    )

    app.setup_provider()

    assert captured["fetched_key"] == secret
    assert captured["api_key"] == secret
    assert secret not in "\n".join(output)
    assert secret not in str(captured.get("models", ""))


def test_operator_run_projects_isolates_failures_and_preserves_safe_states():
    cli = _cli_module()
    calls = []

    class Record:
        def __init__(self, job_id, state, code=""):
            self.job_id = job_id
            self.state = state
            self.error_code = code

        def as_dict(self):
            return {"job_id": self.job_id, "state": self.state, "error_code": self.error_code}

    class Service:
        def run_project(self, _db, project_id, actor_id=""):
            calls.append((project_id, actor_id))
            if project_id == "bad":
                return Record(project_id, "NEEDS_REVIEW", "segmentation.ambiguous_boundary")
            return Record(project_id, "READY_TO_RENDER")

    results = cli.run_projects(
        object(),
        ["bad", "good"],
        service_factory=lambda **_kwargs: Service(),
        db_factory=lambda: object(),
        actor_id="operator-1",
    )

    assert [row["job_id"] for row in results] == ["bad", "good"]
    assert results[0]["state"] == "NEEDS_REVIEW"
    assert results[1]["state"] == "READY_TO_RENDER"
    assert calls == [("bad", "operator-1"), ("good", "operator-1")]


def test_operator_run_projects_propagates_ctrl_c_without_converting_it_to_a_fake_failure():
    cli = _cli_module()

    class Service:
        def run_project(self, _db, _project_id, actor_id=""):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        cli.run_projects(
            object(),
            ["chapter-1"],
            service_factory=lambda **_kwargs: Service(),
        )


def test_launcher_exists_and_selects_project_venv_without_shell_arguments():
    root = Path(__file__).parents[1]
    launcher = root / "run_operator.cmd"
    powershell_launcher = root / "scripts" / "operator_launcher.ps1"

    assert launcher.is_file()
    launcher_text = launcher.read_text(encoding="utf-8")
    powershell_text = powershell_launcher.read_text(encoding="utf-8")
    assert '"%~dp0scripts\\operator_launcher.ps1"' in launcher_text
    assert ".venv\\Scripts\\python.exe" in powershell_text
    assert "scripts\\bootstrap_operator_cli.py" in powershell_text
    assert "%*" not in launcher_text


@pytest.mark.skipif(
    importlib.util.find_spec("sqlalchemy") is None,
    reason="requires the repository dependency-complete test environment",
)
def test_existing_encrypted_byok_boundary_never_persists_plaintext_key(db, monkeypatch):
    cli = _cli_module()
    from app.constants import CredentialKind
    from app.models import User, Workspace
    from app.services import credentials, providers

    user = User(email="operator@example.com", name="Operator", password_hash="x")
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Operator Workspace")
    db.add(workspace)
    db.flush()
    secret = "sk-operator-secret-123456"
    monkeypatch.setattr(
        providers,
        "verify_credential",
        lambda *args, **kwargs: providers.VerificationResult(
            ok=True,
            models=[providers.ModelInfo("vision-model")],
            message="accepted",
        ),
    )

    row, _ = credentials.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id=user.id,
        kind=CredentialKind.LLM,
        provider="openai",
        api_key=secret,
        base_url="https://gateway.example.test/v1",
        model="vision-model",
        verify=True,
    )

    assert secret not in row.encrypted_secret
    assert secret not in str(cli.safe_error_text(RuntimeError("provider accepted"), secret=secret))
    assert credentials.public_view(row)["key_hint"] == row.key_hint
    assert "api_key" not in credentials.public_view(row)


@pytest.mark.skipif(
    importlib.util.find_spec("sqlalchemy") is None,
    reason="requires the repository dependency-complete test environment",
)
def test_import_chapter_uses_ingest_and_is_idempotent(db, tmp_path):
    cli = _cli_module()
    from PIL import Image

    from app.models import User, Workspace

    image = Image.new("RGB", (240, 240), (30, 50, 80))
    image_path = tmp_path / "01.png"
    image.save(image_path, format="PNG")
    user = User(email="importer@example.com", name="Importer", password_hash="x")
    db.add(user)
    db.flush()
    workspace = Workspace(owner_id=user.id, name="Import Workspace")
    db.add(workspace)
    db.flush()
    chapter = cli.discover_chapter_folder(tmp_path)

    first = cli.import_chapter_folder(db, chapter, workspace_id=workspace.id, actor_id=user.id)
    second = cli.import_chapter_folder(db, chapter, workspace_id=workspace.id, actor_id=user.id)

    assert first.created is True
    assert second.created is False
    assert first.project_id == second.project_id
