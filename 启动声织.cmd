@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if defined VOICE_LOOM_PYTHON (
  set "VOICE_LOOM_LAUNCH_PYTHON=%VOICE_LOOM_PYTHON%"
) else if exist "%~dp0.venv\Scripts\python.exe" (
  set "VOICE_LOOM_LAUNCH_PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
  set "VOICE_LOOM_LAUNCH_PYTHON=G:\AI\seedvc\venv\Scripts\python.exe"
)
if not exist "%VOICE_LOOM_LAUNCH_PYTHON%" (
  echo 未找到 Python。请设置 VOICE_LOOM_PYTHON，详见 README.md。
  pause
  exit /b 1
)
"%VOICE_LOOM_LAUNCH_PYTHON%" "%~dp0launcher.py" %*
if errorlevel 1 pause
endlocal
