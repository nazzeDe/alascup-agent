from datetime import datetime, timezone
from uuid import UUID, uuid4

from loguru import logger

from src.agent.loop.audit import _safe_uuid
from src.agent.nodes import _chat_id_ctx
from src.agent.nodes._tool_dispatch import _parse_args
from src.agent.state import Transition
from src.models.audit import AuditActor, AuditEvent, AuditLevel
from src.models.tool import ApprovalStatus, ExecutionStatus
from src.observability.debug_log import log as debug_log


async def review_node(state, *, executor, rule_engine, audit_logger, lifecycle=None):
    """Review all tool_calls: classify mutable tools → rule match → decide.

    Returns pending_approval when human decision is needed — the orchestrator
    handles the approval loop externally (no interrupt/resume).
    """
    tool_calls = state.get("tool_calls", []) or []
    logger.debug("review_node: tc_count={count}", count=len(tool_calls))
    debug_log("DEBUG", "review_node entered", tc_count=len(tool_calls))
    turn_id = state.get("_turn_id")
    iteration = state.get("_iteration")
    model = state.get("_model")
    approved: list[dict] = []
    rejected: list[dict] = []
    pending: list[dict] = []

    for tc in tool_calls:
        name, is_read_only, is_rollbackable, args = await _classify_tool_call(tc, executor)
        decision = rule_engine.evaluate(name, is_read_only, is_rollbackable)
        await _apply_decision(tc, name, is_read_only, decision, audit_logger, approved, rejected, pending,
                              turn_id=turn_id, iteration=iteration, args=args, model=model, lifecycle=lifecycle)

    tool_names = [t.get("function", {}).get("name", "?") for t in tool_calls]
    debug_log("DEBUG", "Review done",
              total=len(tool_calls), approved=len(approved), rejected=len(rejected), pending=len(pending),
              tools=",".join(tool_names))

    if pending:
        return await _build_pending_response(pending, approved, rejected, audit_logger, turn_id, iteration, model)

    return _build_final_response(approved, rejected)


async def _classify_tool_call(tc: dict, executor) -> tuple[str, bool, bool, dict]:
    fn = tc.get("function", {})
    name = fn.get("name", "")
    args = _parse_args(fn.get("arguments", "{}"))

    if tc.get("mutable"):
        try:
            classification = await executor.classify_companion(name, args, tc.get("server_name", ""))
        except Exception:
            logger.opt(exception=True).warning(
                "classify_companion failed for tool={tool}, treating as dangerous", tool=name,
            )
            classification = {"safe": False}
        return name, classification.get("safe", False), False, args
    return name, tc.get("is_read_only", False), tc.get("is_rollbackable", False), args


async def _apply_decision(
    tc: dict,
    name: str,
    is_read_only: bool,
    decision: str,
    audit_logger,
    approved: list[dict],
    rejected: list[dict],
    pending: list[dict],
    *,
    turn_id=None,
    iteration=None,
    args: dict | None = None,
    model: str | None = None,
    lifecycle=None,
) -> None:
    chat_id = _safe_uuid(_chat_id_ctx.get())
    if decision == "REJECT":
        rejected.append(tc)
        await _audit_review_decision(audit_logger, "TOOL_REJECTED", name, decision="REJECT",
                              turn_id=turn_id, iteration=iteration,
                              chat_id=chat_id, request_id=_safe_uuid(tc.get("request_id", "")),
                              params=args, model=model)
        if lifecycle is not None:
            chat_id_str = _chat_id_ctx.get()
            cid = UUID(chat_id_str) if chat_id_str else None
            await lifecycle.update(cid, tc.get("call_id"), approval_status=ApprovalStatus.REJECTED, execution_status=ExecutionStatus.FAILED)
    elif decision == "AUTO_APPROVE":
        tc["is_read_only"] = is_read_only
        tc["request_id"] = str(uuid4())
        approved.append(tc)
        await _audit_review_decision(audit_logger, "TOOL_AUTO_APPROVED", name, level=AuditLevel.INFO,
                              turn_id=turn_id, iteration=iteration,
                              chat_id=chat_id, request_id=_safe_uuid(tc["request_id"]),
                              params=args, model=model)
        if lifecycle is not None:
            chat_id_str = _chat_id_ctx.get()
            cid = UUID(chat_id_str) if chat_id_str else None
            await lifecycle.update(cid, tc.get("call_id"), approval_status=ApprovalStatus.APPROVED, execution_status=ExecutionStatus.RUNNING)
    else:
        tc["is_read_only"] = is_read_only
        pending.append(tc)
        await _audit_review_decision(audit_logger, "TOOL_REQUEST_CREATED", name, level=AuditLevel.WARN,
                              turn_id=turn_id, iteration=iteration,
                              chat_id=chat_id, params=args, model=model)


async def _build_pending_response(pending, approved, rejected, audit_logger, turn_id, iteration, model=None):
    for tc in pending:
        tc["request_id"] = str(uuid4())
    names = ",".join(t.get("function", {}).get("name", "?") for t in pending)
    debug_log("WARN", "Tools require approval — returning to orchestrator", tools=names)
    await _audit_review_decision(audit_logger, "TOOL_REQUEST_CREATED", names,
                          level=AuditLevel.WARN, turn_id=turn_id, iteration=iteration,
                          model=model)
    return {
        "approved_tool_calls": approved,
        "rejected_tool_calls": rejected,
        "pending_approval": pending,
        "transition": Transition.APPROVAL_PENDING,
    }


def _build_final_response(approved, rejected):
    transition = (
        Transition.APPROVAL_GRANTED if approved
        else Transition.APPROVAL_REJECTED
    ) if (approved or rejected) else None
    return {"approved_tool_calls": approved, "rejected_tool_calls": rejected, "transition": transition}


async def _audit_review_decision(audit_logger, event: str, tool_name: str, level=None,
                      turn_id=None, iteration=None,
                      chat_id: UUID | None = None,
                      request_id: UUID | None = None,
                      params: dict | None = None,
                      model: str | None = None,
                      **kwargs) -> None:
    if audit_logger is None:
        return
    if level is None:
        level = AuditLevel.WARN if "REJECT" in event else AuditLevel.INFO
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        chat_id=chat_id,
        request_id=request_id,
        level=level,
        actor=AuditActor.POLICY.value,
        event=event,
        tool_name=tool_name or None,
        params=params,
        model=model,
        turn_id=turn_id,
        iteration=iteration,
        **{k: v for k, v in kwargs.items() if v is not None},
    ))
