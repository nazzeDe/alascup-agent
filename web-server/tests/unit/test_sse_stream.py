"""SSEStream tests. Verify session_init first event, agent event sequence, done last event."""

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.agent.loop.orchestrator import LoopOrchestrator
from src.chat_turn import ChatTurn
from src.models.message import Message, MessageType
from src.sse_stream import SSEStream


# ── Mocks ────────────────────────────────────────────────────────────────

class MockLLM:
    """Yields assistant + done events (minimal happy path)."""
    def __init__(self, responses: list[dict] | None = None):
        self.responses = responses or [{"content": "no tools needed"}]
        self._idx = 0
        self.escalated = False
        self.fallback_switched = False

    async def generate_stream(self, messages, tools=None, system=None, chat_id=None):
        resp = self.responses[min(self._idx, len(self.responses) - 1)]
        self._idx += 1

        if "error" in resp:
            err = resp["error"]
            yield {"event": "error", "data": json.dumps(err)}
            yield {"event": "done", "data": "{}"}
            return

        reasoning = resp.get("reasoning", "")
        content = resp.get("content", "")
        tool_calls = resp.get("tool_calls")

        if reasoning:
            yield {"event": "assistant", "data": json.dumps({"reasoning_content": reasoning})}

        if content:
            yield {"event": "assistant", "data": json.dumps({"delta": content})}
        if tool_calls:
            for tc in tool_calls:
                yield {"event": "tool_call", "data": json.dumps(tc)}
        yield {"event": "done", "data": "{}"}

    def escalate_max_tokens(self) -> None:
        self.escalated = True

    def switch_to_fallback(self) -> None:
        self.fallback_switched = True


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

    async def execute(self, tool_name, arguments, *, server_name=None, approval_status=None, request_id=None, **kwargs):
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return {"execution_status": "SUCCEEDED", "output": f"result of {tool_name}"}

    async def execute_parallel(self, calls: list[dict]) -> list[dict]:
        results = []
        for c in calls:
            self.calls.append({"tool_name": c["tool_name"], "arguments": c.get("arguments", {})})
            results.append({"tool_call_id": c.get("call_id", ""), "result": {
                "execution_status": "SUCCEEDED", "output": f"result of {c['tool_name']}",
            }})
        return results

    async def classify(self, tool_name, params, server_name=""):
        return {"is_read_only": True, "is_rollbackable": True}

    def list_tools(self) -> list[dict]:
        return [
            {"name": "get_cpu", "server_name": "tool-server", "mutable": False, "is_read_only": True},
            {"name": "get_memory", "server_name": "tool-server", "mutable": False, "is_read_only": True},
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


class MockAuditLogger:
    def __init__(self):
        self.events: list = []

    async def log(self, event):
        self.events.append(event)


class MockPromptManager:
    """Simple prompt manager that returns a canned system prompt."""
    def build_system_prompt(self) -> str:
        return "You are a helpful assistant."


class MockSessionManager:
    def __init__(self):
        from src.models.session import ChatSession
        self._sessions: dict[UUID, ChatSession] = {}

    async def create_session(self):
        from src.models.session import ChatSession
        sid = uuid4()
        session = ChatSession(
            id=sid, title=None, messages=[],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[sid] = session
        return session

    async def get_session(self, chat_id: UUID):
        if chat_id not in self._sessions:
            return await self.create_session()
        return self._sessions[chat_id]

    async def add_message(self, chat_id: UUID, msg: Message):
        pass

    async def set_title(self, chat_id: UUID, title: str):
        if chat_id in self._sessions:
            self._sessions[chat_id].title = title

    async def list_sessions(self):
        return list(self._sessions.values())

    async def delete_session(self, chat_id: UUID):
        self._sessions.pop(chat_id, None)

    async def add_tool_call(self, chat_id: UUID, call):
        return uuid4()

    async def update_tool_call(self, tid, cid, **kw):
        pass


pytestmark = pytest.mark.asyncio


async def _collect_events(gen):
    """Collect SSE events preserving full dicts."""
    events = []
    async for e in gen:
        events.append(e)
    return events


# ── Helper: build SSEStream ──────────────────────────────────────────────

def _build_stream(user_message, chat_id, session_mgr, prompt_mgr,
                  llm, executor, context_mgr, audit, bridge, rule_engine,
                  agent_max_iterations=30):
    """Build SSEStream with ChatTurn and orchestrator."""

    def orch_builder():
        return LoopOrchestrator(
            context_manager=context_mgr,
            bridge=bridge, audit_logger=audit, error_recovery=None,
            llm=llm, chat_id=chat_id or "",
            agent_max_iterations=agent_max_iterations,
            tool_executor=executor,
            rule_engine=rule_engine,
        )

    turn = ChatTurn(
        user_message=user_message, chat_id=chat_id,
        session_manager=session_mgr, prompt_manager=prompt_mgr,
        orchestrator_builder=orch_builder, tool_executor=executor,
    )

    return SSEStream(turn=turn)


# ── Fixtures ─────────────────────────────────────────────────────────────

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

@pytest.fixture
def session_manager():
    return MockSessionManager()

@pytest.fixture
def prompt_manager():
    return MockPromptManager()


# ── Tests ────────────────────────────────────────────────────────────────

class TestSSEStreamSessionInit:
    """SSEStream first event always session_init, carries real chat_id."""

    async def test_first_event_is_session_init_with_real_chat_id(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        llm.responses = [{"content": "Hello.", "tool_calls": None}]

        stream = _build_stream("hello", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        assert len(events) >= 2, f"Expected at least 2 events, got {len(events)}"
        first = events[0]
        assert first["event"] == "session_init", f"First event: {first['event']}"
        data = json.loads(first["data"])
        chat_id = data["chat_id"]
        assert chat_id != "new", f"chat_id should be a real UUID, not 'new'"
        UUID(chat_id)

    async def test_session_init_when_chat_id_provided(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """Providing an existing chat_id should still yield session_init first."""
        session = await session_manager.create_session()
        existing_id = str(session.id)

        llm.responses = [{"content": "Welcome back.", "tool_calls": None}]

        stream = _build_stream("continue", existing_id, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        first = events[0]
        assert first["event"] == "session_init"
        data = json.loads(first["data"])
        assert data["chat_id"] == existing_id


class TestSSEStreamAgentEvents:
    """session_init followed by normal agent event sequence."""

    async def test_session_init_followed_by_agent_events(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        llm.responses = [{"content": "CPU is normal.", "tool_calls": None}]

        stream = _build_stream("check CPU", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert event_types[0] == "session_init"
        assert "assistant" in event_types[1:], f"Expected 'assistant' after session_init, got {event_types[1:]}"

    async def test_agent_events_include_tool_calls(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        executor.list_tools = lambda: [{"name": "get_cpu", "server_name": "tool-server", "mutable": False, "is_read_only": True}]
        llm.responses = [
            {"content": "", "tool_calls": [
                {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}},
            ]},
            {"content": "CPU is 85%.", "tool_calls": None},
        ]

        stream = _build_stream("check CPU", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert event_types[0] == "session_init"
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "assistant" in event_types


class TestSSEStreamDone:
    """done is always the last event."""

    async def test_done_is_last_event_text_only(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        llm.responses = [{"content": "OK.", "tool_calls": None}]

        stream = _build_stream("test", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)
        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"

    async def test_done_is_last_event_with_tool_calls(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        executor.list_tools = lambda: [{"name": "get_cpu", "server_name": "tool-server", "mutable": False, "is_read_only": True}]
        llm.responses = [
            {"content": "", "tool_calls": [
                {"id": "tc-1", "function": {"name": "get_cpu", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]

        stream = _build_stream("check", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)
        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"

    async def test_done_present_even_on_agent_error(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """If the LLM yields an error, done should still be the last event."""
        llm.responses = [{"error": {"code": 500, "message": "LLM crash"}}]

        stream = _build_stream("test", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        assert events[0]["event"] == "session_init"
        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"


class TestSSEStreamSessionPersistence:
    """Verify session and messages are properly persisted."""

    async def test_user_message_persisted(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        llm.responses = [{"content": "Got it.", "tool_calls": None}]

        stream = _build_stream("save this", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        data = json.loads(events[0]["data"])
        chat_id = UUID(data["chat_id"])

        session = await session_manager.get_session(chat_id)
        assert session is not None

    async def test_session_title_set(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        llm.responses = [{"content": "Title test.", "tool_calls": None}]

        stream = _build_stream("Long message that should be truncated for title", None,
                               session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        await _collect_events(stream)


class TestSSEStreamApproval:
    """SSEStream approval flow event sequence completeness tests.

    Verify tool_approval_required -> tool_call + tool_result event sequence.
    """

    async def test_approval_flow_yields_tool_call_and_result(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """Approval granted should yield tool_call + tool_result events."""
        class RiskyExecutor(MockExecutor):
            def list_tools(self):
                return [
                    {"name": "restart_service", "server_name": "tool-server",
                     "mutable": False, "is_read_only": False},
                ]

            async def classify(self, tool_name, params, server_name=""):
                return {"is_read_only": False, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                for c in calls:
                    self.calls.append({"tool_name": c["tool_name"], "arguments": c.get("arguments", {})})
                return [{"tool_call_id": c.get("call_id", ""), "result": {
                    "execution_status": "SUCCEEDED", "output": f"result of {c['tool_name']}",
                }} for c in calls]

        risky = RiskyExecutor()
        llm.responses = [
            {"content": "", "tool_calls": [
                {"id": "tc-1", "function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Service restarted.", "tool_calls": None},
        ]

        stream = _build_stream("restart", None, session_manager, prompt_manager,
                               llm, risky, context_manager, audit, bridge, rule_engine)

        events: list[dict] = []
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        event_types = [e["event"] for e in events]
        assert "session_init" in event_types, f"Missing session_init: {event_types}"
        assert "tool_approval_required" in event_types, f"Missing tool_approval_required: {event_types}"
        assert "tool_call" in event_types, f"Missing tool_call: {event_types}"
        assert "tool_result" in event_types, f"Missing tool_result: {event_types}"
        assert event_types[-1] == "done", f"Last event should be done, got: {event_types[-1]}"

    async def test_done_is_last_event_after_approval(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """After approval flow, done must be the last event."""
        class RiskyExecutor(MockExecutor):
            def list_tools(self):
                return [
                    {"name": "restart_service", "server_name": "tool-server",
                     "mutable": False, "is_read_only": False},
                ]

            async def classify(self, tool_name, params, server_name=""):
                return {"is_read_only": False, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                for c in calls:
                    self.calls.append({"tool_name": c["tool_name"], "arguments": c.get("arguments", {})})
                return [{"tool_call_id": c.get("call_id", ""), "result": {
                    "execution_status": "SUCCEEDED", "output": f"result of {c['tool_name']}",
                }} for c in calls]

        risky = RiskyExecutor()
        llm.responses = [
            {"content": "", "tool_calls": [
                {"id": "tc-1", "function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "All done.", "tool_calls": None},
        ]

        stream = _build_stream("restart", None, session_manager, prompt_manager,
                               llm, risky, context_manager, audit, bridge, rule_engine)

        events: list[dict] = []
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        assert len(events) >= 3, f"Expected at least 3 events, got {len(events)}"
        assert events[0]["event"] == "session_init"
        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"


class TestSSEStreamApprovalEventOrdering:
    """Verify tool events appear before done, and all tools are resolved."""

    async def test_tool_finished_before_done_in_approval_flow(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """Tool call and tool result events must appear before the done event."""
        class RiskyExecutor(MockExecutor):
            def list_tools(self):
                return [
                    {"name": "restart_service", "server_name": "tool-server",
                     "mutable": False, "is_read_only": False},
                ]

            async def classify(self, tool_name, params, server_name=""):
                return {"is_read_only": False, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                for c in calls:
                    self.calls.append({"tool_name": c["tool_name"], "arguments": c.get("arguments", {})})
                return [{"tool_call_id": c.get("call_id", ""), "result": {
                    "execution_status": "SUCCEEDED", "output": f"result of {c['tool_name']}",
                }} for c in calls]

        risky = RiskyExecutor()
        llm.responses = [
            {"content": "", "tool_calls": [
                {"id": "tc-1", "function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]

        stream = _build_stream("restart", None, session_manager, prompt_manager,
                               llm, risky, context_manager, audit, bridge, rule_engine)

        events: list[dict] = []
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        event_types = [e["event"] for e in events]

        # Find indices
        try:
            done_idx = event_types.index("done")
        except ValueError:
            done_idx = None

        # Find last tool_call and last tool_result
        last_tool_call_idx = None
        last_tool_result_idx = None
        for i, et in enumerate(event_types):
            if et == "tool_call":
                last_tool_call_idx = i
            elif et == "tool_result":
                last_tool_result_idx = i

        assert last_tool_call_idx is not None, "Expected at least one tool_call event"
        assert last_tool_result_idx is not None, "Expected at least one tool_result event"
        assert done_idx is not None, "Expected a done event"

        assert last_tool_call_idx < done_idx, (
            f"Last tool_call at index {last_tool_call_idx} must be before done at index {done_idx}"
        )
        assert last_tool_result_idx < done_idx, (
            f"Last tool_result at index {last_tool_result_idx} must be before done at index {done_idx}"
        )
        assert done_idx == len(event_types) - 1, (
            f"Done must be the very last event, got {event_types[-1]} at index {done_idx}"
        )

    async def test_all_tools_resolved_before_stream_close(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """Every tool_call event must have a corresponding tool_result with matching call_id."""
        class RiskyExecutor(MockExecutor):
            def list_tools(self):
                return [
                    {"name": "restart_service", "server_name": "tool-server",
                     "mutable": False, "is_read_only": False},
                ]

            async def classify(self, tool_name, params, server_name=""):
                return {"is_read_only": False, "is_rollbackable": True}

            async def execute_parallel(self, calls: list[dict]) -> list[dict]:
                for c in calls:
                    self.calls.append({"tool_name": c["tool_name"], "arguments": c.get("arguments", {})})
                return [{"tool_call_id": c.get("call_id", ""), "result": {
                    "execution_status": "SUCCEEDED", "output": f"result of {c['tool_name']}",
                }} for c in calls]

        risky = RiskyExecutor()
        llm.responses = [
            {"content": "", "tool_calls": [
                {"id": "tc-1", "function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]

        stream = _build_stream("restart", None, session_manager, prompt_manager,
                               llm, risky, context_manager, audit, bridge, rule_engine)

        events: list[dict] = []
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        # Collect all tool_call and tool_result call_ids
        tool_call_ids: set[str] = set()
        tool_result_ids: set[str] = set()

        for e in events:
            if e["event"] == "tool_call":
                data = json.loads(e["data"])
                tool_call_ids.add(data["call_id"])
            elif e["event"] == "tool_result":
                data = json.loads(e["data"])
                tool_result_ids.add(data["call_id"])

        # Every tool_call must have a matching tool_result
        missing_results = tool_call_ids - tool_result_ids
        assert not missing_results, (
            f"Tool calls without results: {missing_results}. "
            f"call_ids: {tool_call_ids}, result_ids: {tool_result_ids}"
        )

        # No orphaned tool_results
        orphaned = tool_result_ids - tool_call_ids
        assert not orphaned, (
            f"Tool results without matching calls: {orphaned}"
        )


class TestSSEStreamStreamingBehavior:
    """Verify that reasoning events and thinking_done are properly forwarded."""

    async def test_reasoning_events_streamed(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """Reasoning content from LLM appears as 'reasoning' events."""
        llm.responses = [{"reasoning": "The user wants CPU info.", "content": "CPU is 42%.", "tool_calls": None}]

        stream = _build_stream("check CPU", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert "reasoning" in event_types, (
            f"Expected 'reasoning' event in stream, got: {event_types}"
        )

        reasoning_events = [e for e in events if e["event"] == "reasoning"]
        assert len(reasoning_events) >= 1
        data = json.loads(reasoning_events[0]["data"])
        assert "delta" in data
        assert "The user wants CPU info" in data["delta"]

    async def test_thinking_done_emitted(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """thinking_done should be emitted after reasoning, before assistant deltas."""
        llm.responses = [{"reasoning": "I need to help.", "content": "OK.", "tool_calls": None}]

        stream = _build_stream("help", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert "thinking_done" in event_types, (
            f"Expected 'thinking_done' event in stream, got: {event_types}"
        )

    async def test_assistant_done_emitted(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """assistant_done should be emitted after assistant deltas."""
        llm.responses = [{"content": "Hello.", "tool_calls": None}]

        stream = _build_stream("hi", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert "assistant_done" in event_types, (
            f"Expected 'assistant_done' event in stream, got: {event_types}"
        )

    async def test_full_event_sequence_with_reasoning(
        self, llm, executor, context_manager, audit, bridge, rule_engine,
        session_manager, prompt_manager,
    ):
        """End-to-end event sequence with reasoning."""
        llm.responses = [{"reasoning": "Simple query.", "content": "Got it.", "tool_calls": None}]

        stream = _build_stream("test", None, session_manager, prompt_manager,
                               llm, executor, context_manager, audit, bridge, rule_engine)
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]

        assert "session_init" in event_types
        assert "reasoning" in event_types
        assert "thinking_done" in event_types
        assert "assistant" in event_types
        assert "done" in event_types

        sidx = event_types.index("session_init")
        didx = event_types.index("done")
        assert sidx < didx
        assert sidx == 0, f"session_init must be first, got idx {sidx}"
        assert didx == len(event_types) - 1, f"done must be last, got idx {didx}"

        # reasoning must appear before thinking_done
        ridx = event_types.index("reasoning")
        tidx = event_types.index("thinking_done")
        assert ridx < tidx
