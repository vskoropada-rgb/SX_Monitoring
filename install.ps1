# install.ps1 — bootstrap-встановлення 1C Monitor
#
# Запуск (PowerShell від Адміністратора):
#   irm "https://raw.githubusercontent.com/vskoropada-rgb/linux-scripts/main/Monitoring/install.ps1" | iex

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "SilentlyContinue"

$DEFAULT_DIR = "D:\setup\monitoring-sc"
$REPO_RAW    = "https://raw.githubusercontent.com/vskoropada-rgb/linux-scripts/main/Monitoring"

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

# ─── UI ──────────────────────────────────────────────────────

function Write-Ok   { param([string]$m) Write-Host "  [OK] $m" -ForegroundColor Green  }
function Write-Err  { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Red    }
function Write-Info { param([string]$m) Write-Host "  [..] $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║  1C Monitor — Bootstrap встановлення        ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ─── Admin check ─────────────────────────────────────────────

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Err "Запустіть PowerShell від імені Адміністратора!"
    Write-Host ""
    Write-Host "  Клікніть правою кнопкою → 'Запуск від імені адміністратора'" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Натисніть Enter"
    exit 1
}

# ─── Папка встановлення ──────────────────────────────────────

$INSTALL_DIR = $DEFAULT_DIR

if (-not (Test-Path "D:\")) {
    Write-Err "Диск D: не знайдено."
    Write-Host ""
    Write-Host "  [1] C:\setup\monitoring-sc  (рекомендовано)" -ForegroundColor White
    Write-Host "  [2] Ввести свій шлях"                        -ForegroundColor White
    Write-Host ""
    $ans = Read-Host "  Вибір (1/2)"
    $INSTALL_DIR = if ($ans -eq "2") { Read-Host "  Шлях встановлення" } else { "C:\setup\monitoring-sc" }
    if (-not $INSTALL_DIR) { Write-Err "Шлях не вказано."; exit 1 }
}

Write-Host "  Папка встановлення: " -NoNewline -ForegroundColor White
Write-Host $INSTALL_DIR -ForegroundColor Cyan
Write-Host ""

# ─── Створення папок ─────────────────────────────────────────

try {
    New-Item -ItemType Directory -Path $INSTALL_DIR              -Force | Out-Null
    New-Item -ItemType Directory -Path "$INSTALL_DIR\collectors" -Force | Out-Null
    Write-Ok "Папку створено"
} catch {
    Write-Err "Не вдалося створити папку: $_"
    Read-Host "  Натисніть Enter"
    exit 1
}

# ─── Завантаження файлів ─────────────────────────────────────

Write-Host ""
Write-Info "Завантаження файлів з GitHub..."
Write-Host ""

$ProgressPreference = "SilentlyContinue"
$countOk = 0; $countFail = 0

foreach ($file in $FILES) {
    $url  = "$REPO_RAW/$($file -replace '\\','/')"
    $dest = Join-Path $INSTALL_DIR $file

    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -ErrorAction Stop
        Write-Host "    OK  $file" -ForegroundColor Green
        $countOk++
    } catch {
        Write-Host "    --  $file" -ForegroundColor DarkGray
        $countFail++
    }
}

$ProgressPreference = "Continue"

Write-Host ""
if ($countFail -eq 0) {
    Write-Ok "Усі файли завантажено ($countOk)"
} else {
    Write-Ok "Завантажено: $countOk   Пропущено: $countFail"
}

# ─── Запуск manage.ps1 ───────────────────────────────────────

$managePath = Join-Path $INSTALL_DIR "manage.ps1"

if (-not (Test-Path $managePath)) {
    Write-Err "manage.ps1 не знайдено — перевірте підключення до Internet."
    Read-Host "  Натисніть Enter"
    exit 1
}

Write-Host ""
Write-Ok "Файли встановлено. Запускаю менеджер..."
Write-Host ""
Start-Sleep 1

Start-Process powershell.exe `
    -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$managePath`"" `
    -Verb RunAs
