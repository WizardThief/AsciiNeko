@echo off
title ASCII AI Companion // Stop Bridge
echo ==========================================================
echo   Chiusura del processo Python (ai_monitor.py)...
echo ==========================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$procs = Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -like '*ai_monitor.py*' }; ^
   if ($procs) { ^
     $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host (' -> Terminato PID ' + $_.ProcessId) }; ^
     Write-Host 'Bridge Python chiuso con successo!' -ForegroundColor Green ^
   } else { ^
     Write-Host 'Nessun processo ai_monitor.py attivo trovato.' -ForegroundColor Yellow ^
   }"

echo.
timeout /t 2
