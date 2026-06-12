from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypedDict

ROLE_MAP = {"human": "user", "ai": "assistant", "tool": "tool", "tool_result": "tool", "tool_call": "assistant"}


class Transition(StrEnum):
    """循环状态变更原因。每个节点返回时附带，写入 audit_events。"""
    USER_MESSAGE = "user_message"
    TOOL_RESULTS = "tool_results"
    APPROVAL_PENDING = "approval_pending"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    CONTEXT_COMPACTED = "context_compacted"
    MAX_OUTPUT_TOKENS_RECOVERY = "max_output_tokens_recovery"
    MODEL_FALLBACK = "model_fallback"
    TURN_LIMIT_EXCEEDED = "turn_limit_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    DONE = "done"
    ERROR_EXIT = "error_exit"


@dataclass
class TurnScratch:
    """Per-iteration scratch data. Recreated each iteration — no manual cleanup."""
    tool_calls: list = field(default_factory=list)
    pending_approval: list = field(default_factory=list)
    approved_tool_calls: list = field(default_factory=list)
    rejected_tool_calls: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    streaming_tool_results: list = field(default_factory=list)
    _emitted_results: list = field(default_factory=list)
    stream_chunks: list = field(default_factory=list)
    llm_error: dict | None = None
    transition: str | None = None


def init_scratch() -> TurnScratch:
    """Return a fresh TurnScratch for a new iteration."""
    return TurnScratch()


def get_transition(scratch: TurnScratch) -> Transition | None:
    """Get the current transition from scratch state."""
    return scratch.transition


def has_pending_approval(scratch: TurnScratch) -> bool:
    """Check if review_node returned pending tool calls needing human approval."""
    return bool(scratch.pending_approval)


class AgentState(TypedDict, total=False):
    messages: list
    system: str | None
    available_tools: list
    transition: Transition | None
    tool_calls: list
    approved_tool_calls: list
    rejected_tool_calls: list
    pending_approval: list
    tool_results: list
    llm_error: dict | None
    streaming_tool_results: list
    _emitted_results: list
    stream_chunks: list
