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

from collectors import disk as disk_collector
from collectors import memory as mem_collector
from collectors import services as svc_collector

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
    if TOPIC_ID:
        payload["message_thread_id"] = int(TOPIC_ID)
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
    config   = _config()
    disk_data = disk_collector.collect(config)
    mem_data  = mem_collector.collect(config)
    svc_data  = svc_collector.collect(config)

    maint_until = storage.get_maintenance_until(SERVER_ID)
    maint_badge = f"\n🔧 <b>Обслуговування до {maint_until.strftime('%H:%M')})</b>" if maint_until else ""

    lines = [
        f"📡 <b>Статус — {COMPANY}</b>{maint_badge}",
        f"🕐 {datetime.now().strftime('%H:%M %d.%m.%Y')}",
        "",
    ]

    for d in disk_data.get("disks", []):
        if "free_pct" in d:
            icon = "🔴" if d["free_pct"] < 10 else "⚠️" if d["free_pct"] < 20 else "✅"
            lines.append(f"{icon} Диск {d['path']}: {d['free_pct']}% вільно ({d['free_gb']}GB)")

    cpu = mem_data.get("cpu", {})
    ram = mem_data.get("ram", {})
    lines.append(f"{'🔴' if cpu.get('percent',0)>85 else '✅'} CPU: {cpu.get('percent','?')}%")
    lines.append(f"{'🔴' if ram.get('percent',0)>90 else '✅'} RAM: {ram.get('percent','?')}% (вільно {ram.get('free_gb','?')}GB)")

    lines.append("")
    for svc in svc_data.get("services", []):
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
    buttons = []

    for i, s in enumerate(sessions):
        state_icon = "🟢" if s.get("state") in ("Active", "Activ") else "🟡"
        lines.append(f"{state_icon} <b>{s.get('username', '?')}</b> | ID: {s.get('session_id')} | {s.get('state')}")
        buttons.append({"text": f"❌ Вибити {s.get('username', s.get('session_id'))}", "callback_data": f"kick_{s.get('session_id')}_{SERVER_ID}"})

    keyboard = []
    # По 2 кнопки в ряд
    for i in range(0, len(buttons), 2):
        keyboard.append(buttons[i:i+2])

    keyboard.append([
        {"text": "❌ Вибити всіх", "callback_data": f"kick_all_{SERVER_ID}"},
        {"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"},
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


def process_message(message: dict):
    text = message.get("text", "").strip()
    if not text:
        return

    logger.info(f"Повідомлення: {text}")

    if text in ("/status", "/start"):
        handle_status()
    elif text == "/sessions":
        handle_sessions()
    elif text == "/disk":
        handle_disk()
    elif text == "/backups":
        handle_backups()
    elif text.startswith("/chart"):
        handle_chart(24)
    elif text.startswith("/maintenance"):
        # /maintenance 2h  або  /maintenance off
        parts = text.split()
        arg = parts[1].lower() if len(parts) > 1 else ""
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


# ─── Long polling ────────────────────────────────────────────

def run():
    logger.info(f"Бот запущений для {COMPANY} ({SERVER_ID})")
    offset = 0

    while True:
        try:
            result = api_call("getUpdates", {
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            })

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
