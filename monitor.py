"""
monitor.py — головний файл, запускається Task Scheduler кожну хвилину
"""
import sys
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import config as _config_module

logger = logging.getLogger("monitor")

import storage
import analyzer
import notifier

from collectors import disk, memory, services, backup


def load_config() -> dict:
    return _config_module.load()


def run():
    logger.info("=== Запуск перевірки ===")
    config = load_config()

    # Режим обслуговування — метрики збираємо, але алерти не надсилаємо
    server_id   = config.get("SERVER_ID", "server")
    maintenance = storage.is_maintenance(server_id)
    if maintenance:
        until = storage.get_maintenance_until(server_id)
        logger.info("Maintenance mode до %s — алерти вимкнено", until)

    all_metrics = {}

    # ── Диски ────────────────────────────────────────────────
    try:
        disk_data = disk.collect(config)
        all_metrics.update(disk_data)
        logger.info("Диски: %s", [f"{d['path']}={d.get('free_pct')}%" for d in disk_data.get("disks", [])])
    except Exception as e:
        logger.error("Помилка збору дисків: %s", e)

    # ── CPU / RAM ────────────────────────────────────────────
    try:
        mem_data = memory.collect(config)
        all_metrics.update(mem_data)
        logger.info("CPU: %s%%, RAM: %s%%",
                    mem_data.get("cpu", {}).get("percent"),
                    mem_data.get("ram", {}).get("percent"))
    except Exception as e:
        logger.error("Помилка збору CPU/RAM: %s", e)

    # ── Сервіси ──────────────────────────────────────────────
    try:
        svc_data = services.collect(config)
        all_metrics.update(svc_data)
        if svc_data.get("newly_stopped"):
            logger.warning("Зупинились сервіси: %s", svc_data["newly_stopped"])
    except Exception as e:
        logger.error("Помилка збору сервісів: %s", e)

    # ── Бекапи ───────────────────────────────────────────────
    try:
        backup_data = backup.collect(config)
        all_metrics.update(backup_data)
        if backup_data.get("latest_size_bytes"):
            storage.save_metric("backup_size_mb", backup_data["latest_size_bytes"] / 1e6)
        logger.info("Бекап: %s — %sг тому", backup_data.get("status"), backup_data.get("latest_age_hours"))
    except Exception as e:
        logger.error("Помилка перевірки бекапів: %s", e)

    # ── Windows Update / pending reboot ──────────────────────
    try:
        from collectors import winupdate
        wu_data = winupdate.collect(config)
        all_metrics.update(wu_data)
        if wu_data.get("reboot_required"):
            logger.warning("Очікує перезавантаження: %s", wu_data.get("reboot_reasons"))
    except Exception as e:
        logger.error("Помилка winupdate: %s", e)

    # ── Security, RDP (pywin32) ───────────────────────────────
    try:
        from collectors import security, rdp
        sec_data = security.collect(config)
        all_metrics.update(sec_data)
        rdp_data = rdp.collect(config)
        all_metrics.update(rdp_data)
        if sec_data.get("brute_force_alerts"):
            logger.warning("Перебір паролів: %s", sec_data["brute_force_alerts"])
        if rdp_data.get("new_ip_alerts"):
            logger.warning("Нові RDP IP: %s", rdp_data["new_ip_alerts"])
    except ImportError:
        logger.warning("pywin32 недоступний — security/RDP пропущено")
    except Exception as e:
        logger.error("Помилка security/RDP: %s", e)

    # ── USB-пристрої ─────────────────────────────────────────
    try:
        from collectors import usb
        usb_data = usb.collect(config)
        all_metrics.update(usb_data)
        if usb_data.get("new_usb_devices"):
            logger.warning("Нові USB: %s", usb_data["new_usb_devices"])
    except Exception as e:
        logger.error("Помилка USB: %s", e)

    # ── Нове ПЗ ──────────────────────────────────────────────
    try:
        from collectors import software
        sw_data = software.collect(config)
        all_metrics.update(sw_data)
        if sw_data.get("new_software"):
            logger.warning("Нове ПЗ: %s", sw_data["new_software"][:5])
    except Exception as e:
        logger.error("Помилка software: %s", e)

    # ── Task Scheduler ────────────────────────────────────────
    try:
        from collectors import schtasks
        task_data = schtasks.collect(config)
        all_metrics.update(task_data)
        if task_data.get("new_scheduled_tasks"):
            logger.warning("Нові завдання: %s", task_data["new_scheduled_tasks"])
    except Exception as e:
        logger.error("Помилка schtasks: %s", e)

    # ── Щоденний звіт о DAILY_REPORT_HOUR ────────────────────
    try:
        report_hour = int(config.get("DAILY_REPORT_HOUR", 10))
        now = datetime.now()
        if now.hour == report_hour and now.minute < 2:
            cooldown_min = 22 * 60  # раз на 22г, не двічі підряд
            if storage.can_send_alert("daily_report", cooldown_min):
                notifier.send_daily_report(all_metrics, config)
                storage.record_alert("daily_report", "report", "info", "daily report sent")
                logger.info("Щоденний звіт надіслано")
    except Exception as e:
        logger.error("Помилка щоденного звіту: %s", e)

    # ── Кеш для швидкого відображення в боті ─────────────────
    try:
        storage.cache_metrics(all_metrics)
    except Exception:
        pass

    # ── Фільтр: не алертити по вже заблокованих IP ───────────
    if all_metrics.get("brute_force_alerts"):
        blocked = storage.get_blocked_ips()
        if blocked:
            before = len(all_metrics["brute_force_alerts"])
            all_metrics["brute_force_alerts"] = [
                a for a in all_metrics["brute_force_alerts"]
                if a["ip"] not in blocked
            ]
            filtered = before - len(all_metrics["brute_force_alerts"])
            if filtered:
                logger.info("Відфільтровано %d вже заблокованих IP", filtered)

    # ── Аналіз і відправка алертів ────────────────────────────
    if not maintenance:
        cooldown = int(config.get("ALERT_COOLDOWN_MIN", 30))
        try:
            decision = analyzer.analyze(all_metrics, config)
            if decision and decision.get("should_alert"):
                alert_key = decision.get("alert_key", "generic")
                severity  = decision.get("severity", "info")
                if storage.can_send_alert(alert_key, cooldown):
                    logger.info("Відправляємо алерт: %s (%s)", alert_key, severity)
                    ok = notifier.send_alert(decision, all_metrics, config)
                    if ok:
                        storage.record_alert(
                            alert_key,
                            decision.get("tags", [""])[0],
                            severity,
                            decision.get("title", ""),
                        )
                        logger.info("Алерт відправлений")
                else:
                    logger.info("Алерт %s в кулдауні", alert_key)
            else:
                logger.info("GPT: все гаразд")
        except Exception as e:
            logger.error("Помилка аналізу/відправки: %s", e)

    # ── Щоденне очищення (03:00–03:02) ───────────────────────
    try:
        now = datetime.now()
        if now.hour == 3 and now.minute < 2:
            storage.cleanup_old_metrics(days=30)
            logger.info("Очищено старі метрики")
    except Exception:
        pass

    logger.info("=== Перевірка завершена ===")


if __name__ == "__main__":
    run()
