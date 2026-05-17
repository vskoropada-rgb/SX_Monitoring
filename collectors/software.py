"""
collectors/software.py — виявлення нового встановленого ПЗ через реєстр
"""
import winreg
import logging
import storage

logger = logging.getLogger(__name__)

_UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _get_installed_software() -> set:
    names = set()
    for hive, key_path in _UNINSTALL_KEYS:
        try:
            key = winreg.OpenKey(hive, key_path)
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                        if name and name.strip():
                            names.add(name.strip())
                    except (FileNotFoundError, OSError):
                        pass
                    winreg.CloseKey(subkey)
                except (FileNotFoundError, OSError):
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            logger.debug("Software registry read: %s", e)
    return names


def collect(config: dict) -> dict:
    current = _get_installed_software()
    new_software = []

    for name in current:
        if not storage.is_known_software(name):
            storage.register_software(name)
            new_software.append(name)

    return {
        "new_software": new_software,
        "software_count": len(current),
    }
