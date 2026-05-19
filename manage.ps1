# manage.ps1 - 1C Monitor Management Script
# Run as Administrator: Right-click -> "Run with PowerShell" (as Admin)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# Bypass перевірки SSL-сертифікатів — потрібно для Windows 2008 R2
# зі застарілим сховищем кореневих сертифікатів
try { [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$ErrorActionPreference = "SilentlyContinue"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile    = Join-Path $ScriptDir ".env"
$EnvExample = Join-Path $ScriptDir ".env.example"
$ReqFile    = Join-Path $ScriptDir "requirements.txt"

# Python 3.8 — остання версія з підтримкою Windows 2008 R2
$PY_VERSION     = "3.8.20"
$PY_URL         = "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-amd64.exe"
$PY_MIN_MAJOR   = 3
$PY_MIN_MINOR   = 8

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

function Invoke-TgApi {
    param([string]$Token, [string]$Method, [hashtable]$Body)
    try {
        return Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$Token/$Method" `
            -Method Post `
            -Body ($Body | ConvertTo-Json -Compress) `
            -ContentType "application/json; charset=utf-8" `
            -ErrorAction Stop
    } catch {
        # Telegram повертає JSON навіть при HTTP 4xx — витягуємо його
        $raw = $_.ErrorDetails.Message
        if ($raw) {
            try { return $raw | ConvertFrom-Json } catch {}
        }
        return $null
    }
}

function New-TelegramTopic {
    param([string]$TopicName)

    $env     = Read-Env
    $token   = Unprotect-Value $env["TG_BOT_TOKEN"]
    $groupId = Unprotect-Value $env["TG_GROUP_ID"]

    if (-not $token -or -not $groupId) {
        Write-Err "Telegram не налаштований. Спочатку виконайте пункт 3."
        return $null
    }

    # ── Крок 1: перевірка групи ──────────────────────────────
    Write-Step "Перевірка групи ($groupId)..."
    $chatResp = Invoke-TgApi $token "getChat" @{ chat_id = $groupId }

    if (-not $chatResp) {
        Write-Err "Не вдалося зв'язатися з Telegram API."
        return $null
    }

    if (-not $chatResp.ok) {
        $code = $chatResp.error_code
        $desc = $chatResp.description
        Write-Err "getChat помилка $code : $desc"
        if ($code -eq 404 -or $desc -match "not found|chat not found") {
            Write-Host ""
            Write-Host "  Можливі причини:" -ForegroundColor Yellow
            Write-Host "  1. TG_GROUP_ID неправильний — перевірте пункт 3" -ForegroundColor Yellow
            Write-Host "  2. Бот ще не доданий до групи" -ForegroundColor Yellow
            Write-Host "  3. Supergroup ID має починатись з -100 (напр. -1001234567890)" -ForegroundColor Yellow
            Write-Host ""
            Write-Host "  Поточний TG_GROUP_ID: $groupId" -ForegroundColor Cyan
        }
        return $null
    }

    $chat = $chatResp.result
    Write-Ok "Група знайдена: '$($chat.title)'  (ID: $($chat.id), тип: $($chat.type))"

    # ── Крок 2: перевірка що увімкнені Гілки ────────────────
    if (-not $chat.is_forum) {
        Write-Err "Гілки (Forum Topics) не увімкнено в цій групі!"
        Write-Host ""
        Write-Host "  Як увімкнути:" -ForegroundColor Yellow
        Write-Host "  Telegram → група → Редагувати → Гілки → увімкнути" -ForegroundColor Yellow
        Write-Host ""
        return $null
    }
    Write-Ok "Гілки увімкнено"

    # ── Крок 3: створення топіку ─────────────────────────────
    Write-Step "Створення топіку '$TopicName'..."
    $resp = Invoke-TgApi $token "createForumTopic" @{ chat_id = $groupId; name = $TopicName }

    if (-not $resp) {
        Write-Err "Не отримано відповідь від API."
        return $null
    }

    if ($resp.ok) {
        $id = $resp.result.message_thread_id
        Write-Ok "Топік створено! ID: $id"
        return $id
    }

    $code = $resp.error_code
    $desc = $resp.description
    Write-Err "createForumTopic помилка $code : $desc"
    if ($desc -match "not enough rights") {
        Write-Host "  Бот має бути адміністратором з правом 'Manage Topics'" -ForegroundColor Yellow
    }
    return $null
}

function Get-TelegramGroupId {
    param([string]$Token)
    try {
        $resp = Invoke-RestMethod -Uri "https://api.telegram.org/bot$Token/getUpdates" `
            -Method Get -UseBasicParsing -ErrorAction Stop
        if ($resp.ok -and $resp.result) {
            foreach ($upd in ($resp.result | Sort-Object { $_.update_id } -Descending)) {
                $chatId = $null
                if ($upd.message)        { $chatId = $upd.message.chat.id }
                if ($upd.channel_post)   { $chatId = $upd.channel_post.chat.id }
                if ($upd.my_chat_member) { $chatId = $upd.my_chat_member.chat.id }
                if ($chatId -and "$chatId" -match "^-\d+$") { return "$chatId" }
            }
        }
    } catch {}
    return $null
}

# ─── Backup validation ───────────────────────────────────────

function Test-BackupFolder {
    param([string]$Path)

    if (-not $Path -or -not (Test-Path $Path)) {
        Write-Err  "Папка не існує: $Path"
        return $false
    }

    $exts = @("*.zip","*.rar","*.7z","*.bak","*.dt","*.1cd")
    $allFiles = @()
    foreach ($ext in $exts) {
        $allFiles += Get-ChildItem $Path -Filter $ext -File -ErrorAction SilentlyContinue
    }

    if (-not $allFiles) {
        Write-Info "Архівів не знайдено (zip/rar/7z/bak/dt/1cd) — можливо бекап ще не запускався"
        return $true
    }

    $valid = $allFiles | Where-Object { $_.Length -gt 1024 }
    $tiny  = $allFiles | Where-Object { $_.Length -le 1024 }

    if ($tiny) {
        Write-Err "Знайдено $($tiny.Count) файл(ів) < 1 KB (можливо пошкоджені)"
    }

    if ($valid) {
        $latest = $valid | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $sizeMB = [math]::Round($latest.Length / 1MB, 1)
        $age    = [math]::Round(((Get-Date) - $latest.LastWriteTime).TotalHours, 1)
        $ext    = $latest.Extension.ToUpper().TrimStart('.')
        Write-Ok "Останній: $($latest.Name)  ($sizeMB MB, ${age}г тому) [$ext]"
        Write-Ok "Всього архівів: $($valid.Count)"
    }

    return $true
}

# ─── TLS 1.2 ─────────────────────────────────────────────────

function Test-Tls {
    Write-Info "Перевірка TLS та інтернет-підключення..."

    # Показуємо доступні в .NET TLS-протоколи
    try {
        $protos = [Enum]::GetValues([Net.SecurityProtocolType]) |
                  Where-Object { $_ -ne 0 } |
                  ForEach-Object { $_.ToString() }
        Write-Host "  .NET TLS протоколи: $($protos -join ', ')" -ForegroundColor DarkGray
    } catch {}

    # Встановлюємо TLS 1.2 + bypass сертифікатів (для 2008 R2)
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    } catch {
        Write-Err "Не вдалося встановити TLS 1.2: $_"
        Write-Host "  Потрібен .NET Framework 4.5 або новіший." -ForegroundColor Yellow
        return $false
    }

    # Перевіряємо реальне підключення до потрібних ресурсів
    $checks = @(
        @{ Name = "python.org";               Url = "https://www.python.org/ftp/python/" },
        @{ Name = "raw.githubusercontent.com"; Url = "https://raw.githubusercontent.com/" },
        @{ Name = "api.telegram.org";          Url = "https://api.telegram.org/" }
    )

    $allOk = $true
    foreach ($c in $checks) {
        try {
            $req          = [Net.HttpWebRequest]::Create($c.Url)
            $req.Method   = "HEAD"
            $req.Timeout  = 8000
            $resp = $req.GetResponse()
            $resp.Close()
            Write-Ok "$($c.Name)"
        } catch {
            $msg = $_.Exception.Message -replace "`r`n.*",""
            Write-Err "$($c.Name) — $msg"
            $allOk = $false
        }
    }

    if (-not $allOk) {
        Write-Host ""
        Write-Host "  Можливі причини:" -ForegroundColor Yellow
        Write-Host "  1. TLS 1.2 вимкнений у реєстрі — запустіть Enable-Tls12 і перезавантажте" -ForegroundColor Yellow
        Write-Host "  2. Брандмауер блокує HTTPS (порт 443)" -ForegroundColor Yellow
        Write-Host "  3. Немає інтернету на сервері" -ForegroundColor Yellow
    }

    return $allOk
}

function Enable-Tls12 {
    Write-Info "Увімкнення TLS 1.2 у реєстрі..."
    $tlsBase = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols"
    foreach ($side in @("Client", "Server")) {
        $key = "$tlsBase\TLS 1.2\$side"
        if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
        Set-ItemProperty -Path $key -Name "Enabled"           -Value 1 -Type DWord -Force
        Set-ItemProperty -Path $key -Name "DisabledByDefault" -Value 0 -Type DWord -Force
    }
    # .NET strong crypto — потрібно щоб pip, requests та openai використовували TLS 1.2
    foreach ($dotnet in @(
        "HKLM:\SOFTWARE\Microsoft\.NETFramework\v4.0.30319",
        "HKLM:\SOFTWARE\Wow6432Node\Microsoft\.NETFramework\v4.0.30319"
    )) {
        if (-not (Test-Path $dotnet)) { New-Item -Path $dotnet -Force | Out-Null }
        Set-ItemProperty -Path $dotnet -Name "SchUseStrongCrypto" -Value 1 -Type DWord -Force
    }
    Write-Ok "TLS 1.2 увімкнено (потрібне перезавантаження для повного застосування)"
}

# ─── 0. Prepare server ──────────────────────────────────────

function Prepare-Server {
    Show-Header "0. Підготовка сервера"

    # Системна інформація
    $os   = (Get-WmiObject Win32_OperatingSystem -ErrorAction SilentlyContinue)
    $arch = if ([Environment]::Is64BitOperatingSystem) { "64-bit" } else { "32-bit" }
    $ram  = if ($os) { "$([math]::Round($os.TotalVisibleMemorySize/1MB, 1)) GB" } else { "?" }

    Write-Host "  Комп'ютер : $env:COMPUTERNAME" -ForegroundColor Cyan
    Write-Host "  ОС        : $(if ($os) { $os.Caption } else { '?' }) $arch" -ForegroundColor Cyan
    Write-Host "  RAM       : $ram" -ForegroundColor Cyan
    Write-Host "  PS версія : $($PSVersionTable.PSVersion)" -ForegroundColor Cyan
    Write-Host ""

    # ExecutionPolicy — потрібно для watchdog.ps1 та інших скриптів
    $policy = Get-ExecutionPolicy -Scope LocalMachine -ErrorAction SilentlyContinue
    if ($policy -in @("Restricted", "AllSigned", "Undefined")) {
        Write-Info "ExecutionPolicy: $policy — встановлюю RemoteSigned"
        try {
            Set-ExecutionPolicy RemoteSigned -Scope LocalMachine -Force
            Write-Ok "ExecutionPolicy = RemoteSigned"
        } catch {
            Write-Err "Не вдалося змінити ExecutionPolicy: $_"
        }
    } else {
        Write-Ok "ExecutionPolicy: $policy"
    }
    Write-Host ""

    # TLS 1.2: спочатку перевіряємо підключення, потім пишемо реєстр
    $tlsOk = Test-Tls
    Write-Host ""
    Enable-Tls12
    if (-not $tlsOk) {
        Write-Host ""
        Write-Host "  УВАГА: інтернет недоступний або TLS не працює." -ForegroundColor Red
        Write-Host "  Реєстровий ключ TLS 1.2 вже записано — перезавантажте сервер і спробуйте знову." -ForegroundColor Yellow
        Pause-Return; return
    }
    Write-Host ""

    # Python — перевірка
    $pyOk = Test-PythonVersion
    if (-not $pyOk) {
        Write-Host ""
        $ans = Read-Host "  Встановити Python $PY_VERSION автоматично? (Y/n)"
        if ($ans -ne "n" -and $ans -ne "N") {
            Install-Python
            $pyOk = Test-PythonVersion
        }
    }

    # pip upgrade
    if ($pyOk) {
        Write-Host ""
        Write-Step "Оновлення pip..."
        python -m pip install --upgrade pip --quiet 2>&1 | Out-Null
        Write-Ok "pip оновлено до $(python -m pip --version 2>&1)"
    }

    Write-Host ""
    if ($pyOk) {
        Write-Ok "Сервер готовий. Перейдіть до пункту 1 (Встановити)."
    } else {
        Write-Err "Python не встановлений — пункт 1 не запрацює."
    }
    Pause-Return
}

function Test-PythonVersion {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Err "Python не знайдений у PATH"
        return $false
    }
    $verStr = python --version 2>&1
    if ($verStr -match "Python (\d+)\.(\d+)") {
        $major = [int]$matches[1]; $minor = [int]$matches[2]
        if ($major -gt $PY_MIN_MAJOR -or ($major -eq $PY_MIN_MAJOR -and $minor -ge $PY_MIN_MINOR)) {
            Write-Ok "Python: $verStr  ($($cmd.Source))"
            return $true
        }
        Write-Err "Python $major.$minor — застаріла версія, потрібно $PY_MIN_MAJOR.$PY_MIN_MINOR+"
        return $false
    }
    Write-Err "Не вдалося визначити версію Python: $verStr"
    return $false
}

function Install-Python {
    Write-Host ""

    # Спочатку пробуємо winget (Windows 10 1709+ / Server 2022)
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Step "Встановлення через winget (Python 3.12)..."
        $result = winget install Python.Python.3.12 --silent `
            --accept-source-agreements --accept-package-agreements 2>&1
        Refresh-Path
        if (Get-Command python -ErrorAction SilentlyContinue) {
            Write-Ok "Python встановлено через winget"
            return
        }
        Write-Info "winget не спрацював, завантажую напряму..."
    }

    # Пряме завантаження з python.org
    $tmpExe = Join-Path $env:TEMP "python-$PY_VERSION-setup.exe"
    Write-Step "Завантаження $PY_URL"
    Write-Info "Розмір: ~25 MB, може зайняти кілька хвилин..."

    try {
        # Net.WebClient з TLS 1.2 і bypass SSL — потрібно для 2008 R2
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($PY_URL, $tmpExe)
        $sizeMB = [math]::Round((Get-Item $tmpExe).Length / 1MB, 1)
        Write-Ok "Завантажено ($sizeMB MB)"
    } catch {
        Write-Err "Помилка завантаження: $_"
        Write-Host ""
        Write-Host "  Завантажте вручну: $PY_URL" -ForegroundColor Yellow
        Write-Host "  Встановіть з параметрами: /quiet InstallAllUsers=1 PrependPath=1" -ForegroundColor Yellow
        return
    }

    Write-Step "Встановлення Python (тихий режим)..."
    $proc = Start-Process $tmpExe `
        -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0" `
        -Wait -PassThru
    Remove-Item $tmpExe -Force -ErrorAction SilentlyContinue

    if ($proc.ExitCode -ne 0) {
        Write-Err "Помилка встановлення (код виходу: $($proc.ExitCode))"
        return
    }

    Refresh-Path

    if (Get-Command python -ErrorAction SilentlyContinue) {
        Write-Ok "Python встановлено: $(python --version 2>&1)"
    } else {
        Write-Info "Python встановлено, але PATH ще не оновлений."
        Write-Info "Закрийте та перевідкрийте PowerShell, або перезавантажте сервер."
    }
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}

# ─── 1. Install / Update ─────────────────────────────────────

function Install-Monitor {
    Show-Header "1. Встановлення / Оновлення"

    Write-Step "Перевірка Python..."
    if (-not (Test-PythonVersion)) {
        Write-Host ""
        Write-Err "Python не готовий. Виконайте спочатку пункт 0 (Підготовка сервера)."
        Pause-Return; return
    }

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

    Write-Step "Запуск моніторингу..."
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    $python = if ($pythonCmd) { $pythonCmd.Source } else { $null }
    if ($python) {
        Start-Process $python -ArgumentList "`"$ScriptDir\main.py`"" -WindowStyle Hidden
        Write-Ok "Моніторинг запущений"
    }

    Write-Host ""
    Write-Ok "Встановлення завершено!"
    Write-Host "  Task Scheduler завдання:" -ForegroundColor Cyan
    Write-Host "    1C_Monitor          — при старті системи (main.py)"
    Write-Host "    1C_Monitor_Watchdog — кожні 5 хвилин"
    Pause-Return
}

function Setup-FirstRun {
    # Якщо поруч є .env.base — імпортуємо токени без ручного введення
    $imported = Import-BaseConfig
    if ($imported) {
        Write-Host "  Кроки: Сервер → Диски → Сервіси" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  ── 1. Назва сервера та Telegram-топік ───" -ForegroundColor Cyan
        Setup-Company-Details
        Write-Host ""
        Write-Host "  ── 2. Диски та папка бекапів ────────────" -ForegroundColor Cyan
        Setup-Paths
        Write-Host ""
        Write-Host "  ── 3. Сервіси 1С / SQL ──────────────────" -ForegroundColor Cyan
        Setup-Services
    } else {
        Write-Host "  Кроки: Telegram → Сервер → Диски → Сервіси → OpenAI (опція)" -ForegroundColor DarkGray
        Write-Host "  Підказка: скопіюйте .env.base з іншого сервера щоб пропустити кроки Telegram/OpenAI" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  ── 1. Telegram ───────────────────────────" -ForegroundColor Cyan
        Setup-Telegram-Credentials
        Write-Host ""
        Write-Host "  ── 2. Назва сервера та Telegram-топік ───" -ForegroundColor Cyan
        Setup-Company-Details
        Write-Host ""
        Write-Host "  ── 3. Диски та папка бекапів ────────────" -ForegroundColor Cyan
        Setup-Paths
        Write-Host ""
        Write-Host "  ── 4. Сервіси 1С / SQL ──────────────────" -ForegroundColor Cyan
        Setup-Services
        Write-Host ""
        Write-Host "  ── 5. OpenAI (Enter = пропустити) ───────" -ForegroundColor Cyan
        Setup-OpenAI-Credentials
    }
}

function Create-Tasks {
    Write-Step "Створення Task Scheduler завдань..."

    foreach ($t in @("1C_Monitor", "1C_Monitor_Bot", "1C_Monitor_Watchdog")) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
    }

    $python    = (Get-Command python).Source
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest

    # main.py — моніторинг + бот в одному процесі, запуск при старті системи
    $settingsMain = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Days 0) `
        -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
    $aMain = New-ScheduledTaskAction -Execute $python -Argument "`"$ScriptDir\main.py`""
    $tMain = New-ScheduledTaskTrigger -AtStartup
    try {
        Register-ScheduledTask -TaskName "1C_Monitor" -Action $aMain -Trigger $tMain `
            -Settings $settingsMain -Principal $principal -Force | Out-Null
        Write-Ok "1C_Monitor створено (при старті, main.py)"
    } catch { Write-Err "Помилка 1C_Monitor: $_" }

    # Watchdog — кожні 5 хвилин, перезапускає main.py якщо впав
    $settingsWd = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $aWd = New-ScheduledTaskAction -Execute "powershell" `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptDir\watchdog.ps1`""
    $tWd = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) `
                                    -Once -At (Get-Date)
    try {
        Register-ScheduledTask -TaskName "1C_Monitor_Watchdog" -Action $aWd -Trigger $tWd `
            -Settings $settingsWd -Principal $principal -Force | Out-Null
        Write-Ok "1C_Monitor_Watchdog створено (кожні 5 хвилин)"
    } catch { Write-Err "Помилка 1C_Monitor_Watchdog: $_" }
}

# ─── 2. Setup company ────────────────────────────────────────

function Setup-Company {
    Show-Header "2. Налаштування сервера"

    Write-Host "  ── Компанія / Топік ──" -ForegroundColor Cyan
    Setup-Company-Details
    Write-Host ""
    Write-Host "  ── Диски та бекапи ──" -ForegroundColor Cyan
    Setup-Paths
    Write-Host ""
    Write-Host "  ── Сервіси ──" -ForegroundColor Cyan
    Setup-Services

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
}

function Setup-Paths {
    $env = Read-Env

    Write-Host "  Поточні диски  : $(Get-DisplayValue 'DISK_PATHS' $env)"   -ForegroundColor DarkGray
    $disks = Read-Host "  Диски через кому (напр. C:\,D:\) або Enter = пропустити"
    if ($disks) { Set-EnvValue "DISK_PATHS" $disks }

    Write-Host ""
    Write-Host "  Папка бекапів  : $(Get-DisplayValue 'BACKUP_PATH' $env)" -ForegroundColor DarkGray
    $backup = Read-Host "  Шлях до архівів (zip/rar/7z/bak) або Enter = пропустити"
    if ($backup) {
        Set-EnvValue "BACKUP_PATH" $backup
        Write-Host ""
        Test-BackupFolder $backup | Out-Null
    }
}

function Setup-Services {
    $env = Read-Env

    Write-Host "  Приклад: 1C:Enterprise 8.3 Server Agent,MSSQLSERVER" -ForegroundColor DarkGray
    Write-Host "  Поточні: $(Get-DisplayValue 'MONITOR_SERVICES' $env)" -ForegroundColor DarkGray
    $services = Read-Host "  Сервіси через кому або Enter = пропустити"
    if ($services) { Set-EnvValue "MONITOR_SERVICES" $services }
}

# ─── B. Base config export / import (AES-256) ────────────────

function Get-AesKey {
    param([string]$Password)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    return $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Password))
}

function Protect-WithPassword {
    param([string]$Plaintext, [byte[]]$Key)
    if (-not $Plaintext) { return "" }
    $secure = ConvertTo-SecureString $Plaintext -AsPlainText -Force
    return ConvertFrom-SecureString $secure -Key $Key
}

function Unprotect-WithPassword {
    param([string]$Encrypted, [byte[]]$Key)
    if (-not $Encrypted) { return "" }
    try {
        $secure = ConvertTo-SecureString $Encrypted -Key $Key
        $bstr   = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        return $plain
    } catch { return $null }
}

function Export-BaseConfig {
    Show-Header "B. Експорт базового конфігу"

    $env = Read-Env
    $token   = Unprotect-Value $env["TG_BOT_TOKEN"]
    $groupId = Unprotect-Value $env["TG_GROUP_ID"]
    $apiKey  = Unprotect-Value $env["OPENAI_API_KEY"]
    $model   = if ($env["OPENAI_MODEL"]) { $env["OPENAI_MODEL"] } else { "gpt-4o-mini" }

    if (-not $token -or -not $groupId) {
        Write-Err "TG_BOT_TOKEN або TG_GROUP_ID не налаштовані — спочатку виконайте пункт 3"
        Pause-Return; return
    }

    Write-Host "  Файл буде зашифрований паролем (AES-256)." -ForegroundColor DarkGray
    Write-Host "  Запам'ятайте пароль — він потрібен при імпорті на новому сервері." -ForegroundColor DarkGray
    Write-Host ""
    $pwd1 = Read-Secret "Пароль"
    $pwd2 = Read-Secret "Повторіть пароль"

    if ($pwd1 -ne $pwd2 -or -not $pwd1) {
        Write-Err "Паролі не співпадають або порожні"
        Pause-Return; return
    }

    $key = Get-AesKey $pwd1

    $basePath = Join-Path $ScriptDir ".env.base"
    @"
TG_BOT_TOKEN=$(Protect-WithPassword $token   $key)
TG_GROUP_ID=$(Protect-WithPassword  $groupId $key)
OPENAI_API_KEY=$(Protect-WithPassword $apiKey $key)
OPENAI_MODEL=$model
"@ | Set-Content $basePath -Encoding UTF8

    Write-Ok "Файл збережено: $basePath"
    Write-Host ""
    Write-Host "  Скопіюйте .env.base на новий сервер поруч з manage.ps1" -ForegroundColor Yellow
    Write-Host "  При першому запуску буде запитано пароль для розшифрування" -ForegroundColor Yellow
    Pause-Return
}

function Import-BaseConfig {
    param([string]$Path = "")
    if (-not $Path) { $Path = Join-Path $ScriptDir ".env.base" }
    if (-not (Test-Path $Path)) { return $false }

    $lines = Get-Content $Path -Encoding UTF8 -ErrorAction SilentlyContinue
    $base  = @{}
    foreach ($line in $lines) {
        if ($line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.+)$") {
            $base[$matches[1]] = $matches[2]
        }
    }
    if (-not $base["TG_BOT_TOKEN"]) { return $false }

    Write-Host ""
    Write-Ok "Знайдено .env.base — введіть пароль для розшифрування"
    $pwd = Read-Secret "Пароль"
    $key = Get-AesKey $pwd

    $token   = Unprotect-WithPassword $base["TG_BOT_TOKEN"] $key
    $groupId = Unprotect-WithPassword $base["TG_GROUP_ID"]  $key

    if (-not $token -or -not $groupId) {
        Write-Err "Невірний пароль або файл пошкоджений"
        return $false
    }

    Set-EnvValue "TG_BOT_TOKEN" (Protect-Value $token)
    Set-EnvValue "TG_GROUP_ID"  (Protect-Value $groupId)

    if ($base["OPENAI_API_KEY"]) {
        $apiKey = Unprotect-WithPassword $base["OPENAI_API_KEY"] $key
        if ($apiKey) { Set-EnvValue "OPENAI_API_KEY" (Protect-Value $apiKey) }
    }
    $model = if ($base["OPENAI_MODEL"]) { $base["OPENAI_MODEL"] } else { "gpt-4o-mini" }
    Set-EnvValue "OPENAI_MODEL" $model

    Write-Ok "Токени імпортовано та зашифровано DPAPI"
    return $true
}

# ─── 3. Telegram credentials ─────────────────────────────────

function Setup-Telegram {
    Show-Header "3. Переналаштування Telegram"
    Setup-Telegram-Credentials
    Write-Info "Перезапустіть бота (пункт 7) для застосування змін"
    Pause-Return
}

function Setup-Telegram-Credentials {
    Write-Host "  Підготовка (якщо ще не зроблено):" -ForegroundColor DarkGray
    Write-Host "    1. @BotFather → /newbot → скопіюйте токен" -ForegroundColor DarkGray
    Write-Host "    2. Додайте бота до групи як адміністратора" -ForegroundColor DarkGray
    Write-Host "    3. У групі: Редагувати → Гілки → увімкнути" -ForegroundColor DarkGray
    Write-Host ""

    $token = Read-Secret "TG_BOT_TOKEN"
    if (-not $token) { Write-Err "Токен обов'язковий"; return }
    Set-EnvValue "TG_BOT_TOKEN" (Protect-Value $token)

    # Авто-визначення Group ID
    Write-Host ""
    Write-Info "Надішліть будь-яке повідомлення у вашу Telegram групу, потім натисніть Enter..."
    Read-Host "  [Enter]" | Out-Null

    $groupId = Get-TelegramGroupId $token
    if ($groupId) {
        Write-Ok "Групу знайдено автоматично: $groupId"
        Set-EnvValue "TG_GROUP_ID" (Protect-Value $groupId)
    } else {
        Write-Info "Не вдалося визначити автоматично."
        Write-Host "  Відкрийте у браузері та скопіюйте id:" -ForegroundColor Yellow
        Write-Host "  https://api.telegram.org/bot<TOKEN>/getUpdates" -ForegroundColor Yellow
        Write-Host ""
        $groupId = Read-Secret "TG_GROUP_ID"
        Set-EnvValue "TG_GROUP_ID" (Protect-Value $groupId)
    }

    Write-Ok "Telegram збережено (зашифровано)"
}

# ─── 4. OpenAI ───────────────────────────────────────────────

function Setup-OpenAI {
    Show-Header "4. Переналаштування OpenAI"
    Setup-OpenAI-Credentials
    Pause-Return
}

function Setup-OpenAI-Credentials {
    Write-Host "  OPENAI_API_KEY буде зашифрований. Enter = пропустити." -ForegroundColor DarkGray
    Write-Host ""
    $apiKey = Read-Secret "OPENAI_API_KEY (Enter = пропустити)"
    if (-not $apiKey) {
        Write-Info "OpenAI пропущено — буде використана fallback-логіка алертів"
        return
    }

    $env   = Read-Env
    $model = Read-Host "  OPENAI_MODEL [$( if ($env['OPENAI_MODEL']) { $env['OPENAI_MODEL'] } else { 'gpt-4o-mini' })]"
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
        @{K="ALERT_COOLDOWN_MIN";   L="Кулдаун (хв)"   },
        @{K="GH_TOKEN";             L="GitHub PAT"      }
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

    foreach ($name in @("1C_Monitor", "1C_Monitor_Watchdog")) {
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

    Write-Step "Зупинка процесу моніторингу..."
    Get-WmiObject Win32_Process |
        Where-Object { $_.CommandLine -match "main\.py" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Ok "Процеси зупинені"

    foreach ($name in @("1C_Monitor", "1C_Monitor_Watchdog")) {
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
    Get-WmiObject Win32_Process |
        Where-Object { $_.CommandLine -match "main\.py" } |
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

# ─── 9. Test Telegram ────────────────────────────────────────

function Test-Telegram {
    Show-Header "9. Тест Telegram"

    $env     = Read-Env
    $token   = Unprotect-Value $env["TG_BOT_TOKEN"]
    $groupId = Unprotect-Value $env["TG_GROUP_ID"]
    $topicId = $env["TG_TOPIC_ID"]
    $company = if ($env["COMPANY_NAME"]) { $env["COMPANY_NAME"] } else { $env:COMPUTERNAME }
    $serverId = if ($env["SERVER_ID"])   { $env["SERVER_ID"]    } else { "?" }

    if (-not $token -or -not $groupId) {
        Write-Err "Telegram не налаштований. Виконайте спочатку пункт 3."
        Pause-Return; return
    }

    Write-Step "Надсилаю тестове повідомлення..."

    $body = @{
        chat_id    = $groupId
        text       = "✅ <b>Тест підключення</b>`n`nСервер: <b>$company</b> ($serverId)`nЧас: $(Get-Date -Format 'HH:mm dd.MM.yyyy')`n`n🔧 manage.ps1 → Тест Telegram"
        parse_mode = "HTML"
    }
    if ($topicId) { $body["message_thread_id"] = [int]$topicId }

    try {
        $resp = Invoke-RestMethod `
            -Uri "https://api.telegram.org/bot$token/sendMessage" `
            -Method Post `
            -Body ($body | ConvertTo-Json -Compress) `
            -ContentType "application/json; charset=utf-8" `
            -ErrorAction Stop
        if ($resp.ok) {
            Write-Ok "Повідомлення надіслано успішно!"
            Write-Info "Перевірте групу в Telegram"
        } else {
            Write-Err "Telegram API: $($resp.description)"
        }
    } catch {
        Write-Err "Помилка запиту: $_"
    }

    Pause-Return
}

# ─── U. Update from GitHub ───────────────────────────────────

function Update-FromGitHub {
    Show-Header "U. Оновлення з GitHub"

    $REPO_RAW = "https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main"

    $files = @(
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

    # ── Перевірка доступу ────────────────────────────────────
    Write-Step "Перевірка підключення до GitHub..."
    try {
        Invoke-WebRequest -Uri "$REPO_RAW/main.py" -Method Head `
            -UseBasicParsing -ErrorAction Stop | Out-Null
        Write-Ok "GitHub доступний"
    } catch {
        Write-Err "Не вдалося підключитися: $_"
        Pause-Return; return
    }

    # ── Резервна копія перед оновленням ──────────────────────
    $backupDir = Join-Path $ScriptDir ".backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    Write-Step "Резервна копія → $backupDir"
    try {
        New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
        Get-ChildItem $ScriptDir -Filter "*.py"  -File | Copy-Item -Destination $backupDir
        Get-ChildItem $ScriptDir -Filter "*.ps1" -File | Copy-Item -Destination $backupDir
        if (Test-Path "$ScriptDir\requirements.txt") {
            Copy-Item "$ScriptDir\requirements.txt" $backupDir
        }
        Write-Ok "Резервну копію створено"
    } catch {
        Write-Err "Помилка резервної копії: $_"
    }

    # ── Завантаження файлів ──────────────────────────────────
    Write-Host ""
    $updated = 0; $skipped = 0
    $ProgressPreference = "SilentlyContinue"

    foreach ($file in $files) {
        $url  = "$REPO_RAW/$($file -replace '\\','/')"
        $dest = Join-Path $ScriptDir $file

        $destDir = Split-Path $dest -Parent
        if (-not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }

        try {
            Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing -ErrorAction Stop
            # Add UTF-8 BOM to .ps1 files so PowerShell 5.x reads them as UTF-8
            if ($file.EndsWith('.ps1')) {
                $bytes = [System.IO.File]::ReadAllBytes($dest)
                $bom   = [byte[]](0xEF, 0xBB, 0xBF)
                if ($bytes.Length -lt 3 -or $bytes[0] -ne 0xEF -or $bytes[1] -ne 0xBB -or $bytes[2] -ne 0xBF) {
                    [System.IO.File]::WriteAllBytes($dest, $bom + $bytes)
                }
            }
            Write-Ok $file
            $updated++
        } catch {
            Write-Info "Пропущено: $file"
            $skipped++
        }
    }

    $ProgressPreference = "Continue"

    Write-Host ""
    Write-Ok "Оновлено: $updated  Пропущено: $skipped"

    # ── pip-залежності ───────────────────────────────────────
    Write-Host ""
    $ans = Read-Host "  Оновити pip-залежності? (Y/n)"
    if ($ans -ne "n" -and $ans -ne "N") {
        Write-Step "pip install -r requirements.txt..."
        python -m pip install -r $ReqFile --quiet 2>&1 | Out-Null
        Write-Ok "Залежності оновлені"
    }

    # ── Перезапуск ───────────────────────────────────────────
    Write-Host ""
    $ans = Read-Host "  Перезапустити моніторинг? (Y/n)"
    if ($ans -ne "n" -and $ans -ne "N") {
        Restart-Monitor
    } else {
        Pause-Return
    }
}

# ─── Main menu ───────────────────────────────────────────────

while ($true) {
    Show-Header "1С Monitor — Управління"
    Write-Host "  0. Підготовка сервера  (Python, ExecutionPolicy)" -ForegroundColor Yellow
    Write-Host "  ───────────────────────────────────────────────"  -ForegroundColor DarkGray
    Write-Host "  1. Встановити / Оновити"                          -ForegroundColor White
    Write-Host "  2. Налаштувати сервер  (назва / топік / диски)"    -ForegroundColor White
    Write-Host "  3. Переналаштувати Telegram  (токен / група)"     -ForegroundColor White
    Write-Host "  4. Переналаштувати OpenAI"                        -ForegroundColor White
    Write-Host "  5. Переглянути налаштування"                      -ForegroundColor White
    Write-Host "  6. Статус завдань"                                 -ForegroundColor White
    Write-Host "  7. Перезапустити моніторинг"                      -ForegroundColor White
    Write-Host "  8. Видалити"                                       -ForegroundColor Red
    Write-Host "  ───────────────────────────────────────────────"  -ForegroundColor DarkGray
    Write-Host "  9. Тест Telegram  (перевірка надсилання)"         -ForegroundColor Cyan
    Write-Host "  U. Оновлення з GitHub"                            -ForegroundColor Cyan
    Write-Host "  B. Базовий конфіг  (Export токенів для нових серверів)" -ForegroundColor Cyan
    Write-Host "  Q. Вийти"                                         -ForegroundColor DarkGray
    Write-Host ""

    $choice = Read-Host "  Ваш вибір"
    switch ($choice.ToUpper()) {
        "0" { Prepare-Server      }
        "1" { Install-Monitor     }
        "2" { Setup-Company       }
        "3" { Setup-Telegram      }
        "4" { Setup-OpenAI        }
        "5" { Show-Config         }
        "6" { Show-Status         }
        "7" { Restart-Monitor     }
        "8" { Uninstall-Monitor   }
        "9" { Test-Telegram       }
        "U" { Update-FromGitHub   }
        "B" { Export-BaseConfig   }
        "Q" { exit                }
        default {
            Write-Host "  Невірний вибір." -ForegroundColor Red
            Start-Sleep 1
        }
    }
}
