"""
collectors/schtasks.py — виявлення нових завдань Task Scheduler (Event ID 4698)
"""
import logging
import storage

logger = logging.getLogger(__name__)

# Системні префікси — ігноруємо
_SYSTEM_PREFIXES = ("\\Microsoft\\", "\\MicrosoftEdge", "\\OneDrive")

# Відомі безпечні завдання що створюємо самі
_OWN_TASKS = {"1C_Monitor", "1C_Monitor_Bot", "1C_Monitor_Watchdog"}


def _is_system_task(name: str) -> bool:
    if name in _OWN_TASKS:
        return True
    return any(name.startswith(p) for p in _SYSTEM_PREFIXES)


def _read_task_events() -> list:
    try:
        import win32evtlog
        handle = win32evtlog.OpenEventLog(None, "Security")
        flags  = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        events = []

        while True:
            records = win32evtlog.ReadEventLog(handle, flags, 0)
            if not records:
                break
            for rec in records:
                if rec.EventID == 4698:
                    inserts = rec.StringInserts or []
                    # StringInserts[4] = TaskName in most Windows versions
                    task_name = inserts[4] if len(inserts) > 4 else (inserts[3] if len(inserts) > 3 else "")
                    user = inserts[1] if len(inserts) > 1 else ""
                    events.append({
                        "task_name": task_name.strip(),
                        "user": user,
                        "time": str(rec.TimeGenerated),
                    })
            if len(events) >= 100:
                break

        win32evtlog.CloseEventLog(handle)
        return events
    except Exception as e:
        logger.debug("Task Scheduler event read: %s", e)
        return []


def collect(config: dict) -> dict:
    events = _read_task_events()
    new_tasks = []

    for ev in events:
        name = ev.get("task_name", "")
        if not name or _is_system_task(name):
            continue
        if not storage.is_known_task(name):
            storage.register_task(name)
            new_tasks.append({"name": name, "user": ev.get("user", ""), "time": ev.get("time", "")})

    return {"new_scheduled_tasks": new_tasks}
