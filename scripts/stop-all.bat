@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
for %%I in ("%ROOT_DIR%") do set "ROOT_DIR=%%~fI"

set "RUN_DIR=%ROOT_DIR%\run"
set "BACKEND_PID_FILE=%RUN_DIR%\backend.pid"
set "FRONTEND_PID_FILE=%RUN_DIR%\frontend.pid"

call :stop_process "backend" "%BACKEND_PID_FILE%"
call :stop_process "frontend" "%FRONTEND_PID_FILE%"
exit /b 0

:stop_process
set "LABEL=%~1"
set "PID_FILE=%~2"

if not exist "%PID_FILE%" (
  echo %LABEL% not running
  exit /b 0
)

set /p PID=<"%PID_FILE%"
tasklist /FI "PID eq %PID%" | findstr /R /C:" %PID% " >nul 2>nul
if errorlevel 1 (
  echo %LABEL% pid %PID% already exited
  del /q "%PID_FILE%" >nul 2>nul
  exit /b 0
)

taskkill /PID %PID% /T /F >nul 2>nul
if errorlevel 1 (
  echo failed to stop %LABEL% (%PID%)
  exit /b 1
)

echo stopped %LABEL% (%PID%)
del /q "%PID_FILE%" >nul 2>nul
exit /b 0
