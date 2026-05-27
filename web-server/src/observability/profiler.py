"""Agent loop checkpoint profiler.

Enabled via ALASCUP_PROFILE=1. Inserts timing and memory checkpoints
at key loop stages. Produces a timeline report each iteration,
auto-flagging operations exceeding 100ms.

Also supports feature-level timing (start_feature/complete_feature)
that integrates with the FeatureTimeTracker for unified reporting.

Output: logs/profile/<ts>-<pid>.log with logs/profile/latest symlink.
"""

import contextvars
import os
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

PROFILE_DIR = Path("logs/profile")
_enabled: bool = os.environ.get("ALASCUP_PROFILE", "").strip() == "1"

_file_path: Path | None = None
_initialized = False

# Context variable so FeatureTimeTracker can delegate to the active Profiler.
_current_profiler: contextvars.ContextVar["Profiler | None"] = contextvars.ContextVar(
    "current_profiler", default=None
)


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


class FeatureRecord:
    __slots__ = ("name", "duration_ms", "status")

    def __init__(self, name: str, duration_ms: float, status: str) -> None:
        self.name = name
        self.duration_ms = duration_ms
        self.status = status


class Profiler:
    """Per-request profiler — one instance per agent run."""

    def __init__(self) -> None:
        self._checkpoints: list[Checkpoint] = []
        self._features: list[FeatureRecord] = []
        self._feature_starts: dict[str, float] = {}
        self._start = time.monotonic()
        self._last = self._start
        self._token: contextvars.Token | None = None
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

    def start_feature(self, name: str) -> None:
        self._feature_starts[name] = time.perf_counter()

    def complete_feature(self, name: str, status: str = "success") -> float:
        started = self._feature_starts.pop(name, None)
        if started is None:
            return 0.0
        duration_ms = (time.perf_counter() - started) * 1000.0
        self._features.append(FeatureRecord(name, duration_ms, status))
        return duration_ms

    def feature_summary(self) -> dict[str, float]:
        grouped: dict[str, list[float]] = {}
        for f in self._features:
            grouped.setdefault(f.name, []).append(f.duration_ms)
        return {name: sum(v) / len(v) for name, v in grouped.items()}

    def activate(self) -> None:
        """Set this profiler as the current one via context variable."""
        self._token = _current_profiler.set(self)

    def deactivate(self) -> None:
        """Reset the context variable."""
        if self._token is not None:
            _current_profiler.reset(self._token)
            self._token = None

    def report(self) -> str | None:
        if not _enabled and not self._features:
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
        if self._features:
            lines.append("  Features:")
            for f in self._features:
                lines.append(f"    {f.name:28s} {f.duration_ms:8.1f}ms  [{f.status}]")
        if _enabled:
            tracemalloc.stop()
        return "\n".join(lines)


def get_current_profiler() -> Profiler | None:
    """Return the active Profiler for this context, or None."""
    return _current_profiler.get(None)


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
