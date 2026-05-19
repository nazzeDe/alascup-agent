from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolServerConfig:
    proc_path: str = field(default_factory=lambda: os.environ.get("TOOLSERVER_PROC_PATH", "/proc"))
    sys_path: str = field(default_factory=lambda: os.environ.get("TOOLSERVER_SYS_PATH", "/sys"))
    log_path: str = field(default_factory=lambda: os.environ.get("TOOLSERVER_LOG_PATH", "/var/log"))
    sandbox_root: str = field(default_factory=lambda: os.environ.get("TOOLSERVER_SANDBOX_ROOT", "/tmp/tool-server-sandbox"))
    cache_ttl: int = field(default_factory=lambda: int(os.environ.get("TOOLSERVER_CACHE_TTL", "600")))
    bash_timeout: int = field(default_factory=lambda: int(os.environ.get("TOOLSERVER_BASH_TIMEOUT", "30")))


def load_config() -> ToolServerConfig:
    return ToolServerConfig()
