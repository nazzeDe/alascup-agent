"""Agent loop orchestrator — composes graph invocation, event emission, and handlers."""

import json
from uuid import uuid4
from typing import AsyncIterator

from langgraph.types import Command
from loguru import logger

from src.agent.loop.audit import log_transition
from src.agent.loop.events import emit_events
from src.agent.loop.handlers import handle_interrupt, handle_llm_error
from src.agent.loop.transitions import (
    clear_transient_fields,
    get_transition,
    has_interrupt,
)
from src.agent.state import ROLE_MAP, Transition
from src.observability.debug_log import log as debug_log
from src.observability.profiler import Profiler, write_profile
from src.tools import start_feature, complete_feature


def _log_profile(profiler: Profiler) -> None:
    report = profiler.report()
    if report:
        write_profile(report)


def _extract_request_id(state: dict) -> str:
    """Extract request_id from LangGraph interrupt state."""
    interrupts = state.get("__interrupt__", [])
    obj = interrupts[0] if interrupts else None
    value = getattr(obj, "value", obj) if obj else {}
    if isinstance(value, dict):
        return value.get("request_id", str(uuid4()))
    return str(uuid4())


def _extract_pending_count(state: dict) -> int:
    """Count pending tool calls in an interrupt state."""
    interrupts = state.get("__interrupt__", [])
    obj = interrupts[0] if interrupts else None
    value = getattr(obj, "value", obj) if obj else {}
    if isinstance(value, dict):
        pending = value.get("pending_tool_calls", [])
        return len(pending) if pending else 1
    return 1


class LoopOrchestrator:
    """Orchestrates the think → review → act → observe ReAct loop.

    SSE connection stays alive across the full chat turn.  When the graph
    interrupts for approval the orchestrator yields the approval event,
    waits for a decision via the bridge, then resumes the graph inline
    — all on the same SSE stream.
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
        checkpointer=None,
    ):
        self._graph = graph
        self._context_manager = context_manager
        self._bridge = bridge
        self._audit = audit_logger
        self._error_recovery = error_recovery
        self._llm = llm
        self._config = {"configurable": {"thread_id": chat_id}}
        self._chat_id = chat_id
        self._checkpointer = checkpointer

    async def run(self, initial_state: dict) -> AsyncIterator[dict]:
        """Execute ReAct loop, yielding all events on one SSE connection."""
        # Clear checkpoint to prevent message duplication across turns.
        # Session DB is the source of truth for history; the checkpoint only
        # needs to persist within a single turn for interrupt/resume.
        if self._checkpointer is not None:
            try:
                await self._checkpointer.adelete_thread(self._chat_id)
            except Exception:
                logger.warning("Failed to clear checkpoint for thread {tid}", tid=self._chat_id)

        state = dict(initial_state)
        def _is_assistant(m):
            r = m.get("role", m.get("type", "")) if isinstance(m, dict) else str(getattr(m, "type", ""))
            return ROLE_MAP.get(r, r) == "assistant"

        emitted_assistant_count = sum(1 for m in state.get("messages", []) if _is_assistant(m))
        debug_log("INFO", "Loop start", chat_id=str(self._chat_id),
                  msg_count=len(state.get("messages", [])), assistant_skip=emitted_assistant_count)

        profiler = Profiler()
        it = 0

        while True:
            it += 1
            loop_feature = f"loop:{self._chat_id}#{it}"
            start_feature(loop_feature)

            # 1. Context compression
            prev_msg_count = len(state.get("messages", []))
            await self._compress_context(state)
            profiler.checkpoint("compress_context")
            after_count = len(state.get("messages", []))
            if after_count < prev_msg_count:
                debug_log("INFO", "Context compressed",
                          before=prev_msg_count, after=after_count, chat_id=str(self._chat_id))

            # 2. Invoke graph (think → review → act → observe)
            result = await self._graph.ainvoke(state, self._config)
            profiler.checkpoint("graph_ainvoke")

            # 3. Interrupt → yield approval events, wait for decision, resume inline.
            while has_interrupt(result):
                debug_log("INFO", "Approval required — SSE stays connected",
                          chat_id=str(self._chat_id))
                async for event in handle_interrupt(
                    result,
                    bridge=self._bridge,
                    audit_logger=self._audit,
                    chat_id=self._chat_id,
                ):
                    yield event
                profiler.checkpoint("interrupt_handled")

                request_id = _extract_request_id(result)
                pending = _extract_pending_count(result)
                decisions = await self._bridge.gather_decisions(request_id, pending)
                debug_log("INFO", "Approval decisions collected",
                          request_id=request_id, count=len(decisions))

                await log_transition(self._audit, Transition.APPROVAL_GRANTED)
                result = await self._graph.ainvoke(
                    Command(resume={"decisions": decisions}),
                    self._config,
                )
                profiler.checkpoint("graph_resume")
                logger.debug("RESUME_GRAPH: msgs={msgs} transition={t} interrupt={intr}",
                             msgs=len(result.get("messages", [])),
                             t=result.get("transition"),
                             intr=has_interrupt(result))

            state = result

            # 4. LLM error recovery
            action, error_event = await self._recover_from_error(state)
            profiler.checkpoint("error_recovery")
            if action == "return":
                debug_log("WARN", "Error recovery exhausted — exiting",
                          chat_id=str(self._chat_id))
                if error_event:
                    yield error_event
                _log_profile(profiler)
                complete_feature(loop_feature)
                return
            if action == "continue":
                debug_log("DEBUG", "Error recovered — retrying loop",
                          chat_id=str(self._chat_id))
                _log_profile(profiler)
                complete_feature(loop_feature)
                continue

            # 5. Emit SSE events
            evs = emit_events(state, chat_id=self._chat_id, skip_assistant_count=emitted_assistant_count)
            for ev in evs:
                if ev.get("event") == "assistant":
                    emitted_assistant_count += 1
                yield ev
            profiler.checkpoint("emit_events")

            # 6. Clear per-iteration transient fields
            clear_transient_fields(state)

            # 7. Route based on transition
            action = await self._handle_transition(state)
            profiler.checkpoint("handle_transition")
            if action == "return":
                debug_log("DEBUG", "Transition → DONE", chat_id=str(self._chat_id))
                yield {"event": "done", "data": "{}"}
                _log_profile(profiler)
                complete_feature(loop_feature)
                return
            if action == "continue":
                debug_log("DEBUG", "Transition → CONTINUE (tool results, re-invoke)",
                          chat_id=str(self._chat_id))
                _log_profile(profiler)
                complete_feature(loop_feature)
                continue

            # Unknown transition — exit safely
            debug_log("WARN", "Unknown transition — exiting", chat_id=str(self._chat_id))
            yield {"event": "done", "data": "{}"}
            _log_profile(profiler)
            complete_feature(loop_feature)
            return

    async def resume(self, decisions: list[str]) -> AsyncIterator[dict]:
        """Resume from checkpoint after human approval.

        Used by tests and programmatic approval paths.  For the normal
        user-facing flow the orchestrator handles interrupts inline in
        run() via the bridge's await_approval.
        """
        feature = f"resume:{self._chat_id}"
        start_feature(feature)
        debug_log("INFO", "Resume after approval", chat_id=str(self._chat_id),
                  decisions=",".join(decisions))
        await log_transition(self._audit, Transition.APPROVAL_GRANTED)

        result = await self._graph.ainvoke(
            Command(resume={"decisions": decisions}),
            {"configurable": {"thread_id": self._chat_id}},
        )
        logger.debug("RESUME_GRAPH: msgs={msgs} transition={t} interrupt={intr}",
                     msgs=len(result.get("messages", [])),
                     t=result.get("transition"),
                     intr=has_interrupt(result))

        while has_interrupt(result):
            async for event in handle_interrupt(
                result,
                bridge=self._bridge,
                audit_logger=self._audit,
                chat_id=self._chat_id,
            ):
                yield event
            request_id = _extract_request_id(result)
            pending = _extract_pending_count(result)
            decisions = await self._bridge.gather_decisions(request_id, pending)
            await log_transition(self._audit, Transition.APPROVAL_GRANTED)
            result = await self._graph.ainvoke(
                Command(resume={"decisions": decisions}),
                {"configurable": {"thread_id": self._chat_id}},
            )

        evs = emit_events(result, chat_id=self._chat_id)
        logger.debug("RESUME_EVENTS: {events}", events=[e.get("event", "") for e in evs])
        for ev in evs:
            yield ev
        yield {"event": "done", "data": "{}"}
        complete_feature(feature)

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
            await log_transition(self._audit, Transition.CONTEXT_COMPACTED)

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
            await log_transition(self._audit, Transition.ERROR_EXIT)
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
        if transition == Transition.DONE:
            await log_transition(self._audit, Transition.DONE)
            return "return"
        if transition == Transition.TOOL_RESULTS:
            await log_transition(self._audit, Transition.TOOL_RESULTS)
            return "continue"
        return None
