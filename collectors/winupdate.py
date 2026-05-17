"""
collectors/winupdate.py — перевірка очікуваного перезавантаження Windows
"""
import winreg
import logging

logger = logging.getLogger(__name__)

_REBOOT_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
]


def collect(config: dict) -> dict:
    reboot_required = False
    reasons = []

    for key_path in _REBOOT_KEYS:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
            winreg.CloseKey(key)
            reboot_required = True
            reasons.append(key_path.split("\\")[-1])
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug("winupdate registry read: %s", e)

    # PendingFileRenameOperations — незакінчені апдейти
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager"
        )
        try:
            val, _ = winreg.QueryValueEx(key, "PendingFileRenameOperations")
            if val:
                reboot_required = True
                reasons.append("PendingFileRename")
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except Exception as e:
        logger.debug("PendingFileRename check: %s", e)

    return {
        "reboot_required": reboot_required,
        "reboot_reasons": reasons,
    }
