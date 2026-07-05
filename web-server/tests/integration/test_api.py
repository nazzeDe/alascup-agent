import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def _capture_chat_id_from_sse(client, message="hello"):
    """Send message without chat_id, capture chat_id from session_init event, drain stream."""
    import json as _json

    chat_id = None
    async with client.stream(
        "POST", "/api/chat", json={"message": message}
    ) as response:
        assert response.status_code == 200
        current_event = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line.split(": ", 1)[1]
            if line.startswith("data: ") and current_event == "session_init":
                chat_id = _json.loads(line[6:])["chat_id"]
            if line == "event: done":
                break
    return chat_id


class _MockLLMAdapter:
    async def generate(self, messages, tools=None, system=None, chat_id=None):
        return {"content": "mock response", "tool_calls": None}

    async def generate_stream(self, messages, tools=None, system=None, chat_id=None):
        yield {"event": "assistant", "data": '{"delta":"mock reply"}'}
        yield {"event": "done", "data": "{}"}

    def escalate_max_tokens(self) -> None:
        pass

    def switch_to_fallback(self) -> None:
        pass


class _MockToolExecutor:
    async def discover(self):
        pass

    def list_tools(self):
        return []

    async def classify_companion(self, tool_name, params, server_name):
        return {"safe": True}

    async def execute(
        self,
        tool_name,
        arguments,
        *,
        server_name=None,
        approval_status=None,
        request_id=None,
    ):
        return {"execution_status": "SUCCEEDED", "output": {}}

    async def execute_parallel(self, calls):
        return [{"execution_status": "SUCCEEDED", "output": {}} for _ in calls]

    async def classify(self, tool_name, params, server_name):
        return {"is_read_only": True, "is_rollbackable": True}


def _build_test_services():
    from src.config.models import RulesConfig
    from src.observability.audit_logger import InMemoryAuditLogger
    from src.security.pending import ApprovalBridge
    from src.security.rule_engine import RuleEngine
    from src.services.container import Services
    from src.services.context_manager import ContextManager
    from src.services.prompt_manager import PromptManager
    from src.services.session_manager import InMemorySessionManager
    from src.services.tool_lifecycle import ToolCallLifecycle

    llm_adapter = _MockLLMAdapter()
    executor = _MockToolExecutor()
    rule_engine = RuleEngine(RulesConfig())
    audit_logger = InMemoryAuditLogger()
    session_mgr = InMemorySessionManager()

    return Services(
        llm_adapter=llm_adapter,
        session_manager=session_mgr,
        prompt_manager=PromptManager(),
        context_manager=ContextManager(),
        rule_engine=rule_engine,
        tool_executor=executor,
        audit_logger=audit_logger,
        approval_bridge=ApprovalBridge(),
        error_recovery=None,
        lifecycle=ToolCallLifecycle(session_mgr),
    )


@pytest.fixture
def app():
    from src.main import create_app

    return create_app(services=_build_test_services())


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("ok", "degraded")
        assert data["version"] == "0.1.0"


class TestSessionsEndpoint:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, client):
        response = await client.get("/api/sessions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


class TestChatTurnSSE:
    @pytest.mark.asyncio
    async def test_chat_turn_returns_sse(self, client):
        url = "/api/chat"
        async with client.stream("POST", url, json={"message": "hello"}) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            events = []
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    events.append(line)
                if line == "event: done":
                    break

            assert any("assistant" in e for e in events)
            assert any("done" in e for e in events)

    @pytest.mark.asyncio
    async def test_chat_turn_missing_message(self, client):
        response = await client.post("/api/chat", json={})
        assert response.status_code == 422


class TestChatTurnSSEIntegration:
    """SSEStream 集成：session_init 第一事件，done 结尾，chat_id 是 UUID。"""

    @pytest.mark.asyncio
    async def test_first_event_is_session_init_with_uuid(self, client):
        """POST /api/chat → 第一 SSE 事件是 session_init，chat_id 是 UUID。"""
        import json as _json
        from uuid import UUID

        session_init_data = None
        async with client.stream(
            "POST",
            "/api/chat",
            json={"message": "hello"},
        ) as response:
            assert response.status_code == 200
            current_event = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event = line.split(": ", 1)[1]
                if line.startswith("data: ") and current_event == "session_init":
                    session_init_data = _json.loads(line[6:])
                    break
                if line == "event: done":
                    break

        assert session_init_data is not None, "First event must be session_init"
        assert "chat_id" in session_init_data
        cid = session_init_data["chat_id"]
        assert cid != "new", f"chat_id should be a real UUID, not '{cid}'"
        UUID(cid)  # raises ValueError if not valid UUID

    @pytest.mark.asyncio
    async def test_event_sequence_ends_with_done(self, client):
        """POST /api/chat → 最后 SSE 事件是 done。"""
        last_event = None
        async with client.stream(
            "POST",
            "/api/chat",
            json={"message": "test"},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    last_event = line.split(": ", 1)[1]

        assert last_event == "done", (
            f"Last SSE event must be 'done', got '{last_event}'"
        )

    @pytest.mark.asyncio
    async def test_no_x_session_id_header(self, client):
        """POST /api/chat → 不再设置 X-Session-ID header（改用 session_init 事件）。"""
        async with client.stream(
            "POST",
            "/api/chat",
            json={"message": "hi"},
        ) as response:
            assert response.status_code == 200
            # X-Session-ID should NOT be set
            assert response.headers.get("X-Session-ID") is None
            # Drain the stream
            async for line in response.aiter_lines():
                if line == "event: done":
                    break


class TestSessionLifecycle:
    """API-002: 会话生命周期——创建 → 对话 → 详情含消息 → 追加对话。"""

    @pytest.mark.asyncio
    async def test_full_lifecycle_messages_persisted(self, client):
        """发送消息 → SSE session_init 创建会话 → GET 详情含消息。"""
        # 1. 发送消息（无 chat_id），捕获 session_init 中的 chat_id
        chat_id = await _capture_chat_id_from_sse(client, "hello")

        # 2. 获取详情——应有消息
        r = await client.get(f"/api/sessions/{chat_id}")
        assert r.status_code == 200
        session = r.json()
        assert len(session["messages"]) >= 2  # user + assistant

    @pytest.mark.asyncio
    async def test_new_chat_creates_session_with_header(self, client):
        """发送消息 → 第一事件 session_init 含真实 chat_id。"""
        import json as _json

        first_event = None
        data_line = None
        async with client.stream(
            "POST",
            "/api/chat",
            json={"message": "hello"},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line.split(": ", 1)[1]
                    if event_type == "session_init" and first_event is None:
                        first_event = event_type
                if (
                    line.startswith("data: ")
                    and first_event == "session_init"
                    and data_line is None
                ):
                    data_line = line[6:]
                if line == "event: done":
                    break

        assert first_event == "session_init"
        data = _json.loads(data_line)
        chat_id = data["chat_id"]

        # 确认会话已创建且含消息
        r = await client.get(f"/api/sessions/{chat_id}")
        assert r.status_code == 200
        assert len(r.json()["messages"]) >= 2

    @pytest.mark.asyncio
    async def test_continuing_conversation_loads_history(self, client):
        """同一 chat_id 追加消息——历史被加载。"""
        # 第一轮：发送消息（无 chat_id），捕获 session_init 中的 chat_id
        chat_id = await _capture_chat_id_from_sse(client, "first message")

        # 第二轮——同 chat_id
        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "message": "second message",
                "chat_id": chat_id,
            },
        ) as response:
            async for line in response.aiter_lines():
                if line == "event: done":
                    break

        # 详情应包含多轮消息
        r = await client.get(f"/api/sessions/{chat_id}")
        session = r.json()
        user_msgs = [m for m in session["messages"] if m["type"] == "user"]
        assert len(user_msgs) == 2
        assert user_msgs[0]["content"] == "first message"
        assert user_msgs[1]["content"] == "second message"


class TestApprovalEndpoint:
    @pytest.mark.asyncio
    async def test_approve_request_not_found(self, client):
        response = await client.post(
            "/api/tool-requests/00000000-0000-0000-0000-000000000000/approval",
            json={
                "chat_id": "11111111-1111-1111-1111-111111111111",
                "approval_status": "APPROVED",
            },
        )
        assert response.status_code == 404
