[CmdletBinding()]
param([string]$RepoRoot = "")
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Serve = Join-Path $RepoRoot "scripts\serve.py"
$PortText = (& $Python -c "from app.config import settings; print(settings.port)" 2>$null | Out-String).Trim()
$Port = if ($PortText -match "^\d+$") { [int]$PortText } else { 8000 }
$Health = "http://127.0.0.1:$Port/api/health"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "ManhwaShorts runtime missing: $Python" }
try { Invoke-WebRequest -UseBasicParsing -Uri $Health -TimeoutSec 2 | Out-Null; exit 0 } catch {}
$LogDir = Join-Path $env:LOCALAPPDATA "ManhwaShorts\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$ServeArg = '"' + $Serve + '"'
Start-Process -FilePath $Python -ArgumentList @($ServeArg) -WorkingDirectory $RepoRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir "server.out.log") -RedirectStandardError (Join-Path $LogDir "server.err.log") | Out-Null
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    try { Invoke-WebRequest -UseBasicParsing -Uri $Health -TimeoutSec 2 | Out-Null; Write-Output "ManhwaShorts server ready on port $Port"; exit 0 } catch {}
}
throw "ManhwaShorts server did not become healthy"
