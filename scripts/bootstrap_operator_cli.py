"""Bootstrap the local runtime and launch the interactive operator console.

This module intentionally uses only the Python standard library so it can run
before the repository virtual environment has third-party packages installed.
It never handles provider credentials and never invokes a shell.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path

MIN_PYTHON = (3, 11)
FINGERPRINT_FILENAME = ".operator-cli-deps.fingerprint"
RUNTIME_REQUIREMENTS = "requirements.txt"
OPERATOR_ENTRYPOINT = Path("scripts") / "run_operator_cli.py"
VERSION_CHECK = "import sys; print('.'.join(str(part) for part in sys.version_info[:3]))"
IMPORT_CHECK = (
    "import sqlalchemy, PIL, fastapi, pydantic, cryptography; "
    "import app.services.credentials, app.services.operator_cli"
)


class BootstrapError(RuntimeError):
    """Safe bootstrap error with a stable recovery code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run_command(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one fixed-argument command without a shell or inherited secrets."""

    return subprocess.run(
        [os.fspath(part) for part in argv],
        cwd=os.fspath(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
        check=False,
    )


def _launch_operator(argv: Sequence[str | os.PathLike[str]], *, cwd: Path) -> int:
    """Run the operator in the same visible terminal."""

    completed = subprocess.run(
        [os.fspath(part) for part in argv],
        cwd=os.fspath(cwd),
        shell=False,
        check=False,
    )
    return int(completed.returncode)


def python_candidates() -> tuple[tuple[str, ...], ...]:
    """Return the required Windows-first interpreter discovery order."""

    return (("py", "-3.11"), ("py", "-3"), ("python",))


def _parse_python_version(output: str) -> tuple[int, int, int] | None:
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", str(output or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _is_supported_version(version: tuple[int, int, int] | None) -> bool:
    return version is not None and version[:2] >= MIN_PYTHON


def _read_python_version(
    command: Sequence[str],
    *,
    runner: CommandRunner,
    cwd: Path | None = None,
) -> tuple[int, int, int] | None:
    try:
        result = runner((*command, "-c", VERSION_CHECK), cwd=cwd, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return _parse_python_version(result.stdout)


def find_supported_python(
    *,
    runner: CommandRunner = _run_command,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, ...]:
    """Find Python 3.11+ using ``py -3.11``, ``py -3``, then ``python``."""

    for candidate in python_candidates():
        if which(candidate[0]) is None:
            continue
        if _is_supported_version(_read_python_version(candidate, runner=runner)):
            return candidate
    raise BootstrapError(
        "bootstrap.python_missing",
        "Python 3.11+ tidak ditemukan; install Python 3.11+ lalu coba lagi.",
    )


def dependency_fingerprint(requirements_bytes: bytes, python_version: str) -> str:
    """Hash exact requirement bytes and the selected interpreter version."""

    payload = b"interactive-production-cli-v1\0" + str(python_version).encode("utf-8")
    payload += b"\0" + bytes(requirements_bytes)
    return hashlib.sha256(payload).hexdigest()


def _venv_python(venv_dir: Path) -> Path:
    windows_python = venv_dir / "Scripts" / "python.exe"
    if os.name == "nt" or windows_python.exists():
        return windows_python
    return venv_dir / "bin" / "python"


def _read_marker(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def _write_marker(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".operator-cli-deps-",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
            handle.write(value + "\n")
        os.replace(temporary_name, path)
    except Exception:
        with suppress(OSError):
            os.unlink(temporary_name)
        raise


def _runtime_healthy(
    python: Path,
    *,
    root: Path,
    runner: CommandRunner,
) -> bool:
    try:
        result = runner(
            (str(python), "-c", IMPORT_CHECK),
            cwd=root,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _classify_install_failure(output: str) -> str:
    text = str(output or "").casefold()
    if "proxy" in text or "407" in text or "connect tunnel" in text:
        return "bootstrap.proxy_error"
    if "ssl" in text or "certificate" in text or "tls" in text:
        return "bootstrap.ssl_error"
    if any(
        marker in text
        for marker in (
            "network is unreachable",
            "temporary failure in name resolution",
            "failed to establish",
            "connection refused",
            "no matching distribution",
            "could not find a version",
        )
    ):
        return "bootstrap.offline_error"
    return "bootstrap.install_failed"


def install_failure_message(output: str) -> str:
    """Map pip output to a safe retry instruction without echoing its body."""

    code = _classify_install_failure(output)
    messages = {
        "bootstrap.proxy_error": "proxy gagal; periksa proxy lalu coba lagi.",
        "bootstrap.ssl_error": "sertifikat SSL gagal; periksa sertifikat jaringan lalu coba lagi.",
        "bootstrap.offline_error": "internet/package index tidak tersedia; hubungkan jaringan lalu coba lagi.",
        "bootstrap.install_failed": "dependensi runtime gagal dipasang; periksa Python lalu coba lagi.",
    }
    return messages[code]


def ensure_runtime(
    repo_root: str | os.PathLike[str],
    *,
    venv_dir: str | os.PathLike[str] | None = None,
    python_command: Sequence[str] | None = None,
    runner: CommandRunner = _run_command,
    emit: Callable[[str], None] = print,
) -> Path:
    """Create or repair the repo venv and return its healthy interpreter path."""

    root = Path(repo_root).resolve()
    requirements_path = root / RUNTIME_REQUIREMENTS
    if not requirements_path.is_file():
        raise BootstrapError(
            "bootstrap.requirements_missing",
            "requirements.txt tidak ditemukan di repository.",
        )
    requirements_bytes = requirements_path.read_bytes()
    venv = Path(venv_dir).resolve() if venv_dir else root / ".venv"
    python_path = _venv_python(venv)
    marker_path = venv / FINGERPRINT_FILENAME

    existing_version = None
    if python_path.is_file():
        existing_version = _read_python_version((str(python_path),), runner=runner, cwd=root)
    if _is_supported_version(existing_version):
        version_text = ".".join(str(part) for part in existing_version)
        expected = dependency_fingerprint(requirements_bytes, version_text)
        if _read_marker(marker_path) == expected and _runtime_healthy(python_path, root=root, runner=runner):
            emit("Lingkungan .venv siap; instalasi dilewati.")
            return python_path

    system_command = tuple(python_command or find_supported_python(runner=runner))
    system_version = _read_python_version(system_command, runner=runner, cwd=root)
    if not _is_supported_version(system_version):
        raise BootstrapError(
            "bootstrap.python_unsupported",
            "Python yang ditemukan bukan versi 3.11+; pilih Python 3.11+ lalu coba lagi.",
        )
    emit("Menyiapkan lingkungan Python lokal...")
    if not _is_supported_version(existing_version):
        try:
            venv_result = runner(
                (*system_command, "-m", "venv", str(venv)),
                cwd=root,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BootstrapError("bootstrap.venv_failed", "venv tidak dapat dibuat atau diperbaiki.") from exc
        if venv_result.returncode != 0:
            raise BootstrapError("bootstrap.venv_failed", "venv tidak dapat dibuat atau diperbaiki.")

        python_path = _venv_python(venv)
    else:
        emit("Lingkungan .venv ada; perbaikan dilakukan tanpa menimpa interpreter aktif.")

    if not python_path.is_file():
        raise BootstrapError("bootstrap.venv_failed", "interpreter .venv tidak tersedia setelah perbaikan.")
    emit("Memasang dependensi runtime dari requirements.txt; ini hanya perlu saat pertama/stale...")
    try:
        pip_result = runner(
            (
                str(python_path),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "-r",
                str(requirements_path),
            ),
            cwd=root,
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError("bootstrap.install_failed", "pip tidak dapat dijalankan; coba lagi.") from exc
    if pip_result.returncode != 0:
        code = _classify_install_failure(f"{pip_result.stdout}\n{pip_result.stderr}")
        raise BootstrapError(code, install_failure_message(f"{pip_result.stdout}\n{pip_result.stderr}"))

    final_version = _read_python_version((str(python_path),), runner=runner, cwd=root)
    if not _is_supported_version(final_version):
        raise BootstrapError("bootstrap.python_unsupported", "versi interpreter .venv tidak didukung.")
    if not _runtime_healthy(python_path, root=root, runner=runner):
        raise BootstrapError(
            "bootstrap.runtime_unhealthy",
            "dependensi belum lengkap; perbaiki jaringan/dependensi lalu coba lagi.",
        )
    version_text = ".".join(str(part) for part in final_version)
    _write_marker(marker_path, dependency_fingerprint(requirements_bytes, version_text))
    emit("Lingkungan runtime siap.")
    return python_path


def bootstrap_and_launch(
    repo_root: str | os.PathLike[str],
    *,
    runner: CommandRunner = _run_command,
    python_command: Sequence[str] | None = None,
    emit: Callable[[str], None] = print,
    launch: Callable[..., int] = _launch_operator,
) -> int:
    """Ensure dependencies and launch the existing operator entrypoint."""

    root = Path(repo_root).resolve()
    python_path = ensure_runtime(
        root,
        runner=runner,
        python_command=python_command,
        emit=emit,
    )
    entrypoint = root / OPERATOR_ENTRYPOINT
    if not entrypoint.is_file():
        raise BootstrapError("bootstrap.entrypoint_missing", "operator entrypoint tidak ditemukan.")
    return int(launch((str(python_path), str(entrypoint)), cwd=root))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    print("ManhwaShorts: menyiapkan operator console...")
    try:
        return bootstrap_and_launch(root)
    except BootstrapError as exc:
        print(f"Blocked safely: {exc}")
        return 1
    except KeyboardInterrupt:
        print("Dibatalkan dengan aman; venv dan checkpoint tidak dihapus.")
        return 130
    except Exception:
        print("Blocked safely: bootstrap gagal; periksa instalasi Python lalu coba lagi.")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "BootstrapError",
    "FINGERPRINT_FILENAME",
    "bootstrap_and_launch",
    "dependency_fingerprint",
    "ensure_runtime",
    "find_supported_python",
    "install_failure_message",
    "main",
    "python_candidates",
]
