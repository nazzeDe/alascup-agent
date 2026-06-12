"""Shared helpers for agent steps: message formatting, tool dispatch, arg parsing."""

import json
from uuid import uuid4

from loguru import logger

from src.agent.messages import normalize_message


# ── Message formatting ──


def _messages(state) -> list[dict]:
    """Convert messages to OpenAI-compatible dicts for LLM call."""
    result: list[dict] = []
    for m in state.get("messages", []):
        entry = normalize_message(m)
        msg: dict = {"role": entry["role"], "content": entry["content"]}
        if entry["role"] == "assistant":
            tcs = entry.get("tool_calls")
            if tcs:
                msg["tool_calls"] = tcs
            rc = entry.get("reasoning_content", "")
            if rc:
                msg["reasoning_content"] = rc
        elif entry["role"] == "tool":
            tc_id = entry.get("tool_call_id", "")
            if tc_id:
                msg["tool_call_id"] = tc_id
            nm = entry.get("name", "")
            if nm:
                msg["name"] = nm
        result.append(msg)
    return result


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
        result.append({
            "type": "function",
            "function": {"name": full_name, "description": desc, "parameters": params},
        })
    return result


# ── Tool dispatch ──


def _parse_args(args: str) -> dict:
    """Parse JSON arguments string to dict. Returns {} on failure."""
    try:
        return json.loads(args) if isinstance(args, str) else args
    except json.JSONDecodeError:
        return {}


async def _dispatch_tool_calls(
    tool_call_blocks: list[dict], executor, available_tools: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Parse server_name prefix, attach metadata, pre-execute readonly tools."""
    tool_index = _build_tool_index(available_tools)

    pending: list[dict] = []
    dispatch: list[dict] = []
    dispatch_call_ids: list[str] = []
    for tc in tool_call_blocks:
        fn = tc.get("function", {})
        full_name = fn.get("name", "")
        args = _parse_args(fn.get("arguments", "{}"))
        tc_id = tc.get("id", str(uuid4()))

        server_name, tool_name = _split_server_tool(full_name)
        tc["function"]["name"] = tool_name

        meta = tool_index.get(full_name, {})
        if not server_name:
            server_name = meta.get("server_name", "")
        tc["server_name"] = server_name

        _classify_and_route(tc, tool_name, args, server_name, tc_id, meta, pending, dispatch, dispatch_call_ids)

    pre_executed: list[dict] = []
    if dispatch:
        disp_results = await _execute_with_error_handling(executor, dispatch)
        for i, dr in enumerate(disp_results):
            pre_executed.append({
                "tool_name": dispatch[i]["tool_name"],
                "result": dr,
                "tool_call_id": dispatch_call_ids[i],
            })

    return pending, pre_executed


async def _execute_with_error_handling(executor, dispatch_list: list[dict]) -> list[dict]:
    """Execute tool calls with error handling. Returns list of result dicts."""
    try:
        return await executor.execute_parallel(dispatch_list)
    except Exception as exc:
        logger.opt(exception=True).warning("tool execution failed: {err}", err=exc)
        return [{"execution_status": "FAILED", "error": {"message": str(exc)}}] * len(dispatch_list)


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


def _classify_and_route(tc, tool_name, args, server_name, tc_id, meta, pending, dispatch, dispatch_call_ids):
    """Classify a tool_call as mutable/readonly/write and route to pending or dispatch."""
    is_mutable = meta.get("mutable", False)
    is_read_only = meta.get("is_read_only", False)

    if is_mutable:
        tc["mutable"] = True
        tc["is_read_only"] = None
        pending.append(tc)
    elif is_read_only:
        dispatch.append({
            "tool_name": tool_name,
            "arguments": args,
            "server_name": server_name,
            "approval_status": "APPROVED",
            "request_id": str(uuid4()),
        })
        dispatch_call_ids.append(tc_id)
    else:
        tc["mutable"] = False
        tc["is_read_only"] = False
        pending.append(tc)
