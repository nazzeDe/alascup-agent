"""EventEmitter — single owner of DomainEvent emission. Converts step outputs + scratch → DomainEvent → EventChannel."""

from uuid import uuid4

from src.agent.domain import AgentToolCall, AgentToolResult
from src.agent.events import (
    ApprovalRequired,
    AssistantDelta,
    AssistantDone,
    EventChannel,
    ReasoningDelta,
    ThinkingDone,
    ToolCallFinished,
    ToolCallStarted,
)
from src.observability.debug_log import log as debug_log
from src.observability import trace_points as tp


class EventEmitter:
    """Converts typed step outputs and scratch state into DomainEvents sent to EventChannel."""

    def __init__(self, channel: EventChannel):
        self._channel = channel

    # ── Streaming (from think_node) ──

    def emit_stream_delta(self, chunk_type: str, delta: str) -> None:
        if chunk_type == "reasoning":
            self._channel.send_nowait(ReasoningDelta(delta=delta))
        elif chunk_type == "assistant":
            self._channel.send_nowait(AssistantDelta(delta=delta))

    def emit_stream_done(self) -> None:
        self._channel.send_nowait(ThinkingDone())
        self._channel.send_nowait(AssistantDone())

    def emit_stream_chunks(self, chunks: list) -> None:
        """Emit ReasoningDelta/AssistantDelta/ThinkingDone/AssistantDone from stream chunks."""
        for chunk_type, delta in chunks:
            self.emit_stream_delta(chunk_type, delta)
        self.emit_stream_done()

    # ── Tool call lifecycle ──

    def emit_tool_started(self, tc: AgentToolCall) -> None:
        params = tc.function.arguments
        call_id = tc.id or str(uuid4())
        debug_log("DEBUG", tp.EMIT_TOOL_STARTED,
                  id=call_id, tool=tc.function.name)
        self._channel.send_nowait(ToolCallStarted(
            call_id=call_id,
            tool_name=tc.function.name,
            params=params,
            is_read_only=bool(tc.is_read_only),
            server=tc.server_name,
        ))

    def emit_tool_finished(self, r: AgentToolResult) -> None:
        res = r.result
        if isinstance(res, dict) and "result" in res and isinstance(res["result"], dict):
            res = res["result"]
        call_id = r.tool_call_id or str(uuid4())
        debug_log("DEBUG", tp.EMIT_TOOL_FINISHED,
                  id=call_id,
                  status=res.get("execution_status", "SUCCEEDED"))
        d = {
            "call_id": call_id,
            "execution_status": res.get("execution_status", "SUCCEEDED"),
        }
        output = res.get("output")
        if output is not None:
            d["output"] = output
        error = res.get("error")
        if error is not None:
            d["error"] = error
        et = res.get("execution_time_ms")
        if et is not None:
            d["execution_time_ms"] = et
        self._channel.send_nowait(ToolCallFinished(**d))

    # ── Approval ──

    def emit_approval_required(self, chat_id: str, request_id: str, tool_name: str, params: dict, reason: str, *, call_id: str = "") -> None:
        self._channel.send_nowait(ApprovalRequired(
            chat_id=chat_id,
            request_id=request_id,
            tool_name=tool_name,
            params=params,
            reason=reason,
            call_id=call_id,
        ))

    # ── Batch helpers used by orchestrator ──

    def emit_tools_started(self, tool_calls: list[AgentToolCall], *, skip_ids: set | None = None) -> None:
        """Emit ToolCallStarted for each tool call, skipping pending approval IDs."""
        skip = skip_ids or set()
        for tc in tool_calls:
            if tc.id in skip:
                continue
            self.emit_tool_started(tc)

    def emit_tools_finished(self, results: list[AgentToolResult]) -> None:
        """Emit ToolCallFinished for each result."""
        for r in results:
            self.emit_tool_finished(r)

    # ── Streaming tool results (pre-executed) ──

    def emit_streaming_tool_results(self, results: list[AgentToolResult]) -> None:
        """Emit ToolCallStarted + ToolCallFinished for pre-executed streaming results."""
        for sr in results:
            self._channel.send_nowait(ToolCallStarted(
                call_id=sr.tool_call_id or str(uuid4()),
                tool_name=sr.tool_name,
                params={},
                is_read_only=sr.is_read_only,
                server=sr.server_name,
            ))
            self.emit_tool_finished(sr)
