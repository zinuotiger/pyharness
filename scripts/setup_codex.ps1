$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $projectRoot 'tooling\codex'
& npm install --prefix $toolRoot --ignore-scripts --registry=https://registry.npmjs.org
if ($LASTEXITCODE -ne 0) { throw 'Project-local Codex installation failed' }
$binary = Join-Path $toolRoot 'node_modules\@openai\codex\bin\codex.js'
if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw 'Codex native executable is missing' }
$version = & node $binary --version
@{executable=$binary;version=$version} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $toolRoot 'runtime.json') -Encoding utf8
Write-Output "Project-local Codex configured: $version"
