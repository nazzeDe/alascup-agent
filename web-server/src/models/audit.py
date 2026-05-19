from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AuditLevel(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AuditEvent(BaseModel):
    timestamp: str
    session_id: UUID | None = None
    request_id: UUID | None = None
    level: AuditLevel
    actor: str
    event: str
    tool_name: str | None = None
    params: dict[str, Any] | None = None
    model: str | None = None
    decision: str | None = None
    execution_status: str | None = None
    backup_ref: str | None = None
    transition: str | None = None
    error: dict[str, Any] | None = None
