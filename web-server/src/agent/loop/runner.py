"""AgentLoop — pure while-loop ReAct executor. Replaces LangGraph StateGraph."""

import asyncio
from uuid import uuid4

from loguru import logger

from src.agent.events import EventChannel, TurnFailed
from src.agent.loop.approval import ApprovalHandler, _inject_rejection_messages
from src.agent.loop.emitter import EventEmitter
from src.agent.loop.circuit_breaker import CircuitBreaker
from src.agent.nodes.observe import route_after_review, route_after_think
from src.agent.state import Transition, TurnScratch, get_transition, init_scratch
from src.agent.turn_context import TurnContext, Auditor
from src.models.audit import AuditActor
from src.observability.debug_log import log as debug_log
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

    Pure Python while-loop replacement for LangGraph StateGraph. Nodes receive
    TurnContext directly instead of RunnableConfig. The orchestrator owns the
    lifecycle (channel, audit, etc.) and delegates inner-loop logic here.
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
    ):
        self._think = think_fn
        self._review = review_fn
        self._act = act_fn
        self._observe = observe_fn
        self._approval = approval
        self._breaker = breaker
        self._auditor_factory = auditor_factory
        self._emitter = emitter
        self._context_manager = context_manager
        self._error_recovery = error_recovery
        self._llm = llm

    async def run(self, state: dict, channel: EventChannel) -> None:
        """Drive the ReAct while-loop. channel.close() in finally."""

        turn_id = uuid4()

        from src.agent.turn_context import _safe_uuid

        chat_id = state.get("_chat_id", "")

        ctx = TurnContext(
            chat_id=_safe_uuid(str(chat_id)) if chat_id else None,
            turn_id=turn_id,
            iteration=0,
            model=state.get("_model", ""),
        )
        auditor = self._auditor_factory(ctx)
        emitter = EventEmitter(channel)

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

            # 0. Circuit breaker: max iterations
            if self._breaker.check_iteration(it):
                await self._handle_turn_limit_exceeded(auditor)
                await channel.send(
                    TurnFailed(
                        code="TURN_LIMIT_EXCEEDED",
                        message=f"Agent exceeded max iterations ({self._breaker.max_iterations}). Task may be too complex — try breaking it down.",
                    )
                )
                _finalize_iteration(profiler, loop_feature)
                return
            self._breaker.inject_hint(state, it)

            # 1. Context compression
            await self._compress_context(state, auditor)

            # 1.5 Circuit breaker: token ceiling
            if await self._check_token_ceiling(state, auditor):
                await channel.send(
                    TurnFailed(
                        code="TOKEN_BUDGET_EXCEEDED",
                        message="Context too large even after compression. Start a new session or narrow the task scope.",
                    )
                )
                _finalize_iteration(profiler, loop_feature)
                return

            # 2. Check if state already indicates termination (e.g., from test fixtures)
            # This replaces graph.ainvoke() which would return state as-is.
            if state.get("transition") == Transition.DONE:
                scratch.transition = Transition.DONE
                logger.debug("graph_done pending_approval=0 tool_calls=0 approved=0 transition=done")
                action = await self._handle_transition(scratch, auditor)
                if action not in ("continue",):
                    _finalize_iteration(profiler, loop_feature)
                    return
                _finalize_iteration(profiler, loop_feature)
                continue

            # 3. Run think -> route -> review -> act -> observe cycle
            # This replaces graph.ainvoke() with direct node calls
            # Yield event loop so side-channel streaming can propagate
            for _ in range(25):
                # STEP: Think
                think_out = await self._think(state, ctx)
                if think_out.assistant_message:
                    state.setdefault("messages", []).append(think_out.assistant_message)
                state["tool_calls"] = think_out.tool_calls
                state["streaming_tool_results"] = think_out.pre_executed
                state["stream_chunks"] = think_out.stream_chunks
                state["llm_error"] = think_out.llm_error

                await asyncio.sleep(0)

                # Emit streaming deltas
                if think_out.stream_chunks:
                    emitter.emit_stream_chunks(think_out.stream_chunks)

                # Handle LLM error from think — break early so step 4
                # recovery handles it (same as old orchestrator flow).
                if think_out.llm_error:
                    break

                # Only set DONE if no error and no tool calls
                state["transition"] = Transition.DONE if think_out.is_done else None

                route = route_after_think(state)
                if route == "__end__":
                    state["transition"] = Transition.DONE
                    break

                # STEP 2: Review (only if there are tool_calls needing review)
                if route == "review":
                    review_out = await self._review(state, ctx)
                    state["approved_tool_calls"] = review_out.approved
                    state["rejected_tool_calls"] = review_out.rejected
                    state["pending_approval"] = review_out.pending
                    state["transition"] = review_out.transition

                    route = route_after_review(state)
                    if route == "__end__":
                        # pending_approval — approval loop handles it below
                        break

                # STEP 3: Act (execute approved tools)
                if route == "act":
                    exec_out = await self._act(state, ctx)
                    state["tool_results"] = exec_out.results
                    state["approved_tool_calls"] = []

                # STEP 4: Observe — format results as tool messages
                obs_out = self._observe(state)
                for tm in obs_out.tool_messages:
                    state.setdefault("messages", []).append(tm)
                state["streaming_tool_results"] = []
                state["tool_results"] = []
                state["rejected_tool_calls"] = []
                state["_emitted_results"] = obs_out.emitted_results
                state["transition"] = obs_out.transition

            profiler.checkpoint("graph_ainvoke")

            # Sync state -> scratch
            scratch.tool_calls = state.get("tool_calls", [])
            scratch.pending_approval = state.get("pending_approval", [])
            scratch.approved_tool_calls = state.get("approved_tool_calls", [])
            scratch.rejected_tool_calls = state.get("rejected_tool_calls", [])
            scratch.tool_results = state.get("tool_results", [])
            scratch.streaming_tool_results = state.get("streaming_tool_results", [])
            scratch._emitted_results = state.get("_emitted_results", [])
            scratch.stream_chunks = state.get("stream_chunks", [])
            scratch.llm_error = state.get("llm_error")
            scratch.transition = state.get("transition")

            logger.debug(
                "graph_done pending_approval={p} tool_calls={t} approved={a} transition={r}",
                p=bool(scratch.pending_approval),
                t=len(scratch.tool_calls),
                a=len(scratch.approved_tool_calls),
                r=get_transition(scratch),
            )

            # 3-3.5. Approval loop — re-enter when there are pending tools
            while True:
                await self._approval.resolve(
                    scratch, profiler=profiler, turn_ctx=ctx, emitter=emitter
                )
                # Copy scratch back to state dict
                state["pending_approval"] = scratch.pending_approval
                state["approved_tool_calls"] = scratch.approved_tool_calls
                state["rejected_tool_calls"] = scratch.rejected_tool_calls
                state["transition"] = scratch.transition
                # Inject rejection messages so LLM sees rejected tools
                _inject_rejection_messages(state, scratch.rejected_tool_calls)
                transition = get_transition(scratch)
                if transition not in (
                    Transition.APPROVAL_GRANTED,
                    Transition.APPROVAL_REJECTED,
                ):
                    break

                # Emit ToolCallStarted for approved tools BEFORE execution
                emitter.emit_tools_started(scratch.approved_tool_calls)

                # Re-enter cycle to execute approved/rejected tool decisions
                for _ in range(25):
                    # Think first — think_node skips if approved_tool_calls present
                    think_out = await self._think(state, ctx)
                    if think_out.assistant_message:
                        state.setdefault("messages", []).append(
                            think_out.assistant_message
                        )
                    state["tool_calls"] = think_out.tool_calls
                    state["streaming_tool_results"] = think_out.pre_executed
                    state["stream_chunks"] = think_out.stream_chunks
                    state["llm_error"] = think_out.llm_error

                    await asyncio.sleep(0)

                    # Emit streaming deltas from resumed cycle
                    if think_out.stream_chunks:
                        emitter.emit_stream_chunks(think_out.stream_chunks)

                    # Handle LLM error in resumed cycle
                    if think_out.llm_error:
                        break

                    state["transition"] = (
                        Transition.DONE if think_out.is_done else None
                    )

                    route = route_after_think(state)
                    if route == "__end__":
                        state["transition"] = Transition.DONE
                        break

                    if route == "review":
                        review_out = await self._review(state, ctx)
                        state["approved_tool_calls"] = review_out.approved
                        state["rejected_tool_calls"] = review_out.rejected
                        state["pending_approval"] = review_out.pending
                        state["transition"] = review_out.transition
                        route = route_after_review(state)
                        if route == "__end__":
                            break

                    if route == "act":
                        exec_out = await self._act(state, ctx)
                        state["tool_results"] = exec_out.results
                        state["approved_tool_calls"] = []

                    obs_out = self._observe(state)
                    for tm in obs_out.tool_messages:
                        state.setdefault("messages", []).append(tm)
                    state["streaming_tool_results"] = []
                    state["tool_results"] = []
                    state["rejected_tool_calls"] = []
                    state["_emitted_results"] = obs_out.emitted_results
                    state["transition"] = obs_out.transition

                profiler.checkpoint("graph_resume")

                # Sync state -> scratch for resumed iteration
                scratch.tool_calls = state.get("tool_calls", [])
                scratch.pending_approval = state.get("pending_approval", [])
                scratch.approved_tool_calls = state.get("approved_tool_calls", [])
                scratch.rejected_tool_calls = state.get("rejected_tool_calls", [])
                scratch.tool_results = state.get("tool_results", [])
                scratch.streaming_tool_results = state.get("streaming_tool_results", [])
                scratch._emitted_results = state.get("_emitted_results", [])
                scratch.stream_chunks = state.get("stream_chunks", [])
                scratch.llm_error = state.get("llm_error")
                scratch.transition = state.get("transition")

                logger.debug(
                    "graph_resumed pending_approval={p} transition={r}",
                    p=bool(scratch.pending_approval),
                    r=get_transition(scratch),
                )

                # Emit ToolCallFinished for executed tools
                result_sources = (
                    scratch._emitted_results or scratch.streaming_tool_results
                )
                emitter.emit_tools_finished(result_sources)
                # Prevent re-emission
                scratch._emitted_results = []
                state["_emitted_results"] = []

                if not scratch.pending_approval:
                    break

            # 4. Error recovery
            action, error_event = await self._recover_from_error(
                state, channel, auditor
            )
            if error_event:
                await channel.send(error_event)
            if action == "continue":
                logger.debug("error_recovery_continue")
                _finalize_iteration(profiler, loop_feature)
                continue
            if action == "return":
                logger.debug("error_recovery_return")
                _finalize_iteration(profiler, loop_feature)
                return

            # 5. Emit SSE events via EventEmitter
            logger.debug("emit_sse msgs={m}", m=len(state.get("messages", [])))
            pending_ids = {
                tc.get("id")
                for tc in scratch.pending_approval
                if tc.get("id")
            }
            emitter.emit_tools_started(scratch.tool_calls, skip_ids=pending_ids)
            emitter.emit_tools_finished(scratch.tool_results)
            emitter.emit_streaming_tool_results(
                scratch._emitted_results or scratch.streaming_tool_results
            )
            profiler.checkpoint("emit_events")

            # 6-7. Transition routing
            action = await self._handle_transition(scratch, auditor)
            profiler.checkpoint("handle_transition")
            logger.debug("transition route={r}", r=action)
            if action not in ("continue",):
                _finalize_iteration(profiler, loop_feature)
                return
            debug_log(
                "DEBUG",
                "Transition -> CONTINUE",
                chat_id=str(state.get("_chat_id", "")),
            )
            _finalize_iteration(profiler, loop_feature)

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
        if not llm_error or not self._error_recovery:
            return None, None
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
            await auditor.transition(
                Transition.ERROR_EXIT, actor=AuditActor.SYSTEM
            )
            return "return", TurnFailed(
                code=llm_error.get("code", 500),
                message="Recovery exhausted",
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

# ── Legacy module-level function (kept for test compatibility) ──

async def handle_llm_error(state: dict, *, error_recovery, context_manager, llm, auditor) -> bool:
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

    strategy = error_recovery.get_strategy(error_type)
    if not strategy.get("recoverable"):
        return False

    action = strategy["action"]
    from src.agent.state import Transition
    from src.models.audit import AuditActor

    if action == "compress_context":
        compressed = await context_manager.compress(state.get("messages", []))
        state["messages"] = compressed
        await auditor.transition(Transition.CONTEXT_COMPACTED, actor=AuditActor.SYSTEM)
    elif action == "aggressive_compress":
        compressed = await context_manager.compress(state.get("messages", []))
        state["messages"] = compressed
        await auditor.transition(Transition.CONTEXT_COMPACTED, actor=AuditActor.SYSTEM)
    elif action == "escalate_token_limit":
        llm.escalate_max_tokens()
    elif action == "continue_inject":
        msgs = list(state.get("messages", []))
        msgs.append({"role": "user", "content": "Please continue from where you stopped."})
        state["messages"] = msgs
    elif action == "switch_fallback_model":
        llm.switch_to_fallback()

    error_recovery.record_attempt(error_type, action)
    state["llm_error"] = None
    return True
