from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_one_command_bootstrap_exists_for_linux_and_windows():
    assert 'exec "$ROOT/install.sh" --systemd --production --with-pocket-tts' in _text("bootstrap.sh")
    cmd = _text("bootstrap.cmd").lower()
    assert 'bootstrap.ps1' in cmd
    assert '%*' in cmd
    ps = _text("bootstrap.ps1")
    for needle in ("Python.Python.3.11", "Gyan.FFmpeg", "UB-Mannheim.TesseractOCR", "Google.Chrome"):
        assert needle in ps


def test_deployment_uses_single_dotenv_not_legacy_shell_env():
    machine = _text("scripts/manhwashorts")
    assert "ms_env.sh" not in machine
    assert "MS_RUNTIME_ENV_FILE" not in machine
    config = _text("app/config.py")
    assert 'BASE_DIR / ".env"' in config
    assert 'MS_TEST_MODE' in config
    assert "migrate_legacy_env.py" in _text("install.sh")


def test_bootstrap_installs_exact_production_sources():
    setup = _text("scripts/setup_suwayomi_extensions.py")
    assert "6247824327199706550" in setup
    assert "7185601298150078890" in setup
    assert "eu.kanade.tachiyomi.extension.en.asurascans" in setup
    assert "eu.kanade.tachiyomi.extension.en.readcomicsonline" in setup
    assert "setup_suwayomi_extensions.py" in _text("install.sh")
    assert "setup_suwayomi_extensions.py" in _text("bootstrap.ps1")
    assert "shutil.move(str(tmp_path), str(DEST))" in _text("scripts/setup_suwayomi.py")


def test_pocket_runtime_is_cross_platform_and_quantization_is_optional():
    setup = _text("scripts/setup_pocket_tts_runtime.py")
    assert "Scripts/python.exe" in setup
    assert 'f"pocket-tts=={VERSION}"' in setup
    assert '"torchao>=0.16.0"' in setup
    assert 'mode = "int8"' in setup
    assert 'else "fp32"' in setup
    win = _text("scripts/start_pocket_tts_windows.ps1")
    assert "mode.txt" in win
    assert '$UseQuantized = $Mode -eq "int8"' in win
    assert "retrying local FP32 mode" in win
    assert 'Set-Content -LiteralPath $ModeFile -Value "fp32"' in win
    linux = _text("scripts/setup_pocket_tts.sh")
    assert "mode.txt" in linux
    assert "QUANTIZE_ARG" in linux


def test_cookie_bootstrap_is_available_on_both_platforms():
    linux = _text("install.sh")
    for needle in ("--youtube-account", "--youtube-cookies", "youtube_browser_account.py import-cookies"):
        assert needle in linux
    win = _text("bootstrap.ps1")
    for needle in ("$YouTubeAccount", "$YouTubeCookies", "youtube_browser_account.py", "import-cookies"):
        assert needle in win
    assert "ManhwaShorts-Server.cmd" in win
    assert "Import cookies.txt" in _text("app/static/app.js")
    assert "/youtube/browser/accounts/{account_id}/cookies" in _text("app/routers/publish.py")
    assert "noVNC/X11 sementara" not in _text("app/templates/index.html")


def test_cross_platform_lock_has_windows_and_unix_backends():
    source = _text("app/services/file_lock.py")
    assert 'os.name == "nt"' in source
    assert "import msvcrt" in source
    assert "import fcntl" in source
    assert "file_lock.try_lock" in _text("scripts/production_run.py")
    assert "file_lock.try_lock" in _text("app/services/youtube_browser.py")
