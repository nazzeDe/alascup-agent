from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.config import ToolServerConfig


@dataclass(frozen=True)
class ToolMeta:
    name: str
    description: str
    is_read_only: bool  # default classification; may be overridden by classify_tool for bash/systemd
    input_schema: dict[str, Any]
    fn: Callable  # fn(config: ToolServerConfig, **params) -> dict


_registry: dict[str, ToolMeta] = {}


def register(meta: ToolMeta) -> None:
    _registry[meta.name] = meta


def get_tool(name: str) -> ToolMeta | None:
    return _registry.get(name)


def list_tools() -> list[ToolMeta]:
    return list(_registry.values())


def clear_registry() -> None:
    _registry.clear()
