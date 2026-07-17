from __future__ import annotations

import json
import os
from ipaddress import ip_address

from pydantic import BaseModel, model_validator

_ENV_TO_FIELD: dict[str, str] = {
    "TOOLSERVER_PROC_PATH": "proc_path",
    "TOOLSERVER_LOG_DIR": "log_dir",
    "TOOLSERVER_SANDBOX_ROOT": "sandbox_root",
    "TOOLSERVER_CACHE_TTL": "cache_ttl",
    "TOOLSERVER_BASH_TIMEOUT": "bash_timeout",
    "TOOLSERVER_HOST_EXEC": "host_exec",
    "TOOLSERVER_HOST": "host",
    "TOOLSERVER_PORT": "port",
    "POSTGRES_DSN": "postgres_dsn",
    "TOOLSERVER_POSTGRES_STATEMENT_TIMEOUT_MS": "postgres_statement_timeout_ms",
    "TOOLSERVER_POSTGRES_MAX_ROWS": "postgres_max_rows",
    "TOOLSERVER_SHARED_SECRET": "shared_secret",
}

DEFAULT_BASH_TIMEOUT_SECONDS = 300
MAX_BASH_TIMEOUT_SECONDS = 600


class ToolServerConfig(BaseModel):
    proc_path: str = "/proc"
    log_dir: str = "/app/logs"
    sandbox_root: str = "/app/sandbox"
    cache_ttl: int = 600
    bash_timeout: int = DEFAULT_BASH_TIMEOUT_SECONDS
    host_exec: str = "direct"
    host: str = "127.0.0.1"
    port: int = 11451
    postgres_dsn: str | None = None
    postgres_statement_timeout_ms: int = 5000
    postgres_max_rows: int = 1000
    shared_secret: str = ""

    @model_validator(mode="after")
    def _require_secret_for_non_loopback(self) -> "ToolServerConfig":
        if not _is_loopback_host(self.host) and not self.shared_secret:
            raise ValueError(
                "TOOLSERVER_SHARED_SECRET is required for non-loopback hosts"
            )
        return self


def load_config() -> ToolServerConfig:
    config_path = os.environ.get("TOOLSERVER_CONFIG_PATH", "/app/config.json")

    try:
        with open(config_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    config = ToolServerConfig(**data)

    overrides = {}
    for env_key, field_name in _ENV_TO_FIELD.items():
        val = os.environ.get(env_key)
        if val is not None:
            field_type = ToolServerConfig.model_fields[field_name].annotation
            overrides[field_name] = int(val) if field_type is int else val

    return (
        ToolServerConfig(**{**config.model_dump(), **overrides})
        if overrides
        else config
    )


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False
