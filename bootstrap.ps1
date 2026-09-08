[CmdletBinding()]
param(
    [switch]$WithoutSuwayomi,
    [switch]$WithoutPocketTTS,
    [switch]$SkipSystemPackages,
    [switch]$NoStartup,
    [string]$YouTubeAccount = "",
    [string]$YouTubeCookies = "",
    [string]$YouTubeLabel = "",
    [string]$ConfigPath = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}
function Have([string]$Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}
function Install-WingetPackage([string]$Id) {
    if (-not (Have "winget.exe")) {
        throw "winget is required. Install/update Microsoft App Installer, then rerun bootstrap.ps1."
    }
    Write-Output "Installing $Id ..."
    & winget install --id $Id -e --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "winget install failed: $Id" }
    Refresh-ProcessPath
}
function Test-Python311 {
    foreach ($candidate in @(@("py", "-3.11"), @("python", ""))) {
        if (-not (Have $candidate[0])) { continue }
        $args = @()
        if ($candidate[1]) { $args += $candidate[1] }
        $args += @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)")
        & $candidate[0] @args 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}
if (-not $SkipSystemPackages) {
    if ($null -eq (Test-Python311)) { Install-WingetPackage "Python.Python.3.11" }
    if (-not (Have "ffmpeg.exe")) { Install-WingetPackage "Gyan.FFmpeg" }
    if (-not (Have "tesseract.exe")) { Install-WingetPackage "UB-Mannheim.TesseractOCR" }
    if (-not (Have "git.exe")) { Install-WingetPackage "Git.Git" }
    $chromeCandidates = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
    if ($chromeCandidates.Count -eq 0) { Install-WingetPackage "Google.Chrome" }
}
$Python = Test-Python311
if ($null -eq $Python) { throw "Python 3.11+ is still unavailable after bootstrap." }
$PythonExe = $Python[0]
$PythonPrefix = @()
if ($Python[1]) { $PythonPrefix += $Python[1] }

Write-Output "Preparing ManhwaShorts Python runtime ..."
$EnsureCode = "from scripts.bootstrap_operator_cli import ensure_runtime; import sys; ensure_runtime(sys.argv[1])"
& $PythonExe @PythonPrefix -c $EnsureCode $Root
if ($LASTEXITCODE -ne 0) { throw "ManhwaShorts .venv bootstrap failed." }
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$EnvPath = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
    if (-not [string]::IsNullOrWhiteSpace($ConfigPath)) {
        if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw "Config file not found: $ConfigPath" }
        Copy-Item -LiteralPath $ConfigPath -Destination $EnvPath
    } else {
        Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination $EnvPath
    }
}
& $VenvPython (Join-Path $Root "scripts\migrate_legacy_env.py")
if ($LASTEXITCODE -ne 0) { throw "Legacy config migration failed." }

function Set-EnvValue([string]$Key, [string]$Value) {
    $code = "from dotenv import set_key; import sys; set_key(sys.argv[1], sys.argv[2], sys.argv[3], quote_mode='auto')"
    & $VenvPython -c $code $EnvPath $Key $Value
    if ($LASTEXITCODE -ne 0) { throw "Could not set $Key in .env" }
}
Set-EnvValue "MS_ENVIRONMENT" "production"
Set-EnvValue "MS_DEBUG" "false"
$Chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if ($Chrome) { Set-EnvValue "MS_YOUTUBE_BROWSER_EXECUTABLE" $Chrome }
$Ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
$Ffprobe = Get-Command ffprobe.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if ($Ffmpeg) { Set-EnvValue "MS_FFMPEG_BIN" $Ffmpeg }
if ($Ffprobe) { Set-EnvValue "MS_FFPROBE_BIN" $Ffprobe }
$Tesseract = Get-Command tesseract.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $Tesseract) {
    $candidate = Join-Path $env:ProgramFiles "Tesseract-OCR\tesseract.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { $Tesseract = $candidate }
}
if ($Tesseract) { Set-EnvValue "MS_TESSERACT_BIN" $Tesseract }
if (Have "espeak-ng.exe") { Set-EnvValue "MS_ESPEAK_BIN" "espeak-ng" }
elseif (Have "espeak.exe") { Set-EnvValue "MS_ESPEAK_BIN" "espeak" }

$RuntimeBase = Join-Path $env:LOCALAPPDATA "ManhwaShorts"
New-Item -ItemType Directory -Force -Path $RuntimeBase | Out-Null
$JavaExe = $null
if (-not $WithoutSuwayomi) {
    if (Have "java.exe") {
        try {
            $versionText = (& java -version 2>&1 | Select-Object -First 1 | Out-String)
            $match = [regex]::Match($versionText, '"(\d+)')
            if ($match.Success -and [int]$match.Groups[1].Value -ge 21) {
                $JavaExe = (Get-Command java.exe).Source
            }
        } catch {}
    }
    if (-not $JavaExe) {
        $JavaRoot = Join-Path $RuntimeBase "java21"
        $JavaExe = Get-ChildItem -Path $JavaRoot -Filter java.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\bin\\java\.exe$' } | Select-Object -First 1 -ExpandProperty FullName
        if (-not $JavaExe) {
            Write-Output "Downloading portable Java 21 runtime ..."
            $zip = Join-Path $env:TEMP "manhwashorts-java21.zip"
            $uri = "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse?project=jdk"
            Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $zip
            if (Test-Path $JavaRoot) { Remove-Item -Recurse -Force $JavaRoot }
            New-Item -ItemType Directory -Force -Path $JavaRoot | Out-Null
            Expand-Archive -LiteralPath $zip -DestinationPath $JavaRoot -Force
            Remove-Item -Force $zip
            $JavaExe = Get-ChildItem -Path $JavaRoot -Filter java.exe -Recurse |
                Where-Object { $_.FullName -match '\\bin\\java\.exe$' } | Select-Object -First 1 -ExpandProperty FullName
        }
    }
    if (-not $JavaExe) { throw "Java 21 runtime could not be prepared." }
    Set-EnvValue "MS_SUWAYOMI_ENABLED" "true"
    Set-EnvValue "MS_SUWAYOMI_AUTO_START" "true"
    Set-EnvValue "MS_SUWAYOMI_JAVA_BIN" $JavaExe
    & $VenvPython (Join-Path $Root "scripts\setup_suwayomi.py") --java-bin $JavaExe
    if ($LASTEXITCODE -ne 0) { throw "Suwayomi setup failed." }
    & $VenvPython (Join-Path $Root "scripts\setup_suwayomi_extensions.py")
    if ($LASTEXITCODE -ne 0) { throw "Suwayomi extension setup failed." }
} else {
    Set-EnvValue "MS_SUWAYOMI_ENABLED" "false"
    Set-EnvValue "MS_SUWAYOMI_AUTO_START" "false"
}
if (-not $WithoutPocketTTS) {
    $PocketRoot = Join-Path $RuntimeBase "pocket-tts"
    & $VenvPython (Join-Path $Root "scripts\setup_pocket_tts_runtime.py") --runtime-dir $PocketRoot
    if ($LASTEXITCODE -ne 0) { throw "Pocket TTS runtime setup failed." }
    Set-EnvValue "MS_TTS_LOCAL_FIRST" "true"
    Set-EnvValue "MS_TTS_POCKET_URL" "http://127.0.0.1:8790"
    Set-EnvValue "MS_TTS_POCKET_VOICE" "alba"
    $PocketModeFile = Join-Path $PocketRoot "mode.txt"
    $PocketMode = if (Test-Path -LiteralPath $PocketModeFile) { (Get-Content -LiteralPath $PocketModeFile -Raw).Trim().ToLowerInvariant() } else { "fp32" }
    Set-EnvValue "MS_TTS_POCKET_MODEL" "pocket-tts-3.1.0-$PocketMode"
    Set-EnvValue "MS_TTS_POCKET_PRODUCTION_SPEED" "0.90"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\start_pocket_tts_windows.ps1") -RuntimeDir $PocketRoot -Voice "alba" -Port 8790
    if ($LASTEXITCODE -ne 0) { throw "Pocket TTS service did not start." }
    if (-not $NoStartup) {
        $Startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
        $StartupCmd = Join-Path $Startup "ManhwaShorts-PocketTTS.cmd"
        $StartScript = Join-Path $Root "scripts\start_pocket_tts_windows.ps1"
        $line = '@echo off' + "`r`n" + 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $StartScript + '" -RuntimeDir "' + $PocketRoot + '" -Voice alba -Port 8790' + "`r`n"
        Set-Content -LiteralPath $StartupCmd -Value $line -Encoding ASCII
    }
} else {
    Set-EnvValue "MS_TTS_LOCAL_FIRST" "false"
}

if (-not $NoStartup) {
    $Startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
    $ServerStartup = Join-Path $Startup "ManhwaShorts-Server.cmd"
    $ServerScript = Join-Path $Root "scripts\start_manhwashorts_windows.ps1"
    $serverLine = '@echo off' + "`r`n" + 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $ServerScript + '" -RepoRoot "' + $Root + '"' + "`r`n"
    Set-Content -LiteralPath $ServerStartup -Value $serverLine -Encoding ASCII
}

Write-Output "Migrating database ..."
& (Join-Path $Root ".venv\Scripts\alembic.exe") upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }

if ($YouTubeAccount -or $YouTubeCookies) {
    if (-not $YouTubeAccount -or -not $YouTubeCookies) { throw "-YouTubeAccount and -YouTubeCookies must be provided together." }
    if (-not (Test-Path -LiteralPath $YouTubeCookies -PathType Leaf)) { throw "YouTube cookies file not found: $YouTubeCookies" }
    Write-Output "Bootstrapping YouTube Studio account from cookies.txt ..."
    & $VenvPython (Join-Path $Root "scripts\youtube_browser_account.py") ensure $YouTubeAccount $YouTubeLabel
    if ($LASTEXITCODE -ne 0) { throw "YouTube account profile setup failed." }
    & $VenvPython (Join-Path $Root "scripts\youtube_browser_account.py") default $YouTubeAccount
    & $VenvPython (Join-Path $Root "scripts\youtube_browser_account.py") import-cookies $YouTubeAccount $YouTubeCookies
    if ($LASTEXITCODE -ne 0) { throw "YouTube cookie import failed." }
}

Write-Output "Running machine doctor ..."
& $VenvPython (Join-Path $Root "scripts\doctor.py")
if ($LASTEXITCODE -ne 0) { throw "Machine doctor found required failures." }
Write-Output ""
Write-Output "ManhwaShorts is ready for production."
Write-Output "Config: $EnvPath"
Write-Output "Manual server: .\.venv\Scripts\python.exe scripts\serve.py"
Write-Output "Production run: .\.venv\Scripts\python.exe scripts\production_run.py ..."
