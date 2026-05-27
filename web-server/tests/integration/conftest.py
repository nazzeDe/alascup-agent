import os

import pytest

from src.services.db import CREATE_TABLES_SQL, Database

DEFAULT_TEST_DSN = "postgresql://nazze:1115@localhost:5432/alascup_agent"


@pytest.fixture(scope="session")
def pg_dsn():
    """DSN for the test PostgreSQL database."""
    return os.environ.get("DATABASE_TEST_URL", DEFAULT_TEST_DSN)


@pytest.fixture
async def test_db(pg_dsn):
    """Real PostgreSQL database with schema, auto-truncated after each test."""
    db = Database(pg_dsn)
    await db.connect()

    yield db

    # Truncate all tables (order matters for FK constraints)
    async with db.pool.acquire() as conn:
        tables = [
            "graph_writes", "graph_checkpoints",
            "tool_calls",
            "messages", "audit_events", "llm_traces",
            "chat_sessions",
        ]
        for table in tables:
            await conn.execute(f"TRUNCATE TABLE {table} CASCADE")

    await db.disconnect()
