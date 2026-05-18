"""
charts.py — генерація графіків через matplotlib
"""
import os
import tempfile
from datetime import datetime
from typing import Optional
from storage import get_metrics_history, get_backup_history


def generate_chart(metric_name: str, hours: int = 24, title: str = None) -> Optional[str]:
    """Генерує PNG графік і повертає шлях до файлу"""
    try:
        import matplotlib
        matplotlib.use("Agg")  # Без GUI
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as dt

        data = get_metrics_history(metric_name, hours=hours)
        if len(data) < 2:
            return None

        times = [dt.fromisoformat(d["time"]) for d in data]
        values = [d["value"] for d in data]

        fig, ax = plt.subplots(figsize=(10, 4))
        fig.patch.set_facecolor("#1e1e2e")
        ax.set_facecolor("#1e1e2e")

        # Колір залежно від метрики
        if "cpu" in metric_name:
            color = "#f38ba8"
            label = "CPU %"
            ax.axhline(y=85, color="#fab387", linestyle="--", alpha=0.5, label="Поріг 85%")
        elif "ram" in metric_name:
            color = "#cba6f7"
            label = "RAM %"
            ax.axhline(y=90, color="#fab387", linestyle="--", alpha=0.5, label="Поріг 90%")
        elif "disk" in metric_name:
            color = "#a6e3a1"
            label = "Вільно %"
            ax.axhline(y=20, color="#f9e2af", linestyle="--", alpha=0.5, label="Поріг 20%")
            ax.axhline(y=10, color="#f38ba8", linestyle="--", alpha=0.5, label="Критично 10%")
        else:
            color = "#89b4fa"
            label = metric_name

        ax.plot(times, values, color=color, linewidth=2, label=label)
        ax.fill_between(times, values, alpha=0.15, color=color)

        # Стиль
        ax.set_ylim(0, 100)
        ax.set_xlabel("Час", color="#cdd6f4", fontsize=9)
        ax.set_ylabel("%", color="#cdd6f4", fontsize=9)
        ax.set_title(title or label, color="#cdd6f4", fontsize=11, pad=10)
        ax.tick_params(colors="#6c7086", labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours // 8)))

        for spine in ax.spines.values():
            spine.set_color("#313244")

        ax.grid(True, color="#313244", alpha=0.5, linestyle="--")
        ax.legend(facecolor="#313244", edgecolor="#45475a",
                 labelcolor="#cdd6f4", fontsize=8)

        plt.tight_layout()

        # Зберігаємо у temp файл
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        plt.savefig(tmp.name, dpi=120, bbox_inches="tight",
                   facecolor=fig.get_facecolor())
        plt.close(fig)
        return tmp.name

    except Exception as e:
        return None


def generate_combined_chart(server_id: str, hours: int = 24) -> Optional[str]:
    """Комбінований графік CPU + RAM"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as dt

        cpu_data = get_metrics_history("cpu_percent", hours=hours)
        ram_data = get_metrics_history("ram_percent", hours=hours)

        if len(cpu_data) < 2 and len(ram_data) < 2:
            return None

        fig, ax = plt.subplots(figsize=(10, 5))
        fig.patch.set_facecolor("#1e1e2e")
        ax.set_facecolor("#1e1e2e")

        if cpu_data:
            times = [dt.fromisoformat(d["time"]) for d in cpu_data]
            values = [d["value"] for d in cpu_data]
            ax.plot(times, values, color="#f38ba8", linewidth=2, label="CPU %")
            ax.fill_between(times, values, alpha=0.1, color="#f38ba8")

        if ram_data:
            times = [dt.fromisoformat(d["time"]) for d in ram_data]
            values = [d["value"] for d in ram_data]
            ax.plot(times, values, color="#cba6f7", linewidth=2, label="RAM %")
            ax.fill_between(times, values, alpha=0.1, color="#cba6f7")

        ax.axhline(y=85, color="#fab387", linestyle="--", alpha=0.4, linewidth=1)

        ax.set_ylim(0, 100)
        ax.set_title(f"CPU та RAM — останні {hours}г", color="#cdd6f4", fontsize=11)
        ax.set_xlabel("Час", color="#cdd6f4", fontsize=9)
        ax.set_ylabel("%", color="#cdd6f4", fontsize=9)
        ax.tick_params(colors="#6c7086", labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=max(1, hours // 8)))

        for spine in ax.spines.values():
            spine.set_color("#313244")

        ax.grid(True, color="#313244", alpha=0.5, linestyle="--")
        ax.legend(facecolor="#313244", edgecolor="#45475a",
                 labelcolor="#cdd6f4", fontsize=9)

        plt.tight_layout()

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        plt.savefig(tmp.name, dpi=120, bbox_inches="tight",
                   facecolor=fig.get_facecolor())
        plt.close(fig)
        return tmp.name

    except Exception as e:
        return None


def generate_backup_chart(days: int = 30) -> Optional[str]:
    """Графік розміру ZIP-бекапів за N днів"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as dt

        history = get_backup_history(days=days)
        if len(history) < 2:
            return None

        times = [dt.fromisoformat(r["detected_at"]) for r in history]
        sizes = [r["size_bytes"] / (1024 * 1024) for r in history]

        fig, ax = plt.subplots(figsize=(10, 4))
        fig.patch.set_facecolor("#1e1e2e")
        ax.set_facecolor("#1e1e2e")

        color = "#89dceb"
        ax.bar(times, sizes, color=color, alpha=0.6, width=0.6)
        ax.plot(times, sizes, color=color, linewidth=1.5, marker="o", markersize=4)

        ax.set_title(f"Розмір ZIP-бекапів за {days} днів", color="#cdd6f4", fontsize=11, pad=10)
        ax.set_xlabel("Дата", color="#cdd6f4", fontsize=9)
        ax.set_ylabel("МБ", color="#cdd6f4", fontsize=9)
        ax.tick_params(colors="#6c7086", labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())

        for spine in ax.spines.values():
            spine.set_color("#313244")
        ax.grid(True, color="#313244", alpha=0.5, linestyle="--", axis="y")

        plt.tight_layout()
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        plt.savefig(tmp.name, dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return tmp.name

    except Exception:
        return None
