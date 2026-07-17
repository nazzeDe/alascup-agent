"""Tests for the chat turn cycle. Covers AG-001, AG-003~AG-007."""

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.agent.loop.orchestrator import LoopOrchestrator
from src.chat_turn import ChatTurn
from src.models.message import Message, MessageType
from src.security.input_safety import InputSafetyGate


class MockLLM:
    def __init__(self, responses: list[dict] | None = None):
        self.responses = responses or [{"content": "no tools needed"}]
        self._idx = 0

    async def generate_stream(self, messages, tools=None, system=None, chat_id=None):
        resp = self.responses[min(self._idx, len(self.responses) - 1)]
        self._idx += 1

        if "error" in resp:
            err = resp["error"]
            yield {"event": "error", "data": json.dumps(err)}
            yield {"event": "done", "data": "{}"}
            return

        content = resp.get("content", "")
        tool_calls = resp.get("tool_calls")

        if content:
            yield {"event": "assistant", "data": json.dumps({"delta": content})}
        if tool_calls:
            for tc in tool_calls:
                yield {"event": "tool_call", "data": json.dumps(tc)}
        yield {"event": "done", "data": "{}"}

    def escalate_max_tokens(self) -> None:
        pass

    def switch_to_fallback(self) -> None:
        pass


class MockClassifier:
    def __init__(self, is_read_only=True, threshold_risky=False):
        self._readonly = is_read_only
        self._threshold_risky = threshold_risky

    async def classify(self, tool_name, params):
        if self._threshold_risky and tool_name in (
            "restart_service",
            "delete_logs",
            "reboot_system",
        ):
            return {"is_read_only": False, "is_rollbackable": False}
        return {"is_read_only": self._readonly, "is_rollbackable": True}


class MockRuleEngine:
    @staticmethod
    def evaluate(tool_name, is_read_only, is_rollbackable):
        if "blacklist" in tool_name:
            return "REJECT"
        if is_read_only:
            return "AUTO_APPROVE"
        return "NEEDS_APPROVAL"


class MockExecutor:
    def __init__(self):
        self.calls: list[dict] = []

    async def execute(
        self,
        tool_name,
        arguments,
        *,
        server_name=None,
        approval_status=None,
        request_id=None,
        **kwargs,
    ):
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return {"execution_status": "SUCCEEDED", "output": f"result of {tool_name}"}

    async def execute_parallel(self, calls: list[dict]) -> list[dict]:
        results = []
        for c in calls:
            self.calls.append(
                {"tool_name": c["tool_name"], "arguments": c.get("arguments", {})}
            )
            results.append(
                {
                    "tool_call_id": c.get("call_id", ""),
                    "result": {
                        "execution_status": "SUCCEEDED",
                        "output": f"result of {c['tool_name']}",
                    },
                }
            )
        return results

    async def classify(self, tool_name, params, server_name=""):
        return {"is_read_only": True, "is_rollbackable": True}

    def list_tools(self) -> list[dict]:
        return []


class MockContextManager:
    window_size = 128000
    threshold = 0.7

    def count_tokens(self, messages):
        return len(str(messages))

    def needs_compression(self, tokens):
        return False

    async def compress(self, messages):
        return messages


class MockAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


class MockSessionManager:
    def __init__(self):
        self.sessions: dict[UUID, dict] = {}

    async def create_session(self):
        sid = uuid4()
        from src.models.session import ChatSession

        session = ChatSession(
            id=sid,
            title=None,
            messages=[],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.sessions[sid] = {"session": session}
        return session

    async def get_session(self, chat_id: UUID):
        if chat_id not in self.sessions:
            return await self.create_session()
        return self.sessions[chat_id]["session"]

    async def add_message(self, chat_id: UUID, msg: Message):
        if chat_id in self.sessions:
            self.sessions[chat_id].setdefault("messages", []).append(msg)

    async def set_title(self, chat_id: UUID, title: str):
        if chat_id in self.sessions:
            self.sessions[chat_id]["session"].title = title

    async def list_sessions(self):
        return [v["session"] for v in self.sessions.values()]

    async def delete_session(self, chat_id: UUID):
        self.sessions.pop(chat_id, None)

    async def add_tool_call(self, chat_id: UUID, call):
        return uuid4()

    async def update_tool_call(self, tid, cid, **kw):
        pass


class MockPromptManager:
    def build_system_prompt(self) -> str:
        return "You are a helpful assistant."


pytestmark = pytest.mark.asyncio


def _tools(*names, is_read_only=True, mutable=False, server="tool-server"):
    """Build available_tools list from bare names."""
    return [
        {
            "name": n,
            "server_name": server,
            "mutable": mutable,
            "is_read_only": is_read_only,
        }
        for n in names
    ]


async def _collect_events(gen) -> list[tuple]:
    """Collect events from async generator preserving wire format."""
    events = []
    async for e in gen:
        events.append((e["event"], e["data"]))
    return events


async def _run_turn_and_collect(
    llm,
    executor,
    context_manager,
    audit,
    bridge,
    rule_engine,
    session_mgr=None,
    prompt_mgr=None,
    user_message="test",
    chat_id=None,
    agent_max_iterations=30,
) -> list[tuple]:
    """Helper: build ChatTurn + SSEStream, collect wire events, return [(event, data), ...].

    If bridge and rule_engine are needed for approval tests, the orchestrator is rebuilt.
    """
    if session_mgr is None:
        session_mgr = MockSessionManager()
    if prompt_mgr is None:
        prompt_mgr = MockPromptManager()

    def orchestrator_builder():
        return LoopOrchestrator(
            context_manager=context_manager,
            bridge=bridge,
            audit_logger=audit,
            error_recovery=None,
            llm=llm,
            chat_id=chat_id or "",
            agent_max_iterations=agent_max_iterations,
            tool_executor=executor,
            rule_engine=rule_engine,
        )

    turn = ChatTurn(
        user_message=user_message,
        chat_id=chat_id,
        session_manager=session_mgr,
        prompt_manager=prompt_mgr,
        orchestrator_builder=orchestrator_builder,
        tool_executor=executor,
        input_safety_gate=InputSafetyGate([]),
        audit_logger=audit,
    )

    from src.sse_stream import SSEStream

    stream = SSEStream(turn=turn)

    return await _collect_events(stream)


@pytest.fixture
def llm():
    return MockLLM()


@pytest.fixture
def rule_engine():
    return MockRuleEngine()


@pytest.fixture
def executor():
    return MockExecutor()


@pytest.fixture
def context_manager():
    return MockContextManager()


@pytest.fixture
def audit():
    return MockAuditLogger()


@pytest.fixture
def bridge():
    from src.security.pending import ApprovalBridge

    return ApprovalBridge()


class TestChatTurnBasic:
    """AG-001: ReAct basic cycle."""

    async def test_text_only_response(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        llm.responses = [{"content": "CPU is normal.", "tool_calls": None}]
        events = await _run_turn_and_collect(
            llm=llm,
            executor=executor,
            context_manager=context_manager,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        event_types = [e[0] for e in events]
        # session_init first, done last
        assert event_types[0] == "session_init"
        assert event_types[-1] == "done"
        assert "assistant" in event_types

    async def test_tool_call_auto_approve_then_done(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        """Readonly tool -> AUTO_APPROVE -> execute -> result -> LLM concludes -> done."""
        executor.list_tools = lambda: _tools("get_cpu")
        llm.responses = [
            {
                "content": "",
                "tool_calls": [
                    {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}},
                ],
            },
            {"content": "CPU is 85%.", "tool_calls": None},
        ]
        events = await _run_turn_and_collect(
            llm=llm,
            executor=executor,
            context_manager=context_manager,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        event_types = [e[0] for e in events]
        assert event_types[0] == "session_init"
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "assistant" in event_types
        assert event_types[-1] == "done"


class TestChatTurnHighRisk:
    """AG-004: High-risk tool approval."""

    async def test_high_risk_yields_approval_required_and_resumes(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        """High-risk tool_call -> pending_approval -> approval_required -> resolve -> continue."""

        class RiskyExecutor(MockExecutor):
            def list_tools(self):
                return _tools("restart_service", is_read_only=False)

            async def classify(self, tool_name, params, server_name=""):
                return {"is_read_only": False, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                results = []
                for c in calls:
                    self.calls.append(
                        {
                            "tool_name": c["tool_name"],
                            "arguments": c.get("arguments", {}),
                        }
                    )
                    results.append(
                        {
                            "tool_call_id": c.get("call_id", ""),
                            "result": {
                                "execution_status": "SUCCEEDED",
                                "output": f"result of {c['tool_name']}",
                            },
                        }
                    )
                return results

        risky = RiskyExecutor()
        llm.responses = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "function": {"name": "restart_service", "arguments": "{}"},
                    },
                ],
            },
            {"content": "Service restarted.", "tool_calls": None},
        ]

        # Build whole flow manually to intercept approval_required
        def orch_builder():
            return LoopOrchestrator(
                context_manager=context_manager,
                bridge=bridge,
                audit_logger=audit,
                error_recovery=None,
                llm=llm,
                chat_id="",
                tool_executor=risky,
                rule_engine=rule_engine,
            )

        turn = ChatTurn(
            user_message="Restart nginx.",
            chat_id=None,
            session_manager=MockSessionManager(),
            prompt_manager=MockPromptManager(),
            orchestrator_builder=orch_builder,
            tool_executor=risky,
            input_safety_gate=self._configured_input_safety_gate(),
            audit_logger=audit,
        )

        from src.sse_stream import SSEStream

        stream = SSEStream(turn=turn)
        events: list[tuple] = []
        async for e in stream:
            events.append((e["event"], e["data"]))
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED", chat_id=data["chat_id"])

        event_types = [e[0] for e in events]
        assert event_types[0] == "session_init"
        assert "tool_approval_required" in event_types
        assert "assistant" in event_types
        assert event_types[-1] == "done"
        assert risky.calls

    @staticmethod
    def _configured_input_safety_gate():
        from pathlib import Path

        from src.config.loader import load_rules_config

        rules_path = Path(__file__).parents[2] / "config" / "rules.json"
        return InputSafetyGate(load_rules_config(rules_path).input_safety)


class TestChatTurnTransitionTracking:
    """AG-006: transition tracking writes to audit log."""

    async def test_transitions_logged_to_audit(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        llm.responses = [{"content": "done.", "tool_calls": None}]
        await _run_turn_and_collect(
            llm=llm,
            executor=executor,
            context_manager=context_manager,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        transition_events = [e for e in audit.events if e.event == "LOOP_TRANSITION"]
        transitions = [e.transition for e in transition_events]
        # done transition is logged by orchestrator; user_message was previously in Query.run()
        assert "done" in transitions


class TestChatTurnExit:
    """Exit conditions: LLM produces text without tool_calls -> transition=done -> exit."""

    async def test_exits_when_llm_produces_text_without_tool_calls(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        llm.responses = [{"content": "All good.", "tool_calls": None}]
        events = await _run_turn_and_collect(
            llm=llm,
            executor=executor,
            context_manager=context_manager,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        assert events[-1][0] == "done"

    async def test_continues_when_tool_calls_present(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        """Tool calls should keep loop alive — LLM reasons on tool_result."""
        executor.list_tools = lambda: _tools("get_cpu")
        llm.responses = [
            {
                "content": "",
                "tool_calls": [
                    {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}},
                ],
            },
            {"content": "Result analyzed.", "tool_calls": None},
        ]
        events = await _run_turn_and_collect(
            llm=llm,
            executor=executor,
            context_manager=context_manager,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        assert events[-1][0] == "done"
        assert executor.calls


class TestChatTurnConcurrent:
    """AG-003, AG-004: concurrent tool_call."""

    async def test_concurrent_readonly_tools_executed(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        """AG-003: LLM returns 2 readonly tool_calls -> parallel execution -> 2 results."""
        executor.list_tools = lambda: _tools("get_cpu", "get_memory")
        llm.responses = [
            {
                "content": "",
                "tool_calls": [
                    {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}},
                    {
                        "id": "tc-2",
                        "function": {"name": "get_memory", "arguments": "{}"},
                    },
                ],
            },
            {"content": "Both checked.", "tool_calls": None},
        ]
        events = await _run_turn_and_collect(
            llm=llm,
            executor=executor,
            context_manager=context_manager,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        event_types = [e[0] for e in events]
        assert event_types.count("tool_call") == 2
        assert event_types.count("tool_result") == 2
        assert len(executor.calls) == 2
        assert events[-1][0] == "done"

    async def test_mixed_readonly_highrisk(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        """AG-004: readonly + high-risk -> readonly pre-executed, high-risk needs approval."""

        class MixedExecutor(MockExecutor):
            def __init__(self):
                super().__init__()

            def list_tools(self):
                return _tools("get_cpu") + _tools("restart_service", is_read_only=False)

            async def classify(self, tool_name, params, server_name=""):
                if tool_name == "restart_service":
                    return {"is_read_only": False, "is_rollbackable": True}
                return {"is_read_only": True, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                results = []
                for c in calls:
                    self.calls.append(
                        {
                            "tool_name": c["tool_name"],
                            "arguments": c.get("arguments", {}),
                        }
                    )
                    results.append(
                        {
                            "tool_call_id": c.get("call_id", ""),
                            "result": {
                                "execution_status": "SUCCEEDED",
                                "output": f"result of {c['tool_name']}",
                            },
                        }
                    )
                return results

        risky = MixedExecutor()
        llm.responses = [
            {
                "content": "",
                "tool_calls": [
                    {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}},
                    {
                        "id": "tc-2",
                        "function": {"name": "restart_service", "arguments": "{}"},
                    },
                ],
            },
            {"content": "Handled.", "tool_calls": None},
        ]

        def orch_builder():
            return LoopOrchestrator(
                context_manager=context_manager,
                bridge=bridge,
                audit_logger=audit,
                error_recovery=None,
                llm=llm,
                chat_id="",
                tool_executor=risky,
                rule_engine=rule_engine,
            )

        turn = ChatTurn(
            user_message="check and restart",
            chat_id=None,
            session_manager=MockSessionManager(),
            prompt_manager=MockPromptManager(),
            orchestrator_builder=orch_builder,
            tool_executor=risky,
            input_safety_gate=InputSafetyGate([]),
            audit_logger=audit,
        )

        from src.sse_stream import SSEStream

        stream = SSEStream(turn=turn)
        events: list[tuple] = []
        async for e in stream:
            events.append((e["event"], e["data"]))
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED", chat_id=data["chat_id"])

        event_types = [e[0] for e in events]
        assert "tool_approval_required" in event_types
        assert "tool_result" in event_types
        assert events[-1][0] == "done"
        assert len(risky.calls) == 2


class TestChatTurnStreamingExecution:
    """AG-007: Streaming tool execution — readonly tools dispatched in think phase."""

    async def test_readonly_tools_pre_executed_skip_review_act(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        """Readonly tool_calls pre-executed by think_node, skip review/act."""
        executor.list_tools = lambda: _tools("get_cpu", "get_memory")
        llm.responses = [
            {
                "content": "",
                "tool_calls": [
                    {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}},
                    {
                        "id": "tc-2",
                        "function": {"name": "get_memory", "arguments": "{}"},
                    },
                ],
            },
            {"content": "Both checked.", "tool_calls": None},
        ]
        events = await _run_turn_and_collect(
            llm=llm,
            executor=executor,
            context_manager=context_manager,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        event_types = [e[0] for e in events]
        assert event_types.count("tool_call") == 2
        assert event_types.count("tool_result") == 2
        assert len(executor.calls) >= 2
        assert events[-1][0] == "done"


class _FakeCompressingContextManager:
    """Compression always triggers, for error recovery tests."""

    window_size = 128000
    threshold = 0.7

    def count_tokens(self, messages):
        return 1000

    def needs_compression(self, tokens):
        return True

    async def compress(self, messages):
        return [{"role": "system", "content": "[summary] compressed"}]


class TestChatTurnErrorRecovery:
    """EH-003, EH-004, EH-005: LLM error recovery chain."""

    @pytest.fixture
    def recovery_ctx(self):
        return _FakeCompressingContextManager()

    async def _run_recovery_turn(
        self, llm, executor, context_manager, audit, bridge, rule_engine, **kw
    ):
        from src.services.error_recovery import ErrorRecovery

        session_mgr = MockSessionManager()
        prompt_mgr = MockPromptManager()
        recovery = ErrorRecovery()

        def orch_builder():
            return LoopOrchestrator(
                context_manager=context_manager,
                bridge=bridge,
                audit_logger=audit,
                error_recovery=recovery,
                llm=llm,
                chat_id="",
                tool_executor=executor,
                rule_engine=rule_engine,
            )

        turn = ChatTurn(
            user_message=kw.get("user_message", "test"),
            chat_id=None,
            session_manager=session_mgr,
            prompt_manager=prompt_mgr,
            orchestrator_builder=orch_builder,
            tool_executor=executor,
            input_safety_gate=InputSafetyGate([]),
            audit_logger=audit,
        )

        from src.sse_stream import SSEStream

        stream = SSEStream(turn=turn)
        return await _collect_events(stream)

    async def test_prompt_too_long_compress_then_retry(
        self, llm, executor, recovery_ctx, audit, bridge, rule_engine
    ):
        """EH-003: LLM returns prompt_too_long -> compress -> retry -> success."""
        llm.responses = [
            {"error": {"code": 413, "message": "prompt too long"}},
            {"content": "recovered response", "tool_calls": None},
        ]
        events = await self._run_recovery_turn(
            llm=llm,
            executor=executor,
            context_manager=recovery_ctx,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        assert events[-1][0] == "done"
        transitions = [
            e.transition for e in audit.events if e.event == "LOOP_TRANSITION"
        ]
        assert "context_compacted" in transitions

    async def test_prompt_too_long_exhausted_yields_error(
        self, llm, executor, recovery_ctx, audit, bridge, rule_engine
    ):
        """EH-003 recovery chain exhausted -> error event -> error_exit."""
        llm.responses = [
            {"error": {"code": 413, "message": "prompt too long"}},
            {"error": {"code": 413, "message": "prompt too long"}},
            {"error": {"code": 413, "message": "prompt too long"}},
        ]
        events = await self._run_recovery_turn(
            llm=llm,
            executor=executor,
            context_manager=recovery_ctx,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        event_types = [e[0] for e in events]
        assert "error" in event_types
        transitions = [
            e.transition for e in audit.events if e.event == "LOOP_TRANSITION"
        ]
        assert "error_exit" in transitions

    async def test_non_recoverable_error_surfaces_immediately(
        self, llm, executor, recovery_ctx, audit, bridge, rule_engine
    ):
        """Non-recoverable error (rate_limit) -> direct error, no retry."""
        llm.responses = [
            {"error": {"code": 429, "message": "rate limited"}},
        ]
        events = await self._run_recovery_turn(
            llm=llm,
            executor=executor,
            context_manager=recovery_ctx,
            audit=audit,
            bridge=bridge,
            rule_engine=rule_engine,
        )
        assert events[-1][0] == "done"
        event_types_before_done = [e[0] for e in events[:-1]]
        assert "error" in event_types_before_done
        transitions = [
            e.transition for e in audit.events if e.event == "LOOP_TRANSITION"
        ]
        assert "error_exit" in transitions


class TestAgentCrashSendsDone:
    """AGENT_CRASH path must send SSE done event."""

    async def test_chat_agent_crash_yields_error_then_done(
        self,
        executor,
        rule_engine,
        audit,
        context_manager,
        bridge,
    ):
        """When LLM raises exception, SSE stream must end with done."""
        from src.sse_stream import SSEStream

        class RaisingLLM:
            async def generate_stream(
                self, messages, tools=None, system=None, chat_id=None
            ):
                if messages is None:
                    yield {}
                raise RuntimeError("simulated LLM crash")

            def escalate_max_tokens(self):
                pass

            def switch_to_fallback(self):
                pass

        session_mgr = MockSessionManager()
        prompt_mgr = MockPromptManager()

        def orch_builder():
            return LoopOrchestrator(
                context_manager=context_manager,
                bridge=bridge,
                audit_logger=audit,
                error_recovery=None,
                llm=RaisingLLM(),
                chat_id="",
                tool_executor=executor,
                rule_engine=rule_engine,
            )

        turn = ChatTurn(
            user_message="test message",
            chat_id=None,
            session_manager=session_mgr,
            prompt_manager=prompt_mgr,
            orchestrator_builder=orch_builder,
            tool_executor=executor,
            input_safety_gate=InputSafetyGate([]),
            audit_logger=audit,
        )

        stream = SSEStream(turn=turn)
        events = await _collect_events(stream)

        event_types = [e[0] for e in events]
        assert "error" in event_types, f"Expected error event, got: {event_types}"
        assert event_types[-1] == "done", (
            f"Expected final done event, got: {event_types}"
        )

    async def test_chat_normal_path_ends_with_done(
        self,
        llm,
        executor,
        rule_engine,
        audit,
        context_manager,
        bridge,
    ):
        """Normal path must also end with done (regression test)."""
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "Hello!", "tool_calls": None}]

        session_mgr = MockSessionManager()
        prompt_mgr = MockPromptManager()

        def orch_builder():
            return LoopOrchestrator(
                context_manager=context_manager,
                bridge=bridge,
                audit_logger=audit,
                error_recovery=None,
                llm=llm,
                chat_id="",
                tool_executor=executor,
                rule_engine=rule_engine,
            )

        turn = ChatTurn(
            user_message="hi",
            chat_id=None,
            session_manager=session_mgr,
            prompt_manager=prompt_mgr,
            orchestrator_builder=orch_builder,
            tool_executor=executor,
            input_safety_gate=InputSafetyGate([]),
            audit_logger=audit,
        )

        stream = SSEStream(turn=turn)
        events = await _collect_events(stream)

        event_types = [e[0] for e in events]
        assert "assistant" in event_types
        assert event_types[-1] == "done", (
            f"Expected final done event, got: {event_types}"
        )


class TestChatTurnFullChainAudit:
    """AL-001: Full chain audit event chain integrity."""

    async def test_full_audit_chain_high_risk(
        self, llm, executor, context_manager, audit, bridge, rule_engine
    ):
        """High-risk operation audit chain: TOOL_REQUEST_CREATED -> TOOL_APPROVED -> TOOL_EXECUTED."""

        class RiskyExecutor(MockExecutor):
            def list_tools(self):
                return _tools("restart_service", is_read_only=False)

            async def classify(self, tool_name, params, server_name=""):
                return {"is_read_only": False, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                results = []
                for c in calls:
                    self.calls.append(
                        {
                            "tool_name": c["tool_name"],
                            "arguments": c.get("arguments", {}),
                        }
                    )
                    results.append(
                        {
                            "tool_call_id": c.get("call_id", ""),
                            "result": {
                                "execution_status": "SUCCEEDED",
                                "output": f"result of {c['tool_name']}",
                            },
                        }
                    )
                return results

        risky = RiskyExecutor()
        llm.responses = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "function": {"name": "restart_service", "arguments": "{}"},
                    },
                ],
            },
            {"content": "Service restarted.", "tool_calls": None},
        ]

        def orch_builder():
            return LoopOrchestrator(
                context_manager=context_manager,
                bridge=bridge,
                audit_logger=audit,
                error_recovery=None,
                llm=llm,
                chat_id="",
                tool_executor=risky,
                rule_engine=rule_engine,
            )

        turn = ChatTurn(
            user_message="restart",
            chat_id=None,
            session_manager=MockSessionManager(),
            prompt_manager=MockPromptManager(),
            orchestrator_builder=orch_builder,
            tool_executor=risky,
            input_safety_gate=InputSafetyGate([]),
            audit_logger=audit,
        )

        from src.sse_stream import SSEStream

        stream = SSEStream(turn=turn)
        async for e in stream:
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED", chat_id=data["chat_id"])

        audit_events = [e.event for e in audit.events]
        assert "TOOL_REQUEST_CREATED" in audit_events
        assert "TOOL_APPROVED" in audit_events
        assert "TOOL_EXECUTED" in audit_events
        created_idx = audit_events.index("TOOL_REQUEST_CREATED")
        approved_idx = audit_events.index("TOOL_APPROVED")
        executed_idx = audit_events.index("TOOL_EXECUTED")
        assert created_idx < approved_idx < executed_idx


class TestHistoryReconstruction:
    """Slice 2: ChatTurn builds history from messages + executed_tool_list."""

    async def test_build_history_uses_persisted_tool_result_messages(self):
        """Tool result messages are replayed from persisted messages."""
        from datetime import datetime, timezone
        from uuid import uuid4
        from src.services.history_projection import build_llm_history
        from src.models.session import ChatSession
        from src.models.message import Message

        chat_id = uuid4()
        llm_tool_call_id = "call_msg_source_001"
        ts1 = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)
        ts_assist = datetime(2026, 6, 13, 12, 0, 1, tzinfo=timezone.utc)
        ts_tool = datetime(2026, 6, 13, 12, 0, 2, tzinfo=timezone.utc)

        session = ChatSession(
            id=chat_id,
            messages=[
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts1.isoformat(),
                    type=MessageType.USER,
                    content="check CPU",
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts_assist.isoformat(),
                    type=MessageType.ASSISTANT,
                    content="",
                    tool_calls=[
                        {
                            "id": llm_tool_call_id,
                            "type": "function",
                            "function": {"name": "get_cpu", "arguments": "{}"},
                        }
                    ],
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts_tool.isoformat(),
                    type=MessageType.TOOL_RESULT,
                    content="[get_cpu] execution_status=SUCCEEDED\noutput=CPU: 45%",
                    tool_call_id=llm_tool_call_id,
                    tool_name="get_cpu",
                ),
            ],
            executed_tool_list=[],
            timestamp=ts_tool.isoformat(),
        )

        history = build_llm_history(session)

        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant", "tool"]
        tool_msg = history[2]
        assert tool_msg["tool_call_id"] == llm_tool_call_id
        assert tool_msg["name"] == "get_cpu"
        assert "CPU: 45%" in tool_msg["content"]

    async def test_build_history_no_tool_results_empty_executed_list(self):
        """Session with no tool results — history unchanged."""
        from datetime import datetime, timezone
        from uuid import uuid4
        from src.services.history_projection import build_llm_history
        from src.models.session import ChatSession
        from src.models.message import Message

        chat_id = uuid4()
        session = ChatSession(
            id=chat_id,
            messages=[
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    type=MessageType.USER,
                    content="hello",
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    type=MessageType.ASSISTANT,
                    content="hi there",
                ),
            ],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        history = build_llm_history(session)

        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant"]

    async def test_build_history_assistant_with_tool_calls(self):
        """Assistant message with tool_calls → output dict includes tool_calls field."""
        from datetime import datetime, timezone
        from uuid import uuid4
        from src.services.history_projection import build_llm_history
        from src.models.session import ChatSession
        from src.models.message import Message

        chat_id = uuid4()
        ts1 = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)
        ts_assist = datetime(2026, 6, 13, 12, 0, 1, tzinfo=timezone.utc)
        ts_tool = datetime(2026, 6, 13, 12, 0, 1, 500000, tzinfo=timezone.utc)
        ts3 = datetime(2026, 6, 13, 12, 0, 2, tzinfo=timezone.utc)

        tool_use_id = str(uuid4())
        tool_calls_data = [
            {
                "id": tool_use_id,
                "type": "function",
                "function": {"name": "get_cpu", "arguments": "{}"},
            },
        ]

        session = ChatSession(
            id=chat_id,
            messages=[
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts1.isoformat(),
                    type=MessageType.USER,
                    content="check CPU",
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts_assist.isoformat(),
                    type=MessageType.ASSISTANT,
                    content="Let me check.",
                    tool_calls=tool_calls_data,
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts_tool.isoformat(),
                    type=MessageType.TOOL_RESULT,
                    content="[get_cpu] execution_status=SUCCEEDED\noutput=CPU: 45%",
                    tool_call_id=tool_use_id,
                    tool_name="get_cpu",
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts3.isoformat(),
                    type=MessageType.ASSISTANT,
                    content="CPU is fine.",
                ),
            ],
            executed_tool_list=[],
            timestamp=ts3.isoformat(),
        )

        history = build_llm_history(session)

        # Expected order: user → assistant(with tool_calls) → tool → assistant(final)
        assert len(history) == 4, (
            f"expected 4 messages, got {len(history)}: {[m['role'] for m in history]}"
        )
        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant", "tool", "assistant"], f"got {roles}"

        # Assistant message with tool_calls
        assist_with_tc = history[1]
        assert assist_with_tc["role"] == "assistant"
        assert "tool_calls" in assist_with_tc, (
            "assistant message must include tool_calls field"
        )
        assert len(assist_with_tc["tool_calls"]) == 1
        assert assist_with_tc["tool_calls"][0]["id"] == tool_use_id

        # Tool message matches tool_call_id
        tool_msg = history[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == tool_use_id
        assert "CPU: 45%" in tool_msg["content"]

    async def test_build_history_tool_call_id_matches_non_uuid_llm_id(self):
        """Tool message tool_call_id must match assistant tool_calls id, even when
        the LLM-generated id is NOT a valid UUID (e.g. OpenAI 'call_abc123')."""
        from datetime import datetime, timezone
        from uuid import uuid4
        from src.services.history_projection import build_llm_history
        from src.models.session import ChatSession
        from src.models.message import Message

        chat_id = uuid4()
        llm_tool_call_id = "call_abc123XYZ"

        ts1 = datetime(2026, 6, 13, 12, 0, 0, tzinfo=timezone.utc)
        ts_assist = datetime(2026, 6, 13, 12, 0, 1, tzinfo=timezone.utc)
        ts_tool = datetime(2026, 6, 13, 12, 0, 1, 500000, tzinfo=timezone.utc)
        ts3 = datetime(2026, 6, 13, 12, 0, 2, tzinfo=timezone.utc)

        tool_calls_data = [
            {
                "id": llm_tool_call_id,
                "type": "function",
                "function": {"name": "get_cpu", "arguments": "{}"},
            },
        ]

        session = ChatSession(
            id=chat_id,
            messages=[
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts1.isoformat(),
                    type=MessageType.USER,
                    content="check CPU",
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts_assist.isoformat(),
                    type=MessageType.ASSISTANT,
                    content="Let me check.",
                    tool_calls=tool_calls_data,
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts_tool.isoformat(),
                    type=MessageType.TOOL_RESULT,
                    content="[get_cpu] execution_status=SUCCEEDED\noutput=CPU: 45%",
                    tool_call_id=llm_tool_call_id,
                    tool_name="get_cpu",
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts3.isoformat(),
                    type=MessageType.ASSISTANT,
                    content="CPU is fine.",
                ),
            ],
            executed_tool_list=[],
            timestamp=ts3.isoformat(),
        )

        history = build_llm_history(session)

        assert len(history) == 4, (
            f"expected 4 messages, got {len(history)}: {[m['role'] for m in history]}"
        )
        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant", "tool", "assistant"], f"got {roles}"

        # Critical: tool message's tool_call_id must match assistant's tool_call id
        tool_msg = history[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == llm_tool_call_id, (
            f"tool_call_id mismatch: {tool_msg['tool_call_id']} != {llm_tool_call_id}"
        )

    async def test_build_history_preserves_reasoning_content(self):
        """Assistant messages with reasoning_content include it in history dicts."""
        from datetime import datetime, timezone
        from uuid import uuid4
        from src.services.history_projection import build_llm_history
        from src.models.session import ChatSession
        from src.models.message import Message

        chat_id = uuid4()
        ts = datetime.now(timezone.utc)

        reasoning = "Let me think about which tool to use..."

        session = ChatSession(
            id=chat_id,
            messages=[
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts.isoformat(),
                    type=MessageType.USER,
                    content="check CPU",
                ),
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts.isoformat(),
                    type=MessageType.ASSISTANT,
                    content="Let me check.",
                    reasoning_content=reasoning,
                ),
            ],
            executed_tool_list=[],
            timestamp=ts.isoformat(),
        )

        history = build_llm_history(session)
        assert len(history) == 2
        assistant_entry = history[1]
        assert assistant_entry["role"] == "assistant"
        assert assistant_entry["reasoning_content"] == reasoning

    async def test_build_history_omits_reasoning_content_when_none(self):
        """Assistant messages without reasoning_content omit it from history."""
        from datetime import datetime, timezone
        from uuid import uuid4
        from src.services.history_projection import build_llm_history
        from src.models.session import ChatSession
        from src.models.message import Message

        chat_id = uuid4()
        ts = datetime.now(timezone.utc)

        session = ChatSession(
            id=chat_id,
            messages=[
                Message(
                    message_id=uuid4(),
                    chat_id=chat_id,
                    timestamp=ts.isoformat(),
                    type=MessageType.ASSISTANT,
                    content="Hello.",
                    reasoning_content=None,
                ),
            ],
            executed_tool_list=[],
            timestamp=ts.isoformat(),
        )

        history = build_llm_history(session)
        assert len(history) == 1
        assert "reasoning_content" not in history[0]
