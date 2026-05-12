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
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
import sys
try:
    import psutil
    HAS_PSUTIL = True
except:
    HAS_PSUTIL = False

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
PASSWORD = "999"

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

def get_cpu_usage():
    out = run_ps("(Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples.CookedValue")
    try: return round(float(out), 1)
    except: return 0

def get_memory_info():
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
    out = run_ps('''
        try {
            $gpus = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -notmatch 'Oray|Remote|Virtual|Display|Basic' }
            if ($gpus) { $gpus[0].Name } else { (Get-CimInstance Win32_VideoController | Select-Object -First 1).Name }
        } catch { "N/A" }
    ''')
    return out if out else "N/A"

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
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
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
    mem = get_memory_info()
    cache = {
        "cpu": get_cpu_usage(), "mem": mem, "disks": get_disk_info(),
        "uptime": get_uptime(), "procs": get_process_count(),
        "net": net, "gpu": get_gpu_info(), "top": get_top_processes(8),
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
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Maple',-apple-system,sans-serif;background:#0a0a1a;color:#e0e0e0;padding:16px;padding-bottom:80px}
.hdr{text-align:center;padding:20px 0 16px;border-bottom:1px solid rgba(255,255,255,.08);margin-bottom:16px}
.hdr h1{font-size:20px;font-weight:600}
.hdr .sub{font-size:13px;color:#888;margin-top:4px}
.card{background:rgba(255,255,255,.04);border-radius:12px;padding:16px;margin-bottom:12px;border:1px solid rgba(255,255,255,.06)}
.ct{font-size:13px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px}
.bw{background:rgba(255,255,255,.08);border-radius:6px;height:22px;position:relative;overflow:hidden;margin-bottom:8px}
.bf{height:100%;border-radius:6px;transition:width .6s}
.bf.g{background:linear-gradient(90deg,#00c853,#69f0ae)}.bf.y{background:linear-gradient(90deg,#ffc107,#ffeb3b)}.bf.r{background:linear-gradient(90deg,#ff5252,#ff8a80)}
.bl{position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:12px;font-weight:600;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.5)}
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
.tabs{display:flex;gap:0;margin-bottom:12px;background:rgba(255,255,255,.04);border-radius:10px;overflow:hidden;border:1px solid rgba(255,255,255,.06)}
.tab{flex:1;padding:10px;text-align:center;font-size:13px;font-weight:500;cursor:pointer;color:#888;transition:all .2s}
.tab.active{background:rgba(105,240,174,.15);color:#69f0ae}
.tab-content{display:none}.tab-content.active{display:block}
.sbtn{display:inline-flex;align-items:center;gap:6px;padding:10px 18px;border:none;border-radius:8px;font-size:14px;font-weight:500;cursor:pointer;color:#fff;background:linear-gradient(135deg,#667eea,#764ba2);margin:4px}
.sbtn:disabled{opacity:.5}.sbtn:active{transform:scale(.95)}
.tbox{display:none;margin-top:12px;background:#0d0d0d;border-radius:8px;border:1px solid rgba(255,255,255,.1);overflow:hidden}
.thdr{background:rgba(255,255,255,.06);padding:8px 12px;font-size:12px;color:#888;display:flex;align-items:center;justify-content:space-between}
.thdr .x{background:none;border:none;color:#888;font-size:18px;cursor:pointer;padding:0 4px}
.tout{padding:12px;max-height:400px;overflow-y:auto;font-family:"Cascadia Mono","Consolas",monospace;font-size:13px;line-height:1.5;white-space:pre-wrap;word-break:break-all;color:#d4d4d4}
.tout::-webkit-scrollbar{width:6px}.tout::-webkit-scrollbar-thumb{background:rgba(255,255,255,.15);border-radius:3px}
.trow{display:flex;align-items:center;padding:8px 12px;border-top:1px solid rgba(255,255,255,.08);gap:8px}
.tp{color:#69f0ae;font-family:monospace;font-size:13px;white-space:nowrap}
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
.footer{position:fixed;bottom:0;left:0;right:0;text-align:center;padding:12px;background:rgba(10,10,26,.95);backdrop-filter:blur(10px);border-top:1px solid rgba(255,255,255,.06);font-size:12px;color:#666}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.ld{display:inline-block;width:6px;height:6px;background:#00c853;border-radius:50%;margin-right:4px;animation:pulse 2s infinite}
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
.exp-item{display:flex;align-items:center;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,.04);cursor:pointer;transition:background .15s;border-radius:4px}
.exp-item:hover{background:rgba(255,255,255,.06)}
.exp-item.selected{background:rgba(105,240,174,.15)}
.exp-item .exp-icon{width:24px;text-align:center;font-size:16px;margin-right:8px}
.exp-item .exp-name{flex:1;color:#ccc;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.exp-item .exp-size{width:80px;text-align:right;color:#888;font-size:12px}
.exp-item .exp-time{width:120px;text-align:right;color:#666;font-size:12px}
.exp-context{position:fixed;background:rgba(30,30,50,.95);border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:4px 0;min-width:160px;z-index:1000;display:none;box-shadow:0 4px 20px rgba(0,0,0,.5)}
.exp-context-item{padding:8px 16px;font-size:13px;color:#ccc;cursor:pointer;transition:background .15s}
.exp-context-item:hover{background:rgba(105,240,174,.15)}
.exp-context-divider{height:1px;background:rgba(255,255,255,.1);margin:4px 0}
.exp-status{display:flex;justify-content:space-between;padding:8px 10px;font-size:12px;color:#888;border-top:1px solid rgba(255,255,255,.06);margin-top:8px}

/* 磁盘大卡片 - 醒目可视化 */
.drive-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.drive-card{position:relative;overflow:hidden;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:0;cursor:pointer;transition:all .25s}
.drive-card:hover{border-color:rgba(105,240,174,.4);transform:translateY(-2px);box-shadow:0 8px 32px rgba(105,240,174,.1)}
.drive-card:active{transform:scale(.97)}
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
    <div id="expList" style="max-height:60vh;min-height:200px;overflow-y:auto;overflow-x:hidden;-webkit-overflow-scrolling:touch;touch-action:pan-y"></div>
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

<div class="footer"><span class="ld"></span> <span id="ts">{{TIMESTAMP}}</span></div>

<script>
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
  document.getElementById('expList').innerHTML='<div style="padding:40px;text-align:center;color:#888"><span class="loading">&#8635;</span> 加载磁盘...</div>';
  try{
    const qa=await(await fetch('/api/drives')).json();
    let dh='<div class="drive-grid">';
    qa.drives.forEach(d=>{
      const total=parseFloat(d.used)+parseFloat(d.free);
      const pct=total>0?Math.round(parseFloat(d.used)/total*100):0;
      const bc=pct<60?'g':pct<85?'y':'r';
      const r=36,circ=2*Math.PI*r,offset=circ-(pct/100)*circ;
      dh+='<div class="drive-card drive-'+bc+'" onclick="expGo(\''+d.letter+':\\')">';
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
    qa.drives.forEach(d=>{qh+='<div class="exp-quick-item" onclick="expGo(\''+d.letter+':\\')">&#128190; '+d.letter+':盘</div>';});
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

let procSortKey='cpu',procSortDir=-1;
function sortProcs(key){
  if(procSortKey===key)procSortDir*=-1;
  else{procSortKey=key;procSortDir=-1;}
  const sorted=[...allProcs].sort((a,b)=>{
    if(key==='name')return procSortDir*a.name.localeCompare(b.name);
    return procSortDir*((a[key]||0)-(b[key]||0));
  });
  renderProcs(sorted);
}
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
body{font-family:-apple-system,sans-serif;background:#0a0a1a;color:#e0e0e0;display:flex;justify-content:center;align-items:center;min-height:100vh}
.login-box{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:32px;width:320px;text-align:center}
.login-box h1{font-size:24px;margin-bottom:8px}
.login-box p{font-size:13px;color:#888;margin-bottom:24px}
.login-box input{width:100%;padding:12px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);border-radius:8px;color:#fff;font-size:16px;outline:none;margin-bottom:16px}
.login-box input:focus{border-color:#69f0ae}
.login-box button{width:100%;padding:12px;background:linear-gradient(135deg,#667eea,#764ba2);border:none;border-radius:8px;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
.login-box button:active{transform:scale(.98)}
.error{color:#ff8a80;font-size:13px;margin-bottom:12px;display:none}

/* 磁盘大卡片 - 醒目可视化 */

/* 颜色主题 */



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

let procSortKey='cpu',procSortDir=-1;
function sortProcs(key){
  if(procSortKey===key)procSortDir*=-1;
  else{procSortKey=key;procSortDir=-1;}
  const sorted=[...allProcs].sort((a,b)=>{
    if(key==='name')return procSortDir*a.name.localeCompare(b.name);
    return procSortDir*((a[key]||0)-(b[key]||0));
  });
  renderProcs(sorted);
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
        self.end_headers()
        self.wfile.write(json.dumps(d, ensure_ascii=False).encode("utf-8"))

    def respond(self, code, ct, body):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]}")

def main():
    net = get_network_info()
    set_keep_screen_alive(True)
    print(f"\n  PC Monitor: http://{net['ip']}:{PORT}\n")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        set_keep_screen_alive(False)
        print("\nStopped")
        server.server_close()

if __name__ == "__main__":
    main()
