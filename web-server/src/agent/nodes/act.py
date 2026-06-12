from uuid import UUID, uuid4

from loguru import logger

from src.agent.nodes._helpers import _execute_with_error_handling, _parse_args
from src.agent.results import ExecuteOutput
from src.agent.turn_context import TurnContext, Auditor, _safe_uuid
from src.models.audit import AuditActor
from src.models.tool import ExecutionStatus
from src.observability.timing import start_feature, complete_feature


async def act_node(state, ctx: TurnContext = None, *, executor, audit_logger=None, lifecycle=None):
    """Execute approved_tool_calls concurrently."""
    tool_calls = state.get("approved_tool_calls", []) or []
    if not tool_calls:
        return ExecuteOutput()

    auditor = Auditor(audit_logger=audit_logger, ctx=ctx)

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

    chat_id = str(ctx.chat_id) if ctx and ctx.chat_id else None
    feature = f"tool_exec:{chat_id}"
    start_feature(feature)
    logger.debug("act_node: executing {count} tools: {names}", count=len(calls), names=[c["tool_name"] for c in calls])
    results = await _execute_with_error_handling(executor, calls)
    complete_feature(feature)
    formatted = []
    for i, r in enumerate(results):
        tc_id = tool_calls[i].get("id", str(uuid4()))
        formatted.append({
            "tool_name": calls[i]["tool_name"],
            "result": r,
            "tool_call_id": tc_id,
            "is_read_only": tool_calls[i].get("is_read_only", False),
            "is_rollbackable": tool_calls[i].get("is_rollbackable", False),
        })
        await auditor.tool_event(
            "TOOL_EXECUTED",
            actor=AuditActor.TOOL,
            tool_name=calls[i]["tool_name"],
            request_id=_safe_uuid(calls[i].get("request_id", "")),
            params=calls[i].get("arguments"),
            execution_status=r.get("execution_status", "UNKNOWN"),
        )
        if lifecycle is not None:
            call_id = tool_calls[i].get("call_id")
            cid = UUID(chat_id) if chat_id else None
            exec_status_str = r.get("execution_status", "UNKNOWN")
            try:
                exec_status = ExecutionStatus(exec_status_str)
            except ValueError:
                exec_status = ExecutionStatus.FAILED
            await lifecycle.update(cid, call_id, execution_status=exec_status, error=r.get("error"), backup_ref=r.get("backup_ref"))
        if r.get("execution_status") == "FAILED":
            err = r.get("error", {})
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            logger.warning("tool_failed tool={name} error={err}", name=calls[i]["tool_name"], err=err_msg)

    return ExecuteOutput(results=formatted)
