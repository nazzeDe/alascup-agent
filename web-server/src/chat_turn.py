"""ChatTurn — owns full turn lifecycle: session, persistence, EventChannel, orchestrator."""

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID, uuid4

from loguru import logger

from src.agent.events import (
    DomainEvent,
    EventChannel,
    TurnFailed,
    TurnStarted,
)
from src.agent.state import AgentState
from src.agent.shared import is_disconnected
from src.models.message import Message, MessageType
from src.observability.debug_log import log as debug_log
from src.observability import trace_points as tp
from src.services.history_projection import build_llm_history


class ChatTurn:
    """Owns the full chat turn lifecycle.

    Creates/fetches session, builds history, sets title, persists user message,
    launches orchestrator, reads domain events from channel, accumulates
    assistant deltas, persists assistant messages on AssistantDone boundaries.
    """

    def __init__(
        self,
        *,
        user_message: str,
        chat_id: str | None,
        session_manager,
        prompt_manager,
        orchestrator_builder,
        tool_executor,
        disconnect_check=None,
    ):
        self._user_message = user_message
        self._chat_id = chat_id
        self._session_manager = session_manager
        self._prompt_manager = prompt_manager
        self._orchestrator_builder = orchestrator_builder
        self._tool_executor = tool_executor
        self._disconnect_check = disconnect_check

    async def _is_disconnected(self) -> bool:
        """Call the disconnect check, supporting both sync and async callables."""
        return await is_disconnected(self._disconnect_check)

    async def events(self) -> AsyncIterator[DomainEvent]:
        """Read domain events from the channel.

        First event is always TurnStarted.
        """
        session = await self._load_session()
        chat_id_uuid = session.id

        yield TurnStarted(chat_id=chat_id_uuid)
        if await self._is_disconnected():
            return

        state = await self._build_initial_state(session)
        channel = EventChannel()
        orchestrator = self._orchestrator_builder()
        orchestrator._chat_id = str(chat_id_uuid)
        orch_task: asyncio.Task | None = asyncio.create_task(
            orchestrator.run(state, channel=channel)
        )

        try:
            while True:
                event = await self._receive_event(channel, orch_task)
                if event == "continue":
                    continue
                if event == "drain":
                    for drained in self._drain_channel(channel, orch_task):
                        yield drained
                    break

                if event is None:
                    break

                cid = getattr(event, 'call_id', 'N/A')
                debug_log("DEBUG", tp.CHAT_TURN_YIELD, type=type(event).__name__, call_id=cid)
                yield event

                if await self._is_disconnected():
                    break

        except Exception as exc:
            logger.opt(exception=True).error(
                "chat_turn_crash chat_id={c}", c=str(chat_id_uuid)
            )
            yield TurnFailed(code="SSE_CRASH", message=str(exc))
        finally:
            await self._cancel_task(orch_task)
            channel.close()

        # After disconnect, we don't yield done — SSEStream does that in finally

    async def _load_session(self):
        if self._chat_id is None:
            return await self._session_manager.create_session()
        return await self._session_manager.get_session(UUID(self._chat_id))

    async def _build_initial_state(self, session) -> AgentState:
        history: list[dict] = build_llm_history(session)
        if not session.title and self._user_message:
            title = self._user_message.split("\n")[0][:20]
            await self._session_manager.set_title(session.id, title)

        user_msg = Message(
            message_id=uuid4(),
            chat_id=session.id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content=self._user_message,
        )
        await self._session_manager.add_message(session.id, user_msg)

        return AgentState(
            messages=history + [{"role": "user", "content": self._user_message}],
            available_tools=self._tool_executor.list_tools(),
            system=self._prompt_manager.build_system_prompt(),
        )

    async def _receive_event(self, channel: EventChannel, orch_task: asyncio.Task):
        try:
            event = await asyncio.wait_for(channel.receive(), timeout=0.1)
        except asyncio.TimeoutError:
            return "drain" if await self._should_drain(channel, orch_task) else "continue"
        if event is not None:
            debug_log("DEBUG", tp.CHAT_TURN_RECEIVED, type=type(event).__name__)
        return event

    async def _should_drain(self, channel: EventChannel, orch_task: asyncio.Task) -> bool:
        return (orch_task.done() and channel.is_closed()) or await self._is_disconnected()

    def _drain_channel(self, channel: EventChannel, orch_task: asyncio.Task):
        if not channel.is_closed():
            channel.close()
        debug_log("DEBUG", tp.CHAT_TURN_DRAIN_START,
                  orch_done=orch_task.done(), channel_closed=channel.is_closed())
        drained_count = 0
        for event in channel.drain_nowait():
            drained_count += 1
            yield event
        debug_log("DEBUG", tp.CHAT_TURN_DRAIN_DONE, drained_events=drained_count)

    async def _cancel_task(self, orch_task: asyncio.Task | None) -> None:
        if not orch_task or orch_task.done():
            return
        orch_task.cancel()
        try:
            await orch_task
        except asyncio.CancelledError:
            pass
