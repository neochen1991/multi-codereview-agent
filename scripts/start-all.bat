@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
for %%I in ("%ROOT_DIR%") do set "ROOT_DIR=%%~fI"

set "RUN_DIR=%ROOT_DIR%\run"
set "LOG_DIR=%ROOT_DIR%\logs"
set "BACKEND_LOG=%LOG_DIR%\backend.log"
set "FRONTEND_LOG=%LOG_DIR%\frontend.log"
set "BACKEND_PID_FILE=%RUN_DIR%\backend.pid"
set "FRONTEND_PID_FILE=%RUN_DIR%\frontend.pid"

if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

set "BACKEND_CMD=cd /d ""%ROOT_DIR%"" && "".venv\Scripts\python.exe"" -m uvicorn app.main:app --app-dir ""%ROOT_DIR%\backend"" --reload --port 8011 >> ""%BACKEND_LOG%"" 2>&1"
set "FRONTEND_CMD=cd /d ""%ROOT_DIR%\frontend"" && npm run dev -- --host 127.0.0.1 --port 5174 >> ""%FRONTEND_LOG%"" 2>&1"

call :start_process "backend" "%BACKEND_PID_FILE%" "%BACKEND_CMD%"
call :start_process "frontend" "%FRONTEND_PID_FILE%" "%FRONTEND_CMD%"

echo started backend on http://127.0.0.1:8011
echo started frontend on http://127.0.0.1:5174
echo logs:
echo   backend  %BACKEND_LOG%
echo   frontend %FRONTEND_LOG%
exit /b 0

:start_process
set "LABEL=%~1"
set "PID_FILE=%~2"
set "COMMAND=%~3"
shift
shift

if exist "%PID_FILE%" (
  set /p EXISTING_PID=<"%PID_FILE%"
  tasklist /FI "PID eq !EXISTING_PID!" | findstr /R /C:" !EXISTING_PID! " >nul 2>nul
  if not errorlevel 1 (
    echo %LABEL% already running with pid !EXISTING_PID!
    exit /b 0
  )
  del /q "%PID_FILE%" >nul 2>nul
)

set "CODEREVIEW_START_CMD=%COMMAND%"
set "CODEREVIEW_PID_FILE=%PID_FILE%"
powershell -NoProfile -Command ^
  "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $env:CODEREVIEW_START_CMD -WorkingDirectory '%ROOT_DIR%' -PassThru;" ^
  "Set-Content -Path $env:CODEREVIEW_PID_FILE -Value $p.Id"

if errorlevel 1 (
  echo failed to start %LABEL%
  exit /b 1
)
exit /b 0
