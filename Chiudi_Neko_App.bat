@echo off
title ASCII AI Companion // Chiudi Neko Desktop App
echo ==========================================================
echo   Chiusura di Neko Desktop App e del Bridge in background...
echo ==========================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like '*ASCII AI Companion*' -or $_.MainWindowTitle -like '*Neko Desktop*' } | ForEach-Object { $_.CloseMainWindow() | Out-Null }; ^
   $procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*ai_monitor.py*' -or $_.CommandLine -like '*app_window.py*') }; ^
   if ($procs) { ^
     $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host (' -> Terminato PID ' + $_.ProcessId) }; ^
   }; ^
   Write-Host 'Neko Desktop App e Bridge chiusi con successo!' -ForegroundColor Green"

echo.
timeout /t 2
