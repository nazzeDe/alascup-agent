import asyncio

from fastmcp import Client

from src.agent.shared import normalize_tool_result
from src.mcp_client.registry import ServerRegistry
from src.models.tool import ExecutionStatus


class ToolExecutor:
    """Execute and classify tool calls via MCP servers.

    Routes calls to the correct server using a ServerRegistry.
    Classification for mutable tools uses companion tools (``{tool}_classify``).
    """

    def __init__(
        self,
        registry: ServerRegistry,
        max_retries: int = 2,
        toolserver_auth_token: str = "",
    ) -> None:
        self._registry = registry
        self._max_retries = max_retries
        self._toolserver_auth_token = toolserver_auth_token

    # ── tool discovery (delegated to registry) ──────────────────────────

    async def discover(self) -> None:
        """Connect to all configured MCP servers and populate the tool cache."""
        await self._registry.discover()

    async def refresh_server(self, server_name: str) -> list[dict]:
        """Re-discover a single server and return its tools."""
        await self._registry.refresh(server_name)
        return self._registry.list_tools()

    def list_tools(self) -> list[dict]:
        """Return the cached tool list from all connected servers."""
        return self._registry.list_tools()

    # ── classification ──────────────────────────────────────────────────

    async def classify_companion(
        self, tool_name: str, params: dict, server_name: str
    ) -> dict:
        """Call ``{tool_name}_classify`` companion tool on *server_name*.

        Returns ``{"safe": bool}``.
        Defaults to unsafe (safe=False) on any failure — fail-closed.
        """
        import json

        url = self._registry.url_for(server_name)
        async with Client(url) as client:
            result = await client.call_tool(
                f"{tool_name}_classify",
                params,
            )
            content = getattr(result, "content", None)
            if isinstance(content, list) and content:
                try:
                    data = json.loads(getattr(content[0], "text", "{}"))
                    return {"safe": data.get("safe", False)}
                except (json.JSONDecodeError, AttributeError):
                    return {"safe": False}
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
        chat_id: str = "",
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
                    call_name, args = self._call_payload(
                        tool_name,
                        arguments,
                        server_name=server_name,
                        approval_status=approval_status,
                        request_id=request_id,
                        chat_id=chat_id,
                    )
                    result = await client.call_tool(call_name, args)
                    tool_server_result = self._tool_server_result(result, server_name)
                    if tool_server_result is not None:
                        return tool_server_result
                    output = _extract_output(result)
                    return {
                        "execution_status": ToolExecutor._resolve_status(result),
                        "output": output,
                    }
            except (ConnectionError, ConnectionRefusedError) as e:
                if attempt == self._max_retries:
                    return {
                        "execution_status": ExecutionStatus.FAILED,
                        "error": {"message": str(e)},
                    }
                await asyncio.sleep(2**attempt)
            except (TimeoutError, RuntimeError) as e:
                return {
                    "execution_status": ExecutionStatus.FAILED,
                    "error": {"message": str(e)},
                }
            except Exception as e:
                return {
                    "execution_status": ExecutionStatus.FAILED,
                    "error": {"message": f"{type(e).__name__}: {e}"},
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
                chat_id=c.get("chat_id", ""),
            )
            for c in calls
        ]
        return await asyncio.gather(*tasks)

    def _call_payload(
        self,
        tool_name: str,
        arguments: dict,
        *,
        server_name: str,
        approval_status: str,
        request_id: str,
        chat_id: str,
    ) -> tuple[str, dict | None]:
        args = arguments if isinstance(arguments, dict) and arguments else None
        if server_name != "tool-server":
            return tool_name, args
        return (
            "execute_tool",
            {
                "tool_name": tool_name,
                "chat_id": chat_id,
                "params": arguments if isinstance(arguments, dict) else {},
                "request_id": request_id,
                "approval_status": approval_status,
                "auth_token": self._toolserver_auth_token,
            },
        )

    @staticmethod
    def _tool_server_result(result, server_name: str) -> dict | None:
        if server_name != "tool-server":
            return None
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            return normalize_tool_result(data)
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return normalize_tool_result(structured)
        return None

    @staticmethod
    def _resolve_status(result) -> ExecutionStatus:
        """Derive execution status from structured_content or isError.

        Tools like bash embed ``execution_status`` in their return dict.
        MCP ``isError`` only reflects transport-level failure, so we
        inspect the payload first and fall back to the protocol flag.
        """
        data = getattr(result, "data", None)
        if isinstance(data, dict):
            raw = data.get("execution_status")
            if raw is not None:
                try:
                    return ExecutionStatus(raw)
                except ValueError:
                    pass
        return (
            ExecutionStatus.FAILED
            if getattr(result, "isError", False)
            else ExecutionStatus.SUCCEEDED
        )


def _extract_output(result) -> str | None:
    """Extract text content from an MCP CallToolResult.

    Handles fastmcp TextContent blocks (content is a list of blocks each
    with a ``.text`` attribute).
    """
    content = getattr(result, "content", None)
    if not content or not isinstance(content, list):
        return None
    texts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return "\n".join(texts) if texts else None
