@echo off
cd /d "%~dp0"
echo Installing project dependencies...
py -m pip install -e .
if errorlevel 1 (
  echo Dependency installation failed. Check Python and internet connection.
  pause
  exit /b 1
)
echo.
echo Starting ASHADhwani at http://127.0.0.1:8000
py -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
pause
