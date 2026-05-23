"""Agent outer loop + inner LangGraph ReAct graph.

Outer layer: context compression, approval pause/resume, exit detection,
audit logging, SSE streaming.
Inner graph: think → review → act → observe → think → … → END.

Async approval model:
  - run() executes until interrupt, then returns immediately.
  - resume(decisions) restores from persisted checkpoint and continues.
"""

from __future__ import annotations

from uuid import uuid4

from src.agent.loop.audit import log_transition
from src.agent.loop.orchestrator import LoopOrchestrator
from src.agent.state import Transition


class Query:
    def __init__(
        self,
        llm,
        graph,
        context_manager,
        pending_approvals=None,
        audit_logger=None,
        error_recovery=None,
        chat_id=None,
    ):
        self._llm = llm
        self._graph = graph
        self._context_manager = context_manager
        self._bridge = pending_approvals
        self._audit = audit_logger
        self._error_recovery = error_recovery
        self._chat_id = str(chat_id) if chat_id else str(uuid4())

    async def run(
        self, messages: list[dict], available_tools: list, system: str | None = None
    ):
        """Run agent until interrupt or DONE. Yields SSE events."""
        await log_transition(self._audit, Transition.USER_MESSAGE)

        state = {
            "messages": messages,
            "available_tools": available_tools,
            "system": system,
            "transition": None,
            "llm_error": None,
        }

        orch = self._build_orchestrator()
        async for event in orch.run(state):
            yield event

    async def resume(self, decisions: list[str]):
        """Resume from checkpoint after human approval.

        Delegates to LoopOrchestrator.resume() which handles the full
        resume cycle: ainvoke → interrupt detection → event emission → done.
        """
        orch = self._build_orchestrator()
        async for event in orch.resume(decisions):
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
        )
