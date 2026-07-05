from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

import psutil

from src.config import ToolServerConfig


MAX_PROCESS_LIMIT = 200


def get_process_list(config: ToolServerConfig, top_n: int = 50) -> dict:
    limit = min(max(top_n, 1), MAX_PROCESS_LIMIT)
    processes = []
    try:
        with _procfs_root(config.proc_path):
            for proc in psutil.process_iter(
                ["pid", "name", "cpu_times", "memory_info", "status"]
            ):
                try:
                    processes.append(_process_info(proc.info))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
    except (FileNotFoundError, PermissionError, OSError):
        pass
    sorted_processes = sorted(
        processes,
        key=lambda process: (process["cpu_time"], process["mem_mb"], process["pid"]),
        reverse=True,
    )
    return {
        "processes": sorted_processes[:limit],
        "limit": limit,
        "max_limit": MAX_PROCESS_LIMIT,
        "total_seen": len(sorted_processes),
        "truncated": len(sorted_processes) > limit,
    }


def _process_info(info: dict) -> dict:
    cpu_time = 0.0
    if info.get("cpu_times"):
        cpu_time = info["cpu_times"].user + info["cpu_times"].system
    mem_bytes = 0
    if info.get("memory_info"):
        mem_bytes = info["memory_info"].rss
    return {
        "pid": info["pid"],
        "name": info["name"],
        "cpu_time": round(cpu_time, 2),
        "mem_mb": round(mem_bytes / (1024**2), 1),
        "status": info["status"],
    }


@contextmanager
def _procfs_root(proc_path: str) -> Iterator[None]:
    previous = psutil.PROCFS_PATH
    psutil.PROCFS_PATH = proc_path
    try:
        yield
    finally:
        psutil.PROCFS_PATH = previous
