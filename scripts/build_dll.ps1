param([string]$Output = "sysinfo.dll")
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$src = Join-Path $PSScriptRoot "sysinfo.cpp"
$out = Join-Path $repo $Output

# Try MSVC first
$vcvars = Get-ChildItem "C:\Program Files\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvarsall.bat" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($vcvars) {
    Write-Host "Building with MSVC..."
    cmd /c "`"$($vcvars.FullName)`" x64 && cl /LD /O2 /EHsc `"$src`" /Fe:`"$out`" /link /DEBUG:NONE /EXPORT:get_system_info /EXPORT:take_screenshot psapi.lib user32.lib gdi32.lib ole32.lib gdiplus.lib"
    Write-Host "Built: $out"
    exit 0
}

# Try MinGW
$gpp = Get-Command g++ -ErrorAction SilentlyContinue
if ($gpp) {
    Write-Host "Building with MinGW..."
    g++ -shared -O2 -static -o "$out" "$src" -lpsapi -luser32 -lgdi32 -lole32 -lgdiplus
    Write-Host "Built: $out"
    exit 0
}

Write-Error "Neither MSVC nor MinGW found. Install Visual Studio Build Tools or MinGW-w64."
exit 1
