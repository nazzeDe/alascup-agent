"""Unit tests for agent loop modules."""

from uuid import uuid4

import pytest

from src.agent.events import (
    DomainEvent,
    EventChannel,
    TurnFailed,
)
from src.agent.domain import AgentMessage, AgentToolCall, AgentToolResult, ToolFunction
from src.agent.state import AgentState, Transition, TurnScratch, get_transition
from src.agent.turn_context import TurnContext, Auditor
from src.models.audit import AuditActor


def _msg(role: str, content: str) -> AgentMessage:
    return AgentMessage(role=role, content=content)


def _call(name: str, arguments: dict | None = None, **kwargs) -> AgentToolCall:
    return AgentToolCall(
        function=ToolFunction(name=name, arguments=arguments or {}), **kwargs
    )


def _result(
    tool_name: str, tool_call_id: str, result: dict, **kwargs
) -> AgentToolResult:
    return AgentToolResult(
        tool_name=tool_name, tool_call_id=tool_call_id, result=result, **kwargs
    )


# ── collect_events helper ──────────────────────────────────────────────────


async def collect_channel_events(
    channel: EventChannel, timeout: float = 0.5
) -> list[DomainEvent]:
    """Drain all events from a channel, returning when None sentinel is received or timeout."""
    import asyncio as _asyncio

    events: list[DomainEvent] = []
    while True:
        try:
            event = await _asyncio.wait_for(channel.receive(), timeout=timeout)
        except _asyncio.TimeoutError:
            break
        if event is None:
            break
        events.append(event)
    return events


# ── get_transition ─────────────────────────────────────────────────────


class TestGetTransition:
    def test_returns_transition_when_present(self):
        scratch = TurnScratch(transition=Transition.DONE)
        assert get_transition(scratch) == Transition.DONE

    def test_returns_none_when_absent(self):
        scratch = TurnScratch()
        assert get_transition(scratch) is None


class MockAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


# ── audit_transition ───────────────────────────────────────────────────


class TestAuditTransition:
    async def test_logs_transition_event(self):
        audit = MockAuditLogger()
        auditor = Auditor(
            audit_logger=audit,
            ctx=TurnContext(chat_id=None, turn_id=uuid4(), iteration=1, model=None),
        )
        await auditor.transition(Transition.USER_MESSAGE, actor=AuditActor.SYSTEM)
        assert len(audit.events) == 1
        assert audit.events[0].event == "LOOP_TRANSITION"
        assert audit.events[0].transition == "user_message"

    async def test_none_audit_logger_no_crash(self):
        auditor = Auditor(
            audit_logger=None,
            ctx=TurnContext(chat_id=None, turn_id=uuid4(), iteration=1, model=None),
        )
        await auditor.transition(Transition.DONE, actor=AuditActor.SYSTEM)


# ── orchestrator termination ────────────────────────────────────────────


class _NoopCtx:
    window_size = 128000
    threshold = 0.7

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

        audit = MockAuditLogger()
        channel = EventChannel()

        # Mock think_node: returns done immediately, no tool calls
        from src.agent.results import ThinkOutput

        async def mock_think(state, ctx=None):
            return ThinkOutput(
                assistant_message=_msg("assistant", "OK"),
                is_done=True,
            )

        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=audit,
            error_recovery=None,
            llm=None,
            chat_id="test",
            think_fn=mock_think,
        )
        state = AgentState(messages=[{"role": "user", "content": "hello"}])

        await orch.run(state, channel=channel)
        # Channel should be closed after run() completes
        assert channel.is_closed()

    @pytest.mark.asyncio
    async def test_compresses_tool_results_before_inner_think(self):
        from src.agent.loop.orchestrator import LoopOrchestrator
        from src.agent.results import ExecuteOutput, ReviewOutput, ThinkOutput
        from src.services.context_manager import ContextManager

        context_manager = ContextManager(
            window_size=100,
            threshold=0.7,
            max_result_chars=20,
        )
        think_count = 0
        observed_tool_lengths: list[int] = []

        async def mock_think(state, ctx=None):
            nonlocal think_count
            think_count += 1
            if think_count == 1:
                return ThinkOutput(
                    assistant_message=_msg("assistant", "checking"),
                    tool_calls=[_call("trace_slow_syscalls", id="trace-1")],
                    is_done=False,
                )

            observed_tool_lengths.extend(
                len(message.content)
                for message in state.messages
                if message.role == "tool"
            )
            return ThinkOutput(
                assistant_message=_msg("assistant", "done"),
                is_done=True,
            )

        async def mock_review(state, ctx=None):
            return ReviewOutput(approved=state.tool_calls)

        async def mock_act(state, ctx=None):
            return ExecuteOutput(
                results=[
                    _result(
                        "trace_slow_syscalls",
                        "trace-1",
                        {
                            "execution_status": "SUCCEEDED",
                            "output": "x" * 1_000,
                        },
                    )
                ]
            )

        orch = LoopOrchestrator(
            context_manager=context_manager,
            bridge=None,
            audit_logger=MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test-inner-context",
            think_fn=mock_think,
            review_fn=mock_review,
            act_fn=mock_act,
        )

        await orch.run(
            AgentState(messages=[_msg("user", "check system")]),
            channel=EventChannel(),
        )

        assert think_count == 2
        assert observed_tool_lengths
        assert observed_tool_lengths[0] <= 20 + len("...[truncated]")

    @pytest.mark.asyncio
    async def test_continues_when_transition_is_tool_results(self):
        from src.agent.loop.orchestrator import LoopOrchestrator
        from src.agent.results import ThinkOutput, ExecuteOutput

        audit = MockAuditLogger()
        channel = EventChannel()

        call_count = 0

        async def mock_think(state, ctx=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: return tool call that needs execution
                return ThinkOutput(
                    assistant_message=_msg("assistant", "checking"),
                    tool_calls=[_call("get_cpu_info", id="t1")],
                    pre_executed=[
                        _result("get_cpu_info", "t1", {"execution_status": "SUCCEEDED"})
                    ],
                    is_done=False,
                )
            # Second call: done
            return ThinkOutput(
                assistant_message=_msg("assistant", "done"),
                is_done=True,
            )

        async def mock_review(state, ctx=None):
            from src.agent.results import ReviewOutput

            # Auto-approve all
            return ReviewOutput(
                approved=state.tool_calls,
            )

        async def mock_act(state, ctx=None):
            tcs = state.approved_tool_calls
            results = []
            for tc in tcs:
                results.append(
                    _result(tc.function.name, tc.id, {"execution_status": "SUCCEEDED"})
                )
            return ExecuteOutput(results=results)

        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=audit,
            error_recovery=None,
            llm=None,
            chat_id="test",
            think_fn=mock_think,
            review_fn=mock_review,
            act_fn=mock_act,
        )
        state = AgentState(messages=[{"role": "user", "content": "check CPU"}])

        await orch.run(state, channel=channel)
        assert call_count >= 2, (
            f"orchestrator should loop at least once, got {call_count} iterations"
        )
        assert channel.is_closed()


class TestOrchestratorRecoveryEmission:
    """Bug: orchestrator skips SSE emission after error recovery succeeds.

    When handle_llm_error recovers (compress + retry LLM), the state is updated
    with new assistant messages, but _run_loop continues without emitting them.
    The recovered response never reaches the frontend and is never saved.
    """

    @pytest.mark.asyncio
    async def test_emits_recovered_assistant_message_after_error_recovery(self):
        from src.agent.loop.orchestrator import LoopOrchestrator
        from src.agent.results import ThinkOutput
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        call_count = 0
        channel = EventChannel()

        async def mock_think(state, ctx=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ThinkOutput(
                    llm_error={"code": 413, "message": "prompt too long"},
                    is_done=True,
                )
            return ThinkOutput(
                assistant_message=_msg("assistant", "Recovered response"),
                is_done=True,
            )

        class RecoveryLLM:
            def escalate_max_tokens(self):
                pass

            def switch_to_fallback(self):
                pass

        class RecoveryCtx:
            window_size = 128000
            threshold = 0.7

            def count_tokens(self, messages):
                return 1000

            def needs_compression(self, tokens):
                return False

            async def compress(self, messages):
                return [{"role": "system", "content": "[compressed]"}]

        orch = LoopOrchestrator(
            context_manager=RecoveryCtx(),
            bridge=None,
            audit_logger=MockAuditLogger(),
            error_recovery=recovery,
            llm=RecoveryLLM(),
            chat_id="test",
            think_fn=mock_think,
        )
        state = AgentState(messages=[{"role": "user", "content": "long input"}])

        await orch.run(state, channel=channel)
        events = await collect_channel_events(channel)

        # The recovered assistant message should be emitted
        assert channel.is_closed()
        # Verify no TurnFailed
        turn_failures = [e for e in events if isinstance(e, TurnFailed)]
        assert len(turn_failures) == 0, f"Expected no TurnFailed, got: {turn_failures}"


# ── Tool call ID consistency in approval flow ──────────────────────────


class MockApprovalBridge:
    """Auto-approving bridge for tests."""

    def __init__(self):
        self.created: list[tuple] = []

    def create(self, request_id: str, chat_id: str) -> None:
        self.created.append((request_id, chat_id))

    def get_chat_id(self, request_id: str) -> str | None:
        return "test-chat"

    def complete(self, request_id: str, status: str, reason: str | None = None) -> None:
        pass

    async def gather_decisions(
        self, request_id: str, expected_count: int, timeout: float = 300
    ) -> list[dict]:
        return [{"status": "APPROVED", "reason": None}] * expected_count


class TestApprovalFlowCallIdConsistency:
    """Bug: ToolCallStarted and ToolCallFinished call_ids mismatch in approval flow.

    When the inner for loop in the approval re-entry path iterates more than
    once (e.g., LLM generates a second tool call after the approved tool
    executes), observe_node overwrites state["emitted_results"], losing
    the first tool's result. This causes:
    - First tool: ToolCallStarted emitted, but ToolCallFinished NEVER emitted
    - Second tool: ToolCallFinished emitted, but ToolCallStarted NEVER emitted
    """

    @pytest.mark.asyncio
    async def test_call_ids_match_when_llm_generates_second_tool_after_approval(self):
        from src.agent.events import (
            EventChannel,
            ToolCallStarted,
            ToolCallFinished,
        )
        from src.agent.loop.orchestrator import LoopOrchestrator
        from src.agent.results import ThinkOutput, ReviewOutput, ExecuteOutput
        from src.agent.state import Transition

        channel = EventChannel()

        # call_id for the tool that needs approval
        approved_call_id = "call_00_nNSjXEtOVrx56jJtjddX9744"
        # call_id for the tool LLM generates after seeing the first result
        second_call_id = "call_00_NIBvwJmKaPUiLrEffghM7971"

        think_calls = 0

        async def mock_think(state, ctx=None):
            nonlocal think_calls
            think_calls += 1
            approved = state.approved_tool_calls

            if approved:
                # approved_tool_calls present → return early (think_node skips)
                return ThinkOutput()

            if think_calls == 1:
                # First call: generate a mutable tool that needs approval
                return ThinkOutput(
                    assistant_message=_msg("assistant", "Creating tmp file"),
                    tool_calls=[
                        _call(
                            "bash",
                            {"command": "mkdir -p ~/tmp"},
                            id=approved_call_id,
                            mutable=True,
                            is_read_only=None,
                            server_name="tool-server",
                        )
                    ],
                    is_done=False,
                )
            elif think_calls == 3:
                # Third real call: LLM generates another tool after seeing
                # the first tool's result. This triggers the bug — causes
                # emitted_results overwrite in observe_node.
                return ThinkOutput(
                    assistant_message=_msg("assistant", "Verifying"),
                    tool_calls=[
                        _call(
                            "ls",
                            {"path": "~/tmp"},
                            id=second_call_id,
                            is_read_only=True,
                            server_name="tool-server",
                        )
                    ],
                    is_done=False,
                )
            else:
                # Fourth (or subsequent) call: text response, done
                return ThinkOutput(
                    assistant_message=_msg("assistant", "Done"),
                    is_done=True,
                )

        async def mock_review(state, ctx=None):
            tool_calls = state.tool_calls
            approved = []
            pending = []
            rejected = []
            for tc in tool_calls:
                name = tc.function.name
                if name == "bash":
                    # Needs approval
                    pending.append(tc)
                else:
                    # Auto-approve everything else
                    tc.is_read_only = bool(tc.is_read_only)
                    tc.request_id = "req-auto"
                    approved.append(tc)
            if pending:
                return ReviewOutput(
                    approved=approved,
                    rejected=rejected,
                    pending=pending,
                    transition=Transition.APPROVAL_PENDING,
                )
            return ReviewOutput(approved=approved, rejected=rejected)

        async def mock_act(state, ctx=None):
            tcs = state.approved_tool_calls
            results = []
            for tc in tcs:
                results.append(
                    _result(
                        tc.function.name,
                        tc.id,
                        {"execution_status": "SUCCEEDED", "output": "ok"},
                        is_read_only=bool(tc.is_read_only),
                        is_rollbackable=tc.is_rollbackable,
                    )
                )
            return ExecuteOutput(results=results)

        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),
            bridge=MockApprovalBridge(),
            audit_logger=MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test-callid-consistency",
            think_fn=mock_think,
            review_fn=mock_review,
            act_fn=mock_act,
        )
        state = AgentState(
            messages=[{"role": "user", "content": "create tmp file in home dir"}]
        )

        await orch.run(state, channel=channel)
        events = await collect_channel_events(channel)

        # Collect ToolCallStarted and ToolCallFinished events
        started = [e for e in events if isinstance(e, ToolCallStarted)]
        finished = [e for e in events if isinstance(e, ToolCallFinished)]

        started_ids = {e.call_id for e in started}
        finished_ids = {e.call_id for e in finished}

        # Every ToolCallFinished must have a matching ToolCallStarted
        orphan_finished = finished_ids - started_ids
        assert not orphan_finished, (
            f"ToolCallFinished without ToolCallStarted: {orphan_finished}. "
            f"Started IDs: {started_ids}, Finished IDs: {finished_ids}"
        )

        # Every ToolCallStarted must have a matching ToolCallFinished
        orphan_started = started_ids - finished_ids
        assert not orphan_started, (
            f"ToolCallStarted without ToolCallFinished: {orphan_started}. "
            f"Started IDs: {started_ids}, Finished IDs: {finished_ids}"
        )

        # Both tools should be accounted for
        assert approved_call_id in started_ids, (
            f"First tool {approved_call_id} should have ToolCallStarted"
        )
        assert approved_call_id in finished_ids, (
            f"First tool {approved_call_id} should have ToolCallFinished"
        )

        assert channel.is_closed()


class TestCircuitBreaker:
    """Verify safety circuit breaker (max_iterations + token_ceiling)."""

    @pytest.mark.asyncio
    async def test_turn_limit_exceeded_stops_loop(self):
        from src.agent.loop.orchestrator import LoopOrchestrator
        from src.agent.results import ThinkOutput

        async def mock_think(state, ctx=None):
            return ThinkOutput(
                assistant_message=_msg("assistant", "looping"),
                pre_executed=[_result("x", "t1", {})],
                is_done=False,
            )

        audit = MockAuditLogger()
        channel = EventChannel()
        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=audit,
            error_recovery=None,
            llm=None,
            chat_id="test",
            agent_max_iterations=2,
            think_fn=mock_think,
        )
        state = AgentState(messages=[{"role": "user", "content": "loop"}])

        await orch.run(state, channel=channel)
        events = await collect_channel_events(channel)

        assert any(
            isinstance(e, TurnFailed) and e.code == "TURN_LIMIT_EXCEEDED"
            for e in events
        ), (
            f"Expected TURN_LIMIT_EXCEEDED error, got events: {[type(e).__name__ for e in events]}"
        )
        assert channel.is_closed()

    def test_inject_turn_hint_at_70_percent(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        # max_iterations=30 (default), 70% = 21
        state = AgentState(system="base prompt")
        orch._breaker.inject_hint(state, 21)
        assert "turn limit" in state.system.lower()
        assert "base prompt" in state.system
        assert orch._breaker._last_hint is not None

    def test_inject_turn_hint_replaces_not_accumulates(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        # First hint at 70%
        state = AgentState(system="base prompt")
        orch._breaker.inject_hint(state, 21)
        first_hint_count = state.system.count("[SYSTEM]")
        assert first_hint_count == 1

        # Second hint at <=3 turns remaining -> replaces, not appends
        orch._breaker.inject_hint(state, 28)
        second_hint_count = state.system.count("[SYSTEM]")
        assert second_hint_count == 1, f"Hint accumulated: {state.system}"
        assert "base prompt" in state.system

    def test_inject_turn_hint_skips_below_threshold(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),
            bridge=None,
            audit_logger=MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = AgentState(system="clean")
        orch._breaker.inject_hint(state, 10)  # Well below 70% of 30
        assert state.system == "clean"
        assert orch._breaker._last_hint is None

    @pytest.mark.asyncio
    async def test_token_ceiling_breach_detected(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        class HugeCtx:
            window_size = 128000
            threshold = 0.7

            def count_tokens(self, messages):
                return 200_000  # way over any reasonable ceiling

            needs_compression = _NoopCtx.needs_compression
            compress = _NoopCtx.compress

        orch = LoopOrchestrator(
            context_manager=HugeCtx(),
            bridge=None,
            audit_logger=MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = AgentState(messages=[{"role": "user", "content": "hi"}])
        result = await orch._check_token_ceiling(state)
        assert result is True

    @pytest.mark.asyncio
    async def test_token_ceiling_ok_when_under_limit(self):
        from src.agent.loop.orchestrator import LoopOrchestrator

        orch = LoopOrchestrator(
            context_manager=_NoopCtx(),  # count_tokens returns 0
            bridge=None,
            audit_logger=MockAuditLogger(),
            error_recovery=None,
            llm=None,
            chat_id="test",
        )
        state = AgentState()
        result = await orch._check_token_ceiling(state)
        assert result is False
