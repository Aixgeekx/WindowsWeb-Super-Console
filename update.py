#!/usr/bin/env python3
"""
一键更新脚本 - 自动提交并推送到GitHub
用法: python update.py [提交信息]
"""

import subprocess
import sys
import os
from datetime import datetime

def run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout.strip() + r.stderr.strip()
    except Exception as e:
        return 1, str(e)

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    print("=" * 50)
    print("  PC Monitor - 自动提交并推送")
    print("=" * 50)
    print()
    
    # 检查git配置
    code, out = run("git config user.name")
    if code != 0 or not out:
        print("[错误] 请先配置git用户信息:")
        print("  git config --global user.name \"你的名字\"")
        print("  git config --global user.email \"你的邮箱\"")
        sys.exit(1)
    
    # 检查修改
    print("[1/4] 检查修改文件...")
    code, out = run("git status -s")
    if not out:
        print("[提示] 没有需要提交的修改")
        sys.exit(0)
    print(out)
    print()
    
    # 暂存
    print("[2/4] 暂存修改...")
    run("git add -A")
    
    # 提交信息
    if len(sys.argv) > 1:
        commit_msg = " ".join(sys.argv[1:])
    else:
        commit_msg = f"Update: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    print(f"[3/4] 提交: {commit_msg}")
    code, out = run(f'git commit -m "{commit_msg}"')
    if code != 0:
        print("[错误] 提交失败:", out)
        sys.exit(1)
    print(out)
    
    # 推送
    print("[4/4] 推送到GitHub...")
    code, out = run("git push origin main")
    if code != 0:
        code, out = run("git push origin master")
    if code != 0:
        print("[错误] 推送失败:", out)
        print("可能需要先设置远程仓库或认证")
        sys.exit(1)
    print(out)
    
    print()
    print("=" * 50)
    print("  完成! GitHub Actions将自动打包exe")
    print("  查看: https://github.com/Aixgeekx/WindowsWeb-Super-Console/actions")
    print("=" * 50)

if __name__ == "__main__":
    main()
