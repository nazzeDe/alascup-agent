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
        self._started_call_ids: set[str] = set()

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

    def emit_tool_started(self, tc: AgentToolCall, *, force: bool = False) -> None:
        call_id = self._stable_call_id(tc)
        if call_id in self._started_call_ids and not force:
            return
        self._started_call_ids.add(call_id)
        debug_log("DEBUG", tp.EMIT_TOOL_STARTED, id=call_id, tool=tc.function.name)
        self._channel.send_nowait(
            ToolCallStarted(
                call_id=call_id,
                tool_name=tc.function.name,
                params=tc.function.arguments,
                is_read_only=bool(tc.is_read_only),
                server=tc.server_name,
            )
        )

    def emit_tool_finished(self, r: AgentToolResult) -> None:
        call_id = r.tool_call_id or str(uuid4())
        res = self._result_payload(r)
        debug_log(
            "DEBUG",
            tp.EMIT_TOOL_FINISHED,
            id=call_id,
            status=res.get("execution_status", "SUCCEEDED"),
        )
        self._channel.send_nowait(
            ToolCallFinished(**self._tool_finished_data(call_id, res))
        )

    def _stable_call_id(self, tc: AgentToolCall) -> str:
        call_id = tc.id or str(uuid4())
        tc.id = call_id
        return call_id

    def _result_payload(self, r: AgentToolResult) -> dict:
        res = r.result
        if (
            isinstance(res, dict)
            and "result" in res
            and isinstance(res["result"], dict)
        ):
            return res["result"]
        return res if isinstance(res, dict) else {}

    def _tool_finished_data(self, call_id: str, res: dict) -> dict:
        data = {
            "call_id": call_id,
            "execution_status": res.get("execution_status", "SUCCEEDED"),
        }
        for source_key, target_key in (
            ("output", "output"),
            ("error", "error"),
            ("execution_time_ms", "execution_time_ms"),
        ):
            value = res.get(source_key)
            if value is not None:
                data[target_key] = value
        return data

    # ── Approval ──

    def emit_approval_required(
        self,
        chat_id: str,
        request_id: str,
        tool_name: str,
        params: dict,
        reason: str,
        *,
        call_id: str = "",
    ) -> None:
        self._channel.send_nowait(
            ApprovalRequired(
                chat_id=chat_id,
                request_id=request_id,
                tool_name=tool_name,
                params=params,
                reason=reason,
                call_id=call_id,
            )
        )

    # ── Batch helpers used by orchestrator ──

    def emit_tools_started(
        self,
        tool_calls: list[AgentToolCall],
        *,
        skip_ids: set | None = None,
        force: bool = False,
    ) -> None:
        """Emit ToolCallStarted for each tool call, skipping pending approval IDs."""
        skip = skip_ids or set()
        for tc in tool_calls:
            if tc.id in skip:
                continue
            self.emit_tool_started(tc, force=force)

    def emit_tools_finished(self, results: list[AgentToolResult]) -> None:
        """Emit ToolCallFinished for each result."""
        for r in results:
            self.emit_tool_finished(r)

    # ── Streaming tool results (pre-executed) ──

    def emit_streaming_tool_results(self, results: list[AgentToolResult]) -> None:
        """Emit ToolCallStarted + ToolCallFinished for pre-executed streaming results."""
        for sr in results:
            self._channel.send_nowait(
                ToolCallStarted(
                    call_id=sr.tool_call_id or str(uuid4()),
                    tool_name=sr.tool_name,
                    params={},
                    is_read_only=sr.is_read_only,
                    server=sr.server_name,
                )
            )
            self.emit_tool_finished(sr)
