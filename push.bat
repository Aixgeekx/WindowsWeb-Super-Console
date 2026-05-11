@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   PC Monitor - 自动提交并推送
echo ========================================
echo.

:: 检查git配置
git config user.name >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 请先配置git用户信息:
    echo   git config --global user.name "你的名字"
    echo   git config --global user.email "你的邮箱"
    pause
    exit /b 1
)

:: 显示当前状态
echo [1/4] 检查修改文件...
git status -s
echo.

:: 暂存所有修改
echo [2/4] 暂存修改...
git add -A

:: 获取修改时间作为提交信息
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set mydate=%%a-%%b-%%c
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set mytime=%%a:%%b
set commit_msg=Update: %mydate% %mytime%

:: 提交
echo [3/4] 提交: %commit_msg%
git commit -m "%commit_msg%"
if %errorlevel% neq 0 (
    echo [提示] 没有需要提交的修改
    pause
    exit /b 0
)

:: 推送
echo [4/4] 推送到GitHub...
git push origin main 2>&1
if %errorlevel% neq 0 (
    git push origin master 2>&1
)

echo.
echo ========================================
echo   完成! GitHub Actions将自动打包exe
echo   查看: https://github.com/Aixgeekx/WindowsWeb-Super-Console/actions
echo ========================================
echo.
pause
