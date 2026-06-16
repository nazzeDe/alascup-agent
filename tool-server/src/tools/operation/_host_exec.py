from __future__ import annotations

import os

from src.config import ToolServerConfig


def _host_cmd(cmd: list[str], config: ToolServerConfig) -> list[str]:
    """Wrap command for host execution when running in Docker.

    Detection order:
    1. TOOLSERVER_HOST_EXEC="direct" → never prefix
    2. TOOLSERVER_HOST_EXEC="chroot" → prefix with chroot /host_root
    3. TOOLSERVER_HOST_EXEC="nsenter" → prefix with nsenter -t 1 -a
    4. Auto-detect: /.dockerenv exists → nsenter prefix
    """
    if config.host_exec == "direct":
        return cmd
    if config.host_exec == "chroot":
        return ["chroot", "/host_root"] + cmd
    if config.host_exec == "nsenter" or os.path.exists("/.dockerenv"):
        return ["nsenter", "-t", "1", "-a", "--"] + cmd
    return cmd
