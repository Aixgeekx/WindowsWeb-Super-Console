param(
    [string]$ServerPath,
    [int]$Port = 9999,
    [int]$IntervalSeconds = 15
)

$ErrorActionPreference = "SilentlyContinue"

if (-not $ServerPath) {
    $repo = Split-Path -Parent $PSScriptRoot
    $ServerPath = Join-Path $repo "server.py"
} else {
    $repo = Split-Path -Parent $ServerPath
}

function Get-PythonPath {
    $python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $python) { $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source }
    return $python
}

function Test-ServerProcess {
    $serverName = [System.IO.Path]::GetFileName($ServerPath)
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    foreach ($p in $procs) {
        if ($p.CommandLine -and $p.CommandLine -like "*$serverName*") {
            return $true
        }
    }
    return $false
}

function Test-PortBusy {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

while ($true) {
    if (-not (Test-ServerProcess)) {
        if (-not (Test-PortBusy)) {
            $python = Get-PythonPath
            if ($python -and (Test-Path $ServerPath)) {
                Start-Process -FilePath $python -ArgumentList @("`"$ServerPath`"", "$Port") -WorkingDirectory $repo -WindowStyle Hidden
            }
        }
    }
    Start-Sleep -Seconds $IntervalSeconds
}
