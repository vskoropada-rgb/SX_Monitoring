"""
bot.py — Telegram бот: інтерактивні кнопки, long polling.

Архітектура:
  - Кожен агент відповідає лише в своєму TG_TOPIC_ID (фільтрація по message_thread_id).
  - thread_id передається явно через всі виклики — без глобального стану і race conditions.
  - Кеш сесій із Lock'ом — qwinsta не запускається паралельно при швидких кліках.
  - Колбеки обробляються у власних потоках, але _send() сам по собі thread-safe.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

import requests

import config as _config_module
import storage
import actions
import notifier
import charts

logger = logging.getLogger("bot")

# ─── Конфіг (read-only після старту) ─────────────────────────

_cfg = _config_module.load()

BOT_TOKEN  = _cfg["TG_BOT_TOKEN"]
GROUP_ID   = _cfg["TG_GROUP_ID"]
TOPIC_ID   = _cfg.get("TG_TOPIC_ID", "") or ""
SERVER_ID  = _cfg["SERVER_ID"]
COMPANY    = _cfg["COMPANY_NAME"]
DISK_PATHS = [p.strip() for p in _cfg.get("DISK_PATHS", "C:\\").split(",") if p.strip()]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _topic_thread() -> Optional[int]:
    """Сконфігурований TOPIC_ID як int, або None."""
    if not TOPIC_ID:
        return None
    try:
        return int(TOPIC_ID)
    except (ValueError, TypeError):
        logger.warning("Невірний TG_TOPIC_ID: %r", TOPIC_ID)
        return None


# ─── HTTP до Telegram API ────────────────────────────────────


def _api_call(method: str, data: dict, timeout: int = 10) -> dict:
    try:
        r = requests.post(f"{API}/{method}", json=data, timeout=timeout)
        return r.json()
    except Exception as e:
        logger.error("API помилка %s: %s", method, e)
        return {}


def _answer_callback(callback_id: str, text: str = "") -> None:
    _api_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def _send(text: str, *, thread_id: Optional[int],
          keyboard: Optional[List[List[dict]]] = None,
          message_id: Optional[int] = None) -> dict:
    """
    Надсилає або редагує повідомлення у вказаному топіку.
    thread_id — обов'язковий параметр (передається з handlers), щоб уникнути race condition.
    """
    payload = {
        "chat_id": GROUP_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})

    if message_id:
        payload["message_id"] = message_id
        result = _api_call("editMessageText", payload)
        if not result.get("ok"):
            err = result.get("description", "")
            if "message is not modified" not in err:
                logger.warning("editMessageText failed: %s — надсилаю нове повідомлення", err)
                del payload["message_id"]
                result = _api_call("sendMessage", payload)
                if not result.get("ok"):
                    logger.error("sendMessage failed: %s | text=%.80r",
                                 result.get("description"), text)
        return result
    result = _api_call("sendMessage", payload)
    if not result.get("ok"):
        logger.error("sendMessage failed: %s | text=%.80r", result.get("description"), text)
    return result


def _send_photo(image_path: str, caption: str, thread_id: Optional[int]) -> None:
    """Делегує до notifier.send_photo з потрібним thread_id."""
    cfg = {
        "TG_BOT_TOKEN": BOT_TOKEN,
        "TG_GROUP_ID":  GROUP_ID,
        "TG_TOPIC_ID":  str(thread_id) if thread_id else TOPIC_ID,
    }
    try:
        notifier.send_photo(image_path, caption, cfg)
    except Exception as e:
        logger.error("send_photo failed: %s", e)


def _send_and_cleanup(path: Optional[str], caption: str, thread_id: Optional[int],
                     empty_msg: str) -> None:
    """Відправляє фото з тимчасового файлу і видаляє його. Або шле empty_msg якщо path None."""
    if not path:
        _send(empty_msg, thread_id=thread_id)
        return
    try:
        _send_photo(path, caption, thread_id)
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


# ─── Кеш сесій (thread-safe) ─────────────────────────────────

_sessions_lock = threading.Lock()
_sessions_cache: List[dict] = []
_sessions_cache_ts: float = 0.0
_SESSIONS_TTL = 30


def _get_sessions() -> List[dict]:
    global _sessions_cache, _sessions_cache_ts
    now = time.monotonic()
    with _sessions_lock:
        if now - _sessions_cache_ts > _SESSIONS_TTL:
            _sessions_cache = actions.get_sessions()
            _sessions_cache_ts = now
        return list(_sessions_cache)


def _invalidate_sessions() -> None:
    global _sessions_cache_ts
    with _sessions_lock:
        _sessions_cache_ts = 0.0


# ─── Обробники команд ────────────────────────────────────────


def handle_status(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    m = storage.load_metrics_cache()
    maint_until = storage.get_maintenance_until(SERVER_ID)
    maint_badge = (
        f"\n🔧 <b>Обслуговування до {maint_until.strftime('%H:%M')}</b>"
        if maint_until else ""
    )

    lines = [
        f"📡 <b>Статус — {COMPANY}</b>{maint_badge}",
        f"🕐 {datetime.now().strftime('%H:%M %d.%m.%Y')}",
        "",
    ]

    for d in m.get("disks", []):
        if "free_pct" in d:
            free = d["free_pct"]
            icon = "🔴" if free < 10 else "⚠️" if free < 20 else "✅"
            lines.append(f"{icon} {d['path']}: {free}% вільно ({d['free_gb']}GB)")

    cpu = m.get("cpu", {})
    ram = m.get("ram", {})
    lines.append(f"{'🔴' if cpu.get('percent', 0) > 85 else '✅'} CPU: {cpu.get('percent', '?')}%")
    lines.append(
        f"{'🔴' if ram.get('percent', 0) > 90 else '✅'} "
        f"RAM: {ram.get('percent', '?')}% (вільно {ram.get('free_gb', '?')}GB)"
    )

    lines.append("")
    for svc in m.get("services", []):
        icon = "✅" if svc.get("is_running") else "❌"
        lines.append(f"{icon} {svc.get('name', '?')}")

    maint_btn = (
        {"text": "✅ Зняти обслуговування", "callback_data": f"maint_off_{SERVER_ID}"}
        if maint_until else
        {"text": "🔧 Обслуговування 2г", "callback_data": f"maint_on_{SERVER_ID}"}
    )

    keyboard = [
        [
            {"text": "👥 Сесії", "callback_data": f"sessions_{SERVER_ID}"},
            {"text": "💾 Диски", "callback_data": f"disk_{SERVER_ID}"},
        ],
        [
            {"text": "📊 Графік 1г",  "callback_data": f"chart_1_{SERVER_ID}"},
            {"text": "📊 Графік 24г", "callback_data": f"chart_24_{SERVER_ID}"},
        ],
        [
            {"text": "📦 Бекапи",        "callback_data": f"backups_{SERVER_ID}"},
            {"text": "📈 Тренд бекапів", "callback_data": f"chart_backup_{SERVER_ID}"},
        ],
        [
            {"text": "🔄 Перезавантажити", "callback_data": f"reboot_confirm_{SERVER_ID}"},
            maint_btn,
        ],
        [
            {"text": "🔒 Заблоковані IP", "callback_data": f"blocked_list_{SERVER_ID}"},
        ],
    ]

    _send("\n".join(lines), thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_sessions(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    sessions = _get_sessions()

    if not sessions:
        text = f"👥 <b>Сесії — {COMPANY}</b>\n\nАктивних сесій немає"
        keyboard = [[{"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"}]]
        _send(text, thread_id=thread_id, keyboard=keyboard, message_id=message_id)
        return

    lines = [f"👥 <b>Активні сесії — {COMPANY}</b>", ""]
    buttons: List[List[dict]] = []

    for s in sessions:
        state = s.get("state", "")
        state_low = state.lower()
        icon = "🟢" if any(state_low.startswith(p) for p in ("activ", "акт")) else "🟡"
        user = s.get("username") or s.get("session_name", "?")
        sid  = s.get("session_id", "")
        lines.append(f"{icon} <b>{user}</b>  ({state})")
        if sid.isdigit() and int(sid) > 0:
            buttons.append([{
                "text": f"🚪 Завершити {user}",
                "callback_data": f"kick_{sid}_{SERVER_ID}",
            }])

    buttons.append([
        {"text": "🚪 Завершити всіх", "callback_data": f"kick_all_{SERVER_ID}"},
        {"text": "🔙 Назад",          "callback_data": f"status_{SERVER_ID}"},
    ])

    _send("\n".join(lines), thread_id=thread_id, keyboard=buttons, message_id=message_id)


def handle_disk(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    text = actions.get_disk_details(DISK_PATHS)
    keyboard = [[
        {"text": "📊 Графік диску", "callback_data": f"chart_disk_{SERVER_ID}"},
        {"text": "🔙 Назад",        "callback_data": f"status_{SERVER_ID}"},
    ]]
    _send(text, thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_backups(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    from collectors import backup as backup_collector
    data = backup_collector.collect(_cfg)

    status = data.get("status", "невідомо")
    icon = "✅" if status == "ok" else "⚠️" if status == "warning" else "❌"

    lines = [
        f"📦 <b>Бекапи — {COMPANY}</b>",
        "",
        f"{icon} Статус: {status}",
    ]

    if "latest_file" in data:
        lines.extend([
            f"📄 Останній: {data.get('latest_file', 'н/д')}",
            f"⏰ Час: {data.get('latest_time', 'н/д')} ({data.get('latest_age_hours')}г тому)",
            f"📏 Розмір: {data.get('latest_size_mb')} MB",
        ])
    elif data.get("error"):
        lines.append(f"❗ {data['error']}")

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
    _send("\n".join(lines), thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_chart(hours: int, metric: str, thread_id: Optional[int],
                 message_id: Optional[int] = None) -> None:
    _send(f"⏳ Генерую графік за {hours}г...", thread_id=thread_id)

    if metric == "combined":
        path = charts.generate_combined_chart(SERVER_ID, hours=hours)
        caption = f"📊 <b>{COMPANY}</b> — CPU та RAM за {hours}г"
    elif metric == "disk":
        if not DISK_PATHS:
            _send("❌ Не налаштовано DISK_PATHS", thread_id=thread_id)
            return
        disk_key = DISK_PATHS[0].replace(":", "").replace("\\", "")
        metric_name = f"disk_{disk_key}_free_pct"
        path = charts.generate_chart(
            metric_name, hours=hours, title=f"Диск {DISK_PATHS[0]} — вільно %"
        )
        caption = f"💾 <b>{COMPANY}</b> — Диск {DISK_PATHS[0]} за {hours}г"
    else:
        path = charts.generate_chart(metric, hours=hours)
        caption = f"📊 <b>{COMPANY}</b> — {metric} за {hours}г"

    _send_and_cleanup(
        path, caption, thread_id,
        empty_msg="❌ Недостатньо даних для графіку. Підождіть поки збереться більше метрик."
    )


def handle_backup_chart(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    _send("⏳ Генерую графік тренду бекапів...", thread_id=thread_id)
    path = charts.generate_backup_chart(days=30)
    _send_and_cleanup(
        path, f"📈 <b>{COMPANY}</b> — Розмір бекапів за 30 днів", thread_id,
        empty_msg="❌ Недостатньо даних для графіку (потрібно 2+ бекапи)"
    )


def handle_reboot_confirm(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    sessions = _get_sessions()
    warn = f"\n⚠️ Є <b>{len(sessions)}</b> активних сесій!" if sessions else ""
    text = (
        f"🔄 <b>Перезавантаження сервера</b>\n"
        f"Компанія: {COMPANY}{warn}\n\n"
        f"Сервер перезавантажиться через 30 секунд.\n"
        f"Підтвердіть дію:"
    )
    keyboard = [[
        {"text": "✅ Підтвердити", "callback_data": f"reboot_do_{SERVER_ID}"},
        {"text": "❌ Скасувати",   "callback_data": f"status_{SERVER_ID}"},
    ]]
    _send(text, thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_reboot(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    ok, msg = actions.reboot_server(delay_sec=30)
    icon = "✅" if ok else "❌"
    keyboard = [[{"text": "🔙 Головне меню", "callback_data": f"status_{SERVER_ID}"}]]
    _send(f"{icon} {msg}", thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_kick(session_id: str, thread_id: Optional[int],
                message_id: Optional[int] = None) -> None:
    ok, msg = actions.kick_session(session_id)
    _invalidate_sessions()
    icon = "✅" if ok else "❌"
    keyboard = [[{"text": "🔄 Оновити список", "callback_data": f"sessions_{SERVER_ID}"}]]
    _send(f"{icon} {msg}", thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_kick_all(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    ok, msg = actions.kick_all_sessions()
    _invalidate_sessions()
    icon = "✅" if ok else "❌"
    text = f"{icon} <b>Результат:</b>\n{msg}"
    keyboard = [[{"text": "🔙 Назад", "callback_data": f"sessions_{SERVER_ID}"}]]
    _send(text, thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_maintenance(on: bool, thread_id: Optional[int],
                       message_id: Optional[int] = None) -> None:
    if on:
        until = datetime.now() + timedelta(hours=2)
        storage.set_maintenance(SERVER_ID, until)
        _send(
            f"🔧 Режим обслуговування увімкнено до {until.strftime('%H:%M')}",
            thread_id=thread_id,
        )
    else:
        storage.clear_maintenance(SERVER_ID)
        _send("✅ Режим обслуговування знято", thread_id=thread_id)
    handle_status(thread_id, message_id)


def handle_blocked_list(thread_id: Optional[int], message_id: Optional[int] = None) -> None:
    blocked = actions.list_blocked_ips()
    if not blocked:
        text = f"🔒 <b>Заблоковані IP — {COMPANY}</b>\n\nЗаблокованих IP немає"
        keyboard = [[{"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"}]]
        _send(text, thread_id=thread_id, keyboard=keyboard, message_id=message_id)
        return

    lines = [f"🔒 <b>Заблоковані IP — {COMPANY}</b>", ""]
    for ip in blocked:
        lines.append(f"🚫 <code>{ip}</code>")

    buttons = [{"text": f"🔓 {ip}", "callback_data": f"unblock_confirm_{ip}"} for ip in blocked]
    keyboard: List[List[dict]] = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    keyboard.append([{"text": "🔙 Назад", "callback_data": f"status_{SERVER_ID}"}])
    _send("\n".join(lines), thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_block_confirm(ip: str, thread_id: Optional[int],
                         message_id: Optional[int] = None) -> None:
    text = (
        f"🚫 <b>Блокування IP</b>\n\n"
        f"IP: <code>{ip}</code>\n\n"
        f"Буде створено правило Windows Firewall яке заблокує "
        f"всі вхідні з'єднання з цього IP.\n\n"
        f"Підтвердіть дію:"
    )
    keyboard = [[
        {"text": "✅ Заблокувати", "callback_data": f"block_do_{ip}"},
        {"text": "❌ Скасувати",   "callback_data": f"status_{SERVER_ID}"},
    ]]
    _send(text, thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_block_do(ip: str, thread_id: Optional[int],
                    message_id: Optional[int] = None) -> None:
    ok, msg = actions.block_ip(ip)
    icon = "✅" if ok else "❌"
    keyboard = [[
        {"text": "🔒 Заблоковані IP", "callback_data": f"blocked_list_{SERVER_ID}"},
        {"text": "🔙 Статус",         "callback_data": f"status_{SERVER_ID}"},
    ]]
    _send(f"{icon} {msg}", thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_unblock_confirm(ip: str, thread_id: Optional[int],
                           message_id: Optional[int] = None) -> None:
    text = f"🔓 <b>Зняття блокування</b>\n\nIP: <code>{ip}</code>\n\nПідтвердіть:"
    keyboard = [[
        {"text": "✅ Розблокувати", "callback_data": f"unblock_do_{ip}"},
        {"text": "❌ Скасувати",    "callback_data": f"blocked_list_{SERVER_ID}"},
    ]]
    _send(text, thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_unblock_do(ip: str, thread_id: Optional[int],
                      message_id: Optional[int] = None) -> None:
    ok, msg = actions.unblock_ip(ip)
    icon = "✅" if ok else "❌"
    keyboard = [[{"text": "🔒 Заблоковані IP", "callback_data": f"blocked_list_{SERVER_ID}"}]]
    _send(f"{icon} {msg}", thread_id=thread_id, keyboard=keyboard, message_id=message_id)


def handle_restart_service(thread_id: Optional[int]) -> None:
    services = [s.strip() for s in _cfg.get("MONITOR_SERVICES", "").split(",") if s.strip()]
    if not services:
        _send("❌ Не налаштовано MONITOR_SERVICES", thread_id=thread_id)
        return
    ok, msg = actions.restart_service(services[0])
    icon = "✅" if ok else "❌"
    _send(f"{icon} {msg}", thread_id=thread_id)


# ─── Dispatcher ──────────────────────────────────────────────


def _dispatch_callback(data: str, thread_id: Optional[int],
                       message_id: Optional[int]) -> None:
    """Викликається у власному потоці після _answer_callback()."""
    try:
        # Префіксні роутери — порядок важливий: більш специфічні зверху
        if data.startswith("chart_1_"):
            handle_chart(1, "combined", thread_id, message_id)
        elif data.startswith("chart_24_"):
            handle_chart(24, "combined", thread_id, message_id)
        elif data.startswith("chart_disk_"):
            handle_chart(24, "disk", thread_id, message_id)
        elif data.startswith("chart_backup_"):
            handle_backup_chart(thread_id, message_id)
        elif data.startswith("status_"):
            handle_status(thread_id, message_id)
        elif data.startswith("sessions_"):
            handle_sessions(thread_id, message_id)
        elif data.startswith("disk_"):
            handle_disk(thread_id, message_id)
        elif data.startswith("backups_"):
            handle_backups(thread_id, message_id)
        elif data.startswith("maint_on_"):
            handle_maintenance(True, thread_id, message_id)
        elif data.startswith("maint_off_"):
            handle_maintenance(False, thread_id, message_id)
        elif data.startswith("reboot_confirm_"):
            handle_reboot_confirm(thread_id, message_id)
        elif data.startswith("reboot_do_"):
            handle_reboot(thread_id, message_id)
        elif data.startswith("kick_all_"):
            handle_kick_all(thread_id, message_id)
        elif data.startswith("kick_"):
            # kick_<sid>_<server_id>
            parts = data.split("_")
            if len(parts) >= 3 and parts[1].isdigit():
                handle_kick(parts[1], thread_id, message_id)
            else:
                logger.warning("Невірний kick callback: %r", data)
        elif data.startswith("restart_service_"):
            handle_restart_service(thread_id)
        elif data.startswith("blocked_list_"):
            handle_blocked_list(thread_id, message_id)
        elif data.startswith("block_confirm_"):
            handle_block_confirm(data[len("block_confirm_"):], thread_id, message_id)
        elif data.startswith("block_do_"):
            handle_block_do(data[len("block_do_"):], thread_id, message_id)
        elif data.startswith("unblock_confirm_"):
            handle_unblock_confirm(data[len("unblock_confirm_"):], thread_id, message_id)
        elif data.startswith("unblock_do_"):
            handle_unblock_do(data[len("unblock_do_"):], thread_id, message_id)
        else:
            logger.warning("Невідомий callback: %r", data)
    except Exception as e:
        logger.exception("Помилка обробки callback %r: %s", data, e)
        try:
            _send(f"❌ Помилка обробки: {e}", thread_id=thread_id)
        except Exception:
            pass


def _process_callback(query: dict) -> None:
    data        = query.get("data", "")
    callback_id = query.get("id", "")
    msg         = query.get("message") or {}
    message_id  = msg.get("message_id")
    msg_thread  = msg.get("message_thread_id")

    # Фільтр: тільки колбеки зі свого топіку
    tid = _topic_thread()
    if tid is not None and msg_thread != tid:
        return

    # Підтверджуємо натискання ОДРАЗУ — прибирає spinning indicator
    if callback_id:
        _answer_callback(callback_id)
    logger.info("Callback: %s (thread=%s)", data, msg_thread)

    # Обробляємо у власному потоці — long polling не блокується
    threading.Thread(
        target=_dispatch_callback,
        args=(data, msg_thread, message_id),
        daemon=True,
        name=f"cb-{data[:20]}",
    ).start()


def _process_message(message: dict) -> None:
    text = (message.get("text") or "").strip()
    if not text:
        return

    msg_thread = message.get("message_thread_id")
    tid = _topic_thread()
    if tid is not None and msg_thread != tid:
        return

    logger.info("Повідомлення: %s (thread=%s)", text, msg_thread)

    # /cmd@botname → /cmd
    first = text.split()[0]
    cmd = first.split("@")[0].lower()
    args = text[len(first):].strip()

    if cmd in ("/status", "/start"):
        handle_status(msg_thread)
    elif cmd == "/sessions":
        handle_sessions(msg_thread)
    elif cmd == "/disk":
        handle_disk(msg_thread)
    elif cmd == "/backups":
        handle_backups(msg_thread)
    elif cmd == "/chart":
        handle_chart(24, "combined", msg_thread)
    elif cmd == "/maintenance":
        parts = args.split()
        arg = parts[0].lower() if parts else ""
        if arg == "off":
            storage.clear_maintenance(SERVER_ID)
            _send("✅ Режим обслуговування знято", thread_id=msg_thread)
        else:
            hours = 2
            if arg.endswith("h") and arg[:-1].isdigit():
                hours = max(1, min(24, int(arg[:-1])))
            until = datetime.now() + timedelta(hours=hours)
            storage.set_maintenance(SERVER_ID, until)
            _send(
                f"🔧 Обслуговування увімкнено на {hours}г (до {until.strftime('%H:%M')})",
                thread_id=msg_thread,
            )


# ─── Long polling ────────────────────────────────────────────


def run() -> None:
    logger.info("Бот запущений для %s (%s)", COMPANY, SERVER_ID)

    if not BOT_TOKEN:
        logger.error("TG_BOT_TOKEN не налаштований — бот зупинено")
        return

    me = _api_call("getMe", {})
    if me.get("ok"):
        logger.info("Бот авторизований як @%s", me["result"].get("username", "?"))
    else:
        logger.error("getMe: %s — перевірте TG_BOT_TOKEN", me.get("description", "невідомо"))
        return

    offset = 0
    while True:
        try:
            result = _api_call("getUpdates", {
                "offset":  offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=40)

            if not result.get("ok"):
                desc = result.get("description", str(result))
                if "Conflict" in desc:
                    # Інший екземпляр бота активний — чекаємо поки його лонг-полл (30с) завершиться
                    logger.warning("getUpdates конфлікт — паралельний екземпляр! Чекаю 35с...")
                    time.sleep(35)
                else:
                    logger.error("getUpdates помилка: %s", desc)
                    time.sleep(5)
                continue

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                try:
                    if "callback_query" in update:
                        _process_callback(update["callback_query"])
                    elif "message" in update:
                        _process_message(update["message"])
                except Exception as e:
                    logger.exception("Помилка update: %s", e)

        except KeyboardInterrupt:
            logger.info("Bot зупинений (Ctrl+C)")
            return
        except Exception as e:
            logger.error("Polling помилка: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    storage.init_db()
    run()
