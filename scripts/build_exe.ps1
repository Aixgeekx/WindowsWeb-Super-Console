param(
    [string]$Name = "WindowsWeb-Super-Console"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$versionPath = Join-Path $repo "VERSION"
$version = if (Test-Path $versionPath) { (Get-Content $versionPath -Raw).Trim() } else { "dev" }

Set-Location $repo
python -m pip install --upgrade pip pyinstaller
pyinstaller --noconfirm --clean --onefile --name "$Name-v$version" server.py

Write-Host "Built: $(Join-Path $repo "dist\$Name-v$version.exe")"
