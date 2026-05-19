import uuid


def validate_execution(approval_status: str, request_id: str, is_read_only: bool) -> tuple[bool, str]:
    if approval_status != "APPROVED":
        return False, "SECURITY_VIOLATION"
    if not is_read_only:
        if not request_id:
            return False, "SECURITY_VIOLATION"
        try:
            uuid.UUID(request_id)
        except (ValueError, AttributeError):
            return False, "SECURITY_VIOLATION"
    return True, ""
