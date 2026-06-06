"""
ebpf/__init__.py — eBPF 系统监控工具导出。

提供 MCP 工具对应的业务函数，包括：
- 持续订阅工具（进程启动/退出、TCP 连接）— 同步，读环形缓冲区
- 按需快照工具（syscall、I/O、OOM）— 异步，启动 bpftrace 子进程
"""
from __future__ import annotations

from .runner import run_on_demand
from .subscription import SubscriptionManager

# 全局订阅管理器实例，由 main.py 初始化
_sub_mgr: SubscriptionManager | None = None


def init_subscriptions() -> SubscriptionManager:
    """创建并返回全局订阅管理器。"""
    global _sub_mgr
    if _sub_mgr is None:
        _sub_mgr = SubscriptionManager()
    return _sub_mgr


def get_subscription_manager() -> SubscriptionManager | None:
    """获取已初始化的订阅管理器（可能为 None）。"""
    return _sub_mgr


# ── 持续订阅工具（同步，读缓冲区即可）─────────────────────────────


def watch_process_exec() -> dict:
    """返回最近的新进程启动事件。"""
    mgr = get_subscription_manager()
    if mgr is None:
        return {"error": "subscription manager not initialized", "events": []}
    return {"events": mgr.drain("execsnoop.bt")}


def watch_process_exit() -> dict:
    """返回最近的进程退出事件。"""
    mgr = get_subscription_manager()
    if mgr is None:
        return {"error": "subscription manager not initialized", "events": []}
    return {"events": mgr.drain("proc_exit.bt")}


def watch_tcp_connections() -> dict:
    """返回最近的 TCP 连接事件。"""
    mgr = get_subscription_manager()
    if mgr is None:
        return {"error": "subscription manager not initialized", "events": []}
    return {"events": mgr.drain("tcpconn.bt")}


# ── 按需快照工具（异步，需 await run_on_demand）─────────────────────


async def trace_syscall_stats(duration: int = 5) -> dict:
    """采集 syscall 频率分布。
    duration: 采样秒数（默认 5s）。
    """
    return {"events": await run_on_demand("syscount.bt", timeout=float(duration))}


async def trace_slow_syscalls(duration: int = 5) -> dict:
    """采集慢系统调用（延迟 > 100μs）。
    duration: 采样秒数（默认 5s）。
    """
    return {"events": await run_on_demand("syscall_slow.bt", timeout=float(duration))}


async def trace_tcp_drops(duration: int = 10) -> dict:
    """诊断 TCP 丢包。
    duration: 采样秒数（默认 10s）。
    """
    return {"events": await run_on_demand("tcpdrop.bt", timeout=float(duration))}


async def trace_io_latency(duration: int = 10) -> dict:
    """采集磁盘 I/O 延迟分布。
    duration: 采样秒数（默认 10s）。
    """
    return {"events": await run_on_demand("biolatency.bt", timeout=float(duration))}


async def trace_oom_events(duration: int = 30) -> dict:
    """捕获 OOM killer 事件。
    duration: 采样秒数（默认 30s）。
    """
    return {"events": await run_on_demand("oomkill.bt", timeout=float(duration))}
