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

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from loguru import logger

from src.agent.loop.audit import audit_transition
from src.agent.loop.orchestrator import LoopOrchestrator
from src.agent.nodes import _event_queue, _chat_id_ctx
from src.agent.state import Transition
from src.models.audit import AuditActor
from src.models.message import Message, MessageType
from src.observability.timing import start_feature, complete_feature, summarize_feature_durations


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

    async def chat(self, user_message: str, *, chat_id: str | None = None):
        """Run a full chat turn: session management, persistence, agent execution, streaming.

        Yields SSE-compatible dicts with ``event`` and ``data`` keys.

        Requires session_manager, prompt_manager, and tool_executor to be set in __init__.
        """
        if not self._session_manager:
            raise RuntimeError("Query.chat() requires session_manager")
        if not self._prompt_manager:
            raise RuntimeError("Query.chat() requires prompt_manager")
        if not self._tool_executor:
            raise RuntimeError("Query.chat() requires tool_executor")
        # ── 1. Session management ──────────────────────────────────────
        if chat_id is None:
            new_session = await self._session_manager.create_session()
            chat_id_uuid = new_session.id
        else:
            chat_id_uuid = UUID(chat_id)

        session = await self._session_manager.get_session(chat_id_uuid)

        history: list[dict] = []
        for m in session.messages:
            if m.is_meta:
                continue
            role = m.type.value if isinstance(m.type, MessageType) else m.type
            history.append({"role": role, "content": m.content})

        if not session.title and user_message:
            title = user_message.split("\n")[0][:20]
            await self._session_manager.set_title(chat_id_uuid, title)

        # ── 2. Persist user message ────────────────────────────────────
        user_msg = Message(
            message_id=uuid4(),
            chat_id=chat_id_uuid,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content=user_message,
        )
        await self._session_manager.add_message(chat_id_uuid, user_msg)

        # ── 3. Build system prompt ─────────────────────────────────────
        system_prompt = self._prompt_manager.build_system_prompt()

        # Update chat_id so orchestrator audit logging sees the real UUID.
        self._chat_id = str(chat_id_uuid)

        # ── 4. ContextVar setup ────────────────────────────────────────
        queue: asyncio.Queue = asyncio.Queue()
        token = _event_queue.set(queue)
        chat_id_token = _chat_id_ctx.set(str(chat_id_uuid))
        # session_manager is now passed explicitly via lifecycle / partial — no ContextVar needed

        # ── 5. Streaming + Agent execution ─────────────────────────────
        feature = f"chat_turn:{str(chat_id_uuid)}"
        start_feature(feature)

        collected_text: list[str] = []
        available_tools = self._tool_executor.list_tools()
        messages = history + [{"role": "user", "content": user_message}]

        event_queue: asyncio.Queue = asyncio.Queue(maxsize=128)

        async def drain_queue():
            """Forward reasoning and assistant streaming events in real-time.

            Runs until cancelled by the outer finally block.  Does NOT break
            on thinking_done — the orchestrator may invoke the graph multiple
            times (e.g. after tool approval), producing multiple think cycles.

            Normalizes events to always carry a ``data`` field (SSE convention).
            """
            while True:
                item = await queue.get()
                if "data" not in item:
                    item["data"] = "{}"
                await event_queue.put(("item", item))

        async def run_agent():
            """Drive the agent iterator and forward events."""
            try:
                async for event in self.run(messages, available_tools, system=system_prompt):
                    await event_queue.put(("agent", event))
            except Exception as exc:
                logger.opt(exception=True).error("agent_run_failed chat_id={c}", c=str(chat_id_uuid))
                error_event = {
                    "event": "error",
                    "data": json.dumps({"code": "AGENT_CRASH", "message": str(exc)}, default=str),
                }
                await event_queue.put(("agent", error_event))
                await event_queue.put(("agent", {"event": "done", "data": "{}"}))
            finally:
                await event_queue.put(("done", None))

        drain_task = asyncio.create_task(drain_queue())
        agent_task = asyncio.create_task(run_agent())
        agent_done = False

        try:
            while not agent_done:
                try:
                    tag, event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if tag == "done":
                    agent_done = True
                    continue

                if event.get("event") == "assistant":
                    try:
                        data = json.loads(event["data"])
                        if data.get("message_id"):
                            collected_text.append(data.get("delta", ""))
                    except (json.JSONDecodeError, KeyError):
                        pass

                yield event

            # Drain remaining items after agent completes.
            while not event_queue.empty():
                tag, event = event_queue.get_nowait()
                if event:
                    yield event

        except Exception as exc:
            logger.opt(exception=True).error("sse_stream_crash chat_id={c}", c=str(chat_id_uuid))
            yield {
                "event": "error",
                "data": json.dumps({"code": "SSE_CRASH", "message": str(exc)}, default=str),
            }
        finally:
            # ── 6. Cleanup ─────────────────────────────────────────────
            try:
                _event_queue.reset(token)
            except ValueError:
                pass  # Token was created in a different Context (async generator GC)
            try:
                _chat_id_ctx.reset(chat_id_token)
            except ValueError:
                pass
            agent_task.cancel()
            drain_task.cancel()
            for t in (agent_task, drain_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            complete_feature(feature)
            summarize_feature_durations()

            # ── 7. Persist assistant message ───────────────────────────
            logger.debug("COLLECTED_TEXT: events={n} text_len={l}",
                         n=len(collected_text), l=sum(len(t) for t in collected_text))
            full_text = "".join(collected_text)
            if full_text:
                assistant_msg = Message(
                    message_id=uuid4(),
                    chat_id=chat_id_uuid,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    type=MessageType.ASSISTANT,
                    content=full_text,
                )
                await self._session_manager.add_message(chat_id_uuid, assistant_msg)
                logger.debug("SAVED_ASSISTANT_MSG: chat_id={c} text_len={l}", c=chat_id_uuid, l=len(full_text))

    async def run(
        self, messages: list[dict], available_tools: list, system: str | None = None
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
        async for event in orch.run(state):
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
