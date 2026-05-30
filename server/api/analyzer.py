"""
analyzer.py — перенесений з SX_Monitoring без змін.
Отримує метрики → повертає рішення про алерт.
"""
import json
from typing import Optional
from datetime import datetime

SYSTEM_PROMPT = """Ти — експерт з моніторингу Windows серверів для 1С.
Аналізуй метрики і вирішуй чи потрібен алерт. Будь лаконічним.

Правила:
- Не спамь: некритична стабільна ситуація — не слати
- Пріоритет: безпека > сервіси > диски > CPU/RAM
- Бекап: алерт тільки якщо дійсно пропущено
- RDP новий IP вночі = завжди алерт
- Перебір паролів: алерт ТІЛЬКИ якщо є секція "=== ПЕРЕБІР ПАРОЛІВ ===" з конкретним IP
- Локальні логіни сервісних акаунтів (без IP) — норма, НЕ алерт

Формат відповіді — ТІЛЬКИ JSON:
{
  "should_alert": true/false,
  "severity": "critical|warning|info",
  "tags": ["#tag1"],
  "title": "До 6 слів",
  "analysis": "1 речення з фактами",
  "alert_key": "стабільний_ключ_без_часу"
}"""


def analyze(metrics: dict, config: dict) -> Optional[dict]:
    if not _has_anything_notable(metrics, config):
        return None

    stable_key = _stable_alert_key(metrics, config)
    api_key    = config.get("OPENAI_API_KEY")

    if not api_key:
        return _fallback_rules(metrics, config, stable_key)

    try:
        from openai import OpenAI
        client   = OpenAI(api_key=api_key)
        context  = _build_context(metrics, config)
        response = client.chat.completions.create(
            model=config.get("OPENAI_MODEL", "gpt-4o-mini"),
            max_tokens=200,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": context},
            ],
        )
        content = response.choices[0].message.content.strip()
        content = content.replace("```json", "").replace("```", "").strip()
        result  = json.loads(content)
        result["alert_key"] = stable_key
        return result
    except Exception:
        return _fallback_rules(metrics, config, stable_key)


def _has_anything_notable(metrics: dict, config: dict) -> bool:
    if metrics.get("brute_force_alerts"): return True
    if metrics.get("new_ip_alerts"):      return True
    if metrics.get("new_admins"):         return True
    if metrics.get("changed_files"):      return True
    if metrics.get("new_usb_devices"):    return True
    if metrics.get("new_software"):       return True
    if metrics.get("new_scheduled_tasks"): return True
    if metrics.get("newly_stopped"):      return True

    warn_pct = float(config.get("DISK_WARNING_PERCENT", 10))
    for d in metrics.get("disks", []):
        if d.get("free_pct", 100) < warn_pct:
            return True

    cpu_warn = float(config.get("CPU_WARNING_PERCENT", 85))
    ram_warn = float(config.get("RAM_WARNING_PERCENT", 90))
    if metrics.get("cpu", {}).get("percent", 0) > cpu_warn: return True
    if metrics.get("ram", {}).get("percent", 0) > ram_warn: return True

    for svc in metrics.get("services", []):
        if not svc.get("is_running"): return True

    if metrics.get("status") in ("warning", "error", "critical"): return True
    return False


def _stable_alert_key(metrics: dict, config: dict) -> str:
    bf = [a for a in metrics.get("brute_force_alerts", []) if a.get("ip")]
    if bf:
        return f"brute_{bf[0]['ip']}"

    if metrics.get("new_ip_alerts"):       return "rdp_new_ip"
    if metrics.get("new_admins"):          return "new_admin"
    if metrics.get("changed_files"):       return "file_changed"
    if metrics.get("new_usb_devices"):     return "new_usb"
    if metrics.get("new_software"):        return "new_software"
    if metrics.get("new_scheduled_tasks"): return "new_schtask"

    newly_stopped = metrics.get("newly_stopped", [])
    if newly_stopped:
        return f"service_stopped_{newly_stopped[0]}"

    for svc in metrics.get("services", []):
        if not svc.get("is_running"):
            return f"service_down_{svc['name']}"

    crit_pct = float(config.get("DISK_CRITICAL_PERCENT", 5))
    warn_pct = float(config.get("DISK_WARNING_PERCENT", 10))
    for d in metrics.get("disks", []):
        free = d.get("free_pct", 100)
        path = d.get("path", "?").rstrip("\\").replace(":", "")
        if free < warn_pct:
            band     = max(0, int(free // 2) * 2)
            severity = "critical" if free < crit_pct else "warning"
            return f"disk_{path}_{severity}_b{band}"

    cpu_warn = float(config.get("CPU_WARNING_PERCENT", 85))
    ram_warn = float(config.get("RAM_WARNING_PERCENT", 90))
    if metrics.get("cpu", {}).get("percent", 0) > cpu_warn: return "cpu_high"
    if metrics.get("ram", {}).get("percent", 0) > ram_warn: return "ram_high"

    backup_status = metrics.get("status")
    if backup_status == "critical": return "backup_critical"
    if backup_status == "warning":  return "backup_warning"

    return "generic"


def _build_context(metrics: dict, config: dict) -> str:
    now  = datetime.now()
    hour = now.hour
    time_ctx = ("нічний час" if hour < 7 else "ранок" if hour < 12
                else "день" if hour < 18 else "вечір")

    lines = [
        f"Сервер: {config.get('COMPANY_NAME', config.get('SERVER_ID', '?'))}",
        f"Час: {now.strftime('%H:%M')} ({time_ctx})",
        "",
    ]

    if "disks" in metrics:
        lines.append("=== ДИСКИ ===")
        for d in metrics["disks"]:
            delta = f", δ1г: {d['delta_1h']:+.1f}%" if d.get("delta_1h") is not None else ""
            lines.append(f"Диск {d['path']}: {d.get('free_pct')}% ({d.get('free_gb')}GB){delta}")

    if "cpu" in metrics:
        lines.append(f"\nCPU: {metrics['cpu']['percent']}% | RAM: {metrics['ram']['percent']}%")

    real_bf = [a for a in metrics.get("brute_force_alerts", []) if a.get("ip")]
    if real_bf:
        lines.append("\n=== ПЕРЕБІР ПАРОЛІВ ===")
        for a in real_bf:
            known = "відома мережа" if a["is_known_network"] else "НЕВІДОМИЙ IP"
            lines.append(f"IP {a['ip']} ({known}): {a['count']} спроб")

    if metrics.get("new_ip_alerts"):
        lines.append("\n=== RDP НОВІ IP ===")
        for login in metrics["new_ip_alerts"]:
            lines.append(f"IP: {login['ip']}, юзер: {login['username']}")

    if "status" in metrics and "latest_file" in metrics:
        lines.append("\n=== БЕКАПИ ===")
        lines.append(f"Статус: {metrics['status']}, "
                     f"{metrics.get('latest_file')}, "
                     f"{metrics.get('latest_age_hours')}г тому")
        if metrics.get("issues"):
            lines.append(f"Проблеми: {'; '.join(metrics['issues'])}")

    lines.append("\nПоверни JSON.")
    return "\n".join(lines)


def _fallback_rules(metrics: dict, config: dict, stable_key: str = None) -> Optional[dict]:
    alerts = []
    warn_pct = float(config.get("DISK_WARNING_PERCENT", 10))
    crit_pct = float(config.get("DISK_CRITICAL_PERCENT", 5))

    for d in metrics.get("disks", []):
        if "free_pct" in d:
            if d["free_pct"] < crit_pct:
                alerts.append(("critical", f"Диск {d['path']}: критично {d['free_pct']}%", ["#critical", "#disk"]))
            elif d["free_pct"] < warn_pct:
                alerts.append(("warning", f"Диск {d['path']}: мало місця {d['free_pct']}%", ["#warning", "#disk"]))

    for bf in metrics.get("brute_force_alerts", []):
        if bf.get("ip") and not bf["is_known_network"]:
            alerts.append(("critical", f"Перебір паролів {bf['ip']}: {bf['count']} спроб",
                           ["#critical", "#brute_force", "#security"]))

    if metrics.get("new_admins"):
        alerts.append(("critical", "Новий адміністратор доданий", ["#critical", "#security", "#admin"]))

    if metrics.get("new_ip_alerts"):
        ip = metrics["new_ip_alerts"][0]["ip"]
        alerts.append(("warning", f"RDP: новий IP {ip}", ["#warning", "#rdp", "#new_ip"]))

    for svc_name in metrics.get("newly_stopped", []):
        alerts.append(("critical", f"Сервіс зупинився: {svc_name}", ["#critical", "#service"]))

    if metrics.get("status") == "critical":
        alerts.append(("critical", f"Критична проблема з бекапом", ["#critical", "#backup"]))
    elif metrics.get("status") == "warning":
        alerts.append(("warning", f"Проблема з бекапом", ["#warning", "#backup"]))

    if not alerts:
        return None

    critical = [a for a in alerts if a[0] == "critical"]
    chosen   = critical[0] if critical else alerts[0]

    if stable_key is None:
        stable_key = _stable_alert_key(metrics, config)

    return {
        "should_alert": True,
        "severity":     chosen[0],
        "tags":         chosen[2],
        "title":        chosen[1],
        "analysis":     chosen[1],
        "alert_key":    stable_key,
    }
