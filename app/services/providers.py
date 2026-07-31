"""BYOK provider adapters: bring your own key for LLM and TTS (v1.1).

Two things this module is responsible for:

1. **Talking to vendors** through a small adapter per API shape. Most vendors
   speak the OpenAI wire format, so ``OpenAICompatibleLLM`` is parameterised by
   base URL and covers OpenAI, OpenRouter, Groq, DeepSeek, Mistral, Together,
   xAI, and any self-hosted gateway. Anthropic and Google use different shapes
   and get their own adapters.

2. **Fetching the model list from the user's own key**, so the UI offers exactly
   what that key can reach instead of a hardcoded guess. This doubles as key
   verification: a provider that lists models has accepted the credential.

Design notes:

- Secrets arrive as plain ``str`` and are used immediately. They are never
  logged, never put in an exception message, and never returned by the API.
  Errors are normalised into ``ProviderError`` with vendor text truncated, and
  the key is scrubbed from that text defensively.
- Every network call has an explicit timeout. A hung vendor must not hang a
  render job.
- ``list_models`` is the only method needed to save a credential. Generation
  methods are used later by the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.constants import CredentialKind

#: Hard ceiling on any single provider call. Verification should feel instant;
#: generation is allowed longer but never unbounded.
VERIFY_TIMEOUT = 20
GENERATE_TIMEOUT = 120
SPEECH_TIMEOUT = 180

#: Vendor error bodies can be long HTML pages. Keep enough to diagnose.
_MAX_ERROR_CHARS = 300


class ProviderError(RuntimeError):
    """A provider call failed. Message is safe to show the user."""


@dataclass(frozen=True)
class ModelInfo:
    """One model offered by a provider."""

    id: str
    label: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label or self.id}


@dataclass
class ProviderSpec:
    """Static description of a provider, used to build the UI and validate input."""

    key: str
    label: str
    kind: CredentialKind
    default_base_url: str
    #: Where the user gets a key. Shown in the UI so nobody has to guess.
    console_url: str = ""
    #: True when the endpoint is user-supplied (self-hosted, proxy, LM Studio).
    custom_endpoint: bool = False
    #: Models to offer when the vendor has no list endpoint.
    static_models: tuple[str, ...] = ()
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": str(self.kind),
            "default_base_url": self.default_base_url,
            "console_url": self.console_url,
            "custom_endpoint": self.custom_endpoint,
            "notes": self.notes,
        }


def _scrub(text: str, secret: str) -> str:
    """Remove a secret from provider error text before it is surfaced."""
    out = text
    if secret and len(secret) >= 8:
        out = out.replace(secret, "[redacted]")
    # Catch keys echoed back in common formats even if not byte-identical.
    out = re.sub(r"(sk-|xi-|AIza)[A-Za-z0-9_\-]{8,}", r"\1[redacted]", out)
    return out


def _clean_error(exc: Exception, secret: str) -> str:
    """Turn any transport/HTTP exception into a short, secret-free message."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = ""
        try:
            payload = exc.response.json()
            if isinstance(payload, dict):
                err = payload.get("error")
                if isinstance(err, dict):
                    body = str(err.get("message") or err.get("type") or "")
                elif isinstance(err, str):
                    body = err
                body = body or str(payload.get("message") or "")
        except Exception:
            body = exc.response.text[:_MAX_ERROR_CHARS]
        body = _scrub(body.strip(), secret)[:_MAX_ERROR_CHARS]

        if status in (401, 403):
            hint = "the key was rejected. Check that it is active and has the right permissions"
        elif status == 404:
            hint = "endpoint not found. Check the base URL"
        elif status == 429:
            hint = "rate limited or out of quota"
        elif status >= 500:
            hint = "the provider is having problems, try again"
        else:
            hint = "the provider refused the request"
        return f"HTTP {status}: {hint}" + (f" ({body})" if body else "")

    if isinstance(exc, httpx.TimeoutException):
        return "the provider did not respond in time"
    if isinstance(exc, httpx.ConnectError):
        return "could not connect. Check the base URL and your network"
    return f"{type(exc).__name__}: {_scrub(str(exc), secret)[:_MAX_ERROR_CHARS]}"


def validate_base_url(url: str | None) -> str | None:
    """Reject anything that is not a plain http(s) URL.

    The endpoint is user-supplied, so this is the one place that decides what
    the server is willing to call. Scheme is restricted to http/https to avoid
    file:// and similar surprises.
    """
    if url is None:
        return None
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return None
    if not cleaned.startswith(("http://", "https://")):
        raise ProviderError("base URL must start with http:// or https://")
    if len(cleaned) > 300:
        raise ProviderError("base URL is too long")
    return cleaned


# --------------------------------------------------------------------------
# LLM adapters
# --------------------------------------------------------------------------


class LLMAdapter(Protocol):
    """What the pipeline needs from any chat-capable provider."""

    def list_models(self, api_key: str, base_url: str | None = None) -> list[ModelInfo]: ...

    def chat_json(
        self,
        api_key: str,
        model: str,
        system: str,
        user: str,
        base_url: str | None = None,
        temperature: float = 0.3,
    ) -> str: ...


@dataclass
class OpenAICompatibleLLM:
    """Adapter for the OpenAI chat-completions wire format.

    Covers OpenAI itself plus every gateway that mimics it. Only the base URL
    differs, which is why this is one class rather than eight.
    """

    spec: ProviderSpec

    def _base(self, base_url: str | None) -> str:
        return validate_base_url(base_url) or self.spec.default_base_url

    def list_models(self, api_key: str, base_url: str | None = None) -> list[ModelInfo]:
        import httpx

        url = f"{self._base(base_url)}/models"
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=VERIFY_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None

        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ProviderError("provider returned an unexpected model list format")

        models: list[ModelInfo] = []
        for row in rows:
            if isinstance(row, dict):
                mid = str(row.get("id") or row.get("name") or "").strip()
            else:
                mid = str(row).strip()
            if mid:
                models.append(ModelInfo(id=mid))
        if not models:
            raise ProviderError("the key works but no models were returned")
        return _sorted_models(models)

    def chat_json(
        self,
        api_key: str,
        model: str,
        system: str,
        user: str,
        base_url: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        import httpx

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        try:
            response = httpx.post(
                f"{self._base(base_url)}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=GENERATE_TIMEOUT,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {type(exc).__name__}") from None
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None


@dataclass
class AnthropicLLM:
    """Anthropic uses x-api-key, a version header, and /messages."""

    spec: ProviderSpec
    api_version: str = "2023-06-01"

    def _base(self, base_url: str | None) -> str:
        return validate_base_url(base_url) or self.spec.default_base_url

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
        }

    def list_models(self, api_key: str, base_url: str | None = None) -> list[ModelInfo]:
        import httpx

        try:
            response = httpx.get(
                f"{self._base(base_url)}/models",
                headers=self._headers(api_key),
                timeout=VERIFY_TIMEOUT,
            )
            response.raise_for_status()
            rows = response.json().get("data", [])
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None

        models = [
            ModelInfo(id=str(r["id"]), label=str(r.get("display_name") or ""))
            for r in rows
            if isinstance(r, dict) and r.get("id")
        ]
        if not models:
            # Older keys may not have the models endpoint; fall back to the spec.
            models = [ModelInfo(id=m) for m in self.spec.static_models]
        if not models:
            raise ProviderError("the key works but no models were returned")
        return _sorted_models(models)

    def chat_json(
        self,
        api_key: str,
        model: str,
        system: str,
        user: str,
        base_url: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        import httpx

        # Anthropic has no JSON mode flag; the instruction goes in the system
        # prompt and the caller already asks for strict JSON.
        try:
            response = httpx.post(
                f"{self._base(base_url)}/messages",
                headers=self._headers(api_key),
                json={
                    "model": model,
                    "max_tokens": 4096,
                    "temperature": temperature,
                    "system": f"{system}\n\nReturn only valid JSON, no prose, no code fences.",
                    "messages": [{"role": "user", "content": user}],
                },
                timeout=GENERATE_TIMEOUT,
            )
            response.raise_for_status()
            blocks = response.json()["content"]
            return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {type(exc).__name__}") from None
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None


@dataclass
class GoogleLLM:
    """Google AI Studio (Gemini): key as a query parameter, generateContent."""

    spec: ProviderSpec

    def _base(self, base_url: str | None) -> str:
        return validate_base_url(base_url) or self.spec.default_base_url

    def list_models(self, api_key: str, base_url: str | None = None) -> list[ModelInfo]:
        import httpx

        try:
            response = httpx.get(
                f"{self._base(base_url)}/models",
                params={"key": api_key, "pageSize": 200},
                timeout=VERIFY_TIMEOUT,
            )
            response.raise_for_status()
            rows = response.json().get("models", [])
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None

        models: list[ModelInfo] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # "models/gemini-1.5-pro" -> "gemini-1.5-pro"
            mid = str(row.get("name", "")).split("/")[-1]
            methods = row.get("supportedGenerationMethods") or []
            if mid and (not methods or "generateContent" in methods):
                models.append(ModelInfo(id=mid, label=str(row.get("displayName") or "")))
        if not models:
            raise ProviderError("the key works but no usable models were returned")
        return _sorted_models(models)

    def chat_json(
        self,
        api_key: str,
        model: str,
        system: str,
        user: str,
        base_url: str | None = None,
        temperature: float = 0.3,
    ) -> str:
        import httpx

        try:
            response = httpx.post(
                f"{self._base(base_url)}/models/{model}:generateContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": [{"role": "user", "parts": [{"text": user}]}],
                    "generationConfig": {
                        "temperature": temperature,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=GENERATE_TIMEOUT,
            )
            response.raise_for_status()
            parts = response.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected response shape: {type(exc).__name__}") from None
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None


# --------------------------------------------------------------------------
# TTS adapters
# --------------------------------------------------------------------------


class TTSAdapter(Protocol):
    """What the pipeline needs from any speech provider."""

    def list_models(self, api_key: str, base_url: str | None = None) -> list[ModelInfo]: ...

    def synthesize(
        self,
        api_key: str,
        model: str,
        text: str,
        out_path: Path,
        voice: str = "",
        speed: float = 1.0,
        base_url: str | None = None,
    ) -> Path: ...


@dataclass
class OpenAISpeechTTS:
    """OpenAI /audio/speech. ``model`` is the TTS model, ``voice`` the timbre."""

    spec: ProviderSpec
    voices: tuple[str, ...] = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")

    def _base(self, base_url: str | None) -> str:
        return validate_base_url(base_url) or self.spec.default_base_url

    def list_models(self, api_key: str, base_url: str | None = None) -> list[ModelInfo]:
        import httpx

        try:
            response = httpx.get(
                f"{self._base(base_url)}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=VERIFY_TIMEOUT,
            )
            response.raise_for_status()
            rows = response.json().get("data", [])
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None

        # The catalogue includes chat models too; keep the speech-capable ones.
        ids = [str(r.get("id", "")) for r in rows if isinstance(r, dict)]
        speech = [m for m in ids if "tts" in m or "audio" in m or "speech" in m]
        models = [ModelInfo(id=m) for m in (speech or list(self.spec.static_models))]
        if not models:
            raise ProviderError("the key works but no speech models were returned")
        return _sorted_models(models)

    def synthesize(
        self,
        api_key: str,
        model: str,
        text: str,
        out_path: Path,
        voice: str = "",
        speed: float = 1.0,
        base_url: str | None = None,
    ) -> Path:
        import httpx

        try:
            response = httpx.post(
                f"{self._base(base_url)}/audio/speech",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model or "tts-1",
                    "voice": voice or self.voices[0],
                    "input": text,
                    # WAV keeps the existing ffprobe/concat path unchanged.
                    "response_format": "wav",
                    "speed": max(0.25, min(4.0, speed)),
                },
                timeout=SPEECH_TIMEOUT,
            )
            response.raise_for_status()
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        return out_path


@dataclass
class ElevenLabsTTS:
    """ElevenLabs: xi-api-key header, voice id in the path.

    Here the user-facing choice is the *voice*, so ``list_models`` returns
    voices. The model field carries the engine (multilingual v2 by default).
    """

    spec: ProviderSpec
    engine: str = "eleven_multilingual_v2"

    def _base(self, base_url: str | None) -> str:
        return validate_base_url(base_url) or self.spec.default_base_url

    def list_models(self, api_key: str, base_url: str | None = None) -> list[ModelInfo]:
        import httpx

        try:
            response = httpx.get(
                f"{self._base(base_url)}/voices",
                headers={"xi-api-key": api_key},
                timeout=VERIFY_TIMEOUT,
            )
            response.raise_for_status()
            rows = response.json().get("voices", [])
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None

        models = [
            ModelInfo(id=str(r["voice_id"]), label=str(r.get("name") or ""))
            for r in rows
            if isinstance(r, dict) and r.get("voice_id")
        ]
        if not models:
            raise ProviderError("the key works but no voices were returned")
        return models

    def synthesize(
        self,
        api_key: str,
        model: str,
        text: str,
        out_path: Path,
        voice: str = "",
        speed: float = 1.0,
        base_url: str | None = None,
    ) -> Path:
        import httpx

        # For ElevenLabs the selected "model" is the voice id.
        voice_id = voice or model
        if not voice_id:
            raise ProviderError("pick a voice first")
        try:
            response = httpx.post(
                f"{self._base(base_url)}/text-to-speech/{voice_id}",
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                params={"output_format": "pcm_24000"},
                json={
                    "text": text,
                    "model_id": self.engine,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
                timeout=SPEECH_TIMEOUT,
            )
            response.raise_for_status()
        except Exception as exc:
            raise ProviderError(_clean_error(exc, api_key)) from None

        # pcm_24000 is headerless; wrap it so ffprobe can read a duration.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(out_path, response.content, sample_rate=24000)
        return out_path


def _write_wav(path: Path, pcm: bytes, sample_rate: int, channels: int = 1) -> None:
    """Wrap raw 16-bit PCM in a WAV container using the stdlib."""
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

_LLM_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="openai",
        label="OpenAI",
        kind=CredentialKind.LLM,
        default_base_url="https://api.openai.com/v1",
        console_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        key="anthropic",
        label="Anthropic (Claude)",
        kind=CredentialKind.LLM,
        default_base_url="https://api.anthropic.com/v1",
        console_url="https://console.anthropic.com/settings/keys",
        static_models=("claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"),
    ),
    ProviderSpec(
        key="google",
        label="Google AI Studio (Gemini)",
        kind=CredentialKind.LLM,
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        console_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        key="openrouter",
        label="OpenRouter",
        kind=CredentialKind.LLM,
        default_base_url="https://openrouter.ai/api/v1",
        console_url="https://openrouter.ai/keys",
        notes="One key, many vendors. Good for trying models before committing.",
    ),
    ProviderSpec(
        key="groq",
        label="Groq",
        kind=CredentialKind.LLM,
        default_base_url="https://api.groq.com/openai/v1",
        console_url="https://console.groq.com/keys",
    ),
    ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        kind=CredentialKind.LLM,
        default_base_url="https://api.deepseek.com/v1",
        console_url="https://platform.deepseek.com/api_keys",
    ),
    ProviderSpec(
        key="mistral",
        label="Mistral",
        kind=CredentialKind.LLM,
        default_base_url="https://api.mistral.ai/v1",
        console_url="https://console.mistral.ai/api-keys",
    ),
    ProviderSpec(
        key="together",
        label="Together AI",
        kind=CredentialKind.LLM,
        default_base_url="https://api.together.xyz/v1",
        console_url="https://api.together.ai/settings/api-keys",
    ),
    ProviderSpec(
        key="xai",
        label="xAI (Grok)",
        kind=CredentialKind.LLM,
        default_base_url="https://api.x.ai/v1",
        console_url="https://console.x.ai",
    ),
    ProviderSpec(
        key="custom_openai",
        label="Custom OpenAI-compatible",
        kind=CredentialKind.LLM,
        default_base_url="",
        custom_endpoint=True,
        notes="Ollama, LM Studio, vLLM, LiteLLM, or any proxy. Supply the base URL.",
    ),
)

_TTS_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        key="openai",
        label="OpenAI Speech",
        kind=CredentialKind.TTS,
        default_base_url="https://api.openai.com/v1",
        console_url="https://platform.openai.com/api-keys",
        static_models=("tts-1", "tts-1-hd", "gpt-4o-mini-tts"),
    ),
    ProviderSpec(
        key="elevenlabs",
        label="ElevenLabs",
        kind=CredentialKind.TTS,
        default_base_url="https://api.elevenlabs.io/v1",
        console_url="https://elevenlabs.io/app/settings/api-keys",
        notes="The list returns your voices; pick one as the narrator.",
    ),
    ProviderSpec(
        key="custom_openai",
        label="Custom OpenAI-compatible speech",
        kind=CredentialKind.TTS,
        default_base_url="",
        custom_endpoint=True,
        static_models=("tts-1",),
        notes="Any endpoint exposing /audio/speech, e.g. openedai-speech.",
    ),
)


def _sorted_models(models: list[ModelInfo]) -> list[ModelInfo]:
    """Stable, human-friendly ordering; de-duplicated by id."""
    seen: dict[str, ModelInfo] = {}
    for m in models:
        seen.setdefault(m.id, m)
    return sorted(seen.values(), key=lambda m: m.id)


def specs_for(kind: CredentialKind | str) -> tuple[ProviderSpec, ...]:
    """Every provider available for a capability."""
    kind = CredentialKind(str(kind))
    return _LLM_SPECS if kind == CredentialKind.LLM else _TTS_SPECS


def get_spec(kind: CredentialKind | str, provider: str) -> ProviderSpec:
    for spec in specs_for(kind):
        if spec.key == provider:
            return spec
    raise ProviderError(f"unknown provider '{provider}' for {kind}")


def get_llm_adapter(provider: str) -> LLMAdapter:
    """Build the adapter for an LLM provider key."""
    spec = get_spec(CredentialKind.LLM, provider)
    if provider == "anthropic":
        return AnthropicLLM(spec)
    if provider == "google":
        return GoogleLLM(spec)
    return OpenAICompatibleLLM(spec)


def get_tts_adapter(provider: str) -> TTSAdapter:
    """Build the adapter for a TTS provider key."""
    spec = get_spec(CredentialKind.TTS, provider)
    if provider == "elevenlabs":
        return ElevenLabsTTS(spec)
    return OpenAISpeechTTS(spec)


def get_adapter(kind: CredentialKind | str, provider: str) -> LLMAdapter | TTSAdapter:
    kind = CredentialKind(str(kind))
    if kind == CredentialKind.LLM:
        return get_llm_adapter(provider)
    return get_tts_adapter(provider)


def catalog() -> dict[str, list[dict[str, Any]]]:
    """Full provider catalogue for the settings UI."""
    return {
        "llm": [s.as_dict() for s in _LLM_SPECS],
        "tts": [s.as_dict() for s in _TTS_SPECS],
    }


@dataclass
class VerificationResult:
    """Outcome of checking a key against its provider."""

    ok: bool
    models: list[ModelInfo] = field(default_factory=list)
    message: str = ""


def verify_credential(
    kind: CredentialKind | str,
    provider: str,
    api_key: str,
    base_url: str | None = None,
) -> VerificationResult:
    """Check a key by asking the provider what models it can reach.

    Never raises for an expected failure: a rejected key is a normal outcome
    that the UI needs to display, not an exception to surface as a 500.
    """
    if not api_key or not api_key.strip():
        return VerificationResult(ok=False, message="API key is empty")

    kind = CredentialKind(str(kind))
    try:
        spec = get_spec(kind, provider)
        resolved = validate_base_url(base_url) or spec.default_base_url
        if not resolved:
            return VerificationResult(
                ok=False, message="this provider needs a base URL"
            )
        adapter = get_adapter(kind, provider)
        models = adapter.list_models(api_key.strip(), resolved)
    except ProviderError as exc:
        return VerificationResult(ok=False, message=str(exc))
    except Exception as exc:  # defensive: never leak a stack trace to the UI
        return VerificationResult(ok=False, message=_clean_error(exc, api_key))

    return VerificationResult(
        ok=True,
        models=models,
        message=f"key accepted, {len(models)} model(s) available",
    )
