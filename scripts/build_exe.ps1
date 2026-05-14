param(
    [string]$Name = "WindowsWeb-Super-Console"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$versionPath = Join-Path $repo "VERSION"
$version = if (Test-Path $versionPath) { (Get-Content $versionPath -Raw).Trim() } else { "dev" }

Set-Location $repo
python -m pip install --upgrade pip pyinstaller
$dllFlag = ""
if (Test-Path (Join-Path $repo "sysinfo.dll")) {
    $dllFlag = "--add-data `"sysinfo.dll;.`""
}
pyinstaller --noconfirm --clean --onefile $dllFlag --name "$Name-v$version" server.py

Write-Host "Built: $(Join-Path $repo "dist\$Name-v$version.exe")"
