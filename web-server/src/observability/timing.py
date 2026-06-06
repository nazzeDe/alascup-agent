"""Feature timing with optional per-request profiling.

Always-on: global feature duration aggregation consumed by /api/metrics.
Profiling mode (ALASCUP_PROFILE=1): per-request checkpoints, memory tracking,
and disk output to logs/profile/<ts>-<pid>.log.

Merged from the former ``src/tools/feature_time_tracker.py`` and
``src/observability/profiler.py``.
"""

from __future__ import annotations

import os
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from loguru import logger

# ---------------------------------------------------------------------------
# Profiling infrastructure (import-time config)
# ---------------------------------------------------------------------------

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


def profiler_enabled() -> bool:
    return _enabled


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



# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureDurationRecord:
    """One completed feature timing record."""

    feature_name: str
    duration_ms: float
    status: str


class Checkpoint:
    """A single timing + memory checkpoint in a profiling run."""

    __slots__ = ("label", "elapsed_ms", "mem_kb")

    def __init__(self, label: str, elapsed_ms: float, mem_kb: float) -> None:
        self.label = label
        self.elapsed_ms = elapsed_ms
        self.mem_kb = mem_kb


class FeatureRecord:
    """A single feature timing record scoped to one profiling run."""

    __slots__ = ("name", "duration_ms", "status")

    def __init__(self, name: str, duration_ms: float, status: str) -> None:
        self.name = name
        self.duration_ms = duration_ms
        self.status = status


# ---------------------------------------------------------------------------
# FeatureTimeTracker
# ---------------------------------------------------------------------------


class FeatureTimeTracker:
    """Feature timing + optional per-request profiling.

    Each instance records feature durations independently — the module-level
    singleton ``_TRACKER`` serves as the global aggregator for ``/api/metrics``.

    When *profile_enabled* is True, the instance additionally collects
    per-request checkpoints, per-request feature records, and memory
    snapshots — producing a timeline report via :meth:`report`.
    """

    def __init__(self, profile_enabled: bool = False) -> None:
        # ---- feature timing (always active, instance-local) ----
        self._active_starts: dict[str, float] = {}
        self._records: list[FeatureDurationRecord] = []
        self._lock = Lock()

        # ---- profiling state (only when enabled) ----
        self._profile_enabled = profile_enabled
        self._checkpoints: list[Checkpoint] = []
        self._features: list[FeatureRecord] = []
        self._feature_starts: dict[str, float] = {}
        self._start = time.monotonic()
        self._last = self._start
        if profile_enabled:
            tracemalloc.start()

    # -- feature timing -------------------------------------------------------

    def start_feature(self, name: str) -> None:
        normalized = _normalize_feature_name(name)

        with self._lock:
            self._active_starts[normalized] = time.perf_counter()

        if self._profile_enabled:
            self._feature_starts[normalized] = time.perf_counter()

        logger.info("feature_started feature={feature}", feature=normalized)

    def complete_feature(self, name: str, status: str = "success") -> float:
        normalized = _normalize_feature_name(name)
        normalized_status = status.strip() or "success"

        with self._lock:
            started_at = self._active_starts.pop(normalized, None)
            if started_at is None:
                raise ValueError(
                    f"Feature '{normalized}' has not been started, cannot complete."
                )
            duration_ms = (time.perf_counter() - started_at) * 1000.0
            self._records.append(
                FeatureDurationRecord(
                    feature_name=normalized,
                    duration_ms=duration_ms,
                    status=normalized_status,
                )
            )

        if self._profile_enabled:
            local_started = self._feature_starts.pop(normalized, None)
            if local_started is not None:
                local_duration = (time.perf_counter() - local_started) * 1000.0
                self._features.append(
                    FeatureRecord(normalized, local_duration, normalized_status)
                )

        logger.info(
            "feature_completed feature={feature} status={status} duration_ms={duration}",
            feature=normalized,
            status=normalized_status,
            duration=_format_duration_ms(duration_ms),
        )
        return duration_ms

    def summarize_feature_durations(self) -> dict[str, float]:
        """Average duration in ms grouped by feature name."""
        with self._lock:
            grouped: dict[str, list[float]] = {}
            for record in self._records:
                grouped.setdefault(record.feature_name, []).append(record.duration_ms)

        summary = {
            feature_name: sum(values) / len(values)
            for feature_name, values in grouped.items()
        }

        formatted = {k: _format_duration_ms(v) for k, v in summary.items()}
        logger.info("feature_summary avg_duration_ms={summary}", summary=formatted)
        return summary

    # -- per-request profiling (no-ops when profile_enabled=False) ------------

    def checkpoint(self, label: str) -> None:
        if not self._profile_enabled:
            return
        now = time.monotonic()
        elapsed = (now - self._last) * 1000
        self._last = now
        mem = tracemalloc.get_traced_memory()
        self._checkpoints.append(Checkpoint(label, elapsed, mem[0] / 1024))

    def feature_summary(self) -> dict[str, float]:
        """Average duration per feature for this profiling run only."""
        grouped: dict[str, list[float]] = {}
        for f in self._features:
            grouped.setdefault(f.name, []).append(f.duration_ms)
        return {name: sum(v) / len(v) for name, v in grouped.items()}

    def report(self) -> str | None:
        if not self._profile_enabled and not self._features:
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
                lines.append(
                    f"    {f.name:28s} {f.duration_ms:8.1f}ms  [{f.status}]"
                )
        if self._profile_enabled:
            tracemalloc.stop()
        return "\n".join(lines)



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_feature_name(feature_name: str) -> str:
    normalized = feature_name.strip()
    if not normalized:
        raise ValueError("feature_name must not be empty")
    return normalized


def _format_duration_ms(duration_ms: float) -> str:
    return f"{duration_ms:.2f}"


# ---------------------------------------------------------------------------
# Module-level singleton and convenience functions
# ---------------------------------------------------------------------------

_TRACKER = FeatureTimeTracker()


def get_tracker() -> FeatureTimeTracker:
    return _TRACKER


def start_feature(feature_name: str) -> None:
    get_tracker().start_feature(feature_name)


def complete_feature(feature_name: str, status: str = "success") -> float:
    return get_tracker().complete_feature(name=feature_name, status=status)


def summarize_feature_durations() -> dict[str, float]:
    return get_tracker().summarize_feature_durations()


# ---------------------------------------------------------------------------
# Import-time init
# ---------------------------------------------------------------------------

_init_file()
