from uuid import UUID, uuid4

from loguru import logger

from src.agent.nodes._helpers import _execute_with_error_handling
from src.agent.results import ExecuteOutput
from src.agent.domain import AgentToolCall, AgentToolResult
from src.agent.mappers import tool_call_to_dispatch
from src.agent.turn_context import TurnContext, Auditor, _safe_uuid
from src.models.audit import AuditActor
from src.observability.debug_log import log as debug_log
from src.observability import trace_points as tp
from src.observability.timing import start_feature, complete_feature


async def act_node(
    state,
    ctx: TurnContext | None = None,
    *,
    executor,
    audit_logger=None,
    lifecycle=None,
):
    """Execute approved_tool_calls concurrently."""
    tool_calls = state.approved_tool_calls
    if not tool_calls:
        return ExecuteOutput()

    auditor = Auditor(audit_logger=audit_logger, ctx=ctx)
    calls = [_call_from_tool_call(tc) for tc in tool_calls]

    chat_id = str(ctx.chat_id) if ctx and ctx.chat_id else None
    feature = f"tool_exec:{chat_id}"
    start_feature(feature)
    debug_log(
        "DEBUG",
        tp.ACT_ENTER,
        count=len(calls),
        names=[c["tool_name"] for c in calls],
        input_ids=[tool_calls[i].id for i in range(len(tool_calls))],
    )
    results = await _execute_with_error_handling(executor, calls)
    complete_feature(feature)
    formatted = []
    for tc, call, result in zip(tool_calls, calls, results, strict=True):
        formatted.append(_formatted_result(tc, call, result))
        await _audit_execution(auditor, call, result)
        await _record_execution_lifecycle(lifecycle, chat_id, tc, result)
        _log_failed_execution(call, result)
    debug_log("DEBUG", tp.ACT_DONE, output_ids=[f.tool_call_id for f in formatted])

    return ExecuteOutput(results=formatted)


def _call_from_tool_call(tc: AgentToolCall) -> dict:
    if not tc.request_id:
        tc.request_id = str(uuid4())
    return tool_call_to_dispatch(tc)


def _formatted_result(tc: AgentToolCall, call: dict, result: dict) -> AgentToolResult:
    return AgentToolResult(
        tool_name=call["tool_name"],
        result=result,
        tool_call_id=tc.id,
        is_read_only=bool(tc.is_read_only),
        is_rollbackable=tc.is_rollbackable,
        server_name=tc.server_name,
        call_id=tc.call_id,
    )


async def _audit_execution(auditor: Auditor, call: dict, result: dict) -> None:
    await auditor.tool_event(
        "TOOL_EXECUTED",
        actor=AuditActor.TOOL,
        tool_name=call["tool_name"],
        request_id=_safe_uuid(call.get("request_id", "")),
        params=call.get("arguments"),
        execution_status=result.get("execution_status", "UNKNOWN"),
    )


async def _record_execution_lifecycle(
    lifecycle, chat_id: str | None, tc: AgentToolCall, result: dict
) -> None:
    if lifecycle is None:
        return
    cid = UUID(chat_id) if chat_id else None
    await lifecycle.mark_executed(cid, tc.call_id, result)


def _log_failed_execution(call: dict, result: dict) -> None:
    if result.get("execution_status") != "FAILED":
        return
    err = result.get("error", {})
    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
    logger.warning(
        "tool_failed tool={name} error={err}", name=call["tool_name"], err=err_msg
    )
