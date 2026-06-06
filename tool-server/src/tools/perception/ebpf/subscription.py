"""
subscription.py — bpftrace 持续订阅管理。

维护后台常驻的 bpftrace 进程，事件写入环形缓冲区，
通过 drain() 非阻塞读取累计事件。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

_PROBES_DIR = Path(__file__).parent / "probes"


class BpftraceDaemon:
    """管理一个持续运行的 bpftrace 进程及其事件缓冲区。"""

    def __init__(
        self,
        script_name: str,
        buffer_size: int = 4096,
        restart_delay: float = 3.0,
    ) -> None:
        self._script_name = script_name
        self._script_path = _PROBES_DIR / script_name
        self._buffer_size = buffer_size
        self._restart_delay = restart_delay

        self._proc: asyncio.subprocess.Process | None = None
        self._buffer: asyncio.Queue[dict] = asyncio.Queue(maxsize=buffer_size)
        self._consume_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动子进程及其消费者协程。"""
        if not self._script_path.exists():
            logger.error("bpftrace_daemon script_not_found path={}", self._script_path)
            return
        logger.info("bpftrace_daemon starting script={}", self._script_name)
        await self._spawn()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def _spawn(self) -> None:
        """创建 bpftrace 子进程。"""
        cmd = ["bpftrace", "--unsafe", "--format=json", str(self._script_path)]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdout is not None
        self._consume_task = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        """逐行消费 stdout，JSON 解析后入队。"""
        assert self._proc is not None and self._proc.stdout is not None
        async for raw in self._proc.stdout:
            try:
                event = json.loads(raw)
                try:
                    self._buffer.put_nowait(event)
                except asyncio.QueueFull:
                    self._buffer.get_nowait()
                    self._buffer.put_nowait(event)
            except json.JSONDecodeError:
                continue

    async def _watchdog(self) -> None:
        """检测子进程退出，自动重启。"""
        assert self._proc is not None
        returncode = await self._proc.wait()
        logger.warning(
            "bpftrace_daemon exited script={} returncode={} restart_in={}s",
            self._script_name, returncode, self._restart_delay,
        )
        # 取消消费者
        if self._consume_task:
            self._consume_task.cancel()

        await asyncio.sleep(self._restart_delay)
        await self._spawn()
        # 重新启动 watchdog
        self._watchdog_task = asyncio.create_task(self._watchdog())

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
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()


class SubscriptionManager:
    """管理所有活跃的 bpftrace 守护进程。"""

    def __init__(self) -> None:
        self._daemons: dict[str, BpftraceDaemon] = {}

    async def start_all(self, enabled: list[str] | None = None) -> None:
        """启动所有启用的持续探针。"""
        scripts = enabled or ["execsnoop.bt", "proc_exit.bt", "tcpconn.bt"]
        for name in scripts:
            daemon = BpftraceDaemon(name)
            await daemon.start()
            self._daemons[name] = daemon
        if self._daemons:
            logger.info("bpftrace_subscription started count={} scripts={}", len(self._daemons), list(self._daemons))

    def drain_all(self) -> dict[str, list[dict]]:
        """清空所有 daemon 缓冲区，返回 {脚本名: [事件]}。"""
        return {name: d.drain() for name, d in self._daemons.items()}

    def drain(self, script_name: str) -> list[dict]:
        """清空指定 daemon 的缓冲区。"""
        daemon = self._daemons.get(script_name)
        return daemon.drain() if daemon else []

    async def shutdown(self) -> None:
        """停止所有 daemon。"""
        for name, daemon in self._daemons.items():
            logger.debug("bpftrace_daemon stopping script={}", name)
            await daemon.stop()
        self._daemons.clear()
