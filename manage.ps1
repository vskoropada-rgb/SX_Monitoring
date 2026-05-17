# manage.ps1 - 1C Monitor Management Script
# Run as Administrator: Right-click -> "Run with PowerShell" (as Admin)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "SilentlyContinue"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile    = Join-Path $ScriptDir ".env"
$EnvExample = Join-Path $ScriptDir ".env.example"
$ReqFile    = Join-Path $ScriptDir "requirements.txt"

# ─── Admin check ─────────────────────────────────────────────

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Host ""
    Write-Host "  ПОМИЛКА: Запустіть PowerShell від імені Адміністратора!" -ForegroundColor Red
    Write-Host ""
    Read-Host "  Натисніть Enter"
    exit 1
}

# ─── DPAPI ───────────────────────────────────────────────────

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

function Unprotect-Value {
    param([string]$Value)
    if (-not $Value -or -not $Value.StartsWith("ENCRYPTED:")) { return $Value }
    try {
        Add-Type -AssemblyName System.Security
        $bytes     = [Convert]::FromBase64String($Value.Substring(10))
        $decrypted = [System.Security.Cryptography.ProtectedData]::Unprotect(
            $bytes, $null,
            [System.Security.Cryptography.DataProtectionScope]::LocalMachine
        )
        return [System.Text.Encoding]::UTF8.GetString($decrypted)
    } catch {
        return ""
    }
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
    if (-not (Test-Path $EnvFile)) { Copy-Item $EnvExample $EnvFile }
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
    if (-not $val)                       { return "[не задано]"   }
    if ($val.StartsWith("ENCRYPTED:"))   { return "[зашифровано]" }
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

# ─── Telegram API ────────────────────────────────────────────

function New-TelegramTopic {
    param([string]$TopicName)

    $env     = Read-Env
    $token   = Unprotect-Value $env["TG_BOT_TOKEN"]
    $groupId = Unprotect-Value $env["TG_GROUP_ID"]

    if (-not $token -or -not $groupId) {
        Write-Err "Telegram не налаштований. Спочатку виконайте пункт 3."
        return $null
    }

    Write-Step "Створення топіку '$TopicName' в Telegram..."
    try {
        $body = @{ chat_id = $groupId; name = $TopicName } | ConvertTo-Json -Compress
        $resp = Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$token/createForumTopic" `
            -Method Post -Body $body -ContentType "application/json; charset=utf-8" `
            -ErrorAction Stop
        if ($resp.ok) {
            $id = $resp.result.message_thread_id
            Write-Ok "Топік створено! ID: $id"
            return $id
        } else {
            Write-Err "Telegram API: $($resp.description)"
            return $null
        }
    } catch {
        Write-Err "Помилка запиту: $_"
        return $null
    }
}

# ─── Backup validation ───────────────────────────────────────

function Test-BackupFolder {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path $Path)) {
        Write-Err  "Папка не існує: $Path"
        return $false
    }

    $zips = Get-ChildItem $Path -Filter "*.zip" -File -ErrorAction SilentlyContinue
    if (-not $zips) {
        Write-Info "ZIP-файлів не знайдено (можливо бекап ще не запускався)"
        return $true
    }

    # Відфільтровуємо порожні/пошкоджені (< 1 KB)
    $valid = $zips | Where-Object { $_.Length -gt 1024 }
    $tiny  = $zips | Where-Object { $_.Length -le 1024 }

    if ($tiny) {
        Write-Err "Знайдено $($tiny.Count) файл(ів) менше 1 KB — можливо пошкоджені:"
        $tiny | ForEach-Object { Write-Host "    $_  ($($_.Length) байт)" -ForegroundColor Red }
    }

    if ($valid) {
        $latest = $valid | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $sizeMB = [math]::Round($latest.Length / 1MB, 1)
        $age    = [math]::Round(((Get-Date) - $latest.LastWriteTime).TotalHours, 1)
        Write-Ok "Останній бекап: $($latest.Name)  ($sizeMB MB, ${age}г тому)"
        Write-Ok "Всього валідних ZIP: $($valid.Count)"
    }

    return $true
}

# ─── 1. Install / Update ─────────────────────────────────────

function Install-Monitor {
    Show-Header "1. Встановлення / Оновлення"

    Write-Step "Перевірка Python..."
    $pyVer = python --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Python не знайдений. Встановіть Python 3.10+ з python.org"
        Pause-Return; return
    }
    Write-Ok $pyVer

    Write-Step "Встановлення залежностей pip..."
    python -m pip install -r $ReqFile --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Помилка встановлення залежностей"
        Pause-Return; return
    }
    Write-Ok "Залежності встановлені"

    if (-not (Test-Path $EnvFile)) {
        Write-Info "Файл .env не знайдений — запускаю першочергове налаштування..."
        Write-Host ""
        Setup-FirstRun
    } else {
        Write-Ok "Файл .env знайдений"
    }

    Create-Tasks

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
    Write-Host "  ── Крок 1: Telegram ──" -ForegroundColor Cyan
    Setup-Telegram-Credentials

    Write-Host ""
    Write-Host "  ── Крок 2: Компанія ──" -ForegroundColor Cyan
    Setup-Company-Details

    Write-Host ""
    Write-Host "  ── Крок 3: OpenAI ──" -ForegroundColor Cyan
    Setup-OpenAI-Credentials
}

function Create-Tasks {
    Write-Step "Створення Task Scheduler завдань..."

    foreach ($t in @("1C_Monitor", "1C_Monitor_Bot", "1C_Monitor_Watchdog")) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
    }

    $python    = (Get-Command python).Source
    $settings  = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

    $a1 = New-ScheduledTaskAction -Execute $python -Argument "`"$ScriptDir\monitor.py`""
    $t1 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 1) `
                                   -Once -At (Get-Date)
    try {
        Register-ScheduledTask -TaskName "1C_Monitor" -Action $a1 -Trigger $t1 `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Ok "1C_Monitor створено (кожну хвилину)"
    } catch { Write-Err "Помилка 1C_Monitor: $_" }

    $a2 = New-ScheduledTaskAction -Execute $python -Argument "`"$ScriptDir\bot.py`""
    $t2 = New-ScheduledTaskTrigger -AtStartup
    try {
        Register-ScheduledTask -TaskName "1C_Monitor_Bot" -Action $a2 -Trigger $t2 `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Ok "1C_Monitor_Bot створено (при старті)"
    } catch { Write-Err "Помилка 1C_Monitor_Bot: $_" }

    # Watchdog — кожні 5 хвилин, перезапускає бота якщо впав
    $a3 = New-ScheduledTaskAction -Execute "powershell" `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptDir\watchdog.ps1`""
    $t3 = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) `
                                   -Once -At (Get-Date)
    try {
        Register-ScheduledTask -TaskName "1C_Monitor_Watchdog" -Action $a3 -Trigger $t3 `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Ok "1C_Monitor_Watchdog створено (кожні 5 хвилин)"
    } catch { Write-Err "Помилка 1C_Monitor_Watchdog: $_" }
}

# ─── 2. Setup company ────────────────────────────────────────

function Setup-Company {
    Show-Header "2. Налаштування компанії"
    Setup-Company-Details
    Write-Info "Перезапустіть моніторинг (пункт 7) для застосування змін"
    Pause-Return
}

function Setup-Company-Details {
    $env = Read-Env

    Write-Host "  Поточна назва : $(Get-DisplayValue 'COMPANY_NAME' $env)" -ForegroundColor DarkGray
    Write-Host "  Поточний ID   : $(Get-DisplayValue 'SERVER_ID' $env)"    -ForegroundColor DarkGray
    Write-Host ""

    $name = Read-Host "  Назва компанії (напр. Компанія А)"
    $sid  = Read-Host "  SERVER_ID — латиниця без пробілів (напр. company_a)"

    if ($name) { Set-EnvValue "COMPANY_NAME" $name }
    if ($sid)  { Set-EnvValue "SERVER_ID"    $sid  }

    # Автоматично створити топік в Telegram
    Write-Host ""
    $displayName = if ($name) { $name } else { (Get-DisplayValue "COMPANY_NAME" $env) }
    $topicId = New-TelegramTopic $displayName

    if ($topicId) {
        Set-EnvValue "TG_TOPIC_ID" $topicId
    } else {
        Write-Info "Не вдалося створити топік автоматично."
        $manual = Read-Host "  Введіть TG_TOPIC_ID вручну (або Enter щоб пропустити)"
        if ($manual) { Set-EnvValue "TG_TOPIC_ID" $manual }
    }

    # Папка бекапів
    Write-Host ""
    Write-Host "  ── Папка бекапів ──" -ForegroundColor Cyan
    Write-Host "  Поточна: $(Get-DisplayValue 'BACKUP_PATH' $env)" -ForegroundColor DarkGray
    Write-Host ""
    $backupPath = Read-Host "  Повний шлях до папки з ZIP-бекапами"

    if ($backupPath) {
        Set-EnvValue "BACKUP_PATH" $backupPath
        Write-Host ""
        Test-BackupFolder $backupPath | Out-Null
    }
}

# ─── 3. Telegram credentials ─────────────────────────────────

function Setup-Telegram {
    Show-Header "3. Переналаштування Telegram"
    Setup-Telegram-Credentials
    Write-Info "Перезапустіть бота (пункт 7) для застосування змін"
    Pause-Return
}

function Setup-Telegram-Credentials {
    Write-Host "  TG_BOT_TOKEN та TG_GROUP_ID будуть зашифровані." -ForegroundColor DarkGray
    Write-Host ""
    $token   = Read-Secret "TG_BOT_TOKEN"
    $groupId = Read-Secret "TG_GROUP_ID"

    Set-EnvValue "TG_BOT_TOKEN" (Protect-Value $token)
    Set-EnvValue "TG_GROUP_ID"  (Protect-Value $groupId)

    Write-Ok "Telegram збережено (зашифровано)"
}

# ─── 4. OpenAI ───────────────────────────────────────────────

function Setup-OpenAI {
    Show-Header "4. Переналаштування OpenAI"
    Setup-OpenAI-Credentials
    Pause-Return
}

function Setup-OpenAI-Credentials {
    Write-Host "  OPENAI_API_KEY буде зашифрований." -ForegroundColor DarkGray
    Write-Host ""
    $apiKey = Read-Secret "OPENAI_API_KEY"
    $env    = Read-Env
    $model  = Read-Host "  OPENAI_MODEL [$( if ($env['OPENAI_MODEL']) { $env['OPENAI_MODEL'] } else { 'gpt-4o-mini' })]"
    if (-not $model) { $model = if ($env["OPENAI_MODEL"]) { $env["OPENAI_MODEL"] } else { "gpt-4o-mini" } }

    Set-EnvValue "OPENAI_API_KEY" (Protect-Value $apiKey)
    Set-EnvValue "OPENAI_MODEL"   $model

    Write-Ok "OpenAI збережено (зашифровано)"
}

# ─── 5. Show config ──────────────────────────────────────────

function Show-Config {
    Show-Header "5. Поточні налаштування"
    $env = Read-Env

    $fields = @(
        @{K="SERVER_ID";            L="Сервер ID"       },
        @{K="COMPANY_NAME";         L="Назва компанії"  },
        @{K="TG_BOT_TOKEN";         L="Telegram токен"  },
        @{K="TG_GROUP_ID";          L="Telegram група"  },
        @{K="TG_TOPIC_ID";          L="Telegram топік"  },
        @{K="OPENAI_API_KEY";       L="OpenAI ключ"     },
        @{K="OPENAI_MODEL";         L="OpenAI модель"   },
        @{K="DISK_PATHS";           L="Диски"           },
        @{K="DISK_WARNING_PERCENT"; L="Диск warn %"     },
        @{K="CPU_WARNING_PERCENT";  L="CPU warn %"      },
        @{K="RAM_WARNING_PERCENT";  L="RAM warn %"      },
        @{K="BACKUP_PATH";          L="Папка бекапів"   },
        @{K="BACKUP_MAX_AGE_HOURS"; L="Макс. вік (год)" },
        @{K="BACKUP_MIN_SIZE_MB";   L="Мін. розмір (MB)"},
        @{K="ALERT_COOLDOWN_MIN";   L="Кулдаун (хв)"   }
    )

    foreach ($f in $fields) {
        $label = $f.L.PadRight(22)
        $value = Get-DisplayValue $f.K $env
        $color = if ($value -eq "[не задано]")   { "DarkGray" } `
            elseif ($value -eq "[зашифровано]")  { "Green"    } `
            else                                 { "White"    }
        Write-Host "  $label : " -NoNewline
        Write-Host $value -ForegroundColor $color
    }

    # Перевірка папки бекапів
    $backupPath = $env["BACKUP_PATH"]
    if ($backupPath -and (Test-Path $backupPath)) {
        Write-Host ""
        Write-Host "  ── Стан бекапів ──" -ForegroundColor Cyan
        Test-BackupFolder $backupPath | Out-Null
    }

    Pause-Return
}

# ─── 6. Task status ──────────────────────────────────────────

function Show-Status {
    Show-Header "6. Статус завдань"

    foreach ($name in @("1C_Monitor", "1C_Monitor_Bot", "1C_Monitor_Watchdog")) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Host "  $name [НЕ ВСТАНОВЛЕНО]" -ForegroundColor DarkGray
            Write-Host ""
            continue
        }
        $info  = Get-ScheduledTaskInfo -TaskName $name
        $state = $task.State
        $color = switch ($state) { "Running" { "Green" } "Ready" { "Cyan" } default { "Red" } }
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

    foreach ($name in @("1C_Monitor", "1C_Monitor_Bot", "1C_Monitor_Watchdog")) {
        try {
            Stop-ScheduledTask  -TaskName $name -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $name
            Write-Ok "$name перезапущено"
        } catch { Write-Err "Не вдалося перезапустити $name" }
    }

    Pause-Return
}

# ─── 8. Uninstall ────────────────────────────────────────────

function Uninstall-Monitor {
    Show-Header "8. Видалення"
    Write-Host "  Це видалить Task Scheduler завдання." -ForegroundColor Red
    Write-Host ""
    $confirm = Read-Host "  Введіть 'ТАК' для підтвердження"
    if ($confirm -cne "ТАК") { Write-Info "Скасовано."; Pause-Return; return }

    foreach ($name in @("1C_Monitor", "1C_Monitor_Bot", "1C_Monitor_Watchdog")) {
        Stop-ScheduledTask       -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
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
        Write-Ok "Завдання видалено. Файли залишені: $ScriptDir"
        Pause-Return; return
    }

    Set-Location $env:TEMP
    try {
        Remove-Item $ScriptDir -Recurse -Force
        Write-Ok "Файли видалені"
    } catch {
        Write-Err "Деякі файли не вдалося видалити (можливо відкриті): $_"
    }

    Write-Host ""
    Write-Host "  Готово. Вікно закриється через 3 секунди..." -ForegroundColor Green
    Start-Sleep 3
    exit
}

# ─── Main menu ───────────────────────────────────────────────

while ($true) {
    Show-Header "1С Monitor — Управління"
    Write-Host "  1. Встановити / Оновити"                     -ForegroundColor White
    Write-Host "  2. Налаштувати компанію  (назва / топік / бекапи)" -ForegroundColor White
    Write-Host "  3. Переналаштувати Telegram  (токен / група)" -ForegroundColor White
    Write-Host "  4. Переналаштувати OpenAI"                   -ForegroundColor White
    Write-Host "  5. Переглянути налаштування"                 -ForegroundColor White
    Write-Host "  6. Статус завдань"                           -ForegroundColor White
    Write-Host "  7. Перезапустити моніторинг"                 -ForegroundColor White
    Write-Host "  8. Видалити"                                 -ForegroundColor Red
    Write-Host "  0. Вийти"                                    -ForegroundColor DarkGray
    Write-Host ""

    $choice = Read-Host "  Ваш вибір"
    switch ($choice) {
        "1" { Install-Monitor  }
        "2" { Setup-Company    }
        "3" { Setup-Telegram   }
        "4" { Setup-OpenAI     }
        "5" { Show-Config      }
        "6" { Show-Status      }
        "7" { Restart-Monitor  }
        "8" { Uninstall-Monitor }
        "0" { exit             }
        default {
            Write-Host "  Невірний вибір." -ForegroundColor Red
            Start-Sleep 1
        }
    }
}
