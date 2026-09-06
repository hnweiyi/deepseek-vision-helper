@echo off
chcp 65001 >nul
title shijuefenxi config tool
setlocal

set "BAT_DIR=%~dp0"
set "SCRIPT=%BAT_DIR%scripts\config_server.py"

if not exist "%SCRIPT%" (
    echo [错误] 找不到 "%SCRIPT%"
    echo 请把整个技能目录放在合适位置后，双击该目录内的 配置工具.bat。
    pause
    exit /b 1
)

start "" pythonw "%SCRIPT%" --open
echo 配置工具已启动，浏览器即将自动打开。关闭浏览器后，在网页点「退出」即可。
timeout /t 3 >nul
exit /b 0