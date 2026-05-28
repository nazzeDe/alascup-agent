"""ServerRegistry — MCP server discovery, tool cache, and URL routing."""

from loguru import logger

from fastmcp import Client

from src.config.models import ServerEntry


class ServerRegistry:
    """Discovers MCP servers and caches tool metadata with routing info.

    Each tool in the cache carries:
      - name: original tool name (no prefix)
      - server_name: which server it belongs to
      - mutable: whether classification is per-call (companion tool required)
      - is_read_only: static read-only flag
      - is_rollbackable: static rollback flag
    """

    def __init__(self, servers: list[ServerEntry]) -> None:
        self._servers = {s.name: s for s in servers}
        self._tools: list[dict] = []

    # ── public API ──────────────────────────────────────────────────────

    def url_for(self, server_name: str) -> str:
        """Return the URL for *server_name*, or raise KeyError."""
        return self._servers[server_name].url

    def list_tools(self) -> list[dict]:
        """Return the cached tool list (all connected servers)."""
        return list(self._tools)

    async def discover(self) -> list[dict]:
        """Connect to every configured server, fetch tools, populate cache.

        Unavailable servers are skipped with a warning.
        Tools with ``meta.hidden=True`` (companion classification tools) are
        stripped from the public tool list.
        """
        all_tools: list[dict] = []
        for entry in self._servers.values():
            try:
                server_tools = await self._fetch_tools(entry)
            except (ConnectionError, ConnectionRefusedError, OSError, RuntimeError) as exc:
                logger.warning(
                    "Server {name} ({url}) unavailable, skipping: {err}",
                    name=entry.name,
                    url=entry.url,
                    err=exc,
                )
                continue
            all_tools.extend(server_tools)
        self._tools = all_tools
        return all_tools

    async def refresh(self, server_name: str) -> None:
        """Re-discover a single server and update its entries in the cache."""
        entry = self._servers[server_name]
        fresh = await self._fetch_tools(entry)
        self._tools = [t for t in self._tools if t["server_name"] != server_name]
        self._tools.extend(fresh)

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    async def _fetch_tools(entry: ServerEntry) -> list[dict]:
        async with Client(entry.url) as client:
            raw = await client.list_tools()
        tools: list[dict] = []
        for t in raw:
            meta = getattr(t, "meta", None) or {}
            if meta.get("hidden"):
                continue
            tools.append({
                "name": t.name,
                "server_name": entry.name,
                "description": getattr(t, "description", ""),
                "params_schema": getattr(t, "inputSchema", None) or {},
                "mutable": meta.get("mutable", False),
                "is_read_only": meta.get("is_read_only", False),
                "is_rollbackable": meta.get("is_rollbackable", False),
            })
        return tools
