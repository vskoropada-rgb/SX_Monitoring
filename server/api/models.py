from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime,
    ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from database import Base


class Server(Base):
    __tablename__ = "servers"

    id         = Column(String, primary_key=True)   # SERVER_ID з агента
    name       = Column(String, nullable=False)      # COMPANY_NAME
    api_key    = Column(String, nullable=False, unique=True)
    tg_topic_id = Column(String)                    # Telegram Forum Topic
    last_seen  = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = (
        Index("idx_metrics_server_name_time", "server_id", "metric_name", "recorded_at"),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    server_id   = Column(String, ForeignKey("servers.id"), nullable=False)
    metric_name = Column(String, nullable=False)
    value       = Column(Float, nullable=False)
    extra       = Column(JSONB)
    recorded_at = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("idx_alerts_server_key_time", "server_id", "alert_key", "sent_at"),
    )

    id         = Column(Integer, primary_key=True, autoincrement=True)
    server_id  = Column(String, ForeignKey("servers.id"), nullable=False)
    alert_key  = Column(String, nullable=False)
    alert_type = Column(String)
    severity   = Column(String, nullable=False)
    message    = Column(Text)
    sent_at    = Column(DateTime, default=datetime.utcnow)


class PendingAlert(Base):
    __tablename__ = "pending_alerts"
    __table_args__ = (
        UniqueConstraint("server_id", "alert_key", name="uq_pending_server_key"),
    )

    id        = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(String, ForeignKey("servers.id"), nullable=False)
    alert_key = Column(String, nullable=False)
    title     = Column(String, nullable=False)
    body      = Column(Text, default="")
    severity  = Column(String, default="warning")
    count     = Column(Integer, default=1)
    added_at  = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Command(Base):
    __tablename__ = "commands"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    server_id     = Column(String, ForeignKey("servers.id"), nullable=False)
    action        = Column(String, nullable=False)  # block_ip | kick_session | restart_service | reboot
    params        = Column(JSONB)                   # {"ip": "1.2.3.4"} | {"session_id": "2"}
    status        = Column(String, default="pending")  # pending | executing | done | failed
    result        = Column(Text)
    tg_chat_id    = Column(String)
    tg_message_id = Column(Integer)
    created_at    = Column(DateTime, default=datetime.utcnow)
    executed_at   = Column(DateTime)


class MetricsSnapshot(Base):
    """Останній знімок метрик кожного сервера — для швидкого відображення статусу в боті."""
    __tablename__ = "metrics_snapshots"

    server_id  = Column(String, ForeignKey("servers.id"), primary_key=True)
    data       = Column(JSONB, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)


class KnownEntity(Base):
    """Відомі IP, USB, ПЗ, задачі, адміни — дедуплікація подій."""
    __tablename__ = "known_entities"
    __table_args__ = (
        UniqueConstraint("server_id", "entity_type", "value", name="uq_entity"),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    server_id   = Column(String, ForeignKey("servers.id"), nullable=False)
    entity_type = Column(String, nullable=False)   # ip | usb | software | task | admin | file_hash
    value       = Column(String, nullable=False)
    meta        = Column(JSONB)
    first_seen  = Column(DateTime, default=datetime.utcnow)
    last_seen   = Column(DateTime, default=datetime.utcnow)
