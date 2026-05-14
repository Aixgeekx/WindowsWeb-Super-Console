#!/usr/bin/env python3
"""
内网电脑状态监控
用法: python server.py [端口号，默认9999]
"""

import subprocess
import socket
import os
import html
import time
import json
import mimetypes
import shutil
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote
import sys
try:
    import psutil
    HAS_PSUTIL = True
except:
    HAS_PSUTIL = False

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
PASSWORD = "999"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CC_SESSIONS_DIR = os.path.join(BASE_DIR, "ccweb_sessions")
CC_AGENT_TIMEOUT = 180

# ─── 系统信息采集 ───

def run_ps(cmd):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace"
        )
        return r.stdout.strip()
    except Exception as e:
        return f"Error: {e}"

def get_all_system_info():
    """一次 PowerShell 获取全部系统信息，避免多次启动进程"""
    out = run_ps('''
        $cpu = [math]::Round((Get-Counter "\\Processor(_Total)\\% Processor Time").CounterSamples.CookedValue, 1)
        $os = Get-CimInstance Win32_OperatingSystem
        $memTotal = [math]::Round($os.TotalVisibleMemorySize/1MB, 2)
        $memFree = [math]::Round($os.FreePhysicalMemory/1MB, 2)
        $memUsed = [math]::Round($memTotal - $memFree, 2)
        $memPct = [math]::Round($memUsed / $memTotal * 100, 1)
        $uptime = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
        $uptimeStr = [math]::Round(((Get-Date) - $uptime).TotalHours, 0)
        $disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
            $t = [math]::Round($_.Size/1GB, 1)
            $f = [math]::Round($_.FreeSpace/1GB, 1)
            $u = [math]::Round($t - $f, 1)
            $p = if($t -gt 0){[math]::Round($u/$t*100,1)}else{0}
            "$($_.DeviceID)|$u|$t|$f|$p"
        }
        $procCount = (Get-Process).Count
        $topProcs = Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 Name,CPU,WorkingSet64 | ForEach-Object {
            "$($_.Name)|$([math]::Round($_.CPU,1))|$([math]::Round($_.WorkingSet64/1MB,1))"
        }
        Write-Output "CPU:$cpu"
        Write-Output "MEM:$memUsed|$memTotal|$memFree|$memPct"
        Write-Output "UPTIME:$uptimeStr"
        Write-Output "PROCS:$procCount"
        foreach($d in $disks){Write-Output "DISK:$d"}
        foreach($p in $topProcs){Write-Output "TOP:$p"}
    ''')
    info = {"cpu":0,"mem":{"used":0,"total":0,"free":0,"percent":0},"disks":[],"uptime":"N/A","procs":0,"top":[]}
    for line in out.split("\n"):
        line = line.strip()
        if line.startswith("CPU:"):
            try: info["cpu"] = float(line[4:])
            except: pass
        elif line.startswith("MEM:"):
            try:
                p = line[4:].split("|")
                info["mem"] = {"used":float(p[0]),"total":float(p[1]),"free":float(p[2]),"percent":float(p[3])}
            except: pass
        elif line.startswith("UPTIME:"):
            try: info["uptime"] = f"0d {int(float(line[7:]))}h"
            except: info["uptime"] = line[7:]
        elif line.startswith("PROCS:"):
            try: info["procs"] = int(line[6:])
            except: pass
        elif line.startswith("DISK:"):
            try:
                p = line[5:].split("|")
                if len(p)==5: info["disks"].append({"drive":p[0],"used":float(p[1]),"total":float(p[2]),"free":float(p[3]),"percent":float(p[4])})
            except: pass
        elif line.startswith("TOP:"):
            try:
                p = line[4:].split("|")
                if len(p)==3: info["top"].append({"name":p[0],"cpu":float(p[1]),"mem_mb":float(p[2])})
            except: pass
    return info

def get_uptime():
    out = run_ps("""
        $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
        $span = (Get-Date) - $boot
        $d = [int]$span.TotalDays
        $h = $span.Hours
        $m = $span.Minutes
        "${d}d ${h}h ${m}m"
    """)
    return out if out else "N/A"

def get_process_count():
    out = run_ps("(Get-Process).Count")
    try: return int(out)
    except: return 0

def get_network_info():
    hostname = socket.gethostname()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except:
        ip = "127.0.0.1"
    return {"hostname": hostname, "ip": ip}

def get_top_processes(n=8):
    ps = (
        'Get-Process | Sort-Object CPU -Descending | Select-Object -First %d Name, CPU, WorkingSet64 |'
        ' ForEach-Object {'
        ' $mem = [math]::Round($_.WorkingSet64/1MB, 1);'
        ' "$($_.Name)|$([math]::Round($_.CPU,1))|$mem" }'
    ) % n
    out = run_ps(ps)
    procs = []
    for line in out.split("\n"):
        line = line.strip()
        if "|" in line:
            parts = line.split("|")
            if len(parts) == 3:
                procs.append({"name": parts[0], "cpu": float(parts[1]) if parts[1] else 0, "mem_mb": float(parts[2]) if parts[2] else 0})
    return procs

def get_gpu_info():
    return run_ps("(Get-CimInstance Win32_VideoController | Where-Object Name -notmatch 'Oray|Remote|Virtual|Display|Basic' | Select-Object -First 1).Name") or "N/A"

# ─── CC-Web 内置会话 ───

def cc_now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def cc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def cc_session_path(session_id):
    safe = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in "-_")
    return os.path.join(CC_SESSIONS_DIR, safe + ".json")

def cc_load_session(session_id):
    try:
        with open(cc_session_path(session_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def cc_save_session(session):
    os.makedirs(CC_SESSIONS_DIR, exist_ok=True)
    with open(cc_session_path(session["id"]), "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)

def cc_public_session(session):
    return {
        "id": session["id"],
        "title": session.get("title") or "新会话",
        "agent": session.get("agent", "codex"),
        "mode": session.get("mode", "yolo"),
        "cwd": session.get("cwd") or "",
        "updated": session.get("updated") or "",
        "messages": session.get("messages", []),
    }

def cc_list_sessions():
    os.makedirs(CC_SESSIONS_DIR, exist_ok=True)
    sessions = []
    for name in os.listdir(CC_SESSIONS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(CC_SESSIONS_DIR, name), "r", encoding="utf-8") as f:
                sessions.append(json.load(f))
        except:
            continue
    sessions.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return [cc_public_session(s) for s in sessions]

def cc_create_session(agent="codex", cwd="", mode="yolo"):
    agent = agent if agent in ("codex", "claude") else "codex"
    mode = mode if mode in ("yolo", "default", "plan") else "yolo"
    cwd = os.path.abspath(cwd) if cwd and os.path.isdir(cwd) else os.path.expanduser("~")
    session = {
        "id": str(uuid.uuid4()),
        "title": "新会话",
        "agent": agent,
        "mode": mode,
        "cwd": cwd,
        "messages": [],
        "created": cc_iso(),
        "updated": cc_iso(),
    }
    cc_save_session(session)
    return session

def cc_cli_exists(name):
    return bool(shutil.which(name))

def cc_build_command(agent, prompt, cwd, mode):
    if agent == "claude":
        if not cc_cli_exists("claude"):
            return None, "找不到 Claude CLI，请确认本机已安装并登录 claude。"
        cmd = ["claude", "-p"]
        if mode == "yolo":
            cmd.append("--dangerously-skip-permissions")
        elif mode in ("plan", "default"):
            cmd.extend(["--permission-mode", mode])
        cmd.append(prompt)
        return cmd, None

    if not cc_cli_exists("codex"):
        return None, "找不到 Codex CLI，请确认本机已安装并登录 codex。"
    cmd = ["codex", "exec", "--skip-git-repo-check"]
    if mode == "yolo":
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    elif mode == "default":
        cmd.append("--full-auto")
    elif mode == "plan":
        cmd.extend(["-s", "read-only"])
    if cwd and os.path.isdir(cwd):
        cmd.extend(["-C", cwd])
    cmd.append(prompt)
    return cmd, None

def cc_run_agent(session, prompt):
    agent = session.get("agent", "codex")
    cwd = session.get("cwd") or os.path.expanduser("~")
    mode = session.get("mode", "yolo")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    cmd, err = cc_build_command(agent, prompt, cwd, mode)
    if err:
        return {"ok": False, "error": err}
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=CC_AGENT_TIMEOUT,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        out = (r.stdout or "").strip()
        if r.stderr:
            out = (out + "\n\n" if out else "") + r.stderr.strip()
        if not out:
            out = "(no output)"
        return {"ok": r.returncode == 0, "output": out, "returncode": r.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{agent} 执行超时（{CC_AGENT_TIMEOUT}s）"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ─── 保持屏幕唤醒 ───

keep_screen_alive = False

def set_keep_screen_alive(enable):
    global keep_screen_alive
    keep_screen_alive = enable
    flag = "0x80000000 | 0x00000001 | 0x00000002" if enable else "0x80000000"
    run_ps(f'Add-Type @"using System.Runtime.InteropServices; public class WinAPI {{ [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags); }}"; [WinAPI]::SetThreadExecutionState({flag})')

# ─── 截屏 ───

def take_screenshot():
    ps_script = '''
    Add-Type @"
    using System;
    using System.Runtime.InteropServices;
    public class DpiFix {
        [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
        [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr dpiContext);
        [DllImport("user32.dll")] public static extern int GetSystemMetrics(int nIndex);
    }
"@
    try { [DpiFix]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null } catch { [DpiFix]::SetProcessDPIAware() | Out-Null }
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $x = [DpiFix]::GetSystemMetrics(76)
    $y = [DpiFix]::GetSystemMetrics(77)
    $w = [DpiFix]::GetSystemMetrics(78)
    $h = [DpiFix]::GetSystemMetrics(79)
    if ($w -le 0 -or $h -le 0) {
        $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $x = $b.Left; $y = $b.Top; $w = $b.Width; $h = $b.Height
    }
    $bmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($x, $y, 0, 0, [System.Drawing.Size]::new($w, $h), [System.Drawing.CopyPixelOperation]::SourceCopy)
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Jpeg)
    [Convert]::ToBase64String($ms.ToArray())
    $g.Dispose(); $bmp.Dispose(); $ms.Dispose()
    '''
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except:
        pass
    return None

# ─── 文件浏览 ───

def get_drives():
    if hasattr(os, "listdrives"):
        drives = []
        for drive_path in os.listdrives():
            try:
                usage = shutil.disk_usage(drive_path)
            except:
                continue
            letter = drive_path[:1].upper() if len(drive_path) >= 2 and drive_path[1] == ":" else drive_path.rstrip("\\/")
            drives.append({
                "letter": letter,
                "used": round(usage.used / 1073741824, 1),
                "free": round(usage.free / 1073741824, 1),
            })
        if drives:
            return drives
    out = run_ps('Get-PSDrive -PSProvider FileSystem | ForEach-Object { "$($_.Name)|$([math]::Round($_.Used/1GB,1))|$([math]::Round($_.Free/1GB,1))" }')
    drives = []
    for line in out.split("\n"):
        line = line.strip()
        if "|" in line:
            parts = line.split("|")
            if len(parts) == 3:
                drives.append({"letter": parts[0], "used": parts[1], "free": parts[2]})
    return drives

def get_quick_access():
    """获取快速访问文件夹"""
    user = os.path.expanduser("~")
    return [
        {"name": "桌面", "path": os.path.join(user, "Desktop")},
        {"name": "下载", "path": os.path.join(user, "Downloads")},
        {"name": "文档", "path": os.path.join(user, "Documents")},
        {"name": "图片", "path": os.path.join(user, "Pictures")},
        {"name": "视频", "path": os.path.join(user, "Videos")},
        {"name": "音乐", "path": os.path.join(user, "Music")},
    ]

def list_directory(path):
    if not os.path.isdir(path):
        return None, f"Not found: {path}"
    items = []
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    is_dir = entry.is_dir()
                    stat = entry.stat()
                    size = 0 if is_dir else stat.st_size
                    mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
                    ext = os.path.splitext(entry.name)[1].lower() if not is_dir else ""
                    items.append({"name": entry.name, "dir": is_dir, "size": size, "time": mtime, "ext": ext})
                except:
                    items.append({"name": entry.name, "dir": False, "size": 0, "time": "?", "ext": ""})
    except PermissionError:
        return [], "Access denied"
    except Exception as e:
        return [], str(e)
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return items, None

def create_folder(path):
    """创建文件夹"""
    try:
        os.makedirs(path, exist_ok=True)
        return True, None
    except Exception as e:
        return False, str(e)

def delete_item(path):
    """删除文件或文件夹"""
    try:
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True, None
    except Exception as e:
        return False, str(e)

def rename_item(old_path, new_path):
    """重命名文件或文件夹"""
    try:
        os.rename(old_path, new_path)
        return True, None
    except Exception as e:
        return False, str(e)

# ─── 缓存 ───

cache = {}
cache_time = 0

def get_all_status():
    global cache, cache_time
    now = time.time()
    if now - cache_time < 3 and cache:
        return cache
    net = get_network_info()
    sysinfo = get_all_system_info()
    cache = {
        "cpu": sysinfo["cpu"], "mem": sysinfo["mem"], "disks": sysinfo["disks"],
        "uptime": sysinfo["uptime"], "procs": sysinfo["procs"],
        "net": net, "gpu": get_gpu_info(), "top": sysinfo["top"],
        "ts": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    cache_time = now
    return cache

# ─── HTML ───

HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>PC Monitor</title>
<style>

@font-face{font-family:'Maple';src:url('/font.ttf') format('truetype');font-weight:normal;font-style:normal}
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:'Maple',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0a1a;color:#e0e0e0;padding:16px;padding-bottom:80px;background-image:radial-gradient(ellipse at 20% 50%,rgba(105,240,174,.04) 0%,transparent 50%),radial-gradient(ellipse at 80% 20%,rgba(100,126,234,.04) 0%,transparent 50%),radial-gradient(ellipse at 50% 80%,rgba(118,75,162,.03) 0%,transparent 50%)}
body::before{content:'';position:fixed;top:0;left:0;right:0;bottom:0;background:radial-gradient(circle at 30% 40%,rgba(105,240,174,.06) 0%,transparent 40%),radial-gradient(circle at 70% 60%,rgba(64,196,255,.04) 0%,transparent 40%);animation:meshMove 20s ease-in-out infinite alternate;pointer-events:none;z-index:-1}
body::after{content:'';position:fixed;top:0;left:0;width:2px;height:2px;background:transparent;box-shadow:25vw 15vh rgba(105,240,174,.15),75vw 25vh rgba(105,240,174,.1),10vw 70vh rgba(105,240,174,.12),60vw 80vh rgba(105,240,174,.08),40vw 45vh rgba(100,126,234,.1),85vw 60vh rgba(100,126,234,.08),15vw 35vh rgba(118,75,162,.1),50vw 10vh rgba(105,240,174,.06),90vw 90vh rgba(100,126,234,.06),30vw 55vh rgba(105,240,174,.09);animation:floatParticles 30s ease-in-out infinite alternate;pointer-events:none;z-index:-1}
@keyframes meshMove{0%{transform:translate(0,0) scale(1)}50%{transform:translate(-20px,15px) scale(1.05)}100%{transform:translate(10px,-10px) scale(1)}}
@keyframes floatParticles{0%{transform:translate(0,0)}25%{transform:translate(-10px,15px)}50%{transform:translate(8px,-12px)}75%{transform:translate(-5px,8px)}100%{transform:translate(3px,-5px)}}
@keyframes cardIn{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:translateY(0)}}
@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
@keyframes tabFadeIn{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
@keyframes statusPulse{0%,100%{opacity:1;box-shadow:0 0 6px rgba(0,200,83,.6),0 0 12px rgba(0,200,83,.3)}50%{opacity:.5;box-shadow:0 0 2px rgba(0,200,83,.3),0 0 4px rgba(0,200,83,.1)}}
@keyframes spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
@keyframes blink{0%,100%{opacity:.7}50%{opacity:0}}
@keyframes captureFlash{0%{opacity:0}20%{opacity:1}100%{opacity:0}}
@keyframes captureSlideIn{from{opacity:0;transform:scale(.9) translateY(10px)}to{opacity:1;transform:scale(1) translateY(0)}}
@keyframes ccPop{from{opacity:0;transform:translateY(6px) scale(.99)}to{opacity:1;transform:none}}
@keyframes pillGlow{0%,100%{box-shadow:0 0 4px rgba(105,240,174,.15)}50%{box-shadow:0 0 10px rgba(105,240,174,.25)}}
.hdr{text-align:center;padding:20px 0 16px;border-bottom:1px solid rgba(255,255,255,.08);margin-bottom:16px;animation:cardIn .5s ease both}
.hdr h1{font-size:20px;font-weight:600}
.hdr .sub{font-size:13px;color:#888;margin-top:4px}
.card{background:rgba(255,255,255,.04);border-radius:12px;padding:16px;margin-bottom:12px;border:1px solid rgba(255,255,255,.06);transition:transform .15s ease,border-color .3s,box-shadow .3s;animation:cardIn .5s ease both}
.card:active{transform:scale(.97);border-color:rgba(105,240,174,.3);box-shadow:0 0 20px rgba(105,240,174,.08),inset 0 0 20px rgba(105,240,174,.03)}
#tab-status .row:nth-child(1) .card:nth-child(1){animation-delay:.05s}
#tab-status .row:nth-child(1) .card:nth-child(2){animation-delay:.1s}
#tab-status .row:nth-child(2) .card:nth-child(1){animation-delay:.15s}
#tab-status .row:nth-child(2) .card:nth-child(2){animation-delay:.2s}
#tab-status > .card:nth-child(3){animation-delay:.25s}
#tab-status > .card:nth-child(4){animation-delay:.3s}
#tab-status .row:nth-child(4) .card:nth-child(1){animation-delay:.35s}
#tab-status .row:nth-child(4) .card:nth-child(2){animation-delay:.4s}
#tab-status > #procCard{animation-delay:.45s}
.ct{font-size:13px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px}
.bw{background:rgba(255,255,255,.08);border-radius:6px;height:22px;position:relative;overflow:hidden;margin-bottom:8px}
.bf{height:100%;border-radius:6px;transition:width .6s ease;position:relative;overflow:hidden}
.bf::after{content:'';position:absolute;top:0;left:0;right:0;bottom:0;background:linear-gradient(90deg,transparent 0%,rgba(255,255,255,.15) 50%,transparent 100%);animation:shimmer 2s ease-in-out infinite}
.bf.g{background:linear-gradient(90deg,#00c853,#69f0ae);box-shadow:0 0 8px rgba(105,240,174,.4)}
.bf.y{background:linear-gradient(90deg,#ffc107,#ffeb3b);box-shadow:0 0 8px rgba(255,235,59,.4)}
.bf.r{background:linear-gradient(90deg,#ff5252,#ff8a80);box-shadow:0 0 8px rgba(255,138,128,.4)}
.bl{position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:12px;font-weight:600;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.5);z-index:2}
.bnum{font-size:36px;font-weight:700;line-height:1}.bnum .u{font-size:16px;font-weight:400;color:#888}
.si{font-size:13px;color:#888;margin-top:4px}
.row{display:flex;gap:10px}.row .card{flex:1;text-align:center}
.dr{display:flex;align-items:center;gap:10px;margin-bottom:10px}.dr:last-child{margin-bottom:0}
.dd{font-size:14px;font-weight:600;min-width:30px}.db{flex:1}.di{font-size:12px;color:#888;min-width:100px;text-align:right}
.df{font-size:11px;color:#69f0ae;margin-top:2px}
.pr{display:flex;align-items:center;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:13px}.pr:last-child{border:none}
.pn{flex:1;color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pc{width:60px;text-align:right;color:#69f0ae;font-weight:500}
.pm{width:70px;text-align:right;color:#90caf9;font-weight:500}
.ph{font-size:11px;color:#666;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid rgba(255,255,255,.08);padding-bottom:6px;margin-bottom:4px}
.gpu{font-size:14px;color:#ccc}
.tabs{display:flex;gap:0;margin-bottom:12px;background:rgba(255,255,255,.04);border-radius:10px;overflow:hidden;border:1px solid rgba(255,255,255,.06);animation:cardIn .5s ease both;animation-delay:.02s}
.tab{flex:1;padding:10px;text-align:center;font-size:13px;font-weight:500;cursor:pointer;color:#888;transition:all .2s}
.tab.active{background:rgba(105,240,174,.15);color:#69f0ae;box-shadow:inset 0 0 12px rgba(105,240,174,.1)}
.tab-content{display:none}.tab-content.active{display:block;animation:tabFadeIn .3s ease}
.sbtn{display:inline-flex;align-items:center;gap:6px;padding:10px 18px;border:none;border-radius:8px;font-size:14px;font-weight:500;cursor:pointer;color:#fff;background:linear-gradient(135deg,#667eea,#764ba2);margin:4px;transition:transform .15s,box-shadow .15s;box-shadow:0 4px 16px rgba(102,126,234,.2)}
.sbtn:disabled{opacity:.5}.sbtn:active{transform:scale(.93);box-shadow:0 2px 8px rgba(102,126,234,.15)}
.ss-flash{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(255,255,255,.15);z-index:9999;animation:captureFlash .4s ease-out forwards;pointer-events:none}
#ssImg{width:100%;height:auto;display:block;border-radius:8px;animation:captureSlideIn .4s ease both}
.tbox{display:none;margin-top:12px;background:#0d0d0d;border-radius:8px;border:1px solid rgba(105,240,174,.15);overflow:hidden;box-shadow:0 0 30px rgba(105,240,174,.05),inset 0 0 30px rgba(0,0,0,.3)}
.thdr{background:rgba(255,255,255,.06);padding:8px 12px;font-size:12px;color:#888;display:flex;align-items:center;justify-content:space-between}
.thdr .x{background:none;border:none;color:#888;font-size:18px;cursor:pointer;padding:0 4px}
.tout{padding:12px;max-height:400px;overflow-y:auto;font-family:"Cascadia Mono","Consolas",monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-all;color:#d4d4d4;position:relative}
.tout::before{content:'';position:absolute;top:0;left:0;right:0;bottom:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.03) 2px,rgba(0,0,0,.03) 4px);pointer-events:none;z-index:1}
.tout::-webkit-scrollbar{width:6px}.tout::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:3px}
.trow{display:flex;align-items:center;padding:8px 12px;border-top:1px solid rgba(255,255,255,.08);gap:8px}
.tp{color:#69f0ae;font-family:monospace;font-size:13px;white-space:nowrap}
.tp::after{content:'\2588';animation:blink 1s step-end infinite;font-size:11px;opacity:.7}
.ti{flex:1;background:none;border:none;color:#fff;font-family:monospace;font-size:13px;outline:none}
.ts{background:#69f0ae;border:none;color:#000;padding:4px 12px;border-radius:4px;font-size:12px;font-weight:600;cursor:pointer}
.flist{max-height:500px;overflow-y:auto}
.fitem{display:flex;align-items:center;padding:10px 8px;border-bottom:1px solid rgba(255,255,255,.04);font-size:13px;cursor:pointer;transition:background .15s}
.fitem:active{background:rgba(255,255,255,.08)}
.fitem:last-child{border:none}
.ficon{width:28px;text-align:center;font-size:16px}
.fname{flex:1;color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fsize{width:80px;text-align:right;color:#888;font-size:12px}
.fdate{width:100px;text-align:right;color:#666;font-size:12px}
.footer{position:fixed;bottom:0;left:0;right:0;text-align:center;padding:12px;background:rgba(10,10,26,.95);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);border-top:1px solid rgba(105,240,174,.08);font-size:12px;color:#666;box-shadow:0 -4px 20px rgba(105,240,174,.03)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.ld{display:inline-block;width:8px;height:8px;background:#00c853;border-radius:50%;margin-right:6px;animation:statusPulse 2s ease-in-out infinite}
@keyframes spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}
.loading{animation:spin 1s linear infinite;display:inline-block}
.drive-item{display:flex;align-items:center;padding:12px 8px;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:background .15s}
.drive-item:active{background:rgba(255,255,255,.08)}
.drive-item:last-child{border:none}
.drive-icon{font-size:20px;margin-right:10px}
.drive-name{font-weight:600;color:#69f0ae;font-size:15px}
.drive-info{margin-left:auto;font-size:12px;color:#888}
/* Explorer styles */
.exp-toolbar{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.exp-btn{padding:6px 10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:6px;color:#ccc;font-size:14px;cursor:pointer;transition:all .15s}
.exp-btn:hover{background:rgba(255,255,255,.1)}
.exp-btn:active{transform:scale(.95)}
.exp-btn.disabled{opacity:.4;cursor:not-allowed}
.exp-addr{display:flex;flex:1;min-width:150px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:6px;overflow:hidden}
.exp-addr input{flex:1;background:none;border:none;color:#e0e0e0;padding:6px 10px;font-size:13px;outline:none;font-family:monospace}
.exp-addr button{background:rgba(105,240,174,.2);border:none;color:#69f0ae;padding:6px 12px;cursor:pointer}
.exp-search{display:flex;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:6px;overflow:hidden}
.exp-search input{width:120px;background:none;border:none;color:#e0e0e0;padding:6px 10px;font-size:13px;outline:none}
.exp-quick{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.exp-quick-item{padding:6px 12px;background:rgba(105,240,174,.1);border:1px solid rgba(105,240,174,.2);border-radius:16px;font-size:12px;color:#69f0ae;cursor:pointer;transition:all .15s}
.exp-quick-item:hover{background:rgba(105,240,174,.2)}
.exp-header{display:flex;align-items:center;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.1);font-size:11px;color:#666;text-transform:uppercase}
.exp-header .exp-hname{flex:1}
.exp-header .exp-hsize{width:80px;text-align:right}
.exp-header .exp-htime{width:120px;text-align:right}
.exp-item{display:flex;align-items:center;padding:10px;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:background .15s,transform .15s;border-radius:4px;touch-action:manipulation;user-select:none;-webkit-user-select:none}
.exp-item:hover{background:rgba(255,255,255,.06)}
.exp-item:active{background:rgba(105,240,174,.12);transform:scale(.98)}
.exp-item.selected{background:rgba(105,240,174,.15)}
.exp-item .exp-icon{width:24px;text-align:center;font-size:16px;margin-right:8px}
.exp-item .exp-name{flex:1;color:#ccc;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.exp-item .exp-size{width:80px;text-align:right;color:#888;font-size:12px}
.exp-item .exp-time{width:120px;text-align:right;color:#666;font-size:12px}
.exp-context{position:fixed;background:rgba(30,30,50,.95);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:4px 0;min-width:160px;z-index:1000;opacity:0;transform:scale(.9) translateY(-5px);transform-origin:top left;pointer-events:none;transition:opacity .15s ease,transform .15s ease;box-shadow:0 8px 32px rgba(0,0,0,.5)}
.exp-context.visible{opacity:1;transform:scale(1) translateY(0);pointer-events:auto}
.exp-context-item{padding:8px 16px;font-size:13px;color:#ccc;cursor:pointer;transition:background .15s}
.exp-context-item:hover{background:rgba(105,240,174,.15)}
.exp-context-divider{height:1px;background:rgba(255,255,255,.1);margin:4px 0}
.exp-status{display:flex;justify-content:space-between;padding:8px 10px;font-size:12px;color:#888;border-top:1px solid rgba(255,255,255,.06);margin-top:8px}

/* 磁盘大卡片 - 醒目可视化 */
.drive-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.drive-card{position:relative;overflow:hidden;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:0;cursor:pointer;transition:all .25s;animation:cardIn .5s ease both}
.drive-card:hover{border-color:rgba(105,240,174,.4);transform:translateY(-2px);box-shadow:0 8px 32px rgba(105,240,174,.1)}
.drive-card:active{transform:scale(.97);box-shadow:0 0 24px rgba(105,240,174,.15)}
.drive-card-bg{position:absolute;bottom:0;left:0;right:0;border-radius:0 0 16px 16px;transition:height .8s ease;opacity:.15}
.drive-card-body{position:relative;padding:20px;display:flex;align-items:center;gap:16px}
.drive-ring{position:relative;width:80px;height:80px;flex-shrink:0}
.drive-ring svg{transform:rotate(-90deg)}
.drive-ring circle{fill:none;stroke-width:6;stroke-linecap:round}
.drive-ring .ring-bg{stroke:rgba(255,255,255,.08)}
.drive-ring .ring-fg{transition:stroke-dashoffset .8s ease}
.drive-ring-pct{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:20px;font-weight:700;color:#fff}
.drive-info{flex:1;min-width:0}
.drive-letter{font-size:24px;font-weight:800;margin-bottom:2px}
.drive-label{font-size:12px;color:#888;margin-bottom:8px}
.drive-bar{background:rgba(255,255,255,.08);border-radius:6px;height:8px;overflow:hidden;margin-bottom:6px}
.drive-bar-fill{height:100%;border-radius:6px;transition:width .8s ease}
.drive-detail{display:flex;justify-content:space-between;font-size:11px;color:#888}
.drive-detail b{color:#ccc;font-weight:500}
/* 颜色主题 */
.drive-g .drive-letter{color:#69f0ae}
.drive-g .ring-fg,.drive-g .drive-bar-fill{stroke:#69f0ae;background:#69f0ae}
.drive-g .drive-card-bg{background:linear-gradient(to top,#69f0ae,#00c853)}
.drive-y .drive-letter{color:#ffeb3b}
.drive-y .ring-fg,.drive-y .drive-bar-fill{stroke:#ffeb3b;background:#ffeb3b}
.drive-y .drive-card-bg{background:linear-gradient(to top,#ffeb3b,#ffc107)}
.drive-r .drive-letter{color:#ff8a80}
.drive-r .ring-fg,.drive-r .drive-bar-fill{stroke:#ff8a80;background:#ff8a80}
.drive-r .drive-card-bg{background:linear-gradient(to top,#ff8a80,#ff5252)}

/* Built-in CC-Web */
.cc-shell{display:grid;grid-template-columns:220px 1fr;min-height:70vh;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.08);border-radius:12px;overflow:hidden}
.cc-side{border-right:1px solid rgba(255,255,255,.08);background:rgba(0,0,0,.14);display:flex;flex-direction:column}
.cc-new{margin:12px;padding:10px 12px;border:0;border-radius:8px;background:linear-gradient(135deg,#69f0ae,#40c4ff);color:#061012;font-weight:800;cursor:pointer;box-shadow:0 8px 24px rgba(64,196,255,.16)}
.cc-list{flex:1;overflow:auto;padding:0 8px 12px}
.cc-session{width:100%;text-align:left;border:1px solid transparent;background:transparent;color:#bbb;border-radius:8px;padding:10px;margin-bottom:6px;cursor:pointer;transition:all .18s}
.cc-session:hover{background:rgba(255,255,255,.06);transform:translateX(2px)}
.cc-session.active{background:rgba(105,240,174,.13);border-color:rgba(105,240,174,.28);color:#fff}
.cc-session b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}
.cc-session span{display:block;color:#888;font-size:11px;margin-top:3px}
.cc-main{display:flex;flex-direction:column;min-width:0}
.cc-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:12px;border-bottom:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.03)}
.cc-head select,.cc-head input{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:8px;color:#eee;padding:8px 10px;outline:none}
.cc-head input{flex:1;min-width:180px;font-family:monospace}
.cc-pill{padding:7px 10px;border-radius:999px;background:rgba(105,240,174,.12);color:#69f0ae;font-size:12px;border:1px solid rgba(105,240,174,.2);animation:pillGlow 3s ease-in-out infinite}
.cc-messages{flex:1;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:12px;min-height:320px;max-height:64vh}
.cc-msg{max-width:92%;padding:12px 14px;border-radius:12px;border:1px solid rgba(255,255,255,.08);animation:ccPop .2s ease both;white-space:pre-wrap;word-break:break-word;line-height:1.55}
.cc-msg.user{align-self:flex-end;background:linear-gradient(135deg,rgba(105,240,174,.2),rgba(64,196,255,.13));color:#fff}
.cc-msg.assistant{align-self:flex-start;background:rgba(255,255,255,.055);color:#ddd}
.cc-msg.system{align-self:center;background:rgba(255,235,59,.1);color:#ffec99;font-size:12px}
.cc-input{display:flex;gap:8px;padding:12px;border-top:1px solid rgba(255,255,255,.08);background:rgba(0,0,0,.12)}
.cc-input textarea{flex:1;min-height:54px;max-height:160px;resize:vertical;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:10px;color:#fff;padding:10px;outline:none}
.cc-send{width:76px;border:0;border-radius:10px;background:linear-gradient(135deg,#7c4dff,#40c4ff);color:white;font-weight:800;cursor:pointer}
.cc-send:disabled{opacity:.5;cursor:wait}
@keyframes ccPop{from{opacity:0;transform:translateY(6px) scale(.99)}to{opacity:1;transform:none}}
.card:hover{border-color:rgba(105,240,174,.18);box-shadow:0 10px 34px rgba(0,0,0,.22),0 0 28px rgba(105,240,174,.07);transform:translateY(-1px)}
.tab.active{position:relative;overflow:hidden}
.tab.active:after{content:'';position:absolute;left:12%;right:12%;bottom:0;height:2px;background:linear-gradient(90deg,transparent,#69f0ae,#40c4ff,transparent);animation:tabScan 2.2s ease-in-out infinite}
.drive-ring .ring-fg{filter:drop-shadow(0 0 6px currentColor)}
.cc-shell{position:relative}
.cc-shell:before{content:'';position:absolute;inset:0;background:linear-gradient(120deg,transparent,rgba(105,240,174,.04),transparent);transform:translateX(-120%);animation:ccSweep 5s ease-in-out infinite;pointer-events:none}
.cc-new,.cc-send{animation:pillGlow 3s ease-in-out infinite}
@keyframes tabScan{0%,100%{transform:translateX(-30%);opacity:.4}50%{transform:translateX(30%);opacity:1}}
@keyframes ccSweep{0%,55%{transform:translateX(-120%)}100%{transform:translateX(120%)}}
@media(max-width:375px){body{padding:10px;padding-bottom:70px}.hdr h1{font-size:17px}.hdr .sub{font-size:11px}.bnum{font-size:28px}.bnum .u{font-size:13px}.card{padding:12px;margin-bottom:10px;border-radius:10px}.ct{font-size:11px}.tab{padding:8px 4px;font-size:12px}.row{gap:8px}.drive-card-body{padding:14px;gap:12px}.drive-ring{width:64px;height:64px}.drive-ring svg{width:64px;height:64px}.drive-ring-pct{font-size:16px}.drive-letter{font-size:20px}.sbtn{padding:8px 14px;font-size:13px}.exp-item{padding:8px 6px}.exp-item .exp-time{display:none}.exp-header .exp-htime{display:none}}
@media(min-width:376px) and (max-width:768px){body{padding:14px;padding-bottom:76px}.hdr h1{font-size:19px}.bnum{font-size:32px}}
@media(max-width:700px){.cc-shell{grid-template-columns:1fr}.cc-side{border-right:0;border-bottom:1px solid rgba(255,255,255,.08);max-height:190px}.cc-messages{max-height:58vh}.cc-input textarea{min-height:44px}}
@media(max-height:500px) and (orientation:landscape){.hdr{padding:10px 0 8px}.hdr h1{font-size:16px}.tabs{margin-bottom:8px}.tab{padding:6px;font-size:12px}.cc-messages{max-height:40vh}}
@media(prefers-reduced-motion:reduce){.card,.drive-card,.tab,.hdr,.tabs{animation:none!important}.bf::after{animation:none}.ld{animation:pulse 2s infinite}.cc-pill{animation:none}body::before{animation:none}body::after{animation:none}.exp-context{transition:none}.tab-content.active{animation:none}}
</style>
</head>
<body>
<div class="hdr">
  <h1 id="hostname">{{HOSTNAME}}</h1>
  <div class="sub">{{IP}}</div>
  <div class="sub">{{UPTIME}}</div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('status')">Status</div>
  <div class="tab" onclick="switchTab('files')">Files</div>
  <div class="tab" onclick="switchTab('tools')">Tools</div>
  <div class="tab" onclick="switchTab('ccweb')">CC-Web</div>
</div>

<!-- Status -->
<div class="tab-content active" id="tab-status">
  <div class="row">
    <div class="card"><div class="ct">CPU</div><div class="bnum" style="color:{{CPU_C}}">{{CPU}}<span class="u">%</span></div></div>
    <div class="card"><div class="ct">MEM</div><div class="bnum" style="color:{{MEM_C}}">{{MEM_P}}<span class="u">%</span></div><div class="si">{{MEM_U}} / {{MEM_T}} GB</div></div>
  </div>
  <div class="card"><div class="ct">CPU Usage</div><div class="bw"><div class="bf {{CPU_B}}" style="width:{{CPU}}%"><span class="bl">{{CPU}}%</span></div></div></div>
  <div class="card"><div class="ct">Memory</div><div class="bw"><div class="bf {{MEM_B}}" style="width:{{MEM_P}}%"><span class="bl">{{MEM_U}} / {{MEM_T}} GB</span></div></div></div>
  <div class="card"><div class="ct">DISK</div>{{DISKS}}</div>
  <div class="row">
    <div class="card"><div class="ct">PROCS</div><div class="bnum" style="color:#ce93d8">{{PROC}}</div></div>
    <div class="card"><div class="ct">GPU</div><div class="gpu">{{GPU}}</div></div>
  </div>
  <div class="card" id="procCard">
    <div class="ct">进程管理 <span style="font-size:11px;color:#888">(右键结束进程)</span></div>
    <div style="margin-bottom:10px;display:flex;gap:8px">
      <input type="text" id="procSearch" placeholder="搜索进程..." style="flex:1;padding:6px 10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:6px;color:#fff;font-size:13px;outline:none" oninput="filterProcs(this.value)">
      <button onclick="refreshProcs()" style="padding:6px 12px;background:rgba(105,240,174,.2);border:none;border-radius:6px;color:#69f0ae;cursor:pointer">刷新</button>
    </div>
    <div class="pr ph" style="cursor:pointer"><span class="pn" onclick="sortProcs('name')">名称 ⇅</span><span class="pc" onclick="sortProcs('cpu')">CPU ⇅</span><span class="pm" onclick="sortProcs('mem')">内存 ⇅</span><span style="width:50px;text-align:right;cursor:pointer" onclick="sortProcs('pid')">PID ⇅</span></div>
    <div id="procList" style="max-height:70vh;min-height:200px;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;touch-action:pan-y;overscroll-behavior:contain"></div>
    <div style="padding:8px 10px;font-size:12px;color:#888;border-top:1px solid rgba(255,255,255,.06)"><span id="procCount">加载中...</span></div>
  </div>
</div>
<!-- Process Context Menu -->
<div class="exp-context" id="procContext">
  <div class="exp-context-item" onclick="procKill()">💀 结束进程</div>
  <div class="exp-context-item" onclick="procForceKill()" style="color:#ff8a80">⚡ 强制结束</div>
</div>

<!-- Explorer -->
<div class="tab-content" id="tab-files">
  <div class="card">
    <div class="ct">资源管理器</div>
    <div class="exp-toolbar">
      <button class="exp-btn" onclick="expBack()" id="expBackBtn" title="后退">◀</button>
      <button class="exp-btn" onclick="expForward()" id="expFwdBtn" title="前进">▶</button>
      <button class="exp-btn" onclick="expHome()" title="主页">🏠</button>
      <button class="exp-btn" onclick="expUp()" title="上一级">⬆</button>
      <div class="exp-addr">
        <input type="text" id="expAddr" value="" placeholder="输入路径..." onkeydown="if(event.key==='Enter')expGo(this.value)">
        <button onclick="expGo(document.getElementById('expAddr').value)">→</button>
      </div>
      <div class="exp-search">
        <input type="text" id="expSearch" placeholder="搜索..." oninput="expFilter(this.value)">
      </div>
    </div>
    <div class="exp-quick" id="expQuick"></div>
    <div class="exp-header"><span class="exp-hname">名称</span><span class="exp-hsize">大小</span><span class="exp-htime">修改时间</span></div>
    <div id="expList" style="max-height:60vh;min-height:200px;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;touch-action:pan-y;overscroll-behavior:contain"></div>
    <div class="exp-status"><span id="expStatus">就绪</span><span id="expCount"></span></div>
  </div>
</div>
<!-- Context Menu -->
<div class="exp-context" id="expContext">
  <div class="exp-context-item" onclick="ctxOpen()">📂 打开</div>
  <div class="exp-context-item" onclick="ctxDownload()">⬇️ 下载</div>
  <div class="exp-context-divider"></div>
  <div class="exp-context-item" onclick="ctxNewFolder()">📁 新建文件夹</div>
  <div class="exp-context-item" onclick="ctxRename()">✏️ 重命名</div>
  <div class="exp-context-item" onclick="ctxDelete()" style="color:#ff8a80">🗑️ 删除</div>
</div>

<!-- Tools -->
<div class="tab-content" id="tab-tools">
  <div class="card" style="text-align:center">
    <div class="ct">TOOLS</div>
    <button class="sbtn" onclick="takeSS()" id="ssBtn">Screenshot</button>
    <button class="sbtn" style="background:linear-gradient(135deg,#11998e,#38ef7d)" onclick="toggleTerm()">PowerShell</button>
  </div>
  <div class="card" id="ssCard" style="display:none">
    <div class="ct">SCREENSHOT</div>
    <img id="ssImg" style="width:100%;height:auto;display:block;border-radius:8px">
  </div>
  <div class="tbox" id="termBox">
    <div class="thdr"><span>PowerShell</span><button class="x" onclick="toggleTerm()">&times;</button></div>
    <div class="tout" id="termOut">Ready.\n\n</div>
    <div class="trow"><span class="tp">PS&gt;</span><input class="ti" id="termIn" placeholder="Enter command..." autocomplete="off" spellcheck="false"><button class="ts" onclick="sendCmd()">Run</button></div>
  </div>
</div>

<!-- CC-Web -->
<div class="tab-content" id="tab-ccweb">
  <div class="card" style="padding:0;overflow:hidden">
    <div class="cc-shell">
      <aside class="cc-side">
        <button class="cc-new" onclick="ccNewSession()">+ 新会话</button>
        <div class="cc-list" id="ccSessionList"></div>
      </aside>
      <section class="cc-main">
        <div class="cc-head">
          <select id="ccAgent"><option value="codex">Codex</option><option value="claude">Claude</option></select>
          <select id="ccMode"><option value="yolo">YOLO</option><option value="default">默认</option><option value="plan">Plan</option></select>
          <input id="ccCwd" placeholder="工作目录，例如 D:\Aix\WindowsWeb-Super-Console">
          <span class="cc-pill" id="ccState">就绪</span>
        </div>
        <div class="cc-messages" id="ccMessages">
          <div class="cc-msg system">内置 CC-Web 已合并到本服务。选择 Agent 后即可发送任务，不再启动外部 8002 服务。</div>
        </div>
        <div class="cc-input">
          <textarea id="ccInput" placeholder="输入给 Claude / Codex 的任务，支持 /clear、/cwd、/mode、/help"></textarea>
          <button class="cc-send" id="ccSend" onclick="ccSend()">发送</button>
        </div>
      </section>
    </div>
  </div>
</div>

<div class="footer"><span class="ld"></span> <span id="ts">{{TIMESTAMP}}</span></div>

<script>
// Tab
function switchTab(n){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['status','files','tools','ccweb'][i]===n));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.getElementById('tab-'+n).classList.add('active');
  if(n==='ccweb')ccLoad();
  if(n==='files')expLoad();
}

// Animated number counting
const _animState={};
function animateValue(el,end,suffix,dur){
  const key=el.id||el.className;
  const start=_animState[key]||0;
  _animState[key]=end;
  if(start===end)return;
  const st=performance.now();
  (function tick(now){
    const p=Math.min((now-st)/(dur||400),1);
    const ease=1-Math.pow(1-p,3);
    const v=Math.round(start+(end-start)*ease);
    el.innerHTML=v+'<span class="u">'+suffix+'</span>';
    if(p<1)requestAnimationFrame(tick);
  })(st);
}

// AJAX refresh
let rt=null;
async function sched(){
  clearTimeout(rt);
  if(!document.hidden)rt=setTimeout(async()=>{
    if(document.hidden)return;
    try{
      const d=await(await fetch('/api')).json();
      animateValue(document.querySelector('#tab-status .row .card:first-child .bnum'),d.cpu,'%');
      animateValue(document.querySelector('#tab-status .row .card:last-child .bnum'),d.mem.percent,'%');
      document.querySelector('#tab-status .row .card:last-child .si').textContent=d.mem.used+' / '+d.mem.total+' GB';
      document.getElementById('ts').textContent=d.ts;
      document.querySelector('.hdr .sub:last-child').textContent='Uptime: '+d.uptime;
    }catch(e){}
    sched();
  },3000);
}
document.addEventListener('visibilitychange',sched);
sched();

// Screenshot
async function takeSS(){
  const btn=document.getElementById('ssBtn');
  btn.disabled=true;btn.innerHTML='<span class="loading">&#8635;</span> Capturing...';
  try{
    const r=await fetch('/api/screenshot');
    const d=await r.json();
    if(d.ok){
      // Flash effect
      const f=document.createElement('div');f.className='ss-flash';
      document.body.appendChild(f);
      setTimeout(()=>f.remove(),500);
      document.getElementById('ssImg').src='data:image/jpeg;base64,'+d.image;
      document.getElementById('ssImg').style.animation='none';
      void document.getElementById('ssImg').offsetWidth;
      document.getElementById('ssImg').style.animation='captureSlideIn .4s ease';
      document.getElementById('ssCard').style.display='block';
    }else alert('Failed');
  }catch(e){alert('Error: '+e.message)}
  btn.disabled=false;btn.innerHTML='Screenshot';
}

// Terminal
let tv=false,ch=[],ci=-1;
function toggleTerm(){tv=!tv;document.getElementById('termBox').style.display=tv?'block':'none';if(tv)document.getElementById('termIn').focus()}
async function sendCmd(){
  const inp=document.getElementById('termIn'),out=document.getElementById('termOut');
  const cmd=inp.value.trim();if(!cmd)return;
  ch.push(cmd);ci=ch.length;
  out.textContent+='PS> '+cmd+'\n';inp.value='';out.scrollTop=out.scrollHeight;
  try{const r=await fetch('/api/terminal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd})});const d=await r.json();if(d.output)out.textContent+=d.output+'\n\n';if(d.error)out.textContent+='[ERROR] '+d.error+'\n\n';}catch(e){out.textContent+='[ERR] '+e.message+'\n\n'}
  out.scrollTop=out.scrollHeight;
}
document.getElementById('termIn').addEventListener('keydown',function(e){
  if(e.key==='Enter')sendCmd();
  else if(e.key==='ArrowUp'){e.preventDefault();if(ci>0){ci--;this.value=ch[ci]}}
  else if(e.key==='ArrowDown'){e.preventDefault();if(ci<ch.length-1){ci++;this.value=ch[ci]}else{ci=ch.length;this.value=''}}
});

// File browser - 使用 data-path 避免转义问题
let loaded=false;
async function loadDrives(){
  loaded=true;
  try{
    const d=await(await fetch('/api/drives')).json();
    const c=document.getElementById('fileList');
    let h='';
    d.drives.forEach(dr=>{
      h+='<div class="drive-item" data-path="'+dr.letter+':\\" onclick="openDir(this.dataset.path)">';
      h+='<span class="drive-icon">&#128190;</span>';
      h+='<span class="drive-name">'+dr.letter+':\\</span>';
      h+='<span class="drive-info">'+dr.used+' GB / '+dr.free+' GB free</span>';
      h+='</div>';
    });
    c.innerHTML=h;
    document.getElementById('fbPath').textContent='All Drives';
  }catch(e){document.getElementById('fileList').innerHTML='<div style="color:#ff8a80">Error loading drives</div>'}
}

let longPressTimer=null,longPressTarget=null;
function startLongPress(e,el){
  e.preventDefault();
  longPressTarget=el;
  longPressTimer=setTimeout(()=>{
    el.style.background='rgba(105,240,174,.15)';
    if(confirm('下载文件 '+el.dataset.name+' ?')){
      window.open('/api/download?p='+encodeURIComponent(el.dataset.path));
    }
    el.style.background='';
    longPressTarget=null;
  },500);
}
function cancelLongPress(){
  if(longPressTimer){clearTimeout(longPressTimer);longPressTimer=null;}
  if(longPressTarget){longPressTarget.style.background='';longPressTarget=null;}
}
async function openDir(path){
  try{
    const r=await fetch('/api/files?p='+encodeURIComponent(path));
    const d=await r.json();
    if(d.error){alert(d.error);return}
    document.getElementById('fbPath').textContent=d.path;
    const c=document.getElementById('fileList');
    let h='<div class="fitem" onclick="loadDrives()"><span class="ficon">&#128194;</span><span class="fname" style="color:#69f0ae">Back to Drives</span></div>';
    d.items.forEach(i=>{
      const fp=d.path.replace(/\\$/,'')+'\\'+i.name;
      if(i.dir){
        h+='<div class="fitem" data-path="'+fp+'" data-name="'+i.name+'" onclick="openDir(this.dataset.path)">';
        h+='<span class="ficon">&#128193;</span><span class="fname">'+i.name+'</span><span class="fdate">'+i.time+'</span></div>';
      }else{
        const sz=i.size>1048576?(i.size/1048576).toFixed(1)+'MB':i.size>1024?(i.size/1024).toFixed(0)+'KB':i.size+'B';
        h+='<div class="fitem" data-path="'+fp+'" data-name="'+i.name+'" ontouchstart="startLongPress(event,this)" ontouchend="cancelLongPress()" ontouchmove="cancelLongPress()" onmousedown="startLongPress(event,this)" onmouseup="cancelLongPress()" onmouseleave="cancelLongPress()">';
        h+='<span class="ficon">&#128196;</span><span class="fname">'+i.name+'</span><span class="fsize">'+sz+'</span><span class="fdate">'+i.time+'</span>';
        h+='<span style="font-size:11px;color:#69f0ae;margin-left:8px">长按下载</span></div>';
      }
    });
    c.innerHTML=h;
  }catch(e){alert('Network error')}
}

// Explorer
let expHistory=[],expIdx=-1,expCurPath='',expAllItems=[],expLoaded=false;
const fileIcons={'.txt':'📄','.pdf':'📕','.doc':'📘','.docx':'📘','.xls':'📗','.xlsx':'📗','.ppt':'📙','.pptx':'📙','.jpg':'🖼️','.jpeg':'🖼️','.png':'🖼️','.gif':'🖼️','.mp3':'🎵','.mp4':'🎬','.zip':'📦','.rar':'📦','.exe':'⚙️','.py':'🐍','.js':'📜','.html':'🌐','.css':'🎨','.json':'📋'};
function getIcon(n,isDir){if(isDir)return '📁';const e='.'+n.split('.').pop().toLowerCase();return fileIcons[e]||'📄';}
function fmtSize(b){if(b>1073741824)return (b/1073741824).toFixed(1)+' GB';if(b>1048576)return (b/1048576).toFixed(1)+' MB';if(b>1024)return (b/1024).toFixed(0)+' KB';return b+' B';}
async function expLoad(){
  if(expLoaded)return;expLoaded=true;
  document.getElementById('expList').innerHTML='<div style="padding:40px;text-align:center;color:#888"><span class="loading">&#8635;</span> 加载磁盘...</div>';
  try{
    const qa=await(await fetch('/api/drives')).json();
    let dh='<div class="drive-grid">';
    qa.drives.forEach(d=>{
      const total=parseFloat(d.used)+parseFloat(d.free);
      const pct=total>0?Math.round(parseFloat(d.used)/total*100):0;
      const bc=pct<60?'g':pct<85?'y':'r';
      const r=36,circ=2*Math.PI*r,offset=circ-(pct/100)*circ;
      dh+=`<div class="drive-card drive-${bc}" onclick="expGo('${d.letter}:\\\\')">`;
      dh+='<div class="drive-card-bg" style="height:'+pct+'%"></div>';
      dh+='<div class="drive-card-body">';
      dh+='<div class="drive-ring"><svg width="80" height="80" viewBox="0 0 80 80">';
      dh+='<circle class="ring-bg" cx="40" cy="40" r="'+r+'"/>';
      dh+='<circle class="ring-fg" cx="40" cy="40" r="'+r+'" stroke-dasharray="'+circ+'" stroke-dashoffset="'+offset+'"/>';
      dh+='</svg><div class="drive-ring-pct">'+pct+'%</div></div>';
      dh+='<div class="drive-info">';
      dh+='<div class="drive-letter">'+d.letter+':</div>';
      dh+='<div class="drive-label">'+(d.letter==='C'?'系统盘':'数据盘')+'</div>';
      dh+='<div class="drive-bar"><div class="drive-bar-fill" style="width:'+pct+'%"></div></div>';
      dh+='<div class="drive-detail"><span>已用 <b>'+d.used+' GB</b></span><span>剩余 <b>'+d.free+' GB</b></span></div>';
      dh+='</div></div></div>';
    });
    dh+='</div>';
    let qh='<div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap">';
    qa.drives.forEach(d=>{qh+=`<div class="exp-quick-item" onclick="expGo('${d.letter}:\\\\')">${d.letter}:盘</div>`;});
    qh+='</div>';
    document.getElementById('expQuick').innerHTML=dh+qh;
    expGo('C:\\');
  }catch(e){document.getElementById('expList').innerHTML='<div style="color:#ff8a80;padding:20px">加载失败</div>'}
}
async function expGo(path){
  if(!path)return;path=path.replace(/\//g,'\\');
  if(path.length===2&&path[1]===':')path+='\\';
  document.getElementById('expStatus').textContent='加载中...';
  try{
    const r=await fetch('/api/files?p='+encodeURIComponent(path));
    const d=await r.json();
    if(d.error){alert(d.error);document.getElementById('expStatus').textContent='错误';return}
    expCurPath=d.path;expAllItems=d.items;
    document.getElementById('expAddr').value=d.path;
    document.getElementById('expSearch').value='';
    if(expIdx<0||expHistory[expIdx]!==d.path){expHistory=expHistory.slice(0,expIdx+1);expHistory.push(d.path);expIdx=expHistory.length-1;}
    expRender(d.items);document.getElementById('expStatus').textContent='就绪';
  }catch(e){document.getElementById('expStatus').textContent='网络错误'}
}
function expRender(items){
  const c=document.getElementById('expList');let h='',dirs=0,files=0;
  items.forEach(i=>{
    h+='<div class="exp-item" data-name="'+i.name+'" data-dir="'+i.dir+'" data-path="'+expCurPath.replace(/\\$/,'')+'\\'+i.name+'" onclick="expDblClick(this)" oncontextmenu="expCtx(event,this)" ontouchstart="expLongPress(event,this)" ontouchend="expCancelPress()" ontouchmove="expCancelPress()">';
    h+='<span class="exp-icon">'+getIcon(i.name,i.dir)+'</span>';
    h+='<span class="exp-name">'+i.name+'</span>';
    h+='<span class="exp-size">'+(i.dir?'':fmtSize(i.size))+'</span>';
    h+='<span class="exp-time">'+i.time+'</span></div>';
    if(i.dir)dirs++;else files++;
  });
  if(!items.length)h='<div style="padding:20px;text-align:center;color:#888">空文件夹</div>';
  c.innerHTML=h;document.getElementById('expCount').textContent=dirs+' 个文件夹, '+files+' 个文件';
}
function expDblClick(el){if(el.dataset.dir==='true')expGo(expCurPath.replace(/\\$/,'')+'\\'+el.dataset.name);}
function expBack(){if(expIdx>0){expIdx--;expGo(expHistory[expIdx])}}
function expForward(){if(expIdx<expHistory.length-1){expIdx++;expGo(expHistory[expIdx])}}
function expHome(){expGo('C:\\')}
function expUp(){const p=expCurPath.replace(/\\$/,'').split('\\');if(p.length>1){p.pop();expGo(p.join('\\')||p[0]+'\\');}}
function expFilter(q){q=q.toLowerCase();expRender(q?expAllItems.filter(i=>i.name.toLowerCase().includes(q)):expAllItems);}
let ctxItem=null,expPressTimer=null,expPressTarget=null;
function expLongPress(e,el){expPressTarget=el;el.style.background='rgba(105,240,174,.08)';expPressTimer=setTimeout(()=>{el.style.background='rgba(105,240,174,.15)';if(el.dataset.dir!=='true'&&confirm('下载 '+el.dataset.name+' ?')){window.open('/api/download?p='+encodeURIComponent(el.dataset.path));}el.style.background='';expPressTarget=null;},600);}
function expCancelPress(){if(expPressTimer){clearTimeout(expPressTimer);expPressTimer=null;}if(expPressTarget){expPressTarget.style.background='';expPressTarget=null;}}
function expCtx(e,el){e.preventDefault();ctxItem=el;el.classList.add('selected');const m=document.getElementById('expContext');m.classList.add('visible');m.style.left=Math.min(e.clientX,window.innerWidth-180)+'px';m.style.top=Math.min(e.clientY,window.innerHeight-200)+'px';}
document.addEventListener('click',()=>{document.getElementById('expContext').classList.remove('visible');document.querySelectorAll('.exp-item.selected').forEach(e=>e.classList.remove('selected'))});
function ctxOpen(){if(ctxItem)expDblClick(ctxItem)}
function ctxDownload(){if(!ctxItem||ctxItem.dataset.dir==='true')return;window.open('/api/download?p='+encodeURIComponent(ctxItem.dataset.path));}
function ctxNewFolder(){const n=prompt('新建文件夹名称:');if(!n)return;fetch('/api/mkdir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:expCurPath.replace(/\\$/,'')+'\\'+n})}).then(r=>r.json()).then(d=>{if(d.ok)expGo(expCurPath);else alert(d.error)});}
function ctxRename(){if(!ctxItem)return;const o=ctxItem.dataset.name;const n=prompt('重命名:',o);if(!n||n===o)return;fetch('/api/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({old:ctxItem.dataset.path,new:expCurPath.replace(/\\$/,'')+'\\'+n})}).then(r=>r.json()).then(d=>{if(d.ok)expGo(expCurPath);else alert(d.error)});}
function ctxDelete(){if(!ctxItem)return;if(!confirm('确定删除 '+ctxItem.dataset.name+' ?'))return;fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:ctxItem.dataset.path})}).then(r=>r.json()).then(d=>{if(d.ok)expGo(expCurPath);else alert(d.error)});}

// Process Manager
let allProcs=[],procCtxItem=null,procPressTimer=null,procPressTarget=null;
async function refreshProcs(){
  try{
    const ctrl=new AbortController();
    const tid=setTimeout(()=>ctrl.abort(),8000);
    const resp=await fetch('/api/processes',{signal:ctrl.signal});
    clearTimeout(tid);
    const data=await resp.json();
    if(data.procs){allProcs=data.procs;renderProcs(allProcs);}
  }catch(e){document.getElementById('procCount').textContent='加载超时，点击刷新';}
}
function renderProcs(procs){
  const c=document.getElementById('procList');let h='';
  procs.forEach(p=>{
    h+='<div class="pr" data-pid="'+p.pid+'" data-name="'+p.name+'" oncontextmenu="procCtx(event,this)" ontouchstart="procLongPress(event,this)" ontouchend="procCancelPress()" ontouchmove="procCancelPress()" style="cursor:pointer">';
    h+='<span class="pn">'+p.name+'</span>';
    h+='<span class="pc">'+(p.cpu?p.cpu.toFixed(1)+'s':'-')+'</span>';
    h+='<span class="pm">'+p.mem+' MB</span>';
    h+='<span style="width:50px;text-align:right;color:#888">'+p.pid+'</span></div>';
  });
  c.innerHTML=h;document.getElementById('procCount').textContent='共 '+procs.length+' 个进程';
}
function filterProcs(q){q=q.toLowerCase();renderProcs(q?allProcs.filter(p=>p.name.toLowerCase().includes(q)):allProcs);}
function procLongPress(e,el){e.preventDefault();procPressTarget=el;procPressTimer=setTimeout(()=>{el.style.background='rgba(105,240,174,.15)';procCtxItem=el;showProcMenu(e.touches?e.touches[0].clientX:0,e.touches?e.touches[0].clientY:0);},500);}
function procCancelPress(){if(procPressTimer){clearTimeout(procPressTimer);procPressTimer=null;}if(procPressTarget){procPressTarget.style.background='';procPressTarget=null;}}
function procCtx(e,el){e.preventDefault();procCtxItem=el;el.style.background='rgba(105,240,174,.15)';showProcMenu(e.clientX,e.clientY);}
function showProcMenu(x,y){const m=document.getElementById('procContext');m.classList.add('visible');m.style.left=Math.min(x,window.innerWidth-180)+'px';m.style.top=Math.min(y,window.innerHeight-100)+'px';}
document.addEventListener('click',()=>{document.getElementById('procContext').classList.remove('visible');document.querySelectorAll('.pr[style*="background"]').forEach(e=>e.style.background='');});
async function procKill(){
  if(!procCtxItem)return;const pid=procCtxItem.dataset.pid,name=procCtxItem.dataset.name;
  if(!confirm('确定结束 '+name+' (PID:'+pid+')?'))return;
  const r=await fetch('/api/kill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pid:parseInt(pid)})});
  const d=await r.json();if(d.ok)refreshProcs();else alert(d.error);
}
async function procForceKill(){
  if(!procCtxItem)return;const pid=procCtxItem.dataset.pid,name=procCtxItem.dataset.name;
  if(!confirm('强制结束 '+name+'?'))return;
  fetch('/api/terminal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd:'Stop-Process -Id '+pid+' -Force'})}).then(()=>refreshProcs());
}

// Built-in CC-Web
let ccLoaded=false,ccSessions=[],ccCurrent=null,ccBusy=false;
function ccEsc(s){return String(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
async function ccJson(url,opt){
  const r=await fetch(url,opt);const d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(d.error||r.statusText);
  return d;
}
async function ccLoad(){
  if(ccLoaded)return;ccLoaded=true;
  try{
    const d=await ccJson('/api/ccweb/sessions');
    ccSessions=d.sessions||[];
    if(ccSessions.length){ccSelect(ccSessions[0].id);}
    else{await ccNewSession();}
  }catch(e){document.getElementById('ccState').textContent='加载失败';ccSystem(e.message);}
}
function ccSystem(msg){
  const box=document.getElementById('ccMessages');
  box.insertAdjacentHTML('beforeend','<div class="cc-msg system">'+ccEsc(msg)+'</div>');
  box.scrollTop=box.scrollHeight;
}
function ccRenderSessions(){
  const list=document.getElementById('ccSessionList');let h='';
  ccSessions.forEach(s=>{
    h+='<button class="cc-session '+(ccCurrent&&ccCurrent.id===s.id?'active':'')+'" data-id="'+ccEsc(s.id)+'" onclick="ccSelect(this.dataset.id)"><b>'+ccEsc(s.title||'新会话')+'</b><span>'+ccEsc((s.agent||'codex')+' · '+(s.updated||''))+'</span></button>';
  });
  list.innerHTML=h||'<div style="padding:12px;color:#888;font-size:12px">暂无会话</div>';
}
function ccRenderMessages(){
  const box=document.getElementById('ccMessages');
  if(!ccCurrent||!ccCurrent.messages||!ccCurrent.messages.length){
    box.innerHTML='<div class="cc-msg system">选择 Claude 或 Codex，输入任务后发送。会话保存在本项目 ccweb_sessions 目录。</div>';
  }else{
    box.innerHTML=ccCurrent.messages.map(m=>'<div class="cc-msg '+ccEsc(m.role)+'">'+ccEsc(m.content)+'</div>').join('');
  }
  box.scrollTop=box.scrollHeight;
}
async function ccSelect(id){
  const s=ccSessions.find(x=>x.id===id);if(!s)return;
  ccCurrent=s;
  document.getElementById('ccAgent').value=s.agent||'codex';
  document.getElementById('ccMode').value=s.mode||'yolo';
  document.getElementById('ccCwd').value=s.cwd||'';
  document.getElementById('ccState').textContent='就绪';
  ccRenderSessions();ccRenderMessages();
}
async function ccNewSession(){
  const agent=document.getElementById('ccAgent')?.value||'codex';
  const mode=document.getElementById('ccMode')?.value||'yolo';
  const cwd=document.getElementById('ccCwd')?.value||'';
  const d=await ccJson('/api/ccweb/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent,mode,cwd})});
  ccSessions=[d.session].concat(ccSessions);
  ccSelect(d.session.id);
}
async function ccSend(){
  if(ccBusy)return;
  if(!ccCurrent)await ccNewSession();
  const inp=document.getElementById('ccInput');
  const text=inp.value.trim();if(!text)return;
  inp.value='';ccBusy=true;
  document.getElementById('ccSend').disabled=true;
  document.getElementById('ccState').textContent='运行中...';
  try{
    const d=await ccJson('/api/ccweb/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:ccCurrent.id,text,agent:document.getElementById('ccAgent').value,mode:document.getElementById('ccMode').value,cwd:document.getElementById('ccCwd').value})});
    ccCurrent=d.session;
    ccSessions=ccSessions.filter(s=>s.id!==ccCurrent.id);
    ccSessions.unshift(ccCurrent);
    ccRenderSessions();ccRenderMessages();
    document.getElementById('ccState').textContent=d.ok?'完成':'有错误';
  }catch(e){
    ccSystem('错误: '+e.message);
    document.getElementById('ccState').textContent='错误';
  }finally{
    ccBusy=false;document.getElementById('ccSend').disabled=false;
  }
}
document.getElementById('ccInput').addEventListener('keydown',function(e){if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();ccSend();}});
setInterval(()=>{if(!document.hidden)refreshProcs();},10000);
setTimeout(refreshProcs,2000);

</script>
</body>
</html>"""

def bc(p):
    return "g" if p<60 else "y" if p<85 else "r"

def render_disks(disks):
    h=""
    for d in disks:
        c=bc(d["percent"])
        h+=f'<div class="dr"><span class="dd">{d["drive"]}</span><div class="db"><div class="bw"><div class="bf {c}" style="width:{d["percent"]}%"></div></div></div><div style="min-width:100px;text-align:right"><div class="di">{d["used"]} / {d["total"]} GB</div><div class="df">Free: {d["free"]} GB</div></div></div>'
    return h

def render_procs(procs):
    h=""
    for p in procs:
        h+=f'<div class="pr"><span class="pn">{p["name"]}</span><span class="pc">{p["cpu"]:.1f}s</span><span class="pm">{p["mem_mb"]:.0f} MB</span></div>'
    return h

def build_html(s):
    cpu=s["cpu"]; m=s["mem"]
    return HTML.replace("{{HOSTNAME}}", s["net"]["hostname"]) \
        .replace("{{IP}}", s["net"]["ip"]) \
        .replace("{{UPTIME}}", "Uptime: "+s["uptime"]) \
        .replace("{{CPU}}", str(cpu)) \
        .replace("{{CPU_C}}", "#69f0ae" if cpu<60 else "#ffeb3b" if cpu<85 else "#ff8a80") \
        .replace("{{CPU_B}}", bc(cpu)) \
        .replace("{{MEM_P}}", str(m["percent"])) \
        .replace("{{MEM_C}}", "#69f0ae" if m["percent"]<60 else "#ffeb3b" if m["percent"]<85 else "#ff8a80") \
        .replace("{{MEM_B}}", bc(m["percent"])) \
        .replace("{{MEM_U}}", str(m["used"])) \
        .replace("{{MEM_T}}", str(m["total"])) \
        .replace("{{DISKS}}", render_disks(s["disks"])) \
        .replace("{{PROC}}", str(s["procs"])) \
        .replace("{{GPU}}", s["gpu"]) \
        .replace("{{PROCS}}", render_procs(s["top"])) \
        .replace("{{TIMESTAMP}}", s["ts"])

# ─── HTTP ───

# 简单的登录会话管理
logged_in_ips = {}
SESSION_TIMEOUT = 3600  # 1小时超时


class Handler(BaseHTTPRequestHandler):
    def get_client_ip(self):
        return self.client_address[0]

    def is_logged_in(self):
        ip = self.get_client_ip()
        if ip in logged_in_ips:
            if time.time() - logged_in_ips[ip] < SESSION_TIMEOUT:
                return True
            else:
                del logged_in_ips[ip]
        return False

    def do_login(self):
        ip = self.get_client_ip()
        logged_in_ips[ip] = time.time()

    def check_auth(self):
        """检查是否已认证，未认证则返回登录页面"""
        if self.is_logged_in():
            return True

        # 显示登录页面
        login_html = '''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#0a0a1a;color:#e0e0e0;display:flex;justify-content:center;align-items:center;min-height:100vh;overflow:hidden}
body::before{content:'';position:fixed;inset:-50%;width:200%;height:200%;background:radial-gradient(circle at 30% 40%,rgba(102,126,234,.15),transparent 50%),radial-gradient(circle at 70% 60%,rgba(118,75,162,.12),transparent 50%),radial-gradient(circle at 50% 20%,rgba(105,240,174,.08),transparent 40%);animation:meshMove 20s ease-in-out infinite;z-index:-2}
body::after{content:'';position:fixed;inset:0;box-shadow:120px 80px 0 .5px rgba(105,240,174,.3),350px 150px 0 .5px rgba(102,126,234,.25),600px 50px 0 .5px rgba(118,75,162,.2),200px 300px 0 .5px rgba(105,240,174,.15),500px 350px 0 .5px rgba(102,126,234,.2),80px 400px 0 .5px rgba(118,75,162,.15),700px 200px 0 .5px rgba(105,240,174,.2),400px 450px 0 .5px rgba(102,126,234,.15),300px 100px 0 .5px rgba(255,255,255,.1);animation:floatParticles 30s linear infinite;z-index:-1;pointer-events:none}
@keyframes meshMove{0%,100%{transform:translate(0,0)}25%{transform:translate(3%,-2%)}50%{transform:translate(-2%,3%)}75%{transform:translate(2%,1%)}}
@keyframes floatParticles{0%{transform:translateY(0)}100%{transform:translateY(-50px)}}
@keyframes loginBoxIn{0%{opacity:0;transform:translateY(30px) scale(.95)}100%{opacity:1;transform:translateY(0) scale(1)}}
@keyframes loginGlow{0%,100%{box-shadow:0 0 20px rgba(102,126,234,.2),0 0 40px rgba(118,75,162,.1)}50%{box-shadow:0 0 30px rgba(102,126,234,.3),0 0 60px rgba(118,75,162,.2)}}
.login-box{background:rgba(255,255,255,.03);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,.08);border-radius:20px;padding:40px 32px;width:340px;text-align:center;animation:loginBoxIn .6s ease both;box-shadow:0 8px 32px rgba(0,0,0,.3),inset 0 1px 0 rgba(255,255,255,.05)}
.login-box h1{font-size:28px;margin-bottom:6px;background:linear-gradient(135deg,#69f0ae,#667eea,#764ba2);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;font-weight:700}
.login-box p{font-size:13px;color:#888;margin-bottom:28px}
.login-box input{width:100%;padding:14px 16px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:12px;color:#fff;font-size:16px;outline:none;margin-bottom:20px;transition:border-color .3s,box-shadow .3s}
.login-box input:focus{border-color:rgba(105,240,174,.5);box-shadow:0 0 0 3px rgba(105,240,174,.15),0 0 20px rgba(105,240,174,.1)}
.login-box input::placeholder{color:#555}
.login-box button{width:100%;padding:14px;background:linear-gradient(135deg,#667eea,#764ba2);border:none;border-radius:12px;color:#fff;font-size:16px;font-weight:600;cursor:pointer;transition:transform .2s,box-shadow .3s;animation:loginGlow 3s ease-in-out infinite}
.login-box button:active{transform:scale(.96)}
.error{color:#ff8a80;font-size:13px;margin-bottom:14px;display:none;animation:loginBoxIn .3s ease}
</style>
</head>
<body>
<div class="login-box">
  <h1>PC Monitor</h1>
  <p>请输入密码访问</p>
  <div class="error" id="err">密码错误</div>
  <input type="password" id="pwd" placeholder="密码" autofocus>
  <button onclick="login()">登录</button>
</div>
<script>
document.getElementById("pwd").addEventListener("keydown",function(e){if(e.key=="Enter")login()});
function login(){
  var pwd=document.getElementById("pwd").value;
  fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:pwd})})
  .then(r=>r.json()).then(d=>{
    if(d.ok)location.reload();
    else{var e=document.getElementById("err");e.style.display="block";e.textContent="密码错误";document.getElementById("pwd").value="";document.getElementById("pwd").focus()}
  }).catch(()=>{var e=document.getElementById("err");e.style.display="block";e.textContent="网络错误"})
}
</script>
</body>
</html>'''
        self.respond(200, "text/html", login_html.encode("utf-8"))
        return False

    def do_GET(self):
        # 登录API不需要认证
        p = urlparse(self.path)
        path = p.path
        qs = parse_qs(p.query)

        # 登录页面始终可访问
        if path in ("/", "/status"):
            if not self.check_auth():
                return

        # API也需要认证
        if path.startswith("/api/") and path != "/api/login":
            if not self.is_logged_in():
                self.json_resp({"error": "Unauthorized"}, 401)
                return

        if path in ("/", "/status"):
            self.respond(200, "text/html", build_html(get_all_status()).encode("utf-8"))
        elif path == "/api":
            self.json_resp(get_all_status())
        elif path == "/api/screenshot":
            img = take_screenshot()
            self.json_resp({"ok": bool(img), "image": img or ""})
        elif path == "/api/drives":
            self.json_resp({"drives": get_drives()})
        elif path == "/font.ttf":
            try:
                fp = os.path.join(os.path.dirname(__file__), "font.ttf")
                with open(fp, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "font/ttf")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            except:
                self.send_error(404)
        elif path == "/api/files":
            # 接收正斜杠或反斜杠路径，统一转成 Windows 路径
            raw = qs.get("p", ["C:\\"])[0]
            pth = unquote(raw).replace("/", "\\")
            if len(pth) == 2 and pth[1] == ":":
                pth += "\\"
            items, err = list_directory(pth)
            if err:
                self.json_resp({"error": err, "path": pth, "items": []})
            else:
                self.json_resp({"path": pth, "items": items})
        elif path == "/api/download":
            raw = qs.get("p", [""])[0]
            pth = unquote(raw).replace("/", "\\")
            if os.path.isfile(pth):
                try:
                    with open(pth, "rb") as f:
                        data = f.read()
                    import mimetypes
                    mime = mimetypes.guess_type(pth)[0] or "application/octet-stream"
                    filename = os.path.basename(pth)
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                    self.send_header("Content-Length", len(data))
                    self.end_headers()
                    self.wfile.write(data)
                except Exception as e:
                    self.send_error(500, str(e))
            else:
                self.send_error(404)
            return
        elif path == "/api/processes":
            # 使用 psutil 快速获取进程列表
            if HAS_PSUTIL:
                try:
                    procs = []
                    for p in psutil.process_iter(['pid', 'name', 'cpu_times', 'memory_info']):
                        try:
                            info = p.info
                            cpu = sum(info['cpu_times'][:2]) if info['cpu_times'] else 0
                            mem = round(info['memory_info'].rss / 1048576) if info['memory_info'] else 0
                            procs.append({'pid': info['pid'], 'name': info['name'] or 'N/A', 'cpu': round(cpu, 1), 'mem': mem})
                        except:
                            continue
                    procs.sort(key=lambda x: x['cpu'], reverse=True)
                    self.json_resp({'procs': procs})
                except Exception as e:
                    self.json_resp({'error': str(e)})
            else:
                self.json_resp({'error': 'psutil not available'})
            return
        elif path == "/api/keepalive":
            s = qs.get("set", [None])[0]
            if s == "on": set_keep_screen_alive(True)
            elif s == "off": set_keep_screen_alive(False)
            self.json_resp({"on": keep_screen_alive})
        elif path == "/api/ccweb/sessions":
            self.json_resp({"sessions": cc_list_sessions()})
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        
        # 登录接口不需要认证
        if path == "/api/login":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body)
                pwd = d.get("password", "")
                if pwd == PASSWORD:
                    self.do_login()
                    self.json_resp({"ok": True})
                else:
                    self.json_resp({"ok": False, "error": "密码错误"})
            except:
                self.json_resp({"ok": False, "error": "请求错误"})
            return

        # 其他POST请求需要认证
        if not self.is_logged_in():
            self.json_resp({"error": "Unauthorized"}, 401)
            return

        if path == "/api/ccweb/sessions":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body or b"{}")
                session = cc_create_session(d.get("agent", "codex"), d.get("cwd", ""), d.get("mode", "yolo"))
                self.json_resp({"ok": True, "session": cc_public_session(session)})
            except Exception as e:
                self.json_resp({"ok": False, "error": str(e)}, 500)
            return
        elif path == "/api/ccweb/send":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body or b"{}")
                text = (d.get("text") or "").strip()
                if not text:
                    self.json_resp({"ok": False, "error": "消息不能为空"}, 400)
                    return
                session = cc_load_session(d.get("session_id")) or cc_create_session(d.get("agent", "codex"), d.get("cwd", ""), d.get("mode", "yolo"))
                session["agent"] = d.get("agent") if d.get("agent") in ("codex", "claude") else session.get("agent", "codex")
                session["mode"] = d.get("mode") if d.get("mode") in ("yolo", "default", "plan") else session.get("mode", "yolo")
                if d.get("cwd") and os.path.isdir(d.get("cwd")):
                    session["cwd"] = os.path.abspath(d.get("cwd"))
                if session.get("title") == "新会话":
                    session["title"] = text[:28] + ("..." if len(text) > 28 else "")

                lower = text.lower()
                session.setdefault("messages", []).append({"role": "user", "content": text, "time": cc_now()})
                if lower == "/clear":
                    session["messages"] = [{"role": "system", "content": "会话已清空。", "time": cc_now()}]
                    result = {"ok": True, "output": "会话已清空。"}
                elif lower == "/help":
                    result = {"ok": True, "output": "内置 CC-Web 命令：/clear 清空会话，/cwd 查看目录，/cwd <路径> 切换目录，/mode <yolo|default|plan> 切换权限模式。普通消息会发送给当前 Claude/Codex CLI。"}
                elif lower.startswith("/cwd"):
                    new_cwd = text[4:].strip()
                    if new_cwd:
                        if os.path.isdir(new_cwd):
                            session["cwd"] = os.path.abspath(new_cwd)
                            result = {"ok": True, "output": "工作目录已切换为: " + session["cwd"]}
                        else:
                            result = {"ok": False, "error": "目录不存在: " + new_cwd}
                    else:
                        result = {"ok": True, "output": "当前工作目录: " + (session.get("cwd") or "")}
                elif lower.startswith("/mode"):
                    mode = text[5:].strip().lower()
                    if mode in ("yolo", "default", "plan"):
                        session["mode"] = mode
                        result = {"ok": True, "output": "权限模式已切换为: " + mode}
                    else:
                        result = {"ok": False, "error": "用法: /mode <yolo|default|plan>"}
                else:
                    result = cc_run_agent(session, text)

                content = result.get("output") or result.get("error") or "(no output)"
                session["messages"].append({"role": "assistant" if result.get("ok") else "system", "content": content, "time": cc_now()})
                session["updated"] = cc_iso()
                cc_save_session(session)
                self.json_resp({"ok": bool(result.get("ok")), "session": cc_public_session(session), "error": result.get("error")})
            except Exception as e:
                self.json_resp({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/mkdir":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body)
                pth = d.get("path", "")
                if not pth:
                    self.json_resp({"ok": False, "error": "路径不能为空"})
                    return
                os.makedirs(pth, exist_ok=True)
                self.json_resp({"ok": True})
            except Exception as e:
                self.json_resp({"ok": False, "error": str(e)})
            return
        elif path == "/api/rename":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body)
                old_path = d.get("old", "")
                new_path = d.get("new", "")
                if not old_path or not new_path:
                    self.json_resp({"ok": False, "error": "路径不能为空"})
                    return
                os.rename(old_path, new_path)
                self.json_resp({"ok": True})
            except Exception as e:
                self.json_resp({"ok": False, "error": str(e)})
            return
        elif path == "/api/delete":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body)
                pth = d.get("path", "")
                if not pth:
                    self.json_resp({"ok": False, "error": "路径不能为空"})
                    return
                import shutil
                if os.path.isdir(pth):
                    shutil.rmtree(pth)
                else:
                    os.remove(pth)
                self.json_resp({"ok": True})
            except Exception as e:
                self.json_resp({"ok": False, "error": str(e)})
            return
        elif path == "/api/kill":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body)
                pid = d.get("pid", 0)
                if not pid:
                    self.json_resp({"ok": False, "error": "PID不能为空"})
                    return
                import signal
                os.kill(pid, signal.SIGTERM)
                self.json_resp({"ok": True})
            except ProcessLookupError:
                self.json_resp({"ok": False, "error": "进程不存在"})
            except PermissionError:
                self.json_resp({"ok": False, "error": "权限不足"})
            except Exception as e:
                self.json_resp({"ok": False, "error": str(e)})
            return
        elif path == "/api/terminal":
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            try:
                d = json.loads(body)
                cmd = d.get("cmd", "")
                if not cmd:
                    self.json_resp({"error": "No command"})
                    return
                if any(f in cmd.lower() for f in ["remove-item","rmdir","del ","format ","shutdown","restart-computer"]):
                    self.json_resp({"error": "Blocked"})
                    return
                r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
                out = r.stdout
                if r.stderr: out += ("\n" if out else "") + r.stderr
                self.json_resp({"output": out or "(no output)"})
            except subprocess.TimeoutExpired:
                self.json_resp({"error": "Timeout"})
            except Exception as e:
                self.json_resp({"error": str(e)})
        else:
            self.send_error(404)

    def json_resp(self, d, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(json.dumps(d, ensure_ascii=False).encode("utf-8"))

    def respond(self, code, ct, body):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]}")

def main():
    net = get_network_info()
    set_keep_screen_alive(True)
    print(f"\n  PC Monitor: http://{net['ip']}:{PORT}\n")
    print("  Built-in CC-Web merged at: / -> CC-Web tab\n")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        set_keep_screen_alive(False)
        print("\nStopped")
        server.server_close()

if __name__ == "__main__":
    main()
