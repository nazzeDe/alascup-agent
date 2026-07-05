from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class AuditLevel(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AuditActor(StrEnum):
    """Who or what triggered this audit event.

    Stored in audit_events.actor VARCHAR(32).
    """

    USER = "user"  # User sent a message → USER_MESSAGE transition
    AGENT = "agent"  # LLM/agent loop routing decisions (DONE, TOOL_RESULTS)
    TOOL = "tool"  # Tool execution events (TOOL_EXECUTED)
    POLICY = "policy"  # Rule engine / review decisions (approvals, rejections)
    SYSTEM = "system"  # Infrastructure: context compaction, token limits, errors


class AuditEvent(BaseModel):
    """Single row in the audit_events table.

    Hierarchy:
      chat_id   — UUID of the chat session (spans many turns)
      turn_id   — UUID of a single user message + its full ReAct loop
      iteration — 1-based counter within a turn (resets to 1 each turn)
      id        — BIGSERIAL PK, provides global event ordering across all sessions

    Relationship:
      request_id — links to tool_calls.request_id (populated for tool events)
    """

    timestamp: str
    chat_id: UUID | None = None
    request_id: UUID | None = None
    turn_id: UUID | None = None
    iteration: int | None = None
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
