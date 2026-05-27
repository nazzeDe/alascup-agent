from enum import StrEnum
from typing import Any
from uuid import UUID

from langgraph.graph import MessagesState

ROLE_MAP = {"human": "user", "ai": "assistant", "tool": "tool"}


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
    DONE = "done"
    ERROR_EXIT = "error_exit"


class AgentState(MessagesState):
    system: str | None = None # type: ignore
    available_tools: list
    transition: Transition | None = None # type: ignore
    tool_calls: list
    approved_tool_calls: list
    rejected_tool_calls: list
    pending_approval: list
    tool_results: list
    llm_error: dict | None = None # type: ignore
    streaming_tool_results: list
    direct_tool_results: list
    _emitted_results: list
    # Observability: set by orchestrator before each graph invocation.
    _turn_id: Any = None  # UUID, but Any avoids LangGraph annotation issues
    _iteration: int | None = None
