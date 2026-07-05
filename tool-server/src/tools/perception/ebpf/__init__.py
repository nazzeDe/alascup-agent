"""
ebpf/__init__.py — eBPF 系统监控工具导出。

提供 MCP 工具对应的业务函数，包括：
- 持续订阅工具（进程启动/退出、TCP 连接）— 同步，读环形缓冲区
- 按需快照工具（syscall、I/O、OOM）— 异步，启动 bpftrace 子进程
"""

from __future__ import annotations

from .runtime import EbpfRuntime

# 全局 eBPF runtime 实例，由 main.py 初始化
_runtime: EbpfRuntime | None = None


def init_ebpf_runtime() -> EbpfRuntime:
    """创建并返回全局 eBPF runtime。"""
    global _runtime
    if _runtime is None:
        _runtime = EbpfRuntime()
    return _runtime


def get_ebpf_runtime() -> EbpfRuntime | None:
    """获取已初始化的 eBPF runtime（可能为 None）。"""
    return _runtime


# ── 持续订阅工具（同步，读缓冲区即可）─────────────────────────────


def watch_process_exec() -> dict:
    """返回最近的新进程启动事件。"""
    runtime = get_ebpf_runtime()
    if runtime is None:
        return {
            "error": "subscription manager not initialized",
            "events": [],
            "probe_status": "not_initialized",
        }
    return runtime.watch("execsnoop.bt")


def watch_process_exit() -> dict:
    """返回最近的进程退出事件。"""
    runtime = get_ebpf_runtime()
    if runtime is None:
        return {
            "error": "subscription manager not initialized",
            "events": [],
            "probe_status": "not_initialized",
        }
    return runtime.watch("proc_exit.bt")


def watch_tcp_connections() -> dict:
    """返回最近的 TCP 连接事件。"""
    runtime = get_ebpf_runtime()
    if runtime is None:
        return {
            "error": "subscription manager not initialized",
            "events": [],
            "probe_status": "not_initialized",
        }
    return runtime.watch("tcpconn.bt")


# ── 按需快照工具（异步，需 await run_on_demand）─────────────────────


async def trace_syscall_stats(duration: int = 10) -> dict:
    """采集 syscall 频率分布。
    duration: 采样秒数（默认 10s）。
    """
    runtime = get_ebpf_runtime() or EbpfRuntime()
    return await runtime.trace("syscount.bt", duration)


async def trace_slow_syscalls(duration: int = 5) -> dict:
    """采集慢系统调用（延迟 > 100μs）。
    duration: 采样秒数（默认 5s）。
    """
    runtime = get_ebpf_runtime() or EbpfRuntime()
    return await runtime.trace("syscall_slow.bt", duration)


async def trace_tcp_drops(duration: int = 10) -> dict:
    """诊断 TCP 丢包。
    duration: 采样秒数（默认 10s）。
    """
    runtime = get_ebpf_runtime() or EbpfRuntime()
    return await runtime.trace("tcpdrop.bt", duration)


async def trace_io_latency(duration: int = 10) -> dict:
    """采集磁盘 I/O 延迟分布。
    duration: 采样秒数（默认 10s）。
    """
    runtime = get_ebpf_runtime() or EbpfRuntime()
    return await runtime.trace("biolatency.bt", duration)


async def trace_oom_events(duration: int = 30) -> dict:
    """捕获 OOM killer 事件。
    duration: 采样秒数（默认 30s）。
    """
    runtime = get_ebpf_runtime() or EbpfRuntime()
    return await runtime.trace("oomkill.bt", duration)
