# 🖥️ Windows Server Monitor для 1С

> Система моніторингу Windows серверів з AI аналізом (GPT-4o-mini) та Telegram сповіщеннями.  
> Розроблено для адміністрування серверів 1С підприємства.

---

## 📋 Технічне завдання

### Контекст
Клієнт адмініструє **5 компаній**, кожна має власний Windows сервер під 1С.  
Потреба: централізований моніторинг без важкого стеку (Terraform + Grafana), мінімальні витрати на інфраструктуру.

### Вимоги

#### Моніторинг
- Вільне місце на дисках з динамікою зміни за годину/добу
- Навантаження CPU та RAM з топ процесами
- Статус сервісів 1С Agent та MSSQL
- Перевірка бекапів — свіжість та розмір файлів
- Windows Event Log — перебір паролів (Event ID 4625)
- RDP сесії — хто підключений, нові IP адреси
- Нові адміністратори системи (Event ID 4732)
- Зміни критичних файлів (hosts та ін.) через SHA-256 хеші

#### Сповіщення
- Telegram група з **темами (Forum Topics)** — кожна компанія має свою тему
- Теги в повідомленнях для швидкої класифікації (`#critical`, `#rdp`, `#backup` тощо)
- Дедуплікація алертів — не спамити одне і те ж (кулдаун 30 хв)
- AI аналіз контексту — GPT-4o-mini приймає рішення чи слати алерт

#### Інтерактивність (Telegram бот)
- Перегляд активних RDP сесій
- Примусове завершення сесії користувача
- Перезавантаження сервера з підтвердженням
- Перезапуск сервісів
- Графіки навантаження (CPU/RAM/Диск) за 1г/24г
- Статус бекапів

#### Архітектура
- Агент на кожному сервері зі своїм `.env` файлом
- Task Scheduler замість Windows Service (простіше в обслуговуванні)
- SQLite для зберігання стану та метрик (без зовнішніх БД)
- Fallback логіка якщо GPT недоступний

---

## ✅ Що реалізовано

### Збір метрик (`collectors/`)

| Модуль | Що збирає |
|---|---|
| `disk.py` | Вільне місце, динаміка за 1г та 24г, розмір томів |
| `memory.py` | CPU % (avg 5 сек), RAM %, swap, топ-5 процесів |
| `security.py` | Event 4625 (brute force), Event 4732 (нові адміни), SHA-256 хеші файлів |
| `rdp.py` | Активні сесії через `qwinsta`, нові IP через Event 4624 LogonType=10 |
| `backup.py` | Пошук файлів бекапів (bak/zip/7z/dt/1cd), вік та розмір |
| `services.py` | Статус сервісів через psutil + `sc query`, фіксація змін |

### AI Аналіз (`analyzer.py`)
- Передає повний контекст метрик у GPT-4o-mini
- Враховує час доби, динаміку змін, комбінацію проблем
- Повертає структурований JSON: `should_alert`, `severity`, `tags`, `analysis`, `recommendation`
- Fallback на правила якщо GPT недоступний

### Telegram (`notifier.py`, `bot.py`)

**Формат алерту:**
```
🔴 [Компанія А] 23:47
#critical #rdp #new_ip

Підключення з невідомого IP
📋 Аналіз: ...
⚡ Рекомендація: ...
📊 Метрики: ...

[📊 Статус] [👥 Сесії]
[🔒 Завершити сесію]
```

**Інтерактивні кнопки:**
- `📊 Статус` — поточний стан сервера
- `👥 Сесії` — список з кнопкою "Вибити" для кожного
- `❌ Вибити всіх` — масове завершення сесій
- `💾 Диски` — деталі з progress bar
- `📊 Графік 1г/24г` — PNG через matplotlib (темна тема)
- `📦 Бекапи` — останні файли та статус
- `🔄 Перезавантажити` — з підтвердженням та попередженням про активні сесії

### Дії (`actions.py`)
- `kick_session(id)` — завершення через `logoff`
- `kick_all_sessions()` — масове завершення
- `restart_service(name)` — `net stop` + `net start`
- `reboot_server(delay)` — `shutdown /r /t 30`
- `get_disk_details()` — з emoji progress bar

### Графіки (`charts.py`)
- Темна тема (Catppuccin Mocha палітра)
- CPU та RAM на одному графіку
- Окремий графік для диску
- Часові мітки, пороги як пунктирні лінії
- Збереження в temp PNG → відправка в TG → видалення

### База даних (`storage.py`)
- `alerts` — дедуплікація за ключем + кулдаун
- `metrics` — часові ряди для графіків (очищення після 30 днів)
- `known_ips` — whitelist RDP IP адрес
- `file_hashes` — SHA-256 для виявлення змін
- `service_states` — попередній стан для фіксації змін
- `known_admins` — список адмінів при першому запуску

### Автоматичне встановлення (`setup.bat`)
- Перевірка прав адміністратора та наявності Python
- `pip install` залежностей
- Створення Task Scheduler завдань:
  - `1C_Monitor` — кожну хвилину, від SYSTEM
  - `1C_Monitor_Bot` — при старті системи
- Запуск бота одразу після встановлення

---

## 🏗️ Архітектура

```
Task Scheduler (кожну хвилину)
        ↓
   monitor.py
        ↓
  collectors/*  ←─── збір метрик
        ↓
  analyzer.py   ←─── GPT-4o-mini аналіз
        ↓
  storage.py    ←─── перевірка кулдауну
        ↓
  notifier.py   ←─── Telegram алерт

Task Scheduler (при старті)
        ↓
    bot.py      ←─── long polling
        ↓
  actions.py    ←─── виконання команд
  charts.py     ←─── генерація графіків
```

---

## 📁 Структура проекту

```
monitor/
├── .env.example            ← шаблон конфігурації
├── monitor.py              ← головний файл (Task Scheduler entry point)
├── bot.py                  ← Telegram бот (long polling)
├── analyzer.py             ← GPT-4o-mini аналіз та прийняття рішень
├── notifier.py             ← відправка повідомлень та фото в Telegram
├── actions.py              ← дії: kick session, reboot, restart service
├── charts.py               ← генерація PNG графіків (matplotlib)
├── storage.py              ← SQLite: метрики, алерти, стан
├── requirements.txt        ← Python залежності
├── setup.bat               ← автоматичне встановлення
├── README.md               ← документація
└── collectors/
    ├── __init__.py
    ├── disk.py             ← диски
    ├── memory.py           ← CPU / RAM
    ├── security.py         ← Event Log, файли, адміни
    ├── rdp.py              ← RDP сесії та логіни
    ├── backup.py           ← перевірка бекапів
    └── services.py         ← Windows сервіси
```

---

## ⚙️ Конфігурація (.env)

```env
# Ідентифікація сервера
SERVER_ID=company_a
COMPANY_NAME=Компанія А

# Telegram
TG_BOT_TOKEN=7123456789:AAF...
TG_GROUP_ID=-1001234567890
TG_TOPIC_ID=12345

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Диски
DISK_PATHS=C:\,D:\
DISK_WARNING_PERCENT=20
DISK_CRITICAL_PERCENT=10

# CPU / RAM
CPU_WARNING_PERCENT=85
RAM_WARNING_PERCENT=90

# Безпека
BRUTE_FORCE_WINDOW_MIN=5
BRUTE_FORCE_THRESHOLD=5
KNOWN_IPS=192.168.1.0/24,10.0.0.0/8

# Бекапи
BACKUP_PATH=D:\Backups
BACKUP_MAX_AGE_HOURS=25
BACKUP_MIN_SIZE_MB=50

# Сервіси
MONITOR_SERVICES=1C:Enterprise 8.3 Server Agent,MSSQLSERVER

# Критичні файли
WATCH_FILES=C:\Windows\System32\drivers\etc\hosts

# Загальні
CHECK_INTERVAL_SEC=60
ALERT_COOLDOWN_MIN=30
```

---

## 🏷️ Система тегів

| Тег | Коли використовується |
|---|---|
| `#critical` | Потребує негайної реакції |
| `#warning` | Варто перевірити |
| `#info` | Інформаційне |
| `#disk` | Проблеми з місцем на диску |
| `#cpu` `#ram` | Навантаження |
| `#rdp` | RDP події |
| `#new_ip` | Підключення з нового IP |
| `#brute_force` | Перебір паролів |
| `#security` | Безпека загально |
| `#admin` | Зміни в адміністраторах |
| `#files` | Зміни критичних файлів |
| `#backup` | Проблеми з бекапами |
| `#service` | Сервіси 1С / MSSQL |

---

## 💰 Витрати

| Компонент | Вартість |
|---|---|
| GPT-4o-mini | ~$0.0006 / 1K токенів |
| 1 перевірка | ~500 токенів |
| 5 серверів × 1440/день | ~$3-5 / місяць |
| Telegram Bot API | Безкоштовно |
| SQLite | Безкоштовно |

---

## 🚀 Встановлення

### Вимоги
- Windows Server 2016+
- Python 3.10+
- Права адміністратора

### Кроки
1. Скопіювати папку `monitor` на сервер
2. Заповнити `.env` (скопіювати з `.env.example`)
3. Запустити від імені Адміністратора:
```
setup.bat
```

### Для кількох серверів
Окрема копія папки на кожному сервері зі своїм `.env`:
```
server1/monitor/.env  →  SERVER_ID=company_a, TG_TOPIC_ID=11111
server2/monitor/.env  →  SERVER_ID=company_b, TG_TOPIC_ID=22222
```

---

## 🛠️ Технічний стек

| Компонент | Технологія |
|---|---|
| Мова | Python 3.10+ |
| Метрики системи | psutil |
| Event Log | pywin32 |
| AI аналіз | OpenAI GPT-4o-mini |
| Telegram | Bot API (requests) |
| Графіки | matplotlib |
| База даних | SQLite (вбудована) |
| Планувальник | Windows Task Scheduler |

---

## 📝 Ліцензія

MIT — вільне використання та модифікація.
