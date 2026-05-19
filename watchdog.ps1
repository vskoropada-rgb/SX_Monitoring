# watchdog.ps1 — ensures main.py is always running
# Triggered by Task Scheduler every 5 minutes under SYSTEM

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python    = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $python) { exit 1 }

$mainRunning = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match [regex]::Escape("main.py") }

if (-not $mainRunning) {
    Start-Process $python -ArgumentList "`"$ScriptDir\main.py`"" -WindowStyle Hidden
}
