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
                  content="hello"):
    return _FakeRecord({
        "id": msg_id or uuid.uuid4(),
        "chat_id": chat_id or uuid.uuid4(),
        "timestamp": ts or datetime.now(timezone.utc),
        "type": msg_type,
        "content": content,
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
                            msg_type="user", content="hello")
        msg = _message_from_row(row)
        assert msg.message_id == msg_id
        assert msg.chat_id == chat_id
        assert msg.type == MessageType.USER
        assert msg.content == "hello"

    def test_reads_reasoning_content_from_row(self):
        """_message_from_row preserves reasoning_content for multi-turn context reconstruction."""
        from src.services.session_manager import _message_from_row

        msg_id = uuid.uuid4()
        chat_id = uuid.uuid4()
        ts = datetime.now(timezone.utc)

        row = _FakeRecord({
            "id": msg_id,
            "chat_id": chat_id,
            "timestamp": ts,
            "type": "assistant",
            "content": "Let me check.",
            "tool_calls": None,
            "reasoning_content": "Let me think about which tool to use...",
        })
        msg = _message_from_row(row)
        assert msg.reasoning_content == "Let me think about which tool to use..."

    def test_reasoning_content_none_when_missing_from_row(self):
        """_message_from_row returns None for reasoning_content when column is absent."""
        from src.services.session_manager import _message_from_row

        msg_id = uuid.uuid4()
        chat_id = uuid.uuid4()
        ts = datetime.now(timezone.utc)

        row = _FakeRecord({
            "id": msg_id,
            "chat_id": chat_id,
            "timestamp": ts,
            "type": "assistant",
            "content": "hello",
            "tool_calls": None,
        })
        msg = _message_from_row(row)
        assert msg.reasoning_content is None

    def test_reads_tool_result_metadata_from_row(self):
        """Tool result messages preserve tool_call_id and tool_name for LLM history replay."""
        from src.services.session_manager import _message_from_row

        msg_id = uuid.uuid4()
        chat_id = uuid.uuid4()
        ts = datetime.now(timezone.utc)

        row = _FakeRecord({
            "id": msg_id,
            "chat_id": chat_id,
            "timestamp": ts,
            "type": "tool_result",
            "content": "[get_cpu] execution_status=SUCCEEDED",
            "tool_calls": None,
            "tool_call_id": "call_abc",
            "tool_name": "get_cpu",
            "reasoning_content": None,
        })
        msg = _message_from_row(row)
        assert msg.tool_call_id == "call_abc"
        assert msg.tool_name == "get_cpu"

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
    async def test_add_tool_result_message_writes_tool_metadata(self):
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
            type=MessageType.TOOL_RESULT,
            content="[get_cpu] execution_status=SUCCEEDED",
            tool_call_id="call_abc",
            tool_name="get_cpu",
        )
        await mgr.add_message(chat_id, msg)

        insert_args = db.execute.call_args_list[0][0]
        assert "tool_call_id" in insert_args[0]
        assert "tool_name" in insert_args[0]
        assert "call_abc" in insert_args
        assert "get_cpu" in insert_args

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


class TestPersistAssistantMessage:
    """ToolCallLifecycle.persist_assistant_message must preserve reasoning_content."""

    @pytest.mark.asyncio
    async def test_persists_reasoning_content(self):
        """persist_assistant_message passes reasoning_content to Message constructor."""
        from src.services.tool_lifecycle import ToolCallLifecycle
        from src.models.message import Message

        sm = MagicMock()
        sm.add_message = AsyncMock()

        lifecycle = ToolCallLifecycle(sm)
        chat_id = uuid.uuid4()

        assistant_msg = {
            "content": "Let me check.",
            "reasoning_content": "Let me think about which tool to use...",
            "tool_calls": [
                {"id": "call_001", "type": "function",
                 "function": {"name": "get_cpu", "arguments": "{}"}},
            ],
        }

        await lifecycle.persist_assistant_message(chat_id, assistant_msg)

        sm.add_message.assert_called_once()
        call_args = sm.add_message.call_args
        msg_arg = call_args[0][1]  # second positional arg is the Message
        assert isinstance(msg_arg, Message)
        assert msg_arg.reasoning_content == "Let me think about which tool to use..."

    @pytest.mark.asyncio
    async def test_persists_none_reasoning_content(self):
        """persist_assistant_message passes None when reasoning_content absent."""
        from src.services.tool_lifecycle import ToolCallLifecycle

        sm = MagicMock()
        sm.add_message = AsyncMock()

        lifecycle = ToolCallLifecycle(sm)
        chat_id = uuid.uuid4()

        assistant_msg = {
            "content": "Hello.",
            "tool_calls": None,
        }

        await lifecycle.persist_assistant_message(chat_id, assistant_msg)

        sm.add_message.assert_called_once()
        msg_arg = sm.add_message.call_args[0][1]
        assert msg_arg.reasoning_content is None


class TestPersistToolResultMessage:
    @pytest.mark.asyncio
    async def test_persists_tool_result_metadata(self):
        from src.services.tool_lifecycle import ToolCallLifecycle
        from src.agent.domain import AgentMessage
        from src.models.message import Message, MessageType

        sm = MagicMock()
        sm.add_message = AsyncMock()

        lifecycle = ToolCallLifecycle(sm)
        chat_id = uuid.uuid4()

        await lifecycle.persist_tool_result(chat_id, [
            AgentMessage(
                role="tool",
                content="[get_cpu] execution_status=SUCCEEDED",
                tool_call_id="call_abc",
                name="get_cpu",
            )
        ])

        sm.add_message.assert_called_once()
        msg_arg = sm.add_message.call_args[0][1]
        assert isinstance(msg_arg, Message)
        assert msg_arg.type == MessageType.TOOL_RESULT
        assert msg_arg.tool_call_id == "call_abc"
        assert msg_arg.tool_name == "get_cpu"
