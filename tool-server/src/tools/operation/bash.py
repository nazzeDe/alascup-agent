from __future__ import annotations

import os as _os
import subprocess

from loguru import logger

from src.config import ToolServerConfig
from src.error.types import security_violation
from src.security.execution_context import is_controlled_execution
from src.tool_result import failed, succeeded
from src.tool_result import failed_error
from src.tools.operation._host_exec import prepare_host_command


def run_bash(
    config: ToolServerConfig, command: str = "", timeout: int | None = None
) -> dict:
    if not is_controlled_execution():
        logger.warning("bash_direct_call_rejected cmd={c!r}", c=command[:120])
        return failed_error(
            security_violation("mutable tools must be called through execute_tool")
        )

    effective_timeout = timeout if timeout is not None else config.bash_timeout
    host_command = prepare_host_command(["bash", "-c", command], config)
    logger.debug(
        "bash_exec cmd={c!r} timeout={t} cwd={w}",
        c=command[:120],
        t=effective_timeout,
        w=host_command.cwd,
    )
    try:
        _ensure_cwd(host_command.cwd)
        # argv is constructed explicitly and subprocess uses shell=False.
        result = subprocess.run(  # noqa: S603
            host_command.argv,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            cwd=host_command.cwd,
        )
        status = "SUCCEEDED" if result.returncode == 0 else "FAILED"
        logger.debug(
            "bash_result rc={rc} stdout_len={so} stderr_len={se}",
            rc=result.returncode,
            so=len(result.stdout),
            se=len(result.stderr),
        )
        payload = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        return succeeded(payload) if status == "SUCCEEDED" else failed(payload)
    except subprocess.TimeoutExpired:
        logger.warning(
            "bash_timeout cmd={c!r} timeout={t}", c=command[:120], t=effective_timeout
        )
        return failed(
            {
                "returncode": -1,
                "stdout": "",
                "stderr": f"command timed out after {effective_timeout}s",
            }
        )
    except FileNotFoundError as exc:
        logger.error(
            "bash_exec_failed filename={f!r} cmd={c!r}",
            f=getattr(exc, "filename", "?"),
            c=command[:120],
        )
        return failed(
            {
                "returncode": -1,
                "stdout": "",
                "stderr": "bash not found",
            }
        )
    except Exception as exc:
        logger.opt(exception=True).error(
            "bash_unexpected_error cmd={c!r}", c=command[:120]
        )
        return failed(
            {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
            }
        )


def _ensure_cwd(cwd: str | None) -> None:
    if cwd is None:
        return
    _os.makedirs(cwd, exist_ok=True)
