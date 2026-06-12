import pytest

pytestmark = pytest.mark.unit


class TestLLMErrorClassification:
    def test_classify_prompt_too_long(self):
        from src.services.llm_adapter import classify_error

        error_type = classify_error(413, "Request too large")
        assert error_type == "prompt_too_long"

    def test_classify_max_output_tokens(self):
        from src.services.llm_adapter import classify_error

        error_type = classify_error(200, "", stop_reason="max_tokens")
        assert error_type == "max_output_tokens"

    def test_classify_rate_limit(self):
        from src.services.llm_adapter import classify_error

        error_type = classify_error(429, "Rate limit exceeded")
        assert error_type == "rate_limit"

    def test_classify_auth_failed(self):
        from src.services.llm_adapter import classify_error

        assert classify_error(401, "") == "auth_failed"
        assert classify_error(403, "") == "auth_failed"

    def test_classify_model_unavailable(self):
        from src.services.llm_adapter import classify_error

        error_type = classify_error(404, "model not found")
        assert error_type == "model_unavailable"

    def test_classify_server_error(self):
        from src.services.llm_adapter import classify_error

        assert classify_error(500, "") == "server_error"
        assert classify_error(503, "") == "server_error"
        assert classify_error(529, "") == "server_error"

    def test_classify_timeout(self):
        from src.services.llm_adapter import classify_error

        error_type = classify_error(0, "connection timeout")
        assert error_type == "timeout"

    def test_classify_success_returns_none(self):
        from src.services.llm_adapter import classify_error

        assert classify_error(200, "") is None

    def test_classify_400_message_format_error(self):
        """400 with missing field error → prompt_too_long (recoverable)."""
        from src.services.llm_adapter import classify_error

        error_type = classify_error(400, "messages[1]: missing field 'type'")
        assert error_type == "prompt_too_long"

    def test_classify_400_content_filter(self):
        """400 content filter rejection → prompt_too_long (recoverable via compress)."""
        from src.services.llm_adapter import classify_error

        error_type = classify_error(400, "content filter triggered")
        assert error_type == "prompt_too_long"

    def test_classify_400_max_tokens_params(self):
        """400 with max_tokens in params → unknown (params issue, not recoverable)."""
        from src.services.llm_adapter import classify_error

        error_type = classify_error(400, "max_tokens must be positive")
        assert error_type == "unknown"


class TestErrorRecovery:
    def test_prompt_too_long_compress_recovery(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        strategy = recovery.get_strategy("prompt_too_long")
        assert strategy["recoverable"] is True
        assert strategy["layer"] == 1
        assert strategy["action"] == "compress_context"

    def test_prompt_too_long_aggressive_recovery(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("prompt_too_long", "compress_context")
        strategy = recovery.get_strategy("prompt_too_long")
        assert strategy["action"] == "aggressive_compress"

    def test_prompt_too_long_exhausted(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("prompt_too_long", "compress_context")
        recovery.record_attempt("prompt_too_long", "aggressive_compress")
        strategy = recovery.get_strategy("prompt_too_long")
        assert strategy["recoverable"] is False

    def test_max_output_tokens_escalate(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        strategy = recovery.get_strategy("max_output_tokens")
        assert strategy["recoverable"] is True
        assert strategy["action"] == "escalate_token_limit"

    def test_max_output_tokens_continue_inject(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("max_output_tokens", "escalate_token_limit")
        strategy = recovery.get_strategy("max_output_tokens")
        assert strategy["action"] == "continue_inject"

    def test_max_output_tokens_continue_limit(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("max_output_tokens", "escalate_token_limit")
        for _ in range(3):
            recovery.record_attempt("max_output_tokens", "continue_inject")
        strategy = recovery.get_strategy("max_output_tokens")
        assert strategy["recoverable"] is False

    def test_model_fallback_recovery(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        strategy = recovery.get_strategy("model_unavailable")
        assert strategy["recoverable"] is True
        assert strategy["action"] == "switch_fallback_model"

    def test_model_fallback_already_attempted(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("server_error", "switch_fallback_model")
        strategy = recovery.get_strategy("server_error")
        assert strategy["recoverable"] is False

    def test_non_recoverable_errors(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        for error_type in ["rate_limit", "auth_failed", "timeout", "unknown"]:
            strategy = recovery.get_strategy(error_type)
            assert strategy["recoverable"] is False

    def test_reset_on_new_turn(self):
        from src.services.error_recovery import ErrorRecovery

        recovery = ErrorRecovery()
        recovery.record_attempt("prompt_too_long", "compress_context")
        recovery.reset_turn()
        strategy = recovery.get_strategy("prompt_too_long")
        assert strategy["layer"] == 1
