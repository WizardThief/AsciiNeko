@echo off
title Push AsciiNeko to GitHub
cd /d "%~dp0"
echo ==========================================================
echo   Autenticazione GitHub e caricamento su:
echo   https://github.com/WizardThief/AsciiNeko.git
echo ==========================================================
echo.
git credential-manager github login
echo.
git push -u origin main
echo.
pause
