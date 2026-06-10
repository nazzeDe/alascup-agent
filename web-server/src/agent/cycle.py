"""ReAct cycle — think → review → act → observe, directly called.

Replaces StateGraph.ainvoke() with a simple async function that runs
the 4-node ReAct cycle with explicit inlined routing.
"""

import asyncio
from functools import partial

from src.agent.nodes import act_node, observe_node, review_node, think_node
from src.agent.nodes.observe import route_after_review, route_after_think


def make_cycle(*, llm, executor, rule_engine, audit_logger, lifecycle=None):
    """Create a bound cycle function.

    Returns an async callable ``(state: dict, config: dict | None) -> dict``
    that runs one or more think→route→node cycles (including the
    observe→think loop-back) until the route reaches END or
    pending_approval.

    The returned function has the same signature as ``CompiledStateGraph.ainvoke``
    and serves as a drop-in replacement.
    """
    _think = partial(think_node, llm=llm, executor=executor, lifecycle=lifecycle)
    _review = partial(
        review_node,
        executor=executor,
        rule_engine=rule_engine,
        audit_logger=audit_logger,
        lifecycle=lifecycle,
    )
    _act = partial(act_node, executor=executor, audit_logger=audit_logger, lifecycle=lifecycle)
    # observe_node has no bound deps
    observe = observe_node

    async def run_cycle(state: dict, config: dict | None = None) -> dict:
        """Run node cycles until END or pending_approval."""
        for _ in range(25):  # recursion-limit safety
            result = await _think(state, config)
            state.update(result)
            # Yield event loop so side-channel streaming events (drain_task
            # forwarding think_node's reasoning/thinking_done tokens) can
            # reach the SSE stream before the cycle continues.
            await asyncio.sleep(0)

            route = route_after_think(state)
            if route == "__end__":
                break

            if route == "review":
                result = await _review(state, config)
                state.update(result)
                route = route_after_review(state)
                if route == "__end__":
                    break  # pending_approval — orchestrator loop handles it

            if route == "act":
                result = await _act(state, config)
                state.update(result)

            # observe always runs after act (or after think for pre-executed tools)
            result = observe(state)
            state.update(result)
            # observe sets TOOL_RESULTS → loop back to think

        return state

    return run_cycle
