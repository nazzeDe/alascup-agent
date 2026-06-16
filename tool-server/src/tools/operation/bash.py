from __future__ import annotations

import subprocess

from loguru import logger

from src.config import ToolServerConfig
from src.tools.operation._host_exec import _host_cmd


def run_bash(config: ToolServerConfig, command: str = "", timeout: int | None = None) -> dict:
    effective_timeout = timeout if timeout is not None else config.bash_timeout
    cmd = _host_cmd(["bash", "-c", command], config)
    cwd = None if cmd[0] in ("nsenter", "chroot") else config.sandbox_root
    if cwd is not None:
        import os as _os
        _os.makedirs(cwd, exist_ok=True)
    logger.debug("bash_exec cmd={c!r} timeout={t} cwd={w}", c=command[:120], t=effective_timeout, w=cwd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            cwd=cwd,
        )
        status = "SUCCEEDED" if result.returncode == 0 else "FAILED"
        logger.debug("bash_result rc={rc} stdout_len={so} stderr_len={se}",
                     rc=result.returncode, so=len(result.stdout), se=len(result.stderr))
        return {
            "execution_status": status,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        logger.warning("bash_timeout cmd={c!r} timeout={t}", c=command[:120], t=effective_timeout)
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": f"command timed out after {effective_timeout}s",
        }
    except FileNotFoundError as exc:
        logger.error("bash_exec_failed filename={f!r} cmd={c!r}", f=getattr(exc, 'filename', '?'), c=command[:120])
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": "bash not found",
        }
    except Exception as exc:
        logger.opt(exception=True).error("bash_unexpected_error cmd={c!r}", c=command[:120])
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }
