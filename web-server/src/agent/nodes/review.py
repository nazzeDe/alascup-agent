from datetime import datetime, timezone
from uuid import uuid4

from langgraph.types import interrupt
from loguru import logger

from src.agent.nodes._tool_dispatch import _parse_args
from src.agent.state import Transition
from src.models.audit import AuditEvent, AuditLevel
from src.observability.debug_log import log as debug_log


async def review_node(state, *, executor, rule_engine, audit_logger):
    """Review all tool_calls: classify mutable tools → rule match → decide."""
    tool_calls = state.get("tool_calls", []) or []
    logger.debug("review_node: tc_count={count}", count=len(tool_calls))
    debug_log("DEBUG", "review_node entered", tc_count=len(tool_calls))
    approved: list[dict] = []
    rejected: list[dict] = []
    pending: list[dict] = []

    for tc in tool_calls:
        name, is_read_only, is_rollbackable = await _classify_tool_call(tc, executor)
        decision = rule_engine.evaluate(name, is_read_only, is_rollbackable)
        await _apply_decision(tc, name, is_read_only, decision, audit_logger, approved, rejected, pending)

    tool_names = [t.get("function", {}).get("name", "?") for t in tool_calls]
    debug_log("DEBUG", "Review done",
              total=len(tool_calls), approved=len(approved), rejected=len(rejected), pending=len(pending),
              tools=",".join(tool_names))

    if pending:
        request_id = str(uuid4())
        debug_log("WARN", "Tools require approval — pausing",
                  tools=",".join([t.get("function", {}).get("name", "?") for t in pending]))
        approval_result = interrupt({
            "event": "approval_required",
            "request_id": request_id,
            "pending_tool_calls": pending,
        })
        decisions = approval_result.get("decisions", [])
        for i, tc in enumerate(pending):
            user_decision = decisions[i] if i < len(decisions) else "EXPIRED"
            if user_decision == "APPROVED":
                tc["approval_status"] = "APPROVED"
                tc["request_id"] = request_id
                approved.append(tc)
                await _log_review(audit_logger, "TOOL_APPROVED",
                            tc.get("function", {}).get("name", ""),
                            level=AuditLevel.WARN)
            else:
                rejected.append(tc)
                await _log_review(audit_logger, "TOOL_REJECTED",
                            tc.get("function", {}).get("name", ""),
                            decision=user_decision)

    return {
        "approved_tool_calls": approved,
        "rejected_tool_calls": rejected,
        "transition": (
            Transition.APPROVAL_GRANTED if approved
            else Transition.APPROVAL_REJECTED
        ) if (approved or rejected) else None,
    }


async def _classify_tool_call(tc: dict, executor) -> tuple[str, bool, bool]:
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
        return name, classification.get("safe", False), False
    return name, tc.get("is_read_only", False), tc.get("is_rollbackable", False)


async def _apply_decision(
    tc: dict,
    name: str,
    is_read_only: bool,
    decision: str,
    audit_logger,
    approved: list[dict],
    rejected: list[dict],
    pending: list[dict],
) -> None:
    if decision == "REJECT":
        rejected.append(tc)
        await _log_review(audit_logger, "TOOL_REJECTED", name, decision="REJECT")
    elif decision == "AUTO_APPROVE":
        tc["is_read_only"] = is_read_only
        tc["request_id"] = str(uuid4())
        approved.append(tc)
        await _log_review(audit_logger, "TOOL_AUTO_APPROVED", name, level=AuditLevel.INFO)
    else:
        tc["is_read_only"] = is_read_only
        pending.append(tc)
        await _log_review(audit_logger, "TOOL_REQUEST_CREATED", name, level=AuditLevel.WARN)


async def _log_review(audit_logger, event: str, tool_name: str, level=None, **kwargs) -> None:
    if audit_logger is None:
        return
    if level is None:
        level = AuditLevel.WARN if "REJECT" in event else AuditLevel.INFO
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=level,
        actor="system",
        event=event,
        tool_name=tool_name or None,
        **{k: v for k, v in kwargs.items() if v is not None},
    ))
