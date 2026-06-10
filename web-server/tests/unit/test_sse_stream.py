"""SSEStream 测试。验证 session_init 第一事件、agent 事件序列、done 最后事件。"""

import json
from uuid import UUID

import pytest

from src.agent.graph import build_graph
from src.agent.query import Query


# ── Mocks (extending test_query.py mocks where needed) ──────────────────────

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

        # Reasoning comes first (model thinks before speaking)
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
        import asyncio
        tasks = [self.execute(c["tool_name"], c.get("arguments", {})) for c in calls]
        return await asyncio.gather(*tasks)

    async def classify(self, tool_name, params, server_name=""):
        return {"is_read_only": True, "is_rollbackable": True}

    def list_tools(self) -> list[dict]:
        return [
            {"name": "get_cpu", "server_name": "tool-server", "mutable": False, "is_read_only": True},
            {"name": "get_memory", "server_name": "tool-server", "mutable": False, "is_read_only": True},
        ]


class MockContextManager:
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


pytestmark = pytest.mark.asyncio


async def _collect_events(gen):
    """Collect SSE events preserving full dicts."""
    events = []
    async for e in gen:
        events.append(e)
    return events


# ── Fixtures ────────────────────────────────────────────────────────────────

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
def graph(llm, executor, rule_engine, audit):
    return build_graph(llm=llm, executor=executor,
                       rule_engine=rule_engine, audit_logger=audit)


@pytest.fixture
def session_manager():
    from src.services.session_manager import InMemorySessionManager
    return InMemorySessionManager()


@pytest.fixture
def prompt_manager():
    return MockPromptManager()


@pytest.fixture
def query(llm, graph, context_manager, bridge, audit):
    return Query(llm=llm, graph=graph, context_manager=context_manager,
                 pending_approvals=bridge, audit_logger=audit)


# ── Tests ───────────────────────────────────────────────────────────────────

class TestSSEStreamSessionInit:
    """SSEStream 第一事件總是 session_init，攜帶真實 chat_id。"""

    async def test_first_event_is_session_init_with_real_chat_id(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "Hello.", "tool_calls": None}]

        stream = SSEStream(
            user_message="hello",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        assert len(events) >= 2, f"Expected at least 2 events, got {len(events)}"
        first = events[0]
        assert first["event"] == "session_init", f"First event: {first['event']}"
        data = json.loads(first["data"])
        chat_id = data["chat_id"]
        assert chat_id != "new", f"chat_id should be a real UUID, not 'new'"
        # Verify it's a valid UUID
        UUID(chat_id)  # raises if invalid

    async def test_session_init_when_chat_id_provided(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        """Providing an existing chat_id should still yield session_init first."""
        from src.sse_stream import SSEStream

        # Create a session first
        session = await session_manager.create_session()
        existing_id = str(session.id)

        llm.responses = [{"content": "Welcome back.", "tool_calls": None}]

        stream = SSEStream(
            user_message="continue",
            chat_id=existing_id,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        first = events[0]
        assert first["event"] == "session_init"
        data = json.loads(first["data"])
        assert data["chat_id"] == existing_id


class TestSSEStreamAgentEvents:
    """session_init 後跟隨正常的 agent 事件序列。"""

    async def test_session_init_followed_by_agent_events(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "CPU is normal.", "tool_calls": None}]

        stream = SSEStream(
            user_message="check CPU",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        # First event: session_init
        assert event_types[0] == "session_init"
        # After session_init: at least assistant, done
        assert "assistant" in event_types[1:], f"Expected 'assistant' after session_init, got {event_types[1:]}"

    async def test_agent_events_include_tool_calls(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
            ]},
            {"content": "CPU is 85%.", "tool_calls": None},
        ]

        stream = SSEStream(
            user_message="check CPU",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert event_types[0] == "session_init"
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "assistant" in event_types


class TestSSEStreamDone:
    """done 永遠是最後一個事件。"""

    async def test_done_is_last_event_text_only(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "OK.", "tool_calls": None}]

        stream = SSEStream(
            user_message="test",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"

    async def test_done_is_last_event_with_tool_calls(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]

        stream = SSEStream(
            user_message="check",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"

    async def test_done_present_even_on_agent_error(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        """If the LLM yields an error, done should still be the last event."""
        from src.sse_stream import SSEStream

        llm.responses = [{"error": {"code": 500, "message": "LLM crash"}}]

        stream = SSEStream(
            user_message="test",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        # First event must be session_init
        assert events[0]["event"] == "session_init"
        # Last event must be done
        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"


class TestSSEStreamSessionPersistence:
    """驗證 session 和消息被正確持久化。"""

    async def test_user_message_persisted(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "Got it.", "tool_calls": None}]

        stream = SSEStream(
            user_message="save this",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        # Extract chat_id from session_init
        data = json.loads(events[0]["data"])
        chat_id = UUID(data["chat_id"])

        session = await session_manager.get_session(chat_id)
        user_msgs = [m for m in session.messages if m.type.value == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0].content == "save this"

    async def test_assistant_message_persisted(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "I will remember this.", "tool_calls": None}]

        stream = SSEStream(
            user_message="remember me",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        data = json.loads(events[0]["data"])
        chat_id = UUID(data["chat_id"])

        session = await session_manager.get_session(chat_id)
        assistant_msgs = [m for m in session.messages if m.type.value == "assistant"]
        assert len(assistant_msgs) == 1
        assert "remember" in assistant_msgs[0].content

    async def test_session_title_set(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "Title test.", "tool_calls": None}]

        stream = SSEStream(
            user_message="Long message that should be truncated for title",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        data = json.loads(events[0]["data"])
        chat_id = UUID(data["chat_id"])

        session = await session_manager.get_session(chat_id)
        assert session.title is not None
        assert len(session.title) <= 20


class TestSSEStreamApproval:
    """SSEStream 审批流程事件序列完整性测试。

    验证 tool_approval_required → tool_call + tool_result 的完整事件链，
    确保 message_id 一致性以及 execution_status 为 SUCCEEDED。
    """

    async def test_approval_flow_yields_tool_call_and_result_same_message_id(
        self, query, session_manager, prompt_manager, executor, llm, bridge, rule_engine, audit,
    ):
        """审批通过后应收到 tool_call + tool_result，两者 message_id 相同。"""
        from src.sse_stream import SSEStream

        # 注入非只读工具，触发 NEEDS_APPROVAL
        executor.list_tools = lambda: [
            {"name": "restart_service", "server_name": "tool-server",
             "mutable": False, "is_read_only": False},
        ]

        # 构建新的 graph，确保工具分类正确
        from src.agent.graph import build_graph
        query._graph = build_graph(
            llm=llm, executor=executor, rule_engine=rule_engine, audit_logger=audit,
        )

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Service restarted.", "tool_calls": None},
        ]

        stream = SSEStream(
            user_message="restart",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )

        events: list[dict] = []
        approval_seen = 0
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                approval_seen += 1
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        # 断言事件序列
        event_types = [e["event"] for e in events]
        assert "session_init" in event_types, f"Missing session_init: {event_types}"
        assert approval_seen == 1, f"Expected 1 tool_approval_required, got {approval_seen}"
        assert "tool_call" in event_types, f"Missing tool_call after approval: {event_types}"
        assert "tool_result" in event_types, f"Missing tool_result after approval: {event_types}"
        assert "assistant" in event_types, f"Missing assistant: {event_types}"
        assert event_types[-1] == "done", f"Last event should be done, got: {event_types[-1]}"

        # 提取 tool_call 和 tool_result
        tool_call_events = [e for e in events if e["event"] == "tool_call"]
        tool_result_events = [e for e in events if e["event"] == "tool_result"]

        assert len(tool_call_events) == 1, f"Expected 1 tool_call, got {len(tool_call_events)}"
        assert len(tool_result_events) == 1, f"Expected 1 tool_result, got {len(tool_result_events)}"

        tc_data = json.loads(tool_call_events[0]["data"])
        tr_data = json.loads(tool_result_events[0]["data"])

        # message_id 一致
        assert tc_data["message_id"] == tr_data["message_id"], (
            f"tool_call.message_id ({tc_data['message_id']}) != "
            f"tool_result.message_id ({tr_data['message_id']})"
        )

        # tool_name 一致
        assert tc_data["tool_name"] == tr_data["tool_name"] == "restart_service"

    async def test_tool_result_has_succeeded_status(
        self, query, session_manager, prompt_manager, executor, llm, bridge, rule_engine, audit,
    ):
        """审批通过后 tool_result 的 execution_status 应为 SUCCEEDED（非 RUNNING）。"""
        from src.sse_stream import SSEStream

        executor.list_tools = lambda: [
            {"name": "restart_service", "server_name": "tool-server",
             "mutable": False, "is_read_only": False},
        ]

        from src.agent.graph import build_graph
        query._graph = build_graph(
            llm=llm, executor=executor, rule_engine=rule_engine, audit_logger=audit,
        )

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]

        stream = SSEStream(
            user_message="restart",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )

        events: list[dict] = []
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        tool_result_events = [e for e in events if e["event"] == "tool_result"]
        assert len(tool_result_events) == 1, f"Expected 1 tool_result, got {len(tool_result_events)}"

        tr_data = json.loads(tool_result_events[0]["data"])
        assert tr_data["execution_status"] == "SUCCEEDED", (
            f"Expected SUCCEEDED, got {tr_data['execution_status']}"
        )
        # 不应出现 RUNNING
        assert tr_data["execution_status"] != "RUNNING"

    async def test_approval_flow_events_sequence_order(
        self, query, session_manager, prompt_manager, executor, llm, bridge, rule_engine, audit,
    ):
        """验证事件顺序：tool_approval_required → tool_call → tool_result → done。"""
        from src.sse_stream import SSEStream

        executor.list_tools = lambda: [
            {"name": "restart_service", "server_name": "tool-server",
             "mutable": False, "is_read_only": False},
        ]

        from src.agent.graph import build_graph
        query._graph = build_graph(
            llm=llm, executor=executor, rule_engine=rule_engine, audit_logger=audit,
        )

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Service is now running.", "tool_calls": None},
        ]

        stream = SSEStream(
            user_message="restart service",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )

        events: list[dict] = []
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        # 提取关键事件类型序列（忽略 session_init）
        key_events = [e["event"] for e in events if e["event"] != "session_init"]
        # 期望：tool_approval_required → assistant → tool_call → tool_result → done
        # （assistant 在 emit_events 中先于 tool_call/tool_result 发射）
        assert "tool_approval_required" in key_events
        assert "tool_call" in key_events
        assert "tool_result" in key_events
        assert "done" in key_events

        approval_idx = key_events.index("tool_approval_required")
        tc_idx = key_events.index("tool_call")
        tr_idx = key_events.index("tool_result")
        done_idx = key_events.index("done")

        assert approval_idx < tc_idx, f"tool_approval_required must come before tool_call: {key_events}"
        assert tc_idx < tr_idx, f"tool_call must come before tool_result: {key_events}"
        assert tr_idx < done_idx, f"tool_result must come before done: {key_events}"

    async def test_done_is_last_event_after_approval(
        self, query, session_manager, prompt_manager, executor, llm, bridge, rule_engine, audit,
    ):
        """审批流程完成后 done 必须是最后一个事件。"""
        from src.sse_stream import SSEStream

        executor.list_tools = lambda: [
            {"name": "restart_service", "server_name": "tool-server",
             "mutable": False, "is_read_only": False},
        ]

        from src.agent.graph import build_graph
        query._graph = build_graph(
            llm=llm, executor=executor, rule_engine=rule_engine, audit_logger=audit,
        )

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "All done.", "tool_calls": None},
        ]

        stream = SSEStream(
            user_message="restart",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )

        events: list[dict] = []
        async for e in stream:
            events.append(e)
            if e["event"] == "tool_approval_required":
                data = json.loads(e["data"])
                bridge.complete(data["request_id"], "APPROVED")

        assert len(events) >= 3, f"Expected at least 3 events, got {len(events)}"
        assert events[0]["event"] == "session_init"
        assert events[-1]["event"] == "done", f"Last event: {events[-1]['event']}"


# ── Streaming behavior tests (regression fix verification) ────────────────────

class TestSSEStreamStreamingBehavior:
    """Verify that the drain_queue pattern correctly forwards real-time deltas.

    Regression 1 & 2 from ADR-0002: reasoning events and assistant deltas
    were lost because SSEStream had no drain_queue consumer.
    """

    async def test_reasoning_events_streamed(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        """Reasoning content from the LLM should appear as 'reasoning' events."""
        from src.sse_stream import SSEStream

        llm.responses = [{"reasoning": "The user wants CPU info.", "content": "CPU is 42%.", "tool_calls": None}]

        stream = SSEStream(
            user_message="check CPU",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert "reasoning" in event_types, (
            f"Expected 'reasoning' event in stream, got: {event_types}"
        )

        # Verify reasoning payload
        reasoning_events = [e for e in events if e["event"] == "reasoning"]
        assert len(reasoning_events) >= 1
        data = json.loads(reasoning_events[0]["data"])
        assert "delta" in data
        assert "The user wants CPU info" in data["delta"]

    async def test_thinking_done_emitted(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        """thinking_done should be emitted after reasoning, before assistant deltas."""
        from src.sse_stream import SSEStream

        llm.responses = [{"reasoning": "I need to help.", "content": "OK.", "tool_calls": None}]

        stream = SSEStream(
            user_message="help",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]
        assert "thinking_done" in event_types, (
            f"Expected 'thinking_done' event in stream, got: {event_types}"
        )

    async def test_assistant_deltas_before_final_message(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        """Streaming assistant deltas (no message_id) appear before the final
        assistant message (with message_id)."""
        from src.sse_stream import SSEStream

        llm.responses = [{"content": "Hello.", "tool_calls": None}]

        stream = SSEStream(
            user_message="hi",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        assistant_events = [e for e in events if e["event"] == "assistant"]
        assert len(assistant_events) >= 2, (
            f"Expected at least 2 assistant events (delta + final), got {len(assistant_events)}: {assistant_events}"
        )

        # First assistant event(s) should be streaming deltas (no message_id)
        streaming_deltas = []
        final_messages = []
        for ae in assistant_events:
            data = json.loads(ae["data"])
            if data.get("message_id"):
                final_messages.append(ae)
            else:
                streaming_deltas.append(ae)

        assert len(streaming_deltas) >= 1, (
            f"Expected at least 1 streaming delta (no message_id), got {len(streaming_deltas)}"
        )
        assert len(final_messages) >= 1, (
            f"Expected at least 1 final message (with message_id), got {len(final_messages)}"
        )

        # Deltas must come before final messages in event order
        last_delta_idx = max(assistant_events.index(d) for d in streaming_deltas)
        first_final_idx = min(assistant_events.index(f) for f in final_messages)
        assert last_delta_idx < first_final_idx, (
            f"Streaming deltas must appear before final messages, "
            f"but last delta at idx {last_delta_idx}, first final at idx {first_final_idx}"
        )

    async def test_full_event_sequence_with_reasoning(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        """End-to-end event sequence with reasoning.

        Expected order: session_init → reasoning → assistant(delta, no msg_id)
        → thinking_done → assistant(final, with msg_id) → done.

        The streaming assistant delta (from drain_queue) appears after reasoning
        and before thinking_done because _forward_to_queue emits deltas inline
        during LLM streaming, while thinking_done is emitted after the LLM
        stream completes.
        """
        from src.sse_stream import SSEStream

        llm.responses = [{"reasoning": "Simple query.", "content": "Got it.", "tool_calls": None}]

        stream = SSEStream(
            user_message="test",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        event_types = [e["event"] for e in events]

        # Verify all expected event types are present
        assert "session_init" in event_types
        assert "reasoning" in event_types
        assert "thinking_done" in event_types
        assert "assistant" in event_types
        assert "done" in event_types

        # Verify core ordering: session_init first, done last
        sidx = event_types.index("session_init")
        didx = event_types.index("done")
        assert sidx < didx
        assert sidx == 0, f"session_init must be first, got idx {sidx}"
        assert didx == len(event_types) - 1, f"done must be last, got idx {didx}"

        # reasoning must appear before thinking_done
        ridx = event_types.index("reasoning")
        tidx = event_types.index("thinking_done")
        assert ridx < tidx, (
            f"reasoning ({ridx}) must come before thinking_done ({tidx})"
        )

        # thinking_done must appear after all streaming deltas (no msg_id)
        # and before the final assistant message (with msg_id)
        assistant_indices = [i for i, e in enumerate(event_types) if e == "assistant"]
        streaming_delta_indices = []
        final_msg_indices = []
        for i in assistant_indices:
            data = json.loads(events[i]["data"])
            if data.get("message_id"):
                final_msg_indices.append(i)
            else:
                streaming_delta_indices.append(i)

        # Streaming deltas appear before thinking_done
        if streaming_delta_indices:
            assert max(streaming_delta_indices) < tidx, (
                f"Streaming deltas ({streaming_delta_indices}) must be before "
                f"thinking_done ({tidx})"
            )

        # Final messages appear after thinking_done
        if final_msg_indices:
            assert tidx < min(final_msg_indices), (
                f"thinking_done ({tidx}) must be before final assistant messages "
                f"({final_msg_indices})"
            )

    async def test_streaming_events_not_lost_during_tool_calls(
        self, query, session_manager, prompt_manager, executor, llm,
    ):
        """When the agent calls tools, the streaming assistant delta for the
        initial response (before tool_call) should not be lost."""
        from src.sse_stream import SSEStream

        llm.responses = [
            {"content": "Let me check.", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
            ]},
            {"content": "CPU is fine.", "tool_calls": None},
        ]

        stream = SSEStream(
            user_message="check CPU",
            chat_id=None,
            session_manager=session_manager,
            prompt_manager=prompt_manager,
            query=query,
            tool_executor=executor,
        )
        events = await _collect_events(stream)

        assistant_events = [e for e in events if e["event"] == "assistant"]
        # Should have: "Let me check." delta + "Let me check." final + "CPU is fine." delta + "CPU is fine." final
        assert len(assistant_events) >= 2, (
            f"Expected at least 2 assistant events (deltas from both turns), "
            f"got {len(assistant_events)}"
        )

        # Verify both tool turns produce assistant deltas
        streaming_no_msgid = [e for e in assistant_events
                              if not json.loads(e["data"]).get("message_id")]
        assert len(streaming_no_msgid) >= 2, (
            f"Expected at least 2 streaming deltas (one per turn), "
            f"got {len(streaming_no_msgid)}"
        )
