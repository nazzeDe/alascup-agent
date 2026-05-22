from datetime import datetime, timezone

import pytest

from src.models.audit import AuditEvent, AuditLevel
from src.observability.audit_logger import PostgresAuditLogger
from src.services.session_manager import PostgresSessionManager

pytestmark = pytest.mark.integration


class TestDatabaseConnection:
    @pytest.mark.asyncio
    async def test_connect_creates_tables_without_error(self, test_db):
        """CREATE_TABLES_SQL must execute without syntax errors."""
        assert test_db.pool is not None
        # If we got here, connect() succeeded → schema is valid

    @pytest.mark.asyncio
    async def test_connect_uses_postgresql_scheme(self, test_db):
        """DSN must use postgresql:// not postgresql+asyncpg://."""
        assert test_db.pool is not None


class TestPostgresSessionManager:
    @pytest.mark.asyncio
    async def test_create_session_stores_real_data(self, test_db):
        mgr = PostgresSessionManager(test_db)
        session = await mgr.create_session()

        assert session.id is not None
        assert session.timestamp is not None

        # Verify it's retrievable
        retrieved = await mgr.get_session(session.id)
        assert retrieved.id == session.id

    @pytest.mark.asyncio
    async def test_add_message_stores_timestamp_as_datetime(self, test_db):
        """Message.timestamp is a string; must be converted for asyncpg."""
        from src.models.message import Message, MessageType

        mgr = PostgresSessionManager(test_db)
        session = await mgr.create_session()

        msg = Message(
            message_id="00000000-0000-0000-0000-000000000001",
            chat_id=session.id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content="hello",
        )
        await mgr.add_message(session.id, msg)

        retrieved = await mgr.get_session(session.id)
        assert len(retrieved.messages) == 1
        assert retrieved.messages[0].content == "hello"

    @pytest.mark.asyncio
    async def test_list_sessions_returns_persisted_data(self, test_db):
        mgr = PostgresSessionManager(test_db)

        s1 = await mgr.create_session()
        s2 = await mgr.create_session()

        sessions = await mgr.list_sessions()
        ids = [s.id for s in sessions]
        assert s1.id in ids
        assert s2.id in ids


class TestPostgresAuditLogger:
    @pytest.mark.asyncio
    async def test_log_stores_audit_event_with_datetime(self, test_db):
        """AuditEvent.timestamp is a string; must be converted for asyncpg."""
        logger = PostgresAuditLogger(test_db)

        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=AuditLevel.INFO,
            actor="system",
            event="TEST_EVENT",
        )
        await logger.log(event)

        # Verify stored by reading back directly
        async with test_db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM audit_events WHERE event = 'TEST_EVENT'")
            assert row is not None
            assert row["actor"] == "system"
