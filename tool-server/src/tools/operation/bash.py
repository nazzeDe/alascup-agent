from __future__ import annotations

import subprocess

from src.config import ToolServerConfig


def run_bash(config: ToolServerConfig, command: str = "", timeout: int | None = None) -> dict:
    effective_timeout = timeout if timeout is not None else config.bash_timeout
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            cwd=config.sandbox_root,
        )
        status = "SUCCEEDED" if result.returncode == 0 else "FAILED"
        return {
            "execution_status": status,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": f"command timed out after {effective_timeout}s",
        }
    except FileNotFoundError:
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": "bash not found",
        }
    except Exception as exc:
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }
