import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}


class DebugLogger:
    """调试日志，由 ALASCUP_DEBUG=1 门控文件输出。

    未开启时仅写入内存环形缓冲（最近 500 条）。
    开启后同步写入文件 + 环形缓冲。
    """

    def __init__(self) -> None:
        self._enabled = os.environ.get("ALASCUP_DEBUG") == "1"
        self._level = os.environ.get("ALASCUP_DEBUG_LEVEL", "INFO")
        self._filter = os.environ.get("ALASCUP_DEBUG_FILTER", "")
        self._ring: deque[str] = deque(maxlen=500)
        self._file_path: Path | None = None

    def debug(self, msg: str) -> None:
        self._write("DEBUG", msg)

    def info(self, msg: str) -> None:
        self._write("INFO", msg)

    def warn(self, msg: str) -> None:
        self._write("WARN", msg)

    def error(self, msg: str) -> None:
        self._write("ERROR", msg)

    def _write(self, level: str, msg: str) -> None:
        if self._filter and self._filter not in msg:
            return
        if not self._level_passes(level):
            return
        ts = datetime.now(timezone.utc).isoformat()
        line = f"[{ts}] [{level}] {msg}"
        self._ring.append(line)
        if self._enabled:
            self._ensure_file()
            if self._file_path:
                with open(self._file_path, "a") as f:
                    f.write(line + "\n")

    def _level_passes(self, level: str) -> bool:
        threshold = _LEVEL_ORDER.get(self._level, 1)
        return _LEVEL_ORDER.get(level, 1) >= threshold

    def _ensure_file(self) -> None:
        if self._file_path is not None:
            return
        log_dir = Path("logs/debug")
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self._file_path = log_dir / f"{ts}.log"
        latest = log_dir / "latest"
        if latest.is_symlink() or not latest.exists():
            if latest.exists():
                latest.unlink()
            latest.symlink_to(self._file_path.name)

    def recent(self, n: int = 50) -> list[str]:
        return list(self._ring)[-n:]

    def is_enabled(self) -> bool:
        return self._enabled
