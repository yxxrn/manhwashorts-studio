#!/usr/bin/env python3
"""Fresh-machine readiness checks for ManhwaShorts."""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True

    @property
    def icon(self) -> str:
        if self.ok:
            return "✅"
        return "❌" if self.required else "⚠️"


def _run(argv: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _command(name: str) -> str:
    return shutil.which(name) or ""


def _java_major(command: str) -> int | None:
    result = _run([command, "-version"]) if command else None
    if result is None:
        return None
    import re

    text = (result.stderr or result.stdout).splitlines()
    match = re.search(r'"(\d+)', text[0] if text else "")
    return int(match.group(1)) if match else None


def collect() -> list[Check]:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    from app.config import settings
    from app.services.youtube_browser import YouTubeStudioBrowserPublisher

    checks: list[Check] = []
    py_ok = sys.version_info >= (3, 11)
    checks.append(Check("Python", py_ok, sys.version.split()[0]))
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    checks.append(Check("Virtualenv", in_venv, sys.prefix if in_venv else "not running from .venv"))

    missing: list[str] = []
    for module in ("fastapi", "sqlalchemy", "PIL", "cv2", "playwright", "cryptography"):
        try:
            importlib.import_module(module)
        except Exception:
            missing.append(module)
    checks.append(Check("Python packages", not missing, "all runtime imports available" if not missing else "missing: " + ", ".join(missing)))

    ffmpeg = _command(settings.ffmpeg_bin)
    ffprobe = _command(settings.ffprobe_bin)
    checks.append(Check("FFmpeg", bool(ffmpeg), ffmpeg or f"{settings.ffmpeg_bin} not found"))
    checks.append(Check("FFprobe", bool(ffprobe), ffprobe or f"{settings.ffprobe_bin} not found"))
    if ffmpeg:
        result = _run([ffmpeg, "-hide_banner", "-filters"])
        filters = (result.stdout + result.stderr) if result else ""
        ok = bool(result and result.returncode == 0 and "zoompan" in filters and "subtitles" in filters)
        checks.append(Check("FFmpeg filters", ok, "zoompan + subtitles/libass" if ok else "requires zoompan and subtitles/libass"))

    font = Path(settings.subtitle_font).expanduser()
    checks.append(Check("Subtitle font", font.is_file(), str(font)))

    tts_name = str(settings.tts_provider or "").lower()
    if tts_name == "espeak":
        espeak = _command(settings.espeak_bin)
        checks.append(Check("TTS", bool(espeak), espeak or "espeak-ng not found"))
    else:
        checks.append(Check("TTS", True, f"configured provider: {tts_name or 'external'}", required=False))

    if settings.youtube_browser_enabled:
        browser = YouTubeStudioBrowserPublisher._resolve_browser_executable(settings.youtube_browser_executable)
        checks.append(Check("Chrome", bool(browser), browser or "Google Chrome/Chromium not found"))
        try:
            importlib.import_module("playwright.sync_api")
            pw_ok = True
        except Exception:
            pw_ok = False
        checks.append(Check("Playwright", pw_ok, "Python driver installed" if pw_ok else "playwright package unavailable"))
        registry_root = Path(settings.youtube_browser_profile_dir).expanduser()
        checks.append(Check("YouTube login", registry_root.exists(), "profile exists; auth can be checked in UI" if registry_root.exists() else "first Google login still required", required=False))

    if settings.suwayomi_enabled:
        java = _command(settings.suwayomi_java_bin)
        major = _java_major(java)
        checks.append(Check("Java", major is not None and major >= 21, f"Java {major}" if major else "Java 21+ not found"))
        jar = Path(settings.suwayomi_jar_path).expanduser()
        checks.append(Check("Suwayomi", jar.is_file(), str(jar) if jar.is_file() else "run scripts/setup_suwayomi.py"))

    for label, path in (("Data dir", settings.data_dir), ("Storage dir", settings.storage_dir), ("Output dir", settings.output_dir), ("Temp dir", settings.tmp_dir)):
        target = Path(path).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe = target / ".doctor-write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            writable = True
        except OSError:
            writable = False
        checks.append(Check(label, writable, str(target)))

    try:
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        head = ScriptDirectory.from_config(config).get_current_head()
        db_engine = create_engine(settings.database_url, future=True)
        with db_engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        schema_ok = bool(current and current == head)
        checks.append(Check("Database schema", schema_ok, f"current={current or 'none'} head={head}"))
    except Exception as exc:
        checks.append(Check("Database schema", False, f"{type(exc).__name__}: {exc}"))

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether this machine is ready for ManhwaShorts")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    checks = collect()
    failed = [item for item in checks if item.required and not item.ok]
    if args.json:
        print(json.dumps({"ready": not failed, "checks": [asdict(item) for item in checks]}, indent=2))
    else:
        print("ManhwaShorts machine doctor")
        for item in checks:
            print(f"{item.icon} {item.name:<18} {item.detail}")
        print()
        print("Machine ready for production." if not failed else f"Machine NOT ready: {len(failed)} required check(s) failed.")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
