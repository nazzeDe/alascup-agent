from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestToolExecutor:
    def test_executor_construction(self):
        from src.mcp_client.executor import ToolExecutor

        executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
        assert executor.tool_server_url == "http://tool:8001"
        assert executor.rag_server_url == "http://rag:8002"
        assert executor.max_retries == 2

    def test_executor_custom_retries(self):
        from src.mcp_client.executor import ToolExecutor

        executor = ToolExecutor(
            tool_server_url="http://tool:8001",
            rag_server_url="http://rag:8002",
            max_retries=0,
        )
        assert executor.max_retries == 0

    def test_is_connect_timeout(self):
        from src.mcp_client.executor import ToolExecutor

        executor = ToolExecutor(tool_server_url="", rag_server_url="")
        assert executor._is_connect_error(ConnectionRefusedError("test"))
        assert not executor._is_connect_error(ValueError("test"))

    @pytest.mark.asyncio
    async def test_list_tools_aggregates_both_servers(self):
        from src.mcp_client.executor import ToolExecutor

        with patch("src.mcp_client.executor.Client") as MockClient:
            mock_tool = MagicMock()
            mock_tool.list_tools = AsyncMock(return_value=[
                MagicMock(name="get_cpu_info"),
                MagicMock(name="get_memory_info"),
            ])
            mock_tool.__aenter__ = AsyncMock(return_value=mock_tool)
            mock_tool.__aexit__ = AsyncMock(return_value=None)

            mock_rag = MagicMock()
            mock_rag.list_tools = AsyncMock(return_value=[
                MagicMock(name="search_knowledge_base"),
            ])
            mock_rag.__aenter__ = AsyncMock(return_value=mock_rag)
            mock_rag.__aexit__ = AsyncMock(return_value=None)

            MockClient.side_effect = [mock_tool, mock_rag]

            executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
            tools = await executor.list_tools()

            assert len(tools) == 3

    @pytest.mark.asyncio
    async def test_execute_routes_to_tool_server(self):
        from src.mcp_client.executor import ToolExecutor

        with patch("src.mcp_client.executor.Client") as MockClient:
            mock_client = MagicMock()
            mock_result = MagicMock()
            mock_result.isError = False
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
            result = await executor.execute("get_cpu_info", {})

            mock_client.call_tool.assert_called_once_with("get_cpu_info", {})
            assert result["execution_status"] == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_execute_retries_on_connect_error(self):
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

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = _FakeClient()

            executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
            result = await executor.execute("get_cpu_info", {})

            assert call_count == 3
            assert result["execution_status"] == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_execute_returns_failed_after_max_retries(self):
        from src.mcp_client.executor import ToolExecutor

        class _FailingClient:
            async def __aenter__(self):
                raise ConnectionError("connection refused")

            async def __aexit__(self, *args):
                pass

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = _FailingClient()

            executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
            result = await executor.execute("get_cpu_info", {})

            assert result["execution_status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_execute_does_not_retry_call_timeout(self):
        from src.mcp_client.executor import ToolExecutor

        class _TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def call_tool(self, name, args):
                raise TimeoutError("call timed out")

        with patch("src.mcp_client.executor.Client") as MockClient:
            MockClient.return_value = _TimeoutClient()

            executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
            result = await executor.execute("get_cpu_info", {})

            assert result["execution_status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_execute_parallel(self):
        from src.mcp_client.executor import ToolExecutor

        with patch("src.mcp_client.executor.Client") as MockClient:
            mock_client = MagicMock()
            mock_result = MagicMock()
            mock_result.isError = False
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
            results = await executor.execute_parallel([
                {"tool_name": "get_cpu_info", "arguments": {}, "server": "tool-server"},
                {"tool_name": "get_memory_info", "arguments": {}, "server": "tool-server"},
            ])

            assert len(results) == 2
            assert all(r["execution_status"] == "SUCCEEDED" for r in results)

    # SC-008: 二次校验拦截未审批请求
    @pytest.mark.asyncio
    async def test_security_violation_for_unapproved_non_readonly(self):
        from src.mcp_client.executor import ToolExecutor

        executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
        result = await executor.execute("restart_service", {}, is_read_only=False, approval_status="PENDING")

        assert result["execution_status"] == "SECURITY_VIOLATION"
        assert result["error"]["code"] == "SECURITY_VIOLATION"

    @pytest.mark.asyncio
    async def test_non_readonly_with_approved_status_passes(self):
        from src.mcp_client.executor import ToolExecutor

        with patch("src.mcp_client.executor.Client") as MockClient:
            mock_client = MagicMock()
            mock_result = MagicMock()
            mock_result.isError = False
            mock_client.call_tool = AsyncMock(return_value=mock_result)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            MockClient.return_value = mock_client

            executor = ToolExecutor(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
            result = await executor.execute("restart_service", {}, is_read_only=False, approval_status="APPROVED")

            assert result["execution_status"] == "SUCCEEDED"
