"""AgentLoop — pure while-loop ReAct executor."""

from uuid import uuid4

from loguru import logger

from src.agent.events import EventChannel, TurnFailed
from src.agent.loop.approval import ApprovalHandler, _inject_rejection_messages
from src.agent.loop.emitter import EventEmitter
from src.agent.loop.circuit_breaker import CircuitBreaker
from src.agent.loop.step import AgentStep, sync_scratch_from_state
from src.agent.state import Transition, TurnScratch, get_transition, init_scratch
from src.agent.turn_context import TurnContext, Auditor
from src.models.audit import AuditActor
from src.observability.debug_log import log as debug_log
from src.observability import trace_points as tp
from src.observability.timing import (
    FeatureTimeTracker,
    write_profile,
    start_feature,
    complete_feature,
    profiler_enabled,
)


def _finalize_iteration(profiler: FeatureTimeTracker, loop_feature: str) -> None:
    _log_profile(profiler)
    complete_feature(loop_feature)


def _log_profile(profiler: FeatureTimeTracker) -> None:
    report = profiler.report()
    if report:
        write_profile(report)


class AgentLoop:
    """Execute ReAct loop: think -> review -> execute -> observe -> repeat.

    Nodes receive TurnContext directly. The orchestrator owns the lifecycle
    (channel, audit, etc.) and delegates inner-loop logic here.
    """

    def __init__(
        self,
        *,
        think_fn,
        review_fn,
        act_fn,
        observe_fn,
        approval: ApprovalHandler,
        breaker: CircuitBreaker,
        auditor_factory,  # callable: (ctx: TurnContext) -> Auditor
        emitter: EventEmitter,
        context_manager,
        error_recovery,
        llm,
        lifecycle=None,
    ):
        self._think = think_fn
        self._review = review_fn
        self._act = act_fn
        self._observe = observe_fn
        self._approval = approval
        self._breaker = breaker
        self._auditor_factory = auditor_factory
        self._context_manager = context_manager
        self._error_recovery = error_recovery
        self._llm = llm
        self._lifecycle = lifecycle

    async def run(self, state: dict, channel: EventChannel) -> None:
        """Drive the ReAct while-loop. channel.close() in finally."""

        turn_id = uuid4()

        from src.agent.turn_context import _safe_uuid

        chat_id = state.get("_chat_id", "")
        emitter = EventEmitter(channel)

        ctx = TurnContext(
            chat_id=_safe_uuid(str(chat_id)) if chat_id else None,
            turn_id=turn_id,
            iteration=0,
            model=state.get("_model", ""),
            stream_sink=emitter,
        )
        auditor = self._auditor_factory(ctx)

        emitted_assistant_count = sum(
            1 for m in state.get("messages", []) if m.get("role", "") == "assistant"
        )
        debug_log(
            "INFO",
            "Loop start",
            chat_id=str(chat_id),
            msg_count=len(state.get("messages", [])),
            assistant_skip=emitted_assistant_count,
        )

        profiler = FeatureTimeTracker(profile_enabled=profiler_enabled())

        try:
            await self._run_loop(state, ctx, auditor, emitter, channel, profiler)
        except Exception as exc:
            logger.opt(exception=True).error(
                "agent_run_failed chat_id={c}", c=str(chat_id)
            )
            await channel.send(
                TurnFailed(code="AGENT_CRASH", message=str(exc))
            )
        finally:
            channel.close()

    async def _run_loop(
        self,
        state: dict,
        ctx: TurnContext,
        auditor: Auditor,
        emitter: EventEmitter,
        channel: EventChannel,
        profiler: FeatureTimeTracker,
    ) -> None:
        """Core ReAct loop — drive think -> review -> act -> observe -> repeat."""
        it = 0

        while True:
            it += 1
            # Create fresh TurnScratch for this iteration
            scratch = init_scratch()

            # Evolve TurnContext with new iteration, recreate Auditor
            ctx = ctx.evolve(iteration=it)
            auditor = self._auditor_factory(ctx)

            loop_feature = f"loop:{state.get('_chat_id', '')}#{it}"
            start_feature(loop_feature)

            if await self._preflight_iteration(state, auditor, channel, it):
                _finalize_iteration(profiler, loop_feature)
                return

            terminal_action = await self._handle_terminal_state(state, scratch, auditor)
            if terminal_action == "return":
                _finalize_iteration(profiler, loop_feature)
                return
            if terminal_action == "continue":
                _finalize_iteration(profiler, loop_feature)
                continue

            step = AgentStep(
                think=self._think,
                review=self._review,
                act=self._act,
                observe=self._observe,
                lifecycle=self._lifecycle,
            )
            await step.run(state, ctx, emitter, phase="main")

            profiler.checkpoint("agent_step_run")

            sync_scratch_from_state(scratch, state)

            debug_log(
                "DEBUG",
                tp.TRANSITION,
                reason="agent_step_done",
                pending_approval=bool(scratch.pending_approval),
                tool_calls=len(scratch.tool_calls),
                approved=len(scratch.approved_tool_calls),
                transition=get_transition(scratch),
            )

            await self._run_approval_loop(state, scratch, ctx, emitter, profiler, step)

            error_action = await self._handle_error_recovery(state, channel, auditor)
            if error_action in ("continue", "return"):
                _finalize_iteration(profiler, loop_feature)
                if error_action == "continue":
                    continue
                return

            self._emit_iteration_events(state, scratch, emitter)
            profiler.checkpoint("emit_events")

            if await self._should_stop_after_transition(scratch, auditor, profiler):
                _finalize_iteration(profiler, loop_feature)
                return
            debug_log(
                "DEBUG",
                "Transition -> CONTINUE",
                chat_id=str(state.get("_chat_id", "")),
            )
            _finalize_iteration(profiler, loop_feature)

    async def _preflight_iteration(
        self, state: dict, auditor: Auditor, channel: EventChannel, it: int
    ) -> bool:
        if self._breaker.check_iteration(it):
            await self._handle_turn_limit_exceeded(auditor)
            await channel.send(
                TurnFailed(
                    code="TURN_LIMIT_EXCEEDED",
                    message=f"Agent exceeded max iterations ({self._breaker.max_iterations}). Task may be too complex — try breaking it down.",
                )
            )
            return True
        self._breaker.inject_hint(state, it)
        await self._compress_context(state, auditor)
        if await self._check_token_ceiling(state, auditor):
            await channel.send(
                TurnFailed(
                    code="TOKEN_BUDGET_EXCEEDED",
                    message="Context too large even after compression. Start a new session or narrow the task scope.",
                )
            )
            return True
        return False

    async def _handle_terminal_state(
        self, state: dict, scratch: TurnScratch, auditor: Auditor
    ) -> str | None:
        if state.get("transition") != Transition.DONE:
            return None
        scratch.transition = Transition.DONE
        debug_log("DEBUG", tp.TRANSITION, reason="agent_step_terminal",
                  pending_approval=0, tool_calls=0, approved=0)
        return await self._handle_transition(scratch, auditor)

    async def _run_approval_loop(
        self,
        state: dict,
        scratch: TurnScratch,
        ctx: TurnContext,
        emitter: EventEmitter,
        profiler: FeatureTimeTracker,
        step: AgentStep,
    ) -> None:
        while True:
            await self._approval.resolve(
                scratch, profiler=profiler, turn_ctx=ctx, emitter=emitter
            )
            self._copy_scratch_to_state(state, scratch)
            _inject_rejection_messages(state, scratch.rejected_tool_calls)
            if get_transition(scratch) not in (
                Transition.APPROVAL_GRANTED,
                Transition.APPROVAL_REJECTED,
            ):
                break

            self._emit_approval_started(scratch, emitter)
            await step.run(state, ctx, emitter, phase="approval re-entry")
            profiler.checkpoint("approval_reentry_run")
            sync_scratch_from_state(scratch, state)
            self._log_approval_reentry(scratch)
            self._emit_approval_finished(state, scratch, emitter)

            if not scratch.pending_approval:
                debug_log("DEBUG", tp.LOOP_EXIT,
                          reason="approval_empty",
                          pending_approval=False)
                break

    def _copy_scratch_to_state(self, state: dict, scratch: TurnScratch) -> None:
        state["pending_approval"] = scratch.pending_approval
        state["approved_tool_calls"] = scratch.approved_tool_calls
        state["rejected_tool_calls"] = scratch.rejected_tool_calls
        state["transition"] = scratch.transition

    def _emit_approval_started(self, scratch: TurnScratch, emitter: EventEmitter) -> None:
        debug_log("DEBUG", tp.EMIT_TOOL_STARTED,
                  count=len(scratch.approved_tool_calls),
                  ids=[tc.get("id") for tc in scratch.approved_tool_calls],
                  phase="approval")
        emitter.emit_tools_started(scratch.approved_tool_calls)

    def _log_approval_reentry(self, scratch: TurnScratch) -> None:
        debug_log(
            "DEBUG",
            tp.TRANSITION,
            reason="approval_reentry_done",
            pending_approval=bool(scratch.pending_approval),
            transition=get_transition(scratch),
        )

    def _emit_approval_finished(
        self, state: dict, scratch: TurnScratch, emitter: EventEmitter
    ) -> None:
        result_sources = scratch._emitted_results or scratch.streaming_tool_results
        debug_log("DEBUG", tp.EMIT_TOOL_FINISHED,
                  count=len(result_sources),
                  ids=[r.get("tool_call_id") for r in result_sources],
                  phase="approval")
        emitter.emit_tools_finished(result_sources)
        scratch._emitted_results = []
        state["_emitted_results"] = []

    async def _handle_error_recovery(
        self, state: dict, channel: EventChannel, auditor: Auditor
    ) -> str | None:
        action, error_event = await self._recover_from_error(state, channel, auditor)
        if error_event:
            await channel.send(error_event)
        if action in ("continue", "return"):
            debug_log("DEBUG", tp.ERROR_RECOVERY, outcome=action)
        return action

    def _emit_iteration_events(
        self, state: dict, scratch: TurnScratch, emitter: EventEmitter
    ) -> None:
        debug_log("DEBUG", tp.EMIT_TOOL_STARTED,
                  msg_count=len(state.get("messages", [])),
                  phase="main_emit")
        pending_ids = {tc.get("id") for tc in scratch.pending_approval if tc.get("id")}
        self._emit_started_events(state, scratch, emitter, pending_ids)
        self._emit_finished_events(state, scratch, emitter)
        self._emit_streaming_events(state, scratch, emitter)

    def _emit_started_events(
        self, state: dict, scratch: TurnScratch, emitter: EventEmitter, pending_ids: set
    ) -> None:
        tc_list = scratch.tool_calls
        if tc_list:
            tc_to_emit = [tc for tc in tc_list if tc.get("id") not in pending_ids]
            debug_log(
                "DEBUG",
                "emit_tools_started",
                chat_id=str(state.get("_chat_id", "")),
                total=len(tc_list),
                skipped_pending=len(tc_list) - len(tc_to_emit),
                emitting=len(tc_to_emit),
                ids=[tc.get("id", "?") for tc in tc_to_emit],
            )
        else:
            debug_log(
                "DEBUG",
                "emit_tools_started SKIPPED — no tool_calls in scratch",
                chat_id=str(state.get("_chat_id", "")),
            )
        emitter.emit_tools_started(scratch.tool_calls, skip_ids=pending_ids)

    def _emit_finished_events(
        self, state: dict, scratch: TurnScratch, emitter: EventEmitter
    ) -> None:
        tr_list = scratch.tool_results
        if tr_list:
            debug_log(
                "DEBUG",
                "emit_tools_finished",
                chat_id=str(state.get("_chat_id", "")),
                count=len(tr_list),
                ids=[tr.get("tool_call_id", "?") for tr in tr_list],
            )
        else:
            debug_log(
                "DEBUG",
                "emit_tools_finished SKIPPED — no tool_results in scratch",
                chat_id=str(state.get("_chat_id", "")),
            )
        emitter.emit_tools_finished(scratch.tool_results)

    def _emit_streaming_events(
        self, state: dict, scratch: TurnScratch, emitter: EventEmitter
    ) -> None:
        stream_src = scratch._emitted_results or scratch.streaming_tool_results
        if stream_src:
            debug_log(
                "DEBUG",
                "emit_streaming_tool_results",
                chat_id=str(state.get("_chat_id", "")),
                count=len(stream_src),
                ids=[r.get("tool_call_id", "?") for r in stream_src],
                source="_emitted_results" if scratch._emitted_results else "streaming_tool_results",
            )
        else:
            debug_log(
                "DEBUG",
                "emit_streaming_tool_results SKIPPED — no emitted_results or streaming_tool_results",
                chat_id=str(state.get("_chat_id", "")),
            )
        emitter.emit_streaming_tool_results(stream_src)

    async def _should_stop_after_transition(
        self, scratch: TurnScratch, auditor: Auditor, profiler: FeatureTimeTracker
    ) -> bool:
        action = await self._handle_transition(scratch, auditor)
        profiler.checkpoint("handle_transition")
        debug_log("DEBUG", tp.TRANSITION, route=action)
        if action in ("continue",):
            return False
        debug_log("DEBUG", tp.LOOP_EXIT,
                  reason="transition_stop",
                  transition=get_transition(scratch))
        return True

    async def _compress_context(self, state: dict, auditor: Auditor) -> None:
        messages = state.get("messages", [])
        tokens = self._context_manager.count_tokens(messages)
        ws = getattr(self._context_manager, "window_size", 128000)
        th = getattr(self._context_manager, "threshold", 0.7)
        limit = int(ws * th)
        if self._context_manager.needs_compression(tokens):
            debug_log(
                "INFO",
                "Context compression triggered",
                tokens=tokens,
                limit=limit,
                msg_count=len(messages),
                chat_id=str(state.get("_chat_id", "")),
            )
            state["messages"] = await self._context_manager.compress(messages)
            await auditor.transition(
                Transition.CONTEXT_COMPACTED, actor=AuditActor.SYSTEM
            )

    async def _check_token_ceiling(
        self, state: dict, auditor: Auditor
    ) -> bool:
        messages = state.get("messages", [])
        tokens = self._context_manager.count_tokens(messages)
        if self._breaker.check_token_ceiling(tokens):
            debug_log(
                "ERROR",
                "Token ceiling breached after compression",
                tokens=tokens,
                ceiling=self._breaker.token_ceiling,
                msg_count=len(messages),
                chat_id=str(state.get("_chat_id", "")),
            )
            await auditor.transition(
                Transition.TOKEN_BUDGET_EXCEEDED, actor=AuditActor.SYSTEM
            )
            return True
        return False

    async def _handle_turn_limit_exceeded(self, auditor: Auditor) -> None:
        debug_log(
            "ERROR",
            "Turn limit exceeded",
            max_iterations=self._breaker.max_iterations,
        )
        await auditor.transition(
            Transition.TURN_LIMIT_EXCEEDED, actor=AuditActor.SYSTEM
        )

    async def _recover_from_error(
        self, state: dict, channel: EventChannel, auditor: Auditor
    ) -> tuple[str | None, "TurnFailed | None"]:
        llm_error = state.get("llm_error")
        if not llm_error:
            return None, None

        debug_log(
            "WARN",
            "LLM error detected",
            code=llm_error.get("code", "?"),
            msg=str(llm_error.get("message", ""))[:200],
            chat_id=str(state.get("_chat_id", "")),
        )

        # No recovery configured — surface error directly so SSE gets 'error' event
        if not self._error_recovery:
            orig_msg = llm_error.get("message", "LLM call failed")
            logger.error(
                "LLM error (no recovery configured) | code={code} | {msg}",
                code=llm_error.get("code", "?"),
                msg=str(orig_msg)[:500],
            )
            return "return", TurnFailed(
                code=llm_error.get("code", 500),
                message=f"LLM error (code={llm_error.get('code', '?')}): {orig_msg}",
            )

        debug_log(
            "WARN",
            "LLM error detected - attempting recovery",
            code=llm_error.get("code", "?"),
            chat_id=str(state.get("_chat_id", "")),
        )
        handled = await self._handle_llm_error(
            state, auditor
        )
        if not handled:
            debug_log(
                "ERROR",
                "Recovery exhausted - exiting loop",
                code=llm_error.get("code", "?"),
                chat_id=str(state.get("_chat_id", "")),
            )
            orig_msg = llm_error.get("message", "unknown")
            logger.error(
                "Recovery exhausted | code={code} | {msg}",
                code=llm_error.get("code", "?"),
                msg=str(orig_msg)[:500],
            )
            await auditor.transition(
                Transition.ERROR_EXIT, actor=AuditActor.SYSTEM
            )
            return "return", TurnFailed(
                code=llm_error.get("code", 500),
                message=f"Recovery exhausted (cause: code={llm_error.get('code', '?')}, {orig_msg})",
            )
        # Clear llm_error from state on success
        state["llm_error"] = None
        return "continue", None

    async def _handle_llm_error(self, state: dict, auditor: Auditor) -> bool:
        """Attempt recovery from an LLM error. Returns True if recovered, False if exhausted."""
        from src.services.llm_adapter import classify_error

        error = state.get("llm_error", {})
        error_type = classify_error(
            error.get("code", 0),
            error.get("message", ""),
            error.get("stop_reason"),
        )
        if not error_type:
            return False

        strategy = self._error_recovery.get_strategy(error_type)
        if not strategy.get("recoverable"):
            return False

        action = strategy["action"]

        if action == "compress_context":
            compressed = await self._context_manager.compress(state.get("messages", []))
            state["messages"] = compressed
            await auditor.transition(Transition.CONTEXT_COMPACTED, actor=AuditActor.SYSTEM)
        elif action == "aggressive_compress":
            compressed = await self._context_manager.compress(state.get("messages", []))
            state["messages"] = compressed
            await auditor.transition(Transition.CONTEXT_COMPACTED, actor=AuditActor.SYSTEM)
        elif action == "escalate_token_limit":
            self._llm.escalate_max_tokens()
        elif action == "continue_inject":
            msgs = list(state.get("messages", []))
            msgs.append({"role": "user", "content": "Please continue from where you stopped."})
            state["messages"] = msgs
        elif action == "switch_fallback_model":
            self._llm.switch_to_fallback()

        self._error_recovery.record_attempt(error_type, action)
        state["llm_error"] = None
        return True

    async def _handle_transition(
        self, scratch: "TurnScratch", auditor: Auditor
    ) -> str | None:
        transition = get_transition(scratch)
        if transition == Transition.DONE:
            await auditor.transition(Transition.DONE, actor=AuditActor.AGENT)
            return "return"
        if transition in (
            Transition.TURN_LIMIT_EXCEEDED,
            Transition.TOKEN_BUDGET_EXCEEDED,
        ):
            return "return"
        if transition in (
            Transition.TOOL_RESULTS,
            Transition.APPROVAL_REJECTED,
            Transition.APPROVAL_PENDING,
        ):
            await auditor.transition(transition, actor=AuditActor.AGENT)
            return "continue"
        if transition == Transition.APPROVAL_GRANTED:
            return "continue"
        return None
