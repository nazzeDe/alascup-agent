from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

import psutil

from src.config import ToolServerConfig

_STARTED_AT = time.time()


def get_tool_server_status(
    config: ToolServerConfig, tool_count: int | None = None
) -> dict[str, Any]:
    proc = psutil.Process()
    started_at = proc.create_time()
    return {
        "status": "healthy",
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "process_name": proc.name(),
        "started_at": started_at,
        "uptime_seconds": round(time.time() - _STARTED_AT, 3),
        "process_uptime_seconds": round(time.time() - started_at, 3),
        "python": {
            "version": sys.version.split()[0],
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "cwd": os.getcwd(),
        "tool_count": tool_count,
        "config": {
            "proc_path": config.proc_path,
            "log_dir": config.log_dir,
            "sandbox_root": config.sandbox_root,
            "cache_ttl": config.cache_ttl,
            "bash_timeout": config.bash_timeout,
            "host_exec": config.host_exec,
            "port": config.port,
            "postgres_configured": bool(config.postgres_dsn),
            "postgres_statement_timeout_ms": config.postgres_statement_timeout_ms,
            "postgres_max_rows": config.postgres_max_rows,
        },
        "resources": {
            "cpu_percent": proc.cpu_percent(interval=0.0),
            "rss_mb": round(proc.memory_info().rss / (1024**2), 2),
            "open_files": len(proc.open_files()),
            "threads": proc.num_threads(),
        },
    }


def get_tool_server_logs(
    config: ToolServerConfig,
    filename: str | None = None,
    lines: int = 200,
) -> dict[str, Any]:
    log_dir = Path(config.log_dir).expanduser().resolve()
    line_limit = min(max(lines, 1), 1000)

    if not log_dir.exists() or not log_dir.is_dir():
        return _log_error(log_dir, filename, [], "log_dir not found")

    available_files = _list_log_files(log_dir)
    selected = _resolve_log_file(log_dir, filename, available_files)
    if selected is None:
        return _log_error(log_dir, filename, available_files, "log file not found")

    if not _is_inside(selected, log_dir) or not selected.is_file():
        return _log_error(log_dir, filename, available_files, "invalid log file")

    return {
        "log_dir": str(log_dir),
        "filename": selected.name,
        "lines": _tail_lines(selected, line_limit),
        "line_limit": line_limit,
        "available_files": [path.name for path in available_files],
    }


def _resolve_log_file(
    log_dir: Path, filename: str | None, available_files: list[Path]
) -> Path | None:
    if filename:
        candidate = Path(filename)
        if candidate.is_absolute() or ".." in candidate.parts:
            return None
        return (log_dir / candidate).resolve()
    return available_files[0] if available_files else None


def _log_error(
    log_dir: Path, filename: str | None, available_files: list[Path], error: str
) -> dict[str, Any]:
    return {
        "log_dir": str(log_dir),
        "filename": filename,
        "lines": [],
        "available_files": [path.name for path in available_files],
        "error": error,
    }


def _list_log_files(log_dir: Path) -> list[Path]:
    files = [
        path for path in log_dir.iterdir() if path.is_file() and not path.is_symlink()
    ]
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _tail_lines(path: Path, limit: int) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()[-limit:]
    except OSError as exc:
        return [f"ERROR: {exc}"]
