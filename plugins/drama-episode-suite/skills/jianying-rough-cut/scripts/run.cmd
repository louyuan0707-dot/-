@echo off
setlocal
set "TASK_PYTHON=%USERPROFILE%\.codex\envs\jianying-rough-cut\Scripts\python.exe"

if not exist "%TASK_PYTHON%" (
  echo Missing configured Python environment: %TASK_PYTHON%
  exit /b 2
)
"%TASK_PYTHON%" "%~dp0run.py" %*
exit /b %ERRORLEVEL%
