"""Agent loop checkpoint profiler.

Enabled via ALASCUP_PROFILE=1. Inserts timing and memory checkpoints
at key loop stages. Produces a timeline report each iteration,
auto-flagging operations exceeding 100ms.

Output: logs/profile/<ts>-<pid>.log with logs/profile/latest symlink.
"""

import os
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

PROFILE_DIR = Path("logs/profile")
_enabled: bool = os.environ.get("ALASCUP_PROFILE", "").strip() == "1"

_file_path: Path | None = None
_initialized = False


def _init_file() -> None:
    global _file_path, _initialized
    if _initialized:
        return
    _initialized = True
    if not _enabled:
        return
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    _file_path = PROFILE_DIR / f"{ts}-{os.getpid()}.log"
    latest = PROFILE_DIR / "latest"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(_file_path.name)


class Checkpoint:
    __slots__ = ("label", "elapsed_ms", "mem_kb")

    def __init__(self, label: str, elapsed_ms: float, mem_kb: float) -> None:
        self.label = label
        self.elapsed_ms = elapsed_ms
        self.mem_kb = mem_kb


class Profiler:
    """Per-request profiler — one instance per agent run."""

    def __init__(self) -> None:
        self._checkpoints: list[Checkpoint] = []
        self._start = time.monotonic()
        self._last = self._start
        if _enabled:
            tracemalloc.start()

    def checkpoint(self, label: str) -> None:
        if not _enabled:
            return
        now = time.monotonic()
        elapsed = (now - self._last) * 1000
        self._last = now
        mem = tracemalloc.get_traced_memory()
        self._checkpoints.append(Checkpoint(label, elapsed, mem[0] / 1024))

    def report(self) -> str | None:
        if not _enabled or not self._checkpoints:
            return None
        total_ms = (time.monotonic() - self._start) * 1000
        lines = [
            f"Profile ({len(self._checkpoints)} steps, {total_ms:.1f}ms):"
        ]
        for c in self._checkpoints:
            flag = " ⚠ SLOW" if c.elapsed_ms > 100 else ""
            lines.append(
                f"  {c.label:30s} {c.elapsed_ms:8.1f}ms  mem={c.mem_kb:.0f}KB{flag}"
            )
        tracemalloc.stop()
        return "\n".join(lines)


def write_profile(report: str) -> None:
    """Write a profile report to the profile log file."""
    if not _enabled or not _file_path:
        return
    try:
        with open(_file_path, "a") as f:
            f.write(report + "\n")
            f.flush()
    except OSError:
        pass


def profiler_enabled() -> bool:
    return _enabled


_init_file()
