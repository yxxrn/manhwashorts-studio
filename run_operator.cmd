@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%POWERSHELL_EXE%" set "POWERSHELL_EXE=powershell.exe"

if defined OPERATOR_CLI_LAUNCHER_PROBE (
  "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\operator_launcher.ps1" -ProbeOnly
) else (
  "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\operator_launcher.ps1"
)
set "EXIT_CODE=%ERRORLEVEL%"

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
