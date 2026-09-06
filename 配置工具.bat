@echo off
chcp 65001 >nul
title shijuefenxi config tool
setlocal

set "BAT_DIR=%~dp0"
set "SCRIPT=%BAT_DIR%scripts\config_server.py"

if not exist "%SCRIPT%" goto missing

rem 优先使用 Python 3.8（pyw 窗口模式），没有 pyw 时退回系统 pythonw
set "CMD="
where pyw >nul 2>nul && set "CMD=pyw -3.8"
if not defined CMD where pythonw >nul 2>nul && set "CMD=pythonw"
if not defined CMD goto no_python

start "" %CMD% "%SCRIPT%" --open
echo 配置工具已启动，浏览器即将自动打开。关闭浏览器后，在网页点「退出」即可。
timeout /t 3 >nul
exit /b 0

:missing
echo [错误] 找不到 "%SCRIPT%"
echo 请把整个技能目录放在合适位置后，双击该目录内的 配置工具.bat。
pause
exit /b 1

:no_python
echo [错误] 未找到 Python（pyw / pythonw）。请安装 Python 3.8 并勾选 Add to PATH。
pause
exit /b 1