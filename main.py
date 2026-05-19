"""
main.py — єдина точка входу: моніторинг (фоновий потік) + Telegram бот
"""
import atexit
import ctypes
import os
import sys
import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import config as _cfg_module

_cfg = _cfg_module.load()

# Єдиний лог для всього — 5 MB × 3 файли = макс 15 MB
_handler = RotatingFileHandler(
    ROOT / "monitor.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
logging.basicConfig(
    level=getattr(logging, _cfg.get("LOG_LEVEL", "INFO"), logging.INFO),
    handlers=[_handler],
)

logger = logging.getLogger("main")

import storage
from monitor import run as monitor_run
from bot import run as bot_run

# ─── PID-lock: один екземпляр за раз ─────────────────────────

_PID_FILE     = ROOT / "monitor.pid"
_MUTEX_HANDLE = None


def _acquire_lock() -> bool:
    """
    Використовує Windows Named Mutex як основний lock.
    Named Mutex автоматично звільняється при завершенні процесу (навіть примусовому),
    тому не залежить від psutil.cmdline() який кидає AccessDenied для SYSTEM-процесів.
    """
    global _MUTEX_HANDLE
    server_id   = _cfg.get("SERVER_ID", "default")
    mutex_name  = f"Global\\1CMonitor_{server_id}"

    try:
        kernel32      = ctypes.windll.kernel32
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, True, mutex_name)
        last_err      = kernel32.GetLastError()

        if last_err == 183:  # ERROR_ALREADY_EXISTS — інший екземпляр вже тримає mutex
            if _MUTEX_HANDLE:
                kernel32.CloseHandle(_MUTEX_HANDLE)
            _MUTEX_HANDLE = None
            logger.error(
                "Інший екземпляр вже запущений (mutex %s) — завершення.", mutex_name
            )
            return False

        if not _MUTEX_HANDLE:
            raise OSError(f"CreateMutexW повернув 0 (err={last_err})")

    except OSError as e:
        logger.error("Не вдалося створити mutex: %s — завершення", e)
        return False
    except Exception:
        # ctypes/windll недоступний — запасний варіант через PID-файл
        return _acquire_lock_pidfile()

    _PID_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)
    return True


def _acquire_lock_pidfile() -> bool:
    """Запасний PID-lock (якщо ctypes недоступний — не Windows або старе середовище)."""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            try:
                import psutil
                p = psutil.Process(old_pid)
                if "main" in " ".join(p.cmdline()).lower():
                    logger.error(
                        "Інший екземпляр вже запущений (PID %d) — завершення. "
                        "Якщо це помилка, видаліть %s", old_pid, _PID_FILE
                    )
                    return False
            except Exception:
                pass
        except (ValueError, OSError):
            pass
    _PID_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)
    return True


def _release_lock() -> None:
    if _MUTEX_HANDLE:
        try:
            ctypes.windll.kernel32.ReleaseMutex(_MUTEX_HANDLE)
            ctypes.windll.kernel32.CloseHandle(_MUTEX_HANDLE)
        except Exception:
            pass
    try:
        _PID_FILE.unlink()
    except OSError:
        pass


# ─── Monitor loop ─────────────────────────────────────────────


def _monitor_loop(stop_event: threading.Event, interval: int):
    logger.info("Monitor loop started (interval=%ds)", interval)
    while not stop_event.is_set():
        try:
            monitor_run()
        except Exception as e:
            logger.error("Monitor loop error: %s", e, exc_info=True)
        stop_event.wait(interval)
    logger.info("Monitor loop stopped")


def main():
    if not _acquire_lock():
        sys.exit(0)

    storage.init_db()

    interval = int(_cfg.get("CHECK_INTERVAL_SEC", 60))
    stop     = threading.Event()

    t = threading.Thread(
        target=_monitor_loop,
        args=(stop, interval),
        daemon=True,
        name="monitor-loop",
    )
    t.start()
    logger.info("=== 1C Monitor started (interval=%ds) ===", interval)

    try:
        bot_run()
    except KeyboardInterrupt:
        logger.info("Shutdown by keyboard")
    finally:
        stop.set()
        logger.info("=== 1C Monitor stopped ===")


if __name__ == "__main__":
    main()
