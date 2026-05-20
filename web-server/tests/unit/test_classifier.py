import pytest

pytestmark = pytest.mark.unit


class TestMcpClientApiError:
    def test_constructor_all_fields(self):
        from src.error import McpClientApiError

        e = McpClientApiError(
            error_code="TIMEOUT",
            message="connection timed out",
            status_code=504,
            detail="retry count 3",
        )
        assert e.error_code == "TIMEOUT"
        assert e.message == "connection timed out"
        assert e.status_code == 504
        assert e.detail == "retry count 3"

    def test_default_status_code(self):
        from src.error import McpClientApiError

        e = McpClientApiError(error_code="ERR", message="msg")
        assert e.status_code == 400

    def test_default_detail_empty(self):
        from src.error import McpClientApiError

        e = McpClientApiError(error_code="ERR", message="msg")
        assert e.detail == ""

    def test_is_exception(self):
        from src.error import McpClientApiError

        e = McpClientApiError(error_code="ERR", message="msg")
        assert isinstance(e, Exception)

    def test_str_uses_message(self):
        from src.error import McpClientApiError

        e = McpClientApiError(error_code="ERR", message="something broke")
        assert str(e) == "something broke"


class TestNormalizeClassification:
    def test_camelcase_keys_converted(self):
        from src.mcp_client.classifier import _normalize_classification

        result = _normalize_classification({"isReadOnly": False, "isRollbackable": True})
        assert result == {"is_read_only": False, "is_rollbackable": True}

    def test_snake_case_keys_preserved(self):
        from src.mcp_client.classifier import _normalize_classification

        result = _normalize_classification({"is_read_only": False, "is_rollbackable": True})
        assert result == {"is_read_only": False, "is_rollbackable": True}

    def test_missing_keys_use_defaults(self):
        from src.mcp_client.classifier import _normalize_classification

        result = _normalize_classification({})
        assert result == {"is_read_only": True, "is_rollbackable": False}

    def test_partial_camelcase_keys(self):
        from src.mcp_client.classifier import _normalize_classification

        result = _normalize_classification({"isReadOnly": False})
        assert result == {"is_read_only": False, "is_rollbackable": False}

    def test_partial_snake_keys(self):
        from src.mcp_client.classifier import _normalize_classification

        result = _normalize_classification({"is_rollbackable": True})
        assert result == {"is_read_only": True, "is_rollbackable": True}


class TestToolClassifierConstruction:
    def test_stores_urls(self):
        from src.mcp_client.classifier import ToolClassifier

        c = ToolClassifier(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")
        assert c._tool_server_url == "http://tool:8001"
        assert c._rag_server_url == "http://rag:8002"


class TestToolClassifierClassify:
    @pytest.mark.asyncio
    async def test_default_server_routes_to_tool_server(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.mcp_client.classifier import ToolClassifier

        c = ToolClassifier(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")

        mock_result = MagicMock()
        mock_result.content = {"isReadOnly": True, "isRollbackable": False}

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.classifier.Client", return_value=mock_client):
            result = await c.classify("get_cpu", {})

        assert result["is_read_only"] is True
        assert result["is_rollbackable"] is False

    @pytest.mark.asyncio
    async def test_rag_server_routes_to_rag_url(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.mcp_client.classifier import ToolClassifier

        c = ToolClassifier(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")

        mock_result = MagicMock()
        mock_result.content = {"is_read_only": True, "is_rollbackable": False}

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.classifier.Client", return_value=mock_client) as mock_cls:
            await c.classify("search_experience", {}, server="rag-server")
            # Should have used rag_server_url
            assert mock_cls.call_args[0][0] == "http://rag:8002"

    @pytest.mark.asyncio
    async def test_non_tool_server_uses_tool_url(self):
        """Only 'tool-server' uses tool URL; any other server name uses rag URL."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.mcp_client.classifier import ToolClassifier

        c = ToolClassifier(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")

        mock_result = MagicMock()
        mock_result.content = {"isReadOnly": False, "isRollbackable": False}

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.classifier.Client", return_value=mock_client) as mock_cls:
            await c.classify("some_tool", {}, server="other-server")
            assert mock_cls.call_args[0][0] == "http://rag:8002"

    @pytest.mark.asyncio
    async def test_non_dict_content_falls_back_to_defaults(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.mcp_client.classifier import ToolClassifier

        c = ToolClassifier(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")

        mock_result = MagicMock()
        mock_result.content = "not a dict"

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.classifier.Client", return_value=mock_client):
            result = await c.classify("get_cpu", {})

        assert result == {"is_read_only": True, "is_rollbackable": False}

    @pytest.mark.asyncio
    async def test_content_none_falls_back_to_defaults(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.mcp_client.classifier import ToolClassifier

        c = ToolClassifier(tool_server_url="http://tool:8001", rag_server_url="http://rag:8002")

        mock_result = MagicMock()
        mock_result.content = None

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=mock_result)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.mcp_client.classifier.Client", return_value=mock_client):
            result = await c.classify("get_cpu", {})

        assert result == {"is_read_only": True, "is_rollbackable": False}
