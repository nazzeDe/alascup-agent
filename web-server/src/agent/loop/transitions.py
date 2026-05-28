"""Typed state transitions and transient field management.

Centralizes the list of per-iteration fields so new fields can't be
forgotten during cleanup.
"""

from src.agent.state import Transition

TRANSIENT_FIELDS = (
    "tool_calls",
    "approved_tool_calls",
    "rejected_tool_calls",
    "pending_approval",
    "tool_results",
    "streaming_tool_results",
)


def clear_transient_fields(state: dict) -> None:
    """Reset all per-iteration fields after processing."""
    for key in TRANSIENT_FIELDS:
        state[key] = []


def has_pending_approval(state: dict) -> bool:
    """Check if review_node returned pending tool calls needing human approval."""
    return bool(state.get("pending_approval"))


def get_transition(state: dict) -> Transition | None:
    return state.get("transition")
