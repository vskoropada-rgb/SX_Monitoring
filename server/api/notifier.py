"""
notifier.py — відправка повідомлень в Telegram.
Перенесений з SX_Monitoring, без змін у логіці.
"""
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

SEVERITY_ICONS = {"critical": "🔴", "warning": "⚠️", "info": "ℹ️"}


def send_alert(decision: dict, metrics: dict, config: dict) -> bool:
    icon    = SEVERITY_ICONS.get(decision.get("severity", "info"), "ℹ️")
    title   = decision.get("title", "Подія на сервері")
    tags    = decision.get("tags", [])
    analysis = decision.get("analysis", "")
    now     = datetime.now().strftime("%H:%M")

    lines = [f"{icon} <b>{title}</b>  {now}"]
    if analysis:
        lines.append(analysis)

    raw_suspects = metrics.get("brute_force_alerts") or metrics.get("suspicious_ips") or []
    for entry in [e for e in raw_suspects if e.get("ip")][:3]:
        ip  = entry["ip"]
        geo = _ip_geo(ip)
        geo_str = f" [{geo}]" if geo else ""
        lines.append(f"🔑 {ip}{geo_str} — {entry['count']} спроб")

    is_security = any(t in tags for t in ("#brute_force", "#new_ip", "#admin", "#files"))
    if not is_security:
        block = _metrics_block(metrics)
        if block:
            lines.append(block)

    keyboard = _build_keyboard(decision, config, metrics)
    return _send_message(config, "\n".join(lines), keyboard)


def send_daily_report(metrics: dict, config: dict, pending_alerts: list = None) -> bool:
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
    b_icon   = "🔴" if b_status == "critical" else "⚠️" if b_status == "warning" else "✅"
    b_line   = (f"{b_icon} {metrics.get('latest_file', 'н/д')}  "
                f"{metrics.get('latest_size_mb', '?')} MB  "
                f"{metrics.get('latest_age_hours', '?')}г тому")

    reboot_line = ("⚠️ Очікує перезавантаження!"
                   if metrics.get("reboot_required")
                   else "✅ Перезавантаження не потрібне")

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
            sev_icon   = SEVERITY_ICONS.get(p.get("severity", "warning"), "⚠️")
            count_str  = f" (×{p['count']})" if p.get("count", 1) > 1 else ""
            lines.append(f"  {sev_icon} {p['title']}{count_str}")
            body = (p.get("body") or "").strip()
            if body:
                lines.append(f"     <i>{body}</i>")

    return _send_message(config, "\n".join(lines))


def send_message(text: str, config: dict, keyboard=None) -> bool:
    return _send_message(config, text, keyboard)


def _metrics_block(metrics: dict) -> str:
    parts = []
    for d in metrics.get("disks", []):
        if "free_pct" in d:
            delta = f" ↓{abs(d['delta_1h']):.1f}%/г" if d.get("delta_1h", 0) < 0 else ""
            parts.append(f"💾 {d['path']} {d['free_pct']}% ({d['free_gb']}GB){delta}")
    if "cpu" in metrics:
        parts.append(f"CPU {metrics['cpu']['percent']}% · RAM {metrics['ram']['percent']}%")
    stopped = [s["name"] for s in metrics.get("services", []) if not s["is_running"]]
    if stopped:
        parts.append("❌ " + ", ".join(stopped))
    return "\n".join(parts)


def _ip_geo(ip: str) -> str:
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=country,city,status", timeout=2)
        d = r.json()
        if d.get("status") == "success":
            return ", ".join(p for p in [d.get("country", ""), d.get("city", "")] if p)
    except Exception:
        pass
    return ""


def _build_keyboard(decision: dict, config: dict, metrics: dict = None) -> dict:
    tags      = decision.get("tags", [])
    server_id = config.get("SERVER_ID", "server")
    metrics   = metrics or {}

    row1 = [
        {"text": "📊 Статус", "callback_data": f"status_{server_id}"},
        {"text": "👥 Сесії",  "callback_data": f"sessions_{server_id}"},
    ]
    row2 = []

    if "#service" in tags:
        row2.append({"text": "🔄 Перезапустити", "callback_data": f"restart_service_{server_id}"})
    if "#rdp" in tags or "#new_ip" in tags:
        row2.append({"text": "🔒 Завершити сесію", "callback_data": f"kill_session_{server_id}"})
    if "#disk" in tags:
        row2.append({"text": "💾 Деталі диску", "callback_data": f"disk_{server_id}"})

    block_ips = []
    if "#brute_force" in tags or "#new_ip" in tags or "#security" in tags:
        for a in metrics.get("brute_force_alerts", [])[:3]:
            if a.get("ip"):
                block_ips.append(a["ip"])
        if not block_ips:
            for a in metrics.get("new_ip_alerts", [])[:2]:
                block_ips.append(a["ip"])
    for ip in block_ips:
        row2.append({"text": f"🚫 {ip}", "callback_data": f"block_confirm_{ip}_{server_id}"})

    buttons = [row1]
    if row2:
        # по 2 кнопки в рядок
        for i in range(0, len(row2), 2):
            buttons.append(row2[i:i+2])

    return {"inline_keyboard": buttons}


def _send_message(config: dict, text: str, keyboard=None) -> bool:
    token    = config.get("TG_BOT_TOKEN")
    group_id = config.get("TG_GROUP_ID")
    topic_id = config.get("TG_TOPIC_ID")

    payload = {
        "chat_id":                  group_id,
        "text":                     text,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    if topic_id:
        payload["message_thread_id"] = int(topic_id)
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)

    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json=payload, timeout=10)
        if r.status_code != 200:
            logger.error("Telegram error: %s — %s", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        logger.error("send_message failed: %s", e)
        return False
