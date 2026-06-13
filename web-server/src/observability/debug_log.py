"""Agent decision-narrative debug logger.

Enabled via ALASCUP_AGENT_TRACE=1. Produces logs/debug/<ts>-<session>.log
with a logs/debug/latest symlink. When disabled, keeps a 500-line
in-memory ring buffer for post-mortem inspection.
"""

import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path("logs/debug")
RING_SIZE = 500

_enabled: bool = os.environ.get("ALASCUP_AGENT_TRACE", "").strip() == "1"
_level: str = os.environ.get("ALASCUP_AGENT_TRACE_LEVEL", "INFO").strip().upper()
_filters: list[str] = [
    f for f in os.environ.get("ALASCUP_AGENT_TRACE_FILTER", "").split(",") if f
]

_ring: deque[str] = deque(maxlen=RING_SIZE)
_lock = threading.Lock()
_file_path: Path | None = None
_initialized = False


def _init_file() -> None:
    global _file_path, _initialized
    if _initialized:
        return
    _initialized = True
    if not _enabled:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    pid = os.getpid()
    _file_path = LOG_DIR / f"{ts}-{pid}.log"
    latest = LOG_DIR / "latest"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(_file_path.name)


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


_init_file()
