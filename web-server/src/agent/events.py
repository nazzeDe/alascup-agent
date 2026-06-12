"""Typed domain events for the agent loop. Serialization happens once at the SSEStream boundary."""

import asyncio
from dataclasses import dataclass
from uuid import UUID


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

    async def send(self, event: DomainEvent) -> None:
        if not self._closed:
            await self._queue.put(event)

    def send_nowait(self, event: DomainEvent) -> None:
        """Non-async send for use in sync contexts (e.g., node return path)."""
        if not self._closed:
            self._queue.put_nowait(event)

    async def receive(self) -> DomainEvent | None:
        """Receive next event. Returns None only after channel is closed AND queue drained."""
        if self._closed:
            # Try non-blocking drain first
            try:
                return self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await self._queue.get()
        except Exception:
            return None

    def close(self) -> None:
        """Close the channel. Subsequent sends are no-ops, receive returns None."""
        self._closed = True
        # Put sentinel to unblock any waiting receive
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    def is_closed(self) -> bool:
        return self._closed
