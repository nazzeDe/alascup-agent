"""Agent loop orchestrator — composes graph invocation, event emission, and handlers."""

import json
from uuid import uuid4
from typing import AsyncIterator

from loguru import logger

from src.agent.loop.audit import log_transition
from src.agent.loop.events import emit_events
from src.agent.loop.handlers import handle_pending_approval, handle_llm_error
from src.agent.loop.transitions import (
    clear_transient_fields,
    get_transition,
)
from src.agent.state import ROLE_MAP, Transition
from src.models.audit import AuditEvent, AuditLevel
from src.observability.debug_log import log as debug_log
from src.observability.profiler import Profiler, write_profile
from src.tools import start_feature, complete_feature


def _safe_uuid(value: str):
    """Parse UUID safely, returning None for non-UUID strings."""
    from uuid import UUID
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


def _is_assistant(m) -> bool:
    r = m.get("role", m.get("type", "")) if isinstance(m, dict) else str(getattr(m, "type", ""))
    return ROLE_MAP.get(r, r) == "assistant"


def _finalize_iteration(profiler: Profiler, loop_feature: str) -> None:
    _log_profile(profiler)
    complete_feature(loop_feature)


def _log_profile(profiler: Profiler) -> None:
    report = profiler.report()
    if report:
        write_profile(report)


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


class LoopOrchestrator:
    """Orchestrates the think → review → act → observe ReAct loop.

    SSE connection stays alive across the full chat turn.  When review_node
    returns pending_approval, the orchestrator yields the approval event,
    waits for a decision via the bridge, merges decisions into state, and
    re-invokes the graph — all on the same SSE stream.

    Graph is a pure function (no checkpointer, no interrupt/resume).
    """

    def __init__(
        self,
        *,
        graph,
        context_manager,
        bridge,
        audit_logger,
        error_recovery,
        llm,
        chat_id: str,
    ):
        self._graph = graph
        self._context_manager = context_manager
        self._bridge = bridge
        self._audit = audit_logger
        self._error_recovery = error_recovery
        self._llm = llm
        self._config = {"configurable": {"thread_id": chat_id}}
        self._chat_id = chat_id

    async def run(self, initial_state: dict) -> AsyncIterator[dict]:
        """Execute ReAct loop, yielding all events on one SSE connection."""

        turn_id = uuid4()
        self._turn_id = turn_id
        self._iteration = 0
        state = dict(initial_state)

        emitted_assistant_count = sum(
            1 for m in state.get("messages", []) if _is_assistant(m)
        )
        debug_log("INFO", "Loop start", chat_id=str(self._chat_id),
                  msg_count=len(state.get("messages", [])), assistant_skip=emitted_assistant_count)

        profiler = Profiler()
        profiler.activate()

        try:
            async for event in self._run_loop(state, turn_id, emitted_assistant_count, profiler):
                yield event
        finally:
            profiler.deactivate()

    async def _run_loop(self, state: dict, turn_id, emitted_assistant_count: int, profiler: Profiler) -> AsyncIterator[dict]:
        """Core ReAct loop — extracted from run() to keep run() as a thin wrapper."""
        it = 0

        while True:
            it += 1
            self._iteration = it
            loop_feature = f"loop:{self._chat_id}#{it}"
            start_feature(loop_feature)

            # 1. Context compression
            await self._compress_context(state)

            # 2. Invoke graph
            state["_turn_id"] = turn_id
            state["_iteration"] = it
            result = await self._graph.ainvoke(state, self._config)
            profiler.checkpoint("graph_ainvoke")

            # 3. Approval flow — orchestrator-owned, no interrupt/resume
            async for event in self._drain_approval_loop(result, turn_id, it, profiler):
                yield event
            state = result

            # 4. Error recovery
            action, error_event = await self._recover_from_error(state)
            if error_event:
                yield error_event
            if action == "continue":
                _finalize_iteration(profiler, loop_feature)
                continue
            if action == "return":
                for ev in self._exit_events(profiler, loop_feature, action):
                    yield ev
                return

            # 5. Emit SSE events
            counter = [emitted_assistant_count]
            async for event in self._emit_sse(state, counter, profiler):
                yield event

            # 6-7. Clear + transition
            clear_transient_fields(state)
            action = await self._handle_transition(state)
            profiler.checkpoint("handle_transition")
            if action not in ("continue",):
                for ev in self._exit_events(profiler, loop_feature, "return"):
                    yield ev
                return
            debug_log("DEBUG", "Transition → CONTINUE", chat_id=str(self._chat_id))
            _finalize_iteration(profiler, loop_feature)

    async def _drain_approval_loop(self, result: dict, turn_id, iteration: int, profiler: Profiler) -> AsyncIterator[dict]:
        """Handle pending approvals until none remain. Mutates result in place."""
        while result.get("pending_approval"):
            debug_log("INFO", "Approval required — SSE stays connected", chat_id=str(self._chat_id))
            pending = result["pending_approval"]
            request_id = pending[0].get("request_id", str(uuid4())) if pending else str(uuid4())

            async for event in handle_pending_approval(
                pending, request_id=request_id,
                bridge=self._bridge, audit_logger=self._audit,
                chat_id=self._chat_id,
            ):
                yield event
            profiler.checkpoint("approval_events_emitted")

            decisions = await self._bridge.gather_decisions(request_id, len(pending))
            debug_log("INFO", "Approval decisions collected",
                      request_id=request_id, count=len(decisions))

            await log_transition(
                self._audit, Transition.APPROVAL_GRANTED,
                chat_id=_safe_uuid(self._chat_id),
                turn_id=self._turn_id, iteration=self._iteration,
            )

            approved, rejected = _apply_decisions(pending, decisions)
            await self._log_approved_tools(approved)
            result["approved_tool_calls"] = result.get("approved_tool_calls", []) + approved
            result["rejected_tool_calls"] = result.get("rejected_tool_calls", []) + rejected
            result["pending_approval"] = []
            result["transition"] = (
                Transition.APPROVAL_GRANTED if approved
                else Transition.APPROVAL_REJECTED
            )

            state = result
            state["_turn_id"] = turn_id
            state["_iteration"] = iteration
            result.update(await self._graph.ainvoke(state, self._config))
            profiler.checkpoint("graph_resume")

    async def _log_approved_tools(self, approved: list[dict]) -> None:
        if not self._audit:
            return
        for tc in approved:
            fn = tc.get("function", {})
            await self._audit.log(AuditEvent(
                timestamp=str(uuid4()),
                level=AuditLevel.INFO,
                actor="system",
                event="TOOL_APPROVED",
                tool_name=fn.get("name"),
                decision="APPROVED",
                turn_id=self._turn_id,
                iteration=self._iteration,
            ))

    def _exit_events(self, profiler: Profiler, loop_feature: str, action: str) -> list[dict]:
        """Finalize loop and yield completion events."""
        _finalize_iteration(profiler, loop_feature)
        if action == "return":
            return [{"event": "done", "data": "{}"}]
        return []

    async def _emit_sse(self, state: dict, counter: list[int], profiler: Profiler):
        """Emit SSE events. counter[0] is skip_assistant_count, updated in place."""
        evs = emit_events(state, chat_id=self._chat_id, skip_assistant_count=counter[0])
        for ev in evs:
            yield ev
            if ev.get("event") == "assistant":
                counter[0] += 1
        profiler.checkpoint("emit_events")


    async def _compress_context(self, state: dict) -> None:
        """Compress message context if over token threshold."""
        messages = state.get("messages", [])
        tokens = self._context_manager.count_tokens(messages)
        ws = getattr(self._context_manager, "window_size", 128000)
        th = getattr(self._context_manager, "threshold", 0.7)
        limit = int(ws * th)
        if self._context_manager.needs_compression(tokens):
            debug_log("INFO", "Context compression triggered",
                      tokens=tokens, limit=limit, msg_count=len(messages),
                      chat_id=str(self._chat_id))
            state["messages"] = await self._context_manager.compress(messages)
            await log_transition(
                self._audit, Transition.CONTEXT_COMPACTED,
                chat_id=_safe_uuid(self._chat_id),
                turn_id=getattr(self, "_turn_id", None),
                iteration=getattr(self, "_iteration", None),
            )

    async def _recover_from_error(self, state: dict) -> tuple[str | None, dict | None]:
        """Attempt error recovery. Returns (action, optional_error_event)."""
        llm_error = state.get("llm_error")
        if not llm_error or not self._error_recovery:
            return None, None
        debug_log("WARN", "LLM error detected — attempting recovery",
                  code=llm_error.get("code", "?"), chat_id=str(self._chat_id))
        handled = await handle_llm_error(
            state,
            error_recovery=self._error_recovery,
            context_manager=self._context_manager,
            llm=self._llm,
            audit_logger=self._audit,
        )
        if not handled:
            debug_log("ERROR", "Recovery exhausted — exiting loop",
                      code=llm_error.get("code", "?"), chat_id=str(self._chat_id))
            await log_transition(
                self._audit, Transition.ERROR_EXIT,
                chat_id=_safe_uuid(self._chat_id),
                turn_id=getattr(self, "_turn_id", None),
                iteration=getattr(self, "_iteration", None),
            )
            return "return", {
                "event": "error",
                "data": json.dumps(
                    {"code": llm_error.get("code", 500), "message": "Recovery exhausted"},
                    default=str,
                ),
            }
        return "continue", None

    async def _handle_transition(self, state: dict) -> str | None:
        """Route loop based on state transition.

        Returns "return" to stop, "continue" to loop, or None to fall through.
        """
        transition = get_transition(state)
        debug_log("DEBUG", "Routing transition", transition=str(transition),
                  chat_id=str(self._chat_id))
        _audit_kwargs = dict(
            chat_id=_safe_uuid(self._chat_id),
            turn_id=getattr(self, "_turn_id", None),
            iteration=getattr(self, "_iteration", None),
        )
        if transition == Transition.DONE:
            await log_transition(self._audit, Transition.DONE, **_audit_kwargs)
            return "return"
        if transition == Transition.TOOL_RESULTS:
            await log_transition(self._audit, Transition.TOOL_RESULTS, **_audit_kwargs)
            return "continue"
        return None
