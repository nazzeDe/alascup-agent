"""Internal helper — consistently serialises fingerprint dicts for embedding."""
from __future__ import annotations


def format_fingerprint(fingerprint: dict) -> str:
    """Sorted k=v pairs, separated by spaces. Same format used in search and save."""
    return " ".join(f"{k}={v}" for k, v in sorted(fingerprint.items()))
