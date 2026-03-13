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
set "VENV_PYTHON=%ROOT_DIR%\.venv\Scripts\python.exe"
set "FRONTEND_NODE_MODULES=%ROOT_DIR%\frontend\node_modules"

if not exist "%RUN_DIR%" mkdir "%RUN_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

call :check_python || exit /b 1
call :ensure_backend_dependencies || exit /b 1
call :check_node || exit /b 1
call :ensure_frontend_dependencies || exit /b 1

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

:check_python
if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" --version >nul 2>nul
  if errorlevel 1 (
    echo detected .venv but python failed to run: %VENV_PYTHON%
    exit /b 1
  )
  exit /b 0
)

echo missing python virtual environment: %VENV_PYTHON%
echo please create it first, for example:
echo   py -3.11 -m venv .venv
echo   .venv\Scripts\python.exe -m pip install -e .
exit /b 1

:check_node
where node >nul 2>nul
if errorlevel 1 (
  echo node.js is not installed or not in PATH
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo npm is not installed or not in PATH
  exit /b 1
)
exit /b 0

:ensure_backend_dependencies
echo checking backend dependencies...
"%VENV_PYTHON%" -c "import sys, httpx, fastapi, pydantic, yaml; parts = tuple(int(p) for p in httpx.__version__.split('.')[:2]); sys.exit(0 if parts >= (0, 27) else 1)" >nul 2>nul
if not errorlevel 1 (
  exit /b 0
)

echo backend dependencies missing or outdated, running pip install -e .
pushd "%ROOT_DIR%" >nul
call "%VENV_PYTHON%" -m pip install -e .
set "PIP_EXIT=%ERRORLEVEL%"
popd >nul

if not "%PIP_EXIT%"=="0" (
  echo backend dependency install failed
  exit /b 1
)
exit /b 0

:ensure_frontend_dependencies
if exist "%FRONTEND_NODE_MODULES%" (
  exit /b 0
)

echo frontend dependencies missing, running npm install...
pushd "%ROOT_DIR%\frontend" >nul
call npm install
set "NPM_EXIT=%ERRORLEVEL%"
popd >nul

if not "%NPM_EXIT%"=="0" (
  echo npm install failed
  exit /b 1
)
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
