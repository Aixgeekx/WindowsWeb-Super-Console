#!/usr/bin/env python3
"""
内网电脑状态监控
用法: python server.py [端口号，默认9999]
"""

import subprocess
import socket
import os
import time
import json
import html
import mimetypes
import shutil
import threading
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
STATUS_CACHE_TTL = 2.5
GPU_CACHE_TTL = 300
MAX_DIR_ITEMS = 1200

cache = {}
cache_time = 0
cache_lock = threading.Lock()
gpu_cache = {"value": None, "time": 0}

# ─── 系统信息采集 ───

def run_ps(cmd):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace"
        )
        return r.stdout.strip()
    except Exception as e:
        return f"Error: {e}"

def gb(value):
    return round(value / 1073741824, 2)

def get_cpu_usage():
    if HAS_PSUTIL:
        try:
            return round(psutil.cpu_percent(interval=0.05), 1)
        except:
            pass
    out = run_ps("(Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples.CookedValue")
    try: return round(float(out), 1)
    except: return 0

def get_memory_info():
    if HAS_PSUTIL:
        try:
            mem = psutil.virtual_memory()
            return {"used": gb(mem.used), "total": gb(mem.total), "free": gb(mem.available), "percent": round(mem.percent, 1)}
        except:
            pass
    out = run_ps("""
        $os = Get-CimInstance Win32_OperatingSystem
        $total = [math]::Round($os.TotalVisibleMemorySize/1MB, 2)
        $free = [math]::Round($os.FreePhysicalMemory/1MB, 2)
        $used = [math]::Round($total - $free, 2)
        $pct = [math]::Round($used / $total * 100, 1)
        "$used|$total|$free|$pct"
    """)
    try:
        parts = out.split("|")
        return {"used": float(parts[0]), "total": float(parts[1]), "free": float(parts[2]), "percent": float(parts[3])}
    except:
        return {"used": 0, "total": 0, "free": 0, "percent": 0}

def get_disk_info():
    if hasattr(os, "listdrives"):
        disks = []
        for drive_path in os.listdrives():
            try:
                usage = shutil.disk_usage(drive_path)
            except:
                continue
            disks.append({
                "drive": drive_path.rstrip("\\/") or drive_path,
                "used": round(usage.used / 1073741824, 1),
                "total": round(usage.total / 1073741824, 1),
                "free": round(usage.free / 1073741824, 1),
                "percent": round((usage.used / usage.total * 100) if usage.total else 0, 1)
            })
        if disks:
            return disks
    if HAS_PSUTIL:
        disks = []
        seen = set()
        for part in psutil.disk_partitions(all=False):
            mount = part.mountpoint
            if not mount or mount in seen:
                continue
            seen.add(mount)
            try:
                usage = psutil.disk_usage(mount)
            except:
                continue
            drive = mount.rstrip("\\/") or mount
            disks.append({
                "drive": drive,
                "used": round(usage.used / 1073741824, 1),
                "total": round(usage.total / 1073741824, 1),
                "free": round(usage.free / 1073741824, 1),
                "percent": round(usage.percent, 1)
            })
        if disks:
            return disks
    out = run_ps("""
        Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | ForEach-Object {
            $total = [math]::Round($_.Size/1GB, 1)
            $free = [math]::Round($_.FreeSpace/1GB, 1)
            $used = [math]::Round($total - $free, 1)
            $pct = if($total -gt 0){[math]::Round($used/$total*100,1)}else{0}
            "$($_.DeviceID)|$used|$total|$free|$pct"
        }
    """)
    disks = []
    for line in out.split("\n"):
        line = line.strip()
        if "|" in line:
            parts = line.split("|")
            if len(parts) == 5:
                disks.append({"drive": parts[0], "used": float(parts[1]), "total": float(parts[2]), "free": float(parts[3]), "percent": float(parts[4])})
    return disks

def get_uptime():
    if HAS_PSUTIL:
        try:
            span = time.time() - psutil.boot_time()
            days = int(span // 86400)
            hours = int((span % 86400) // 3600)
            mins = int((span % 3600) // 60)
            return f"{days}d {hours}h {mins}m"
        except:
            pass
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
    if HAS_PSUTIL:
        try:
            return len(psutil.pids())
        except:
            pass
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
    if HAS_PSUTIL:
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_times"]):
            try:
                info = p.info
                cpu = sum(info["cpu_times"][:2]) if info["cpu_times"] else 0
                mem = info["memory_info"].rss / 1048576 if info["memory_info"] else 0
                procs.append({"name": info["name"] or "N/A", "cpu": round(cpu, 1), "mem_mb": round(mem, 1)})
            except:
                continue
        procs.sort(key=lambda x: x["cpu"], reverse=True)
        return procs[:n]
    out = run_ps(f"""
        Get-Process | Sort-Object CPU -Descending | Select-Object -First {n} Name, CPU, WorkingSet64 |
        ForEach-Object {{
            $mem = [math]::Round($_.WorkingSet64/1MB, 1)
            "$($_.Name)|$([math]::Round($_.CPU,1))|$mem"
        }}
    """)
    procs = []
    for line in out.split("\n"):
        line = line.strip()
        if "|" in line:
            parts = line.split("|")
            if len(parts) == 3:
                procs.append({"name": parts[0], "cpu": float(parts[1]) if parts[1] else 0, "mem_mb": float(parts[2]) if parts[2] else 0})
    return procs

def get_gpu_info():
    now = time.time()
    if gpu_cache["value"] and now - gpu_cache["time"] < GPU_CACHE_TTL:
        return gpu_cache["value"]
    out = run_ps('''
        try {
            $gpus = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notmatch 'Oray|Remote|Virtual|Display|Basic' }
            if ($gpus) { $gpus[0].Name } else { (Get-CimInstance Win32_VideoController | Select-Object -First 1).Name }
        } catch { "N/A" }
    ''')
    gpu_cache["value"] = out if out else "N/A"
    gpu_cache["time"] = now
    return gpu_cache["value"]

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
public static class NativeScreen {
    [DllImport("user32.dll")] public static extern int GetSystemMetrics(int nIndex);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr dpiContext);
}
"@
    try { [NativeScreen]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null } catch { try { [NativeScreen]::SetProcessDPIAware() | Out-Null } catch {} }
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName System.Windows.Forms
    $x = [NativeScreen]::GetSystemMetrics(76)
    $y = [NativeScreen]::GetSystemMetrics(77)
    $w = [NativeScreen]::GetSystemMetrics(78)
    $h = [NativeScreen]::GetSystemMetrics(79)
    if ($w -le 0 -or $h -le 0) {
        $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $x = $b.Left; $y = $b.Top; $w = $b.Width; $h = $b.Height
    }
    $bmp = New-Object System.Drawing.Bitmap($w, $h, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($x, $y, 0, 0, [System.Drawing.Size]::new($w, $h), [System.Drawing.CopyPixelOperation]::SourceCopy)
    $ms = New-Object System.IO.MemoryStream
    $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq "image/jpeg" } | Select-Object -First 1
    $enc = New-Object System.Drawing.Imaging.EncoderParameters(1)
    $enc.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, 88L)
    $bmp.Save($ms, $codec, $enc)
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    @{ image = [Convert]::ToBase64String($ms.ToArray()); width = $w; height = $h; left = $x; top = $y } | ConvertTo-Json -Compress
    $g.Dispose(); $bmp.Dispose(); $ms.Dispose()
    '''
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            out = r.stdout.strip()
            try:
                return json.loads(out)
            except:
                return {"image": out, "width": 0, "height": 0, "left": 0, "top": 0}
    except Exception as e:
        return {"error": str(e)}
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
            drives.append({"letter": letter, "used": round(usage.used / 1073741824, 1), "free": round(usage.free / 1073741824, 1)})
        if drives:
            return drives
    if HAS_PSUTIL:
        drives = []
        seen = set()
        for part in psutil.disk_partitions(all=False):
            mount = part.mountpoint
            if not mount or mount in seen:
                continue
            seen.add(mount)
            try:
                usage = psutil.disk_usage(mount)
            except:
                continue
            letter = mount[:1].upper() if len(mount) >= 2 and mount[1] == ":" else mount.rstrip("\\/")
            drives.append({"letter": letter, "used": round(usage.used / 1073741824, 1), "free": round(usage.free / 1073741824, 1)})
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
        return None, f"Not found: {path}", False
    items = []
    truncated = False
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if len(items) >= MAX_DIR_ITEMS:
                    truncated = True
                    break
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
        return [], "Access denied", False
    except Exception as e:
        return [], str(e), False
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return items, None, truncated

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

def get_all_status():
    global cache, cache_time
    now = time.time()
    if now - cache_time < STATUS_CACHE_TTL and cache:
        return cache
    with cache_lock:
        now = time.time()
        if now - cache_time < STATUS_CACHE_TTL and cache:
            return cache
        net = get_network_info()
        mem = get_memory_info()
        cache = {
            "cpu": get_cpu_usage(), "mem": mem, "disks": get_disk_info(),
            "uptime": get_uptime(), "procs": get_process_count(),
            "net": net, "gpu": get_gpu_info(), "top": get_top_processes(5),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        cache_time = now
        return cache

def get_processes():
    procs = []
    if HAS_PSUTIL:
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = p.info
                mem = round(info["memory_info"].rss / 1048576) if info["memory_info"] else 0
                cpu = p.cpu_percent(interval=None)
                procs.append({"pid": info["pid"], "name": info["name"] or "N/A", "cpu": round(cpu, 1), "mem": mem})
            except:
                continue
        procs.sort(key=lambda x: (x["cpu"], x["mem"]), reverse=True)
        return procs, "%"

    out = run_ps("""
        Get-Process | Select-Object Id,ProcessName,CPU,WorkingSet64 |
        ConvertTo-Json -Depth 3 -Compress
    """)
    try:
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
        for p in data:
            mem = round((p.get("WorkingSet64") or 0) / 1048576)
            cpu = round(float(p.get("CPU") or 0), 1)
            procs.append({"pid": p.get("Id"), "name": p.get("ProcessName") or "N/A", "cpu": cpu, "mem": mem})
        procs.sort(key=lambda x: (x["cpu"], x["mem"]), reverse=True)
        return procs, "s"
    except Exception:
        return [], "s"

# ─── HTML ───

HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>PC Monitor</title>
<style>
@font-face{font-family:'Maple';src:url('/font.ttf') format('truetype');font-weight:normal;font-style:normal}
:root{--bg:#07111f;--panel:rgba(15,24,38,.74);--panel2:rgba(255,255,255,.055);--line:rgba(255,255,255,.1);--text:#e8edf7;--muted:#8f9aac;--good:#4ade80;--warn:#facc15;--bad:#fb7185;--info:#38bdf8;--violet:#a78bfa}
*{margin:0;padding:0;box-sizing:border-box}
html{background:#07111f}
body{font-family:'Maple',-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh;background:linear-gradient(140deg,#07111f 0%,#101828 48%,#17111f 100%);color:var(--text);padding:16px;padding-bottom:78px;overflow-x:hidden}
body:before{content:"";position:fixed;inset:0;pointer-events:none;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:28px 28px;mask-image:linear-gradient(to bottom,rgba(0,0,0,.78),transparent 76%);z-index:-1}
button,input{font:inherit}
.hdr{position:relative;text-align:left;padding:18px 16px;margin-bottom:14px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(135deg,rgba(56,189,248,.12),rgba(74,222,128,.08) 55%,rgba(167,139,250,.1));box-shadow:0 18px 45px rgba(0,0,0,.28);overflow:hidden;animation:panelIn .45s ease both}
.hdr:after{content:"";position:absolute;left:0;right:0;bottom:0;height:2px;background:linear-gradient(90deg,var(--good),var(--info),var(--violet));animation:scan 4s ease-in-out infinite}
.hdr h1{font-size:21px;font-weight:700;line-height:1.2}
.hdr .sub{font-size:13px;color:var(--muted);margin-top:5px}
.card{position:relative;background:var(--panel);border-radius:8px;padding:16px;margin-bottom:12px;border:1px solid var(--line);box-shadow:0 14px 30px rgba(0,0,0,.22);backdrop-filter:blur(14px);overflow:hidden;animation:panelIn .45s ease both}
.card:before{content:"";position:absolute;inset:0;background:linear-gradient(120deg,transparent,rgba(255,255,255,.045),transparent);transform:translateX(-120%);transition:transform .7s ease;pointer-events:none}
.card:hover:before{transform:translateX(120%)}
.ct{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:0;margin-bottom:12px;font-weight:700}
.bw{background:rgba(255,255,255,.08);border-radius:6px;height:22px;position:relative;overflow:hidden;margin-bottom:8px;border:1px solid rgba(255,255,255,.06)}
.bf{height:100%;border-radius:6px;transition:width .55s ease,background .3s ease;position:relative;min-width:2px}
.bf:after{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,.32),transparent);transform:translateX(-100%);animation:barSweep 2.6s ease-in-out infinite}
.bf.g{background:linear-gradient(90deg,#16a34a,var(--good))}.bf.y{background:linear-gradient(90deg,#f59e0b,var(--warn))}.bf.r{background:linear-gradient(90deg,#ef4444,var(--bad))}
.bl{position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:12px;font-weight:700;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.55)}
.bnum{font-size:36px;font-weight:800;line-height:1}.bnum .u{font-size:16px;font-weight:500;color:var(--muted)}
.si{font-size:13px;color:var(--muted);margin-top:4px}
.row{display:flex;gap:10px}.row .card{flex:1;text-align:center}
.metric-card{text-align:left!important;min-height:206px}
.metric-top{display:flex;align-items:center;justify-content:space-between;gap:12px}
.gauge{--pct:0;--gcolor:var(--good);width:82px;aspect-ratio:1;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--gcolor) calc(var(--pct)*1%),rgba(255,255,255,.08) 0);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08),0 0 28px rgba(56,189,248,.08)}
.gauge:before{content:"";position:absolute;width:60px;aspect-ratio:1;border-radius:50%;background:#101827;border:1px solid rgba(255,255,255,.08)}
.gauge span{position:relative;font-size:13px;font-weight:800;color:#fff}
.spark{display:block;width:100%;height:54px;margin-top:12px;border-radius:6px;background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.025));border:1px solid rgba(255,255,255,.06)}
.dr{display:flex;align-items:center;gap:10px;margin-bottom:10px;padding:8px;border-radius:6px;background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.045)}
.dr:last-child{margin-bottom:0}
.dd{font-size:14px;font-weight:800;min-width:34px;color:#dbeafe}.db{flex:1}.di{font-size:12px;color:var(--muted);min-width:100px;text-align:right}
.df{font-size:11px;color:var(--good);margin-top:2px}
.pr{position:relative;display:flex;align-items:center;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.05);font-size:13px;gap:8px}
.pr:last-child{border:none}
.pr:before{content:"";position:absolute;left:0;bottom:0;height:1px;width:var(--load,0%);background:linear-gradient(90deg,var(--info),transparent);opacity:.75}
.pn{flex:1;color:#d2d9e6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pc{width:64px;text-align:right;color:var(--good);font-weight:700}
.pm{width:74px;text-align:right;color:#93c5fd;font-weight:700}
.ph{font-size:11px;color:#708095;text-transform:uppercase;border-bottom:1px solid rgba(255,255,255,.08);padding-bottom:6px;margin-bottom:4px}
.gpu{font-size:14px;color:#d2d9e6;line-height:1.35;word-break:break-word}
.tabs{display:flex;gap:4px;margin-bottom:12px;background:rgba(255,255,255,.045);border-radius:8px;overflow:hidden;border:1px solid var(--line);padding:4px;position:sticky;top:8px;z-index:20;backdrop-filter:blur(16px)}
.tab{flex:1;padding:10px;text-align:center;font-size:13px;font-weight:700;cursor:pointer;color:var(--muted);transition:background .2s ease,color .2s ease,transform .2s ease;border-radius:6px;user-select:none}
.tab.active{background:linear-gradient(135deg,rgba(74,222,128,.22),rgba(56,189,248,.16));color:#dfffea;box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}
.tab:active{transform:scale(.98)}
.tab-content{display:none}.tab-content.active{display:block;animation:panelIn .24s ease both}
.sbtn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:10px 18px;border:none;border-radius:8px;font-size:14px;font-weight:700;cursor:pointer;color:#fff;background:linear-gradient(135deg,#2563eb,#7c3aed);margin:4px;transition:transform .16s ease,filter .16s ease,box-shadow .16s ease;box-shadow:0 10px 24px rgba(37,99,235,.22)}
.sbtn:hover{filter:brightness(1.08)}.sbtn:disabled{opacity:.55;cursor:wait}.sbtn:active{transform:scale(.96)}
.tbox{display:none;margin-top:12px;background:#090d14;border-radius:8px;border:1px solid var(--line);overflow:hidden;box-shadow:0 18px 40px rgba(0,0,0,.3)}
.thdr{background:rgba(255,255,255,.06);padding:9px 12px;font-size:12px;color:var(--muted);display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid rgba(255,255,255,.06)}
.thdr .x{background:none;border:none;color:var(--muted);font-size:18px;cursor:pointer;padding:0 4px}
.tout{padding:12px;max-height:400px;overflow-y:auto;font-family:"Cascadia Mono","Consolas",monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-all;color:#d4d4d4}
.tout::-webkit-scrollbar,.exp-list::-webkit-scrollbar,#procList::-webkit-scrollbar{width:7px}.tout::-webkit-scrollbar-thumb,.exp-list::-webkit-scrollbar-thumb,#procList::-webkit-scrollbar-thumb{background:rgba(255,255,255,.18);border-radius:6px}
.trow{display:flex;align-items:center;padding:9px 12px;border-top:1px solid rgba(255,255,255,.08);gap:8px}
.tp{color:var(--good);font-family:monospace;font-size:13px;white-space:nowrap}
.ti{flex:1;background:none;border:none;color:#fff;font-family:monospace;font-size:13px;outline:none;min-width:0}
.ts{background:var(--good);border:none;color:#07111f;padding:5px 12px;border-radius:6px;font-size:12px;font-weight:800;cursor:pointer}
.footer{position:fixed;bottom:0;left:0;right:0;text-align:center;padding:12px;background:rgba(7,17,31,.9);backdrop-filter:blur(16px);border-top:1px solid var(--line);font-size:12px;color:var(--muted);z-index:30}
.ld{display:inline-block;width:7px;height:7px;background:var(--good);border-radius:50%;margin-right:4px;box-shadow:0 0 14px var(--good);animation:pulse 2s infinite}
.loading{animation:spin 1s linear infinite;display:inline-block}
.exp-toolbar{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.exp-btn{min-width:34px;padding:6px 10px;background:rgba(255,255,255,.07);border:1px solid var(--line);border-radius:6px;color:#d1d5db;font-size:14px;cursor:pointer;transition:background .15s ease,transform .15s ease,color .15s ease}
.exp-btn:hover{background:rgba(255,255,255,.12);color:#fff}
.exp-btn:active{transform:scale(.95)}
.exp-btn.disabled,.exp-btn:disabled{opacity:.42;cursor:not-allowed}
.exp-addr{display:flex;flex:1;min-width:180px;background:rgba(255,255,255,.06);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.exp-addr input{flex:1;background:none;border:none;color:var(--text);padding:7px 10px;font-size:13px;outline:none;font-family:monospace;min-width:0}
.exp-addr button{background:rgba(74,222,128,.18);border:none;color:#bbf7d0;padding:6px 12px;cursor:pointer;font-weight:800}
.exp-search{display:flex;background:rgba(255,255,255,.06);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.exp-search input{width:140px;background:none;border:none;color:var(--text);padding:7px 10px;font-size:13px;outline:none}
.exp-quick{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.exp-quick-item{padding:6px 12px;background:rgba(56,189,248,.09);border:1px solid rgba(56,189,248,.2);border-radius:8px;font-size:12px;color:#bae6fd;cursor:pointer;transition:background .15s ease,transform .15s ease}
.exp-quick-item:hover{background:rgba(56,189,248,.17)}
.exp-quick-item:active{transform:scale(.97)}
.exp-header{display:flex;align-items:center;padding:8px 10px;border-bottom:1px solid var(--line);font-size:11px;color:#708095;text-transform:uppercase}
.exp-header .exp-hname{flex:1}.exp-header .exp-hsize{width:88px;text-align:right}.exp-header .exp-htime{width:126px;text-align:right}
.exp-list{max-height:410px;overflow-y:auto;-webkit-overflow-scrolling:touch;touch-action:pan-y}
.exp-item{display:flex;align-items:center;padding:9px 10px;border-bottom:1px solid rgba(255,255,255,.045);cursor:pointer;transition:background .15s ease,transform .15s ease;border-radius:6px;gap:8px}
.exp-item:hover{background:rgba(255,255,255,.065)}
.exp-item:active{transform:scale(.995)}
.exp-item.selected{background:rgba(74,222,128,.14);box-shadow:inset 0 0 0 1px rgba(74,222,128,.22)}
.exp-item .exp-icon{width:24px;text-align:center;font-size:16px;flex:0 0 24px}
.exp-item .exp-name{flex:1;color:#d2d9e6;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.exp-item .exp-size{width:88px;text-align:right;color:var(--muted);font-size:12px;flex:0 0 88px}
.exp-item .exp-time{width:126px;text-align:right;color:#708095;font-size:12px;flex:0 0 126px}
.exp-context{position:fixed;background:rgba(12,18,30,.96);border:1px solid var(--line);border-radius:8px;padding:4px 0;min-width:166px;z-index:1000;display:none;box-shadow:0 18px 50px rgba(0,0,0,.48);backdrop-filter:blur(16px)}
.exp-context-item{padding:9px 16px;font-size:13px;color:#d1d5db;cursor:pointer;transition:background .15s ease}
.exp-context-item:hover{background:rgba(74,222,128,.14)}
.exp-context-divider{height:1px;background:rgba(255,255,255,.1);margin:4px 0}
.exp-status{display:flex;justify-content:space-between;gap:10px;padding:8px 10px;font-size:12px;color:var(--muted);border-top:1px solid rgba(255,255,255,.06);margin-top:8px}
.toast{position:fixed;left:50%;bottom:58px;transform:translate(-50%,12px);background:rgba(12,18,30,.96);border:1px solid var(--line);color:#fff;padding:9px 14px;border-radius:8px;font-size:13px;box-shadow:0 16px 40px rgba(0,0,0,.36);opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease;z-index:1200;max-width:calc(100vw - 32px)}
.toast.show{opacity:1;transform:translate(-50%,0)}
@keyframes panelIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.78)}}
@keyframes spin{0%{transform:rotate(0)}100%{transform:rotate(360deg)}}
@keyframes scan{0%,100%{transform:translateX(-35%)}50%{transform:translateX(35%)}}
@keyframes barSweep{0%,45%{transform:translateX(-120%)}100%{transform:translateX(120%)}}
@media (max-width:640px){body{padding:12px;padding-bottom:78px}.row{flex-direction:column;gap:0}.metric-top{align-items:flex-start}.gauge{width:76px}.exp-header .exp-htime,.exp-item .exp-time{display:none}.exp-search{width:100%}.exp-search input{width:100%}.exp-addr{min-width:100%}.tabs{top:6px}.bnum{font-size:33px}}
@media (prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
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
</div>

<!-- Status -->
<div class="tab-content active" id="tab-status">
  <div class="row">
    <div class="card metric-card">
      <div class="metric-top">
        <div><div class="ct">CPU</div><div class="bnum" id="cpuValue" style="color:{{CPU_C}}">{{CPU}}<span class="u">%</span></div></div>
        <div class="gauge" id="cpuGauge" style="--pct:{{CPU}};--gcolor:{{CPU_C}}"><span>{{CPU}}%</span></div>
      </div>
      <canvas class="spark" id="cpuSpark"></canvas>
    </div>
    <div class="card metric-card">
      <div class="metric-top">
        <div><div class="ct">MEM</div><div class="bnum" id="memValue" style="color:{{MEM_C}}">{{MEM_P}}<span class="u">%</span></div><div class="si" id="memInfo">{{MEM_U}} / {{MEM_T}} GB</div></div>
        <div class="gauge" id="memGauge" style="--pct:{{MEM_P}};--gcolor:{{MEM_C}}"><span>{{MEM_P}}%</span></div>
      </div>
      <canvas class="spark" id="memSpark"></canvas>
    </div>
  </div>
  <div class="card"><div class="ct">CPU Usage</div><div class="bw"><div class="bf {{CPU_B}}" id="cpuBar" style="width:{{CPU}}%"><span class="bl" id="cpuBarText">{{CPU}}%</span></div></div></div>
  <div class="card"><div class="ct">Memory</div><div class="bw"><div class="bf {{MEM_B}}" id="memBar" style="width:{{MEM_P}}%"><span class="bl" id="memBarText">{{MEM_U}} / {{MEM_T}} GB</span></div></div></div>
  <div class="card"><div class="ct">DISK</div><div id="diskList">{{DISKS}}</div></div>
  <div class="row">
    <div class="card"><div class="ct">PROCS</div><div class="bnum" id="procTotal" style="color:#a78bfa">{{PROC}}</div></div>
    <div class="card"><div class="ct">GPU</div><div class="gpu" id="gpuName">{{GPU}}</div></div>
  </div>
  <div class="card" id="procCard">
    <div class="ct">进程管理 <span style="font-size:11px;color:#888">(右键结束进程)</span></div>
    <div style="margin-bottom:10px;display:flex;gap:8px">
      <input type="text" id="procSearch" placeholder="搜索进程..." style="flex:1;padding:6px 10px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:6px;color:#fff;font-size:13px;outline:none" oninput="filterProcs(this.value)">
      <button onclick="refreshProcs()" style="padding:6px 12px;background:rgba(105,240,174,.2);border:none;border-radius:6px;color:#69f0ae;cursor:pointer">刷新</button>
    </div>
    <div class="pr ph"><span class="pn">名称</span><span class="pc">CPU</span><span class="pm">内存</span><span style="width:50px;text-align:right">PID</span></div>
    <div id="procList" style="max-height:350px;overflow-y:auto;-webkit-overflow-scrolling:touch;touch-action:pan-y"></div>
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
    <div id="expList" class="exp-list"></div>
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
    <button class="sbtn" onclick="takeSS()" id="ssBtn">Full Screenshot</button>
    <button class="sbtn" style="background:linear-gradient(135deg,#11998e,#38ef7d)" onclick="toggleTerm()">PowerShell</button>
  </div>
  <div class="card" id="ssCard" style="display:none">
    <div class="ct">SCREENSHOT <span id="ssInfo" style="color:#8f9aac;font-weight:500"></span></div>
    <img id="ssImg" style="width:100%;height:auto;display:block;border-radius:8px">
  </div>
  <div class="tbox" id="termBox">
    <div class="thdr"><span>PowerShell</span><button class="x" onclick="toggleTerm()">&times;</button></div>
    <div class="tout" id="termOut">Ready.\n\n</div>
    <div class="trow"><span class="tp">PS&gt;</span><input class="ti" id="termIn" placeholder="Enter command..." autocomplete="off" spellcheck="false"><button class="ts" onclick="sendCmd()">Run</button></div>
  </div>
</div>

<div class="footer"><span class="ld"></span> <span id="ts">{{TIMESTAMP}}</span></div>
<div class="toast" id="toast"></div>

<script type="application/json" id="legacy-js-disabled">
// Tab
function switchTab(n){
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',['status','files','tools'][i]===n));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  document.getElementById('tab-'+n).classList.add('active');
  if(n==='files')expLoad();
}

// AJAX refresh
let rt=null;
function sched(){
  clearTimeout(rt);
  if(!document.hidden)rt=setTimeout(async()=>{
    if(document.hidden)return;
    try{
      const d=await(await fetch('/api')).json();
      document.querySelector('#tab-status .row .card:first-child .bnum').innerHTML=d.cpu+'<span class="u">%</span>';
      document.querySelector('#tab-status .row .card:last-child .bnum').innerHTML=d.mem.percent+'<span class="u">%</span>';
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
      document.getElementById('ssImg').src='data:image/jpeg;base64,'+d.image;
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
  try{
    const qa=await(await fetch('/api/drives')).json();
    let qh='';
    qa.drives.forEach(d=>{qh+='<div class="exp-quick-item" onclick="expGo(\''+d.letter+':\\\')">💾 '+d.letter+':\ ('+d.free+' GB free)</div>';});
    document.getElementById('expQuick').innerHTML=qh;
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
    h+='<div class="exp-item" data-name="'+i.name+'" data-dir="'+i.dir+'" data-path="'+expCurPath.replace(/\\$/,'')+'\\'+i.name+'" ondblclick="expDblClick(this)" oncontextmenu="expCtx(event,this)" ontouchstart="expLongPress(event,this)" ontouchend="expCancelPress()" ontouchmove="expCancelPress()" onmousedown="expLongPress(event,this)" onmouseup="expCancelPress()" onmouseleave="expCancelPress()">';
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
function expLongPress(e,el){e.preventDefault();expPressTarget=el;expPressTimer=setTimeout(()=>{el.style.background='rgba(105,240,174,.15)';if(el.dataset.dir!=='true'&&confirm('下载 '+el.dataset.name+' ?')){window.open('/api/download?p='+encodeURIComponent(el.dataset.path));}el.style.background='';expPressTarget=null;},500);}
function expCancelPress(){if(expPressTimer){clearTimeout(expPressTimer);expPressTimer=null;}if(expPressTarget){expPressTarget.style.background='';expPressTarget=null;}}
function expCtx(e,el){e.preventDefault();ctxItem=el;el.classList.add('selected');const m=document.getElementById('expContext');m.style.display='block';m.style.left=Math.min(e.clientX,window.innerWidth-180)+'px';m.style.top=Math.min(e.clientY,window.innerHeight-200)+'px';}
document.addEventListener('click',()=>{document.getElementById('expContext').style.display='none';document.querySelectorAll('.exp-item.selected').forEach(e=>e.classList.remove('selected'))});
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
function showProcMenu(x,y){const m=document.getElementById('procContext');m.style.display='block';m.style.left=Math.min(x,window.innerWidth-180)+'px';m.style.top=Math.min(y,window.innerHeight-100)+'px';}
document.addEventListener('click',()=>{document.getElementById('procContext').style.display='none';document.querySelectorAll('.pr[style*="background"]').forEach(e=>e.style.background='');});
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
setInterval(()=>{if(!document.hidden)refreshProcs();},10000);
setTimeout(refreshProcs,2000);
</script>
<script>
const $ = (id) => document.getElementById(id);
const cpuSeries = [];
const memSeries = [];
let statusTimer = null;
let expHistory = [];
let expIdx = -1;
let expCurPath = "";
let expAllItems = [];
let expLoaded = false;
let expSeq = 0;
let ctxItem = null;
let expPressTimer = null;
let expPressTarget = null;
let allProcs = [];
let cpuUnit = "s";
let procCtxItem = null;
let procPressTimer = null;
let procPressTarget = null;
let tv = false;
let ch = [];
let ci = -1;

const fileIcons = {
  ".txt":"TXT",".pdf":"PDF",".doc":"DOC",".docx":"DOC",".xls":"XLS",".xlsx":"XLS",
  ".ppt":"PPT",".pptx":"PPT",".jpg":"IMG",".jpeg":"IMG",".png":"IMG",".gif":"IMG",
  ".mp3":"AUD",".mp4":"VID",".zip":"ZIP",".rar":"ZIP",".exe":"EXE",".py":"PY",
  ".js":"JS",".html":"WEB",".css":"CSS",".json":"JSON"
};

function toast(msg){
  const el = $("toast");
  if(!el) return;
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._tid);
  el._tid = setTimeout(() => el.classList.remove("show"), 2200);
}

async function fetchJson(url, options){
  const resp = await fetch(url, options);
  const data = await resp.json().catch(() => ({}));
  if(!resp.ok) throw new Error(data.error || resp.statusText || "Request failed");
  return data;
}

function colorFor(p){
  return p < 60 ? "#4ade80" : p < 85 ? "#facc15" : "#fb7185";
}

function barClass(p){
  return p < 60 ? "g" : p < 85 ? "y" : "r";
}

function setGauge(id, value){
  const el = $(id);
  if(!el) return;
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  el.style.setProperty("--pct", v);
  el.style.setProperty("--gcolor", colorFor(v));
  const label = el.querySelector("span");
  if(label) label.textContent = v.toFixed(1) + "%";
}

function setBar(id, textId, value, label){
  const bar = $(id);
  if(!bar) return;
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  bar.style.width = v + "%";
  bar.className = "bf " + barClass(v);
  const txt = $(textId);
  if(txt) txt.textContent = label;
}

function pushSeries(arr, value){
  arr.push(Math.max(0, Math.min(100, Number(value) || 0)));
  while(arr.length > 42) arr.shift();
}

function drawSpark(id, arr, color){
  const canvas = $(id);
  if(!canvas || !arr.length) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.lineWidth = 2;
  ctx.strokeStyle = color;
  ctx.beginPath();
  arr.forEach((v, i) => {
    const x = arr.length === 1 ? 0 : i * rect.width / (arr.length - 1);
    const y = rect.height - (v / 100) * (rect.height - 10) - 5;
    if(i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  const grad = ctx.createLinearGradient(0, 0, 0, rect.height);
  grad.addColorStop(0, color + "55");
  grad.addColorStop(1, color + "00");
  ctx.lineTo(rect.width, rect.height);
  ctx.lineTo(0, rect.height);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
}

function renderDisks(disks){
  const list = $("diskList");
  if(!list) return;
  list.replaceChildren();
  (disks || []).forEach(d => {
    const row = document.createElement("div");
    row.className = "dr";
    const name = document.createElement("span");
    name.className = "dd";
    name.textContent = d.drive;
    const db = document.createElement("div");
    db.className = "db";
    const bw = document.createElement("div");
    bw.className = "bw";
    const bf = document.createElement("div");
    bf.className = "bf " + barClass(d.percent);
    bf.style.width = Math.max(0, Math.min(100, Number(d.percent) || 0)) + "%";
    bw.appendChild(bf);
    db.appendChild(bw);
    const meta = document.createElement("div");
    meta.style.minWidth = "100px";
    meta.style.textAlign = "right";
    const used = document.createElement("div");
    used.className = "di";
    used.textContent = `${d.used} / ${d.total} GB`;
    const free = document.createElement("div");
    free.className = "df";
    free.textContent = `Free: ${d.free} GB`;
    meta.append(used, free);
    row.append(name, db, meta);
    list.appendChild(row);
  });
}

async function refreshStatus(){
  try{
    const d = await fetchJson("/api");
    const cpu = Number(d.cpu) || 0;
    const mem = Number(d.mem && d.mem.percent) || 0;
    $("cpuValue").innerHTML = cpu + '<span class="u">%</span>';
    $("cpuValue").style.color = colorFor(cpu);
    $("memValue").innerHTML = mem + '<span class="u">%</span>';
    $("memValue").style.color = colorFor(mem);
    $("memInfo").textContent = `${d.mem.used} / ${d.mem.total} GB`;
    $("ts").textContent = d.ts;
    document.querySelector(".hdr .sub:last-child").textContent = "Uptime: " + d.uptime;
    $("procTotal").textContent = d.procs;
    $("gpuName").textContent = d.gpu;
    setGauge("cpuGauge", cpu);
    setGauge("memGauge", mem);
    setBar("cpuBar", "cpuBarText", cpu, cpu + "%");
    setBar("memBar", "memBarText", mem, `${d.mem.used} / ${d.mem.total} GB`);
    renderDisks(d.disks);
    pushSeries(cpuSeries, cpu);
    pushSeries(memSeries, mem);
    drawSpark("cpuSpark", cpuSeries, colorFor(cpu));
    drawSpark("memSpark", memSeries, colorFor(mem));
  }catch(e){
    toast("状态刷新失败: " + e.message);
  }
}

function sched(){
  clearTimeout(statusTimer);
  if(document.hidden) return;
  statusTimer = setTimeout(async () => {
    await refreshStatus();
    sched();
  }, 2800);
}

function switchTab(n){
  document.querySelectorAll(".tab").forEach((t, i) => t.classList.toggle("active", ["status","files","tools"][i] === n));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
  $("tab-" + n).classList.add("active");
  if(n === "files") expLoad();
}

async function takeSS(){
  const btn = $("ssBtn");
  btn.disabled = true;
  btn.innerHTML = '<span class="loading">&#8635;</span> Capturing...';
  try{
    const d = await fetchJson("/api/screenshot");
    if(d.ok && d.image){
      $("ssImg").src = "data:image/jpeg;base64," + d.image;
      $("ssInfo").textContent = d.width && d.height ? ` ${d.width}x${d.height}` : "";
      $("ssCard").style.display = "block";
      toast("全屏截图完成");
    }else{
      toast(d.error || "截图失败");
    }
  }catch(e){
    toast("截图失败: " + e.message);
  }finally{
    btn.disabled = false;
    btn.textContent = "Full Screenshot";
  }
}

function toggleTerm(){
  tv = !tv;
  $("termBox").style.display = tv ? "block" : "none";
  if(tv) $("termIn").focus();
}

async function sendCmd(){
  const inp = $("termIn");
  const out = $("termOut");
  const cmd = inp.value.trim();
  if(!cmd) return;
  ch.push(cmd);
  ci = ch.length;
  out.textContent += "PS> " + cmd + "\n";
  inp.value = "";
  out.scrollTop = out.scrollHeight;
  try{
    const d = await fetchJson("/api/terminal", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({cmd})
    });
    if(d.output) out.textContent += d.output + "\n\n";
    if(d.error) out.textContent += "[ERROR] " + d.error + "\n\n";
  }catch(e){
    out.textContent += "[ERR] " + e.message + "\n\n";
  }
  out.scrollTop = out.scrollHeight;
}

function normPath(path){
  if(!path) return "";
  path = String(path).replace(/\//g, "\\");
  if(path.length === 2 && path[1] === ":") path += "\\";
  return path;
}

function joinPath(base, name){
  return normPath(base).replace(/\\$/, "") + "\\" + name;
}

function fmtSize(b){
  b = Number(b) || 0;
  if(b > 1073741824) return (b / 1073741824).toFixed(1) + " GB";
  if(b > 1048576) return (b / 1048576).toFixed(1) + " MB";
  if(b > 1024) return (b / 1024).toFixed(0) + " KB";
  return b + " B";
}

function getIcon(n, isDir){
  if(isDir) return "DIR";
  const pos = String(n).lastIndexOf(".");
  const ext = pos >= 0 ? String(n).slice(pos).toLowerCase() : "";
  return fileIcons[ext] || "FILE";
}

async function expLoad(){
  if(expLoaded) return;
  expLoaded = true;
  try{
    const qa = await fetchJson("/api/drives");
    const quick = $("expQuick");
    quick.replaceChildren();
    (qa.drives || []).forEach(d => {
      const path = normPath(d.letter + ":\\");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "exp-quick-item";
      btn.dataset.path = path;
      btn.textContent = `${d.letter}: ${d.free} GB free`;
      btn.addEventListener("click", () => expGo(btn.dataset.path));
      quick.appendChild(btn);
    });
    const first = (qa.drives || []).find(d => String(d.letter).toUpperCase() === "C") || (qa.drives || [])[0];
    if(first) expGo(first.letter + ":\\");
  }catch(e){
    $("expList").innerHTML = '<div style="color:#ff8a80;padding:20px">加载失败</div>';
    $("expStatus").textContent = "加载失败";
  }
}

async function expGo(path, fromHistory){
  path = normPath(path);
  if(!path) return;
  const seq = ++expSeq;
  $("expStatus").textContent = "加载中...";
  try{
    const d = await fetchJson("/api/files?p=" + encodeURIComponent(path));
    if(seq !== expSeq) return;
    if(d.error){
      $("expStatus").textContent = "错误";
      toast(d.error);
      return;
    }
    expCurPath = d.path;
    expAllItems = d.items || [];
    $("expAddr").value = d.path;
    $("expSearch").value = "";
    if(!fromHistory && (expIdx < 0 || expHistory[expIdx] !== d.path)){
      expHistory = expHistory.slice(0, expIdx + 1);
      expHistory.push(d.path);
      expIdx = expHistory.length - 1;
    }
    expRender(expAllItems);
    $("expStatus").textContent = d.truncated ? `已显示前 ${d.limit} 项` : "就绪";
    $("expBackBtn").disabled = expIdx <= 0;
    $("expFwdBtn").disabled = expIdx >= expHistory.length - 1;
  }catch(e){
    if(seq === expSeq){
      $("expStatus").textContent = "网络错误";
      toast("目录加载失败: " + e.message);
    }
  }
}

function expRender(items){
  const c = $("expList");
  c.replaceChildren();
  let dirs = 0;
  let files = 0;
  if(!items.length){
    const empty = document.createElement("div");
    empty.style.cssText = "padding:20px;text-align:center;color:#888";
    empty.textContent = "空文件夹";
    c.appendChild(empty);
    $("expCount").textContent = "0 个文件夹, 0 个文件";
    return;
  }
  const frag = document.createDocumentFragment();
  items.forEach(i => {
    const row = document.createElement("div");
    row.className = "exp-item";
    row.dataset.name = i.name;
    row.dataset.dir = String(!!i.dir);
    row.dataset.path = joinPath(expCurPath, i.name);

    const icon = document.createElement("span");
    icon.className = "exp-icon";
    icon.textContent = getIcon(i.name, i.dir);
    const name = document.createElement("span");
    name.className = "exp-name";
    name.textContent = i.name;
    const size = document.createElement("span");
    size.className = "exp-size";
    size.textContent = i.dir ? "" : fmtSize(i.size);
    const time = document.createElement("span");
    time.className = "exp-time";
    time.textContent = i.time;
    row.append(icon, name, size, time);

    row.addEventListener("click", () => {
      document.querySelectorAll(".exp-item.selected").forEach(el => el.classList.remove("selected"));
      row.classList.add("selected");
      ctxItem = row;
      if(row.dataset.dir === "true") expGo(row.dataset.path);
    });
    row.addEventListener("contextmenu", e => expCtx(e, row));
    row.addEventListener("pointerdown", e => expLongPress(e, row));
    row.addEventListener("pointerup", expCancelPress);
    row.addEventListener("pointercancel", expCancelPress);
    row.addEventListener("pointerleave", expCancelPress);
    frag.appendChild(row);
    if(i.dir) dirs++; else files++;
  });
  c.appendChild(frag);
  $("expCount").textContent = `${dirs} 个文件夹, ${files} 个文件`;
}

function expBack(){
  if(expIdx > 0){
    expIdx--;
    expGo(expHistory[expIdx], true);
  }
}

function expForward(){
  if(expIdx < expHistory.length - 1){
    expIdx++;
    expGo(expHistory[expIdx], true);
  }
}

function expHome(){
  expGo("C:\\");
}

function expUp(){
  const p = normPath(expCurPath).replace(/\\$/, "").split("\\");
  if(p.length > 1){
    p.pop();
    expGo(p.join("\\") || p[0] + "\\");
  }
}

function expFilter(q){
  q = String(q || "").toLowerCase();
  expRender(q ? expAllItems.filter(i => String(i.name).toLowerCase().includes(q)) : expAllItems);
}

function expLongPress(e, el){
  if(e.pointerType === "mouse" && e.button !== 0) return;
  expPressTarget = el;
  expPressTimer = setTimeout(() => {
    el.classList.add("selected");
    ctxItem = el;
    if(el.dataset.dir !== "true") ctxDownload();
  }, 650);
}

function expCancelPress(){
  if(expPressTimer){
    clearTimeout(expPressTimer);
    expPressTimer = null;
  }
  expPressTarget = null;
}

function expCtx(e, el){
  e.preventDefault();
  ctxItem = el;
  document.querySelectorAll(".exp-item.selected").forEach(x => x.classList.remove("selected"));
  el.classList.add("selected");
  const m = $("expContext");
  m.style.display = "block";
  m.style.left = Math.min(e.clientX, window.innerWidth - 190) + "px";
  m.style.top = Math.min(e.clientY, window.innerHeight - 220) + "px";
}

function ctxOpen(){
  if(!ctxItem) return;
  if(ctxItem.dataset.dir === "true") expGo(ctxItem.dataset.path);
  else ctxDownload();
}

function ctxDownload(){
  if(!ctxItem || ctxItem.dataset.dir === "true") return;
  window.open("/api/download?p=" + encodeURIComponent(ctxItem.dataset.path));
}

async function ctxNewFolder(){
  const n = prompt("新建文件夹名称:");
  if(!n) return;
  try{
    const d = await fetchJson("/api/mkdir", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:joinPath(expCurPath, n)})});
    if(d.ok) expGo(expCurPath, true); else toast(d.error || "创建失败");
  }catch(e){ toast(e.message); }
}

async function ctxRename(){
  if(!ctxItem) return;
  const oldName = ctxItem.dataset.name;
  const n = prompt("重命名:", oldName);
  if(!n || n === oldName) return;
  try{
    const d = await fetchJson("/api/rename", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({old:ctxItem.dataset.path,new:joinPath(expCurPath, n)})});
    if(d.ok) expGo(expCurPath, true); else toast(d.error || "重命名失败");
  }catch(e){ toast(e.message); }
}

async function ctxDelete(){
  if(!ctxItem) return;
  if(!confirm("确定删除 " + ctxItem.dataset.name + " ?")) return;
  try{
    const d = await fetchJson("/api/delete", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:ctxItem.dataset.path})});
    if(d.ok) expGo(expCurPath, true); else toast(d.error || "删除失败");
  }catch(e){ toast(e.message); }
}

async function refreshProcs(){
  try{
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 8000);
    const data = await fetchJson("/api/processes", {signal:ctrl.signal});
    clearTimeout(tid);
    if(data.procs){
      allProcs = data.procs;
      cpuUnit = data.cpu_unit || "s";
      renderProcs(allProcs);
    }
  }catch(e){
    $("procCount").textContent = "加载超时，点击刷新";
  }
}

function renderProcs(procs){
  const c = $("procList");
  c.replaceChildren();
  const frag = document.createDocumentFragment();
  const maxMem = Math.max(1, ...procs.map(p => Number(p.mem) || 0));
  procs.forEach(p => {
    const row = document.createElement("div");
    row.className = "pr";
    row.style.cursor = "pointer";
    row.style.setProperty("--load", Math.min(100, ((Number(p.mem) || 0) / maxMem) * 100) + "%");
    row.dataset.pid = p.pid;
    row.dataset.name = p.name;
    const name = document.createElement("span");
    name.className = "pn";
    name.textContent = p.name;
    const cpu = document.createElement("span");
    cpu.className = "pc";
    cpu.textContent = p.cpu ? Number(p.cpu).toFixed(1) + cpuUnit : "-";
    const mem = document.createElement("span");
    mem.className = "pm";
    mem.textContent = p.mem + " MB";
    const pid = document.createElement("span");
    pid.style.cssText = "width:50px;text-align:right;color:#888";
    pid.textContent = p.pid;
    row.append(name, cpu, mem, pid);
    row.addEventListener("contextmenu", e => procCtx(e, row));
    row.addEventListener("pointerdown", e => procLongPress(e, row));
    row.addEventListener("pointerup", procCancelPress);
    row.addEventListener("pointercancel", procCancelPress);
    row.addEventListener("pointerleave", procCancelPress);
    frag.appendChild(row);
  });
  c.appendChild(frag);
  $("procCount").textContent = "共 " + procs.length + " 个进程";
}

function filterProcs(q){
  q = String(q || "").toLowerCase();
  renderProcs(q ? allProcs.filter(p => String(p.name).toLowerCase().includes(q)) : allProcs);
}

function procLongPress(e, el){
  if(e.pointerType === "mouse" && e.button !== 0) return;
  procPressTarget = el;
  procPressTimer = setTimeout(() => {
    procCtxItem = el;
    showProcMenu(e.clientX || 16, e.clientY || 16);
  }, 650);
}

function procCancelPress(){
  if(procPressTimer){
    clearTimeout(procPressTimer);
    procPressTimer = null;
  }
  procPressTarget = null;
}

function procCtx(e, el){
  e.preventDefault();
  procCtxItem = el;
  showProcMenu(e.clientX, e.clientY);
}

function showProcMenu(x, y){
  const m = $("procContext");
  m.style.display = "block";
  m.style.left = Math.min(x, window.innerWidth - 190) + "px";
  m.style.top = Math.min(y, window.innerHeight - 120) + "px";
}

async function procKill(){
  if(!procCtxItem) return;
  const pid = Number(procCtxItem.dataset.pid);
  const name = procCtxItem.dataset.name;
  if(!confirm("确定结束 " + name + " (PID:" + pid + ")?")) return;
  try{
    const d = await fetchJson("/api/kill", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pid})});
    if(d.ok) refreshProcs(); else toast(d.error || "结束失败");
  }catch(e){ toast(e.message); }
}

async function procForceKill(){
  if(!procCtxItem) return;
  const pid = Number(procCtxItem.dataset.pid);
  const name = procCtxItem.dataset.name;
  if(!confirm("强制结束 " + name + "?")) return;
  try{
    const d = await fetchJson("/api/kill", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pid, force:true})});
    if(d.ok) refreshProcs(); else toast(d.error || "强制结束失败");
  }catch(e){ toast(e.message); }
}

document.addEventListener("click", e => {
  if(!e.target.closest(".exp-context")){
    $("expContext").style.display = "none";
    $("procContext").style.display = "none";
  }
});

$("termIn").addEventListener("keydown", function(e){
  if(e.key === "Enter") sendCmd();
  else if(e.key === "ArrowUp"){
    e.preventDefault();
    if(ci > 0){ ci--; this.value = ch[ci]; }
  }else if(e.key === "ArrowDown"){
    e.preventDefault();
    if(ci < ch.length - 1){ ci++; this.value = ch[ci]; }
    else{ ci = ch.length; this.value = ""; }
  }
});

document.addEventListener("visibilitychange", () => {
  if(!document.hidden){
    refreshStatus();
    sched();
    refreshProcs();
  }else{
    clearTimeout(statusTimer);
  }
});
window.addEventListener("resize", () => {
  drawSpark("cpuSpark", cpuSeries, colorFor(cpuSeries[cpuSeries.length - 1] || 0));
  drawSpark("memSpark", memSeries, colorFor(memSeries[memSeries.length - 1] || 0));
});

refreshStatus().then(sched);
setInterval(() => { if(!document.hidden) refreshProcs(); }, 10000);
setTimeout(refreshProcs, 600);
</script>
</body>
</html>"""

def bc(p):
    return "g" if p<60 else "y" if p<85 else "r"

def render_disks(disks):
    h=""
    for d in disks:
        c=bc(d["percent"])
        drive = html.escape(str(d["drive"]))
        h+=f'<div class="dr"><span class="dd">{drive}</span><div class="db"><div class="bw"><div class="bf {c}" style="width:{d["percent"]}%"></div></div></div><div style="min-width:100px;text-align:right"><div class="di">{d["used"]} / {d["total"]} GB</div><div class="df">Free: {d["free"]} GB</div></div></div>'
    return h

def render_procs(procs):
    h=""
    for p in procs:
        name = html.escape(str(p["name"]))
        h+=f'<div class="pr"><span class="pn">{name}</span><span class="pc">{p["cpu"]:.1f}s</span><span class="pm">{p["mem_mb"]:.0f} MB</span></div>'
    return h

def build_html(s):
    cpu=s["cpu"]; m=s["mem"]
    return HTML.replace("{{HOSTNAME}}", html.escape(str(s["net"]["hostname"]))) \
        .replace("{{IP}}", html.escape(str(s["net"]["ip"]))) \
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
        .replace("{{GPU}}", html.escape(str(s["gpu"]))) \
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
body{font-family:-apple-system,sans-serif;background:#0a0a1a;color:#e0e0e0;display:flex;justify-content:center;align-items:center;min-height:100vh}
.login-box{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:32px;width:320px;text-align:center}
.login-box h1{font-size:24px;margin-bottom:8px}
.login-box p{font-size:13px;color:#888;margin-bottom:24px}
.login-box input{width:100%;padding:12px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:8px;color:#fff;font-size:16px;outline:none;margin-bottom:16px}
.login-box input:focus{border-color:#69f0ae}
.login-box button{width:100%;padding:12px;background:linear-gradient(135deg,#667eea,#764ba2);border:none;border-radius:8px;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
.login-box button:active{transform:scale(.98)}
.error{color:#ff8a80;font-size:13px;margin-bottom:12px;display:none}
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
    else{document.getElementById("err").style.display="block";document.getElementById("pwd").value="";document.getElementById("pwd").focus()}
  }).catch(()=>{document.getElementById("err").textContent="网络错误";document.getElementById("err").style.display="block"})
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
            shot = take_screenshot()
            ok = bool(shot and shot.get("image"))
            self.json_resp({"ok": ok, **(shot or {"image": ""})})
        elif path == "/api/drives":
            self.json_resp({"drives": get_drives()})
        elif path == "/api/files":
            # 接收正斜杠或反斜杠路径，统一转成 Windows 路径
            raw = qs.get("p", ["C:\\"])[0]
            pth = unquote(raw).replace("/", "\\")
            if len(pth) == 2 and pth[1] == ":":
                pth += "\\"
            items, err, truncated = list_directory(pth)
            if err:
                self.json_resp({"error": err, "path": pth, "items": [], "truncated": False})
            else:
                self.json_resp({"path": pth, "items": items, "truncated": truncated, "limit": MAX_DIR_ITEMS})
        elif path == "/font.ttf":
            try:
                with open(os.path.join(os.path.dirname(__file__), "font.ttf"), "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "font/ttf")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            except:
                self.send_error(404)
        elif path == "/api/download":
            raw = qs.get("p", [""])[0]
            pth = unquote(raw).replace("/", "\\")
            if os.path.isfile(pth):
                try:
                    mime = mimetypes.guess_type(pth)[0] or "application/octet-stream"
                    filename = os.path.basename(pth)
                    quoted = quote(filename)
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Disposition", f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quoted}')
                    self.send_header("Content-Length", str(os.path.getsize(pth)))
                    self.end_headers()
                    with open(pth, "rb") as f:
                        shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
                except Exception as e:
                    self.send_error(500, str(e))
            else:
                self.send_error(404)
            return
        elif path == "/api/processes":
            try:
                procs, cpu_unit = get_processes()
                self.json_resp({'procs': procs, 'cpu_unit': cpu_unit})
            except Exception as e:
                self.json_resp({'error': str(e)})
            return
        elif path == "/api/keepalive":
            s = qs.get("set", [None])[0]
            if s == "on": set_keep_screen_alive(True)
            elif s == "off": set_keep_screen_alive(False)
            self.json_resp({"on": keep_screen_alive})
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
                force = bool(d.get("force", False))
                if not pid:
                    self.json_resp({"ok": False, "error": "PID不能为空"})
                    return
                if HAS_PSUTIL:
                    proc = psutil.Process(int(pid))
                    proc.kill() if force else proc.terminate()
                elif force:
                    subprocess.run(["taskkill", "/PID", str(int(pid)), "/F"], capture_output=True, text=True, timeout=8)
                else:
                    import signal
                    os.kill(int(pid), signal.SIGTERM)
                self.json_resp({"ok": True})
            except PermissionError:
                self.json_resp({"ok": False, "error": "权限不足"})
            except ProcessLookupError:
                self.json_resp({"ok": False, "error": "进程不存在"})
            except Exception as e:
                if HAS_PSUTIL and e.__class__.__name__ == "NoSuchProcess":
                    self.json_resp({"ok": False, "error": "进程不存在"})
                elif HAS_PSUTIL and e.__class__.__name__ == "AccessDenied":
                    self.json_resp({"ok": False, "error": "权限不足"})
                else:
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
        body = json.dumps(d, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond(self, code, ct, body):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]}")

def main():
    net = get_network_info()
    set_keep_screen_alive(True)
    print(f"\n  PC Monitor: http://{net['ip']}:{PORT}\n")
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
