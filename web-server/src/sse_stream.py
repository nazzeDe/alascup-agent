"""SSEStream — stateless wire adapter: DomainEvent -> SSE text."""

import json
from typing import Callable

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
from src.agent.shared import is_disconnected
from src.observability.debug_log import log as debug_log
from src.observability import trace_points as tp

WireEvent = dict[str, str]


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
        return await is_disconnected(self._disconnect_check)

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
            debug_log("DEBUG", tp.SSE_DONE)
            yield {"event": "done", "data": "{}"}

    def _to_wire(self, event: DomainEvent) -> WireEvent | None:
        """Single dispatch: DomainEvent -> {event, data}."""
        handlers: tuple[tuple[type, Callable[[object], WireEvent]], ...] = (
            (TurnStarted, self._turn_started_to_wire),
            (ReasoningDelta, self._reasoning_delta_to_wire),
            (ThinkingDone, self._thinking_done_to_wire),
            (AssistantDelta, self._assistant_delta_to_wire),
            (AssistantDone, self._assistant_done_to_wire),
            (ToolCallStarted, self._tool_call_started_to_wire),
            (ToolCallFinished, self._tool_call_finished_to_wire),
            (ApprovalRequired, self._approval_required_to_wire),
            (TurnFailed, self._turn_failed_to_wire),
        )
        for event_type, handler in handlers:
            if isinstance(event, event_type):
                return handler(event)
        # Unknown event type — not emitted to SSE wire
        debug_log("DEBUG", tp.SSE_UNHANDLED, type=type(event).__name__)
        return None

    def _turn_started_to_wire(self, event: TurnStarted) -> WireEvent:
        return {
            "event": "session_init",
            "data": json.dumps({"chat_id": str(event.chat_id)}),
        }

    def _reasoning_delta_to_wire(self, event: ReasoningDelta) -> WireEvent:
        return {"event": "reasoning", "data": json.dumps({"delta": event.delta})}

    def _thinking_done_to_wire(self, event: ThinkingDone) -> WireEvent:
        return {"event": "thinking_done", "data": "{}"}

    def _assistant_delta_to_wire(self, event: AssistantDelta) -> WireEvent:
        return {"event": "assistant", "data": json.dumps({"delta": event.delta})}

    def _assistant_done_to_wire(self, event: AssistantDone) -> WireEvent:
        return {"event": "assistant_done", "data": "{}"}

    def _tool_call_started_to_wire(self, event: ToolCallStarted) -> WireEvent:
        debug_log(
            "DEBUG", tp.SSE_TOOL_STARTED, call_id=event.call_id, tool=event.tool_name
        )
        return {
            "event": "tool_call",
            "data": json.dumps(
                {
                    "call_id": event.call_id,
                    "tool_name": event.tool_name,
                    "params": event.params,
                    "is_read_only": event.is_read_only,
                    "server": event.server,
                }
            ),
        }

    def _tool_call_finished_to_wire(self, event: ToolCallFinished) -> WireEvent:
        debug_log(
            "DEBUG",
            tp.SSE_TOOL_FINISHED,
            call_id=event.call_id,
            status=event.execution_status,
        )
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

    def _approval_required_to_wire(self, event: ApprovalRequired) -> WireEvent:
        return {
            "event": "tool_approval_required",
            "data": json.dumps(
                {
                    "chat_id": event.chat_id,
                    "request_id": event.request_id,
                    "tool_name": event.tool_name,
                    "params": event.params,
                    "reason": event.reason,
                    "call_id": event.call_id,
                }
            ),
        }

    def _turn_failed_to_wire(self, event: TurnFailed) -> WireEvent:
        debug_log("DEBUG", tp.SSE_TURN_FAILED, code=event.code, msg=event.message)
        return {
            "event": "error",
            "data": json.dumps({"code": event.code, "message": event.message}),
        }
