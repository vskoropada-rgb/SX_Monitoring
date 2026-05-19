"""
actions.py — дії на сервері: завершення сесій, сервіси, перезавантаження, firewall.

qwinsta парсер працює з англійським і російським Windows (не залежить від назв колонок).
"""
from __future__ import annotations

import logging
import subprocess
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Константи ───────────────────────────────────────────────

_FW_RULE_PREFIX = "1C_Monitor_Block_"

# Префікси станів qwinsta (en + ru, регістр не важливий)
_STATE_ACTIVE = ("activ", "акт", "conn", "конн")
_STATE_DISCONNECTED = ("disc", "откл")
_STATE_LISTENING = ("listen", "слуш", "lst", "down")

# Псевдо-сесії, які потрібно ігнорувати коли немає юзера
_SYSTEM_SESSION_NAMES = ("services", "rdp-tcp", "console")


# ─── qwinsta ─────────────────────────────────────────────────


def _parse_qwinsta_line(raw: str) -> Optional[dict]:
    """
    Парсить один рядок виводу qwinsta. Повертає dict або None.

    Стратегія: знаходимо токен з суто цифрами (це ID сесії) і відштовхуємось від нього.
    Все до ID — session_name + username, все після — state і службові поля.
    Це працює незалежно від мови заголовка (USERNAME / ПОЛЬЗОВАТЕЛЬ і т.п.).
    """
    if not raw or not raw.strip():
        return None

    # Прибираємо маркер поточної сесії '>' або провідні пробіли
    body = raw.lstrip(" >\t")
    tokens = body.split()
    if len(tokens) < 3:
        return None

    # Перший цілочисельний токен — це session ID
    id_idx = next(
        (i for i, t in enumerate(tokens) if t.isdigit()),
        None,
    )
    if id_idx is None or id_idx == 0:
        return None

    session_name = tokens[0]
    username = " ".join(tokens[1:id_idx]) if id_idx > 1 else ""
    session_id = tokens[id_idx]
    state = tokens[id_idx + 1] if id_idx + 1 < len(tokens) else ""

    return {
        "session_name": session_name,
        "username": username,
        "session_id": session_id,
        "state": state,
    }


def _is_active_state(state: str) -> bool:
    s = state.lower()
    return any(s.startswith(p) for p in _STATE_ACTIVE + _STATE_DISCONNECTED)


def _is_listening_state(state: str) -> bool:
    s = state.lower()
    return any(s.startswith(p) for p in _STATE_LISTENING)


def get_sessions() -> List[dict]:
    """Активні RDP/console сесії. Працює з en/ru Windows."""
    try:
        result = subprocess.run(
            ["qwinsta"],
            capture_output=True, text=True,
            encoding="cp866", timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.error("qwinsta failed: %s", e)
        return []

    sessions: List[dict] = []
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        logger.debug("qwinsta returned no data: %r", result.stdout[:200])
        return sessions

    # Пропускаємо заголовок (lines[0])
    for raw in lines[1:]:
        parsed = _parse_qwinsta_line(raw)
        if not parsed:
            continue

        # Ігноруємо listening-templates (rdp-tcp у стані Listen)
        if _is_listening_state(parsed["state"]):
            continue

        # Тільки активні/відключені сесії
        if not _is_active_state(parsed["state"]):
            continue

        # Системні сесії без юзера (services з ID=0)
        if not parsed["username"] and parsed["session_name"].lower() in _SYSTEM_SESSION_NAMES:
            continue

        sessions.append({
            "session_name": parsed["session_name"],
            "username":     parsed["username"] or parsed["session_name"],
            "session_id":   parsed["session_id"],
            "state":        parsed["state"],
        })

    logger.debug("qwinsta parsed %d sessions", len(sessions))
    return sessions


def kick_session(session_id: str) -> Tuple[bool, str]:
    """Завершує сесію по ID через logoff."""
    if not session_id or not session_id.isdigit():
        return False, f"Невірний session_id: {session_id!r}"
    try:
        result = subprocess.run(
            ["logoff", session_id, "/server:localhost"],
            capture_output=True, text=True, encoding="cp866", timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "Таймаут logoff"
    except Exception as e:
        return False, f"Виняток: {e}"

    if result.returncode == 0:
        logger.info("Сесія %s завершена", session_id)
        return True, f"Сесія {session_id} завершена"
    err = (result.stderr or result.stdout or "").strip()
    return False, f"logoff помилка: {err}"


def kick_all_sessions() -> Tuple[bool, str]:
    """Завершує всі активні сесії крім сесії 0 (services)."""
    sessions = get_sessions()
    if not sessions:
        return True, "Активних сесій немає"

    results = []
    for s in sessions:
        sid = s.get("session_id", "")
        if sid.isdigit() and int(sid) > 0:
            ok, msg = kick_session(sid)
            mark = "✅" if ok else "❌"
            results.append(f"{mark} {s.get('username', sid)}: {msg}")
    return True, "\n".join(results) if results else "Немає сесій для завершення"


# ─── Сервіси ─────────────────────────────────────────────────


def _net_command(verb: str, service: str, timeout: int = 30) -> Tuple[int, str]:
    """net stop|start з повертанням returncode + повідомлення."""
    try:
        r = subprocess.run(
            ["net", verb, service],
            capture_output=True, text=True,
            encoding="cp866", timeout=timeout,
        )
        msg = (r.stderr or r.stdout or "").strip()
        return r.returncode, msg
    except subprocess.TimeoutExpired:
        return 1, f"net {verb} {service}: таймаут"
    except Exception as e:
        return 1, f"net {verb}: {e}"


def restart_service(service_name: str) -> Tuple[bool, str]:
    import time
    code, msg = _net_command("stop", service_name)
    if code != 0 and "не запущена" not in msg.lower() and "not started" not in msg.lower():
        return False, f"Помилка зупинки: {msg}"

    time.sleep(3)

    code, msg = _net_command("start", service_name)
    if code == 0:
        return True, f"Сервіс «{service_name}» перезапущений"
    return False, f"Помилка запуску: {msg}"


def start_service(service_name: str) -> Tuple[bool, str]:
    code, msg = _net_command("start", service_name)
    if code == 0:
        return True, f"Сервіс «{service_name}» запущений"
    return False, msg


# ─── Перезавантаження ────────────────────────────────────────


def reboot_server(delay_sec: int = 30) -> Tuple[bool, str]:
    try:
        r = subprocess.run(
            ["shutdown", "/r", "/t", str(delay_sec), "/c",
             "Перезавантаження через Telegram бот моніторингу"],
            capture_output=True, text=True, encoding="cp866",
        )
        if r.returncode == 0:
            return True, f"🔄 Сервер перезавантажиться через {delay_sec} сек"
        return False, (r.stderr or r.stdout or "").strip()
    except Exception as e:
        return False, str(e)


def cancel_reboot() -> Tuple[bool, str]:
    try:
        subprocess.run(["shutdown", "/a"], capture_output=True, timeout=10)
        return True, "Перезавантаження скасовано"
    except Exception as e:
        return False, str(e)


# ─── Firewall ────────────────────────────────────────────────


def block_ip(ip: str) -> Tuple[bool, str]:
    rule_name = f"{_FW_RULE_PREFIX}{ip}"
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule_name}", "dir=in", "action=block",
             f"remoteip={ip}", "protocol=any", "enable=yes"],
            capture_output=True, text=True, encoding="cp866", timeout=15,
        )
    except Exception as e:
        return False, str(e)

    if r.returncode == 0:
        import storage
        storage.record_blocked_ip(ip)
        logger.info("IP %s заблоковано у Firewall", ip)
        return True, f"IP {ip} заблоковано у Windows Firewall"
    return False, (r.stdout + r.stderr).strip()


def unblock_ip(ip: str) -> Tuple[bool, str]:
    rule_name = f"{_FW_RULE_PREFIX}{ip}"
    try:
        r = subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             f"name={rule_name}"],
            capture_output=True, text=True, encoding="cp866", timeout=15,
        )
    except Exception as e:
        return False, str(e)

    if r.returncode == 0:
        import storage
        storage.remove_blocked_ip(ip)
        logger.info("Блокування IP %s знято", ip)
        return True, f"IP {ip} розблоковано"
    return False, (r.stdout + r.stderr).strip()


def list_blocked_ips() -> List[str]:
    try:
        import storage
        return sorted(storage.get_blocked_ips())
    except Exception:
        return []


# ─── Диски ───────────────────────────────────────────────────


def get_disk_details(paths: List[str]) -> str:
    """Текстова детальна інформація по дисках (для бота)"""
    import psutil
    lines = ["💾 <b>Деталі дисків:</b>"]
    for path in paths:
        try:
            u = psutil.disk_usage(path)
            free_gb  = round(u.free  / 1e9, 2)
            total_gb = round(u.total / 1e9, 2)
            used_gb  = round(u.used  / 1e9, 2)
            pct = round((u.used / u.total) * 100, 1)
            lines.append(f"\n<b>Диск {path}</b>")
            lines.append(f"{_progress_bar(pct)} {pct}%")
            lines.append(f"Використано: {used_gb}GB / {total_gb}GB")
            lines.append(f"Вільно: {free_gb}GB")
        except Exception as e:
            lines.append(f"Диск {path}: помилка — {e}")
    return "\n".join(lines)


def _progress_bar(percent: float, length: int = 10) -> str:
    filled = max(0, min(length, int(percent / 100 * length)))
    empty = length - filled
    if percent > 80:
        char = "🟥"
    elif percent > 60:
        char = "🟧"
    else:
        char = "🟩"
    return char * filled + "⬜" * empty
