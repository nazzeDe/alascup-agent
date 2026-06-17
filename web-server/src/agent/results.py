from dataclasses import dataclass, field

@dataclass
class ThinkOutput:
    assistant_message: dict | None = None
    tool_calls: list = field(default_factory=list)
    pre_executed: list = field(default_factory=list)
    is_done: bool = False
    stream_chunks: list = field(default_factory=list)  # (type, delta) tuples for streaming
    llm_error: dict | None = None


@dataclass
class ReviewOutput:
    approved: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    transition: str | None = None


@dataclass
class ExecuteOutput:
    results: list = field(default_factory=list)


@dataclass
class ObserveOutput:
    tool_messages: list = field(default_factory=list)
    emitted_results: list = field(default_factory=list)
    transition: str = ""
