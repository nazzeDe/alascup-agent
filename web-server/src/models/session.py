from uuid import UUID

from pydantic import BaseModel, Field

from src.models.message import Message
from src.models.tool import ToolCall


class ChatSession(BaseModel):
    id: UUID = Field(serialization_alias="chat_id")
    title: str | None = None
    messages: list[Message] = Field(default_factory=list)
    executed_tool_list: list[ToolCall] = Field(default_factory=list)
    turn_count: int = 0
    transition: str = ""
    timestamp: str
    deleted: bool = False
