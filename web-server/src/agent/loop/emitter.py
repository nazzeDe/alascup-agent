"""EventEmitter — single owner of DomainEvent emission. Converts step outputs + scratch → DomainEvent → EventChannel."""

import json
from uuid import uuid4

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


class EventEmitter:
    """Converts typed step outputs and scratch state into DomainEvents sent to EventChannel."""

    def __init__(self, channel: EventChannel):
        self._channel = channel

    # ── Streaming (from think_node) ──

    def emit_stream_chunks(self, chunks: list) -> None:
        """Emit ReasoningDelta/AssistantDelta/ThinkingDone/AssistantDone from stream chunks."""
        for chunk_type, delta in chunks:
            if chunk_type == "reasoning":
                self._channel.send_nowait(ReasoningDelta(delta=delta))
            elif chunk_type == "assistant":
                self._channel.send_nowait(AssistantDelta(delta=delta))
        self._channel.send_nowait(ThinkingDone())
        self._channel.send_nowait(AssistantDone())

    def emit_thinking_done(self) -> None:
        self._channel.send_nowait(ThinkingDone())

    def emit_assistant_done(self) -> None:
        self._channel.send_nowait(AssistantDone())

    # ── Tool call lifecycle ──

    def emit_tool_started(self, tc: dict) -> None:
        fn = tc.get("function", {})
        params = fn.get("arguments", "{}")
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}
        self._channel.send_nowait(ToolCallStarted(
            call_id=tc.get("id") or str(uuid4()),
            tool_name=fn.get("name", ""),
            params=params,
            is_read_only=tc.get("is_read_only", False),
            server=tc.get("server_name", ""),
        ))

    def emit_tool_finished(self, r: dict) -> None:
        res = r.get("result", {})
        if isinstance(res, dict) and "result" in res and isinstance(res["result"], dict):
            res = res["result"]
        d = {
            "call_id": r.get("tool_call_id") or str(uuid4()),
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

    def emit_approval_required(self, chat_id: str, request_id: str, tool_name: str, params: dict, reason: str) -> None:
        self._channel.send_nowait(ApprovalRequired(
            chat_id=chat_id,
            request_id=request_id,
            tool_name=tool_name,
            params=params,
            reason=reason,
        ))

    # ── Batch helpers used by orchestrator ──

    def emit_tools_started(self, tool_calls: list, *, skip_ids: set | None = None) -> None:
        """Emit ToolCallStarted for each tool call, skipping pending approval IDs."""
        skip = skip_ids or set()
        for tc in tool_calls:
            if tc.get("id") in skip:
                continue
            self.emit_tool_started(tc)

    def emit_tools_finished(self, results: list) -> None:
        """Emit ToolCallFinished for each result."""
        for r in results:
            self.emit_tool_finished(r)

    # ── Streaming tool results (pre-executed) ──

    def emit_streaming_tool_results(self, results: list) -> None:
        """Emit ToolCallStarted + ToolCallFinished for pre-executed streaming results."""
        for sr in results:
            self._channel.send_nowait(ToolCallStarted(
                call_id=sr.get("tool_call_id") or str(uuid4()),
                tool_name=sr.get("tool_name", ""),
                params={},
                is_read_only=sr.get("is_read_only", False),
                server=sr.get("server_name", ""),
            ))
            self.emit_tool_finished(sr)
