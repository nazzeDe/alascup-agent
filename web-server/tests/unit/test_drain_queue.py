"""Tests for event streaming via EventChannel — ensures deltas survive across multiple think cycles."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.agent.loop.orchestrator import LoopOrchestrator
from src.chat_turn import ChatTurn
from src.security.input_safety import InputSafetyGate

pytestmark = pytest.mark.asyncio


class MockLLM:
    def __init__(self):
        self._call = 0

    async def generate_stream(self, messages, tools=None, system=None, chat_id=None):
        self._call += 1
        if self._call == 1:
            yield {"event": "assistant", "data": json.dumps({"delta": "I need to"})}
            yield {
                "event": "tool_call",
                "data": json.dumps(
                    {
                        "id": "tc-1",
                        "function": {"name": "restart_service", "arguments": "{}"},
                    }
                ),
            }
            yield {"event": "done", "data": "{}"}
        else:
            yield {"event": "assistant", "data": json.dumps({"delta": "Service"})}
            yield {"event": "assistant", "data": json.dumps({"delta": " restarted."})}
            yield {"event": "done", "data": "{}"}

    def escalate_max_tokens(self):
        pass

    def switch_to_fallback(self):
        pass


class MockRuleEngine:
    @staticmethod
    def evaluate(tool_name, is_read_only, is_rollbackable):
        if is_read_only:
            return "AUTO_APPROVE"
        return "NEEDS_APPROVAL"


class MockExecutor:
    def __init__(self):
        self.calls: list[dict] = []

    def list_tools(self):
        return [
            {
                "name": "restart_service",
                "server_name": "tool-server",
                "mutable": True,
                "is_read_only": False,
            }
        ]

    async def execute(self, tool_name, arguments, **kwargs):
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return {"execution_status": "SUCCEEDED", "output": "done"}

    async def classify(self, tool_name, params, server_name=""):
        return {"is_read_only": False, "is_rollbackable": True}

    async def execute_parallel(self, calls: list[dict]) -> list[dict]:
        for c in calls:
            self.calls.append(
                {"tool_name": c["tool_name"], "arguments": c.get("arguments", {})}
            )
        return [
            {
                "tool_call_id": c.get("call_id", ""),
                "result": {
                    "execution_status": "SUCCEEDED",
                    "output": "done",
                },
            }
            for c in calls
        ]


class MockContextManager:
    window_size = 128000
    threshold = 0.7

    def count_tokens(self, messages):
        return len(str(messages))

    def needs_compression(self, tokens):
        return False

    async def compress(self, messages):
        return messages


class MockSessionManager:
    def __init__(self):
        self._sessions = {}

    async def create_session(self):
        from src.models.session import ChatSession

        sid = uuid4()
        session = ChatSession(
            id=sid,
            title=None,
            messages=[],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[sid] = session
        return session

    async def get_session(self, chat_id):
        if chat_id not in self._sessions:
            return await self.create_session()
        return self._sessions[chat_id]

    async def add_message(self, chat_id, msg):
        pass

    async def set_title(self, chat_id, title):
        pass

    async def list_sessions(self):
        return list(self._sessions.values())

    async def delete_session(self, chat_id):
        self._sessions.pop(chat_id, None)

    async def add_tool_call(self, chat_id, call):
        return uuid4()

    async def update_tool_call(self, tid, cid, **kw):
        pass


class MockPromptManager:
    def build_system_prompt(self):
        return "You are a helpful assistant."


class MockAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


class TestDrainQueueIntegration:
    """Integration-level test: events survive approval -> second LLM call."""

    @pytest.fixture
    def bridge(self):
        from src.security.pending import ApprovalBridge

        return ApprovalBridge()

    @pytest.fixture
    def audit(self):
        return MockAuditLogger()

    @pytest.fixture
    def context_manager(self):
        return MockContextManager()

    async def test_drain_queue_survives_approval_cycle(
        self, bridge, context_manager, audit
    ):
        """Full chat() flow with tool approval: verify streaming deltas in all cycles."""
        llm = MockLLM()
        executor = MockExecutor()
        rule_engine = MockRuleEngine()

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
            user_message="restart the service",
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

        events: list[tuple] = []
        async for e in stream:
            ev_data = e.get("data", "{}")
            parsed = json.loads(ev_data) if isinstance(ev_data, str) else ev_data
            events.append((e["event"], parsed))
            if e["event"] == "tool_approval_required":
                bridge.complete(
                    parsed["request_id"], "APPROVED", chat_id=parsed["chat_id"]
                )

        event_types = [e[0] for e in events]

        # Should have thinking_done event (from think_node)
        thinking_done_count = event_types.count("thinking_done")
        assert thinking_done_count >= 2, (
            f"Expected >=2 thinking_done events (one per cycle), got {thinking_done_count}. "
            f"Events: {event_types}"
        )

        # Should have assistant events from both cycles
        assistant_count = event_types.count("assistant")
        assert assistant_count >= 3, (
            f"Expected >=3 assistant events, got {assistant_count}. Events: {event_types}"
        )

        # Verify tool call was executed
        assert executor.calls, "Expected executor to be called"
