@echo off
chcp 65001 >nul
title Avvio Neko Desktop App
cd /d "%~dp0"

echo ==========================================================
echo   🐾 AVVIO NEKO DESKTOP APP ^& ASSISTENTE INTELLIGENTE
echo   - Finestra Dashboard + Mini-Widget [🗕 MINI]
echo   - Scorciatoia Globale: Alt + N (configurabile in ⚙️ Impostazioni)
echo ==========================================================

:: Chiude eventuali vecchie istanze per riavviare la finestra nativa pywebview + server unificato
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*app_window.py*' -or $_.CommandLine -like '*ai_monitor.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

start "" pythonw "%~dp0bridge\app_window.py"
exit
