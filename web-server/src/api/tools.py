from fastapi import APIRouter, Depends, Query
from src.services.container import tool_executor

router = APIRouter()


@router.get("/tools")
async def list_tools(executor=Depends(tool_executor)):
    return executor.list_tools()


@router.post("/tools/refresh")
async def refresh_tools(
    server_name: str = Query("", description="Server to refresh, or empty for all"),
    executor=Depends(tool_executor),
):
    """Re-discover tools from a specific MCP server (or all servers if name is empty)."""
    if server_name:
        return await executor.refresh_server(server_name)
    await executor.discover()
    return executor.list_tools()
