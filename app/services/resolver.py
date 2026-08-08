"""Decide which provider a pipeline stage should use (v1.1 BYOK).

One place answers the question "whose key, which model?" so the analyse, script,
and voice stages cannot drift apart. The precedence is deliberate and narrow:

1. A **verified BYOK credential** with a selected model for this workspace.
2. Otherwise the **environment-configured** provider (``MS_LLM_PROVIDER`` etc).
3. Otherwise the **local deterministic** provider (rules / espeak).

Rule 3 is what keeps the app working offline with no keys at all, which is the
v1.0 behaviour and must not regress.

Every resolution returns a small report alongside the provider so callers can
tell the user *why* a stage behaved the way it did. Silent provider selection is
how people end up surprised by a bill or by robotic audio.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import settings
from app.constants import CredentialKind
from app.services import analysis as analysis_svc
from app.services import credentials as cred_svc
from app.services import providers as providers_svc
from app.services import tts as tts_svc
from app.services.vision_adapter import (
    OpenAICompatibleVisionProvider,
    VisionCapabilityReport,
    VisionObservationProvider,
)


@dataclass
class Resolution:
    """Which provider was chosen, and why."""

    #: "byok" | "env" | "local"
    source: str
    #: Vendor key for BYOK, or the local provider name.
    provider: str
    model: str = ""
    label: str = ""
    credential_id: str = ""
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_byok(self) -> bool:
        return self.source == "byok"

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "label": self.label,
            "credential_id": self.credential_id,
            "reason": self.reason,
        }


_VISION_OPENAI_COMPATIBLE_PROVIDERS = frozenset(
    {
        "openai",
        "openrouter",
        "groq",
        "deepseek",
        "mistral",
        "together",
        "xai",
        "custom_openai",
    }
)


def _vision_model(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _secret_value(value: object) -> str:
    if value is None:
        return ""
    try:
        getter = getattr(value, "get_secret_value", None)
        if callable(getter):
            value = getter()
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _vision_base_url(provider: str, value: object) -> str:
    candidate = value if isinstance(value, str) else None
    try:
        resolved = providers_svc.validate_base_url(candidate)
    except Exception:
        resolved = None
    if resolved:
        return resolved

    try:
        spec = providers_svc.get_spec(CredentialKind.LLM, provider)
        return providers_svc.validate_base_url(spec.default_base_url) or ""
    except Exception:
        return ""


def _vision_unavailable(
    provider_type: str,
    provider_name: str,
    model: str | None,
    blocking_reason: str,
) -> tuple[None, VisionCapabilityReport]:
    return None, VisionCapabilityReport(
        provider_type=provider_type,
        provider_name=provider_name,
        model=model,
        image_input=False,
        structured_json=False,
        available=False,
        blocking_reason=blocking_reason,
    )


def _build_vision_provider(
    provider: str,
    model: str | None,
    base_url: str,
    api_key: str,
) -> tuple[VisionObservationProvider | None, VisionCapabilityReport]:
    if not model or not base_url or not api_key:
        return _vision_unavailable(
            "openai_compatible",
            "openai_compatible",
            model,
            "vision_capability_missing",
        )

    adapter = OpenAICompatibleVisionProvider(
        base_url=base_url,
        model=model,
        api_key=api_key,
    )
    report = adapter.capability()
    if not report.available:
        return None, report
    return adapter, report


def resolve_vision(
    db: Session, workspace_id: str
) -> tuple[VisionObservationProvider | None, VisionCapabilityReport]:
    """Resolve a configured multimodal provider without a text fallback."""
    row = cred_svc.active_credential(db, workspace_id, CredentialKind.LLM)
    if row is not None:
        provider = str(row.provider)
        model = _vision_model(row.model)
        if provider not in _VISION_OPENAI_COMPATIBLE_PROVIDERS:
            return _vision_unavailable(
                provider,
                provider,
                model,
                "vision_provider_unsupported",
            )

        base_url = _vision_base_url(provider, row.base_url)
        if not model or not base_url:
            return _vision_unavailable(
                "openai_compatible",
                "openai_compatible",
                model,
                "vision_capability_missing",
            )
        try:
            api_key = cred_svc.reveal_secret(row)
        except Exception:
            return _vision_unavailable(
                "openai_compatible",
                "openai_compatible",
                model,
                "vision_capability_missing",
            )

        adapter, report = _build_vision_provider(provider, model, base_url, api_key)
        if adapter is None or not report.available:
            return None, report
        cred_svc.mark_used(db, row)
        return adapter, report

    configured_provider = str(settings.llm_provider or "").strip()
    if configured_provider == "openai_compatible":
        model = _vision_model(settings.llm_model)
        base_url = _vision_base_url(
            configured_provider,
            settings.llm_base_url,
        )
        api_key = _secret_value(settings.llm_api_key)
        return _build_vision_provider(
            configured_provider,
            model,
            base_url,
            api_key,
        )
    if configured_provider and configured_provider != "rules":
        return _vision_unavailable(
            configured_provider,
            configured_provider,
            _vision_model(settings.llm_model),
            "vision_provider_unsupported",
        )
    return _vision_unavailable(
        "rules",
        "rules",
        None,
        "vision_capability_missing",
    )


def describe_llm(db: Session, workspace_id: str) -> Resolution:
    """Report which analyser would run, without building it."""
    row = cred_svc.active_credential(db, workspace_id, CredentialKind.LLM)
    if row is not None:
        return Resolution(
            source="byok",
            provider=row.provider,
            model=row.model,
            label=row.label,
            credential_id=row.id,
            reason=f"using your {row.label} key ({row.key_hint})",
        )
    if settings.llm_provider == "openai_compatible" and settings.llm_api_key:
        return Resolution(
            source="env",
            provider="openai_compatible",
            model=settings.llm_model,
            label="environment LLM",
            reason="using the server's configured LLM (MS_LLM_*)",
        )
    return Resolution(
        source="local",
        provider="rules",
        label="built-in rules",
        reason="no LLM key configured; using offline rule-based analysis",
    )


def resolve_analyzer(db: Session, workspace_id: str) -> tuple[analysis_svc.Analyzer, Resolution]:
    """Build the analyser for a workspace.

    A decryption failure is downgraded to the local analyser rather than raised:
    a corrupt or re-keyed credential should not make the project un-analysable.
    """
    decision = describe_llm(db, workspace_id)

    if decision.source == "byok":
        row = cred_svc.get_credential(db, workspace_id, decision.credential_id)
        try:
            api_key = cred_svc.reveal_secret(row)
        except cred_svc.CredentialError as exc:
            fallback = Resolution(
                source="local",
                provider="rules",
                label="built-in rules",
                reason=f"stored {row.label} key could not be read ({exc}); used rules",
            )
            return analysis_svc.RulesAnalyzer(), fallback

        cred_svc.mark_used(db, row)
        analyzer = analysis_svc.ByokAnalyzer(
            provider=row.provider,
            api_key=api_key,
            model=row.model,
            base_url=row.base_url,
            label=row.label,
        )
        return analyzer, decision

    if decision.source == "env":
        return analysis_svc.LLMAnalyzer(), decision

    return analysis_svc.RulesAnalyzer(), decision


def describe_tts(db: Session, workspace_id: str) -> Resolution:
    """Report which speech provider would run, without building it."""
    row = cred_svc.active_credential(db, workspace_id, CredentialKind.TTS)
    if row is not None:
        return Resolution(
            source="byok",
            provider=row.provider,
            model=row.model,
            label=row.label,
            credential_id=row.id,
            reason=f"using your {row.label} key ({row.key_hint})",
        )
    if settings.tts_provider == "http" and settings.tts_http_url:
        return Resolution(
            source="env",
            provider="http",
            label="environment TTS",
            reason="using the server's configured TTS endpoint (MS_TTS_HTTP_URL)",
        )
    return Resolution(
        source="local",
        provider=settings.tts_provider or "espeak",
        label="espeak-ng",
        reason="no speech key configured; using offline espeak-ng",
    )


def resolve_tts(
    db: Session, workspace_id: str, override: str | None = None
) -> tuple[tts_svc.TTSProvider, Resolution]:
    """Build the speech provider for a workspace.

    ``override`` forces a local provider by name, which the seed script and tests
    use to stay offline. An explicit override always wins so automated runs never
    spend a user's credits by accident.
    """
    if override:
        return tts_svc.get_provider(override), Resolution(
            source="local",
            provider=override,
            label=override,
            reason=f"explicitly overridden to '{override}'",
        )

    decision = describe_tts(db, workspace_id)

    if decision.source == "byok":
        row = cred_svc.get_credential(db, workspace_id, decision.credential_id)
        try:
            api_key = cred_svc.reveal_secret(row)
        except cred_svc.CredentialError as exc:
            raise tts_svc.TTSError(
                f"stored {row.label} voice credential could not be read; no fallback voice is allowed"
            ) from exc

        cred_svc.mark_used(db, row)
        provider = tts_svc.ByokProvider(
            provider=row.provider,
            api_key=api_key,
            model=row.model,
            base_url=row.base_url,
            label=row.label,
        )
        return provider, decision

    return tts_svc.get_provider(decision.provider), decision


def describe_all(db: Session, workspace_id: str) -> dict[str, object]:
    """Provider summary for the UI status panel."""
    return {
        "llm": describe_llm(db, workspace_id).as_dict(),
        "tts": describe_tts(db, workspace_id).as_dict(),
    }
