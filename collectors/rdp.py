"""
collectors/rdp.py — моніторинг RDP сесій + нові IP через Event Log
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta
from typing import List

import actions
from storage import is_known_ip, register_ip

logger = logging.getLogger(__name__)


def get_active_sessions() -> List[dict]:
    """Активні RDP-сесії через qwinsta (використовує parser з actions.py)."""
    return actions.get_sessions()


def get_session_ips() -> dict:
    """Мапа IP→True для встановлених підключень на порт 3389 (netstat)."""
    ips: dict = {}
    try:
        result = subprocess.run(
            ["netstat", "-n"],
            capture_output=True, text=True,
            encoding="cp866", timeout=10,
        )
    except Exception as e:
        logger.error("netstat failed: %s", e)
        return ips

    for line in result.stdout.splitlines():
        if ":3389" not in line or "ESTABLISHED" not in line:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        remote = parts[2]
        ip = remote.rsplit(":", 1)[0].strip("[]")
        if ip:
            ips[ip] = True
    return ips


def get_recent_rdp_logins(minutes: int = 60) -> List[dict]:
    """Event ID 4624 LogonType=10 — успішні RDP-входи за останні N хвилин."""
    try:
        import win32evtlog
    except ImportError:
        logger.warning("pywin32 недоступний — RDP логіни пропущено")
        return []

    logins: List[dict] = []
    try:
        hand = win32evtlog.OpenEventLog(None, "Security")
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        cutoff = datetime.now() - timedelta(minutes=minutes)
    except Exception as e:
        logger.error("OpenEventLog failed: %s", e)
        return logins

    try:
        while True:
            records = win32evtlog.ReadEventLog(hand, flags, 0)
            if not records:
                break
            for rec in records:
                try:
                    event_time = datetime(*rec.TimeGenerated.timetuple()[:6])
                    if event_time < cutoff:
                        return logins  # читали з кінця, далі тільки старіші
                    if (rec.EventID & 0xFFFF) != 4624:
                        continue

                    strings = rec.StringInserts or []
                    # LogonType — індекс 8 (стандартний layout 4624)
                    if len(strings) <= 8:
                        continue
                    logon_type = (strings[8] or "").strip()
                    if logon_type != "10":  # 10 = RemoteInteractive (RDP)
                        continue

                    username = (strings[5] or "").strip() if len(strings) > 5 else ""
                    ip = (strings[18] or "").strip() if len(strings) > 18 else "unknown"

                    if not username or not ip:
                        continue

                    logins.append({
                        "username": username,
                        "ip":       ip,
                        "time":     event_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "is_new_ip": not is_known_ip(ip),
                    })
                    if ip not in ("", "-", "unknown"):
                        register_ip(ip, username)
                except Exception as e:
                    logger.debug("Skip event: %s", e)
    finally:
        try:
            win32evtlog.CloseEventLog(hand)
        except Exception:
            pass

    return logins


def collect(config: dict) -> dict:
    active = get_active_sessions()
    ips = get_session_ips()
    minutes = max(2, int(config.get("CHECK_INTERVAL_SEC", 60)) // 60 + 2)
    recent = get_recent_rdp_logins(minutes=minutes)

    new_ip_alerts = [
        l for l in recent
        if l["is_new_ip"] and l["ip"] not in ("", "-", "unknown")
    ]

    return {
        "active_sessions": active,
        "active_ips":      list(ips.keys()),
        "recent_logins":   recent,
        "new_ip_alerts":   new_ip_alerts,
        "session_count":   len(active),
    }
