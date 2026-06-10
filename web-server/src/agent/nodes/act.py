from datetime import datetime, timezone
from uuid import UUID, uuid4

from langchain_core.runnables.config import RunnableConfig
from loguru import logger

from src.agent.loop.audit import _safe_uuid
from src.agent.nodes._tool_dispatch import _parse_args, _execute_with_error_handling
from src.models.audit import AuditActor, AuditEvent, AuditLevel
from src.models.tool import ExecutionStatus
from src.observability.timing import start_feature, complete_feature


async def act_node(state, config: RunnableConfig = None, *, executor, audit_logger=None, lifecycle=None):
    """Execute approved_tool_calls concurrently."""
    tool_calls = state.get("approved_tool_calls", []) or []
    if not tool_calls:
        return {"tool_results": []}

    turn_id = state.get("_turn_id")
    iteration = state.get("_iteration")
    model = state.get("_model")
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

    configurable = config.get("configurable", {}) if config else {}
    chat_id = configurable.get("_chat_id")
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
        await _audit_tool_executed(
            audit_logger, calls[i]["tool_name"],
            execution_status=r.get("execution_status", "UNKNOWN"),
            turn_id=turn_id, iteration=iteration,
            chat_id=_safe_uuid(chat_id) if chat_id else None,
            request_id=_safe_uuid(calls[i].get("request_id", "")),
            params=calls[i].get("arguments"),
            model=model,
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

    return {"tool_results": formatted, "approved_tool_calls": []}


async def _audit_tool_executed(audit_logger, tool_name: str,
             execution_status: str = "UNKNOWN",
             turn_id=None, iteration=None,
             chat_id: UUID | None = None,
             request_id: UUID | None = None,
             params: dict | None = None,
             model: str | None = None) -> None:
    if audit_logger is None:
        return
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        chat_id=chat_id,
        request_id=request_id,
        level=AuditLevel.INFO,
        actor=AuditActor.TOOL.value,
        event="TOOL_EXECUTED",
        tool_name=tool_name,
        params=params,
        model=model,
        execution_status=execution_status,
        turn_id=turn_id,
        iteration=iteration,
    ))
