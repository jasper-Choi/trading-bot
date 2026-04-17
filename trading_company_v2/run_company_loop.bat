@echo off
setlocal
cd /d %~dp0

if not exist .venv (
  python -m venv .venv
)

if not exist .venv\Scripts\python.exe (
  echo [error] .venv\Scripts\python.exe not found
  pause
  exit /b 1
)

if not exist .venv\.deps_ready (
  echo [info] installing dependencies for first run
  .venv\Scripts\python.exe -m pip install --upgrade pip
  if errorlevel 1 (
    echo [error] pip upgrade failed
    pause
    exit /b 1
  )

  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [error] dependency install failed
    pause
    exit /b 1
  )

  type nul > .venv\.deps_ready
)

echo [info] starting 15-minute company loop
.venv\Scripts\python.exe -m app.runtime

if errorlevel 1 (
  echo [error] app.runtime exited with failure
  pause
  exit /b 1
)
