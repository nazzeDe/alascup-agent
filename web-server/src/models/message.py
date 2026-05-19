from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class MessageType(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"


class Message(BaseModel):
    message_id: UUID
    chat_id: UUID
    timestamp: str
    type: MessageType
    content: str
    is_meta: bool = Field(default=False)
