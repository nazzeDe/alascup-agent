"""
ebpf/__init__.py — eBPF 系统监控工具导出。

提供 MCP 工具对应的业务函数，包括：
- 持续订阅工具（进程启动/退出、TCP 连接）— 同步，读环形缓冲区
- 按需快照工具（syscall、I/O、OOM）— 异步，启动 bpftrace 子进程
"""

from __future__ import annotations

from .runtime import EbpfRuntime, ProbeKind, WatchKind

# 全局 eBPF runtime 实例，由 main.py 初始化
_runtime: EbpfRuntime | None = None


def init_ebpf_runtime(runtime: EbpfRuntime | None = None) -> EbpfRuntime:
    """Install or return the process-wide runtime used by function adapters."""
    global _runtime
    if runtime is not None:
        _runtime = runtime
    elif _runtime is None:
        _runtime = EbpfRuntime()
    return _runtime


def get_ebpf_runtime() -> EbpfRuntime | None:
    """获取已初始化的 eBPF runtime（可能为 None）。"""
    return _runtime


def _require_runtime() -> EbpfRuntime:
    """Return the process-wide runtime used by legacy function adapters."""
    return init_ebpf_runtime()


# ── 持续订阅工具（同步，读缓冲区即可）─────────────────────────────


def watch_process_exec() -> dict:
    """返回最近的新进程启动事件。"""
    return _require_runtime().watch(WatchKind.PROCESS_EXEC)


def watch_process_exit() -> dict:
    """返回最近的进程退出事件。"""
    return _require_runtime().watch(WatchKind.PROCESS_EXIT)


def watch_tcp_connections() -> dict:
    """返回最近的 TCP 连接事件。"""
    return _require_runtime().watch(WatchKind.TCP_CONNECTIONS)


# ── 按需快照工具（异步，统一通过 runtime.capture）───────────────────


async def trace_syscall_stats(duration: int = 10) -> dict:
    """采集 syscall 频率分布。
    duration: 采样秒数（默认 10s）。
    """
    return await _require_runtime().capture(ProbeKind.SYSCALL_STATS, duration)


async def trace_slow_syscalls(duration: int = 5) -> dict:
    """采集慢系统调用（延迟 > 100μs）。
    duration: 采样秒数（默认 5s）。
    """
    return await _require_runtime().capture(ProbeKind.SLOW_SYSCALLS, duration)


async def trace_tcp_drops(duration: int = 10) -> dict:
    """诊断 TCP 丢包。
    duration: 采样秒数（默认 10s）。
    """
    return await _require_runtime().capture(ProbeKind.TCP_DROPS, duration)


async def trace_io_latency(duration: int = 10) -> dict:
    """采集磁盘 I/O 延迟分布。
    duration: 采样秒数（默认 10s）。
    """
    return await _require_runtime().capture(ProbeKind.IO_LATENCY, duration)


async def trace_oom_events(duration: int = 30) -> dict:
    """捕获 OOM killer 事件。
    duration: 采样秒数（默认 30s）。
    """
    return await _require_runtime().capture(ProbeKind.OOM_EVENTS, duration)
