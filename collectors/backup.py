"""
collectors/backup.py — перевірка ZIP-бекапів: цілісність, розклад, тренд розміру
"""
import os
import glob
import zipfile
import logging
from collections import Counter
from datetime import datetime, timedelta
import storage

logger = logging.getLogger(__name__)


def _check_zip(filepath: str, password: str = None) -> str:
    """Перевіряє ZIP-архів. Повертає: 'ok' | 'encrypted' | 'corrupted' | 'too_small'"""
    if os.path.getsize(filepath) <= 1024:
        return "too_small"
    try:
        with zipfile.ZipFile(filepath) as zf:
            if password:
                zf.setpassword(password.encode())
            result = zf.testzip()
            return "ok" if result is None else "corrupted"
    except RuntimeError as e:
        msg = str(e).lower()
        if "password" in msg or "encrypted" in msg:
            return "encrypted"
        return "corrupted"
    except zipfile.BadZipFile:
        return "corrupted"
    except Exception:
        return "error"


def _get_expected_backup_hour() -> int | None:
    """Вираховує типову годину бекапу на основі历史"""
    history = storage.get_backup_history(days=30)
    if len(history) < 5:
        return None
    hours = []
    for r in history:
        try:
            hours.append(datetime.fromisoformat(r["detected_at"]).hour)
        except Exception:
            pass
    if not hours:
        return None
    return Counter(hours).most_common(1)[0][0]


def collect(config: dict) -> dict:
    backup_path   = config.get("BACKUP_PATH", "")
    max_age_hours = int(config.get("BACKUP_MAX_AGE_HOURS", 25))
    min_size_mb   = float(config.get("BACKUP_MIN_SIZE_MB", 10))
    zip_password  = config.get("BACKUP_ZIP_PASSWORD", "")

    if not backup_path or not os.path.exists(backup_path):
        return {
            "status": "error",
            "error": f"Папка бекапів не знайдена: {backup_path}",
            "backup_path": backup_path,
        }

    all_files = glob.glob(os.path.join(backup_path, "*.zip"))

    expected_hour = _get_expected_backup_hour()

    if not all_files:
        now = datetime.now()
        if expected_hour is not None and now.hour >= expected_hour + 2:
            return {
                "status": "warning",
                "issues": [f"Бекапів не знайдено, очікувався о ~{expected_hour:02d}:00"],
                "backup_path": backup_path,
                "schedule_missed": True,
                "expected_backup_hour": expected_hour,
            }
        return {"status": "no_files", "backup_path": backup_path,
                "error": "ZIP-файли не знайдені"}

    # Найновіший файл
    latest_file      = max(all_files, key=os.path.getmtime)
    latest_mtime     = datetime.fromtimestamp(os.path.getmtime(latest_file))
    latest_size_bytes = os.path.getsize(latest_file)
    latest_size_mb   = round(latest_size_bytes / 1e6, 2)
    age_hours        = round((datetime.now() - latest_mtime).total_seconds() / 3600, 1)

    # Реєструємо нові файли в storage + перевіряємо цілісність
    latest_integrity = "unknown"
    for f in all_files:
        fname = os.path.basename(f)
        if not storage.is_known_backup(fname):
            size  = os.path.getsize(f)
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            integ = _check_zip(f, zip_password or None)
            storage.record_backup(fname, size, mtime.isoformat(), integ)
            logger.info("Новий бекап: %s (%sMB) — %s", fname, round(size/1e6,1), integ)
            if f == latest_file:
                latest_integrity = integ
        elif f == latest_file:
            latest_integrity = storage.get_backup_integrity(fname) or "ok"

    # Останні файли за 48г
    recent_files = []
    for f in all_files:
        mtime = datetime.fromtimestamp(os.path.getmtime(f))
        if datetime.now() - mtime < timedelta(hours=48):
            recent_files.append({
                "name":      os.path.basename(f),
                "size_mb":   round(os.path.getsize(f) / 1e6, 2),
                "age_hours": round((datetime.now() - mtime).total_seconds() / 3600, 1),
                "time":      mtime.strftime("%Y-%m-%d %H:%M"),
            })
    recent_files.sort(key=lambda x: x["age_hours"])

    # Розклад: чи пропущено очікуваний бекап?
    schedule_info   = f"~{expected_hour:02d}:00" if expected_hour is not None else None
    schedule_missed = (
        expected_hour is not None
        and datetime.now().hour >= expected_hour + 3
        and age_hours > 23
    )

    # Список проблем
    issues = []
    status = "ok"

    if latest_integrity == "corrupted":
        status = "critical"
        issues.append("ZIP-архів пошкоджений!")
    elif latest_integrity == "too_small":
        status = "warning"
        issues.append("Архів менше 1 KB — можливо порожній")
    elif latest_integrity == "encrypted":
        issues.append("Архів зашифрований (цілісність без пароля не перевірена)")

    if latest_size_mb < min_size_mb and latest_integrity not in ("corrupted", "too_small"):
        status = "warning" if status == "ok" else status
        issues.append(f"Розмір {latest_size_mb}MB менше мінімуму {min_size_mb}MB")

    if age_hours > max_age_hours:
        status = "warning" if status == "ok" else status
        issues.append(f"Останній бекап {age_hours}г тому (ліміт {max_age_hours}г)")

    if schedule_missed:
        status = "warning" if status == "ok" else status
        issues.append(f"Очікувався бекап о {schedule_info}, але немає свіжого")

    return {
        "status":               status,
        "issues":               issues,
        "latest_file":          os.path.basename(latest_file),
        "latest_size_mb":       latest_size_mb,
        "latest_size_bytes":    latest_size_bytes,
        "latest_age_hours":     age_hours,
        "latest_time":          latest_mtime.strftime("%Y-%m-%d %H:%M"),
        "latest_integrity":     latest_integrity,
        "recent_files":         recent_files[:5],
        "total_files":          len(all_files),
        "backup_path":          backup_path,
        "expected_backup_hour": expected_hour,
        "schedule_info":        schedule_info,
        "schedule_missed":      schedule_missed,
    }
