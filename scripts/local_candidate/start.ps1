$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot 'runtime/harness-venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python)) { Write-Error 'Run install.ps1 first.'; exit 1 }
& $python -I -B -X utf8 (Join-Path $PSScriptRoot 'trial.py')
exit $LASTEXITCODE
