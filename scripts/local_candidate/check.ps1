$ErrorActionPreference = 'Stop'
$python = Join-Path $PSScriptRoot 'runtime/harness-venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $python)) { Write-Error 'Run install.ps1 first.'; exit 1 }
& $python -I -B -X utf8 (Join-Path $PSScriptRoot 'evleven_integration.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -B -X utf8 (Join-Path $PSScriptRoot 'acceptance.py')
exit $LASTEXITCODE
