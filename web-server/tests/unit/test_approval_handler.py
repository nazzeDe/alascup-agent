"""Unit tests for ApprovalHandler and its module-level helpers."""

from uuid import UUID, uuid4
from unittest.mock import MagicMock

import pytest

from src.agent.events import ApprovalRequired, EventChannel
from src.agent.loop.approval import (
    ApprovalHandler,
    _apply_decisions,
    _format_rejection_message,
    _inject_rejection_messages,
)
from src.agent.loop.emitter import EventEmitter
from src.agent.state import Transition, TurnScratch
from src.agent.turn_context import TurnContext
from src.models.audit import AuditLevel
from src.models.tool import ApprovalStatus, ExecutionStatus


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_turn_ctx(chat_id: UUID | None = None, turn_id: UUID = None, iteration: int = 1, model: str = "test-model") -> TurnContext:
    if turn_id is None:
        turn_id = uuid4()
    if chat_id is None:
        chat_id = uuid4()
    return TurnContext(chat_id=chat_id, turn_id=turn_id, iteration=iteration, model=model)


async def _collect_approval_events(channel: EventChannel, timeout: float = 0.5) -> list[ApprovalRequired]:
    import asyncio as _asyncio
    events: list[ApprovalRequired] = []
    while True:
        try:
            event = await _asyncio.wait_for(channel.receive(), timeout=timeout)
        except _asyncio.TimeoutError:
            break
        if event is None:
            break
        if isinstance(event, ApprovalRequired):
            events.append(event)
    return events


def _make_scratch(**overrides) -> TurnScratch:
    """Create a TurnScratch with sensible defaults for testing."""
    defaults = {
        "pending_approval": [],
        "approved_tool_calls": [],
        "rejected_tool_calls": [],
        "tool_calls": [],
        "tool_results": [],
        "streaming_tool_results": [],
        "_emitted_results": [],
        "llm_error": None,
        "transition": None,
    }
    defaults.update(overrides)
    return TurnScratch(**defaults)


# ── _apply_decisions ─────────────────────────────────────────────────────────


class TestApplyDecisions:
    def test_splits_approved_and_rejected(self):
        pending = [
            {"function": {"name": "get_cpu"}, "id": "1"},
            {"function": {"name": "rm_file"}, "id": "2"},
        ]
        decisions = [{"status": "APPROVED", "reason": None},
                     {"status": "REJECTED", "reason": "not needed"}]
        approved, rejected = _apply_decisions(pending, decisions)
        assert len(approved) == 1
        assert approved[0]["id"] == "1"
        assert approved[0]["approval_status"] == "APPROVED"
        assert len(rejected) == 1
        assert rejected[0]["id"] == "2"
        assert rejected[0]["rejection_reason"] == "not needed"

    def test_missing_decisions_default_to_expired(self):
        pending = [
            {"function": {"name": "get_cpu"}, "id": "1"},
            {"function": {"name": "rm_file"}, "id": "2"},
        ]
        decisions = [{"status": "APPROVED", "reason": None}]  # Only one decision for two tools
        approved, rejected = _apply_decisions(pending, decisions)
        assert len(approved) == 1
        assert approved[0]["id"] == "1"
        assert len(rejected) == 1
        assert rejected[0]["id"] == "2"
        assert "rejection_reason" in rejected[0]

    def test_all_approved(self):
        pending = [
            {"function": {"name": "a"}, "id": "1"},
            {"function": {"name": "b"}, "id": "2"},
        ]
        approved, rejected = _apply_decisions(pending, [
            {"status": "APPROVED", "reason": None},
            {"status": "APPROVED", "reason": None},
        ])
        assert len(approved) == 2
        assert len(rejected) == 0

    def test_all_expired_default(self):
        pending = [
            {"function": {"name": "a"}, "id": "1"},
            {"function": {"name": "b"}, "id": "2"},
        ]
        decisions = [{"status": "EXPIRED", "reason": None},
                     {"status": "EXPIRED", "reason": None}]
        approved, rejected = _apply_decisions(pending, decisions)
        assert len(approved) == 0
        assert len(rejected) == 2


# ── _inject_rejection_messages ─────────────────────────────────────────────


class TestInjectRejectionMessages:
    def test_appends_tool_messages(self):
        state = {"messages": [{"role": "user", "content": "hello"}]}
        rejected = [
            {"function": {"name": "get_cpu"}, "id": "tc-1", "rejection_reason": "not needed"},
        ]
        _inject_rejection_messages(state, rejected)
        assert len(state["messages"]) == 2
        tool_msg = state["messages"][-1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "tc-1"
        assert tool_msg["name"] == "get_cpu"
        assert "REJECTED" in tool_msg["content"]
        assert "not needed" in tool_msg["content"]
        assert "do not retry" in tool_msg["content"].lower()

    def test_rejection_without_reason(self):
        state = {"messages": [{"role": "user", "content": "hello"}]}
        rejected = [
            {"function": {"name": "rm_file"}, "id": "tc-x"},
        ]
        _inject_rejection_messages(state, rejected)
        tool_msg = state["messages"][-1]
        assert "rejection_reason=" not in tool_msg["content"]
        assert "do not retry" in tool_msg["content"].lower()

    def test_empty_rejected_no_change(self):
        state = {"messages": [{"role": "user", "content": "hello"}]}
        _inject_rejection_messages(state, [])
        assert len(state["messages"]) == 1

    def test_multiple_rejected(self):
        state = {"messages": []}
        rejected = [
            {"function": {"name": "tool_a"}, "id": "a", "rejection_reason": "nope"},
            {"function": {"name": "tool_b"}, "id": "b", "rejection_reason": "stop"},
        ]
        _inject_rejection_messages(state, rejected)
        assert len(state["messages"]) == 2
        assert state["messages"][0]["name"] == "tool_a"
        assert "nope" in state["messages"][0]["content"]
        assert state["messages"][1]["name"] == "tool_b"
        assert "stop" in state["messages"][1]["content"]

    def test_unknown_function_name(self):
        state = {"messages": []}
        rejected = [{"id": "no-fn"}]
        _inject_rejection_messages(state, rejected)
        assert state["messages"][0]["name"] == "unknown"


# ── _format_rejection_message ──────────────────────────────────────────────


class TestFormatRejectionMessage:
    def test_with_reason(self):
        tc = {"function": {"name": "rm_file"}, "id": "tc-1",
              "rejection_reason": "I don't want to create this"}
        msg = _format_rejection_message(tc)
        assert "[rm_file] execution_status=REJECTED" in msg
        assert "rejection_reason=I don't want to create this" in msg
        assert "Do NOT retry." in msg
        assert "propose an alternative" not in msg

    def test_without_reason(self):
        tc = {"function": {"name": "get_cpu"}, "id": "tc-2"}
        msg = _format_rejection_message(tc)
        assert "[get_cpu] execution_status=REJECTED" in msg
        assert "rejection_reason=" not in msg
        assert "error=Tool was rejected by human. Do NOT retry." in msg

    def test_unknown_tool_name(self):
        tc = {"id": "bare"}
        msg = _format_rejection_message(tc)
        assert "[unknown] execution_status=REJECTED" in msg


# ── mock helpers ────────────────────────────────────────────────────────────


class _MockAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


class _MockBridge:
    def __init__(self, decisions: list[str] | None = None):
        self.created: list[tuple] = []
        self._decisions = [{"status": d, "reason": None} for d in (decisions or ["APPROVED"])]

    def create(self, request_id: str, chat_id: str) -> None:
        self.created.append((request_id, chat_id))

    async def gather_decisions(self, request_id: str, expected_count: int):
        return self._decisions


class _MockLifecycle:
    def __init__(self):
        self.updates: list[dict] = []

    async def update(self, chat_id, call_id, **kwargs):
        self.updates.append({"chat_id": chat_id, "call_id": call_id, **kwargs})

    async def mark_approved(self, chat_id, call_id):
        self.updates.append({
            "chat_id": chat_id,
            "call_id": call_id,
            "approval_status": ApprovalStatus.APPROVED,
            "execution_status": ExecutionStatus.RUNNING,
        })

    async def mark_rejected(self, chat_id, call_id):
        self.updates.append({
            "chat_id": chat_id,
            "call_id": call_id,
            "approval_status": ApprovalStatus.REJECTED,
            "execution_status": ExecutionStatus.FAILED,
        })

    async def mark_expired(self, chat_id, call_id):
        self.updates.append({
            "chat_id": chat_id,
            "call_id": call_id,
            "approval_status": ApprovalStatus.EXPIRED,
            "execution_status": ExecutionStatus.FAILED,
        })


_MOCK_PROFILER = MagicMock()


# ── ApprovalHandler.resolve ─────────────────────────────────────────────────


class TestApprovalHandlerResolve:
    """Tests for ApprovalHandler.resolve() — the sole public method.

    resolve() emits ApprovalRequired events, waits on the bridge, applies
    decisions, and returns. Multi-round approval is handled by AgentLoop.
    """

    @pytest.mark.asyncio
    async def test_no_pending_approval_yields_nothing(self):
        """When scratch has no pending_approval, resolve sends no events."""
        handler = ApprovalHandler(
            bridge=MagicMock(),
            audit_logger=MagicMock(),
        )
        scratch = _make_scratch()
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()
        events = await _collect_approval_events(channel)
        assert events == []

    @pytest.mark.asyncio
    async def test_pending_approval_sends_approval_required(self):
        """With pending_approval, resolve sends ApprovalRequired events."""
        bridge = _MockBridge(decisions=["APPROVED"])
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()
        events = await _collect_approval_events(channel)
        assert len(events) == 1
        assert events[0].tool_name == "get_cpu"
        assert events[0].request_id == "req-1"

    @pytest.mark.asyncio
    async def test_approved_tool_added_to_result(self):
        """Approved tools populate scratch.approved_tool_calls."""
        bridge = _MockBridge(decisions=["APPROVED"])
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()

        assert len(scratch.approved_tool_calls) == 1
        assert scratch.approved_tool_calls[0]["approval_status"] == "APPROVED"
        assert scratch.pending_approval == []
        assert scratch.transition == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_rejected_tool_added_to_result_and_injected(self):
        """Rejected tools populate scratch.rejected_tool_calls.
        Message injection is handled by the orchestrator, not resolve().
        """
        bridge = _MockBridge(decisions=["REJECTED"])
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "rm_file", "arguments": "{}"}, "id": "tc-x", "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()

        assert len(scratch.rejected_tool_calls) == 1
        assert scratch.pending_approval == []
        assert scratch.transition == Transition.APPROVAL_REJECTED

    @pytest.mark.asyncio
    async def test_single_pass_approval_does_not_loop(self):
        """resolve() processes one round of pending_approval, then returns.

        Multi-round approval is handled by AgentLoop re-entering the step,
        not by resolve() looping internally.
        """
        bridge = _MockBridge(decisions=["APPROVED"])
        audit = _MockAuditLogger()
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=audit,
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "tool_a", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()
        events = await _collect_approval_events(channel)

        # Single round — one approval event
        assert len(events) == 1
        assert scratch.transition == Transition.APPROVAL_GRANTED
        assert len(scratch.approved_tool_calls) == 1
        assert scratch.pending_approval == []

    @pytest.mark.asyncio
    async def test_audit_approved_logged(self):
        """Approved tool -> TOOL_APPROVED audit event."""
        bridge = _MockBridge(decisions=["APPROVED"])
        audit = _MockAuditLogger()
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=audit,
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()

        tool_approved = [e for e in audit.events if e.event == "TOOL_APPROVED"]
        assert len(tool_approved) == 1
        assert tool_approved[0].tool_name == "get_cpu"
        assert tool_approved[0].decision == "APPROVED"
        assert tool_approved[0].level == AuditLevel.INFO

    @pytest.mark.asyncio
    async def test_audit_rejected_logged(self):
        """Rejected tool -> TOOL_REJECTED audit event."""
        bridge = _MockBridge(decisions=["REJECTED"])
        audit = _MockAuditLogger()
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=audit,
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "rm_file", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()

        tool_rejected = [e for e in audit.events if e.event == "TOOL_REJECTED"]
        assert len(tool_rejected) == 1
        assert tool_rejected[0].tool_name == "rm_file"
        assert tool_rejected[0].decision == "REJECTED"
        assert tool_rejected[0].level == AuditLevel.WARN

    @pytest.mark.asyncio
    async def test_lifecycle_updated_on_approved(self):
        """Lifecycle receives APPROVED/RUNNING status for approved tools."""
        bridge = _MockBridge(decisions=["APPROVED"])
        lifecycle = _MockLifecycle()
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
            lifecycle=lifecycle,
        )
        chat_id = "12345678-1234-5678-1234-567812345678"
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1", "call_id": "call-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(chat_id=chat_id), emitter=emitter)
        channel.close()

        assert len(lifecycle.updates) == 1
        update = lifecycle.updates[0]
        assert update["call_id"] == "call-1"
        assert update["approval_status"] == ApprovalStatus.APPROVED
        assert update["execution_status"] == ExecutionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_lifecycle_updated_on_rejected(self):
        """Lifecycle receives REJECTED/FAILED status for rejected tools."""
        bridge = _MockBridge(decisions=["REJECTED"])
        lifecycle = _MockLifecycle()
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
            lifecycle=lifecycle,
        )
        chat_id = "12345678-1234-5678-1234-567812345678"
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "rm_file", "arguments": "{}"}, "request_id": "req-1", "call_id": "call-2"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(chat_id=chat_id), emitter=emitter)
        channel.close()

        assert len(lifecycle.updates) == 1
        update = lifecycle.updates[0]
        assert update["call_id"] == "call-2"
        assert update["approval_status"] == ApprovalStatus.REJECTED
        assert update["execution_status"] == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_lifecycle_updated_on_expired(self):
        """Lifecycle receives EXPIRED/FAILED status for expired tools."""
        bridge = _MockBridge(decisions=["EXPIRED"])
        lifecycle = _MockLifecycle()
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
            lifecycle=lifecycle,
        )
        chat_id = "12345678-1234-5678-1234-567812345678"
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "rm_file", "arguments": "{}"}, "request_id": "req-1", "call_id": "call-3"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(chat_id=chat_id), emitter=emitter)
        channel.close()

        assert len(lifecycle.updates) == 1
        update = lifecycle.updates[0]
        assert update["call_id"] == "call-3"
        assert update["approval_status"] == ApprovalStatus.EXPIRED
        assert update["execution_status"] == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_no_lifecycle_no_crash(self):
        """Missing lifecycle does not crash resolve."""
        bridge = _MockBridge(decisions=["APPROVED"])
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
            lifecycle=None,
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()
        # No exception -> success
        assert scratch.transition == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_no_audit_logger_no_crash(self):
        """Missing audit_logger does not crash resolve."""
        bridge = _MockBridge(decisions=["APPROVED"])
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=None,
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=emitter)
        channel.close()
        assert scratch.transition == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_missing_turn_context_handled(self):
        """When turn_ctx is not passed, resolve uses safe defaults (TurnContext fields optional)."""
        bridge = _MockBridge(decisions=["APPROVED"])
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        # TurnContext with None chat_id (not a real UUID)
        ctx = TurnContext(chat_id=None, turn_id=uuid4(), iteration=0, model=None)
        channel = EventChannel()
        emitter = EventEmitter(channel)
        await handler.resolve(scratch, turn_ctx=ctx, emitter=emitter)
        channel.close()
        assert scratch.transition == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_no_channel_no_crash(self):
        """Missing emitter does not crash resolve."""
        bridge = _MockBridge(decisions=["APPROVED"])
        handler = ApprovalHandler(
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        scratch = _make_scratch(
            pending_approval=[
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
        )
        await handler.resolve(scratch, turn_ctx=_make_turn_ctx(), emitter=None)
        assert scratch.transition == Transition.APPROVAL_GRANTED
