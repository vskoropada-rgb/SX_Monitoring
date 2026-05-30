"""
POST /api/metrics  — головний endpoint: агент надсилає дані кожну хвилину.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from database import get_db
from models import Server, Metric, MetricsSnapshot
from auth import get_server

router = APIRouter(prefix="/api", tags=["metrics"])


@router.post("/metrics")
def receive_metrics(
    payload: dict,
    background: BackgroundTasks,
    server: Server = Depends(get_server),
    db: Session = Depends(get_db),
):
    now = datetime.utcnow()

    # Оновлюємо last_seen
    server.last_seen = now
    db.add(server)

    # Зберігаємо числові метрики для Grafana
    _save_numeric_metrics(db, server.id, payload, now)

    # Оновлюємо snapshot для швидкого відображення в боті
    stmt = insert(MetricsSnapshot).values(
        server_id=server.id,
        data=payload,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=["server_id"],
        set_={"data": payload, "updated_at": now},
    )
    db.execute(stmt)
    db.commit()

    # Аналіз і відправка алертів — у фоні щоб не блокувати агента
    background.add_task(_analyze_and_alert, server.id, server.name, payload)

    return {"ok": True}


def _save_numeric_metrics(db: Session, server_id: str, payload: dict, now: datetime):
    rows = []

    cpu = payload.get("cpu", {})
    if "percent" in cpu:
        rows.append(Metric(server_id=server_id, metric_name="cpu_percent",
                           value=cpu["percent"], recorded_at=now))

    ram = payload.get("ram", {})
    if "percent" in ram:
        rows.append(Metric(server_id=server_id, metric_name="ram_percent",
                           value=ram["percent"],
                           extra={"free_gb": ram.get("free_gb")},
                           recorded_at=now))

    for disk in payload.get("disks", []):
        if "free_pct" in disk:
            path_key = disk["path"].rstrip("\\").replace(":", "").replace("\\", "_")
            rows.append(Metric(server_id=server_id,
                               metric_name=f"disk_free_{path_key}",
                               value=disk["free_pct"],
                               extra={"free_gb": disk.get("free_gb"),
                                      "total_gb": disk.get("total_gb")},
                               recorded_at=now))

    if payload.get("latest_size_bytes"):
        rows.append(Metric(server_id=server_id, metric_name="backup_size_mb",
                           value=payload["latest_size_bytes"] / 1e6,
                           recorded_at=now))

    if rows:
        db.bulk_save_objects(rows)


def _analyze_and_alert(server_id: str, server_name: str, payload: dict):
    """Викликається у фоновому потоці після збереження метрик."""
    try:
        import analyzer
        import notifier
        import storage_helpers as storage

        db = next(get_db())
        server = db.query(Server).filter(Server.id == server_id).first()
        if not server:
            return

        from config import settings
        config = {
            "SERVER_ID":              server.id,
            "COMPANY_NAME":           server.name,
            "TG_BOT_TOKEN":           settings.tg_bot_token,
            "TG_GROUP_ID":            settings.tg_group_id,
            "TG_TOPIC_ID":            server.tg_topic_id or "",
            "OPENAI_API_KEY":         settings.openai_api_key,
            "OPENAI_MODEL":           settings.openai_model,
            "ALERT_COOLDOWN_MIN":     str(settings.alert_cooldown_min),
            "DAILY_REPORT_HOUR":      str(settings.daily_report_hour),
        }

        decision = analyzer.analyze(payload, config)
        if not decision or not decision.get("should_alert"):
            return

        alert_key = decision.get("alert_key", "generic")
        severity  = decision.get("severity", "info")

        cooldown = int(config.get("ALERT_COOLDOWN_MIN", 30))
        if not storage.can_send_alert(db, server_id, alert_key, cooldown):
            return

        if severity == "critical":
            notifier.send_alert(decision, payload, config)
            storage.record_alert(db, server_id, alert_key,
                                 decision.get("tags", [""])[0], severity,
                                 decision.get("title", ""))
        else:
            storage.add_pending_alert(db, server_id, alert_key,
                                      decision.get("title", "Подія"),
                                      decision.get("analysis", ""), severity)
            storage.record_alert(db, server_id, alert_key,
                                 decision.get("tags", [""])[0], severity,
                                 decision.get("title", ""))

        # Щоденний звіт
        now = datetime.utcnow()
        if now.hour == int(config["DAILY_REPORT_HOUR"]) and now.minute < 2:
            if storage.can_send_alert(db, server_id, "daily_report", 22 * 60):
                pending = storage.get_pending_alerts(db, server_id)
                notifier.send_daily_report(payload, config, pending_alerts=pending)
                storage.clear_pending_alerts(db, server_id)
                storage.record_alert(db, server_id, "daily_report", "report", "info", "sent")

    except Exception as e:
        import logging
        logging.getLogger(__name__).error("analyze_and_alert error: %s", e)
    finally:
        try:
            db.close()
        except Exception:
            pass
