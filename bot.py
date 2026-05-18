"""
bot.py — Telegram бот з інтерактивними кнопками
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config as _config_module

_cfg = _config_module.load()

import requests
import json
import time
from datetime import datetime

import storage
import actions
import notifier
import charts

logger = logging.getLogger("bot")

BOT_TOKEN  = _cfg["TG_BOT_TOKEN"]
GROUP_ID   = _cfg["TG_GROUP_ID"]
TOPIC_ID   = _cfg["TG_TOPIC_ID"]
SERVER_ID  = _cfg["SERVER_ID"]
COMPANY    = _cfg["COMPANY_NAME"]
DISK_PATHS = [p.strip() for p in _cfg.get("DISK_PATHS", "C:\\").split(",")]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Стан: очікування підтвердження
pending_confirmations = {}

# Тред де була написана команда — відповідаємо туди ж, не в TOPIC_ID
_reply_thread_id: int | None = None


def api_call(method: str, data: dict) -> dict:
    try:
        r = requests.post(f"{API}/{method}", json=data, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"API помилка {method}: {e}")
        return {}


def answer_callback(callback_id: str, text: str = ""):
    api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def send(text: str, keyboard=None, message_id: int = None):
    """Відправляє або редагує повідомлення"""
    payload = {
        "chat_id": GROUP_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    thread = _reply_thread_id if _reply_thread_id is not None else (int(TOPIC_ID) if TOPIC_ID else None)
    if thread:
        payload["message_thread_id"] = thread
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})

    if message_id:
        payload["message_id"] = message_id
        return api_call("editMessageText", payload)
    return api_call("sendMessage", payload)


def send_photo(image_path: str, caption: str):
    config = {"TG_BOT_TOKEN": BOT_TOKEN, "TG_GROUP_ID": GROUP_ID, "TG_TOPIC_ID": TOPIC_ID}
    notifier.send_photo(image_path, caption, config)


# ─── Обробники команд ────────────────────────────────────────

def handle_status(message_id=None):
    m = storage.load_metrics_cache()

    maint_until = storage.get_maintenance_until(SERVER_ID)
    maint_badge = f"\n🔧 <b>Обслуговування до {maint_until.strftime('%H:%M')}</b>" if maint_until else ""

    lines = [
        f"📡 <b>Статус — {COMPANY}</b>{maint_badge}",
        f"🕐 {datetime.now().strftime('%H:%M %d.%m.%Y')}",
        "",
    ]

    for d in m.get("disks", []):
        if "free_pct" in d:
            icon = "🔴" if d["free_pct"] < 10 else "⚠️" if d["free_pct"] < 20 else "✅"
            lines.append(f"{icon} {d['path']}: {d['free_pct']}% вільно ({d['free_gb']}GB)")

    cpu = m.get("cpu", {})
    ram = m.get("ram", {})
    lines.append(f"{'🔴' if cpu.get('percent',0)>85 else '✅'} CPU: {cpu.get('percent','?')}%")
    lines.append(f"{'🔴' if ram.get('percent',0)>90 else '✅'} RAM: {ram.get('percent','?')}% (вільно {ram.get('free_gb','?')}GB)")

    lines.append("")
    for svc in m.get("services", []):
        icon = "✅" if svc["is_running"] else "❌"
        lines.append(f"{icon} {svc['name']}")

    maint_btn = (
        {"text": "✅ Зняти обслуговування", "callback_data": f"maint_off_{SERVER_ID}"}
        if maint_until else
        {"text": "🔧 Обслуговування 2г",   "callback_data": f"maint_on_{SERVER_ID}"}
    )

    keyboard = [
        [
            {"text": "👥 Сесії",      "callback_data": f"sessions_{SERVER_ID}"},
            {"text": "💾 Диски",      "callback_data": f"disk_{SERVER_ID}"},
        ],
        [
            {"text": "📊 Графік 1г",  "callback_data": f"chart_1_{SERVER_ID}"},
            {"text": "📊 Графік 24г", "callback_data": f"chart_24_{SERVER_ID}"},
        ],
        [
            {"text": "📦 Бекапи",      "callback_data": f"backups_{SERVER_ID}"},
            {"text": "📈 Тренд бекапів","callback_data": f"chart_backup_{SERVER_ID}"},
        ],
        [
            {"text": "🔄 Перезавантажити", "callback_data": f"reboot_confirm_{SERVER_ID}"},
            maint_btn,
        ],
        [
            {"text": "🔒 Заблоковані IP", "callback_data": f"blocked_list_{SERVER_ID}"},
        ],
    ]

    send("\n".join(lines), keyboard, message_id)


def handle_sessions(message_id=None):
    sessions = actions.get_sessions()

    if not sessions:
        text = f"👥 <b>Сесії — {COMPANY}</b>\n\nАктивних сесій немає"
        keyboard = [[{"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"}]]
        send(text, keyboard, message_id)
        return

    lines = [f"👥 <b>Активні сесії — {COMPANY}</b>", ""]
    keyboard = []

    for s in sessions:
        state_icon = "🟢" if s.get("state") in ("Active", "Activ") else "🟡"
        user = s.get("username") or s.get("session_name", "?")
        sid  = s.get("session_id", "")
        lines.append(f"{state_icon} <b>{user}</b>  ({s.get('state', '')})")
        keyboard.append([{
            "text": f"🚪 Завершити {user}",
            "callback_data": f"kick_{sid}_{SERVER_ID}",
        }])

    keyboard.append([
        {"text": "🚪 Завершити всіх", "callback_data": f"kick_all_{SERVER_ID}"},
        {"text": "🔙 Назад",          "callback_data": f"status_{SERVER_ID}"},
    ])

    send("\n".join(lines), keyboard, message_id)


def handle_disk(message_id=None):
    text = actions.get_disk_details(DISK_PATHS)
    keyboard = [
        [
            {"text": "📊 Графік диску", "callback_data": f"chart_disk_{SERVER_ID}"},
            {"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"},
        ]
    ]
    send(text, keyboard, message_id)


def handle_backups(message_id=None):
    from collectors import backup as backup_collector
    config = _config()
    data = backup_collector.collect(config)

    icon = "✅" if data.get("status") == "ok" else "⚠️" if data.get("status") == "warning" else "❌"
    lines = [
        f"📦 <b>Бекапи — {COMPANY}</b>",
        "",
        f"{icon} Статус: {data.get('status', 'невідомо')}",
        f"📄 Останній: {data.get('latest_file', 'н/д')}",
        f"⏰ Час: {data.get('latest_time', 'н/д')} ({data.get('latest_age_hours')}г тому)",
        f"📏 Розмір: {data.get('latest_size_mb')} MB",
    ]

    if data.get("issues"):
        lines.append("")
        lines.append("⚠️ <b>Проблеми:</b>")
        for issue in data["issues"]:
            lines.append(f"• {issue}")

    if data.get("recent_files"):
        lines.append("")
        lines.append("📋 <b>Останні файли:</b>")
        for f in data["recent_files"][:3]:
            lines.append(f"• {f['name']} — {f['size_mb']}MB ({f['age_hours']}г тому)")

    keyboard = [[{"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"}]]
    send("\n".join(lines), keyboard, message_id)


def handle_chart(hours: int, metric: str = "combined", message_id=None):
    loading_text = f"⏳ Генерую графік за {hours}г..."
    send(loading_text)

    if metric == "combined":
        path = charts.generate_combined_chart(SERVER_ID, hours=hours)
        caption = f"📊 <b>{COMPANY}</b> — CPU та RAM за {hours}г"
    elif metric == "disk":
        disk_metric = f"disk_{DISK_PATHS[0].replace(':', '').replace(chr(92), '')}_free_pct"
        path = charts.generate_chart(disk_metric, hours=hours, title=f"Диск {DISK_PATHS[0]} — вільно %")
        caption = f"💾 <b>{COMPANY}</b> — Диск {DISK_PATHS[0]} за {hours}г"
    else:
        path = charts.generate_chart(metric, hours=hours)
        caption = f"📊 <b>{COMPANY}</b> — {metric} за {hours}г"

    if path:
        send_photo(path, caption)
        import os
        try:
            os.unlink(path)
        except Exception:
            pass
    else:
        send("❌ Недостатньо даних для графіку. Зберіться більше метрик.")


def handle_reboot_confirm(message_id=None):
    sessions = actions.get_sessions()
    session_count = len(sessions)

    warn = f"\n⚠️ Є <b>{session_count}</b> активних сесій!" if session_count > 0 else ""

    text = (
        f"🔄 <b>Перезавантаження сервера</b>\n"
        f"Компанія: {COMPANY}{warn}\n\n"
        f"Сервер перезавантажиться через 30 секунд.\n"
        f"Підтвердіть дію:"
    )
    keyboard = [
        [
            {"text": "✅ Підтвердити", "callback_data": f"reboot_do_{SERVER_ID}"},
            {"text": "❌ Скасувати", "callback_data": f"status_{SERVER_ID}"},
        ]
    ]
    send(text, keyboard, message_id)


def handle_reboot(message_id=None):
    ok, msg = actions.reboot_server(delay_sec=30)
    icon = "✅" if ok else "❌"
    text = f"{icon} {msg}"
    keyboard = [[{"text": "🔙 Головне меню", "callback_data": f"status_{SERVER_ID}"}]]
    send(text, keyboard, message_id)


def handle_backup_chart(message_id=None):
    send("⏳ Генерую графік тренду бекапів...")
    path = charts.generate_backup_chart(days=30)
    if path:
        send_photo(path, f"📈 <b>{COMPANY}</b> — Розмір бекапів за 30 днів")
        try:
            import os
            os.unlink(path)
        except Exception:
            pass
    else:
        send("❌ Недостатньо даних для графіку (потрібно 2+ бекапи)")


def handle_kick(session_id: str, message_id=None):
    ok, msg = actions.kick_session(session_id)
    icon = "✅" if ok else "❌"
    text = f"{icon} {msg}"
    time.sleep(1)
    handle_sessions(message_id)


def handle_kick_all(message_id=None):
    ok, msg = actions.kick_all_sessions()
    text = f"{'✅' if ok else '❌'} <b>Результат:</b>\n{msg}"
    keyboard = [[{"text": "🔙 Назад", "callback_data": f"sessions_{SERVER_ID}"}]]
    send(text, keyboard, message_id)


def handle_blocked_list(message_id=None):
    blocked = actions.list_blocked_ips()
    if not blocked:
        text = f"🔒 <b>Заблоковані IP — {COMPANY}</b>\n\nЗаблокованих IP немає"
        keyboard = [[{"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"}]]
        send(text, keyboard, message_id)
        return

    lines = [f"🔒 <b>Заблоковані IP — {COMPANY}</b>", ""]
    for ip in blocked:
        lines.append(f"🚫 <code>{ip}</code>")

    buttons = [{"text": f"🔓 {ip}", "callback_data": f"unblock_confirm_{ip}"} for ip in blocked]
    keyboard = []
    for i in range(0, len(buttons), 2):
        keyboard.append(buttons[i:i+2])
    keyboard.append([{"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"}])
    send("\n".join(lines), keyboard, message_id)


def handle_block_confirm(ip: str, message_id=None):
    text = (
        f"🚫 <b>Блокування IP</b>\n\n"
        f"IP: <code>{ip}</code>\n\n"
        f"Буде створено правило Windows Firewall яке заблокує "
        f"всі вхідні з'єднання з цього IP.\n\n"
        f"Підтвердіть дію:"
    )
    keyboard = [
        [
            {"text": "✅ Заблокувати", "callback_data": f"block_do_{ip}"},
            {"text": "❌ Скасувати",   "callback_data": f"status_{SERVER_ID}"},
        ]
    ]
    send(text, keyboard, message_id)


def handle_block_do(ip: str, message_id=None):
    ok, msg = actions.block_ip(ip)
    icon = "✅" if ok else "❌"
    keyboard = [
        [
            {"text": "🔒 Заблоковані IP", "callback_data": f"blocked_list_{SERVER_ID}"},
            {"text": "🔙 Статус",          "callback_data": f"status_{SERVER_ID}"},
        ]
    ]
    send(f"{icon} {msg}", keyboard, message_id)


def handle_unblock_confirm(ip: str, message_id=None):
    text = (
        f"🔓 <b>Зняття блокування</b>\n\n"
        f"IP: <code>{ip}</code>\n\nПідтвердіть:"
    )
    keyboard = [
        [
            {"text": "✅ Розблокувати", "callback_data": f"unblock_do_{ip}"},
            {"text": "❌ Скасувати",    "callback_data": f"blocked_list_{SERVER_ID}"},
        ]
    ]
    send(text, keyboard, message_id)


def handle_unblock_do(ip: str, message_id=None):
    ok, msg = actions.unblock_ip(ip)
    icon = "✅" if ok else "❌"
    keyboard = [[{"text": "🔒 Заблоковані IP", "callback_data": f"blocked_list_{SERVER_ID}"}]]
    send(f"{icon} {msg}", keyboard, message_id)


# ─── Dispatcher ─────────────────────────────────────────────

def process_callback(query: dict):
    data = query.get("data", "")
    callback_id = query.get("id")
    message_id = query.get("message", {}).get("message_id")

    answer_callback(callback_id)
    logger.info(f"Callback: {data}")

    if data.startswith("status_"):
        handle_status(message_id)
    elif data.startswith("sessions_"):
        handle_sessions(message_id)
    elif data.startswith("disk_"):
        handle_disk(message_id)
    elif data.startswith("backups_"):
        handle_backups(message_id)
    elif data.startswith("chart_1_"):
        handle_chart(1, "combined", message_id)
    elif data.startswith("chart_24_"):
        handle_chart(24, "combined", message_id)
    elif data.startswith("chart_disk_"):
        handle_chart(24, "disk", message_id)
    elif data.startswith("chart_backup_"):
        handle_backup_chart(message_id)
    elif data.startswith("maint_on_"):
        from datetime import timedelta
        until = datetime.now() + timedelta(hours=2)
        storage.set_maintenance(SERVER_ID, until)
        send(f"🔧 Режим обслуговування увімкнено до {until.strftime('%H:%M')}")
        handle_status(message_id)
    elif data.startswith("maint_off_"):
        storage.clear_maintenance(SERVER_ID)
        send("✅ Режим обслуговування знято")
        handle_status(message_id)
    elif data.startswith("reboot_confirm_"):
        handle_reboot_confirm(message_id)
    elif data.startswith("reboot_do_"):
        handle_reboot(message_id)
    elif data.startswith("kick_all_"):
        handle_kick_all(message_id)
    elif data.startswith("kick_"):
        parts = data.split("_")
        if len(parts) >= 3:
            session_id = parts[1]
            handle_kick(session_id, message_id)
    elif data.startswith("restart_service_"):
        service = _cfg.get("MONITOR_SERVICES", "").split(",")[0].strip()
        ok, msg = actions.restart_service(service)
        send(f"{'✅' if ok else '❌'} {msg}")
    elif data.startswith("blocked_list_"):
        handle_blocked_list(message_id)
    elif data.startswith("block_confirm_"):
        handle_block_confirm(data[len("block_confirm_"):], message_id)
    elif data.startswith("block_do_"):
        handle_block_do(data[len("block_do_"):], message_id)
    elif data.startswith("unblock_confirm_"):
        handle_unblock_confirm(data[len("unblock_confirm_"):], message_id)
    elif data.startswith("unblock_do_"):
        handle_unblock_do(data[len("unblock_do_"):], message_id)


def process_message(message: dict):
    global _reply_thread_id
    text = message.get("text", "").strip()
    if not text:
        return

    # Відповідаємо у тому самому треді (гілці форуму) де написана команда
    _reply_thread_id = message.get("message_thread_id")
    logger.info(f"Повідомлення: {text} (thread={_reply_thread_id})")

    # В групах Telegram команди надходять як /cmd@botname — відкидаємо суфікс
    first_word = text.split()[0]
    cmd = first_word.split("@")[0].lower()
    args = text[len(first_word):].strip()

    if cmd in ("/status", "/start"):
        handle_status()
    elif cmd == "/sessions":
        handle_sessions()
    elif cmd == "/disk":
        handle_disk()
    elif cmd == "/backups":
        handle_backups()
    elif cmd == "/chart":
        handle_chart(24)
    elif cmd == "/maintenance":
        # /maintenance 2h  або  /maintenance off
        parts = args.split()
        arg = parts[0].lower() if parts else ""
        if arg == "off":
            storage.clear_maintenance(SERVER_ID)
            send("✅ Режим обслуговування знято")
        else:
            hours = 2
            if arg.endswith("h") and arg[:-1].isdigit():
                hours = max(1, min(24, int(arg[:-1])))
            from datetime import timedelta
            until = datetime.now() + timedelta(hours=hours)
            storage.set_maintenance(SERVER_ID, until)
            send(f"🔧 Обслуговування увімкнено на {hours}г (до {until.strftime('%H:%M')})")

    _reply_thread_id = None


# ─── Long polling ────────────────────────────────────────────

def run():
    logger.info(f"Бот запущений для {COMPANY} ({SERVER_ID})")

    if not BOT_TOKEN:
        logger.error("TG_BOT_TOKEN не налаштований — перевірте .env")
    else:
        me = api_call("getMe", {})
        if me.get("ok"):
            logger.info(f"Бот авторизований як @{me['result'].get('username', '?')}")
        else:
            logger.error(f"getMe помилка: {me.get('description', 'невідомо')} — перевірте TG_BOT_TOKEN")

    offset = 0

    while True:
        try:
            result = api_call("getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            })

            if not result.get("ok"):
                logger.error(f"getUpdates помилка: {result.get('description', result)}")
                time.sleep(5)
                continue

            updates = result.get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    if "callback_query" in update:
                        process_callback(update["callback_query"])
                    elif "message" in update:
                        process_message(update["message"])
                except Exception as e:
                    logger.error(f"Помилка обробки update: {e}")

        except Exception as e:
            logger.error(f"Polling помилка: {e}")
            time.sleep(5)


def _config() -> dict:
    return _cfg


if __name__ == "__main__":
    storage.init_db()
    run()
