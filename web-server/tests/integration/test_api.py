import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


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
    from src.agent.graph import build_graph
    from src.config.models import RulesConfig
    from src.observability.audit_logger import InMemoryAuditLogger
    from src.security.pending import ApprovalBridge
    from src.security.rule_engine import RuleEngine
    from src.services.container import Services
    from src.services.context_manager import ContextManager
    from src.services.prompt_manager import PromptManager
    from src.services.session_manager import InMemorySessionManager

    llm_adapter = _MockLLMAdapter()
    executor = _MockToolExecutor()
    rule_engine = RuleEngine(RulesConfig())
    audit_logger = InMemoryAuditLogger()

    graph = build_graph(
        llm=llm_adapter,
        executor=executor,
        rule_engine=rule_engine,
        audit_logger=audit_logger,
    )

    return Services(
        llm_adapter=llm_adapter,
        session_manager=InMemorySessionManager(),
        prompt_manager=PromptManager(),
        context_manager=ContextManager(),
        rule_engine=rule_engine,
        tool_executor=executor,
        audit_logger=audit_logger,
        approval_bridge=ApprovalBridge(),
        graph=graph,
        error_recovery=None,
    )


@pytest.fixture
def app():
    from src.main import app

    app.state.services = _build_test_services()
    return app


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
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"


class TestSessionsEndpoint:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self, client):
        response = await client.get("/api/sessions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_create_session(self, client):
        response = await client.post("/api/sessions")
        assert response.status_code == 201
        assert "id" in response.json()


class TestChatTurnSSE:
    @pytest.mark.asyncio
    async def test_chat_turn_returns_sse(self, client):
        r = await client.post("/api/sessions")
        chat_id = r.json()["id"]

        url = "/api/chat"
        async with client.stream(
            "POST", url, json={"message": "hello", "chat_id": chat_id}
        ) as response:
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
        r = await client.post("/api/sessions")
        chat_id = r.json()["id"]
        response = await client.post("/api/chat", json={"chat_id": chat_id})
        assert response.status_code == 422


class TestSessionLifecycle:
    """API-002: 会话生命周期——创建 → 对话 → 详情含消息 → 追加对话。"""

    @pytest.mark.asyncio
    async def test_full_lifecycle_messages_persisted(self, client):
        """POST 创建 → POST messages → GET 详情含消息。"""
        # 1. 创建会话
        r = await client.post("/api/sessions")
        assert r.status_code == 201
        chat_id = r.json()["id"]

        # 2. 发送消息
        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "message": "hello",
                "chat_id": chat_id,
            },
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line == "event: done":
                    break

        # 3. 获取详情——应有消息
        r = await client.get(f"/api/sessions/{chat_id}")
        assert r.status_code == 200
        session = r.json()
        assert len(session["messages"]) >= 2  # user + assistant

    @pytest.mark.asyncio
    async def test_new_chat_creates_session_with_header(self, client):
        """发送消息 → 响应头含 X-Session-ID。"""
        r = await client.post("/api/sessions")
        chat_id = r.json()["id"]

        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "message": "hello",
                "chat_id": chat_id,
            },
        ) as response:
            assert response.status_code == 200
            assert response.headers.get("X-Session-ID") == chat_id
            async for line in response.aiter_lines():
                if line == "event: done":
                    break

        # 确认会话已创建且含消息
        r = await client.get(f"/api/sessions/{chat_id}")
        assert r.status_code == 200
        assert len(r.json()["messages"]) >= 2

    @pytest.mark.asyncio
    async def test_continuing_conversation_loads_history(self, client):
        """同一 chat_id 追加消息——历史被加载。"""
        # 创建会话 + 第一轮
        r = await client.post("/api/sessions")
        chat_id = r.json()["id"]

        async with client.stream(
            "POST",
            "/api/chat",
            json={
                "message": "first message",
                "chat_id": chat_id,
            },
        ) as response:
            async for line in response.aiter_lines():
                if line == "event: done":
                    break

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
            json={"approval_status": "APPROVED"},
        )
        assert response.status_code == 404
