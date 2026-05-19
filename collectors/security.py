"""
collectors/security.py — безпека: перебір паролів, нові адміни, зміни файлів
"""
import win32evtlog
import win32evtlogutil
import win32con
import hashlib
import os
import ipaddress
from datetime import datetime, timedelta
from collections import defaultdict
from storage import (get_known_admins, add_known_admin,
                     get_file_hash, update_file_hash)


def _get_events(source: str, event_ids: list, minutes: int = 10) -> list:
    """Читає події з Windows Event Log за останні N хвилин"""
    events = []
    try:
        hand = win32evtlog.OpenEventLog(None, source)
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        cutoff = datetime.now() - timedelta(minutes=minutes)

        while True:
            records = win32evtlog.ReadEventLog(hand, flags, 0)
            if not records:
                break
            for rec in records:
                try:
                    event_time = datetime(*rec.TimeGenerated.timetuple()[:6])
                    if event_time < cutoff:
                        raise StopIteration
                    if rec.EventID & 0xFFFF in event_ids:
                        events.append(rec)
                except StopIteration:
                    win32evtlog.CloseEventLog(hand)
                    return events
        win32evtlog.CloseEventLog(hand)
    except Exception as e:
        pass
    return events


def collect_brute_force(config: dict) -> dict:
    """Event ID 4625 — невдалі спроби входу"""
    window_min = int(config.get("BRUTE_FORCE_WINDOW_MIN", 5))
    threshold = int(config.get("BRUTE_FORCE_THRESHOLD", 5))
    known_networks_str = config.get("KNOWN_IPS", "192.168.1.0/24")

    known_networks = []
    for net in known_networks_str.split(","):
        try:
            known_networks.append(ipaddress.ip_network(net.strip(), strict=False))
        except Exception:
            pass

    events = _get_events("Security", [4625], minutes=window_min)
    ip_attempts = defaultdict(list)
    user_attempts = defaultdict(int)

    for rec in events:
        try:
            strings = rec.StringInserts
            if strings and len(strings) > 19:
                ip = strings[19].strip() if strings[19] else "unknown"
                username = strings[5].strip() if strings[5] else "unknown"
                ip_attempts[ip].append(username)
                user_attempts[username] += 1
        except Exception:
            pass

    alerts = []
    suspicious_ips = []  # всі IP з будь-якими невдалими спробами (для кнопки блокування)

    for ip, users in ip_attempts.items():
        if ip in ("unknown", "", "-"):
            continue
        count = len(users)
        is_known = False
        try:
            ip_obj = ipaddress.ip_address(ip)
            is_known = any(ip_obj in net for net in known_networks)
        except Exception:
            pass

        entry = {
            "ip": ip,
            "count": count,
            "usernames": list(set(users))[:5],
            "is_known_network": is_known,
        }

        if count >= threshold:
            alerts.append(entry)

        if not is_known:
            suspicious_ips.append(entry)

    # Сортуємо за кількістю спроб
    suspicious_ips.sort(key=lambda x: x["count"], reverse=True)

    return {
        "brute_force_alerts": alerts,
        "suspicious_ips": suspicious_ips[:5],
        "total_failed_logins": len(events),
        "window_min": window_min,
    }


def collect_new_admins(config: dict) -> dict:
    """Event ID 4732 — додавання до групи Administrators"""
    events = _get_events("Security", [4732], minutes=int(config.get("CHECK_INTERVAL_SEC", 60)) // 60 + 1)
    known = get_known_admins()
    new_admins = []

    for rec in events:
        try:
            strings = rec.StringInserts
            if strings and len(strings) > 2:
                added_user = strings[0].strip()
                added_by = strings[4].strip() if len(strings) > 4 else "unknown"
                group = strings[2].strip() if len(strings) > 2 else ""

                if "Administrator" in group or "Администрат" in group:
                    if added_user not in known:
                        new_admins.append({
                            "username": added_user,
                            "added_by": added_by,
                            "group": group,
                            "time": str(rec.TimeGenerated),
                        })
                        add_known_admin(added_user)
        except Exception:
            pass

    # Також перевіряємо поточних адмінів при першому запуску
    if not known:
        _bootstrap_known_admins()

    return {"new_admins": new_admins}


def _bootstrap_known_admins() -> None:
    """
    На першому запуску додаємо поточних адмінів у "відомі" щоб уникнути
    false-positive алертів. Парсимо `net localgroup Administrators` обережно —
    пропускаємо заголовки, рамки і службові рядки на en/ru локалях.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["net", "localgroup", "Administrators"],
            capture_output=True, text=True, encoding="cp866", timeout=15,
        )
    except Exception as e:
        # Російська локаль може мати іншу назву групи
        try:
            result = subprocess.run(
                ["net", "localgroup", "Администраторы"],
                capture_output=True, text=True, encoding="cp866", timeout=15,
            )
        except Exception as e2:
            return

    _SKIP_PREFIXES = (
        "alias name", "comment", "members",     # en
        "псевдоним", "комментарий", "члены",    # ru
    )
    _SKIP_KEYWORDS = (
        "completed successfully",   # en
        "выполнена успешно",        # ru
    )

    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("-"):
            continue
        low = line.lower()
        if any(low.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if any(kw in low for kw in _SKIP_KEYWORDS):
            continue

        # Доменні записи: "BUILTIN\Administrators" — беремо лише після останнього '\'
        if "\\" in line:
            line = line.rsplit("\\", 1)[-1]

        if line:
            add_known_admin(line)


def collect_file_changes(config: dict) -> dict:
    """Перевіряє хеші критичних файлів"""
    watch_files = [f.strip() for f in config.get(
        "WATCH_FILES",
        r"C:\Windows\System32\drivers\etc\hosts"
    ).split(",")]

    changed_files = []

    for file_path in watch_files:
        if not os.path.exists(file_path):
            continue
        try:
            with open(file_path, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()

            stored_hash = get_file_hash(file_path)

            if stored_hash is None:
                # Перший запуск — запам'ятовуємо
                update_file_hash(file_path, current_hash)
            elif stored_hash != current_hash:
                changed_files.append({
                    "path": file_path,
                    "old_hash": stored_hash[:16] + "...",
                    "new_hash": current_hash[:16] + "...",
                    "size_bytes": os.path.getsize(file_path),
                    "modified": datetime.fromtimestamp(
                        os.path.getmtime(file_path)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                })
                update_file_hash(file_path, current_hash)
        except Exception as e:
            pass

    return {"changed_files": changed_files}


def collect(config: dict) -> dict:
    result = {}
    result.update(collect_brute_force(config))
    result.update(collect_new_admins(config))
    result.update(collect_file_changes(config))
    return result
