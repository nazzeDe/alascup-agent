import pytest
from pydantic import ValidationError

from src.agent.state import AgentState, Transition, TurnScratch


class TestAgentState:
    def test_defaults_are_independent_lists(self):
        left = AgentState()
        right = AgentState()

        left.messages.append({"role": "user", "content": "hi"})

        assert right.messages == []

    def test_rejects_dict_style_access(self):
        state = AgentState(system="base")

        with pytest.raises(TypeError):
            _ = state["system"]

    def test_validates_transition_enum(self):
        with pytest.raises(ValidationError):
            AgentState(transition="not-a-transition")


class TestTurnScratch:
    def test_uses_pydantic_model(self):
        scratch = TurnScratch(transition=Transition.DONE)

        assert scratch.model_dump()["transition"] == Transition.DONE


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
            "turn_limit_exceeded",
            "token_budget_exceeded",
            "done",
            "error_exit",
        }
        actual = {t.value for t in Transition}
        assert actual == expected
