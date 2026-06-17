"""Agent loop orchestrator — composes AgentLoop invocation, event emission, and handlers."""

from functools import partial
from loguru import logger

from src.agent.events import EventChannel, TurnFailed
from src.agent.loop.approval import ApprovalHandler
from src.agent.loop.circuit_breaker import CircuitBreaker
from src.agent.loop.runner import AgentLoop
from src.agent.nodes import act_node, observe_node, review_node, think_node
from src.agent.turn_context import TurnContext, Auditor


class LoopOrchestrator:
    """Wraps AgentLoop with lifecycle management (bridge, audit, emitter).

    The orchestrator owns the SSE connection lifecycle. AgentLoop owns the
    inner think-review-act-observe cycle — pure Python while-loop, no LangGraph.
    """

    def __init__(
        self,
        *,
        graph=None,  # deprecated, kept for backward compat in tests; ignored
        context_manager,
        bridge,
        audit_logger,
        error_recovery,
        llm,
        chat_id: str,
        lifecycle=None,
        agent_max_iterations: int = 30,
        agent_token_ceiling_ratio: float = 0.95,
        tool_executor=None,
        rule_engine=None,
        # Injectables for tests — default to real nodes, tests inject mocks
        think_fn=None,
        review_fn=None,
        act_fn=None,
        observe_fn=None,
    ):
        self._context_manager = context_manager
        self._bridge = bridge
        self._audit = audit_logger
        self._error_recovery = error_recovery
        self._llm = llm
        self._model: str = (
            getattr(llm, "_config", None)
            and getattr(llm._config, "model", "")
            or ""
        )
        self._chat_id = chat_id
        self._lifecycle = lifecycle
        self._tool_executor = tool_executor
        self._rule_engine = rule_engine
        self._think_fn = think_fn
        self._review_fn = review_fn
        self._act_fn = act_fn
        self._observe_fn = observe_fn
        self._approval = ApprovalHandler(
            bridge=bridge, audit_logger=audit_logger, lifecycle=lifecycle
        )
        ws = getattr(self._context_manager, "window_size", 128000)
        self._breaker = CircuitBreaker(
            max_iterations=agent_max_iterations,
            token_ceiling=int(ws * agent_token_ceiling_ratio),
        )

    async def run(self, initial_state: dict, *, channel: EventChannel) -> None:
        """Execute ReAct loop, sending DomainEvents to channel. channel.close() in finally."""

        state = dict(initial_state)

        # Store metadata in state for AgentLoop to pick up
        state["_chat_id"] = self._chat_id
        state["_model"] = self._model

        # Build AgentLoop with bound node functions (or use injectables from tests)
        thinker = self._think_fn or partial(
            think_node, llm=self._llm, executor=self._tool_executor, lifecycle=self._lifecycle
        )
        reviewer = self._review_fn or partial(
            review_node,
            executor=self._tool_executor,
            rule_engine=self._rule_engine,
            audit_logger=self._audit,
            lifecycle=self._lifecycle,
        )
        actor = self._act_fn or partial(
            act_node,
            executor=self._tool_executor,
            audit_logger=self._audit,
            lifecycle=self._lifecycle,
        )
        observer = self._observe_fn or observe_node

        def auditor_factory(ctx: TurnContext) -> Auditor:
            return Auditor(audit_logger=self._audit, ctx=ctx)

        loop = AgentLoop(
            think_fn=thinker,
            review_fn=reviewer,
            act_fn=actor,
            observe_fn=observer,
            approval=self._approval,
            breaker=self._breaker,
            auditor_factory=auditor_factory,
            emitter=None,  # AgentLoop creates its own emitter
            context_manager=self._context_manager,
            error_recovery=self._error_recovery,
            llm=self._llm,
            lifecycle=self._lifecycle,
        )

        try:
            await loop.run(state, channel)
        except Exception as exc:
            logger.opt(exception=True).error(
                "agent_run_failed chat_id={c}", c=str(self._chat_id)
            )
            await channel.send(
                TurnFailed(code="AGENT_CRASH", message=str(exc))
            )
        finally:
            channel.close()

    async def _check_token_ceiling(self, state: dict) -> bool:
        """Public accessor for tests — delegates to circuit breaker check."""
        messages = state.get("messages", [])
        tokens = self._context_manager.count_tokens(messages)
        return self._breaker.check_token_ceiling(tokens)
