[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$portWasExplicit = $PSBoundParameters.ContainsKey("Port")

function Test-LoopbackPortAvailable {
    param([int]$Candidate)

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Candidate)
    try {
        $listener.Start()
        return $true
    }
    catch [System.Net.Sockets.SocketException] {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Get-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

if (-not (Test-LoopbackPortAvailable -Candidate $Port)) {
    if ($portWasExplicit) {
        throw "127.0.0.1:$Port is already in use. Close the old process or choose another port with -Port."
    }
    $oldDefaultPort = $Port
    $Port = Get-FreeLoopbackPort
    Write-Host "127.0.0.1:$oldDefaultPort is already in use; this development session will use 127.0.0.1:$Port."
}

$hostRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $hostRoot "backend"
$desktop = Join-Path $hostRoot "desktop"

if (-not $Python) {
    $Python = Join-Path $backend ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python. Follow software/host/README.md first."
}
if (-not (Test-Path -LiteralPath (Join-Path $desktop "node_modules\electron\dist\electron.exe"))) {
    throw "Electron dependencies are missing. Run npm.cmd ci in software/host/desktop first."
}

$oldPython = $env:PVNA_PYTHON
$oldPort = $env:PVNA_API_PORT
try {
    # Electron main owns the random token and Python child lifecycle. No device is connected here.
    $env:PVNA_PYTHON = (Resolve-Path -LiteralPath $Python).Path
    $env:PVNA_API_PORT = [string]$Port
    Push-Location $desktop
    try {
        & npm.cmd run dev
        if ($LASTEXITCODE -ne 0) {
            throw "Desktop development process exited with code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if ($null -eq $oldPython) {
        Remove-Item Env:PVNA_PYTHON -ErrorAction SilentlyContinue
    }
    else {
        $env:PVNA_PYTHON = $oldPython
    }
    if ($null -eq $oldPort) {
        Remove-Item Env:PVNA_API_PORT -ErrorAction SilentlyContinue
    }
    else {
        $env:PVNA_API_PORT = $oldPort
    }
}
