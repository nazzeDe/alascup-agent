from __future__ import annotations

from typing import Any

from src.error.types import ServerError


def succeeded(payload: dict | None = None) -> dict:
    result = dict(payload or {})
    result.setdefault("execution_status", "SUCCEEDED")
    return result


def normalize_result(payload: dict | None = None) -> dict[str, Any]:
    """Project a tool function payload onto the execute_tool result contract."""
    result = dict(payload or {})
    if result.get("error") is not None:
        result["error"] = _normalize_error(result["error"])
        result["execution_status"] = "FAILED"
    else:
        result.setdefault("execution_status", "SUCCEEDED")

    if "output" not in result:
        output = {
            key: value
            for key, value in result.items()
            if key not in {"execution_status", "error"}
        }
        if output:
            result["output"] = output
    return result


def failed(payload: dict | None = None) -> dict:
    result = dict(payload or {})
    result["execution_status"] = "FAILED"
    return result


def failed_error(error: ServerError) -> dict:
    return failed({"error": error.to_jsonrpc()})


def _normalize_error(error: Any) -> dict[str, Any]:
    if isinstance(error, dict):
        return error
    return {"message": str(error)}
