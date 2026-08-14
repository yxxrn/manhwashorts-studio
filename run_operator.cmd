@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "BOOTSTRAP_SCRIPT=%~dp0scripts\bootstrap_operator_cli.py"
set "PYTHON_EXE="
set "PYTHON_SELECTOR="

rem Prefer the repository venv; the bootstrap script repairs missing imports in place.
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
  goto :run_bootstrap
)

rem Discover a supported interpreter without hardcoded user paths or PATH changes.
where py >nul 2>&1
if not errorlevel 1 (
  py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_SELECTOR=-3.11"
    goto :run_bootstrap
  )
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_SELECTOR=-3"
    goto :run_bootstrap
  )
)

where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON_EXE=python"
    goto :run_bootstrap
  )
)

echo Python 3.11+ tidak ditemukan. Install Python 3.11+ dari python.org, lalu coba lagi.
set "EXIT_CODE=2"
goto :finish

:run_bootstrap
echo Menjalankan bootstrap operator; API key belum diminta.
if defined PYTHON_SELECTOR (
  "%PYTHON_EXE%" %PYTHON_SELECTOR% "%BOOTSTRAP_SCRIPT%"
) else (
  "%PYTHON_EXE%" "%BOOTSTRAP_SCRIPT%"
)
set "EXIT_CODE=%ERRORLEVEL%"

:finish
echo.
if "%EXIT_CODE%"=="0" (
  echo Operator console closed.
) else (
  echo Operator console stopped with code %EXIT_CODE%.
  echo No API key was printed or saved by the bootstrap launcher.
  echo Retry after fixing Python, package network, proxy, or SSL settings.
)
pause
endlocal & exit /b %EXIT_CODE%
