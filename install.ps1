# install.ps1 - 1C Monitor bootstrap installer
#
# PowerShell 3.0+:
#   irm "https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main/install.ps1" | iex
#
# PowerShell 2.0 / Windows 2008 R2:
#   [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
#   (New-Object Net.WebClient).DownloadString("https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main/install.ps1") | iex

$ErrorActionPreference = "SilentlyContinue"

# TLS 1.2 — required for GitHub
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Old OS (2008 R2) may have outdated root certs — bypass SSL validation
[Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }

$REPO_RAW    = "https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main"
$DEFAULT_DIR = "D:\1c_monitor"

$FILES = @(
    "main.py", "monitor.py", "bot.py", "config.py",
    "storage.py", "analyzer.py", "notifier.py", "charts.py",
    "actions.py", "manage.ps1", "watchdog.ps1", "install.ps1",
    "requirements.txt", ".env.example",
    "collectors/__init__.py",
    "collectors/disk.py",     "collectors/memory.py",
    "collectors/services.py", "collectors/backup.py",
    "collectors/winupdate.py","collectors/security.py",
    "collectors/rdp.py",      "collectors/usb.py",
    "collectors/software.py", "collectors/schtasks.py"
)

Write-Host ""
Write-Host "  =================================" -ForegroundColor Cyan
Write-Host "  1C Monitor - Bootstrap installer" -ForegroundColor Cyan
Write-Host "  =================================" -ForegroundColor Cyan
Write-Host ""

# --- Admin check ---

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Host "  [!!] Run PowerShell as Administrator!" -ForegroundColor Red
    Read-Host "  Press Enter"
    exit 1
}

# --- PowerShell version check ---

$psVer = $PSVersionTable.PSVersion.Major
Write-Host "  PowerShell: $psVer   OS: $([Environment]::OSVersion.Version)" -ForegroundColor DarkGray

if ($psVer -lt 3) {
    Write-Host ""
    Write-Host "  [!!] PowerShell $psVer is too old. Need 5.1." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Download Windows Management Framework 5.1:" -ForegroundColor Yellow
    Write-Host "  https://www.microsoft.com/download/details.aspx?id=54616" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Install WMF 5.1, reboot, then run this script again." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Press Enter"
    exit 1
}

# --- Install directory ---

$INSTALL_DIR = $DEFAULT_DIR

if (-not (Test-Path "D:\")) {
    $ans = Read-Host "  D:\ not found. Use C:\1c_monitor? (Y/n)"
    $INSTALL_DIR = if ($ans -eq "n" -or $ans -eq "N") {
        Read-Host "  Enter install path"
    } else { "C:\1c_monitor" }
    if (-not $INSTALL_DIR) { exit 1 }
}

Write-Host "  Install dir: $INSTALL_DIR" -ForegroundColor Cyan
Write-Host ""

# --- Create directories ---

try {
    New-Item -ItemType Directory -Path $INSTALL_DIR              -Force | Out-Null
    New-Item -ItemType Directory -Path "$INSTALL_DIR\collectors" -Force | Out-Null
    Write-Host "  [OK] Directory created" -ForegroundColor Green
} catch {
    Write-Host "  [!!] Cannot create directory: $_" -ForegroundColor Red
    Read-Host "  Press Enter"
    exit 1
}

# --- Download files ---

Write-Host ""
Write-Host "  Downloading files from GitHub..." -ForegroundColor Yellow
Write-Host ""

$wc = New-Object System.Net.WebClient
$wc.Encoding = [System.Text.Encoding]::UTF8

$ok = 0; $fail = 0; $lastErr = ""

foreach ($file in $FILES) {
    $url  = "$REPO_RAW/$($file -replace '\\','/')"
    $dest = Join-Path $INSTALL_DIR $file

    try {
        $wc.DownloadFile($url, $dest)

        # Add UTF-8 BOM to .ps1 files for PowerShell 5.x
        if ($file.EndsWith('.ps1')) {
            $bytes = [System.IO.File]::ReadAllBytes($dest)
            $bom   = [byte[]](0xEF, 0xBB, 0xBF)
            if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF) {
                [System.IO.File]::WriteAllBytes($dest, $bom + $bytes)
            }
        }

        Write-Host "    OK  $file" -ForegroundColor Green
        $ok++
    } catch {
        $lastErr = $_.Exception.Message
        Write-Host "    --  $file  ($lastErr)" -ForegroundColor Red
        $fail++
    }
}

Write-Host ""

if ($ok -eq 0) {
    Write-Host "  [!!] All downloads failed!" -ForegroundColor Red
    Write-Host "  Last error: $lastErr" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Possible causes:" -ForegroundColor Yellow
    Write-Host "    1. No internet access from this server" -ForegroundColor White
    Write-Host "    2. Firewall blocks github.com / raw.githubusercontent.com" -ForegroundColor White
    Write-Host "    3. Proxy required (set in IE settings)" -ForegroundColor White
    Write-Host ""
    Write-Host "  Test manually:" -ForegroundColor Yellow
    Write-Host "    ping raw.githubusercontent.com" -ForegroundColor White
    Write-Host ""
    Read-Host "  Press Enter"
    exit 1
}

if ($fail -gt 0) {
    Write-Host "  [OK] Downloaded: $ok   Failed: $fail" -ForegroundColor Yellow
} else {
    Write-Host "  [OK] All $ok files downloaded" -ForegroundColor Green
}

# --- Launch manage.ps1 ---

$managePath = Join-Path $INSTALL_DIR "manage.ps1"

if (-not (Test-Path $managePath)) {
    Write-Host "  [!!] manage.ps1 not found - check internet connection" -ForegroundColor Red
    Read-Host "  Press Enter"
    exit 1
}

Write-Host ""
Write-Host "  [OK] Launching manage.ps1..." -ForegroundColor Green
Write-Host ""
Start-Sleep 1

Start-Process powershell.exe `
    -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$managePath`"" `
    -Verb RunAs
