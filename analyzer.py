"""
analyzer.py — GPT-4o-mini аналіз метрик і прийняття рішень
"""
import json
import os
from typing import Optional
from openai import OpenAI
from datetime import datetime

SYSTEM_PROMPT = """Ти — експерт з моніторингу Windows серверів для 1С.
Аналізуй метрики і вирішуй чи потрібен алерт. Будь лаконічним.

Правила:
- Не спамь: некритична стабільна ситуація — не слати
- Пріоритет: безпека > сервіси > диски > CPU/RAM
- Бекап: алерт тільки якщо дійсно пропущено
- RDP новий IP вночі = завжди алерт
- Перебір паролів: алерт ТІЛЬКИ якщо в контексті є секція "=== ПЕРЕБІР ПАРОЛІВ ===" з конкретним IP і кількістю. БЕЗ цих даних — НЕ генеруй алерт про брутфорс, навіть якщо бачиш цифру "невдалих входів"
- Локальні логіни сервісних акаунтів (без IP) — норма для 1С серверів, НЕ алерт

Формат відповіді — ТІЛЬКИ JSON:
{
  "should_alert": true/false,
  "severity": "critical|warning|info",
  "tags": ["#tag1", "#tag2"],
  "title": "До 6 слів, конкретно",
  "analysis": "1 коротке речення з фактами (IP, кількість, ім'я)",
  "alert_key": "стабільний_ключ_категорії_без_часу_і_чисел"
}

alert_key — це СТАБІЛЬНИЙ ідентифікатор категорії проблеми. НЕ включай час, дати, IP або будь-які змінні дані.
Приклади правильних ключів: backup_corrupted, disk_C_critical, brute_force_unknown, service_stopped_1c, rdp_new_ip

Теги: #critical #warning #info #disk #cpu #ram #rdp #new_ip #brute_force #security #admin #files #backup #service #resolved
"""


def analyze(metrics: dict, config: dict) -> Optional[dict]:
    """
    Передає метрики GPT і отримує рішення.
    Повертає dict з рішенням або None якщо не треба слати.
    """
    # Якщо все в нормі — не витрачаємо токени GPT
    if not _has_anything_notable(metrics, config):
        return None

    # Детермінований ключ обчислюємо до GPT — GPT-відповідь може варіювати
    stable_key = _stable_alert_key(metrics, config)

    api_key = config.get("OPENAI_API_KEY")
    if not api_key:
        return _fallback_rules(metrics, config, stable_key)

    client = OpenAI(api_key=api_key)
    model = config.get("OPENAI_MODEL", "gpt-4o-mini")

    context = _build_context(metrics, config)

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=200,
            temperature=0.1,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context}
            ]
        )

        content = response.choices[0].message.content.strip()
        # Очищаємо можливі markdown backticks
        content = content.replace("```json", "").replace("```", "").strip()
        result = json.loads(content)
        # Завжди перекриваємо GPT-ключ детермінованим — GPT може варіювати ключ
        # і зламати cooldown-дедуплікацію
        result["alert_key"] = stable_key
        return result

    except Exception as e:
        # Якщо GPT недоступний — fallback на правила
        return _fallback_rules(metrics, config, stable_key)


def _build_context(metrics: dict, config: dict) -> str:
    now = datetime.now()
    hour = now.hour
    time_context = "нічний час" if 0 <= hour < 7 else "ранок" if 7 <= hour < 12 else "день" if 12 <= hour < 18 else "вечір"

    lines = [
        f"Сервер: {config.get('COMPANY_NAME', config.get('SERVER_ID', 'Unknown'))}",
        f"Час: {now.strftime('%H:%M')} ({time_context}), {now.strftime('%d.%m.%Y')}",
        "",
    ]

    # Диски
    if "disks" in metrics:
        lines.append("=== ДИСКИ ===")
        for d in metrics["disks"]:
            if "error" in d:
                lines.append(f"Диск {d['path']}: ПОМИЛКА — {d['error']}")
            else:
                delta = f", динаміка за 1г: {d['delta_1h']:+.1f}%" if d.get("delta_1h") is not None else ""
                lines.append(f"Диск {d['path']}: {d['free_pct']}% вільно ({d['free_gb']}GB з {d['total_gb']}GB){delta}")

    # CPU/RAM
    if "cpu" in metrics:
        lines.append("\n=== CPU / RAM ===")
        lines.append(f"CPU: {metrics['cpu']['percent']}% | RAM: {metrics['ram']['percent']}% (вільно {metrics['ram']['free_gb']}GB)")
        if metrics.get("top_processes"):
            top = metrics["top_processes"][:3]
            top_str = ", ".join([f"{p['name']}({p['cpu_pct']}%CPU)" for p in top])
            lines.append(f"Топ процеси: {top_str}")

    # Безпека — показуємо лише ЗОВНІШНІ (з IP) брутфорс-події
    real_bf = [a for a in metrics.get("brute_force_alerts", []) if a.get("ip")]
    if real_bf:
        lines.append("\n=== ПЕРЕБІР ПАРОЛІВ ===")
        for alert in real_bf:
            known = "відома мережа" if alert["is_known_network"] else "НЕВІДОМИЙ IP"
            lines.append(f"IP {alert['ip']} ({known}): {alert['count']} спроб, юзери: {', '.join(alert['usernames'][:3])}")
    # `total_failed_logins` і локальні логіни НЕ показуємо GPT — це шум від
    # сервісних акаунтів, який GPT інтерпретує як атаку (галюцинація).

    if "new_admins" in metrics and metrics["new_admins"]:
        lines.append("\n=== НОВІ АДМІНИ ===")
        for a in metrics["new_admins"]:
            lines.append(f"Додано {a['username']} до адмінів (ким: {a['added_by']})")

    if "changed_files" in metrics and metrics["changed_files"]:
        lines.append("\n=== ЗМІНИ ФАЙЛІВ ===")
        for f in metrics["changed_files"]:
            lines.append(f"Змінено: {f['path']} о {f['modified']}")

    # RDP
    if "new_ip_alerts" in metrics and metrics["new_ip_alerts"]:
        lines.append("\n=== RDP НОВІ IP ===")
        for login in metrics["new_ip_alerts"]:
            lines.append(f"Новий IP: {login['ip']}, юзер: {login['username']}, час: {login['time']}")

    if "active_sessions" in metrics:
        count = metrics.get("session_count", 0)
        lines.append(f"\nАктивних RDP сесій: {count}")

    # Сервіси
    if "services" in metrics:
        lines.append("\n=== СЕРВІСИ ===")
        for svc in metrics["services"]:
            icon = "✅" if svc["is_running"] else "❌"
            changed = " (ЩОЙНО ЗУПИНИВСЯ)" if svc.get("just_stopped") else ""
            lines.append(f"{icon} {svc['name']}: {svc['status']}{changed}")

    # Бекапи
    if "status" in metrics and "latest_file" in metrics:
        lines.append("\n=== БЕКАПИ ===")
        lines.append(f"Статус: {metrics['status']}")
        lines.append(f"Останній: {metrics.get('latest_file', 'н/д')}, {metrics.get('latest_age_hours')}г тому, {metrics.get('latest_size_mb')}MB")
        if metrics.get("issues"):
            lines.append(f"Проблеми: {'; '.join(metrics['issues'])}")

    lines.append("\nВизнач чи потрібно відправляти алерт і поверни JSON.")
    return "\n".join(lines)


def _stable_alert_key(metrics: dict, config: dict) -> str:
    """Детермінований alert_key з метрик — не залежить від GPT-відповіді."""
    # Пріоритет: безпека > сервіси > диски > ресурси > бекапи
    bf = metrics.get("brute_force_alerts", [])
    if bf:
        external = [a for a in bf if a.get("ip")]
        if external:
            return f"brute_{external[0]['ip']}"
        return "brute_force_local"

    if metrics.get("new_ip_alerts"):
        return "rdp_new_ip"

    if metrics.get("new_admins"):
        return "new_admin"

    if metrics.get("changed_files"):
        return "file_changed"

    if metrics.get("new_usb_devices"):
        return "new_usb"

    if metrics.get("new_software"):
        return "new_software"

    if metrics.get("new_scheduled_tasks"):
        return "new_schtask"

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
            # Бенд 2%: при кожному зниженні на 2% — новий ключ → новий алерт одразу
            # Приклад: 9%→b8, 7%→b6, 4%→b4_critical, 2%→b2_critical
            band = max(0, int(free // 2) * 2)
            severity = "critical" if free < crit_pct else "warning"
            return f"disk_{path}_{severity}_b{band}"

    cpu_warn = float(config.get("CPU_WARNING_PERCENT", 85))
    ram_warn = float(config.get("RAM_WARNING_PERCENT", 90))
    if metrics.get("cpu", {}).get("percent", 0) > cpu_warn:
        return "cpu_high"
    if metrics.get("ram", {}).get("percent", 0) > ram_warn:
        return "ram_high"

    backup_status = metrics.get("status")
    if backup_status in ("critical", "error"):
        return "backup_critical"
    if backup_status == "warning":
        return "backup_warning"

    return "generic"


def _has_anything_notable(metrics: dict, config: dict) -> bool:
    """Швидка перевірка без GPT — чи є взагалі щось варте аналізу"""

    # Безпека — завжди аналізуємо
    if metrics.get("brute_force_alerts"):
        return True
    if metrics.get("new_ip_alerts"):
        return True
    if metrics.get("new_admins"):
        return True
    if metrics.get("changed_files"):
        return True
    if metrics.get("new_usb_devices"):
        return True
    if metrics.get("new_software"):
        return True
    if metrics.get("new_scheduled_tasks"):
        return True

    # Диски
    warn_pct = float(config.get("DISK_WARNING_PERCENT", 10))
    for d in metrics.get("disks", []):
        if d.get("free_pct", 100) < warn_pct:
            return True

    # CPU / RAM
    cpu_warn = float(config.get("CPU_WARNING_PERCENT", 85))
    ram_warn = float(config.get("RAM_WARNING_PERCENT", 90))
    if metrics.get("cpu", {}).get("percent", 0) > cpu_warn:
        return True
    if metrics.get("ram", {}).get("percent", 0) > ram_warn:
        return True

    # Сервіси
    if metrics.get("newly_stopped"):
        return True
    for svc in metrics.get("services", []):
        if not svc.get("is_running"):
            return True

    # Бекапи
    if metrics.get("status") in ("warning", "error", "critical"):
        return True

    return False


def _fallback_rules(metrics: dict, config: dict, stable_key: str = None) -> Optional[dict]:
    """Резервна логіка якщо GPT недоступний"""
    alerts = []

    # Диски
    warn_pct = float(config.get("DISK_WARNING_PERCENT", 10))
    crit_pct = float(config.get("DISK_CRITICAL_PERCENT", 5))
    for d in metrics.get("disks", []):
        if "free_pct" in d:
            if d["free_pct"] < crit_pct:
                alerts.append(("critical", f"Диск {d['path']}: критично мало місця {d['free_pct']}%",
                               ["#critical", "#disk"]))
            elif d["free_pct"] < warn_pct:
                alerts.append(("warning", f"Диск {d['path']}: мало місця {d['free_pct']}%",
                               ["#warning", "#disk"]))

    # Перебір паролів
    for bf in metrics.get("brute_force_alerts", []):
        if not bf["is_known_network"]:
            ip_str = bf["ip"] or "локальний"
            alerts.append(("critical", f"Перебір паролів {ip_str}: {bf['count']} спроб",
                          ["#critical", "#brute_force", "#security"]))

    # Нові адміни
    if metrics.get("new_admins"):
        alerts.append(("critical", "Новий адміністратор доданий до системи",
                      ["#critical", "#security", "#admin"]))

    # Зміни файлів
    if metrics.get("changed_files"):
        alerts.append(("warning", f"Змінено критичний файл: {metrics['changed_files'][0]['path']}",
                      ["#warning", "#security", "#files"]))

    # Нові RDP IP
    if metrics.get("new_ip_alerts"):
        ip = metrics["new_ip_alerts"][0]["ip"]
        alerts.append(("warning", f"RDP: підключення з нового IP {ip}",
                      ["#warning", "#rdp", "#new_ip"]))

    # Сервіси зупинились
    for svc_name in metrics.get("newly_stopped", []):
        alerts.append(("critical", f"Сервіс зупинився: {svc_name}",
                      ["#critical", "#service"]))

    # Бекапи
    if metrics.get("status") == "critical":
        alerts.append(("critical", f"Критична проблема з бекапом: {', '.join(metrics.get('issues', []))}",
                      ["#critical", "#backup"]))
    elif metrics.get("status") == "warning":
        alerts.append(("warning", f"Проблема з бекапом: {', '.join(metrics.get('issues', []))}",
                      ["#warning", "#backup"]))

    if not alerts:
        return None

    # Беремо найважливіший алерт
    critical = [a for a in alerts if a[0] == "critical"]
    chosen = critical[0] if critical else alerts[0]

    if stable_key is None:
        stable_key = _stable_alert_key(metrics, config)

    return {
        "should_alert": True,
        "severity": chosen[0],
        "tags": chosen[2],
        "title": chosen[1],
        "analysis": chosen[1],
        "recommendation": "Перевірте сервер",
        "alert_key": stable_key,
    }
