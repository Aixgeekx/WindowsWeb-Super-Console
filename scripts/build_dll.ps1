param([string]$Output = "sysinfo.dll")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$src = Join-Path $PSScriptRoot "sysinfo.cpp"
$out = Join-Path $repo $Output

# Try MSVC first via VS DevShell
$devShell = Get-ChildItem "C:\Program Files\Microsoft Visual Studio\*\*\Common7\Tools\Microsoft.VisualStudio.DevShell.dll" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($devShell) {
    Write-Host "Building with MSVC..."
    Import-Module $devShell.FullName -ErrorAction Stop
    $vsInstallPath = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $devShell.FullName))
    Enter-VsDevShell -VsInstallPath $vsInstallPath -DevCmdArguments '-arch=x64' -ErrorAction Stop
    cl /LD /O2 /EHsc "$src" /Fe:"$out" /link /DEBUG:NONE /PDB:NONE /INCREMENTAL:NO /EXPORT:get_system_info /EXPORT:take_screenshot psapi.lib user32.lib gdi32.lib
    Write-Host "Built: $out"
    exit 0
}

# Try MSVC via vcvarsall.bat
$vcvars = Get-ChildItem "C:\Program Files\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvarsall.bat" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($vcvars) {
    Write-Host "Building with MSVC (vcvarsall)..."
    # Find MSVC bin directory
    $msvcRoot = Join-Path (Split-Path (Split-Path (Split-Path $vcvars.FullName))) "Tools\MSVC"
    $msvcVer = Get-ChildItem $msvcRoot -Directory | Sort-Object Name -Descending | Select-Object -First 1
    $msvcBin = Join-Path $msvcVer.FullName "bin\Hostx64\x64"
    $env:PATH = "$msvcBin;$env:PATH"
    cmd /c "`"$($vcvars.FullName)`" x64 && cl /LD /O2 /EHsc `"$src`" /Fe:`"$out`" /link /DEBUG:NONE /PDB:NONE /INCREMENTAL:NO /EXPORT:get_system_info /EXPORT:take_screenshot psapi.lib user32.lib gdi32.lib"
    Write-Host "Built: $out"
    exit 0
}

# Try MinGW
$gpp = Get-Command g++ -ErrorAction SilentlyContinue
if ($gpp) {
    Write-Host "Building with MinGW..."
    g++ -shared -O2 -static -o "$out" "$src" -lpsapi -luser32 -lgdi32
    Write-Host "Built: $out"
    exit 0
}

Write-Error "Neither MSVC nor MinGW found. Install Visual Studio Build Tools or MinGW-w64."
exit 1
