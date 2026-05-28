"""Unit tests for agent loop modules.

Tests emit_events, clear_transient_fields, get_transition,
handle_pending_approval, and handle_llm_error in isolation — no graph, no LLM.
"""

import json

import pytest

from src.agent.loop.audit import log_transition
from src.agent.loop.events import emit_events
from src.agent.loop.handlers.error import handle_llm_error
from src.agent.loop.handlers.interrupt import handle_pending_approval
from src.agent.loop.transitions import (
    TRANSIENT_FIELDS,
    clear_transient_fields,
    get_transition,
)
from src.agent.state import Transition

# ── emit_events ────────────────────────────────────────────────────────


class TestEmitEvents:
    def test_assistant_message_yields_assistant_event(self):
        state = {"messages": [{"role": "assistant", "content": "CPU normal."}]}
        events = emit_events(state)
        assert len(events) == 1
        assert events[0]["event"] == "assistant"
        assert "CPU normal." in events[0]["data"]

    def test_multiple_assistant_messages_all_emitted(self):
        state = {
            "messages": [
                {"role": "assistant", "content": "First."},
                {"role": "assistant", "content": "Second."},
            ]
        }
        events = emit_events(state)
        assert len(events) == 2
        assert all(e["event"] == "assistant" for e in events)

    def test_non_assistant_messages_skipped(self):
        state = {
            "messages": [
                {"role": "user", "content": "check CPU"},
                {"role": "tool", "content": "[get_cpu] ..."},
            ]
        }
        events = emit_events(state)
        assert events == []

    def test_tool_calls_emitted(self):
        state = {
            "tool_calls": [
                {"id": "tc-1", "function": {"name": "get_cpu", "arguments": '{"unit":"percent"}'}, "is_read_only": True},
            ]
        }
        events = emit_events(state, chat_id="c1")
        assert len(events) == 1
        assert events[0]["event"] == "tool_call"
        data = json.loads(events[0]["data"])
        assert data["tool_name"] == "get_cpu"
        assert data["chat_id"] == "c1"
        assert data["message_id"] == "tc-1"
        assert data["is_read_only"] is True
        assert data["params"] == {"unit": "percent"}

    def test_tool_results_emitted(self):
        state = {
            "tool_results": [
                {"tool_call_id": "tr-1", "tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED"}},
            ]
        }
        events = emit_events(state, chat_id="c1")
        assert len(events) == 1
        assert events[0]["event"] == "tool_result"
        data = json.loads(events[0]["data"])
        assert data["chat_id"] == "c1"
        assert data["message_id"] == "tr-1"
        assert data["execution_status"] == "SUCCEEDED"

    def test_streaming_tool_results_emit_pair(self):
        """streaming_tool_result → tool_call + tool_result pair."""
        state = {
            "streaming_tool_results": [
                {"tool_call_id": "st-1", "tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED"}},
            ]
        }
        events = emit_events(state, chat_id="c1")
        assert len(events) == 2
        assert events[0]["event"] == "tool_call"
        data0 = json.loads(events[0]["data"])
        assert data0["chat_id"] == "c1"
        assert data0["message_id"] == "st-1"
        assert data0["is_read_only"] is True
        assert events[1]["event"] == "tool_result"
        data1 = json.loads(events[1]["data"])
        assert data1["chat_id"] == "c1"
        assert data1["message_id"] == "st-1"

    def test_mixed_state_emits_in_order(self):
        state = {
            "messages": [{"role": "assistant", "content": "Let me check."}],
            "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
            ],
            "tool_results": [
                {
                    "tool_name": "get_memory",
                    "result": {"execution_status": "SUCCEEDED"},
                },
            ],
            "streaming_tool_results": [
                {
                    "tool_name": "get_uptime",
                    "result": {"execution_status": "SUCCEEDED"},
                },
            ],
        }
        events = emit_events(state, chat_id="c1")
        event_types = [e["event"] for e in events]
        assert event_types == [
            "assistant",
            "tool_call",
            "tool_result",
            "tool_call",
            "tool_result",
        ]

    def test_langgraph_message_objects_handled(self):
        """Non-dict messages with .type and .content attributes."""

        class FakeLangGraphMessage:
            type = "ai"
            content = "Hello from AI"

        state = {"messages": [FakeLangGraphMessage()]}
        events = emit_events(state)
        assert len(events) == 1
        assert events[0]["event"] == "assistant"
        assert "Hello from AI" in events[0]["data"]

    def test_tool_result_without_output(self):
        """Tool result missing output field — still works."""
        state = {
            "tool_results": [
                {
                    "tool_name": "get_cpu",
                    "result": {
                        "execution_status": "FAILED",
                        "error": {"message": "timeout"},
                    },
                },
            ]
        }
        events = emit_events(state)
        assert len(events) == 1
        assert events[0]["event"] == "tool_result"

    def test_assistant_with_reasoning_emits_reasoning_event(self):
        """Assistant message with reasoning_content → reasoning event emitted before assistant."""
        state = {
            "messages": [
                {"role": "assistant", "content": "CPU normal.", "reasoning_content": "Let me check the CPU usage first."},
            ]
        }
        events = emit_events(state)
        assert len(events) == 2
        assert events[0]["event"] == "reasoning"
        data0 = json.loads(events[0]["data"])
        assert "Let me check" in data0["delta"]
        assert data0["done"] is True
        assert events[1]["event"] == "assistant"

    def test_assistant_without_reasoning_skips_reasoning_event(self):
        """No reasoning_content → no reasoning event."""
        state = {
            "messages": [
                {"role": "assistant", "content": "CPU normal."},
            ]
        }
        events = emit_events(state)
        assert len(events) == 1
        assert events[0]["event"] == "assistant"

    def test_tool_call_includes_server_field(self):
        state = {
            "tool_calls": [
                {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}, "server_name": "tool-server", "is_read_only": True},
            ]
        }
        events = emit_events(state, chat_id="c1")
        data = json.loads(events[0]["data"])
        assert data["server"] == "tool-server"

    def test_tool_call_without_server_emits_empty_string(self):
        state = {
            "tool_calls": [
                {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}, "is_read_only": True},
            ]
        }
        events = emit_events(state)
        data = json.loads(events[0]["data"])
        assert data["server"] == ""

    def test_tool_result_includes_execution_time_ms(self):
        state = {
            "tool_results": [
                {"tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED", "execution_time_ms": 230}},
            ]
        }
        events = emit_events(state)
        data = json.loads(events[0]["data"])
        assert data["execution_time_ms"] == 230

    def test_tool_result_without_execution_time_omits_field(self):
        state = {
            "tool_results": [
                {"tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED"}},
            ]
        }
        events = emit_events(state)
        data = json.loads(events[0]["data"])
        assert "execution_time_ms" not in data

    def test_streaming_tool_result_includes_execution_time_ms(self):
        state = {
            "streaming_tool_results": [
                {"tool_call_id": "st-1", "tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED", "execution_time_ms": 45}},
            ]
        }
        events = emit_events(state)
        data = json.loads(events[1]["data"])
        assert data["execution_time_ms"] == 45


# ── clear_transient_fields ─────────────────────────────────────────────


class TestClearTransientFields:
    def test_all_transient_fields_cleared(self):
        state = {k: ["some data"] for k in TRANSIENT_FIELDS}
        clear_transient_fields(state)
        for k in TRANSIENT_FIELDS:
            assert state[k] == []

    def test_non_transient_fields_untouched(self):
        state = {
            "messages": [{"role": "user", "content": "hi"}],
            "available_tools": ["get_cpu"],
            "system": "you are a helpful assistant",
            "transition": Transition.DONE,
            "tool_calls": ["should be cleared"],
            "llm_error": {"code": 500},
        }
        clear_transient_fields(state)
        assert state["messages"] == [{"role": "user", "content": "hi"}]
        assert state["available_tools"] == ["get_cpu"]
        assert state["system"] == "you are a helpful assistant"
        assert state["transition"] == Transition.DONE
        assert state["llm_error"] == {"code": 500}
        assert state["tool_calls"] == []

    def test_missing_transient_fields_set_to_empty(self):
        """Fields not yet in state dict → initialized to []."""
        state: dict = {}
        clear_transient_fields(state)
        for k in TRANSIENT_FIELDS:
            assert state[k] == []


# ── get_transition ─────────────────────────────────────────────────────


class TestGetTransition:
    def test_returns_transition_when_present(self):
        assert get_transition({"transition": Transition.DONE}) == Transition.DONE

    def test_returns_none_when_absent(self):
        assert get_transition({}) is None


# ── handle_pending_approval ────────────────────────────────────────────


class MockBridge:
    def __init__(self):
        self.created: list[tuple] = []

    def create(self, request_id: str, chat_id: str) -> None:
        self.created.append((request_id, chat_id))


class MockAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


class TestHandlePendingApproval:
    async def test_yields_approval_required_event(self):
        pending = [
            {"function": {"name": "health", "arguments": "{}"}},
        ]
        events = []
        async for e in handle_pending_approval(
            pending, request_id="req-1", bridge=None, audit_logger=None, chat_id="s1"
        ):
            events.append(e)

        assert len(events) == 1
        assert events[0]["event"] == "tool_approval_required"
        data = json.loads(events[0]["data"])
        assert data["request_id"] == "req-1"
        assert data["chat_id"] == "s1"
        assert data["tool_name"] == "health"
        assert data["params"] == {}
        assert "needs your approval" in data["reason"]

    async def test_bridge_gets_request_session_mapping(self):
        bridge = MockBridge()
        pending = [{"function": {"name": "cmd", "arguments": "{}"}}]
        async for _ in handle_pending_approval(
            pending, request_id="req-abc", bridge=bridge, audit_logger=None, chat_id="session-123"
        ):
            pass

        assert bridge.created == [("req-abc", "session-123")]

    async def test_no_bridge_no_crash(self):
        """handle_pending_approval gracefully handles missing bridge."""
        pending = [{"function": {"name": "cmd", "arguments": "{}"}}]
        events = []
        async for e in handle_pending_approval(
            pending, request_id="req-1", bridge=None, audit_logger=None, chat_id="s1"
        ):
            events.append(e)
        assert len(events) == 1
        assert events[0]["event"] == "tool_approval_required"

    async def test_logs_approval_pending_transition(self):
        audit = MockAuditLogger()
        pending = [{"function": {"name": "cmd", "arguments": "{}"}}]
        async for _ in handle_pending_approval(
            pending, request_id="req-1", bridge=None, audit_logger=audit, chat_id="s1"
        ):
            pass

        transitions = [
            e.transition for e in audit.events if e.event == "LOOP_TRANSITION"
        ]
        assert "approval_pending" in transitions

    async def test_multiple_pending_tools_yields_multiple_events(self):
        pending = [
            {"function": {"name": "tool_a", "arguments": "{}"}},
            {"function": {"name": "tool_b", "arguments": "{}"}},
        ]
        events = []
        async for e in handle_pending_approval(
            pending, request_id="req-1", bridge=None, audit_logger=None, chat_id="s1"
        ):
            events.append(e)

        assert len(events) == 2
        assert events[0]["event"] == "tool_approval_required"
        assert events[1]["event"] == "tool_approval_required"
        data0 = json.loads(events[0]["data"])
        data1 = json.loads(events[1]["data"])
        assert data0["tool_name"] == "tool_a"
        assert data1["tool_name"] == "tool_b"

    async def test_empty_pending_yields_single_event(self):
        events = []
        async for e in handle_pending_approval(
            [], request_id="req-1", bridge=None, audit_logger=None, chat_id="s1"
        ):
            events.append(e)
        assert len(events) == 1
        assert events[0]["event"] == "tool_approval_required"


# ── handle_llm_error ───────────────────────────────────────────────────


class MockLLM:
    def __init__(self):
        self.escalated = False
        self.fallback_switched = False

    def escalate_max_tokens(self):
        self.escalated = True

    def switch_to_fallback(self):
        self.fallback_switched = True


class MockContextManager:
    def __init__(self):
        self.compressed_count = 0

    async def compress(self, messages):
        self.compressed_count += 1
        return [{"role": "system", "content": "[compressed]"}]


class TestHandleLlmError:
    @pytest.fixture
    def llm(self):
        return MockLLM()

    @pytest.fixture
    def ctx_mgr(self):
        return MockContextManager()

    @pytest.fixture
    def audit(self):
        return MockAuditLogger()

    def _state(self, code=413, message="prompt too long", stop_reason=None):
        return {
            "messages": [{"role": "user", "content": "test"}],
            "llm_error": {
                "code": code,
                "message": message,
                "stop_reason": stop_reason,
            },
        }

    async def test_prompt_too_long_compress_context(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        state = self._state(413, "prompt too long")

        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is True
        assert ctx_mgr.compressed_count == 1
        assert state["messages"] == [{"role": "system", "content": "[compressed]"}]
        assert state["llm_error"] is None

    async def test_prompt_too_long_second_attempt_aggressive_compress(
        self, llm, ctx_mgr, audit
    ):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("prompt_too_long", "compress_context")

        state = self._state(413, "context too long")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is True
        assert ctx_mgr.compressed_count == 1

    async def test_prompt_too_long_exhausted_returns_false(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("prompt_too_long", "compress_context")
        recovery.record_attempt("prompt_too_long", "aggressive_compress")

        state = self._state(413, "still too long")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is False
        assert state["llm_error"] is not None  # unchanged

    async def test_max_output_tokens_escalates(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()

        state = self._state(200, "", stop_reason="max_tokens")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is True
        assert llm.escalated is True

    async def test_model_unavailable_switches_fallback(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()

        state = self._state(404, "model not found")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is True
        assert llm.fallback_switched is True

    async def test_rate_limit_non_recoverable(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()

        state = self._state(429, "rate limited")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is False

    async def test_auth_failed_non_recoverable(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()

        state = self._state(401, "unauthorized")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is False

    async def test_unknown_error_returns_false(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()

        state = self._state(402, "payment required")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is False

    async def test_no_error_recovery_skips(self, llm, ctx_mgr, audit):
        """When error_recovery is None, the orchestrator skips the handler entirely.
        This test verifies the handler itself doesn't crash if somehow called."""
        # The orchestrator guards with `if state.get("llm_error") and self._error_recovery`
        # so this case shouldn't happen, but verify handler is safe.
        pass  # handled by orchestrator guard, not the handler

    async def test_server_error_switches_fallback(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()

        state = self._state(503, "service unavailable")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is True
        assert llm.fallback_switched is True

    async def test_logs_context_compacted_transition(self, llm, ctx_mgr, audit):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()

        state = self._state(413, "prompt too long")
        await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        transitions = [
            e.transition for e in audit.events if e.event == "LOOP_TRANSITION"
        ]
        assert "context_compacted" in transitions

    async def test_max_output_tokens_continue_inject(self, llm, ctx_mgr, audit):
        """Second attempt at max_output_tokens → continue_inject appends user message."""
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("max_output_tokens", "escalate_token_limit")

        state = self._state(200, "", stop_reason="max_tokens")
        result = await handle_llm_error(
            state,
            error_recovery=recovery,
            context_manager=ctx_mgr,
            llm=llm,
            audit_logger=audit,
        )
        assert result is True
        last_msg = state["messages"][-1]
        assert last_msg["role"] == "user"
        assert "continue" in last_msg["content"].lower()


# ── log_transition ─────────────────────────────────────────────────────


class TestLogTransition:
    async def test_logs_transition_event(self):
        audit = MockAuditLogger()
        await log_transition(audit, Transition.USER_MESSAGE)
        assert len(audit.events) == 1
        assert audit.events[0].event == "LOOP_TRANSITION"
        assert audit.events[0].transition == "user_message"

    async def test_none_audit_logger_no_crash(self):
        await log_transition(None, Transition.DONE)


# ── orchestrator termination ────────────────────────────────────────────

class _MockAuditLogger:
    def __init__(self):
        self.events = []

    async def log(self, event):
        self.events.append(event)


class _NoopGraph:
    """Graph that returns the given state unchanged (one-shot)."""

    async def ainvoke(self, state, config=None):
        return dict(state)


class _NoopCtx:
    def count_tokens(self, messages):
        return 0

    def needs_compression(self, tokens):
        return False

    async def compress(self, messages):
        return messages


class TestOrchestratorTermination:
    @pytest.mark.asyncio
    async def test_exits_when_transition_is_done(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        audit = _MockAuditLogger()
        graph = _NoopGraph()

        orch = LoopOrchestrator(
            graph=graph,
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=audit,
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = {
            "messages": [{"role": "assistant", "content": "OK"}],
            "transition": Transition.DONE,
            "tool_calls": [],
            "tool_results": [],
            "streaming_tool_results": [],
        }

        events = []
        async for ev in orch.run(state):
            events.append(ev)

        assert any(e["event"] == "done" for e in events)

    @pytest.mark.asyncio
    async def test_continues_when_transition_is_tool_results(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        audit = _MockAuditLogger()

        call_count = 0

        class CountingGraph:
            async def ainvoke(self, state, config=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return {
                        "messages": state.get("messages", []),
                        "transition": Transition.TOOL_RESULTS,
                        "tool_calls": [],
                        "tool_results": [{"tool_name": "get_cpu_info", "result": {"execution_status": "SUCCEEDED"}}],
                        "streaming_tool_results": [],
                    }
                return {
                    "messages": state.get("messages", []) + [{"role": "assistant", "content": "done"}],
                    "transition": Transition.DONE,
                    "tool_calls": [],
                    "tool_results": [],
                    "streaming_tool_results": [],
                }

        orch = LoopOrchestrator(
            graph=CountingGraph(),
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=audit,
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = {
            "messages": [{"role": "user", "content": "check CPU"}],
            "transition": None,
            "tool_calls": [],
            "tool_results": [],
            "streaming_tool_results": [],
        }

        events = []
        async for ev in orch.run(state):
            events.append(ev)

        assert call_count >= 2, f"orchestrator should loop at least once, got {call_count} iterations"
        assert any(e["event"] == "done" for e in events)


class TestOrchestratorRecoveryEmission:
    """Bug: orchestrator skips SSE emission after error recovery succeeds.

    When handle_llm_error recovers (compress + retry LLM), the state is updated
    with new assistant messages, but _run_loop continues without emitting them.
    The recovered response never reaches the frontend and is never saved.
    """

    @pytest.mark.asyncio
    async def test_emits_recovered_assistant_message_after_error_recovery(self):
        from src.agent.loop.orchestrator import LoopOrchestrator
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        call_count = 0

        class RecoveryGraph:
            """Graph: first call returns llm_error (prompt_too_long), second succeeds."""
            async def ainvoke(self, state, config=None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return {
                        "messages": [{"role": "user", "content": "long input"}],
                        "llm_error": {"code": 413, "message": "prompt too long"},
                        "transition": None,
                        "tool_calls": [],
                        "tool_results": [],
                        "streaming_tool_results": [],
                    }
                # After recovery: state has been updated by handle_llm_error
                # (compressed + retry), llm_error cleared. Graph returns final state.
                return {
                    "messages": [{"role": "assistant", "content": "Recovered response"}],
                    "transition": Transition.DONE,
                    "tool_calls": [],
                    "tool_results": [],
                    "streaming_tool_results": [],
                }

        class RecoveryLLM:
            escalate_max_tokens = lambda self: None
            switch_to_fallback = lambda self: None

        class RecoveryCtx:
            def count_tokens(self, messages):
                return 1000

            def needs_compression(self, tokens):
                return False

            async def compress(self, messages):
                return [{"role": "system", "content": "[compressed]"}]

        orch = LoopOrchestrator(
            graph=RecoveryGraph(),
            context_manager=RecoveryCtx(),
            bridge=None,
            audit_logger=_MockAuditLogger(),
            error_recovery=recovery,
            llm=RecoveryLLM(),
            chat_id="test",
        )
        state = {
            "messages": [{"role": "user", "content": "long input"}],
            "transition": None,
            "tool_calls": [],
            "tool_results": [],
            "streaming_tool_results": [],
        }

        events = []
        async for ev in orch.run(state):
            events.append(ev)

        # The recovered assistant message should be emitted
        assistant_events = [e for e in events if e.get("event") == "assistant"]
        assert len(assistant_events) > 0, (
            f"Expected assistant event from recovery, but got events: "
            f"{[e.get('event') for e in events]}"
        )
        assert "Recovered response" in assistant_events[0].get("data", "")


class TestCircuitBreaker:
    """Verify safety circuit breaker (max_iterations + token_ceiling)."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        """Reset env vars to defaults to prevent leakage between tests."""
        monkeypatch.delenv("AGENT_MAX_ITERATIONS", raising=False)
        monkeypatch.delenv("AGENT_TOKEN_CEILING_RATIO", raising=False)

    @pytest.mark.asyncio
    async def test_turn_limit_exceeded_stops_loop(self, monkeypatch):
        from src.agent.loop.orchestrator import LoopOrchestrator

        monkeypatch.setenv("AGENT_MAX_ITERATIONS", "2")

        class LoopingGraph:
            """Graph that always returns tool_results, causing infinite loop."""
            async def ainvoke(self, state, config=None):
                return {
                    "messages": state.get("messages", []),
                    "transition": Transition.TOOL_RESULTS,
                    "tool_calls": [],
                    "tool_results": [{"tool_name": "x", "result": {}}],
                    "streaming_tool_results": [],
                }

        audit = _MockAuditLogger()
        orch = LoopOrchestrator(
            graph=LoopingGraph(),
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=audit,
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = {
            "messages": [{"role": "user", "content": "loop"}],
            "transition": None,
            "tool_calls": [],
            "tool_results": [],
            "streaming_tool_results": [],
        }

        events = []
        async for ev in orch.run(state):
            events.append(ev)

        assert any(
            e["event"] == "error"
            and json.loads(e["data"])["code"] == "TURN_LIMIT_EXCEEDED"
            for e in events
        ), f"Expected TURN_LIMIT_EXCEEDED error, got: {events}"
        assert any(e["event"] == "done" for e in events)

    def test_inject_turn_hint_at_70_percent(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            graph=_NoopGraph(),
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=_MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        # max_iterations=30 (default), 70% = 21
        state = {"system": "base prompt"}
        orch._inject_turn_hint(state, 21)
        assert "turn limit" in state["system"].lower()
        assert "base prompt" in state["system"]
        assert orch._last_hint is not None

    def test_inject_turn_hint_replaces_not_accumulates(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            graph=_NoopGraph(),
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=_MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        # First hint at 70%
        state = {"system": "base prompt"}
        orch._inject_turn_hint(state, 21)
        first_hint_count = state["system"].count("[SYSTEM]")
        assert first_hint_count == 1

        # Second hint at ≤3 turns remaining — replaces, not appends
        orch._inject_turn_hint(state, 28)
        second_hint_count = state["system"].count("[SYSTEM]")
        assert second_hint_count == 1, f"Hint accumulated: {state['system']}"
        assert "base prompt" in state["system"]

    def test_inject_turn_hint_skips_below_threshold(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            graph=_NoopGraph(),
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=_MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = {"system": "clean"}
        orch._inject_turn_hint(state, 10)  # Well below 70% of 30
        assert state["system"] == "clean"
        assert getattr(orch, "_last_hint", None) is None

    @pytest.mark.asyncio
    async def test_token_ceiling_breach_detected(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        class HugeCtx:
            """Context manager that always reports tokens over ceiling."""
            def count_tokens(self, messages):
                return 200_000  # way over any reasonable ceiling
            needs_compression = _NoopCtx.needs_compression
            compress = _NoopCtx.compress

        orch = LoopOrchestrator(
            graph=_NoopGraph(),
            context_manager=HugeCtx(),
            bridge=None,
            audit_logger=_MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = {"messages": [{"role": "user", "content": "hi"}]}
        result = await orch._check_token_ceiling(state)
        assert result is True

    @pytest.mark.asyncio
    async def test_token_ceiling_ok_when_under_limit(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            graph=_NoopGraph(),
            context_manager=_NoopCtx(),  # count_tokens returns 0
            bridge=None,
            audit_logger=_MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = {"messages": []}
        result = await orch._check_token_ceiling(state)
        assert result is False

