[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AccessToken,
    [int]$Port = 8765,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$hostRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $hostRoot "backend"
$env:PVNA_ACCESS_TOKEN = $AccessToken

Push-Location $backend
try {
    & $Python -m pvna_host.main --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "Backend exited with code $LASTEXITCODE"
    }
}
finally {
    Remove-Item Env:PVNA_ACCESS_TOKEN -ErrorAction SilentlyContinue
    Pop-Location
}
