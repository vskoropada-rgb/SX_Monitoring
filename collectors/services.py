"""
collectors/services.py — моніторинг Windows сервісів
"""
import subprocess
import psutil
from storage import get_service_state, update_service_state


def get_service_status(service_name: str) -> str:
    """Отримує статус сервісу"""
    try:
        for svc in psutil.win_service_iter():
            if service_name.lower() in svc.name().lower() or service_name.lower() in svc.display_name().lower():
                return svc.status()
    except Exception:
        pass

    # Fallback через sc query
    try:
        result = subprocess.run(
            ["sc", "query", service_name],
            capture_output=True, text=True, encoding="cp866"
        )
        if "RUNNING" in result.stdout:
            return "running"
        elif "STOPPED" in result.stdout:
            return "stopped"
        elif "PAUSED" in result.stdout:
            return "paused"
    except Exception:
        pass

    return "unknown"


def collect(config: dict) -> dict:
    services_str = config.get(
        "MONITOR_SERVICES",
        "1C:Enterprise 8.3 Server Agent,MSSQLSERVER"
    )
    service_names = [s.strip() for s in services_str.split(",")]

    results = []
    newly_stopped = []

    for name in service_names:
        if not name:
            continue

        current_status = get_service_status(name)
        previous_status = get_service_state(name)

        # Фіксуємо зміну статусу
        status_changed = previous_status and previous_status != current_status
        just_stopped = status_changed and current_status in ("stopped", "unknown")
        just_started = status_changed and current_status == "running" and previous_status in ("stopped",)

        update_service_state(name, current_status)

        svc_info = {
            "name": name,
            "status": current_status,
            "is_running": current_status == "running",
            "status_changed": status_changed,
            "just_stopped": just_stopped,
            "just_started": just_started,
            "previous_status": previous_status,
        }
        results.append(svc_info)

        if just_stopped:
            newly_stopped.append(name)

    return {
        "services": results,
        "newly_stopped": newly_stopped,
        "all_running": all(s["is_running"] for s in results),
    }
