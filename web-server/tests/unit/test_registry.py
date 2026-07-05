from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _make_tool(
    name,
    description="",
    mutable=False,
    is_read_only=False,
    is_rollbackable=False,
    hidden=False,
):
    t = MagicMock()
    t.name = name
    t.description = description
    t.meta = {
        "mutable": mutable,
        "is_read_only": is_read_only,
        "is_rollbackable": is_rollbackable,
        "hidden": hidden,
    }
    return t


def _make_client(tools: list):
    mock = MagicMock()
    mock.list_tools = AsyncMock(return_value=tools)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


class TestServerRegistryUrlFor:
    def test_returns_url_for_known_server(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )
        assert registry.url_for("tool-server") == "http://tool:8001"

    def test_raises_keyerror_for_unknown_server(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )
        with pytest.raises(KeyError):
            registry.url_for("unknown")


class TestServerRegistryDiscover:
    @pytest.mark.asyncio
    async def test_discovers_all_servers(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        tool_client = _make_client(
            [
                _make_tool("get_cpu_info", mutable=False, is_read_only=True),
                _make_tool(
                    "bash_classify", mutable=False, is_read_only=True, hidden=True
                ),
            ]
        )

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )

        with patch("src.mcp_client.registry.Client") as MockClient:
            MockClient.side_effect = [tool_client]
            tools = await registry.discover()

        assert len(tools) == 1
        tool_names = {t["name"] for t in tools}
        assert "get_cpu_info" in tool_names
        assert "bash_classify" not in tool_names

    @pytest.mark.asyncio
    async def test_discover_adds_metadata_fields(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        client = _make_client(
            [
                _make_tool(
                    "get_cpu_info",
                    description="Read CPU info",
                    mutable=False,
                    is_read_only=True,
                ),
            ]
        )

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )

        with patch("src.mcp_client.registry.Client") as MockClient:
            MockClient.return_value = client
            tools = await registry.discover()

        assert tools[0]["server_name"] == "tool-server"
        assert tools[0]["mutable"] is False
        assert tools[0]["is_read_only"] is True
        assert tools[0]["is_rollbackable"] is False
        assert tools[0]["description"] == "Read CPU info"

    @pytest.mark.asyncio
    async def test_skips_unavailable_server(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        good_client = _make_client(
            [
                _make_tool("get_cpu_info", mutable=False, is_read_only=True),
            ]
        )

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
                ServerEntry(name="offline-server", url="http://offline:9999"),
            ]
        )

        def _factory(url):
            if "offline" in url:
                raise ConnectionError("refused")
            return good_client

        with patch("src.mcp_client.registry.Client") as MockClient:
            MockClient.side_effect = _factory
            tools = await registry.discover()

        assert len(tools) == 1
        assert tools[0]["name"] == "get_cpu_info"

    @pytest.mark.asyncio
    async def test_hidden_tools_filtered_from_llm_list(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        client = _make_client(
            [
                _make_tool(
                    "bash_classify", mutable=False, is_read_only=True, hidden=True
                ),
                _make_tool("bash", mutable=True),
                _make_tool("get_cpu_info", mutable=False, is_read_only=True),
            ]
        )

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )

        with patch("src.mcp_client.registry.Client") as MockClient:
            MockClient.return_value = client
            tools = await registry.discover()

        names = {t["name"] for t in tools}
        assert "bash_classify" not in names
        assert "bash" in names
        assert "get_cpu_info" in names


class TestServerRegistryRefresh:
    @pytest.mark.asyncio
    async def test_refresh_updates_single_server_cache(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        initial = _make_client(
            [
                _make_tool("get_cpu_info", mutable=False, is_read_only=True),
            ]
        )
        updated = _make_client(
            [
                _make_tool("get_cpu_info", mutable=False, is_read_only=True),
                _make_tool("get_memory_info", mutable=False, is_read_only=True),
            ]
        )

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )

        with patch("src.mcp_client.registry.Client") as MockClient:
            MockClient.side_effect = [initial]
            await registry.discover()
            assert len(registry.list_tools()) == 1

            MockClient.side_effect = [updated]
            await registry.refresh("tool-server")
            assert len(registry.list_tools()) == 2

    @pytest.mark.asyncio
    async def test_refresh_unknown_server_raises(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )

        with pytest.raises(KeyError):
            await registry.refresh("unknown")


class TestServerRegistryListTools:
    @pytest.mark.asyncio
    async def test_returns_cached_after_discover(self):
        from src.config.models import ServerEntry
        from src.mcp_client.registry import ServerRegistry

        client = _make_client(
            [
                _make_tool("get_cpu_info", mutable=False, is_read_only=True),
            ]
        )

        registry = ServerRegistry(
            [
                ServerEntry(name="tool-server", url="http://tool:8001"),
            ]
        )

        with patch("src.mcp_client.registry.Client") as MockClient:
            MockClient.return_value = client
            await registry.discover()

        tools = registry.list_tools()
        assert len(tools) == 1
