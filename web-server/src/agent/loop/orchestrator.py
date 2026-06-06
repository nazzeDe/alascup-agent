"""Agent loop orchestrator — composes graph invocation, event emission, and handlers."""

import json
import os
from uuid import uuid4
from typing import AsyncIterator

from loguru import logger

from src.agent.loop.approval import ApprovalHandler
from src.agent.loop.audit import audit_transition, _safe_uuid
from src.agent.loop.events import emit_events
from src.agent.loop.handlers import handle_llm_error
from src.agent.loop.transitions import (
    clear_transient_fields,
    get_transition,
)
from src.agent.state import ROLE_MAP, Transition
from src.models.audit import AuditActor
from src.observability.debug_log import log as debug_log
from src.observability.timing import FeatureTimeTracker, write_profile, start_feature, complete_feature, profiler_enabled


def _is_assistant(m) -> bool:
    r = m.get("role", m.get("type", "")) if isinstance(m, dict) else str(getattr(m, "type", ""))
    return ROLE_MAP.get(r, r) == "assistant"


def _finalize_iteration(profiler: FeatureTimeTracker, loop_feature: str) -> None:
    _log_profile(profiler)
    complete_feature(loop_feature)


def _log_profile(profiler: FeatureTimeTracker) -> None:
    report = profiler.report()
    if report:
        write_profile(report)




class LoopOrchestrator:
    """Orchestrates the think → review → act → observe ReAct loop.

    SSE connection stays alive across the full chat turn.  When review_node
    returns pending_approval, the orchestrator yields the approval event,
    waits for a decision via the bridge, merges decisions into state, and
    re-invokes the graph — all on the same SSE stream.
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
        lifecycle=None,
    ):
        self._graph = graph
        self._context_manager = context_manager
        self._bridge = bridge
        self._audit = audit_logger
        self._error_recovery = error_recovery
        self._llm = llm
        self._model: str = getattr(llm, "_config", None) and getattr(llm._config, "model", "") or ""
        self._config = {}
        self._chat_id = chat_id
        self._lifecycle = lifecycle
        self._approval = ApprovalHandler(graph=graph, bridge=bridge, audit_logger=audit_logger, lifecycle=lifecycle)
        # Circuit breaker: hard limits to prevent token explosion and infinite loops.
        # Industry pattern (LangGraph recursion_limit=25, Claude Code max_turns).
        # No interactive pause — just stop and report. User can re-submit if needed.
        self._max_iterations = int(os.getenv("AGENT_MAX_ITERATIONS", "30"))
        ws = getattr(self._context_manager, "window_size", 128000)
        self._token_ceiling = int(ws * float(os.getenv("AGENT_TOKEN_CEILING_RATIO", "0.95")))

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

        profiler = FeatureTimeTracker(profile_enabled=profiler_enabled())

        async for event in self._run_loop(state, turn_id, emitted_assistant_count, profiler):
            yield event

    async def _run_loop(self, state: dict, turn_id, emitted_assistant_count: int, profiler: FeatureTimeTracker) -> AsyncIterator[dict]:
        """Core ReAct loop — extracted from run() to keep run() as a thin wrapper."""
        it = 0

        while True:
            it += 1
            self._iteration = it
            loop_feature = f"loop:{self._chat_id}#{it}"
            start_feature(loop_feature)

            # 0. Circuit breaker: max iterations (LangGraph recursion_limit pattern)
            if it > self._max_iterations:
                await self._handle_turn_limit_exceeded()
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "code": "TURN_LIMIT_EXCEEDED",
                        "message": f"Agent exceeded max iterations ({self._max_iterations}). Task may be too complex — try breaking it down.",
                    }),
                }
                for ev in self._exit_events(profiler, loop_feature, "return"):
                    yield ev
                return
            self._inject_turn_hint(state, it)

            # 1. Context compression
            await self._compress_context(state)

            # 1.5 Circuit breaker: token ceiling (compression exhausted, still over budget)
            if await self._check_token_ceiling(state):
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "code": "TOKEN_BUDGET_EXCEEDED",
                        "message": "Context too large even after compression. Start a new session or narrow the task scope.",
                    }),
                }
                for ev in self._exit_events(profiler, loop_feature, "return"):
                    yield ev
                return

            # 2. Invoke graph
            state["_turn_id"] = turn_id
            state["_iteration"] = it
            state["_model"] = self._model
            result = await self._graph.ainvoke(state, self._config)
            profiler.checkpoint("graph_ainvoke")
            logger.debug("graph_done pending_approval={p} tool_calls={t} approved={a} transition={r}",
                         p=bool(result.get("pending_approval")),
                         t=len(result.get("tool_calls", [])),
                         a=len(result.get("approved_tool_calls", [])),
                         r=get_transition(result))

            # 3. Approval flow — orchestrator-owned, no interrupt/resume
            result["_chat_id"] = self._chat_id
            async for event in self._approval.resolve(result, profiler=profiler):
                yield event
            state = result
            logger.debug("approval_loop_exited pending_approval={p} transition={r}",
                         p=bool(state.get("pending_approval")),
                         r=get_transition(state))

            # 4. Error recovery
            action, error_event = await self._recover_from_error(state)
            if error_event:
                yield error_event
            if action == "continue":
                logger.debug("error_recovery_continue")
                _finalize_iteration(profiler, loop_feature)
                continue
            if action == "return":
                logger.debug("error_recovery_return")
                for ev in self._exit_events(profiler, loop_feature, action):
                    yield ev
                return

            # 5. Emit SSE events
            counter = [emitted_assistant_count]
            logger.debug("emit_sse msgs={m} skip={s}", m=len(state.get("messages", [])), s=counter[0])
            async for event in self._emit_sse(state, counter, profiler):
                yield event

            # 6-7. Clear + transition
            clear_transient_fields(state)
            action = await self._handle_transition(state)
            profiler.checkpoint("handle_transition")
            logger.debug("transition route={r}", r=action)
            if action not in ("continue",):
                for ev in self._exit_events(profiler, loop_feature, "return"):
                    yield ev
                return
            debug_log("DEBUG", "Transition → CONTINUE", chat_id=str(self._chat_id))
            _finalize_iteration(profiler, loop_feature)

    def _exit_events(self, profiler: FeatureTimeTracker, loop_feature: str, action: str) -> list[dict]:
        """Finalize loop and yield completion events."""
        _finalize_iteration(profiler, loop_feature)
        if action == "return":
            return [{"event": "done", "data": "{}"}]
        return []

    async def _emit_sse(self, state: dict, counter: list[int], profiler: FeatureTimeTracker):
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
            await audit_transition(
                self._audit, Transition.CONTEXT_COMPACTED,
                chat_id=_safe_uuid(self._chat_id),
                turn_id=getattr(self, "_turn_id", None),
                iteration=getattr(self, "_iteration", None),
                actor=AuditActor.SYSTEM, model=self._model,
            )

    def _inject_turn_hint(self, state: dict, it: int) -> str | None:
        """Inject progressive hints to LLM when approaching iteration limit.

        Injects once at 70% threshold, then replaces with a stronger hint
        when ≤3 turns remain. Never appends — hint is replaced not accumulated.
        """
        remaining = self._max_iterations - it
        if remaining <= 3:
            hint = f"\n\n[SYSTEM] Only {remaining} turns remaining. Conclude immediately with a summary of what you know."
        elif it >= int(self._max_iterations * 0.7):
            hint = "\n\n[SYSTEM] Approaching turn limit. Prioritize completion — skip non-critical investigation."
        else:
            return None

        system = state.get("system") or ""
        prev = getattr(self, "_last_hint", None)
        if prev and prev in system:
            system = system.replace(prev, hint)
        else:
            system += hint
        self._last_hint = hint
        state["system"] = system
        return hint

    async def _check_token_ceiling(self, state: dict) -> bool:
        """Return True if token count exceeds hard ceiling even after compression.

        This is the last-resort circuit breaker: if compression couldn't bring
        tokens under the ceiling, the conversation is too large to continue.
        """
        messages = state.get("messages", [])
        tokens = self._context_manager.count_tokens(messages)
        if tokens > self._token_ceiling:
            debug_log("ERROR", "Token ceiling breached after compression",
                      tokens=tokens, ceiling=self._token_ceiling,
                      msg_count=len(messages), chat_id=str(self._chat_id))
            await audit_transition(
                self._audit, Transition.TOKEN_BUDGET_EXCEEDED,
                chat_id=_safe_uuid(self._chat_id),
                turn_id=getattr(self, "_turn_id", None),
                iteration=getattr(self, "_iteration", None),
                actor=AuditActor.SYSTEM, model=self._model,
            )
            return True
        return False

    async def _handle_turn_limit_exceeded(self) -> None:
        """Log turn limit exceeded and audit the event."""
        debug_log("ERROR", "Turn limit exceeded",
                  max_iterations=self._max_iterations, chat_id=str(self._chat_id))
        await audit_transition(
            self._audit, Transition.TURN_LIMIT_EXCEEDED,
            chat_id=_safe_uuid(self._chat_id),
            turn_id=getattr(self, "_turn_id", None),
            iteration=getattr(self, "_iteration", None),
            actor=AuditActor.SYSTEM, model=self._model,
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
            await audit_transition(
                self._audit, Transition.ERROR_EXIT,
                chat_id=_safe_uuid(self._chat_id),
                turn_id=getattr(self, "_turn_id", None),
                iteration=getattr(self, "_iteration", None),
                actor=AuditActor.SYSTEM, model=self._model,
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
            actor=AuditActor.AGENT,
            model=self._model,
        )
        if transition == Transition.DONE:
            await audit_transition(self._audit, Transition.DONE, **_audit_kwargs)
            return "return"
        if transition in (Transition.TURN_LIMIT_EXCEEDED, Transition.TOKEN_BUDGET_EXCEEDED):
            return "return"
        if transition in (Transition.TOOL_RESULTS, Transition.APPROVAL_REJECTED):
            await audit_transition(self._audit, transition, **_audit_kwargs)
            return "continue"
        if transition == Transition.APPROVAL_GRANTED:
            return "continue"
        return None

