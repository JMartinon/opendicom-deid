@echo off
setlocal
rem This applies only to this PowerShell process; it does not change machine policy.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %*
set "build_result=%ERRORLEVEL%"
if not "%build_result%"=="0" echo Build failed. Read the error above; no verified application was produced.
echo.
pause
exit /b %build_result%
