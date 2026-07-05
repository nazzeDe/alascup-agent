from __future__ import annotations

import psutil

from src.config import ToolServerConfig


def get_process_list(config: ToolServerConfig) -> list[dict]:
    processes = []
    try:
        for proc in psutil.process_iter(
            ["pid", "name", "cpu_times", "memory_info", "status"]
        ):
            try:
                info = proc.info
                cpu_time = 0.0
                if info.get("cpu_times"):
                    cpu_time = info["cpu_times"].user + info["cpu_times"].system
                mem_bytes = 0
                if info.get("memory_info"):
                    mem_bytes = info["memory_info"].rss
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"],
                        "cpu_time": round(cpu_time, 2),
                        "mem_mb": round(mem_bytes / (1024**2), 1),
                        "status": info["status"],
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return processes
