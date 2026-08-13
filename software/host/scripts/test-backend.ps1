[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$hostRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $hostRoot "backend"

Push-Location $backend
try {
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Backend tests failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
