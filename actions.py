"""
actions.py — дії на сервері: завершення сесій, перезапуск сервісів, перезавантаження, firewall
"""
import subprocess
import logging
from typing import List, Tuple
import psutil

logger = logging.getLogger(__name__)

_FW_RULE_PREFIX = "1C_Monitor_Block_"


def get_sessions() -> list:
    """Повертає список активних сесій через qwinsta"""
    sessions = []
    try:
        result = subprocess.run(
            ["qwinsta"], capture_output=True, text=True, encoding="cp866"
        )
        lines = result.stdout.splitlines()
        if not lines:
            return sessions

        # Визначаємо позиції колонок з заголовка (fixed-width format)
        header = lines[0]
        user_col  = header.find("USERNAME")
        id_col    = header.find("ID")
        state_col = header.find("STATE")
        if id_col < 0 or state_col < 0:
            user_col, id_col, state_col = 19, 38, 48

        for line in lines[1:]:
            if len(line) <= id_col:
                continue
            line_body    = line[1:]  # прибираємо '>' або пробіл на початку
            session_name = line_body[:user_col].strip()
            username     = line_body[user_col:id_col].strip()
            rest = line_body[id_col:].split()
            if not rest or not rest[0].isdigit():
                continue
            session_id = rest[0]
            state      = rest[1] if len(rest) > 1 else "Unknown"

            # Пропускаємо системні сесії без користувача
            if not username and session_name in ("services", "rdp-tcp"):
                continue
            if state not in ("Active", "Activ", "Disc"):
                continue

            sessions.append({
                "session_name": session_name,
                "username":     username if username else session_name,
                "session_id":   session_id,
                "state":        state,
            })
    except Exception as e:
        logger.error(f"Помилка отримання сесій: {e}")
    return sessions


def kick_session(session_id: str) -> Tuple[bool, str]:
    """Завершує RDP сесію за ID"""
    try:
        result = subprocess.run(
            ["logoff", session_id, "/server:localhost"],
            capture_output=True, text=True, encoding="cp866"
        )
        if result.returncode == 0:
            logger.info(f"Сесія {session_id} завершена")
            return True, f"Сесія {session_id} успішно завершена"
        else:
            return False, f"Помилка: {result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return False, f"Виняток: {e}"


def kick_all_sessions() -> Tuple[bool, str]:
    """Завершує всі активні RDP сесії"""
    sessions = get_sessions()
    if not sessions:
        return True, "Активних сесій немає"

    results = []
    for session in sessions:
        sid = session.get("session_id", "")
        if sid and sid.isdigit() and int(sid) > 0:
            ok, msg = kick_session(sid)
            results.append(f"{'✅' if ok else '❌'} {session.get('username', sid)}: {msg}")

    return True, "\n".join(results)


def restart_service(service_name: str) -> Tuple[bool, str]:
    """Перезапускає Windows сервіс"""
    try:
        # Зупиняємо
        stop = subprocess.run(
            ["net", "stop", service_name],
            capture_output=True, text=True, encoding="cp866", timeout=30
        )

        import time
        time.sleep(3)

        # Запускаємо
        start = subprocess.run(
            ["net", "start", service_name],
            capture_output=True, text=True, encoding="cp866", timeout=30
        )

        if start.returncode == 0:
            return True, f"Сервіс '{service_name}' успішно перезапущений"
        else:
            return False, f"Помилка запуску: {start.stderr.strip() or start.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return False, f"Таймаут при перезапуску сервісу '{service_name}'"
    except Exception as e:
        return False, f"Виняток: {e}"


def start_service(service_name: str) -> Tuple[bool, str]:
    """Запускає зупинений сервіс"""
    try:
        result = subprocess.run(
            ["net", "start", service_name],
            capture_output=True, text=True, encoding="cp866", timeout=30
        )
        if result.returncode == 0:
            return True, f"Сервіс '{service_name}' запущений"
        else:
            return False, f"Помилка: {result.stderr.strip() or result.stdout.strip()}"
    except Exception as e:
        return False, f"Виняток: {e}"


def reboot_server(delay_sec: int = 30) -> Tuple[bool, str]:
    """Перезавантажує сервер з затримкою"""
    try:
        result = subprocess.run(
            ["shutdown", "/r", "/t", str(delay_sec), "/c", "Перезавантаження через Telegram бот моніторингу"],
            capture_output=True, text=True, encoding="cp866"
        )
        if result.returncode == 0:
            return True, f"🔄 Сервер перезавантажується через {delay_sec} секунд"
        else:
            return False, f"Помилка: {result.stderr.strip()}"
    except Exception as e:
        return False, f"Виняток: {e}"


def cancel_reboot() -> Tuple[bool, str]:
    """Скасовує заплановане перезавантаження"""
    try:
        subprocess.run(["shutdown", "/a"], capture_output=True)
        return True, "Перезавантаження скасовано"
    except Exception as e:
        return False, str(e)


def block_ip(ip: str) -> Tuple[bool, str]:
    """Блокує вхідні з'єднання з IP через Windows Firewall"""
    rule_name = f"{_FW_RULE_PREFIX}{ip}"
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule_name}", "dir=in", "action=block",
             f"remoteip={ip}", "protocol=any", "enable=yes"],
            capture_output=True, text=True, encoding="cp866"
        )
        if result.returncode == 0:
            import storage
            storage.record_blocked_ip(ip)
            logger.info("IP %s заблоковано у Firewall", ip)
            return True, f"IP {ip} заблоковано у Windows Firewall"
        return False, f"Помилка: {(result.stdout + result.stderr).strip()}"
    except Exception as e:
        return False, str(e)


def unblock_ip(ip: str) -> Tuple[bool, str]:
    """Знімає блокування IP з Windows Firewall"""
    rule_name = f"{_FW_RULE_PREFIX}{ip}"
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule",
             f"name={rule_name}"],
            capture_output=True, text=True, encoding="cp866"
        )
        if result.returncode == 0:
            import storage
            storage.remove_blocked_ip(ip)
            logger.info("Блокування IP %s знято", ip)
            return True, f"IP {ip} розблоковано"
        return False, f"Помилка: {(result.stdout + result.stderr).strip()}"
    except Exception as e:
        return False, str(e)


def list_blocked_ips() -> List[str]:
    """Повертає список IP заблокованих через цей моніторинг"""
    try:
        ps_cmd = (
            f"Get-NetFirewallRule -DisplayName '{_FW_RULE_PREFIX}*' "
            "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty DisplayName"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10
        )
        ips = []
        for line in result.stdout.splitlines():
            name = line.strip()
            if name.startswith(_FW_RULE_PREFIX):
                ips.append(name[len(_FW_RULE_PREFIX):])
        return ips
    except Exception:
        return []


def get_disk_details(paths: list) -> str:
    """Детальна інформація по дисках"""
    import psutil
    lines = ["💾 <b>Деталі дисків:</b>"]
    for path in paths:
        try:
            usage = psutil.disk_usage(path)
            free_gb = round(usage.free / 1e9, 2)
            total_gb = round(usage.total / 1e9, 2)
            used_gb = round(usage.used / 1e9, 2)
            pct = round((usage.used / usage.total) * 100, 1)
            bar = _progress_bar(pct)
            lines.append(f"\n<b>Диск {path}</b>")
            lines.append(f"{bar} {pct}%")
            lines.append(f"Використано: {used_gb}GB / {total_gb}GB")
            lines.append(f"Вільно: {free_gb}GB")
        except Exception as e:
            lines.append(f"Диск {path}: помилка — {e}")
    return "\n".join(lines)


def _progress_bar(percent: float, length: int = 10) -> str:
    filled = int(percent / 100 * length)
    empty = length - filled
    if percent > 80:
        char = "🟥"
    elif percent > 60:
        char = "🟧"
    else:
        char = "🟩"
    return char * filled + "⬜" * empty
