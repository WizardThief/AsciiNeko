@echo off
title Applica Sfondo Neko // Dietro le icone di Windows (Lively + Bridge)
cd /d "%~dp0"
echo ==========================================================
echo   Attivazione Sfondo Neko (dietro le cartelle di Windows)
echo ==========================================================

REM 1. Chiudi eventuali finestre vecchie e riavvia il Bridge Python invisibile
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ai_monitor.py*' -or $_.CommandLine -like '*desktop_theme.py*' -or $_.CommandLine -like '*.desktop_layer_profile*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

start "" pythonw "%~dp0bridge\ai_monitor.py"
timeout /t 1 /nobreak >nul

REM 2. Imposta lo sfondo http://127.0.0.1:8765/ dentro Lively Wallpaper (livello WorkerW dietro le cartelle vere di Windows)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$layoutPath = Join-Path $env:LOCALAPPDATA 'Lively Wallpaper\WallpaperLayout.json'; ^
   $nekoWp = Join-Path $env:LOCALAPPDATA 'Lively Wallpaper\Library\SaveData\wptmp\5nmg4k0x.q4a'; ^
   if ((Test-Path $layoutPath) -and (Test-Path $nekoWp)) { ^
     $json = Get-Content $layoutPath -Raw | ConvertFrom-Json; ^
     foreach ($item in $json) { $item.LivelyInfoPath = $nekoWp }; ^
     $json | ConvertTo-Json -Depth 10 | Set-Content $layoutPath -Encoding UTF8; ^
   }; ^
   $livelyExe = 'C:\Program Files\Lively Wallpaper\Lively.exe'; ^
   if (Test-Path $livelyExe) { ^
     Get-Process -Name 'Lively*' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue; ^
     Start-Sleep -Milliseconds 500; ^
     Start-Process $livelyExe; ^
     Write-Host 'Sfondo applicato dietro le icone di Windows tramite Lively!' -ForegroundColor Green ^
   }"

echo.
timeout /t 2
