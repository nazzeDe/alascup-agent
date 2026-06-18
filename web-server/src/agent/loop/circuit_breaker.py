"""Circuit breaker — hard limits to prevent token explosion and infinite loops.

Extracted from LoopOrchestrator. Uses the same hard-limit pattern as agent
systems with max-turn controls. No interactive pause — just stop and report.
"""


class CircuitBreaker:
    """Hard limits to prevent token explosion and infinite loops."""

    def __init__(self, max_iterations: int, token_ceiling: int):
        self.max_iterations = max_iterations
        self.token_ceiling = token_ceiling
        self._last_hint: str | None = None

    def check_iteration(self, it: int) -> bool:
        """Return True if iteration count exceeds max."""
        return it > self.max_iterations

    def check_token_ceiling(self, tokens: int) -> bool:
        """Return True if token count exceeds hard ceiling."""
        return tokens > self.token_ceiling

    def inject_hint(self, state: dict, it: int) -> str | None:
        """Inject progressive hints to LLM when approaching iteration limit.

        Injects once at 70% threshold, then replaces with a stronger hint
        when <=3 turns remain. Never appends -- hint is replaced not accumulated.
        """
        remaining = self.max_iterations - it
        if remaining <= 3:
            hint = f"\n\n[SYSTEM] Only {remaining} turns remaining. Conclude immediately with a summary of what you know."
        elif it >= int(self.max_iterations * 0.7):
            hint = "\n\n[SYSTEM] Approaching turn limit. Prioritize completion — skip non-critical investigation."
        else:
            return None

        system = state.get("system") or ""
        prev = self._last_hint
        if prev and prev in system:
            system = system.replace(prev, hint)
        else:
            system += hint
        self._last_hint = hint
        state["system"] = system
        return hint
