@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run the main starter ^(start-host.bat^) once, then try again.
  pause
  exit /b 1
)
echo Installing Laya ^(open decision engine, ~1.3 GB download^)...
".venv\Scripts\python" -m pip install --quiet -e ".[laya]"
if errorlevel 1 (
  echo.
  echo  [!] Installation failed - check your internet connection.
  pause
  exit /b 1
)
echo.
echo Done. Laya is optional and runs 100% locally - restart the host to use it.
pause
