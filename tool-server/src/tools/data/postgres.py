from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import asyncpg

from src.config import ToolServerConfig

_ALLOWED_START = re.compile(r"^\s*(select|with|explain)\b", re.IGNORECASE | re.DOTALL)
_DANGEROUS_WORDS = re.compile(
    r"\b("
    r"alter|analyze|begin|call|checkpoint|close|cluster|comment|commit|copy|create|"
    r"deallocate|delete|discard|do|drop|execute|grant|insert|listen|load|lock|merge|"
    r"move|notify|prepare|reassign|refresh|reindex|release|reset|revoke|rollback|"
    r"savepoint|security|set|truncate|unlisten|update|vacuum"
    r")\b",
    re.IGNORECASE,
)


async def get_postgres_schema(config: ToolServerConfig, schema: str = "public") -> dict[str, Any]:
    if not config.postgres_dsn:
        return _missing_dsn()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        return {"error": "invalid schema"}

    query = """
        select
            c.table_schema,
            c.table_name,
            c.column_name,
            c.data_type,
            c.is_nullable,
            c.column_default
        from information_schema.columns c
        join information_schema.tables t
          on t.table_schema = c.table_schema
         and t.table_name = c.table_name
        where c.table_schema = $1
          and t.table_type = 'BASE TABLE'
        order by c.table_schema, c.table_name, c.ordinal_position
    """
    rows = await _fetch(config, query, schema)
    tables: dict[str, dict[str, Any]] = {}
    for row in rows:
        table = tables.setdefault(
            row["table_name"],
            {"schema": row["table_schema"], "table": row["table_name"], "columns": []},
        )
        table["columns"].append({
            "name": row["column_name"],
            "data_type": row["data_type"],
            "nullable": row["is_nullable"] == "YES",
            "default": row["column_default"],
        })
    return {"schema": schema, "tables": list(tables.values()), "table_count": len(tables)}


async def postgres_readonly_query(
    config: ToolServerConfig,
    sql: str,
    max_rows: int = 200,
) -> dict[str, Any]:
    if not config.postgres_dsn:
        return _missing_dsn()

    ok, error = _validate_readonly_sql(sql)
    if not ok:
        return {"error": error}

    row_limit = min(max(max_rows, 1), config.postgres_max_rows)
    timeout_seconds = max(config.postgres_statement_timeout_ms, 1) / 1000
    normalized_sql = sql.rstrip().rstrip(";")
    rows = await _fetch_limited(config, normalized_sql, limit=row_limit + 1, timeout=timeout_seconds)
    truncated = len(rows) > row_limit
    limited_rows = rows[:row_limit]
    return {
        "columns": list(limited_rows[0].keys()) if limited_rows else [],
        "rows": [_record_to_dict(row) for row in limited_rows],
        "row_count": len(limited_rows),
        "truncated": truncated,
        "max_rows": row_limit,
    }


def _validate_readonly_sql(sql: str) -> tuple[bool, str]:
    stripped = sql.strip()
    if not stripped:
        return False, "sql is required"
    if _has_multiple_statements(stripped):
        return False, "multiple statements are not allowed"
    scrubbed = _strip_literals_and_comments(stripped)
    if not _ALLOWED_START.match(scrubbed):
        return False, "only SELECT, WITH, and EXPLAIN are allowed"
    if _DANGEROUS_WORDS.search(scrubbed):
        return False, "write, DDL, transaction, and session-control statements are not allowed"
    return True, ""


def _has_multiple_statements(sql: str) -> bool:
    scrubbed = _strip_literals_and_comments(sql)
    return ";" in scrubbed.rstrip(";")


def _strip_literals_and_comments(sql: str) -> str:
    scanner = _SqlScrubber(sql)
    return scanner.scrub()


class _SqlScrubber:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.result: list[str] = []
        self.index = 0
        self.state = "default"

    def scrub(self) -> str:
        while self.index < len(self.sql):
            if self.state == "line_comment":
                self._consume_line_comment()
            elif self.state == "block_comment":
                self._consume_block_comment()
            elif self.state == "single":
                self._consume_single_quoted()
            elif self.state == "double":
                self._consume_double_quoted()
            else:
                self._consume_default()
        return "".join(self.result)

    @property
    def _char(self) -> str:
        return self.sql[self.index]

    @property
    def _next(self) -> str:
        return self.sql[self.index + 1] if self.index + 1 < len(self.sql) else ""

    def _consume_line_comment(self) -> None:
        if self._char == "\n":
            self.state = "default"
            self.result.append(self._char)
        self.index += 1

    def _consume_block_comment(self) -> None:
        if self._char == "*" and self._next == "/":
            self.state = "default"
            self.index += 2
        else:
            self.index += 1

    def _consume_single_quoted(self) -> None:
        if self._char == "'" and self._next == "'":
            self.index += 2
            return
        if self._char == "'":
            self.state = "default"
        self.index += 1

    def _consume_double_quoted(self) -> None:
        if self._char == '"':
            self.state = "default"
        self.index += 1

    def _consume_default(self) -> None:
        if self._char == "-" and self._next == "-":
            self.state = "line_comment"
            self.index += 2
            return
        if self._char == "/" and self._next == "*":
            self.state = "block_comment"
            self.index += 2
            return
        if self._char == "'":
            self.state = "single"
            self.index += 1
            return
        if self._char == '"':
            self.state = "double"
            self.index += 1
            return
        self.result.append(self._char)
        self.index += 1


async def _fetch(config: ToolServerConfig, sql: str, *args: Any, timeout: float | None = None) -> Sequence[Any]:
    conn = await _connect(config)
    try:
        async with conn.transaction(readonly=True):
            await _set_statement_timeout(conn, config)
            return await conn.fetch(sql, *args, timeout=timeout)
    finally:
        await conn.close()


async def _fetch_limited(
    config: ToolServerConfig,
    sql: str,
    limit: int,
    timeout: float | None = None,
) -> list[Any]:
    conn = await _connect(config)
    rows = []
    try:
        async with conn.transaction(readonly=True):
            await _set_statement_timeout(conn, config)
            async for row in conn.cursor(sql, prefetch=min(limit, 50), timeout=timeout):
                rows.append(row)
                if len(rows) >= limit:
                    break
        return rows
    finally:
        await conn.close()


async def _connect(config: ToolServerConfig) -> asyncpg.Connection:
    return await asyncpg.connect(config.postgres_dsn)


async def _set_statement_timeout(conn: asyncpg.Connection, config: ToolServerConfig) -> None:
    timeout_ms = max(int(config.postgres_statement_timeout_ms), 1)
    await conn.fetchval("select set_config('statement_timeout', $1, true)", f"{timeout_ms}ms")


def _record_to_dict(row: Any) -> dict[str, Any]:
    return {key: _json_safe(value) for key, value in dict(row).items()}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    return str(value)


def _missing_dsn() -> dict[str, str]:
    return {"error": "POSTGRES_DSN is not configured"}
