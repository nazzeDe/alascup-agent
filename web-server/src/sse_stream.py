"""SSEStream — stateless wire adapter: DomainEvent -> SSE text."""

import asyncio
import json

from loguru import logger

from src.agent.events import (
    ApprovalRequired,
    AssistantDelta,
    AssistantDone,
    DomainEvent,
    ReasoningDelta,
    ThinkingDone,
    ToolCallFinished,
    ToolCallStarted,
    TurnFailed,
    TurnStarted,
)


class SSEStream:
    """Stateless wire adapter: DomainEvent -> SSE text.

    Usage::

        turn = ChatTurn(...)
        stream = SSEStream(turn=turn, disconnect_check=...)
        async for wire_event in stream:
            # wire_event: {"event": str, "data": str}

    Contract:
    - First event is always 'session_init' with chat_id
    - Last event is always 'done' (emitted in finally)
    """

    def __init__(self, *, turn, disconnect_check=None):
        self._turn = turn
        self._disconnect_check = disconnect_check

    async def _is_disconnected(self) -> bool:
        if not self._disconnect_check:
            return False
        result = self._disconnect_check()
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)

    async def __aiter__(self):
        """Map DomainEvents to SSE wire format. Emit 'done' in finally."""
        try:
            async for event in self._turn.events():
                wire = self._to_wire(event)
                if wire:
                    yield wire
                if await self._is_disconnected():
                    break
        finally:
            logger.debug("SSEStream.__aiter__ yielding done event")
            yield {"event": "done", "data": "{}"}

    def _to_wire(self, event: DomainEvent) -> dict | None:
        """Single dispatch: DomainEvent -> {event, data}."""
        if isinstance(event, TurnStarted):
            return {"event": "session_init", "data": json.dumps({"chat_id": str(event.chat_id)})}
        elif isinstance(event, ReasoningDelta):
            return {"event": "reasoning", "data": json.dumps({"delta": event.delta})}
        elif isinstance(event, ThinkingDone):
            return {"event": "thinking_done", "data": "{}"}
        elif isinstance(event, AssistantDelta):
            return {"event": "assistant", "data": json.dumps({"delta": event.delta})}
        elif isinstance(event, AssistantDone):
            return {"event": "assistant_done", "data": "{}"}
        elif isinstance(event, ToolCallStarted):
            logger.debug("SSEStream.to_wire ToolCallStarted call_id={} tool={}",
                         event.call_id, event.tool_name)
            return {"event": "tool_call", "data": json.dumps({
                "call_id": event.call_id,
                "tool_name": event.tool_name,
                "params": event.params,
                "is_read_only": event.is_read_only,
                "server": event.server,
            })}
        elif isinstance(event, ToolCallFinished):
            logger.debug("SSEStream.to_wire ToolCallFinished call_id={} status={}",
                         event.call_id, event.execution_status)
            data: dict = {
                "call_id": event.call_id,
                "execution_status": event.execution_status,
            }
            if event.output is not None:
                data["output"] = event.output
            if event.error is not None:
                data["error"] = event.error
            if event.execution_time_ms is not None:
                data["execution_time_ms"] = event.execution_time_ms
            return {"event": "tool_result", "data": json.dumps(data)}
        elif isinstance(event, ApprovalRequired):
            return {"event": "tool_approval_required", "data": json.dumps({
                "chat_id": event.chat_id,
                "request_id": event.request_id,
                "tool_name": event.tool_name,
                "params": event.params,
                "reason": event.reason,
            })}
        elif isinstance(event, TurnFailed):
            logger.debug("sse_stream TurnFailed code={code} msg={msg}", code=event.code, msg=event.message)
            return {"event": "error", "data": json.dumps({"code": event.code, "message": event.message})}
        return None
