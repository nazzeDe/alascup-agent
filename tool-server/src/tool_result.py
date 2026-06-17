from __future__ import annotations

from src.error.types import ServerError


def succeeded(payload: dict | None = None) -> dict:
    result = dict(payload or {})
    result.setdefault("execution_status", "SUCCEEDED")
    return result


def failed(payload: dict | None = None) -> dict:
    result = dict(payload or {})
    result["execution_status"] = "FAILED"
    return result


def failed_error(error: ServerError) -> dict:
    return failed({"error": error.to_jsonrpc()})
