from __future__ import annotations

import os

from loguru import logger

from src import ToolServerConfig
from src.cache import ToolCache
from src.error.types import execution_failed, security_violation, tool_not_found
from src.tools.registry import get_tool

LOG_LEVEL = os.getenv("TOOL_SERVER_LOG_LEVEL", "WARNING").upper()


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
        logger.warning("tool_not_found tool={name} chat_id={cid}", name=tool_name, cid=chat_id)
        err = tool_not_found(tool_name)
        return {"execution_status": "FAILED", "error": err.to_jsonrpc()}

    ok, err = validate_execution(approval_status, request_id, tool_meta.is_read_only)
    if not ok:
        logger.warning("security_violation tool={name} reason={r} chat_id={cid}", name=tool_name, r=err, cid=chat_id)
        return {"execution_status": "FAILED", "error": security_violation(err).to_jsonrpc()}

    if request_id:
        cached = cache.get(request_id)
        if cached is not None:
            logger.debug("cache_hit request_id={rid}", rid=request_id)
            return cached

    logger.info("tool_execute tool={name} params={p} chat_id={cid}", name=tool_name, p=params, cid=chat_id)
    try:
        result = tool_meta.fn(config, **params)
    except Exception as exc:
        logger.opt(exception=True).error("tool_failed tool={name} chat_id={cid}", name=tool_name, cid=chat_id)
        result = {"execution_status": "FAILED", "error": execution_failed(str(exc)).to_jsonrpc()}

    result.setdefault("execution_status", "SUCCEEDED")
    status = result.get("execution_status", "?")
    logger.info("tool_result tool={name} status={s} chat_id={cid}", name=tool_name, s=status, cid=chat_id)

    if request_id:
        cache.put(request_id, result)

    return result
