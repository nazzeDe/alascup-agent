import json

from src.models.audit import AuditEvent


class AuditLogger:
    async def log(self, event: AuditEvent) -> None:
        raise NotImplementedError


class InMemoryAuditLogger(AuditLogger):
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def log(self, event: AuditEvent) -> None:
        self.events.append(event)


class PostgresAuditLogger(AuditLogger):
    def __init__(self, db) -> None:
        self._db = db

    async def log(self, event: AuditEvent) -> None:
        await self._db.execute(
            """INSERT INTO audit_events (timestamp, chat_id, request_id, level, actor,
               event, tool_name, params, model, decision, execution_status, backup_ref, error)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)""",
            event.timestamp,
            event.session_id,
            event.request_id,
            event.level.value,
            event.actor,
            event.event,
            event.tool_name,
            json.dumps(event.params) if event.params else None,
            event.model,
            event.decision,
            event.execution_status,
            event.backup_ref,
            json.dumps(event.error) if event.error else None,
        )
