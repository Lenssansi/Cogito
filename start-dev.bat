@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ── Cogito 开发启动键 ── 双击即可。Electron 会自动拉起后端 + Vite 并开窗。
if not exist "backend\.venv\Scripts\python.exe" (
  echo [!] backend venv not found. First-time setup, run once:
  echo       python -m venv backend\.venv
  echo       backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
  pause
  exit /b 1
)
if not exist "app\node_modules" (
  echo [!] frontend deps not found. First-time setup, run once:  cd app  then  npm install
  pause
  exit /b 1
)

echo Starting Cogito ... Electron will auto-start backend + Vite, then open the window.
cd app
call npm run dev
