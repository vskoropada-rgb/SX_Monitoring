"""
analyzer.py — GPT-4o-mini аналіз метрик і прийняття рішень
"""
import json
import os
from openai import OpenAI
from datetime import datetime

SYSTEM_PROMPT = """Ти — експерт з моніторингу Windows серверів для 1С.
Твоя задача: аналізувати метрики сервера і вирішувати чи потрібно відправляти алерт адміністратору.

Правила прийняття рішень:
- Враховуй КОНТЕКСТ: час доби, динаміку змін, комбінацію проблем
- Не спамь: якщо ситуація некритична і стабільна — не слати
- Пріоритизуй: безпека > сервіси > диски > CPU/RAM
- Для бекапів: алерт тільки якщо ДІЙСНО пропущено (враховуй час розкладу)
- RDP новий IP вночі = завжди алерт
- Перебір паролів з невідомих IP = завжди алерт

Формат відповіді — ТІЛЬКИ JSON:
{
  "should_alert": true/false,
  "severity": "critical|warning|info",
  "tags": ["#tag1", "#tag2"],
  "title": "Короткий заголовок",
  "analysis": "Детальний аналіз українською (2-4 речення)",
  "recommendation": "Конкретна рекомендація що робити",
  "alert_key": "унікальний ключ для дедуплікації"
}

Доступні теги: #critical #warning #info #disk #cpu #ram #rdp #new_ip #brute_force #security #admin #files #backup #service #resolved
"""


def analyze(metrics: dict, config: dict) -> dict | None:
    """
    Передає метрики GPT і отримує рішення.
    Повертає dict з рішенням або None якщо не треба слати.
    """
    api_key = config.get("OPENAI_API_KEY")
    if not api_key:
        return _fallback_rules(metrics, config)

    client = OpenAI(api_key=api_key)
    model = config.get("OPENAI_MODEL", "gpt-4o-mini")

    # Формуємо контекст для GPT
    context = _build_context(metrics, config)

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=500,
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
        return result

    except Exception as e:
        # Якщо GPT недоступний — fallback на правила
        return _fallback_rules(metrics, config)


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

    # Безпека
    if "brute_force_alerts" in metrics and metrics["brute_force_alerts"]:
        lines.append("\n=== ПЕРЕБІР ПАРОЛІВ ===")
        for alert in metrics["brute_force_alerts"]:
            known = "відома мережа" if alert["is_known_network"] else "НЕВІДОМИЙ IP"
            lines.append(f"IP {alert['ip']} ({known}): {alert['count']} спроб, юзери: {', '.join(alert['usernames'][:3])}")
    elif "total_failed_logins" in metrics:
        lines.append(f"\nНевдалих входів за {metrics.get('window_min', 5)}хв: {metrics['total_failed_logins']}")

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


def _fallback_rules(metrics: dict, config: dict) -> dict | None:
    """Резервна логіка якщо GPT недоступний"""
    alerts = []

    # Диски
    warn_pct = float(config.get("DISK_WARNING_PERCENT", 20))
    crit_pct = float(config.get("DISK_CRITICAL_PERCENT", 10))
    for d in metrics.get("disks", []):
        if "free_pct" in d:
            if d["free_pct"] < crit_pct:
                alerts.append(("critical", f"🔴 Диск {d['path']}: критично мало місця {d['free_pct']}%",
                               ["#critical", "#disk"], f"disk_{d['path']}_critical"))
            elif d["free_pct"] < warn_pct:
                alerts.append(("warning", f"⚠️ Диск {d['path']}: мало місця {d['free_pct']}%",
                               ["#warning", "#disk"], f"disk_{d['path']}_warning"))

    # Перебір паролів
    for bf in metrics.get("brute_force_alerts", []):
        if not bf["is_known_network"]:
            alerts.append(("critical", f"🔴 Перебір паролів з {bf['ip']}: {bf['count']} спроб",
                          ["#critical", "#brute_force", "#security"], f"brute_{bf['ip']}"))

    # Нові адміни
    if metrics.get("new_admins"):
        alerts.append(("critical", "🔴 Новий адміністратор доданий до системи",
                      ["#critical", "#security", "#admin"], "new_admin"))

    # Зміни файлів
    if metrics.get("changed_files"):
        alerts.append(("warning", f"⚠️ Змінено критичний файл: {metrics['changed_files'][0]['path']}",
                      ["#warning", "#security", "#files"], "file_changed"))

    # Нові RDP IP
    if metrics.get("new_ip_alerts"):
        ip = metrics["new_ip_alerts"][0]["ip"]
        alerts.append(("warning", f"⚠️ RDP: підключення з нового IP {ip}",
                      ["#warning", "#rdp", "#new_ip"], f"rdp_new_{ip}"))

    # Сервіси зупинились
    for svc_name in metrics.get("newly_stopped", []):
        alerts.append(("critical", f"🔴 Сервіс зупинився: {svc_name}",
                      ["#critical", "#service"], f"service_stopped_{svc_name}"))

    # Бекапи
    if metrics.get("status") == "warning":
        alerts.append(("warning", f"⚠️ Проблема з бекапом: {', '.join(metrics.get('issues', []))}",
                      ["#warning", "#backup"], "backup_issue"))

    if not alerts:
        return None

    # Беремо найважливіший алерт
    critical = [a for a in alerts if a[0] == "critical"]
    chosen = critical[0] if critical else alerts[0]

    return {
        "should_alert": True,
        "severity": chosen[0],
        "tags": chosen[2],
        "title": chosen[1],
        "analysis": chosen[1],
        "recommendation": "Перевірте сервер",
        "alert_key": chosen[3],
    }
