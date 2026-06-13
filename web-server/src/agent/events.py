"""Typed domain events for the agent loop. Serialization happens once at the SSEStream boundary."""

import asyncio
from dataclasses import dataclass
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
    params: dict
    is_read_only: bool
    server: str


@dataclass(frozen=True)
class ToolCallFinished:
    call_id: str
    execution_status: str
    output: str | None = None
    error: dict | None = None
    execution_time_ms: int | None = None


@dataclass(frozen=True)
class ApprovalRequired:
    chat_id: str
    request_id: str
    tool_name: str
    params: dict
    reason: str
    call_id: str = ""


@dataclass(frozen=True)
class TurnFailed:
    code: str | int
    message: str


# Union type
DomainEvent = TurnStarted | ReasoningDelta | ThinkingDone | AssistantDelta | AssistantDone | ToolCallStarted | ToolCallFinished | ApprovalRequired | TurnFailed


class EventChannel:
    """Thin wrapper over asyncio.Queue[DomainEvent | None] with close() sentinel."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._instance_id = id(self)

    async def send(self, event: DomainEvent) -> None:
        if not self._closed:
            await self._queue.put(event)

    def send_nowait(self, event: DomainEvent) -> None:
        """Non-async send for use in sync contexts (e.g., node return path)."""
        if not self._closed:
            self._queue.put_nowait(event)
            debug_log("DEBUG", tp.EVENT_SEND,
                      instance_id=self._instance_id, event=type(event).__name__,
                      closed=self._closed, qsize=self._queue.qsize())
            if isinstance(event, (ToolCallStarted, ToolCallFinished)):
                debug_log("DEBUG", tp.EVENT_SEND,
                          instance_id=self._instance_id, event=type(event).__name__,
                          call_id=event.call_id,
                          execution_status=getattr(event, 'execution_status', 'N/A'))
        else:
            debug_log("DEBUG", tp.EVENT_SEND_DROPPED,
                      instance_id=self._instance_id, event=type(event).__name__)

    async def receive(self) -> DomainEvent | None:
        """Receive next event. Returns None only after channel is closed AND queue drained."""
        if self._closed:
            # Try non-blocking drain first
            try:
                event = self._queue.get_nowait()
                debug_log("DEBUG", tp.EVENT_RECEIVE,
                          instance_id=self._instance_id,
                          event=type(event).__name__ if event else 'None',
                          qsize=self._queue.qsize(),
                          mode="drain")
                return event
            except asyncio.QueueEmpty:
                return None
        try:
            event = await self._queue.get()
            debug_log("DEBUG", tp.EVENT_RECEIVE,
                      instance_id=self._instance_id,
                      event=type(event).__name__ if event else 'None',
                      qsize=self._queue.qsize(),
                      mode="await")
            return event
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("EventChannel.receive ERROR id={} exc={}", self._instance_id, exc)
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
        debug_log("DEBUG", tp.EVENT_CLOSE,
                  instance_id=self._instance_id, was_closed=self._closed, qsize=self._queue.qsize())
        self._closed = True
        # Put sentinel to unblock any waiting receive
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    def is_closed(self) -> bool:
        return self._closed
