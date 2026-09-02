"""TDD contracts for the one-click operator runtime bootstrap."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest


def _bootstrap_module():
    return importlib.import_module("scripts.bootstrap_operator_cli")


class FakeRunner:
    def __init__(self, *, health_failures: int = 0, pip_returncode: int = 0, pip_stderr: str = ""):
        self.calls: list[tuple[str, ...]] = []
        self.health_failures = health_failures
        self.pip_returncode = pip_returncode
        self.pip_stderr = pip_stderr

    def __call__(self, argv, **_kwargs):
        command = tuple(str(part) for part in argv)
        self.calls.append(command)
        if "-m" in command and "venv" in command:
            target = Path(command[-1])
            (target / "Scripts").mkdir(parents=True, exist_ok=True)
            (target / "Scripts" / "python.exe").write_bytes(b"fake-python")
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-m" in command and "pip" in command:
            return subprocess.CompletedProcess(command, self.pip_returncode, "", self.pip_stderr)
        if "-c" in command:
            source = command[-1]
            if "version_info" in source:
                return subprocess.CompletedProcess(command, 0, "3.11.8\n", "")
            if "sqlalchemy" in source:
                if self.health_failures:
                    self.health_failures -= 1
                    return subprocess.CompletedProcess(command, 1, "", "missing sqlalchemy")
                return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, "", "")


def _requirements(root: Path) -> Path:
    path = root / "requirements.txt"
    path.write_bytes(b"SQLAlchemy==2.0.51\nPillow==12.2.0\n")
    return path


def test_bootstrap_module_and_launcher_contract_are_present():
    bootstrap = _bootstrap_module()
    assert callable(bootstrap.ensure_runtime)
    assert callable(bootstrap.find_supported_python)
    assert callable(bootstrap.dependency_fingerprint)
    root = Path(__file__).parents[2]
    launcher = root / "run_operator.cmd"
    powershell_launcher = root / "scripts" / "operator_launcher.ps1"
    launcher_text = launcher.read_text(encoding="utf-8").lower()
    powershell_text = powershell_launcher.read_text(encoding="utf-8").lower()
    assert '"%~dp0scripts\\operator_launcher.ps1"' in launcher_text
    assert "scripts\\bootstrap_operator_cli.py" in powershell_text
    assert ".venv\\scripts\\python.exe" in powershell_text
    assert "-3.11" in powershell_text
    assert "-3" in powershell_text
    assert "python" in powershell_text
    assert "requirements-dev.txt" not in launcher_text + powershell_text
    assert "invoke-expression" not in launcher_text + powershell_text
    assert "%*" not in launcher_text


def test_interpreter_candidates_are_ordered_and_reject_unsupported_python():
    bootstrap = _bootstrap_module()
    assert bootstrap.python_candidates() == (("py", "-3.11"), ("py", "-3"), ("python3",), ("python",))

    calls = []

    def runner(argv, **_kwargs):
        calls.append(tuple(argv))
        version = "3.10.12\n" if len(calls) == 1 else "3.11.8\n"
        return subprocess.CompletedProcess(argv, 0, version, "")

    found = bootstrap.find_supported_python(
        runner=runner,
        which=lambda executable: executable,
    )

    assert found == ("py", "-3")
    assert calls[0][:2] == ("py", "-3.11")
    assert calls[1][:2] == ("py", "-3")


def test_missing_venv_is_created_installed_and_marked(tmp_path):
    bootstrap = _bootstrap_module()
    _requirements(tmp_path)
    runner = FakeRunner()
    messages = []

    result = bootstrap.ensure_runtime(
        tmp_path,
        python_command=("python",),
        runner=runner,
        emit=messages.append,
    )

    assert result == tmp_path / ".venv" / "Scripts" / "python.exe"
    assert any("-m" in call and "venv" in call for call in runner.calls)
    assert any("-m" in call and "pip" in call for call in runner.calls)
    assert (tmp_path / ".venv" / bootstrap.FINGERPRINT_FILENAME).is_file()
    assert any("memasang" in message.casefold() for message in messages)


def test_healthy_venv_with_current_fingerprint_skips_install(tmp_path):
    bootstrap = _bootstrap_module()
    requirements = _requirements(tmp_path)
    venv = tmp_path / ".venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake-python")
    marker = venv / bootstrap.FINGERPRINT_FILENAME
    marker.write_text(
        bootstrap.dependency_fingerprint(requirements.read_bytes(), "3.11.8") + "\n",
        encoding="ascii",
    )
    runner = FakeRunner()

    result = bootstrap.ensure_runtime(
        tmp_path,
        python_command=("python",),
        runner=runner,
    )

    assert result == python
    assert not any("-m" in call and "pip" in call for call in runner.calls)


def test_stale_fingerprint_repairs_existing_venv(tmp_path):
    bootstrap = _bootstrap_module()
    _requirements(tmp_path)
    venv = tmp_path / ".venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake-python")
    (venv / bootstrap.FINGERPRINT_FILENAME).write_text("stale\n", encoding="ascii")
    runner = FakeRunner()

    bootstrap.ensure_runtime(tmp_path, python_command=("python",), runner=runner)

    assert any("-m" in call and "pip" in call for call in runner.calls)
    assert (venv / bootstrap.FINGERPRINT_FILENAME).read_text(encoding="ascii").strip() != "stale"


def test_supported_existing_venv_repairs_in_place_without_reinvoking_venv(tmp_path):
    bootstrap = _bootstrap_module()
    _requirements(tmp_path)
    venv = tmp_path / ".venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake-python")
    (venv / bootstrap.FINGERPRINT_FILENAME).write_text("stale\n", encoding="ascii")
    runner = FakeRunner()

    result = bootstrap.ensure_runtime(tmp_path, python_command=("python",), runner=runner)

    assert result == python
    assert any("-m" in call and "pip" in call for call in runner.calls)
    assert not any("-m" in call and "venv" in call for call in runner.calls)


def test_missing_runtime_import_repairs_even_with_current_fingerprint(tmp_path):
    bootstrap = _bootstrap_module()
    requirements = _requirements(tmp_path)
    venv = tmp_path / ".venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fake-python")
    (venv / bootstrap.FINGERPRINT_FILENAME).write_text(
        bootstrap.dependency_fingerprint(requirements.read_bytes(), "3.11.8") + "\n",
        encoding="ascii",
    )
    runner = FakeRunner(health_failures=1)

    bootstrap.ensure_runtime(tmp_path, python_command=("python",), runner=runner)

    assert any("-m" in call and "pip" in call for call in runner.calls)


@pytest.mark.parametrize(
    ("pip_stderr", "expected_code"),
    [
        ("Could not connect to proxy: 407", "bootstrap.proxy_error"),
        ("SSL certificate verify failed", "bootstrap.ssl_error"),
        ("network is unreachable", "bootstrap.offline_error"),
        ("Could not build wheels for package", "bootstrap.install_failed"),
    ],
)
def test_pip_failure_is_classified_retryably_and_does_not_write_success_marker(
    tmp_path, pip_stderr, expected_code
):
    bootstrap = _bootstrap_module()
    _requirements(tmp_path)
    runner = FakeRunner(pip_returncode=1, pip_stderr=pip_stderr)

    with pytest.raises(bootstrap.BootstrapError, match=expected_code):
        bootstrap.ensure_runtime(tmp_path, python_command=("python",), runner=runner)

    assert not (tmp_path / ".venv" / bootstrap.FINGERPRINT_FILENAME).exists()


def test_fingerprint_is_deterministic_and_changes_with_requirements_or_python():
    bootstrap = _bootstrap_module()
    first = bootstrap.dependency_fingerprint(b"requirements", "3.11.8")
    assert first == bootstrap.dependency_fingerprint(b"requirements", "3.11.8")
    assert first != bootstrap.dependency_fingerprint(b"requirements-changed", "3.11.8")
    assert first != bootstrap.dependency_fingerprint(b"requirements", "3.12.0")


def test_first_run_bootstrap_launches_quoted_path_without_shell(tmp_path):
    bootstrap = _bootstrap_module()
    root = tmp_path / "operator console"
    root.mkdir()
    _requirements(root)
    entrypoint = root / "scripts" / "run_operator_cli.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("# disposable test entrypoint\n", encoding="utf-8")
    runner = FakeRunner()
    launched = []

    result = bootstrap.bootstrap_and_launch(
        root,
        runner=runner,
        python_command=("python",),
        launch=lambda argv, cwd: launched.append((tuple(argv), cwd)) or 0,
    )

    assert result == 0
    assert launched
    assert launched[0][0][0].endswith("python.exe")
    assert str(root) in str(launched[0][1])
    assert all(";" not in part and "|" not in part for part in launched[0][0])


def test_install_failure_messages_never_echo_provider_or_secret_text():
    bootstrap = _bootstrap_module()
    secret = "sk-bootstrap-test-secret-123456"
    message = bootstrap.install_failure_message(
        f"HTTP proxy rejected Authorization Bearer {secret}; body={secret}"
    )
    assert secret not in message
    assert "Bearer" not in message
    assert "body" not in message
