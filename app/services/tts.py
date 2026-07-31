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

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.config import settings


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


# espeak-ng voice names keyed by our language codes.
VOICE_CATALOG: dict[str, dict[str, str]] = {
    "id": {"label": "Indonesian (espeak)", "espeak": "id"},
    "id-male": {"label": "Indonesian, lower pitch", "espeak": "id"},
    "en": {"label": "English US (espeak)", "espeak": "en-us"},
    "en-gb": {"label": "English UK (espeak)", "espeak": "en-gb"},
    "ko": {"label": "Korean (espeak)", "espeak": "ko"},
}


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
        self, text: str, out_path: Path, voice_id: str = "id", speed: float = 1.0
    ) -> SpeechClip:
        if not text.strip():
            raise TTSError("cannot synthesize empty text")
        if not self.available():
            raise TTSError(
                f"{settings.espeak_bin} not found. Install it with: "
                "sudo apt-get install espeak-ng"
            )

        voice = VOICE_CATALOG.get(voice_id, VOICE_CATALOG["id"])["espeak"]
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
        )


class NullProvider:
    """Generates silence of the estimated duration. Used in tests and CI."""

    name = "null"

    def available(self) -> bool:
        return shutil.which(settings.ffmpeg_bin) is not None

    def synthesize(
        self, text: str, out_path: Path, voice_id: str = "id", speed: float = 1.0
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
        )


class HttpProvider:
    """Calls an external TTS HTTP API that returns raw audio bytes."""

    name = "http"

    def available(self) -> bool:
        return bool(settings.tts_http_url)

    def synthesize(
        self, text: str, out_path: Path, voice_id: str = "id", speed: float = 1.0
    ) -> SpeechClip:
        import httpx

        url = settings.tts_http_url
        if not url:
            raise TTSError("MS_TTS_HTTP_URL is not configured")

        headers = {"Content-Type": "application/json"}
        if settings.tts_http_key:
            headers["Authorization"] = f"Bearer {settings.tts_http_key.get_secret_value()}"

        try:
            response = httpx.post(
                url,
                headers=headers,
                json={"text": text, "voice": voice_id, "speed": speed, "format": "wav"},
                timeout=180,
            )
            response.raise_for_status()
        except Exception as exc:
            raise TTSError(f"TTS HTTP request failed: {type(exc).__name__}: {exc}") from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        duration = probe_duration(out_path)
        return SpeechClip(
            path=out_path,
            text=text,
            duration=duration,
            voice_id=voice_id,
            provider=self.name,
            word_timings=estimate_word_timings(text, duration),
        )


_PROVIDERS: dict[str, type] = {
    "espeak": EspeakProvider,
    "null": NullProvider,
    "http": HttpProvider,
}


def get_provider(name: str | None = None) -> TTSProvider:
    """Return the configured provider, falling back to null if unavailable."""
    key = (name or settings.tts_provider or "espeak").lower()
    provider = _PROVIDERS.get(key, EspeakProvider)()
    if not provider.available() and key != "null":
        return NullProvider()
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
