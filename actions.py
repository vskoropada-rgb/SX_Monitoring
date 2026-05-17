"""
actions.py — дії на сервері: завершення сесій, перезапуск сервісів, перезавантаження
"""
import subprocess
import logging
import psutil

logger = logging.getLogger(__name__)


def get_sessions() -> list:
    """Повертає список активних сесій"""
    sessions = []
    try:
        result = subprocess.run(
            ["qwinsta"],
            capture_output=True, text=True, encoding="cp866"
        )
        lines = result.stdout.splitlines()
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 3:
                # Формат: SESSIONNAME  USERNAME  ID  STATE
                try:
                    if len(parts) >= 4 and parts[3] in ("Active", "Activ", "Disc"):
                        sessions.append({
                            "session_name": parts[0],
                            "username": parts[1],
                            "session_id": parts[2],
                            "state": parts[3],
                        })
                    elif len(parts) >= 3 and parts[0] not in ("console", "services", "rdp-tcp", ">services", ">console"):
                        sessions.append({
                            "session_name": parts[0],
                            "username": parts[1] if len(parts) > 1 else "",
                            "session_id": parts[2] if len(parts) > 2 else "",
                            "state": parts[3] if len(parts) > 3 else "Unknown",
                        })
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Помилка отримання сесій: {e}")
    return sessions


def kick_session(session_id: str) -> tuple[bool, str]:
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


def kick_all_sessions() -> tuple[bool, str]:
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


def restart_service(service_name: str) -> tuple[bool, str]:
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


def start_service(service_name: str) -> tuple[bool, str]:
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


def reboot_server(delay_sec: int = 30) -> tuple[bool, str]:
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


def cancel_reboot() -> tuple[bool, str]:
    """Скасовує заплановане перезавантаження"""
    try:
        subprocess.run(["shutdown", "/a"], capture_output=True)
        return True, "Перезавантаження скасовано"
    except Exception as e:
        return False, str(e)


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
