from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastmcp import Client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _DummyEbpfRuntime:
    async def start_all(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


@pytest_asyncio.fixture
async def mcp_client(tmp_path: Path):
    from src.config import ToolServerConfig
    from src.main import create_server

    config = ToolServerConfig(sandbox_root=str(tmp_path))
    with patch("src.main.init_ebpf_runtime", return_value=_DummyEbpfRuntime()):
        server = await create_server(config)

    async with Client(server) as client:
        yield client


class TestMcpIntegration:
    async def test_mcp_discovers_public_and_companion_tools(self, mcp_client: Client):
        tools = await mcp_client.list_tools()
        names = {tool.name for tool in tools}

        assert "get_cpu_info" in names
        assert "get_tool_server_status" in names
        assert "get_tool_server_logs" in names
        assert "get_postgres_schema" in names
        assert "postgres_readonly_query" in names
        assert "bash" in names
        assert "bash_classify" in names
        assert "execute_tool" in names

    async def test_classification_companion_returns_structured_result(
        self, mcp_client: Client
    ):
        safe = await mcp_client.call_tool("bash_classify", {"command": "ls /tmp"})
        unsafe = await mcp_client.call_tool("bash_classify", {"command": "rm -rf /tmp"})

        assert safe.structured_content == {"safe": True}
        assert unsafe.structured_content == {"safe": False}

    async def test_execute_tool_enforces_approval_over_mcp(self, mcp_client: Client):
        result = await mcp_client.call_tool(
            "execute_tool",
            {
                "tool_name": "bash",
                "chat_id": "chat-it",
                "params": {"command": "echo should-not-run", "timeout": 2},
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "approval_status": "PENDING",
            },
        )

        assert result.structured_content == {
            "execution_status": "FAILED",
            "error": {
                "code": 403,
                "message": "SECURITY_VIOLATION",
                "data": "approval_status must be APPROVED, got PENDING",
            },
        }

    async def test_execute_tool_runs_approved_command_over_mcp(
        self, mcp_client: Client
    ):
        result = await mcp_client.call_tool(
            "execute_tool",
            {
                "tool_name": "bash",
                "chat_id": "chat-it",
                "params": {"command": "echo mcp-ok", "timeout": 2},
                "request_id": "550e8400-e29b-41d4-a716-446655440000",
                "approval_status": "APPROVED",
            },
        )

        assert result.structured_content["execution_status"] == "SUCCEEDED"
        assert result.structured_content["stdout"] == "mcp-ok\n"
