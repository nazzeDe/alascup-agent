import json
import uuid
from datetime import datetime, timezone

from src.models.message import Message, MessageType
from src.models.session import ChatSession
from src.models.tool import ApprovalStatus, ExecutionStatus, ToolCall


class InMemorySessionManager:
    """内存会话管理，仅用于测试。"""

    def __init__(self) -> None:
        self._sessions: dict[uuid.UUID, ChatSession] = {}

    async def create_session(self) -> ChatSession:
        session_id = uuid.uuid4()
        session = ChatSession(
            id=session_id,
            messages=[],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[session_id] = session
        return session

    async def get_session(self, session_id: uuid.UUID) -> ChatSession:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise KeyError(f"session not found: {session_id}")

    async def list_sessions(self) -> list[ChatSession]:
        return list(self._sessions.values())

    async def add_message(self, session_id: uuid.UUID, msg: Message) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"session not found: {session_id}")
        self._sessions[session_id].messages.append(msg)

    async def add_tool_call(self, session_id: uuid.UUID, call: ToolCall) -> None:
        if session_id not in self._sessions:
            raise KeyError(f"session not found: {session_id}")
        self._sessions[session_id].executed_tool_list.append(call)


class PostgresSessionManager:
    def __init__(self, db) -> None:
        self._db = db

    async def create_session(self) -> ChatSession:
        session_id = uuid.uuid4()
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO chat_sessions (id, created_at, updated_at) VALUES ($1, $2, $2)",
            session_id, now,
        )
        return ChatSession(id=session_id, messages=[], executed_tool_list=[], timestamp=now)

    async def get_session(self, session_id: uuid.UUID) -> ChatSession:
        row = await self._db.fetchrow("SELECT * FROM chat_sessions WHERE id = $1", session_id)
        if row is None:
            raise KeyError(f"session not found: {session_id}")
        msgs = await self._db.fetch(
            "SELECT * FROM messages WHERE chat_id = $1 ORDER BY timestamp", session_id
        )
        calls = await self._db.fetch(
            "SELECT * FROM tool_calls WHERE chat_id = $1 ORDER BY created_at", session_id
        )
        return ChatSession(
            id=row["id"],
            messages=[_message_from_row(m) for m in msgs],
            executed_tool_list=[_tool_call_from_row(t) for t in calls],
            timestamp=row["updated_at"].isoformat(),
        )

    async def list_sessions(self) -> list[ChatSession]:
        rows = await self._db.fetch("SELECT id, updated_at FROM chat_sessions ORDER BY updated_at DESC")
        return [
            ChatSession(id=r["id"], messages=[], executed_tool_list=[], timestamp=r["updated_at"].isoformat())
            for r in rows
        ]

    async def add_message(self, session_id: uuid.UUID, msg: Message) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO messages (id, chat_id, timestamp, type, content, is_meta) VALUES ($1,$2,$3,$4,$5,$6)",
            msg.message_id, session_id, msg.timestamp, msg.type.value, msg.content, msg.is_meta,
        )
        await self._db.execute(
            "UPDATE chat_sessions SET updated_at = $1 WHERE id = $2", now, session_id,
        )

    async def add_tool_call(self, session_id: uuid.UUID, call: ToolCall) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO tool_calls (id, chat_id, message_id, tool_name, server_name,
               is_read_only, is_rollbackable, params, request_id, approval_status,
               execution_status, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
            call.message_id, session_id, call.message_id, call.name, call.server.value,
            call.is_read_only, call.is_rollbackable, json.dumps(call.params),
            call.request_id, call.approval_status.value, call.execution_status.value, now,
        )
        await self._db.execute(
            "UPDATE chat_sessions SET updated_at = $1 WHERE id = $2", now, session_id,
        )


def _message_from_row(row) -> Message:
    return Message(
        message_id=row["id"],
        session_id=row["chat_id"],
        timestamp=row["timestamp"].isoformat(),
        type=MessageType(row["type"]),
        content=row["content"],
        is_meta=row["is_meta"],
    )


def _tool_call_from_row(row) -> ToolCall:
    return ToolCall(
        name=row["tool_name"],
        server=row["server_name"],
        description="",
        is_read_only=row["is_read_only"],
        is_rollbackable=row["is_rollbackable"],
        params_schema={},
        session_id=row["chat_id"],
        message_id=row["message_id"],
        params=json.loads(row["params"]) if isinstance(row["params"], str) else row["params"],
        request_id=row["request_id"],
        approval_status=ApprovalStatus(row["approval_status"]),
        execution_status=ExecutionStatus(row["execution_status"]),
        error=json.loads(row["error"]) if isinstance(row.get("error"), str) else row.get("error"),
        timestamp=row["created_at"].isoformat(),
    )
