"""
Хелпери для роботи з PostgreSQL замість SQLite storage.py.
Зберігають ту саму логіку що була в SX_Monitoring/storage.py.
"""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from models import Alert, PendingAlert


def can_send_alert(db: Session, server_id: str, alert_key: str, cooldown_min: int) -> bool:
    row = (
        db.query(Alert)
        .filter(Alert.server_id == server_id, Alert.alert_key == alert_key)
        .order_by(Alert.sent_at.desc())
        .first()
    )
    if not row:
        return True
    return datetime.utcnow() - row.sent_at > timedelta(minutes=cooldown_min)


def record_alert(db: Session, server_id: str, alert_key: str,
                 alert_type: str, severity: str, message: str):
    db.add(Alert(
        server_id=server_id,
        alert_key=alert_key,
        alert_type=alert_type,
        severity=severity,
        message=message,
    ))
    db.commit()


def add_pending_alert(db: Session, server_id: str, alert_key: str,
                      title: str, body: str, severity: str):
    stmt = insert(PendingAlert).values(
        server_id=server_id,
        alert_key=alert_key,
        title=title,
        body=body or "",
        severity=severity,
    ).on_conflict_do_update(
        constraint="uq_pending_server_key",
        set_={
            "title":      title,
            "body":       body or "",
            "updated_at": datetime.utcnow(),
            "count":      PendingAlert.count + 1,
        },
    )
    db.execute(stmt)
    db.commit()


def get_pending_alerts(db: Session, server_id: str) -> list:
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    rows = (
        db.query(PendingAlert)
        .filter(PendingAlert.server_id == server_id)
        .order_by(PendingAlert.added_at)
        .all()
    )
    result = [
        {"alert_key": r.alert_key, "title": r.title,
         "body": r.body, "severity": r.severity, "count": r.count}
        for r in rows
    ]
    result.sort(key=lambda x: sev_order.get(x["severity"], 2))
    return result


def clear_pending_alerts(db: Session, server_id: str):
    db.query(PendingAlert).filter(PendingAlert.server_id == server_id).delete()
    db.commit()
