@echo off
REM VOXCUT launcher (Windows). Bootstraps uv, syncs deps, starts the server.
setlocal
cd /d "%~dp0..\backend"

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv...
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo Preparing environment...
uv sync --python 3.12 --quiet

set "PORT=8484"
if not "%VOXCUT_PORT%"=="" set "PORT=%VOXCUT_PORT%"

start "" /b cmd /c "timeout /t 3 >nul & for /f %%t in ('uv run --quiet python -c \"from voxcut.config import settings; print(settings().session_token)\"') do start http://127.0.0.1:%PORT%/?t=%%t"

echo Starting VOXCUT on http://127.0.0.1:%PORT%/
uv run --quiet uvicorn voxcut.main:app --host 127.0.0.1 --port %PORT%
