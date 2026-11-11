@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PORT=8000"

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% .*LISTENING"') do set "PIDFOUND=%%a"
if not defined PIDFOUND (
  if exist ".venv\Scripts\python.exe" (
    start "声线实验室服务" /min ".venv\Scripts\python.exe" voice_server.py --port %PORT%
  ) else (
    start "声线实验室服务" /min python voice_server.py --port %PORT%
  )
  timeout /t 2 /nobreak >nul
)
start "" "http://localhost:%PORT%/video.html"
exit /b 0
