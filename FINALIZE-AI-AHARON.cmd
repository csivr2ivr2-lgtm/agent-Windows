@echo off
setlocal
cd /d "%~dp0"

fltmc >nul 2>&1
if not "%errorlevel%"=="0" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\finalize-ai-aharon.ps1"
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" (
  echo ai aharon finalizer completed.
) else (
  echo ai aharon finalizer found blockers. Exit code: %RC%
)
pause
exit /b %RC%
