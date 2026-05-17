# manage.ps1 - 1C Monitor Management Script
# Run as Administrator: Right-click -> "Run with PowerShell" (as Admin)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "SilentlyContinue"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile     = Join-Path $ScriptDir ".env"
$EnvExample  = Join-Path $ScriptDir ".env.example"
$ReqFile     = Join-Path $ScriptDir "requirements.txt"

# ─── Admin check ─────────────────────────────────────────────

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

if (-not $isAdmin) {
    Write-Host ""
    Write-Host "  ПОМИЛКА: Запустіть PowerShell від імені Адміністратора!" -ForegroundColor Red
    Write-Host ""
    Read-Host "  Натисніть Enter"
    exit 1
}

# ─── DPAPI encryption ────────────────────────────────────────

function Protect-Value {
    param([string]$Plaintext)
    Add-Type -AssemblyName System.Security
    $bytes     = [System.Text.Encoding]::UTF8.GetBytes($Plaintext)
    $encrypted = [System.Security.Cryptography.ProtectedData]::Protect(
        $bytes, $null,
        [System.Security.Cryptography.DataProtectionScope]::LocalMachine
    )
    return "ENCRYPTED:" + [Convert]::ToBase64String($encrypted)
}

# ─── .env helpers ────────────────────────────────────────────

function Read-Env {
    $result = @{}
    if (-not (Test-Path $EnvFile)) { return $result }
    foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
        if ($line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
            $result[$matches[1]] = $matches[2]
        }
    }
    return $result
}

function Set-EnvValue {
    param([string]$Key, [string]$Value)
    if (-not (Test-Path $EnvFile)) {
        Copy-Item $EnvExample $EnvFile
    }
    $lines = Get-Content $EnvFile -Encoding UTF8
    $found = $false
    $newLines = $lines | ForEach-Object {
        if ($_ -match "^$([regex]::Escape($Key))\s*=") {
            $found = $true
            "$Key=$Value"
        } else { $_ }
    }
    if (-not $found) { $newLines += "$Key=$Value" }
    Set-Content $EnvFile -Value $newLines -Encoding UTF8
}

function Get-DisplayValue {
    param([string]$Key, [hashtable]$Env)
    $val = $Env[$Key]
    if (-not $val)                  { return "[не задано]" }
    if ($val.StartsWith("ENCRYPTED:")) { return "[зашифровано]" }
    return $val
}

# ─── UI helpers ──────────────────────────────────────────────

function Show-Header {
    param([string]$Title)
    Clear-Host
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║  $($Title.PadRight(44))║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Ok   { param([string]$msg) Write-Host "  [OK] $msg" -ForegroundColor Green  }
function Write-Err  { param([string]$msg) Write-Host "  [!!] $msg" -ForegroundColor Red    }
function Write-Info { param([string]$msg) Write-Host "  [..] $msg" -ForegroundColor Yellow }
function Write-Step { param([string]$msg) Write-Host "  --> $msg"  -ForegroundColor White  }

function Read-Secret {
    param([string]$Prompt)
    Write-Host "  ${Prompt}: " -NoNewline -ForegroundColor White
    $secure = Read-Host -AsSecureString
    $bstr   = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    return $plain
}

function Pause-Return { Read-Host "`n  Натисніть Enter для повернення в меню" | Out-Null }

# ─── 1. Install / Update ─────────────────────────────────────

function Install-Monitor {
    Show-Header "1. Встановлення / Оновлення"

    # Python check
    Write-Step "Перевірка Python..."
    $pyVer = python --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Python не знайдений. Встановіть Python 3.10+ з python.org"
        Pause-Return; return
    }
    Write-Ok $pyVer

    # Dependencies
    Write-Step "Встановлення залежностей pip..."
    python -m pip install -r $ReqFile --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Помилка встановлення залежностей"
        Pause-Return; return
    }
    Write-Ok "Залежності встановлені"

    # First-time .env setup
    if (-not (Test-Path $EnvFile)) {
        Write-Info "Файл .env не знайдений. Запускаю першочергове налаштування..."
        Write-Host ""
        Setup-FirstRun
    } else {
        Write-Ok "Файл .env знайдений"
    }

    # Task Scheduler
    Create-Tasks

    # Start bot immediately
    Write-Step "Запуск Telegram бота..."
    $python = (Get-Command python -ErrorAction SilentlyContinue)?.Source
    if ($python) {
        Start-Process $python -ArgumentList "`"$ScriptDir\bot.py`"" -WindowStyle Hidden
        Write-Ok "Бот запущений"
    }

    Write-Host ""
    Write-Ok "Встановлення завершено!"
    Write-Host "  Task Scheduler завдання:" -ForegroundColor Cyan
    Write-Host "    1C_Monitor      — кожну хвилину"
    Write-Host "    1C_Monitor_Bot  — при старті системи"
    Pause-Return
}

function Setup-FirstRun {
    Write-Host "  ── Ідентифікація сервера ──" -ForegroundColor Cyan
    $sid  = Read-Host "  SERVER_ID (напр. company_a)"
    $name = Read-Host "  COMPANY_NAME (напр. Компанія А)"
    Set-EnvValue "SERVER_ID"    $sid
    Set-EnvValue "COMPANY_NAME" $name

    Write-Host ""
    Setup-Telegram-Full

    Write-Host ""
    Setup-OpenAI
}

function Create-Tasks {
    Write-Step "Створення Task Scheduler завдань..."

    foreach ($t in @("1C_Monitor", "1C_Monitor_Bot")) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
    }

    $python   = (Get-Command python).Source
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

    # Monitor — every minute
    $a1 = New-ScheduledTaskAction -Execute $python -Argument "`"$ScriptDir\monitor.py`""
    $t1 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 1) `
                                   -Once -At (Get-Date)
    try {
        Register-ScheduledTask -TaskName "1C_Monitor" -Action $a1 -Trigger $t1 `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Ok "1C_Monitor створено (кожну хвилину)"
    } catch { Write-Err "Помилка 1C_Monitor: $_" }

    # Bot — at startup
    $a2 = New-ScheduledTaskAction -Execute $python -Argument "`"$ScriptDir\bot.py`""
    $t2 = New-ScheduledTaskTrigger -AtStartup
    try {
        Register-ScheduledTask -TaskName "1C_Monitor_Bot" -Action $a2 -Trigger $t2 `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Ok "1C_Monitor_Bot створено (при старті)"
    } catch { Write-Err "Помилка 1C_Monitor_Bot: $_" }
}

# ─── 2. Change TG topic ──────────────────────────────────────

function Setup-Topic {
    Show-Header "2. Змінити Telegram топік"
    $env = Read-Env
    Write-Host "  Поточний топік : $(Get-DisplayValue 'TG_TOPIC_ID' $env)" -ForegroundColor White
    Write-Host ""
    $topicId = Read-Host "  Новий TG_TOPIC_ID"
    if ($topicId) {
        Set-EnvValue "TG_TOPIC_ID" $topicId
        Write-Ok "Топік змінено на: $topicId"
        Write-Info "Перезапустіть бота (пункт 7) для застосування змін"
    } else {
        Write-Info "Скасовано — значення не змінено"
    }
    Pause-Return
}

# ─── 3. Reconfigure Telegram ─────────────────────────────────

function Setup-Telegram-Full {
    Write-Host "  ── Telegram налаштування ──" -ForegroundColor Cyan
    $token   = Read-Secret "TG_BOT_TOKEN"
    $groupId = Read-Secret "TG_GROUP_ID"
    $topicId = Read-Host  "  TG_TOPIC_ID"

    Set-EnvValue "TG_BOT_TOKEN" (Protect-Value $token)
    Set-EnvValue "TG_GROUP_ID"  (Protect-Value $groupId)
    Set-EnvValue "TG_TOPIC_ID"  $topicId

    Write-Ok "Telegram збережено (токен та група — зашифровані)"
}

function Setup-Telegram {
    Show-Header "3. Переналаштування Telegram"
    Setup-Telegram-Full
    Write-Info "Перезапустіть бота (пункт 7) для застосування змін"
    Pause-Return
}

# ─── 4. Reconfigure OpenAI ───────────────────────────────────

function Setup-OpenAI {
    if (-not (Show-Header -and $false)) { Show-Header "4. Переналаштування OpenAI" }
    Write-Host "  ── OpenAI налаштування ──" -ForegroundColor Cyan
    $apiKey = Read-Secret "OPENAI_API_KEY"
    $model  = Read-Host  "  OPENAI_MODEL [gpt-4o-mini]"
    if (-not $model) { $model = "gpt-4o-mini" }

    Set-EnvValue "OPENAI_API_KEY" (Protect-Value $apiKey)
    Set-EnvValue "OPENAI_MODEL"   $model

    Write-Ok "OpenAI збережено (ключ — зашифрований)"
}

function Reconfigure-OpenAI {
    Show-Header "4. Переналаштування OpenAI"
    Setup-OpenAI
    Pause-Return
}

# ─── 5. Show config ──────────────────────────────────────────

function Show-Config {
    Show-Header "5. Поточні налаштування"
    $env = Read-Env

    $fields = @(
        @{K="SERVER_ID";           L="Сервер ID"         },
        @{K="COMPANY_NAME";        L="Назва компанії"     },
        @{K="TG_BOT_TOKEN";        L="Telegram токен"     },
        @{K="TG_GROUP_ID";         L="Telegram група"     },
        @{K="TG_TOPIC_ID";         L="Telegram топік"     },
        @{K="OPENAI_API_KEY";      L="OpenAI ключ"        },
        @{K="OPENAI_MODEL";        L="OpenAI модель"      },
        @{K="DISK_PATHS";          L="Диски"              },
        @{K="DISK_WARNING_PERCENT";L="Диск warn %"        },
        @{K="CPU_WARNING_PERCENT"; L="CPU warn %"         },
        @{K="RAM_WARNING_PERCENT"; L="RAM warn %"         },
        @{K="BACKUP_PATH";         L="Шлях бекапів"       },
        @{K="MONITOR_SERVICES";    L="Сервіси"            },
        @{K="ALERT_COOLDOWN_MIN";  L="Кулдаун (хв)"      }
    )

    foreach ($f in $fields) {
        $label = $f.L.PadRight(22)
        $value = Get-DisplayValue $f.K $env
        $color = if ($value -eq "[не задано]") { "DarkGray" } `
            elseif ($value -eq "[зашифровано]") { "Green" } `
            else { "White" }
        Write-Host "  $label : " -NoNewline
        Write-Host $value -ForegroundColor $color
    }

    Pause-Return
}

# ─── 6. Task status ──────────────────────────────────────────

function Show-Status {
    Show-Header "6. Статус завдань"

    foreach ($name in @("1C_Monitor", "1C_Monitor_Bot")) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host "  $name" -NoNewline -ForegroundColor White
            Write-Host " [НЕ ВСТАНОВЛЕНО]" -ForegroundColor DarkGray
            continue
        }
        $info  = Get-ScheduledTaskInfo -TaskName $name
        $state = $task.State
        $color = switch ($state) {
            "Running" { "Green" }
            "Ready"   { "Cyan"  }
            default   { "Red"   }
        }
        Write-Host "  $name" -NoNewline -ForegroundColor White
        Write-Host " [$state]" -ForegroundColor $color
        Write-Host "    Останній запуск : $($info.LastRunTime)"
        Write-Host "    Код результату  : $($info.LastTaskResult)"
        Write-Host "    Наступний запуск: $($info.NextRunTime)"
        Write-Host ""
    }

    Pause-Return
}

# ─── 7. Restart ──────────────────────────────────────────────

function Restart-Monitor {
    Show-Header "7. Перезапуск моніторингу"

    Write-Step "Зупинка Python процесів бота..."
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match "bot\.py" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Ok "Процеси зупинені"

    foreach ($name in @("1C_Monitor", "1C_Monitor_Bot")) {
        try {
            Stop-ScheduledTask  -TaskName $name -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $name
            Write-Ok "Завдання $name перезапущено"
        } catch {
            Write-Err "Не вдалося перезапустити $name"
        }
    }

    Pause-Return
}

# ─── 8. Uninstall ────────────────────────────────────────────

function Uninstall-Monitor {
    Show-Header "8. Видалення"
    Write-Host "  Це видалить Task Scheduler завдання." -ForegroundColor Red
    Write-Host ""
    $confirm = Read-Host "  Введіть 'ТАК' для підтвердження"
    if ($confirm -cne "ТАК") {
        Write-Info "Скасовано."
        Pause-Return; return
    }

    foreach ($name in @("1C_Monitor", "1C_Monitor_Bot")) {
        Stop-ScheduledTask       -TaskName $name -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        Write-Ok "Завдання $name видалено"
    }

    Write-Step "Зупинка Python процесів..."
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match "monitor\.py|bot\.py" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Ok "Python процеси зупинені"

    Write-Host ""
    $delFiles = Read-Host "  Видалити всі файли програми? (введіть 'ТАК')"
    if ($delFiles -cne "ТАК") {
        Write-Ok "Завдання видалено. Файли залишені у: $ScriptDir"
        Pause-Return; return
    }

    Set-Location $env:TEMP
    try {
        Remove-Item $ScriptDir -Recurse -Force
        Write-Ok "Файли видалені"
    } catch {
        Write-Err "Не вдалося видалити деякі файли (можливо відкриті): $_"
    }

    Write-Host ""
    Write-Host "  Видалення завершено. Вікно закриється через 3 секунди..." -ForegroundColor Green
    Start-Sleep 3
    exit
}

# ─── Main menu ───────────────────────────────────────────────

while ($true) {
    Show-Header "1С Monitor — Управління"
    Write-Host "  1. Встановити / Оновити"              -ForegroundColor White
    Write-Host "  2. Змінити Telegram топік"            -ForegroundColor White
    Write-Host "  3. Переналаштувати Telegram"          -ForegroundColor White
    Write-Host "  4. Переналаштувати OpenAI"            -ForegroundColor White
    Write-Host "  5. Переглянути налаштування"          -ForegroundColor White
    Write-Host "  6. Статус завдань"                    -ForegroundColor White
    Write-Host "  7. Перезапустити моніторинг"          -ForegroundColor White
    Write-Host "  8. Видалити"                          -ForegroundColor Red
    Write-Host "  0. Вийти"                             -ForegroundColor DarkGray
    Write-Host ""

    $choice = Read-Host "  Ваш вибір"
    switch ($choice) {
        "1" { Install-Monitor    }
        "2" { Setup-Topic        }
        "3" { Setup-Telegram     }
        "4" { Reconfigure-OpenAI }
        "5" { Show-Config        }
        "6" { Show-Status        }
        "7" { Restart-Monitor    }
        "8" { Uninstall-Monitor  }
        "0" { exit               }
        default {
            Write-Host "  Невірний вибір. Спробуйте ще раз." -ForegroundColor Red
            Start-Sleep 1
        }
    }
}
