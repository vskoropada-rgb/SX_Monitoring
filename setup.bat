@echo off
chcp 65001 > nul
echo ============================================
echo  Встановлення монітора Windows для 1С
echo ============================================
echo.

:: Перевірка прав адміністратора
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ПОМИЛКА] Запустіть setup.bat від імені Адміністратора!
    pause
    exit /b 1
)

:: Перевірка Python
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [ПОМИЛКА] Python не знайдений. Встановіть Python 3.10+ з python.org
    pause
    exit /b 1
)

echo [OK] Python знайдений
python --version

:: Отримуємо шлях до скрипта
set SCRIPT_DIR=%~dp0
set SCRIPT_DIR=%SCRIPT_DIR:~0,-1%

echo.
echo [1/4] Встановлення залежностей...
pip install -r "%SCRIPT_DIR%\requirements.txt" --quiet
if %errorLevel% neq 0 (
    echo [ПОМИЛКА] Не вдалося встановити залежності
    pause
    exit /b 1
)
echo [OK] Залежності встановлені

:: Перевірка .env
if not exist "%SCRIPT_DIR%\.env" (
    echo.
    echo [!] Файл .env не знайдений
    echo     Копіюю .env.example в .env...
    copy "%SCRIPT_DIR%\.env.example" "%SCRIPT_DIR%\.env"
    echo.
    echo [ВАЖЛИВО] Заповніть файл .env перед продовженням!
    echo           Відкрити зараз? (Y/N)
    set /p OPEN_ENV=
    if /i "%OPEN_ENV%"=="Y" notepad "%SCRIPT_DIR%\.env"
    echo.
    echo Після заповнення .env запустіть setup.bat знову
    pause
    exit /b 0
)
echo [OK] Файл .env знайдений

echo.
echo [2/4] Створення завдання моніторингу (кожну хвилину)...

:: Видаляємо старі завдання якщо є
schtasks /delete /tn "1C_Monitor" /f >nul 2>&1
schtasks /delete /tn "1C_Monitor_Bot" /f >nul 2>&1

:: Завдання моніторингу — кожну хвилину
schtasks /create ^
    /tn "1C_Monitor" ^
    /tr "python \"%SCRIPT_DIR%\monitor.py\"" ^
    /sc MINUTE /mo 1 ^
    /ru SYSTEM ^
    /rl HIGHEST ^
    /f ^
    /st 00:00 >nul

if %errorLevel% neq 0 (
    echo [ПОМИЛКА] Не вдалося створити завдання моніторингу
) else (
    echo [OK] Завдання 1C_Monitor створене (кожну хвилину)
)

echo.
echo [3/4] Створення завдання Telegram бота...

:: Бот — запускається при старті системи, якщо не запущений
schtasks /create ^
    /tn "1C_Monitor_Bot" ^
    /tr "python \"%SCRIPT_DIR%\bot.py\"" ^
    /sc ONSTART ^
    /ru SYSTEM ^
    /rl HIGHEST ^
    /f ^
    /delay 0001:00 >nul

if %errorLevel% neq 0 (
    echo [ПОМИЛКА] Не вдалося створити завдання бота
) else (
    echo [OK] Завдання 1C_Monitor_Bot створене (при старті системи)
)

echo.
echo [4/4] Запуск бота...
start /b python "%SCRIPT_DIR%\bot.py"
timeout /t 2 /nobreak >nul
echo [OK] Бот запущений

echo.
echo ============================================
echo  Встановлення завершено!
echo ============================================
echo.
echo  Завдання Task Scheduler:
echo  - 1C_Monitor      : кожну хвилину
echo  - 1C_Monitor_Bot  : при старті системи
echo.
echo  Логи:
echo  - %SCRIPT_DIR%\monitor.log
echo  - %SCRIPT_DIR%\bot.log
echo.
echo  Для перевірки запустіть:
echo  python "%SCRIPT_DIR%\monitor.py"
echo.
pause
