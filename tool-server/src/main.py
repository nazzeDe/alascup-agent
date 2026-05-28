from __future__ import annotations

import os
import sys
from typing import Any

from fastmcp import FastMCP
from loguru import logger

from src.cache import create_cache
from src.config import ToolServerConfig, load_config
from src.handlers import handle_execute_tool
from src.security.bash_classify import classify_bash
from src.security.operation_classify import classify_manage_service
from src.tools.operation import manage_service, run_bash
from src.tools.perception import (
    get_cpu_info,
    get_disk_usage,
    get_memory_info,
    get_network_info,
    get_process_list,
    read_logs,
)
from src.tools.registry import (
    ToolMeta,
    clear_registry,
    list_tools,
    register,
)

# Attach classification functions to mutable operation tools (used by
# _register_tools to auto-populate classify_fn on ToolMeta).
run_bash.__classify__ = classify_bash  # type: ignore[attr-defined]
manage_service.__classify__ = classify_manage_service  # type: ignore[attr-defined]

TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_cpu_info": {
        "type": "object",
        "properties": {},
    },
    "get_memory_info": {
        "type": "object",
        "properties": {},
    },
    "get_disk_usage": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "磁盘路径", "default": "/"},
        },
    },
    "get_network_info": {
        "type": "object",
        "properties": {},
    },
    "get_process_list": {
        "type": "object",
        "properties": {},
    },
    "read_logs": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "日志文件路径"},
            "lines": {"type": "integer", "description": "返回行数", "default": 50},
        },
    },
    "run_bash": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell 命令"},
            "timeout": {"type": "integer", "description": "超时秒数"},
        },
    },
    "manage_service": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "服务名称"},
            "action": {"type": "string", "description": "操作: status, start, stop, restart, enable, disable, list"},
        },
    },
}

# Unified output schema — all tools return a dict.
_ANY_OBJECT: dict[str, Any] = {"type": "object"}

PERCEPTION_TOOLS = {
    "get_cpu_info": ("获取 CPU 型号、核心数、负载和利用率", get_cpu_info, True,
                     {"is_read_only": True, "is_rollbackable": True, "mutable": False}),
    "get_memory_info": ("获取物理内存和 Swap 使用量", get_memory_info, True,
                        {"is_read_only": True, "is_rollbackable": True, "mutable": False}),
    "get_disk_usage": ("获取磁盘使用率和空间分布", get_disk_usage, True,
                       {"is_read_only": True, "is_rollbackable": True, "mutable": False}),
    "get_network_info": ("获取网卡地址和 I/O 计数器", get_network_info, True,
                         {"is_read_only": True, "is_rollbackable": True, "mutable": False}),
    "get_process_list": ("获取运行进程列表（PID/名称/CPU/内存/状态）", get_process_list, True,
                         {"is_read_only": True, "is_rollbackable": True, "mutable": False}),
    "read_logs": ("读取日志文件末尾行", read_logs, True,
                  {"is_read_only": True, "is_rollbackable": True, "mutable": False}),
}

OPERATION_TOOLS = {
    "run_bash": ("在沙箱环境中执行 Shell 命令", run_bash, False,
                 {"is_read_only": False, "is_rollbackable": False, "mutable": True}),
    "manage_service": ("管理 systemd 服务", manage_service, False,
                       {"is_read_only": False, "is_rollbackable": False, "mutable": True}),
}


def _register_tools(config: ToolServerConfig) -> None:
    clear_registry()
    for tools_dict in (PERCEPTION_TOOLS, OPERATION_TOOLS):
        for name, (desc, fn, is_read_only, meta) in tools_dict.items():
            classify_fn = getattr(fn, "__classify__", None)
            register(ToolMeta(
                name=name,
                description=desc,
                is_read_only=is_read_only,
                input_schema=TOOL_SCHEMAS.get(name, {}),
                fn=lambda c=config, f=fn, **kw: f(c, **kw),
                classify_fn=classify_fn,
                meta=meta,
            ))


def _register_classify_companions(server: FastMCP) -> None:
    """Auto-generate companion classification tools for mutable tools.

    Scans the registry for tools with a classify_fn and creates hidden
    MCP companion tools named {tool_name}_classify. These are called by
    the web-server review layer to dynamically determine safe/dangerous.
    """

    # Map tool name → wrapper factory that produces an explicit-param callable
    # (fastmcp rejects **kwargs, so we must expose named parameters).
    # Values: (Callable) -> Callable — factories producing explicit-param wrappers
    _COMPANION_FACTORY: dict[str, Any] = {
        "run_bash": lambda fn: lambda command="": fn(command),
        "manage_service": lambda fn: lambda name="", action="": fn(name=name, action=action),
    }

    for tool in list_tools():
        if tool.classify_fn is None:
            continue
        factory = _COMPANION_FACTORY.get(tool.name)
        if factory is None:
            continue

        companion_name = f"{tool.name}_classify"
        classify_fn = tool.classify_fn
        companion_fn = factory(classify_fn)

        server.tool(
            name=companion_name,
            description=f"Security classification companion for {tool.name}",
            meta={"hidden": True, "is_read_only": True, "mutable": False},
        )(companion_fn)

        register(ToolMeta(
            name=companion_name,
            description=f"Security classification companion for {tool.name}",
            is_read_only=True,
            input_schema={},
            fn=lambda c=None, f=classify_fn, **kw: f(**kw),
            classify_fn=None,
            hidden=True,
            meta={"hidden": True, "is_read_only": True, "mutable": False},
        ))


def create_server(config: ToolServerConfig) -> FastMCP:
    _register_tools(config)
    cache = create_cache(ttl=config.cache_ttl)

    server = FastMCP(name="tool-server")

    # ── perception tools (explicit signatures — fastmcp rejects **kwargs) ──
    @server.tool(name="get_cpu_info", description="获取 CPU 型号、核心数、负载和利用率", output_schema=_ANY_OBJECT, meta={"is_read_only": True, "is_rollbackable": True, "mutable": False})
    def _get_cpu_info() -> dict:
        return get_cpu_info(config)

    @server.tool(name="get_memory_info", description="获取物理内存和 Swap 使用量", output_schema=_ANY_OBJECT, meta={"is_read_only": True, "is_rollbackable": True, "mutable": False})
    def _get_memory_info() -> dict:
        return get_memory_info(config)

    @server.tool(name="get_disk_usage", description="获取磁盘使用率和空间分布", output_schema=_ANY_OBJECT, meta={"is_read_only": True, "is_rollbackable": True, "mutable": False})
    def _get_disk_usage(path: str = "/") -> dict:
        return get_disk_usage(config, path=path)

    @server.tool(name="get_network_info", description="获取网卡地址和 I/O 计数器", output_schema=_ANY_OBJECT, meta={"is_read_only": True, "is_rollbackable": True, "mutable": False})
    def _get_network_info() -> dict:
        return get_network_info(config)

    @server.tool(name="get_process_list", description="获取运行进程列表（PID/名称/CPU/内存/状态）", output_schema=_ANY_OBJECT, meta={"is_read_only": True, "is_rollbackable": True, "mutable": False})
    def _get_process_list() -> dict:
        return {"processes": get_process_list(config)}

    @server.tool(name="read_logs", description="读取日志文件末尾行", output_schema=_ANY_OBJECT, meta={"is_read_only": True, "is_rollbackable": True, "mutable": False})
    def _read_logs(path: str = "", lines: int = 50) -> dict:
        return {"lines": read_logs(config, path=path, lines=lines)}

    # ── operation tools ──
    @server.tool(name="run_bash", description="在沙箱环境中执行 Shell 命令", output_schema=_ANY_OBJECT, meta={"is_read_only": False, "is_rollbackable": False, "mutable": True})
    def _run_bash(command: str = "", timeout: int | None = None) -> dict:
        return run_bash(config, command=command, timeout=timeout)

    @server.tool(name="manage_service", description="管理 systemd 服务", output_schema=_ANY_OBJECT, meta={"is_read_only": False, "is_rollbackable": False, "mutable": True})
    def _manage_service(name: str = "", action: str = "") -> dict:
        return manage_service(config, name=name, action=action)

    # ── execute_tool: secured execution dispatcher, called by web-server after approval ──
    @server.tool(
        name="execute_tool",
        description="安全执行工具（需 APPROVED 状态 + 有效 request_id）",
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
    def execute(
        tool_name: str = "",
        chat_id: str = "",
        message_id: str = "",
        params: dict | None = None,
        request_id: str = "",
        approval_status: str = "PENDING",
    ) -> dict:
        return handle_execute_tool(
            tool_name=tool_name,
            chat_id=chat_id,
            message_id=message_id,
            params=params or {},
            request_id=request_id,
            approval_status=approval_status,
            config=config,
            cache=cache,
        )

    # ── health: lightweight liveness probe ──
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
        meta={"is_read_only": True, "is_rollbackable": False, "mutable": False},
    )
    def health() -> dict:
        return {"status": "healthy", "tool_count": len(list_tools())}

    # ── companion classification tools for mutable operation tools ──
    _register_classify_companions(server)

    return server


def main() -> int:
    log_level = os.getenv("TOOL_SERVER_LOG_LEVEL", "WARNING").upper()
    logger.remove()
    logger.add(sys.stderr, level=log_level, format="{time:HH:mm:ss.SSS} | {level: <8} | {message}")

    try:
        config = load_config()
        server = create_server(config)
        server.run(transport="streamable-http", host="0.0.0.0", port=config.port)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.opt(exception=True).error("Failed to start tool-server")
        return 1


# Lazy module-level server for CLI (fastmcp run src/main.py).
# Created on access so that plain imports don't trigger full server init.
def __getattr__(name: str):
    if name == "mcp":
        return create_server(load_config())
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if __name__ == "__main__":
    raise SystemExit(main())
