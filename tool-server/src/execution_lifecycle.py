from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP
from loguru import logger

from src.cache import ToolCache
from src.error.types import execution_failed, security_violation, tool_not_found
from src.security.execution_context import controlled_execution
from src.security.validate import validate_execution
from src.tool_result import failed_error, normalize_result


@dataclass(frozen=True)
class _InflightRegistration:
    owner: bool
    future: asyncio.Future | None = None
    wait_for: asyncio.Future | None = None


async def execute_tool_lifecycle(
    server: FastMCP,
    tool_name: str,
    chat_id: str,
    params: dict,
    request_id: str,
    approval_status: str,
    cache: ToolCache,
    auth_token: str = "",
    shared_secret: str = "",
) -> dict:
    """Run lookup, approval gate, idempotency cache, invocation, and normalization."""
    tool = await _lookup_tool(server, tool_name, chat_id)
    if isinstance(tool, dict):
        return tool

    is_read_only = (tool.meta or {}).get("is_read_only", False)
    security_error = _validate_tool_request(
        approval_status,
        request_id,
        is_read_only,
        auth_token,
        shared_secret,
        tool_name,
        chat_id,
    )
    if security_error is not None:
        return security_error

    context = _cache_context(chat_id, tool_name, params)
    reusable, inflight = await _reusable_result_or_inflight(
        cache, request_id, context, tool_name, chat_id
    )
    if reusable is not None:
        return reusable

    return await _invoke_and_record_result(
        tool.fn,
        params,
        tool_name,
        chat_id,
        not is_read_only,
        cache,
        request_id,
        context,
        inflight,
    )


async def _invoke_and_record_result(
    fn,
    params: dict,
    tool_name: str,
    chat_id: str,
    controlled: bool,
    cache: ToolCache,
    request_id: str,
    context: dict[str, str],
    inflight: _InflightRegistration,
) -> dict:
    try:
        result = await _invoke_tool(fn, params, tool_name, chat_id, controlled)
        return _record_successful_result(
            cache, request_id, context, inflight, result, tool_name, chat_id
        )
    except BaseException as exc:
        _fail_owned_inflight(inflight, exc)
        raise
    finally:
        if inflight.owner and request_id:
            cache.evict_inflight(request_id)


def _record_successful_result(
    cache: ToolCache,
    request_id: str,
    context: dict[str, str],
    inflight: _InflightRegistration,
    result: dict,
    tool_name: str,
    chat_id: str,
) -> dict:
    result = normalize_result(result)
    status = result.get("execution_status", "?")
    logger.info(
        "tool_result tool={name} status={s} chat_id={cid}",
        name=tool_name,
        s=status,
        cid=chat_id,
    )
    if request_id:
        _put_cached_result(cache, request_id, context, result)
        _resolve_owned_inflight(inflight, result)
    return result


async def _reusable_result_or_inflight(
    cache: ToolCache,
    request_id: str,
    context: dict[str, str],
    tool_name: str,
    chat_id: str,
) -> tuple[dict | None, _InflightRegistration]:
    cached = _get_cached_result(cache, request_id, context, tool_name, chat_id)
    if cached is not None:
        return cached, _InflightRegistration(owner=False)

    inflight = _join_or_register_inflight(
        cache, request_id, context, tool_name, chat_id
    )
    if isinstance(inflight, dict):
        return inflight, _InflightRegistration(owner=False)
    if inflight.wait_for is None:
        return None, inflight

    logger.debug("inflight_wait request_id={rid}", rid=request_id)
    return await asyncio.shield(inflight.wait_for), _InflightRegistration(owner=False)


async def _lookup_tool(server: FastMCP, tool_name: str, chat_id: str):
    tool = await server.get_tool(tool_name)
    if tool is not None:
        return tool
    logger.warning(
        "tool_not_found tool={name} chat_id={cid}", name=tool_name, cid=chat_id
    )
    return failed_error(tool_not_found(tool_name))


def _validate_tool_request(
    approval_status: str,
    request_id: str,
    is_read_only: bool,
    auth_token: str,
    shared_secret: str,
    tool_name: str,
    chat_id: str,
) -> dict | None:
    ok, err = validate_execution(
        approval_status,
        request_id,
        is_read_only,
        auth_token=auth_token,
        shared_secret=shared_secret,
    )
    if ok:
        return None
    logger.warning(
        "security_violation tool={name} reason={r} chat_id={cid}",
        name=tool_name,
        r=err,
        cid=chat_id,
    )
    return failed_error(security_violation(err))


def _join_or_register_inflight(
    cache: ToolCache,
    request_id: str,
    context: dict[str, str],
    tool_name: str,
    chat_id: str,
) -> _InflightRegistration | dict:
    if not request_id:
        return _InflightRegistration(owner=False)

    inflight = cache.get_inflight(request_id)
    if inflight is not None:
        return _join_inflight(request_id, inflight, context, tool_name, chat_id)

    future = asyncio.get_running_loop().create_future()
    cache.put_inflight(request_id, context, future)
    return _InflightRegistration(owner=True, future=future)


def _join_inflight(
    request_id: str,
    inflight,
    context: dict[str, str],
    tool_name: str,
    chat_id: str,
) -> _InflightRegistration | dict:
    inflight_context, future = inflight
    if inflight_context == context:
        return _InflightRegistration(owner=False, wait_for=future)

    logger.warning(
        "inflight_context_mismatch request_id={rid} tool={name} chat_id={cid}",
        rid=request_id,
        name=tool_name,
        cid=chat_id,
    )
    return failed_error(
        security_violation("request_id reused with different execution context")
    )


def _resolve_owned_inflight(registration: _InflightRegistration, result: dict) -> None:
    if registration.owner and registration.future is not None:
        registration.future.set_result(result)


def _fail_owned_inflight(
    registration: _InflightRegistration, exc: BaseException
) -> None:
    if registration.owner and registration.future is not None:
        if not registration.future.done():
            registration.future.set_exception(exc)
        registration.future.add_done_callback(lambda fut: fut.exception())


def _get_cached_result(
    cache: ToolCache,
    request_id: str,
    expected_context: dict[str, str],
    tool_name: str,
    chat_id: str,
) -> dict | None:
    if not request_id:
        return None
    cached = cache.get(request_id)
    if cached is None:
        return None

    if not isinstance(cached, dict) or cached.get("context") != expected_context:
        logger.warning(
            "cache_context_mismatch request_id={rid} tool={name} chat_id={cid}",
            rid=request_id,
            name=tool_name,
            cid=chat_id,
        )
        return failed_error(
            security_violation("request_id reused with different execution context")
        )

    logger.debug("cache_hit request_id={rid}", rid=request_id)
    return cached["result"]


def _put_cached_result(
    cache: ToolCache,
    request_id: str,
    context: dict[str, str],
    result: dict,
) -> None:
    cache.put(
        request_id,
        {"context": context, "result": result},
    )


def _cache_context(chat_id: str, tool_name: str, params: dict) -> dict[str, str]:
    return {
        "chat_id": chat_id,
        "tool_name": tool_name,
        "params_hash": _params_hash(params),
    }


def _params_hash(params: dict[str, Any]) -> str:
    encoded = json.dumps(
        params, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _params_log_summary(params: dict[str, Any]) -> str:
    parts = []
    for key in sorted(params):
        value = params[key]
        if isinstance(value, str):
            parts.append(f"{key}:<redacted:{len(value)} chars>")
        else:
            parts.append(f"{key}:<{type(value).__name__}>")
    digest = _params_hash(params)
    return "{" + ", ".join(parts) + f"; sha256={digest[:12]}" + "}"


async def _invoke_tool(
    fn, params: dict, tool_name: str, chat_id: str, controlled: bool = False
) -> dict:
    logger.info(
        "tool_execute tool={name} params={p} chat_id={cid}",
        name=tool_name,
        p=_params_log_summary(params),
        cid=chat_id,
    )
    try:
        if controlled:
            with controlled_execution():
                result = fn(**params)
                if asyncio.iscoroutine(result):
                    result = await result
                return result
        result = fn(**params)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as exc:
        logger.opt(exception=True).error(
            "tool_failed tool={name} chat_id={cid}", name=tool_name, cid=chat_id
        )
        return failed_error(execution_failed(str(exc)))
