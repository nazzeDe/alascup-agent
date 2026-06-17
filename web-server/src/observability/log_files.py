"""Shared log-file initialization helpers."""

import os
from datetime import datetime, timezone
from pathlib import Path


def init_timestamped_log_file(enabled: bool, directory: Path) -> Path | None:
    """Create timestamped log file and latest symlink when enabled."""
    if not enabled:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    file_path = directory / f"{ts}-{os.getpid()}.log"
    latest = directory / "latest"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(file_path.name)
    return file_path
