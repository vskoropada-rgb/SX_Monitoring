"""
collectors/rdp.py — моніторинг RDP сесій
"""
import subprocess
import re
import win32evtlog
from datetime import datetime, timedelta
from storage import is_known_ip, register_ip


def get_active_sessions() -> list:
    """Отримує активні RDP сесії через qwinsta"""
    sessions = []
    try:
        result = subprocess.run(
            ["qwinsta"],
            capture_output=True, text=True, encoding="cp866"
        )
        lines = result.stdout.splitlines()
        for line in lines[1:]:  # Пропускаємо заголовок
            parts = line.split()
            if len(parts) >= 3 and parts[0] not in ("console", "services", "rdp-tcp"):
                try:
                    session = {
                        "session_name": parts[0] if not parts[0].isdigit() else "",
                        "username": parts[1] if len(parts) > 1 else "unknown",
                        "session_id": parts[2] if len(parts) > 2 else "",
                        "state": parts[3] if len(parts) > 3 else "",
                        "type": parts[4] if len(parts) > 4 else "",
                    }
                    # Фільтруємо тільки реальні сесії
                    if session["state"] in ("Active", "Disc", "Activ"):
                        sessions.append(session)
                except Exception:
                    pass
    except Exception as e:
        pass
    return sessions


def get_session_ips() -> dict:
    """Отримує IP адреси для сесій через netstat"""
    session_ips = {}
    try:
        result = subprocess.run(
            ["netstat", "-n"],
            capture_output=True, text=True, encoding="cp866"
        )
        for line in result.stdout.splitlines():
            # RDP порт 3389
            if ":3389" in line and "ESTABLISHED" in line:
                parts = line.split()
                if len(parts) >= 3:
                    remote = parts[2]
                    ip = remote.rsplit(":", 1)[0].strip("[]")
                    session_ips[ip] = True
    except Exception:
        pass
    return session_ips


def get_recent_rdp_logins(minutes: int = 60) -> list:
    """Event ID 4624 — успішні входи по RDP (LogonType=10)"""
    logins = []
    try:
        hand = win32evtlog.OpenEventLog(None, "Security")
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

                    if (rec.EventID & 0xFFFF) == 4624:
                        strings = rec.StringInserts
                        if strings and len(strings) > 18:
                            logon_type = strings[8].strip() if len(strings) > 8 else ""
                            if logon_type == "10":  # RemoteInteractive = RDP
                                username = strings[5].strip()
                                ip = strings[18].strip() if len(strings) > 18 else "unknown"
                                logins.append({
                                    "username": username,
                                    "ip": ip,
                                    "time": event_time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "is_new_ip": not is_known_ip(ip),
                                })
                                if ip and ip != "-":
                                    register_ip(ip, username)
                except StopIteration:
                    win32evtlog.CloseEventLog(hand)
                    return logins
                except Exception:
                    pass

        win32evtlog.CloseEventLog(hand)
    except Exception:
        pass
    return logins


def collect(config: dict) -> dict:
    active_sessions = get_active_sessions()
    session_ips = get_session_ips()
    recent_logins = get_recent_rdp_logins(
        minutes=int(config.get("CHECK_INTERVAL_SEC", 60)) // 60 + 2
    )

    # Нові IP — тільки невідомі
    new_ip_alerts = [l for l in recent_logins if l["is_new_ip"] and l["ip"] not in ("", "-", "unknown")]

    return {
        "active_sessions": active_sessions,
        "active_ips": list(session_ips.keys()),
        "recent_logins": recent_logins,
        "new_ip_alerts": new_ip_alerts,
        "session_count": len(active_sessions),
    }
