# watchdog.ps1 — ensures bot.py is always running
# Triggered by Task Scheduler every 5 minutes under SYSTEM

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$python    = (Get-Command python -ErrorAction SilentlyContinue)?.Source
if (-not $python) { exit 1 }

$botRunning = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match [regex]::Escape("bot.py") }

if (-not $botRunning) {
    Start-Process $python -ArgumentList "`"$ScriptDir\bot.py`"" -WindowStyle Hidden
}
