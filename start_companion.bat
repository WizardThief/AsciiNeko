@echo off
title ASCII AI Companion // Launcher
cd /d "%~dp0"
echo ==========================================================
echo   Avvio di ASCII AI Companion Bridge + Anteprima Live...
echo ==========================================================

REM Chiude eventuali istanze precedenti di ai_monitor.py per evitare duplicati
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -like '*ai_monitor.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM Avvia il bridge in background silenzioso (pythonw)
start "" pythonw "%~dp0bridge\ai_monitor.py"
timeout /t 1 /nobreak >nul
start "" "http://127.0.0.1:8765/"
echo Bridge avviato in background su http://127.0.0.1:8765/
