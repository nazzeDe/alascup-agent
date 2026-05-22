"""Agent loop orchestrator — composes graph invocation, event emission, and handlers."""

import json
from typing import AsyncIterator

from src.agent.loop.audit import log_transition
from src.agent.loop.events import emit_events
from src.agent.loop.handlers import handle_interrupt, handle_llm_error
from src.agent.loop.transitions import (
    clear_transient_fields,
    get_transition,
    has_interrupt,
)
from src.agent.state import Transition


class LoopOrchestrator:
    """Orchestrates the think → review → act → observe ReAct loop.

    Owns the loop dependencies and exposes a single async generator.
    Dependencies are injected at construction time — the orchestrator is
    stateless across loop iterations (all mutable state lives in the
    state dict).
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
        """Execute the ReAct loop until interrupt or DONE."""
        state = dict(initial_state)
        emitted_assistant_count = 0

        while True:
            # 1. Context compression
            await self._compress_context(state)

            # 2. Invoke graph (think → review → act → observe)
            result = await self._graph.ainvoke(state, self._config)

            # 3. Interrupt → yield approval event, stop
            if has_interrupt(result):
                async for event in handle_interrupt(
                    result,
                    bridge=self._bridge,
                    audit_logger=self._audit,
                    chat_id=self._chat_id,
                ):
                    yield event
                return

            state = result

            # 4. LLM error recovery
            action, error_event = await self._recover_from_error(state)
            if action == "return":
                if error_event:
                    yield error_event
                return
            if action == "continue":
                continue

            # 5. Emit SSE events
            evs = emit_events(state, chat_id=self._chat_id, skip_assistant_count=emitted_assistant_count)
            for ev in evs:
                if ev.get("event") == "assistant":
                    emitted_assistant_count += 1
                yield ev

            # 6. Clear per-iteration transient fields
            clear_transient_fields(state)

            # 7. Route based on transition
            action = await self._handle_transition(state)
            if action == "return":
                yield {"event": "done", "data": "{}"}
                return
            if action == "continue":
                continue

            # Unknown transition — exit safely
            yield {"event": "done", "data": "{}"}
            return

    async def _compress_context(self, state: dict) -> None:
        """Compress message context if over token threshold."""
        tokens = self._context_manager.count_tokens(state.get("messages", []))
        if self._context_manager.needs_compression(tokens):
            state["messages"] = await self._context_manager.compress(
                state.get("messages", [])
            )
            await log_transition(self._audit, Transition.CONTEXT_COMPACTED)

    async def _recover_from_error(self, state: dict) -> tuple[str | None, dict | None]:
        """Attempt error recovery. Returns (action, optional_error_event)."""
        if not state.get("llm_error") or not self._error_recovery:
            return None, None
        handled = await handle_llm_error(
            state,
            error_recovery=self._error_recovery,
            context_manager=self._context_manager,
            llm=self._llm,
            audit_logger=self._audit,
        )
        if not handled:
            llm_error = state["llm_error"]
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
        if transition == Transition.DONE:
            await log_transition(self._audit, Transition.DONE)
            return "return"
        if transition == Transition.TOOL_RESULTS:
            await log_transition(self._audit, Transition.TOOL_RESULTS)
            return "continue"
        return None
