#!/usr/bin/env python3
"""Create a self-contained Pocket TTS CPU runtime on Linux, macOS, or Windows."""
from __future__ import annotations

import argparse
import os
import subprocess
import venv
from pathlib import Path

VERSION = "3.1.0"


def _venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(argv: list[str], timeout: int = 1800) -> None:
    completed = subprocess.run(argv, check=False, timeout=timeout)
    if completed.returncode != 0:
        raise SystemExit(f"command failed ({completed.returncode}): {argv[0]}")


def main() -> int:
    default_root = (
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ManhwaShorts" / "pocket-tts"
        if os.name == "nt"
        else Path.home() / "pocket-tts-runtime"
    )
    parser = argparse.ArgumentParser(description="Install Pocket TTS in an isolated CPU-only venv")
    parser.add_argument("--runtime-dir", type=Path, default=default_root)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    runtime = args.runtime_dir.expanduser().resolve()
    python = _venv_python(runtime / ".venv")

    if args.force and (runtime / ".venv").exists():
        import shutil

        shutil.rmtree(runtime / ".venv")
    if not python.is_file():
        runtime.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, clear=False).create(runtime / ".venv")
        python = _venv_python(runtime / ".venv")

    if not args.force and python.is_file():
        healthy = subprocess.run(
            [str(python), "-c", f"import importlib.metadata as m, torch; raise SystemExit(0 if m.version('pocket-tts') == '{VERSION}' and not torch.cuda.is_available() else 1)"],
            capture_output=True, text=True, check=False, timeout=60,
        )
        executable = runtime / ".venv" / ("Scripts/pocket-tts.exe" if os.name == "nt" else "bin/pocket-tts")
        if healthy.returncode == 0 and executable.is_file():
            quant_probe = subprocess.run(
                [str(python), "-c", "import torchao"], capture_output=True, text=True, check=False, timeout=60
            )
            mode = "int8" if quant_probe.returncode == 0 else "fp32"
            (runtime / "mode.txt").write_text(mode + "\n", encoding="ascii")
            print(f"Pocket TTS runtime already ready: {runtime} mode={mode}")
            return 0

    _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-U", "pip", "setuptools", "wheel"])
    _run([
        str(python), "-m", "pip", "install", "--disable-pip-version-check",
        "--index-url", "https://download.pytorch.org/whl/cpu", "torch>=2.5",
    ])
    _run([
        str(python), "-m", "pip", "install", "--disable-pip-version-check",
        f"pocket-tts=={VERSION}",
    ])
    quant = subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "torchao>=0.16.0"],
        capture_output=True, text=True, check=False, timeout=1800,
    )
    probe = subprocess.run(
        [str(python), "-c", "import torch, pocket_tts; print(torch.cuda.is_available())"],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if probe.returncode != 0:
        raise SystemExit("Pocket TTS runtime import check failed")
    if probe.stdout.strip().casefold() != "false":
        raise SystemExit("Pocket TTS bootstrap expected a CPU-only PyTorch runtime")
    quantized = quant.returncode == 0 and subprocess.run(
        [str(python), "-c", "import torchao"], capture_output=True, text=True, check=False, timeout=60
    ).returncode == 0
    mode = "int8" if quantized else "fp32"
    (runtime / "mode.txt").write_text(mode + "\n", encoding="ascii")
    if not quantized:
        print("Pocket TTS quantization unavailable on this platform; using local FP32 mode.")
    executable = runtime / ".venv" / ("Scripts/pocket-tts.exe" if os.name == "nt" else "bin/pocket-tts")
    if not executable.is_file():
        raise SystemExit(f"Pocket TTS executable missing: {executable}")
    print(f"Pocket TTS runtime ready: {runtime} mode={mode}")
    print(f"Executable: {executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
