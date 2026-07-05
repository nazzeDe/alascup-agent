from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from loguru import logger

from src.cache import create_cache
from src.config import ToolServerConfig, load_config
from src.tool_catalog import register_tool_catalog
from src.tools.perception.ebpf.runtime import EbpfRuntime


async def create_server(
    config: ToolServerConfig, ebpf_runtime: EbpfRuntime | None = None
) -> FastMCP:
    """Create a fully configured FastMCP tool-server.

    Tool definitions live in the Tool catalog Module. FastMCP's own
    get_tool() / list_tools() serve as the canonical runtime directory.

    The eBPF subscription lifecycle (start on boot, stop on shutdown) is
    managed through FastMCP's ``lifespan`` parameter instead of the removed
    ``on_shutdown`` callback.
    """
    cache = create_cache(ttl=config.cache_ttl)
    ebpf_runtime = ebpf_runtime or EbpfRuntime()

    @asynccontextmanager
    async def _lifespan(server: FastMCP):
        """Start eBPF subscription daemons on boot, stop on shutdown."""
        _startup_task = asyncio.ensure_future(_start_ebpf_subscriptions(ebpf_runtime))
        try:
            yield
        finally:
            _startup_task.cancel()
            try:
                await _startup_task
            except asyncio.CancelledError:
                logger.debug("eBPF subscription startup task cancelled during shutdown")
            except Exception:
                logger.opt(exception=True).warning(
                    "eBPF subscription startup task failed during shutdown"
                )
            await ebpf_runtime.shutdown()
            logger.info("eBPF subscriptions stopped")

    server = FastMCP(
        name="tool-server",
        lifespan=_lifespan,
    )

    register_tool_catalog(server, config, cache, ebpf_runtime)
    return server


async def _start_ebpf_subscriptions(sub_mgr) -> None:
    """在后台启动 ebpf 持续订阅 daemon。"""
    try:
        await sub_mgr.start_all()
    except Exception:
        logger.opt(exception=True).warning(
            "eBPF subscription startup failed (non-fatal)"
        )


def main() -> int:
    log_level = os.getenv("TOOL_SERVER_LOG_LEVEL", "WARNING").upper()
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format="{time:HH:mm:ss.SSS} | {level: <8} | {message}",
    )

    try:
        config = load_config()
        server = asyncio.run(create_server(config))
        server.run(transport="streamable-http", host=config.host, port=config.port)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.opt(exception=True).error("Failed to start tool-server")
        return 1


# Lazy module-level server for CLI (fastmcp run src/main.py).
_mcp: FastMCP | None = None


def __getattr__(name: str):
    if name == "mcp":
        global _mcp
        if _mcp is None:
            _mcp = asyncio.run(create_server(load_config()))
        return _mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    raise SystemExit(main())
