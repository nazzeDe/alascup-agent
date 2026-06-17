from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from src.config import ToolServerConfig


@dataclass(frozen=True)
class HostCommand:
    argv: list[str]
    cwd: str | None


class HostExecutionAdapter(Protocol):
    def prepare(self, cmd: list[str], config: ToolServerConfig) -> HostCommand: ...


class DirectHostExecution:
    def prepare(self, cmd: list[str], config: ToolServerConfig) -> HostCommand:
        return HostCommand(argv=cmd, cwd=config.sandbox_root)


class NsenterHostExecution:
    def prepare(self, cmd: list[str], config: ToolServerConfig) -> HostCommand:
        return HostCommand(argv=["nsenter", "-t", "1", "-a", "--"] + cmd, cwd=None)


class ChrootHostExecution:
    def prepare(self, cmd: list[str], config: ToolServerConfig) -> HostCommand:
        return HostCommand(argv=["chroot", "/host_root"] + cmd, cwd=None)


def prepare_host_command(cmd: list[str], config: ToolServerConfig) -> HostCommand:
    """Prepare argv and cwd for host execution.

    Detection order:
    1. TOOLSERVER_HOST_EXEC="direct" → never prefix
    2. TOOLSERVER_HOST_EXEC="chroot" → prefix with chroot /host_root
    3. TOOLSERVER_HOST_EXEC="nsenter" → prefix with nsenter -t 1 -a
    4. Auto-detect: /.dockerenv exists → nsenter prefix
    """
    return _select_adapter(config).prepare(cmd, config)


def _select_adapter(config: ToolServerConfig) -> HostExecutionAdapter:
    if config.host_exec == "direct":
        return DirectHostExecution()
    if config.host_exec == "chroot":
        return ChrootHostExecution()
    if config.host_exec == "nsenter" or os.path.exists("/.dockerenv"):
        return NsenterHostExecution()
    return DirectHostExecution()
