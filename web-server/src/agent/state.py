from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from src.agent.domain import AgentMessage, AgentToolCall, AgentToolResult

ROLE_MAP = {"human": "user", "ai": "assistant", "tool": "tool", "tool_result": "tool", "tool_call": "assistant"}

StreamChunk = tuple[str, str]
LLMError = dict[str, object]


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
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"  # noqa: S105 - transition label, not secret.
    DONE = "done"
    ERROR_EXIT = "error_exit"


class AgentState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    messages: list[AgentMessage] = Field(default_factory=list)
    system: str | None = None
    available_tools: list[dict] = Field(default_factory=list)
    transition: Transition | None = None
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    approved_tool_calls: list[AgentToolCall] = Field(default_factory=list)
    rejected_tool_calls: list[AgentToolCall] = Field(default_factory=list)
    pending_approval: list[AgentToolCall] = Field(default_factory=list)
    tool_results: list[AgentToolResult] = Field(default_factory=list)
    llm_error: LLMError | None = None
    streaming_tool_results: list[AgentToolResult] = Field(default_factory=list)
    emitted_results: list[AgentToolResult] = Field(default_factory=list)
    stream_chunks: list[StreamChunk] = Field(default_factory=list)
    chat_id: str = ""
    llm_model: str = ""


class TurnScratch(BaseModel):
    """Per-iteration scratch data. Recreated each iteration."""

    model_config = ConfigDict(validate_assignment=True)

    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    pending_approval: list[AgentToolCall] = Field(default_factory=list)
    approved_tool_calls: list[AgentToolCall] = Field(default_factory=list)
    rejected_tool_calls: list[AgentToolCall] = Field(default_factory=list)
    tool_results: list[AgentToolResult] = Field(default_factory=list)
    streaming_tool_results: list[AgentToolResult] = Field(default_factory=list)
    emitted_results: list[AgentToolResult] = Field(default_factory=list)
    stream_chunks: list[StreamChunk] = Field(default_factory=list)
    llm_error: LLMError | None = None
    transition: Transition | None = None


def init_scratch() -> TurnScratch:
    """Return a fresh TurnScratch for a new iteration."""
    return TurnScratch()


def get_transition(scratch: TurnScratch) -> Transition | None:
    """Get the current transition from scratch state."""
    return scratch.transition
