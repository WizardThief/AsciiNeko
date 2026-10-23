@echo off
setlocal
set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

REM Launch with pythonw.exe if available to suppress console window flashing
where pythonw >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" pythonw -m neko_bot.ui.quick_bar %*
) else (
    start "" python -m neko_bot.ui.quick_bar %*
)
endlocal
