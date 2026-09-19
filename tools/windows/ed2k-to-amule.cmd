@echo off
REM ed2k:// protocol handler -> opens the aMule dashboard with the link in the URL fragment.
REM No password or token is stored here: the dashboard tab uses your existing browser session.
REM Edit DASHBOARD_URL below, then double-click register-ed2k-handler.reg (or run it from an admin prompt).

set "DASHBOARD_URL=http://192.168.1.10:8078"

set "LINK=%~1"
if "%LINK%"=="" (
  echo Usage: %~nx0 "ed2k://|file|...|/"
  exit /b 1
)
REM Browsers pass the raw link; keep it as-is, the dashboard decodes and validates it.
start "" "%DASHBOARD_URL%/#add=%LINK%"
