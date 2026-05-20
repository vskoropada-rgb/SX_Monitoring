"""
notifier.py — відправка повідомлень в Telegram
"""
import requests
import logging
from typing import List
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
    tags_list = decision.get("tags", [])
    title = decision.get("title", "Подія на сервері")
    analysis = decision.get("analysis", "")
    now = datetime.now().strftime("%H:%M")

    lines = [f"{icon} <b>{title}</b>  {now}"]

    if analysis:
        lines.append(analysis)

    # Брутфорс — показуємо лише записи з реальним IP (без IP = локальні
    # сервісні акаунти, нічого не дають для дії)
    raw_suspects = metrics.get("brute_force_alerts") or metrics.get("suspicious_ips") or []
    all_suspects = [e for e in raw_suspects if e.get("ip")]
    for entry in all_suspects[:3]:
        ip = entry["ip"]
        users = ", ".join(entry.get("usernames", [])[:3]) or "?"
        geo = _ip_geo(ip)
        geo_str = f" [{geo}]" if geo else ""
        lines.append(f"🔑 {ip}{geo_str} — {entry['count']} спроб ({users})")

    # Метрики — тільки для не-security алертів, коротко
    is_security = any(t in tags_list for t in ("#brute_force", "#new_ip", "#admin", "#files", "#security"))
    if not is_security:
        metrics_block = _format_metrics_block(metrics)
        if metrics_block:
            lines.append(metrics_block)

    text = "\n".join(lines)

    # Кнопки дій
    keyboard = _build_keyboard(decision, config, metrics)

    return _send_message(bot_token, group_id, topic_id, text, keyboard)


def send_message(text: str, config: dict, keyboard=None) -> bool:
    """Відправляє довільне повідомлення"""
    bot_token = config.get("TG_BOT_TOKEN")
    group_id = config.get("TG_GROUP_ID")
    topic_id = config.get("TG_TOPIC_ID")
    return _send_message(bot_token, group_id, topic_id, text, keyboard)


def _format_metrics_block(metrics: dict) -> str:
    parts = []

    for d in metrics.get("disks", []):
        if "free_pct" in d:
            delta = f" ↓{abs(d['delta_1h']):.1f}%/г" if d.get("delta_1h") and d["delta_1h"] < 0 else ""
            parts.append(f"💾 {d['path']} {d['free_pct']}% ({d['free_gb']}GB){delta}")

    if "cpu" in metrics:
        parts.append(f"CPU {metrics['cpu']['percent']}% · RAM {metrics['ram']['percent']}%")

    stopped = [s["name"] for s in metrics.get("services", []) if not s["is_running"]]
    if stopped:
        parts.append("❌ " + ", ".join(stopped))

    return "\n".join(parts)


def _ip_geo(ip: str) -> str:
    """Повертає 'Країна, місто' або '' якщо недоступно. Timeout 2с."""
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=country,city,status",
                         timeout=2)
        d = r.json()
        if d.get("status") == "success":
            parts = [d.get("country", ""), d.get("city", "")]
            return ", ".join(p for p in parts if p)
    except Exception:
        pass
    return ""


def _extract_block_ips(decision: dict, metrics: dict) -> List[str]:
    """Повертає список IP які треба запропонувати заблокувати."""
    tags = decision.get("tags", [])
    ips = []

    if "#brute_force" in tags or "#security" in tags:
        # Спочатку alerts (вище порогу), потім suspicious_ips (будь-які невідомі)
        alerts = [a for a in metrics.get("brute_force_alerts", []) if a.get("ip")]
        unknown = [a for a in alerts if not a.get("is_known_network")]
        source = unknown if unknown else alerts
        for a in sorted(source, key=lambda x: x["count"], reverse=True)[:3]:
            ips.append(a["ip"])

        # Якщо brute_force_alerts порожній — беремо з suspicious_ips
        if not ips:
            for s in metrics.get("suspicious_ips", [])[:3]:
                if s.get("ip") and s["ip"] not in ips:
                    ips.append(s["ip"])

    if "#new_ip" in tags or "#rdp" in tags:
        for a in metrics.get("new_ip_alerts", [])[:2]:
            if a["ip"] not in ips:
                ips.append(a["ip"])

    # Fallback: парсимо alert_key
    if not ips:
        ak = decision.get("alert_key", "")
        for prefix in ("brute_", "rdp_new_"):
            if ak.startswith(prefix):
                ips.append(ak[len(prefix):])
                break

    return ips


def _build_keyboard(decision: dict, config: dict, metrics: dict = None) -> dict:
    """Будує inline клавіатуру залежно від типу алерту"""
    tags = decision.get("tags", [])
    server_id = config.get("SERVER_ID", "server")
    if metrics is None:
        metrics = {}

    row1 = [
        {"text": "📊 Статус", "callback_data": f"status_{server_id}"},
        {"text": "👥 Сесії",  "callback_data": f"sessions_{server_id}"},
    ]
    row2 = []

    if "#service" in tags:
        row2.append({"text": "🔄 Перезапустити сервіс", "callback_data": f"restart_service_{server_id}"})

    if "#rdp" in tags or "#new_ip" in tags:
        row2.append({"text": "🔒 Завершити сесію", "callback_data": f"kill_session_{server_id}"})

    if "#disk" in tags:
        row2.append({"text": "💾 Деталі диску", "callback_data": f"disk_{server_id}"})

    # Кнопки блокування — по одній на IP (до 3 штук), по 2 в рядок
    block_ips = _extract_block_ips(decision, metrics) if (
        "#brute_force" in tags or "#new_ip" in tags or "#security" in tags
    ) else []
    block_btns = [{"text": f"🚫 {ip}", "callback_data": f"block_confirm_{ip}"} for ip in block_ips]
    for i in range(0, len(block_btns), 2):
        row2 += block_btns[i:i+2]

    buttons = [row1]
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


def send_daily_report(metrics: dict, config: dict, pending_alerts: list = None) -> bool:
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

    b_status = metrics.get("status", "")
    b_icon = "🔴" if b_status == "critical" else "⚠️" if b_status == "warning" else "✅"
    b_line = (
        f"{b_icon} {metrics.get('latest_file', 'н/д')}  "
        f"{metrics.get('latest_size_mb', '?')} MB  "
        f"{metrics.get('latest_age_hours', '?')}г тому"
    )

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

    if pending_alerts:
        lines += ["", "📋 <b>Накопичені сповіщення за добу:</b>"]
        for p in pending_alerts:
            sev_icon = SEVERITY_ICONS.get(p.get("severity", "warning"), "⚠️")
            count_str = f" (×{p['count']})" if p.get("count", 1) > 1 else ""
            lines.append(f"  {sev_icon} {p['title']}{count_str}")
            body = (p.get("body") or "").strip()
            if body:
                lines.append(f"     <i>{body}</i>")

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
