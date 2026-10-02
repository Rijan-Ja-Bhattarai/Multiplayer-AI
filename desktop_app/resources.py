"""System resource sampling for the Resources page.

Kept apart from the widgets so it can be exercised without Qt. Sampling
is cheap, but not free, so the caller decides when to poll; the page only
does it while it is on screen.

Percentages from psutil are differences between two reads, so the first
call after start-up reports zero. The module primes itself in
``__init__`` and callers get real numbers from the first sample.
"""

from __future__ import annotations

import os
import time

import psutil


class ResourceSampler:
    """Reads CPU, memory, disk and process usage.

    Args:
        disk_path: Path whose filesystem should be measured. Defaults to
            the preferences directory, which is where the app actually
            writes.
    """

    def __init__(self, disk_path=None, process=None):
        # Coerced to str because psutil.disk_usage rejects os.PathLike on
        # Windows, and Storage.directory is a Path. Passing it straight
        # through raised TypeError, which the sample() guard below turned
        # into a silently unavailable meter.
        self.disk_path = str(disk_path) if disk_path else os.path.abspath(os.sep)
        self.process = process or psutil.Process()
        self.started = time.time()
        # Prime the CPU counters, otherwise the first sample is zero.
        psutil.cpu_percent(interval=None)
        self.process.cpu_percent(interval=None)

    @staticmethod
    def _humanize_bytes(value):
        size = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
            if abs(size) < 1024 or unit == "PB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"

    def _disk_target(self):
        """A directory that certainly exists, for the filesystem query."""
        return self.disk_path if os.path.isdir(self.disk_path) else os.path.abspath(os.sep)

    def sample(self):
        """One reading of every meter.

        Returns:
            A dict with cpu (percent), per_core (list of percents),
            memory (dict), disk (dict), process (dict) and uptime (str).

        Any single probe that fails reports as unavailable rather than
        taking the whole page down: a container or a locked-down host may
        not expose everything, and a blank panel is worse than a partial
        one.
        """
        reading = {}
        reading["cpu"] = psutil.cpu_percent(interval=None)
        reading["per_core"] = psutil.cpu_percent(interval=None, percpu=True)

        try:
            memory = psutil.virtual_memory()
            reading["memory"] = {
                "used": memory.used,
                "total": memory.total,
                "percent": memory.percent,
                "used_human": self._humanize_bytes(memory.used),
                "total_human": self._humanize_bytes(memory.total),
                "available_human": self._humanize_bytes(memory.available),
            }
        except Exception:
            reading["memory"] = None

        try:
            disk = psutil.disk_usage(self._disk_target())
            reading["disk"] = {
                "used": disk.used,
                "total": disk.total,
                "free": disk.free,
                "percent": disk.percent,
                "used_human": self._humanize_bytes(disk.used),
                "total_human": self._humanize_bytes(disk.total),
                "free_human": self._humanize_bytes(disk.free),
                "path": self._disk_target(),
            }
        except Exception:
            reading["disk"] = None

        try:
            with self.process.oneshot():
                reading["process"] = {
                    "cpu": self.process.cpu_percent(interval=None),
                    "memory": self.process.memory_info().rss,
                    "memory_human": self._humanize_bytes(self.process.memory_info().rss),
                    "threads": self.process.num_threads(),
                }
        except Exception:
            reading["process"] = None

        boot = psutil.boot_time()
        seconds = max(0, int(time.time() - boot))
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, _ = divmod(seconds, 60)
        reading["uptime"] = (
            f"{days}d {hours}h {minutes}m" if days else f"{hours}h {minutes}m"
        )
        reading["core_count"] = psutil.cpu_count(logical=True)
        return reading
