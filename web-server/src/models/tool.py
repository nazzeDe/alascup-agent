from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, computed_field


class ServerName(StrEnum):
    TOOL_SERVER = "tool-server"
    RAG_SERVER = "rag-server"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ExecutionStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Tool(BaseModel):
    name: str
    server: ServerName
    description: str
    is_read_only: bool
    is_rollbackable: bool
    params_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCall(Tool):
    chat_id: UUID
    message_id: UUID
    call_id: UUID | None = Field(default=None)
    llm_trace_id: UUID | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: UUID | None = None
    approval_status: ApprovalStatus
    execution_status: ExecutionStatus
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    timestamp: str

    @computed_field
    @property
    def tool_name(self) -> str:
        """Duplicate of name for frontend compatibility with SSE wire format."""
        return self.name


class ToolRequest(Tool):
    chat_id: UUID
    message_id: UUID
    request_id: UUID
    params: dict[str, Any] = Field(default_factory=dict)
    approval_status: ApprovalStatus
    created_at: str
    approved_at: str | None = None
    expired_at: str | None = None
    rejected_reason: str | None = None


class ToolApproval(BaseModel):
    approval_status: ApprovalStatus
    reason: str | None = None


class ToolResult(BaseModel):
    execution_status: ExecutionStatus
    backup_ref: str | None = None
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
