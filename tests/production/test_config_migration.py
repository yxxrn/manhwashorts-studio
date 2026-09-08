from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migrate_legacy_env.py"


def _module():
    spec = importlib.util.spec_from_file_location("migrate_legacy_env_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_env_migrates_into_dotenv_without_echoing_secrets(tmp_path, monkeypatch, capsys):
    module = _module()
    env = tmp_path / ".env"
    example = tmp_path / ".env.example"
    legacy = tmp_path / "ms_env.sh"
    example.write_text("MS_ENVIRONMENT=local\nMS_DEBUG=true\nMS_LLM_API_KEY=\n", encoding="utf-8")
    env.write_text("MS_ENVIRONMENT=production\nMS_DEBUG=true\nMS_LLM_API_KEY=\n", encoding="utf-8")
    secret = "secret-never-print"
    legacy.write_text(f"export MS_DEBUG=false\nMS_LLM_API_KEY='{secret}'\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "ENV_PATH", env)
    monkeypatch.setattr(module, "EXAMPLE_PATH", example)
    monkeypatch.setattr(module, "LEGACY_PATH", legacy)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    assert module.main() == 0
    text = env.read_text(encoding="utf-8")
    assert "MS_ENVIRONMENT=production" in text
    assert "MS_DEBUG='false'" in text or "MS_DEBUG=false" in text
    assert secret in text
    assert secret not in capsys.readouterr().out
    assert not legacy.exists()
    assert list((tmp_path / "data" / "migration-backups").glob("ms_env.sh.*.bak"))
