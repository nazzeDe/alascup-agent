"""Thin feature timing utility for main business flow.

Main flow only needs two lines:
1) start_feature("feature-name")
2) complete_feature("feature-name")

When a Profiler is active (via context variable), timing records are
delegated to the Profiler for unified reporting. Otherwise, records
are stored locally as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from time import perf_counter

from loguru import logger


@dataclass(frozen=True)
class FeatureDurationRecord:
    # 一条记录代表一次"功能"完成后的耗时结果。
    feature_name: str
    duration_ms: float
    status: str


class FeatureTimeTracker:
    """Track duration for big features and print unified logs."""

    def __init__(self) -> None:
        # 当前正在计时的功能开始时间，key 是功能名，value 是起始时间戳。
        self._active_starts: dict[str, float] = {}
        # 已完成的记录都存起来，后面可以做汇总统计。
        self._records: list[FeatureDurationRecord] = []
        # 这里加锁是为了避免多个线程同时读写时把数据弄乱。
        self._lock = Lock()

    def start_feature(self, feature_name: str) -> None:
        normalized_name = _normalize_feature_name(feature_name)
        # Delegate to active Profiler if available.
        from src.observability.profiler import get_current_profiler
        profiler = get_current_profiler()
        if profiler is not None:
            profiler.start_feature(normalized_name)
        with self._lock:
            self._active_starts[normalized_name] = perf_counter()
        logger.info("feature_started feature={feature}", feature=normalized_name)

    def complete_feature(self, feature_name: str, status: str = "success") -> float:
        normalized_name = _normalize_feature_name(feature_name)
        normalized_status = status.strip() or "success"

        with self._lock:
            started_at = self._active_starts.pop(normalized_name, None)
            if started_at is None:
                raise ValueError(
                    f"Feature '{normalized_name}' has not been started, cannot complete."
                )
            duration_ms = (perf_counter() - started_at) * 1000.0
            self._records.append(
                FeatureDurationRecord(
                    feature_name=normalized_name,
                    duration_ms=duration_ms,
                    status=normalized_status,
                )
            )

        # Delegate to active Profiler if available.
        from src.observability.profiler import get_current_profiler
        profiler = get_current_profiler()
        if profiler is not None:
            profiler.complete_feature(normalized_name, normalized_status)

        logger.info(
            "feature_completed feature={feature} status={status} duration_ms={duration}",
            feature=normalized_name,
            status=normalized_status,
            duration=_format_duration_ms(duration_ms),
        )
        return duration_ms

    def summarize_feature_durations(self) -> dict[str, float]:
        """Return average duration in ms grouped by feature."""

        with self._lock:
            # grouped 结构长这样：功能名 -> [多次耗时]
            grouped: dict[str, list[float]] = {}
            for record in self._records:
                grouped.setdefault(record.feature_name, []).append(record.duration_ms)

        # 对每个功能算平均耗时，得到一个更适合查看的摘要。
        summary = {
            feature_name: sum(values) / len(values)
            for feature_name, values in grouped.items()
        }

        # 日志里顺手把平均耗时格式化成字符串，避免小数看起来太乱。
        formatted_summary = {
            feature_name: _format_duration_ms(avg_ms)
            for feature_name, avg_ms in summary.items()
        }
        logger.info("feature_summary avg_duration_ms={summary}", summary=formatted_summary)
        return summary


def _normalize_feature_name(feature_name: str) -> str:
    # 去掉首尾空格，避免同一个功能因为多了空格被当成两个名字。
    normalized_name = feature_name.strip()
    if not normalized_name:
        raise ValueError("feature_name must not be empty")
    return normalized_name


def _format_duration_ms(duration_ms: float) -> str:
    # 统一保留两位小数，日志输出更容易读。
    return f"{duration_ms:.2f}"


# 模块级单例：主链路直接调用 start/complete/summarize 就够了，不用每次都 new tracker。
_TRACKER = FeatureTimeTracker()


def get_tracker() -> FeatureTimeTracker:
    # 对外只暴露一个获取入口，外部不用关心单例是怎么保存的。
    return _TRACKER


def start_feature(feature_name: str) -> None:
    # 主链路里通常只需要这一层薄包装，直接调用更顺手。
    get_tracker().start_feature(feature_name)


def complete_feature(feature_name: str, status: str = "success") -> float:
    # 完成时返回耗时，调用方可以拿这个值做后续展示或统计。
    return get_tracker().complete_feature(feature_name=feature_name, status=status)


def summarize_feature_durations() -> dict[str, float]:
    # 汇总结果直接返回给调用方，方便在报告里再做加工。
    return get_tracker().summarize_feature_durations()
