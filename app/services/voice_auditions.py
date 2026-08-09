"""Four-voice neural audition generation.

Auditions are deliberately separate from final voice-over generation.  They
compare one deterministic, punctuation-preserving excerpt with four requested
neural voices and never change the project's selected voice or its final audio
segments.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.constants import CredentialKind
from app.services import credentials as credentials_svc
from app.services import pipeline as pipeline_svc
from app.services import storage
from app.services import tts as tts_svc

ROLE_ORDER: tuple[str, ...] = (
    "hook",
    "setup",
    "escalation",
    "editorial_insight",
    "payoff_open_loop",
)

_OPENAI_VOICES = frozenset({"alloy", "echo", "fable", "onyx", "nova", "shimmer"})
_NEURAL_PROVIDERS = frozenset({"openai", "custom_openai", "elevenlabs"})
_AUDITION_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class VoiceAuditionError(pipeline_svc.PipelineError):
    """Safe, machine-readable audition boundary failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ComparisonExcerpt:
    text: str
    represented_roles: tuple[str, ...]
    role_word_counts: tuple[int, ...]


def _role(section: Mapping[str, Any]) -> str:
    value = section.get("editorial_role") or section.get("section")
    return value.strip() if isinstance(value, str) else ""


def build_audition_text(
    sections: Sequence[Mapping[str, Any]],
) -> ComparisonExcerpt:
    """Build a bounded excerpt while retaining original role token text."""
    rows = list(sections)
    roles = tuple(_role(section) for section in rows)
    if len(roles) != len(set(roles)):
        raise VoiceAuditionError(
            "voice_audition_roles_invalid",
            "the approved evidence script roles must be unique and ordered",
        )
    if roles != ROLE_ORDER:
        if set(ROLE_ORDER) - set(roles):
            raise VoiceAuditionError(
                "voice_audition_roles_missing",
                "the approved evidence script must contain all five editorial roles",
            )
        raise VoiceAuditionError(
            "voice_audition_roles_invalid",
            "the approved evidence script roles must be unique and ordered",
        )

    token_rows: list[list[str]] = []
    for section in rows:
        text = section.get("text")
        if not isinstance(text, str) or not text.strip():
            raise VoiceAuditionError(
                "voice_audition_script_invalid",
                "each editorial role must contain spoken text",
            )
        token_rows.append(text.strip().split())
    available = sum(len(tokens) for tokens in token_rows)
    if available < 45:
        raise VoiceAuditionError(
            "voice_audition_excerpt_length",
            "the five roles do not contain enough spoken text for an audition excerpt",
        )
    budget = min(60, available)
    counts = [min(7, len(tokens)) for tokens in token_rows]
    remaining = budget - sum(counts)
    while remaining:
        progressed = False
        for index, tokens in enumerate(token_rows):
            if counts[index] >= len(tokens):
                continue
            counts[index] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break
    selected_rows: list[list[str]] = []
    for tokens, count in zip(token_rows, counts, strict=True):
        selected = list(tokens[:count])
        if count < len(tokens) and tokens[-1][-1:] in ".?!" and selected:
            mark = tokens[-1][-1]
            selected[-1] = selected[-1].rstrip(".,?!") + mark
        selected_rows.append(selected)
    selected = [" ".join(tokens) for tokens in selected_rows]
    return ComparisonExcerpt(
        " ".join(selected),
        ROLE_ORDER,
        tuple(counts),
    )


def build_comparison_excerpt(
    sections: Sequence[Mapping[str, Any]],
) -> ComparisonExcerpt:
    """Backward-compatible name for the bounded audition-text builder."""
    return build_audition_text(sections)


def validate_voice_ids(voice_ids: Sequence[str]) -> tuple[str, ...]:
    values = tuple(voice_id.strip() for voice_id in voice_ids if isinstance(voice_id, str))
    if len(values) != 4:
        raise VoiceAuditionError(
            "voice_audition_voice_count",
            "exactly four voice IDs are required",
        )
    if any(not value for value in values) or len(set(values)) != 4:
        raise VoiceAuditionError(
            "voice_audition_voice_ids_invalid",
            "voice IDs must be four unique nonempty values",
        )
    return values


def _model_ids(available_models: Sequence[Any]) -> set[str]:
    ids: set[str] = set()
    for item in available_models:
        if isinstance(item, Mapping):
            value = item.get("id")
        else:
            value = item
        if isinstance(value, str) and value.strip():
            ids.add(value.strip())
    return ids


def validate_voice_selection(
    provider: str,
    voice_id: str,
    available_models: Sequence[Any],
) -> None:
    """Validate an audition voice without exposing credential material."""
    provider_key = (provider or "").strip().lower()
    valid = voice_id in _OPENAI_VOICES if provider_key in {"openai", "custom_openai"} else False
    if provider_key == "elevenlabs":
        valid = voice_id in _model_ids(available_models)
    if provider_key not in _NEURAL_PROVIDERS or not valid:
        raise VoiceAuditionError(
            "voice_audition_voice_invalid",
            "the requested voice is not available for the selected neural provider",
        )


def _load_approved_evidence_script(
    db: Any, project_id: str
) -> list[dict[str, Any]]:
    script = pipeline_svc.latest_script_row(db, project_id)
    if (
        script is None
        or script.generator != "vision_evidence_v2"
        or script.approved_at is None
        or not script.approved_by
        or not (script.editorial_metadata or {}).get("editorial_review_confirmed")
    ):
        raise VoiceAuditionError(
            "voice_audition_script_not_approved",
            "the current evidence script must be explicitly approved first",
        )
    return [dict(section) for section in (script.sections or [])]


def _resolve_neural_provider(
    db: Any, project_id: str
) -> tts_svc.ByokProvider:
    project = pipeline_svc.get_project(db, project_id)
    row = credentials_svc.active_credential(db, project.workspace_id, CredentialKind.TTS)
    if row is None or row.provider not in _NEURAL_PROVIDERS:
        raise VoiceAuditionError(
            "voice_audition_credential_missing",
            "an active verified neural TTS credential is required",
        )
    try:
        api_key = credentials_svc.reveal_secret(row)
        provider = tts_svc.ByokProvider(
            provider=row.provider,
            api_key=api_key,
            model=row.model,
            base_url=row.base_url,
            voice=row.model if row.provider == "elevenlabs" else "",
            label=row.label,
        )
    except Exception:
        raise VoiceAuditionError(
            "voice_audition_credential_invalid",
            "the configured neural TTS credential could not be used",
        ) from None
    if not provider.available():
        raise VoiceAuditionError(
            "voice_audition_credential_invalid",
            "the configured neural TTS credential is unavailable",
        )
    provider._audition_provider_key = row.provider
    provider._audition_available_models = row.available_models or []
    provider._audition_label = row.label or row.provider
    return provider


def _provider_metadata(provider: Any) -> tuple[str, str, Sequence[Any]]:
    key = str(
        getattr(provider, "_audition_provider_key", "")
        or getattr(provider, "_provider", "")
        or str(getattr(provider, "name", "")).removeprefix("byok:")
    )
    label = str(getattr(provider, "_audition_label", "") or getattr(provider, "label", "") or key)
    available = getattr(provider, "_audition_available_models", ())
    return key, label, available


def _safe_audition_id(checksum: str) -> str:
    if not _AUDITION_ID_RE.fullmatch(checksum):
        raise VoiceAuditionError(
            "voice_audition_storage_invalid",
            "the generated audition checksum was invalid",
        )
    return checksum


def audition_path(project_id: str, audition_id: str) -> Path:
    if not project_id or "/" in project_id or "\\" in project_id:
        raise VoiceAuditionError("voice_audition_download_invalid", "invalid project scope")
    if not isinstance(audition_id, str) or not _AUDITION_ID_RE.fullmatch(audition_id):
        raise VoiceAuditionError(
            "voice_audition_download_invalid",
            "audition ID must be lowercase hexadecimal",
        )
    return storage.path_for(
        f"projects/{project_id}/voice-auditions/{audition_id[:16]}.wav"
    )


def _cleanup_files(paths: Sequence[Path]) -> None:
    for path in paths:
        with suppress(OSError):
            path.unlink(missing_ok=True)


def generate_auditions(
    db: Any,
    project_id: str,
    voice_ids: Sequence[str],
    speed: float = 1.15,
    actor_id: str = "",
) -> dict[str, Any]:
    """Generate four isolated neural samples from one approved excerpt."""
    selected = validate_voice_ids(voice_ids)
    if not 0.5 <= float(speed) <= 2.0:
        raise VoiceAuditionError("voice_audition_speed_invalid", "speed must be between 0.5 and 2.0")

    sections = _load_approved_evidence_script(db, project_id)
    excerpt = build_audition_text(sections)
    provider = _resolve_neural_provider(db, project_id)
    provider_key, provider_label, available_models = _provider_metadata(provider)
    if provider_key not in _NEURAL_PROVIDERS:
        raise VoiceAuditionError(
            "voice_audition_credential_missing",
            "only configured neural TTS providers may generate auditions",
        )
    for voice_id in selected:
        validate_voice_selection(provider_key, voice_id, available_models)

    work = storage.workspace_dir(project_id, "voice-auditions")
    generated: list[tuple[str, Any, Path]] = []
    temp_paths: list[Path] = []
    created_storage_keys: list[str] = []
    try:
        for index, voice_id in enumerate(selected):
            output = work / f"{index:02d}-{uuid.uuid4().hex}.wav"
            temp_paths.append(output)
            try:
                clip = provider.synthesize(excerpt.text, output, voice_id, float(speed))
            except Exception:
                raise VoiceAuditionError(
                    "voice_audition_generation_failed",
                    "neural TTS provider failed; no audition files were published",
                ) from None
            path = Path(getattr(clip, "path", output))
            if not path.is_file() or path.stat().st_size == 0:
                raise VoiceAuditionError(
                    "voice_audition_generation_failed",
                    "neural TTS provider returned no audio",
                )
            generated.append((voice_id, clip, path))

        checksums: set[str] = set()
        for _voice_id, _clip, path in generated:
            checksum = storage.sha256_file(path)
            if checksum in checksums:
                raise VoiceAuditionError(
                    "voice_audition_duplicate_audio",
                    "each requested voice must produce distinct audition audio",
                )
            checksums.add(checksum)

        items: list[dict[str, Any]] = []
        for index, (voice_id, clip, path) in enumerate(generated):
            checksum = storage.sha256_file(path)
            storage_key = (
                f"projects/{project_id}/voice-auditions/{checksum[:16]}.wav"
            )
            existed_before = storage.exists(storage_key)
            stored = storage.put_file(
                f"projects/{project_id}/voice-auditions",
                path,
                f"{index:02d}.wav",
            )
            if not existed_before:
                created_storage_keys.append(stored.storage_key)
            audition_id = _safe_audition_id(stored.checksum)
            items.append(
                {
                    "audition_id": audition_id,
                    "checksum_prefix": stored.checksum[:16],
                    "index": index,
                    "voice_id": voice_id,
                    "provider": provider_label,
                    "provider_key": provider_key,
                    "duration": round(float(getattr(clip, "duration", 0.0) or 0.0), 3),
                    "represented_roles": list(excerpt.represented_roles),
                    "download_url": f"/api/projects/{project_id}/voice/auditions/{audition_id}.wav",
                }
            )
    except VoiceAuditionError:
        for key in created_storage_keys:
            storage.delete(key)
        _cleanup_files([*temp_paths, *(path for _, _, path in generated)])
        raise
    except Exception:
        for key in created_storage_keys:
            storage.delete(key)
        _cleanup_files([*temp_paths, *(path for _, _, path in generated)])
        raise VoiceAuditionError(
            "voice_audition_generation_failed",
            "neural TTS provider failed; no audition files were published",
        ) from None
    finally:
        _cleanup_files([*temp_paths, *(path for _, _, path in generated)])

    summary = {"count": len(items), "provider": provider_key, "voice_ids": list(selected)}
    if db is not None:
        pipeline_svc.audit(
            db,
            "voice.auditions.generate",
            "project",
            project_id,
            actor_id,
            **summary,
        )
    return {
        "text": excerpt.text,
        "speed": float(speed),
        "represented_roles": list(excerpt.represented_roles),
        "items": items,
    }
