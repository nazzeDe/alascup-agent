from __future__ import annotations

import os

import psutil

from src.config import ToolServerConfig


def get_cpu_info(config: ToolServerConfig) -> dict:
    model_name = _read_model_name(config.proc_path)

    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "cores": psutil.cpu_count() or 1,
        "model_name": model_name,
        "loadavg": list(psutil.getloadavg()),
    }


def _read_model_name(proc_path: str) -> str:
    cpuinfo_path = os.path.join(proc_path, "cpuinfo")
    if not os.path.isfile(cpuinfo_path):
        return ""
    with open(cpuinfo_path) as f:
        for line in f:
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return ""
