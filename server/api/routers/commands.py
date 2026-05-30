"""
Черга команд: бот пише → агент читає і виконує → звітує назад.
"""
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Server, Command
from auth import get_server

router = APIRouter(prefix="/api", tags=["commands"])


class CommandResult(BaseModel):
    status: Literal["done", "failed"]
    result: str = ""


@router.get("/commands/pending")
def get_pending_commands(
    server: Server = Depends(get_server),
    db: Session = Depends(get_db),
):
    """Агент опитує цей endpoint кожні 5 секунд."""
    cmds = (
        db.query(Command)
        .filter(Command.server_id == server.id, Command.status == "pending")
        .order_by(Command.created_at)
        .all()
    )
    for cmd in cmds:
        cmd.status = "executing"
    db.commit()

    return [
        {"id": c.id, "action": c.action, "params": c.params or {}}
        for c in cmds
    ]


@router.post("/commands/{command_id}/result")
def post_command_result(
    command_id: int,
    body: CommandResult,
    server: Server = Depends(get_server),
    db: Session = Depends(get_db),
):
    cmd = db.query(Command).filter(
        Command.id == command_id, Command.server_id == server.id
    ).first()
    if not cmd:
        raise HTTPException(status_code=404, detail="Command not found")

    cmd.status      = body.status
    cmd.result      = body.result
    cmd.executed_at = datetime.utcnow()
    db.commit()

    # Відповідаємо в Telegram якщо є куди
    if cmd.tg_chat_id:
        _notify_result(cmd)

    return {"ok": True}


def create_command(
    db: Session,
    server_id: str,
    action: str,
    params: dict,
    tg_chat_id: str = None,
    tg_message_id: int = None,
) -> Command:
    cmd = Command(
        server_id=server_id,
        action=action,
        params=params,
        tg_chat_id=tg_chat_id,
        tg_message_id=tg_message_id,
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    return cmd


def _notify_result(cmd: Command):
    try:
        import requests
        from config import settings
        icon = "✅" if cmd.status == "done" else "❌"
        text = f"{icon} <b>{cmd.action}</b>\n{cmd.result or ''}"
        requests.post(
            f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
            json={"chat_id": cmd.tg_chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass
