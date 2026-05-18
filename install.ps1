# install.ps1 — bootstrap-встановлення 1C Monitor
#
# PowerShell 2.0 (Windows 2008 R2):
#   [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
#   (New-Object Net.WebClient).DownloadString("https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main/install.ps1") | iex
#
# PowerShell 3.0+:
#   irm "https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main/install.ps1" | iex

# Примусово TLS 1.2 — потрібно для GitHub на старих ОС
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "SilentlyContinue"

# Встановлюємо UTF-8 тільки якщо консоль підтримує
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$DEFAULT_DIR = "D:\setup\monitoring-sc"
$REPO_RAW    = "https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main"

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

# ─── Перевірка версії PowerShell ─────────────────────────────

$psVer = $PSVersionTable.PSVersion.Major
if ($psVer -lt 3) {
    Write-Host ""
    Write-Host "  PowerShell $psVer виявлено (Windows 2008 R2?)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Для роботи manage.ps1 потрібен PowerShell 5.1." -ForegroundColor Yellow
    Write-Host "  Завантажте Windows Management Framework 5.1:" -ForegroundColor White
    Write-Host "  https://www.microsoft.com/download/details.aspx?id=54616" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Після встановлення WMF 5.1 та перезавантаження" -ForegroundColor White
    Write-Host "  запустіть install.ps1 знову." -ForegroundColor White
    Write-Host ""
    Read-Host "  Натисніть Enter"
    exit 1
}

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

$wc = New-Object System.Net.WebClient
$wc.Encoding = [System.Text.Encoding]::UTF8

$countOk = 0; $countFail = 0

foreach ($file in $FILES) {
    $url  = "$REPO_RAW/$($file -replace '\\','/')"
    $dest = Join-Path $INSTALL_DIR $file

    try {
        $wc.DownloadFile($url, $dest)
        # Додаємо UTF-8 BOM до .ps1 щоб PowerShell 5.x читав як UTF-8
        if ($file.EndsWith('.ps1')) {
            $bytes = [System.IO.File]::ReadAllBytes($dest)
            $bom   = [byte[]](0xEF, 0xBB, 0xBF)
            if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
                [System.IO.File]::WriteAllBytes($dest, $bom + $bytes)
            }
        }
        Write-Host "    OK  $file" -ForegroundColor Green
        $countOk++
    } catch {
        Write-Host "    --  $file" -ForegroundColor DarkGray
        $countFail++
    }
}

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
