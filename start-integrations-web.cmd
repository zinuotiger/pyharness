@echo off
setlocal
cd /d "%~dp0"
uv run --extra web pyharness-web --data-dir "%LOCALAPPDATA%\PyHarness\integrations-20261013" --open-browser
