"""
collectors/memory.py — моніторинг CPU та RAM
"""
import psutil
import time
from storage import save_metric


def collect(config: dict) -> dict:
    # CPU — середнє за 5 секунд
    cpu_pct = psutil.cpu_percent(interval=5)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    # RAM
    ram = psutil.virtual_memory()
    ram_used_pct = ram.percent
    ram_free_gb = round(ram.available / 1e9, 2)
    ram_total_gb = round(ram.total / 1e9, 2)

    # Swap
    swap = psutil.swap_memory()

    # Топ процеси по CPU
    top_cpu = []
    for proc in sorted(psutil.process_iter(["name", "cpu_percent", "memory_percent"]),
                       key=lambda p: p.info["cpu_percent"] or 0, reverse=True)[:5]:
        try:
            top_cpu.append({
                "name": proc.info["name"],
                "cpu_pct": round(proc.info["cpu_percent"] or 0, 1),
                "ram_pct": round(proc.info["memory_percent"] or 0, 1),
            })
        except Exception:
            pass

    # Зберігаємо метрики
    save_metric("cpu_percent", cpu_pct)
    save_metric("ram_percent", ram_used_pct, {"free_gb": ram_free_gb})

    return {
        "cpu": {
            "percent": cpu_pct,
            "count": cpu_count,
            "freq_mhz": round(cpu_freq.current) if cpu_freq else None,
        },
        "ram": {
            "percent": ram_used_pct,
            "free_gb": ram_free_gb,
            "total_gb": ram_total_gb,
        },
        "swap": {
            "percent": swap.percent,
            "used_gb": round(swap.used / 1e9, 2),
        },
        "top_processes": top_cpu,
    }
