"""
storage.py — SQLite для стану, дедуплікації алертів та зберігання метрик
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "monitor.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        -- Алерти (дедуплікація)
        CREATE TABLE IF NOT EXISTS alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key   TEXT NOT NULL,
            alert_type  TEXT NOT NULL,
            severity    TEXT NOT NULL,
            message     TEXT,
            sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        );
        CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts(alert_key, sent_at);

        -- Метрики (для графіків)
        CREATE TABLE IF NOT EXISTS metrics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL,
            value       REAL NOT NULL,
            extra       TEXT,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name, recorded_at);

        -- Відомі IP для RDP
        CREATE TABLE IF NOT EXISTS known_ips (
            ip          TEXT PRIMARY KEY,
            username    TEXT,
            first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP,
            count       INTEGER DEFAULT 1
        );

        -- Хеші критичних файлів
        CREATE TABLE IF NOT EXISTS file_hashes (
            file_path   TEXT PRIMARY KEY,
            hash        TEXT NOT NULL,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Стан сервісів
        CREATE TABLE IF NOT EXISTS service_states (
            service_name TEXT PRIMARY KEY,
            status       TEXT NOT NULL,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Адміністратори системи
        CREATE TABLE IF NOT EXISTS known_admins (
            username    TEXT PRIMARY KEY,
            added_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)


def can_send_alert(alert_key: str, cooldown_min: int = 30) -> bool:
    """Перевіряє чи пройшов кулдаун для повторного алерту"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sent_at FROM alerts WHERE alert_key = ? ORDER BY sent_at DESC LIMIT 1",
            (alert_key,)
        ).fetchone()
        if not row:
            return True
        last = datetime.fromisoformat(row["sent_at"])
        return datetime.now() - last > timedelta(minutes=cooldown_min)


def record_alert(alert_key: str, alert_type: str, severity: str, message: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alerts (alert_key, alert_type, severity, message) VALUES (?, ?, ?, ?)",
            (alert_key, alert_type, severity, message)
        )


def save_metric(metric_name: str, value: float, extra: dict = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO metrics (metric_name, value, extra) VALUES (?, ?, ?)",
            (metric_name, value, json.dumps(extra) if extra else None)
        )


def get_metrics_history(metric_name: str, hours: int = 24) -> list:
    since = datetime.now() - timedelta(hours=hours)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT value, recorded_at FROM metrics WHERE metric_name = ? AND recorded_at > ? ORDER BY recorded_at",
            (metric_name, since.isoformat())
        ).fetchall()
    return [{"value": r["value"], "time": r["recorded_at"]} for r in rows]


def is_known_ip(ip: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT ip FROM known_ips WHERE ip = ?", (ip,)).fetchone()
        return row is not None


def register_ip(ip: str, username: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO known_ips (ip, username) VALUES (?, ?)
            ON CONFLICT(ip) DO UPDATE SET last_seen=CURRENT_TIMESTAMP, count=count+1, username=excluded.username
        """, (ip, username))


def get_file_hash(file_path: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT hash FROM file_hashes WHERE file_path = ?", (file_path,)).fetchone()
        return row["hash"] if row else None


def update_file_hash(file_path: str, hash_val: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO file_hashes (file_path, hash) VALUES (?, ?)
            ON CONFLICT(file_path) DO UPDATE SET hash=excluded.hash, updated_at=CURRENT_TIMESTAMP
        """, (file_path, hash_val))


def get_service_state(service_name: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM service_states WHERE service_name = ?", (service_name,)).fetchone()
        return row["status"] if row else None


def update_service_state(service_name: str, status: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO service_states (service_name, status) VALUES (?, ?)
            ON CONFLICT(service_name) DO UPDATE SET status=excluded.status, updated_at=CURRENT_TIMESTAMP
        """, (service_name, status))


def get_known_admins() -> set:
    with get_conn() as conn:
        rows = conn.execute("SELECT username FROM known_admins").fetchall()
        return {r["username"] for r in rows}


def add_known_admin(username: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO known_admins (username) VALUES (?)", (username,)
        )


def cleanup_old_metrics(days: int = 30):
    """Видаляє старі метрики щоб БД не росла"""
    cutoff = datetime.now() - timedelta(days=days)
    with get_conn() as conn:
        conn.execute("DELETE FROM metrics WHERE recorded_at < ?", (cutoff.isoformat(),))
