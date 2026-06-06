"""Unit tests for ApprovalHandler and its module-level helpers."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.loop.approval import (
    ApprovalHandler,
    _apply_decisions,
    _inject_rejection_messages,
)
from src.agent.state import Transition
from src.models.audit import AuditActor, AuditEvent, AuditLevel
from src.models.tool import ApprovalStatus, ExecutionStatus


# ── _apply_decisions ──────────────────────────────────────────────────────


class TestApplyDecisions:
    def test_all_approved(self):
        pending = [
            {"function": {"name": "get_cpu"}, "id": "1"},
            {"function": {"name": "get_mem"}, "id": "2"},
        ]
        decisions = ["APPROVED", "APPROVED"]
        approved, rejected = _apply_decisions(pending, decisions)
        assert len(approved) == 2
        assert len(rejected) == 0
        assert approved[0]["approval_status"] == "APPROVED"
        assert approved[1]["approval_status"] == "APPROVED"

    def test_all_rejected(self):
        pending = [
            {"function": {"name": "get_cpu"}, "id": "1"},
            {"function": {"name": "get_mem"}, "id": "2"},
        ]
        decisions = ["REJECTED", "REJECTED"]
        approved, rejected = _apply_decisions(pending, decisions)
        assert len(approved) == 0
        assert len(rejected) == 2

    def test_mixed(self):
        pending = [
            {"function": {"name": "get_cpu"}, "id": "1"},
            {"function": {"name": "get_mem"}, "id": "2"},
            {"function": {"name": "get_uptime"}, "id": "3"},
        ]
        decisions = ["APPROVED", "REJECTED", "APPROVED"]
        approved, rejected = _apply_decisions(pending, decisions)
        assert len(approved) == 2
        assert len(rejected) == 1
        assert approved[0]["id"] == "1"
        assert rejected[0]["id"] == "2"
        assert approved[1]["id"] == "3"

    def test_missing_decision_defaults_to_expired(self):
        """Fewer decisions than pending → remaining treated as EXPIRED (rejected)."""
        pending = [
            {"function": {"name": "get_cpu"}, "id": "1"},
            {"function": {"name": "get_mem"}, "id": "2"},
        ]
        decisions = ["APPROVED"]  # only 1 decision for 2 tools
        approved, rejected = _apply_decisions(pending, decisions)
        assert len(approved) == 1
        assert len(rejected) == 1
        assert rejected[0]["id"] == "2"


# ── _inject_rejection_messages ─────────────────────────────────────────────


class TestInjectRejectionMessages:
    def test_appends_tool_messages(self):
        state = {"messages": [{"role": "user", "content": "hello"}]}
        rejected = [
            {"function": {"name": "get_cpu"}, "id": "tc-1"},
        ]
        _inject_rejection_messages(state, rejected)
        assert len(state["messages"]) == 2
        tool_msg = state["messages"][-1]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "tc-1"
        assert tool_msg["name"] == "get_cpu"
        assert "REJECTED" in tool_msg["content"]
        assert "do not retry" in tool_msg["content"].lower()

    def test_empty_rejected_no_change(self):
        state = {"messages": [{"role": "user", "content": "hello"}]}
        _inject_rejection_messages(state, [])
        assert len(state["messages"]) == 1

    def test_multiple_rejected(self):
        state = {"messages": []}
        rejected = [
            {"function": {"name": "tool_a"}, "id": "a"},
            {"function": {"name": "tool_b"}, "id": "b"},
        ]
        _inject_rejection_messages(state, rejected)
        assert len(state["messages"]) == 2
        assert state["messages"][0]["name"] == "tool_a"
        assert state["messages"][1]["name"] == "tool_b"

    def test_unknown_function_name(self):
        state = {"messages": []}
        rejected = [{"id": "no-fn"}]
        _inject_rejection_messages(state, rejected)
        assert state["messages"][0]["name"] == "unknown"


# ── mock helpers ────────────────────────────────────────────────────────────


class _MockAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


class _MockBridge:
    def __init__(self, decisions: list[str] | None = None):
        self.created: list[tuple] = []
        self._decisions = decisions or ["APPROVED"]

    def create(self, request_id: str, chat_id: str) -> None:
        self.created.append((request_id, chat_id))

    async def gather_decisions(self, request_id: str, expected_count: int):
        return self._decisions


class _MockGraph:
    """Graph that returns a state with no pending approvals."""
    def __init__(self, responses: list[dict] | None = None):
        self._responses = responses or [{}]
        self._calls: list[dict] = []

    async def ainvoke(self, state, config=None):
        self._calls.append(dict(state))
        idx = min(len(self._calls) - 1, len(self._responses) - 1)
        resp = dict(self._responses[idx])
        # Preserve previous fields unless explicitly overridden
        out = dict(state)
        out.update(resp)
        return out


class _MockLifecycle:
    def __init__(self):
        self.updates: list[dict] = []

    async def update(self, chat_id, call_id, **kwargs):
        self.updates.append({"chat_id": chat_id, "call_id": call_id, **kwargs})


_MOCK_PROFILER = MagicMock()


# ── ApprovalHandler.resolve ─────────────────────────────────────────────────


class TestApprovalHandlerResolve:
    """Tests for ApprovalHandler.resolve() — the sole public method."""

    @pytest.mark.asyncio
    async def test_no_pending_approval_yields_nothing(self):
        """When result has no pending_approval, resolve yields no events."""
        handler = ApprovalHandler(
            graph=AsyncMock(),
            bridge=MagicMock(),
            audit_logger=MagicMock(),
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "messages": [{"role": "assistant", "content": "done"}],
        }
        events = []
        async for ev in handler.resolve(result):
            events.append(ev)
        assert events == []

    @pytest.mark.asyncio
    async def test_pending_approval_yields_approval_required(self):
        """With pending_approval, resolve yields tool_approval_required events."""
        bridge = _MockBridge(decisions=["APPROVED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        events = []
        async for ev in handler.resolve(result):
            events.append(ev)

        approval_events = [e for e in events if e["event"] == "tool_approval_required"]
        assert len(approval_events) == 1
        data = json.loads(approval_events[0]["data"])
        assert data["tool_name"] == "get_cpu"
        assert data["request_id"] == "req-1"

    @pytest.mark.asyncio
    async def test_approved_tool_added_to_result(self):
        """Approved tools populate result['approved_tool_calls']."""
        bridge = _MockBridge(decisions=["APPROVED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass

        assert len(result["approved_tool_calls"]) == 1
        assert result["approved_tool_calls"][0]["approval_status"] == "APPROVED"
        assert result["pending_approval"] == []
        assert result["transition"] == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_rejected_tool_added_to_result_and_injected(self):
        """Rejected tools populate result['rejected_tool_calls'] and get injected into messages."""
        bridge = _MockBridge(decisions=["REJECTED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_REJECTED},
        ])
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "rm_file", "arguments": "{}"}, "id": "tc-x", "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [{"role": "user", "content": "delete tmp"}],
        }
        async for _ in handler.resolve(result):
            pass

        assert len(result["rejected_tool_calls"]) == 1
        assert result["pending_approval"] == []
        assert result["transition"] == Transition.APPROVAL_REJECTED
        # Rejection message injected
        assert len(result["messages"]) == 2
        assert result["messages"][-1]["role"] == "tool"
        assert "REJECTED" in result["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_multi_round_approval(self):
        """Graph returns more pending approvals after first resolution → second round."""
        bridge = _MockBridge(decisions=["APPROVED"])
        # First graph resume returns another pending; second finishes.
        graph = _MockGraph(responses=[
            {"pending_approval": [
                {"function": {"name": "tool_b", "arguments": "{}"}, "request_id": "req-2"},
            ], "transition": None},
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        audit = _MockAuditLogger()
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=audit,
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "tool_a", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        events = []
        async for ev in handler.resolve(result):
            events.append(ev)

        # Two rounds → two approval events
        approval_events = [e for e in events if e["event"] == "tool_approval_required"]
        assert len(approval_events) == 2
        assert len(graph._calls) == 2
        assert result["transition"] == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_audit_approved_logged(self):
        """Approved tool → TOOL_APPROVED audit event."""
        bridge = _MockBridge(decisions=["APPROVED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        audit = _MockAuditLogger()
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=audit,
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass

        tool_approved = [e for e in audit.events if e.event == "TOOL_APPROVED"]
        assert len(tool_approved) == 1
        assert tool_approved[0].tool_name == "get_cpu"
        assert tool_approved[0].decision == "APPROVED"
        assert tool_approved[0].level == AuditLevel.INFO

    @pytest.mark.asyncio
    async def test_audit_rejected_logged(self):
        """Rejected tool → TOOL_REJECTED audit event."""
        bridge = _MockBridge(decisions=["REJECTED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_REJECTED},
        ])
        audit = _MockAuditLogger()
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=audit,
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "rm_file", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass

        tool_rejected = [e for e in audit.events if e.event == "TOOL_REJECTED"]
        assert len(tool_rejected) == 1
        assert tool_rejected[0].tool_name == "rm_file"
        assert tool_rejected[0].decision == "REJECTED"
        assert tool_rejected[0].level == AuditLevel.WARN

    @pytest.mark.asyncio
    async def test_lifecycle_updated_on_approved(self):
        """Lifecycle receives APPROVED/RUNNING status for approved tools."""
        bridge = _MockBridge(decisions=["APPROVED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        lifecycle = _MockLifecycle()
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
            lifecycle=lifecycle,
        )
        chat_id = "12345678-1234-5678-1234-567812345678"
        result = {
            "_chat_id": chat_id,
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1", "call_id": "call-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass

        assert len(lifecycle.updates) == 1
        update = lifecycle.updates[0]
        assert update["call_id"] == "call-1"
        assert update["approval_status"] == ApprovalStatus.APPROVED
        assert update["execution_status"] == ExecutionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_lifecycle_updated_on_rejected(self):
        """Lifecycle receives REJECTED/FAILED status for rejected tools."""
        bridge = _MockBridge(decisions=["REJECTED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_REJECTED},
        ])
        lifecycle = _MockLifecycle()
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
            lifecycle=lifecycle,
        )
        chat_id = "12345678-1234-5678-1234-567812345678"
        result = {
            "_chat_id": chat_id,
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "rm_file", "arguments": "{}"}, "request_id": "req-1", "call_id": "call-2"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass

        assert len(lifecycle.updates) == 1
        update = lifecycle.updates[0]
        assert update["call_id"] == "call-2"
        assert update["approval_status"] == ApprovalStatus.REJECTED
        assert update["execution_status"] == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_no_lifecycle_no_crash(self):
        """Missing lifecycle does not crash resolve."""
        bridge = _MockBridge(decisions=["APPROVED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
            lifecycle=None,
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass
        # No exception → success
        assert result["transition"] == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_no_audit_logger_no_crash(self):
        """Missing audit_logger does not crash resolve."""
        bridge = _MockBridge(decisions=["APPROVED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=None,
        )
        result = {
            "_chat_id": "c1",
            "_turn_id": None,
            "_iteration": 1,
            "_model": "test-model",
            "pending_approval": [
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass
        assert result["transition"] == Transition.APPROVAL_GRANTED

    @pytest.mark.asyncio
    async def test_missing_turn_id_model_from_result_handled(self):
        """When _turn_id and _model are missing, resolve still works."""
        bridge = _MockBridge(decisions=["APPROVED"])
        graph = _MockGraph(responses=[
            {"pending_approval": [], "transition": Transition.APPROVAL_GRANTED},
        ])
        handler = ApprovalHandler(
            graph=graph,
            bridge=bridge,
            audit_logger=_MockAuditLogger(),
        )
        result = {
            "_chat_id": "",
            "_iteration": 0,
            "pending_approval": [
                {"function": {"name": "get_cpu", "arguments": "{}"}, "request_id": "req-1"},
            ],
            "approved_tool_calls": [],
            "rejected_tool_calls": [],
            "messages": [],
        }
        async for _ in handler.resolve(result):
            pass
        assert result["transition"] == Transition.APPROVAL_GRANTED
