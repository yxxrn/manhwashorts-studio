"""RED contract for the fail-closed vision resolver boundary."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from app.constants import CredentialStatus
from app.models import ProviderCredential, User, Workspace
from app.security import hash_password
from app.services import credentials as cred_svc
from app.services import resolver as resolver_svc

pytestmark = pytest.mark.usefixtures("app_settings")


@pytest.fixture()
def workspace(db):
    user = User(
        email="resolver-vision@example.com",
        password_hash=hash_password("testpass1234"),
    )
    db.add(user)
    db.flush()
    result = Workspace(owner_id=user.id, name="Resolver Vision WS")
    db.add(result)
    db.flush()
    return result


def _resolve_vision():
    """Keep the RED failure in the test body while the boundary is absent."""
    resolver = getattr(resolver_svc, "resolve_vision", None)
    assert callable(
        resolver
    ), "app.services.resolver.resolve_vision is absent (Task 6 RED boundary)"
    return resolver


def _assert_safe_report(report, *forbidden: str) -> None:
    fields = (
        "provider_type",
        "provider_name",
        "model",
        "image_input",
        "structured_json",
        "available",
        "blocking_reason",
    )
    serialised = json.dumps(
        {field: getattr(report, field, None) for field in fields},
        default=str,
        sort_keys=True,
    ).lower()
    assert "api_key" not in serialised
    assert "key_hint" not in serialised
    for value in forbidden:
        assert value.lower() not in serialised


def _add_verified_credential(
    db,
    workspace: Workspace,
    *,
    provider: str,
    model: str,
    base_url: str | None,
    key_hint: str = "...MASK",
) -> ProviderCredential:
    row = ProviderCredential(
        workspace_id=workspace.id,
        kind="llm",
        provider=provider,
        label=f"{provider} test credential",
        encrypted_secret="ciphertext-only-test-value",
        key_hint=key_hint,
        base_url=base_url,
        model=model,
        status=CredentialStatus.VERIFIED,
        is_default=True,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def test_resolve_vision_blocks_local_rules_without_verified_credential(
    db, workspace, monkeypatch
):
    monkeypatch.setattr(resolver_svc.settings, "llm_provider", "rules")
    monkeypatch.setattr(resolver_svc.settings, "llm_api_key", None)

    resolve_vision = _resolve_vision()
    provider, report = resolve_vision(db, workspace.id)

    assert provider is None
    assert report.available is False
    assert report.blocking_reason == "vision_capability_missing"
    assert report.image_input is False
    assert report.structured_json is False
    _assert_safe_report(report)


def test_resolve_vision_builds_custom_openai_provider_without_capability_network(
    db, workspace, mock_provider_url, good_key, monkeypatch
):
    resolve_vision = _resolve_vision()
    row, result = cred_svc.save_credential(
        db,
        workspace_id=workspace.id,
        actor_id="resolver-test",
        kind="llm",
        provider="custom_openai",
        api_key=good_key,
        base_url=mock_provider_url,
        model="mock-large",
    )
    assert result.ok

    from app.services import vision_adapter

    def network_is_forbidden(*args, **kwargs):
        raise AssertionError("vision capability checks must not make a network request")

    monkeypatch.setattr(vision_adapter.httpx, "post", network_is_forbidden)
    monkeypatch.setattr(vision_adapter.httpx, "get", network_is_forbidden)

    provider, report = resolve_vision(db, workspace.id)

    from app.services.vision_adapter import OpenAICompatibleVisionProvider

    assert isinstance(provider, OpenAICompatibleVisionProvider)
    assert report.available is True
    assert report.provider_type == "openai_compatible"
    assert report.provider_name == "openai_compatible"
    assert report.model == "mock-large"
    assert report.image_input is True
    assert report.structured_json is True
    assert report.blocking_reason is None
    assert provider.capability() == report
    _assert_safe_report(report, good_key, row.key_hint)


def test_resolve_vision_fails_closed_on_credential_decryption_error(
    db, workspace, mock_provider_url, monkeypatch
):
    resolve_vision = _resolve_vision()
    row = _add_verified_credential(
        db,
        workspace,
        provider="custom_openai",
        model="mock-large",
        base_url=mock_provider_url,
    )
    secret_error = "decrypt-sentinel-secret-must-not-leak"

    def fail_to_decrypt(_credential):
        raise cred_svc.CredentialError(secret_error)

    monkeypatch.setattr(cred_svc, "reveal_secret", fail_to_decrypt)

    provider, report = resolve_vision(db, workspace.id)

    assert provider is None
    assert report.available is False
    assert report.blocking_reason == "vision_capability_missing"
    _assert_safe_report(report, secret_error, row.key_hint)


def test_resolve_vision_rejects_explicitly_unsupported_llm_provider(
    db, workspace, monkeypatch
):
    resolve_vision = _resolve_vision()
    _add_verified_credential(
        db,
        workspace,
        provider="anthropic",
        model="claude-test",
        base_url=None,
    )

    def decryption_is_not_allowed(_credential):
        raise AssertionError("unsupported providers must fail before secret use")

    monkeypatch.setattr(cred_svc, "reveal_secret", decryption_is_not_allowed)

    provider, report = resolve_vision(db, workspace.id)

    assert provider is None
    assert report.available is False
    assert report.blocking_reason == "vision_provider_unsupported"
    _assert_safe_report(report)


def test_resolve_vision_uses_openai_compatible_environment_without_network(
    db, workspace, mock_provider_url, monkeypatch
):
    resolve_vision = _resolve_vision()
    env_key = "sk-environment-sentinel-must-not-appear"
    monkeypatch.setattr(resolver_svc.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(resolver_svc.settings, "llm_base_url", mock_provider_url)
    monkeypatch.setattr(resolver_svc.settings, "llm_model", "mock-large")
    monkeypatch.setattr(resolver_svc.settings, "llm_api_key", SecretStr(env_key))

    from app.services import vision_adapter

    def network_is_forbidden(*args, **kwargs):
        raise AssertionError("environment capability checks must not make a network request")

    monkeypatch.setattr(vision_adapter.httpx, "post", network_is_forbidden)
    monkeypatch.setattr(vision_adapter.httpx, "get", network_is_forbidden)

    provider, report = resolve_vision(db, workspace.id)

    from app.services.vision_adapter import OpenAICompatibleVisionProvider

    assert isinstance(provider, OpenAICompatibleVisionProvider)
    assert report.available is True
    assert report.model == "mock-large"
    assert report.blocking_reason is None
    assert provider.capability() == report
    _assert_safe_report(report, env_key)


def test_resolve_vision_rejects_invalid_explicit_endpoint_without_fallback(
    db, workspace, monkeypatch
):
    resolve_vision = _resolve_vision()
    _add_verified_credential(
        db,
        workspace,
        provider="openai",
        model="gpt-test",
        base_url="ftp://unexpected.example/v1",
    )
    calls: list[str] = []

    def record_reveal(_credential):
        calls.append("reveal_secret")
        return "sk-endpoint-test"

    def record_mark_used(_db, _credential):
        calls.append("mark_used")

    monkeypatch.setattr(cred_svc, "reveal_secret", record_reveal)
    monkeypatch.setattr(cred_svc, "mark_used", record_mark_used)

    provider, report = resolve_vision(db, workspace.id)

    assert provider is None
    assert report.available is False
    assert report.blocking_reason == "vision_capability_missing"
    assert calls == []
