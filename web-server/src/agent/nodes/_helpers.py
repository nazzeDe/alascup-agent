"""Shared helpers for agent steps: message formatting, tool dispatch, arg parsing."""

import json
from typing import Any
from uuid import uuid4

from loguru import logger

from src.agent.domain import AgentToolCall, AgentToolResult, ToolFunction
from src.agent.mappers import message_to_openai, tool_call_to_dispatch


# ── Message formatting ──


def _messages(state) -> list[dict]:
    """Convert messages to OpenAI-compatible dicts for LLM call."""
    return [message_to_openai(m) for m in state.messages]


def _format_tools(tools: list) -> list[dict]:
    """Convert tool schemas to OpenAI function-calling format."""
    result: list[dict] = []
    for t in tools:
        if isinstance(t, dict):
            name = t.get("name", "")
            server = t.get("server_name", "")
            desc = t.get("description", "")
            params = t.get("params_schema", {})
        else:
            name = getattr(t, "name", "")
            server = getattr(t, "server_name", "")
            desc = getattr(t, "description", "")
            params = getattr(t, "params_schema", {})
        full_name = f"{server}__{name}" if server else name
        if not params or not isinstance(params, dict) or params.get("type") != "object":
            params = {"type": "object", "properties": {}}
        result.append(
            {
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": desc,
                    "parameters": params,
                },
            }
        )
    return result


# ── Tool dispatch ──


def _parse_args(args: str | dict[str, Any]) -> dict[str, Any]:
    """Parse JSON arguments string to dict. Returns {} on failure."""
    try:
        return json.loads(args) if isinstance(args, str) else args
    except json.JSONDecodeError:
        return {}


async def _dispatch_tool_calls(
    tool_call_blocks: list[AgentToolCall], executor, available_tools: list[dict]
) -> tuple[list[AgentToolCall], list[AgentToolResult]]:
    """Parse server_name prefix, attach metadata, pre-execute readonly tools."""
    tool_index = _build_tool_index(available_tools)

    pending: list[AgentToolCall] = []
    dispatch: list[dict[str, Any]] = []
    dispatch_call_ids: list[str] = []
    for tc in tool_call_blocks:
        full_name = tc.function.name
        args = tc.function.arguments
        tc_id = tc.id

        server_name, tool_name = _split_server_tool(full_name)
        tc.function = ToolFunction(name=tool_name, arguments=args)

        meta = tool_index.get(full_name, {})
        if not server_name:
            server_name = meta.get("server_name", "")
        tc.server_name = server_name

        _classify_and_route(
            tc,
            tool_name,
            args,
            server_name,
            tc_id,
            meta,
            pending,
            dispatch,
            dispatch_call_ids,
        )

    pre_executed: list[AgentToolResult] = []
    if dispatch:
        disp_results = await _execute_with_error_handling(executor, dispatch)
        for i, dr in enumerate(disp_results):
            pre_executed.append(
                AgentToolResult(
                    tool_name=dispatch[i]["tool_name"],
                    result=dr,
                    tool_call_id=dispatch_call_ids[i],
                    is_read_only=True,
                    server_name=dispatch[i]["server_name"],
                )
            )

    return pending, pre_executed


async def _execute_with_error_handling(
    executor, dispatch_list: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Execute tool calls with error handling. Returns list of result dicts."""
    try:
        return await executor.execute_parallel(dispatch_list)
    except Exception as exc:
        logger.opt(exception=True).warning("tool execution failed: {err}", err=exc)
        return [{"execution_status": "FAILED", "error": {"message": str(exc)}}] * len(
            dispatch_list
        )


def _build_tool_index(available_tools: list[dict]) -> dict[str, dict]:
    """Build lookup dict: 'server__name' and 'name' → tool metadata."""
    tool_index: dict[str, dict] = {}
    for t in available_tools:
        server = t.get("server_name", "")
        name = t.get("name", "")
        if server and name:
            tool_index[f"{server}__{name}"] = t
            tool_index[name] = t
    return tool_index


def _split_server_tool(full_name: str) -> tuple[str, str]:
    """Split 'server__tool' into (server, tool). Returns ('', full_name) if no prefix."""
    if "__" in full_name:
        server, tool = full_name.split("__", 1)
        return server, tool
    return "", full_name


def _classify_and_route(
    tc: AgentToolCall,
    tool_name: str,
    args: dict[str, Any],
    server_name: str,
    tc_id: str,
    meta: dict,
    pending: list[AgentToolCall],
    dispatch: list[dict[str, Any]],
    dispatch_call_ids: list[str],
) -> None:
    """Classify a tool_call as mutable/readonly/write and route to pending or dispatch."""
    is_mutable = meta.get("mutable", False)
    is_read_only = meta.get("is_read_only", False)

    if is_mutable:
        tc.mutable = True
        tc.is_read_only = None
        pending.append(tc)
    elif is_read_only:
        tc.approval_status = "APPROVED"
        tc.request_id = str(uuid4())
        dispatch.append(tool_call_to_dispatch(tc))
        dispatch_call_ids.append(tc_id)
    else:
        tc.mutable = False
        tc.is_read_only = False
        pending.append(tc)
