@echo off
setlocal
cd /d "%~dp0"

set "PORT=8765"

where python >nul 2>nul
if %errorlevel%==0 (
  python scripts\dashboard_service.py start --port %PORT% %*
) else (
  py -3 scripts\dashboard_service.py start --port %PORT% %*
)

endlocal
