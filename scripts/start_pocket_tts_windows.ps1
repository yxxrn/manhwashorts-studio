[CmdletBinding()]
param(
    [string]$RuntimeDir = "",
    [string]$Voice = "alba",
    [int]$Port = 8790
)
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($RuntimeDir)) {
    $RuntimeDir = Join-Path $env:LOCALAPPDATA "ManhwaShorts\pocket-tts"
}
$PocketExe = Join-Path $RuntimeDir ".venv\Scripts\pocket-tts.exe"
if (-not (Test-Path -LiteralPath $PocketExe -PathType Leaf)) {
    throw "Pocket TTS runtime not found: $PocketExe"
}
$Health = "http://127.0.0.1:$Port/openapi.json"
function Test-PocketHealth {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Health -TimeoutSec 2 | Out-Null
        return $true
    } catch { return $false }
}
if (Test-PocketHealth) { exit 0 }
$LogDir = Join-Path $env:LOCALAPPDATA "ManhwaShorts\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stdout = Join-Path $LogDir "pocket-tts.out.log"
$Stderr = Join-Path $LogDir "pocket-tts.err.log"
$ModeFile = Join-Path $RuntimeDir "mode.txt"
$Mode = if (Test-Path -LiteralPath $ModeFile) {
    (Get-Content -LiteralPath $ModeFile -Raw).Trim().ToLowerInvariant()
} else { "fp32" }
function Start-PocketProcess([bool]$Quantize) {
    $Arguments = @(
        "serve", "--host", "127.0.0.1", "--port", "$Port",
        "--language", "english", "--default-voice", $Voice
    )
    if ($Quantize) { $Arguments += "--quantize" }
    return Start-Process -FilePath $PocketExe -ArgumentList $Arguments `
        -WindowStyle Hidden -RedirectStandardOutput $Stdout `
        -RedirectStandardError $Stderr -PassThru
}
function Wait-Pocket([System.Diagnostics.Process]$Process, [int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        Start-Sleep -Seconds 1
        if (Test-PocketHealth) { return $true }
        if ($Process.HasExited) { break }
    }
    return $false
}
$UseQuantized = $Mode -eq "int8"
$Process = Start-PocketProcess $UseQuantized
if (Wait-Pocket $Process 60) {
    Write-Output "Pocket TTS ready on localhost:$Port with voice=$Voice mode=$Mode"
    exit 0
}
if ($UseQuantized) {
    if (-not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
    $Mode = "fp32"
    Set-Content -LiteralPath $ModeFile -Value "fp32" -Encoding ASCII
    Write-Output "Pocket TTS INT8 startup failed; retrying local FP32 mode."
    $Process = Start-PocketProcess $false
    if (Wait-Pocket $Process 60) {
        Write-Output "Pocket TTS ready on localhost:$Port with voice=$Voice mode=fp32"
        exit 0
    }
}
if (-not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
}
throw "Pocket TTS did not become healthy; see $Stderr"
