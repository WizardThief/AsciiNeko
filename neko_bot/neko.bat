@echo off
setlocal

:: Resolve project root directory containing this batch file
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Ensure PYTHONPATH includes project root so neko_bot module is importable
set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"

:: Enforce UTF-8 encoding for accents and symbols
set "PYTHONIOENCODING=utf-8"

:: Locate Python executable
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PY_EXE=python"
) else (
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        echo [ERROR] Python not found in PATH or standard installation directory. >&2
        exit /b 1
    )
)

:: Forward all arguments with preserved quoting
"%PY_EXE%" -m neko_bot %*
set "EXIT_CODE=%ERRORLEVEL%"

exit /b %EXIT_CODE%
