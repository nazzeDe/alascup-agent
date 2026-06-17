"""ChatTurn — owns full turn lifecycle: session, persistence, EventChannel, orchestrator."""

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID, uuid4

from loguru import logger

from src.agent.events import (
    DomainEvent,
    EventChannel,
    ToolCallFinished,
    ToolCallStarted,
    TurnFailed,
    TurnStarted,
)
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
        if not self._disconnect_check:
            return False
        result = self._disconnect_check()
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)

    async def events(self) -> AsyncIterator[DomainEvent]:
        """Read domain events from the channel.

        First event is always TurnStarted.
        """
        # 1. Session management
        if self._chat_id is None:
            session = await self._session_manager.create_session()
        else:
            session = await self._session_manager.get_session(UUID(self._chat_id))

        chat_id_uuid = session.id

        # 2. Yield TurnStarted
        yield TurnStarted(chat_id=chat_id_uuid)
        if await self._is_disconnected():
            return

        # 3. Build history from session messages + executed_tool_list
        history: list[dict] = build_llm_history(session)

        # 4. Set title
        if not session.title and self._user_message:
            title = self._user_message.split("\n")[0][:20]
            await self._session_manager.set_title(chat_id_uuid, title)

        # 5. Persist user message
        user_msg = Message(
            message_id=uuid4(),
            chat_id=chat_id_uuid,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content=self._user_message,
        )
        await self._session_manager.add_message(chat_id_uuid, user_msg)

        # 6. Build system prompt
        system_prompt = self._prompt_manager.build_system_prompt()

        # 7. Build available tools
        available_tools = self._tool_executor.list_tools()

        # 8. Create channel and orchestrator
        channel = EventChannel()
        orchestrator = self._orchestrator_builder()
        orchestrator._chat_id = str(chat_id_uuid)

        # Build initial state
        messages = history + [{"role": "user", "content": self._user_message}]
        state = {
            "messages": messages,
            "available_tools": available_tools,
            "system": system_prompt,
            "transition": None,
            "llm_error": None,
        }

        # 9. Launch orchestrator as a task
        orch_task: asyncio.Task | None = asyncio.create_task(
            orchestrator.run(state, channel=channel)
        )

        # 10. Read from channel, yield events to SSE stream.
        # Assistant message persistence is handled by think_node → lifecycle.persist_assistant_message().
        disconnected = False

        try:
            while True:
                # Check for task completion while waiting
                event: DomainEvent | None = None
                try:
                    # Wait for next event with timeout to check disconnect
                    event = await asyncio.wait_for(channel.receive(), timeout=0.1)
                    if event is not None:
                        debug_log("DEBUG", tp.CHAT_TURN_RECEIVED, type=type(event).__name__)
                except asyncio.TimeoutError:
                    if (orch_task.done() and channel.is_closed()) or await self._is_disconnected():
                        if await self._is_disconnected():
                            disconnected = True
                        # Drain any remaining events
                        if not channel.is_closed():
                            channel.close()
                        # After close(), drain_nowait() deterministically
                        # drains all queued events without timeout risk.
                        debug_log("DEBUG", tp.CHAT_TURN_DRAIN_START,
                                  orch_done=orch_task.done(), channel_closed=channel.is_closed())
                        drained_count = 0
                        for ev in channel.drain_nowait():
                            drained_count += 1
                            yield ev
                        debug_log("DEBUG", tp.CHAT_TURN_DRAIN_DONE, drained_events=drained_count)
                        break
                    continue

                if event is None:
                    # Channel closed
                    break

                # AssistantDelta events are yielded directly to SSE stream.
                # Persistence is handled by think_node → lifecycle.persist_assistant_message()

                cid = getattr(event, 'call_id', 'N/A')
                debug_log("DEBUG", tp.CHAT_TURN_YIELD, type=type(event).__name__, call_id=cid)

                yield event

                if await self._is_disconnected():
                    disconnected = True
                    break

        except Exception as exc:
            logger.opt(exception=True).error(
                "chat_turn_crash chat_id={c}", c=str(chat_id_uuid)
            )
            yield TurnFailed(code="SSE_CRASH", message=str(exc))
        finally:
            # Cancel orchestrator task
            if orch_task and not orch_task.done():
                orch_task.cancel()
                try:
                    await orch_task
                except asyncio.CancelledError:
                    pass
            channel.close()

        # After disconnect, we don't yield done — SSEStream does that in finally
