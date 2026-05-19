"""Query 测试。对应 tests/README.md AG-001, AG-003~AG-007。"""

import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.agent.graph import build_graph
from src.agent.query import Query


class MockLLM:
    def __init__(self, responses: list[dict] | None = None):
        self.responses = responses or [{"content": "no tools needed"}]
        self._idx = 0
        self.escalated = False
        self.fallback_switched = False

    async def generate_stream(self, messages, tools=None, system=None):
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
        self.escalated = True

    def switch_to_fallback(self) -> None:
        self.fallback_switched = True


class MockClassifier:
    def __init__(self, is_read_only=True, threshold_risky=False):
        self._readonly = is_read_only
        self._threshold_risky = threshold_risky

    async def classify(self, tool_name, params):
        if self._threshold_risky and tool_name in ("restart_service", "delete_logs", "reboot_system"):
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

    async def execute(self, tool_name, arguments, **kwargs):
        self.calls.append({"tool_name": tool_name, "arguments": arguments})
        return {"execution_status": "SUCCEEDED", "output": f"result of {tool_name}"}

    async def execute_parallel(self, calls: list[dict]) -> list[dict]:
        import asyncio
        tasks = [self.execute(c["tool_name"], c.get("arguments", {})) for c in calls]
        return await asyncio.gather(*tasks)


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


pytestmark = pytest.mark.asyncio


async def _collect(gen):
    events = []
    async for e in gen:
        events.append((e["event"], e["data"]))
    return events


@pytest.fixture
def llm():
    return MockLLM()


@pytest.fixture
def classifier():
    return MockClassifier()


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
def graph(llm, executor, classifier, rule_engine, audit):
    return build_graph(llm=llm, executor=executor, classifier=classifier,
                       rule_engine=rule_engine, audit_logger=audit,
                       checkpointer=MemorySaver())


@pytest.fixture
def query(llm, graph, context_manager, bridge, audit):
    return Query(llm=llm, graph=graph, context_manager=context_manager,
                 pending_approvals=bridge, audit_logger=audit)


class TestQueryBasic:
    """AG-001: ReAct 基本循环。"""

    async def test_text_only_response_yields_assistant_and_done(self, query, llm):
        llm.responses = [{"content": "CPU is normal.", "tool_calls": None}]
        events = await _collect(query.run(
            [{"role": "user", "content": "check CPU"}], available_tools=[],
        ))
        event_types = [e[0] for e in events]
        assert event_types == ["assistant", "done"]

    async def test_tool_call_auto_approve_then_done(self, query, llm):
        """读工具 → AUTO_APPROVE → 执行 → 结果推回 → LLM 输出结论 → done。"""
        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
            ]},
            {"content": "CPU is 85%.", "tool_calls": None},
        ]
        events = await _collect(query.run(
            [{"role": "user", "content": "check CPU"}], available_tools=[],
        ))
        event_types = [e[0] for e in events]
        assert "tool_call" in event_types
        assert "tool_result" in event_types
        assert "assistant" in event_types
        assert event_types[-1] == "done"


class TestQueryHighRisk:
    """AG-004: 高风险工具审批。"""

    async def test_high_risk_yields_approval_required_and_resumes(self, query, llm, bridge, executor, rule_engine, audit):
        """高风险 tool_call → interrupt → yield approval_required → resolve → 继续执行。"""
        query._graph = build_graph(llm=llm, executor=executor, classifier=MockClassifier(is_read_only=True, threshold_risky=True),
                                   rule_engine=rule_engine, audit_logger=audit, checkpointer=MemorySaver())

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Service restarted.", "tool_calls": None},
        ]
        events: list[tuple] = []

        # Phase 1: run until interrupt
        async for e in query.run(
            [{"role": "user", "content": "restart"}], available_tools=[],
        ):
            events.append((e["event"], e["data"]))

        # Phase 2: resume
        async for e in query.resume(["APPROVED"]):
            events.append((e["event"], e["data"]))

        event_types = [e[0] for e in events]
        assert "tool_approval_required" in event_types
        assert "assistant" in event_types
        assert event_types[-1] == "done"
        assert executor.calls


class TestQueryTransitionTracking:
    """AG-006: transition 追踪写入审计日志。"""

    async def test_transitions_logged_to_audit(self, query, llm, audit):
        llm.responses = [{"content": "done.", "tool_calls": None}]
        await _collect(query.run(
            [{"role": "user", "content": "test"}], available_tools=[],
        ))
        transition_events = [e for e in audit.events if e.event == "LOOP_TRANSITION"]
        transitions = [e.transition for e in transition_events]
        assert "user_message" in transitions
        assert "done" in transitions


class TestQueryExit:
    """退出条件：LLM 无 tool_call AND stop_reason=end 时 transition=done 退出。"""

    async def test_exits_when_llm_produces_text_without_tool_calls(self, query, llm):
        llm.responses = [{"content": "All good.", "tool_calls": None}]
        events = await _collect(query.run(
            [{"role": "user", "content": "test"}], available_tools=[],
        ))
        assert events[-1][0] == "done"

    async def test_continues_when_tool_calls_present(self, query, llm, executor):
        """有 tool_call 时不应立即 done——LLM 需要基于 tool_result 继续推理。"""
        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
            ]},
            {"content": "Result analyzed.", "tool_calls": None},
        ]
        events = await _collect(query.run(
            [{"role": "user", "content": "check"}], available_tools=[],
        ))
        assert events[-1][0] == "done"
        assert executor.calls


class TestQueryConcurrent:
    """AG-003, AG-004: 并发 tool_call。"""

    async def test_concurrent_readonly_tools_executed(self, query, llm, executor):
        """AG-003: LLM 单次返回两个只读 tool_call → 并行执行 → 两个结果。"""
        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
                {"function": {"name": "get_memory", "arguments": "{}"}},
            ]},
            {"content": "Both checked.", "tool_calls": None},
        ]
        events = await _collect(query.run(
            [{"role": "user", "content": "check both"}], available_tools=[],
        ))
        event_types = [e[0] for e in events]
        assert event_types.count("tool_call") == 2
        assert event_types.count("tool_result") == 2
        assert len(executor.calls) == 2
        assert events[-1][0] == "done"

    async def test_concurrent_readonly_tools_single_parallel_call(self, query, llm, executor):
        """AG-003: 验证只读工具通过 execute_parallel 一次调用。"""
        parallel_called = False
        original = executor.execute_parallel

        async def spy_parallel(calls):
            nonlocal parallel_called
            parallel_called = True
            return await original(calls)

        executor.execute_parallel = spy_parallel

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
                {"function": {"name": "get_memory", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]
        await _collect(query.run(
            [{"role": "user", "content": "check"}], available_tools=[],
        ))
        assert parallel_called

    async def test_mixed_readonly_highrisk(self, query, llm, bridge, executor, rule_engine, audit):
        """AG-004: 一个只读 + 一个高风险 → 只读和审批通过的工具最终都执行。"""
        query._graph = build_graph(llm=llm, executor=executor, classifier=MockClassifier(is_read_only=True, threshold_risky=True),
                                   rule_engine=rule_engine, audit_logger=audit, checkpointer=MemorySaver())

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Handled.", "tool_calls": None},
        ]
        events: list[tuple] = []

        # Phase 1: run until interrupt
        async for e in query.run(
            [{"role": "user", "content": "check and restart"}], available_tools=[],
        ):
            events.append((e["event"], e["data"]))

        # Phase 2: resume
        async for e in query.resume(["APPROVED"]):
            events.append((e["event"], e["data"]))

        event_types = [e[0] for e in events]
        assert "tool_approval_required" in event_types
        assert "tool_result" in event_types
        assert events[-1][0] == "done"
        assert len(executor.calls) == 2


class TestQueryStreamingExecution:
    """AG-007: 流式工具执行——只读工具在 think 阶段即被分发。"""

    async def test_readonly_tools_pre_executed_skip_review_act(self, query, llm, executor):
        """只读 tool_call 被 think_node 预执行，跳过 review/act，结果直接推送。"""
        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
                {"function": {"name": "get_memory", "arguments": "{}"}},
            ]},
            {"content": "Both checked.", "tool_calls": None},
        ]
        events = await _collect(query.run(
            [{"role": "user", "content": "check both"}], available_tools=[],
        ))
        event_types = [e[0] for e in events]
        # tool_call events from think_node emit; tool_result events from observe_node
        assert event_types.count("tool_call") == 2
        assert event_types.count("tool_result") == 2
        assert len(executor.calls) >= 2
        assert events[-1][0] == "done"

    async def test_mixed_streaming_highrisk_still_reviewed(self, query, llm, bridge, executor, rule_engine, audit):
        """混合：只读预执行，高风险走审查 → interrupt → 审批 → 执行。"""
        query._graph = build_graph(llm=llm, executor=executor, classifier=MockClassifier(is_read_only=True, threshold_risky=True),
                                   rule_engine=rule_engine, audit_logger=audit, checkpointer=MemorySaver())

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "get_cpu", "arguments": "{}"}},
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]
        events: list[tuple] = []

        # Phase 1: run until interrupt
        async for e in query.run(
            [{"role": "user", "content": "check and restart"}], available_tools=[],
        ):
            events.append((e["event"], e["data"]))

        # Phase 2: resume
        async for e in query.resume(["APPROVED"]):
            events.append((e["event"], e["data"]))

        event_types = [e[0] for e in events]
        assert "tool_approval_required" in event_types
        assert "tool_result" in event_types
        assert events[-1][0] == "done"
        assert len(executor.calls) >= 2


class _FakeCompressingContextManager:
    """压缩总是触发，用于错误恢复测试。"""
    def count_tokens(self, messages):
        return 1000

    def needs_compression(self, tokens):
        return True

    async def compress(self, messages):
        return [{"role": "system", "content": "[summary] compressed"}]


class TestQueryErrorRecovery:
    """EH-003, EH-004, EH-005: LLM 错误恢复链。"""

    @pytest.fixture
    def recovery_loop(self, llm, graph, bridge, audit):
        from src.services.error_recovery import ErrorRecovery

        return Query(
            llm=llm, graph=graph,
            context_manager=_FakeCompressingContextManager(),
            pending_approvals=bridge, audit_logger=audit,
            error_recovery=ErrorRecovery(),
        )

    async def test_prompt_too_long_compress_then_retry(self, recovery_loop, llm, audit):
        """EH-003: LLM 返回 prompt_too_long → 压缩 → 重试 → 成功。"""
        llm.responses = [
            {"error": {"code": 413, "message": "prompt too long"}},
            {"content": "recovered response", "tool_calls": None},
        ]
        events = await _collect(recovery_loop.run(
            [{"role": "user", "content": "test"}], available_tools=[],
        ))
        assert events[-1][0] == "done"
        transitions = [e.transition for e in audit.events if e.event == "LOOP_TRANSITION"]
        assert "context_compacted" in transitions

    async def test_prompt_too_long_exhausted_yields_error(self, recovery_loop, llm, audit):
        """EH-003 恢复链耗尽 → 推送 error 事件 → error_exit。"""
        llm.responses = [
            {"error": {"code": 413, "message": "prompt too long"}},
            {"error": {"code": 413, "message": "prompt too long"}},
            {"error": {"code": 413, "message": "prompt too long"}},
        ]
        events = await _collect(recovery_loop.run(
            [{"role": "user", "content": "test"}], available_tools=[],
        ))
        event_types = [e[0] for e in events]
        assert "error" in event_types
        transitions = [e.transition for e in audit.events if e.event == "LOOP_TRANSITION"]
        assert "error_exit" in transitions

    async def test_max_output_tokens_continue_then_retry(self, recovery_loop, llm):
        """EH-004: max_output_tokens → escalate → retry → success。"""
        llm.responses = [
            {"error": {"code": 200, "message": "", "stop_reason": "max_tokens"}},
            {"content": "continued response", "tool_calls": None},
        ]
        events = await _collect(recovery_loop.run(
            [{"role": "user", "content": "test"}], available_tools=[],
        ))
        assert events[-1][0] == "done"

    async def test_model_unavailable_fallback_then_retry(self, recovery_loop, llm):
        """EH-005: model_unavailable → fallback → retry → success。"""
        llm.responses = [
            {"error": {"code": 503, "message": "service unavailable"}},
            {"content": "fallback response", "tool_calls": None},
        ]
        events = await _collect(recovery_loop.run(
            [{"role": "user", "content": "test"}], available_tools=[],
        ))
        assert events[-1][0] == "done"

    async def test_non_recoverable_error_surfaces_immediately(self, recovery_loop, llm, audit):
        """不可恢复错误（rate_limit）→ 直接推送 error，不重试。"""
        llm.responses = [
            {"error": {"code": 429, "message": "rate limited"}},
        ]
        events = await _collect(recovery_loop.run(
            [{"role": "user", "content": "test"}], available_tools=[],
        ))
        assert events[0][0] == "error"
        transitions = [e.transition for e in audit.events if e.event == "LOOP_TRANSITION"]
        assert "error_exit" in transitions


class TestQueryFullChainAudit:
    """AL-001: 全链路审计事件链完整性。"""

    async def test_full_audit_chain_high_risk(self, query, llm, bridge, audit, executor, rule_engine):
        """高风险操作完整审计链：TOOL_REQUEST_CREATED → TOOL_APPROVED → TOOL_EXECUTED。"""
        query._graph = build_graph(llm=llm, executor=executor, classifier=MockClassifier(is_read_only=True, threshold_risky=True),
                                   rule_engine=rule_engine, audit_logger=audit, checkpointer=MemorySaver())

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "restart_service", "arguments": "{}"}},
            ]},
            {"content": "Service restarted.", "tool_calls": None},
        ]

        # Phase 1: run until interrupt
        async for e in query.run(
            [{"role": "user", "content": "restart"}], available_tools=[],
        ):
            pass

        # Phase 2: resume after approval
        async for e in query.resume(["APPROVED"]):
            pass

        audit_events = [e.event for e in audit.events]
        assert "TOOL_REQUEST_CREATED" in audit_events
        assert "TOOL_APPROVED" in audit_events
        assert "TOOL_EXECUTED" in audit_events
        # Verify order: CREATED before APPROVED before EXECUTED
        created_idx = audit_events.index("TOOL_REQUEST_CREATED")
        approved_idx = audit_events.index("TOOL_APPROVED")
        executed_idx = audit_events.index("TOOL_EXECUTED")
        assert created_idx < approved_idx < executed_idx

    async def test_full_audit_chain_mixed(self, query, llm, bridge, audit, executor, rule_engine):
        """混合场景：TOOL_REQUEST_CREATED → TOOL_APPROVED → TOOL_EXECUTED 链路完整。"""
        query._graph = build_graph(llm=llm, executor=executor, classifier=MockClassifier(is_read_only=True, threshold_risky=True),
                                   rule_engine=rule_engine, audit_logger=audit, checkpointer=MemorySaver())

        llm.responses = [
            {"content": "", "tool_calls": [
                {"function": {"name": "restart_service", "arguments": "{}"}},
                {"function": {"name": "delete_logs", "arguments": "{}"}},
            ]},
            {"content": "Done.", "tool_calls": None},
        ]

        # Phase 1: run until interrupt
        async for e in query.run(
            [{"role": "user", "content": "restart and clean"}], available_tools=[],
        ):
            pass

        # Phase 2: resume after approval
        async for e in query.resume(["APPROVED", "APPROVED"]):
            pass

        audit_events = [e.event for e in audit.events]
        assert "TOOL_REQUEST_CREATED" in audit_events
        assert "TOOL_APPROVED" in audit_events
        # 两个高风险工具都审批通过并执行
        assert audit_events.count("TOOL_EXECUTED") >= 2
