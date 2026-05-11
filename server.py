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
    out = run_ps('try { (Get-CimInstance Win32_VideoController | Select-Object -First 1).Name } catch { "N/A" }')
    return out

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

def list_directory(path):
    if not os.path.isdir(path):
        return None, f"Not found: {path}"
    items = []
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                is_dir = os.path.isdir(full)
                size = 0 if is_dir else os.path.getsize(full)
                mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(full)))
                items.append({"name": name, "dir": is_dir, "size": size, "time": mtime})
            except:
                items.append({"name": name, "dir": False, "size": 0, "time": "?"})
    except PermissionError:
        return [], "Access denied"
    except Exception as e:
        return [], str(e)
    items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return items, None

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
  <div class="card"><div class="ct">TOP PROCESSES</div><div class="pr ph"><span class="pn">Name</span><span class="pc">CPU</span><span class="pm">Memory</span></div>{{PROCS}}</div>
</div>

<!-- Files -->
<div class="tab-content" id="tab-files">
  <div class="card">
    <div class="ct">FILE BROWSER</div>
    <div id="fbPath" style="font-size:13px;color:#69f0ae;margin-bottom:10px;font-family:monospace">Loading...</div>
    <div class="flist" id="fileList"></div>
  </div>
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
  if(n==='files'&&!loaded)loadDrives();
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

async function openDir(path){
  try{
    const r=await fetch('/api/files?p='+encodeURIComponent(path));
    const d=await r.json();
    if(d.error){alert(d.error);return}
    document.getElementById('fbPath').textContent=d.path;
    const c=document.getElementById('fileList');
    let h='<div class="fitem" onclick="loadDrives()"><span class="ficon">&#128194;</span><span class="fname" style="color:#69f0ae">Back to Drives</span></div>';
    d.items.forEach(i=>{
      if(i.dir){
        const fp=d.path.replace(/\\$/,'')+'\\'+i.name;
        h+='<div class="fitem" data-path="'+fp+'" onclick="openDir(this.dataset.path)">';
        h+='<span class="ficon">&#128193;</span><span class="fname">'+i.name+'</span><span class="fdate">'+i.time+'</span></div>';
      }else{
        const sz=i.size>1048576?(i.size/1048576).toFixed(1)+'MB':i.size>1024?(i.size/1024).toFixed(0)+'KB':i.size+'B';
        h+='<div class="fitem"><span class="ficon">&#128196;</span><span class="fname">'+i.name+'</span><span class="fsize">'+sz+'</span><span class="fdate">'+i.time+'</span></div>';
      }
    });
    c.innerHTML=h;
  }catch(e){alert('Network error')}
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

        if path == "/api/terminal":
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
