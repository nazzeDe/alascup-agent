from uuid import UUID

from pydantic import BaseModel, Field

from src.models.message import Message
from src.models.tool import ToolCall


class ChatSession(BaseModel):
    id: UUID
    title: str | None = None
    messages: list[Message] = Field(default_factory=list)
    executed_tool_list: list[ToolCall] = Field(default_factory=list)
    turn_count: int = 0
    max_turns: int = 30
    transition: str = ""
    timestamp: str
