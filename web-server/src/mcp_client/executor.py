import asyncio

from fastmcp import Client

from src.mcp_client.registry import ServerRegistry
from src.models.tool import ExecutionStatus


class ToolExecutor:
    """Execute and classify tool calls via MCP servers.

    Routes calls to the correct server using a ServerRegistry.
    Merges the classification concern (previously ToolClassifier).
    """

    def __init__(self, registry: ServerRegistry, max_retries: int = 2) -> None:
        self._registry = registry
        self._max_retries = max_retries

    # ── tool discovery (delegated to registry) ──────────────────────────

    def list_tools(self) -> list[dict]:
        """Return the cached tool list from all connected servers."""
        return self._registry.list_tools()

    # ── classification ──────────────────────────────────────────────────

    async def classify(self, tool_name: str, params: dict, server_name: str) -> dict:
        """Call ``classify_tool`` on *server_name* and normalize the result.

        Returns ``{"is_read_only": bool, "is_rollbackable": bool}``.
        Defaults to dangerous (read_only=False) on any failure.
        """
        url = self._registry.url_for(server_name)
        async with Client(url) as client:
            result = await client.call_tool(
                "classify_tool",
                {"tool_name": tool_name, "params": params},
            )
            content = getattr(result, "content", None)
            if isinstance(content, dict):
                return _normalize_classification(content)
            return {"is_read_only": False, "is_rollbackable": False}

    # ── execution ───────────────────────────────────────────────────────

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        *,
        server_name: str,
        approval_status: str,
        request_id: str,
    ) -> dict:
        """Execute *tool_name* on *server_name*.

        All tools must carry an approved ``approval_status`` and a valid
        ``request_id`` — the server enforces this regardless of risk level.
        """
        if approval_status != "APPROVED":
            return {
                "execution_status": "SECURITY_VIOLATION",
                "error": {
                    "code": "SECURITY_VIOLATION",
                    "message": "approval_status must be APPROVED",
                },
            }
        url = self._registry.url_for(server_name)
        for attempt in range(self._max_retries + 1):
            try:
                async with Client(url) as client:
                    result = await client.call_tool(tool_name, arguments)
                    return {
                        "execution_status": (
                            ExecutionStatus.SUCCEEDED
                            if not getattr(result, "isError", False)
                            else ExecutionStatus.FAILED
                        ),
                        "output": getattr(result, "content", None),
                    }
            except (ConnectionError, ConnectionRefusedError) as e:
                if attempt == self._max_retries:
                    return {
                        "execution_status": ExecutionStatus.FAILED,
                        "error": {"message": str(e)},
                    }
                await asyncio.sleep(2**attempt)
            except TimeoutError as e:
                return {
                    "execution_status": ExecutionStatus.FAILED,
                    "error": {"message": str(e)},
                }
        return {"execution_status": ExecutionStatus.FAILED}

    async def execute_parallel(self, calls: list[dict]) -> list[dict]:
        """Execute multiple tool calls concurrently."""
        tasks = [
            self.execute(
                c["tool_name"],
                c.get("arguments", {}),
                server_name=c["server_name"],
                approval_status=c.get("approval_status", "APPROVED"),
                request_id=c.get("request_id", ""),
            )
            for c in calls
        ]
        return await asyncio.gather(*tasks)

    @staticmethod
    def _is_connect_error(exc: Exception) -> bool:
        return isinstance(exc, (ConnectionError, ConnectionRefusedError, TimeoutError))


def _normalize_classification(content: dict) -> dict:
    """Normalize camelCase (MCP) to snake_case."""
    return {
        "is_read_only": content.get("isReadOnly", content.get("is_read_only", False)),
        "is_rollbackable": content.get("isRollbackable", content.get("is_rollbackable", False)),
    }
