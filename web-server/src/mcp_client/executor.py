import asyncio

from fastmcp import Client

from src.mcp_client.registry import ServerRegistry
from src.models.tool import ExecutionStatus


class ToolExecutor:
    """Execute and classify tool calls via MCP servers.

    Routes calls to the correct server using a ServerRegistry.
    Classification for mutable tools uses companion tools (``{tool}_classify``).
    """

    def __init__(self, registry: ServerRegistry, max_retries: int = 2) -> None:
        self._registry = registry
        self._max_retries = max_retries

    # ── tool discovery (delegated to registry) ──────────────────────────

    async def discover(self) -> None:
        """Connect to all configured MCP servers and populate the tool cache."""
        await self._registry.discover()

    def list_tools(self) -> list[dict]:
        """Return the cached tool list from all connected servers."""
        return self._registry.list_tools()

    # ── classification ──────────────────────────────────────────────────

    async def classify_companion(self, tool_name: str, params: dict, server_name: str) -> dict:
        """Call ``{tool_name}_classify`` companion tool on *server_name*.

        Returns ``{"safe": bool}``.
        Defaults to unsafe (safe=False) on any failure — fail-closed.
        """
        url = self._registry.url_for(server_name)
        async with Client(url) as client:
            result = await client.call_tool(
                f"{tool_name}_classify", params,
            )
            content = getattr(result, "content", None)
            if isinstance(content, dict):
                return {"safe": content.get("safe", False)}
            return {"safe": False}

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
