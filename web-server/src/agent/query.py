"""Agent outer loop + inner LangGraph ReAct graph.

Outer layer: context compression, approval handling, exit detection,
audit logging, SSE streaming, session management, persistence.
Inner graph: think → review → act → observe → think → … → END.

async approval model:
  - run() yields all events on a single SSE connection.
  - Interrupts are handled inline via bridge.gather_decisions().
  - resume(decisions) kept for programmatic/test use.
"""

from __future__ import annotations

from typing import Protocol, AsyncIterator
from uuid import UUID, uuid4

from loguru import logger

from src.agent.loop.audit import audit_transition
from src.agent.loop.orchestrator import LoopOrchestrator
from src.agent.state import Transition
from src.models.audit import AuditActor


class AgentRunner(Protocol):
    """Protocol for agent execution — SSEStream depends on this, not Query."""
    async def run(self, messages: list[dict], available_tools: list,
                  system: str | None = None, *, _event_queue=None,
                  _chat_id: str | None = None) -> AsyncIterator[dict]:
        ...


class Query:
    def __init__(
        self,
        llm,
        graph,
        context_manager,
        session_manager=None,
        prompt_manager=None,
        tool_executor=None,
        pending_approvals=None,
        audit_logger=None,
        error_recovery=None,
        chat_id=None,
        lifecycle=None,
    ):
        self._llm = llm
        self._graph = graph
        self._context_manager = context_manager
        self._session_manager = session_manager
        self._prompt_manager = prompt_manager
        self._tool_executor = tool_executor
        self._bridge = pending_approvals
        self._audit = audit_logger
        self._error_recovery = error_recovery
        self._chat_id = str(chat_id) if chat_id else str(uuid4())
        self._lifecycle = lifecycle

    async def run(
        self, messages: list[dict], available_tools: list, system: str | None = None,
        *, _event_queue=None, _chat_id: str | None = None,
    ):
        """Run agent until DONE or approval needed. Yields SSE events."""
        chat_uuid = None
        try:
            chat_uuid = UUID(self._chat_id)
        except (ValueError, AttributeError):
            pass
        model = getattr(self._llm, "_config", None) and getattr(self._llm._config, "model", "") or None
        await audit_transition(
            self._audit, Transition.USER_MESSAGE,
            chat_id=chat_uuid,
            actor=AuditActor.USER,
            model=model,
        )

        state = {
            "messages": messages,
            "available_tools": available_tools,
            "system": system,
            "transition": None,
            "llm_error": None,
        }

        orch = self._build_orchestrator()
        async for event in orch.run(state, _event_queue=_event_queue, _chat_id=_chat_id):
            yield event

    async def resume(self, decisions: list[str]):
        """Resume after human approval (programmatic/test path).

        In the normal user-facing flow the orchestrator handles approval
        inline in run() via the bridge. This method is kept for backward
        compatibility with tests.
        """
        orch = self._build_orchestrator()
        async for event in orch.run({"messages": [], "decisions": decisions}):
            yield event

    def _build_orchestrator(self) -> LoopOrchestrator:
        return LoopOrchestrator(
            graph=self._graph,
            context_manager=self._context_manager,
            bridge=self._bridge,
            audit_logger=self._audit,
            error_recovery=self._error_recovery,
            llm=self._llm,
            chat_id=self._chat_id,
            lifecycle=self._lifecycle,
        )
