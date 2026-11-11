@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PORT=8000"
rem 文件里是 UTF-8 内容，这里明确用 UTF-8 代码页，避免 PowerShell 参数里的中文变乱码
chcp 65001 >nul

rem 找一个正在监听 8000 的进程；没有就启动本机服务
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% .*LISTENING"') do set "PIDFOUND=%%a"
if not defined PIDFOUND (
  if exist ".venv\Scripts\python.exe" (
    echo 正在启动声线实验室（AI 克隆引擎）...
    start "声线实验室服务" /min ".venv\Scripts\python.exe" voice_server.py --port %PORT%
  ) else (
    echo 未安装 AI 引擎，使用系统 Python 启动（轻量模式）...
    echo 如需 AI 声音克隆，请先双击“安装AI克隆引擎.bat”。
    start "声线实验室服务" /min python voice_server.py --port %PORT%
  )
  timeout /t 2 /nobreak >nul
)
start "" "http://localhost:%PORT%/"
exit /b 0
