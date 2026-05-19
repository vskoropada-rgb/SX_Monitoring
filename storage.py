"""
storage.py — SQLite для стану, дедуплікації алертів та зберігання метрик
"""
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "monitor.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
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

        CREATE TABLE IF NOT EXISTS metrics (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL,
            value       REAL NOT NULL,
            extra       TEXT,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name, recorded_at);

        CREATE TABLE IF NOT EXISTS known_ips (
            ip          TEXT PRIMARY KEY,
            username    TEXT,
            first_seen  DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen   DATETIME DEFAULT CURRENT_TIMESTAMP,
            count       INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS file_hashes (
            file_path   TEXT PRIMARY KEY,
            hash        TEXT NOT NULL,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS service_states (
            service_name TEXT PRIMARY KEY,
            status       TEXT NOT NULL,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS known_admins (
            username    TEXT PRIMARY KEY,
            added_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Режим обслуговування (maintenance) per server
        CREATE TABLE IF NOT EXISTS maintenance (
            server_id TEXT PRIMARY KEY,
            until_ts  DATETIME NOT NULL
        );

        -- Історія бекапів (для графіку розміру та розкладу)
        CREATE TABLE IF NOT EXISTS backup_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            filename    TEXT NOT NULL,
            size_bytes  INTEGER NOT NULL,
            mtime       DATETIME NOT NULL,
            detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            integrity   TEXT DEFAULT 'unknown'
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_backup_fname ON backup_history(filename);
        CREATE INDEX IF NOT EXISTS idx_backup_det ON backup_history(detected_at);

        -- Відомі USB-пристрої
        CREATE TABLE IF NOT EXISTS known_usb (
            instance_id   TEXT PRIMARY KEY,
            friendly_name TEXT,
            first_seen    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Встановлене ПЗ (для виявлення нового)
        CREATE TABLE IF NOT EXISTS known_software (
            name       TEXT PRIMARY KEY,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Відомі Task Scheduler завдання
        CREATE TABLE IF NOT EXISTS known_tasks (
            task_name  TEXT PRIMARY KEY,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        -- Заблоковані IP (через Telegram бот → Windows Firewall)
        CREATE TABLE IF NOT EXISTS blocked_ips (
            ip         TEXT PRIMARY KEY,
            blocked_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Кеш останніх метрик для швидкого відображення в боті
        CREATE TABLE IF NOT EXISTS metrics_cache (
            key        TEXT PRIMARY KEY,
            data       TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)


# ─── Alerts ──────────────────────────────────────────────────

def can_send_alert(alert_key: str, cooldown_min: int = 30) -> bool:
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


# ─── Metrics ─────────────────────────────────────────────────

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
            "SELECT value, recorded_at FROM metrics "
            "WHERE metric_name = ? AND recorded_at > ? ORDER BY recorded_at",
            (metric_name, since.isoformat())
        ).fetchall()
    return [{"value": r["value"], "time": r["recorded_at"]} for r in rows]


# ─── Known IPs ───────────────────────────────────────────────

def is_known_ip(ip: str) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM known_ips WHERE ip = ?", (ip,)).fetchone() is not None


def register_ip(ip: str, username: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO known_ips (ip, username) VALUES (?, ?)
            ON CONFLICT(ip) DO UPDATE SET
                last_seen=CURRENT_TIMESTAMP, count=count+1,
                username=excluded.username
        """, (ip, username))


# ─── File hashes ─────────────────────────────────────────────

def get_file_hash(file_path: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT hash FROM file_hashes WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row["hash"] if row else None


def update_file_hash(file_path: str, hash_val: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO file_hashes (file_path, hash) VALUES (?, ?)
            ON CONFLICT(file_path) DO UPDATE SET
                hash=excluded.hash, updated_at=CURRENT_TIMESTAMP
        """, (file_path, hash_val))


# ─── Service states ──────────────────────────────────────────

def get_service_state(service_name: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM service_states WHERE service_name = ?", (service_name,)
        ).fetchone()
        return row["status"] if row else None


def update_service_state(service_name: str, status: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO service_states (service_name, status) VALUES (?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                status=excluded.status, updated_at=CURRENT_TIMESTAMP
        """, (service_name, status))


# ─── Admins ──────────────────────────────────────────────────

def get_known_admins() -> set:
    with get_conn() as conn:
        rows = conn.execute("SELECT username FROM known_admins").fetchall()
        return {r["username"] for r in rows}


def add_known_admin(username: str):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO known_admins (username) VALUES (?)", (username,))


# ─── Maintenance ─────────────────────────────────────────────

def set_maintenance(server_id: str, until: datetime):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO maintenance (server_id, until_ts) VALUES (?, ?)
            ON CONFLICT(server_id) DO UPDATE SET until_ts=excluded.until_ts
        """, (server_id, until.isoformat()))


def clear_maintenance(server_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM maintenance WHERE server_id = ?", (server_id,))


def is_maintenance(server_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT until_ts FROM maintenance WHERE server_id = ?", (server_id,)
        ).fetchone()
        if not row:
            return False
        until = datetime.fromisoformat(row["until_ts"])
        if datetime.now() > until:
            clear_maintenance(server_id)
            return False
        return True


def get_maintenance_until(server_id: str) -> Optional[datetime]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT until_ts FROM maintenance WHERE server_id = ?", (server_id,)
        ).fetchone()
        if not row:
            return None
        until = datetime.fromisoformat(row["until_ts"])
        return until if datetime.now() <= until else None


# ─── Backup history ──────────────────────────────────────────

def is_known_backup(filename: str) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM backup_history WHERE filename = ?", (filename,)
        ).fetchone() is not None


def record_backup(filename: str, size_bytes: int, mtime: str, integrity: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO backup_history (filename, size_bytes, mtime, integrity)
            VALUES (?, ?, ?, ?)
        """, (filename, size_bytes, mtime, integrity))


def update_backup_integrity(filename: str, integrity: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE backup_history SET integrity = ? WHERE filename = ?",
            (integrity, filename),
        )


def get_backup_integrity(filename: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT integrity FROM backup_history WHERE filename = ?", (filename,)
        ).fetchone()
        return row["integrity"] if row else None


def get_backup_history(days: int = 30) -> list:
    since = datetime.now() - timedelta(days=days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT filename, size_bytes, mtime, detected_at, integrity "
            "FROM backup_history WHERE detected_at > ? ORDER BY detected_at",
            (since.isoformat(),)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── USB devices ─────────────────────────────────────────────

def is_known_usb(instance_id: str) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM known_usb WHERE instance_id = ?", (instance_id,)
        ).fetchone() is not None


def register_usb(instance_id: str, friendly_name: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO known_usb (instance_id, friendly_name) VALUES (?, ?)",
            (instance_id, friendly_name)
        )


# ─── Software ────────────────────────────────────────────────

def is_known_software(name: str) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM known_software WHERE name = ?", (name,)
        ).fetchone() is not None


def register_software(name: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO known_software (name) VALUES (?)", (name,)
        )


# ─── Scheduled tasks ─────────────────────────────────────────

def is_known_task(task_name: str) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM known_tasks WHERE task_name = ?", (task_name,)
        ).fetchone() is not None


def register_task(task_name: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO known_tasks (task_name) VALUES (?)", (task_name,)
        )


# ─── Metrics cache ───────────────────────────────────────────

def cache_metrics(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO metrics_cache (key, data) VALUES ('last', ?)
            ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP
        """, (json.dumps(data, default=str),))


def load_metrics_cache() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT data FROM metrics_cache WHERE key='last'"
        ).fetchone()
        return json.loads(row["data"]) if row else {}


# ─── Blocked IPs ─────────────────────────────────────────────

def record_blocked_ip(ip: str):
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO blocked_ips (ip) VALUES (?)", (ip,))


def remove_blocked_ip(ip: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM blocked_ips WHERE ip = ?", (ip,))


def get_blocked_ips() -> set:
    with get_conn() as conn:
        rows = conn.execute("SELECT ip FROM blocked_ips").fetchall()
        return {r["ip"] for r in rows}


# ─── Cleanup ─────────────────────────────────────────────────

def cleanup_old_metrics(days: int = 30):
    cutoff = datetime.now() - timedelta(days=days)
    with get_conn() as conn:
        conn.execute("DELETE FROM metrics WHERE recorded_at < ?", (cutoff.isoformat(),))
        # Backup history зберігаємо 90 днів для графіку тренду
        cutoff_backup = datetime.now() - timedelta(days=90)
        conn.execute("DELETE FROM backup_history WHERE detected_at < ?", (cutoff_backup.isoformat(),))
