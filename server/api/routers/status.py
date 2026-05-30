"""
Статус серверів для Telegram бота і дашборду.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import Server, MetricsSnapshot

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/servers")
def list_servers(db: Session = Depends(get_db)):
    servers = db.query(Server).all()
    return [
        {
            "id":        s.id,
            "name":      s.name,
            "last_seen": s.last_seen.isoformat() if s.last_seen else None,
        }
        for s in servers
    ]


@router.get("/status/{server_id}")
def server_status(server_id: str, db: Session = Depends(get_db)):
    snap = db.query(MetricsSnapshot).filter(
        MetricsSnapshot.server_id == server_id
    ).first()
    if not snap:
        return {"error": "No data yet"}
    return snap.data


@router.get("/status")
def all_status(db: Session = Depends(get_db)):
    snaps = db.query(MetricsSnapshot).all()
    return {s.server_id: s.data for s in snaps}


@router.post("/servers/register")
def register_server(payload: dict, db: Session = Depends(get_db)):
    """
    Реєстрація нового агента.
    payload: {server_id, name, api_key, tg_topic_id?}
    """
    from models import Server as ServerModel
    existing = db.query(ServerModel).filter(
        ServerModel.id == payload["server_id"]
    ).first()
    if existing:
        existing.name = payload.get("name", existing.name)
        existing.tg_topic_id = payload.get("tg_topic_id", existing.tg_topic_id)
        db.commit()
        return {"ok": True, "action": "updated"}

    server = ServerModel(
        id=payload["server_id"],
        name=payload["name"],
        api_key=payload["api_key"],
        tg_topic_id=payload.get("tg_topic_id"),
    )
    db.add(server)
    db.commit()
    return {"ok": True, "action": "created"}
