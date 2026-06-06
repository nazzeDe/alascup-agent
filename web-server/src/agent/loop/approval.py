"""Approval handler — resolves pending tool approvals on the same SSE stream."""

from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID, uuid4

from src.agent.loop.audit import audit_transition, _safe_uuid
from src.agent.loop.handlers import handle_pending_approval
from src.agent.nodes._tool_dispatch import _parse_args
from src.agent.state import Transition
from src.models.audit import AuditActor, AuditEvent, AuditLevel
from src.models.tool import ApprovalStatus, ExecutionStatus
from src.observability.debug_log import log as debug_log


def _apply_decisions(pending: list[dict], decisions: list[str]) -> tuple[list[dict], list[dict]]:
    """Split pending tool calls into approved and rejected based on decisions."""
    approved = []
    rejected = []
    for i, tc in enumerate(pending):
        decision = decisions[i] if i < len(decisions) else "EXPIRED"
        if decision == "APPROVED":
            tc["approval_status"] = "APPROVED"
            approved.append(tc)
        else:
            rejected.append(tc)
    return approved, rejected


def _inject_rejection_messages(state: dict, rejected: list[dict]) -> None:
    """Append tool-role rejection messages so LLM can propose alternatives."""
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
            "content": f"[{name}] execution_status=REJECTED\nerror=Tool was rejected by human or policy. Do NOT retry this exact call — propose an alternative approach.",
        })
    state["messages"] = state.get("messages", []) + messages


class ApprovalHandler:
    """Parse tool approvals. One public method resolve()."""

    def __init__(self, *, graph, bridge, audit_logger, lifecycle=None):
        self._graph = graph
        self._bridge = bridge
        self._audit = audit_logger
        self._lifecycle = lifecycle

    async def resolve(self, result: dict, *, profiler=None) -> AsyncIterator[dict]:
        """Process result's pending_approval, yield SSE events, mutate result in place.

        Reads from result: _chat_id, _turn_id, _iteration, _model (set by orchestrator
        before calling resolve).

        Yields: tool_approval_required, assistant etc.
        Side effects: modifies result["approved_tool_calls"], result["rejected_tool_calls"],
                      result["pending_approval"], result["transition"], result["messages"],
                      re-invokes graph via self._graph.ainvoke().
        """
        chat_id: str = result.get("_chat_id", "")
        turn_id = result.get("_turn_id")
        base_iteration: int = result.get("_iteration", 0)
        model: str = result.get("_model", "")

        sub_it = 0
        while result.get("pending_approval"):
            sub_it += 1
            iteration = base_iteration + sub_it
            result["_iteration"] = iteration

            debug_log("INFO", "Approval required — SSE stays connected", chat_id=chat_id)
            pending = result["pending_approval"]
            # Process each pending tool independently — each has its own request_id.
            all_approved: list[dict] = []
            all_rejected: list[dict] = []
            for tc in pending:
                request_id = tc.get("request_id", str(uuid4()))

                async for event in handle_pending_approval(
                    [tc], request_id=request_id,
                    bridge=self._bridge, audit_logger=self._audit,
                    chat_id=chat_id,
                    turn_id=turn_id, iteration=iteration,
                    model=model,
                ):
                    yield event
                if profiler:
                    profiler.checkpoint("approval_events_emitted")

                decisions = await self._bridge.gather_decisions(request_id, 1)
                debug_log("INFO", "Approval decisions collected",
                          request_id=request_id, count=len(decisions))

                approved, rejected = _apply_decisions([tc], decisions)
                all_approved.extend(approved)
                all_rejected.extend(rejected)

            await audit_transition(
                self._audit, Transition.APPROVAL_GRANTED,
                chat_id=_safe_uuid(chat_id),
                turn_id=turn_id, iteration=iteration,
                actor=AuditActor.POLICY, model=model,
            )

            await self._audit_approved(all_approved, chat_id, turn_id, iteration, model)
            await self._audit_rejected(all_rejected, chat_id, turn_id, iteration, model)
            if self._lifecycle is not None:
                cid = UUID(chat_id) if chat_id else None
                for tc in all_approved:
                    await self._lifecycle.update(cid, tc.get("call_id"),
                                                 approval_status=ApprovalStatus.APPROVED,
                                                 execution_status=ExecutionStatus.RUNNING)
                for tc in all_rejected:
                    await self._lifecycle.update(cid, tc.get("call_id"),
                                                 approval_status=ApprovalStatus.REJECTED,
                                                 execution_status=ExecutionStatus.FAILED)
            result["approved_tool_calls"] = result.get("approved_tool_calls", []) + all_approved
            result["rejected_tool_calls"] = result.get("rejected_tool_calls", []) + all_rejected
            result["pending_approval"] = []
            result["transition"] = (
                Transition.APPROVAL_GRANTED if all_approved
                else Transition.APPROVAL_REJECTED
            )

            # Append rejected tool feedback to messages so LLM can adjust.
            _inject_rejection_messages(result, all_rejected)

            result["_turn_id"] = turn_id
            result["_iteration"] = iteration
            result.update(await self._graph.ainvoke(result, {}))
            if profiler:
                profiler.checkpoint("graph_resume")

    async def _audit_approved(self, approved: list[dict], chat_id: str, turn_id, iteration: int, model: str) -> None:
        if not self._audit:
            return
        for tc in approved:
            fn = tc.get("function", {})
            args = _parse_args(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
            await self._audit.log(AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                chat_id=_safe_uuid(chat_id),
                request_id=_safe_uuid(tc.get("request_id", "")),
                turn_id=turn_id,
                iteration=iteration,
                level=AuditLevel.INFO,
                actor=AuditActor.POLICY.value,
                event="TOOL_APPROVED",
                tool_name=fn.get("name"),
                params=args,
                model=model,
                decision="APPROVED",
            ))

    async def _audit_rejected(self, rejected: list[dict], chat_id: str, turn_id, iteration: int, model: str) -> None:
        if not self._audit:
            return
        for tc in rejected:
            fn = tc.get("function", {})
            args = _parse_args(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
            await self._audit.log(AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                chat_id=_safe_uuid(chat_id),
                request_id=_safe_uuid(tc.get("request_id", "")),
                turn_id=turn_id,
                iteration=iteration,
                level=AuditLevel.WARN,
                actor=AuditActor.POLICY.value,
                event="TOOL_REJECTED",
                tool_name=fn.get("name"),
                params=args,
                model=model,
                decision="REJECTED",
            ))
