"""Classifier for manage_service operations.

Read-only actions (status, list, etc.) → safe=True.
Write actions (start, stop, restart, etc.) → safe=False.
"""

from __future__ import annotations

_READONLY_ACTIONS: set[str] = {
    "status",
    "is-active",
    "is-enabled",
    "list",
    "show",
    "list-units",
    "list-timers",
}


def classify_manage_service(name: str = "", action: str = "") -> dict:
    """Classify a manage_service operation as safe (read-only) or dangerous.

    Args:
        name: Service name (unused for classification).
        action: The systemctl action to classify.

    Returns:
        {"safe": bool}
    """
    return {"safe": action in _READONLY_ACTIONS}
