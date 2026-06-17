"""Shared helpers for agent-facing adapters."""

import asyncio
import json
from collections.abc import Callable
from typing import Any


async def is_disconnected(disconnect_check: Callable[[], Any] | None) -> bool:
    """Evaluate optional disconnect callback, accepting sync or async results."""
    if not disconnect_check:
        return False
    result = disconnect_check()
    if asyncio.iscoroutine(result):
        result = await result
    return bool(result)


def parse_json_object(value: Any) -> dict:
    """Parse object-ish JSON input. Invalid/non-dict values become {}."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def error_message(error: Any) -> str:
    """Normalize error payload to message text."""
    if isinstance(error, dict):
        return error.get("message", "")
    return str(error) if error else ""
