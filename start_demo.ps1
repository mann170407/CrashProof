# start_demo.ps1 - start the AtomicDocs Crash Lab on BOTH kernels and open the UI.
#
#   Windows backend : python ui\server.py --port 8765   (serves the UI + /api)    own window
#   Linux backend   : wsl -d Ubuntu -- bash <...>/ui/start_linux_backend.sh       own window
#                     (syncs the code to ~/crashproof on ext4, serves /api on 8766)
#
# Waits until both /api/info answer (up to 30 s), says which are up, then opens
# http://127.0.0.1:8765 in the default browser. A backend that is already
# running is left alone. Double-click start_demo.bat to run this.
# -NoBrowser: start + wait only (for testing).
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$WinUrl = 'http://127.0.0.1:8765/api/info'
$LinuxUrl = 'http://127.0.0.1:8766/api/info'

function Test-Api([string]$url) {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# --- Windows backend -------------------------------------------------------
if (Test-Api $WinUrl) {
    Write-Host 'Windows backend already running on :8765'
} else {
    Write-Host 'Starting Windows backend on :8765 ...'
    $server = Join-Path $Root 'ui\server.py'
    Start-Process -FilePath 'cmd.exe' -WorkingDirectory $Root -ArgumentList @(
        '/k', "title Crash Lab - Windows backend :8765 && python -u `"$server`" --port 8765")
}

# --- Linux backend (WSL2) ----------------------------------------------------
if (Test-Api $LinuxUrl) {
    Write-Host 'Linux backend already running on :8766'
} else {
    # C:\Users\... -> /mnt/c/Users/...
    $drive = $Root.Substring(0, 1).ToLower()
    $wslRoot = '/mnt/' + $drive + ($Root.Substring(2) -replace '\\', '/')
    $script = "$wslRoot/ui/start_linux_backend.sh"
    Write-Host "Starting Linux backend on :8766 (WSL2: bash $script) ..."
    Start-Process -FilePath 'cmd.exe' -ArgumentList @(
        '/k', "title Crash Lab - Linux backend :8766 && wsl.exe -d Ubuntu -- bash `"$script`"")
}

# --- wait for both -----------------------------------------------------------
$deadline = (Get-Date).AddSeconds(30)
$winUp = $false
$linuxUp = $false
while ((Get-Date) -lt $deadline -and -not ($winUp -and $linuxUp)) {
    if (-not $winUp) { $winUp = Test-Api $WinUrl }
    if (-not $linuxUp) { $linuxUp = Test-Api $LinuxUrl }
    if (-not ($winUp -and $linuxUp)) { Start-Sleep -Milliseconds 500 }
}
if ($winUp) { $w = 'UP' } else { $w = 'NOT RESPONDING' }
if ($linuxUp) { $l = 'UP' } else { $l = 'NOT RESPONDING' }
Write-Host ''
Write-Host "  Windows backend  http://127.0.0.1:8765  $w"
Write-Host "  Linux backend    http://127.0.0.1:8766  $l"
if (-not ($winUp -and $linuxUp)) {
    Write-Host '  (the UI falls back to recorded fixtures for a backend that is down)'
}

if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765' }
if (-not ($winUp -and $linuxUp)) { exit 1 }
