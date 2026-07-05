"""
subscription.py — bpftrace 持续订阅管理。

维护后台常驻的 bpftrace 进程，事件写入环形缓冲区，
通过 drain() 非阻塞读取累计事件。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from .resolver import resolve


class BpftraceDaemon:
    """管理一个持续运行的 bpftrace 进程及其事件缓冲区。"""

    def __init__(
        self,
        script_name: str,
        buffer_size: int = 4096,
        restart_delay: float = 3.0,
        resolve_fn: Callable[[str], Path | None] = resolve,
    ) -> None:
        self._script_name = script_name
        self._script_path = resolve_fn(script_name)
        self._buffer_size = buffer_size
        self._restart_delay = restart_delay

        self._proc: asyncio.subprocess.Process | None = None
        self._buffer: asyncio.Queue[dict] = asyncio.Queue(maxsize=buffer_size)
        self._consume_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._permanent_failure: bool = False

    async def start(self) -> None:
        """启动子进程及其消费者协程。"""
        if self._script_path is None:
            logger.error(
                "bpftrace_daemon no_variant script={} - no probe variant found for this kernel",
                self._script_name,
            )
            self._permanent_failure = True
            return
        if not self._script_path.exists():
            logger.error("bpftrace_daemon script_not_found path={}", self._script_path)
            self._permanent_failure = True
            return
        logger.info(
            "bpftrace_daemon starting script={} path={}",
            self._script_name,
            self._script_path,
        )
        await self._spawn(start_watchdog=True)

    async def _spawn(self, start_watchdog: bool = False) -> None:
        """创建 bpftrace 子进程。"""
        cmd = ["bpftrace", "--unsafe", "-f", "json", str(self._script_path)]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if self._proc.stdout is None or self._proc.stderr is None:
            raise RuntimeError("bpftrace process pipes were not created")
        self._consume_task = asyncio.create_task(self._consume())
        self._stderr_task = asyncio.create_task(self._collect_stderr())
        if start_watchdog:
            self._watchdog_task = asyncio.create_task(self._watchdog())

    async def _consume(self) -> None:
        """逐行消费 stdout，JSON 解析后入队。"""
        stdout = self._require_pipe("stdout")
        async for raw in stdout:
            try:
                event = json.loads(raw)
                try:
                    self._buffer.put_nowait(event)
                except asyncio.QueueFull:
                    self._buffer.get_nowait()
                    self._buffer.put_nowait(event)
            except json.JSONDecodeError:
                continue

    async def _collect_stderr(self) -> None:
        """收集 stderr 输出，进程退出时记录错误信息。"""
        stderr = self._require_pipe("stderr")
        stderr_data = await stderr.read()
        text = stderr_data.decode(errors="replace").strip()
        if text:
            logger.warning(
                "bpftrace_daemon stderr script={} msg={}", self._script_name, text
            )
            self._permanent_failure = _is_fatal_error(text)

    async def _watchdog(self) -> None:
        """检测子进程退出，自动重启。"""
        proc = self._require_process()
        returncode = await proc.wait()

        # 取消消费者
        if self._consume_task:
            self._consume_task.cancel()
        # 等待 stderr 收集完成
        if self._stderr_task:
            try:
                await asyncio.wait_for(self._stderr_task, timeout=2.0)
            except asyncio.TimeoutError:
                self._stderr_task.cancel()

        logger.warning(
            "bpftrace_daemon exited script={} returncode={} permanent_failure={} "
            "restart_in={}s",
            self._script_name,
            returncode,
            self._permanent_failure,
            self._restart_delay,
        )

        if self._permanent_failure:
            logger.warning(
                "bpftrace_daemon permanent_failure script={} - not retrying",
                self._script_name,
            )
            return

        await asyncio.sleep(self._restart_delay)
        await self._spawn(start_watchdog=True)

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._proc is None:
            raise RuntimeError("bpftrace process has not been started")
        return self._proc

    def _require_pipe(self, stream: str):
        proc = self._require_process()
        pipe = getattr(proc, stream)
        if pipe is None:
            raise RuntimeError(f"bpftrace process {stream} pipe is unavailable")
        return pipe

    def drain(self) -> list[dict]:
        """非阻塞清空缓冲区，返回所有等待事件。"""
        events: list[dict] = []
        while not self._buffer.empty():
            try:
                events.append(self._buffer.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    async def stop(self) -> None:
        """安全停止。"""
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._consume_task:
            self._consume_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()

    @property
    def permanent_failure(self) -> bool:
        """是否因权限等永久性错误而停止。"""
        return self._permanent_failure

    @property
    def status(self) -> str:
        """探针守护进程状态。

        Returns:
            "not_started" — start() 未被调用
            "running" — bpftrace 进程运行中
            "permanent_failure" — 永久性失败，不会重试
            "stopped" — 已调用 stop()
        """
        if self._watchdog_task and not self._watchdog_task.done():
            return "running"
        if self._permanent_failure:
            return "permanent_failure"
        if self._proc is not None and self._proc.returncode is not None:
            # 进程已退出但 watchdog 未启动 → 可能是 start_fail 或正在重启
            return "permanent_failure" if self._permanent_failure else "running"
        if self._proc is None:
            return "not_started"
        return "running"


# ── helpers ──────────────────────────────────────────────────────────


def _is_fatal_error(text: str) -> bool:
    """检查 stderr 是否包含不可恢复的错误（不应重试）。"""
    text_lower = text.lower()
    fatal_keywords = [
        # 权限错误
        "cap_dac_read_search",
        "cap_bpf",
        "permission denied",
        "operation not permitted",
        "not permitted",
        # bpftrace 脚本语法/语义错误
        "unknown function",
        "does not contain a field named",
        "unknown identifier",
        "syntax error",
    ]
    return any(k in text_lower for k in fatal_keywords)


class SubscriptionManager:
    """管理所有活跃的 bpftrace 守护进程。"""

    def __init__(self, resolve_fn: Callable[[str], Path | None] = resolve) -> None:
        self._resolve_fn = resolve_fn
        self._daemons: dict[str, BpftraceDaemon] = {}

    async def start_all(self, enabled: list[str] | None = None) -> None:
        """启动所有启用的持续探针。"""
        scripts = enabled or ["execsnoop.bt", "proc_exit.bt", "tcpconn.bt"]
        for name in scripts:
            daemon = BpftraceDaemon(name, resolve_fn=self._resolve_fn)
            await daemon.start()
            self._daemons[name] = daemon
        if self._daemons:
            logger.info(
                "bpftrace_subscription started count={} scripts={}",
                len(self._daemons),
                list(self._daemons),
            )

    def drain_all(self) -> dict[str, list[dict]]:
        """清空所有 daemon 缓冲区，返回 {脚本名: [事件]}。"""
        return {name: d.drain() for name, d in self._daemons.items()}

    def drain(self, script_name: str) -> list[dict]:
        """清空指定 daemon 的缓冲区。"""
        daemon = self._daemons.get(script_name)
        return daemon.drain() if daemon else []

    def probe_status(self, script_name: str) -> str:
        """返回指定探针的状态字符串。

        Returns:
            "not_registered" — 探针未在 start_all 中注册
            "not_started" — 已注册但未启动
            "running" — bpftrace 进程运行中
            "permanent_failure" — 永久性失败，不会重试
            "stopped" — 已调用 stop()
        """
        daemon = self._daemons.get(script_name)
        return daemon.status if daemon else "not_registered"

    async def shutdown(self) -> None:
        """停止所有 daemon。"""
        for name, daemon in self._daemons.items():
            logger.debug("bpftrace_daemon stopping script={}", name)
            await daemon.stop()
        self._daemons.clear()
