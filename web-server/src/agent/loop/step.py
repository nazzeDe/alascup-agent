"""Agent step execution: think -> review -> act -> observe."""

import asyncio

from src.agent.nodes.observe import route_after_review, route_after_think
from src.agent.state import AgentState, Transition
from src.observability.debug_log import log as debug_log


class AgentStep:
    """Run one bounded think/review/act/observe pass against state."""

    def __init__(self, *, think, review, act, observe, lifecycle=None):
        self._think = think
        self._review = review
        self._act = act
        self._observe = observe
        self._lifecycle = lifecycle

    async def run(self, state: AgentState, ctx, emitter, *, phase: str = "main") -> None:
        state.emitted_results = []
        for _ in range(25):
            think_out = await self._think(state, ctx)
            self._apply_think_output(state, think_out)

            await asyncio.sleep(0)

            if think_out.stream_chunks:
                emitter.emit_stream_chunks(think_out.stream_chunks)

            if think_out.llm_error:
                break

            route = self._route_after_think(state, think_out, phase)
            if route == "__end__":
                state.transition = Transition.DONE
                break

            if route == "review":
                route = await self._run_review(state, ctx, phase)
                if route == "__end__":
                    break

            if route == "act":
                await self._run_act(state, ctx, emitter, phase)
            elif route == "observe":
                debug_log(
                    "DEBUG",
                    f"Skip act_node ({phase})",
                    chat_id=str(state.chat_id),
                    streaming_results_count=len(state.streaming_tool_results),
                )
            elif route not in ("__end__",):
                debug_log(
                    "WARN",
                    "Unexpected route after think",
                    chat_id=str(state.chat_id),
                    route=route,
                )

            await self._run_observe(state, ctx)

    def _apply_think_output(self, state: AgentState, think_out) -> None:
        if think_out.assistant_message:
            state.messages.append(think_out.assistant_message)
        state.tool_calls = think_out.tool_calls
        state.streaming_tool_results = think_out.pre_executed
        state.stream_chunks = think_out.stream_chunks
        state.llm_error = think_out.llm_error

    def _route_after_think(self, state: AgentState, think_out, phase: str) -> str:
        state.transition = Transition.DONE if think_out.is_done else None
        route = route_after_think(state)
        debug_log(
            "DEBUG",
            f"Route after think ({phase})",
            chat_id=str(state.chat_id),
            route=route,
            tool_calls_count=len(state.tool_calls),
            streaming_results_count=len(state.streaming_tool_results),
            approved_count=len(state.approved_tool_calls),
            llm_error=bool(state.llm_error),
            is_done=think_out.is_done,
        )
        return route

    async def _run_review(self, state: AgentState, ctx, phase: str) -> str:
        review_out = await self._review(state, ctx)
        state.approved_tool_calls = review_out.approved
        state.rejected_tool_calls = review_out.rejected
        state.pending_approval = review_out.pending
        state.transition = review_out.transition
        route = route_after_review(state)
        debug_log(
            "DEBUG",
            f"Route after review ({phase})",
            chat_id=str(state.chat_id),
            route=route,
            approved_count=len(state.approved_tool_calls),
            pending_count=len(state.pending_approval),
            rejected_count=len(state.rejected_tool_calls),
        )
        return route

    async def _run_act(self, state: AgentState, ctx, emitter, phase: str) -> None:
        approved = state.approved_tool_calls
        debug_log(
            "DEBUG",
            f"Enter act_node ({phase})",
            chat_id=str(state.chat_id),
            approved_count=len(approved),
            approved_ids=[tc.id for tc in approved],
        )
        emitter.emit_tools_started(approved)
        exec_out = await self._act(state, ctx)
        state.tool_results = exec_out.results
        state.approved_tool_calls = []

    async def _run_observe(self, state: AgentState, ctx) -> None:
        obs_out = self._observe(state)
        for tm in obs_out.tool_messages:
            state.messages.append(tm)
        if self._lifecycle and obs_out.tool_messages:
            await self._lifecycle.persist_tool_result(ctx.chat_id, obs_out.tool_messages)
        state.streaming_tool_results = []
        state.tool_results = []
        state.rejected_tool_calls = []
        state.emitted_results.extend(obs_out.emitted_results)
        state.transition = obs_out.transition


def sync_scratch_from_state(scratch, state: AgentState) -> None:
    scratch.tool_calls = state.tool_calls
    scratch.pending_approval = state.pending_approval
    scratch.approved_tool_calls = state.approved_tool_calls
    scratch.rejected_tool_calls = state.rejected_tool_calls
    scratch.tool_results = state.tool_results
    scratch.streaming_tool_results = state.streaming_tool_results
    scratch.emitted_results = state.emitted_results
    scratch.stream_chunks = state.stream_chunks
    scratch.llm_error = state.llm_error
    scratch.transition = state.transition
