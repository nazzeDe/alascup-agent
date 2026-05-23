"""Classifier for manage_service operations.

Read-only actions (status, list, etc.) → safe=True.
Write actions (start, stop, restart, etc.) → safe=False.
"""

from __future__ import annotations

from src.tools.operation.systemd import _READONLY_ACTIONS


def classify_manage_service(name: str = "", action: str = "") -> dict:
    return {"safe": action in _READONLY_ACTIONS}
