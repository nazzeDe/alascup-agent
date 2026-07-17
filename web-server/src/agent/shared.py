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


def approval_result_error(approval_status: str | None) -> dict[str, str]:
    """Build a stable error category and message for an approval outcome."""
    if approval_status == "EXPIRED":
        return {
            "data": "approval_expired",
            "message": "Tool approval expired before a decision was received. Do NOT retry.",
        }
    return {
        "data": "human_rejected",
        "message": "Tool was rejected by human. Do NOT retry.",
    }


def normalize_tool_result(value: Any) -> dict[str, Any]:
    """Normalize structured tool results before they enter agent state."""
    if not isinstance(value, dict):
        return {
            "execution_status": "FAILED",
            "error": {"message": str(value)},
        }

    result = dict(value)
    if result.get("error") is not None:
        result["error"] = _normalize_error(result["error"])
        result["execution_status"] = "FAILED"
    else:
        result.setdefault("execution_status", "SUCCEEDED")

    if "output" not in result:
        output = {
            key: item
            for key, item in result.items()
            if key not in {"execution_status", "error"}
        }
        if output:
            result["output"] = output
    return result


def _normalize_error(error: Any) -> dict[str, Any]:
    if isinstance(error, dict):
        return error
    return {"message": str(error)}
