[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [switch]$ProbeOnly
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
$BootstrapScript = Join-Path $RepoRoot "scripts\bootstrap_operator_cli.py"
$VersionCode = 'import sys; print(sys.version_info[0].__str__() + chr(46) + sys.version_info[1].__str__() + chr(46) + sys.version_info[2].__str__())'

function New-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [AllowEmptyCollection()][string[]]$Arguments = @()
    )

    [pscustomobject]@{
        Executable = $Executable
        Arguments = @($Arguments)
    }
}

function Get-ApplicationPath {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }

    $path = [string]$command.Source
    if ([string]::IsNullOrWhiteSpace($path)) {
        $path = [string]$command.Path
    }
    if ([string]::IsNullOrWhiteSpace($path)) {
        return $null
    }
    return $path
}

function Test-PythonCandidate {
    param([Parameter(Mandatory = $true)][pscustomobject]$Candidate)

    try {
        $probeArguments = @($Candidate.Arguments) + @("-c", $VersionCode)
        $output = & $Candidate.Executable @probeArguments 2>$null
        $exitCode = $LASTEXITCODE
    }
    catch {
        return $false
    }

    if ($exitCode -ne 0) {
        return $false
    }

    $versionText = ($output | Out-String).Trim()
    $match = [regex]::Match($versionText, "^(\d+)\.(\d+)\.(\d+)$")
    if (-not $match.Success) {
        return $false
    }

    $major = [int]$match.Groups[1].Value
    $minor = [int]$match.Groups[2].Value
    return (($major -gt 3) -or (($major -eq 3) -and ($minor -ge 11)))
}

function Select-PythonCandidate {
    $candidates = New-Object System.Collections.Generic.List[object]
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        $candidates.Add((New-PythonCandidate -Executable $venvPython -Arguments @()))
    }

    $seen = @{}
    foreach ($name in @("py.exe", "py")) {
        $path = Get-ApplicationPath -Name $name
        if (-not [string]::IsNullOrWhiteSpace($path)) {
            $key = $path.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $candidates.Add((New-PythonCandidate -Executable $path -Arguments @("-3.11")))
                $candidates.Add((New-PythonCandidate -Executable $path -Arguments @("-3")))
            }
        }
    }

    foreach ($name in @("python.exe", "python")) {
        $path = Get-ApplicationPath -Name $name
        if (-not [string]::IsNullOrWhiteSpace($path)) {
            $key = $path.ToLowerInvariant()
            if (-not $seen.ContainsKey($key)) {
                $seen[$key] = $true
                $candidates.Add((New-PythonCandidate -Executable $path -Arguments @()))
            }
        }
    }

    foreach ($candidate in $candidates) {
        if (Test-PythonCandidate -Candidate $candidate) {
            return $candidate
        }
    }
    return $null
}

$selected = Select-PythonCandidate
if ($null -eq $selected) {
    Write-Output "Python 3.11+ tidak ditemukan atau launcher Python Windows tidak sehat."
    Write-Output "Install Python 3.11+ dari python.org, lalu coba lagi."
    exit 2
}

if ($ProbeOnly) {
    $probe = [ordered]@{
        mode = "probe"
        executable = $selected.Executable
        arguments = @($selected.Arguments)
        bootstrap = $BootstrapScript
        argv = @($selected.Arguments) + @($BootstrapScript)
    }
    $probe | ConvertTo-Json -Compress
    exit 0
}

Write-Output "Menjalankan bootstrap operator; API key belum diminta."
$bootstrapArguments = @($selected.Arguments) + @($BootstrapScript)
try {
    & $selected.Executable @bootstrapArguments
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
}
catch {
    Write-Output "operator.startup_failed: Python launcher tidak dapat dijalankan."
    $exitCode = 101
}
exit ([int]$exitCode)
