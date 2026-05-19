"""Integration tests for rag-server MCP server.

Tests the full chain: config → create_server → MCP tool calls via fastmcp client.
Uses ChromaDB in-memory mode so no persistent storage is needed.
"""

import asyncio

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def chroma_client():
    import chromadb
    return chromadb.Client()


@pytest.fixture
def embedder():
    """Use deterministic embedder for CI — SentenceTransformer needs network."""
    return _DummyEmbedder(384)


class _DummyEmbedder:
    """Deterministic embedder to avoid network-dependent model downloads."""

    def __init__(self, dim=384):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        vec = [h[i] / 255.0 for i in range(min(len(h), self.dim))]
        while len(vec) < self.dim:
            vec.append(0.0)
        return vec


@pytest.fixture
def rag_config(chroma_client, embedder):
    from src.config import RagServerConfig
    return RagServerConfig(
        chroma_client=chroma_client,
        embedding_fn=embedder.embed,
        similarity_threshold=0.95,
        chroma_mode="memory",
        collection_name="test_knowledge",
    )


@pytest.fixture
def mcp_server(rag_config):
    from src.main import create_server
    return create_server(rag_config)


def _result_content(result) -> dict | list:
    """Extract structured content from fastmcp CallToolResult."""
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if hasattr(result, "content") and result.content:
        import json
        from mcp.types import TextContent
        for block in result.content:
            if isinstance(block, TextContent):
                try:
                    return json.loads(block.text)
                except (json.JSONDecodeError, TypeError):
                    return block.text
    return result


class TestMCPServerTools:
    """RG-005: Integration tests for MCP tool registration and invocation."""

    def test_health_tool(self, mcp_server):
        async def _call():
            from fastmcp.client import Client
            async with Client(mcp_server) as client:
                result = await client.call_tool("health", {})
                return _result_content(result)

        data = asyncio.run(_call())
        assert data["status"] == "healthy"

    def test_search_experience_empty(self, mcp_server):
        async def _call():
            from fastmcp.client import Client
            async with Client(mcp_server) as client:
                result = await client.call_tool("search_experience", {
                    "query": "CPU 占用高",
                    "fingerprint": {},
                    "top_k": 5,
                })
                return _result_content(result)

        data = asyncio.run(_call())
        assert isinstance(data, dict)
        assert isinstance(data["results"], list)
        assert len(data["results"]) == 0

    def test_save_and_search_roundtrip(self, mcp_server):
        async def _call():
            from fastmcp.client import Client
            async with Client(mcp_server) as client:
                save_result = await client.call_tool("save_experience", {
                    "symptom": "磁盘空间不足",
                    "fingerprint": {"disk_percent": "92", "largest_dir": "/tmp/logs"},
                    "root_cause": "日志文件堆积未轮转",
                    "solution": "清理 /tmp/logs，配置 logrotate",
                })
                save_data = _result_content(save_result)
                assert save_data["status"] == "saved"

                search_result = await client.call_tool("search_experience", {
                    "query": "磁盘空间不足",
                    "fingerprint": {"disk_percent": "92"},
                    "top_k": 5,
                })
                return _result_content(search_result)

        data = asyncio.run(_call())
        results = data["results"]
        assert len(results) >= 1
        assert any("logrotate" in r.get("text", "") for r in results)

    def test_mark_invalid(self, mcp_server):
        async def _call():
            from fastmcp.client import Client
            async with Client(mcp_server) as client:
                save_result = await client.call_tool("save_experience", {
                    "symptom": "Nginx 不可用",
                    "fingerprint": {"service_name": "nginx"},
                    "root_cause": "配置语法错误",
                    "solution": "修正 nginx.conf",
                })
                save_data = _result_content(save_result)
                record_id = save_data["record_id"]

                await client.call_tool("mark_experience_invalid", {
                    "record_id": record_id,
                    "reason": "方案过时",
                })

                search_result = await client.call_tool("search_experience", {
                    "query": "Nginx 不可用",
                    "fingerprint": {"service_name": "nginx"},
                    "top_k": 5,
                })
                return _result_content(search_result)

        data = asyncio.run(_call())
        results = data["results"]
        assert not any("nginx.conf" in r.get("text", "") for r in results)

    def test_list_tools(self, mcp_server):
        async def _call():
            return await mcp_server.list_tools()

        tools = asyncio.run(_call())
        tool_names = {t.name for t in tools}
        expected = {"search_experience", "save_experience", "mark_experience_invalid", "health"}
        assert expected.issubset(tool_names)
