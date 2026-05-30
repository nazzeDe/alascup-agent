"""Audit event helpers for the agent loop — writes to audit_events table."""

from datetime import datetime, timezone
from uuid import UUID

from src.agent.state import Transition
from src.models.audit import AuditActor, AuditEvent, AuditLevel


def _safe_uuid(value: str | None) -> UUID | None:
    """Parse a UUID string, silently returning None for invalid/missing values."""
    if not value:
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


async def audit_transition(
    audit_logger,
    transition: Transition,
    *,
    chat_id: UUID | None = None,
    turn_id: UUID | None = None,
    iteration: int | None = None,
    actor: AuditActor = AuditActor.SYSTEM,
    request_id: UUID | None = None,
    params: dict | None = None,
    model: str | None = None,
) -> None:
    if audit_logger is None:
        return
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        chat_id=chat_id,
        request_id=request_id,
        turn_id=turn_id,
        iteration=iteration,
        level=AuditLevel.INFO,
        actor=actor.value,
        event="LOOP_TRANSITION",
        params=params,
        model=model,
        transition=transition.value,
    ))
