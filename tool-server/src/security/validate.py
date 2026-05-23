import uuid


def validate_execution(approval_status: str, request_id: str, is_read_only: bool) -> tuple[bool, str]:
    if approval_status != "APPROVED":
        return False, f"approval_status must be APPROVED, got {approval_status}"
    if not is_read_only:
        if not request_id:
            return False, "request_id required for destructive operations"
        try:
            uuid.UUID(request_id)
        except (ValueError, AttributeError):
            return False, f"invalid request_id UUID: {request_id!r}"
    return True, ""
