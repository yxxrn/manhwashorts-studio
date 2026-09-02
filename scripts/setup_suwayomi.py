#!/usr/bin/env python3
"""Install the pinned Suwayomi sidecar JAR used by ManhwaShorts."""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

VERSION = "v2.3.2243"
SHA256 = "821141b32e170d4a02d3cbdfed577ed8f07bd22383ff5f4132ebb5ae40e98dd5"
FILENAME = f"Suwayomi-Server-{VERSION}.jar"
URL = f"https://github.com/Suwayomi/Suwayomi-Server/releases/download/{VERSION}/{FILENAME}"
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "vendor" / "suwayomi" / "Suwayomi-Server.jar"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def java_major() -> int | None:
    try:
        process = subprocess.run(["java", "-version"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    text = (process.stderr or process.stdout).splitlines()[0] if (process.stderr or process.stdout) else ""
    match = re.search(r'"(\d+)', text)
    return int(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    major = java_major()
    if major is None or major < 21:
        raise SystemExit("Suwayomi requires Java 21+. Install a Java 21 runtime, then rerun this setup.")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.is_file() and sha256(DEST) == SHA256 and not args.force:
        print(f"Suwayomi {VERSION} already installed: {DEST}")
        return 0
    with tempfile.NamedTemporaryFile(prefix="suwayomi-", suffix=".jar", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        print(f"Downloading Suwayomi {VERSION}...")
        with urllib.request.urlopen(URL, timeout=60) as response, tmp_path.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual = sha256(tmp_path)
        if actual != SHA256:
            raise SystemExit(f"checksum mismatch: expected {SHA256}, got {actual}")
        tmp_path.replace(DEST)
    finally:
        tmp_path.unlink(missing_ok=True)
    print(f"Installed: {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
