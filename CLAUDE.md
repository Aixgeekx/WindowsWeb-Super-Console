# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Windows Web Super Console is a single-file Python web server providing remote access to a Windows PC via browser. It monitors system status, enables file browsing, remote screenshot, and PowerShell terminal execution from mobile browsers over LAN.

## Architecture

**Single-file server**: `server.py` (~1800 lines) contains all backend logic, system info collection, and embedded HTML/CSS/JS frontend. The frontend is served as inline strings within the Python code.

### Key Components
- **System info collection**: Uses PowerShell subprocess calls (`run_ps()`) for CPU, memory, disk, GPU, and process stats
- **Optional dependency**: `psutil` for fast process listing (falls back gracefully if not installed)
- **CC-Web integration**: Built-in session management for a separate Node.js project (`ccweb`) on port 8002
- **Session system**: IP-based with 1-hour timeout, hardcoded password `999`

### Frontend Structure
The HTML/CSS/JS is embedded as multi-line strings in `server.py`. Four main tabs:
- **Status**: Real-time CPU, memory, disk, GPU monitoring
- **Files**: Directory browsing with file info
- **Tools**: Screenshot capture and PowerShell terminal
- **CC**: Web interface for CC-Web sessions (optional feature)

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
- `VERSION` - Current version number (e.g., "2.0.0") - used by build scripts and GitHub Actions
- `autostart.vbs` - Windows startup registration script (adds to Startup folder)
- `scripts/build_exe.ps1` - PyInstaller build script
- `scripts/install_keepalive.ps1` - Scheduled task installer for auto-restart
- `scripts/uninstall_keepalive.ps1` - Scheduled task uninstaller
- `.github/workflows/build.yml` - CI/CD for automatic EXE build on tag push

## Release Process

1. Update version in `VERSION` file
2. Commit changes
3. Push a `v{version}` tag to GitHub
4. GitHub Actions automatically builds and publishes the EXE

```bash
git tag v2.0.0
git push origin v2.0.0
```

## Security

- **Authentication**: Hardcoded password `999` for web access
- **Terminal safety**: PowerShell terminal blocks dangerous commands: `Remove-Item`, `rmdir`, `del`, `format`, `shutdown`, `Restart-Computer`
- **Path traversal**: File browsing is restricted to the server's base directory
