"""
runner.py — bpftrace 按需快照执行器。

每次调用启动一个 bpftrace --format=json 进程，捕获 JSON 事件流后退出。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loguru import logger

_PROBES_DIR = Path(__file__).parent / "probes"

# 各探针的超时预设（秒）
_DEFAULT_TIMEOUTS: dict[str, float] = {
    "syscount.bt": 5.0,
    "syscall_slow.bt": 5.0,
    "tcpdrop.bt": 10.0,
    "biolatency.bt": 10.0,
    "oomkill.bt": 30.0,
}
_DEFAULT_TIMEOUT_FALLBACK: float = 10.0


async def run_on_demand(
    script_name: str,
    timeout: float | None = None,
    args: dict[str, str] | None = None,
) -> list[dict]:
    """运行一个 bpftrace 探针脚本并返回解析后的事件列表。

    Args:
        script_name: probes/ 下的 .bt 文件名（例如 "syscount.bt"）
        timeout: 超时秒数。为 None 时使用脚本对应的预设值。
        args: 可选的环境变量注入（设为 BPFTRACE_ARG_* 命名前缀）

    Returns:
        解析后的 JSON 事件列表。失败时返回 [{ "error": ..., "script": ... }]。
    """
    script_path = _PROBES_DIR / script_name
    if not script_path.exists():
        err = f"probe script not found: {script_path}"
        logger.error(err)
        return [{"error": err, "script": script_name}]

    effective_timeout = timeout if timeout is not None else _DEFAULT_TIMEOUTS.get(script_name, _DEFAULT_TIMEOUT_FALLBACK)

    cmd = ["bpftrace", "--unsafe", "-f", "json", str(script_path)]
    env = {f"BPFTRACE_ARG_{k.upper()}": v for k, v in (args or {}).items()}

    logger.debug("bpftrace start script={} timeout={}s", script_name, effective_timeout)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**env} if env else None,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=effective_timeout
        )
    except asyncio.TimeoutError:
        logger.warning("bpftrace timeout script={} timeout={}s", script_name, effective_timeout)
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return [{"error": "timeout", "script": script_name, "timeout_s": effective_timeout}]

    if proc.returncode != 0:
        stderr_text = stderr.decode(errors="replace").strip() if stderr else ""
        logger.error("bpftrace failed script={} returncode={} stderr={}", script_name, proc.returncode, stderr_text)
        return [{"error": stderr_text, "script": script_name, "returncode": proc.returncode}]

    return _parse_bpftrace_output(stdout.decode(errors="replace"))


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
