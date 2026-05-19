import pytest

from src.agent.state import AgentState, Transition


class MockClassifier:
    """返回固定的分级结果。"""

    def __init__(self, is_read_only=True, is_rollbackable=True):
        self._readonly = is_read_only
        self._rollbackable = is_rollbackable

    async def classify(self, tool_name: str, params: dict) -> dict:
        return {"is_read_only": self._readonly, "is_rollbackable": self._rollbackable}


class MockRuleEngine:
    """规则匹配：名称含 `blacklist` 的→拒绝，名称含 `whitelist` 的→放行，其余按分级。"""

    @staticmethod
    def evaluate(tool_name: str, is_read_only: bool, is_rollbackable: bool) -> str:
        if "blacklist" in tool_name:
            return "REJECT"
        if "whitelist" in tool_name:
            return "AUTO_APPROVE"
        if is_read_only:
            return "AUTO_APPROVE"
        return "NEEDS_APPROVAL"


class MockAuditLogger:
    def __init__(self):
        self.events: list[dict] = []

    async def log(self, event):
        self.events.append({
            "level": event.level,
            "event": event.event,
            "tool_name": event.tool_name,
            "decision": event.decision,
        })


def _state(tool_calls=None):
    return AgentState(
        messages=[{"role": "user", "content": "check system"}],
        available_tools=[],
        tool_calls=tool_calls or [],
        transition=None,
    )


class TestReviewNode:
    async def test_read_only_tools_auto_approved(self):
        from src.agent.nodes import review_node

        tool_calls = [
            {"function": {"name": "get_cpu", "arguments": "{}"}},
            {"function": {"name": "get_memory", "arguments": "{}"}},
        ]
        classifier = MockClassifier(is_read_only=True)
        rule_engine = MockRuleEngine()
        audit = MockAuditLogger()

        result = await review_node(
            _state(tool_calls), classifier=classifier, rule_engine=rule_engine, audit_logger=audit,
        )

        assert len(result["approved_tool_calls"]) == 2
        assert result["approved_tool_calls"][0]["function"]["name"] == "get_cpu"
        assert result["approved_tool_calls"][1]["function"]["name"] == "get_memory"
        assert result["rejected_tool_calls"] == []

    async def test_blacklisted_tools_rejected(self):
        from src.agent.nodes import review_node

        tool_calls = [
            {"function": {"name": "blacklist_cmd", "arguments": "{}"}},
            {"function": {"name": "get_cpu", "arguments": "{}"}},
        ]
        classifier = MockClassifier(is_read_only=True)
        rule_engine = MockRuleEngine()
        audit = MockAuditLogger()

        result = await review_node(
            _state(tool_calls), classifier=classifier, rule_engine=rule_engine, audit_logger=audit,
        )

        assert len(result["approved_tool_calls"]) == 1
        assert result["approved_tool_calls"][0]["function"]["name"] == "get_cpu"
        assert len(result["rejected_tool_calls"]) == 1
        assert result["rejected_tool_calls"][0]["function"]["name"] == "blacklist_cmd"

    async def test_whitelist_overrides_classification(self):
        """白名单优先于分级：即使分级为高风险，whitelist 也直接放行。"""
        from src.agent.nodes import review_node

        tool_calls = [
            {"function": {"name": "whitelist_cleanup", "arguments": "{}"}},
        ]
        classifier = MockClassifier(is_read_only=False, is_rollbackable=False)
        rule_engine = MockRuleEngine()
        audit = MockAuditLogger()

        result = await review_node(
            _state(tool_calls), classifier=classifier, rule_engine=rule_engine, audit_logger=audit,
        )

        assert len(result["approved_tool_calls"]) == 1
        assert result["rejected_tool_calls"] == []

    async def test_high_risk_triggers_interrupt(self):
        """高风险工具（非只读、非白名单、非黑名单）→ interrupt() 暂停。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        tool_calls = [
            {"function": {"name": "delete_logs", "arguments": '{"path":"/var/log"}'}},
        ]
        classifier = MockClassifier(is_read_only=False, is_rollbackable=False)
        rule_engine = MockRuleEngine()
        audit = MockAuditLogger()

        mock_interrupt = MagicMock(return_value={"decisions": ["APPROVED"]})

        with patch("src.agent.nodes.interrupt", mock_interrupt):
            from src.agent.nodes import review_node
            result = await review_node(
                _state(tool_calls), classifier=classifier, rule_engine=rule_engine, audit_logger=audit,
            )

        mock_interrupt.assert_called_once()
        interrupt_arg = mock_interrupt.call_args[0][0]
        assert interrupt_arg["event"] == "approval_required"
        assert len(interrupt_arg["pending_tool_calls"]) == 1
        assert interrupt_arg["pending_tool_calls"][0]["function"]["name"] == "delete_logs"
        # 用户批准后 → 加入 approved
        assert len(result["approved_tool_calls"]) == 1

    async def test_interrupt_resume_rejected(self):
        """用户拒绝 → 工具加入 rejected 列表。"""
        from unittest.mock import MagicMock, patch

        tool_calls = [
            {"function": {"name": "delete_logs", "arguments": "{}"}},
        ]
        classifier = MockClassifier(is_read_only=False, is_rollbackable=False)
        rule_engine = MockRuleEngine()
        audit = MockAuditLogger()

        mock_interrupt = MagicMock(return_value={"decisions": ["REJECTED"]})

        with patch("src.agent.nodes.interrupt", mock_interrupt):
            from src.agent.nodes import review_node
            result = await review_node(
                _state(tool_calls), classifier=classifier, rule_engine=rule_engine, audit_logger=audit,
            )

        assert result["approved_tool_calls"] == []
        assert len(result["rejected_tool_calls"]) == 1

    async def test_mixed_classifications(self):
        """混合场景：只读放行、黑名单拒绝、高风险暂停。"""
        from unittest.mock import MagicMock, patch

        tool_calls = [
            {"function": {"name": "get_cpu", "arguments": "{}"}},          # 只读
            {"function": {"name": "blacklist_cmd", "arguments": "{}"}},    # 黑名单
            {"function": {"name": "delete_logs", "arguments": "{}"}},      # 高风险
            {"function": {"name": "get_memory", "arguments": "{}"}},       # 只读
        ]
        classifier = MockClassifier(is_read_only=True, is_rollbackable=True)
        rule_engine = MockRuleEngine()
        audit = MockAuditLogger()

        mock_interrupt = MagicMock(return_value={"decisions": ["APPROVED"]})

        with patch("src.agent.nodes.interrupt", mock_interrupt):
            from src.agent.nodes import review_node
            result = await review_node(
                _state(tool_calls), classifier=classifier, rule_engine=rule_engine, audit_logger=audit,
            )

        names = [tc["function"]["name"] for tc in result["approved_tool_calls"]]
        assert "get_cpu" in names
        assert "get_memory" in names
        assert "delete_logs" in names
        assert "blacklist_cmd" not in names
        assert len(result["rejected_tool_calls"]) == 1

    async def test_audit_logged_for_rejected(self):
        """黑名单拒绝时写审计日志。"""
        from src.agent.nodes import review_node

        tool_calls = [
            {"function": {"name": "blacklist_cmd", "arguments": "{}"}},
        ]
        classifier = MockClassifier()
        rule_engine = MockRuleEngine()
        audit = MockAuditLogger()

        await review_node(
            _state(tool_calls), classifier=classifier, rule_engine=rule_engine, audit_logger=audit,
        )

        assert len(audit.events) == 1
        assert audit.events[0]["event"] == "TOOL_REJECTED"
        assert audit.events[0]["tool_name"] == "blacklist_cmd"
        assert audit.events[0]["decision"] == "REJECT"

    async def test_transition_when_all_auto_approved(self):
        from src.agent.nodes import review_node

        tool_calls = [
            {"function": {"name": "get_cpu", "arguments": "{}"}},
        ]
        result = await review_node(
            _state(tool_calls),
            classifier=MockClassifier(is_read_only=True),
            rule_engine=MockRuleEngine(),
            audit_logger=MockAuditLogger(),
        )

        assert result["transition"] == Transition.APPROVAL_GRANTED

    async def test_transition_when_all_rejected(self):
        from src.agent.nodes import review_node

        tool_calls = [
            {"function": {"name": "blacklist_cmd", "arguments": "{}"}},
        ]
        result = await review_node(
            _state(tool_calls),
            classifier=MockClassifier(),
            rule_engine=MockRuleEngine(),
            audit_logger=MockAuditLogger(),
        )

        assert result["transition"] == Transition.APPROVAL_REJECTED

    async def test_no_tool_calls_returns_empty(self):
        from src.agent.nodes import review_node

        result = await review_node(
            _state([]),
            classifier=MockClassifier(),
            rule_engine=MockRuleEngine(),
            audit_logger=MockAuditLogger(),
        )

        assert result["approved_tool_calls"] == []
        assert result["rejected_tool_calls"] == []
