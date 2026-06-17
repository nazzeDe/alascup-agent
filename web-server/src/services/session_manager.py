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
        chat_id = uuid.uuid4()
        session = ChatSession(
            id=chat_id,
            messages=[],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[chat_id] = session
        return session

    async def get_session(self, chat_id: uuid.UUID) -> ChatSession:
        try:
            return self._sessions[chat_id]
        except KeyError:
            raise KeyError(f"session not found: {chat_id}")

    async def list_sessions(self) -> list[ChatSession]:
        return list(self._sessions.values())

    async def add_message(self, chat_id: uuid.UUID, msg: Message) -> None:
        if chat_id not in self._sessions:
            raise KeyError(f"session not found: {chat_id}")
        self._sessions[chat_id].messages.append(msg)

    async def add_tool_call(self, chat_id: uuid.UUID, call: ToolCall) -> uuid.UUID:
        if chat_id not in self._sessions:
            raise KeyError(f"session not found: {chat_id}")
        self._sessions[chat_id].executed_tool_list.append(call)
        return uuid.uuid4()

    async def update_tool_call(
        self,
        tool_call_id: uuid.UUID,
        chat_id: uuid.UUID,
        *,
        approval_status: ApprovalStatus | None = None,
        execution_status: ExecutionStatus | None = None,
        error: dict | None = None,
        backup_ref: str | None = None,
        llm_trace_id: uuid.UUID | None = None,
        result: dict | None = None,
    ) -> None:
        """No-op for in-memory manager: tool calls are mutated in-place."""

    async def delete_session(self, chat_id: uuid.UUID) -> None:
        self._sessions.pop(chat_id, None)

    async def set_title(self, chat_id: uuid.UUID, title: str) -> None:
        if chat_id in self._sessions and not self._sessions[chat_id].title:
            self._sessions[chat_id].title = title


class PostgresSessionManager:
    def __init__(self, db) -> None:
        self._db = db

    async def create_session(self) -> ChatSession:
        chat_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        await self._db.execute(
            "INSERT INTO chat_sessions (id, created_at, updated_at) VALUES ($1, $2, $2)",
            chat_id,
            now,
        )
        return ChatSession(
            id=chat_id, messages=[], executed_tool_list=[], timestamp=now.isoformat()
        )

    async def get_session(self, chat_id: uuid.UUID) -> ChatSession:
        row = await self._db.fetchrow(
            "SELECT * FROM chat_sessions WHERE id = $1", chat_id
        )
        if row is None:
            raise KeyError(f"session not found: {chat_id}")
        msgs = await self._db.fetch(
            "SELECT * FROM messages WHERE chat_id = $1 ORDER BY timestamp", chat_id
        )
        calls = await self._db.fetch(
            "SELECT * FROM tool_calls WHERE chat_id = $1 ORDER BY created_at", chat_id
        )
        return ChatSession(
            id=row["id"],
            title=row.get("title"),
            messages=[_message_from_row(m) for m in msgs],
            executed_tool_list=[_tool_call_from_row(t) for t in calls],
            timestamp=row["updated_at"].isoformat(),
        )

    async def list_sessions(self) -> list[ChatSession]:
        rows = await self._db.fetch(
            "SELECT id, title, updated_at FROM chat_sessions WHERE deleted = false ORDER BY updated_at DESC"
        )
        return [
            ChatSession(
                id=r["id"],
                title=r.get("title"),
                messages=[],
                executed_tool_list=[],
                timestamp=r["updated_at"].isoformat(),
            )
            for r in rows
        ]

    async def delete_session(self, chat_id: uuid.UUID) -> None:
        await self._db.execute(
            "UPDATE chat_sessions SET deleted = true WHERE id = $1", chat_id
        )

    async def add_message(self, chat_id: uuid.UUID, msg: Message) -> None:
        now = datetime.now(timezone.utc)
        msg_ts = datetime.fromisoformat(msg.timestamp)
        await self._db.execute(
            "INSERT INTO messages (id, chat_id, timestamp, type, content, tool_calls, reasoning_content, tool_call_id, tool_name) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            msg.message_id,
            chat_id,
            msg_ts,
            msg.type.value,
            msg.content,
            json.dumps(msg.tool_calls) if msg.tool_calls else None,
            msg.reasoning_content,
            msg.tool_call_id,
            msg.tool_name,
        )
        await self._db.execute(
            "UPDATE chat_sessions SET updated_at = $1 WHERE id = $2",
            now,
            chat_id,
        )

    async def add_tool_call(self, chat_id: uuid.UUID, call: ToolCall) -> uuid.UUID:
        now = datetime.now(timezone.utc)
        call_id = uuid.uuid4()
        await self._db.execute(
            """INSERT INTO tool_calls (id, chat_id, message_id, tool_name, server_name,
               is_read_only, is_rollbackable, params, request_id, approval_status,
               execution_status, created_at, llm_trace_id, llm_tool_call_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
            call_id,
            chat_id,
            call.message_id,
            call.name,
            call.server.value,
            call.is_read_only,
            call.is_rollbackable,
            json.dumps(call.params),
            call.request_id,
            call.approval_status.value,
            call.execution_status.value,
            now,
            call.llm_trace_id,
            call.llm_tool_call_id,
        )
        await self._db.execute(
            "UPDATE chat_sessions SET updated_at = $1 WHERE id = $2",
            now,
            chat_id,
        )
        return call_id

    async def update_tool_call(
        self,
        tool_call_id: uuid.UUID,
        chat_id: uuid.UUID,
        *,
        approval_status: ApprovalStatus | None = None,
        execution_status: ExecutionStatus | None = None,
        error: dict | None = None,
        backup_ref: str | None = None,
        llm_trace_id: uuid.UUID | None = None,
        result: dict | None = None,
    ) -> None:
        """Update tool_call row after classification, approval, or execution."""
        now = datetime.now(timezone.utc)
        sets: list[str] = []
        args: list = []

        if approval_status is not None:
            sets.append(f"approval_status = ${len(args) + 1}")
            args.append(approval_status.value)
        if execution_status is not None:
            sets.append(f"execution_status = ${len(args) + 1}")
            args.append(execution_status.value)
        if error is not None:
            sets.append(f"error = ${len(args) + 1}")
            args.append(json.dumps(error))
        if backup_ref is not None:
            sets.append(f"backup_ref = ${len(args) + 1}")
            args.append(backup_ref)
        if llm_trace_id is not None:
            sets.append(f"llm_trace_id = ${len(args) + 1}")
            args.append(llm_trace_id)
        if result is not None:
            sets.append(f"result = ${len(args) + 1}")
            args.append(json.dumps(result))
        if execution_status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED):
            sets.append(f"executed_at = ${len(args) + 1}")
            args.append(now)

        if not sets:
            return

        args.append(tool_call_id)
        await self._db.execute(
            f"UPDATE tool_calls SET {', '.join(sets)} WHERE id = ${len(args)}",
            *args,
        )
        await self._db.execute(
            "UPDATE chat_sessions SET updated_at = $1 WHERE id = $2",
            now,
            chat_id,
        )

    async def set_title(self, chat_id: uuid.UUID, title: str) -> None:
        await self._db.execute(
            "UPDATE chat_sessions SET title = $1 WHERE id = $2 AND title IS NULL",
            title,
            chat_id,
        )


def _message_from_row(row) -> Message:
    raw_tc = row.get("tool_calls")
    tool_calls = (
        json.loads(raw_tc)
        if isinstance(raw_tc, str)
        else raw_tc
    )
    return Message(
        message_id=row["id"],
        chat_id=row["chat_id"],
        timestamp=row["timestamp"].isoformat(),
        type=MessageType(row["type"]),
        content=row["content"],
        tool_calls=tool_calls,
        tool_call_id=row.get("tool_call_id"),
        tool_name=row.get("tool_name"),
        reasoning_content=row.get("reasoning_content"),
    )


def _tool_call_from_row(row) -> ToolCall:
    raw_result = row.get("result")
    result = (
        json.loads(raw_result)
        if isinstance(raw_result, str)
        else raw_result
    )
    return ToolCall(
        call_id=row["id"],
        name=row["tool_name"],
        server=row["server_name"],
        description="",
        is_read_only=row["is_read_only"],
        is_rollbackable=row["is_rollbackable"],
        params_schema={},
        chat_id=row["chat_id"],
        message_id=row.get("message_id"),
        llm_tool_call_id=row.get("llm_tool_call_id"),
        params=json.loads(row["params"])
        if isinstance(row["params"], str)
        else row["params"],
        request_id=row["request_id"],
        approval_status=ApprovalStatus(row["approval_status"]),
        execution_status=ExecutionStatus(row["execution_status"]),
        error=json.loads(row["error"])
        if isinstance(row.get("error"), str)
        else row.get("error"),
        result=result,
        llm_trace_id=row.get("llm_trace_id"),
        timestamp=row["created_at"].isoformat(),
    )
