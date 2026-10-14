# Install the exact locally accepted R1 delivery; never editable, never rebuild it.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ArtifactDirectory,
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [string]$UvPath = 'uv'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$base = Join-Path $repo '.work/integration-evleven'
$wheelHash = '40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e'
$manifestHash = '73b0614848d54546d4d1cf89e6f37649a9126e516eec0d2dc9b67ca1a07bdc80'
$phPython = (Resolve-Path -LiteralPath $PythonPath).Path
$artifactRoot = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$inputManifest = Join-Path $artifactRoot 'delivery-manifest.json'
$inputWheel = Join-Path $artifactRoot 'evleven-1.1.0-py3-none-any.whl'
if ((Get-FileHash -LiteralPath $inputManifest).Hash.ToLowerInvariant() -ne $manifestHash) { throw 'R1 manifest checksum mismatch.' }
if ((Get-FileHash -LiteralPath $inputWheel).Hash.ToLowerInvariant() -ne $wheelHash) { throw 'R1 wheel checksum mismatch.' }
New-Item -ItemType Directory -Force "$base/artifacts", "$base/cache", "$base/tmp" | Out-Null
$savedEnv = @{}
foreach ($key in @('UV_CACHE_DIR', 'TEMP', 'TMP')) { $savedEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
try {
    $env:UV_CACHE_DIR = "$base/cache"
    $env:TEMP = "$base/tmp"
    $env:TMP = "$base/tmp"
    $manifest = "$base/artifacts/delivery-manifest.json"
    if (!(Test-Path -LiteralPath $manifest)) {
        Copy-Item -LiteralPath $inputManifest -Destination $manifest
    }
    if ((Get-FileHash -LiteralPath $manifest).Hash.ToLowerInvariant() -ne $manifestHash) { throw 'R1 manifest checksum mismatch.' }
    $delivery = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
    $artifact = $delivery.artifacts | Where-Object { $_.sha256 -eq $wheelHash }
    if (!$artifact) { throw 'Pinned wheel is absent from the R1 manifest.' }
    $wheel = Join-Path "$base/artifacts" (Split-Path $artifact.path -Leaf)
    if (!(Test-Path -LiteralPath $wheel)) {
        # The accepted manifest is a checksum record, never authority to read
        # its original machine paths. Read only the explicit artifact directory.
        Copy-Item -LiteralPath $inputWheel -Destination $wheel
    }
    if ((Get-FileHash -LiteralPath $wheel).Hash.ToLowerInvariant() -ne $wheelHash) { throw 'R1 wheel checksum mismatch.' }
    $constraints = $delivery.installed_distributions.PSObject.Properties |
        Where-Object Name -ne 'evleven' | ForEach-Object { "$($_.Name)==$($_.Value)" }
    [IO.File]::WriteAllLines("$base/artifacts/constraints.txt", [string[]]$constraints)
    $r1Python = "$base/evleven-venv/Scripts/python.exe"
    if (!(Test-Path -LiteralPath $r1Python)) {
        & $UvPath venv --python $phPython --no-python-downloads "$base/evleven-venv"
        if ($LASTEXITCODE -ne 0) { throw 'R1 venv creation failed.' }
    }
    & $UvPath pip install --python $r1Python -c "$base/artifacts/constraints.txt" $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Pinned R1 installation failed.' }
    & $UvPath pip check --python $r1Python
    if ($LASTEXITCODE -ne 0) { throw 'R1 dependency check failed.' }
    & $r1Python -I -B "$PSScriptRoot/evleven_r1_server.py" --verify --data-dir "$base/setup-check"
    if ($LASTEXITCODE -ne 0) { throw 'R1 installed import verification failed.' }
    Write-Output 'R1 ready. Run the existing PyHarness integration launcher:'
    Write-Output "$phPython scripts/evleven_integration.py"
}
finally {
    foreach ($key in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($key, $savedEnv[$key], 'Process') }
}
