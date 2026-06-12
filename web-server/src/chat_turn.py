"""ChatTurn — owns full turn lifecycle: session, persistence, EventChannel, orchestrator."""

import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID, uuid4

from loguru import logger

from src.agent.events import (
    AssistantDelta,
    AssistantDone,
    DomainEvent,
    EventChannel,
    ToolCallFinished,
    ToolCallStarted,
    TurnFailed,
    TurnStarted,
)
from src.models.message import Message, MessageType


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
        Accumulates AssistantDelta deltas and persists on AssistantDone.
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

        # 3. Build history from session messages
        history: list[dict] = []
        for m in session.messages:
            if m.is_meta:
                continue
            role = m.type.value if isinstance(m.type, MessageType) else m.type
            history.append({"role": role, "content": m.content})

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

        # 10. Read from channel, accumulate assistant deltas, persist on AssistantDone
        collected_text: list[str] = []
        disconnected = False

        try:
            while True:
                # Check for task completion while waiting
                event: DomainEvent | None = None
                try:
                    # Wait for next event with timeout to check disconnect
                    event = await asyncio.wait_for(channel.receive(), timeout=0.1)
                    if event is not None:
                        logger.debug("ChatTurn received: type={}", type(event).__name__)
                except asyncio.TimeoutError:
                    if (orch_task.done() and channel.is_closed()) or await self._is_disconnected():
                        if await self._is_disconnected():
                            disconnected = True
                        # Drain any remaining events
                        if not channel.is_closed():
                            channel.close()
                        # After close(), drain_nowait() deterministically
                        # drains all queued events without timeout risk.
                        logger.debug("ChatTurn drain start: orch_done={} channel_closed={}",
                                     orch_task.done(), channel.is_closed())
                        drained_count = 0
                        for ev in channel.drain_nowait():
                            drained_count += 1
                            if isinstance(ev, AssistantDelta):
                                collected_text.append(ev.delta)
                            yield ev
                        logger.debug("ChatTurn drain done: drained_events={}", drained_count)
                        break
                    continue

                if event is None:
                    # Channel closed
                    break

                # Accumulate assistant deltas
                if isinstance(event, AssistantDelta):
                    collected_text.append(event.delta)
                elif isinstance(event, AssistantDone):
                    # Persist accumulated assistant text
                    full_text = "".join(collected_text)
                    collected_text.clear()
                    if full_text:
                        assistant_msg = Message(
                            message_id=uuid4(),
                            chat_id=chat_id_uuid,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            type=MessageType.ASSISTANT,
                            content=full_text,
                        )
                        await self._session_manager.add_message(chat_id_uuid, assistant_msg)

                cid = getattr(event, 'call_id', 'N/A')
                logger.debug("ChatTurn yielding: type={} call_id={}", type(event).__name__, cid)

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
