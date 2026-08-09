"""Contract tests for the four-voice neural audition workflow.

These tests intentionally import the future audition service inside each test
body.  The RED phase must collect cleanly on a checkout where that service and
its API/schema surface do not exist yet.
"""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace

import pytest

ROLE_ORDER = (
    "hook",
    "setup",
    "escalation",
    "editorial_insight",
    "payoff_open_loop",
)

SECTIONS = [
    {
        "section": "hook",
        "editorial_role": "hook",
        "text": "A locked harbor starts with Mara hearing a bell no one else can explain, while the silent pier keeps every witness far from the water.",
        "claim_ids": ["claim-hook"],
        "evidence_panel_ids": ["panel-1"],
    },
    {
        "section": "setup",
        "editorial_role": "setup",
        "text": "She hides a brass compass as a guard closes the dock gate, and the choice leaves her outside with no obvious way through.",
        "claim_ids": ["claim-setup"],
        "evidence_panel_ids": ["panel-2"],
    },
    {
        "section": "conflict",
        "editorial_role": "escalation",
        "text": "The gate opens toward a dark boat while Mara remains outside, footsteps approach, and the scene suggests someone else was expected inside.",
        "claim_ids": ["claim-escalation"],
        "evidence_panel_ids": ["panel-3"],
    },
    {
        "section": "twist",
        "editorial_role": "editorial_insight",
        "text": "That turns the missing compass into a crucial clue about who was invited, although the panels cannot prove the visitor's identity or motive.",
        "claim_ids": ["claim-insight"],
        "evidence_panel_ids": ["panel-3"],
    },
    {
        "section": "cta",
        "editorial_role": "payoff_open_loop",
        "text": "So whom did the gate admit, and why was Mara left waiting on shore when the dark boat disappeared beyond the opening?",
        "claim_ids": ["claim-payoff"],
        "evidence_panel_ids": ["panel-3"],
    },
]


def _service():
    """Load the implementation under test only after pytest collection."""
    from app.services import voice_auditions

    return voice_auditions


class _RecordingProvider:
    name = "byok:custom_openai"
    label = "Mock neural TTS"

    def __init__(self, *, fail_at: int | None = None, secret: str = "") -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.fail_at = fail_at
        self.secret = secret

    def available(self) -> bool:
        return True

    def synthesize(self, text: str, out_path: Path, voice_id: str, speed: float):
        self.calls.append((text, voice_id, speed))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise RuntimeError(f"provider failed {self.secret}".strip())
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"RIFF" + b"audition-wav-" + voice_id.encode())
        return SimpleNamespace(
            path=out_path,
            text=text,
            duration=1.25,
            voice_id=voice_id,
            provider=self.name,
            word_timings=[],
            voice_profile={"provider": self.name, "voice_id": voice_id},
        )


def _patch_valid_generation(monkeypatch, service, provider):
    monkeypatch.setattr(
        service,
        "_load_approved_evidence_script",
        lambda _db, _project_id: list(SECTIONS),
    )
    monkeypatch.setattr(
        service,
        "_resolve_neural_provider",
        lambda _db, _project_id: provider,
    )


class _FileProvider:
    name = "byok:custom_openai"
    label = "Mock file provider"

    def __init__(self, payload_for_voice, *, fail_at=None, write_before_failure=False):
        self.payload_for_voice = payload_for_voice
        self.fail_at = fail_at
        self.write_before_failure = write_before_failure
        self.calls = []
        self.paths = []

    def available(self):
        return True

    def synthesize(self, text, out_path, voice_id, speed):
        self.calls.append((text, voice_id, speed))
        self.paths.append(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.payload_for_voice(voice_id)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            if self.write_before_failure:
                out_path.write_bytes(payload)
            raise RuntimeError("provider failure")
        out_path.write_bytes(payload)
        return SimpleNamespace(
            path=out_path,
            text=text,
            duration=1.25,
            voice_id=voice_id,
            provider=self.name,
            word_timings=[],
            voice_profile={"provider": self.name, "voice_id": voice_id},
        )


def test_request_requires_exactly_four_unique_voice_ids_and_bounded_speed():
    _service()
    from app.schemas import VoiceAuditionRequest

    request = VoiceAuditionRequest(voice_ids=["nova", "echo", "fable", "onyx"])
    assert request.voice_ids == ["nova", "echo", "fable", "onyx"]
    assert request.speed == 1.15
    for voice_ids in (
        [],
        ["nova", "echo", "fable"],
        ["nova", "echo", "fable", "onyx", "alloy"],
        ["nova", "echo", "echo", "onyx"],
        ["nova", "", "fable", "onyx"],
    ):
        with pytest.raises(ValueError):
            VoiceAuditionRequest(voice_ids=voice_ids)
    with pytest.raises(ValueError):
        VoiceAuditionRequest(
            voice_ids=["nova", "echo", "fable", "onyx"], speed=2.01
        )


def test_comparison_excerpt_uses_all_roles_in_order_and_keeps_prosody():
    service = _service()
    source_words = sum(len(section["text"].split()) for section in SECTIONS)
    assert 90 <= source_words <= 125
    excerpt = service.build_audition_text(SECTIONS)
    assert excerpt.represented_roles == ROLE_ORDER
    assert 45 <= len(excerpt.text.split()) <= 65
    positions = [excerpt.text.index(section["text"].split()[0]) for section in SECTIONS]
    assert positions == sorted(positions)
    assert any(mark in excerpt.text for mark in (",", ".", "?"))


def test_audition_text_caps_very_long_roles_without_sampling():
    service = _service()
    very_long = [
        {**section, "text": f'{section["text"]} ' + "The evidence remains qualified." * 40}
        for section in SECTIONS
    ]
    excerpt = service.build_audition_text(very_long)
    assert len(excerpt.text.split()) == 60
    assert excerpt.represented_roles == ROLE_ORDER


def test_audition_text_allocates_balanced_role_prefixes():
    service = _service()
    sections = [
        {
            **section,
            "text": " ".join(f"role{index}_word{word}" for word in range(1, 31)),
        }
        for index, section in enumerate(SECTIONS, start=1)
    ]
    excerpt = service.build_audition_text(sections)
    assert 45 <= sum(excerpt.role_word_counts) <= 65
    assert max(excerpt.role_word_counts) - min(excerpt.role_word_counts) <= 1


def test_audition_text_keeps_each_role_contiguous_when_truncated():
    service = _service()
    source = []
    for index, section in enumerate(SECTIONS, start=1):
        tokens = [f"role{index}_{word}" for word in range(1, 20)]
        tokens.append(f"role{index}_terminal.")
        source.append({**section, "text": " ".join(tokens)})
    excerpt = service.build_audition_text(source)
    third_tokens = source[2]["text"].split()
    third_count = excerpt.role_word_counts[2]
    assert third_count < len(third_tokens)
    assert "role3_terminal." not in excerpt.text
    assert " ".join(third_tokens[: third_count - 1]) in excerpt.text


def test_audition_text_rejects_only_missing_roles_or_genuinely_short_input():
    service = _service()
    short = [{**section, "text": "short fact."} for section in SECTIONS]
    with pytest.raises(service.VoiceAuditionError) as error:
        service.build_audition_text(short)
    assert error.value.code == "voice_audition_excerpt_length"


def test_excerpt_rejects_missing_or_duplicate_editorial_roles():
    service = _service()
    missing = [section for section in SECTIONS if section["editorial_role"] != "setup"]
    with pytest.raises(service.VoiceAuditionError) as missing_error:
        service.build_comparison_excerpt(missing)
    assert missing_error.value.code == "voice_audition_roles_missing"

    duplicate = list(SECTIONS)
    duplicate[-1] = {**duplicate[-1], "editorial_role": "setup"}
    with pytest.raises(service.VoiceAuditionError) as duplicate_error:
        service.build_comparison_excerpt(duplicate)
    assert duplicate_error.value.code == "voice_audition_roles_invalid"


def test_excerpt_never_samples_or_randomizes_panel_or_role_order(monkeypatch):
    service = _service()

    def explode(*_args, **_kwargs):
        raise AssertionError("random selection is forbidden for audition text")

    monkeypatch.setattr(random, "choice", explode)
    monkeypatch.setattr(random, "sample", explode)
    monkeypatch.setattr(random, "shuffle", explode)
    excerpt = service.build_audition_text(SECTIONS)
    assert excerpt.represented_roles == ROLE_ORDER


@pytest.mark.parametrize(
    ("provider", "voice_id", "available_models", "valid"),
    [
        ("openai", "nova", [{"id": "tts-1"}], True),
        ("custom_openai", "alloy", [{"id": "tts-1"}], True),
        ("openai", "made-up-voice", [{"id": "tts-1"}], False),
        ("elevenlabs", "voice-a", [{"id": "voice-a"}], True),
        ("elevenlabs", "voice-z", [{"id": "voice-a"}], False),
    ],
)
def test_provider_voice_validation_is_explicit_and_uses_safe_catalogues(
    provider, voice_id, available_models, valid
):
    service = _service()
    if valid:
        service.validate_voice_selection(provider, voice_id, available_models)
    else:
        with pytest.raises(service.VoiceAuditionError) as error:
            service.validate_voice_selection(provider, voice_id, available_models)
        assert error.value.code == "voice_audition_voice_invalid"


def test_generation_requires_current_approved_evidence_script(monkeypatch):
    service = _service()
    provider = _RecordingProvider()
    monkeypatch.setattr(
        service,
        "_load_approved_evidence_script",
        lambda _db, _project_id: (_ for _ in ()).throw(
            service.VoiceAuditionError("voice_audition_script_not_approved", "approve the current evidence script")
        ),
    )
    monkeypatch.setattr(
        service,
        "_resolve_neural_provider",
        lambda *_args: pytest.fail("provider must not resolve before the approval gate"),
    )
    with pytest.raises(service.VoiceAuditionError) as error:
        service.generate_auditions(None, "project-a", ["nova", "echo", "fable", "onyx"])
    assert error.value.code == "voice_audition_script_not_approved"
    assert provider.calls == []


def test_generation_requires_verified_neural_credential(monkeypatch):
    service = _service()
    monkeypatch.setattr(
        service,
        "_load_approved_evidence_script",
        lambda _db, _project_id: list(SECTIONS),
    )
    monkeypatch.setattr(
        service,
        "_resolve_neural_provider",
        lambda *_args: (_ for _ in ()).throw(
            service.VoiceAuditionError("voice_audition_credential_missing", "verified neural TTS credential required")
        ),
    )
    with pytest.raises(service.VoiceAuditionError) as error:
        service.generate_auditions(None, "project-a", ["nova", "echo", "fable", "onyx"])
    assert error.value.code == "voice_audition_credential_missing"


def test_four_auditions_share_exact_punctuated_text_and_roles(monkeypatch):
    service = _service()
    provider = _RecordingProvider()
    _patch_valid_generation(monkeypatch, service, provider)
    result = service.generate_auditions(
        None,
        "project-a",
        ["nova", "echo", "fable", "onyx"],
        speed=1.1,
        actor_id="user-a",
    )
    assert len(result["items"]) == 4
    assert len({call[0] for call in provider.calls}) == 1
    assert provider.calls[0][0] == result["text"]
    assert result["represented_roles"] == list(ROLE_ORDER)
    assert all(item["represented_roles"] == list(ROLE_ORDER) for item in result["items"])
    assert all(mark in result["text"] for mark in (",", ".", "?"))


def test_provider_voice_ids_never_enter_audition_temp_paths(monkeypatch, tmp_path):
    service = _service()
    voice_ids = ["voice/alpha", r"voice\beta", "voice:gamma", "voice..delta"]
    provider = _FileProvider(lambda voice_id: b"RIFF-" + voice_id.encode())
    provider._audition_provider_key = "elevenlabs"
    provider._audition_available_models = [{"id": voice_id} for voice_id in voice_ids]
    _patch_valid_generation(monkeypatch, service, provider)
    monkeypatch.setattr(service.storage, "workspace_dir", lambda *_args: tmp_path)

    service.generate_auditions(None, "project-a", voice_ids)

    assert len(provider.paths) == 4
    assert all(path.parent == tmp_path for path in provider.paths)
    assert all("/" not in path.name and "\\" not in path.name for path in provider.paths)
    assert all(voice_id not in path.name for path, voice_id in zip(provider.paths, voice_ids, strict=True))


def test_provider_failure_aborts_without_partial_success_or_fallback(monkeypatch):
    service = _service()
    provider = _RecordingProvider(fail_at=3, secret="sk-audition-secret")
    _patch_valid_generation(monkeypatch, service, provider)
    with pytest.raises(service.VoiceAuditionError) as error:
        service.generate_auditions(
            None, "project-a", ["nova", "echo", "fable", "onyx"]
        )
    assert error.value.code == "voice_audition_generation_failed"
    assert "sk-audition-secret" not in str(error.value)
    assert "espeak" not in str(error.value).lower()
    assert "null" not in str(error.value).lower()


def test_partial_temp_output_is_cleaned_after_provider_failure(monkeypatch, tmp_path):
    service = _service()
    provider = _FileProvider(
        lambda _voice_id: b"RIFF-partial",
        fail_at=1,
        write_before_failure=True,
    )
    _patch_valid_generation(monkeypatch, service, provider)
    monkeypatch.setattr(service.storage, "workspace_dir", lambda *_args: tmp_path)

    with pytest.raises(service.VoiceAuditionError) as error:
        service.generate_auditions(None, "project-a", ["nova", "echo", "fable", "onyx"])

    assert error.value.code == "voice_audition_generation_failed"
    assert list(tmp_path.iterdir()) == []


def test_duplicate_audio_checksums_abort_before_publishing(monkeypatch, tmp_path):
    service = _service()
    provider = _FileProvider(lambda _voice_id: b"RIFF-identical")
    _patch_valid_generation(monkeypatch, service, provider)
    monkeypatch.setattr(service.storage, "workspace_dir", lambda *_args: tmp_path)
    put_calls = []
    monkeypatch.setattr(
        service.storage,
        "put_file",
        lambda *args, **kwargs: put_calls.append((args, kwargs)),
    )

    with pytest.raises(service.VoiceAuditionError) as error:
        service.generate_auditions(None, "project-a", ["nova", "echo", "fable", "onyx"])

    assert error.value.code == "voice_audition_duplicate_audio"
    assert put_calls == []


def test_rollback_preserves_preexisting_content_addressed_object(monkeypatch, tmp_path):
    import hashlib

    from app.services.storage import StoredObject

    service = _service()
    payloads = {
        "nova": b"RIFF-nova",
        "echo": b"RIFF-echo",
        "fable": b"RIFF-fable",
        "onyx": b"RIFF-onyx",
    }
    provider = _FileProvider(lambda voice_id: payloads[voice_id])
    _patch_valid_generation(monkeypatch, service, provider)
    monkeypatch.setattr(service.storage, "workspace_dir", lambda *_args: tmp_path)
    prefix = "projects/project-a/voice-auditions"
    first_checksum = hashlib.sha256(payloads["nova"]).hexdigest()
    existing_key = f"{prefix}/{first_checksum[:16]}.wav"
    objects = {existing_key: payloads["nova"]}
    deleted = []
    put_count = 0

    def fake_put_file(storage_prefix, source, filename=None):
        nonlocal put_count
        put_count += 1
        if put_count == 2:
            raise RuntimeError("storage failed")
        data = source.read_bytes()
        checksum = hashlib.sha256(data).hexdigest()
        key = f"{storage_prefix}/{checksum[:16]}.wav"
        objects.setdefault(key, data)
        path = tmp_path / f"stored-{checksum[:16]}.wav"
        return StoredObject(key, path, len(data), checksum)

    monkeypatch.setattr(service.storage, "put_file", fake_put_file)
    monkeypatch.setattr(service.storage, "exists", lambda key: key in objects)
    monkeypatch.setattr(
        service.storage,
        "delete",
        lambda key: deleted.append(key) or objects.pop(key, None) is not None,
    )

    with pytest.raises(service.VoiceAuditionError) as error:
        service.generate_auditions(None, "project-a", list(payloads))

    assert error.value.code == "voice_audition_generation_failed"
    assert objects[existing_key] == payloads["nova"]
    assert existing_key not in deleted


def test_missing_or_local_provider_never_silently_falls_back(monkeypatch):
    service = _service()
    monkeypatch.setattr(
        service,
        "_load_approved_evidence_script",
        lambda _db, _project_id: list(SECTIONS),
    )
    monkeypatch.setattr(
        service,
        "_resolve_neural_provider",
        lambda *_args: (_ for _ in ()).throw(
            service.VoiceAuditionError("voice_audition_credential_missing", "neural credential required")
        ),
    )
    with pytest.raises(service.VoiceAuditionError):
        service.generate_auditions(None, "project-a", ["nova", "echo", "fable", "onyx"])


def test_response_is_safe_and_audit_contains_only_nonsecret_summary(monkeypatch):
    service = _service()
    provider = _RecordingProvider()
    _patch_valid_generation(monkeypatch, service, provider)
    audit_rows = []
    monkeypatch.setattr(
        service.pipeline_svc,
        "audit",
        lambda _db, _action, _entity_type, _entity_id, _actor_id, **fields: audit_rows.append(fields),
    )
    result = service.generate_auditions(
        object(),
        "project-a",
        ["nova", "echo", "fable", "onyx"],
        actor_id="user-a",
    )
    forbidden = {"storage_key", "path", "api_key", "ciphertext", "key_hint"}
    assert not forbidden.intersection(result)
    assert all(not forbidden.intersection(item) for item in result["items"])
    assert audit_rows
    assert set(audit_rows[-1]) <= {"count", "provider", "voice_ids"}
    assert "audition_id" in result["items"][0]
    assert result["items"][0]["download_url"].startswith(
        "/api/projects/project-a/voice/auditions/"
    )


def test_audition_service_has_no_empty_audit_test_hook():
    service = _service()
    assert not hasattr(service, "_audit_audition_batch")


def test_existing_final_audio_segments_are_not_mutated(monkeypatch):
    service = _service()
    provider = _RecordingProvider()
    _patch_valid_generation(monkeypatch, service, provider)
    existing = [SimpleNamespace(voice_id="selected", storage_key="final.wav", duration=2.0)]
    monkeypatch.setattr(service, "_load_existing_final_audio", lambda *_args: existing, raising=False)
    before = [vars(segment).copy() for segment in existing]
    service.generate_auditions(
        None, "project-a", ["nova", "echo", "fable", "onyx"]
    )
    assert [vars(segment) for segment in existing] == before


def test_download_identifier_is_lowercase_hex_and_project_scoped():
    service = _service()
    audition_id = "a" * 64
    path = service.audition_path("project-a", audition_id)
    assert str(path).replace("\\", "/").endswith(
        f"projects/project-a/voice-auditions/{audition_id[:16]}.wav"
    )
    for invalid in ("../secret", "A" * 64, "g" * 64, "short", "a" * 63):
        with pytest.raises(service.VoiceAuditionError):
            service.audition_path("project-a", invalid)


def test_http_surface_requires_ownership_and_exposes_only_safe_download(auth_client):
    _service()
    project_response = auth_client.post(
        "/api/projects", json={"title": "Audition project", "language": "en"}
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]
    response = auth_client.post(
        f"/api/projects/{project_id}/voice/auditions",
        json={"voice_ids": ["nova", "echo", "fable", "onyx"]},
    )
    assert response.status_code in {200, 401, 422}
    assert "storage_key" not in response.text
    assert "api_key" not in response.text
    assert "ciphertext" not in response.text


def test_download_route_rejects_traversal_and_cross_project_access(auth_client):
    _service()
    project_response = auth_client.post(
        "/api/projects", json={"title": "Audition project", "language": "en"}
    )
    assert project_response.status_code == 201, project_response.text
    project_id = project_response.json()["id"]
    for suffix in ("../secret.wav", "A" * 64 + ".wav", "g" * 64 + ".wav"):
        response = auth_client.get(
            f"/api/projects/{project_id}/voice/auditions/{suffix}"
        )
        assert response.status_code in {404, 422}


def test_no_final_voice_selection_is_created_by_audition(monkeypatch):
    service = _service()
    provider = _RecordingProvider()
    _patch_valid_generation(monkeypatch, service, provider)
    project = SimpleNamespace(voice_id="existing-selected-voice")
    monkeypatch.setattr(service, "_load_project", lambda *_args: project, raising=False)
    service.generate_auditions(None, "project-a", ["nova", "echo", "fable", "onyx"])
    assert project.voice_id == "existing-selected-voice"


def test_openai_byok_keeps_model_separate_from_project_voice(monkeypatch, tmp_path):
    _service()
    from app.services import providers as providers_service
    from app.services import tts

    calls = []

    class Adapter:
        def synthesize(self, **kwargs):
            calls.append(kwargs)
            kwargs["out_path"].write_bytes(b"RIFF audition")
            return kwargs["out_path"]

    monkeypatch.setattr(providers_service, "get_tts_adapter", lambda _provider: Adapter())
    monkeypatch.setattr(tts, "probe_duration", lambda _path: 1.0)
    provider = tts.ByokProvider(
        "custom_openai", "sk-test-audition", "tts-1", "http://mock.test/v1"
    )
    provider.synthesize("A punctuated excerpt.", tmp_path / "voice.wav", voice_id="nova")
    assert calls[0]["model"] == "tts-1"
    assert calls[0]["voice"] == "nova"
    assert calls[0]["voice"] != calls[0]["model"]


def test_default_project_voice_sentinel_uses_stored_voice_per_provider(monkeypatch, tmp_path):
    from app.constants import DEFAULT_ENGLISH_VOICE_ID
    from app.services import providers as providers_service
    from app.services import tts

    calls = []

    class Adapter:
        def synthesize(self, **kwargs):
            calls.append(kwargs)
            kwargs["out_path"].write_bytes(b"RIFF audition")
            return kwargs["out_path"]

    monkeypatch.setattr(providers_service, "get_tts_adapter", lambda _provider: Adapter())
    monkeypatch.setattr(tts, "probe_duration", lambda _path: 1.0)

    eleven = tts.ByokProvider(
        "elevenlabs",
        "sk-eleven",
        "eleven-model",
        "http://mock.test/v1",
        voice="stored-eleven",
    )
    eleven.synthesize("A punctuated excerpt.", tmp_path / "eleven-default.wav", DEFAULT_ENGLISH_VOICE_ID)
    eleven.synthesize("A punctuated excerpt.", tmp_path / "eleven-explicit.wav", "explicit-eleven")

    openai = tts.ByokProvider(
        "custom_openai",
        "sk-openai",
        "tts-1",
        "http://mock.test/v1",
        voice="stored-openai",
    )
    openai.synthesize("A punctuated excerpt.", tmp_path / "openai-default.wav", DEFAULT_ENGLISH_VOICE_ID)
    openai.synthesize("A punctuated excerpt.", tmp_path / "openai-explicit.wav", "nova")

    assert [call["voice"] for call in calls] == [
        "stored-eleven",
        "explicit-eleven",
        "stored-openai",
        "nova",
    ]
    assert [call["model"] for call in calls] == ["eleven-model", "eleven-model", "tts-1", "tts-1"]
