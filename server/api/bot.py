"""
Telegram бот — централізований для всіх серверів.
Адаптація bot.py з SX_Monitoring під multi-server PostgreSQL.
"""
import logging
import time
import json
import requests
from datetime import datetime
from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal, init_db
from models import Server, MetricsSnapshot
from routers.commands import create_command

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("bot")

BASE_URL = f"https://api.telegram.org/bot{settings.tg_bot_token}"


def _api(method: str, payload: dict) -> dict:
    try:
        r = requests.post(f"{BASE_URL}/{method}", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        logger.error("%s error: %s", method, e)
        return {"ok": False}


def _send(chat_id: str, text: str, topic_id: str = None,
          keyboard: dict = None, message_id: int = None) -> dict:
    payload = {"chat_id": chat_id, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    if topic_id:
        payload["message_thread_id"] = int(topic_id)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)

    if message_id:
        payload["message_id"] = message_id
        result = _api("editMessageText", payload)
        if not result.get("ok"):
            err = result.get("description", "")
            if "message is not modified" not in err:
                del payload["message_id"]
                result = _api("sendMessage", payload)
        return result

    return _api("sendMessage", payload)


def _get_server_by_callback(data: str) -> tuple[Server | None, str]:
    """Повертає (server, action) з callback_data типу 'status_company_a'."""
    parts = data.split("_", 1)
    if len(parts) < 2:
        return None, data
    action, server_id = parts[0], parts[1]
    # action може бути складеним: 'restart_service', 'kill_session', etc.
    # Знаходимо сервер по суфіксу
    db: Session = SessionLocal()
    try:
        servers = db.query(Server).all()
        for s in servers:
            if data.endswith(s.id):
                action_part = data[: len(data) - len(s.id) - 1]
                return s, action_part
    finally:
        db.close()
    return None, action


def handle_callback(update: dict):
    cb   = update.get("callback_query", {})
    data = cb.get("data", "")
    msg  = cb.get("message", {})
    chat_id    = str(msg.get("chat", {}).get("id", ""))
    message_id = msg.get("message_id")
    topic_id   = str(msg.get("message_thread_id", "")) or None

    _api("answerCallbackQuery", {"callback_query_id": cb["id"]})

    server, action = _get_server_by_callback(data)
    if not server:
        _send(chat_id, "❌ Сервер не знайдено", topic_id, message_id=message_id)
        return

    db: Session = SessionLocal()
    try:
        snap = db.query(MetricsSnapshot).filter(
            MetricsSnapshot.server_id == server.id
        ).first()
        metrics = snap.data if snap else {}
    finally:
        db.close()

    if action == "status":
        _handle_status(chat_id, topic_id, message_id, server, metrics)
    elif action == "sessions":
        _handle_sessions(chat_id, topic_id, message_id, server, metrics)
    elif action == "disk":
        _handle_disk(chat_id, topic_id, message_id, server, metrics)
    elif action == "restart_service":
        _queue_command(chat_id, topic_id, message_id, server, "restart_service", {})
    elif action == "kill_session":
        _handle_kill_menu(chat_id, topic_id, message_id, server, metrics)
    elif action.startswith("kill_confirm_"):
        session_id = action.replace("kill_confirm_", "")
        _queue_command(chat_id, topic_id, message_id, server,
                       "kick_session", {"session_id": session_id})
    elif action.startswith("block_confirm_"):
        ip = data.replace(f"block_confirm_", "").replace(f"_{server.id}", "")
        _queue_command(chat_id, topic_id, message_id, server,
                       "block_ip", {"ip": ip})
    elif action == "reboot":
        _queue_command(chat_id, topic_id, message_id, server, "reboot", {})


def _handle_status(chat_id, topic_id, message_id, server: Server, metrics: dict):
    cpu  = metrics.get("cpu", {})
    ram  = metrics.get("ram", {})
    disks = metrics.get("disks", [])

    lines = [f"📊 <b>Статус — {server.name}</b>",
             f"🕐 {datetime.now().strftime('%H:%M:%S')}", ""]

    lines.append(f"🖥 CPU: <b>{cpu.get('percent', '?')}%</b>  "
                 f"RAM: <b>{ram.get('percent', '?')}%</b> "
                 f"(вільно {ram.get('free_gb', '?')} GB)")

    for d in disks:
        icon = "🔴" if d.get("free_pct", 100) < 5 else \
               "⚠️" if d.get("free_pct", 100) < 10 else "💾"
        lines.append(f"{icon} {d['path']} {d.get('free_pct')}% ({d.get('free_gb')} GB)")

    for svc in metrics.get("services", []):
        icon = "✅" if svc["is_running"] else "❌"
        lines.append(f"{icon} {svc['name']}")

    keyboard = {"inline_keyboard": [[
        {"text": "🔄 Оновити", "callback_data": f"status_{server.id}"},
        {"text": "👥 Сесії",   "callback_data": f"sessions_{server.id}"},
    ]]}
    _send(chat_id, "\n".join(lines), topic_id, keyboard, message_id)


def _handle_sessions(chat_id, topic_id, message_id, server: Server, metrics: dict):
    sessions = metrics.get("active_sessions", [])
    if not sessions:
        _send(chat_id, "👥 Активних сесій немає", topic_id, message_id=message_id)
        return

    lines = [f"👥 <b>Сесії — {server.name}</b>", ""]
    for s in sessions:
        lines.append(f"• {s.get('username', '?')} [{s.get('state', '?')}] "
                     f"id={s.get('id', '?')}")

    btns = [[{"text": f"🚫 Kick {s.get('username','?')}",
              "callback_data": f"kill_confirm_{s.get('id', '0')}_{server.id}"}]
            for s in sessions[:5]]
    _send(chat_id, "\n".join(lines), topic_id, {"inline_keyboard": btns}, message_id)


def _handle_disk(chat_id, topic_id, message_id, server: Server, metrics: dict):
    lines = [f"💾 <b>Диски — {server.name}</b>", ""]
    for d in metrics.get("disks", []):
        delta = f" ↓{abs(d['delta_1h']):.1f}%/г" if d.get("delta_1h", 0) < 0 else ""
        lines.append(f"{d['path']}: {d.get('free_pct')}% вільно "
                     f"({d.get('free_gb')} / {d.get('total_gb')} GB){delta}")
    _send(chat_id, "\n".join(lines), topic_id, message_id=message_id)


def _handle_kill_menu(chat_id, topic_id, message_id, server: Server, metrics: dict):
    sessions = metrics.get("active_sessions", [])
    if not sessions:
        _send(chat_id, "Активних сесій немає", topic_id, message_id=message_id)
        return
    btns = [[{"text": f"❌ {s.get('username','?')} (id={s.get('id','?')})",
              "callback_data": f"kill_confirm_{s.get('id','0')}_{server.id}"}]
            for s in sessions[:5]]
    btns.append([{"text": "❌ Всі сесії", "callback_data": f"kill_confirm_all_{server.id}"}])
    _send(chat_id, "Оберіть сесію для завершення:", topic_id,
          {"inline_keyboard": btns}, message_id)


def _queue_command(chat_id, topic_id, message_id, server: Server,
                   action: str, params: dict):
    db: Session = SessionLocal()
    try:
        create_command(db, server.id, action, params, chat_id, message_id)
        icons = {"block_ip": "🚫", "kick_session": "👤",
                 "restart_service": "🔄", "reboot": "🔴"}
        icon = icons.get(action, "⏳")
        _send(chat_id,
              f"{icon} Команда <b>{action}</b> поставлена в чергу для {server.name}\n"
              f"Виконається протягом 5-10 секунд...",
              topic_id, message_id=message_id)
    finally:
        db.close()


def run():
    init_db()
    logger.info("Bot started")
    offset = 0

    while True:
        try:
            result = _api("getUpdates", {"offset": offset, "timeout": 30,
                                         "allowed_updates": ["callback_query"]})
            if not result.get("ok"):
                desc = result.get("description", "")
                if "409" in desc or "conflict" in desc.lower():
                    logger.warning("getUpdates conflict — паралельний екземпляр!")
                time.sleep(5)
                continue

            for upd in result.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd:
                        handle_callback(upd)
                except Exception as e:
                    logger.error("handle update error: %s", e)

        except Exception as e:
            logger.error("polling error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run()
