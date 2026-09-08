#!/usr/bin/env python3
"""Migrate legacy ``ms_env.sh`` deployment settings into the single ``.env`` file."""
from __future__ import annotations

import argparse
import re
import shlex
import shutil
import time
from contextlib import suppress
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
LEGACY_PATH = ROOT / "ms_env.sh"
KEY_RE = re.compile(r"^MS_[A-Z0-9_]+$")


def _legacy_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, comments=True, posix=True)
        except ValueError as exc:
            raise SystemExit(f"legacy env contains invalid shell quoting: {exc}") from exc
        if parts and parts[0] == "export":
            parts = parts[1:]
        if len(parts) != 1 or "=" not in parts[0]:
            continue
        key, value = parts[0].split("=", 1)
        if KEY_RE.fullmatch(key):
            values[key] = value
    return values


def _ensure_env() -> None:
    if not ENV_PATH.exists():
        shutil.copy2(EXAMPLE_PATH, ENV_PATH)
    with suppress(OSError):
        ENV_PATH.chmod(0o600)
def main() -> int:
    parser = argparse.ArgumentParser(description="Merge legacy ms_env.sh into .env without printing secrets")
    parser.add_argument("--keep-legacy", action="store_true", help="leave ms_env.sh in place after migration")
    args = parser.parse_args()
    _ensure_env()
    if not LEGACY_PATH.is_file():
        print("Config ready: .env (no legacy runtime env found)")
        return 0

    legacy = _legacy_values(LEGACY_PATH)
    current = {key: value for key, value in dotenv_values(ENV_PATH).items() if value is not None}
    defaults = {key: value for key, value in dotenv_values(EXAMPLE_PATH).items() if value is not None}
    migrated = 0
    preserved = 0
    for key, value in legacy.items():
        current_value = current.get(key)
        if current_value is not None and current_value != defaults.get(key):
            preserved += 1
            continue
        set_key(str(ENV_PATH), key, value, quote_mode="auto")
        migrated += 1

    backup = ""
    if not args.keep_legacy:
        backup_dir = ROOT / "data" / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"ms_env.sh.{int(time.time())}.bak"
        shutil.move(str(LEGACY_PATH), str(backup_path))
        with suppress(OSError):
            backup_path.chmod(0o600)
        backup = str(backup_path.relative_to(ROOT))
    print(f"Config migration complete: migrated={migrated} preserved={preserved} file=.env")
    with suppress(OSError):
        ENV_PATH.chmod(0o600)
    if backup:
        print(f"Legacy backup moved to {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
