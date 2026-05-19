from __future__ import annotations

from src import ToolServerConfig
from src.cache import ToolCache
from src.error.types import execution_failed, security_violation, tool_not_found
from src.tools.registry import get_tool


def handle_execute_tool(
    tool_name: str,
    chat_id: str,
    message_id: str,
    params: dict,
    request_id: str,
    approval_status: str,
    config: ToolServerConfig,
    cache: ToolCache,
) -> dict:
    """Dispatch execute_tool request with full request-id security pipeline."""
    from src.security.validate import validate_execution

    tool_meta = get_tool(tool_name)
    if tool_meta is None:
        err = tool_not_found(tool_name)
        return {"execution_status": "FAILED", "error": err.to_jsonrpc()}

    ok, err = validate_execution(approval_status, request_id, tool_meta.is_read_only)
    if not ok:
        return {"execution_status": "FAILED", "error": security_violation(err).to_jsonrpc()}

    if request_id:
        cached = cache.get(request_id)
        if cached is not None:
            return cached

    try:
        result = tool_meta.fn(config, **params)
    except Exception as exc:
        result = {"execution_status": "FAILED", "error": execution_failed(str(exc)).to_jsonrpc()}

    result.setdefault("execution_status", "SUCCEEDED")

    if request_id:
        cache.put(request_id, result)

    return result
