import json
from uuid import uuid4


from src.agent.nodes import (
    act_node,
    observe_node,
    think_node,
    _messages,
    _merge_tool_block,
)
from src.agent.state import AgentState, Transition
from src.agent.turn_context import TurnContext


class MockLLM:
    """模拟 LLM 流式输出。"""

    def __init__(self, events: list[dict]):
        self._events = events

    async def generate_stream(self, messages, tools=None, system=None, chat_id=None):
        for e in self._events:
            yield e


def _state():
    return AgentState(messages=[], available_tools=[], transition=None)


class TestThinkNode:
    async def test_text_only_returns_done(self):
        """LLM 只产出文本，无 tool_call → transition=DONE。"""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "CPU 45%, normal."})},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert result.is_done is True
        assert result.assistant_message is not None
        assert result.assistant_message["role"] == "assistant"
        assert "CPU" in result.assistant_message["content"]

    async def test_text_accumulates_multiple_deltas(self):
        """多个 text delta 拼接成完整 assistant 消息。"""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "Hello "})},
            {"event": "assistant", "data": json.dumps({"delta": "World"})},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert result.assistant_message["content"] == "Hello World"
        assert result.is_done is True

    async def test_tool_call_only_no_text(self):
        """LLM 只产出 tool_call，无文本 → 返回 tool_calls 列表。"""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "get_cpu", "arguments": '{"unit":"percent"}'}
            })},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert result.is_done is False  # 不设 transition——由后续节点决定
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "get_cpu"

    async def test_text_and_tool_calls_combined(self):
        """LLM 先输出文本，再调工具 → 文本和 tool_calls 同时返回。"""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "Let me check."})},
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "get_disk", "arguments": "{}"}
            })},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert result.assistant_message["role"] == "assistant"
        assert result.assistant_message["content"] == "Let me check."
        assert len(result.tool_calls) == 1
        assert result.is_done is False  # 有 tool_call，不应设 DONE

    async def test_multiple_tool_calls_accumulated(self):
        """多个 tool_call 都被收集，顺序保留。"""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "get_cpu", "arguments": "{}"}
            })},
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "get_memory", "arguments": "{}"}
            })},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert len(result.tool_calls) == 2
        assert result.tool_calls[0]["function"]["name"] == "get_cpu"
        assert result.tool_calls[1]["function"]["name"] == "get_memory"

    async def test_streaming_tool_use_chunks_are_merged(self):
        """tool_call 可能分多个 chunk 到达（name 先到，arguments 后到）。"""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "bash", "arguments": ""}
            })},
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "", "arguments": '{"cmd":"ls"}'}
            })},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert len(result.tool_calls) == 1
        fn = result.tool_calls[0]["function"]
        assert fn["name"] == "bash"
        assert "ls" in fn["arguments"]


class MockExecutor:
    def __init__(self, results=None):
        self._results = results or {}
        self.calls: list[dict] = []

    async def execute(self, tool_name: str, arguments: dict, **kwargs):
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return self._results.get(tool_name, {"execution_status": "SUCCEEDED"})

    async def execute_parallel(self, calls: list[dict]) -> list[dict]:
        import asyncio
        tasks = [self.execute(c["tool_name"], c.get("arguments", {})) for c in calls]
        return await asyncio.gather(*tasks)


def _state_with_tools(approved=None):
    return AgentState(
        messages=[{"role": "user", "content": "check"}],
        available_tools=[],
        approved_tool_calls=approved or [],
        transition=None,
    )


class TestActNode:
    """对应 tests/README.md SC-005、AG-001、AG-003。"""

    async def test_executes_approved_tool_calls(self):
        executor = MockExecutor()
        state = _state_with_tools([
            {"function": {"name": "get_cpu", "arguments": '{"unit":"percent"}'}},
            {"function": {"name": "get_memory", "arguments": "{}"}},
        ])
        result = await act_node(state, executor=executor)

        assert len(executor.calls) == 2
        tool_names = [c["tool_name"] for c in executor.calls]
        assert "get_cpu" in tool_names
        assert "get_memory" in tool_names
        assert len(result.results) == 2

    async def test_concurrent_readonly_tools_use_execute_parallel(self, monkeypatch):
        """AG-003: 多个只读工具 → execute_parallel 一次性调用。"""
        parallel_called = False
        call_args = []

        class _SpyExecutor:
            async def execute_parallel(self, calls):
                nonlocal parallel_called
                parallel_called = True
                call_args.extend(calls)
                return [{"execution_status": "SUCCEEDED"} for _ in calls]

        state = _state_with_tools([
            {"function": {"name": "get_cpu", "arguments": "{}"}},
            {"function": {"name": "get_memory", "arguments": "{}"}},
        ])
        result = await act_node(state, executor=_SpyExecutor())

        assert parallel_called
        assert len(call_args) == 2
        assert call_args[0]["tool_name"] == "get_cpu"
        assert call_args[1]["tool_name"] == "get_memory"
        assert len(result.results) == 2

    async def test_empty_approved_list_returns_nothing(self):
        executor = MockExecutor()
        result = await act_node(_state_with_tools([]), executor=executor)

        assert result.results == []
        assert executor.calls == []

    async def test_execution_failure_recorded(self):
        """工具执行失败 → 结果中包含失败信息，不抛异常。"""
        executor = MockExecutor({
            "bad_tool": {"execution_status": "FAILED", "error": {"message": "permission denied"}},
        })
        state = _state_with_tools([
            {"function": {"name": "bad_tool", "arguments": "{}"}},
        ])
        result = await act_node(state, executor=executor)

        assert result.results[0]["result"]["execution_status"] == "FAILED"
        assert "permission denied" in str(result.results[0]["result"])

    async def test_tool_results_include_tool_name(self):
        executor = MockExecutor()
        state = _state_with_tools([
            {"function": {"name": "get_cpu", "arguments": "{}"}},
        ])
        result = await act_node(state, executor=executor)

        assert result.results[0]["tool_name"] == "get_cpu"

    async def test_no_fake_execution_time_per_tool(self):
        """act_node does not fabricate per-tool execution_time_ms for parallel batch."""
        executor = MockExecutor()
        state = _state_with_tools([
            {"function": {"name": "get_cpu", "arguments": "{}"}},
        ])
        result = await act_node(state, executor=executor)

        et = result.results[0]["result"].get("execution_time_ms")
        assert et is None

    async def test_passes_result_to_lifecycle_mark_executed(self):
        """act_node passes result output to lifecycle execution transition."""
        from unittest.mock import AsyncMock, MagicMock

        executor = MockExecutor({
            "get_cpu": {"execution_status": "SUCCEEDED", "output": "CPU: 45%"},
        })
        lifecycle = MagicMock()
        lifecycle.mark_executed = AsyncMock()

        state = _state_with_tools([
            {"function": {"name": "get_cpu", "arguments": "{}"}, "call_id": uuid4()},
        ])
        await act_node(state, executor=executor, lifecycle=lifecycle)

        lifecycle.mark_executed.assert_called_once()
        assert lifecycle.mark_executed.call_args.args[2]["output"] == "CPU: 45%"


class TestStreamingThink:
    """AG-007: think_node stream-dispatch based on tool metadata (three pools)."""

    def _state_with_tools(self, tools: list[dict]):
        return AgentState(messages=[], available_tools=tools, transition=None)

    async def test_dispatches_readonly_tools_inline(self):
        """Safe-pool (non-mutable, readonly) → pre-executed, streaming_tool_results."""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "tool-server__get_cpu", "arguments": "{}"}
            })},
            {"event": "done", "data": "{}"},
        ])
        executor = MockExecutor()
        state = self._state_with_tools([
            {"name": "get_cpu", "server_name": "tool-server", "mutable": False, "is_read_only": True},
        ])

        result = await think_node(state, llm=llm, executor=executor)

        assert len(result.pre_executed) == 1
        assert result.pre_executed[0]["tool_name"] == "get_cpu"
        assert result.tool_calls == []

    async def test_non_readonly_stays_in_tool_calls(self):
        """Approval-pool (non-mutable, not readonly) → stays in tool_calls."""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "tool-server__restart_service", "arguments": "{}"}
            })},
            {"event": "done", "data": "{}"},
        ])
        executor = MockExecutor()
        state = self._state_with_tools([
            {"name": "restart_service", "server_name": "tool-server", "mutable": False, "is_read_only": False},
        ])

        result = await think_node(state, llm=llm, executor=executor)

        assert result.pre_executed == []
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "restart_service"

    async def test_mixed_readonly_and_highrisk(self):
        """Mixed: readonly pre-executed, write stays in tool_calls."""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "tool-server__get_cpu", "arguments": "{}"}
            })},
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "tool-server__restart_service", "arguments": "{}"}
            })},
            {"event": "done", "data": "{}"},
        ])
        executor = MockExecutor()
        state = self._state_with_tools([
            {"name": "get_cpu", "server_name": "tool-server", "mutable": False, "is_read_only": True},
            {"name": "restart_service", "server_name": "tool-server", "mutable": False, "is_read_only": False},
        ])

        result = await think_node(state, llm=llm, executor=executor)

        assert len(result.pre_executed) == 1
        assert result.pre_executed[0]["tool_name"] == "get_cpu"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "restart_service"

    async def test_mutable_tool_stays_pending(self):
        """Mutable pool — not pre-executed, stays in tool_calls for review_node classification."""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "tool-server__bash", "arguments": '{"cmd":"ls"}'}
            })},
            {"event": "done", "data": "{}"},
        ])
        executor = MockExecutor()
        state = self._state_with_tools([
            {"name": "bash", "server_name": "tool-server", "mutable": True, "is_read_only": False},
        ])

        result = await think_node(state, llm=llm, executor=executor)

        assert result.pre_executed == []
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc["function"]["name"] == "bash"
        assert tc["mutable"] is True
        assert tc["server_name"] == "tool-server"

    async def test_prefix_parsing_attaches_server_name(self):
        """Server prefix is parsed once and attached to the tool_call dict (Q24)."""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "tool-server__get_cpu_info", "arguments": "{}"}
            })},
            {"event": "done", "data": "{}"},
        ])
        executor = MockExecutor()
        state = self._state_with_tools([
            {"name": "get_cpu_info", "server_name": "tool-server", "mutable": False, "is_read_only": True},
        ])

        result = await think_node(state, llm=llm, executor=executor)

        assert result.tool_calls == []
        assert len(result.pre_executed) == 1
        assert result.pre_executed[0]["tool_name"] == "get_cpu_info"


class TestObserveNode:
    """对应 tests/README.md AG-001 后半段：tool_result → assistant message 流转。"""

    def test_appends_tool_results_as_messages(self):
        state = _state_with_tools()
        result = observe_node(state, tool_results=[
            {"tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED", "output": "CPU: 45%"}},
            {"tool_name": "get_memory", "result": {"execution_status": "SUCCEEDED", "output": "Mem: 8G"}},
        ])

        assert len(result.tool_messages) == 2
        assert result.tool_messages[0]["role"] == "tool"
        assert "get_cpu" in result.tool_messages[0]["content"]
        assert "get_memory" in result.tool_messages[1]["content"]
        assert result.transition == Transition.TOOL_RESULTS

    def test_empty_results_returns_empty(self):
        result = observe_node(_state_with_tools(), tool_results=[])

        assert result.tool_messages == []
        assert result.transition == Transition.TOOL_RESULTS

    def test_merges_streaming_tool_results(self):
        """AG-007: observe_node 合并 streaming_tool_results 和 tool_results。"""
        state = _state_with_tools()
        state["streaming_tool_results"] = [
            {"tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED", "output": "CPU: 45%"}},
        ]

        result = observe_node(state, tool_results=[
            {"tool_name": "get_memory", "result": {"execution_status": "SUCCEEDED", "output": "Mem: 8G"}},
        ])

        assert len(result.tool_messages) == 2
        assert result.transition == Transition.TOOL_RESULTS

    def test_clears_streaming_tool_results(self):
        """observe_node clears streaming_tool_results and tool_results, preserves _emitted_results."""
        state = _state_with_tools()
        state["streaming_tool_results"] = [
            {"tool_name": "get_cpu", "tool_call_id": "t1", "result": {"execution_status": "SUCCEEDED"}},
        ]

        result = observe_node(state, tool_results=[
            {"tool_name": "get_mem", "tool_call_id": "t2", "result": {"execution_status": "SUCCEEDED"}},
        ])

        assert len(result.emitted_results) == 2


class TestMessagesConversion:
    """Verify _messages preserves fields required by the LLM API."""

    def test_preserves_tool_calls_on_assistant(self):
        """Assistant messages must carry tool_calls for the LLM to accept tool responses."""
        state = {
            "messages": [
                {"role": "assistant", "content": "Let me check.",
                 "tool_calls": [{"id": "tc1", "function": {"name": "get_cpu", "arguments": "{}"}}]},
            ]
        }
        result = _messages(state)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert "tool_calls" in result[0]
        assert result[0]["tool_calls"][0]["id"] == "tc1"

    def test_normalizes_tool_calls_type_field(self):
        """Tool calls without type=function get normalized (DeepSeek/OpenAI API requirement)."""
        state = {
            "messages": [
                {"role": "assistant", "content": "Let me check.",
                 "tool_calls": [
                     {"id": "tc1", "function": {"name": "get_cpu", "arguments": "{}"}},
                     {"id": "tc2", "function": {"name": "get_mem", "arguments": "{}"}},
                 ]},
            ]
        }
        result = _messages(state)
        tcs = result[0]["tool_calls"]
        for tc in tcs:
            assert tc["type"] == "function", f"tool_call {tc['id']} missing type=function"

    def test_preserves_tool_call_id_on_tool(self):
        """Tool messages must carry tool_call_id for the LLM to associate with tool_calls."""
        state = {
            "messages": [
                {"role": "tool", "content": "[get_cpu] SUCCEEDED", "tool_call_id": "tc1"},
            ]
        }
        result = _messages(state)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc1"

    def test_preserves_name_on_tool(self):
        """Tool messages must carry name — DeepSeek requires this field."""
        state = {
            "messages": [
                {"role": "tool", "content": "[get_cpu] SUCCEEDED",
                 "tool_call_id": "tc1", "name": "get_cpu"},
            ]
        }
        result = _messages(state)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["name"] == "get_cpu"

    def test_maps_human_to_user(self):
        """LangGraph human role → user for API."""
        state = {"messages": [{"role": "human", "content": "hello"}]}
        result = _messages(state)
        assert result[0]["role"] == "user"

    def test_maps_ai_to_assistant(self):
        """LangGraph ai role → assistant for API."""
        state = {"messages": [{"role": "ai", "content": "hello there"}]}
        result = _messages(state)
        assert result[0]["role"] == "assistant"

    def test_maps_tool_result_to_tool(self):
        """tool_result role (from DB history) → tool for LLM API."""
        state = {"messages": [
            {"role": "tool_result", "content": "[get_cpu] SUCCEEDED",
             "tool_call_id": "tc1", "name": "get_cpu"},
        ]}
        result = _messages(state)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc1"
        assert result[0]["name"] == "get_cpu"

    def test_maps_tool_call_to_assistant(self):
        """tool_call role → assistant for LLM API."""
        state = {"messages": [
            {"role": "tool_call", "content": "",
             "tool_calls": [{"id": "tc1", "function": {"name": "get_cpu", "arguments": "{}"}}]},
        ]}
        result = _messages(state)
        assert result[0]["role"] == "assistant"
        assert "tool_calls" in result[0]


class TestMergeToolBlock:
    """Verify _merge_tool_block sets required fields."""

    def test_new_block_has_id(self):
        """First chunk (with name) must create a block with a valid id."""
        blocks: list[dict] = []
        chunk = {"function": {"name": "get_cpu", "arguments": "{}"}}
        _merge_tool_block(blocks, chunk)
        assert len(blocks) == 1
        assert "id" in blocks[0]
        assert blocks[0]["id"] != ""

    def test_chunk_with_id_preserves_it(self):
        """If the chunk carries an id, it should be used."""
        blocks: list[dict] = []
        chunk = {"id": "explicit-id", "function": {"name": "get_cpu", "arguments": "{}"}}
        _merge_tool_block(blocks, chunk)
        assert blocks[0]["id"] == "explicit-id"

    def test_arguments_only_chunk_appends(self):
        """Arguments-only chunk appends to the last block without creating a new one."""
        blocks = [{"id": "t1", "function": {"name": "get_cpu", "arguments": '{"unit":'}}]
        chunk = {"function": {"arguments": '"percent"}'}}
        _merge_tool_block(blocks, chunk)
        assert len(blocks) == 1
        assert blocks[0]["function"]["arguments"] == '{"unit":"percent"}'

    def test_new_block_has_type_function(self):
        """Every tool_call block must have type=function (DeepSeek/OpenAI API requirement)."""
        blocks: list[dict] = []
        chunk = {"function": {"name": "get_cpu", "arguments": "{}"}}
        _merge_tool_block(blocks, chunk)
        assert blocks[0]["type"] == "function"

    def test_arguments_only_block_has_type_function(self):
        """Arguments-only chunk that creates a new block must also have type=function."""
        blocks: list[dict] = []
        chunk = {"function": {"name": "", "arguments": '{"cmd":"ls"}'}}
        _merge_tool_block(blocks, chunk)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "function"
        assert blocks[0]["function"]["name"] == ""
        assert blocks[0]["function"]["arguments"] == '{"cmd":"ls"}'


class TestReasoningStreaming:
    """B2a: think_node captures reasoning and content in ThinkOutput.stream_chunks."""

    async def test_streams_reasoning_to_queue(self):
        """reasoning_content in assistant events → captured in ThinkOutput.stream_chunks."""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "Let me", "reasoning_content": "Let me"})},
            {"event": "assistant", "data": json.dumps({"delta": " check", "reasoning_content": " check"})},
            {"event": "done", "data": "{}"},
        ])
        ctx = TurnContext(chat_id=None, turn_id=uuid4(), iteration=1, model="test-model")

        result = await think_node(_state(), ctx, llm=llm)

        assert result.assistant_message["reasoning_content"] == "Let me check"
        # stream_chunks records (type, delta) tuples for later emission by EventEmitter
        assert len(result.stream_chunks) == 4  # 2 reasoning + 2 assistant
        types = [t for t, _ in result.stream_chunks]
        assert "reasoning" in types

    async def test_no_queue_no_crash(self):
        """When ctx is None, reasoning still works."""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "OK", "reasoning_content": "think"})},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)
        assert result.assistant_message["reasoning_content"] == "think"
        assert result.is_done is True

    async def test_content_streamed_to_queue_without_message_id(self):
        """Content chunks are captured in ThinkOutput.stream_chunks as AssistantDelta-type tuples."""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "Hello "})},
            {"event": "assistant", "data": json.dumps({"delta": "world"})},
            {"event": "done", "data": "{}"},
        ])
        ctx = TurnContext(chat_id=None, turn_id=uuid4(), iteration=1, model="test-model")

        result = await think_node(_state(), ctx, llm=llm)

        assert result.assistant_message["content"] == "Hello world"
        # stream_chunks records (type, delta) tuples for later emission by EventEmitter
        assert len(result.stream_chunks) == 2
        assert result.stream_chunks == [("assistant", "Hello "), ("assistant", "world")]

    async def test_no_reasoning_no_queue_events(self):
        """When there's no reasoning_content, only content chunk captured in stream_chunks."""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "CPU normal."})},
            {"event": "done", "data": "{}"},
        ])
        ctx = TurnContext(chat_id=None, turn_id=uuid4(), iteration=1, model="test-model")

        result = await think_node(_state(), ctx, llm=llm)

        # stream_chunks records (type, delta) tuples for later emission by EventEmitter
        assert len(result.stream_chunks) == 1
        assert result.stream_chunks == [("assistant", "CPU normal.")]

    async def test_reasoning_streamed_directly(self):
        """think_node captures streaming content in ThinkOutput."""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "Hello", "reasoning_content": "thinking"})},
            {"event": "done", "data": "{}"},
        ])
        ctx = TurnContext(chat_id=None, turn_id=uuid4(), iteration=1, model="test-model")

        result = await think_node(_state(), ctx, llm=llm)

        assert result.assistant_message["content"] == "Hello"
        assert result.assistant_message["reasoning_content"] == "thinking"
