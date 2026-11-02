@echo off
setlocal
cd /d "%~dp0"
title Jev Agent - local host

rem ============================================================
rem  Double-click starter. Prepares everything and runs the host.
rem ============================================================

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
if errorlevel 1 (
  echo.
  echo  [!] Python 3.12 or newer is required.
  echo      Install it from https://www.python.org/downloads/
  echo      IMPORTANT: tick "Add python.exe to PATH" in the installer.
  echo      Then double-click this file again.
  echo.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo.
  echo  First run: preparing the agent. About one minute, internet needed...
  python -m venv .venv
  ".venv\Scripts\python" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python" -m pip install --quiet -e ".[documents]"
  if errorlevel 1 (
    echo.
    echo  [!] Installation failed. Check your internet connection and try again.
    echo.
    pause
    exit /b 1
  )
)

rem Earlier templates shipped a wrong NVIDIA model id (zai/ instead of z-ai/): fix it in place.
if exist ".env" (
  findstr /C:"zai/glm-5.3" ".env" >nul 2>nul && (
    powershell -NoProfile -Command "(Get-Content .env) -replace 'zai/glm-5.3','z-ai/glm-5.3' | Set-Content .env" >nul 2>nul
    echo  Fixed an outdated model id in .env ^(zai/ -^> z-ai/^).
  )
)

if not exist ".env" (
  copy /y "env-template.txt" ".env" >nul
  echo.
  echo  Settings file created: .env  -  opening it in Notepad now.
  echo.
  echo  NEXT - paste ONE API key. Easiest option, free, 2 minutes:
  echo    1. Open  https://console.groq.com/keys  and log in
  echo    2. Click "Create API Key" and copy it
  echo    3. In Notepad, replace  PASTE-YOUR-GROQ-KEY-HERE  with your key
  echo    4. Save with Ctrl+S and close Notepad
  echo    5. Double-click this file again
  echo.
  start notepad ".env"
  pause
  exit /b 0
)

findstr /C:"PASTE-YOUR-GROQ-KEY-HERE" ".env" >nul 2>nul
if not errorlevel 1 (
  findstr /C:"PASTE-YOUR-NVIDIA-KEY-HERE" ".env" >nul 2>nul
  if not errorlevel 1 (
    echo.
    echo  [!] No API key configured yet.
    echo      The file .env is opening in Notepad.
    echo      Paste at least one key - free Groq key: https://console.groq.com/keys
    echo      Save, close, and double-click this file again.
    echo.
    start notepad ".env"
    pause
    exit /b 1
  )
)

echo.
echo  Host starting. KEEP THIS WINDOW OPEN while you use the sidebar.
echo.
echo  Now in Firefox:
echo    1. Type  about:debugging  in the address bar and press Enter
echo    2. Click "This Firefox"  then  "Load Temporary Add-on..."
echo    3. Open this folder, then the "extension" folder, pick "manifest.json"
echo    4. Open the Jev sidebar with the toolbar button
echo.
".venv\Scripts\jev-firefox"
echo.
echo  The host stopped.
echo.
pause
exit /b 0
