import json

import pytest

from src.agent.nodes import act_node, observe_node, think_node
from src.agent.state import AgentState, Transition


class MockLLM:
    """模拟 LLM 流式输出。"""

    def __init__(self, events: list[dict]):
        self._events = events
        self._call_count = 0

    async def generate_stream(self, messages, tools=None, system=None):
        self._call_count += 1
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

        assert result["transition"] == Transition.DONE
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "assistant"
        assert "CPU" in result["messages"][0]["content"]

    async def test_text_accumulates_multiple_deltas(self):
        """多个 text delta 拼接成完整 assistant 消息。"""
        llm = MockLLM([
            {"event": "assistant", "data": json.dumps({"delta": "Hello "})},
            {"event": "assistant", "data": json.dumps({"delta": "World"})},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert result["messages"][0]["content"] == "Hello World"
        assert result["transition"] == Transition.DONE

    async def test_tool_call_only_no_text(self):
        """LLM 只产出 tool_call，无文本 → 返回 tool_calls 列表。"""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "get_cpu", "arguments": '{"unit":"percent"}'}
            })},
            {"event": "done", "data": "{}"},
        ])
        result = await think_node(_state(), llm=llm)

        assert result["transition"] is None  # 不设 transition——由后续节点决定
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "get_cpu"

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

        assert result["messages"][0]["role"] == "assistant"
        assert result["messages"][0]["content"] == "Let me check."
        assert len(result["tool_calls"]) == 1
        assert result["transition"] is None  # 有 tool_call，不应设 DONE

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

        assert len(result["tool_calls"]) == 2
        assert result["tool_calls"][0]["function"]["name"] == "get_cpu"
        assert result["tool_calls"][1]["function"]["name"] == "get_memory"

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

        assert len(result["tool_calls"]) == 1
        fn = result["tool_calls"][0]["function"]
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
        assert len(result["tool_results"]) == 2

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
        assert len(result["tool_results"]) == 2

    async def test_empty_approved_list_returns_nothing(self):
        executor = MockExecutor()
        result = await act_node(_state_with_tools([]), executor=executor)

        assert result["tool_results"] == []
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

        assert result["tool_results"][0]["result"]["execution_status"] == "FAILED"
        assert "permission denied" in str(result["tool_results"][0]["result"])

    async def test_tool_results_include_tool_name(self):
        executor = MockExecutor()
        state = _state_with_tools([
            {"function": {"name": "get_cpu", "arguments": "{}"}},
        ])
        result = await act_node(state, executor=executor)

        assert result["tool_results"][0]["tool_name"] == "get_cpu"


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

        assert len(result["streaming_tool_results"]) == 1
        assert result["streaming_tool_results"][0]["tool_name"] == "get_cpu"
        assert result["tool_calls"] == []

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

        assert result["streaming_tool_results"] == []
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "restart_service"

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

        assert len(result["streaming_tool_results"]) == 1
        assert result["streaming_tool_results"][0]["tool_name"] == "get_cpu"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "restart_service"

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

        assert result["streaming_tool_results"] == []
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["function"]["name"] == "bash"
        assert tc["mutable"] is True
        assert tc["server_name"] == "tool-server"

    async def test_prefix_parsing_attaches_server_name(self):
        """Server prefix is parsed once and attached to the tool_call dict (Q24)."""
        llm = MockLLM([
            {"event": "tool_call", "data": json.dumps({
                "function": {"name": "rag-server__search_experience", "arguments": "{}"}
            })},
            {"event": "done", "data": "{}"},
        ])
        executor = MockExecutor()
        state = self._state_with_tools([
            {"name": "search_experience", "server_name": "rag-server", "mutable": False, "is_read_only": True},
        ])

        result = await think_node(state, llm=llm, executor=executor)

        assert result["tool_calls"] == []
        assert len(result["streaming_tool_results"]) == 1
        assert result["streaming_tool_results"][0]["tool_name"] == "search_experience"


class TestObserveNode:
    """对应 tests/README.md AG-001 后半段：tool_result → assistant message 流转。"""

    def test_appends_tool_results_as_messages(self):
        state = _state_with_tools()
        result = observe_node(state, tool_results=[
            {"tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED", "output": "CPU: 45%"}},
            {"tool_name": "get_memory", "result": {"execution_status": "SUCCEEDED", "output": "Mem: 8G"}},
        ])

        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "tool"
        assert "get_cpu" in result["messages"][0]["content"]
        assert "get_memory" in result["messages"][1]["content"]
        assert result["transition"] == Transition.TOOL_RESULTS

    def test_empty_results_returns_empty(self):
        result = observe_node(_state_with_tools(), tool_results=[])

        assert result["messages"] == []
        assert result["transition"] == Transition.TOOL_RESULTS

    def test_merges_streaming_tool_results(self):
        """AG-007: observe_node 合并 streaming_tool_results 和 tool_results。"""
        state = _state_with_tools()
        state["streaming_tool_results"] = [
            {"tool_name": "get_cpu", "result": {"execution_status": "SUCCEEDED", "output": "CPU: 45%"}},
        ]

        result = observe_node(state, tool_results=[
            {"tool_name": "get_memory", "result": {"execution_status": "SUCCEEDED", "output": "Mem: 8G"}},
        ])

        assert len(result["messages"]) == 2
        assert result["transition"] == Transition.TOOL_RESULTS
