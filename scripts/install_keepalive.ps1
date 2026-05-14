param(
    [int]$Port = 9999,
    [string]$TaskName = "WindowsWebSuperConsole"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$server = Join-Path $repo "server.py"
$watcher = Join-Path $PSScriptRoot "keepalive.ps1"

if (-not (Test-Path $server)) {
    throw "server.py not found: $server"
}
if (-not (Test-Path $watcher)) {
    throw "keepalive.ps1 not found: $watcher"
}

$powershell = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
if (-not $powershell) { throw "powershell.exe not found in PATH" }

$args = "-NoProfile -ExecutionPolicy Bypass -File `"$watcher`" -ServerPath `"$server`" -Port $Port"
$action = New-ScheduledTaskAction -Execute $powershell -Argument $args -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Keep Windows Web Super Console running on login and restart it if it exits." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Server: $server"
Write-Host "Watcher: $watcher"
Write-Host "Port: $Port"
