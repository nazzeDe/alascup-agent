import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class FakeRegistry:
    def __init__(self, servers: dict[str, str] | None = None):
        self._urls = servers or {"tool-server": "http://tool:8001", "rag-server": "http://rag:8002"}

    def url_for(self, server_name: str) -> str:
        return self._urls[server_name]


class TestToolExecutorConstruction:
    def test_stores_registry_and_default_retries(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()
        executor = ToolExecutor(registry)
        assert executor._registry is registry
        assert executor._max_retries == 2

    def test_custom_retries(self):
        from src.mcp_client.executor import ToolExecutor

        executor = ToolExecutor(FakeRegistry(), max_retries=0)
        assert executor._max_retries == 0

    def test_is_connect_error(self):
        from src.mcp_client.executor import ToolExecutor

        executor = ToolExecutor(FakeRegistry())
        assert executor._is_connect_error(ConnectionRefusedError("test"))
        assert not executor._is_connect_error(ValueError("test"))


class TestToolExecutorExecute:
    @pytest.mark.asyncio
    async def test_routes_via_registry_url(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry({"my-server": "http://my:8001"})

        with patch("src.mcp_client.executor.Client") as MockClient:
            mock_client = MagicMock()
            mock_result = MagicMock()
            mock_result.isError = False
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            executor = ToolExecutor(registry)
            result = await executor.execute(
                "get_cpu_info", {},
                server_name="my-server",
                approval_status="APPROVED",
                request_id="req-1",
            )

            MockClient.assert_called_once_with("http://my:8001")
            mock_client.call_tool.assert_called_once_with("get_cpu_info", {})
            assert result["execution_status"] == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_security_violation_when_not_approved(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()
        executor = ToolExecutor(registry)
        result = await executor.execute(
            "restart_service", {},
            server_name="tool-server",
            approval_status="PENDING",
            request_id="req-1",
        )

        assert result["execution_status"] == "SECURITY_VIOLATION"
        assert result["error"]["code"] == "SECURITY_VIOLATION"

    @pytest.mark.asyncio
    async def test_approved_passes_security_check(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()

        with patch("src.mcp_client.executor.Client") as MockClient:
            mock_client = MagicMock()
            mock_result = MagicMock()
            mock_result.isError = False
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            executor = ToolExecutor(registry)
            result = await executor.execute(
                "restart_service", {},
                server_name="tool-server",
                approval_status="APPROVED",
                request_id="req-1",
            )

            assert result["execution_status"] == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_retries_on_connect_error(self):
        from src.mcp_client.executor import ToolExecutor

        call_count = 0

        class _FakeClient:
            async def __aenter__(self):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ConnectionError("connection refused")
                return self

            async def __aexit__(self, *args):
                pass

            async def call_tool(self, name, args):
                result = MagicMock()
                result.isError = False
                return result

        registry = FakeRegistry()

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = _FakeClient()

            executor = ToolExecutor(registry)
            result = await executor.execute(
                "get_cpu_info", {},
                server_name="tool-server",
                approval_status="APPROVED",
                request_id="req-1",
            )

        assert call_count == 3
        assert result["execution_status"] == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_failed_after_max_retries(self):
        from src.mcp_client.executor import ToolExecutor

        class _FailingClient:
            async def __aenter__(self):
                raise ConnectionError("connection refused")

            async def __aexit__(self, *args):
                pass

        registry = FakeRegistry()

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = _FailingClient()

            executor = ToolExecutor(registry)
            result = await executor.execute(
                "get_cpu_info", {},
                server_name="tool-server",
                approval_status="APPROVED",
                request_id="req-1",
            )

        assert result["execution_status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_no_retry_on_timeout(self):
        from src.mcp_client.executor import ToolExecutor

        class _TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def call_tool(self, name, args):
                raise TimeoutError("call timed out")

        registry = FakeRegistry()

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = _TimeoutClient()

            executor = ToolExecutor(registry)
            result = await executor.execute(
                "get_cpu_info", {},
                server_name="tool-server",
                approval_status="APPROVED",
                request_id="req-1",
            )

        assert result["execution_status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_unknown_server_raises_keyerror(self):
        from src.mcp_client.executor import ToolExecutor

        executor = ToolExecutor(FakeRegistry())
        with pytest.raises(KeyError):
            await executor.execute(
                "get_cpu_info", {},
                server_name="nonexistent",
                approval_status="APPROVED",
                request_id="req-1",
            )


class TestToolExecutorExecuteParallel:
    @pytest.mark.asyncio
    async def test_executes_all_calls(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()

        with patch("src.mcp_client.executor.Client") as MockClient:
            mock_client = MagicMock()
            mock_result = MagicMock()
            mock_result.isError = False
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            executor = ToolExecutor(registry)
            results = await executor.execute_parallel([
                {
                    "tool_name": "get_cpu_info",
                    "arguments": {},
                    "server_name": "tool-server",
                    "approval_status": "APPROVED",
                    "request_id": "req-1",
                },
                {
                    "tool_name": "search_experience",
                    "arguments": {"query": "cpu high"},
                    "server_name": "rag-server",
                    "approval_status": "APPROVED",
                    "request_id": "req-2",
                },
            ])

        assert len(results) == 2
        assert all(r["execution_status"] == "SUCCEEDED" for r in results)


class TestToolExecutorClassify:
    @pytest.mark.asyncio
    async def test_calls_classify_tool_on_correct_server(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()

        mock_result = MagicMock()
        mock_result.content = {"isReadOnly": False, "isRollbackable": True}

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = mock_client
            executor = ToolExecutor(registry)
            result = await executor.classify("bash", {"cmd": "ls"}, server_name="tool-server")

        MockClient.assert_called_once_with("http://tool:8001")
        mock_client.call_tool.assert_called_once_with(
            "classify_tool", {"tool_name": "bash", "params": {"cmd": "ls"}}
        )
        assert result == {"is_read_only": False, "is_rollbackable": True}

    @pytest.mark.asyncio
    async def test_normalizes_snake_case_keys(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()

        mock_result = MagicMock()
        mock_result.content = {"is_read_only": True, "is_rollbackable": False}

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = mock_client
            executor = ToolExecutor(registry)
            result = await executor.classify("get_cpu", {}, server_name="tool-server")

        assert result == {"is_read_only": True, "is_rollbackable": False}

    @pytest.mark.asyncio
    async def test_non_dict_content_defaults_to_dangerous(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()

        mock_result = MagicMock()
        mock_result.content = "not a dict"

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = mock_client
            executor = ToolExecutor(registry)
            result = await executor.classify("bash", {}, server_name="tool-server")

        assert result == {"is_read_only": False, "is_rollbackable": False}

    @pytest.mark.asyncio
    async def test_none_content_defaults_to_dangerous(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()

        mock_result = MagicMock()
        mock_result.content = None

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = mock_client
            executor = ToolExecutor(registry)
            result = await executor.classify("bash", {}, server_name="tool-server")

        assert result == {"is_read_only": False, "is_rollbackable": False}

    @pytest.mark.asyncio
    async def test_partial_camelcase_keys(self):
        from src.mcp_client.executor import ToolExecutor

        registry = FakeRegistry()

        mock_result = MagicMock()
        mock_result.content = {"isReadOnly": False}

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = mock_client
            executor = ToolExecutor(registry)
            result = await executor.classify("bash", {}, server_name="tool-server")

        assert result == {"is_read_only": False, "is_rollbackable": False}
