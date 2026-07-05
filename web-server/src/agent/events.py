"""Typed domain events for the agent loop. Serialization happens once at the SSEStream boundary."""

import asyncio
from collections.abc import Awaitable, Callable, AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from loguru import logger

from src.observability.debug_log import log as debug_log
from src.observability import trace_points as tp


@dataclass(frozen=True)
class TurnStarted:
    chat_id: UUID


@dataclass(frozen=True)
class ReasoningDelta:
    delta: str


@dataclass(frozen=True)
class ThinkingDone:
    pass


@dataclass(frozen=True)
class AssistantDelta:
    delta: str
    # No message_id — message boundary carried by AssistantDone


@dataclass(frozen=True)
class AssistantDone:
    pass


@dataclass(frozen=True)
class ToolCallStarted:
    call_id: str
    tool_name: str
    params: dict[str, Any]
    is_read_only: bool
    server: str


@dataclass(frozen=True)
class ToolCallFinished:
    call_id: str
    execution_status: str
    output: str | None = None
    error: dict[str, Any] | None = None
    execution_time_ms: int | None = None


@dataclass(frozen=True)
class ApprovalRequired:
    chat_id: str
    request_id: str
    tool_name: str
    params: dict[str, Any]
    reason: str
    call_id: str = ""


@dataclass(frozen=True)
class TurnFailed:
    code: str | int
    message: str


# Union type
DomainEvent = (
    TurnStarted
    | ReasoningDelta
    | ThinkingDone
    | AssistantDelta
    | AssistantDone
    | ToolCallStarted
    | ToolCallFinished
    | ApprovalRequired
    | TurnFailed
)


class EventChannel:
    """Thin wrapper over asyncio.Queue[DomainEvent | None] with close() sentinel."""

    def __init__(self):
        self._queue: asyncio.Queue[DomainEvent | None] = asyncio.Queue()
        self._closed = False
        self._instance_id = id(self)

    async def send(self, event: DomainEvent) -> None:
        if not self._closed:
            await self._queue.put(event)

    def send_nowait(self, event: DomainEvent) -> None:
        """Non-async send for use in sync contexts (e.g., node return path)."""
        if not self._closed:
            self._queue.put_nowait(event)
            debug_log(
                "DEBUG",
                tp.EVENT_SEND,
                instance_id=self._instance_id,
                event=type(event).__name__,
                closed=self._closed,
                qsize=self._queue.qsize(),
            )
            if isinstance(event, (ToolCallStarted, ToolCallFinished)):
                debug_log(
                    "DEBUG",
                    tp.EVENT_SEND,
                    instance_id=self._instance_id,
                    event=type(event).__name__,
                    call_id=event.call_id,
                    execution_status=getattr(event, "execution_status", "N/A"),
                )
        else:
            debug_log(
                "DEBUG",
                tp.EVENT_SEND_DROPPED,
                instance_id=self._instance_id,
                event=type(event).__name__,
            )

    async def receive(self) -> DomainEvent | None:
        """Receive next event. Returns None only after channel is closed AND queue drained."""
        if self._closed:
            # Try non-blocking drain first
            try:
                event = self._queue.get_nowait()
                debug_log(
                    "DEBUG",
                    tp.EVENT_RECEIVE,
                    instance_id=self._instance_id,
                    event=type(event).__name__ if event else "None",
                    qsize=self._queue.qsize(),
                    mode="drain",
                )
                return event
            except asyncio.QueueEmpty:
                return None
        try:
            event = await self._queue.get()
            debug_log(
                "DEBUG",
                tp.EVENT_RECEIVE,
                instance_id=self._instance_id,
                event=type(event).__name__ if event else "None",
                qsize=self._queue.qsize(),
                mode="await",
            )
            return event
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "EventChannel.receive ERROR id={} exc={}", self._instance_id, exc
            )
            return None

    def drain_nowait(self):
        """Yield all remaining events without blocking. For use after close().

        After close(), no new events can be added (send_nowait is a no-op),
        so a simple get_nowait() loop drains everything deterministically.
        """
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event is None:
                # Sentinel reached — stop
                break
            yield event

    def close(self) -> None:
        """Close the channel. Subsequent sends are no-ops, receive returns None."""
        debug_log(
            "DEBUG",
            tp.EVENT_CLOSE,
            instance_id=self._instance_id,
            was_closed=self._closed,
            qsize=self._queue.qsize(),
        )
        self._closed = True
        # Put sentinel to unblock any waiting receive
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            logger.warning("EventChannel.close queue full id={}", self._instance_id)

    def is_closed(self) -> bool:
        return self._closed


EventProducer = Callable[[EventChannel], Awaitable[None]]
DisconnectCheck = Callable[[], Awaitable[bool]]


class EventFlow:
    """Owns producer task, channel close/drain, and DomainEvent output."""

    def __init__(
        self,
        producer: EventProducer,
        *,
        disconnect_check: DisconnectCheck | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        self._producer = producer
        self._disconnect_check = disconnect_check
        self._poll_interval = poll_interval

    async def events(self) -> AsyncIterator[DomainEvent]:
        channel = EventChannel()
        task = asyncio.create_task(self._run_producer(channel))
        observe_finished_producer = False
        try:
            while True:
                event = await self._receive_event(channel, task)
                if event == "continue":
                    continue
                if event == "drain":
                    for drained in self._drain_channel(channel, task):
                        yield drained
                    observe_finished_producer = True
                    break
                if event is None:
                    observe_finished_producer = True
                    break
                yield event
                if await self._is_disconnected():
                    break
        finally:
            if observe_finished_producer:
                await self._finish_task(task)
            else:
                await self._cancel_task(task)
            channel.close()

    async def _run_producer(self, channel: EventChannel) -> None:
        try:
            await self._producer(channel)
        finally:
            channel.close()

    async def _receive_event(self, channel: EventChannel, task: asyncio.Task):
        try:
            return await asyncio.wait_for(
                channel.receive(), timeout=self._poll_interval
            )
        except asyncio.TimeoutError:
            return "drain" if await self._should_drain(channel, task) else "continue"

    async def _should_drain(self, channel: EventChannel, task: asyncio.Task) -> bool:
        return (task.done() and channel.is_closed()) or await self._is_disconnected()

    def _drain_channel(self, channel: EventChannel, task: asyncio.Task):
        if not channel.is_closed():
            channel.close()
        debug_log(
            "DEBUG",
            tp.CHAT_TURN_DRAIN_START,
            orch_done=task.done(),
            channel_closed=channel.is_closed(),
        )
        drained_count = 0
        for event in channel.drain_nowait():
            drained_count += 1
            yield event
        debug_log("DEBUG", tp.CHAT_TURN_DRAIN_DONE, drained_events=drained_count)

    async def _is_disconnected(self) -> bool:
        if self._disconnect_check is None:
            return False
        return await self._disconnect_check()

    async def _cancel_task(self, task: asyncio.Task | None) -> None:
        if not task:
            return
        if task.done():
            try:
                task.exception()
            except asyncio.CancelledError:
                pass
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _finish_task(self, task: asyncio.Task | None) -> None:
        if not task:
            return
        if not task.done():
            await self._cancel_task(task)
            return
        try:
            await task
        except asyncio.CancelledError:
            pass
