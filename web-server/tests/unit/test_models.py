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
