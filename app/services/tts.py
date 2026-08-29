"""Text-to-speech providers (PRD FR-05).

Providers implement one interface so the render pipeline never cares which
engine produced the audio:

* ``EspeakProvider`` - local espeak-ng, no network, always available.
* ``HttpProvider``   - any HTTP TTS endpoint returning audio bytes.
* ``NullProvider``   - silent audio of the estimated length, for tests.

Word timings are estimated by distributing the measured clip duration across
words weighted by length. That is accurate enough to drive karaoke-style
subtitles without requiring a forced aligner.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.constants import DEFAULT_ENGLISH_VOICE_ID


class TTSError(RuntimeError):
    """Raised when speech synthesis fails."""


@dataclass
class SpeechClip:
    """One synthesised utterance on disk."""

    path: Path
    text: str
    duration: float
    voice_id: str
    provider: str
    word_timings: list[dict] = field(default_factory=list)
    voice_profile: dict = field(default_factory=dict)

    @property
    def voice_profile_hash(self) -> str:
        payload = json.dumps(self.voice_profile or {"provider": self.provider, "voice_id": self.voice_id}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


# espeak-ng voice names keyed by our language codes.
VOICE_CATALOG: dict[str, dict[str, str]] = {
    "en": {"label": "American English (espeak)", "espeak": "en-us"},
    "en-gb": {"label": "British English (espeak)", "espeak": "en-gb"},
    "id": {"label": "Indonesian (espeak)", "espeak": "id"},
    "id-male": {"label": "Indonesian, lower pitch", "espeak": "id"},
    "ko": {"label": "Korean (espeak)", "espeak": "ko"},
}


def voice_profile_for(provider: str, voice_id: str, *, language: str = "en-US", model: str = "", speed: float = 1.0, sample_rate: int = 48000, channels: int = 2, **extra) -> dict:
    """Canonical immutable identity for every clip in a render job."""
    profile = {
        "provider": provider, "model": model, "voice_id": voice_id,
        "language": language, "speed": round(float(speed), 4),
        "sample_rate": int(sample_rate), "channels": int(channels),
        **{key: value for key, value in extra.items() if value not in (None, "")},
    }
    return profile


def probe_duration(path: Path) -> float:
    """Read a media file's duration with ffprobe."""
    try:
        out = subprocess.run(
            [
                settings.ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return round(float(out.stdout.strip() or 0.0), 3)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as exc:
        raise TTSError(f"could not probe duration of {path.name}: {exc}") from exc


def estimate_word_timings(text: str, duration: float, offset: float = 0.0) -> list[dict]:
    """Distribute ``duration`` across words, weighted by character length.

    Longer words take proportionally longer to say, and punctuation earns a
    small pause, which keeps subtitles from drifting ahead of the voice.
    """
    words = re.findall(r"\S+", text)
    if not words or duration <= 0:
        return []

    weights: list[float] = []
    for word in words:
        # +1 so single-character words still get a slice.
        weight = len(re.sub(r"\W", "", word)) + 1.0
        if word.endswith((",", ";", ":")):
            weight += 1.5
        elif word.endswith((".", "!", "?")):
            weight += 2.5
        weights.append(weight)

    total = sum(weights) or 1.0
    timings: list[dict] = []
    cursor = offset
    for word, weight in zip(words, weights, strict=True):
        span = duration * (weight / total)
        timings.append(
            {
                "word": word,
                "start": round(cursor, 3),
                "end": round(cursor + span, 3),
            }
        )
        cursor += span
    # Absorb rounding drift into the final word.
    if timings:
        timings[-1]["end"] = round(offset + duration, 3)
    return timings


class TTSProvider(Protocol):
    name: str

    def synthesize(self, text: str, out_path: Path, voice_id: str, speed: float) -> SpeechClip: ...

    def available(self) -> bool: ...


class EspeakProvider:
    """Local synthesis via espeak-ng. Robotic but dependency-free and offline."""

    name = "espeak"

    def available(self) -> bool:
        return shutil.which(settings.espeak_bin) is not None

    def synthesize(
        self, text: str, out_path: Path, voice_id: str = "en", speed: float = 1.0
    ) -> SpeechClip:
        if not text.strip():
            raise TTSError("cannot synthesize empty text")
        if not self.available():
            raise TTSError(
                f"{settings.espeak_bin} not found. Install it with: "
                "sudo apt-get install espeak-ng"
            )

        voice = VOICE_CATALOG.get(voice_id, VOICE_CATALOG["en"])["espeak"]
        # espeak's default 175 wpm; scale by the requested speed.
        wpm = max(80, min(400, int(175 * speed)))
        pitch = 30 if voice_id.endswith("-male") else 50

        out_path.parent.mkdir(parents=True, exist_ok=True)
        raw = out_path.with_suffix(".raw.wav")
        try:
            subprocess.run(
                [
                    settings.espeak_bin,
                    "-v", voice,
                    "-s", str(wpm),
                    "-p", str(pitch),
                    "-w", str(raw),
                    text,
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise TTSError(f"espeak-ng failed: {exc.stderr[:400]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TTSError("espeak-ng timed out") from exc

        # Normalise to 48 kHz stereo AAC-friendly WAV and level the loudness so
        # concatenated segments do not jump in volume.
        try:
            subprocess.run(
                [
                    settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(raw),
                    "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ar", "48000", "-ac", "2",
                    str(out_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise TTSError(f"audio normalisation failed: {exc.stderr[:400]}") from exc
        finally:
            raw.unlink(missing_ok=True)

        duration = probe_duration(out_path)
        return SpeechClip(
            path=out_path,
            text=text,
            duration=duration,
            voice_id=voice_id,
            provider=self.name,
            word_timings=estimate_word_timings(text, duration),
            voice_profile=voice_profile_for(self.name, voice_id, language=("id" if voice_id.startswith("id") else "en-US"), model="", speed=speed, sample_rate=48000, channels=2),
        )


class NullProvider:
    """Generates silence of the estimated duration. Used in tests and CI."""

    name = "null"

    def available(self) -> bool:
        return shutil.which(settings.ffmpeg_bin) is not None

    def synthesize(
        self, text: str, out_path: Path, voice_id: str = "en", speed: float = 1.0
    ) -> SpeechClip:
        from app.services.script import estimate_duration

        duration = max(0.8, estimate_duration(text) / max(0.1, speed))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"anullsrc=r=48000:cl=stereo:d={duration:.3f}",
                "-t", f"{duration:.3f}",
                str(out_path),
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
        return SpeechClip(
            path=out_path,
            text=text,
            duration=probe_duration(out_path),
            voice_id=voice_id,
            provider=self.name,
            word_timings=estimate_word_timings(text, duration),
            voice_profile=voice_profile_for(self.name, voice_id, language=("id" if voice_id.startswith("id") else "en-US"), model="", speed=speed, sample_rate=48000, channels=2),
        )


class HttpProvider:
    """Calls an external TTS HTTP API that returns raw audio bytes."""

    name = "http"

    def available(self) -> bool:
        return bool(settings.tts_http_url)

    def synthesize(
        self, text: str, out_path: Path, voice_id: str = "en", speed: float = 1.0
    ) -> SpeechClip:
        import httpx

        url = settings.tts_http_url
        if not url:
            raise TTSError("MS_TTS_HTTP_URL is not configured")

        headers = {"Content-Type": "application/json"}
        if settings.tts_http_key:
            headers["Authorization"] = f"Bearer {settings.tts_http_key.get_secret_value()}"

        if settings.tts_http_protocol == "grok":
            payload = {
                "model": settings.tts_http_model,
                "text": text,
                "language": "id" if voice_id.startswith("id") else settings.tts_http_language,
            }
        elif settings.tts_http_protocol == "openai":
            payload = {
                "model": settings.tts_http_model,
                "input": text,
                "voice": settings.tts_http_voice or voice_id or "default",
                "response_format": settings.tts_http_response_format,
                "speed": max(0.25, min(4.0, speed)),
                # Explicit project voice choice; never infer from image content.
                "language": "id" if voice_id.startswith("id") else settings.tts_http_language,
                "instruct": settings.tts_http_instruct,
                "num_step": settings.tts_http_num_step,
                "guidance_scale": settings.tts_http_guidance_scale,
                "seed": settings.tts_http_seed,
            }
        else:
            payload = {
                "text": text,
                "voice": voice_id,
                "speed": speed,
                "format": "wav",
            }

        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=900)
            response.raise_for_status()
        except Exception as exc:
            raise TTSError(f"TTS HTTP request failed: {type(exc).__name__}: {exc}") from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        self._polish(out_path)
        duration = probe_duration(out_path)
        return SpeechClip(
            path=out_path,
            text=text,
            duration=duration,
            voice_id=voice_id,
            provider=self.name,
            word_timings=estimate_word_timings(text, duration),
            voice_profile=voice_profile_for(self.name, voice_id, language=("id" if voice_id.startswith("id") else "en-US"), speed=speed, sample_rate=24000, channels=1),
        )

    def synthesize_sections(
        self, texts: list[str], work_dir: Path, voice_id: str = "en", speed: float = 1.0
    ) -> list[SpeechClip]:
        """Generate sections with one locked provider voice configuration."""
        import httpx

        if not texts or not all(text.strip() for text in texts):
            raise TTSError("cannot synthesize empty sections")
        language = "id" if voice_id.startswith("id") else settings.tts_http_language
        headers = {"Content-Type": "application/json"}
        if settings.tts_http_key:
            headers["Authorization"] = f"Bearer {settings.tts_http_key.get_secret_value()}"
        if settings.tts_http_protocol == "grok":
            # Grok-protocol endpoints reject OpenAI-shaped payloads (they
            # require "text"/"language" and have no voice/instruct fields),
            # so mirror the single-clip grok contract for every section.
            text_key = "text"
            base_payload = {
                "model": settings.tts_http_model,
                "text": texts[0],
                "language": language,
            }
        else:
            text_key = "input"
            base_payload = {
                "model": settings.tts_http_model,
                "input": texts[0],
                "voice": settings.tts_http_voice or voice_id or "default",
                "response_format": settings.tts_http_response_format,
                "speed": max(0.25, min(4.0, speed)),
                "language": language,
                "instruct": settings.tts_http_instruct,
                "num_step": min(settings.tts_http_num_step, 8),
                "guidance_scale": settings.tts_http_guidance_scale,
                "seed": settings.tts_http_seed,
            }
        clips: list[SpeechClip] = []
        ref_path = work_dir / "shared_voice_reference.wav"
        try:
            response = httpx.post(settings.tts_http_url, headers=headers, json=base_payload, timeout=900)
            if response.status_code == 503:
                fallback = dict(base_payload)
                fallback[text_key] = texts[0]
                response = httpx.post(settings.tts_http_url or "", headers=headers, json=fallback, timeout=900)
            response.raise_for_status()
            ref_path.write_bytes(response.content)
            self._polish(ref_path)
            for index, text in enumerate(texts):
                path = work_dir / f"{index:02d}_session.wav"
                if index == 0:
                    # Preserve the exact polished reference bytes without
                    # materializing the whole audio file in Python memory.
                    shutil.copyfile(ref_path, path)
                else:
                    # Reuse the same provider settings for every section. A
                    # transient 503 must not silently switch voices or providers.
                    stable_payload = dict(base_payload)
                    stable_payload[text_key] = text
                    if text_key == "input":
                        stable_payload["speed"] = max(0.25, min(4.0, speed))
                        stable_payload["num_step"] = min(settings.tts_http_num_step, 8)
                    result = None
                    for _attempt in range(4):
                        result = httpx.post(
                            settings.tts_http_url or "",
                            headers=headers,
                            json=stable_payload,
                            timeout=900,
                        )
                        if result.status_code != 503:
                            break
                        retry_after = float(result.headers.get("retry-after", "30"))
                        time.sleep(min(90.0, max(5.0, retry_after)))
                    assert result is not None
                    result.raise_for_status()
                    path.write_bytes(result.content)
                    self._polish(path)
                duration = probe_duration(path)
                clips.append(SpeechClip(path, text, duration, voice_id, self.name, estimate_word_timings(text, duration), voice_profile_for(self.name, voice_id, language=language, model=settings.tts_http_model, speed=speed, sample_rate=24000, channels=1, instruct=settings.tts_http_instruct, seed=settings.tts_http_seed)))
            return clips
        except Exception as exc:
            raise TTSError(f"shared-reference TTS failed: {type(exc).__name__}: {exc}") from exc
        finally:
            ref_path.unlink(missing_ok=True)

    @staticmethod
    def _polish(path: Path) -> None:
        """Apply a selected mastering preset without hiding bad TTS output."""
        preset = settings.tts_http_audio_filter.lower().strip()
        filters = {
            "natural": "highpass=f=70,lowpass=f=15000,loudnorm=I=-16:TP=-1.5:LRA=7",
            "warm": "highpass=f=65,lowpass=f=14500,equalizer=f=180:t=q:w=0.8:g=1.2,equalizer=f=3200:t=q:w=1:g=1.2,acompressor=threshold=-20dB:ratio=2:attack=18:release=100,loudnorm=I=-16:TP=-1.5:LRA=7",
            "clear": "highpass=f=80,lowpass=f=16000,equalizer=f=2800:t=q:w=1:g=1.5,equalizer=f=6500:t=q:w=1:g=0.8,acompressor=threshold=-22dB:ratio=2:attack=12:release=90,loudnorm=I=-15:TP=-1.5:LRA=6",
            "expressive": "highpass=f=70,lowpass=f=15500,acompressor=threshold=-24dB:ratio=2.5:attack=25:release=140,equalizer=f=2200:t=q:w=1:g=1.0,equalizer=f=5200:t=q:w=1:g=0.7,loudnorm=I=-15:TP=-1.5:LRA=7",
        }
        audio_filter = filters.get(preset)
        if not audio_filter:
            return
        polished = path.with_name(f".{path.stem}.polished{path.suffix}")
        try:
            subprocess.run(
                [settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-af", audio_filter, "-ar", "24000", "-ac", "1", str(polished)],
                capture_output=True, text=True, timeout=180, check=True,
            )
            if polished.stat().st_size < 1024:
                raise TTSError("audio mastering returned an empty clip")
            polished.replace(path)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            polished.unlink(missing_ok=True)
            raise TTSError(f"audio mastering failed: {exc}") from exc


class ByokProvider:
    """Speech from a user-supplied key (v1.1 BYOK).

    Holds the decrypted key only for the life of the object, which the pipeline
    creates per voice-over run and drops immediately afterwards. The key is
    never written to disk or logged.

    Deliberately does NOT fall back to espeak on failure: the user chose to pay
    for this voice, so a silent downgrade to a robotic one would be a worse
    outcome than a clear error they can act on.
    """

    name = "byok"

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        base_url: str | None = None,
        voice: str = "",
        label: str = "",
    ) -> None:
        from app.services import providers as pv

        self._adapter = pv.get_tts_adapter(provider)
        self._provider = provider
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._voice = voice
        self.name = f"byok:{provider}"
        self.label = label or provider

    def available(self) -> bool:
        return bool(self._api_key and self._model)

    def synthesize(
        self, text: str, out_path: Path, voice_id: str = "en", speed: float = 1.0
    ) -> SpeechClip:
        from app.services.providers import ProviderError

        provider_key = self._provider.lower()
        requested_voice = (voice_id or "").strip()
        explicit_voice = (
            requested_voice
            if requested_voice and requested_voice != DEFAULT_ENGLISH_VOICE_ID
            else ""
        )
        if provider_key in {"openai", "custom_openai"}:
            # OpenAI-compatible APIs keep the selected TTS model separate from
            # the timbre voice.  Never send the model identifier as ``voice``.
            voice = explicit_voice or self._voice or "alloy"
        elif provider_key == "elevenlabs":
            # An explicit project choice wins; otherwise use the credential's
            # stored voice selection.
            voice = explicit_voice or self._voice or self._model
        else:
            voice = explicit_voice or self._voice or "default"
        if not voice:
            raise TTSError(f"{self.label} has no selected voice")
        try:
            self._adapter.synthesize(
                api_key=self._api_key,
                model=self._model,
                text=text,
                out_path=out_path,
                voice=voice,
                speed=speed,
                base_url=self._base_url,
            )
        except ProviderError as exc:
            raise TTSError(f"{self.label} speech failed: {exc}") from None

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise TTSError(f"{self.label} returned empty audio")

        duration = probe_duration(out_path)
        return SpeechClip(
            path=out_path,
            text=text,
            duration=duration,
            voice_id=voice or voice_id,
            provider=self.name,
            word_timings=estimate_word_timings(text, duration),
            voice_profile=voice_profile_for(self.name, voice or voice_id, language="en-US", model=self._model, speed=speed),
        )


_PROVIDERS: dict[str, type] = {
    "espeak": EspeakProvider,
    "null": NullProvider,
    "http": HttpProvider,
}


def get_provider(name: str | None = None) -> TTSProvider:
    """Return the configured provider, falling back to null if unavailable."""
    key = (name or settings.tts_provider or "espeak").lower()
    provider_cls = _PROVIDERS.get(key)
    if provider_cls is None:
        raise TTSError(f"unknown TTS provider: {key}")
    provider = provider_cls()
    if not provider.available() and key != "null":
        raise TTSError(f"TTS provider unavailable: {key}")
    return provider


def concat_audio(clips: list[Path], out_path: Path, gap: float = 0.18) -> float:
    """Concatenate clips with a small gap between beats. Returns duration."""
    if not clips:
        raise TTSError("no audio clips to concatenate")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filters: list[str] = []
    for i, clip in enumerate(clips):
        inputs += ["-i", str(clip)]
        filters.append(f"[{i}:a]")

    if gap > 0 and len(clips) > 1:
        # Insert silence between segments using adelay-free concat of pads.
        filter_parts = []
        for i, _ in enumerate(clips):
            pad = f"[a{i}]"
            if i < len(clips) - 1:
                filter_parts.append(f"[{i}:a]apad=pad_dur={gap}{pad}")
            else:
                filter_parts.append(f"[{i}:a]anull{pad}")
        chain = ";".join(filter_parts)
        streams = "".join(f"[a{i}]" for i in range(len(clips)))
        filter_complex = f"{chain};{streams}concat=n={len(clips)}:v=0:a=1[out]"
    else:
        filter_complex = f"{''.join(filters)}concat=n={len(clips)}:v=0:a=1[out]"

    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "48000", "-ac", "2",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    except subprocess.CalledProcessError as exc:
        raise TTSError(f"audio concat failed: {exc.stderr[:500]}") from exc
    return probe_duration(out_path)
