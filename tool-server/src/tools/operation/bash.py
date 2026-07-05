from __future__ import annotations

import os as _os
import signal
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
        payload = _run_host_command(
            host_command.argv, host_command.cwd, effective_timeout, command
        )
        logger.debug(
            "bash_result rc={rc} stdout_len={so} stderr_len={se}",
            rc=payload["returncode"],
            so=len(payload["stdout"]),
            se=len(payload["stderr"]),
        )
        return succeeded(payload) if payload["returncode"] == 0 else failed(payload)
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


def _run_host_command(
    argv: list[str], cwd: str | None, timeout: int, command: str
) -> dict[str, int | str]:
    # argv is constructed explicitly and subprocess uses shell=False.
    process = subprocess.Popen(  # noqa: S603
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        stdout, stderr = process.communicate()
        logger.warning("bash_timeout cmd={c!r} timeout={t}", c=command[:120], t=timeout)
        return {
            "returncode": -1,
            "stdout": stdout or getattr(exc, "stdout", "") or "",
            "stderr": stderr
            or getattr(exc, "stderr", "")
            or f"command timed out after {timeout}s",
        }


def _terminate_process_group(process: subprocess.Popen) -> None:
    try:
        _os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except ProcessLookupError:
        return
    except Exception:
        try:
            process.kill()
        except Exception:
            return

    if process.returncode is None:
        try:
            _os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
