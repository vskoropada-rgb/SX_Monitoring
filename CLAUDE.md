# SX_Monitoring — контекст для нової сесії Claude

## Що це

Система моніторингу Windows-серверів для 1С з AI-аналізом (GPT-4o-mini) і Telegram-сповіщеннями.
Репозиторій: **https://github.com/vskoropada-rgb/SX_Monitoring**

Клієнт адмініструє 5 компаній, кожна зі своїм Windows-сервером під 1С.
На кожному сервері — окремий агент з власним `.env`. Усі алерти йдуть у єдину Telegram-групу з темами (Forum Topics), по темі на компанію.

## Стек

| Шар | Технологія |
|---|---|
| Мова | Python 3.10+ |
| Системні метрики | psutil, pywin32 |
| AI-аналіз | OpenAI GPT-4o-mini |
| Telegram | Bot API (long polling, inline keyboard) |
| Графіки | matplotlib (темна тема) |
| БД | SQLite (вбудована) |
| Планувальник | Windows Task Scheduler |
| Шифрування секретів | Windows DPAPI |
| Встановлення | PowerShell (`install.ps1` + `manage.ps1`) |

## Архітектура

```
Task Scheduler → main.py
                    ├── monitor-loop (threading, кожні N сек)
                    │       └── collectors/* → analyzer.py → storage.py → notifier.py
                    └── bot.run() (long polling)
                              └── actions.py  charts.py
```

`main.py` — єдина точка входу: запускає монітор у фоновому потоці та бот у головному.

## Структура файлів

```
SX_Monitoring/
├── main.py              # точка входу (monitor loop + bot)
├── monitor.py           # цикл збору метрик
├── bot.py               # Telegram long-polling бот
├── analyzer.py          # GPT-4o-mini аналіз → JSON рішення
├── notifier.py          # відправка в Telegram (текст + фото)
├── actions.py           # kick session, reboot, restart service
├── charts.py            # PNG графіки CPU/RAM/диск (matplotlib)
├── storage.py           # SQLite: метрики, алерти, хеші, стани
├── config.py            # завантаження .env
├── watchdog.ps1         # перезапуск main.py якщо впав
├── manage.ps1           # PowerShell меню: встановлення, запуск, тест
├── install.ps1          # bootstrap: завантажує файли з GitHub + запускає manage.ps1
├── requirements.txt
├── .env.example
└── collectors/
    ├── disk.py          # вільне місце + динаміка за 1г/24г
    ├── memory.py        # CPU %, RAM %, топ-5 процесів
    ├── security.py      # Event 4625 (brute force), 4732 (нові адміни), SHA-256 файлів
    ├── rdp.py           # qwinsta + Event 4624 LogonType=10
    ├── backup.py        # bak/zip/7z/dt/1cd — вік та розмір
    ├── services.py      # psutil + sc query, фіксація змін
    ├── winupdate.py     # Windows Update статус
    ├── usb.py           # нові USB пристрої
    ├── software.py      # встановлене ПЗ
    └── schtasks.py      # заплановані задачі
```

## Конфігурація (.env)

```env
SERVER_ID=company_a
COMPANY_NAME=Компанія А

TG_BOT_TOKEN=7123456789:AAF...
TG_GROUP_ID=-1001234567890
TG_TOPIC_ID=12345

OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

DISK_PATHS=C:\,D:\
DISK_WARNING_PERCENT=20
DISK_CRITICAL_PERCENT=10

CPU_WARNING_PERCENT=85
RAM_WARNING_PERCENT=90

BRUTE_FORCE_WINDOW_MIN=5
BRUTE_FORCE_THRESHOLD=5
KNOWN_IPS=192.168.1.0/24,10.0.0.0/8

BACKUP_PATH=D:\Backups
BACKUP_MAX_AGE_HOURS=25
BACKUP_MIN_SIZE_MB=50

MONITOR_SERVICES=1C:Enterprise 8.3 Server Agent,MSSQLSERVER
WATCH_FILES=C:\Windows\System32\drivers\etc\hosts

CHECK_INTERVAL_SEC=60
ALERT_COOLDOWN_MIN=30
```

## Встановлення на сервер

```powershell
# PowerShell від Адміністратора:
irm "https://raw.githubusercontent.com/vskoropada-rgb/SX_Monitoring/main/install.ps1" | iex
```

`install.ps1` завантажує всі файли з GitHub, потім запускає `manage.ps1`.
`manage.ps1` — інтерактивне меню: підготовка сервера, налаштування `.env`, встановлення Task Scheduler задач, тест Telegram, оновлення з GitHub.

## Що вже реалізовано

- Всі collectors (disk, memory, security, rdp, backup, services, winupdate, usb, software, schtasks)
- AI-аналіз з fallback на правила якщо GPT недоступний
- Telegram алерти з inline-кнопками (статус, сесії, диски, графіки, бекапи, перезавантаження)
- Дедуплікація алертів (кулдаун 30 хв за ключем)
- Графіки CPU/RAM/диск у PNG (темна тема, пороги пунктиром)
- Kick session / kick all / restart service / reboot з підтвердженням
- SQLite для метрик, алертів, хешів файлів, станів сервісів, відомих IP/адмінів
- RotatingFileHandler (5 MB × 3 = макс 15 MB)
- DPAPI шифрування секретів у `.env`
- Watchdog (PowerShell) для автоперезапуску
- Bootstrap-встановлення одним рядком

## Можливі наступні кроки

- Додати `collectors/schtasks.py`, `collectors/winupdate.py`, `collectors/usb.py`, `collectors/software.py` — файли є, але логіка може потребувати доопрацювання
- Додати Telegram-команду `/setup` для початкового налаштування прямо з бота
- Щоденний звіт у Telegram (summary по всіх серверах)
- Підтримка кількох серверів в одному агенті (multi-tenant)
- Web-дашборд (FastAPI + htmx) замість або на додачу до бота
- Юніт-тести для collectors та analyzer

## Примітки

- Проект виріс з підпапки `Monitoring/` репо `vskoropada-rgb/linux-scripts` і перенесений у `vskoropada-rgb/SX_Monitoring` як окремий проект
- `install.ps1` тепер завантажує файли з `SX_Monitoring` (не з `linux-scripts`)
- Для нових серверів: окрема копія файлів + свій `.env` з унікальним `SERVER_ID` та `TG_TOPIC_ID`
