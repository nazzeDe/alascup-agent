"""Agent decision-narrative debug logger.

Enabled via ALASCUP_AGENT_TRACE=1. Produces logs/debug/<ts>-<session>.log
with a logs/debug/latest symlink. When disabled, keeps a 500-line
in-memory ring buffer for post-mortem inspection.

Usage::

    from src.observability.debug_log import log, traced
    from src.observability import trace_points as tp

    log("DEBUG", tp.THINK_DONE, tool_calls=3, latency_ms=120)
    with traced(tp.LOOP_ITERATION, iteration=5):
        ...
"""

import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from src.observability.log_files import init_timestamped_log_file

LOG_DIR = Path("logs/debug")
RING_SIZE = 500

_enabled: bool = os.environ.get("ALASCUP_AGENT_TRACE", "").strip() == "1"
_level: str = os.environ.get("ALASCUP_AGENT_TRACE_LEVEL", "INFO").strip().upper()
_filters: list[str] = [
    f for f in os.environ.get("ALASCUP_AGENT_TRACE_FILTER", "").split(",") if f
]

_ring: deque[str] = deque(maxlen=RING_SIZE)
_lock = threading.Lock()
_file_path: Path | None = init_timestamped_log_file(_enabled, LOG_DIR)


def _should_log(level: str) -> bool:
    order = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
    return order.get(level, 1) >= order.get(_level, 1)


def _matches_filter(message: str) -> bool:
    if not _filters:
        return True
    msg_lower = message.lower()
    return any(f.lower() in msg_lower for f in _filters)


def log(level: str, message: str, **ctx) -> None:
    """Write a debug narrative entry with optional context."""
    if not _should_log(level):
        return
    if not _matches_filter(message):
        return

    ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else ""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    line = f"[{ts}] {level:<5} {message}"
    if ctx_str:
        line += f"  {ctx_str}"

    with _lock:
        _ring.append(line)
        if _enabled and _file_path:
            try:
                with open(_file_path, "a") as f:
                    f.write(line + "\n")
                    f.flush()
            except OSError:
                pass


def dump_recent(n: int = 100) -> list[str]:
    """Return the most recent ring-buffer entries for inspection."""
    with _lock:
        return list(_ring)[-n:]


@contextmanager
def traced(phase: str, **ctx):
    """Auto-log enter/exit with elapsed_ms. On exception, log error and re-raise.

    Usage::

        with traced("act_node", tool_count=3):
            results = await execute_tools(...)
    """
    log("DEBUG", f"{phase}_enter", **ctx)
    t0 = time.monotonic()
    try:
        yield
    except Exception as exc:
        log("ERROR", f"{phase}_error", error=str(exc), **ctx)
        raise
    else:
        elapsed_ms = (time.monotonic() - t0) * 1000
        log("DEBUG", f"{phase}_exit", elapsed_ms=round(elapsed_ms, 2), **ctx)
