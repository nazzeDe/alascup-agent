from __future__ import annotations

import os

from fastmcp import FastMCP

from src.cache import ToolCache
from src.execution_lifecycle import execute_tool_lifecycle

LOG_LEVEL = os.getenv("TOOL_SERVER_LOG_LEVEL", "WARNING").upper()


async def handle_execute_tool(
    server: FastMCP,
    tool_name: str,
    chat_id: str,
    params: dict,
    request_id: str,
    approval_status: str,
    cache: ToolCache,
    auth_token: str = "",
    shared_secret: str = "",
) -> dict:
    """FastMCP adapter for execute_tool requests."""
    return await execute_tool_lifecycle(
        server=server,
        tool_name=tool_name,
        chat_id=chat_id,
        params=params,
        request_id=request_id,
        approval_status=approval_status,
        cache=cache,
        auth_token=auth_token,
        shared_secret=shared_secret,
    )
