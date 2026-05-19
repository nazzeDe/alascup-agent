class ErrorRecovery:
    """Manages LLM error recovery strategies per doc/详细设计.md"""

    _RECOVERY_CHAIN = {
        "prompt_too_long": [
            {"action": "compress_context", "layer": 1},
            {"action": "aggressive_compress", "layer": 2},
        ],
        "max_output_tokens": [
            {"action": "escalate_token_limit", "layer": 1},
            {"action": "continue_inject", "layer": 2},
        ],
        "model_unavailable": [
            {"action": "switch_fallback_model", "layer": 1},
        ],
        "server_error": [
            {"action": "switch_fallback_model", "layer": 1},
        ],
    }

    _NON_RECOVERABLE = {"rate_limit", "auth_failed", "timeout", "unknown"}

    def __init__(self) -> None:
        self._attempts: dict[str, list[str]] = {}
        self._continue_count: int = 0

    def get_strategy(self, error_type: str) -> dict:
        if error_type in self._NON_RECOVERABLE:
            return {"recoverable": False, "action": "surface_error", "layer": 0}

        chain = self._RECOVERY_CHAIN.get(error_type, [])
        attempted = self._attempts.get(error_type, [])

        if error_type == "max_output_tokens" and self._continue_count >= 3:
            return {"recoverable": False, "action": "surface_error", "layer": 3}

        if len(attempted) < len(chain):
            return {**chain[len(attempted)], "recoverable": True}

        return {"recoverable": False, "action": "surface_error", "layer": len(chain) + 1}

    def record_attempt(self, error_type: str, action: str) -> None:
        if error_type not in self._attempts:
            self._attempts[error_type] = []
        self._attempts[error_type].append(action)
        if action == "continue_inject":
            self._continue_count += 1

    def reset_turn(self) -> None:
        self._attempts.clear()
        self._continue_count = 0
