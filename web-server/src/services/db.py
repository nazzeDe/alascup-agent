from urllib.parse import urlparse

import asyncpg
from loguru import logger

CREATE_TABLES_SQL = """
DO $$ BEGIN CREATE TYPE msg_type AS ENUM ('user', 'assistant', 'tool_call', 'tool_result', 'system'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE approval_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE execution_status AS ENUM ('PENDING_APPROVAL', 'RUNNING', 'SUCCEEDED', 'FAILED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE audit_level AS ENUM ('INFO', 'WARN', 'ERROR', 'CRITICAL'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY,
    title VARCHAR(256),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted BOOLEAN NOT NULL DEFAULT false
);

-- Soft migration for existing databases
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS deleted BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY,
    chat_id UUID NOT NULL REFERENCES chat_sessions(id),
    timestamp TIMESTAMPTZ NOT NULL,
    type msg_type NOT NULL,
    content TEXT NOT NULL,
    is_meta BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_time ON messages(chat_id, timestamp);

CREATE TABLE IF NOT EXISTS tool_calls (
    id UUID PRIMARY KEY,
    chat_id UUID NOT NULL REFERENCES chat_sessions(id),
    message_id UUID REFERENCES messages(id),
    tool_name VARCHAR(128) NOT NULL,
    server_name VARCHAR(32) NOT NULL,
    is_read_only BOOLEAN NOT NULL,
    is_rollbackable BOOLEAN NOT NULL,
    params JSONB NOT NULL DEFAULT '{}',
    request_id UUID,
    approval_status approval_status NOT NULL,
    execution_status execution_status NOT NULL,
    error JSONB,
    backup_ref VARCHAR(256),
    llm_trace_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ
);

-- Soft migrations for existing databases
ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_calls JSONB;
ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS llm_trace_id UUID;
ALTER TABLE tool_calls ALTER COLUMN message_id DROP NOT NULL;
ALTER TABLE tool_calls DROP CONSTRAINT IF EXISTS tool_calls_message_id_fkey;
ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS result JSONB;
ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS llm_tool_call_id TEXT;
CREATE INDEX IF NOT EXISTS idx_tool_calls_trace ON tool_calls(llm_trace_id);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    chat_id UUID,
    request_id UUID,
    turn_id UUID,
    iteration INTEGER,
    level audit_level NOT NULL,
    actor VARCHAR(32) NOT NULL,  -- AuditActor enum: user, agent, tool, policy, system
    event VARCHAR(64) NOT NULL,
    tool_name VARCHAR(128),
    params JSONB,
    model VARCHAR(64),
    decision VARCHAR(16),
    execution_status VARCHAR(16),
    backup_ref VARCHAR(256),
    transition VARCHAR(32),
    error JSONB
);

-- Soft migration for existing databases
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS transition VARCHAR(32);

CREATE TABLE IF NOT EXISTS llm_traces (
    id UUID PRIMARY KEY,
    chat_id UUID NOT NULL REFERENCES chat_sessions(id),
    model VARCHAR(64) NOT NULL,
    prompt_text JSONB NOT NULL,
    completion_text JSONB,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool | None:
        return self._pool

    async def connect(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
        except asyncpg.InvalidCatalogNameError:
            # Database doesn't exist — create it via the default 'postgres' database
            parsed = urlparse(self._dsn)
            target_db = parsed.path.lstrip("/")
            admin_dsn = self._dsn.rsplit("/", 1)[0] + "/postgres"
            logger.info("Database '%s' not found, creating...", target_db)
            admin_conn = await asyncpg.connect(admin_dsn)
            try:
                await admin_conn.execute(f'CREATE DATABASE "{target_db}"')
            finally:
                await admin_conn.close()
            self._pool = await asyncpg.create_pool(self._dsn, min_size=2, max_size=10)
        except OSError as exc:
            raise RuntimeError(
                f"PostgreSQL is not reachable at {self._dsn}: {exc}"
            ) from exc
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TABLES_SQL)

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, *args) -> str:
        async with self._pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)
