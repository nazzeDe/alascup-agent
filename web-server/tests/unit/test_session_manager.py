import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


class _FakeRecord:
    """Minimal asyncpg.Record stand-in: supports __getitem__ and .get()."""
    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


def _make_msg_row(msg_id=None, chat_id=None, ts=None, msg_type="user",
                  content="hello", is_meta=False):
    return _FakeRecord({
        "id": msg_id or uuid.uuid4(),
        "chat_id": chat_id or uuid.uuid4(),
        "timestamp": ts or datetime.now(timezone.utc),
        "type": msg_type,
        "content": content,
        "is_meta": is_meta,
    })


def _make_call_row(tool_name="get_cpu", server_name="tool-server",
                   is_read_only=True, is_rollbackable=False,
                   chat_id=None, message_id=None, params=None,
                   request_id=None, approval_status="APPROVED",
                   execution_status="SUCCEEDED", error=None, result=None,
                   created_at=None):
    data = {
        "id": uuid.uuid4(),
        "tool_name": tool_name,
        "server_name": server_name,
        "is_read_only": is_read_only,
        "is_rollbackable": is_rollbackable,
        "chat_id": chat_id or uuid.uuid4(),
        "message_id": message_id or uuid.uuid4(),
        "params": params if params is not None else "{}",
        "request_id": request_id,
        "approval_status": approval_status,
        "execution_status": execution_status,
        "error": error,
        "created_at": created_at or datetime.now(timezone.utc),
    }
    if result is not None:
        data["result"] = result
    return _FakeRecord(data)


class TestMessageFromRow:
    def test_converts_row_to_message(self):
        from src.services.session_manager import _message_from_row
        from src.models.message import MessageType

        msg_id = uuid.uuid4()
        chat_id = uuid.uuid4()
        ts = datetime.now(timezone.utc)

        row = _make_msg_row(msg_id=msg_id, chat_id=chat_id, ts=ts,
                            msg_type="user", content="hello", is_meta=False)
        msg = _message_from_row(row)
        assert msg.message_id == msg_id
        assert msg.chat_id == chat_id
        assert msg.type == MessageType.USER
        assert msg.content == "hello"
        assert msg.is_meta is False

    def test_is_meta_true(self):
        from src.services.session_manager import _message_from_row

        row = _make_msg_row(msg_type="assistant", content="thinking...", is_meta=True)
        msg = _message_from_row(row)
        assert msg.is_meta is True


class TestToolCallFromRow:
    def test_converts_row_to_tool_call(self):
        from src.services.session_manager import _tool_call_from_row
        from src.models.tool import ApprovalStatus, ExecutionStatus

        msg_id = uuid.uuid4()
        chat_id = uuid.uuid4()

        row = _make_call_row(
            chat_id=chat_id, message_id=msg_id,
            params='{"filter": "cpu"}',
            approval_status="APPROVED", execution_status="SUCCEEDED",
        )
        tc = _tool_call_from_row(row)
        assert tc.name == "get_cpu"
        assert tc.server == "tool-server"
        assert tc.is_read_only is True
        assert tc.is_rollbackable is False
        assert tc.params == {"filter": "cpu"}
        assert tc.approval_status == ApprovalStatus.APPROVED
        assert tc.execution_status == ExecutionStatus.SUCCEEDED
        assert tc.timestamp is not None

    def test_params_already_dict(self):
        from src.services.session_manager import _tool_call_from_row

        row = _make_call_row(params={"filter": "cpu"})
        tc = _tool_call_from_row(row)
        assert tc.params == {"filter": "cpu"}

    def test_error_string_parsed_to_dict(self):
        from src.services.session_manager import _tool_call_from_row

        row = _make_call_row(
            execution_status="FAILED",
            error='{"code": "TIMEOUT", "message": "timed out"}',
        )
        tc = _tool_call_from_row(row)
        assert tc.error == {"code": "TIMEOUT", "message": "timed out"}

    def test_error_already_dict(self):
        from src.services.session_manager import _tool_call_from_row

        row = _make_call_row(
            execution_status="FAILED",
            error={"code": "TIMEOUT"},
        )
        tc = _tool_call_from_row(row)
        assert tc.error == {"code": "TIMEOUT"}

    def test_error_none(self):
        from src.services.session_manager import _tool_call_from_row

        row = _make_call_row(error=None)
        tc = _tool_call_from_row(row)
        assert tc.error is None

    def test_error_key_missing(self):
        """When the error key is not in the row at all."""
        from src.services.session_manager import _tool_call_from_row

        data = {
            "id": uuid.uuid4(),
            "tool_name": "get_cpu", "server_name": "tool-server",
            "is_read_only": True, "is_rollbackable": False,
            "chat_id": uuid.uuid4(), "message_id": uuid.uuid4(),
            "params": "{}", "request_id": None,
            "approval_status": "APPROVED", "execution_status": "SUCCEEDED",
            "created_at": datetime.now(timezone.utc),
        }
        row = _FakeRecord(data)
        tc = _tool_call_from_row(row)
        assert tc.error is None

    def test_result_json_string_parsed_to_dict(self):
        """result JSONB string is parsed to dict."""
        from src.services.session_manager import _tool_call_from_row

        row = _make_call_row(
            execution_status="SUCCEEDED",
            result='{"execution_status": "SUCCEEDED", "output": "CPU: 45%"}',
        )
        tc = _tool_call_from_row(row)
        assert tc.result == {"execution_status": "SUCCEEDED", "output": "CPU: 45%"}

    def test_result_already_dict(self):
        from src.services.session_manager import _tool_call_from_row

        row = _make_call_row(
            execution_status="SUCCEEDED",
            result={"execution_status": "SUCCEEDED", "output": "CPU: 45%"},
        )
        tc = _tool_call_from_row(row)
        assert tc.result == {"execution_status": "SUCCEEDED", "output": "CPU: 45%"}

    def test_result_none(self):
        from src.services.session_manager import _tool_call_from_row

        row = _make_call_row(result=None)
        tc = _tool_call_from_row(row)
        assert tc.result is None

    def test_result_key_missing(self):
        """When the result key is not in the row at all — returns None."""
        from src.services.session_manager import _tool_call_from_row

        data = {
            "id": uuid.uuid4(),
            "tool_name": "get_cpu", "server_name": "tool-server",
            "is_read_only": True, "is_rollbackable": False,
            "chat_id": uuid.uuid4(), "message_id": uuid.uuid4(),
            "params": "{}", "request_id": None,
            "approval_status": "APPROVED", "execution_status": "SUCCEEDED",
            "created_at": datetime.now(timezone.utc),
        }
        row = _FakeRecord(data)
        tc = _tool_call_from_row(row)
        assert tc.result is None


class TestPostgresSessionManagerCreate:
    @pytest.mark.asyncio
    async def test_create_session_inserts_and_returns(self):
        from src.services.session_manager import PostgresSessionManager

        db = MagicMock()
        db.execute = AsyncMock()
        mgr = PostgresSessionManager(db)

        session = await mgr.create_session()
        assert session.id is not None
        assert session.messages == []
        assert session.executed_tool_list == []
        assert db.execute.call_count == 1


class TestPostgresSessionManagerGet:
    @pytest.mark.asyncio
    async def test_get_session_not_found(self):
        from src.services.session_manager import PostgresSessionManager

        db = MagicMock()
        db.fetchrow = AsyncMock(return_value=None)
        mgr = PostgresSessionManager(db)

        with pytest.raises(KeyError, match="session not found"):
            await mgr.get_session(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_get_session_returns_with_empty_messages_and_calls(self):
        from src.services.session_manager import PostgresSessionManager

        chat_id = uuid.uuid4()
        db = MagicMock()
        db.fetchrow = AsyncMock(return_value=_FakeRecord({
            "id": chat_id,
            "updated_at": datetime.now(timezone.utc),
        }))
        db.fetch = AsyncMock(side_effect=[[], []])

        mgr = PostgresSessionManager(db)
        session = await mgr.get_session(chat_id)
        assert session.id == chat_id
        assert session.messages == []
        assert session.executed_tool_list == []


class TestPostgresSessionManagerList:
    @pytest.mark.asyncio
    async def test_list_returns_empty(self):
        from src.services.session_manager import PostgresSessionManager

        db = MagicMock()
        db.fetch = AsyncMock(return_value=[])
        mgr = PostgresSessionManager(db)

        sessions = await mgr.list_sessions()
        assert sessions == []

    @pytest.mark.asyncio
    async def test_list_returns_sessions(self):
        from src.services.session_manager import PostgresSessionManager

        chat_id = uuid.uuid4()
        ts = datetime.now(timezone.utc)

        db = MagicMock()
        db.fetch = AsyncMock(return_value=[
            _FakeRecord({"id": chat_id, "updated_at": ts}),
        ])
        mgr = PostgresSessionManager(db)

        sessions = await mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].id == chat_id


class TestPostgresSessionManagerAddMessage:
    @pytest.mark.asyncio
    async def test_add_message_inserts_and_updates(self):
        from src.services.session_manager import PostgresSessionManager
        from src.models.message import Message, MessageType

        db = MagicMock()
        db.execute = AsyncMock()
        mgr = PostgresSessionManager(db)

        chat_id = uuid.uuid4()
        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=chat_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content="hello",
        )
        await mgr.add_message(chat_id, msg)
        assert db.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_add_message_passes_is_meta(self):
        from src.services.session_manager import PostgresSessionManager
        from src.models.message import Message, MessageType

        db = MagicMock()
        db.execute = AsyncMock()
        mgr = PostgresSessionManager(db)

        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.ASSISTANT,
            content="thinking...",
            is_meta=True,
        )
        await mgr.add_message(msg.chat_id, msg)

        # INSERT INTO messages (id, chat_id, timestamp, type, content, is_meta)
        insert_args = db.execute.call_args_list[0][0]
        assert insert_args[6] is True  # is_meta is 7th positional arg ($6)


class TestPostgresSessionManagerAddToolCall:
    @pytest.mark.asyncio
    async def test_add_tool_call_inserts_and_updates(self):
        from src.services.session_manager import PostgresSessionManager
        from src.models.tool import (ApprovalStatus, ExecutionStatus,
                                      ServerName, ToolCall)

        db = MagicMock()
        db.execute = AsyncMock()
        mgr = PostgresSessionManager(db)

        call = ToolCall(
            name="get_cpu",
            server=ServerName.TOOL_SERVER,
            description="get CPU info",
            is_read_only=True,
            is_rollbackable=False,
            params_schema={},
            chat_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            params={},
            request_id=None,
            approval_status=ApprovalStatus.APPROVED,
            execution_status=ExecutionStatus.SUCCEEDED,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        await mgr.add_tool_call(call.chat_id, call)
        assert db.execute.call_count == 2


class TestPostgresSessionManagerKeyError:
    @pytest.mark.asyncio
    async def test_add_message_to_nonexistent_session_raises(self):
        from src.services.session_manager import PostgresSessionManager
        from src.models.message import Message, MessageType

        db = MagicMock()
        db.execute = AsyncMock(side_effect=Exception("foreign key violation"))
        mgr = PostgresSessionManager(db)

        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content="hello",
        )
        with pytest.raises(Exception, match="foreign key violation"):
            await mgr.add_message(msg.chat_id, msg)


class TestPostgresSessionManagerUpdateToolCall:
    """Tests for update_tool_call with result persistence."""

    @pytest.mark.asyncio
    async def test_update_tool_call_writes_result(self):
        """result dict is persisted to tool_calls.result JSONB column."""
        from src.services.session_manager import PostgresSessionManager
        from src.models.tool import ExecutionStatus

        db = MagicMock()
        db.execute = AsyncMock()
        mgr = PostgresSessionManager(db)

        call_id = uuid.uuid4()
        chat_id = uuid.uuid4()
        await mgr.update_tool_call(
            call_id, chat_id,
            execution_status=ExecutionStatus.SUCCEEDED,
            result={"execution_status": "SUCCEEDED", "output": "CPU: 45%"},
        )
        # First call: UPDATE tool_calls SET ... result = $n
        update_sql = db.execute.call_args_list[0][0][0]
        assert "result" in update_sql
        # Second call: UPDATE chat_sessions SET updated_at
        assert db.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_update_tool_call_result_none_skips(self):
        """result=None does not add result clause to UPDATE."""
        from src.services.session_manager import PostgresSessionManager
        from src.models.tool import ExecutionStatus

        db = MagicMock()
        db.execute = AsyncMock()
        mgr = PostgresSessionManager(db)

        call_id = uuid.uuid4()
        chat_id = uuid.uuid4()
        await mgr.update_tool_call(
            call_id, chat_id,
            execution_status=ExecutionStatus.SUCCEEDED,
            result=None,
        )
        update_sql = db.execute.call_args_list[0][0][0]
        assert "result" not in update_sql
