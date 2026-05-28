import uuid

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.unit


@pytest.fixture
def app_with_bridge():
    """Build app with a real ApprovalBridge but mock everything else."""
    from src.agent.graph import build_graph
    from src.config.models import RulesConfig
    from src.models.tool import ApprovalStatus
    from src.observability.audit_logger import InMemoryAuditLogger
    from src.security.pending import ApprovalBridge
    from src.security.rule_engine import RuleEngine
    from src.services.container import Services
    from src.services.context_manager import ContextManager
    from src.services.prompt_manager import PromptManager
    from src.services.session_manager import InMemorySessionManager

    class _MockLLM:
        async def generate(self, messages, tools=None, system=None, chat_id=None):
            return {"content": "approved and done", "tool_calls": None}

        async def generate_stream(self, messages, tools=None, system=None, chat_id=None):
            yield {"event": "assistant", "data": '{"delta":"resumed reply"}'}
            yield {"event": "done", "data": "{}"}

        def escalate_max_tokens(self):
            pass

    class _MockExecutor:
        async def list_tools(self):
            return []

        async def execute(self, tool_name, arguments, *, server_name=None, approval_status=None, request_id=None):
            return {"execution_status": "SUCCEEDED", "output": {}}

        async def execute_parallel(self, calls):
            return [{"execution_status": "SUCCEEDED", "output": {}} for _ in calls]

        async def classify(self, tool_name, params, server_name=""):
            return {"is_read_only": True, "is_rollbackable": True}

    graph = build_graph(
        llm=_MockLLM(),
        executor=_MockExecutor(),
        rule_engine=RuleEngine(RulesConfig()),
        audit_logger=InMemoryAuditLogger(),
    )

    bridge = ApprovalBridge()
    services = Services(
        llm_adapter=_MockLLM(),
        session_manager=InMemorySessionManager(),
        prompt_manager=PromptManager(),
        context_manager=ContextManager(),
        rule_engine=RuleEngine(RulesConfig()),
        tool_executor=_MockExecutor(),
        audit_logger=InMemoryAuditLogger(),
        approval_bridge=bridge,
        graph=graph,
    )
    return services, bridge


@pytest.fixture
async def client(app_with_bridge):
    from src.main import app

    services, _ = app_with_bridge
    app.state.services = services
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestApprovalEndpoint:
    @pytest.mark.asyncio
    async def test_invalid_approval_status_returns_400(self, app_with_bridge):
        """SC-007: Invalid approval_status → 400."""
        from src.main import app

        services, bridge = app_with_bridge
        app.state.services = services

        # create a valid request mapping first
        req_id = str(uuid.uuid4())
        chat_id = str(uuid.uuid4())
        bridge.create(req_id, chat_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                f"/api/tool-requests/{req_id}/approval",
                json={"approval_status": "INVALID_STATUS"},
            )
            assert r.status_code == 400
            assert "invalid approval_status" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_request_not_found_returns_404(self, app_with_bridge):
        from src.main import app

        services, _ = app_with_bridge
        app.state.services = services

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/tool-requests/00000000-0000-0000-0000-000000000000/approval",
                json={"approval_status": "APPROVED"},
            )
            assert r.status_code == 404

    async def _setup_session(self, app_with_bridge, chat_id: str, bridge, req_id: str):
        """Create session first (simulating prior chat turn), then register the approval request."""
        from src.main import app

        services, _ = app_with_bridge
        await services.session_manager.create_session()  # dummy session for InMemory
        # Override: we need session with our specific chat_id
        from src.models.session import ChatSession
        services.session_manager._sessions[uuid.UUID(chat_id)] = ChatSession(
            id=uuid.UUID(chat_id),
            messages=[],
            executed_tool_list=[],
            timestamp="2025-01-01T00:00:00+00:00",
        )
        bridge.create(req_id, chat_id)
        app.state.services = services

    @pytest.mark.asyncio
    async def test_approve_request_succeeds(self, app_with_bridge):
        """Approve an existing request → signals bridge and returns success."""
        services, bridge = app_with_bridge
        req_id = str(uuid.uuid4())
        chat_id = str(uuid.uuid4())
        await self._setup_session(app_with_bridge, chat_id, bridge, req_id)

        from src.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                f"/api/tool-requests/{req_id}/approval",
                json={"approval_status": "APPROVED", "reason": "looks safe"},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["approval_status"] == "APPROVED"
            assert data["request_id"] == req_id
            assert data["reason"] == "looks safe"

    @pytest.mark.asyncio
    async def test_reject_request(self, app_with_bridge):
        """Reject a request → signals bridge and returns success."""
        services, bridge = app_with_bridge
        req_id = str(uuid.uuid4())
        chat_id = str(uuid.uuid4())
        await self._setup_session(app_with_bridge, chat_id, bridge, req_id)

        from src.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                f"/api/tool-requests/{req_id}/approval",
                json={"approval_status": "REJECTED"},
            )
            assert r.status_code == 200
            data = r.json()
            assert data["approval_status"] == "REJECTED"
            assert data["request_id"] == req_id

    @pytest.mark.asyncio
    async def test_approval_without_reason(self, app_with_bridge):
        """Reason is optional."""
        services, bridge = app_with_bridge
        req_id = str(uuid.uuid4())
        chat_id = str(uuid.uuid4())
        await self._setup_session(app_with_bridge, chat_id, bridge, req_id)

        from src.main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                f"/api/tool-requests/{req_id}/approval",
                json={"approval_status": "APPROVED"},
            )
            assert r.status_code == 200
