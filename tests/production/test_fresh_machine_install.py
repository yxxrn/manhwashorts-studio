from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_env_example_has_no_machine_specific_home_path():
    text = (ROOT / ".env.example").read_text()
    assert "/home/ubuntu/" not in text
    assert "MS_YOUTUBE_BROWSER_EXECUTABLE=google-chrome" in text


def test_fresh_machine_installer_covers_native_runtime_and_migration():
    text = (ROOT / "install.sh").read_text()
    for required in (
        "python3-venv",
        "ffmpeg",
        "espeak-ng",
        "tesseract-ocr",
        "openjdk-21-jre-headless",
        "google-chrome-stable_current_amd64.deb",
        "alembic\" upgrade head",
        "scripts/manhwashorts doctor",
    ):
        assert required in text


def test_machine_cli_exposes_recovery_commands():
    text = (ROOT / "scripts" / "manhwashorts").read_text()
    for command in ("doctor", "migrate", "serve", "youtube-account", "suwayomi-setup"):
        assert command in text
