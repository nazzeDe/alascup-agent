from __future__ import annotations

import json
import os

from pydantic import BaseModel

_ENV_TO_FIELD: dict[str, str] = {
    "TOOLSERVER_PROC_PATH": "proc_path",
    "TOOLSERVER_SANDBOX_ROOT": "sandbox_root",
    "TOOLSERVER_CACHE_TTL": "cache_ttl",
    "TOOLSERVER_BASH_TIMEOUT": "bash_timeout",
    "TOOLSERVER_HOST_EXEC": "host_exec",
    "TOOLSERVER_PORT": "port",
}


class ToolServerConfig(BaseModel):
    proc_path: str = "/proc"
    sandbox_root: str = "/tmp/tool-server-sandbox"
    cache_ttl: int = 600
    bash_timeout: int = 30
    host_exec: str = "direct"
    port: int = 11451


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

    return config.model_copy(update=overrides) if overrides else config
