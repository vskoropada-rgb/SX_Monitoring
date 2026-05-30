from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import Server


def get_server(
    x_api_key: str = Header(..., alias="X-Api-Key"),
    db: Session = Depends(get_db),
) -> Server:
    server = db.query(Server).filter(Server.api_key == x_api_key).first()
    if not server:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return server
