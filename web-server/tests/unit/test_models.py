import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


class TestMessage:
    def test_message_construction(self):
        from src.models.message import Message, MessageType

        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content="hello",
        )
        assert msg.type == MessageType.USER
        assert msg.is_meta is False

    def test_message_is_meta_default(self):
        from src.models.message import Message, MessageType

        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.SYSTEM,
            content="approval passed",
            is_meta=True,
        )
        assert msg.is_meta is True

    def test_message_type_enum(self):
        from src.models.message import MessageType

        assert MessageType.USER == "user"
        assert MessageType.ASSISTANT == "assistant"
        assert MessageType.TOOL_CALL == "tool_call"
        assert MessageType.TOOL_RESULT == "tool_result"
        assert MessageType.SYSTEM == "system"

    def test_message_type_invalid(self):
        from src.models.message import Message

        with pytest.raises(ValidationError):
            Message(
                message_id=uuid.uuid4(),
                chat_id=uuid.uuid4(),
                timestamp=datetime.now(timezone.utc).isoformat(),
                type="invalid",  # type: ignore
                content="x",
            )

    def test_message_serialization(self):
        from src.models.message import Message, MessageType

        msg_id = uuid.uuid4()
        chat_id = uuid.uuid4()
        msg = Message(
            message_id=msg_id,
            chat_id=chat_id,
            timestamp="2026-05-10T12:00:00Z",
            type=MessageType.USER,
            content="test",
        )
        data = msg.model_dump(mode="json")
        assert data["message_id"] == str(msg_id)
        assert data["chat_id"] == str(chat_id)
        assert data["type"] == "user"
        assert data["is_meta"] is False


class TestTool:
    def test_tool_construction(self):
        from src.models.tool import Tool

        tool = Tool(
            name="get_cpu_info",
            server="tool-server",
            description="Get CPU usage",
            is_read_only=True,
            is_rollbackable=False,
            params_schema={"type": "object", "properties": {}},
        )
        assert tool.name == "get_cpu_info"
        assert tool.server == "tool-server"
        assert tool.is_read_only is True

    def test_tool_server_enum(self):
        from src.models.tool import ServerName, Tool

        tool = Tool(
            name="search",
            server=ServerName.RAG_SERVER,
            description="search kb",
            is_read_only=True,
            is_rollbackable=False,
            params_schema={},
        )
        assert tool.server == "rag-server"


class TestToolCall:
    def test_tool_call_extends_tool(self):
        from src.models.tool import ApprovalStatus, ExecutionStatus, ToolCall

        call = ToolCall(
            name="delete_temp_files",
            server="tool-server",
            description="clean temp",
            is_read_only=False,
            is_rollbackable=True,
            params_schema={"type": "object"},
            chat_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            params={"path": "/tmp"},
            approval_status=ApprovalStatus.PENDING,
            execution_status=ExecutionStatus.PENDING_APPROVAL,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert call.name == "delete_temp_files"
        assert call.is_read_only is False
        assert call.approval_status == ApprovalStatus.PENDING
        assert call.request_id is None  # optional

    def test_tool_call_with_request_id(self):
        from src.models.tool import ApprovalStatus, ExecutionStatus, ToolCall

        req_id = uuid.uuid4()
        call = ToolCall(
            name="restart_service",
            server="tool-server",
            description="restart",
            is_read_only=False,
            is_rollbackable=False,
            params_schema={},
            chat_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            params={"service": "nginx"},
            request_id=req_id,
            approval_status=ApprovalStatus.APPROVED,
            execution_status=ExecutionStatus.RUNNING,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert call.request_id == req_id

    def test_tool_call_with_llm_trace_id(self):
        from src.models.tool import ApprovalStatus, ExecutionStatus, ToolCall

        trace_id = uuid.uuid4()
        call = ToolCall(
            name="get_cpu_info",
            server="tool-server",
            description="get cpu",
            is_read_only=True,
            is_rollbackable=False,
            params_schema={},
            chat_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            params={},
            llm_trace_id=trace_id,
            approval_status=ApprovalStatus.APPROVED,
            execution_status=ExecutionStatus.SUCCEEDED,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert call.llm_trace_id == trace_id


class TestToolRequest:
    def test_tool_request_extends_tool(self):
        from src.models.tool import ApprovalStatus, ToolRequest

        req_id = uuid.uuid4()
        tr = ToolRequest(
            name="delete_temp_files",
            server="tool-server",
            description="clean",
            is_read_only=False,
            is_rollbackable=True,
            params_schema={},
            chat_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            request_id=req_id,
            params={"path": "/tmp"},
            approval_status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        assert tr.request_id == req_id
        assert tr.approval_status == ApprovalStatus.PENDING
        assert tr.approved_at is None

    def test_tool_request_with_rejected_reason(self):
        from src.models.tool import ApprovalStatus, ToolRequest

        req_id = uuid.uuid4()
        tr = ToolRequest(
            name="restart_service",
            server="tool-server",
            description="restart",
            is_read_only=False,
            is_rollbackable=False,
            params_schema={},
            chat_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            request_id=req_id,
            params={"service": "nginx"},
            approval_status=ApprovalStatus.REJECTED,
            created_at=datetime.now(timezone.utc).isoformat(),
            rejected_reason="不需要重启",
        )
        assert tr.rejected_reason == "不需要重启"


class TestToolApproval:
    def test_approval_approved(self):
        from src.models.tool import ToolApproval

        approval = ToolApproval(approval_status="APPROVED")
        assert approval.approval_status == "APPROVED"

    def test_approval_rejected_with_reason(self):
        from src.models.tool import ToolApproval

        approval = ToolApproval(approval_status="REJECTED", reason="too risky")
        assert approval.reason == "too risky"

    def test_approval_invalid_status(self):
        from src.models.tool import ToolApproval

        with pytest.raises(ValidationError):
            ToolApproval(approval_status="MAYBE")  # type: ignore


class TestToolResult:
    def test_result_succeeded(self):
        from src.models.tool import ToolResult

        result = ToolResult(
            execution_status="SUCCEEDED",
            output={"cpu_percent": 85},
        )
        assert result.execution_status == "SUCCEEDED"
        assert result.output == {"cpu_percent": 85}

    def test_result_failed_with_error(self):
        from src.models.tool import ToolResult

        result = ToolResult(
            execution_status="FAILED",
            error={"code": "TIMEOUT", "message": "connection timeout"},
        )
        assert result.execution_status == "FAILED"


class TestChatSession:
    def test_session_construction(self):
        from src.models.message import Message, MessageType
        from src.models.session import ChatSession

        chat_id = uuid.uuid4()
        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=chat_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content="hello",
        )
        session = ChatSession(
            id=chat_id,
            messages=[msg],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert session.id == chat_id
        assert len(session.messages) == 1

    def test_session_with_title_and_transition(self):
        from src.models.session import ChatSession

        chat_id = uuid.uuid4()
        session = ChatSession(
            id=chat_id,
            title="CPU 诊断会话",
            messages=[],
            executed_tool_list=[],
            turn_count=3,
            transition="tool_results",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert session.title == "CPU 诊断会话"
        assert session.turn_count == 3
        assert session.transition == "tool_results"

    def test_session_defaults(self):
        from src.models.session import ChatSession

        chat_id = uuid.uuid4()
        session = ChatSession(
            id=chat_id,
            messages=[],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert session.title is None
        assert session.turn_count == 0
        assert session.transition == ""


class TestAuditEvent:
    def test_audit_event_construction(self):
        from src.models.audit import AuditEvent, AuditLevel

        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=AuditLevel.INFO,
            actor="web-server",
            event="AUTO_APPROVED",
        )
        assert event.level == AuditLevel.INFO
        assert event.chat_id is None  # optional

    def test_audit_level_enum(self):
        from src.models.audit import AuditLevel

        assert AuditLevel.INFO == "INFO"
        assert AuditLevel.WARN == "WARN"
        assert AuditLevel.ERROR == "ERROR"
        assert AuditLevel.CRITICAL == "CRITICAL"

    def test_audit_event_with_transition(self):
        from src.models.audit import AuditEvent, AuditLevel

        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=AuditLevel.INFO,
            actor="web-server",
            event="LOOP_TRANSITION",
            transition="tool_results",
        )
        assert event.transition == "tool_results"


class TestApiErrorResponse:
    def test_error_response(self):
        from src.models.api_error import ApiErrorResponse

        err = ApiErrorResponse(code="NOT_FOUND", message="session not found")
        data = err.model_dump()
        assert data["code"] == "NOT_FOUND"
        assert data["message"] == "session not found"


class TestWebServerError:
    def test_webserver_error(self):
        from src.models.api_error import WebServerError

        err = WebServerError(status_code=500, code="INTERNAL", message="boom")
        assert err.status_code == 500
        assert err.code == "INTERNAL"
        assert str(err) == "[INTERNAL] boom"
