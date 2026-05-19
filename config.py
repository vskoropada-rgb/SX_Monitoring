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
    "DAILY_REPORT_HOUR", "BACKUP_ZIP_PASSWORD",
]


def _decrypt(value: str) -> str:
    if not value.startswith(_ENCRYPTED_PREFIX):
        return value
    try:
        import base64, ctypes, ctypes.wintypes

        CRYPTPROTECT_LOCAL_MACHINE = 0x4

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        raw = base64.b64decode(value[len(_ENCRYPTED_PREFIX):])
        buf = ctypes.create_string_buffer(raw, len(raw))
        inp = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        out = DATA_BLOB()

        # Пробуємо LocalMachine (як зашифровано в manage.ps1)
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None,
            CRYPTPROTECT_LOCAL_MACHINE,
            ctypes.byref(out),
        )
        if not ok:
            # Fallback: CurrentUser scope (без флагу)
            ok = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)
            )
        if not ok:
            raise OSError(f"CryptUnprotectData failed, GetLastError={ctypes.GetLastError()}")

        plaintext = ctypes.string_at(out.pbData, out.cbData)
        ctypes.windll.kernel32.LocalFree(out.pbData)
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
