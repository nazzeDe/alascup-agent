from dataclasses import dataclass, field

from src.agent.domain import AgentMessage, AgentToolCall, AgentToolResult
from src.agent.state import LLMError, StreamChunk


@dataclass
class ThinkOutput:
    assistant_message: AgentMessage | None = None
    tool_calls: list[AgentToolCall] = field(default_factory=list)
    pre_executed: list[AgentToolResult] = field(default_factory=list)
    is_done: bool = False
    stream_chunks: list[StreamChunk] = field(default_factory=list)
    llm_error: LLMError | None = None


@dataclass
class ReviewOutput:
    approved: list[AgentToolCall] = field(default_factory=list)
    rejected: list[AgentToolCall] = field(default_factory=list)
    pending: list[AgentToolCall] = field(default_factory=list)
    transition: str | None = None


@dataclass
class ExecuteOutput:
    results: list[AgentToolResult] = field(default_factory=list)


@dataclass
class ObserveOutput:
    tool_messages: list[AgentMessage] = field(default_factory=list)
    emitted_results: list[AgentToolResult] = field(default_factory=list)
    transition: str = ""
