"""Internal agent domain model.

Wire protocols stay outside this module. OpenAI, MCP, SSE, and DB shapes are
projected through mappers.
"""

import json
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ToolFunction(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def _parse_arguments(cls, value):
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                return json.loads(value) if value else {}
            except json.JSONDecodeError:
                return {"_raw": value}
        return value


class AgentToolCall(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    function: ToolFunction
    server_name: str = ""
    mutable: bool = False
    is_read_only: bool | None = None
    is_rollbackable: bool = False
    approval_status: str | None = None
    request_id: str | None = None
    call_id: UUID | None = None
    rejection_reason: str | None = None


class AgentToolResult(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    tool_name: str
    tool_call_id: str
    result: dict[str, Any] = Field(default_factory=dict)
    is_read_only: bool = False
    is_rollbackable: bool = False
    server_name: str = ""
    call_id: UUID | None = None


class AgentMessage(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    role: Literal["user", "assistant", "tool", "system"]
    content: str = ""
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    reasoning_content: str | None = None

    @field_validator("role", mode="before")
    @classmethod
    def _normalize_role(cls, value):
        return {
            "human": "user",
            "ai": "assistant",
            "tool_result": "tool",
            "tool_call": "assistant",
        }.get(value, value)
