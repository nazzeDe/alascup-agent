from __future__ import annotations

import os

from src.config import ToolServerConfig


def read_logs(config: ToolServerConfig, path: str = "", lines: int = 50) -> list[str]:
    if not path:
        return []

    full_path = path if os.path.isabs(path) else os.path.join(config.log_path, path)
    if not os.path.isfile(full_path):
        return []

    with open(full_path, errors="replace") as f:
        all_lines = f.readlines()
    return [line.rstrip("\n") for line in all_lines[-lines:]]
