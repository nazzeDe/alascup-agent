from __future__ import annotations

import shutil

from src.config import ToolServerConfig


def get_disk_usage(config: ToolServerConfig, path: str = "/") -> dict:
    usage = shutil.disk_usage(path)

    total_gb = round(usage.total / (1024**3), 1)
    used_gb = round(usage.used / (1024**3), 1)
    free_gb = round(usage.free / (1024**3), 1)
    percent = round(100.0 * usage.used / usage.total, 1) if usage.total > 0 else 0.0

    return {
        "partition": path,
        "disk_total_gb": total_gb,
        "disk_used_gb": used_gb,
        "disk_free_gb": free_gb,
        "disk_usage_percent": percent,
    }
