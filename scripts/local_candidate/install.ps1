param([string]$PythonPath = 'python', [string]$UvPath = 'uv')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$root = $PSScriptRoot
$state = Join-Path $root 'runtime'
$saved = @{}
$pushed = $false
try {
    $PythonPath = (Get-Command $PythonPath -ErrorAction Stop).Source
    $UvPath = (Get-Command $UvPath -ErrorAction Stop).Source
    Push-Location -LiteralPath $root
    $pushed = $true
    $manifest = Get-Content -LiteralPath (Join-Path $root 'SHA256SUMS.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in $manifest.PSObject.Properties) {
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $actual = ([BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes((Join-Path $root $entry.Name))))).Replace('-', '').ToLower() }
        finally { $hasher.Dispose() }
        if ($actual -ne $entry.Value) { throw "Checksum mismatch: $($entry.Name)" }
    }
    foreach ($key in @('UV_CACHE_DIR','TEMP','TMP','PYTHONPATH','PYTHONUTF8','UV_PYTHON_INSTALL_DIR')) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    }
    foreach ($pair in @{UV_CACHE_DIR='cache'; TEMP='tmp'; TMP='tmp'; UV_PYTHON_INSTALL_DIR='python-unused'}.GetEnumerator()) {
        $path = Join-Path $state $pair.Value
        New-Item -ItemType Directory -Force -Path $path | Out-Null
        [Environment]::SetEnvironmentVariable($pair.Key, $path, 'Process')
    }
    $env:PYTHONPATH = ''; $env:PYTHONUTF8 = '1'
    & $PythonPath -I -c 'import sys; assert sys.version_info[:2] == (3,13), "This candidate requires tested CPython 3.13"'
    if ($LASTEXITCODE -ne 0) { throw 'CPython 3.13 required; no Python/model download is performed.' }
    foreach ($role in @('harness','evleven')) {
        $venv = "./runtime/$role-venv"
        $exe = Join-Path $venv 'Scripts/python.exe'
        if (!(Test-Path -LiteralPath $exe)) {
            & $UvPath venv --no-python-downloads --python $PythonPath $venv
            if ($LASTEXITCODE -ne 0) { throw "Environment creation failed: $role" }
        }
        $wheel = if ($role -eq 'harness') {'pyharness-0.1.0-py3-none-any.whl'} else {'evleven-1.1.0-py3-none-any.whl'}
        & $UvPath pip install --python $exe --no-python-downloads --only-binary :all: -c "./$role-constraints.txt" -r "./$role-requirements.txt" "./wheels/$wheel"
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed: $role" }
        & $UvPath pip check --python $exe
        if ($LASTEXITCODE -ne 0) { throw "Dependency check failed: $role" }
    }
    & (Join-Path $state 'harness-venv/Scripts/python.exe') -I -B -X utf8 (Join-Path $root 'integrity.py')
    if ($LASTEXITCODE -ne 0) { throw 'Installed artifact verification failed' }
    & (Join-Path $state 'evleven-venv/Scripts/python.exe') -I -B -X utf8 (Join-Path $root 'evleven_r1_server.py') --verify --data-dir (Join-Path $state 'preflight-r1')
    if ($LASTEXITCODE -ne 0) { throw 'R1 artifact verification failed' }
    Write-Output "Installed. Next: & '$root\start.ps1'. Data/logs: $state"
} catch { Write-Error -ErrorAction Continue $_; exit 1 }
finally {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') }
    if ($pushed) { Pop-Location }
}
