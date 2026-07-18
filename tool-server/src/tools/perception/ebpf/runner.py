"""
runner.py — bpftrace 按需快照执行器。

每次调用启动一个 bpftrace --format=json 进程，捕获 JSON 事件流后退出。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from loguru import logger

from .resolver import resolve

# 各按需探针脚本内固定采样窗口（秒）
_SCRIPT_WINDOWS: dict[str, float] = {
    "syscount.bt": 10.0,
    "syscall_slow.bt": 5.0,
    "tcpdrop.bt": 10.0,
    "biolatency.bt": 10.0,
    "oomkill.bt": 30.0,
}
_DEFAULT_WINDOW_FALLBACK: float = 10.0
_TIMEOUT_GRACE_SECONDS: float = 5.0
MAX_OUTPUT_BYTES: int = 1 * 1024 * 1024
_MAX_READ_CHUNK_BYTES: int = 64 * 1024
_MAX_ERROR_TEXT_CHARS: int = 4096


class ProbeExecutor(Protocol):
    """Internal port for executing one resolved bpftrace probe."""

    async def execute(self, script_name: str, timeout: float) -> list[dict]: ...


class BpftraceProbeExecutor:
    """Production adapter for the bpftrace process boundary."""

    def __init__(self, resolve_fn: Callable[[str], Path | None] = resolve) -> None:
        self._resolve_fn = resolve_fn

    async def execute(self, script_name: str, timeout: float) -> list[dict]:
        return await run_on_demand(
            script_name,
            timeout=timeout,
            resolve_fn=self._resolve_fn,
        )


class _OutputLimitExceeded(Exception):
    def __init__(self, stream: str, max_output_bytes: int) -> None:
        self.stream = stream
        self.max_output_bytes = max_output_bytes
        super().__init__(f"bpftrace {stream} output exceeded {max_output_bytes} bytes")


class _OutputBudget:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._used = 0

    @property
    def remaining(self) -> int:
        return self._limit - self._used

    def consume(self, size: int, stream: str) -> None:
        if self._used + size > self._limit:
            raise _OutputLimitExceeded(stream, self._limit)
        self._used += size


async def run_on_demand(
    script_name: str,
    timeout: float | None = None,
    args: dict[str, str] | None = None,
    resolve_fn: Callable[[str], Path | None] = resolve,
) -> list[dict]:
    """运行一个 bpftrace 探针脚本并返回解析后的事件列表。

    Args:
        script_name: probes/ 下的 .bt 文件名（例如 "syscount.bt"）
        timeout: 超时秒数。为 None 时使用脚本对应的预设值。
        args: 可选的环境变量注入（设为 BPFTRACE_ARG_* 命名前缀）

    Returns:
        解析后的 JSON 事件列表。失败时返回 [{ "error": ..., "script": ... }]。
    """
    script_path, error = _resolve_script(script_name, resolve_fn)
    if error is not None:
        return [error]

    effective_timeout = (
        timeout if timeout is not None else on_demand_timeout(script_name)
    )
    stdout, stderr, returncode = await _run_bpftrace(
        script_path, script_name, effective_timeout, args
    )
    if returncode == "spawn_failed":
        return [
            {
                "error": "spawn_failed",
                "script": script_name,
                "message": _decode_error(stderr),
            }
        ]
    if returncode == "output_limit_exceeded":
        return [
            {
                "error": "output_limit_exceeded",
                "script": script_name,
                "max_output_bytes": MAX_OUTPUT_BYTES,
                "stream": _decode_error(stderr),
            }
        ]
    if returncode == "timeout":
        return [
            {"error": "timeout", "script": script_name, "timeout_s": effective_timeout}
        ]
    if returncode != 0:
        stderr_text = _decode_error(stderr)
        logger.error(
            "bpftrace failed script={} returncode={} stderr={}",
            script_name,
            returncode,
            stderr_text,
        )
        return [{"error": stderr_text, "script": script_name, "returncode": returncode}]

    return _parse_bpftrace_output(stdout.decode(errors="replace"))


def on_demand_timeout(
    script_name: str, requested_duration: float | None = None
) -> float:
    script_window = _SCRIPT_WINDOWS.get(script_name, _DEFAULT_WINDOW_FALLBACK)
    requested_window = requested_duration if requested_duration is not None else 0.0
    return max(script_window, requested_window) + _TIMEOUT_GRACE_SECONDS


def _resolve_script(
    script_name: str,
    resolve_fn: Callable[[str], Path | None],
) -> tuple[Path | None, dict | None]:
    script_path = resolve_fn(script_name)
    if script_path is None:
        err = f"probe script not found (no variant for this kernel): {script_name}"
        logger.error(err)
        return None, {"error": err, "script": script_name}
    if not script_path.exists():
        err = f"probe script not found: {script_path}"
        logger.error(err)
        return None, {"error": err, "script": script_name}
    return script_path, None


async def _run_bpftrace(
    script_path: Path,
    script_name: str,
    effective_timeout: float,
    args: dict[str, str] | None,
) -> tuple[bytes, bytes, int | str]:
    cmd = ["bpftrace", "--unsafe", "-f", "json", str(script_path)]
    env = {f"BPFTRACE_ARG_{k.upper()}": v for k, v in (args or {}).items()}

    logger.debug("bpftrace start script={} timeout={}s", script_name, effective_timeout)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**env} if env else None,
        )
    except OSError as exc:
        logger.error("bpftrace spawn failed script={} error={}", script_name, exc)
        return b"", str(exc).encode(errors="replace"), "spawn_failed"

    if proc.stdout is None or proc.stderr is None:
        await _terminate_bpftrace(proc)
        message = "bpftrace process pipes were not created"
        logger.error("bpftrace spawn failed script={} error={}", script_name, message)
        return b"", message.encode(), "spawn_failed"

    budget = _OutputBudget(MAX_OUTPUT_BYTES)
    stdout_task = asyncio.create_task(_read_bounded(proc.stdout, budget, "stdout"))
    stderr_task = asyncio.create_task(_read_bounded(proc.stderr, budget, "stderr"))
    wait_task = asyncio.create_task(proc.wait())
    tasks = (stdout_task, stderr_task, wait_task)

    try:
        await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, wait_task),
            timeout=effective_timeout,
        )
    except _OutputLimitExceeded as exc:
        logger.error(
            "bpftrace output limit exceeded script={} stream={} max_bytes={}",
            script_name,
            exc.stream,
            exc.max_output_bytes,
        )
        await _cancel_tasks(tasks)
        await _terminate_bpftrace(proc)
        return b"", exc.stream.encode(), "output_limit_exceeded"
    except asyncio.TimeoutError:
        logger.warning(
            "bpftrace timeout script={} timeout={}s", script_name, effective_timeout
        )
        await _cancel_tasks(tasks)
        await _terminate_bpftrace(proc)
        return b"", b"", "timeout"
    except asyncio.CancelledError:
        logger.warning("bpftrace cancelled script={}", script_name)
        await _cancel_tasks(tasks)
        await _terminate_bpftrace(proc)
        raise
    finally:
        await _cancel_tasks(tasks)

    return stdout_task.result(), stderr_task.result(), proc.returncode or 0


async def _read_bounded(stream, budget: _OutputBudget, stream_name: str) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = await stream.read(min(_MAX_READ_CHUNK_BYTES, budget.remaining + 1))
        if not chunk:
            return b"".join(chunks)
        budget.consume(len(chunk), stream_name)
        chunks.append(chunk)


async def _cancel_tasks(tasks: tuple[asyncio.Task, ...]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _decode_error(data: bytes) -> str:
    text = data.decode(errors="replace").strip() if data else ""
    if len(text) > _MAX_ERROR_TEXT_CHARS:
        return text[:_MAX_ERROR_TEXT_CHARS] + "..."
    return text


async def _terminate_bpftrace(proc) -> None:
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


def _parse_bpftrace_output(text: str) -> list[dict]:
    """逐行解析 bpftrace --format=json 的输出。

    跳过空行、控制台消息和格式错误的行。
    """
    events: list[dict] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith(("Attaching", "WARNING:", "Warning:")):
            continue
        try:
            obj = json.loads(line)
            events.append(obj)
        except json.JSONDecodeError:
            logger.debug("bpftrace parse skip line={!r}", line[:120])
            continue
    return events
