"""
monitor.py — головний файл, запускається Task Scheduler кожну хвилину
"""
import os
import sys
import logging
from pathlib import Path
from datetime import datetime

# Додаємо поточну папку до шляху
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# Логування
log_level = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    filename=Path(__file__).parent / "monitor.log",
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("monitor")

import storage
import analyzer
import notifier

from collectors import disk, memory, services, backup


def load_config() -> dict:
    return {key: os.getenv(key, "") for key in [
        "SERVER_ID", "COMPANY_NAME",
        "TG_BOT_TOKEN", "TG_GROUP_ID", "TG_TOPIC_ID",
        "OPENAI_API_KEY", "OPENAI_MODEL",
        "DISK_PATHS", "DISK_WARNING_PERCENT", "DISK_CRITICAL_PERCENT",
        "CPU_WARNING_PERCENT", "RAM_WARNING_PERCENT",
        "BRUTE_FORCE_WINDOW_MIN", "BRUTE_FORCE_THRESHOLD", "KNOWN_IPS",
        "BACKUP_PATH", "BACKUP_MAX_AGE_HOURS", "BACKUP_MIN_SIZE_MB",
        "MONITOR_SERVICES", "WATCH_FILES",
        "CHECK_INTERVAL_SEC", "ALERT_COOLDOWN_MIN",
    ]}


def run():
    logger.info("=== Запуск перевірки ===")
    config = load_config()

    # Ініціалізація БД
    storage.init_db()

    # Збираємо метрики
    all_metrics = {}

    # Диски
    try:
        disk_data = disk.collect(config)
        all_metrics.update(disk_data)
        logger.info(f"Диски: {[f\"{d['path']}={d.get('free_pct', 'err')}%\" for d in disk_data.get('disks', [])]}")
    except Exception as e:
        logger.error(f"Помилка збору дисків: {e}")

    # CPU/RAM
    try:
        mem_data = memory.collect(config)
        all_metrics.update(mem_data)
        logger.info(f"CPU: {mem_data.get('cpu', {}).get('percent')}%, RAM: {mem_data.get('ram', {}).get('percent')}%")
    except Exception as e:
        logger.error(f"Помилка збору CPU/RAM: {e}")

    # Сервіси
    try:
        svc_data = services.collect(config)
        all_metrics.update(svc_data)
        if svc_data.get("newly_stopped"):
            logger.warning(f"Зупинились сервіси: {svc_data['newly_stopped']}")
    except Exception as e:
        logger.error(f"Помилка збору сервісів: {e}")

    # Бекапи
    try:
        backup_data = backup.collect(config)
        all_metrics.update(backup_data)
        logger.info(f"Бекап: {backup_data.get('status')} — {backup_data.get('latest_age_hours')}г тому")
    except Exception as e:
        logger.error(f"Помилка перевірки бекапів: {e}")

    # Безпека та RDP — тільки якщо pywin32 доступний
    try:
        from collectors import security, rdp
        sec_data = security.collect(config)
        all_metrics.update(sec_data)

        rdp_data = rdp.collect(config)
        all_metrics.update(rdp_data)

        if sec_data.get("brute_force_alerts"):
            logger.warning(f"Перебір паролів: {sec_data['brute_force_alerts']}")
        if rdp_data.get("new_ip_alerts"):
            logger.warning(f"Нові RDP IP: {rdp_data['new_ip_alerts']}")
    except ImportError:
        logger.warning("pywin32 недоступний — безпека та RDP не перевіряються")
    except Exception as e:
        logger.error(f"Помилка збору безпеки/RDP: {e}")

    # Аналіз через GPT
    cooldown = int(config.get("ALERT_COOLDOWN_MIN", 30))

    try:
        decision = analyzer.analyze(all_metrics, config)

        if decision and decision.get("should_alert"):
            alert_key = decision.get("alert_key", "generic")
            severity = decision.get("severity", "info")

            if storage.can_send_alert(alert_key, cooldown):
                logger.info(f"Відправляємо алерт: {alert_key} ({severity})")
                ok = notifier.send_alert(decision, all_metrics, config)
                if ok:
                    storage.record_alert(alert_key, decision.get("tags", [""])[0], severity, decision.get("title", ""))
                    logger.info("Алерт відправлений")
            else:
                logger.info(f"Алерт {alert_key} в кулдауні (>{cooldown}хв)")
        else:
            logger.info("GPT: все гаразд, алерт не потрібен")

    except Exception as e:
        logger.error(f"Помилка аналізу/відправки: {e}")

    # Очищення старих метрик раз на день
    try:
        current_hour = datetime.now().hour
        current_min = datetime.now().minute
        if current_hour == 3 and current_min < 2:
            storage.cleanup_old_metrics(days=30)
            logger.info("Очищено старі метрики")
    except Exception:
        pass

    logger.info("=== Перевірка завершена ===")


if __name__ == "__main__":
    run()
