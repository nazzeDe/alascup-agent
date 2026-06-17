
from src.agent.state import Transition


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


