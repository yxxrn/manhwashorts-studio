"""Application configuration.

Settings are loaded from environment variables and an optional ``.env`` file.
Nothing here should ever be logged verbatim: secrets are stored as
``SecretStr`` so accidental ``repr()`` calls do not leak values.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration for ManhwaShorts Studio."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        env_prefix="MS_",
        extra="ignore",
    )

    # --- Core ---
    app_name: str = "ManhwaShorts Studio"
    version: str = "1.5.6"
    environment: str = Field(default="local", description="local | staging | production")
    debug: bool = True
    # Registration stays open for local development, closed on the private
    # production instance after the initial account is created.
    allow_registration: bool = True

    host: str = "127.0.0.1"
    port: int = 8000

    # --- Storage ---
    data_dir: Path = BASE_DIR / "data"
    storage_dir: Path = BASE_DIR / "data" / "storage"
    output_dir: Path = BASE_DIR / "data" / "output"
    tmp_dir: Path = BASE_DIR / "data" / "tmp"

    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'manhwashorts.db'}"

    # --- Security ---
    # Left unset by default and resolved from data/.secret_key on first use
    # (see resolve_secret_key). Generating a fresh key per process would
    # invalidate every session cookie on restart, logging everyone out.
    secret_key: SecretStr | None = None
    session_cookie: str = "ms_session"
    session_max_age: int = 60 * 60 * 24 * 7

    # Fernet key for encrypting OAuth tokens at rest. Generated on first run
    # and persisted to data/.fernet_key if not supplied.
    fernet_key: SecretStr | None = None

    # --- Upload limits ---
    max_upload_mb: int = 25
    allowed_image_types: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")
    allowed_doc_types: tuple[str, ...] = (
        "text/plain",
        "text/markdown",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    # --- Video defaults ---
    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30
    max_short_seconds: int = 60
    default_target_seconds: int = 60

    # --- Rendering ---
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    render_workers: int = 1
    # Which encoder to use: auto | cpu | nvenc | qsv | vaapi | videotoolbox.
    # "auto" prefers a working GPU and falls back to CPU. An unavailable GPU
    # never fails a render; it falls back and records why.
    video_encoder: str = Field(
        default="auto",
        description="auto | cpu | nvenc | qsv | vaapi | videotoolbox",
    )
    subtitle_font: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    subtitle_font_name: str = "Anton"
    # "legacy" accepts the original {text, format} local API; "openai" sends
    # {input, response_format} to OmniVoice's /v1/audio/speech endpoint.
    tts_http_protocol: str = "legacy"
    tts_http_model: str = "omnivoice"
    tts_http_response_format: str = "wav"
    tts_http_instruct: str = "male, young adult, moderate pitch, american accent"
    tts_http_language: str = "en"
    tts_http_voice: str = "default"
    tts_http_seed: int = 42
    tts_http_num_step: int = 32
    tts_http_guidance_scale: float = 1.8
    # Preset audio polish applied after OmniVoice returns a valid clip.
    # "expressive" = the selected no. 4 mastering profile.
    tts_http_audio_filter: str = "expressive"
    # zoompan can introduce micro-jitter on still webtoon art; production keeps
    # panels static and uses clip fades. Re-enable only after a visual A/B pass.
    motion_enabled: bool = False


    # --- Cleanup (Fase 0.1 - keep the project light) ---
    # How old scratch files in data/tmp can be before being deleted.
    tmp_retention_days: int = 2
    # How old final videos in data/output can be before being deleted (only if not referenced).
    output_retention_days: int = 45
    # Soft limit. When exceeded, cleanup will be more aggressive on next run.
    max_data_gb: int = 12

    # --- Strip slicing (v1.4.0) ---
    # Webtoon pages ship as one long vertical strip. Cropping such a page to a
    # single 9:16 frame throws away most of it (measured: 70.7% on a 1:6 page),
    # so tall images are split into consecutive scene-sized pieces instead.
    strip_slice_enabled: bool = True
    # Height/width above which an image is treated as a strip. 2.5 sits well
    # clear of a normal portrait panel (~1:1.5) and of 9:16 itself (~1:1.78).
    strip_slice_min_ratio: float = 2.5
    # Ceiling on pieces from one image, so a freakishly long scan cannot flood
    # a project with assets.
    strip_slice_max_parts: int = 12

    # --- TTS ---
    tts_provider: str = Field(default="espeak", description="espeak | null | http")
    espeak_bin: str = "espeak-ng"
    tts_http_url: str | None = None
    tts_http_key: SecretStr | None = None

    # --- LLM ---
    llm_provider: str = Field(default="rules", description="rules | openai_compatible")
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout: int = 90

    # --- YouTube ---
    youtube_client_id: str | None = None
    youtube_client_secret: SecretStr | None = None
    youtube_redirect_uri: str = "http://127.0.0.1:8000/api/youtube/callback"
    youtube_enabled: bool = False

    # --- Policy guardrails ---
    require_rights_declaration: bool = True
    max_consecutive_panels_per_chapter: int = 8
    allow_public_publish: bool = False

    @field_validator("environment")
    @classmethod
    def _validate_env(cls, v: str) -> str:
        allowed = {"local", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"environment must be one of {sorted(allowed)}")
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.storage_dir, self.output_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)

    def resolve_secret_key(self) -> str:
        """Return the session signing key, persisting one on first run.

        Stored in ``data/.secret_key`` with 0600 permissions so sessions survive
        a restart. Set ``MS_SECRET_KEY`` to supply your own instead.
        """
        if self.secret_key is not None:
            return self.secret_key.get_secret_value()

        self.ensure_dirs()
        key_path = self.data_dir / ".secret_key"
        if key_path.exists():
            existing = key_path.read_text(encoding="utf-8").strip()
            if existing:
                self.secret_key = SecretStr(existing)
                return existing

        value = secrets.token_urlsafe(48)
        key_path.write_text(value, encoding="utf-8")
        key_path.chmod(0o600)
        self.secret_key = SecretStr(value)
        return value

    def resolve_fernet_key(self) -> bytes:
        """Return the Fernet key, generating and persisting one if needed."""
        if self.fernet_key is not None:
            return self.fernet_key.get_secret_value().encode()

        from cryptography.fernet import Fernet

        self.ensure_dirs()
        key_path = self.data_dir / ".fernet_key"
        if key_path.exists():
            return key_path.read_bytes().strip()

        key = Fernet.generate_key()
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        return key


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
