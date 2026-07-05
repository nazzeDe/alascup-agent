"""Approval handler — resolves pending tool approvals on the same SSE stream."""

import asyncio
from uuid import uuid4

from src.agent.loop.emitter import EventEmitter
from src.agent.state import AgentState, Transition, TurnScratch
from src.agent.domain import AgentMessage, AgentToolCall, AgentToolResult
from src.agent.turn_context import TurnContext, Auditor, _safe_uuid
from src.models.audit import AuditActor, AuditLevel
from src.observability.debug_log import log as debug_log


def _apply_decisions(
    pending: list[AgentToolCall], decisions: list[dict]
) -> tuple[list[AgentToolCall], list[AgentToolCall]]:
    """Split pending tool calls into approved and rejected based on decisions.

    Attaches rejection_reason to rejected tool calls so _inject_rejection_messages
    and observe_node can include the user's message in the LLM-facing tool result.
    """
    approved: list[AgentToolCall] = []
    rejected: list[AgentToolCall] = []
    for i, tc in enumerate(pending):
        d = (
            decisions[i]
            if i < len(decisions)
            else {"status": "EXPIRED", "reason": None}
        )
        status = d.get("status", "EXPIRED") if isinstance(d, dict) else d
        if status == "APPROVED":
            tc.approval_status = "APPROVED"
            approved.append(tc)
        else:
            tc.approval_status = "EXPIRED" if status == "EXPIRED" else "REJECTED"
            tc.rejection_reason = d.get("reason") if isinstance(d, dict) else None
            rejected.append(tc)
    return approved, rejected


def _format_rejection_message(tc: AgentToolCall) -> str:
    """Build the LLM-facing tool-result string for a rejected tool call.

    Includes the user's rejection reason when provided, and omits the
    "propose an alternative approach" instruction that previously caused
    the LLM to retry with different tools.
    """
    name = tc.function.name or "unknown"
    reason = tc.rejection_reason
    parts = [f"[{name}] execution_status=REJECTED"]
    if reason:
        parts.append(f"rejection_reason={reason}")
    parts.append("error=Tool was rejected by human. Do NOT retry.")
    return "\n".join(parts)


def _rejection_tool_result(tc: AgentToolCall) -> AgentToolResult:
    result = {
        "execution_status": "REJECTED",
        "error": {"message": "Tool was rejected by human. Do NOT retry."},
    }
    if tc.rejection_reason:
        result["rejection_reason"] = tc.rejection_reason
    return AgentToolResult(
        tool_name=tc.function.name or "unknown",
        tool_call_id=tc.id or "rejected",
        result=result,
        server_name=tc.server_name,
        call_id=tc.call_id,
    )


def _rejection_tool_results(rejected: list[AgentToolCall]) -> list[AgentToolResult]:
    return [_rejection_tool_result(tc) for tc in rejected]


def _inject_rejection_messages(
    state: AgentState, rejected: list[AgentToolCall]
) -> list[AgentMessage]:
    """Append tool-role rejection messages so LLM can respond to the rejection."""
    if not rejected:
        return []
    messages = []
    for tc in rejected:
        name = tc.function.name or "unknown"
        messages.append(
            AgentMessage(
                role="tool",
                tool_call_id=tc.id or "rejected",
                name=name,
                content=_format_rejection_message(tc),
            )
        )
    state.messages.extend(messages)
    return messages


class ApprovalHandler:
    """Parse tool approvals. One public method resolve().

    Pure side-effecting helper: emits ApprovalRequired events, waits on bridge,
    applies decisions, and returns. AgentLoop owns the next execution step.
    """

    def __init__(self, *, bridge, audit_logger, lifecycle=None):
        self._bridge = bridge
        self._audit = audit_logger
        self._lifecycle = lifecycle

    async def resolve(
        self,
        scratch: TurnScratch,
        *,
        profiler=None,
        turn_ctx: TurnContext | None = None,
        emitter: EventEmitter | None = None,
    ) -> None:
        """Process scratch.pending_approval, emit ApprovalRequired events, apply decisions.

        Receives TurnContext and EventEmitter from the orchestrator.

        Sends: ApprovalRequired events via emitter.
        Side effects: modifies scratch.approved_tool_calls, scratch.rejected_tool_calls,
                      scratch.pending_approval, scratch.transition.

        AgentLoop re-enters the step after approval; think_node sees
        approved_tool_calls and route_after_think sends them to act_node.
        """
        if not scratch.pending_approval:
            return

        chat_id = str(turn_ctx.chat_id) if turn_ctx and turn_ctx.chat_id else ""
        auditor = Auditor(audit_logger=self._audit, ctx=turn_ctx)

        debug_log("INFO", "Approval required — SSE stays connected", chat_id=chat_id)
        pending = scratch.pending_approval
        all_approved: list[AgentToolCall] = []
        all_rejected: list[AgentToolCall] = []
        try:
            for tc in pending:
                approved, rejected = await self._resolve_one(
                    tc, chat_id, auditor, emitter, profiler
                )
                all_approved.extend(approved)
                all_rejected.extend(rejected)
        except asyncio.CancelledError:
            await self._mark_cancelled_pending(turn_ctx, pending)
            raise

        final_transition = (
            Transition.APPROVAL_GRANTED
            if all_approved
            else Transition.APPROVAL_REJECTED
        )
        await auditor.transition(final_transition, actor=AuditActor.POLICY)

        await self._audit_approved(auditor, all_approved)
        await self._audit_rejected(auditor, all_rejected)
        lifecycle_chat_id = turn_ctx.chat_id if turn_ctx else None
        await self._update_lifecycle(lifecycle_chat_id, all_approved, all_rejected)
        scratch.approved_tool_calls = scratch.approved_tool_calls + all_approved
        scratch.rejected_tool_calls = scratch.rejected_tool_calls + all_rejected
        scratch.pending_approval = []
        scratch.transition = (
            Transition.APPROVAL_GRANTED
            if all_approved
            else Transition.APPROVAL_REJECTED
        )

    async def _resolve_one(
        self,
        tc: AgentToolCall,
        chat_id: str,
        auditor: Auditor,
        emitter: EventEmitter | None,
        profiler,
    ) -> tuple[list[AgentToolCall], list[AgentToolCall]]:
        request_id = tc.request_id or str(uuid4())
        tc.request_id = request_id

        if self._bridge:
            self._bridge.create(request_id, chat_id)
        await auditor.transition(Transition.APPROVAL_PENDING, actor=AuditActor.POLICY)

        if emitter is not None:
            emitter.emit_tool_started(tc)
            self._emit_approval_required(emitter, tc, chat_id, request_id)
        if profiler:
            profiler.checkpoint("approval_events_emitted")

        if self._bridge is None:
            decisions = [{"status": "EXPIRED", "reason": None}]
        else:
            decisions = await self._bridge.gather_decisions(request_id, 1)
        debug_log(
            "INFO",
            "Approval decisions collected",
            request_id=request_id,
            count=len(decisions),
        )
        return _apply_decisions([tc], decisions)

    def _emit_approval_required(
        self, emitter: EventEmitter, tc: AgentToolCall, chat_id: str, request_id: str
    ) -> None:
        emitter.emit_approval_required(
            chat_id=chat_id,
            request_id=request_id,
            tool_name=tc.function.name,
            params=tc.function.arguments,
            reason=tc.function.name + " needs your approval to execute",
            call_id=tc.id,
        )

    async def _audit_approved(
        self, auditor: Auditor, approved: list[AgentToolCall]
    ) -> None:
        if not self._audit:
            return
        for tc in approved:
            await auditor.tool_event(
                "TOOL_APPROVED",
                actor=AuditActor.POLICY,
                tool_name=tc.function.name,
                request_id=_safe_uuid(tc.request_id or ""),
                params=tc.function.arguments,
                decision="APPROVED",
            )

    async def _audit_rejected(
        self, auditor: Auditor, rejected: list[AgentToolCall]
    ) -> None:
        if not self._audit:
            return
        for tc in rejected:
            rejection_reason = tc.rejection_reason
            await auditor.tool_event(
                "TOOL_REJECTED",
                actor=AuditActor.POLICY,
                tool_name=tc.function.name,
                request_id=_safe_uuid(tc.request_id or ""),
                params=tc.function.arguments,
                level=AuditLevel.WARN,
                decision="REJECTED",
                error={"rejection_reason": rejection_reason}
                if rejection_reason
                else None,
            )

    async def _update_lifecycle(
        self, chat_id, approved: list[AgentToolCall], rejected: list[AgentToolCall]
    ) -> None:
        if self._lifecycle is None:
            return
        for tc in approved:
            await self._lifecycle.mark_approved(chat_id, tc.call_id, tc.request_id)
        for tc in rejected:
            if tc.approval_status == "EXPIRED":
                await self._lifecycle.mark_expired(chat_id, tc.call_id, tc.request_id)
            else:
                await self._lifecycle.mark_rejected(chat_id, tc.call_id, tc.request_id)

    async def _mark_cancelled_pending(
        self, turn_ctx: TurnContext | None, pending: list[AgentToolCall]
    ) -> None:
        if self._lifecycle is None or turn_ctx is None:
            return
        for tc in pending:
            await self._lifecycle.mark_expired(
                turn_ctx.chat_id, tc.call_id, tc.request_id
            )
