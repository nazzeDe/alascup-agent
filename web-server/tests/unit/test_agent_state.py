import pytest

from src.agent.state import AgentState, Transition


class TestTransition:
    def test_all_transitions_match_doc(self):
        """Transition 枚举值必须与 web-server/README.md Transition 追踪表一致。"""
        expected = {
            "user_message",
            "tool_results",
            "approval_pending",
            "approval_granted",
            "approval_rejected",
            "context_compacted",
            "max_output_tokens_recovery",
            "model_fallback",
            "done",
            "error_exit",
        }
        actual = {t.value for t in Transition}
        assert actual == expected


class TestAgentState:
    def test_fields_exist_with_defaults(self):
        """AgentState 包含 messages、available_tools、transition 三个字段。"""
        state = AgentState(
            messages=[{"role": "user", "content": "hello"}],
            available_tools=[],
            transition=None,
        )
        assert state["messages"][0]["content"] == "hello"
        assert state["available_tools"] == []
        assert state["transition"] is None

    def test_transition_is_writable(self):
        """transition 字段可被覆盖，每个节点返回时设置当前步骤的变迁原因。"""
        state = AgentState(
            messages=[],
            available_tools=[],
            transition=Transition.USER_MESSAGE,
        )
        assert state["transition"] == "user_message"
