from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
import sys
from typing import Any

from fastmcp import FastMCP
from loguru import logger

from src.cache import create_cache
from src.config import ToolServerConfig, load_config
from src.handlers import handle_execute_tool
from src.security.bash_classify import classify_bash
from src.tools.operation import run_bash
from src.tools.perception import (
    get_cpu_info,
    get_disk_usage,
    get_memory_info,
    get_network_info,
    get_process_list,
)
from src.tools.perception.ebpf import (
    init_subscriptions,
    watch_process_exec,
    watch_process_exit,
    watch_tcp_connections,
    trace_syscall_stats,
    trace_slow_syscalls,
    trace_tcp_drops,
    trace_io_latency,
    trace_oom_events,
)

from collections.abc import Callable

_ANY_OBJECT: dict[str, Any] = {"type": "object"}


async def create_server(config: ToolServerConfig) -> FastMCP:
    """Create a fully configured FastMCP tool-server.

    Each tool is defined exactly once via @server.tool() — there is no
    separate internal registry.  FastMCP's own get_tool() / list_tools()
    serve as the canonical tool directory.

    The eBPF subscription lifecycle (start on boot, stop on shutdown) is
    managed through FastMCP's ``lifespan`` parameter instead of the removed
    ``on_shutdown`` callback.
    """
    cache = create_cache(ttl=config.cache_ttl)
    _sub_mgr = init_subscriptions()

    @asynccontextmanager
    async def _lifespan(server: FastMCP):
        """Start eBPF subscription daemons on boot, stop on shutdown."""
        _startup_task = asyncio.ensure_future(_start_ebpf_subscriptions(_sub_mgr))
        try:
            yield
        finally:
            _startup_task.cancel()
            try:
                await _startup_task
            except (asyncio.CancelledError, Exception):
                pass
            await _sub_mgr.shutdown()
            logger.info("eBPF subscriptions stopped")

    server = FastMCP(
        name="tool-server",
        lifespan=_lifespan,
    )

    # classify_fn can't live in meta (FastMCP serializes meta → must be
    # JSON-safe).  Track mutable tools locally instead.
    _classify_fns: dict[str, Callable] = {}

    # ── Perception tools (read-only system queries) ──────────────────────

    @server.tool(
        name="get_cpu_info",
        description="获取 CPU 型号、核心数、负载和利用率",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _get_cpu_info() -> dict:
        return get_cpu_info(config)

    @server.tool(
        name="get_memory_info",
        description="获取物理内存和 Swap 使用量",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _get_memory_info() -> dict:
        return get_memory_info(config)

    @server.tool(
        name="get_disk_usage",
        description="获取磁盘使用率和空间分布",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _get_disk_usage(path: str = "/") -> dict:
        return get_disk_usage(config, path=path)

    @server.tool(
        name="get_network_info",
        description="获取网卡地址和 I/O 计数器",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _get_network_info() -> dict:
        return get_network_info(config)

    @server.tool(
        name="get_process_list",
        description="获取运行进程列表（PID/名称/CPU/内存/状态）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _get_process_list() -> dict:
        return {"processes": get_process_list(config)}

    # ── Operation tools (mutable — classify_fn tracked locally) ───────────

    @server.tool(
        name="bash",
        description="在沙箱环境中执行 Shell 命令",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": False, "is_rollbackable": False, "mutable": True},
    )
    def _bash(command: str = "", timeout: int | None = None) -> dict:
        return run_bash(config, command=command, timeout=timeout)

    _classify_fns["bash"] = classify_bash

    # ── execute_tool: secured dispatcher ─────────────────────────────────

    @server.tool(
        name="execute_tool",
        description="安全执行工具（需 APPROVED 状态 + 有效 request_id）",
        meta={"hidden": True},
        output_schema={
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "chat_id": {"type": "string"},
                "message_id": {"type": "string"},
                "params": {"type": "object"},
                "request_id": {"type": "string"},
                "approval_status": {"type": "string"},
            },
        },
    )
    async def execute(
        tool_name: str = "",
        chat_id: str = "",
        message_id: str = "",
        params: dict | None = None,
        request_id: str = "",
        approval_status: str = "PENDING",
    ) -> dict:
        return await handle_execute_tool(
            server=server,
            tool_name=tool_name,
            chat_id=chat_id,
            message_id=message_id,
            params=params or {},
            request_id=request_id,
            approval_status=approval_status,
            config=config,
            cache=cache,
        )

    # ── eBPF tools (read-only, bpftrace-based system observability) ───────

    @server.tool(
        name="watch_process_exec",
        description="实时进程启动事件流（基于 eBPF 的持续跟踪）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _watch_process_exec() -> dict:
        return watch_process_exec()

    @server.tool(
        name="watch_process_exit",
        description="实时进程退出事件流（PID + 退出码，基于 eBPF）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _watch_process_exit() -> dict:
        return watch_process_exit()

    @server.tool(
        name="watch_tcp_connections",
        description="实时 TCP 连接追踪（基于 eBPF）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    def _watch_tcp_connections() -> dict:
        return watch_tcp_connections()

    @server.tool(
        name="trace_syscall_stats",
        description="采集系统调用频率分布（按进程分组，基于 eBPF）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    async def _trace_syscall_stats(duration: int = 5) -> dict:
        return await trace_syscall_stats(duration=duration)

    @server.tool(
        name="trace_slow_syscalls",
        description="检测延迟超过 100μs 的慢系统调用（基于 eBPF）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    async def _trace_slow_syscalls(duration: int = 5) -> dict:
        return await trace_slow_syscalls(duration=duration)

    @server.tool(
        name="trace_tcp_drops",
        description="诊断 TCP 丢包事件（基于 eBPF）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    async def _trace_tcp_drops(duration: int = 10) -> dict:
        return await trace_tcp_drops(duration=duration)

    @server.tool(
        name="trace_io_latency",
        description="采集磁盘 I/O 延迟分布（基于 eBPF）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    async def _trace_io_latency(duration: int = 10) -> dict:
        return await trace_io_latency(duration=duration)

    @server.tool(
        name="trace_oom_events",
        description="捕获 OOM killer 进程杀死事件（基于 eBPF）",
        output_schema=_ANY_OBJECT,
        meta={"is_read_only": True, "is_rollbackable": True, "mutable": False},
    )
    async def _trace_oom_events(duration: int = 30) -> dict:
        return await trace_oom_events(duration=duration)

    # ── Start eBPF subscription daemons after tools are registered ────────
    # ── health: lightweight liveness probe ───────────────────────────────

    @server.tool(
        name="health",
        description="Liveness 探针，返回工具列表和节点信息",
        output_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "tool_count": {"type": "integer"},
            },
        },
        meta={"is_read_only": True, "is_rollbackable": False, "mutable": False, "hidden": True},
    )
    async def health() -> dict:
        tools = await server.list_tools()
        return {"status": "healthy", "tool_count": len(tools)}

    # ── Companion classification tools for mutable tools ─────────────────

    _register_classify_companions(server, _classify_fns)

    return server


async def _start_ebpf_subscriptions(sub_mgr) -> None:
    """在后台启动 ebpf 持续订阅 daemon。"""
    try:
        await sub_mgr.start_all()
    except Exception:
        logger.opt(exception=True).warning("eBPF subscription startup failed (non-fatal)")


def _register_classify_companions(server: FastMCP, classify_fns: dict[str, Callable]) -> None:
    """Auto-generate hidden companion classification tools.

    For each tool name in *classify_fns*, creates a hidden
    {name}_classify companion on the same server.
    """
    _COMPANION_FACTORY: dict[str, Any] = {
        "bash": lambda fn: lambda command="": fn(command),
    }

    for tool_name, classify_fn in classify_fns.items():
        factory = _COMPANION_FACTORY.get(tool_name)
        if factory is None:
            continue

        companion_name = f"{tool_name}_classify"
        companion_fn = factory(classify_fn)

        server.tool(
            name=companion_name,
            description=f"Security classification companion for {tool_name}",
            meta={"hidden": True, "is_read_only": True, "mutable": False},
        )(companion_fn)


def main() -> int:
    log_level = os.getenv("TOOL_SERVER_LOG_LEVEL", "WARNING").upper()
    logger.remove()
    logger.add(sys.stderr, level=log_level, format="{time:HH:mm:ss.SSS} | {level: <8} | {message}")

    try:
        config = load_config()
        server = asyncio.run(create_server(config))
        server.run(transport="streamable-http", host="0.0.0.0", port=config.port)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
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