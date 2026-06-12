from dataclasses import dataclass, field

from src.agent.state import Transition


@dataclass
class ThinkOutput:
    assistant_message: dict | None = None
    tool_calls: list = field(default_factory=list)
    pre_executed: list = field(default_factory=list)
    is_done: bool = False
    stream_chunks: list = field(default_factory=list)  # (type, delta) tuples for streaming
    llm_error: dict | None = None

    def to_state_dict(self) -> dict:
        """Map typed fields back to the legacy state-dict keys consumers expect."""
        result: dict = {}
        if self.llm_error is not None:
            result["messages"] = []
            result["tool_calls"] = []
            result["llm_error"] = self.llm_error
            result["transition"] = Transition.ERROR_EXIT
            return result
        if self.assistant_message:
            result["messages"] = [self.assistant_message]
        result["tool_calls"] = self.tool_calls
        result["streaming_tool_results"] = self.pre_executed
        result["stream_chunks"] = self.stream_chunks
        result["transition"] = Transition.DONE if self.is_done else None
        return result


@dataclass
class ReviewOutput:
    approved: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    transition: str | None = None

    def to_state_dict(self) -> dict:
        """Map typed fields back to the legacy state-dict keys."""
        return {
            "approved_tool_calls": self.approved,
            "rejected_tool_calls": self.rejected,
            "pending_approval": self.pending,
            "transition": self.transition,
        }


@dataclass
class ExecuteOutput:
    results: list = field(default_factory=list)

    def to_state_dict(self) -> dict:
        """Map typed fields back to the legacy state-dict keys."""
        return {
            "tool_results": self.results,
            "approved_tool_calls": [],
        }


@dataclass
class ObserveOutput:
    tool_messages: list = field(default_factory=list)
    emitted_results: list = field(default_factory=list)
    transition: str = ""

    def to_state_dict(self) -> dict:
        """Map typed fields back to the legacy state-dict keys."""
        return {
            "messages": self.tool_messages,
            "streaming_tool_results": [],
            "tool_results": [],
            "rejected_tool_calls": [],
            "_emitted_results": self.emitted_results,
            "transition": self.transition,
        }
