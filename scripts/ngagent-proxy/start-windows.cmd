@echo off
setlocal

cd /d "%~dp0"

if not exist ".env" (
  if exist ".env.windows.example" (
    copy ".env.windows.example" ".env" >nul
    echo Created .env from .env.windows.example
    echo Edit .env and set LOCAL_PROXY_KEY before exposing this proxy to other tools.
  ) else (
    echo Missing .env and .env.windows.example
    exit /b 1
  )
)

node src\server.js
