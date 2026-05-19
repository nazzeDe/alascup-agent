import asyncio

from fastmcp import Client

from src.models.tool import ExecutionStatus


class ToolExecutor:
    def __init__(
        self,
        tool_server_url: str,
        rag_server_url: str,
        max_retries: int = 2,
    ) -> None:
        self.tool_server_url = tool_server_url
        self.rag_server_url = rag_server_url
        self.max_retries = max_retries

    async def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        async with Client(self.tool_server_url) as client:
            for t in await client.list_tools():
                tools.append({"name": t.name, "server": "tool-server", "description": getattr(t, "description", "")})
        async with Client(self.rag_server_url) as client:
            for t in await client.list_tools():
                tools.append({"name": t.name, "server": "rag-server", "description": getattr(t, "description", "")})
        return tools

    def _server_url(self, server: str | None = None) -> str:
        if server and server != "tool-server":
            return self.rag_server_url
        return self.tool_server_url

    async def execute(self, tool_name: str, arguments: dict, server: str | None = None,
                      approval_status: str | None = None, is_read_only: bool = True) -> dict:
        if not is_read_only and approval_status != "APPROVED":
            return {
                "execution_status": "SECURITY_VIOLATION",
                "error": {"code": "SECURITY_VIOLATION", "message": "approval_status must be APPROVED for non-readonly tools"},
            }
        server_url = self._server_url(server)
        for attempt in range(self.max_retries + 1):
            try:
                async with Client(server_url) as client:
                    result = await client.call_tool(tool_name, arguments)
                    return {
                        "execution_status": ExecutionStatus.SUCCEEDED
                        if not getattr(result, "isError", False)
                        else ExecutionStatus.FAILED,
                        "output": getattr(result, "content", None),
                    }
            except (ConnectionError, ConnectionRefusedError) as e:
                if attempt == self.max_retries:
                    return {"execution_status": ExecutionStatus.FAILED, "error": {"message": str(e)}}
                await asyncio.sleep(2 ** attempt)
            except TimeoutError as e:
                return {"execution_status": ExecutionStatus.FAILED, "error": {"message": str(e)}}
        return {"execution_status": ExecutionStatus.FAILED}

    async def execute_parallel(self, calls: list[dict]) -> list[dict]:
        tasks = [
            self.execute(c["tool_name"], c.get("arguments", {}), c.get("server"),
                         c.get("approval_status"), c.get("is_read_only", True))
            for c in calls
        ]
        return await asyncio.gather(*tasks)

    @staticmethod
    def _is_connect_error(exc: Exception) -> bool:
        return isinstance(exc, (ConnectionError, ConnectionRefusedError, TimeoutError))
