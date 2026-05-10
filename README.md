# Windows Web Super Console

通过手机浏览器远程查看和控制你的 Windows 电脑。

![Python](https://img.shields.io/badge/Python-3.x-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey)
![No Dependencies](https://img.shields.io/badge/Dependencies-None-brightgreen)

## 功能

- **📊 实时状态监控** - CPU、内存、磁盘、进程数、显卡信息
- **📸 远程截屏** - 一键截取电脑屏幕，在手机上查看
- **⌨️ Web PowerShell** - 在浏览器中执行 PowerShell 命令
- **📁 文件浏览** - 浏览电脑文件目录，查看文件信息
- **🚀 开机自启** - 电脑登录后自动运行

## 快速开始

### 1. 下载

```bash
git clone https://github.com/Aixgeekx/WindowsWeb-Super-Console.git
cd WindowsWeb-Super-Console
```

### 2. 运行

```bash
python server.py
```

默认端口 `9999`，自定义端口：

```bash
python server.py 8080
```

### 3. 访问

- **本机**: http://127.0.0.1:9999
- **手机**: http://你的电脑IP:9999

> 💡 确保手机和电脑在同一局域网（同一 WiFi）

## 页面说明

### Status 标签页
- CPU / 内存使用率（实时进度条）
- 磁盘使用情况（显示剩余空间）
- 占用最高的 Top 进程
- GPU 信息

### Files 标签页
- 浏览电脑文件目录
- 点击文件夹进入
- 点击 `..` 返回上级
- 显示文件大小和修改时间

### Tools 标签页
- **Screenshot** - 截取电脑屏幕，点击图片可下载
- **PowerShell** - 在线执行命令（支持命令历史，上下箭头翻阅）

## 开机自启

双击 `autostart.vbs` 会将启动脚本添加到 Windows 启动文件夹，每次登录自动运行。

### 手动设置

```powershell
# 创建计划任务
schtasks /create /tn "PCStatusMonitor" /tr "pythonw C:\path\to\server.py" /sc onlogon /rl highest /f

# 删除计划任务
schtasks /delete /tn "PCStatusMonitor" /f
```

### 管理命令

```powershell
# 查看任务状态
schtasks /query /tn "PCStatusMonitor"

# 手动启动
schtasks /run /tn "PCStatusMonitor"

# 手动停止
schtasks /end /tn "PCStatusMonitor"
```

## 安全说明

PowerShell 终端禁止执行以下危险命令：
- `Remove-Item` / `rmdir` / `del`
- `format`
- `shutdown` / `Restart-Computer`

## 技术栈

- Python 3（标准库，无需安装第三方包）
- PowerShell（系统信息采集）
- 纯 HTML/CSS/JS（前端）

## 截图

访问 http://你的电脑IP:9999 查看效果

## License

MIT
