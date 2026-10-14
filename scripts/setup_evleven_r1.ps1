# Install the exact locally accepted R1 delivery; never editable, never rebuild it.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$base = Join-Path $repo '.work/integration-evleven'
$source = 'D:\zinuotiger-workspace\repositories\evleven'
$wheelHash = '40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e'
$manifestHash = '73b0614848d54546d4d1cf89e6f37649a9126e516eec0d2dc9b67ca1a07bdc80'
$phPython = Join-Path $repo '.work/verification/venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $phPython)) { throw 'The verified PyHarness environment is missing.' }
New-Item -ItemType Directory -Force "$base/artifacts", "$base/cache", "$base/tmp" | Out-Null
$savedEnv = @{}
foreach ($key in @('UV_CACHE_DIR', 'TEMP', 'TMP')) { $savedEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
try {
    $env:UV_CACHE_DIR = "$base/cache"
    $env:TEMP = "$base/tmp"
    $env:TMP = "$base/tmp"
    $manifest = "$base/artifacts/delivery-manifest.json"
    if (!(Test-Path -LiteralPath $manifest)) {
        Copy-Item -LiteralPath "$source/reports/r1-evidence/delivery-manifest.json" -Destination $manifest
    }
    if ((Get-FileHash -LiteralPath $manifest).Hash.ToLowerInvariant() -ne $manifestHash) { throw 'R1 manifest checksum mismatch.' }
    $delivery = Get-Content -Raw -LiteralPath $manifest | ConvertFrom-Json
    $artifact = $delivery.artifacts | Where-Object { $_.sha256 -eq $wheelHash }
    if (!$artifact) { throw 'Pinned wheel is absent from the R1 manifest.' }
    $wheel = Join-Path "$base/artifacts" (Split-Path $artifact.path -Leaf)
    if (!(Test-Path -LiteralPath $wheel)) {
        $report = Get-Content -Raw -LiteralPath "$source/reports/R1-ACCEPTANCE-REPORT.md"
        if (!$report.Contains($wheelHash)) { throw 'R1 acceptance report does not confirm this delivery.' }
        $resolved = (Resolve-Path -LiteralPath $artifact.path).Path
        if (!$resolved.StartsWith("$source\dist\r1\", [StringComparison]::OrdinalIgnoreCase)) { throw 'Delivery path outside approved R1 artifacts.' }
        Copy-Item -LiteralPath $resolved -Destination $wheel
    }
    if ((Get-FileHash -LiteralPath $wheel).Hash.ToLowerInvariant() -ne $wheelHash) { throw 'R1 wheel checksum mismatch.' }
    $constraints = $delivery.installed_distributions.PSObject.Properties |
        Where-Object Name -ne 'evleven' | ForEach-Object { "$($_.Name)==$($_.Value)" }
    [IO.File]::WriteAllLines("$base/artifacts/constraints.txt", [string[]]$constraints)
    $r1Python = "$base/evleven-venv/Scripts/python.exe"
    if (!(Test-Path -LiteralPath $r1Python)) {
        & uv venv --python $phPython "$base/evleven-venv"
        if ($LASTEXITCODE -ne 0) { throw 'R1 venv creation failed.' }
    }
    & uv pip install --python $r1Python -c "$base/artifacts/constraints.txt" $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Pinned R1 installation failed.' }
    & uv pip check --python $r1Python
    if ($LASTEXITCODE -ne 0) { throw 'R1 dependency check failed.' }
    & $r1Python -I -B "$PSScriptRoot/evleven_r1_server.py" --verify --data-dir "$base/setup-check"
    if ($LASTEXITCODE -ne 0) { throw 'R1 installed import verification failed.' }
    Write-Output 'R1 ready. Run the existing PyHarness integration launcher:'
    Write-Output "$phPython scripts/evleven_integration.py"
}
finally {
    foreach ($key in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($key, $savedEnv[$key], 'Process') }
}
