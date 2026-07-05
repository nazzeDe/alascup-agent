"""TurnContext — per-turn observability context (frozen).  Auditor — wraps
audit_logger + TurnContext to collapse duplicated chat_id/turn_id/iteration/model kwarg blocks."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
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


class StreamSink(Protocol):
    def emit_stream_delta(self, chunk_type: str, delta: str) -> None:
        """Emit one streaming text chunk."""

    def emit_stream_done(self) -> None:
        """Emit stream boundary events."""


@dataclass(frozen=True)
class TurnContext:
    chat_id: UUID | None
    turn_id: UUID
    iteration: int
    model: str | None
    stream_sink: StreamSink | None = None

    def evolve(self, *, iteration: int | None = None) -> "TurnContext":
        return TurnContext(
            chat_id=self.chat_id,
            turn_id=self.turn_id,
            iteration=iteration if iteration is not None else self.iteration,
            model=self.model,
            stream_sink=self.stream_sink,
        )


class Auditor:
    """Wraps audit_logger + TurnContext to collapse duplicated kwarg blocks.

    Methods correspond to the patterns found at the actual call sites:

    1) audit_transition(...) with chat_id/turn_id/iteration/model/actor — used in
       orchestrator.py, approval.py, handlers/error.py, handlers/interrupt.py
    2) audit_logger.log(AuditEvent(...)) with manually repeated turn context fields — used in
       approval.py (_audit_approved/_audit_rejected), act.py (_audit_tool_executed),
       review.py (_audit_review_decision)
    """

    def __init__(self, audit_logger, ctx: TurnContext | None = None):
        self._audit = audit_logger
        self._ctx = ctx

    @property
    def ctx(self) -> TurnContext | None:
        return self._ctx

    # ── transition helper (collapses orchestrator/approval/error/interrupt) ──

    async def transition(
        self,
        transition: Transition,
        *,
        actor: AuditActor,
        request_id: UUID | None = None,
        params: dict | None = None,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.log(
            AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                chat_id=self._ctx.chat_id if self._ctx else None,
                request_id=request_id,
                turn_id=self._ctx.turn_id if self._ctx else None,
                iteration=self._ctx.iteration if self._ctx else None,
                level=AuditLevel.INFO,
                actor=actor.value,
                event="LOOP_TRANSITION",
                params=params,
                model=self._ctx.model if self._ctx else None,
                transition=transition.value,
            )
        )

    # ── tool_event helper (collapses act.py _audit_tool_executed, review.py _audit_review_decision,
    #    approval.py _audit_approved / _audit_rejected) ──

    async def tool_event(
        self,
        event: str,
        *,
        actor: AuditActor,
        tool_name: str | None = None,
        request_id: UUID | None = None,
        params: dict | None = None,
        level: AuditLevel | None = None,
        decision: str | None = None,
        execution_status: str | None = None,
        **kwargs,
    ) -> None:
        if self._audit is None:
            return
        if level is None:
            level = AuditLevel.WARN if "REJECT" in event else AuditLevel.INFO
        await self._audit.log(
            AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                chat_id=self._ctx.chat_id if self._ctx else None,
                request_id=request_id,
                turn_id=self._ctx.turn_id if self._ctx else None,
                iteration=self._ctx.iteration if self._ctx else None,
                level=level,
                actor=actor.value,
                event=event,
                tool_name=tool_name,
                params=params,
                model=self._ctx.model if self._ctx else None,
                decision=decision,
                execution_status=execution_status,
                **{k: v for k, v in kwargs.items() if v is not None},
            )
        )
