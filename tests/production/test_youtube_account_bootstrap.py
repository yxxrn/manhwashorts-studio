from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts" / "youtube_browser_account.py"


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["MS_TEST_MODE"] = "1"
    env["MS_YOUTUBE_BROWSER_ACCOUNTS_DIR"] = str(tmp_path / "accounts")
    env["MS_YOUTUBE_BROWSER_PROFILE_DIR"] = str(tmp_path / "legacy")
    return env


def test_youtube_account_ensure_is_idempotent(tmp_path):
    env = _env(tmp_path)
    command = [sys.executable, str(CLI), "ensure", "rurushortss", "RuruShorts"]
    first = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    second = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout.strip() == second.stdout.strip()
