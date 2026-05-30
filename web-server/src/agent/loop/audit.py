"""Audit event helpers for the agent loop — writes to audit_events table."""

from datetime import datetime, timezone
from uuid import UUID

from src.agent.state import Transition
from src.models.audit import AuditEvent, AuditLevel


async def audit_transition(
    audit_logger,
    transition: Transition,
    *,
    chat_id: UUID | None = None,
    turn_id: UUID | None = None,
    iteration: int | None = None,
) -> None:
    if audit_logger is None:
        return
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        chat_id=chat_id,
        turn_id=turn_id,
        iteration=iteration,
        level=AuditLevel.INFO,
        actor="system",
        event="LOOP_TRANSITION",
        transition=transition.value,
    ))
