from __future__ import annotations

import os

from src.config import ToolServerConfig


def _host_cmd(cmd: list[str], config: ToolServerConfig) -> list[str]:
    """Wrap command with nsenter when running in Docker to execute on host.

    Detection order:
    1. TOOLSERVER_HOST_EXEC="nsenter" → always prefix
    2. TOOLSERVER_HOST_EXEC="direct" → never prefix
    3. Auto-detect: /.dockerenv exists → prefix
    """
    if config.host_exec == "direct":
        return cmd
    if config.host_exec == "nsenter" or os.path.exists("/.dockerenv"):
        return ["nsenter", "-t", "1", "-a", "--"] + cmd
    return cmd
