@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PORT=8765"
set "CMD_RUNNER="
set "CMD_ARGS="
set "EXIT_CODE=1"

echo ========================================
echo Stop Dashboard Service
echo ========================================
echo.

where python >nul 2>nul
if %errorlevel%==0 (
  set "CMD_RUNNER=python"
  set "CMD_ARGS=scripts\dashboard_service.py stop --port %PORT% %*"
) else (
  where py >nul 2>nul
  if %errorlevel%==0 (
    set "CMD_RUNNER=py"
    set "CMD_ARGS=-3 scripts\dashboard_service.py stop --port %PORT% %*"
  )
)

if not defined CMD_RUNNER (
  echo [ERROR] Python or py launcher was not found.
  echo [ERROR] The dashboard service could not be stopped.
  goto :finish
)

echo Stopping service on port %PORT%...
echo.

%CMD_RUNNER% %CMD_ARGS%
set "EXIT_CODE=%errorlevel%"
echo.

if "%EXIT_CODE%"=="0" (
  echo [OK] Stop command finished successfully.
  echo Check the message above to confirm whether the service was stopped or was already not running.
) else (
  echo [ERROR] Stop command failed with exit code %EXIT_CODE%.
  echo Check the message above for details.
)

:finish
echo.
pause
endlocal
