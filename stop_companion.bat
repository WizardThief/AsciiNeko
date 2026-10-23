@echo off
title ASCII AI Companion // Stacca Tema Desktop & Stop Bridge
echo ==========================================================
echo   Disattivazione Tema Desktop Neko e chiusura Bridge...
echo ==========================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like '*ASCII AI Companion*' } | ForEach-Object { $_.CloseMainWindow() | Out-Null }; ^
   $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ai_monitor.py*' -or $_.CommandLine -like '*desktop_theme.py*' -or $_.CommandLine -like '*.desktop_layer_profile*' }; ^
   if ($procs) { ^
     $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host (' -> Terminato PID ' + $_.ProcessId) }; ^
   }; ^
   Write-Host 'Tema Desktop Neko staccato e Bridge chiuso con successo!' -ForegroundColor Green"

echo.
timeout /t 2
