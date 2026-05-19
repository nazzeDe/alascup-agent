from __future__ import annotations

import shutil
import subprocess

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


def get_top_dirs(config: ToolServerConfig, path: str = "/") -> list[dict]:
    """Return top-10 largest top-level directories using du."""
    try:
        result = subprocess.run(
            ["du", "-sm", "--max-depth=1", path],
            capture_output=True, text=True, timeout=30,
        )
        lines = result.stdout.strip().split("\n")
        entries = []
        for line in lines:
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                size_mb = int(parts[0])
                dir_path = parts[1]
                if dir_path != path:
                    entries.append({"path": dir_path, "size_mb": size_mb})
        entries.sort(key=lambda e: e["size_mb"], reverse=True)
        return entries[:10]
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return []
