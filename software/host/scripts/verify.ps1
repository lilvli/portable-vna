[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$hostRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $hostRoot "..\..")).Path
$backend = Join-Path $hostRoot "backend"
$desktop = Join-Path $hostRoot "desktop"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend environment not found: $python"
}

Push-Location $backend
try {
    if (Get-Command uv.exe -ErrorAction SilentlyContinue) {
        $previousUvCache = $env:UV_CACHE_DIR
        $env:UV_CACHE_DIR = Join-Path $backend ".uv-cache"
        & uv.exe lock --check
        $env:UV_CACHE_DIR = $previousUvCache
        if ($LASTEXITCODE -ne 0) { throw "uv.lock is stale" }
    }
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
    & $python -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }
    & $python -m ruff format --check src tests
    if ($LASTEXITCODE -ne 0) { throw "ruff format check failed" }
    & $python -m compileall -q src
    if ($LASTEXITCODE -ne 0) { throw "compileall failed" }
}
finally {
    Pop-Location
}

Push-Location $desktop
try {
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "desktop tests failed" }
    & npm.cmd run lint
    if ($LASTEXITCODE -ne 0) { throw "desktop lint failed" }
    & npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw "desktop typecheck failed" }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "desktop production build failed" }
}
finally {
    Pop-Location
}

Push-Location $repoRoot
try {
    & git -c safe.directory=$repoRoot diff --check -- software/host
    if ($LASTEXITCODE -ne 0) { throw "git diff --check failed for software/host" }
}
finally {
    Pop-Location
}

Write-Host "Offline verification passed. Real serial, FPGA, JESD, and RF access performed: 0."
