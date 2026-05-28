import json
from uuid import uuid4

from loguru import logger


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


async def _execute_with_error_handling(executor, dispatch_list: list[dict]) -> list[dict]:
    """Execute tool calls with error handling. Returns list of result dicts."""
    try:
        return await executor.execute_parallel(dispatch_list)
    except Exception as exc:
        logger.opt(exception=True).warning("tool execution failed: {err}", err=exc)
        return [{"execution_status": "FAILED", "error": {"message": str(exc)}}] * len(dispatch_list)


def _parse_args(args: str) -> dict:
    try:
        return json.loads(args) if isinstance(args, str) else args
    except json.JSONDecodeError:
        return {}
