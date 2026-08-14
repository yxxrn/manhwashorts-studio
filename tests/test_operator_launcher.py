"""Windows launcher construction and structural invocation contracts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
CMD_PATH = REPO_ROOT / "run_operator.cmd"
POWERSHELL_PATH = REPO_ROOT / "scripts" / "operator_launcher.ps1"


def _powershell_executable() -> str:
    candidates = [
        shutil.which("powershell.exe"),
        str(
            Path(os.environ.get("SYSTEMROOT", r"C:\\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        ),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("Windows PowerShell is unavailable in this verification environment")


def _probe_launcher(repo_root: Path) -> dict[str, object]:
    completed = subprocess.run(
        [
            _powershell_executable(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(POWERSHELL_PATH),
            "-RepoRoot",
            str(repo_root),
            "-ProbeOnly",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout.strip())


def _probe_cmd(cmd_path: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["OPERATOR_CLI_LAUNCHER_PROBE"] = "1"
    return subprocess.run(
        ["cmd.exe", "/d", "/c", str(cmd_path)],
        cwd=cwd,
        input="\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )


def test_cmd_uses_structural_powershell_dispatch_not_combined_python_command():
    text = CMD_PATH.read_text(encoding="utf-8").lower()

    assert (
        '"%~dp0scripts\\operator_launcher.ps1"' in text
        and "-noprofile" in text
        and "-executionpolicy bypass" in text
        and "-file" in text
    )
    assert "python_exe" not in text
    assert "python_selector" not in text
    assert '"%python_exe%"' not in text
    assert 'py" "' not in text
    assert "%*" not in text


def test_actual_launcher_probe_keeps_executable_and_arguments_separate():
    payload = _probe_launcher(REPO_ROOT)

    assert payload["mode"] == "probe"
    assert isinstance(payload["executable"], str)
    assert '"' not in payload["executable"]
    assert isinstance(payload["arguments"], list)
    assert payload["bootstrap"] == str(REPO_ROOT / "scripts" / "bootstrap_operator_cli.py")
    assert payload["argv"] == [*payload["arguments"], payload["bootstrap"]]
    assert payload["argv"][-1].endswith("bootstrap_operator_cli.py")


def test_actual_launcher_probe_handles_repo_path_with_spaces(tmp_path):
    spaced_root = tmp_path / "operator console [safe]"
    (spaced_root / "scripts").mkdir(parents=True)
    (spaced_root / "scripts" / "bootstrap_operator_cli.py").write_text(
        "# probe-only fixture\n", encoding="utf-8"
    )

    payload = _probe_launcher(spaced_root)

    assert payload["bootstrap"] == str(spaced_root / "scripts" / "bootstrap_operator_cli.py")
    assert payload["argv"][-1] == payload["bootstrap"]
    assert all('"' not in str(part) for part in payload["argv"])


def test_actual_cmd_dispatch_handles_repository_path_without_malformed_python_join():
    completed = _probe_cmd(CMD_PATH, REPO_ROOT)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"mode":"probe"' in completed.stdout
    assert 'py" "' not in completed.stdout


def test_actual_cmd_dispatch_handles_path_with_spaces(tmp_path):
    spaced_root = tmp_path / "operator console [safe]"
    (spaced_root / "scripts").mkdir(parents=True)
    (spaced_root / "scripts" / "bootstrap_operator_cli.py").write_text(
        "# probe-only fixture\n", encoding="utf-8"
    )
    shutil.copy2(CMD_PATH, spaced_root / "run_operator.cmd")
    shutil.copy2(POWERSHELL_PATH, spaced_root / "scripts" / "operator_launcher.ps1")

    completed = _probe_cmd(spaced_root / "run_operator.cmd", spaced_root)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert '"mode":"probe"' in completed.stdout
    assert 'py" "' not in completed.stdout


def test_actual_cmd_normal_mode_reaches_mock_bootstrap_in_space_path(tmp_path):
    if shutil.which("python.exe") is None and shutil.which("python") is None:
        pytest.skip("Python is unavailable for the disposable launcher smoke")

    spaced_root = tmp_path / "operator console [normal smoke]"
    scripts = spaced_root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "bootstrap_operator_cli.py").write_text(
        "print('operator-bootstrap-sentinel')\n", encoding="utf-8"
    )
    shutil.copy2(CMD_PATH, spaced_root / "run_operator.cmd")
    shutil.copy2(POWERSHELL_PATH, scripts / "operator_launcher.ps1")

    environment = os.environ.copy()
    environment.pop("OPERATOR_CLI_LAUNCHER_PROBE", None)
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(spaced_root / "run_operator.cmd")],
        cwd=spaced_root,
        input="\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "operator-bootstrap-sentinel" in completed.stdout


def test_launcher_source_validates_py_fallback_and_never_derives_py_from_install_path():
    text = POWERSHELL_PATH.read_text(encoding="utf-8").lower()

    assert "get-command" in text
    assert "-3.11" in text
    assert "-3" in text
    assert "test-pythoncandidate" in text
    assert "python311\\py" not in text
    assert "invoke-expression" not in text
