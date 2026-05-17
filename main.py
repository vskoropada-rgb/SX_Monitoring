"""
main.py — єдина точка входу: моніторинг (фоновий потік) + Telegram бот
"""
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
