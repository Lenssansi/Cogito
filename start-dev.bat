@echo off
cd /d "%~dp0"

REM Cogito dev launcher (double-click). Electron auto-starts backend + Vite, then opens the window.
if not exist "backend\.venv\Scripts\python.exe" (
  echo [!] backend venv not found. First-time setup, run once:
  echo       python -m venv backend\.venv
  echo       backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
  pause
  exit /b 1
)
if not exist "app\node_modules" (
  echo [!] frontend deps not found. First-time setup: cd app, then npm install
  pause
  exit /b 1
)

echo Starting Cogito ... Electron will auto-start backend + Vite, then open the window.
cd app
call npm run dev
