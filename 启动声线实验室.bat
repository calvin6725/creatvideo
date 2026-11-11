@echo off
setlocal
cd /d "%~dp0"
set "PORT=8000"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% .*LISTENING"') do set "PID=%%a"
if not defined PID (
  start "声线实验室服务" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0voice_server.ps1" -Port %PORT%
  timeout /t 1 /nobreak >nul
)
start "" "http://localhost:%PORT%/"
exit /b 0
