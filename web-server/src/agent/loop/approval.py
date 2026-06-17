"""Approval handler — resolves pending tool approvals on the same SSE stream."""

import json

from uuid import uuid4

from src.agent.loop.emitter import EventEmitter
from src.agent.nodes._helpers import _parse_args
from src.agent.state import Transition, TurnScratch
from src.agent.turn_context import TurnContext, Auditor, _safe_uuid
from src.models.audit import AuditActor, AuditLevel
from src.models.tool import ApprovalStatus, ExecutionStatus
from src.observability.debug_log import log as debug_log


def _apply_decisions(pending: list[dict], decisions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split pending tool calls into approved and rejected based on decisions.

    Attaches rejection_reason to rejected tool calls so _inject_rejection_messages
    and observe_node can include the user's message in the LLM-facing tool result.
    """
    approved = []
    rejected = []
    for i, tc in enumerate(pending):
        d = decisions[i] if i < len(decisions) else {"status": "EXPIRED", "reason": None}
        status = d["status"] if isinstance(d, dict) else d
        if status == "APPROVED":
            tc["approval_status"] = "APPROVED"
            approved.append(tc)
        else:
            tc["approval_status"] = "EXPIRED" if status == "EXPIRED" else "REJECTED"
            tc["rejection_reason"] = d.get("reason") if isinstance(d, dict) else None
            rejected.append(tc)
    return approved, rejected


def _format_rejection_message(tc: dict) -> str:
    """Build the LLM-facing tool-result string for a rejected tool call.

    Includes the user's rejection reason when provided, and omits the
    "propose an alternative approach" instruction that previously caused
    the LLM to retry with different tools.
    """
    fn = tc.get("function", {})
    name = fn.get("name", "unknown")
    reason = tc.get("rejection_reason")
    parts = [f"[{name}] execution_status=REJECTED"]
    if reason:
        parts.append(f"rejection_reason={reason}")
    parts.append("error=Tool was rejected by human. Do NOT retry.")
    return "\n".join(parts)


def _inject_rejection_messages(state: dict, rejected: list[dict]) -> None:
    """Append tool-role rejection messages so LLM can respond to the rejection."""
    if not rejected:
        return
    messages: list[dict] = []
    for tc in rejected:
        fn = tc.get("function", {})
        name = fn.get("name", "unknown")
        messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", "rejected"),
            "name": name,
            "content": _format_rejection_message(tc),
        })
    state["messages"] = state.get("messages", []) + messages


class ApprovalHandler:
    """Parse tool approvals. One public method resolve().

    Pure side-effecting helper: emits ApprovalRequired events, waits on bridge,
    applies decisions, returns. Does NOT call graph.ainvoke — the orchestrator's
    outer loop is the sole graph driver.
    """

    def __init__(self, *, bridge, audit_logger, lifecycle=None):
        self._bridge = bridge
        self._audit = audit_logger
        self._lifecycle = lifecycle

    async def resolve(self, scratch: TurnScratch, *, profiler=None, turn_ctx: TurnContext = None, emitter: EventEmitter = None) -> None:
        """Process scratch.pending_approval, emit ApprovalRequired events, apply decisions.

        Receives TurnContext and EventEmitter from the orchestrator.

        Sends: ApprovalRequired events via emitter.
        Side effects: modifies scratch.approved_tool_calls, scratch.rejected_tool_calls,
                      scratch.pending_approval, scratch.transition.

        Does NOT call graph.ainvoke. The orchestrator re-enters the loop and calls
        graph.ainvoke on the next iteration — think_node sees approved_tool_calls
        and route_after_think sends it to act_node.
        """
        if not scratch.pending_approval:
            return

        chat_id = str(turn_ctx.chat_id) if turn_ctx.chat_id else ""
        auditor = Auditor(audit_logger=self._audit, ctx=turn_ctx)

        debug_log("INFO", "Approval required — SSE stays connected", chat_id=chat_id)
        pending = scratch.pending_approval
        all_approved: list[dict] = []
        all_rejected: list[dict] = []
        for tc in pending:
            request_id = tc.get("request_id", str(uuid4()))

            # Inline handle_pending_approval: register bridge + audit
            if self._bridge:
                self._bridge.create(request_id, chat_id)
            await auditor.transition(Transition.APPROVAL_PENDING, actor=AuditActor.POLICY)

            if emitter is not None:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                params = fn.get("arguments", "{}")
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except (json.JSONDecodeError, TypeError):
                        params = {}
                emitter.emit_approval_required(
                    chat_id=chat_id,
                    request_id=request_id,
                    tool_name=fn.get("name", ""),
                    params=params,
                    reason=fn.get("name", "") + " needs your approval to execute",
                    call_id=tc.get("id", ""),
                )
            if profiler:
                profiler.checkpoint("approval_events_emitted")

            decisions = await self._bridge.gather_decisions(request_id, 1)
            debug_log("INFO", "Approval decisions collected",
                      request_id=request_id, count=len(decisions))

            approved, rejected = _apply_decisions([tc], decisions)
            all_approved.extend(approved)
            all_rejected.extend(rejected)

        await auditor.transition(Transition.APPROVAL_GRANTED, actor=AuditActor.POLICY)

        # Audit approved tools
        if self._audit:
            for tc in all_approved:
                fn = tc.get("function", {})
                args = _parse_args(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
                await auditor.tool_event(
                    "TOOL_APPROVED",
                    actor=AuditActor.POLICY,
                    tool_name=fn.get("name"),
                    request_id=_safe_uuid(tc.get("request_id", "")),
                    params=args,
                    decision="APPROVED",
                )
        # Audit rejected tools
        if self._audit:
            for tc in all_rejected:
                fn = tc.get("function", {})
                args = _parse_args(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
                rejection_reason = tc.get("rejection_reason")
                await auditor.tool_event(
                    "TOOL_REJECTED",
                    actor=AuditActor.POLICY,
                    tool_name=fn.get("name"),
                    request_id=_safe_uuid(tc.get("request_id", "")),
                    params=args,
                    level=AuditLevel.WARN,
                    decision="REJECTED",
                    error={"rejection_reason": rejection_reason} if rejection_reason else None,
                )
        if self._lifecycle is not None:
            cid = turn_ctx.chat_id
            for tc in all_approved:
                await self._lifecycle.mark_approved(cid, tc.get("call_id"))
            for tc in all_rejected:
                if tc.get("approval_status") == "EXPIRED":
                    await self._lifecycle.mark_expired(cid, tc.get("call_id"))
                else:
                    await self._lifecycle.mark_rejected(cid, tc.get("call_id"))
        scratch.approved_tool_calls = scratch.approved_tool_calls + all_approved
        scratch.rejected_tool_calls = scratch.rejected_tool_calls + all_rejected
        scratch.pending_approval = []
        scratch.transition = (
            Transition.APPROVAL_GRANTED if all_approved
            else Transition.APPROVAL_REJECTED
        )
