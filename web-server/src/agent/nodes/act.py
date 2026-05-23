import time as time_mod
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger

from src.agent.nodes._tool_dispatch import _parse_args, _execute_with_error_handling
from src.models.audit import AuditEvent, AuditLevel


async def act_node(state, *, executor, audit_logger=None):
    """Execute approved_tool_calls concurrently."""
    tool_calls = state.get("approved_tool_calls", []) or []
    if not tool_calls:
        return {"tool_results": []}

    calls = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        calls.append({
            "tool_name": fn.get("name", ""),
            "arguments": _parse_args(fn.get("arguments", "{}")),
            "server_name": tc.get("server_name", ""),
            "approval_status": tc.get("approval_status", "APPROVED"),
            "request_id": tc.get("request_id", str(uuid4())),
        })

    logger.debug("act_node: executing {count} tools: {names}", count=len(calls), names=[c["tool_name"] for c in calls])
    start = time_mod.monotonic()
    results = await _execute_with_error_handling(executor, calls)
    elapsed_ms = int((time_mod.monotonic() - start) * 1000)

    formatted = []
    for i, r in enumerate(results):
        tc_id = tool_calls[i].get("id", str(uuid4()))
        r["execution_time_ms"] = elapsed_ms
        formatted.append({
            "tool_name": calls[i]["tool_name"],
            "result": r,
            "tool_call_id": tc_id,
        })
        await _log_act(
            audit_logger, calls[i]["tool_name"],
            execution_status=r.get("execution_status", "UNKNOWN"),
        )

    return {"tool_results": formatted}


async def _log_act(audit_logger, tool_name: str,
             execution_status: str = "UNKNOWN") -> None:
    if audit_logger is None:
        return
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=AuditLevel.INFO,
        actor="system",
        event="TOOL_EXECUTED",
        tool_name=tool_name,
        execution_status=execution_status,
    ))
