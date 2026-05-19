"""Audit logging helpers for the agent loop."""

from datetime import datetime, timezone

from src.agent.state import Transition
from src.models.audit import AuditEvent, AuditLevel


async def log_transition(audit_logger, transition: Transition) -> None:
    if audit_logger is None:
        return
    await audit_logger.log(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        level=AuditLevel.INFO,
        actor="system",
        event="LOOP_TRANSITION",
        transition=transition.value,
    ))
