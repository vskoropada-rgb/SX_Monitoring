"""
config.py — завантаження конфігурації з .env, розшифровка DPAPI-захищених полів
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_ENV_PATH        = Path(__file__).parent / ".env"
_ENCRYPTED_PREFIX = "ENCRYPTED:"
_SENSITIVE        = {"TG_BOT_TOKEN", "TG_GROUP_ID", "OPENAI_API_KEY"}

_ALL_KEYS = [
    "SERVER_ID", "COMPANY_NAME",
    "TG_BOT_TOKEN", "TG_GROUP_ID", "TG_TOPIC_ID",
    "OPENAI_API_KEY", "OPENAI_MODEL",
    "DISK_PATHS", "DISK_WARNING_PERCENT", "DISK_CRITICAL_PERCENT",
    "CPU_WARNING_PERCENT", "RAM_WARNING_PERCENT", "CPU_CHECK_INTERVAL_SEC",
    "BRUTE_FORCE_WINDOW_MIN", "BRUTE_FORCE_THRESHOLD", "KNOWN_IPS",
    "BACKUP_PATH", "BACKUP_MAX_AGE_HOURS", "BACKUP_MIN_SIZE_MB",
    "MONITOR_SERVICES", "WATCH_FILES",
    "CHECK_INTERVAL_SEC", "ALERT_COOLDOWN_MIN", "LOG_LEVEL",
]


def _decrypt(value: str) -> str:
    if not value.startswith(_ENCRYPTED_PREFIX):
        return value
    try:
        import base64
        import win32crypt
        data = base64.b64decode(value[len(_ENCRYPTED_PREFIX):])
        # CRYPTPROTECT_LOCAL_MACHINE = 4 — matches PowerShell DataProtectionScope.LocalMachine
        _, plaintext = win32crypt.CryptUnprotectData(data, None, None, None, None, 4)
        return plaintext.decode("utf-8")
    except Exception as e:
        logger.error("Не вдалося розшифрувати значення конфігурації: %s", e)
        return ""


def load() -> dict:
    load_dotenv(_ENV_PATH, override=True)
    result = {}
    for key in _ALL_KEYS:
        raw = os.getenv(key, "")
        result[key] = _decrypt(raw) if key in _SENSITIVE else raw
    return result
