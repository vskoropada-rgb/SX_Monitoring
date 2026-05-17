"""
notifier.py — відправка повідомлень в Telegram
"""
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SEVERITY_ICONS = {
    "critical": "🔴",
    "warning": "⚠️",
    "info": "ℹ️",
}


def send_alert(decision: dict, metrics: dict, config: dict) -> bool:
    """Відправляє алерт в Telegram тему компанії"""
    bot_token = config.get("TG_BOT_TOKEN")
    group_id = config.get("TG_GROUP_ID")
    topic_id = config.get("TG_TOPIC_ID")
    company = config.get("COMPANY_NAME", config.get("SERVER_ID", "Server"))

    if not bot_token or not group_id:
        logger.error("TG_BOT_TOKEN або TG_GROUP_ID не налаштовані")
        return False

    icon = SEVERITY_ICONS.get(decision.get("severity", "info"), "ℹ️")
    tags = " ".join(decision.get("tags", []))
    title = decision.get("title", "Подія на сервері")
    analysis = decision.get("analysis", "")
    recommendation = decision.get("recommendation", "")
    now = datetime.now().strftime("%H:%M %d.%m.%Y")

    # Формуємо повідомлення
    lines = [
        f"{icon} <b>[{company}]</b> {now}",
        f"{tags}",
        "",
        f"<b>{title}</b>",
    ]

    if analysis:
        lines.append("")
        lines.append(f"📋 {analysis}")

    if recommendation:
        lines.append("")
        lines.append(f"⚡ <b>Рекомендація:</b> {recommendation}")

    # Додаємо ключові метрики
    metrics_block = _format_metrics_block(metrics)
    if metrics_block:
        lines.append("")
        lines.append(metrics_block)

    text = "\n".join(lines)

    # Кнопки дій
    keyboard = _build_keyboard(decision, config)

    return _send_message(bot_token, group_id, topic_id, text, keyboard)


def send_message(text: str, config: dict, keyboard=None) -> bool:
    """Відправляє довільне повідомлення"""
    bot_token = config.get("TG_BOT_TOKEN")
    group_id = config.get("TG_GROUP_ID")
    topic_id = config.get("TG_TOPIC_ID")
    return _send_message(bot_token, group_id, topic_id, text, keyboard)


def _format_metrics_block(metrics: dict) -> str:
    lines = ["📊 <b>Метрики:</b>"]

    if "disks" in metrics:
        for d in metrics["disks"]:
            if "free_pct" in d:
                delta = f" ↓{abs(d['delta_1h']):.1f}%/г" if d.get("delta_1h") and d["delta_1h"] < 0 else ""
                lines.append(f"• Диск {d['path']}: {d['free_pct']}% вільно ({d['free_gb']}GB){delta}")

    if "cpu" in metrics:
        lines.append(f"• CPU: {metrics['cpu']['percent']}% | RAM: {metrics['ram']['percent']}%")

    if "services" in metrics:
        for svc in metrics["services"]:
            icon = "✅" if svc["is_running"] else "❌"
            lines.append(f"• {icon} {svc['name']}")

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _build_keyboard(decision: dict, config: dict) -> dict:
    """Будує inline клавіатуру залежно від типу алерту"""
    tags = decision.get("tags", [])
    server_id = config.get("SERVER_ID", "server")
    buttons = []

    row1 = []
    row2 = []

    # Завжди показуємо статус і сесії
    row1.append({"text": "📊 Статус", "callback_data": f"status_{server_id}"})
    row1.append({"text": "👥 Сесії", "callback_data": f"sessions_{server_id}"})

    if "#service" in tags:
        row2.append({"text": "🔄 Перезапустити сервіс", "callback_data": f"restart_service_{server_id}"})

    if "#rdp" in tags or "#new_ip" in tags:
        row2.append({"text": "🔒 Завершити сесію", "callback_data": f"kill_session_{server_id}"})

    if "#disk" in tags:
        row2.append({"text": "💾 Деталі диску", "callback_data": f"disk_{server_id}"})

    buttons.append(row1)
    if row2:
        buttons.append(row2)

    return {"inline_keyboard": buttons}


def _send_message(bot_token: str, group_id: str, topic_id: str, text: str, keyboard=None) -> bool:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": group_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if topic_id:
        payload["message_thread_id"] = int(topic_id)

    if keyboard:
        import json
        payload["reply_markup"] = json.dumps(keyboard)

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True
        else:
            logger.error(f"Telegram помилка: {resp.status_code} — {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Помилка відправки в Telegram: {e}")
        return False


def send_daily_report(metrics: dict, config: dict) -> bool:
    """Щоденний звіт о заданій годині"""
    company = config.get("COMPANY_NAME", config.get("SERVER_ID", "Server"))
    now     = datetime.now()

    disk_lines = []
    for d in metrics.get("disks", []):
        if "free_pct" in d:
            icon = "🔴" if d["free_pct"] < 10 else "⚠️" if d["free_pct"] < 20 else "✅"
            disk_lines.append(f"  {icon} {d['path']}: {d['free_pct']}% ({d['free_gb']} GB)")

    cpu = metrics.get("cpu", {}).get("percent", "?")
    ram = metrics.get("ram", {}).get("percent", "?")

    b_icon = "✅" if metrics.get("status") == "ok" else "⚠️"
    b_line = (
        f"{b_icon} {metrics.get('latest_file', 'н/д')}  "
        f"{metrics.get('latest_size_mb', '?')} MB  "
        f"{metrics.get('latest_age_hours', '?')}г тому"
    )
    if metrics.get("schedule_info"):
        b_line += f"  (розклад {metrics['schedule_info']})"

    reboot_line = (
        "⚠️ Очікує перезавантаження!" if metrics.get("reboot_required")
        else "✅ Перезавантаження не потрібне"
    )

    lines = [
        f"📊 <b>Щоденний звіт — {company}</b>",
        f"📅 {now.strftime('%d.%m.%Y %H:%M')}",
        "",
        "💾 <b>Диски:</b>",
        *disk_lines,
        "",
        f"🖥 CPU: <b>{cpu}%</b>  |  RAM: <b>{ram}%</b>",
        "",
        f"📦 <b>Бекап:</b>  {b_line}",
        "",
        f"🔄 {reboot_line}",
    ]

    if metrics.get("issues"):
        lines += ["", "⚠️ <b>Проблеми:</b>"] + [f"  • {i}" for i in metrics["issues"]]

    return send_message("\n".join(lines), config)


def send_photo(image_path: str, caption: str, config: dict) -> bool:
    """Відправляє зображення (графік)"""
    bot_token = config.get("TG_BOT_TOKEN")
    group_id = config.get("TG_GROUP_ID")
    topic_id = config.get("TG_TOPIC_ID")

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

    payload = {
        "chat_id": group_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if topic_id:
        payload["message_thread_id"] = int(topic_id)

    try:
        with open(image_path, "rb") as f:
            resp = requests.post(url, data=payload, files={"photo": f}, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Помилка відправки фото: {e}")
        return False
