from __future__ import annotations

import subprocess

from src.config import ToolServerConfig

_READONLY_ACTIONS = {"status", "is-active", "is-enabled", "list", "show", "list-units", "list-timers"}


def manage_service(config: ToolServerConfig, name: str = "", action: str = "") -> dict:
    if not name or not action:
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": "name and action are required",
        }

    if action in ("list", "list-units", "list-timers"):
        cmd = ["systemctl", action]
    else:
        cmd = ["systemctl", action, name]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
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
            "stderr": "command timed out after 30s",
        }
    except FileNotFoundError:
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": "systemctl not found",
        }
    except Exception as exc:
        return {
            "execution_status": "FAILED",
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }
