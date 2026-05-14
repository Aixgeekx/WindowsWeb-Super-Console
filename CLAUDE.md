# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Windows Web Super Console is a single-file Python web server that provides remote access to a Windows PC via browser. It monitors system status (CPU, memory, disk, GPU), enables file browsing, remote screenshot, and PowerShell terminal execution from a mobile browser over LAN.

## Architecture

- **Single-file server**: `server.py` contains all backend logic, system info collection, and embedded HTML/CSS/JS frontend
- **System info collection**: Uses PowerShell subprocess calls for hardware stats (CPU, memory, disk, GPU, processes)
- **Optional dependency**: `psutil` for fast process listing (falls back gracefully if not installed)
- **CC-Web integration**: Optional integration with a separate Node.js project (`ccweb`) running on port 8002

## Development Commands

```bash
# Run the server (default port 9999)
python server.py

# Run on custom port
python server.py 8080

# Build standalone EXE (requires PyInstaller)
powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1

# Auto-commit and push to GitHub
python update.py "commit message"
```

## Key Files

- `server.py` - Main server with all backend and frontend code
- `VERSION` - Current version number (e.g., "1.9.3")
- `autostart.vbs` - Windows startup registration script
- `scripts/build_exe.ps1` - PyInstaller build script
- `scripts/install_keepalive.ps1` - Scheduled task installer for auto-restart
- `scripts/uninstall_keepalive.ps1` - Scheduled task uninstaller

## Release Process

1. Update version in `VERSION` file
2. Push a `v{version}` tag to GitHub
3. GitHub Actions automatically builds and publishes the EXE

## Authentication

The server has a hardcoded password (`999`) for web access. Session management is IP-based with 1-hour timeout.

## Terminal Security

The PowerShell terminal blocks dangerous commands: `Remove-Item`, `rmdir`, `del`, `format`, `shutdown`, `Restart-Computer`.
