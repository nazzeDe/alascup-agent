from uuid import uuid4

from loguru import logger

from src.agent.nodes._helpers import _parse_args
from src.agent.results import ReviewOutput
from src.agent.state import Transition
from src.agent.turn_context import TurnContext, Auditor, _safe_uuid
from src.models.audit import AuditActor, AuditLevel
from src.observability.debug_log import log as debug_log


async def review_node(state, ctx: TurnContext = None, *, executor, rule_engine, audit_logger, lifecycle=None):
    """Review all tool_calls: classify mutable tools -> rule match -> decide.

    Returns pending_approval when human decision is needed — the orchestrator
    handles the approval loop externally (no interrupt/resume).
    """
    tool_calls = state.tool_calls
    auditor = Auditor(audit_logger=audit_logger, ctx=ctx)
    chat_id = str(ctx.chat_id) if ctx and ctx.chat_id else None
    debug_log("DEBUG", "review_node entered", tc_count=len(tool_calls))
    approved: list[dict] = []
    rejected: list[dict] = []
    pending: list[dict] = []

    for tc in tool_calls:
        name, is_read_only, is_rollbackable, args = await _classify_tool_call(tc, executor)
        decision = rule_engine.evaluate(name, is_read_only, is_rollbackable)
        await _apply_decision(tc, name, is_read_only, decision, auditor, approved, rejected, pending,
                              args=args, lifecycle=lifecycle, chat_id=chat_id)

    tool_names = [t.get("function", {}).get("name", "?") for t in tool_calls]
    debug_log("DEBUG", "Review done",
              total=len(tool_calls), approved=len(approved), rejected=len(rejected), pending=len(pending),
              tools=",".join(tool_names))

    if pending:
        return await _build_pending_response(pending, approved, rejected, auditor)

    return _build_final_response(approved, rejected)


async def _classify_tool_call(tc: dict, executor) -> tuple[str, bool, bool, dict]:
    fn = tc.get("function", {})
    name = fn.get("name", "")
    args = _parse_args(fn.get("arguments", "{}"))

    if tc.get("mutable"):
        try:
            classify_args = {k: v for k, v in args.items() if k != "timeout"}
            classification = await executor.classify_companion(name, classify_args, tc.get("server_name", ""))
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
    auditor: Auditor,
    approved: list[dict],
    rejected: list[dict],
    pending: list[dict],
    *,
    args: dict | None = None,
    lifecycle=None,
    chat_id: str | None = None,
) -> None:
    chat_id_uuid = _safe_uuid(chat_id)
    if decision == "REJECT":
        rejected.append(tc)
        await auditor.tool_event(
            "TOOL_REJECTED", actor=AuditActor.POLICY, tool_name=name,
            request_id=_safe_uuid(tc.get("request_id", "")),
            params=args, decision="REJECT",
        )
        if lifecycle is not None and chat_id_uuid is not None:
            await lifecycle.mark_rejected(chat_id_uuid, tc.get("call_id"))
    elif decision == "AUTO_APPROVE":
        tc["is_read_only"] = is_read_only
        tc["request_id"] = str(uuid4())
        approved.append(tc)
        await auditor.tool_event(
            "TOOL_AUTO_APPROVED", actor=AuditActor.POLICY, tool_name=name,
            request_id=_safe_uuid(tc["request_id"]),
            params=args,
        )
        if lifecycle is not None and chat_id_uuid is not None:
            await lifecycle.mark_approved(chat_id_uuid, tc.get("call_id"))
    else:
        tc["is_read_only"] = is_read_only
        pending.append(tc)
        await auditor.tool_event(
            "TOOL_REQUEST_CREATED", actor=AuditActor.POLICY, tool_name=name,
            params=args,
        )


async def _build_pending_response(pending, approved, rejected, auditor: Auditor) -> ReviewOutput:
    for tc in pending:
        tc["request_id"] = str(uuid4())
    names = ",".join(t.get("function", {}).get("name", "?") for t in pending)
    debug_log("WARN", "Tools require approval — returning to orchestrator", tools=names)
    await auditor.tool_event(
        "TOOL_REQUEST_CREATED", actor=AuditActor.POLICY, tool_name=names,
        level=AuditLevel.WARN,
    )
    return ReviewOutput(
        approved=approved,
        rejected=rejected,
        pending=pending,
        transition=Transition.APPROVAL_PENDING,
    )


def _build_final_response(approved, rejected) -> ReviewOutput:
    transition = (
        Transition.APPROVAL_GRANTED if approved
        else Transition.APPROVAL_REJECTED
    ) if (approved or rejected) else None
    return ReviewOutput(
        approved=approved,
        rejected=rejected,
        transition=transition,
    )
