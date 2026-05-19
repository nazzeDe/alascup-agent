import os
import time


class Profiler:
    """检查点剖析器，由 ALASCUP_PROFILE=1 门控。

    未开启时 checkpoint() 为零开销。
    开启后在 agent 循环各关键步骤插入检查点，记录耗时。
    每轮结束产出时间线报告，自动标记 >100ms 的慢操作。
    """

    def __init__(self) -> None:
        self._enabled = os.environ.get("ALASCUP_PROFILE") == "1"
        self._start: float | None = None
        self._checkpoints: list[dict] = []

    def checkpoint(self, name: str) -> None:
        if not self._enabled:
            return
        now = time.perf_counter()
        if self._start is None:
            self._start = now
        elapsed = (now - self._start) * 1000.0
        self._checkpoints.append({
            "name": name,
            "elapsed_ms": elapsed,
        })

    def report(self) -> dict:
        if not self._checkpoints:
            return {}
        result: dict = {}
        prev_elapsed = 0.0
        for cp in self._checkpoints:
            duration = cp["elapsed_ms"] - prev_elapsed
            result[cp["name"]] = {
                "step_ms": round(duration, 2),
                "total_ms": round(cp["elapsed_ms"], 2),
                "slow": duration > 100,
            }
            prev_elapsed = cp["elapsed_ms"]
        return result

    def reset(self) -> None:
        self._start = None
        self._checkpoints.clear()

    def is_enabled(self) -> bool:
        return self._enabled
