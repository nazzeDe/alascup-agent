"""Tracks SSE emission state to avoid re-sending across iterations."""


class EmissionTracker:
    """Tracks SSE emission state to avoid re-sending across iterations."""

    def __init__(self, initial_assistant_count: int = 0):
        self.assistant_count: int = initial_assistant_count
        self.emitted_result_ids: set[str] = set()

    def record_assistant_emitted(self) -> None:
        """Increment assistant emission counter."""
        self.assistant_count += 1

    def mark_tool_results_emitted(self, results: list[dict]) -> None:
        """Record tool_call_ids as emitted."""
        for r in results:
            tid = r.get("tool_call_id", "")
            if tid:
                self.emitted_result_ids.add(tid)

    def get_new_tool_results(self, all_results: list[dict]) -> list[dict]:
        """Return only results whose tool_call_id is not yet emitted."""
        return [r for r in all_results
                if r.get("tool_call_id", "") not in self.emitted_result_ids]

    def advance_iteration(self) -> None:
        """Clear iteration-level state. assistant_count persists."""
        self.emitted_result_ids.clear()
