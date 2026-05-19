"""Typed state transitions and transient field management.

Centralizes the list of per-iteration fields so new fields can't be
forgotten during cleanup.
"""

from src.agent.state import Transition

TRANSIENT_FIELDS = (
    "tool_calls",
    "approved_tool_calls",
    "rejected_tool_calls",
    "tool_results",
    "streaming_tool_results",
)


def clear_transient_fields(state: dict) -> None:
    """Reset all per-iteration fields after processing."""
    for key in TRANSIENT_FIELDS:
        state[key] = []


def has_interrupt(state: dict) -> bool:
    """Check if graph execution was suspended for human approval."""
    return bool(state.get("__interrupt__"))


def get_transition(state: dict) -> Transition | None:
    return state.get("transition")
