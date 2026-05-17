"""
collectors/backup.py — перевірка бекапів
"""
import os
import glob
from datetime import datetime, timedelta
from pathlib import Path


def collect(config: dict) -> dict:
    backup_path = config.get("BACKUP_PATH", "")
    max_age_hours = int(config.get("BACKUP_MAX_AGE_HOURS", 25))
    min_size_mb = float(config.get("BACKUP_MIN_SIZE_MB", 50))

    if not backup_path or not os.path.exists(backup_path):
        return {
            "status": "error",
            "error": f"Папка бекапів не знайдена: {backup_path}",
            "backup_path": backup_path,
        }

    # Знаходимо всі файли бекапів
    extensions = ["*.bak", "*.zip", "*.7z", "*.tar", "*.gz", "*.dt", "*.1cd"]
    all_files = []
    for ext in extensions:
        all_files.extend(glob.glob(os.path.join(backup_path, "**", ext), recursive=True))
        all_files.extend(glob.glob(os.path.join(backup_path, ext)))

    if not all_files:
        return {
            "status": "no_files",
            "backup_path": backup_path,
            "error": "Файли бекапів не знайдені",
        }

    # Знаходимо найновіший файл
    latest_file = max(all_files, key=os.path.getmtime)
    latest_mtime = datetime.fromtimestamp(os.path.getmtime(latest_file))
    latest_size_mb = round(os.path.getsize(latest_file) / 1e6, 2)
    age_hours = round((datetime.now() - latest_mtime).total_seconds() / 3600, 1)

    # Всі файли за останні 48 годин
    recent_files = []
    for f in all_files:
        mtime = datetime.fromtimestamp(os.path.getmtime(f))
        if datetime.now() - mtime < timedelta(hours=48):
            recent_files.append({
                "name": os.path.basename(f),
                "size_mb": round(os.path.getsize(f) / 1e6, 2),
                "age_hours": round((datetime.now() - mtime).total_seconds() / 3600, 1),
                "time": mtime.strftime("%Y-%m-%d %H:%M"),
            })

    recent_files.sort(key=lambda x: x["age_hours"])

    # Перевірки
    is_too_old = age_hours > max_age_hours
    is_too_small = latest_size_mb < min_size_mb

    status = "ok"
    issues = []

    if is_too_old:
        status = "warning"
        issues.append(f"Останній бекап {age_hours}г тому (ліміт {max_age_hours}г)")

    if is_too_small:
        status = "warning"
        issues.append(f"Розмір бекапу {latest_size_mb}MB менше мінімуму {min_size_mb}MB")

    return {
        "status": status,
        "issues": issues,
        "latest_file": os.path.basename(latest_file),
        "latest_size_mb": latest_size_mb,
        "latest_age_hours": age_hours,
        "latest_time": latest_mtime.strftime("%Y-%m-%d %H:%M"),
        "recent_files": recent_files[:5],
        "total_files": len(all_files),
        "backup_path": backup_path,
    }
