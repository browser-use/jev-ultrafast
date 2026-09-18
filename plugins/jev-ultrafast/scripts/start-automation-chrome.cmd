@echo off
rem Start (or reuse) the dedicated automation Chrome that jev-ultrafast drives.
rem Keep PORT and PROFILE in sync with BU_CDP_URL in the project .env.
rem The anti-throttling flags keep background/occluded windows rendering, which
rem Page.captureScreenshot needs (the inspector UI captures screenshots).
setlocal
if "%JEV_CHROME_PORT%"=="" (set PORT=9222) else (set PORT=%JEV_CHROME_PORT%)
if "%JEV_CHROME_PROFILE%"=="" (set PROFILE=%USERPROFILE%\.config\browser-harness\chrome-automation) else (set PROFILE=%JEV_CHROME_PROFILE%)

netstat -ano | findstr /R /C:"TCP.*:%PORT%.*LISTENING" >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Automation Chrome is already listening on port %PORT%.
  exit /b 0
)

set CHROME=%JEV_CHROME%
if "%CHROME%"=="" set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
  echo ERROR: Chrome not found. Set JEV_CHROME to chrome.exe and retry.
  exit /b 1
)

start "" "%CHROME%" --remote-debugging-port=%PORT% --user-data-dir="%PROFILE%" ^
 --no-first-run --no-default-browser-check ^
 --disable-backgrounding-occluded-windows --disable-renderer-backgrounding ^
 --disable-background-timer-throttling --disable-features=CalculateNativeWinOcclusion ^
 about:blank

echo Started automation Chrome on port %PORT%.
echo Set BU_CDP_URL=http://127.0.0.1:%PORT% in the project .env, and close that window to stop it.
