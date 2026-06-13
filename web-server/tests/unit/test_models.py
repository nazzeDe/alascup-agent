import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit


class TestMessage:
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

    def test_message_with_reasoning_content(self):
        """Message model preserves reasoning_content for multi-turn tool-call conversations."""
        from src.models.message import Message, MessageType

        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.ASSISTANT,
            content="I'll check the CPU.",
            reasoning_content="Let me think about which tool to use...",
        )
        assert msg.reasoning_content == "Let me think about which tool to use..."

    def test_message_reasoning_content_none_by_default(self):
        """reasoning_content defaults to None when not provided."""
        from src.models.message import Message, MessageType

        msg = Message(
            message_id=uuid.uuid4(),
            chat_id=uuid.uuid4(),
            timestamp=datetime.now(timezone.utc).isoformat(),
            type=MessageType.USER,
            content="hello",
        )
        assert msg.reasoning_content is None


class TestToolRequest:
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
    def test_approval_invalid_status(self):
        from src.models.tool import ToolApproval

        with pytest.raises(ValidationError):
            ToolApproval(approval_status="MAYBE")  # type: ignore


class TestChatSession:
    def test_session_with_title(self):
        from src.models.session import ChatSession

        chat_id = uuid.uuid4()
        session = ChatSession(
            id=chat_id,
            title="CPU 诊断会话",
            messages=[],
            executed_tool_list=[],
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        assert session.title == "CPU 诊断会话"
