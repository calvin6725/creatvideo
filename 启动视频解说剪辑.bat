@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PORT=9100"
set "PAGE=/video.html"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  echo 未安装 AI 引擎，使用系统 Python 启动（轻量模式）...
  echo 如需 AI 声音克隆，请先双击“安装AI克隆引擎.bat”。
  set "PY=python"
)

"%PY%" tools\launch.py --port %PORT% --page "%PAGE%"
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo 启动失败（错误码 %CODE%）。日志见 logs\server.log，也可手动执行：
  echo   .venv\Scripts\python.exe voice_server.py --port %PORT%
  pause
)
exit /b %CODE%