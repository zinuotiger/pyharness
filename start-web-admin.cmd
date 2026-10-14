@echo off
setlocal
cd /d "%~dp0"
set "PYHARNESS_LAUNCH_PYTHON=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not exist "%PYHARNESS_LAUNCH_PYTHON%" (
    echo Python 3.13 was not found at "%PYHARNESS_LAUNCH_PYTHON%".
    pause
    exit /b 1
)
echo Starting PyHarness as the local application administrator.
echo Keep this window open. Press Ctrl+C to stop the server.
"%PYHARNESS_LAUNCH_PYTHON%" -B -m pyharness.web --data-dir "D:\zinuotiger-workspace\temporary-runtimes\pyharness-ui-20261003-manual\data" --port 60460 --open-browser
if errorlevel 1 (
    pause
    exit /b 1
)
