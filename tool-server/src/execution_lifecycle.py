from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from loguru import logger

from src.cache import ToolCache
from src.error.types import execution_failed, security_violation, tool_not_found
from src.security.validate import validate_execution
from src.tool_result import failed_error, succeeded


async def execute_tool_lifecycle(
    server: FastMCP,
    tool_name: str,
    chat_id: str,
    params: dict,
    request_id: str,
    approval_status: str,
    cache: ToolCache,
) -> dict:
    """Run lookup, approval gate, idempotency cache, invocation, and normalization."""
    tool = await server.get_tool(tool_name)
    if tool is None:
        logger.warning(
            "tool_not_found tool={name} chat_id={cid}", name=tool_name, cid=chat_id
        )
        return failed_error(tool_not_found(tool_name))

    is_read_only = (tool.meta or {}).get("is_read_only", False)
    ok, err = validate_execution(approval_status, request_id, is_read_only)
    if not ok:
        logger.warning(
            "security_violation tool={name} reason={r} chat_id={cid}",
            name=tool_name,
            r=err,
            cid=chat_id,
        )
        return failed_error(security_violation(err))

    cached = _get_cached_result(cache, request_id)
    if cached is not None:
        return cached

    result = await _invoke_tool(tool.fn, params, tool_name, chat_id)
    result = succeeded(result)

    status = result.get("execution_status", "?")
    logger.info(
        "tool_result tool={name} status={s} chat_id={cid}",
        name=tool_name,
        s=status,
        cid=chat_id,
    )

    if request_id:
        cache.put(request_id, result)

    return result


def _get_cached_result(cache: ToolCache, request_id: str) -> dict | None:
    if not request_id:
        return None
    cached = cache.get(request_id)
    if cached is not None:
        logger.debug("cache_hit request_id={rid}", rid=request_id)
    return cached


async def _invoke_tool(fn, params: dict, tool_name: str, chat_id: str) -> dict:
    logger.info(
        "tool_execute tool={name} params={p} chat_id={cid}",
        name=tool_name,
        p=params,
        cid=chat_id,
    )
    try:
        result = fn(**params)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as exc:
        logger.opt(exception=True).error(
            "tool_failed tool={name} chat_id={cid}", name=tool_name, cid=chat_id
        )
        return failed_error(execution_failed(str(exc)))
