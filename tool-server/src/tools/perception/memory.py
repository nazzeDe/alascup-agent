from __future__ import annotations

import psutil

from src.config import ToolServerConfig


def get_memory_info(config: ToolServerConfig) -> dict:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    return {
        "mem_total_gb": round(mem.total / (1024**3), 1),
        "mem_used_gb": round(mem.used / (1024**3), 1),
        "mem_free_gb": round(mem.available / (1024**3), 1),
        "mem_usage_percent": mem.percent,
        "swap_total_gb": round(swap.total / (1024**3), 1),
        "swap_used_gb": round(swap.used / (1024**3), 1),
    }
