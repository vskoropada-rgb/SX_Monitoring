# install.ps1 — bootstrap-встановлення 1C Monitor (приватний репозиторій)
#
# 1. Створіть GitHub Fine-Grained PAT:
#    https://github.com/settings/tokens  →  "Fine-grained tokens"
#    Permissions: Contents = Read-only
#
# 2. Запустіть PowerShell від Адміністратора та виконайте:
#
#    $env:GH_TOKEN = "ghp_xxxxxxxxxxxxxxxxxx"
#    irm -H @{Authorization="token $env:GH_TOKEN"} `
#        "https://raw.githubusercontent.com/vskoropada-rgb/linux-scripts/main/Monitoring/install.ps1" | iex

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

function Write-Banner {
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║  1C Monitor — Bootstrap встановлення        ║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Ok   { param([string]$m) Write-Host "  [OK] $m" -ForegroundColor Green  }
function Write-Err  { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Red    }
function Write-Info { param([string]$m) Write-Host "  [..] $m" -ForegroundColor Yellow }

# ─── DPAPI (inline, без залежності від manage.ps1) ───────────

function Local-Protect {
    param([string]$p)
    Add-Type -AssemblyName System.Security
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($p)
    $enc   = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes, $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    return "ENCRYPTED:" + [Convert]::ToBase64String($enc)
}

function Local-SetEnv {
    param([string]$EnvFile, [string]$Key, [string]$Value)
    $lines = if (Test-Path $EnvFile) { Get-Content $EnvFile -Encoding UTF8 } else { @() }
    $found = $false
    $newLines = $lines | ForEach-Object {
        if ($_ -match "^$([regex]::Escape($Key))\s*=") { $found = $true; "$Key=$Value" } else { $_ }
    }
    if (-not $found) { $newLines += "$Key=$Value" }
    Set-Content $EnvFile -Value $newLines -Encoding UTF8
}

# ─── Admin check ─────────────────────────────────────────────

Write-Banner

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Err "Запустіть PowerShell від імені Адміністратора!"
    Write-Host ""
    Write-Host "  Клікніть правою кнопкою → 'Запуск від імені адміністратора'" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "  Натисніть Enter"
    exit 1
}

# ─── GitHub токен ────────────────────────────────────────────

$ghToken = $env:GH_TOKEN
if (-not $ghToken) {
    Write-Host "  GitHub PAT не знайдено в " -NoNewline -ForegroundColor Yellow
    Write-Host "`$env:GH_TOKEN" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Отримати токен: https://github.com/settings/tokens" -ForegroundColor DarkGray
    Write-Host "  Permissions: Contents = Read-only" -ForegroundColor DarkGray
    Write-Host ""
    $ghToken = Read-Host "  Введіть GitHub PAT"
    if (-not $ghToken) {
        Write-Err "Токен не вказано — встановлення скасовано."
        Read-Host "  Натисніть Enter"
        exit 1
    }
}

$authHeader = @{ Authorization = "token $ghToken" }

# Перевірка токену
Write-Info "Перевірка токену..."
try {
    Invoke-WebRequest -Uri "$REPO_RAW/install.ps1" -Headers $authHeader `
        -Method Head -UseBasicParsing -ErrorAction Stop | Out-Null
    Write-Ok "Токен дійсний, репозиторій доступний"
} catch {
    Write-Err "Помилка доступу до репозиторію: $_"
    Write-Host ""
    Write-Host "  Перевірте:" -ForegroundColor Yellow
    Write-Host "   1. Токен правильний і не прострочений"    -ForegroundColor Yellow
    Write-Host "   2. Permissions: Contents = Read-only"     -ForegroundColor Yellow
    Write-Host "   3. Токен видано для потрібного репозиторію" -ForegroundColor Yellow
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

Write-Host ""
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
        Invoke-WebRequest -Uri $url -OutFile $dest -Headers $authHeader `
            -UseBasicParsing -ErrorAction Stop
        Write-Host "    OK  $file" -ForegroundColor Green
        $countOk++
    } catch {
        Write-Host "    --  $file" -ForegroundColor DarkGray
        $countFail++
    }
}

$ProgressPreference = "Continue"

Write-Host ""
Write-Ok "Завантажено: $countOk   Пропущено: $countFail"

# ─── Зберегти токен зашифровано у .env ───────────────────────

Write-Host ""
$ans = Read-Host "  Зберегти токен (зашифровано DPAPI) для майбутніх оновлень? (Y/n)"
if ($ans -ne "n" -and $ans -ne "N") {
    $envFile = Join-Path $INSTALL_DIR ".env"
    if (-not (Test-Path $envFile)) {
        $envExample = Join-Path $INSTALL_DIR ".env.example"
        if (Test-Path $envExample) { Copy-Item $envExample $envFile }
    }
    Local-SetEnv $envFile "GH_TOKEN" (Local-Protect $ghToken)
    Write-Ok "Токен збережено в .env (зашифровано)"
}

# ─── Запуск manage.ps1 ───────────────────────────────────────

$managePath = Join-Path $INSTALL_DIR "manage.ps1"

if (-not (Test-Path $managePath)) {
    Write-Err "manage.ps1 не знайдено — перевірте завантаження."
    Read-Host "  Натисніть Enter"
    exit 1
}

Write-Host ""
Write-Ok "Встановлення завершено! Запускаю менеджер..."
Write-Host ""
Start-Sleep 1

Start-Process powershell.exe `
    -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$managePath`"" `
    -Verb RunAs
