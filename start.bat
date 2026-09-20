@echo off
rem Double-click launcher for the Marathon Training app.
rem Runs start.ps1 with the execution policy bypassed for this one process only, so nothing on
rem your system changes. Extra arguments are passed through, e.g.:  start.bat -Dev   start.bat -Port 8100
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set RC=%errorlevel%
if not "%RC%"=="0" (
    echo.
    echo Marathon stopped with an error. See the messages above.
    pause
)
exit /b %RC%
