"""Tool operation classifier — queries tool-server for is_read_only/is_rollbackable."""

from fastmcp import Client


class ToolClassifier:
    """Classify tool operations by querying tool-server's classify_tool MCP tool."""

    def __init__(self, tool_server_url: str, rag_server_url: str) -> None:
        self._tool_server_url = tool_server_url
        self._rag_server_url = rag_server_url

    async def classify(self, tool_name: str, params: dict, server: str | None = None) -> dict:
        """Return {"is_read_only": bool, "is_rollbackable": bool}."""
        server_url = self._tool_server_url
        if server and server != "tool-server":
            server_url = self._rag_server_url

        async with Client(server_url) as client:
            result = await client.call_tool("classify_tool", {"tool_name": tool_name, "params": params})
            content = getattr(result, "content", None)
            if isinstance(content, dict):
                return _normalize_classification(content)
            return {"is_read_only": True, "is_rollbackable": False}


def _normalize_classification(content: dict) -> dict:
    """Normalize camelCase (MCP) to snake_case."""
    return {
        "is_read_only": content.get("isReadOnly", content.get("is_read_only", True)),
        "is_rollbackable": content.get("isRollbackable", content.get("is_rollbackable", False)),
    }
