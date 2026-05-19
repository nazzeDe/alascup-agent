from __future__ import annotations


def mark_experience_invalid(record_id: str, reason: str, store) -> dict:
    """Mark an entry as invalid. Physical record is preserved for audit."""
    ok = store.mark_invalid(record_id)
    if ok:
        return {"status": "marked_invalid", "record_id": record_id, "reason": reason}
    return {"status": "not_found", "record_id": record_id}
